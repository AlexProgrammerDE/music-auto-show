use std::{
    collections::{HashMap, HashSet},
    path::PathBuf,
    sync::Arc,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result, bail};
use tokio::{
    sync::{Mutex, RwLock, watch},
    task::JoinHandle,
};
use tokio_util::sync::CancellationToken;
use tracing::{error, info};

use crate::{
    audio::{AudioAnalyzer, AudioCapture, list_devices, simulated_audio, start_capture},
    config,
    dmx::{DmxOutput, validate_config},
    effects::EffectsEngine,
    proto::v1::{
        AudioInputMode, AudioRuntimeStatus, BeatNetStatus, CommandResult, DmxRuntimeStatus,
        MediaInfo, Recording, RecordingStatus, RunState, ShowCommand, ShowConfig, ShowSnapshot,
    },
};

struct Runtime {
    analyzer: Option<AudioAnalyzer>,
    capture: Option<AudioCapture>,
    dmx: Option<DmxOutput>,
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

pub struct App {
    config_path: PathBuf,
    config: RwLock<ShowConfig>,
    snapshot: RwLock<ShowSnapshot>,
    runtime: Mutex<Runtime>,
    snapshot_tx: watch::Sender<ShowSnapshot>,
    task: Mutex<Option<JoinHandle<()>>>,
    media_task: Mutex<Option<JoinHandle<()>>>,
    media: RwLock<MediaInfo>,
    shutdown: CancellationToken,
    cli_simulate: bool,
}

impl App {
    pub async fn load(config_path: PathBuf, simulate: bool) -> Result<Self> {
        let mut show_config = config::load(&config_path, simulate)?;
        normalize_config(&mut show_config, simulate)?;
        let snapshot = stopped_snapshot(&show_config, simulate);
        let (snapshot_tx, _) = watch::channel(snapshot.clone());
        Ok(Self {
            config_path,
            config: RwLock::new(show_config),
            snapshot: RwLock::new(snapshot),
            runtime: Mutex::new(Runtime::default()),
            snapshot_tx,
            task: Mutex::new(None),
            media_task: Mutex::new(None),
            media: RwLock::new(MediaInfo::default()),
            shutdown: CancellationToken::new(),
            cli_simulate: simulate,
        })
    }

    pub async fn start_runtime(self: &Arc<Self>) -> Result<()> {
        let mut task = self.task.lock().await;
        if task.is_some() {
            return Ok(());
        }
        let app = Arc::clone(self);
        *task = Some(tokio::spawn(async move {
            let mut interval = tokio::time::interval(Duration::from_millis(25));
            interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                tokio::select! {
                    biased;
                    () = app.shutdown.cancelled() => break,
                    _ = interval.tick() => {
                        if let Err(error) = app.tick().await {
                            error!(%error, "show frame failed");
                            app.fail_show(error.to_string()).await;
                        }
                    }
                }
            }
        }));
        drop(task);
        let mut media_task = self.media_task.lock().await;
        if media_task.is_none() {
            let media_app = Arc::clone(self);
            *media_task = Some(tokio::spawn(async move {
                crate::media::monitor(&media_app.media, media_app.shutdown.clone()).await;
            }));
        }
        Ok(())
    }

    pub async fn stop_runtime(&self) {
        self.shutdown.cancel();
        self.stop_show().await;
        let mut runtime = self.runtime.lock().await;
        runtime.capture = None;
        runtime.dmx = None;
        runtime.analyzer = None;
        runtime.recording_monitor = false;
        drop(runtime);
        if let Some(task) = self.task.lock().await.take()
            && let Err(error) = task.await
        {
            error!(%error, "show runtime task did not stop cleanly");
        }
        if let Some(task) = self.media_task.lock().await.take()
            && let Err(error) = task.await
        {
            error!(%error, "media monitor task did not stop cleanly");
        }
    }

    pub async fn snapshot(&self) -> ShowSnapshot {
        self.snapshot.read().await.clone()
    }

    pub fn subscribe(&self) -> watch::Receiver<ShowSnapshot> {
        self.snapshot_tx.subscribe()
    }

    pub(crate) async fn wait_for_shutdown(&self) {
        self.shutdown.cancelled().await;
    }

    pub async fn config(&self) -> ShowConfig {
        self.config.read().await.clone()
    }

    pub async fn export_config(&self) -> Result<(String, String)> {
        let config = self.config.read().await;
        let json = config::to_json(&config)?;
        Ok((json, config_filename(&config.name)))
    }

