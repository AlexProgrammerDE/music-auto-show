use std::{
    collections::VecDeque,
    str::FromStr,
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Receiver, RecvTimeoutError, SyncSender, TrySendError},
    },
    thread,
    time::{Duration, Instant},
};

use anyhow::{Context, Result, bail};
use arc_swap::ArcSwap;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use realfft::{RealFftPlanner, RealToComplex, num_complex::Complex};

use crate::{
    beatnet::{BeatEstimate, BeatNetPlus},
    proto::v1::{
        AudioAnalysis, AudioConfig, AudioDevice, AudioInputMode, AudioRuntimeStatus, BeatNetStatus,
        Recording, RecordingStatus, SpectrogramFrame,
    },
    timing::PeriodicSchedule,
};

const ANALYSIS_RATE: u32 = 44_100;
const FFT_SIZE: usize = 1_024;
const MAX_RECORDING_SECONDS: f32 = 30.0;
const PIPEWIRE_DEFAULT_SINK_ID: &str = "pipewire:sink_default";
const CAPTURE_QUEUE_DEPTH: usize = 32;
const CAPTURE_BUFFER_CAPACITY: usize = 8_192;
const AUDIO_WORKER_COMMAND_DEPTH: usize = 8;
const AUDIO_ANALYSIS_INTERVAL: Duration = Duration::from_millis(20);
const MAX_SIMULATION_CATCHUP: Duration = Duration::from_millis(200);

pub struct AudioCapture {
    receiver: Receiver<Vec<f32>>,
    available_buffers: SyncSender<Vec<f32>>,
    status: AudioRuntimeStatus,
    stream_error: Arc<Mutex<String>>,
    dropped_sample_blocks: Arc<AtomicU64>,
    recoverable_stream_events: Arc<AtomicU64>,
    _guard: CaptureGuard,
}

impl AudioCapture {
    fn drain_samples_into(&self, pending: &mut Vec<f32>) {
        pending.clear();
        while let Ok(mut samples) = self.receiver.try_recv() {
            pending.append(&mut samples);
            let _ = self.available_buffers.try_send(samples);
        }
    }

    pub fn sample_rate(&self) -> u32 {
        self.status.sample_rate
    }

    pub fn status(&self) -> AudioRuntimeStatus {
        let mut status = self.status.clone();
        if let Ok(error) = self.stream_error.lock()
            && !error.is_empty()
        {
            status.running = false;
            status.last_error = error.clone();
        }
        status.dropped_sample_blocks = self.dropped_sample_blocks.load(Ordering::Relaxed);
        status
    }

    fn recoverable_stream_events(&self) -> u64 {
        self.recoverable_stream_events.load(Ordering::Relaxed)
    }
}

#[derive(Clone)]
pub struct AudioWorkerSnapshot {
    pub analysis: AudioAnalysis,
    pub runtime: AudioRuntimeStatus,
    pub beatnet: BeatNetStatus,
    pub recording: RecordingStatus,
    pub analysis_deadlines_skipped: u64,
    pub recoverable_stream_events: u64,
}

/// Owns the stateful analyzer and recording lifecycle on a dedicated thread.
///
/// The CPAL stream retains its platform-safe owner thread. This worker drains
/// its bounded queue on the audio clock so BeatNet and recordings do not inherit
/// the independently configurable DMX cadence.
pub struct AudioWorker {
    commands: SyncSender<AudioWorkerCommand>,
    latest: Arc<ArcSwap<AudioWorkerSnapshot>>,
    thread: Option<thread::JoinHandle<()>>,
}

impl AudioWorker {
    pub fn start(config: &AudioConfig, mode: AudioInputMode, cli_simulate: bool) -> Result<Self> {
        let config = config.clone();
        let latest = Arc::new(ArcSwap::from_pointee(stopped_worker_snapshot(
            &config,
            cli_simulate,
        )));
        let worker_latest = Arc::clone(&latest);
        let (commands, receiver) = mpsc::sync_channel(AUDIO_WORKER_COMMAND_DEPTH);
        let (ready_sender, ready_receiver) = mpsc::sync_channel(1);
        let thread = thread::Builder::new()
            .name("music-auto-show-audio".into())
            .spawn(
                move || match AudioWorkerState::new(config, mode, cli_simulate) {
                    Ok(mut state) => {
                        state.publish(&worker_latest);
                        if ready_sender.send(Ok(())).is_ok() {
                            state.run(receiver, &worker_latest);
                        }
                    }
                    Err(error) => {
                        let _ = ready_sender.send(Err(format!("{error:#}")));
                    }
                },
            )
            .context("failed to start audio analysis worker")?;
        match ready_receiver.recv() {
            Ok(Ok(())) => Ok(Self {
                commands,
                latest,
                thread: Some(thread),
            }),
            Ok(Err(error)) => {
                let _ = thread.join();
                bail!(error);
            }
            Err(error) => {
                let _ = thread.join();
                bail!("audio analysis worker stopped during startup: {error}");
            }
        }
    }

    pub fn snapshot(&self) -> AudioWorkerSnapshot {
        self.latest.load_full().as_ref().clone()
    }

    pub fn start_recording(&self) -> Result<RecordingStatus> {
        let (reply, response) = mpsc::sync_channel(0);
        self.commands
            .send(AudioWorkerCommand::StartRecording { reply })
            .context("audio analysis worker is unavailable")?;
        response
            .recv()
            .context("audio analysis worker stopped while starting a recording")?
    }

    pub fn stop_recording(&self) -> Result<Recording> {
        let (reply, response) = mpsc::sync_channel(0);
        self.commands
            .send(AudioWorkerCommand::StopRecording { reply })
            .context("audio analysis worker is unavailable")?;
        response
            .recv()
            .context("audio analysis worker stopped while finishing a recording")?
    }

    pub fn clear_recording(&self) -> Result<RecordingStatus> {
        let (reply, response) = mpsc::sync_channel(0);
        self.commands
            .send(AudioWorkerCommand::ClearRecording { reply })
            .context("audio analysis worker is unavailable")?;
        response
            .recv()
            .context("audio analysis worker stopped while clearing a recording")?
    }

    fn shutdown(&mut self) {
        let Some(thread) = self.thread.take() else {
            return;
        };
        let (reply, completed) = mpsc::sync_channel(0);
        if self
            .commands
            .send(AudioWorkerCommand::Shutdown { reply })
            .is_ok()
        {
            let _ = completed.recv();
        }
        let _ = thread.join();
    }
}

impl Drop for AudioWorker {
    fn drop(&mut self) {
        self.shutdown();
    }
}

enum AudioWorkerCommand {
    StartRecording {
        reply: SyncSender<Result<RecordingStatus>>,
    },
    StopRecording {
        reply: SyncSender<Result<Recording>>,
    },
    ClearRecording {
        reply: SyncSender<Result<RecordingStatus>>,
    },
    Shutdown {
        reply: SyncSender<()>,
    },
}

