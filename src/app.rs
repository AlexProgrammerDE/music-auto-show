use std::{
    path::PathBuf,
    sync::{
        Arc, Mutex as StdMutex, MutexGuard,
        mpsc::{self, Receiver, RecvTimeoutError, Sender},
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, anyhow};
use thiserror::Error;
use tokio::{
    sync::{Mutex, oneshot, watch},
    task::JoinHandle,
};
use tokio_util::sync::CancellationToken;
use tracing::{error, info, warn};

use crate::{
    audio::{AudioAnalyzer, AudioCapture, list_devices, simulated_audio, start_capture},
    config::{self, ConfigError, ValidatedShowConfig},
    dmx::DmxOutput,
    effects::EffectsEngine,
    media::MediaState,
    proto::v1::{
        AudioInputMode, AudioRuntimeStatus, BeatNetStatus, CommandResult, DmxConfig,
        DmxRuntimeStatus, MediaInfo, Recording, RecordingStatus, RunState, ShowCommand, ShowConfig,
        ShowSnapshot,
    },
};

const DMX_RECONNECT_INITIAL: Duration = Duration::from_secs(1);
const DMX_RECONNECT_MAX: Duration = Duration::from_secs(30);
const IDLE_DMX_STATUS_INTERVAL: Duration = Duration::from_secs(1);
const SHUTDOWN_BLACKOUT_FRAMES: usize = 3;

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
}

impl From<anyhow::Error> for AppError {
    fn from(error: anyhow::Error) -> Self {
        Self::Runtime(error)
    }
}

struct Runtime {
    analyzer: Option<AudioAnalyzer>,
    capture: Option<AudioCapture>,
    dmx: Option<DmxOutput>,
    dmx_status: DmxRuntimeStatus,
    dmx_retry_at: Instant,
    dmx_retry_delay: Duration,
    dmx_status_dirty: bool,
    last_idle_dmx_publish: Instant,
    effects: EffectsEngine,
    simulation_started: Instant,
    simulation_beat: u64,
    simulation_was_beat: bool,
    recording_monitor: bool,
    frame_count: u64,
    fps_started: Instant,
}