    pub async fn import_config(&self, json: &str) -> Result<ShowConfig> {
        let imported = config::parse_json(json, self.cli_simulate)?;
        self.update_config(imported).await
    }

    pub async fn reset_config(&self) -> Result<ShowConfig> {
        self.update_config(config::default_show_config(self.cli_simulate))
            .await
    }

    pub async fn update_config(&self, mut updated: ShowConfig) -> Result<ShowConfig> {
        normalize_config(&mut updated, self.cli_simulate)?;
        let snapshot = self.snapshot.read().await;
        if snapshot
            .recording
            .as_ref()
            .is_some_and(|recording| recording.recording)
        {
            bail!("stop the audio recording before updating the configuration");
        }
        let was_running = snapshot.run_state == RunState::Running as i32;
        drop(snapshot);
        if was_running {
            self.stop_show().await;
        }
        config::save(&self.config_path, &updated)?;
        *self.config.write().await = updated.clone();
        if was_running {
            self.start_show().await?;
        } else {
            let mut snapshot = self.snapshot.write().await;
            reset_inactive_runtime_snapshot(&mut snapshot, &updated, self.cli_simulate);
            self.publish(&mut snapshot);
        }
        Ok(updated)
    }

    pub async fn control(&self, command: ShowCommand) -> Result<CommandResult> {
        match command {
            ShowCommand::Start => self.start_show().await,
            ShowCommand::Stop => {
                self.stop_show().await;
                Ok(self.command_result(true, "Show stopped").await)
            }
            ShowCommand::Unspecified => bail!("show command is required"),
        }
    }

    async fn start_show(&self) -> Result<CommandResult> {
        let snapshot = self.snapshot.read().await;
        if snapshot.run_state == RunState::Running as i32 {
            drop(snapshot);
            return Ok(self.command_result(true, "Show is already running").await);
        }
        if snapshot
            .recording
            .as_ref()
            .is_some_and(|recording| recording.recording)
        {
            bail!("stop the audio recording before starting the show");
        }
        drop(snapshot);
        self.set_run_state(RunState::Starting, "Starting audio and DMX")
            .await;
        let startup = async {
            let config = self.config.read().await.clone();
            let dmx_config = config
                .dmx
                .as_ref()
                .context("DMX configuration is missing")?;
            let audio_config = config
                .audio
                .as_ref()
                .context("audio configuration is missing")?;
            validate_config(dmx_config)?;
            let simulate_audio = self.cli_simulate || audio_config.simulate;
            let simulate_dmx = self.cli_simulate || dmx_config.simulate;

            let mut effective_dmx = dmx_config.clone();
            effective_dmx.simulate = simulate_dmx;
            let dmx = DmxOutput::open(&effective_dmx)?;
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
            let mut runtime = self.runtime.lock().await;
            runtime.analyzer = Some(analyzer);
            runtime.capture = capture;
            runtime.dmx = Some(dmx);
            runtime.effects = EffectsEngine::default();
            runtime.simulation_started = Instant::now();
            runtime.simulation_beat = 0;
            runtime.simulation_was_beat = false;
            runtime.recording_monitor = false;
            runtime.frame_count = 0;
            runtime.fps_started = Instant::now();
            Ok::<_, anyhow::Error>((simulate_audio, simulate_dmx))
        }
        .await;
        let (simulate_audio, simulate_dmx) = match startup {
            Ok(simulation) => simulation,
            Err(error) => {
                self.fail_show(error.to_string()).await;
                return Err(error);
            }
        };
        self.set_run_state(RunState::Running, "Show running").await;
        info!(simulate_audio, simulate_dmx, "show started");
        Ok(self.command_result(true, "Show started").await)
    }

    async fn stop_show(&self) {
        if self.snapshot.read().await.run_state == RunState::Stopped as i32 {
            return;
        }
        self.set_run_state(RunState::Stopping, "Stopping show")
            .await;
        let mut runtime = self.runtime.lock().await;
        runtime.capture = None;
        runtime.dmx = None;
        runtime.analyzer = None;
        runtime.recording_monitor = false;
        drop(runtime);
        let config = self.config.read().await.clone();
        let mut snapshot = self.snapshot.write().await;
        snapshot.run_state = RunState::Stopped as i32;
        snapshot.status_message = "Stopped".into();
        snapshot.recording = Some(stopped_recording_status());
        reset_inactive_runtime_snapshot(&mut snapshot, &config, self.cli_simulate);
        self.publish(&mut snapshot);
    }