struct AudioWorkerState {
    analyzer: AudioAnalyzer,
    capture: Option<AudioCapture>,
    config: AudioConfig,
    simulate: bool,
    pending: Vec<f32>,
    analysis: AudioAnalysis,
    simulation_sample: u64,
    simulation_started: Instant,
    simulation_beat: u64,
    simulation_was_beat: bool,
    analysis_deadlines_skipped: u64,
}

impl AudioWorkerState {
    fn new(config: AudioConfig, mode: AudioInputMode, cli_simulate: bool) -> Result<Self> {
        let simulate = cli_simulate || config.simulate;
        let capture = if simulate {
            None
        } else {
            Some(start_capture(&config, mode)?)
        };
        let sample_rate = capture
            .as_ref()
            .map_or(ANALYSIS_RATE, AudioCapture::sample_rate);
        Ok(Self {
            analyzer: AudioAnalyzer::new(sample_rate, config.gain, &config.beatnet_model_path),
            capture,
            config,
            simulate,
            pending: Vec::with_capacity(CAPTURE_BUFFER_CAPACITY),
            analysis: AudioAnalysis::default(),
            simulation_sample: 0,
            simulation_started: Instant::now(),
            simulation_beat: 0,
            simulation_was_beat: false,
            analysis_deadlines_skipped: 0,
        })
    }

    fn run(
        &mut self,
        receiver: Receiver<AudioWorkerCommand>,
        latest: &ArcSwap<AudioWorkerSnapshot>,
    ) {
        let mut schedule = PeriodicSchedule::immediate(AUDIO_ANALYSIS_INTERVAL, Instant::now());
        loop {
            match receiver.recv_timeout(schedule.remaining(Instant::now())) {
                Ok(AudioWorkerCommand::StartRecording { reply }) => {
                    let result = if self.analyzer.start_recording() {
                        Ok(self.analyzer.recording_status())
                    } else {
                        Err(anyhow::anyhow!("audio recording could not start"))
                    };
                    let _ = reply.send(result);
                    self.publish(latest);
                }
                Ok(AudioWorkerCommand::StopRecording { reply }) => {
                    let result = self.analyzer.stop_recording();
                    let _ = reply.send(result);
                    self.publish(latest);
                }
                Ok(AudioWorkerCommand::ClearRecording { reply }) => {
                    self.analyzer.clear_recording();
                    let status = self.analyzer.recording_status();
                    let _ = reply.send(Ok(status));
                    self.publish(latest);
                }
                Ok(AudioWorkerCommand::Shutdown { reply }) => {
                    let _ = reply.send(());
                    return;
                }
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => return,
            }

            if schedule.is_due(Instant::now()) {
                self.tick();
                self.analysis_deadlines_skipped = self
                    .analysis_deadlines_skipped
                    .saturating_add(schedule.advance(Instant::now()));
                self.publish(latest);
            }
        }
    }

    fn tick(&mut self) {
        if self.simulate {
            self.tick_simulated(self.simulation_started.elapsed());
            return;
        }

        if let Some(capture) = &self.capture {
            capture.drain_samples_into(&mut self.pending);
        }
        if !self.pending.is_empty() {
            self.analysis = self.analyzer.process(&self.pending);
        }
    }

    fn tick_simulated(&mut self, elapsed: Duration) {
        let target_sample =
            u64::try_from(samples_for_duration(ANALYSIS_RATE, elapsed)).unwrap_or(u64::MAX);
        let maximum_batch =
            u64::try_from(samples_for_duration(ANALYSIS_RATE, MAX_SIMULATION_CATCHUP))
                .unwrap_or(u64::MAX);
        if target_sample.saturating_sub(self.simulation_sample) > maximum_batch {
            self.simulation_sample = target_sample.saturating_sub(maximum_batch);
        }
        let sample_count = usize::try_from(target_sample.saturating_sub(self.simulation_sample))
            .unwrap_or(usize::MAX);
        if sample_count == 0 {
            return;
        }
        let (samples, mut beat) = simulated_audio(
            self.simulation_sample as f64 / f64::from(ANALYSIS_RATE),
            sample_count,
            self.simulation_beat,
        );
        if beat.beat && !self.simulation_was_beat {
            self.simulation_beat += 1;
            beat.estimated_beat = self.simulation_beat;
            beat.estimated_bar = self.simulation_beat / 4;
            beat.downbeat = self.simulation_beat.is_multiple_of(4);
        }
        self.simulation_was_beat = beat.beat;
        self.simulation_sample = self.simulation_sample.saturating_add(sample_count as u64);
        self.analysis = self.analyzer.process_simulated(&samples, beat);
    }

    fn publish(&self, latest: &ArcSwap<AudioWorkerSnapshot>) {
        let runtime = self.capture.as_ref().map_or_else(
            || simulated_worker_status(&self.config),
            AudioCapture::status,
        );
        let recoverable_stream_events = self
            .capture
            .as_ref()
            .map_or(0, AudioCapture::recoverable_stream_events);
        latest.store(Arc::new(AudioWorkerSnapshot {
            analysis: self.analysis.clone(),
            runtime,
            beatnet: self.analyzer.beatnet_status(),
            recording: self.analyzer.recording_status(),
            analysis_deadlines_skipped: self.analysis_deadlines_skipped,
            recoverable_stream_events,
        }));
    }
}

fn stopped_worker_snapshot(config: &AudioConfig, simulate: bool) -> AudioWorkerSnapshot {
    AudioWorkerSnapshot {
        analysis: AudioAnalysis::default(),
        runtime: AudioRuntimeStatus {
            configured_mode: config.mode,
            configured_device_id: config.device_id.clone(),
            selection_reason: "not_started".into(),
            simulated: simulate || config.simulate,
            ..Default::default()
        },
        beatnet: BeatNetStatus {
            model_name: "BeatNet+".into(),
            model_path: config.beatnet_model_path.clone(),
            status: "Idle".into(),
            ..Default::default()
        },
        recording: RecordingStatus {
            max_duration_seconds: MAX_RECORDING_SECONDS,
            ..Default::default()
        },
        analysis_deadlines_skipped: 0,
        recoverable_stream_events: 0,
    }
}

fn simulated_worker_status(config: &AudioConfig) -> AudioRuntimeStatus {
    AudioRuntimeStatus {
        configured_mode: config.mode,
        actual_mode: AudioInputMode::Auto as i32,
        configured_device_id: config.device_id.clone(),
        device_name: "Simulated audio generator".into(),
        device_type: "simulated".into(),
        host_api: "Simulation".into(),
        channels: 1,
        sample_rate: ANALYSIS_RATE,
        selection_reason: "simulated".into(),
        running: true,
        simulated: true,
        ..Default::default()
    }
}

fn samples_for_duration(sample_rate: u32, duration: Duration) -> usize {
    usize::try_from(
        duration.as_nanos().saturating_mul(u128::from(sample_rate))
            / Duration::from_secs(1).as_nanos(),
    )
    .unwrap_or(usize::MAX)
}

#[cfg(test)]
fn drain_queued_samples(receiver: &Receiver<Vec<f32>>) -> Vec<f32> {
    let mut pending = Vec::new();
    while let Ok(mut samples) = receiver.try_recv() {
        pending.append(&mut samples);
    }
    pending
}