impl Default for Runtime {
    fn default() -> Self {
        Self {
            analyzer: None,
            capture: None,
            dmx: None,
            dmx_status: DmxRuntimeStatus::default(),
            dmx_retry_at: Instant::now(),
            dmx_retry_delay: DMX_RECONNECT_INITIAL,
            dmx_status_dirty: true,
            last_idle_dmx_publish: Instant::now(),
            effects: EffectsEngine::default(),
            simulation_started: Instant::now(),
            simulation_beat: 0,
            simulation_was_beat: false,
            recording_monitor: false,
            frame_count: 0,
            fps_started: Instant::now(),
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
    Shutdown {
        reply: oneshot::Sender<()>,
    },
}

pub struct App {
    config_path: PathBuf,
    config_tx: watch::Sender<Arc<ValidatedShowConfig>>,
    snapshot_tx: watch::Sender<Arc<ShowSnapshot>>,
    media_tx: watch::Sender<Arc<MediaState>>,
    command_tx: Sender<RuntimeCommand>,
    command_rx: StdMutex<Option<Receiver<RuntimeCommand>>>,
    runtime_thread: StdMutex<Option<thread::JoinHandle<()>>>,
    media_task: Mutex<Option<JoinHandle<()>>>,
    shutdown: CancellationToken,
    cli_simulate: bool,
}

impl App {
    pub async fn load(config_path: PathBuf, simulate: bool) -> Result<Self, AppError> {
        let show_config = Arc::new(config::load(&config_path, simulate)?);
        let snapshot = Arc::new(stopped_snapshot(&show_config, simulate));
        let (config_tx, _) = watch::channel(show_config);
        let (snapshot_tx, _) = watch::channel(snapshot);
        let (media_tx, _) = watch::channel(Arc::new(MediaState::default()));
        let (command_tx, command_rx) = mpsc::channel();
        Ok(Self {
            config_path,
            config_tx,
            snapshot_tx,
            media_tx,
            command_tx,
            command_rx: StdMutex::new(Some(command_rx)),
            runtime_thread: StdMutex::new(None),
            media_task: Mutex::new(None),
            shutdown: CancellationToken::new(),
            cli_simulate: simulate,
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
            let app = Arc::clone(self);
            let runtime_thread = thread::Builder::new()
                .name("music-auto-show-runtime".into())
                .spawn(move || RuntimeLoop::new(app).run(receiver))
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
            let (reply, stopped) = oneshot::channel();
            if self
                .command_tx
                .send(RuntimeCommand::Shutdown { reply })
                .is_ok()
            {
                let _ = stopped.await;
            }
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
        self.snapshot_tx.borrow().as_ref().clone()
    }

    pub fn subscribe(&self) -> watch::Receiver<Arc<ShowSnapshot>> {
        self.snapshot_tx.subscribe()
    }

    pub(crate) async fn wait_for_shutdown(&self) {
        self.shutdown.cancelled().await;
    }

    pub async fn config(&self) -> ShowConfig {
        self.config_tx.borrow().as_proto().clone()
    }

    pub async fn export_config(&self) -> Result<(String, String), AppError> {
        let config = self.config_tx.borrow().clone();
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

    pub async fn media_artwork(&self, revision: &str) -> Option<Arc<[u8]>> {
        self.media_tx.borrow().artwork(revision)
    }

    fn send_command(&self, command: RuntimeCommand) -> Result<(), AppError> {
        self.command_tx
            .send(command)
            .map_err(|_| AppError::Unavailable)
    }
}

struct RuntimeLoop {
    app: Arc<App>,
    config: Arc<ValidatedShowConfig>,
    snapshot: ShowSnapshot,
    runtime: Runtime,
}

impl RuntimeLoop {
    fn new(app: Arc<App>) -> Self {
        let config = app.config_tx.borrow().clone();
        let snapshot = app.snapshot_tx.borrow().as_ref().clone();
        let runtime = Runtime {
            dmx_status: active_dmx_status(&config, app.cli_simulate),
            ..Default::default()
        };
        Self {
            app,
            config,
            snapshot,
            runtime,
        }
    }

    fn run(mut self, receiver: Receiver<RuntimeCommand>) {
        let mut next_frame = Instant::now();
        loop {
            let timeout = next_frame.saturating_duration_since(Instant::now());
            match receiver.recv_timeout(timeout) {
                Ok(RuntimeCommand::Shutdown { reply }) => {
                    self.stop_show();
                    self.shutdown_dmx();
                    let _ = reply.send(());
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

            if Instant::now() >= next_frame {
                if let Err(error) = self.tick() {
                    error!(%error, "show frame failed");
                    self.fail_show(error.to_string());
                }
                next_frame = Instant::now() + self.frame_interval();
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
            RuntimeCommand::Shutdown { reply } => {
                let _ = reply.send(());
            }
        }
    }

    fn frame_interval(&self) -> Duration {
        dmx_frame_interval(self.config.dmx().fps)
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
        config::save(&self.app.config_path, &updated)?;
        let was_running = self.snapshot.run_state == RunState::Running as i32;
        let dmx_changed = self.config.dmx() != updated.dmx();
        if was_running {
            self.stop_show();
        }
        if dmx_changed {
            self.blank_and_release_dmx(SHUTDOWN_BLACKOUT_FRAMES);
        }
        self.config = Arc::clone(&updated);
        self.app.config_tx.send_replace(updated);
        if dmx_changed {
            self.reset_dmx_manager();
        }
        if was_running {
            self.start_show()?;
        } else {
            reset_inactive_runtime_snapshot(
                &mut self.snapshot,
                &self.config,
                self.app.cli_simulate,
            );
            self.publish();
        }
        Ok(self.config.as_proto().clone())
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
        if self.snapshot.run_state == RunState::Running as i32 {
            return Ok(self.command_result(true, "Show is already running"));
        }
        if self
            .snapshot
            .recording
            .as_ref()
            .is_some_and(|recording| recording.recording)
        {
            return Err(AppError::FailedPrecondition(
                "stop the audio recording before starting the show".into(),
            ));
        }
        self.set_run_state(RunState::Starting, "Starting audio and effects");
        let startup = (|| {
            let audio_config = self.config.audio();
            let simulate_audio = self.app.cli_simulate || audio_config.simulate;
            let capture = if simulate_audio {
                None
            } else {
                Some(start_capture(audio_config)?)
            };
            let sample_rate = capture.as_ref().map_or(44_100, AudioCapture::sample_rate);
            let analyzer = AudioAnalyzer::new(
                sample_rate,
                audio_config.gain,
                &audio_config.beatnet_model_path,
            );
            self.runtime.analyzer = Some(analyzer);
            self.runtime.capture = capture;
            self.runtime.effects = EffectsEngine::default();
            self.runtime.simulation_started = Instant::now();
            self.runtime.simulation_beat = 0;
            self.runtime.simulation_was_beat = false;
            self.runtime.recording_monitor = false;
            self.runtime.frame_count = 0;
            self.runtime.fps_started = Instant::now();
            Ok::<_, anyhow::Error>(simulate_audio)
        })();
        let simulate_audio = match startup {
            Ok(simulation) => simulation,
            Err(error) => {
                self.fail_show(error.to_string());
                return Err(error.into());
            }
        };
        self.set_run_state(RunState::Running, "Show running");
        info!(
            simulate_audio,
            simulate_dmx = self.runtime.dmx_status.simulated,
            "show started"
        );
        Ok(self.command_result(true, "Show started"))
    }

    fn stop_show(&mut self) {
        if self.snapshot.run_state == RunState::Stopped as i32 {
            return;
        }
        self.set_run_state(RunState::Stopping, "Stopping show");
        self.send_connected_blackout(1);
        self.runtime.capture = None;
        self.runtime.analyzer = None;
        self.runtime.recording_monitor = false;
        self.snapshot.run_state = RunState::Stopped as i32;
        self.snapshot.status_message = "Stopped".into();
        self.snapshot.recording = Some(stopped_recording_status());
        reset_inactive_runtime_snapshot(&mut self.snapshot, &self.config, self.app.cli_simulate);
        self.snapshot.dmx_runtime = Some(self.runtime.dmx_status.clone());
        self.publish();
    }

    fn set_blackout(&mut self, enabled: bool) -> CommandResult {
        self.snapshot.blackout = enabled;
        if enabled {
            self.send_connected_blackout(1);
            self.snapshot.dmx_universe = self.zero_universe();
            self.snapshot.dmx_runtime = Some(self.runtime.dmx_status.clone());
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
        let running = self.snapshot.run_state == RunState::Running as i32;
        if !running {
            let audio_config = self.config.audio();
            let simulate_audio = self.app.cli_simulate || audio_config.simulate;
            let capture = if simulate_audio {
                None
            } else {
                Some(start_capture(audio_config)?)
            };
            let sample_rate = capture.as_ref().map_or(44_100, AudioCapture::sample_rate);
            self.runtime.analyzer = Some(AudioAnalyzer::new(
                sample_rate,
                audio_config.gain,
                &audio_config.beatnet_model_path,
            ));
            self.runtime.capture = capture;
            self.runtime.simulation_started = Instant::now();
            self.runtime.simulation_beat = 0;
            self.runtime.simulation_was_beat = false;
            self.runtime.recording_monitor = true;
        }
        let analyzer = self
            .runtime
            .analyzer
            .as_mut()
            .context("audio input is not running")?;
        if !analyzer.start_recording() {
            return Err(AppError::FailedPrecondition(
                "audio recording could not start".into(),
            ));
        }
        let status = analyzer.recording_status();
        self.snapshot.recording = Some(status.clone());
        self.publish();
        Ok(status)
    }

    fn stop_recording(&mut self) -> Result<Recording, AppError> {
        let recording = self
            .runtime
            .analyzer
            .as_mut()
            .context("audio input is not running")?
            .stop_recording()?;
        let stopped_monitor = self.runtime.recording_monitor;
        if stopped_monitor {
            self.runtime.capture = None;
            self.runtime.analyzer = None;
            self.runtime.recording_monitor = false;
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
        let analyzer = self
            .runtime
            .analyzer
            .as_mut()
            .context("audio input is not running")?;
        analyzer.clear_recording();
        let status = analyzer.recording_status();
        let stopped_monitor = self.runtime.recording_monitor;
        if stopped_monitor {
            self.runtime.capture = None;
            self.runtime.analyzer = None;
            self.runtime.recording_monitor = false;
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
        if self.snapshot.run_state != RunState::Running as i32 {
            let universe = self.zero_universe();
            self.send_dmx_frame(&universe);
            if self.runtime.recording_monitor {
                return self.tick_recording_monitor();
            }
            self.snapshot.dmx_universe = universe;
            if self.runtime.dmx_status_dirty
                || self.runtime.last_idle_dmx_publish.elapsed() >= IDLE_DMX_STATUS_INTERVAL
            {
                self.snapshot.dmx_runtime = Some(self.runtime.dmx_status.clone());
                self.runtime.dmx_status_dirty = false;
                self.runtime.last_idle_dmx_publish = Instant::now();
                self.publish();
            }
            return Ok(());
        }
        let simulate = self.app.cli_simulate || self.config.audio().simulate;
        let blackout = self.snapshot.blackout;
        let previous_audio = self.snapshot.audio.clone().unwrap_or_default();
        let previous_effects_fps = self.snapshot.effects_fps;
        let detected_media = self.app.media_tx.borrow().info().clone();
        let audio = if simulate {
            let elapsed = self.runtime.simulation_started.elapsed().as_secs_f32();
            let (samples, mut beat) = simulated_audio(elapsed, self.runtime.simulation_beat);
            if beat.beat && !self.runtime.simulation_was_beat {
                self.runtime.simulation_beat += 1;
                beat.estimated_beat = self.runtime.simulation_beat;
                beat.estimated_bar = self.runtime.simulation_beat / 4;
                beat.downbeat = self.runtime.simulation_beat.is_multiple_of(4);
            }
            self.runtime.simulation_was_beat = beat.beat;
            self.runtime
                .analyzer
                .as_mut()
                .context("audio analyzer is not initialized")?
                .process_simulated(&samples, beat)
        } else {
            let pending = self
                .runtime
                .capture
                .as_ref()
                .map(AudioCapture::drain_samples)
                .unwrap_or_default();
            if pending.is_empty() {
                previous_audio
            } else {
                self.runtime
                    .analyzer
                    .as_mut()
                    .context("audio analyzer is not initialized")?
                    .process(&pending)
            }
        };
        let analyzer = self
            .runtime
            .analyzer
            .as_ref()
            .context("audio analyzer is not initialized")?;
        let beatnet = analyzer.beatnet_status();
        let recording = analyzer.recording_status();
        let media = if simulate {
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
        let output =
            self.runtime
                .effects
                .process(&self.config, &audio, &media.album_colors, blackout);
        self.send_dmx_frame(&output.universe);
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
        let audio_runtime = self.runtime.capture.as_ref().map_or_else(
            || simulated_audio_status(&self.config),
            AudioCapture::status,
        );
        self.snapshot.status_message = if blackout {
            "Blackout active"
        } else {
            "Show running"
        }
        .into();
        self.snapshot.audio = Some(audio);
        self.snapshot.audio_runtime = Some(audio_runtime);
        self.snapshot.dmx_runtime = Some(self.runtime.dmx_status.clone());
        self.snapshot.recording = Some(recording);
        self.snapshot.beatnet = Some(beatnet);
        self.snapshot.media = Some(media);
        self.snapshot.fixture_states = output.fixture_states;
        self.snapshot.dmx_universe = output.universe;
        self.snapshot.effects_fps = effects_fps;
        self.publish();
        Ok(())
    }

    fn tick_recording_monitor(&mut self) -> Result<(), AppError> {
        if !self.runtime.recording_monitor {
            return Ok(());
        }
        let simulate = self.app.cli_simulate || self.config.audio().simulate;
        let previous_audio = self.snapshot.audio.clone().unwrap_or_default();
        let audio = if simulate {
            let elapsed = self.runtime.simulation_started.elapsed().as_secs_f32();
            let (samples, mut beat) = simulated_audio(elapsed, self.runtime.simulation_beat);
            if beat.beat && !self.runtime.simulation_was_beat {
                self.runtime.simulation_beat += 1;
                beat.estimated_beat = self.runtime.simulation_beat;
                beat.estimated_bar = self.runtime.simulation_beat / 4;
                beat.downbeat = self.runtime.simulation_beat.is_multiple_of(4);
            }
            self.runtime.simulation_was_beat = beat.beat;
            self.runtime
                .analyzer
                .as_mut()
                .context("audio analyzer is not initialized")?
                .process_simulated(&samples, beat)
        } else {
            let pending = self
                .runtime
                .capture
                .as_ref()
                .map(AudioCapture::drain_samples)
                .unwrap_or_default();
            if pending.is_empty() {
                previous_audio
            } else {
                self.runtime
                    .analyzer
                    .as_mut()
                    .context("audio analyzer is not initialized")?
                    .process(&pending)
            }
        };
        let analyzer = self
            .runtime
            .analyzer
            .as_ref()
            .context("audio analyzer is not initialized")?;
        self.snapshot.status_message = "Recording input check".into();
        self.snapshot.audio = Some(audio);
        self.snapshot.audio_runtime = Some(self.runtime.capture.as_ref().map_or_else(
            || simulated_audio_status(&self.config),
            AudioCapture::status,
        ));
        self.snapshot.recording = Some(analyzer.recording_status());
        self.snapshot.beatnet = Some(analyzer.beatnet_status());
        self.snapshot.media = Some(self.app.media_tx.borrow().info().clone());
        self.snapshot.dmx_runtime = Some(self.runtime.dmx_status.clone());
        self.snapshot.dmx_universe = self.zero_universe();
        self.publish();
        Ok(())
    }

    fn effective_dmx_config(&self) -> DmxConfig {
        let mut config = self.config.dmx().clone();
        config.simulate = self.app.cli_simulate || config.simulate;
        config
    }

    fn zero_universe(&self) -> Vec<u8> {
        vec![0; self.config.dmx().universe_size as usize]
    }

    fn ensure_dmx_connected(&mut self) {
        if self.runtime.dmx.is_some() || Instant::now() < self.runtime.dmx_retry_at {
            return;
        }

        let config = self.effective_dmx_config();
        match DmxOutput::open(&config) {
            Ok(output) => {
                let send_count = self.runtime.dmx_status.send_count;
                let error_count = self.runtime.dmx_status.error_count;
                let mut status = output.status();
                status.send_count = send_count;
                status.error_count = error_count;
                self.runtime.dmx = Some(output);
                self.runtime.dmx_status = status;
                self.runtime.dmx_retry_delay = DMX_RECONNECT_INITIAL;
                self.runtime.dmx_retry_at = Instant::now();
                self.runtime.dmx_status_dirty = true;
                info!(
                    port = %self.runtime.dmx_status.port,
                    simulated = self.runtime.dmx_status.simulated,
                    "DMX output acquired"
                );
            }
            Err(error) => {
                self.record_dmx_error(error.to_string());
                warn!(
                    error = %self.runtime.dmx_status.last_error,
                    retry_seconds = self.runtime.dmx_retry_delay.as_secs_f32(),
                    "DMX output unavailable; retrying"
                );
                self.schedule_dmx_retry();
            }
        }
    }

    fn send_dmx_frame(&mut self, universe: &[u8]) {
        self.ensure_dmx_connected();
        self.send_connected_frame(universe);
    }

    fn send_connected_frame(&mut self, universe: &[u8]) {
        let result = match self.runtime.dmx.as_mut() {
            Some(dmx) => dmx.send(universe),
            None => return,
        };

        match result {
            Ok(()) => {
                self.runtime.dmx_status.send_count += 1;
                self.runtime.dmx_status.consecutive_errors = 0;
                self.runtime.dmx_status.last_error.clear();
            }
            Err(error) => {
                warn!(%error, "DMX output failed; reconnecting");
                self.runtime.dmx = None;
                self.record_dmx_error(error.to_string());
                self.schedule_dmx_retry();
            }
        }
    }

    fn record_dmx_error(&mut self, message: String) {
        self.runtime.dmx_status.configured_port = self.config.dmx().port.clone();
        self.runtime.dmx_status.running = true;
        self.runtime.dmx_status.is_open = false;
        self.runtime.dmx_status.simulated = self.app.cli_simulate || self.config.dmx().simulate;
        self.runtime.dmx_status.error_count += 1;
        self.runtime.dmx_status.consecutive_errors += 1;
        self.runtime.dmx_status.last_error = message;
        self.runtime.dmx_status_dirty = true;
    }

    fn schedule_dmx_retry(&mut self) {
        self.runtime.dmx_retry_at = Instant::now() + self.runtime.dmx_retry_delay;
        self.runtime.dmx_retry_delay = self
            .runtime
            .dmx_retry_delay
            .saturating_mul(2)
            .min(DMX_RECONNECT_MAX);
    }

    fn send_connected_blackout(&mut self, frames: usize) {
        let universe = self.zero_universe();
        for _ in 0..frames {
            if self.runtime.dmx.is_none() {
                break;
            }
            self.send_connected_frame(&universe);
        }
    }

    fn blank_and_release_dmx(&mut self, frames: usize) {
        self.send_connected_blackout(frames);
        self.runtime.dmx = None;
    }

    fn reset_dmx_manager(&mut self) {
        self.runtime.dmx = None;
        self.runtime.dmx_status = active_dmx_status(&self.config, self.app.cli_simulate);
        self.runtime.dmx_retry_at = Instant::now();
        self.runtime.dmx_retry_delay = DMX_RECONNECT_INITIAL;
        self.runtime.dmx_status_dirty = true;
        self.snapshot.dmx_runtime = Some(self.runtime.dmx_status.clone());
        self.snapshot.dmx_universe = self.zero_universe();
    }

    fn shutdown_dmx(&mut self) {
        self.blank_and_release_dmx(SHUTDOWN_BLACKOUT_FRAMES);
        self.runtime.dmx_status.running = false;
        self.runtime.dmx_status.is_open = false;
    }

    fn set_run_state(&mut self, state: RunState, message: &str) {
        self.snapshot.run_state = state as i32;
        self.snapshot.status_message = message.into();
        self.publish();
    }

    fn fail_show(&mut self, message: String) {
        self.send_connected_blackout(1);
        self.runtime.capture = None;
        self.runtime.analyzer = None;
        self.runtime.recording_monitor = false;
        self.snapshot.run_state = RunState::Error as i32;
        self.snapshot.status_message = message;
        self.snapshot.recording = Some(stopped_recording_status());
        reset_inactive_runtime_snapshot(&mut self.snapshot, &self.config, self.app.cli_simulate);
        self.snapshot.dmx_runtime = Some(self.runtime.dmx_status.clone());
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
        self.snapshot.sequence += 1;
        self.snapshot.captured_at_unix_ms = unix_millis();
        self.app
            .snapshot_tx
            .send_replace(Arc::new(self.snapshot.clone()));
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

fn simulated_audio_status(config: &ValidatedShowConfig) -> AudioRuntimeStatus {
    let mut status = stopped_audio_status(config, true);
    status.actual_mode = AudioInputMode::Auto as i32;
    status.device_name = "Simulated audio generator".into();
    status.device_type = "simulated".into();
    status.host_api = "Simulation".into();
    status.channels = 1;
    status.sample_rate = 44_100;
    status.selection_reason = "simulated".into();
    status.running = true;
    status
}

fn stopped_dmx_status(config: &ValidatedShowConfig, simulate: bool) -> DmxRuntimeStatus {
    DmxRuntimeStatus {
        configured_port: config.dmx().port.clone(),
        simulated: simulate || config.dmx().simulate,
        ..Default::default()
    }
}

fn active_dmx_status(config: &ValidatedShowConfig, simulate: bool) -> DmxRuntimeStatus {
    DmxRuntimeStatus {
        running: true,
        ..stopped_dmx_status(config, simulate)
    }
}

fn dmx_frame_interval(fps: u32) -> Duration {
    Duration::from_secs_f64(1.0 / f64::from(fps.max(1)))
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
                let snapshot = snapshots.borrow().as_ref().clone();
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
