use std::{
    collections::{HashMap, VecDeque},
    f32::consts::{PI, TAU},
    time::Duration,
};

use rand::{Rng, SeedableRng, rngs::StdRng, seq::IndexedRandom};

use crate::{
    config::ValidatedShowConfig,
    proto::v1::{
        AudioAnalysis, ChannelCapability, ChannelConfig, EffectFixtureMode, EffectsConfig,
        FixtureConfig, FixtureProfile, FixtureState, MovementMode, RgbColor, RotationMode,
        StrobeEffectMode, VisualizationMode,
    },
};

const REFERENCE_FRAME_SECONDS: f32 = 0.025;
const MAX_EFFECT_STEP_SECONDS: f32 = 0.1;
const REFERENCE_TEMPO: f32 = 120.0;

#[derive(Debug, Clone)]
pub struct EffectOutput {
    pub fixture_states: Vec<FixtureState>,
    pub universe: Vec<u8>,
}

pub struct EffectsEngine {
    states: HashMap<String, FixtureState>,
    smoothed: HashMap<String, FixtureState>,
    target_pan: HashMap<String, f32>,
    target_tilt: HashMap<String, f32>,
    sweep_phase: HashMap<String, f32>,
    sweep_direction: HashMap<String, f32>,
    wall_corner_index: HashMap<String, usize>,
    elapsed_seconds: f32,
    last_beat: u64,
    last_bar: u64,
    beats_since_move: u64,
    target_hue: f32,
    current_hue: f32,
    album_hues: Vec<f32>,
    color_index: usize,
    base_intensity: f32,
    pulse_intensity: f32,
    energy_history: VecDeque<f32>,
    is_drop: bool,
    drop_time: f32,
    strobe_active: bool,
    strobe_end_time: f32,
    rotation_phase: f32,
    smoothed_rotation: f32,
    rng: StdRng,
}

impl Default for EffectsEngine {
    fn default() -> Self {
        Self {
            states: HashMap::new(),
            smoothed: HashMap::new(),
            target_pan: HashMap::new(),
            target_tilt: HashMap::new(),
            sweep_phase: HashMap::new(),
            sweep_direction: HashMap::new(),
            wall_corner_index: HashMap::new(),
            elapsed_seconds: 0.0,
            last_beat: 0,
            last_bar: 0,
            beats_since_move: 0,
            target_hue: 0.0,
            current_hue: 0.0,
            album_hues: Vec::new(),
            color_index: 0,
            base_intensity: 0.5,
            pulse_intensity: 0.0,
            energy_history: VecDeque::with_capacity(40),
            is_drop: false,
            drop_time: 0.0,
            strobe_active: false,
            strobe_end_time: 0.0,
            rotation_phase: 0.0,
            smoothed_rotation: 64.0,
            rng: StdRng::seed_from_u64(0x4d55_5349_4353_484f),
        }
    }
}

impl EffectsEngine {
    pub fn process(
        &mut self,
        config: &ValidatedShowConfig,
        audio: &AudioAnalysis,
        album_colors: &[RgbColor],
        blackout: bool,
        delta: Duration,
    ) -> EffectOutput {
        let delta_seconds = delta
            .as_secs_f32()
            .clamp(f32::EPSILON, MAX_EFFECT_STEP_SECONDS);
        self.elapsed_seconds += delta_seconds;
        self.ensure_fixtures(config);
        let universe_size = config
            .dmx
            .as_ref()
            .map_or(512, |dmx| dmx.universe_size.clamp(1, 512))
            as usize;

        if blackout {
            self.zero_states(config, true);
            return EffectOutput {
                fixture_states: self.ordered_states(config),
                universe: vec![0; universe_size],
            };
        }

        let now = self.elapsed_seconds;
        self.update_album_hues(album_colors);
        let beat_triggered = audio.estimated_beat != self.last_beat;
        let bar_triggered = audio.estimated_bar != self.last_bar;
        self.last_beat = audio.estimated_beat;
        self.last_bar = audio.estimated_bar;
        if beat_triggered {
            self.beats_since_move += 1;
        }
        self.update_energy_tracking(audio.energy, now);

        if audio.energy < 0.01 && audio.tempo <= 0.0 {
            self.zero_states(config, false);
        } else {
            self.apply_visualization(
                config,
                audio,
                beat_triggered,
                bar_triggered,
                now,
                delta_seconds,
            );
            self.process_effect_fixtures(
                config,
                audio,
                beat_triggered,
                bar_triggered,
                delta_seconds,
            );
            if effects(config).movement_enabled {
                self.apply_movement(config, audio, beat_triggered, bar_triggered, delta_seconds);
            }
            if effects(config).force_max_brightness {
                self.apply_force_max_brightness(config);
            }
        }

        self.apply_smoothing(config, delta_seconds);
        let universe = self.map_universe(config, universe_size);
        EffectOutput {
            fixture_states: self.ordered_states(config),
            universe,
        }
    }

    fn ensure_fixtures(&mut self, config: &ValidatedShowConfig) {
        for fixture in &config.fixtures {
            let key = fixture_key(fixture);
            self.states
                .entry(key.clone())
                .or_insert_with(|| default_state(fixture));
            self.smoothed
                .entry(key.clone())
                .or_insert_with(|| default_state(fixture));
            self.target_pan.entry(key.clone()).or_insert(128.0);
            self.target_tilt.entry(key.clone()).or_insert(128.0);
            self.sweep_phase.entry(key.clone()).or_insert(0.0);
            self.sweep_direction.entry(key.clone()).or_insert(1.0);
            self.wall_corner_index.entry(key).or_insert(0);
        }
    }

    fn update_album_hues(&mut self, colors: &[RgbColor]) {
        let hues: Vec<f32> = colors
            .iter()
            .filter_map(|color| {
                let (hue, saturation, value) = rgb_to_hsv(
                    color.red as f32 / 255.0,
                    color.green as f32 / 255.0,
                    color.blue as f32 / 255.0,
                );
                (saturation > 0.2 && value > 0.2).then_some(hue)
            })
            .collect();
        if hues != self.album_hues {
            self.album_hues = hues;
            self.color_index = 0;
        }
    }

    fn apply_visualization(
        &mut self,
        config: &ValidatedShowConfig,
        audio: &AudioAnalysis,
        beat: bool,
        bar: bool,
        now: f32,
        delta_seconds: f32,
    ) {
        match config.visualization_mode() {
            VisualizationMode::Energy | VisualizationMode::Unspecified => {
                self.energy_mode(config, audio, beat, bar, now, delta_seconds)
            }
            VisualizationMode::FrequencySplit => self.frequency_split_mode(config, audio, beat),
            VisualizationMode::BeatPulse => self.beat_pulse_mode(config, audio, beat),
            VisualizationMode::ColorCycle => self.color_cycle_mode(config, audio, beat),
            VisualizationMode::RainbowWave => self.rainbow_wave_mode(config, audio, beat, now),
            VisualizationMode::StrobeBeat => self.strobe_beat_mode(config, audio, beat),
            VisualizationMode::RandomFlash => self.random_flash_mode(config, audio, beat),
        }
    }

    fn energy_mode(
        &mut self,
        config: &ValidatedShowConfig,
        audio: &AudioAnalysis,
        beat: bool,
        bar: bool,
        now: f32,
        delta_seconds: f32,
    ) {
        let settings = effects(config);
        if !self.album_hues.is_empty() {
            if bar {
                self.color_index = (self.color_index + 1) % self.album_hues.len();
            }
            self.target_hue = (self.album_hues[self.color_index] + audio.mid * 0.08) % 1.0;
        } else {
            let base_hue = (now * 0.03 * settings.color_speed) % 1.0;
            self.target_hue = (base_hue + audio.mid * 0.2 + if bar { 0.25 } else { 0.0 }) % 1.0;
        }
        let mut hue_diff = self.target_hue - self.current_hue;
        if hue_diff > 0.5 {
            hue_diff -= 1.0;
        } else if hue_diff < -0.5 {
            hue_diff += 1.0;
        }
        self.current_hue = (self.current_hue + hue_diff * time_adjusted_factor(0.1, delta_seconds))
            .rem_euclid(1.0);

        self.base_intensity = 0.3 + audio.energy * 0.5;
        if beat {
            self.pulse_intensity = 0.3 + audio.bass * 0.4;
        } else {
            self.pulse_intensity *= 1.0 - ease_out_cubic(audio.beat_position);
        }
        let brightness = (self.base_intensity + self.pulse_intensity).min(1.0);
        let saturation = 0.6 + audio.energy * 0.4;

        for fixture in &config.fixtures {
            let intensity = clamp01(brightness * fixture.intensity_scale * settings.intensity);
            let key = fixture_key(fixture);
            if let Some(state) = self.states.get_mut(&key) {
                set_hsv(state, self.current_hue, saturation, intensity);
                state.dimmer = dmx(intensity);
                state.strobe = if self.strobe_active && settings.strobe_on_drop {
                    180
                } else {
                    0
                };
            }
        }
    }