struct CaptureGuard {
    stop: Option<SyncSender<()>>,
    thread: Option<thread::JoinHandle<()>>,
}

impl Drop for CaptureGuard {
    fn drop(&mut self) {
        if let Some(stop) = self.stop.take() {
            let _ = stop.send(());
        }
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

pub fn list_devices() -> Vec<AudioDevice> {
    let host = cpal::default_host();
    let default_id = host
        .default_input_device()
        .and_then(|device| device.id().ok())
        .map(|id| id.to_string());
    let default_loopback_id = default_system_output(&host)
        .and_then(|device| device.id().ok())
        .map(|id| id.to_string());
    let host_name = host.id().to_string();
    let mut devices = Vec::new();
    if let Ok(inputs) = host.devices() {
        for device in inputs
            .filter(|device| device.supports_input() || supports_system_output_capture(device))
        {
            let (Ok(id), Ok(description)) = (device.id(), device.description()) else {
                continue;
            };
            let id = id.to_string();
            let configuration = capture_stream_config(&device).ok();
            let channels = configuration
                .as_ref()
                .map_or(0, |config| config.channels() as u32);
            let sample_rate = configuration
                .as_ref()
                .map_or(0, |config| config.sample_rate());
            let output_channels = device
                .default_output_config()
                .ok()
                .map_or(0, |config| config.channels() as u32);
            let is_default_loopback = default_loopback_id.as_deref() == Some(id.as_str());
            let name = display_device_name(&id, description.name());
            devices.push(AudioDevice {
                id: id.clone(),
                name: name.clone(),
                device_type: capture_device_type(&id, &description).into(),
                channels,
                output_channels,
                sample_rate,
                host_api: host_name.clone(),
                is_default: default_id.as_deref() == Some(id.as_str()),
                is_default_loopback,
            });
        }
    }
    devices.sort_by(|left, right| {
        right
            .is_default_loopback
            .cmp(&left.is_default_loopback)
            .then_with(|| right.is_default.cmp(&left.is_default))
            .then_with(|| left.name.to_lowercase().cmp(&right.name.to_lowercase()))
    });
    devices
}

fn start_capture(config: &AudioConfig, mode: AudioInputMode) -> Result<AudioCapture> {
    start_cpal_capture(config, mode)
}

fn start_cpal_capture(config: &AudioConfig, mode: AudioInputMode) -> Result<AudioCapture> {
    let config = config.clone();
    let stream_error = Arc::new(Mutex::new(String::new()));
    let stream_error_thread = Arc::clone(&stream_error);
    let (sender, receiver) = mpsc::sync_channel(CAPTURE_QUEUE_DEPTH);
    let (available_buffers, reusable_buffers) = mpsc::sync_channel(CAPTURE_QUEUE_DEPTH);
    for _ in 0..CAPTURE_QUEUE_DEPTH {
        available_buffers
            .send(Vec::with_capacity(CAPTURE_BUFFER_CAPACITY))
            .map_err(|_| anyhow::anyhow!("failed to initialize audio capture buffers"))?;
    }
    let dropped_sample_blocks = Arc::new(AtomicU64::new(0));
    let dropped_sample_blocks_thread = Arc::clone(&dropped_sample_blocks);
    let recoverable_stream_events = Arc::new(AtomicU64::new(0));
    let recoverable_stream_events_thread = Arc::clone(&recoverable_stream_events);
    let buffer_recycler = available_buffers.clone();
    let (ready_sender, ready_receiver) = mpsc::sync_channel(1);
    let (stop_sender, stop_receiver) = mpsc::sync_channel(0);
    let thread = thread::Builder::new()
        .name("cpal-audio-capture".into())
        .spawn(move || {
            match build_cpal_stream(
                &config,
                mode,
                CapturePipeline {
                    sender,
                    reusable_buffers,
                    buffer_recycler,
                    stream_error: stream_error_thread,
                    dropped_sample_blocks: dropped_sample_blocks_thread,
                    recoverable_stream_events: recoverable_stream_events_thread,
                },
            ) {
                Ok((stream, status)) => {
                    if ready_sender.send(Ok(status)).is_err() {
                        return;
                    }
                    let _ = stop_receiver.recv();
                    drop(stream);
                }
                Err(error) => {
                    let _ = ready_sender.send(Err(format!("{error:#}")));
                }
            }
        })?;
    let status = match ready_receiver.recv() {
        Ok(Ok(status)) => status,
        Ok(Err(error)) => {
            let _ = thread.join();
            bail!(error);
        }
        Err(error) => {
            let _ = thread.join();
            bail!("audio capture thread stopped during startup: {error}");
        }
    };
    Ok(AudioCapture {
        receiver,
        available_buffers,
        status,
        stream_error,
        dropped_sample_blocks,
        recoverable_stream_events,
        _guard: CaptureGuard {
            stop: Some(stop_sender),
            thread: Some(thread),
        },
    })
}

struct CapturePipeline {
    sender: mpsc::SyncSender<Vec<f32>>,
    reusable_buffers: Receiver<Vec<f32>>,
    buffer_recycler: SyncSender<Vec<f32>>,
    stream_error: Arc<Mutex<String>>,
    dropped_sample_blocks: Arc<AtomicU64>,
    recoverable_stream_events: Arc<AtomicU64>,
}

fn build_cpal_stream(
    config: &AudioConfig,
    mode: AudioInputMode,
    pipeline: CapturePipeline,
) -> Result<(cpal::Stream, AudioRuntimeStatus)> {
    let CapturePipeline {
        sender,
        reusable_buffers,
        buffer_recycler,
        stream_error,
        dropped_sample_blocks,
        recoverable_stream_events,
    } = pipeline;
    let selection = select_capture_device(config, mode)?;
    let selected = selection.device;
    let id = selected.id()?.to_string();
    let description = selected.description()?;
    let name = display_device_name(&id, description.name());
    let supported = capture_stream_config(&selected)
        .with_context(|| format!("audio device {name} cannot be captured"))?;
    let channels = supported.channels() as usize;
    let sample_rate = supported.sample_rate();
    let stream_config: cpal::StreamConfig = supported.into();
    let stream = match supported.sample_format() {
        cpal::SampleFormat::F32 => selected.build_input_stream(
            stream_config,
            move |data: &[f32], _| {
                send_mono(
                    data,
                    channels,
                    &sender,
                    &reusable_buffers,
                    &buffer_recycler,
                    &dropped_sample_blocks,
                    |sample| sample,
                )
            },
            capture_error_callback(
                Arc::clone(&stream_error),
                Arc::clone(&recoverable_stream_events),
            ),
            None,
        )?,
        cpal::SampleFormat::I16 => selected.build_input_stream(
            stream_config,
            move |data: &[i16], _| {
                send_mono(
                    data,
                    channels,
                    &sender,
                    &reusable_buffers,
                    &buffer_recycler,
                    &dropped_sample_blocks,
                    |sample| sample as f32 / 32_768.0,
                )
            },
            capture_error_callback(
                Arc::clone(&stream_error),
                Arc::clone(&recoverable_stream_events),
            ),
            None,
        )?,
        cpal::SampleFormat::U16 => selected.build_input_stream(
            stream_config,
            move |data: &[u16], _| {
                send_mono(
                    data,
                    channels,
                    &sender,
                    &reusable_buffers,
                    &buffer_recycler,
                    &dropped_sample_blocks,
                    |sample| sample as f32 / 32_768.0 - 1.0,
                )
            },
            capture_error_callback(
                Arc::clone(&stream_error),
                Arc::clone(&recoverable_stream_events),
            ),
            None,
        )?,
        format => bail!("unsupported input sample format {format:?}"),
    };
    stream.play()?;
    Ok((
        stream,
        AudioRuntimeStatus {
            configured_mode: mode as i32,
            actual_mode: selection.actual_mode as i32,
            device_name: name,
            device_type: capture_device_type(&id, &description).into(),
            host_api: selected.id()?.host().to_string(),
            channels: channels as u32,
            sample_rate,
            selection_reason: selection.reason,
            running: true,
            last_error: String::new(),
            simulated: false,
            configured_device_id: config.device_id.clone(),
            device_id: id,
            missing_device_id: selection.missing_device_id,
            dropped_sample_blocks: 0,
        },
    ))
}

struct DeviceSelection {
    device: cpal::Device,
    actual_mode: AudioInputMode,
    reason: String,
    missing_device_id: String,
}

fn select_capture_device(config: &AudioConfig, mode: AudioInputMode) -> Result<DeviceSelection> {
    let host = cpal::default_host();
    let mut missing_device_id = String::new();

    let selected = match mode {
        AudioInputMode::ManualDevice | AudioInputMode::PipewireSink => (!config
            .device_id
            .is_empty())
        .then_some(config.device_id.as_str())
        .and_then(|configured| match resolve_configured_device(configured) {
            Some(device) if mode != AudioInputMode::PipewireSink || is_pipewire_sink(&device) => {
                Some((device, "configured_device"))
            }
            _ => {
                missing_device_id = config.device_id.clone();
                None
            }
        })
        .or_else(|| {
            if mode == AudioInputMode::PipewireSink {
                default_system_output(&host)
                    .map(|device| (device, "default_system_output"))
                    .or_else(|| {
                        host.default_input_device()
                            .map(|device| (device, "default_input"))
                    })
            } else {
                host.default_input_device()
                    .map(|device| (device, "default_input"))
            }
        }),
        AudioInputMode::SystemAudio => default_system_output(&host)
            .map(|device| (device, "default_system_output"))
            .or_else(|| {
                host.default_input_device()
                    .map(|device| (device, "default_input"))
            }),
        AudioInputMode::Microphone => host
            .default_input_device()
            .map(|device| (device, "default_input")),
        AudioInputMode::Auto | AudioInputMode::Unspecified => default_system_output(&host)
            .map(|device| (device, "auto_default_system_output"))
            .or_else(|| {
                host.default_input_device()
                    .map(|device| (device, "auto_default_input"))
            }),
    }
    .context("no capturable audio device is available")?;

    let actual_mode = if is_pipewire_sink(&selected.0) {
        AudioInputMode::PipewireSink
    } else if supports_system_output_capture(&selected.0) {
        AudioInputMode::SystemAudio
    } else if mode == AudioInputMode::ManualDevice {
        AudioInputMode::ManualDevice
    } else {
        AudioInputMode::Microphone
    };
    let reason = if missing_device_id.is_empty() {
        selected.1.to_owned()
    } else {
        format!("configured_device_missing_fallback_to_{}", selected.1)
    };

    Ok(DeviceSelection {
        device: selected.0,
        actual_mode,
        reason,
        missing_device_id,
    })
}

fn resolve_configured_device(configured: &str) -> Option<cpal::Device> {
    let id = cpal::DeviceId::from_str(configured).ok()?;
    cpal::host_from_id(id.host())
        .ok()
        .and_then(|configured_host| configured_host.device_by_id(&id))
}

fn default_system_output(host: &cpal::Host) -> Option<cpal::Device> {
    match host.id().to_string().as_str() {
        "pipewire" => {
            let id = cpal::DeviceId::from_str(PIPEWIRE_DEFAULT_SINK_ID).ok()?;
            host.device_by_id(&id)
        }
        "pulseaudio" => {
            let sink_id = host.default_output_device()?.id().ok()?;
            let monitor_id =
                cpal::DeviceId::new(sink_id.host(), format!("{}.monitor", sink_id.id()));
            host.device_by_id(&monitor_id)
        }
        "wasapi" => host.default_output_device(),
        _ => None,
    }
}

fn is_pipewire_sink(device: &cpal::Device) -> bool {
    let (Ok(id), Ok(description)) = (device.id(), device.description()) else {
        return false;
    };
    id.host().to_string() == "pipewire"
        && (id.id() == "sink_default" || description.direction() == cpal::DeviceDirection::Duplex)
}

fn supports_system_output_capture(device: &cpal::Device) -> bool {
    let (Ok(id), Ok(description)) = (device.id(), device.description()) else {
        return false;
    };
    match id.host().to_string().as_str() {
        "pipewire" => {
            id.id() == "sink_default" || description.direction() == cpal::DeviceDirection::Duplex
        }
        "pulseaudio" => id.id().ends_with(".monitor"),
        "wasapi" => description.direction() == cpal::DeviceDirection::Output,
        _ => false,
    }
}

fn capture_stream_config(
    device: &cpal::Device,
) -> Result<cpal::SupportedStreamConfig, cpal::Error> {
    if device.supports_input() {
        device.default_input_config()
    } else {
        device.default_output_config()
    }
}

fn capture_device_type(id: &str, description: &cpal::DeviceDescription) -> &'static str {
    let system_output = (id.starts_with("pipewire:")
        && (id == PIPEWIRE_DEFAULT_SINK_ID
            || description.direction() == cpal::DeviceDirection::Duplex))
        || (id.starts_with("pulseaudio:") && id.ends_with(".monitor"))
        || (id.starts_with("wasapi:") && description.direction() == cpal::DeviceDirection::Output);
    if system_output {
        "monitor"
    } else if description.interface_type() == cpal::InterfaceType::Line {
        "line_in"
    } else {
        "microphone"
    }
}

fn display_device_name(id: &str, name: &str) -> String {
    match id {
        PIPEWIRE_DEFAULT_SINK_ID => "Default system output".into(),
        "pipewire:input_default" => "Default audio input".into(),
        _ => name.into(),
    }
}

fn capture_error_callback(
    stream_error: Arc<Mutex<String>>,
    recoverable_stream_events: Arc<AtomicU64>,
) -> impl FnMut(cpal::Error) + Send + 'static {
    move |error| {
        if matches!(
            error.kind(),
            cpal::ErrorKind::DeviceChanged
                | cpal::ErrorKind::Xrun
                | cpal::ErrorKind::RealtimeDenied
        ) {
            recoverable_stream_events.fetch_add(1, Ordering::Relaxed);
            tracing::warn!(%error, "audio input stream reported a recoverable event");
            return;
        }
        tracing::error!(%error, "audio input stream failed");
        if let Ok(mut current_error) = stream_error.lock() {
            *current_error = error.to_string();
        }
    }
}