    pub async fn set_blackout(&self, enabled: bool) -> CommandResult {
        let mut snapshot = self.snapshot.write().await;
        snapshot.blackout = enabled;
        snapshot.status_message = if enabled {
            "Blackout active"
        } else if snapshot.run_state == RunState::Running as i32 {
            "Show running"
        } else {
            "Stopped"
        }
        .into();
        self.publish(&mut snapshot);
        CommandResult {
            success: true,
            message: snapshot.status_message.clone(),
            run_state: snapshot.run_state,
            blackout: enabled,
        }
    }

    pub async fn start_recording(&self) -> Result<RecordingStatus> {
        let running = self.snapshot.read().await.run_state == RunState::Running as i32;
        if !running {
            let config = self.config.read().await.clone();
            let audio_config = config
                .audio
                .as_ref()
                .context("audio configuration is missing")?;
            let simulate_audio = self.cli_simulate || audio_config.simulate;
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
            let mut runtime = self.runtime.lock().await;
            runtime.analyzer = Some(analyzer);
            runtime.capture = capture;
            runtime.simulation_started = Instant::now();
            runtime.simulation_beat = 0;
            runtime.simulation_was_beat = false;
            runtime.recording_monitor = true;
        }
        let mut runtime = self.runtime.lock().await;
        let analyzer = runtime
            .analyzer
            .as_mut()
            .context("audio input is not running")?;
        if !analyzer.start_recording() {
            bail!("audio recording could not start");
        }
        let status = analyzer.recording_status();
        drop(runtime);
        let mut snapshot = self.snapshot.write().await;
        snapshot.recording = Some(status.clone());
        self.publish(&mut snapshot);
        Ok(status)
    }

    pub async fn stop_recording(&self) -> Result<Recording> {
        let mut runtime = self.runtime.lock().await;
        let recording = runtime
            .analyzer
            .as_mut()
            .context("audio input is not running")?
            .stop_recording();
        let stopped_monitor = runtime.recording_monitor;
        if stopped_monitor {
            runtime.capture = None;
            runtime.analyzer = None;
            runtime.recording_monitor = false;
        }
        drop(runtime);
        let config = if stopped_monitor {
            Some(self.config.read().await.clone())
        } else {
            None
        };
        if let Some(status) = recording.status.clone() {
            let mut snapshot = self.snapshot.write().await;
            snapshot.recording = Some(status);
            if let Some(config) = config {
                snapshot.status_message = "Stopped".into();
                reset_inactive_runtime_snapshot(&mut snapshot, &config, self.cli_simulate);
            }
            self.publish(&mut snapshot);
        }
        Ok(recording)
    }

    pub async fn clear_recording(&self) -> Result<RecordingStatus> {
        let mut runtime = self.runtime.lock().await;
        let analyzer = runtime
            .analyzer
            .as_mut()
            .context("audio input is not running")?;
        analyzer.clear_recording();
        let status = analyzer.recording_status();
        let stopped_monitor = runtime.recording_monitor;
        if stopped_monitor {
            runtime.capture = None;
            runtime.analyzer = None;
            runtime.recording_monitor = false;
        }
        drop(runtime);
        let config = if stopped_monitor {
            Some(self.config.read().await.clone())
        } else {
            None
        };
        let mut snapshot = self.snapshot.write().await;
        snapshot.recording = Some(status.clone());
        if let Some(config) = config {
            snapshot.status_message = "Stopped".into();
            reset_inactive_runtime_snapshot(&mut snapshot, &config, self.cli_simulate);
        }
        self.publish(&mut snapshot);
        Ok(status)
    }

    pub fn audio_devices(&self) -> Vec<crate::proto::v1::AudioDevice> {
        list_devices()
    }