    fn frequency_split_mode(
        &mut self,
        config: &ValidatedShowConfig,
        audio: &AudioAnalysis,
        beat: bool,
    ) {
        let settings = effects(config);
        let mut fixtures = config.fixtures.clone();
        fixtures.sort_by_key(|fixture| fixture.position);
        let third = (fixtures.len() / 3).max(1);
        for (index, fixture) in fixtures.iter().enumerate() {
            let scale = fixture.intensity_scale * settings.intensity;
            let (mut intensity, hue) = if index < third {
                ((0.3 + audio.bass * 0.7) * scale, audio.bass * 0.1)
            } else if index < third * 2 {
                ((0.3 + audio.mid * 0.7) * scale, 0.25 + audio.mid * 0.15)
            } else {
                ((0.3 + audio.high * 0.7) * scale, 0.6 + audio.high * 0.15)
            };
            if beat {
                intensity = (intensity + 0.2).min(1.0);
            }
            if let Some(state) = self.states.get_mut(&fixture_key(fixture)) {
                set_hsv(state, hue, 0.9, intensity);
                state.dimmer = dmx(intensity);
            }
        }
    }

    fn beat_pulse_mode(&mut self, config: &ValidatedShowConfig, audio: &AudioAnalysis, beat: bool) {
        let palette = [0.0, 0.15, 0.55, 0.75, 0.9];
        let base_hue = if self.album_hues.is_empty() {
            palette[audio.estimated_bar as usize % palette.len()]
        } else {
            self.album_hues[audio.estimated_bar as usize % self.album_hues.len()]
        };
        let settings = effects(config);
        for fixture in &config.fixtures {
            let scale = fixture.intensity_scale * settings.intensity;
            let (brightness, hue) = if beat {
                (scale, (base_hue + fixture.position as f32 * 0.05) % 1.0)
            } else {
                (
                    scale * (1.0 - ease_out_cubic(audio.beat_position)) * 0.8,
                    base_hue,
                )
            };
            if let Some(state) = self.states.get_mut(&fixture_key(fixture)) {
                set_hsv(state, hue, 0.85, brightness);
                state.dimmer = dmx(brightness);
            }
        }
    }

    fn color_cycle_mode(
        &mut self,
        config: &ValidatedShowConfig,
        audio: &AudioAnalysis,
        beat: bool,
    ) {
        let base_hue = if self.album_hues.is_empty() {
            ((audio.estimated_beat % 32) as f32 + audio.beat_position) / 32.0
        } else {
            let current = audio.estimated_bar as usize % self.album_hues.len();
            let next = (current + 1) % self.album_hues.len();
            let mut diff = self.album_hues[next] - self.album_hues[current];
            if diff > 0.5 {
                diff -= 1.0;
            } else if diff < -0.5 {
                diff += 1.0;
            }
            (self.album_hues[current] + diff * audio.beat_position).rem_euclid(1.0)
        };
        let settings = effects(config);
        let base_brightness = 0.4 + audio.energy * 0.4;
        let pulse = if beat {
            0.2
        } else {
            0.2 * (1.0 - audio.beat_position)
        };
        let count = config.fixtures.len().max(1) as f32;
        for fixture in &config.fixtures {
            let hue = (base_hue + fixture.position as f32 / count * 0.3) % 1.0;
            let brightness =
                (base_brightness + pulse) * fixture.intensity_scale * settings.intensity;
            if let Some(state) = self.states.get_mut(&fixture_key(fixture)) {
                set_hsv(state, hue, 0.9, brightness);
                state.dimmer = dmx(brightness);
            }
        }
    }

    fn rainbow_wave_mode(
        &mut self,
        config: &ValidatedShowConfig,
        audio: &AudioAnalysis,
        beat: bool,
        now: f32,
    ) {
        let settings = effects(config);
        let wave = (now * 0.5 * settings.color_speed) % 1.0;
        let base_brightness = 0.3 + audio.energy * 0.5;
        let mut fixtures = config.fixtures.clone();
        fixtures.sort_by_key(|fixture| fixture.position);
        let count = fixtures.len().max(1) as f32;
        for (index, fixture) in fixtures.iter().enumerate() {
            let phase = index as f32 / count;
            let hue = (wave + phase) % 1.0;
            let wave_brightness = 0.5 + 0.5 * ((wave + phase) * TAU).sin();
            let mut brightness =
                base_brightness * wave_brightness * fixture.intensity_scale * settings.intensity;
            if beat {
                brightness = (brightness + 0.15).min(1.0);
            }
            if let Some(state) = self.states.get_mut(&fixture_key(fixture)) {
                set_hsv(state, hue, 0.85, brightness.max(0.1));
                state.dimmer = dmx(brightness);
            }
        }
    }

    fn strobe_beat_mode(
        &mut self,
        config: &ValidatedShowConfig,
        audio: &AudioAnalysis,
        beat: bool,
    ) {
        let settings = effects(config);
        for fixture in &config.fixtures {
            let scale = fixture.intensity_scale * settings.intensity;
            if let Some(state) = self.states.get_mut(&fixture_key(fixture)) {
                if beat {
                    state.red = 255;
                    state.green = 255;
                    state.blue = 255;
                    state.dimmer = dmx(scale);
                    state.strobe = 200;
                } else {
                    let decay = (1.0 - audio.beat_position * 3.0).max(0.0);
                    let brightness = dmx(decay * scale);
                    state.red = brightness;
                    state.green = brightness;
                    state.blue = brightness;
                    state.dimmer = brightness;
                    state.strobe = if audio.beat_position < 0.3 { 200 } else { 0 };
                }
            }
        }
    }

    fn random_flash_mode(
        &mut self,
        config: &ValidatedShowConfig,
        audio: &AudioAnalysis,
        beat: bool,
    ) {
        let settings = effects(config);
        let mut flash_names = Vec::new();
        let flash_hue = if beat && !config.fixtures.is_empty() {
            let count = (config.fixtures.len() / 3).max(1);
            flash_names = config
                .fixtures
                .choose_multiple(&mut self.rng, count.min(config.fixtures.len()))
                .map(fixture_key)
                .collect();
            self.album_hues
                .choose(&mut self.rng)
                .copied()
                .unwrap_or_else(|| self.rng.random())
        } else {
            0.0
        };
        for fixture in &config.fixtures {
            let scale = fixture.intensity_scale * settings.intensity;
            if let Some(state) = self.states.get_mut(&fixture_key(fixture)) {
                if flash_names.contains(&fixture_key(fixture)) {
                    set_hsv(state, flash_hue, 1.0, scale);
                    state.dimmer = dmx(scale);
                } else {
                    let decay = (1.0 - audio.beat_position * 2.5).max(0.0);
                    state.dimmer = (state.dimmer as f32 * decay) as u32;
                    state.red = (state.red as f32 * decay) as u32;
                    state.green = (state.green as f32 * decay) as u32;
                    state.blue = (state.blue as f32 * decay) as u32;
                }
            }
        }
    }

    fn process_effect_fixtures(
        &mut self,
        config: &ValidatedShowConfig,
        audio: &AudioAnalysis,
        beat: bool,
        _bar: bool,
        delta_seconds: f32,
    ) {
        self.update_rotation(config, audio, beat, delta_seconds);
        let strobe = self.strobe_value(config, audio, beat);
        let pattern = self.strobe_effect_value(config, audio);
        let effect_rotation = self.preview_rotation(config);
        for fixture in &config.fixtures {
            let profile = find_profile(config, fixture);
            let channels = effective_channels(fixture, profile);
            let has_effect = channels
                .iter()
                .any(|channel| channel.channel_type == "effect");
            let is_effect = profile.is_some_and(|profile| profile.fixture_type == "effect");
            let rotation = has_effect.then(|| {
                self.rotation_value_for_channel(
                    config,
                    channels.iter().find(|ch| ch.channel_type == "effect"),
                )
            });
            let key = fixture_key(fixture);
            if is_effect {
                let color_macro = self.states.get(&key).map_or(222, |state| {
                    rgb_to_color_macro(state.red, state.green, state.blue, audio.energy)
                });
                if let Some(state) = self.states.get_mut(&key) {
                    if profile.is_some_and(|profile| profile.color_mixing == "dual_color_channels")
                    {
                        if let Some(profile) = profile {
                            apply_dual_color_mapping(state, profile, self.current_hue);
                        }
                    } else {
                        state.color_macro = color_macro;
                    }
                    state.strobe = strobe;
                    state.effect = rotation.unwrap_or_default();
                    state.effect_pattern = pattern;
                    state.effect_rotation = effect_rotation;
                }
            } else if let (Some(rotation), Some(state)) = (rotation, self.states.get_mut(&key)) {
                state.effect = rotation;
                state.effect_rotation = effect_rotation;
            }
        }
    }