fn send_mono<T: Copy>(
    data: &[T],
    channels: usize,
    sender: &mpsc::SyncSender<Vec<f32>>,
    reusable_buffers: &Receiver<Vec<f32>>,
    buffer_recycler: &SyncSender<Vec<f32>>,
    dropped_sample_blocks: &AtomicU64,
    convert: impl Fn(T) -> f32,
) {
    let Ok(mut mono) = reusable_buffers.try_recv() else {
        dropped_sample_blocks.fetch_add(1, Ordering::Relaxed);
        return;
    };
    let channels = channels.max(1);
    let frames = data.len().div_ceil(channels);
    if frames > mono.capacity() {
        let _ = buffer_recycler.try_send(mono);
        dropped_sample_blocks.fetch_add(1, Ordering::Relaxed);
        return;
    }
    mono.clear();
    mono.extend(data.chunks(channels).map(|frame| {
        frame
            .iter()
            .copied()
            .map(|sample| {
                let sample = convert(sample);
                if sample.is_finite() { sample } else { 0.0 }
            })
            .sum::<f32>()
            / frame.len() as f32
    }));
    match sender.try_send(mono) {
        Ok(()) => {}
        Err(TrySendError::Full(mono) | TrySendError::Disconnected(mono)) => {
            let _ = buffer_recycler.try_send(mono);
            dropped_sample_blocks.fetch_add(1, Ordering::Relaxed);
        }
    }
}

