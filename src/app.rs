use std::{
    path::PathBuf,
    sync::{
        Arc, Mutex as StdMutex, MutexGuard,
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Receiver, RecvTimeoutError, SyncSender, TrySendError},
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use anyhow::anyhow;
use thiserror::Error;
use tokio::{
    sync::{Mutex, oneshot, watch},
    task::JoinHandle,
};
use tokio_util::sync::CancellationToken;
use tracing::{error, info, warn};

use crate::{
    audio::{AudioWorker, AudioWorkerSnapshot, list_devices},
    bluetooth::BluetoothReceiver,
    config::{self, ConfigError, ValidatedShowConfig},
    dmx::{DmxWorker, frame_interval as dmx_frame_interval},
    effects::EffectsEngine,
    media::MediaState,
    proto::v1::{
        AudioRuntimeStatus, BeatNetStatus, BluetoothReceiverStatus, CommandResult, DmxConfig,
        DmxRuntimeStatus, MediaInfo, Recording, RecordingStatus, RunState, RuntimeTimingStatus,
        ShowCommand, ShowConfig, ShowSnapshot,
    },
    timing::PeriodicSchedule,
};

/// Idle snapshots only need periodic DMX health updates, not one update per frame.
const IDLE_DMX_STATUS_INTERVAL: Duration = Duration::from_secs(1);
/// Multiple zero frames make shutdown visible even if one USB transfer is lost.
const SHUTDOWN_BLACKOUT_FRAMES: usize = 3;
const RUNTIME_COMMAND_DEPTH: usize = 32;
const RECORDING_MONITOR_INTERVAL: Duration = Duration::from_millis(20);

#[derive(Debug, Error)]
pub enum AppError {
    #[error(transparent)]
    Config(#[from] ConfigError),
    #[error("{0}")]
    FailedPrecondition(String),
    #[error("{0:#}")]
    Runtime(#[source] anyhow::Error),
    #[error("show runtime is unavailable")]
    Unavailable,
    #[error("show runtime command queue is full")]
    ResourceExhausted,
}

impl From<anyhow::Error> for AppError {
    fn from(error: anyhow::Error) -> Self {
        Self::Runtime(error)
    }
}

struct Runtime {
    mode: RuntimeMode,
    // The dedicated worker owns serial timing so effect processing never waits
    // for a full DMX packet to drain over USB.
    dmx: DmxWorker,
    last_idle_dmx_publish: Instant,
    effects: EffectsEngine,
    last_effect_tick: Instant,
    frame_count: u64,
    fps_started: Instant,
    effects_deadlines_skipped: u64,
    audio_deadlines_skipped: u64,
    recoverable_audio_events: u64,
    last_effect_tick_ms: f32,
    max_effect_tick_ms: f32,
}

impl Runtime {
    fn new(dmx: DmxWorker) -> Self {
        Self {
            mode: RuntimeMode::Stopped,
            dmx,
            last_idle_dmx_publish: Instant::now(),
            effects: EffectsEngine::default(),
            last_effect_tick: Instant::now(),
            frame_count: 0,
            fps_started: Instant::now(),
            effects_deadlines_skipped: 0,
            audio_deadlines_skipped: 0,
            recoverable_audio_events: 0,
            last_effect_tick_ms: 0.0,
            max_effect_tick_ms: 0.0,
        }
    }
}

enum RuntimeMode {
    Stopped,
    Starting,
    Running(AudioWorker),
    Monitoring(AudioWorker),
    Stopping,
    Error,
}

impl RuntimeMode {
    fn run_state(&self) -> RunState {
        match self {
            Self::Stopped | Self::Monitoring(_) => RunState::Stopped,
            Self::Starting => RunState::Starting,
            Self::Running(_) => RunState::Running,
            Self::Stopping => RunState::Stopping,
            Self::Error => RunState::Error,
        }
    }
}

enum RuntimeCommand {
    UpdateConfig {
        config: Box<ShowConfig>,
        reply: oneshot::Sender<Result<ShowConfig, AppError>>,
    },
    Control {
        command: ShowCommand,
        reply: oneshot::Sender<Result<CommandResult, AppError>>,
    },
    SetBlackout {
        enabled: bool,
        reply: oneshot::Sender<Result<CommandResult, AppError>>,
    },
    StartRecording {
        reply: oneshot::Sender<Result<RecordingStatus, AppError>>,
    },
    StopRecording {
        reply: oneshot::Sender<Result<Recording, AppError>>,
    },
    ClearRecording {
        reply: oneshot::Sender<Result<RecordingStatus, AppError>>,
    },
    Shutdown,
}

pub(crate) struct PublishedState {
    pub(crate) config: Arc<ValidatedShowConfig>,
    pub(crate) snapshot: Arc<ShowSnapshot>,
}

pub struct App {
    config_path: PathBuf,
    state_tx: watch::Sender<Arc<PublishedState>>,
    media_tx: watch::Sender<Arc<MediaState>>,
    command_tx: SyncSender<RuntimeCommand>,
    command_rx: StdMutex<Option<Receiver<RuntimeCommand>>>,
    runtime_thread: StdMutex<Option<thread::JoinHandle<()>>>,
    media_task: Mutex<Option<JoinHandle<()>>>,
    bluetooth: BluetoothReceiver,
    shutdown: CancellationToken,
    cli_simulate: bool,
    command_queue_rejections: AtomicU64,
}

impl App {
    pub async fn load(config_path: PathBuf, simulate: bool) -> Result<Self, AppError> {
        let show_config = Arc::new(config::load(&config_path, simulate)?);
        let snapshot = Arc::new(stopped_snapshot(&show_config, simulate));
        let (state_tx, _) = watch::channel(Arc::new(PublishedState {
            config: show_config,
            snapshot,
        }));
        let (media_tx, _) = watch::channel(Arc::new(MediaState::default()));
        let (command_tx, command_rx) = mpsc::sync_channel(RUNTIME_COMMAND_DEPTH);
        Ok(Self {
            config_path,
            state_tx,
            media_tx,
            command_tx,
            command_rx: StdMutex::new(Some(command_rx)),
            runtime_thread: StdMutex::new(None),
            media_task: Mutex::new(None),
            bluetooth: BluetoothReceiver::new(),
            shutdown: CancellationToken::new(),
            cli_simulate: simulate,
            command_queue_rejections: AtomicU64::new(0),
        })
    }

    pub async fn start_runtime(self: &Arc<Self>) -> Result<(), AppError> {
        {
            let mut runtime_thread_slot = lock_unpoisoned(&self.runtime_thread);
            if runtime_thread_slot.is_some() {
                return Ok(());
            }
            let receiver = lock_unpoisoned(&self.command_rx)
                .take()
                .ok_or(AppError::Unavailable)?;
            let config = Arc::clone(&self.state_tx.borrow().config);
            let dmx = DmxWorker::start(effective_dmx_config(&config, self.cli_simulate))?;
            let app = Arc::clone(self);
            let runtime_thread = thread::Builder::new()
                .name("music-auto-show-runtime".into())
                .spawn(move || RuntimeLoop::new(app, dmx).run(receiver))
                .map_err(|error| {
                    AppError::Runtime(
                        anyhow::Error::new(error).context("failed to start show runtime"),
                    )
                })?;
            *runtime_thread_slot = Some(runtime_thread);
        }

        let mut media_task = self.media_task.lock().await;
        if media_task.is_none() {
            let media_tx = self.media_tx.clone();
            let shutdown = self.shutdown.clone();
            *media_task = Some(tokio::spawn(async move {
                crate::media::monitor(media_tx, shutdown).await;
            }));
        }
        Ok(())
    }

    pub async fn stop_runtime(&self) {
        self.shutdown.cancel();
        let runtime_thread = lock_unpoisoned(&self.runtime_thread).take();
        if let Some(runtime_thread) = runtime_thread {
            let _ = self.command_tx.try_send(RuntimeCommand::Shutdown);
            let joined = tokio::task::spawn_blocking(move || runtime_thread.join()).await;
            match joined {
                Ok(Ok(())) => {}
                Ok(Err(_)) => error!("show runtime thread panicked during shutdown"),
                Err(error) => error!(%error, "show runtime join task failed"),
            }
        }
        if let Some(task) = self.media_task.lock().await.take()
            && let Err(error) = task.await
        {
            error!(%error, "media monitor task did not stop cleanly");
        }
    }

    pub async fn snapshot(&self) -> ShowSnapshot {
        self.state_tx.borrow().snapshot.as_ref().clone()
    }

    pub(crate) fn subscribe(&self) -> watch::Receiver<Arc<PublishedState>> {
        self.state_tx.subscribe()
    }

    pub(crate) async fn wait_for_shutdown(&self) {
        self.shutdown.cancelled().await;
    }

    pub async fn config(&self) -> ShowConfig {
        self.state_tx.borrow().config.as_proto().clone()
    }

    pub async fn export_config(&self) -> Result<(String, String), AppError> {
        let config = Arc::clone(&self.state_tx.borrow().config);
        let json = config::to_json(&config)?;
        Ok((json, config_filename(&config.name)))
    }

    pub async fn import_config(&self, json: &str) -> Result<ShowConfig, AppError> {
        let imported = config::parse_json(json, self.cli_simulate)?;
        self.update_config(imported.into_proto()).await
    }

    pub async fn reset_config(&self) -> Result<ShowConfig, AppError> {
        self.update_config(config::default_show_config(self.cli_simulate))
            .await
    }

    pub async fn update_config(&self, config: ShowConfig) -> Result<ShowConfig, AppError> {
        let (reply, response) = oneshot::channel();
        self.send_command(RuntimeCommand::UpdateConfig {
            config: Box::new(config),
            reply,
        })?;
        response.await.map_err(|_| AppError::Unavailable)?
    }

    pub async fn control(&self, command: ShowCommand) -> Result<CommandResult, AppError> {
        let (reply, response) = oneshot::channel();
        self.send_command(RuntimeCommand::Control { command, reply })?;
        response.await.map_err(|_| AppError::Unavailable)?
    }

    pub async fn set_blackout(&self, enabled: bool) -> Result<CommandResult, AppError> {
        let (reply, response) = oneshot::channel();
        self.send_command(RuntimeCommand::SetBlackout { enabled, reply })?;
        response.await.map_err(|_| AppError::Unavailable)?
    }

    pub async fn start_recording(&self) -> Result<RecordingStatus, AppError> {
        let (reply, response) = oneshot::channel();
        self.send_command(RuntimeCommand::StartRecording { reply })?;
        response.await.map_err(|_| AppError::Unavailable)?
    }

    pub async fn stop_recording(&self) -> Result<Recording, AppError> {
        let (reply, response) = oneshot::channel();
        self.send_command(RuntimeCommand::StopRecording { reply })?;
        response.await.map_err(|_| AppError::Unavailable)?
    }

    pub async fn clear_recording(&self) -> Result<RecordingStatus, AppError> {
        let (reply, response) = oneshot::channel();
        self.send_command(RuntimeCommand::ClearRecording { reply })?;
        response.await.map_err(|_| AppError::Unavailable)?
    }

    pub async fn audio_devices(&self) -> Result<Vec<crate::proto::v1::AudioDevice>, AppError> {
        tokio::task::spawn_blocking(list_devices)
            .await
            .map_err(|error| {
                AppError::Runtime(anyhow!(error).context("audio device enumeration task failed"))
            })
    }

    pub async fn bluetooth_receiver_status(&self) -> BluetoothReceiverStatus {
        self.bluetooth.status().await
    }

    pub async fn set_bluetooth_receiver_pairing(
        &self,
        enabled: bool,
        timeout_seconds: u32,
    ) -> Result<BluetoothReceiverStatus, AppError> {
        self.bluetooth
            .set_pairing(enabled, timeout_seconds)
            .await
            .map_err(AppError::from)
    }

    pub async fn connect_bluetooth_receiver_device(
        &self,
        device_id: &str,
    ) -> Result<BluetoothReceiverStatus, AppError> {
        self.bluetooth
            .connect(device_id)
            .await
            .map_err(AppError::from)
    }

    pub async fn disconnect_bluetooth_receiver_device(
        &self,
        device_id: &str,
    ) -> Result<BluetoothReceiverStatus, AppError> {
        self.bluetooth
            .disconnect(device_id)
            .await
            .map_err(AppError::from)
    }

    pub async fn forget_bluetooth_receiver_device(
        &self,
        device_id: &str,
    ) -> Result<BluetoothReceiverStatus, AppError> {
        self.bluetooth
            .forget(device_id)
            .await
            .map_err(AppError::from)
    }

    pub async fn media_artwork(&self, revision: &str) -> Option<Arc<[u8]>> {
        self.media_tx.borrow().artwork(revision)
    }

    fn send_command(&self, command: RuntimeCommand) -> Result<(), AppError> {
        match self.command_tx.try_send(command) {
            Ok(()) => Ok(()),
            Err(TrySendError::Full(_)) => {
                self.command_queue_rejections
                    .fetch_add(1, Ordering::Relaxed);
                Err(AppError::ResourceExhausted)
            }
            Err(TrySendError::Disconnected(_)) => Err(AppError::Unavailable),
        }
    }
}

struct RuntimeLoop {
    app: Arc<App>,
    config: Arc<ValidatedShowConfig>,
    snapshot: ShowSnapshot,
    runtime: Runtime,
    publication_suspended: bool,
}

#[derive(Clone, Copy)]
struct ConfigChanges {
    audio: bool,
    dmx: bool,
    effects: bool,
}

impl ConfigChanges {
    fn between(previous: &ValidatedShowConfig, updated: &ValidatedShowConfig) -> Self {
        Self {
            audio: previous.audio() != updated.audio(),
            dmx: previous.dmx() != updated.dmx(),
            effects: previous.effects() != updated.effects()
                || previous.profiles != updated.profiles
                || previous.fixtures != updated.fixtures,
        }
    }
}

impl RuntimeLoop {
    fn new(app: Arc<App>, dmx: DmxWorker) -> Self {
        let state = app.state_tx.borrow().clone();
        let config = Arc::clone(&state.config);
        let mut snapshot = state.snapshot.as_ref().clone();
        snapshot.dmx_runtime = Some(dmx.status());
        Self {
            app,
            config,
            snapshot,
            runtime: Runtime::new(dmx),
            publication_suspended: false,
        }
    }

    fn run(mut self, receiver: Receiver<RuntimeCommand>) {
        let mut schedule = PeriodicSchedule::immediate(self.frame_interval(), Instant::now());
        loop {
            let received = receiver.recv_timeout(schedule.remaining(Instant::now()));
            if self.app.shutdown.is_cancelled() {
                self.stop_show();
                self.shutdown_dmx();
                return;
            }
            match received {
                Ok(RuntimeCommand::Shutdown) => {
                    self.stop_show();
                    self.shutdown_dmx();
                    return;
                }
                Ok(command) => self.handle_command(command),
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => {
                    self.stop_show();
                    self.shutdown_dmx();
                    return;
                }
            }

            let frame_interval = self.frame_interval();
            if schedule.period() != frame_interval {
                schedule.reset(frame_interval, Instant::now());
            }
            if schedule.is_due(Instant::now()) {
                if let Err(error) = self.tick() {
                    error!(%error, "show frame failed");
                    self.fail_show(error.to_string());
                }
                self.runtime.effects_deadlines_skipped = self
                    .runtime
                    .effects_deadlines_skipped
                    .saturating_add(schedule.advance(Instant::now()));
            }
        }
    }

    fn handle_command(&mut self, command: RuntimeCommand) {
        match command {
            RuntimeCommand::UpdateConfig { config, reply } => {
                let _ = reply.send(self.update_config(*config));
            }
            RuntimeCommand::Control { command, reply } => {
                let _ = reply.send(self.control(command));
            }
            RuntimeCommand::SetBlackout { enabled, reply } => {
                let _ = reply.send(Ok(self.set_blackout(enabled)));
            }
            RuntimeCommand::StartRecording { reply } => {
                let _ = reply.send(self.start_recording());
            }
            RuntimeCommand::StopRecording { reply } => {
                let _ = reply.send(self.stop_recording());
            }
            RuntimeCommand::ClearRecording { reply } => {
                let _ = reply.send(self.clear_recording());
            }
            RuntimeCommand::Shutdown => {}
        }
    }

    fn frame_interval(&self) -> Duration {
        if matches!(&self.runtime.mode, RuntimeMode::Monitoring(_)) {
            RECORDING_MONITOR_INTERVAL
        } else {
            dmx_frame_interval(self.config.dmx().fps)
        }
    }

    fn update_config(&mut self, updated: ShowConfig) -> Result<ShowConfig, AppError> {
        if self
            .snapshot
            .recording
            .as_ref()
            .is_some_and(|recording| recording.recording)
        {
            return Err(AppError::FailedPrecondition(
                "stop the audio recording before updating the configuration".into(),
            ));
        }
        let updated = Arc::new(ValidatedShowConfig::new(updated, self.app.cli_simulate)?);
        let previous = Arc::clone(&self.config);
        let changes = ConfigChanges::between(&previous, &updated);
        let was_running = matches!(&self.runtime.mode, RuntimeMode::Running(_));
        self.publication_suspended = true;
        self.config = Arc::clone(&updated);

        let apply = (|| -> Result<(), AppError> {
            if changes.dmx {
                // Reconfiguration is acknowledged only after the old port has
                // sent its blackout sequence and adopted the new config.
                self.runtime.dmx.reconfigure(
                    effective_dmx_config(&updated, self.app.cli_simulate),
                    SHUTDOWN_BLACKOUT_FRAMES,
                )?;
            }
            if was_running && changes.audio {
                self.replace_running_audio()?;
            }
            if changes.audio || changes.effects {
                self.runtime.effects = EffectsEngine::default();
            }
            config::save(&self.app.config_path, &updated)?;
            if was_running {
                self.tick()?;
            }
            Ok(())
        })();
        if let Err(error) = apply {
            return Err(self.rollback_config(previous, was_running, changes, error));
        }

        self.snapshot.config_generation = self.snapshot.config_generation.saturating_add(1);
        if !was_running {
            reset_inactive_runtime_snapshot(
                &mut self.snapshot,
                &self.config,
                self.app.cli_simulate,
            );
        }
        self.publication_suspended = false;
        self.publish();
        Ok(self.config.as_proto().clone())
    }

    fn rollback_config(
        &mut self,
        previous: Arc<ValidatedShowConfig>,
        was_running: bool,
        changes: ConfigChanges,
        original: AppError,
    ) -> AppError {
        self.config = Arc::clone(&previous);
        let rollback = (|| -> Result<(), AppError> {
            if changes.dmx {
                self.runtime.dmx.reconfigure(
                    effective_dmx_config(&previous, self.app.cli_simulate),
                    SHUTDOWN_BLACKOUT_FRAMES,
                )?;
            }
            if was_running && changes.audio {
                self.replace_running_audio()?;
            }
            if changes.audio || changes.effects {
                self.runtime.effects = EffectsEngine::default();
            }
            Ok(())
        })();
        if let Err(error) = config::save(&self.app.config_path, &previous) {
            warn!(%error, "could not restore the previous persisted configuration");
        }
        self.publication_suspended = false;

        match rollback {
            Ok(()) => {
                if was_running {
                    self.snapshot.run_state = RunState::Running as i32;
                    self.snapshot.status_message = if self.snapshot.blackout {
                        "Blackout active".into()
                    } else {
                        "Show running".into()
                    };
                } else {
                    reset_inactive_runtime_snapshot(
                        &mut self.snapshot,
                        &self.config,
                        self.app.cli_simulate,
                    );
                }
                self.publish();
                original
            }
            Err(rollback) => {
                self.fail_show(format!(
                    "configuration update failed: {original}; rollback also failed: {rollback}"
                ));
                AppError::Runtime(anyhow!(
                    "configuration update failed: {original}; rollback also failed: {rollback}"
                ))
            }
        }
    }

    fn replace_running_audio(&mut self) -> Result<(), AppError> {
        let previous = std::mem::replace(&mut self.runtime.mode, RuntimeMode::Starting);
        drop(previous);
        let audio = AudioWorker::start(
            self.config.audio(),
            self.config.audio_mode(),
            self.app.cli_simulate,
        )
        .map_err(AppError::from)?;
        self.runtime.mode = RuntimeMode::Running(audio);
        self.runtime.frame_count = 0;
        self.runtime.fps_started = Instant::now();
        let now = Instant::now();
        self.runtime.last_effect_tick = now.checked_sub(self.frame_interval()).unwrap_or(now);
        Ok(())
    }

    fn control(&mut self, command: ShowCommand) -> Result<CommandResult, AppError> {
        match command {
            ShowCommand::Start => self.start_show(),
            ShowCommand::Stop => {
                self.stop_show();
                Ok(self.command_result(true, "Show stopped"))
            }
            ShowCommand::Unspecified => Err(AppError::FailedPrecondition(
                "show command is required".into(),
            )),
        }
    }

    fn start_show(&mut self) -> Result<CommandResult, AppError> {
        match &self.runtime.mode {
            RuntimeMode::Running(_) => {
                return Ok(self.command_result(true, "Show is already running"));
            }
            RuntimeMode::Monitoring(_) => {
                return Err(AppError::FailedPrecondition(
                    "stop the audio recording before starting the show".into(),
                ));
            }
            RuntimeMode::Starting | RuntimeMode::Stopping => {
                return Err(AppError::FailedPrecondition(
                    "show runtime is transitioning".into(),
                ));
            }
            RuntimeMode::Stopped | RuntimeMode::Error => {}
        }
        self.runtime.mode = RuntimeMode::Starting;
        self.publish_mode("Starting audio and effects");
        let audio = match AudioWorker::start(
            self.config.audio(),
            self.config.audio_mode(),
            self.app.cli_simulate,
        ) {
            Ok(audio) => audio,
            Err(error) => {
                self.fail_show(error.to_string());
                return Err(error.into());
            }
        };
        let simulate_audio = audio.snapshot().runtime.simulated;
        self.runtime.mode = RuntimeMode::Running(audio);
        self.runtime.effects = EffectsEngine::default();
        self.runtime.frame_count = 0;
        self.runtime.fps_started = Instant::now();
        let now = Instant::now();
        self.runtime.last_effect_tick = now.checked_sub(self.frame_interval()).unwrap_or(now);
        self.publish_mode("Show running");
        info!(
            simulate_audio,
            simulate_dmx = self.runtime.dmx.status().simulated,
            "show started"
        );
        Ok(self.command_result(true, "Show started"))
    }

    fn stop_show(&mut self) {
        if matches!(&self.runtime.mode, RuntimeMode::Stopped) {
            return;
        }
        let previous = std::mem::replace(&mut self.runtime.mode, RuntimeMode::Stopping);
        self.publish_mode("Stopping show");
        drop(previous);
        self.runtime.dmx.blackout();
        self.runtime.mode = RuntimeMode::Stopped;
        self.snapshot.run_state = RunState::Stopped as i32;
        self.snapshot.status_message = "Stopped".into();
        self.snapshot.recording = Some(stopped_recording_status());
        reset_inactive_runtime_snapshot(&mut self.snapshot, &self.config, self.app.cli_simulate);
        self.snapshot.dmx_runtime = Some(self.runtime.dmx.status());
        self.publish();
    }

    fn set_blackout(&mut self, enabled: bool) -> CommandResult {
        self.snapshot.blackout = enabled;
        if enabled {
            self.runtime.dmx.blackout();
            self.snapshot.dmx_universe = self.zero_universe();
            self.snapshot.dmx_runtime = Some(self.runtime.dmx.status());
        }
        self.snapshot.status_message = if enabled {
            "Blackout active"
        } else if self.snapshot.run_state == RunState::Running as i32 {
            "Show running"
        } else {
            "Stopped"
        }
        .into();
        self.publish();
        CommandResult {
            success: true,
            message: self.snapshot.status_message.clone(),
            run_state: self.snapshot.run_state,
            blackout: enabled,
        }
    }

    fn start_recording(&mut self) -> Result<RecordingStatus, AppError> {
        let status = match &self.runtime.mode {
            RuntimeMode::Running(audio) | RuntimeMode::Monitoring(audio) => {
                audio.start_recording()?
            }
            RuntimeMode::Stopped | RuntimeMode::Error => {
                let audio = AudioWorker::start(
                    self.config.audio(),
                    self.config.audio_mode(),
                    self.app.cli_simulate,
                )?;
                let status = audio.start_recording()?;
                self.runtime.mode = RuntimeMode::Monitoring(audio);
                self.snapshot.status_message = "Recording input check".into();
                status
            }
            RuntimeMode::Starting | RuntimeMode::Stopping => {
                return Err(AppError::FailedPrecondition(
                    "show runtime is transitioning".into(),
                ));
            }
        };
        self.snapshot.recording = Some(status.clone());
        self.publish();
        Ok(status)
    }

    fn stop_recording(&mut self) -> Result<Recording, AppError> {
        let stopped_monitor = matches!(&self.runtime.mode, RuntimeMode::Monitoring(_));
        let recording = match &self.runtime.mode {
            RuntimeMode::Running(audio) | RuntimeMode::Monitoring(audio) => {
                audio.stop_recording()?
            }
            _ => return Err(anyhow!("audio input is not running").into()),
        };
        if stopped_monitor {
            let monitor = std::mem::replace(&mut self.runtime.mode, RuntimeMode::Stopped);
            drop(monitor);
        }
        if let Some(status) = recording.status.clone() {
            self.snapshot.recording = Some(status);
            if stopped_monitor {
                self.snapshot.status_message = "Stopped".into();
                reset_inactive_runtime_snapshot(
                    &mut self.snapshot,
                    &self.config,
                    self.app.cli_simulate,
                );
            }
            self.publish();
        }
        Ok(recording)
    }

    fn clear_recording(&mut self) -> Result<RecordingStatus, AppError> {
        let stopped_monitor = matches!(&self.runtime.mode, RuntimeMode::Monitoring(_));
        let status = match &self.runtime.mode {
            RuntimeMode::Running(audio) | RuntimeMode::Monitoring(audio) => {
                audio.clear_recording()?
            }
            _ => return Err(anyhow!("audio input is not running").into()),
        };
        if stopped_monitor {
            let monitor = std::mem::replace(&mut self.runtime.mode, RuntimeMode::Stopped);
            drop(monitor);
        }
        self.snapshot.recording = Some(status.clone());
        if stopped_monitor {
            self.snapshot.status_message = "Stopped".into();
            reset_inactive_runtime_snapshot(
                &mut self.snapshot,
                &self.config,
                self.app.cli_simulate,
            );
        }
        self.publish();
        Ok(status)
    }

    fn tick(&mut self) -> Result<(), AppError> {
        let started = Instant::now();
        let publish_after_tick = matches!(
            &self.runtime.mode,
            RuntimeMode::Running(_) | RuntimeMode::Monitoring(_)
        );
        let result = self.tick_frame();
        let elapsed_ms = started.elapsed().as_secs_f32() * 1_000.0;
        self.runtime.last_effect_tick_ms = elapsed_ms;
        self.runtime.max_effect_tick_ms = self.runtime.max_effect_tick_ms.max(elapsed_ms);
        if result.is_ok() && publish_after_tick {
            self.publish();
        }
        result
    }

    fn tick_frame(&mut self) -> Result<(), AppError> {
        let running_audio = match &self.runtime.mode {
            RuntimeMode::Running(audio) => Some(audio.snapshot()),
            _ => None,
        };
        if let Some(audio) = running_audio {
            return self.tick_show(audio);
        }

        let monitoring_audio = match &self.runtime.mode {
            RuntimeMode::Monitoring(audio) => Some(audio.snapshot()),
            _ => None,
        };
        let universe = self.zero_universe();
        self.runtime.dmx.set_universe(&universe)?;
        if let Some(audio) = monitoring_audio {
            return self.tick_recording_monitor(audio);
        }
        self.snapshot.dmx_universe = universe;
        if self.runtime.last_idle_dmx_publish.elapsed() >= IDLE_DMX_STATUS_INTERVAL {
            self.snapshot.dmx_runtime = Some(self.runtime.dmx.status());
            self.runtime.last_idle_dmx_publish = Instant::now();
            self.publish();
        }
        Ok(())
    }

    fn tick_show(&mut self, audio_frame: AudioWorkerSnapshot) -> Result<(), AppError> {
        let blackout = self.snapshot.blackout;
        let previous_effects_fps = self.snapshot.effects_fps;
        let detected_media = self.app.media_tx.borrow().info().clone();
        let media = if audio_frame.runtime.simulated {
            MediaInfo {
                track_name: "Simulated Audio".into(),
                is_playing: true,
                ..Default::default()
            }
        } else if detected_media.track_name.is_empty() {
            MediaInfo {
                track_name: "System Audio".into(),
                is_playing: true,
                ..Default::default()
            }
        } else {
            detected_media
        };
        let now = Instant::now();
        let delta = now.saturating_duration_since(self.runtime.last_effect_tick);
        self.runtime.last_effect_tick = now;
        let output = self.runtime.effects.process(
            &self.config,
            &audio_frame.analysis,
            &media.album_colors,
            blackout,
            delta,
        );
        self.runtime.dmx.set_universe(&output.universe)?;
        self.runtime.frame_count += 1;
        let elapsed = self.runtime.fps_started.elapsed().as_secs_f32();
        let effects_fps = if elapsed >= 1.0 {
            let fps = self.runtime.frame_count as f32 / elapsed;
            self.runtime.frame_count = 0;
            self.runtime.fps_started = Instant::now();
            fps
        } else {
            previous_effects_fps
        };
        self.snapshot.status_message = if blackout {
            "Blackout active"
        } else {
            "Show running"
        }
        .into();
        self.snapshot.audio = Some(audio_frame.analysis);
        self.snapshot.audio_runtime = Some(audio_frame.runtime);
        self.snapshot.dmx_runtime = Some(self.runtime.dmx.status());
        self.snapshot.recording = Some(audio_frame.recording);
        self.snapshot.beatnet = Some(audio_frame.beatnet);
        self.snapshot.media = Some(media);
        self.snapshot.fixture_states = output.fixture_states;
        self.snapshot.dmx_universe = output.universe;
        self.snapshot.effects_fps = effects_fps;
        self.runtime.audio_deadlines_skipped = audio_frame.analysis_deadlines_skipped;
        self.runtime.recoverable_audio_events = audio_frame.recoverable_stream_events;
        Ok(())
    }

    fn tick_recording_monitor(&mut self, audio: AudioWorkerSnapshot) -> Result<(), AppError> {
        self.snapshot.status_message = "Recording input check".into();
        self.snapshot.audio = Some(audio.analysis);
        self.snapshot.audio_runtime = Some(audio.runtime);
        self.snapshot.recording = Some(audio.recording);
        self.snapshot.beatnet = Some(audio.beatnet);
        self.snapshot.media = Some(self.app.media_tx.borrow().info().clone());
        self.snapshot.dmx_runtime = Some(self.runtime.dmx.status());
        self.snapshot.dmx_universe = self.zero_universe();
        self.runtime.audio_deadlines_skipped = audio.analysis_deadlines_skipped;
        self.runtime.recoverable_audio_events = audio.recoverable_stream_events;
        Ok(())
    }

    fn zero_universe(&self) -> Vec<u8> {
        vec![0; self.config.dmx().universe_size as usize]
    }

    fn shutdown_dmx(&mut self) {
        if let Err(error) = self.runtime.dmx.shutdown(SHUTDOWN_BLACKOUT_FRAMES) {
            warn!(%error, "DMX output did not stop cleanly");
        }
    }

    fn publish_mode(&mut self, message: &str) {
        self.snapshot.run_state = self.runtime.mode.run_state() as i32;
        self.snapshot.status_message = message.into();
        self.publish();
    }

    fn fail_show(&mut self, message: String) {
        self.runtime.dmx.blackout();
        let previous = std::mem::replace(&mut self.runtime.mode, RuntimeMode::Error);
        drop(previous);
        self.snapshot.run_state = RunState::Error as i32;
        self.snapshot.status_message = message;
        self.snapshot.recording = Some(stopped_recording_status());
        reset_inactive_runtime_snapshot(&mut self.snapshot, &self.config, self.app.cli_simulate);
        self.snapshot.dmx_runtime = Some(self.runtime.dmx.status());
        self.publish();
    }

    fn command_result(&self, success: bool, message: &str) -> CommandResult {
        CommandResult {
            success,
            message: message.into(),
            run_state: self.snapshot.run_state,
            blackout: self.snapshot.blackout,
        }
    }

    fn publish(&mut self) {
        if self.publication_suspended {
            return;
        }
        self.snapshot.sequence += 1;
        self.snapshot.captured_at_unix_ms = unix_millis();
        self.snapshot.timing = Some(RuntimeTimingStatus {
            effects_deadlines_skipped: self.runtime.effects_deadlines_skipped,
            audio_deadlines_skipped: self.runtime.audio_deadlines_skipped,
            dmx_deadlines_skipped: self.runtime.dmx.deadlines_skipped(),
            command_queue_rejections: self.app.command_queue_rejections.load(Ordering::Relaxed),
            recoverable_audio_events: self.runtime.recoverable_audio_events,
            last_effect_tick_ms: self.runtime.last_effect_tick_ms,
            max_effect_tick_ms: self.runtime.max_effect_tick_ms,
        });
        self.app.state_tx.send_replace(Arc::new(PublishedState {
            config: Arc::clone(&self.config),
            snapshot: Arc::new(self.snapshot.clone()),
        }));
    }
}

fn lock_unpoisoned<T>(mutex: &StdMutex<T>) -> MutexGuard<'_, T> {
    match mutex.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    }
}

fn config_filename(name: &str) -> String {
    let stem = name
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() {
                character.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect::<String>()
        .split('-')
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("-");
    format!("{}.json", if stem.is_empty() { "show" } else { &stem })
}

fn stopped_snapshot(config: &ValidatedShowConfig, simulate: bool) -> ShowSnapshot {
    let mut snapshot = ShowSnapshot {
        captured_at_unix_ms: unix_millis(),
        run_state: RunState::Stopped as i32,
        status_message: "Stopped".into(),
        recording: Some(stopped_recording_status()),
        dmx_runtime: Some(stopped_dmx_status(config, simulate)),
        config_generation: 1,
        ..Default::default()
    };
    reset_inactive_runtime_snapshot(&mut snapshot, config, simulate);
    snapshot
}

fn reset_inactive_runtime_snapshot(
    snapshot: &mut ShowSnapshot,
    config: &ValidatedShowConfig,
    simulate: bool,
) {
    snapshot.audio = Some(Default::default());
    snapshot.audio_runtime = Some(stopped_audio_status(config, simulate));
    snapshot.beatnet = Some(stopped_beatnet_status(config));
    snapshot.media = Some(MediaInfo {
        track_name: "No track".into(),
        ..Default::default()
    });
    snapshot.fixture_states.clear();
    snapshot.dmx_universe = vec![0; config.dmx().universe_size as usize];
    snapshot.effects_fps = 0.0;
}

fn stopped_recording_status() -> RecordingStatus {
    RecordingStatus {
        max_duration_seconds: 30.0,
        ..Default::default()
    }
}

fn stopped_beatnet_status(config: &ValidatedShowConfig) -> BeatNetStatus {
    BeatNetStatus {
        model_name: "BeatNet+".into(),
        model_path: config.audio().beatnet_model_path.clone(),
        status: "Idle".into(),
        ..Default::default()
    }
}

fn stopped_audio_status(config: &ValidatedShowConfig, simulate: bool) -> AudioRuntimeStatus {
    let audio = config.audio();
    AudioRuntimeStatus {
        configured_mode: audio.mode,
        configured_device_id: audio.device_id.clone(),
        selection_reason: "not_started".into(),
        simulated: simulate || audio.simulate,
        ..Default::default()
    }
}

fn stopped_dmx_status(config: &ValidatedShowConfig, simulate: bool) -> DmxRuntimeStatus {
    DmxRuntimeStatus {
        configured_port: config.dmx().port.clone(),
        simulated: simulate || config.dmx().simulate,
        ..Default::default()
    }
}

fn effective_dmx_config(config: &ValidatedShowConfig, simulate: bool) -> DmxConfig {
    // CLI simulation is an override for the process lifetime and must not be
    // persisted into the user's saved hardware configuration.
    let mut dmx = config.dmx().clone();
    dmx.simulate |= simulate;
    dmx
}

fn unix_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn wait_for_snapshot(
        app: &App,
        condition: impl Fn(&ShowSnapshot) -> bool,
    ) -> ShowSnapshot {
        let mut snapshots = app.subscribe();
        tokio::time::timeout(Duration::from_secs(3), async move {
            loop {
                let snapshot = snapshots.borrow().snapshot.as_ref().clone();
                if condition(&snapshot) {
                    return snapshot;
                }
                snapshots
                    .changed()
                    .await
                    .expect("snapshot stream should remain available");
            }
        })
        .await
        .expect("expected runtime snapshot was not published")
    }

    #[test]
    fn config_filename_is_safe_and_readable() {
        assert_eq!(config_filename("Friday Night #1"), "friday-night-1.json");
        assert_eq!(config_filename("***"), "show.json");
    }

    #[test]
    fn dmx_frame_interval_uses_the_configured_rate() {
        assert_eq!(dmx_frame_interval(20), Duration::from_millis(50));
        assert_eq!(dmx_frame_interval(40), Duration::from_millis(25));
    }

    #[test]
    fn config_changes_restart_only_the_affected_subsystems() {
        let previous = ValidatedShowConfig::new(config::default_show_config(true), true)
            .expect("default configuration should validate");
        let mut updated = previous.as_proto().clone();
        updated.effects.as_mut().expect("effects config").intensity = 0.8;
        let updated =
            ValidatedShowConfig::new(updated, true).expect("updated configuration should validate");

        let changes = ConfigChanges::between(&previous, &updated);
        assert!(!changes.audio);
        assert!(!changes.dmx);
        assert!(changes.effects);
    }

    #[test]
    fn inactive_snapshot_clears_live_subsystem_values() {
        let config = ValidatedShowConfig::new(config::default_show_config(true), true)
            .expect("default configuration should validate");
        let mut snapshot = stopped_snapshot(&config, true);
        snapshot.audio.as_mut().expect("audio snapshot").tempo = 128.0;
        let beatnet = snapshot.beatnet.as_mut().expect("BeatNet snapshot");
        beatnet.available = true;
        beatnet.processing = true;
        beatnet.last_error = "stale error".into();
        snapshot.media.as_mut().expect("media snapshot").track_name = "Old track".into();
        snapshot.fixture_states.push(Default::default());
        snapshot.dmx_universe[0] = 255;
        snapshot.effects_fps = 40.0;
        snapshot.dmx_runtime = Some(DmxRuntimeStatus {
            running: true,
            is_open: true,
            send_count: 17,
            ..Default::default()
        });

        reset_inactive_runtime_snapshot(&mut snapshot, &config, true);

        assert_eq!(snapshot.audio.expect("audio snapshot").tempo, 0.0);
        let beatnet = snapshot.beatnet.expect("BeatNet snapshot");
        assert!(!beatnet.available);
        assert!(!beatnet.processing);
        assert_eq!(beatnet.status, "Idle");
        assert!(beatnet.last_error.is_empty());
        assert_eq!(beatnet.model_path, "models/beatnet-plus.pt");
        assert_eq!(
            snapshot.media.expect("media snapshot").track_name,
            "No track"
        );
        assert!(snapshot.fixture_states.is_empty());
        assert!(snapshot.dmx_universe.iter().all(|value| *value == 0));
        assert_eq!(snapshot.effects_fps, 0.0);
        let dmx = snapshot.dmx_runtime.expect("DMX runtime snapshot");
        assert!(dmx.running);
        assert!(dmx.is_open);
        assert_eq!(dmx.send_count, 17);
    }

    #[tokio::test]
    async fn runtime_command_queue_applies_backpressure() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let app = App::load(directory.path().join("config.json"), true)
            .await
            .expect("simulated application should load");

        for _ in 0..RUNTIME_COMMAND_DEPTH {
            let (reply, _response) = oneshot::channel();
            app.send_command(RuntimeCommand::SetBlackout {
                enabled: false,
                reply,
            })
            .expect("queue should accept its bounded capacity");
        }
        let (reply, _response) = oneshot::channel();
        let error = app
            .send_command(RuntimeCommand::SetBlackout {
                enabled: false,
                reply,
            })
            .expect_err("queue should reject overload");

        assert!(matches!(error, AppError::ResourceExhausted));
        assert_eq!(app.command_queue_rejections.load(Ordering::Relaxed), 1);
    }

    #[tokio::test]
    async fn dmx_sends_zero_universes_while_the_show_is_stopped() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let app = Arc::new(
            App::load(directory.path().join("config.json"), true)
                .await
                .expect("simulated application should load"),
        );
        app.start_runtime()
            .await
            .expect("show runtime should start");

        let first = wait_for_snapshot(&app, |snapshot| {
            snapshot
                .dmx_runtime
                .as_ref()
                .is_some_and(|dmx| dmx.is_open && dmx.send_count >= 2)
        })
        .await;
        let first_send_count = first.dmx_runtime.expect("DMX runtime snapshot").send_count;
        assert_eq!(first.run_state, RunState::Stopped as i32);
        assert!(first.dmx_universe.iter().all(|value| *value == 0));

        let later = wait_for_snapshot(&app, |snapshot| {
            snapshot
                .dmx_runtime
                .as_ref()
                .is_some_and(|dmx| dmx.send_count > first_send_count)
        })
        .await;
        assert_eq!(later.run_state, RunState::Stopped as i32);
        assert!(later.dmx_universe.iter().all(|value| *value == 0));
        app.stop_runtime().await;
    }

    #[tokio::test]
    async fn concurrent_start_requests_share_one_runtime() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let app = Arc::new(
            App::load(directory.path().join("config.json"), true)
                .await
                .expect("simulated application should load"),
        );
        app.start_runtime()
            .await
            .expect("show runtime should start");

        let (first, second) = tokio::join!(
            app.control(ShowCommand::Start),
            app.control(ShowCommand::Start)
        );

        assert!(first.expect("first start should succeed").success);
        assert!(second.expect("second start should succeed").success);
        assert_eq!(app.snapshot().await.run_state, RunState::Running as i32);
        app.stop_runtime().await;
    }

    #[tokio::test]
    async fn stopped_recording_monitor_uses_its_own_audio_clock() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let app = Arc::new(
            App::load(directory.path().join("config.json"), true)
                .await
                .expect("simulated application should load"),
        );
        app.start_runtime()
            .await
            .expect("show runtime should start");
        app.start_recording().await.expect("recording should start");

        let recording = wait_for_snapshot(&app, |snapshot| {
            snapshot
                .recording
                .as_ref()
                .is_some_and(|recording| recording.duration_seconds >= 0.08)
        })
        .await;

        assert_eq!(recording.run_state, RunState::Stopped as i32);
        let result = app.stop_recording().await.expect("recording should stop");
        assert!(
            result
                .status
                .is_some_and(|recording| recording.duration_seconds >= 0.08)
        );
        app.stop_runtime().await;
    }

    #[tokio::test]
    async fn failed_config_persistence_restores_the_running_show() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let blocked_parent = directory.path().join("not-a-directory");
        std::fs::write(&blocked_parent, b"file").expect("blocking file should be written");
        let app = Arc::new(
            App::load(blocked_parent.join("config.json"), true)
                .await
                .expect("default configuration should load"),
        );
        app.start_runtime()
            .await
            .expect("show runtime should start");
        app.control(ShowCommand::Start)
            .await
            .expect("show should start");
        let original = app.config().await;
        let mut updated = original.clone();
        updated.name = "Configuration that cannot persist".into();

        app.update_config(updated)
            .await
            .expect_err("configuration persistence should fail");

        assert_eq!(app.config().await.name, original.name);
        assert_eq!(app.snapshot().await.run_state, RunState::Running as i32);
        app.stop_runtime().await;
    }

    #[tokio::test]
    async fn stopping_the_show_keeps_dmx_connected_and_sending_zeros() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let app = Arc::new(
            App::load(directory.path().join("config.json"), true)
                .await
                .expect("simulated application should load"),
        );
        app.start_runtime()
            .await
            .expect("show runtime should start");
        wait_for_snapshot(&app, |snapshot| {
            snapshot
                .dmx_runtime
                .as_ref()
                .is_some_and(|dmx| dmx.is_open && dmx.send_count > 0)
        })
        .await;
        app.control(ShowCommand::Start)
            .await
            .expect("show should start");
        app.control(ShowCommand::Stop)
            .await
            .expect("show should stop");

        let stopped = app.snapshot().await;
        let stopped_dmx = stopped.dmx_runtime.expect("DMX runtime snapshot");
        assert_eq!(stopped.run_state, RunState::Stopped as i32);
        assert!(stopped_dmx.running);
        assert!(stopped_dmx.is_open);
        assert!(stopped.dmx_universe.iter().all(|value| *value == 0));

        let later = wait_for_snapshot(&app, |snapshot| {
            snapshot.run_state == RunState::Stopped as i32
                && snapshot
                    .dmx_runtime
                    .as_ref()
                    .is_some_and(|dmx| dmx.send_count > stopped_dmx.send_count)
        })
        .await;
        assert!(later.dmx_universe.iter().all(|value| *value == 0));
        app.stop_runtime().await;
    }

    #[tokio::test]
    async fn missing_dmx_does_not_prevent_the_show_from_starting() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let config_path = directory.path().join("config.json");
        let mut show_config = config::default_show_config(false);
        let dmx = show_config.dmx.as_mut().expect("DMX configuration");
        dmx.port = directory
            .path()
            .join("missing-dmx-device")
            .display()
            .to_string();
        dmx.simulate = false;
        show_config
            .audio
            .as_mut()
            .expect("audio configuration")
            .simulate = true;
        let config = ValidatedShowConfig::new(show_config, false)
            .expect("test configuration should validate");
        config::save(&config_path, &config).expect("test configuration should save");
        let app = Arc::new(
            App::load(config_path, false)
                .await
                .expect("application should load"),
        );
        app.start_runtime()
            .await
            .expect("show runtime should start");

        let unavailable = wait_for_snapshot(&app, |snapshot| {
            snapshot
                .dmx_runtime
                .as_ref()
                .is_some_and(|dmx| !dmx.last_error.is_empty())
        })
        .await;
        let unavailable_dmx = unavailable.dmx_runtime.expect("DMX runtime snapshot");
        assert!(unavailable_dmx.running);
        assert!(!unavailable_dmx.is_open);
        assert!(
            unavailable_dmx
                .last_error
                .contains("failed to open DMX interface")
        );

        let result = app
            .control(ShowCommand::Start)
            .await
            .expect("show should start while DMX reconnects");

        assert!(result.success);
        let snapshot = app.snapshot().await;
        assert_eq!(snapshot.run_state, RunState::Running as i32);
        assert!(snapshot.dmx_runtime.expect("DMX runtime snapshot").running);
        app.stop_runtime().await;
    }
}