    fn update_rotation(
        &mut self,
        config: &ValidatedShowConfig,
        audio: &AudioAnalysis,
        beat: bool,
        delta_seconds: f32,
    ) {
        if audio.energy < 0.01 || audio.tempo <= 0.0 {
            self.rotation_phase = 0.0;
            self.smoothed_rotation = 0.0;
            return;
        }
        let mode = config.rotation_mode();
        let effect_mode = config.effect_fixture_mode();
        let dt = delta_seconds;
        let tempo_scale = (audio.tempo / REFERENCE_TEMPO).clamp(0.4, 1.8);
        let movement_scale = match effect_mode {
            EffectFixtureMode::StrobeOnly => 0.2,
            EffectFixtureMode::StrobeFocus => 0.5,
            EffectFixtureMode::MovementFocus => 1.3,
            EffectFixtureMode::MovementOnly => 1.5,
            _ => 1.0,
        };
        let scale = tempo_scale * movement_scale;
        match mode {
            RotationMode::Off | RotationMode::Unspecified => {
                self.rotation_phase = 0.0;
                self.smoothed_rotation = 0.0;
            }
            RotationMode::ManualSlow => {
                self.rotation_phase = (self.rotation_phase + dt / 8.0 * scale) % 1.0;
                self.smoothed_rotation = self.rotation_phase * 127.0;
            }
            RotationMode::ManualBeat => {
                if beat {
                    let target = (audio.estimated_beat % 8) as f32 / 8.0;
                    let mut diff = target - self.rotation_phase;
                    if diff > 0.5 {
                        diff -= 1.0
                    } else if diff < -0.5 {
                        diff += 1.0
                    }
                    self.rotation_phase =
                        (self.rotation_phase + diff * 0.15 * scale).rem_euclid(1.0);
                }
                self.smoothed_rotation = self.rotation_phase * 127.0;
            }
            RotationMode::AutoSlow => {
                self.rotation_phase = (self.rotation_phase + dt / 10.0 * scale) % 1.0;
                self.smoothed_rotation = 140.0;
            }
            RotationMode::AutoMedium => {
                self.rotation_phase = (self.rotation_phase + dt / 6.0 * scale) % 1.0;
                self.smoothed_rotation = 180.0;
            }
            RotationMode::AutoFast => {
                self.rotation_phase = (self.rotation_phase + dt / 3.0 * scale) % 1.0;
                self.smoothed_rotation = 230.0;
            }
            RotationMode::AutoMusic => {
                let tempo_factor = (audio.tempo / 150.0).min(1.0) * 0.05;
                let phase_speed = 0.05 * scale + audio.energy * 0.15 + tempo_factor;
                self.rotation_phase = (self.rotation_phase + dt * phase_speed * 10.0) % 1.0;
                let target = 140.0 + audio.energy * 80.0 + tempo_factor * 20.0;
                self.smoothed_rotation +=
                    (target - self.smoothed_rotation) * time_adjusted_factor(0.02, delta_seconds);
            }
        }
    }

    fn rotation_value_for_channel(
        &self,
        config: &ValidatedShowConfig,
        channel: Option<&ChannelConfig>,
    ) -> u32 {
        let Some(channel) = channel else {
            return self.smoothed_rotation.clamp(0.0, 255.0) as u32;
        };
        let mode = config.rotation_mode();
        let auto_mode = matches!(
            mode,
            RotationMode::AutoSlow
                | RotationMode::AutoMedium
                | RotationMode::AutoFast
                | RotationMode::AutoMusic
        );
        let position = 0.5 + 0.5 * (self.rotation_phase * TAU).sin();
        if auto_mode && let Some(range) = capability_range(channel, |capability| capability.is_auto)
        {
            scale_range(self.rotation_phase, range)
        } else if let Some(range) = capability_range(channel, |capability| capability.is_manual) {
            scale_range(position, range)
        } else {
            scale_range(position, usable_range(channel))
        }
    }

    fn preview_rotation(&self, config: &ValidatedShowConfig) -> f32 {
        let mode = config.rotation_mode();
        match mode {
            RotationMode::Off | RotationMode::Unspecified => 0.0,
            RotationMode::AutoSlow
            | RotationMode::AutoMedium
            | RotationMode::AutoFast
            | RotationMode::AutoMusic => self.rotation_phase,
            RotationMode::ManualSlow | RotationMode::ManualBeat => {
                0.5 + 0.5 * (self.rotation_phase * TAU).sin()
            }
        }
    }

    fn strobe_value(&self, config: &ValidatedShowConfig, audio: &AudioAnalysis, beat: bool) -> u32 {
        let mode = config.effect_fixture_mode();
        if mode == EffectFixtureMode::MovementOnly {
            return 0;
        }
        if mode == EffectFixtureMode::MovementFocus {
            return if self.strobe_active { 150 } else { 0 };
        }
        let mut value = if self.strobe_active { 200 } else { 0 };
        match mode {
            EffectFixtureMode::StrobeOnly if value == 0 => {
                if beat && audio.bass > 0.5 {
                    value = (80.0 + audio.bass * 120.0) as u32;
                } else if audio.energy > 0.2 {
                    value = (6.0 + audio.energy * 100.0) as u32;
                }
            }
            EffectFixtureMode::StrobeFocus if value == 0 => {
                if beat && audio.bass > 0.6 {
                    value = (60.0 + audio.bass * 100.0) as u32;
                } else if audio.energy > 0.5 {
                    value = (6.0 + (audio.energy - 0.5) * 60.0) as u32;
                }
            }
            _ if value == 0 && beat && audio.bass > 0.8 => {
                value = (80.0 + audio.bass * 80.0) as u32;
            }
            _ => {}
        }
        value
    }

    fn strobe_effect_value(&self, config: &ValidatedShowConfig, audio: &AudioAnalysis) -> u32 {
        let settings = effects(config);
        if !settings.strobe_effect_enabled {
            return 0;
        }
        let mode = config.strobe_effect_mode();
        let speed = settings.strobe_effect_speed;
        match mode {
            StrobeEffectMode::Off | StrobeEffectMode::Unspecified => 0,
            StrobeEffectMode::Effect18Strobe => 180 + (speed * 75.0) as u32,
            StrobeEffectMode::Auto => {
                if self.is_drop {
                    return 180 + (speed * 75.0) as u32;
                }
                let (start, end) = if audio.energy < 0.4 {
                    (1_u64, 6_u64)
                } else if audio.energy < 0.7 {
                    (7, 12)
                } else {
                    (13, 17)
                };
                let effect = start + audio.estimated_bar % (end - start + 1);
                10 + (effect as u32 - 1) * 10 + ((speed * 0.5 + audio.bass * 0.5) * 9.0) as u32
            }
            specific => {
                let effect = specific as u32 - StrobeEffectMode::Effect1 as u32 + 1;
                10 + (effect - 1) * 10 + (speed * 9.0) as u32
            }
        }
    }

    fn update_energy_tracking(&mut self, energy: f32, now: f32) {
        self.energy_history.push_back(energy);
        if self.energy_history.len() > 40 {
            self.energy_history.pop_front();
        }
        let len = self.energy_history.len();
        let recent_count = len.min(10);
        let recent = self
            .energy_history
            .iter()
            .skip(len - recent_count)
            .sum::<f32>()
            / recent_count.max(1) as f32;
        let older_count = len.saturating_sub(10);
        let older = if older_count == 0 {
            recent
        } else {
            self.energy_history.iter().take(older_count).sum::<f32>() / older_count as f32
        };
        if recent > older + 0.3 && energy > 0.6 {
            if !self.is_drop {
                self.is_drop = true;
                self.drop_time = now;
                self.strobe_active = true;
                self.strobe_end_time = now + 0.5;
            }
        } else if now - self.drop_time > 2.0 {
            self.is_drop = false;
        }
        if self.strobe_active && now > self.strobe_end_time {
            self.strobe_active = false;
        }
    }

