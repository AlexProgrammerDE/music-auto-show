use std::{
    io::Write,
    sync::{
        Arc, Mutex, MutexGuard,
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Receiver, RecvTimeoutError, Sender, SyncSender},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use anyhow::{Context, Result, bail};
use serialport::{
    DataBits, FlowControl, Parity, SerialPort, SerialPortInfo, SerialPortType, StopBits,
};
use tracing::{info, warn};

use crate::{
    proto::v1::{DmxConfig, DmxRuntimeStatus},
    timing::PeriodicSchedule,
};

/// ANSI E1.11 DMX512-A transmits every slot as 8N2 at 250 kbit/s.
const DMX_BAUD_RATE: u32 = 250_000;
/// Open DMX has no break command, so a zero byte at 50 kbit/s synthesizes it.
const OPEN_DMX_BREAK_BAUD_RATE: u32 = 50_000;
/// A zero data byte provides the nine consecutive low bits used as BREAK.
const OPEN_DMX_BREAK_BYTE: u8 = 0;
/// Start code zero identifies standard DMX512 dimmer data.
const DMX_NULL_START_CODE: u8 = 0;
/// DMX packets must carry at least one data slot in this application.
const DMX_MIN_CHANNELS: usize = 1;
/// One DMX universe contains at most 512 data slots, excluding the start code.
const DMX_MAX_CHANNELS: usize = 512;
/// The service accepts output rates starting at one packet per second.
const DMX_MIN_FPS: u32 = 1;
/// ANSI E1.11 caps a full 512-slot universe at 44 packets per second.
const DMX_MAX_FPS: u32 = 44;
/// One 8N2 slot is 11 bits and therefore takes 44 us at the DMX line rate.
#[cfg(test)]
const DMX_SLOT_MICROS: u64 = 44;
/// Nine low bits at the break baud rate produce a 180 us BREAK.
#[cfg(test)]
const OPEN_DMX_BREAK_MICROS: u64 = 180;
/// The two stop bits of the synthetic break byte provide 40 us of MARK.
#[cfg(test)]
const OPEN_DMX_STOP_MARK_MICROS: u64 = 40;
/// Extra MARK time after restoring 250 kbit/s, beyond the break byte's stop bits.
const OPEN_DMX_EXTRA_MAB_MICROS: u64 = 12;
/// Duration form of the extra MARK time used by the serial sender.
const OPEN_DMX_EXTRA_MAB: Duration = Duration::from_micros(OPEN_DMX_EXTRA_MAB_MICROS);
/// ANSI E1.11-2024 requires a transmitter BREAK of at least 92 us.
#[cfg(test)]
const DMX_MIN_BREAK_MICROS: u64 = 92;
/// ANSI E1.11-2024 requires MARK AFTER BREAK of at least 12 us.
#[cfg(test)]
const DMX_MIN_MAB_MICROS: u64 = 12;
/// ANSI E1.11-2024 requires at least 1,204 us between consecutive BREAK starts.
const DMX_MIN_BREAK_TO_BREAK: Duration = Duration::from_micros(1_204);
/// Failed hardware acquisition is retried quickly on the first attempt.
const DMX_RECONNECT_INITIAL: Duration = Duration::from_secs(1);
/// The reconnect backoff is capped so a reattached adapter recovers promptly.
const DMX_RECONNECT_MAX: Duration = Duration::from_secs(30);
/// Serial operations fail in bounded time so the worker can reconnect or stop.
const DMX_SERIAL_TIMEOUT: Duration = Duration::from_millis(100);
/// Drop still sends several zero frames when callers omit an explicit shutdown.
const DEFAULT_SHUTDOWN_BLACKOUT_FRAMES: usize = 3;
/// FTDI's vendor ID is used by Open DMX-class FT232R adapters.
const FTDI_VENDOR_ID: u16 = 0x0403;
/// The FT232R product ID identifies the USB-to-serial chip in these adapters.
const FT232R_PRODUCT_ID: u16 = 0x6001;

// Keep the software-generated BREAK and MARK AFTER BREAK above the standard's
// transmitter minima even if one of the timing constants changes later.
#[cfg(test)]
const _: () = {
    assert!(OPEN_DMX_BREAK_MICROS >= DMX_MIN_BREAK_MICROS);
    assert!(OPEN_DMX_STOP_MARK_MICROS + OPEN_DMX_EXTRA_MAB_MICROS >= DMX_MIN_MAB_MICROS);
};

/// Owns the DMX serial device on a dedicated thread.
///
/// The show loop only replaces the latest universe in a fixed buffer. This
/// keeps audio and effect processing non-blocking and prevents a slow USB
/// write from building a queue of stale lighting frames.
pub struct DmxWorker {
    universe: Arc<Mutex<UniverseBuffer>>,
    status: Arc<Mutex<DmxRuntimeStatus>>,
    deadlines_skipped: Arc<AtomicU64>,
    commands: Sender<WorkerCommand>,
    thread: Option<JoinHandle<()>>,
}

impl DmxWorker {
    /// Starts continuous output using the supplied hardware and timing config.
    pub fn start(config: DmxConfig) -> Result<Self> {
        validate_config(&config)?;
        let universe = Arc::new(Mutex::new(UniverseBuffer::new(
            config.universe_size as usize,
        )));
        let status = Arc::new(Mutex::new(active_status(&config)));
        let deadlines_skipped = Arc::new(AtomicU64::new(0));
        let (commands, receiver) = mpsc::channel();
        let worker_universe = Arc::clone(&universe);
        let worker_status = Arc::clone(&status);
        let worker_deadlines_skipped = Arc::clone(&deadlines_skipped);
        let thread = thread::Builder::new()
            .name("music-auto-show-dmx".into())
            .spawn(move || {
                WorkerState::new(
                    config,
                    worker_universe,
                    worker_status,
                    worker_deadlines_skipped,
                )
                .run(receiver);
            })
            .context("failed to start DMX output worker")?;
        Ok(Self {
            universe,
            status,
            deadlines_skipped,
            commands,
            thread: Some(thread),
        })
    }

    /// Replaces the universe that will be used for the next transmitted frame.
    pub fn set_universe(&self, universe: &[u8]) -> Result<()> {
        lock_unpoisoned(&self.universe).set(universe)
    }

    /// Zeros the shared universe without waiting for the serial worker.
    pub fn blackout(&self) {
        lock_unpoisoned(&self.universe).blackout();
    }

    /// Drains blackout frames on the old output, then applies a new config.
    ///
    /// Configuration changes are rare control-plane operations, so this call
    /// waits for the worker acknowledgement and can safely report completion.
    pub fn reconfigure(&self, config: DmxConfig, blackout_frames: usize) -> Result<()> {
        validate_config(&config)?;
        let (reply, completed) = mpsc::sync_channel(0);
        self.commands
            .send(WorkerCommand::Reconfigure {
                config,
                blackout_frames,
                reply,
            })
            .context("DMX output worker is unavailable")?;
        completed
            .recv()
            .context("DMX output worker stopped during reconfiguration")
    }

    /// Returns a consistent copy of the worker's connection and send counters.
    pub fn status(&self) -> DmxRuntimeStatus {
        lock_unpoisoned(&self.status).clone()
    }

    pub fn deadlines_skipped(&self) -> u64 {
        self.deadlines_skipped.load(Ordering::Relaxed)
    }

    /// Sends the requested blackout frames, releases the port, and joins the worker.
    pub fn shutdown(&mut self, blackout_frames: usize) -> Result<()> {
        let Some(thread) = self.thread.take() else {
            return Ok(());
        };
        let (reply, completed) = mpsc::sync_channel(0);
        let sent = self.commands.send(WorkerCommand::Shutdown {
            blackout_frames,
            reply,
        });
        if sent.is_ok() {
            let _ = completed.recv();
        }
        thread
            .join()
            .map_err(|_| anyhow::anyhow!("DMX output worker panicked"))?;
        sent.context("DMX output worker stopped before shutdown")
    }
}

impl Drop for DmxWorker {
    fn drop(&mut self) {
        let _ = self.shutdown(DEFAULT_SHUTDOWN_BLACKOUT_FRAMES);
    }
}

enum WorkerCommand {
    /// Replaces output configuration after safely blacking out the old device.
    Reconfigure {
        config: DmxConfig,
        blackout_frames: usize,
        reply: SyncSender<()>,
    },
    /// Performs the final blackout sequence before the worker exits.
    Shutdown {
        blackout_frames: usize,
        reply: SyncSender<()>,
    },
}

/// State confined to the worker thread, including exclusive serial ownership.
struct WorkerState {
    config: DmxConfig,
    universe: Arc<Mutex<UniverseBuffer>>,
    status: Arc<Mutex<DmxRuntimeStatus>>,
    deadlines_skipped: Arc<AtomicU64>,
    output: Option<DmxOutput>,
    retry_at: Instant,
    retry_delay: Duration,
}

impl WorkerState {
    fn new(
        config: DmxConfig,
        universe: Arc<Mutex<UniverseBuffer>>,
        status: Arc<Mutex<DmxRuntimeStatus>>,
        deadlines_skipped: Arc<AtomicU64>,
    ) -> Self {
        Self {
            config,
            universe,
            status,
            deadlines_skipped,
            output: None,
            retry_at: Instant::now(),
            retry_delay: DMX_RECONNECT_INITIAL,
        }
    }

    fn run(mut self, receiver: Receiver<WorkerCommand>) {
        let mut schedule =
            PeriodicSchedule::immediate(frame_interval(self.config.fps), Instant::now());
        loop {
            // Waiting on the command channel avoids polling while still waking
            // exactly when the next periodic frame is due.
            let timeout = schedule.remaining(Instant::now());
            match receiver.recv_timeout(timeout) {
                Ok(WorkerCommand::Reconfigure {
                    config,
                    blackout_frames,
                    reply,
                }) => {
                    self.reconfigure(config, blackout_frames);
                    let _ = reply.send(());
                    schedule.reset(frame_interval(self.config.fps), Instant::now());
                }
                Ok(WorkerCommand::Shutdown {
                    blackout_frames,
                    reply,
                }) => {
                    self.shutdown(blackout_frames);
                    let _ = reply.send(());
                    return;
                }
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => {
                    self.shutdown(DEFAULT_SHUTDOWN_BLACKOUT_FRAMES);
                    return;
                }
            }

            if schedule.is_due(Instant::now()) {
                self.send_latest();
                let skipped = schedule.advance(Instant::now());
                self.deadlines_skipped.fetch_add(skipped, Ordering::Relaxed);
            }
        }
    }

    fn send_latest(&mut self) {
        self.ensure_connected();
        let (universe, universe_size) = lock_unpoisoned(&self.universe).snapshot();
        self.send_connected(&universe[..universe_size]);
    }

    fn ensure_connected(&mut self) {
        if self.output.is_some() || Instant::now() < self.retry_at {
            return;
        }

        match DmxOutput::open(&self.config) {
            Ok(output) => {
                let mut connected = output.status();
                // Connection changes must not reset lifetime health counters.
                {
                    let previous = lock_unpoisoned(&self.status);
                    connected.send_count = previous.send_count;
                    connected.error_count = previous.error_count;
                }
                self.output = Some(output);
                *lock_unpoisoned(&self.status) = connected;
                self.retry_delay = DMX_RECONNECT_INITIAL;
                self.retry_at = Instant::now();
                let status = lock_unpoisoned(&self.status);
                info!(port = %status.port, simulated = status.simulated, "DMX output acquired");
            }
            Err(error) => {
                self.record_error(error.to_string());
                warn!(
                    error = %error,
                    retry_seconds = self.retry_delay.as_secs_f32(),
                    "DMX output unavailable; retrying"
                );
                self.schedule_retry();
            }
        }
    }

    fn send_connected(&mut self, universe: &[u8]) {
        let result = match self.output.as_mut() {
            Some(output) => output.send(universe),
            None => return,
        };

        match result {
            Ok(()) => {
                let mut status = lock_unpoisoned(&self.status);
                status.send_count += 1;
                status.consecutive_errors = 0;
                status.last_error.clear();
            }
            Err(error) => {
                warn!(%error, "DMX output failed; reconnecting");
                self.output = None;
                self.record_error(error.to_string());
                self.schedule_retry();
            }
        }
    }

    fn record_error(&self, message: String) {
        let mut status = lock_unpoisoned(&self.status);
        status.configured_port = self.config.port.clone();
        status.running = true;
        status.is_open = false;
        status.simulated = self.config.simulate;
        status.error_count += 1;
        status.consecutive_errors += 1;
        status.last_error = message;
    }

    fn schedule_retry(&mut self) {
        self.retry_at = Instant::now() + self.retry_delay;
        self.retry_delay = self.retry_delay.saturating_mul(2).min(DMX_RECONNECT_MAX);
    }

    fn send_blackout_frames(&mut self, frames: usize) {
        let universe_size = lock_unpoisoned(&self.universe).len();
        let blackout = [0; DMX_MAX_CHANNELS];
        for _ in 0..frames {
            if self.output.is_none() {
                break;
            }
            self.send_connected(&blackout[..universe_size]);
        }
    }

    fn reconfigure(&mut self, config: DmxConfig, blackout_frames: usize) {
        // Send zeros through the old port before releasing it so fixtures do
        // not remain latched at their last nonzero values.
        self.send_blackout_frames(blackout_frames);
        self.output = None;
        self.config = config;
        lock_unpoisoned(&self.universe).reset(self.config.universe_size as usize);
        let mut reset = active_status(&self.config);
        {
            let previous = lock_unpoisoned(&self.status);
            reset.send_count = previous.send_count;
            reset.error_count = previous.error_count;
        }
        *lock_unpoisoned(&self.status) = reset;
        self.retry_at = Instant::now();
        self.retry_delay = DMX_RECONNECT_INITIAL;
    }

    fn shutdown(&mut self, blackout_frames: usize) {
        self.send_blackout_frames(blackout_frames);
        self.output = None;
        let mut status = lock_unpoisoned(&self.status);
        status.running = false;
        status.is_open = false;
    }
}

/// Fixed-size latest-value storage shared with the real-time show loop.
///
/// A fixed array bounds memory and copying. `len` preserves configurations
/// that intentionally transmit fewer than the maximum 512 channels.
struct UniverseBuffer {
    channels: [u8; DMX_MAX_CHANNELS],
    len: usize,
}

impl UniverseBuffer {
    fn new(len: usize) -> Self {
        Self {
            channels: [0; DMX_MAX_CHANNELS],
            len,
        }
    }

    fn set(&mut self, universe: &[u8]) -> Result<()> {
        validate_universe_size(universe.len())?;
        if universe.len() != self.len {
            bail!(
                "DMX universe has {} channels, but the configured universe has {}",
                universe.len(),
                self.len
            );
        }
        self.channels.fill(0);
        self.channels[..universe.len()].copy_from_slice(universe);
        Ok(())
    }

    fn blackout(&mut self) {
        self.channels.fill(0);
    }

    fn reset(&mut self, len: usize) {
        self.channels.fill(0);
        self.len = len;
    }

    fn snapshot(&self) -> ([u8; DMX_MAX_CHANNELS], usize) {
        (self.channels, self.len)
    }

    fn len(&self) -> usize {
        self.len
    }
}

/// A connected output implementation owned exclusively by the worker thread.
enum DmxOutput {
    Simulated {
        status: DmxRuntimeStatus,
    },
    Serial {
        port: Box<dyn SerialPort>,
        status: DmxRuntimeStatus,
        break_spacing_anchor: Option<Instant>,
    },
}

impl DmxOutput {
    fn open(config: &DmxConfig) -> Result<Self> {
        if config.simulate {
            return Ok(Self::Simulated {
                status: DmxRuntimeStatus {
                    configured_port: config.port.clone(),
                    port: "Simulated DMX".into(),
                    device_info: "No hardware output".into(),
                    interface_type: "simulated".into(),
                    break_method: "simulated".into(),
                    running: true,
                    is_open: true,
                    simulated: true,
                    ..Default::default()
                },
            });
        }

        // Automatic selection is deliberately conservative because an FTDI
        // adapter may control unrelated equipment on the same machine.
        let port_name = if config.port.trim().is_empty() || config.port.eq_ignore_ascii_case("auto")
        {
            detect_port()?
        } else {
            config.port.clone()
        };
        let device_info = serialport::available_ports()
            .ok()
            .and_then(|ports| ports.into_iter().find(|port| port.port_name == port_name))
            .map(|port| format_port(&port))
            .unwrap_or_else(|| "Unknown serial device".into());
        let port_builder = serialport::new(&port_name, DMX_BAUD_RATE)
            .data_bits(DataBits::Eight)
            .stop_bits(StopBits::Two)
            .parity(Parity::None)
            .flow_control(FlowControl::None)
            .timeout(DMX_SERIAL_TIMEOUT);
        #[cfg(unix)]
        let port_builder = port_builder.exclusive(true);
        let mut port = port_builder
            .open()
            .with_context(|| format!("failed to open DMX interface {port_name}"))?;
        if let Err(error) = port.write_request_to_send(false) {
            tracing::debug!(%error, "DMX interface does not support clearing RTS");
        }
        if let Err(error) = port.write_data_terminal_ready(false) {
            tracing::debug!(%error, "DMX interface does not support clearing DTR");
        }
        port.flush()?;
        Ok(Self::Serial {
            port,
            status: DmxRuntimeStatus {
                configured_port: config.port.clone(),
                port: port_name,
                device_info,
                interface_type: "Open DMX USB".into(),
                break_method: "baudrate_switch".into(),
                running: true,
                is_open: true,
                simulated: false,
                ..Default::default()
            },
            break_spacing_anchor: None,
        })
    }

    fn send(&mut self, universe: &[u8]) -> Result<()> {
        match self {
            Self::Simulated { .. } => Ok(()),
            Self::Serial {
                port,
                break_spacing_anchor,
                ..
            } => send_open_dmx(port.as_mut(), universe, break_spacing_anchor),
        }
    }

    fn status(&self) -> DmxRuntimeStatus {
        match self {
            Self::Simulated { status } | Self::Serial { status, .. } => status.clone(),
        }
    }
}

fn send_open_dmx(
    port: &mut dyn SerialPort,
    universe: &[u8],
    break_spacing_anchor: &mut Option<Instant>,
) -> Result<()> {
    validate_universe_size(universe.len())?;
    // Short universes and consecutive blackout requests can finish before the
    // standard's minimum packet duration, so enforce break-to-break explicitly.
    wait_for_break_spacing(*break_spacing_anchor);

    // A zero byte at 50 kbaud holds the line low for one start bit and eight
    // data bits (180 us). Its two stop bits then hold MARK for another 40 us.
    port.set_baud_rate(OPEN_DMX_BREAK_BAUD_RATE)
        .context("failed to select Open DMX break baud rate")?;
    port.write_all(&[OPEN_DMX_BREAK_BYTE])
        .context("failed to write Open DMX break")?;
    port.flush().context("failed to drain Open DMX break")?;
    // Record after the byte has drained. This is a conservative upper bound on
    // the real BREAK start, so the next spacing wait cannot run too short due
    // to USB or kernel buffering latency.
    *break_spacing_anchor = Some(Instant::now());

    port.set_baud_rate(DMX_BAUD_RATE)
        .context("failed to restore DMX baud rate")?;
    thread::sleep(OPEN_DMX_EXTRA_MAB);

    // Start code zero denotes standard dimmer data. One contiguous write keeps
    // slot timing in the serial driver and avoids gaps between channels.
    let mut frame = [DMX_NULL_START_CODE; DMX_MAX_CHANNELS + 1];
    frame[1..=universe.len()].copy_from_slice(universe);
    port.write_all(&frame[..=universe.len()])
        .context("failed to write DMX frame")?;
    port.flush().context("failed to drain DMX frame")?;
    Ok(())
}

fn wait_for_break_spacing(last_break_at: Option<Instant>) {
    let Some(last_break_at) = last_break_at else {
        return;
    };
    let remaining = remaining_break_spacing(last_break_at.elapsed());
    if !remaining.is_zero() {
        thread::sleep(remaining);
    }
}

fn remaining_break_spacing(elapsed: Duration) -> Duration {
    DMX_MIN_BREAK_TO_BREAK.saturating_sub(elapsed)
}

/// Converts the configured packet rate to its start-to-start interval.
pub(crate) fn frame_interval(fps: u32) -> Duration {
    Duration::from_secs_f64(1.0 / f64::from(fps.max(DMX_MIN_FPS)))
}

#[cfg(test)]
fn nominal_packet_duration(universe_size: usize) -> Duration {
    Duration::from_micros(
        OPEN_DMX_BREAK_MICROS
            + OPEN_DMX_STOP_MARK_MICROS
            + OPEN_DMX_EXTRA_MAB.as_micros() as u64
            + DMX_SLOT_MICROS * (universe_size as u64 + 1),
    )
}

fn detect_port() -> Result<String> {
    let ports = serialport::available_ports().context("failed to enumerate serial ports")?;
    select_detected_port(&ports)
}

fn select_detected_port(ports: &[SerialPortInfo]) -> Result<String> {
    // Refuse ambiguity instead of guessing which adapter is connected to the
    // lighting bus. An explicit path remains available in configuration.
    let candidates = ports
        .iter()
        .filter(|port| is_open_dmx_candidate(port))
        .collect::<Vec<_>>();
    match candidates.as_slice() {
        [] => bail!("no serial DMX interface was found"),
        [port] => Ok(port.port_name.clone()),
        ports => bail!(
            "multiple serial DMX candidates were found ({}); select an explicit port",
            ports
                .iter()
                .map(|port| port.port_name.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn is_open_dmx_candidate(port: &SerialPortInfo) -> bool {
    let description = format_port(port).to_ascii_uppercase();
    description.contains("DMX")
        || matches!(
            &port.port_type,
            SerialPortType::UsbPort(info)
                if info.vid == FTDI_VENDOR_ID && info.pid == FT232R_PRODUCT_ID
        )
}

fn format_port(port: &SerialPortInfo) -> String {
    match &port.port_type {
        SerialPortType::UsbPort(info) => format!(
            "{} (VID:0x{:04x} PID:0x{:04x})",
            info.product.as_deref().unwrap_or("USB serial device"),
            info.vid,
            info.pid
        ),
        SerialPortType::PciPort => "PCI serial device".into(),
        SerialPortType::BluetoothPort => "Bluetooth serial device".into(),
        SerialPortType::Unknown => "Unknown serial device".into(),
    }
}

fn active_status(config: &DmxConfig) -> DmxRuntimeStatus {
    DmxRuntimeStatus {
        configured_port: config.port.clone(),
        running: true,
        simulated: config.simulate,
        ..Default::default()
    }
}

fn validate_universe_size(universe_size: usize) -> Result<()> {
    if !(DMX_MIN_CHANNELS..=DMX_MAX_CHANNELS).contains(&universe_size) {
        bail!("DMX universe size must be between 1 and 512");
    }
    Ok(())
}

/// Validates protocol limits before a worker or hardware connection is started.
pub fn validate_config(config: &DmxConfig) -> Result<()> {
    validate_universe_size(config.universe_size as usize)?;
    if !(DMX_MIN_FPS..=DMX_MAX_FPS).contains(&config.fps) {
        bail!("DMX output rate must be between 1 and 44 FPS");
    }
    Ok(())
}

fn lock_unpoisoned<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    match mutex.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    }
}

#[cfg(test)]
mod tests {
    use serialport::UsbPortInfo;

    use super::*;

    #[test]
    fn rejects_rates_above_the_dmx_limit() {
        let config = DmxConfig {
            fps: 45,
            universe_size: 512,
            ..Default::default()
        };
        assert!(validate_config(&config).is_err());
    }

    #[test]
    fn latest_universe_must_match_the_configured_channel_count() {
        let mut universe = UniverseBuffer::new(4);
        universe
            .set(&[1, 2, 3, 4])
            .expect("matching universe should be accepted");
        assert!(universe.set(&[1, 2, 3]).is_err());
    }

    #[test]
    fn open_dmx_framing_exceeds_transmitter_minimums() {
        assert_eq!(nominal_packet_duration(512), Duration::from_micros(22_804));
    }

    #[test]
    fn short_packets_wait_for_minimum_break_spacing() {
        assert_eq!(
            remaining_break_spacing(Duration::from_micros(320)),
            Duration::from_micros(884)
        );
        assert_eq!(
            remaining_break_spacing(DMX_MIN_BREAK_TO_BREAK),
            Duration::ZERO
        );
    }

    #[test]
    fn automatic_detection_rejects_ambiguous_ftdi_devices() {
        let first = usb_port("/dev/ttyUSB0", FTDI_VENDOR_ID, FT232R_PRODUCT_ID, None);
        let second = usb_port("/dev/ttyUSB1", FTDI_VENDOR_ID, FT232R_PRODUCT_ID, None);
        let error =
            select_detected_port(&[first, second]).expect_err("selection should be ambiguous");
        assert!(error.to_string().contains("select an explicit port"));
    }

    #[test]
    fn automatic_detection_ignores_unrelated_serial_devices() {
        let unrelated = usb_port("/dev/ttyACM0", 0x2341, 0x0043, Some("Arduino Uno"));
        let dmx = usb_port(
            "/dev/ttyUSB0",
            FTDI_VENDOR_ID,
            FT232R_PRODUCT_ID,
            Some("FT232R USB UART"),
        );
        assert_eq!(
            select_detected_port(&[unrelated, dmx]).unwrap(),
            "/dev/ttyUSB0"
        );
    }

    fn usb_port(name: &str, vid: u16, pid: u16, product: Option<&str>) -> SerialPortInfo {
        SerialPortInfo {
            port_name: name.into(),
            port_type: SerialPortType::UsbPort(UsbPortInfo {
                vid,
                pid,
                serial_number: None,
                manufacturer: None,
                product: product.map(str::to_owned),
            }),
        }
    }
}