pub struct AudioAnalyzer {
    sample_rate: u32,
    gain: f32,
    beatnet: Option<BeatNetPlus>,
    beatnet_status: BeatNetStatus,
    analysis_fft: AnalysisFft,
    resampler: LinearResampler,
    energy_history: VecDeque<f32>,
    bass_history: VecDeque<f32>,
    mid_history: VecDeque<f32>,
    high_history: VecDeque<f32>,
    onset_history: VecDeque<f32>,
    spectrogram: VecDeque<Vec<f32>>,
    last_spectrogram: Instant,
    recording: Recorder,
    last_analysis: AudioAnalysis,
}

impl AudioAnalyzer {
    pub fn new(sample_rate: u32, gain: f32, model_path: &str) -> Self {
        let (beatnet, beatnet_status) = match BeatNetPlus::load(model_path) {
            Ok(model) => (
                Some(model),
                BeatNetStatus {
                    available: true,
                    processing: false,
                    model_name: "BeatNet+ generic".into(),
                    model_path: model_path.into(),
                    status: "Ready".into(),
                    last_error: String::new(),
                    buffer_duration_seconds: BeatNetPlus::TEMPO_WINDOW_SECONDS,
                },
            ),
            Err(error) => (
                None,
                BeatNetStatus {
                    available: false,
                    processing: false,
                    model_name: "BeatNet+".into(),
                    model_path: model_path.into(),
                    status: "Model unavailable".into(),
                    last_error: error.to_string(),
                    buffer_duration_seconds: 0.0,
                },
            ),
        };
        Self {
            sample_rate,
            gain,
            beatnet,
            beatnet_status,
            analysis_fft: AnalysisFft::new(FFT_SIZE),
            resampler: LinearResampler::new(sample_rate, 22_050),
            energy_history: VecDeque::with_capacity(10),
            bass_history: VecDeque::with_capacity(5),
            mid_history: VecDeque::with_capacity(5),
            high_history: VecDeque::with_capacity(5),
            onset_history: VecDeque::with_capacity(64),
            spectrogram: VecDeque::with_capacity(50),
            last_spectrogram: Instant::now() - Duration::from_secs(1),
            recording: Recorder::new(sample_rate),
            last_analysis: AudioAnalysis::default(),
        }
    }

    pub fn process(&mut self, samples: &[f32]) -> AudioAnalysis {
        if samples.is_empty() {
            return self.last_analysis.clone();
        }
        self.recording.capture(samples);
        let rms = (samples.iter().map(|sample| sample * sample).sum::<f32>()
            / samples.len() as f32)
            .sqrt();
        push_bounded(&mut self.energy_history, rms, 10);
        let fft = self.analysis_fft.process(samples);
        let signal_power = rms * rms;
        let band = |low, high| band_power(fft, self.sample_rate, FFT_SIZE, low, high);
        let (bass, mid, high) = normalize_bands(
            band(20.0, 250.0),
            band(250.0, 4_000.0),
            band(4_000.0, 16_000.0),
            signal_power,
            self.gain,
        );
        push_bounded(&mut self.bass_history, bass, 5);
        push_bounded(&mut self.mid_history, mid, 5);
        push_bounded(&mut self.high_history, high, 5);

        let resampled = self.resampler.process(samples);
        let beat_result = self
            .beatnet
            .as_mut()
            .map(|model| model.push_resampled_samples(&resampled));
        let beat = match beat_result {
            Some(Ok(Some(beat))) => {
                self.beatnet_status.processing = true;
                beat
            }
            Some(Ok(None)) => {
                self.beatnet_status.processing = true;
                fallback_beat(&self.last_analysis)
            }
            Some(Err(error)) => {
                self.beatnet_status.available = false;
                self.beatnet_status.processing = false;
                self.beatnet_status.status = "Inference failed".into();
                self.beatnet_status.last_error = error.to_string();
                self.beatnet = None;
                fallback_beat(&self.last_analysis)
            }
            None => fallback_beat(&self.last_analysis),
        };
        self.onset_history
            .push_back(beat.beat_activation.max(beat.downbeat_activation));
        if self.onset_history.len() > 64 {
            self.onset_history.pop_front();
        }

        let average_rms = average(&self.energy_history);
        let gain_db = 20.0 * self.gain.max(0.0001).log10();
        let energy = if average_rms > 1e-10 {
            ((20.0 * average_rms.log10() + gain_db + 60.0) / 60.0).clamp(0.0, 1.0)
        } else {
            0.0
        };
        let bass = average(&self.bass_history);
        let mid = average(&self.mid_history);
        let high = average(&self.high_history);
        let danceability = (bass * 0.5 + energy * 0.5).min(1.0);
        let valence = if bass + mid + high > 0.0 {
            ((mid + high * 2.0) / (bass + mid + high + 0.001)).min(1.0)
        } else {
            0.0
        };

        if self.last_spectrogram.elapsed() >= Duration::from_millis(100) {
            self.spectrogram.push_back(spectrogram_frame(
                fft,
                self.sample_rate,
                FFT_SIZE,
                self.gain,
            ));
            if self.spectrogram.len() > 50 {
                self.spectrogram.pop_front();
            }
            self.last_spectrogram = Instant::now();
        }
        self.last_analysis = AudioAnalysis {
            energy,
            rms,
            bass,
            mid,
            high,
            tempo: beat.tempo,
            beat_detected: beat.beat,
            downbeat_detected: beat.downbeat,
            beat_confidence: beat.confidence,
            beat_position: beat.beat_position,
            bar_position: beat.bar_position,
            estimated_beat: beat.estimated_beat,
            estimated_bar: beat.estimated_bar,
            danceability,
            valence,
            waveform: waveform(samples, self.gain),
            spectrum: spectrum(fft, self.sample_rate, FFT_SIZE, self.gain),
            spectrogram: self
                .spectrogram
                .iter()
                .cloned()
                .map(|bins| SpectrogramFrame { bins })
                .collect(),
            onset_history: self.onset_history.iter().copied().collect(),
        };
        self.last_analysis.clone()
    }