    fn apply_movement(
        &mut self,
        config: &ValidatedShowConfig,
        audio: &AudioAnalysis,
        beat: bool,
        bar: bool,
        delta_seconds: f32,
    ) {
        let settings = effects(config);
        let speed = settings.movement_speed;
        if speed <= 0.01 || audio.energy < 0.01 || audio.tempo <= 0.0 {
            return;
        }
        let mode = config.movement_mode();
        let mut movement_fixtures: Vec<_> = config
            .fixtures
            .iter()
            .enumerate()
            .filter_map(|(config_index, fixture)| {
                let (has_pan, has_tilt) = controllable_movement_axes(config, fixture);
                (has_pan || has_tilt).then_some((config_index, fixture, has_pan, has_tilt))
            })
            .collect();
        movement_fixtures
            .sort_by_key(|(config_index, fixture, _, _)| (fixture.position, *config_index));
        let movement_fixture_count = movement_fixtures.len();
        if movement_fixture_count == 0 {
            return;
        }
        if beat && mode == MovementMode::Chase {
            let chase = self
                .wall_corner_index
                .entry("__chase_index__".into())
                .or_default();
            *chase = (*chase + 1) % movement_fixture_count;
        }

        for (movement_index, (_, fixture, has_pan, has_tilt)) in
            movement_fixtures.into_iter().enumerate()
        {
            self.movement_target(
                fixture,
                audio,
                beat,
                bar,
                mode,
                speed,
                has_pan,
                has_tilt,
                movement_index,
                movement_fixture_count,
                delta_seconds,
            );
            self.interpolate_position(fixture, mode, speed, audio.tempo, delta_seconds);
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn movement_target(
        &mut self,
        fixture: &FixtureConfig,
        audio: &AudioAnalysis,
        beat: bool,
        bar: bool,
        mode: MovementMode,
        speed: f32,
        has_pan: bool,
        has_tilt: bool,
        movement_index: usize,
        movement_fixture_count: usize,
        delta_seconds: f32,
    ) {
        let key = fixture_key(fixture);
        let pan_range = fixture.pan_max.saturating_sub(fixture.pan_min) as f32;
        let tilt_range = fixture.tilt_max.saturating_sub(fixture.tilt_min) as f32;
        let pan_center = (fixture.pan_max + fixture.pan_min) as f32 / 2.0;
        let tilt_center = (fixture.tilt_max + fixture.tilt_min) as f32 / 2.0;
        let energy = audio.energy;
        let bass = audio.bass;
        let phase_offset = fixture.position as f32;

        match mode {
            MovementMode::Subtle => {
                if bar {
                    if has_pan {
                        let positions = [0.0, 0.1, -0.05, 0.08, -0.1, 0.05, -0.08, 0.0];
                        let factor = positions[audio.estimated_bar as usize % positions.len()];
                        self.target_pan.insert(
                            key.clone(),
                            pan_center + factor * pan_range / 2.0 * speed * 0.3,
                        );
                    }
                    if has_tilt {
                        let positions = [0.2, 0.3, 0.15, 0.35, 0.25, 0.1, 0.3, 0.2];
                        let factor = positions[audio.estimated_bar as usize % positions.len()];
                        self.target_tilt
                            .insert(key, tilt_center + factor * tilt_range / 2.0 * speed * 0.5);
                    }
                }
            }
            MovementMode::Standard | MovementMode::Unspecified => {
                if bar && has_pan {
                    let positions = [0.0, 0.5, 0.2, -0.4, 0.4, -0.5, -0.2, 0.0];
                    let factor = positions[audio.estimated_bar as usize % positions.len()];
                    self.target_pan.insert(
                        key.clone(),
                        pan_center + factor * pan_range / 2.0 * speed * (0.5 + energy * 0.5),
                    );
                }
                if beat && (energy > 0.25 || self.beats_since_move >= 4) && has_tilt {
                    let positions = [0.4, -0.2, 0.2, -0.4, 0.3, -0.1, 0.25, -0.3];
                    let factor = positions[audio.estimated_beat as usize % positions.len()];
                    self.target_tilt.insert(
                        key,
                        tilt_center + factor * tilt_range / 2.0 * speed * (0.5 + bass * 0.5),
                    );
                    self.beats_since_move = 0;
                }
            }
            MovementMode::Dramatic => {
                if (bar || (beat && energy > 0.5)) && has_pan {
                    let positions = [0.0, 0.9, -0.7, 0.5, -0.9, 0.7, -0.5, 0.8];
                    let index =
                        (audio.estimated_bar * 4 + audio.estimated_beat) as usize % positions.len();
                    self.target_pan.insert(
                        key.clone(),
                        pan_center + positions[index] * pan_range / 2.0 * speed,
                    );
                }
                if beat && (energy > 0.3 || self.beats_since_move >= 2) && has_tilt {
                    let positions = [0.8, -0.6, 0.5, -0.8, 0.9, -0.4, 0.6, -0.7];
                    let factor = positions[audio.estimated_beat as usize % positions.len()];
                    self.target_tilt.insert(
                        key,
                        tilt_center + factor * tilt_range / 2.0 * speed * (0.7 + bass * 0.3),
                    );
                    self.beats_since_move = 0;
                }
            }
            MovementMode::WallWash => {
                let positions = [
                    (-0.9, 0.7),
                    (-0.9, 0.3),
                    (-0.5, 0.9),
                    (0.0, 0.8),
                    (0.0, 0.4),
                    (0.5, 0.9),
                    (0.9, 0.3),
                    (0.9, 0.7),
                ];
                if bar {
                    let current = *self.wall_corner_index.get(&key).unwrap_or(&0);
                    let next = (current + 1) % positions.len();
                    self.wall_corner_index.insert(key.clone(), next);
                    let actual = (next + fixture.position as usize * 2) % positions.len();
                    let (pan, tilt) = positions[actual];
                    if has_pan {
                        self.target_pan
                            .insert(key.clone(), pan_center + pan * pan_range / 2.0 * speed);
                    }
                    if has_tilt {
                        self.target_tilt
                            .insert(key.clone(), tilt_center + tilt * tilt_range / 2.0 * speed);
                    }
                }
                if beat && bass > 0.6 && has_tilt {
                    let current = self.target_tilt.get(&key).copied().unwrap_or(tilt_center);
                    self.target_tilt.insert(
                        key,
                        (current + tilt_range / 2.0 * 0.1 * bass).min(fixture.tilt_max as f32),
                    );
                }
            }
            MovementMode::Sweep => {
                let tempo = tempo_scale(audio.tempo);
                let rate = 0.03 * speed * tempo;
                let mut phase = self.sweep_phase.get(&key).copied().unwrap_or(0.0);
                let mut direction = self.sweep_direction.get(&key).copied().unwrap_or(1.0);
                phase += delta_seconds * rate * direction;
                if phase >= 1.0 {
                    phase = 1.0;
                    direction = -1.0
                } else if phase <= 0.0 {
                    phase = 0.0;
                    direction = 1.0
                }
                self.sweep_phase.insert(key.clone(), phase);
                self.sweep_direction.insert(key.clone(), direction);
                let smooth = ease_in_out_sine(phase);
                if has_pan {
                    self.target_pan.insert(
                        key.clone(),
                        pan_center + (smooth * 2.0 - 1.0) * pan_range / 2.0 * 0.85,
                    );
                }
                if has_tilt {
                    let tilt_phase = (phase + phase_offset * 0.25) % 1.0;
                    let factor = 0.3 + ease_in_out_sine(tilt_phase) * 0.5;
                    self.target_tilt
                        .insert(key.clone(), tilt_center + factor * tilt_range / 2.0);
                }
                if energy > 0.6 {
                    self.sweep_phase
                        .insert(key, phase + delta_seconds * rate * 0.3);
                }
            }
            MovementMode::Random => {
                if beat && self.rng.random::<f32>() < 0.4 + energy * 0.4 {
                    if has_pan {
                        self.target_pan.insert(
                            key.clone(),
                            pan_center
                                + self.rng.random_range(-0.9..=0.9) * pan_range / 2.0 * speed,
                        );
                    }
                    if has_tilt {
                        self.target_tilt.insert(
                            key,
                            tilt_center
                                + self.rng.random_range(-0.3..=0.9) * tilt_range / 2.0 * speed,
                        );
                    }
                    self.beats_since_move = 0;
                }
            }
            MovementMode::Circle => {
                let rate = 0.08 * speed * tempo_scale(audio.tempo) * (0.8 + energy * 0.4);
                let mut phase =
                    self.sweep_phase.get(&key).copied().unwrap_or(0.0) + delta_seconds * rate * TAU;
                phase %= TAU;
                self.sweep_phase.insert(key.clone(), phase);
                if beat {
                    self.sweep_direction.insert(key.clone(), 1.0);
                }
                let pulse = (self.sweep_direction.get(&key).copied().unwrap_or(0.0)
                    - delta_seconds * 3.0)
                    .max(0.0);
                self.sweep_direction.insert(key.clone(), pulse);
                let angle = phase + phase_offset * PI / 3.0;
                let size = 0.5 * speed + pulse * 0.3 * bass;
                if has_pan {
                    self.target_pan.insert(
                        key.clone(),
                        pan_center + angle.cos() * size * pan_range / 2.0,
                    );
                }
                if has_tilt {
                    self.target_tilt.insert(
                        key,
                        tilt_center + angle.sin() * size * 0.7 * tilt_range / 2.0,
                    );
                }
            }
            MovementMode::Figure8 => {
                let rate = 0.06 * speed * tempo_scale(audio.tempo) * (0.7 + energy * 0.4);
                let mut phase =
                    self.sweep_phase.get(&key).copied().unwrap_or(0.0) + delta_seconds * rate * TAU;
                phase %= TAU;
                self.sweep_phase.insert(key.clone(), phase);
                let angle = phase + phase_offset * PI / 4.0;
                let size = 0.6 * speed * (0.8 + energy * 0.2);
                if has_pan {
                    self.target_pan.insert(
                        key.clone(),
                        pan_center + angle.cos() * size * pan_range / 2.0,
                    );
                }
                if has_tilt {
                    let factor = (2.0 * angle).sin() * 0.5 * size + 0.2 * size;
                    self.target_tilt
                        .insert(key, tilt_center + factor * tilt_range / 2.0);
                }
            }
            MovementMode::Ballyhoo => {
                let rate = 0.12 * speed * tempo_scale(audio.tempo) * (0.85 + energy * 0.3);
                let mut phase =
                    self.sweep_phase.get(&key).copied().unwrap_or(0.0) + delta_seconds * rate * TAU;
                phase %= TAU;
                let angle = phase + phase_offset * PI / 2.0;
                let size = 0.85 * speed;
                if has_pan {
                    self.target_pan.insert(
                        key.clone(),
                        pan_center + angle.sin() * size * pan_range / 2.0,
                    );
                }
                if has_tilt {
                    let factor = (angle + PI / 4.0).sin() * size * 0.6 + 0.2 * energy;
                    self.target_tilt
                        .insert(key.clone(), tilt_center + factor * tilt_range / 2.0);
                }
                if beat && bass > 0.7 {
                    phase = TAU - phase;
                }
                self.sweep_phase.insert(key, phase);
            }
            MovementMode::Fan => {
                let mut amount = self.sweep_phase.get(&key).copied().unwrap_or(0.5);
                let mut target = self.sweep_direction.get(&key).copied().unwrap_or(0.5);
                if bar {
                    target = if audio.estimated_bar.is_multiple_of(2) {
                        0.9
                    } else {
                        0.2
                    };
                    self.sweep_direction.insert(key.clone(), target);
                }
                amount += (target - amount)
                    * time_adjusted_factor(0.08 * speed * tempo_scale(audio.tempo), delta_seconds);
                self.sweep_phase.insert(key.clone(), amount);
                let normalized = if movement_fixture_count > 1 {
                    movement_index as f32 / (movement_fixture_count - 1) as f32 * 2.0 - 1.0
                } else {
                    0.0
                };
                if has_pan {
                    self.target_pan.insert(
                        key.clone(),
                        pan_center + normalized * amount * speed * 0.85 * pan_range / 2.0,
                    );
                }
                if has_tilt {
                    let pulse = if beat && bass > 0.4 { bass * 0.2 } else { 0.0 };
                    self.target_tilt.insert(
                        key,
                        tilt_center + (0.3 - amount * 0.4 + pulse) * speed * tilt_range / 2.0,
                    );
                }
            }
            MovementMode::Chase => {
                let chase = *self.wall_corner_index.get("__chase_index__").unwrap_or(&0);
                let positions = [
                    (0.8, 0.6),
                    (0.5, 0.8),
                    (0.0, 0.7),
                    (-0.5, 0.8),
                    (-0.8, 0.6),
                    (-0.6, 0.3),
                    (0.0, 0.2),
                    (0.6, 0.3),
                ];
                if movement_index == chase {
                    let (pan, tilt) =
                        positions[(chase + audio.estimated_bar as usize) % positions.len()];
                    if has_pan {
                        self.target_pan
                            .insert(key.clone(), pan_center + pan * pan_range / 2.0 * speed);
                    }
                    if has_tilt {
                        self.target_tilt
                            .insert(key, tilt_center + tilt * tilt_range / 2.0 * speed);
                    }
                } else {
                    let spread =
                        (movement_index as f32 / (movement_fixture_count - 1).max(1) as f32 - 0.5)
                            * 0.3;
                    if has_pan {
                        self.target_pan
                            .insert(key.clone(), pan_center + spread * pan_range / 2.0 * speed);
                    }
                    if has_tilt {
                        self.target_tilt
                            .insert(key, tilt_center + 0.2 * tilt_range / 2.0 * speed);
                    }
                }
            }
            MovementMode::StrobePosition => {
                if beat {
                    let positions: [(f32, f32); 8] = [
                        (0.9, 0.9),
                        (-0.9, 0.9),
                        (0.0, -0.5),
                        (0.7, 0.0),
                        (-0.7, 0.0),
                        (0.9, -0.3),
                        (-0.9, -0.3),
                        (0.0, 0.9),
                    ];
                    let index = (audio.estimated_beat
                        + fixture.position as u64 * 3
                        + audio.estimated_bar) as usize
                        % positions.len();
                    let (mut pan, mut tilt) = positions[index];
                    if energy > 0.6 {
                        pan += self.rng.random_range(-0.2..=0.2);
                        tilt += self.rng.random_range(-0.1..=0.2);
                    }
                    if has_pan {
                        self.target_pan.insert(
                            key.clone(),
                            pan_center + pan.clamp(-1.0, 1.0) * pan_range / 2.0 * speed,
                        );
                    }
                    if has_tilt {
                        self.target_tilt.insert(
                            key,
                            tilt_center + tilt.clamp(-1.0, 1.0) * tilt_range / 2.0 * speed,
                        );
                    }
                    self.beats_since_move = 0;
                }
            }
            MovementMode::Crazy => {
                let scale = tempo_scale(audio.tempo);
                let boost = 0.6 + energy;
                let mut pan_phase = self
                    .sweep_phase
                    .get(&key)
                    .copied()
                    .unwrap_or_else(|| self.rng.random::<f32>() * TAU);
                let mut tilt_phase = self
                    .sweep_direction
                    .get(&key)
                    .copied()
                    .unwrap_or_else(|| self.rng.random::<f32>() * TAU);
                if tilt_phase.abs() <= 1.0 {
                    tilt_phase = self.rng.random::<f32>() * TAU;
                }
                pan_phase = (pan_phase + delta_seconds * 0.18 * speed * scale * boost * TAU) % TAU;
                tilt_phase =
                    (tilt_phase + delta_seconds * 0.23 * speed * scale * boost * TAU) % TAU;
                if beat {
                    let roll = self.rng.random::<f32>();
                    if bass > 0.7 && roll < 0.4 {
                        pan_phase = TAU - pan_phase;
                    }
                    if energy > 0.6 && roll < 0.5 {
                        pan_phase += self.rng.random_range(0.5..=1.5);
                        tilt_phase += self.rng.random_range(0.3..=1.0);
                    }
                    if roll < 0.25 {
                        pan_phase = self.rng.random::<f32>() * TAU;
                        tilt_phase = self.rng.random::<f32>() * TAU;
                    }
                }
                if bar && self.rng.random::<f32>() < 0.3 {
                    tilt_phase = TAU - tilt_phase;
                }
                self.sweep_phase.insert(key.clone(), pan_phase);
                self.sweep_direction.insert(key.clone(), tilt_phase);
                let offset = phase_offset * PI * 0.7;
                let pan_factor =
                    ((pan_phase + offset).sin() + (pan_phase * 2.7 + offset).sin() * 0.3) / 1.3;
                let tilt_factor =
                    ((tilt_phase + offset * 0.5).sin() + (tilt_phase * 1.9).cos() * 0.4) / 1.4;
                if has_pan {
                    self.target_pan.insert(
                        key.clone(),
                        (fixture.pan_min as f32 + (pan_factor + 1.0) / 2.0 * pan_range)
                            .clamp(fixture.pan_min as f32, fixture.pan_max as f32),
                    );
                }
                if has_tilt {
                    self.target_tilt.insert(
                        key,
                        (fixture.tilt_min as f32 + (tilt_factor + 1.0) / 2.0 * tilt_range)
                            .clamp(fixture.tilt_min as f32, fixture.tilt_max as f32),
                    );
                }
                self.beats_since_move = 0;
            }
        }
    }

    fn interpolate_position(
        &mut self,
        fixture: &FixtureConfig,
        mode: MovementMode,
        speed: f32,
        tempo: f32,
        delta_seconds: f32,
    ) {
        let (mut pan_rate, mut tilt_rate) = match mode {
            MovementMode::Subtle => (0.06, 0.06),
            MovementMode::Standard | MovementMode::Unspecified => (0.12, 0.15),
            MovementMode::Dramatic => (0.18, 0.22),
            MovementMode::WallWash => (0.08, 0.10),
            MovementMode::Sweep => (0.05, 0.05),
            MovementMode::Random => (0.10, 0.12),
            MovementMode::Circle => (0.15, 0.15),
            MovementMode::Figure8 => (0.12, 0.12),
            MovementMode::Ballyhoo => (0.25, 0.25),
            MovementMode::Fan => (0.10, 0.12),
            MovementMode::Chase => (0.20, 0.22),
            MovementMode::StrobePosition => (0.45, 0.45),
            MovementMode::Crazy => (0.55, 0.55),
        };
        let multiplier = tempo_scale(tempo) * (0.2 + speed * 0.8);
        pan_rate = time_adjusted_factor(pan_rate * multiplier, delta_seconds);
        tilt_rate = time_adjusted_factor(tilt_rate * multiplier, delta_seconds);
        let key = fixture_key(fixture);
        let target_pan = self.target_pan.get(&key).copied().unwrap_or(128.0);
        let target_tilt = self.target_tilt.get(&key).copied().unwrap_or(128.0);
        if let Some(state) = self.states.get_mut(&key) {
            state.pan = (state.pan as f32 + (target_pan - state.pan as f32) * pan_rate)
                .clamp(0.0, 255.0) as u32;
            state.tilt = (state.tilt as f32 + (target_tilt - state.tilt as f32) * tilt_rate)
                .clamp(0.0, 255.0) as u32;
            let mode_offset = match mode {
                MovementMode::Dramatic | MovementMode::StrobePosition | MovementMode::Crazy => {
                    -20.0
                }
                MovementMode::Ballyhoo | MovementMode::Chase => -10.0,
                MovementMode::Sweep | MovementMode::Subtle | MovementMode::Figure8 => 15.0,
                _ => 0.0,
            };
            state.pan_tilt_speed =
                (127.0 - (tempo - REFERENCE_TEMPO) + mode_offset + (0.5 - speed) * 100.0)
                    .clamp(0.0, 255.0) as u32;
        }
    }

    fn apply_force_max_brightness(&mut self, config: &ValidatedShowConfig) {
        let settings = effects(config);
        for fixture in &config.fixtures {
            let max_dimmer = dmx(fixture.intensity_scale * settings.intensity);
            let profile = find_profile(config, fixture);
            let channels = effective_channels(fixture, profile);
            let has_dimmer = channels.iter().any(|channel| {
                channel.enabled && channel.fixed_value.is_none() && is_dimmer(&channel.channel_type)
            });
            let has_color = channels.iter().any(|channel| {
                channel.enabled && channel.fixed_value.is_none() && is_color(&channel.channel_type)
            });
            if let Some(state) = self.states.get_mut(&fixture_key(fixture)) {
                let color_max = max_color(state);
                if max_dimmer == 0 {
                    state.dimmer = 0;
                    scale_colors(state, 0);
                } else if color_max > 0 || state.dimmer > 0 || state.color_macro > 0 {
                    if has_dimmer {
                        state.dimmer = max_dimmer;
                    }
                    if has_color && color_max > 0 {
                        scale_colors(state, if has_dimmer { 255 } else { max_dimmer });
                    }
                }
            }
        }
    }

    fn apply_smoothing(&mut self, config: &ValidatedShowConfig, delta_seconds: f32) {
        let factor = effects(config).smooth_factor.clamp(0.0, 1.0);
        let blend = time_adjusted_factor(1.0 - factor, delta_seconds);
        let retained = 1.0 - blend;
        for fixture in &config.fixtures {
            let key = fixture_key(fixture);
            let Some(current) = self.states.get(&key) else {
                continue;
            };
            let Some(smoothed) = self.smoothed.get_mut(&key) else {
                continue;
            };
            macro_rules! smooth {
                ($field:ident) => {
                    smoothed.$field =
                        (smoothed.$field as f32 * retained + current.$field as f32 * blend) as u32;
                };
            }
            smooth!(red);
            smooth!(green);
            smooth!(blue);
            smooth!(white);
            smooth!(amber);
            smooth!(uv);
            smooth!(cyan);
            smooth!(magenta);
            smooth!(yellow);
            smooth!(dimmer);
            smooth!(zoom);
            smooth!(focus);
            smoothed.pan = current.pan;
            smoothed.pan_fine = current.pan_fine;
            smoothed.tilt = current.tilt;
            smoothed.tilt_fine = current.tilt_fine;
            smoothed.pan_tilt_speed = current.pan_tilt_speed;
            smoothed.strobe = current.strobe;
            smoothed.color_macro = current.color_macro;
            smoothed.effect = current.effect;
            smoothed.effect_speed = current.effect_speed;
            smoothed.effect_pattern = current.effect_pattern;
            smoothed.effect_rotation = current.effect_rotation;
            smoothed.gobo = current.gobo;
            smoothed.prism = current.prism;
            smoothed.iris = current.iris;
        }
    }

    fn map_universe(&self, config: &ValidatedShowConfig, universe_size: usize) -> Vec<u8> {
        let mut universe = vec![0_u8; universe_size];
        for fixture in &config.fixtures {
            let Some(state) = self.smoothed.get(&fixture_key(fixture)) else {
                continue;
            };
            let profile = find_profile(config, fixture);
            for channel in effective_channels(fixture, profile)
                .iter()
                .filter(|channel| channel.enabled)
            {
                let dmx_channel = fixture
                    .start_channel
                    .saturating_add(channel.offset)
                    .saturating_sub(1);
                if dmx_channel == 0 || dmx_channel as usize > universe_size {
                    continue;
                }
                let value = channel
                    .fixed_value
                    .unwrap_or_else(|| channel_value(state, channel));
                universe[dmx_channel as usize - 1] =
                    value.clamp(channel.min_value, channel.max_value).min(255) as u8;
            }
        }
        universe
    }

    fn zero_states(&mut self, config: &ValidatedShowConfig, immediate: bool) {
        for fixture in &config.fixtures {
            let key = fixture_key(fixture);
            if let Some(state) = self.states.get_mut(&key) {
                zero_light(state);
            }
            if immediate && let Some(state) = self.smoothed.get_mut(&key) {
                zero_light(state);
            }
        }
        self.rotation_phase = 0.0;
        self.smoothed_rotation = 0.0;
    }

    fn ordered_states(&self, config: &ValidatedShowConfig) -> Vec<FixtureState> {
        config
            .fixtures
            .iter()
            .filter_map(|fixture| self.smoothed.get(&fixture_key(fixture)).cloned())
            .collect()
    }
}

fn effects(config: &ValidatedShowConfig) -> &EffectsConfig {
    config.effects()
}

fn fixture_key(fixture: &FixtureConfig) -> String {
    if fixture.id.is_empty() {
        fixture.name.clone()
    } else {
        fixture.id.clone()
    }
}

fn default_state(fixture: &FixtureConfig) -> FixtureState {
    FixtureState {
        fixture_id: fixture_key(fixture),
        fixture_name: fixture.name.clone(),
        dimmer: 255,
        pan: 128,
        tilt: 128,
        zoom: 128,
        focus: 128,
        iris: 255,
        ..Default::default()
    }
}

fn find_profile<'a>(
    config: &'a ValidatedShowConfig,
    fixture: &FixtureConfig,
) -> Option<&'a FixtureProfile> {
    config
        .profiles
        .iter()
        .find(|profile| profile.name == fixture.profile_name)
}