    async fn tick(&self) -> Result<()> {
        if self.snapshot.read().await.run_state != RunState::Running as i32 {
            if self.runtime.lock().await.recording_monitor {
                return self.tick_recording_monitor().await;
            }
            return Ok(());
        }
        let config = self.config.read().await.clone();
        let simulate =
            self.cli_simulate || config.audio.as_ref().is_some_and(|audio| audio.simulate);
        let (blackout, previous_audio, previous_effects_fps) = {
            let snapshot = self.snapshot.read().await;
            (
                snapshot.blackout,
                snapshot.audio.clone().unwrap_or_default(),
                snapshot.effects_fps,
            )
        };
        let detected_media = self.media.read().await.clone();
        let mut runtime = self.runtime.lock().await;
        let audio = if simulate {
            let elapsed = runtime.simulation_started.elapsed().as_secs_f32();
            let (samples, mut beat) = simulated_audio(elapsed, runtime.simulation_beat);
            if beat.beat && !runtime.simulation_was_beat {
                runtime.simulation_beat += 1;
                beat.estimated_beat = runtime.simulation_beat;
                beat.estimated_bar = runtime.simulation_beat / 4;
                beat.downbeat = runtime.simulation_beat.is_multiple_of(4);
            }
            runtime.simulation_was_beat = beat.beat;
            runtime
                .analyzer
                .as_mut()
                .context("audio analyzer is not initialized")?
                .process_simulated(&samples, beat)
        } else {
            let mut latest = None;
            if let Some(capture) = &runtime.capture {
                while let Ok(samples) = capture.receiver.try_recv() {
                    latest = Some(samples);
                }
            }
            match latest {
                Some(samples) => runtime
                    .analyzer
                    .as_mut()
                    .context("audio analyzer is not initialized")?
                    .process(&samples),
                None => previous_audio,
            }
        };
        let analyzer = runtime
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
        let output = runtime
            .effects
            .process(&config, &audio, &media.album_colors, blackout);
        if let Some(dmx) = runtime.dmx.as_mut() {
            dmx.send(&output.universe);
        }
        runtime.frame_count += 1;
        let elapsed = runtime.fps_started.elapsed().as_secs_f32();
        let effects_fps = if elapsed >= 1.0 {
            let fps = runtime.frame_count as f32 / elapsed;
            runtime.frame_count = 0;
            runtime.fps_started = Instant::now();
            fps
        } else {
            previous_effects_fps
        };
        let audio_runtime = runtime
            .capture
            .as_ref()
            .map_or_else(|| simulated_audio_status(&config), AudioCapture::status);
        let dmx_runtime = runtime
            .dmx
            .as_ref()
            .map_or_else(DmxRuntimeStatus::default, DmxOutput::status);
        drop(runtime);

        let mut snapshot = self.snapshot.write().await;
        snapshot.status_message = if blackout {
            "Blackout active"
        } else {
            "Show running"
        }
        .into();
        snapshot.audio = Some(audio);
        snapshot.audio_runtime = Some(audio_runtime);
        snapshot.dmx_runtime = Some(dmx_runtime);
        snapshot.recording = Some(recording);
        snapshot.beatnet = Some(beatnet);
        snapshot.media = Some(media);
        snapshot.fixture_states = output.fixture_states;
        snapshot.dmx_universe = output.universe;
        snapshot.effects_fps = effects_fps;
        self.publish(&mut snapshot);
        Ok(())
    }

    async fn tick_recording_monitor(&self) -> Result<()> {
        let config = self.config.read().await.clone();
        let simulate =
            self.cli_simulate || config.audio.as_ref().is_some_and(|audio| audio.simulate);
        let previous_audio = self.snapshot.read().await.audio.clone().unwrap_or_default();
        let mut runtime = self.runtime.lock().await;
        if !runtime.recording_monitor {
            return Ok(());
        }
        let audio = if simulate {
            let elapsed = runtime.simulation_started.elapsed().as_secs_f32();
            let (samples, mut beat) = simulated_audio(elapsed, runtime.simulation_beat);
            if beat.beat && !runtime.simulation_was_beat {
                runtime.simulation_beat += 1;
                beat.estimated_beat = runtime.simulation_beat;
                beat.estimated_bar = runtime.simulation_beat / 4;
                beat.downbeat = runtime.simulation_beat.is_multiple_of(4);
            }
            runtime.simulation_was_beat = beat.beat;
            runtime
                .analyzer
                .as_mut()
                .context("audio analyzer is not initialized")?
                .process_simulated(&samples, beat)
        } else {
            let mut latest = None;
            if let Some(capture) = &runtime.capture {
                while let Ok(samples) = capture.receiver.try_recv() {
                    latest = Some(samples);
                }
            }
            match latest {
                Some(samples) => runtime
                    .analyzer
                    .as_mut()
                    .context("audio analyzer is not initialized")?
                    .process(&samples),
                None => previous_audio,
            }
        };
        let analyzer = runtime
            .analyzer
            .as_ref()
            .context("audio analyzer is not initialized")?;
        let recording = analyzer.recording_status();
        let beatnet = analyzer.beatnet_status();
        let audio_runtime = runtime
            .capture
            .as_ref()
            .map_or_else(|| simulated_audio_status(&config), AudioCapture::status);
        drop(runtime);
        let media = self.media.read().await.clone();
        let mut snapshot = self.snapshot.write().await;
        snapshot.status_message = "Recording input check".into();
        snapshot.audio = Some(audio);
        snapshot.audio_runtime = Some(audio_runtime);
        snapshot.recording = Some(recording);
        snapshot.beatnet = Some(beatnet);
        snapshot.media = Some(media);
        self.publish(&mut snapshot);
        Ok(())
    }