    pub fn process_simulated(&mut self, samples: &[f32], beat: BeatEstimate) -> AudioAnalysis {
        let mut analysis = self.process(samples);
        analysis.tempo = beat.tempo;
        analysis.beat_detected = beat.beat;
        analysis.downbeat_detected = beat.downbeat;
        analysis.beat_confidence = beat.confidence;
        analysis.beat_position = beat.beat_position;
        analysis.bar_position = beat.bar_position;
        analysis.estimated_beat = beat.estimated_beat;
        analysis.estimated_bar = beat.estimated_bar;
        self.last_analysis = analysis.clone();
        analysis
    }

    pub fn beatnet_status(&self) -> BeatNetStatus {
        self.beatnet_status.clone()
    }
    pub fn start_recording(&mut self) -> bool {
        self.recording.start()
    }
    pub fn stop_recording(&mut self) -> Result<Recording> {
        self.recording.stop()
    }
    pub fn clear_recording(&mut self) {
        self.recording.clear()
    }
    pub fn recording_status(&self) -> RecordingStatus {
        self.recording.status()
    }
}

fn fallback_beat(previous: &AudioAnalysis) -> BeatEstimate {
    BeatEstimate {
        tempo: previous.tempo,
        beat_position: previous.beat_position,
        bar_position: previous.bar_position,
        estimated_beat: previous.estimated_beat,
        estimated_bar: previous.estimated_bar,
        ..Default::default()
    }
}

struct AnalysisFft {
    size: usize,
    plan: Arc<dyn RealToComplex<f32>>,
    input: Vec<f32>,
    output: Vec<Complex<f32>>,
    power: Vec<f32>,
}

impl AnalysisFft {
    fn new(size: usize) -> Self {
        let mut planner = RealFftPlanner::<f32>::new();
        let plan = planner.plan_fft_forward(size);
        Self {
            size,
            input: plan.make_input_vec(),
            output: plan.make_output_vec(),
            power: vec![0.0; size / 2 + 1],
            plan,
        }
    }

    fn process(&mut self, samples: &[f32]) -> &[f32] {
        self.input.fill(0.0);
        let copy = samples.len().min(self.size);
        let source_start = samples.len().saturating_sub(copy);
        self.input[..copy].copy_from_slice(&samples[source_start..]);
        for (index, value) in self.input.iter_mut().enumerate() {
            *value *=
                0.5 - 0.5 * (std::f32::consts::TAU * index as f32 / (self.size - 1) as f32).cos();
        }
        if self
            .plan
            .process(&mut self.input, &mut self.output)
            .is_err()
        {
            self.power.fill(0.0);
            return &self.power;
        }
        for (power, value) in self.power.iter_mut().zip(&self.output) {
            *power = value.norm_sqr();
        }
        &self.power
    }
}

#[cfg(test)]
fn fft_power(samples: &[f32], size: usize) -> Vec<f32> {
    AnalysisFft::new(size).process(samples).to_vec()
}

fn band_power(fft: &[f32], sample_rate: u32, size: usize, low: f32, high: f32) -> f32 {
    let low = (low * size as f32 / sample_rate as f32) as usize;
    let high = (high * size as f32 / sample_rate as f32) as usize;
    let low = low.min(fft.len().saturating_sub(1));
    let high = high.clamp(low + 1, fft.len());
    fft[low..high].iter().sum::<f32>() / (high - low) as f32
}

fn normalize_bands(bass: f32, mid: f32, high: f32, signal: f32, gain: f32) -> (f32, f32, f32) {
    if signal < 1e-10 {
        return (0.0, 0.0, 0.0);
    }
    let normalized = |power: f32, offset: f32| {
        let db = 10.0 * (power / signal + 1e-10).log10();
        ((db - offset + 30.0) / 60.0 + (gain - 1.0) * 0.2).clamp(0.0, 1.0)
    };
    (
        normalized(bass, 35.0),
        normalized(mid, 25.0),
        normalized(high, 5.0),
    )
}

fn waveform(samples: &[f32], gain: f32) -> Vec<f32> {
    let chunk = (samples.len() / 100).max(1);
    (0..100)
        .map(|index| {
            let start = index * chunk;
            let end = (start + chunk).min(samples.len());
            if start >= samples.len() {
                0.0
            } else {
                let average = samples[start..end].iter().sum::<f32>() / (end - start) as f32;
                (average / 0.3 * gain).clamp(-1.0, 1.0)
            }
        })
        .collect()
}

fn spectrum(fft: &[f32], sample_rate: u32, size: usize, gain: f32) -> Vec<f32> {
    let minimum = sample_rate as f32 / size as f32;
    let power_scale = one_sided_power_scale(size);
    logarithmic_bands(32, minimum, 16_000.0_f32.min(sample_rate as f32 / 2.0))
        .map(|(low, high)| {
            visualization_level(
                band_peak_power(fft, sample_rate, size, low, high) * power_scale,
                gain,
                -72.0,
                -12.0,
            )
        })
        .collect()
}

fn spectrogram_frame(fft: &[f32], sample_rate: u32, size: usize, gain: f32) -> Vec<f32> {
    let minimum = sample_rate as f32 / size as f32;
    let power_scale = one_sided_power_scale(size);
    logarithmic_bands(64, minimum, 16_000.0_f32.min(sample_rate as f32 / 2.0))
        .map(|(low, high)| {
            visualization_level(
                band_peak_power(fft, sample_rate, size, low, high) * power_scale,
                gain,
                -90.0,
                -12.0,
            )
        })
        .collect()
}

fn band_peak_power(fft: &[f32], sample_rate: u32, size: usize, low: f32, high: f32) -> f32 {
    let low = (low * size as f32 / sample_rate as f32).floor() as usize;
    let high = (high * size as f32 / sample_rate as f32).ceil() as usize;
    let low = low.min(fft.len().saturating_sub(1));
    let high = high.clamp(low + 1, fft.len());
    fft[low..high].iter().copied().fold(0.0, f32::max)
}

fn one_sided_power_scale(size: usize) -> f32 {
    if size < 2 {
        return 0.0;
    }
    16.0 / (3.0 * size as f32 * (size - 1) as f32)
}

fn visualization_level(power: f32, gain: f32, floor_db: f32, ceiling_db: f32) -> f32 {
    let power_db = 10.0 * (power + 1e-12).log10();
    let gain_db = 20.0 * gain.max(0.0001).log10();
    ((power_db + gain_db - floor_db) / (ceiling_db - floor_db)).clamp(0.0, 1.0)
}

fn logarithmic_bands(count: usize, minimum: f32, maximum: f32) -> impl Iterator<Item = (f32, f32)> {
    let ratio = (maximum / minimum).powf(1.0 / count as f32);
    (0..count).map(move |index| {
        (
            minimum * ratio.powi(index as i32),
            minimum * ratio.powi(index as i32 + 1),
        )
    })
}