fn effective_channels<'a>(
    fixture: &'a FixtureConfig,
    profile: Option<&'a FixtureProfile>,
) -> &'a [ChannelConfig] {
    if fixture.channels.is_empty() {
        profile.map_or(&[], |profile| profile.channels.as_slice())
    } else {
        &fixture.channels
    }
}

fn controllable_movement_axes(
    config: &ValidatedShowConfig,
    fixture: &FixtureConfig,
) -> (bool, bool) {
    let channels = effective_channels(fixture, find_profile(config, fixture));
    let has_pan = channels.iter().any(|channel| {
        channel.enabled && channel.fixed_value.is_none() && channel.channel_type == "position_pan"
    });
    let has_tilt = channels.iter().any(|channel| {
        channel.enabled && channel.fixed_value.is_none() && channel.channel_type == "position_tilt"
    });
    (has_pan, has_tilt)
}

fn dmx(value: f32) -> u32 {
    (clamp01(value) * 255.0) as u32
}

fn clamp01(value: f32) -> f32 {
    value.clamp(0.0, 1.0)
}

fn ease_out_cubic(value: f32) -> f32 {
    1.0 - (1.0 - clamp01(value)).powi(3)
}

fn ease_in_out_sine(value: f32) -> f32 {
    -((PI * value).cos() - 1.0) / 2.0
}