    async fn set_run_state(&self, state: RunState, message: &str) {
        let mut snapshot = self.snapshot.write().await;
        snapshot.run_state = state as i32;
        snapshot.status_message = message.into();
        self.publish(&mut snapshot);
    }

    async fn fail_show(&self, message: String) {
        let mut runtime = self.runtime.lock().await;
        runtime.capture = None;
        runtime.dmx = None;
        runtime.analyzer = None;
        runtime.recording_monitor = false;
        drop(runtime);
        let config = self.config.read().await.clone();
        let mut snapshot = self.snapshot.write().await;
        snapshot.run_state = RunState::Error as i32;
        snapshot.status_message = message;
        snapshot.recording = Some(stopped_recording_status());
        reset_inactive_runtime_snapshot(&mut snapshot, &config, self.cli_simulate);
        self.publish(&mut snapshot);
    }

    async fn command_result(&self, success: bool, message: &str) -> CommandResult {
        let snapshot = self.snapshot.read().await;
        CommandResult {
            success,
            message: message.into(),
            run_state: snapshot.run_state,
            blackout: snapshot.blackout,
        }
    }

    fn publish(&self, snapshot: &mut ShowSnapshot) {
        snapshot.sequence += 1;
        snapshot.captured_at_unix_ms = unix_millis();
        self.snapshot_tx.send_replace(snapshot.clone());
    }
}

fn normalize_config(config: &mut ShowConfig, cli_simulate: bool) -> Result<()> {
    if config.name.trim().is_empty() {
        config.name = "My Light Show".into();
    }
    let dmx = config.dmx.get_or_insert_with(Default::default);
    if dmx.universe_size == 0 {
        dmx.universe_size = 512;
    }
    if dmx.fps == 0 {
        dmx.fps = 40;
    }
    if cli_simulate {
        dmx.simulate = true;
    }
    validate_config(dmx)?;
    let audio = config.audio.get_or_insert_with(Default::default);
    if audio.mode == AudioInputMode::Unspecified as i32 {
        audio.mode = AudioInputMode::Auto as i32;
    }
    if audio.gain == 0.0 {
        audio.gain = 1.0;
    }
    if audio.beatnet_model_path.trim().is_empty() {
        audio.beatnet_model_path = "models/beatnet-plus.pt".into();
    }
    if !(0.1..=5.0).contains(&audio.gain) {
        bail!("audio gain must be between 0.1 and 5.0");
    }
    if cli_simulate {
        audio.simulate = true;
    }
    if config.effects.is_none() {
        config.effects = config::default_show_config(cli_simulate).effects;
    }
    let mut profiles = config::default_profiles();
    for custom in std::mem::take(&mut config.profiles) {
        if let Some(existing) = profiles
            .iter_mut()
            .find(|profile| profile.name == custom.name)
        {
            *existing = custom;
        } else {
            profiles.push(custom);
        }
    }
    config.profiles = profiles;
    let profile_channels: HashMap<_, _> = config
        .profiles
        .iter()
        .map(|profile| (profile.name.clone(), profile.channels.clone()))
        .collect();
    let universe_size = config.dmx.as_ref().map_or(512, |dmx| dmx.universe_size);
    let mut fixture_ids = HashSet::new();
    let mut fixture_names = HashSet::new();
    for (index, fixture) in config.fixtures.iter_mut().enumerate() {
        if fixture.id.is_empty() {
            fixture.id = stable_fixture_id(&fixture.name, fixture.start_channel, index);
        }
        if !fixture_ids.insert(fixture.id.clone()) {
            bail!("fixture id '{}' is used more than once", fixture.id);
        }
        if fixture.name.trim().is_empty() {
            bail!("fixture {} has no name", index + 1);
        }
        if !fixture_names.insert(fixture.name.trim().to_lowercase()) {
            bail!("fixture name '{}' is used more than once", fixture.name);
        }
        if fixture.start_channel == 0 || fixture.start_channel > universe_size {
            bail!("fixture '{}' has an invalid start channel", fixture.name);
        }
        if !(0.0..=1.0).contains(&fixture.intensity_scale) {
            bail!(
                "fixture '{}' intensity must be between 0 and 1",
                fixture.name
            );
        }
        if fixture.pan_min > fixture.pan_max || fixture.pan_max > 255 {
            bail!("fixture '{}' has invalid pan limits", fixture.name);
        }
        if fixture.tilt_min > fixture.tilt_max || fixture.tilt_max > 255 {
            bail!("fixture '{}' has invalid tilt limits", fixture.name);
        }
        let channels = if fixture.channels.is_empty() {
            profile_channels
                .get(&fixture.profile_name)
                .map(Vec::as_slice)
                .unwrap_or_default()
        } else {
            fixture.channels.as_slice()
        };
        let mut offsets = HashSet::new();
        for channel in channels {
            if channel.offset == 0 || !offsets.insert(channel.offset) {
                bail!(
                    "fixture '{}' has an invalid or duplicate channel offset {}",
                    fixture.name,
                    channel.offset
                );
            }
            if channel.min_value > channel.max_value
                || channel.max_value > 255
                || channel.default_value > 255
                || channel.fixed_value.is_some_and(|value| value > 255)
            {
                bail!(
                    "fixture '{}' channel '{}' has values outside 0..=255",
                    fixture.name,
                    channel.name
                );
            }
        }
        let last_channel = fixture
            .start_channel
            .saturating_add(
                channels
                    .iter()
                    .map(|channel| channel.offset)
                    .max()
                    .unwrap_or(1),
            )
            .saturating_sub(1);
        if last_channel > universe_size {
            bail!(
                "fixture '{}' extends past DMX universe channel {}",
                fixture.name,
                universe_size
            );
        }
    }
    Ok(())
}