fn push_bounded(values: &mut VecDeque<f32>, value: f32, limit: usize) {
    values.push_back(value);
    if values.len() > limit {
        values.pop_front();
    }
}

fn average(values: &VecDeque<f32>) -> f32 {
    if values.is_empty() {
        0.0
    } else {
        values.iter().sum::<f32>() / values.len() as f32
    }
}

struct LinearResampler {
    ratio: f64,
    position: f64,
    previous: f32,
    initialized: bool,
}

impl LinearResampler {
    fn new(input_rate: u32, output_rate: u32) -> Self {
        Self {
            ratio: input_rate as f64 / output_rate as f64,
            position: 0.0,
            previous: 0.0,
            initialized: false,
        }
    }

    fn process(&mut self, input: &[f32]) -> Vec<f32> {
        if input.is_empty() {
            return Vec::new();
        }
        if !self.initialized {
            self.previous = input[0];
            self.initialized = true;
        }
        let mut extended = Vec::with_capacity(input.len() + 1);
        extended.push(self.previous);
        extended.extend_from_slice(input);
        let mut output = Vec::new();
        while self.position + 1.0 < extended.len() as f64 {
            let index = self.position.floor() as usize;
            let fraction = (self.position - index as f64) as f32;
            output.push(extended[index] * (1.0 - fraction) + extended[index + 1] * fraction);
            self.position += self.ratio;
        }
        self.position -= input.len() as f64;
        if let Some(&last) = input.last() {
            self.previous = last;
        }
        output
    }
}