fn tempo_scale(tempo: f32) -> f32 {
    if tempo <= 0.0 {
        1.0
    } else {
        (tempo / REFERENCE_TEMPO).clamp(0.4, 1.8)
    }
}

fn time_adjusted_factor(reference_factor: f32, delta_seconds: f32) -> f32 {
    let reference_factor = reference_factor.clamp(0.0, 1.0);
    if reference_factor == 0.0 || delta_seconds <= 0.0 {
        return 0.0;
    }
    if reference_factor == 1.0 {
        return 1.0;
    }
    1.0 - (1.0 - reference_factor).powf(delta_seconds / REFERENCE_FRAME_SECONDS)
}

fn set_hsv(state: &mut FixtureState, hue: f32, saturation: f32, value: f32) {
    let (red, green, blue) = hsv_to_rgb(hue.rem_euclid(1.0), clamp01(saturation), clamp01(value));
    state.red = dmx(red);
    state.green = dmx(green);
    state.blue = dmx(blue);
}

fn hsv_to_rgb(hue: f32, saturation: f32, value: f32) -> (f32, f32, f32) {
    if saturation == 0.0 {
        return (value, value, value);
    }
    let sector = (hue * 6.0).floor() as i32;
    let fraction = hue * 6.0 - sector as f32;
    let p = value * (1.0 - saturation);
    let q = value * (1.0 - saturation * fraction);
    let t = value * (1.0 - saturation * (1.0 - fraction));
    match sector.rem_euclid(6) {
        0 => (value, t, p),
        1 => (q, value, p),
        2 => (p, value, t),
        3 => (p, q, value),
        4 => (t, p, value),
        _ => (value, p, q),
    }
}

fn rgb_to_hsv(red: f32, green: f32, blue: f32) -> (f32, f32, f32) {
    let max = red.max(green).max(blue);
    let min = red.min(green).min(blue);
    let delta = max - min;
    let hue = if delta == 0.0 {
        0.0
    } else if max == red {
        ((green - blue) / delta).rem_euclid(6.0) / 6.0
    } else if max == green {
        ((blue - red) / delta + 2.0) / 6.0
    } else {
        ((red - green) / delta + 4.0) / 6.0
    };
    let saturation = if max == 0.0 { 0.0 } else { delta / max };
    (hue, saturation, max)
}

fn apply_dual_color_mapping(state: &mut FixtureState, profile: &FixtureProfile, hue: f32) {
    if profile.dual_color_map.len() < 3 {
        return;
    }
    let brightness = state.red.max(state.green).max(state.blue).max(1) as f32 / 255.0;
    let values: Vec<u32> = profile
        .dual_color_map
        .iter()
        .map(|mapping| {
            let contribution = [mapping.primary_hue, mapping.secondary_hue]
                .into_iter()
                .flatten()
                .map(|candidate| (1.0 - hue_distance(hue, candidate) * 6.0).max(0.0))
                .fold(0.0_f32, f32::max);
            dmx(contribution * brightness)
        })
        .collect();
    state.red = values[0];
    state.green = values[1];
    state.blue = values[2];
}

fn hue_distance(first: f32, second: f32) -> f32 {
    let difference = (first - second).abs();
    difference.min(1.0 - difference)
}

fn rgb_to_color_macro(red: u32, green: u32, blue: u32, energy: f32) -> u32 {
    let red_on = red > 50;
    let green_on = green > 50;
    let blue_on = blue > 50;
    let minimum = red.min(green).min(blue);
    let maximum = red.max(green).max(blue);
    let white_like = maximum - minimum < 60 && minimum > 150;
    if energy > 0.85 {
        return (230.0 + (energy - 0.85) * 166.0).min(255.0) as u32;
    }
    let count = red_on as u8 + green_on as u8 + blue_on as u8;
    match (red_on, green_on, blue_on, white_like, count) {
        (_, _, _, true, 3) => 208,
        (_, _, _, true, _) => 58,
        (_, _, _, false, 3) => 163,
        (true, true, _, _, _) => {
            if red + green > 350 {
                178
            } else {
                73
            }
        }
        (true, _, true, _, _) => 88,
        (_, true, true, _, _) => {
            if green + blue > 350 {
                193
            } else {
                118
            }
        }
        (_, _, true, _, _) => {
            if blue > 200 {
                148
            } else {
                43
            }
        }
        (_, true, _, _, _) => {
            if green > 200 {
                133
            } else {
                28
            }
        }
        (true, _, _, _, _) => {
            if red > 200 {
                103
            } else {
                13
            }
        }
        _ => 222,
    }
}