fn stable_fixture_id(name: &str, channel: u32, index: usize) -> String {
    let slug: String = name
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
    format!(
        "{}-{}-{}",
        if slug.is_empty() { "fixture" } else { &slug },
        channel,
        index + 1
    )
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

fn stopped_snapshot(config: &ShowConfig, simulate: bool) -> ShowSnapshot {
    let mut snapshot = ShowSnapshot {
        captured_at_unix_ms: unix_millis(),
        run_state: RunState::Stopped as i32,
        status_message: "Stopped".into(),
        recording: Some(stopped_recording_status()),
        ..Default::default()
    };
    reset_inactive_runtime_snapshot(&mut snapshot, config, simulate);
    snapshot
}

fn reset_inactive_runtime_snapshot(
    snapshot: &mut ShowSnapshot,
    config: &ShowConfig,
    simulate: bool,
) {
    snapshot.audio = Some(Default::default());
    snapshot.audio_runtime = Some(stopped_audio_status(config, simulate));
    snapshot.dmx_runtime = Some(stopped_dmx_status(config, simulate));
    snapshot.beatnet = Some(stopped_beatnet_status(config));
    snapshot.media = Some(MediaInfo {
        track_name: "No track".into(),
        ..Default::default()
    });
    snapshot.fixture_states.clear();
    snapshot.dmx_universe =
        vec![0; config.dmx.as_ref().map_or(512, |dmx| dmx.universe_size) as usize];
    snapshot.effects_fps = 0.0;
}

fn stopped_recording_status() -> RecordingStatus {
    RecordingStatus {
        max_duration_seconds: 30.0,
        ..Default::default()
    }
}

fn stopped_beatnet_status(config: &ShowConfig) -> BeatNetStatus {
    BeatNetStatus {
        model_name: "BeatNet+".into(),
        model_path: config
            .audio
            .as_ref()
            .map_or_else(String::new, |audio| audio.beatnet_model_path.clone()),
        status: "Idle".into(),
        ..Default::default()
    }
}

fn stopped_audio_status(config: &ShowConfig, simulate: bool) -> AudioRuntimeStatus {
    let audio = config.audio.as_ref();
    AudioRuntimeStatus {
        configured_mode: audio.map_or(AudioInputMode::Auto as i32, |audio| audio.mode),
        configured_device_id: audio.map_or_else(String::new, |audio| audio.device_id.clone()),
        selection_reason: "not_started".into(),
        simulated: simulate || audio.is_some_and(|audio| audio.simulate),
        ..Default::default()
    }
}

fn simulated_audio_status(config: &ShowConfig) -> AudioRuntimeStatus {
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

fn stopped_dmx_status(config: &ShowConfig, simulate: bool) -> DmxRuntimeStatus {
    DmxRuntimeStatus {
        configured_port: config
            .dmx
            .as_ref()
            .map_or_else(String::new, |dmx| dmx.port.clone()),
        simulated: simulate || config.dmx.as_ref().is_some_and(|dmx| dmx.simulate),
        ..Default::default()
    }
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

    #[test]
    fn fixture_identity_does_not_depend_on_array_index_alone() {
        assert_eq!(stable_fixture_id("Front Wash", 17, 0), "front-wash-17-1");
    }

    #[test]
    fn config_filename_is_safe_and_readable() {
        assert_eq!(config_filename("Friday Night #1"), "friday-night-1.json");
        assert_eq!(config_filename("***"), "show.json");
    }

    #[test]
    fn normalization_preserves_explicit_zero_fixture_controls() {
        let mut config = config::default_show_config(true);
        config.fixtures[0].intensity_scale = 0.0;
        config.fixtures[0].pan_min = 0;
        config.fixtures[0].pan_max = 0;
        config.fixtures[0].tilt_min = 0;
        config.fixtures[0].tilt_max = 0;
        normalize_config(&mut config, true).unwrap();
        let fixture = &config.fixtures[0];
        assert_eq!(fixture.intensity_scale, 0.0);
        assert_eq!(fixture.pan_max, 0);
        assert_eq!(fixture.tilt_max, 0);
    }

    #[test]
    fn normalization_rejects_duplicate_fixture_names() {
        let mut config = config::default_show_config(true);
        config.fixtures[1].name = config.fixtures[0].name.to_uppercase();
        assert!(normalize_config(&mut config, true).is_err());
    }

    #[test]
    fn inactive_snapshot_clears_live_subsystem_values() {
        let config = config::default_show_config(true);
        let mut snapshot = stopped_snapshot(&config, true);
        snapshot.audio.as_mut().unwrap().tempo = 128.0;
        snapshot.beatnet.as_mut().unwrap().available = true;
        snapshot.beatnet.as_mut().unwrap().processing = true;
        snapshot.beatnet.as_mut().unwrap().last_error = "stale error".into();
        snapshot.media.as_mut().unwrap().track_name = "Old track".into();
        snapshot.fixture_states.push(Default::default());
        snapshot.dmx_universe[0] = 255;
        snapshot.effects_fps = 40.0;

        reset_inactive_runtime_snapshot(&mut snapshot, &config, true);

        assert_eq!(snapshot.audio.unwrap().tempo, 0.0);
        let beatnet = snapshot.beatnet.unwrap();
        assert!(!beatnet.available);
        assert!(!beatnet.processing);
        assert_eq!(beatnet.status, "Idle");
        assert!(beatnet.last_error.is_empty());
        assert_eq!(beatnet.model_path, "models/beatnet-plus.pt");
        assert_eq!(snapshot.media.unwrap().track_name, "No track");
        assert!(snapshot.fixture_states.is_empty());
        assert!(snapshot.dmx_universe.iter().all(|value| *value == 0));
        assert_eq!(snapshot.effects_fps, 0.0);
    }

    #[tokio::test]
    async fn failed_start_leaves_a_consistent_error_snapshot() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let app = App::load(directory.path().join("config.json"), true)
            .await
            .expect("simulated application should load");
        app.config.write().await.dmx = None;
        app.runtime.lock().await.recording_monitor = true;

        let error = app
            .start_show()
            .await
            .expect_err("show startup should fail");

        assert_eq!(error.to_string(), "DMX configuration is missing");
        let snapshot = app.snapshot().await;
        assert_eq!(snapshot.run_state, RunState::Error as i32);
        assert_eq!(snapshot.status_message, "DMX configuration is missing");
        assert!(!snapshot.audio_runtime.unwrap().running);
        assert!(!snapshot.dmx_runtime.unwrap().running);
        assert_eq!(snapshot.beatnet.unwrap().status, "Idle");
        assert!(!app.runtime.lock().await.recording_monitor);
    }
}