struct Recorder {
    phase: RecordingPhase,
    sample_rate: u32,
    samples: Vec<f32>,
    sum_squares: f64,
    peak: f32,
    clipped: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RecordingPhase {
    Idle,
    Capturing,
    LimitReached,
}

impl Recorder {
    fn new(sample_rate: u32) -> Self {
        Self {
            phase: RecordingPhase::Idle,
            sample_rate,
            samples: Vec::new(),
            sum_squares: 0.0,
            peak: 0.0,
            clipped: 0,
        }
    }
    fn start(&mut self) -> bool {
        if self.phase != RecordingPhase::Idle {
            return false;
        }
        self.clear_samples();
        self.phase = RecordingPhase::Capturing;
        true
    }
    fn clear(&mut self) {
        self.phase = RecordingPhase::Idle;
        self.clear_samples();
    }
    fn clear_samples(&mut self) {
        self.samples.clear();
        self.sum_squares = 0.0;
        self.peak = 0.0;
        self.clipped = 0;
    }
    fn capture(&mut self, samples: &[f32]) {
        if self.phase != RecordingPhase::Capturing {
            return;
        }
        let maximum = (self.sample_rate as f32 * MAX_RECORDING_SECONDS) as usize;
        let remaining = maximum.saturating_sub(self.samples.len());
        for sample in samples.iter().take(remaining).copied() {
            self.samples.push(sample);
            self.sum_squares += (sample * sample) as f64;
            self.peak = self.peak.max(sample.abs());
            self.clipped += u64::from(sample.abs() >= 0.99);
        }
        if self.samples.len() >= maximum {
            self.phase = RecordingPhase::LimitReached;
        }
    }
    fn status(&self) -> RecordingStatus {
        RecordingStatus {
            recording: self.phase != RecordingPhase::Idle,
            has_recording: !self.samples.is_empty(),
            duration_seconds: self.samples.len() as f32 / self.sample_rate as f32,
            max_duration_seconds: MAX_RECORDING_SECONDS,
            sample_rate: self.sample_rate,
            channels: 1,
            peak: self.peak,
            rms: if self.samples.is_empty() {
                0.0
            } else {
                (self.sum_squares / self.samples.len() as f64).sqrt() as f32
            },
            clipped_samples: self.clipped,
            source: "Analysis mono stream".into(),
            error: String::new(),
            limit_reached: self.phase == RecordingPhase::LimitReached,
        }
    }
    fn stop(&mut self) -> Result<Recording> {
        if self.phase == RecordingPhase::Idle {
            bail!("audio recording is not active");
        }
        self.phase = RecordingPhase::Idle;
        let mut wav = Vec::new();
        {
            let cursor = std::io::Cursor::new(&mut wav);
            let mut writer = hound::WavWriter::new(
                cursor,
                hound::WavSpec {
                    channels: 1,
                    sample_rate: self.sample_rate,
                    bits_per_sample: 16,
                    sample_format: hound::SampleFormat::Int,
                },
            )
            .context("failed to create diagnostic WAV")?;
            for sample in &self.samples {
                writer
                    .write_sample((sample.clamp(-1.0, 1.0) * i16::MAX as f32) as i16)
                    .context("failed to encode diagnostic WAV sample")?;
            }
            writer
                .finalize()
                .context("failed to finalize diagnostic WAV")?;
        }
        Ok(Recording {
            status: Some(self.status()),
            wav,
        })
    }
}

fn simulated_audio(
    start_seconds: f64,
    sample_count: usize,
    beat_count: u64,
) -> (Vec<f32>, BeatEstimate) {
    let elapsed = start_seconds as f32;
    let tempo = 128.0;
    let interval = 60.0 / tempo;
    let phase = (elapsed % interval) / interval;
    let beat = phase < 0.06;
    let energy = 0.5 + 0.3 * (elapsed * 0.2).sin();
    let samples = (0..sample_count)
        .map(|index| {
            let time = (start_seconds + index as f64 / f64::from(ANALYSIS_RATE)) as f32;
            let amplitude = (0.12 + energy * 0.28 + (1.0 - phase) * 0.1).min(0.85);
            amplitude
                * (0.48 * (std::f32::consts::TAU * 90.0 * time).sin()
                    + 0.32 * (std::f32::consts::TAU * 440.0 * time).sin()
                    + 0.18 * (std::f32::consts::TAU * 1_800.0 * time).sin()
                    + 0.08 * (std::f32::consts::TAU * 6_200.0 * time).sin())
        })
        .collect();
    (
        samples,
        BeatEstimate {
            tempo,
            beat,
            downbeat: beat && beat_count.is_multiple_of(4),
            confidence: 0.9,
            beat_position: phase,
            bar_position: ((beat_count % 4) as f32 + phase) / 4.0,
            estimated_beat: beat_count,
            estimated_bar: beat_count / 4,
            beat_activation: if beat { 0.9 } else { 0.1 },
            downbeat_activation: if beat && beat_count.is_multiple_of(4) {
                0.9
            } else {
                0.05
            },
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn capture_handle_is_sendable_between_runtime_threads() {
        fn assert_send<T: Send>() {}
        assert_send::<AudioCapture>();
    }

    #[test]
    fn capture_drain_preserves_every_queued_sample_in_order() {
        let (sender, receiver) = mpsc::sync_channel(4);
        sender.send(vec![1.0, 2.0]).expect("first block queues");
        sender.send(vec![3.0]).expect("second block queues");
        sender.send(vec![4.0, 5.0]).expect("third block queues");

        assert_eq!(
            drain_queued_samples(&receiver),
            vec![1.0, 2.0, 3.0, 4.0, 5.0]
        );
    }

    #[test]
    fn capture_callback_reuses_preallocated_buffers() {
        let (sender, receiver) = mpsc::sync_channel(1);
        let (available, reusable) = mpsc::sync_channel(1);
        available
            .send(Vec::with_capacity(8))
            .expect("capture buffer should queue");
        let dropped = AtomicU64::new(0);

        send_mono(
            &[1.0_f32, 3.0, 5.0, 7.0],
            2,
            &sender,
            &reusable,
            &available,
            &dropped,
            |sample| sample,
        );

        assert_eq!(
            receiver.recv().expect("mono buffer should queue"),
            vec![2.0, 6.0]
        );
        assert_eq!(dropped.load(Ordering::Relaxed), 0);
    }

    #[test]
    fn capture_callback_counts_pool_exhaustion() {
        let (sender, receiver) = mpsc::sync_channel(1);
        let (available, reusable) = mpsc::sync_channel(1);
        let dropped = AtomicU64::new(0);

        send_mono(
            &[1.0_f32, 2.0],
            1,
            &sender,
            &reusable,
            &available,
            &dropped,
            |sample| sample,
        );

        assert!(receiver.try_recv().is_err());
        assert_eq!(dropped.load(Ordering::Relaxed), 1);
    }

    #[test]
    fn capture_callback_recycles_oversized_buffers() {
        let (sender, receiver) = mpsc::sync_channel(1);
        let (available, reusable) = mpsc::sync_channel(1);
        available
            .send(Vec::with_capacity(1))
            .expect("capture buffer should queue");
        let dropped = AtomicU64::new(0);

        send_mono(
            &[1.0_f32, 2.0],
            1,
            &sender,
            &reusable,
            &available,
            &dropped,
            |sample| sample,
        );

        assert!(receiver.try_recv().is_err());
        assert!(reusable.try_recv().is_ok());
        assert_eq!(dropped.load(Ordering::Relaxed), 1);
    }

    #[test]
    fn capture_callback_sanitizes_non_finite_samples() {
        let (sender, receiver) = mpsc::sync_channel(1);
        let (available, reusable) = mpsc::sync_channel(1);
        available
            .send(Vec::with_capacity(4))
            .expect("capture buffer should queue");
        let dropped = AtomicU64::new(0);

        send_mono(
            &[f32::NAN, f32::INFINITY, 0.5, -0.5],
            1,
            &sender,
            &reusable,
            &available,
            &dropped,
            |sample| sample,
        );

        assert_eq!(
            receiver.recv().expect("mono buffer should queue"),
            vec![0.0, 0.0, 0.5, -0.5]
        );
    }

    #[test]
    fn recording_at_the_duration_limit_remains_stoppable() {
        let mut recorder = Recorder::new(10);
        assert!(recorder.start());

        recorder.capture(&[0.25; 400]);

        let status = recorder.status();
        assert!(status.recording);
        assert!(status.limit_reached);
        assert_eq!(status.duration_seconds, MAX_RECORDING_SECONDS);
        assert!(
            !recorder.start(),
            "a full recording must not be overwritten"
        );
        let recording = recorder.stop().expect("full recording should encode");
        assert!(!recording.wav.is_empty());
        assert!(!recording.status.expect("recording status").recording);
    }

    #[test]
    fn simulated_recording_uses_the_audio_clock() {
        let config = AudioConfig {
            mode: AudioInputMode::Auto as i32,
            simulate: true,
            gain: 1.0,
            beatnet_model_path: String::new(),
            ..Default::default()
        };
        let mut worker = AudioWorkerState::new(config, AudioInputMode::Auto, true)
            .expect("simulated audio worker should start");
        assert!(worker.analyzer.start_recording());

        for tick in 1..=50 {
            worker.tick_simulated(Duration::from_millis(tick * 20));
        }

        let recording = worker
            .analyzer
            .stop_recording()
            .expect("recording should encode");
        approx::assert_abs_diff_eq!(
            recording.status.expect("recording status").duration_seconds,
            1.0,
            epsilon = 0.0001
        );
    }

    #[test]
    fn diagnostic_recording_is_a_valid_wav() {
        let mut recorder = Recorder::new(44_100);
        assert!(recorder.start());
        recorder.capture(&vec![0.25; 4_410]);
        let recording = recorder.stop().expect("recording should encode");
        assert!(recording.wav.starts_with(b"RIFF"));
        approx::assert_abs_diff_eq!(
            recording.status.expect("status").duration_seconds,
            0.1,
            epsilon = 0.001
        );
    }

    #[test]
    fn spectrogram_retains_five_seconds_at_ten_hertz() {
        let mut analyzer = AudioAnalyzer::new(44_100, 1.0, "");
        analyzer.spectrogram = (0..50).map(|_| vec![0.0; 64]).collect();
        analyzer.spectrogram.push_back(vec![1.0; 64]);
        if analyzer.spectrogram.len() > 50 {
            analyzer.spectrogram.pop_front();
        }
        assert_eq!(analyzer.spectrogram.len(), 50);
    }

    #[test]
    fn fft_visualization_power_matches_signal_power() {
        let amplitude = 0.2;
        let frequency_bin = 32.0;
        let samples: Vec<_> = (0..FFT_SIZE)
            .map(|index| {
                amplitude
                    * (std::f32::consts::TAU * frequency_bin * index as f32 / FFT_SIZE as f32).sin()
            })
            .collect();
        let fft = fft_power(&samples, FFT_SIZE);
        let measured_power = fft.iter().sum::<f32>() * one_sided_power_scale(FFT_SIZE);
        approx::assert_abs_diff_eq!(measured_power, amplitude * amplitude * 0.5, epsilon = 0.001);
    }

    #[test]
    fn typical_signal_does_not_saturate_visualizations() {
        let amplitude = 0.2;
        let frequency_bin = 32.0;
        let samples: Vec<_> = (0..FFT_SIZE)
            .map(|index| {
                amplitude
                    * (std::f32::consts::TAU * frequency_bin * index as f32 / FFT_SIZE as f32).sin()
            })
            .collect();
        let fft = fft_power(&samples, FFT_SIZE);
        let spectrum = spectrum(&fft, 48_000, FFT_SIZE, 1.0);
        let spectrogram = spectrogram_frame(&fft, 48_000, FFT_SIZE, 1.0);

        assert!(spectrum.iter().copied().fold(0.0, f32::max) < 1.0);
        assert!(spectrogram.iter().copied().fold(0.0, f32::max) < 1.0);
        assert!(spectrum.iter().any(|value| *value > 0.5));
        assert!(spectrogram.iter().any(|value| *value > 0.5));
    }

    #[test]
    fn waveform_keeps_signal_polarity() {
        let samples: Vec<_> = (0..100)
            .map(|index| if index < 50 { 0.15 } else { -0.15 })
            .collect();
        let waveform = waveform(&samples, 1.0);

        assert!(waveform.iter().any(|value| *value > 0.0));
        assert!(waveform.iter().any(|value| *value < 0.0));
    }
}