fn capability_range(
    channel: &ChannelConfig,
    predicate: impl Fn(&ChannelCapability) -> bool,
) -> Option<(u32, u32)> {
    let mut matching = channel
        .capabilities
        .iter()
        .filter(|capability| capability.usable && predicate(capability));
    let first = matching.next()?;
    let mut minimum = first.min_value;
    let mut maximum = first.max_value;
    for capability in matching {
        minimum = minimum.min(capability.min_value);
        maximum = maximum.max(capability.max_value);
    }
    Some((minimum, maximum))
}

fn usable_range(channel: &ChannelConfig) -> (u32, u32) {
    capability_range(channel, |_| true).unwrap_or((channel.min_value, channel.max_value))
}

fn scale_range(value: f32, (minimum, maximum): (u32, u32)) -> u32 {
    (minimum as f32 + clamp01(value) * maximum.saturating_sub(minimum) as f32) as u32
}

fn is_dimmer(channel_type: &str) -> bool {
    matches!(
        channel_type,
        "intensity" | "intensity_dimmer" | "intensity_master_dimmer"
    )
}

fn is_color(channel_type: &str) -> bool {
    matches!(
        channel_type,
        "intensity_red"
            | "intensity_green"
            | "intensity_blue"
            | "intensity_white"
            | "intensity_amber"
            | "intensity_uv"
            | "intensity_cyan"
            | "intensity_magenta"
            | "intensity_yellow"
    )
}

fn max_color(state: &FixtureState) -> u32 {
    [
        state.red,
        state.green,
        state.blue,
        state.white,
        state.amber,
        state.uv,
        state.cyan,
        state.magenta,
        state.yellow,
    ]
    .into_iter()
    .max()
    .unwrap_or_default()
}

fn scale_colors(state: &mut FixtureState, target: u32) {
    let current = max_color(state);
    if current == 0 {
        return;
    }
    let scale = target.min(255) as f32 / current as f32;
    state.red = (state.red as f32 * scale).min(255.0) as u32;
    state.green = (state.green as f32 * scale).min(255.0) as u32;
    state.blue = (state.blue as f32 * scale).min(255.0) as u32;
    state.white = (state.white as f32 * scale).min(255.0) as u32;
    state.amber = (state.amber as f32 * scale).min(255.0) as u32;
    state.uv = (state.uv as f32 * scale).min(255.0) as u32;
    state.cyan = (state.cyan as f32 * scale).min(255.0) as u32;
    state.magenta = (state.magenta as f32 * scale).min(255.0) as u32;
    state.yellow = (state.yellow as f32 * scale).min(255.0) as u32;
}

fn channel_value(state: &FixtureState, channel: &ChannelConfig) -> u32 {
    match channel.channel_type.as_str() {
        "intensity" | "intensity_dimmer" => state.dimmer,
        "intensity_master_dimmer" => master_dimmer(state, channel),
        "intensity_red" => state.red,
        "intensity_green" => state.green,
        "intensity_blue" => state.blue,
        "intensity_white" => state.white,
        "intensity_amber" => state.amber,
        "intensity_uv" => state.uv,
        "intensity_cyan" => state.cyan,
        "intensity_magenta" => state.magenta,
        "intensity_yellow" => state.yellow,
        "position_pan" => state.pan,
        "position_pan_fine" => state.pan_fine,
        "position_tilt" => state.tilt,
        "position_tilt_fine" => state.tilt_fine,
        "speed_pan_tilt_fast_slow" => state.pan_tilt_speed,
        "speed_pan_tilt_slow_fast" => 255 - state.pan_tilt_speed.min(255),
        "shutter_strobe" | "shutter_strobe_slow_fast" | "shutter_strobe_fast_slow" => state.strobe,
        "color_macro" | "color_wheel" => state.color_macro,
        "effect" => state.effect,
        "effect_speed" => state.effect_speed,
        "effect_pattern" | "effect_pattern_speed" => state.effect_pattern,
        "gobo_wheel" | "gobo_index" => state.gobo,
        "prism" | "prism_rotation" => state.prism,
        "beam_zoom_small_big" | "beam_zoom_big_small" => state.zoom,
        "beam_focus_near_far" | "beam_focus_far_near" => state.focus,
        "shutter_iris_min_to_max" | "shutter_iris_max_to_min" => state.iris,
        "nothing" | "fixed" | "maintenance" => channel.default_value,
        unsupported => {
            debug_assert!(
                false,
                "unsupported channel type reached effects: {unsupported}"
            );
            channel.default_value
        }
    }
}

fn master_dimmer(state: &FixtureState, channel: &ChannelConfig) -> u32 {
    if channel.capabilities.is_empty() {
        return state.dimmer;
    }
    let named = |needle: &str| {
        channel
            .capabilities
            .iter()
            .find(|capability| capability.name.to_lowercase().contains(needle))
    };
    if state.dimmer < 5 {
        return named("off")
            .or_else(|| named("no function"))
            .map_or(0, |capability| capability.min_value);
    }
    if state.strobe > 0
        && let Some(capability) = named("strobe")
    {
        return capability.min_value
            + (state.strobe as f32 / 255.0
                * capability.max_value.saturating_sub(capability.min_value) as f32)
                as u32;
    }
    if state.dimmer >= 250
        && let Some(capability) = named("open")
    {
        return capability.min_value;
    }
    if let Some(capability) = named("dimmer") {
        return capability.min_value
            + ((state.dimmer.saturating_sub(5)) as f32 / 244.0
                * capability.max_value.saturating_sub(capability.min_value) as f32)
                as u32;
    }
    state.dimmer
}

fn zero_light(state: &mut FixtureState) {
    state.red = 0;
    state.green = 0;
    state.blue = 0;
    state.white = 0;
    state.amber = 0;
    state.uv = 0;
    state.cyan = 0;
    state.magenta = 0;
    state.yellow = 0;
    state.dimmer = 0;
    state.strobe = 0;
    state.color_macro = 0;
    state.effect = 0;
    state.effect_pattern = 0;
    state.effect_rotation = 0.0;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::default_show_config;

    fn validated(config: crate::proto::v1::ShowConfig) -> ValidatedShowConfig {
        ValidatedShowConfig::new(config, true).expect("test configuration should validate")
    }

    fn test_channel(offset: u32, channel_type: &str) -> ChannelConfig {
        ChannelConfig {
            offset,
            name: channel_type.into(),
            channel_type: channel_type.into(),
            default_value: 0,
            min_value: 0,
            max_value: 255,
            enabled: true,
            ..Default::default()
        }
    }

    fn test_fixture(
        id: &str,
        start_channel: u32,
        position: u32,
        channels: Vec<ChannelConfig>,
    ) -> FixtureConfig {
        FixtureConfig {
            id: id.into(),
            name: id.into(),
            start_channel,
            position,
            intensity_scale: 1.0,
            pan_min: 0,
            pan_max: 255,
            tilt_min: 0,
            tilt_max: 255,
            channels,
            ..Default::default()
        }
    }

    const VISUALIZATION_MODES: [VisualizationMode; 7] = [
        VisualizationMode::Energy,
        VisualizationMode::FrequencySplit,
        VisualizationMode::BeatPulse,
        VisualizationMode::ColorCycle,
        VisualizationMode::RainbowWave,
        VisualizationMode::StrobeBeat,
        VisualizationMode::RandomFlash,
    ];
    const MOVEMENT_MODES: [MovementMode; 13] = [
        MovementMode::Subtle,
        MovementMode::Standard,
        MovementMode::Dramatic,
        MovementMode::WallWash,
        MovementMode::Sweep,
        MovementMode::Random,
        MovementMode::Circle,
        MovementMode::Figure8,
        MovementMode::Ballyhoo,
        MovementMode::Fan,
        MovementMode::Chase,
        MovementMode::StrobePosition,
        MovementMode::Crazy,
    ];

    #[test]
    fn blackout_zeroes_the_entire_universe() {
        let config = validated(default_show_config(true));
        let output = EffectsEngine::default().process(
            &config,
            &AudioAnalysis::default(),
            &[],
            true,
            Duration::from_millis(25),
        );
        assert_eq!(output.universe, vec![0; 512]);
        assert!(output.fixture_states.iter().all(|state| state.dimmer == 0));
    }

    #[test]
    fn color_macro_never_selects_the_off_range() {
        assert!(rgb_to_color_macro(0, 0, 0, 0.0) > 5);
        assert_eq!(rgb_to_color_macro(255, 0, 0, 0.5), 103);
    }

    #[test]
    fn hue_wrap_distance_is_shortest_path() {
        approx::assert_abs_diff_eq!(hue_distance(0.98, 0.02), 0.04, epsilon = 0.0001);
    }

    #[test]
    fn stage_rotation_matches_manual_and_automatic_modes() {
        let mut config = default_show_config(true);
        let engine = EffectsEngine {
            rotation_phase: 0.25,
            ..Default::default()
        };

        config
            .effects
            .as_mut()
            .expect("effects configuration")
            .rotation_mode = RotationMode::ManualSlow as i32;
        let config = validated(config);
        approx::assert_abs_diff_eq!(engine.preview_rotation(&config), 1.0, epsilon = 0.0001);

        let mut config = config.into_proto();
        config
            .effects
            .as_mut()
            .expect("effects configuration")
            .rotation_mode = RotationMode::AutoSlow as i32;
        let config = validated(config);
        approx::assert_abs_diff_eq!(engine.preview_rotation(&config), 0.25, epsilon = 0.0001);
    }

    #[test]
    fn stage_rotation_survives_fixture_smoothing() {
        let config = validated(default_show_config(true));
        let fixture_key = fixture_key(&config.fixtures[0]);
        let mut engine = EffectsEngine::default();
        engine.ensure_fixtures(&config);
        engine
            .states
            .get_mut(&fixture_key)
            .expect("fixture state")
            .effect_rotation = 0.75;

        engine.apply_smoothing(&config, REFERENCE_FRAME_SECONDS);

        approx::assert_abs_diff_eq!(
            engine
                .smoothed
                .get(&fixture_key)
                .expect("smoothed fixture state")
                .effect_rotation,
            0.75,
            epsilon = 0.0001
        );
    }

    #[test]
    fn every_legacy_visualization_and_movement_mode_produces_bounded_output() {
        for visualization in VISUALIZATION_MODES {
            for movement in MOVEMENT_MODES {
                let mut config = default_show_config(true);
                config.fixtures = vec![FixtureConfig {
                    id: "moving-head".into(),
                    name: "Moving head".into(),
                    profile_name: "Purelight Muvy WashQ 14ch".into(),
                    start_channel: 1,
                    position: 0,
                    intensity_scale: 0.85,
                    pan_min: 16,
                    pan_max: 240,
                    tilt_min: 32,
                    tilt_max: 224,
                    channels: Vec::new(),
                }];
                let settings = config.effects.as_mut().expect("effects configuration");
                settings.mode = visualization as i32;
                settings.movement_mode = movement as i32;
                settings.movement_speed = 1.0;
                settings.smooth_factor = 0.25;
                let config = validated(config);
                let mut engine = EffectsEngine::default();
                let mut output = None;
                for beat in 1..=8 {
                    let audio = AudioAnalysis {
                        energy: 0.8,
                        rms: 0.65,
                        bass: 0.9,
                        mid: 0.6,
                        high: 0.45,
                        tempo: 128.0,
                        beat_detected: true,
                        beat_confidence: 0.9,
                        estimated_beat: beat,
                        estimated_bar: beat / 4,
                        ..Default::default()
                    };
                    output = Some(engine.process(
                        &config,
                        &audio,
                        &[],
                        false,
                        Duration::from_millis(25),
                    ));
                }
                let output = output.expect("effect output should be produced");
                assert_eq!(output.universe.len(), 512);
                assert!(output.universe.iter().any(|value| *value > 0));
                let state = &output.fixture_states[0];
                assert!((16..=240).contains(&state.pan));
                assert!((32..=224).contains(&state.tilt));
            }
        }
    }

    #[test]
    fn fixed_channel_values_still_respect_fixture_ranges() {
        let mut config = default_show_config(true);
        config.fixtures[0].channels = vec![ChannelConfig {
            offset: 1,
            name: "Fixed".into(),
            channel_type: "fixed".into(),
            fixed_value: Some(220),
            min_value: 10,
            max_value: 100,
            enabled: true,
            ..Default::default()
        }];
        let audio = AudioAnalysis {
            energy: 0.8,
            tempo: 120.0,
            ..Default::default()
        };
        let config = validated(config);
        let output = EffectsEngine::default().process(
            &config,
            &audio,
            &[],
            false,
            Duration::from_millis(25),
        );
        assert_eq!(output.universe[0], 100);
    }

    #[test]
    fn movement_phase_is_stable_across_output_rates() {
        fn phase_after_one_second(fps: u32) -> f32 {
            let mut config = default_show_config(true);
            let settings = config.effects.as_mut().expect("effects configuration");
            settings.movement_enabled = true;
            settings.movement_mode = MovementMode::Circle as i32;
            let config = validated(config);
            let fixture = fixture_key(&config.fixtures[0]);
            let audio = AudioAnalysis {
                energy: 0.8,
                tempo: 120.0,
                estimated_beat: 1,
                ..Default::default()
            };
            let mut engine = EffectsEngine::default();
            let delta = Duration::from_secs_f64(1.0 / f64::from(fps));
            for _ in 0..fps {
                engine.process(&config, &audio, &[], false, delta);
            }
            engine.sweep_phase[&fixture]
        }

        approx::assert_abs_diff_eq!(
            phase_after_one_second(20),
            phase_after_one_second(40),
            epsilon = 0.0001
        );
    }

    #[test]
    fn chase_uses_only_controllable_movers_in_show_order() {
        let mut config = default_show_config(true);
        let mut fixed_pan = test_channel(1, "position_pan");
        fixed_pan.fixed_value = Some(0);
        config.fixtures = vec![
            test_fixture("static-light", 1, 0, vec![test_channel(1, "intensity_red")]),
            test_fixture("fixed-mover", 2, 20, vec![fixed_pan]),
            test_fixture(
                "later-mover",
                3,
                30,
                vec![
                    test_channel(1, "position_pan"),
                    test_channel(2, "position_tilt"),
                ],
            ),
            test_fixture(
                "earlier-mover",
                5,
                10,
                vec![
                    test_channel(1, "position_pan"),
                    test_channel(2, "position_tilt"),
                ],
            ),
        ];
        let settings = config.effects.as_mut().expect("effects configuration");
        settings.movement_mode = MovementMode::Chase as i32;
        settings.movement_speed = 1.0;
        let config = validated(config);
        let mut engine = EffectsEngine::default();

        engine.process(
            &config,
            &AudioAnalysis {
                energy: 0.8,
                tempo: 120.0,
                estimated_beat: 1,
                ..Default::default()
            },
            &[],
            false,
            Duration::from_millis(25),
        );

        assert_eq!(engine.wall_corner_index["__chase_index__"], 1);
        approx::assert_abs_diff_eq!(engine.target_pan["earlier-mover"], 108.375);
        approx::assert_abs_diff_eq!(engine.target_pan["later-mover"], 191.25);
        assert_eq!(engine.target_pan["fixed-mover"], 128.0);

        engine.process(
            &config,
            &AudioAnalysis {
                energy: 0.8,
                tempo: 120.0,
                estimated_beat: 2,
                ..Default::default()
            },
            &[],
            false,
            Duration::from_millis(25),
        );

        assert_eq!(engine.wall_corner_index["__chase_index__"], 0);
        approx::assert_abs_diff_eq!(engine.target_pan["earlier-mover"], 229.5);
        approx::assert_abs_diff_eq!(engine.target_pan["later-mover"], 146.625);
    }

    #[test]
    fn single_mover_chase_does_not_pause_for_static_fixtures() {
        let mut config = default_show_config(true);
        config.fixtures = vec![
            test_fixture(
                "static-before",
                1,
                0,
                vec![test_channel(1, "intensity_red")],
            ),
            test_fixture(
                "only-mover",
                2,
                1,
                vec![
                    test_channel(1, "position_pan"),
                    test_channel(2, "position_tilt"),
                ],
            ),
            test_fixture(
                "static-after",
                4,
                2,
                vec![test_channel(1, "intensity_blue")],
            ),
        ];
        let settings = config.effects.as_mut().expect("effects configuration");
        settings.movement_mode = MovementMode::Chase as i32;
        settings.movement_speed = 1.0;
        let config = validated(config);
        let mut engine = EffectsEngine::default();

        for estimated_beat in [1, 2] {
            engine.process(
                &config,
                &AudioAnalysis {
                    energy: 0.8,
                    tempo: 120.0,
                    estimated_beat,
                    ..Default::default()
                },
                &[],
                false,
                Duration::from_millis(25),
            );

            assert_eq!(engine.wall_corner_index["__chase_index__"], 0);
            approx::assert_abs_diff_eq!(engine.target_pan["only-mover"], 229.5);
        }
    }

    #[test]
    fn fan_spreads_only_movement_capable_fixtures() {
        let mut config = default_show_config(true);
        config.fixtures = vec![
            test_fixture("static-light", 1, 0, vec![test_channel(1, "intensity_red")]),
            test_fixture(
                "right-mover",
                2,
                30,
                vec![
                    test_channel(1, "position_pan"),
                    test_channel(2, "position_tilt"),
                ],
            ),
            test_fixture(
                "left-mover",
                4,
                10,
                vec![
                    test_channel(1, "position_pan"),
                    test_channel(2, "position_tilt"),
                ],
            ),
        ];
        let settings = config.effects.as_mut().expect("effects configuration");
        settings.movement_mode = MovementMode::Fan as i32;
        settings.movement_speed = 1.0;
        let config = validated(config);
        let mut engine = EffectsEngine::default();

        engine.process(
            &config,
            &AudioAnalysis {
                energy: 0.8,
                tempo: 120.0,
                ..Default::default()
            },
            &[],
            false,
            Duration::from_millis(25),
        );

        assert!(engine.target_pan["left-mover"] < 127.5);
        assert!(engine.target_pan["right-mover"] > 127.5);
    }
}
