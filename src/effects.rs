use std::{
    collections::{HashMap, VecDeque},
    f32::consts::{PI, TAU},
    time::Duration,
};

use rand::{Rng, SeedableRng, rngs::StdRng, seq::IndexedRandom};

use crate::{
    config::ValidatedShowConfig,
    grandma2::{DmxSemantic, MappedChannel, MappedFunction, ParsedFixtureType},
    proto::v1::{
        AudioAnalysis, EffectDriver, EffectFixtureMode, EffectRuntimeStatus, EffectsConfig,
        EnergyTier, FixtureConfig, FixtureState, FixtureVisualKind, MovementMode, RgbColor,
        RotationMode, StrobeEffectMode, VisualizationMode,
    },
};

const REFERENCE_FRAME_SECONDS: f32 = 0.025;
const MAX_EFFECT_STEP_SECONDS: f32 = 0.1;
const REFERENCE_TEMPO: f32 = 120.0;
const REFERENCE_AXIS_RANGE_DEGREES: f32 = 360.0;

#[derive(Debug, Clone, Copy)]
struct CrazyMotionState {
    pan_phase: f32,
    tilt_phase: f32,
    pan_velocity: f32,
    tilt_velocity: f32,
}

impl CrazyMotionState {
    fn new(position: u32) -> Self {
        let offset = position as f32 * PI * 0.7;
        Self {
            pan_phase: offset.rem_euclid(TAU),
            tilt_phase: (offset + PI * 0.65).rem_euclid(TAU),
            pan_velocity: 1.0,
            tilt_velocity: 1.0,
        }
    }
}

#[derive(Debug, Clone)]
pub struct EffectOutput {
    pub fixture_states: Vec<FixtureState>,
    pub universe: Vec<u8>,
    pub runtime: EffectRuntimeStatus,
}

pub struct EffectsEngine {
    states: HashMap<String, FixtureState>,
    smoothed: HashMap<String, FixtureState>,
    target_pan: HashMap<String, f32>,
    target_tilt: HashMap<String, f32>,
    sweep_phase: HashMap<String, f32>,
    sweep_direction: HashMap<String, f32>,
    circle_phase: HashMap<String, f32>,
    circle_pulse: HashMap<String, f32>,
    figure_eight_phase: HashMap<String, f32>,
    ballyhoo_phase: HashMap<String, f32>,
    ballyhoo_direction: HashMap<String, f32>,
    fan_amount: HashMap<String, f32>,
    fan_target: HashMap<String, f32>,
    crazy_motion: HashMap<String, CrazyMotionState>,
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
            circle_phase: HashMap::new(),
            circle_pulse: HashMap::new(),
            figure_eight_phase: HashMap::new(),
            ballyhoo_phase: HashMap::new(),
            ballyhoo_direction: HashMap::new(),
            fan_amount: HashMap::new(),
            fan_target: HashMap::new(),
            crazy_motion: HashMap::new(),
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
                runtime: self.runtime_status(config, audio, false),
            };
        }

        let now = self.elapsed_seconds;
        self.update_album_hues(album_colors);
        let beat_triggered = audio.estimated_beat != self.last_beat;
        let beat_accent_active = beat_triggered && beat_response(config) > 0.0;
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
                beat_accent_active,
                bar_triggered,
                delta_seconds,
            );
            if effects(config).movement_enabled {
                self.apply_movement(
                    config,
                    audio,
                    beat_accent_active,
                    bar_triggered,
                    delta_seconds,
                );
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
            runtime: self.runtime_status(config, audio, beat_accent_active),
        }
    }

    fn runtime_status(
        &self,
        config: &ValidatedShowConfig,
        audio: &AudioAnalysis,
        beat_accent_active: bool,
    ) -> EffectRuntimeStatus {
        let visualization_mode = config.visualization_mode();
        let harmony_palette_active =
            effects(config).harmony_palette_enabled && audio.harmonic_confidence >= 0.18;
        let mut active_drivers: Vec<i32> = match visualization_mode {
            VisualizationMode::Energy | VisualizationMode::Unspecified => vec![
                EffectDriver::Energy as i32,
                EffectDriver::Bass as i32,
                EffectDriver::Mid as i32,
                EffectDriver::Beat as i32,
                EffectDriver::Palette as i32,
            ],
            VisualizationMode::FrequencySplit => vec![
                EffectDriver::Bass as i32,
                EffectDriver::Mid as i32,
                EffectDriver::High as i32,
                EffectDriver::Beat as i32,
            ],
            VisualizationMode::BeatPulse => vec![
                EffectDriver::Beat as i32,
                EffectDriver::Downbeat as i32,
                EffectDriver::Palette as i32,
            ],
            VisualizationMode::ColorCycle => vec![
                EffectDriver::Beat as i32,
                EffectDriver::Palette as i32,
                EffectDriver::Time as i32,
            ],
            VisualizationMode::RainbowWave => vec![
                EffectDriver::Energy as i32,
                EffectDriver::Beat as i32,
                EffectDriver::Time as i32,
            ],
            VisualizationMode::StrobeBeat => {
                vec![EffectDriver::Beat as i32, EffectDriver::Bass as i32]
            }
            VisualizationMode::RandomFlash => {
                vec![EffectDriver::Beat as i32, EffectDriver::Palette as i32]
            }
        };
        if self.is_drop {
            active_drivers.push(EffectDriver::Structure as i32);
        }
        if harmony_palette_active {
            active_drivers.push(EffectDriver::Harmony as i32);
        }
        let rendered_color = average_rendered_color(self.smoothed.values());
        EffectRuntimeStatus {
            visualization_mode: visualization_mode as i32,
            movement_mode: config.movement_mode() as i32,
            rotation_mode: config.rotation_mode() as i32,
            strobe_effect_mode: config.strobe_effect_mode() as i32,
            palette_index: self.color_index as u32,
            rendered_color: Some(rendered_color),
            drop_active: self.is_drop,
            strobe_active: self.strobe_active,
            beat_accent_active,
            beat_response: beat_response(config),
            energy_tier: energy_tier(audio.energy) as i32,
            active_drivers,
            effect_cycle_position: (audio.estimated_beat % 32) as u32 + 1,
            harmony_palette_active,
        }
    }

    fn ensure_fixtures(&mut self, config: &ValidatedShowConfig) {
        for fixture in &config.fixtures {
            let key = fixture_key(fixture);
            let fixture_type = find_fixture_type(config, fixture);
            let pan_default = axis_default_position(fixture, fixture_type, DmxSemantic::Pan);
            let tilt_default = axis_default_position(fixture, fixture_type, DmxSemantic::Tilt);
            self.states
                .entry(key.clone())
                .or_insert_with(|| default_state(fixture, fixture_type));
            self.smoothed
                .entry(key.clone())
                .or_insert_with(|| default_state(fixture, fixture_type));
            self.target_pan.entry(key.clone()).or_insert(pan_default);
            self.target_tilt.entry(key.clone()).or_insert(tilt_default);
            self.sweep_phase.entry(key.clone()).or_insert(0.0);
            self.sweep_direction.entry(key.clone()).or_insert(1.0);
            self.crazy_motion
                .entry(key.clone())
                .or_insert_with(|| CrazyMotionState::new(fixture.position));
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
        if settings.harmony_palette_enabled && audio.harmonic_confidence >= 0.18 {
            let circle_of_fifths_position = (audio.key_pitch_class * 7) % 12;
            self.target_hue = (circle_of_fifths_position as f32 / 12.0 + audio.mid * 0.04) % 1.0;
        } else if !self.album_hues.is_empty() {
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
            self.pulse_intensity = (0.3 + audio.bass * 0.4) * beat_response(config);
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
                intensity = (intensity + 0.2 * beat_response(config)).min(1.0);
            }
            if let Some(state) = self.states.get_mut(&fixture_key(fixture)) {
                set_hsv(state, hue, 0.9, intensity);
                state.dimmer = dmx(intensity);
            }
        }
    }

    fn beat_pulse_mode(&mut self, config: &ValidatedShowConfig, audio: &AudioAnalysis, beat: bool) {
        let palette = [0.0, 0.15, 0.55, 0.75, 0.9];
        let base_hue =
            if effects(config).harmony_palette_enabled && audio.harmonic_confidence >= 0.18 {
                ((audio.key_pitch_class * 7) % 12) as f32 / 12.0
            } else if self.album_hues.is_empty() {
                palette[audio.estimated_bar as usize % palette.len()]
            } else {
                self.album_hues[audio.estimated_bar as usize % self.album_hues.len()]
            };
        let settings = effects(config);
        for fixture in &config.fixtures {
            let scale = fixture.intensity_scale * settings.intensity;
            let (brightness, hue) = if beat {
                (
                    scale * (0.15 + 0.85 * beat_response(config)).min(1.0),
                    (base_hue + fixture.position as f32 * 0.05) % 1.0,
                )
            } else {
                (
                    scale
                        * (0.12
                            + (1.0 - ease_out_cubic(audio.beat_position))
                                * 0.68
                                * beat_response(config))
                        .min(1.0),
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
        let base_hue =
            if effects(config).harmony_palette_enabled && audio.harmonic_confidence >= 0.18 {
                let harmonic_hue = ((audio.key_pitch_class * 7) % 12) as f32 / 12.0;
                (harmonic_hue + audio.beat_position * 0.04).rem_euclid(1.0)
            } else if self.album_hues.is_empty() {
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
            0.2 * beat_response(config)
        } else {
            0.2 * (1.0 - audio.beat_position) * beat_response(config)
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
                brightness = (brightness + 0.15 * beat_response(config)).min(1.0);
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
        let response = beat_response(config).min(1.0);
        for fixture in &config.fixtures {
            let scale = fixture.intensity_scale * settings.intensity;
            if let Some(state) = self.states.get_mut(&fixture_key(fixture)) {
                if beat {
                    let brightness = scale * (0.12 + 0.88 * response);
                    state.red = dmx(brightness);
                    state.green = dmx(brightness);
                    state.blue = dmx(brightness);
                    state.dimmer = dmx(brightness);
                    state.strobe = (200.0 * response) as u32;
                } else {
                    let decay = (1.0 - audio.beat_position * 3.0).max(0.0) * response;
                    let brightness = dmx((0.06 + decay * 0.94) * scale);
                    state.red = brightness;
                    state.green = brightness;
                    state.blue = brightness;
                    state.dimmer = brightness;
                    state.strobe = if audio.beat_position < 0.3 {
                        (200.0 * response) as u32
                    } else {
                        0
                    };
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
        let response = beat_response(config).min(1.0);
        let mut flash_names = Vec::new();
        let flash_hue = if beat && response > 0.0 && !config.fixtures.is_empty() {
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
                    let brightness = scale * (0.12 + 0.88 * response);
                    set_hsv(state, flash_hue, 1.0, brightness);
                    state.dimmer = dmx(brightness);
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
        let pattern_speed = dmx(effects(config).strobe_effect_speed);
        let effect_rotation = self.preview_rotation(config);
        for fixture in &config.fixtures {
            let Some(fixture_type) = find_fixture_type(config, fixture) else {
                continue;
            };
            let has_effect = fixture_type.channels.iter().any(|channel| {
                channel.has_semantic(DmxSemantic::Rotation)
                    || channel.has_semantic(DmxSemantic::EffectSpeed)
            });
            let is_effect = fixture_type.visual.kind() == FixtureVisualKind::Effect;
            let has_pattern = fixture_type.has_semantic(DmxSemantic::EffectPattern);
            let rotation = has_effect.then(|| {
                self.rotation_value_for_channel(
                    config,
                    fixture_type.channels.iter().find(|channel| {
                        channel.has_semantic(DmxSemantic::Rotation)
                            || channel.has_semantic(DmxSemantic::EffectSpeed)
                    }),
                )
            });
            let key = fixture_key(fixture);
            if is_effect {
                let color_macro = self.states.get(&key).map_or(222, |state| {
                    rgb_to_color_macro(state.red, state.green, state.blue, audio.energy)
                });
                if let Some(state) = self.states.get_mut(&key) {
                    state.color_macro = color_macro;
                    state.strobe = strobe;
                    state.effect = rotation.unwrap_or_default();
                    state.effect_speed = if has_pattern { pattern_speed } else { 0 };
                    state.effect_pattern = if has_pattern { pattern } else { 0 };
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
        _config: &ValidatedShowConfig,
        _channel: Option<&MappedChannel>,
    ) -> u32 {
        self.smoothed_rotation.clamp(0.0, 255.0) as u32
    }

    fn preview_rotation(&self, config: &ValidatedShowConfig) -> f32 {
        let mode = config.rotation_mode();
        match mode {
            RotationMode::Off | RotationMode::Unspecified => 0.0,
            RotationMode::AutoSlow
            | RotationMode::AutoMedium
            | RotationMode::AutoFast
            | RotationMode::AutoMusic
            | RotationMode::ManualSlow
            | RotationMode::ManualBeat => self.rotation_phase,
        }
    }

    fn strobe_value(&self, config: &ValidatedShowConfig, audio: &AudioAnalysis, beat: bool) -> u32 {
        let mode = config.effect_fixture_mode();
        let response = beat_response(config).min(1.0);
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
                    value = ((80.0 + audio.bass * 120.0) * response) as u32;
                } else if audio.energy > 0.2 {
                    value = (6.0 + audio.energy * 100.0) as u32;
                }
            }
            EffectFixtureMode::StrobeFocus if value == 0 => {
                if beat && audio.bass > 0.6 {
                    value = ((60.0 + audio.bass * 100.0) * response) as u32;
                } else if audio.energy > 0.5 {
                    value = (6.0 + (audio.energy - 0.5) * 60.0) as u32;
                }
            }
            _ if value == 0 && beat && audio.bass > 0.8 => {
                value = ((80.0 + audio.bass * 80.0) * response) as u32;
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
        match mode {
            StrobeEffectMode::Off | StrobeEffectMode::Unspecified => 0,
            StrobeEffectMode::Effect18Strobe => 18,
            StrobeEffectMode::Auto => {
                if self.is_drop {
                    return 18;
                }
                let (start, end) = if audio.energy < 0.4 {
                    (1_u64, 6_u64)
                } else if audio.energy < 0.7 {
                    (7, 12)
                } else {
                    (13, 17)
                };
                let effect = start + audio.estimated_bar % (end - start + 1);
                effect as u32
            }
            specific => specific as u32 - StrobeEffectMode::Effect1 as u32 + 1,
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

        let mut moved_on_beat = false;
        for (movement_index, (_, fixture, has_pan, has_tilt)) in
            movement_fixtures.into_iter().enumerate()
        {
            moved_on_beat |= self.movement_target(
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
            self.interpolate_position(config, fixture, mode, speed, audio.tempo, delta_seconds);
        }
        if moved_on_beat {
            self.beats_since_move = 0;
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
    ) -> bool {
        let key = fixture_key(fixture);
        let pan_min = fixture.movement_pan_min * 255.0;
        let pan_max = fixture.movement_pan_max * 255.0;
        let tilt_min = fixture.movement_tilt_min * 255.0;
        let tilt_max = fixture.movement_tilt_max * 255.0;
        let pan_range = pan_max - pan_min;
        let tilt_range = tilt_max - tilt_min;
        let pan_center = (pan_max + pan_min) / 2.0;
        let tilt_center = (tilt_max + tilt_min) / 2.0;
        let energy = audio.energy;
        let bass = audio.bass;
        let phase_offset = fixture.position as f32;
        let mut moved_on_beat = false;

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
                    moved_on_beat = true;
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
                    moved_on_beat = true;
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
                    self.target_tilt
                        .insert(key, (current + tilt_range / 2.0 * 0.1 * bass).min(tilt_max));
                }
            }
            MovementMode::Sweep => {
                let tempo = tempo_scale(audio.tempo);
                let energy_boost = if energy > 0.6 { 1.3 } else { 1.0 };
                let rate = 0.03 * speed * tempo * energy_boost;
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
                    let factor = sweep_tilt_factor(phase, phase_offset);
                    self.target_tilt
                        .insert(key.clone(), tilt_center + factor * tilt_range / 2.0);
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
                    moved_on_beat = true;
                }
            }
            MovementMode::Circle => {
                let rate = 0.08 * speed * tempo_scale(audio.tempo) * (0.8 + energy * 0.4);
                let mut phase = self.circle_phase.get(&key).copied().unwrap_or(0.0)
                    + delta_seconds * rate * TAU;
                phase %= TAU;
                self.circle_phase.insert(key.clone(), phase);
                if beat {
                    self.circle_pulse.insert(key.clone(), 1.0);
                    moved_on_beat = true;
                }
                let pulse = (self.circle_pulse.get(&key).copied().unwrap_or(0.0)
                    - delta_seconds * 3.0)
                    .max(0.0);
                self.circle_pulse.insert(key.clone(), pulse);
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
                let mut phase = self.figure_eight_phase.get(&key).copied().unwrap_or(0.0)
                    + delta_seconds * rate * TAU;
                phase %= TAU;
                self.figure_eight_phase.insert(key.clone(), phase);
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
                let mut direction = self.ballyhoo_direction.get(&key).copied().unwrap_or(1.0);
                let mut phase = self.ballyhoo_phase.get(&key).copied().unwrap_or(0.0)
                    + delta_seconds * rate * direction * TAU;
                phase = phase.rem_euclid(TAU);
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
                    direction = -direction;
                    moved_on_beat = true;
                }
                self.ballyhoo_phase.insert(key.clone(), phase);
                self.ballyhoo_direction.insert(key, direction);
            }
            MovementMode::Fan => {
                let mut amount = self.fan_amount.get(&key).copied().unwrap_or(0.5);
                let mut target = self.fan_target.get(&key).copied().unwrap_or(0.5);
                if bar {
                    target = if audio.estimated_bar.is_multiple_of(2) {
                        0.9
                    } else {
                        0.2
                    };
                    self.fan_target.insert(key.clone(), target);
                }
                amount += (target - amount)
                    * time_adjusted_factor(0.08 * speed * tempo_scale(audio.tempo), delta_seconds);
                self.fan_amount.insert(key.clone(), amount);
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
                    moved_on_beat = true;
                }
            }
            MovementMode::Crazy => {
                let scale = tempo_scale(audio.tempo);
                let boost = 0.6 + energy;
                let motion = self
                    .crazy_motion
                    .entry(key.clone())
                    .or_insert_with(|| CrazyMotionState::new(fixture.position));
                if beat {
                    let roll = self.rng.random::<f32>();
                    if bass > 0.7 && roll < 0.4 {
                        motion.pan_velocity = -motion.pan_velocity;
                    }
                    if energy > 0.6 && roll < 0.5 {
                        motion.pan_velocity += self.rng.random_range(-0.35..=0.35) * boost;
                        motion.tilt_velocity += self.rng.random_range(-0.3..=0.3) * boost;
                    }
                    if roll < 0.25 {
                        motion.pan_velocity += self.rng.random_range(-0.5..=0.5);
                        motion.tilt_velocity += self.rng.random_range(-0.45..=0.45);
                    }
                }
                if bar && self.rng.random::<f32>() < 0.3 {
                    motion.tilt_velocity = -motion.tilt_velocity;
                }
                let velocity_recovery = time_adjusted_factor(0.025, delta_seconds);
                motion.pan_velocity += (1.0 - motion.pan_velocity) * velocity_recovery;
                motion.tilt_velocity += (1.0 - motion.tilt_velocity) * velocity_recovery;
                motion.pan_velocity = motion.pan_velocity.clamp(-1.6, 1.6);
                motion.tilt_velocity = motion.tilt_velocity.clamp(-1.6, 1.6);
                motion.pan_phase = (motion.pan_phase
                    + delta_seconds * 0.18 * speed * scale * boost * TAU * motion.pan_velocity)
                    .rem_euclid(TAU);
                motion.tilt_phase = (motion.tilt_phase
                    + delta_seconds * 0.23 * speed * scale * boost * TAU * motion.tilt_velocity)
                    .rem_euclid(TAU);
                let offset = phase_offset * PI * 0.7;
                let (pan_factor, tilt_factor) =
                    crazy_axis_factors(motion.pan_phase, motion.tilt_phase, offset);
                if has_pan {
                    self.target_pan.insert(
                        key.clone(),
                        (pan_min + (pan_factor + 1.0) / 2.0 * pan_range).clamp(pan_min, pan_max),
                    );
                }
                if has_tilt {
                    self.target_tilt.insert(
                        key,
                        (tilt_min + (tilt_factor + 1.0) / 2.0 * tilt_range)
                            .clamp(tilt_min, tilt_max),
                    );
                }
                moved_on_beat |= beat;
            }
        }
        moved_on_beat
    }

    fn interpolate_position(
        &mut self,
        config: &ValidatedShowConfig,
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
            let pan_min = fixture.movement_pan_min * 255.0;
            let pan_max = fixture.movement_pan_max * 255.0;
            let tilt_min = fixture.movement_tilt_min * 255.0;
            let tilt_max = fixture.movement_tilt_max * 255.0;
            let fixture_type = find_fixture_type(config, fixture);
            let next_pan = slew_axis(
                fine_axis_position(state.pan, state.pan_fine),
                target_pan,
                pan_rate,
                axis_slew_delta(
                    fixture_type,
                    DmxSemantic::Pan,
                    pan_max - pan_min,
                    mode,
                    speed,
                    tempo,
                    delta_seconds,
                ),
                pan_min,
                pan_max,
            );
            let next_tilt = slew_axis(
                fine_axis_position(state.tilt, state.tilt_fine),
                target_tilt,
                tilt_rate,
                axis_slew_delta(
                    fixture_type,
                    DmxSemantic::Tilt,
                    tilt_max - tilt_min,
                    mode,
                    speed,
                    tempo,
                    delta_seconds,
                ),
                tilt_min,
                tilt_max,
            );
            (state.pan, state.pan_fine) = fine_axis_channels(next_pan);
            (state.tilt, state.tilt_fine) = fine_axis_channels(next_tilt);
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
            let Some(fixture_type) = find_fixture_type(config, fixture) else {
                continue;
            };
            let has_dimmer = fixture_type.has_semantic(DmxSemantic::Dimmer);
            let has_color = fixture_type.has_direct_color();
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
            let Some(fixture_type) = find_fixture_type(config, fixture) else {
                continue;
            };
            for channel in &fixture_type.channels {
                let dmx_channel = fixture
                    .start_channel
                    .saturating_add(channel.coarse)
                    .saturating_sub(1);
                if dmx_channel == 0 || dmx_channel as usize > universe_size {
                    continue;
                }
                universe[dmx_channel as usize - 1] =
                    channel_value(state, channel, fixture_type).min(255) as u8;
                if let Some(fine) = channel.fine {
                    let fine_channel = fixture.start_channel.saturating_add(fine).saturating_sub(1);
                    if fine_channel > 0 && fine_channel as usize <= universe_size {
                        universe[fine_channel as usize - 1] =
                            channel_fine_value(state, channel).min(255) as u8;
                    }
                }
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

fn beat_response(config: &ValidatedShowConfig) -> f32 {
    (effects(config).beat_sensitivity * 2.0).clamp(0.0, 2.0)
}

fn energy_tier(energy: f32) -> EnergyTier {
    if energy < 0.33 {
        EnergyTier::Low
    } else if energy < 0.7 {
        EnergyTier::Medium
    } else {
        EnergyTier::High
    }
}

fn average_rendered_color<'a>(states: impl Iterator<Item = &'a FixtureState>) -> RgbColor {
    let (red, green, blue, count) =
        states.fold((0_u64, 0_u64, 0_u64, 0_u64), |accumulator, state| {
            (
                accumulator.0 + u64::from(state.red),
                accumulator.1 + u64::from(state.green),
                accumulator.2 + u64::from(state.blue),
                accumulator.3 + 1,
            )
        });
    if count == 0 {
        return RgbColor::default();
    }
    RgbColor {
        red: (red / count) as u32,
        green: (green / count) as u32,
        blue: (blue / count) as u32,
    }
}

fn fixture_key(fixture: &FixtureConfig) -> String {
    if fixture.id.is_empty() {
        fixture.name.clone()
    } else {
        fixture.id.clone()
    }
}

fn default_state(
    fixture: &FixtureConfig,
    fixture_type: Option<&ParsedFixtureType>,
) -> FixtureState {
    let (pan, pan_fine) = fine_axis_channels(axis_default_position(
        fixture,
        fixture_type,
        DmxSemantic::Pan,
    ));
    let (tilt, tilt_fine) = fine_axis_channels(axis_default_position(
        fixture,
        fixture_type,
        DmxSemantic::Tilt,
    ));
    FixtureState {
        fixture_id: fixture_key(fixture),
        fixture_name: fixture.name.clone(),
        dimmer: 255,
        pan,
        pan_fine,
        tilt,
        tilt_fine,
        zoom: 128,
        focus: 128,
        iris: 255,
        ..Default::default()
    }
}

fn axis_default_position(
    fixture: &FixtureConfig,
    fixture_type: Option<&ParsedFixtureType>,
    semantic: DmxSemantic,
) -> f32 {
    let (minimum, maximum) = match semantic {
        DmxSemantic::Pan => (
            fixture.movement_pan_min * 255.0,
            fixture.movement_pan_max * 255.0,
        ),
        DmxSemantic::Tilt => (
            fixture.movement_tilt_min * 255.0,
            fixture.movement_tilt_max * 255.0,
        ),
        _ => return 127.5,
    };
    fixture_type
        .and_then(|fixture_type| {
            fixture_type
                .channels
                .iter()
                .find(|channel| channel.has_semantic(semantic))
        })
        .map_or((minimum + maximum) / 2.0, |channel| {
            fine_axis_position(channel.default_value, 0).clamp(minimum, maximum)
        })
}

fn find_fixture_type<'a>(
    config: &'a ValidatedShowConfig,
    fixture: &FixtureConfig,
) -> Option<&'a ParsedFixtureType> {
    config.grandma2().get(&fixture.fixture_type_id)
}

fn controllable_movement_axes(
    config: &ValidatedShowConfig,
    fixture: &FixtureConfig,
) -> (bool, bool) {
    find_fixture_type(config, fixture).map_or((false, false), |fixture_type| {
        (
            fixture_type.has_semantic(DmxSemantic::Pan),
            fixture_type.has_semantic(DmxSemantic::Tilt),
        )
    })
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

fn crazy_axis_factors(pan_phase: f32, tilt_phase: f32, offset: f32) -> (f32, f32) {
    let pan = ((pan_phase + offset).sin() + (pan_phase * 3.0 + offset).sin() * 0.3) / 1.3;
    let tilt = ((tilt_phase + offset * 0.5).sin() + (tilt_phase * 2.0).cos() * 0.4) / 1.4;
    (pan, tilt)
}

fn sweep_tilt_factor(phase: f32, phase_offset: f32) -> f32 {
    0.55 - ((phase + phase_offset * 0.25) * TAU).cos() * 0.25
}

fn movement_slew_rate(mode: MovementMode) -> f32 {
    match mode {
        MovementMode::Subtle => 0.12,
        MovementMode::Standard | MovementMode::Unspecified => 0.22,
        MovementMode::Dramatic => 0.38,
        MovementMode::WallWash => 0.16,
        MovementMode::Sweep => 0.25,
        MovementMode::Random => 0.32,
        MovementMode::Circle => 0.35,
        MovementMode::Figure8 => 0.30,
        MovementMode::Ballyhoo => 0.45,
        MovementMode::Fan => 0.20,
        MovementMode::Chase => 0.40,
        MovementMode::StrobePosition => 0.50,
        MovementMode::Crazy => 0.55,
    }
}

fn physical_axis_range(
    fixture_type: Option<&ParsedFixtureType>,
    semantic: DmxSemantic,
) -> Option<f32> {
    let visual = &fixture_type?.visual;
    let range = match semantic {
        DmxSemantic::Pan => (visual.pan_max_degrees - visual.pan_min_degrees).abs(),
        DmxSemantic::Tilt => (visual.tilt_max_degrees - visual.tilt_min_degrees).abs(),
        _ => return None,
    };
    (range > f32::EPSILON && range.is_finite()).then_some(range)
}

fn axis_slew_delta(
    fixture_type: Option<&ParsedFixtureType>,
    semantic: DmxSemantic,
    configured_span: f32,
    mode: MovementMode,
    speed: f32,
    tempo: f32,
    delta_seconds: f32,
) -> f32 {
    let response = movement_slew_rate(mode) * (0.25 + speed * 0.75) * tempo_scale(tempo).sqrt();
    physical_axis_range(fixture_type, semantic).map_or(configured_span * response, |degrees| {
        255.0 * response * REFERENCE_AXIS_RANGE_DEGREES / degrees
    }) * delta_seconds
}

fn fine_axis_position(coarse: u32, fine: u32) -> f32 {
    let raw = coarse.min(255) * 256 + fine.min(255);
    raw as f32 / 65_535.0 * 255.0
}

fn fine_axis_channels(position: f32) -> (u32, u32) {
    let raw = (position.clamp(0.0, 255.0) / 255.0 * 65_535.0).round() as u32;
    (raw / 256, raw % 256)
}

fn slew_axis(
    current: f32,
    target: f32,
    response: f32,
    max_delta: f32,
    minimum: f32,
    maximum: f32,
) -> f32 {
    let current = current.clamp(0.0, 255.0);
    let target = target.clamp(minimum, maximum);
    let desired_delta = (target - current) * response.clamp(0.0, 1.0);
    (current + desired_delta.clamp(-max_delta.max(0.0), max_delta.max(0.0))).clamp(0.0, 255.0)
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

fn channel_value(
    state: &FixtureState,
    channel: &MappedChannel,
    fixture_type: &ParsedFixtureType,
) -> u32 {
    if channel.has_semantic(DmxSemantic::Dimmer) {
        return dimmer_channel_value(state, channel);
    }
    if channel.has_semantic(DmxSemantic::Strobe) {
        return if state.strobe == 0 {
            safe_idle_value(channel)
        } else {
            semantic_function(channel, DmxSemantic::Strobe)
                .map_or(channel.default_value, |function| {
                    function.normalized_value(state.strobe as f32 / 255.0)
                })
        };
    }
    if channel.has_semantic(DmxSemantic::CustomColor) {
        return custom_color_value(state, channel);
    }

    for (semantic, value) in [
        (DmxSemantic::Red, state.red),
        (DmxSemantic::Green, state.green),
        (DmxSemantic::Blue, state.blue),
        (DmxSemantic::White, state.white),
        (DmxSemantic::Amber, state.amber),
        (DmxSemantic::Uv, state.uv),
        (DmxSemantic::Cyan, state.cyan),
        (DmxSemantic::Magenta, state.magenta),
        (DmxSemantic::Yellow, state.yellow),
        (DmxSemantic::Pan, state.pan),
        (DmxSemantic::Tilt, state.tilt),
        (DmxSemantic::PositionSpeed, state.pan_tilt_speed),
        (DmxSemantic::Gobo, state.gobo),
        (DmxSemantic::Prism, state.prism),
        (DmxSemantic::Zoom, state.zoom),
        (DmxSemantic::Focus, state.focus),
        (DmxSemantic::Iris, state.iris),
    ] {
        if let Some(function) = semantic_function(channel, semantic) {
            return function.normalized_value(value as f32 / 255.0);
        }
    }

    if channel.has_semantic(DmxSemantic::ColorMacro) {
        return if fixture_type.has_direct_color() {
            safe_idle_value(channel)
        } else {
            color_macro_value(state, channel)
        };
    }
    if channel.has_semantic(DmxSemantic::ColorMacroSpeed) {
        return safe_idle_value(channel);
    }
    if channel.has_semantic(DmxSemantic::Rotation) {
        return rotation_channel_value(state.effect, channel);
    }
    if let Some(function) = semantic_function(channel, DmxSemantic::EffectSpeed) {
        let value = if state.effect_speed > 0 {
            state.effect_speed
        } else {
            state.effect
        };
        return function.normalized_value(value as f32 / 255.0);
    }
    if channel.has_semantic(DmxSemantic::EffectPattern) {
        return effect_pattern_value(state.effect_pattern, state.effect_speed, channel);
    }
    safe_idle_value(channel)
}

fn channel_fine_value(state: &FixtureState, channel: &MappedChannel) -> u32 {
    if channel.has_semantic(DmxSemantic::Pan) {
        state.pan_fine
    } else if channel.has_semantic(DmxSemantic::Tilt) {
        state.tilt_fine
    } else {
        0
    }
}

fn semantic_function(channel: &MappedChannel, semantic: DmxSemantic) -> Option<&MappedFunction> {
    channel
        .functions
        .iter()
        .find(|function| function.semantic == semantic)
}

fn safe_idle_value(channel: &MappedChannel) -> u32 {
    channel
        .functions
        .iter()
        .find(|function| function.semantic == DmxSemantic::NoFeature)
        .map_or(channel.default_value, |function| function.from_dmx)
}

fn dimmer_channel_value(state: &FixtureState, channel: &MappedChannel) -> u32 {
    if state.dimmer < 5 {
        return safe_idle_value(channel);
    }
    if state.strobe > 0
        && let Some(function) = semantic_function(channel, DmxSemantic::Strobe)
    {
        return function.normalized_value(state.strobe as f32 / 255.0);
    }
    if state.dimmer >= 250
        && let Some(function) = semantic_function(channel, DmxSemantic::Shutter)
    {
        return function.from_dmx;
    }
    semantic_function(channel, DmxSemantic::Dimmer).map_or(channel.default_value, |function| {
        function.normalized_value(state.dimmer as f32 / 255.0)
    })
}

fn custom_color_value(state: &FixtureState, channel: &MappedChannel) -> u32 {
    let red = state.red as f32 / 255.0;
    let green = state.green as f32 / 255.0;
    let blue = state.blue as f32 / 255.0;
    let (hue, saturation, rgb_value) = rgb_to_hsv(red, green, blue);
    let white_value = state.white as f32 / 255.0;
    let contribution = channel
        .color_hues
        .iter()
        .map(|candidate| {
            if *candidate < 0.0 {
                (1.0 - saturation) * rgb_value.max(white_value)
            } else {
                (1.0 - hue_distance(hue, *candidate) * 6.0).max(0.0) * rgb_value
            }
        })
        .fold(0.0_f32, f32::max);
    semantic_function(channel, DmxSemantic::CustomColor).map_or(channel.default_value, |function| {
        function.normalized_value(contribution)
    })
}

fn color_macro_value(state: &FixtureState, channel: &MappedChannel) -> u32 {
    if state.dimmer < 5 || max_color(state) < 5 {
        return safe_idle_value(channel);
    }
    let target = [
        state.red as f32 / 255.0,
        state.green as f32 / 255.0,
        state.blue as f32 / 255.0,
        state.white as f32 / 255.0,
    ];
    channel
        .functions
        .iter()
        .filter(|function| function.semantic == DmxSemantic::ColorMacro)
        .flat_map(|function| &function.channel_sets)
        .filter_map(|set| {
            let candidate = named_color(&set.name)?;
            let score = target
                .iter()
                .zip(candidate)
                .map(|(actual, expected)| (actual - expected).powi(2))
                .sum::<f32>();
            Some((score, (set.from_dmx + set.to_dmx) / 2))
        })
        .min_by(|(left, _), (right, _)| left.total_cmp(right))
        .map_or_else(|| safe_idle_value(channel), |(_, value)| value)
}

fn named_color(name: &str) -> Option<[f32; 4]> {
    let name = name.to_ascii_lowercase();
    if ["off", "manual", "change", "macro", "speed"]
        .iter()
        .any(|token| name.contains(token))
    {
        return None;
    }
    let compact = name.replace([' ', '+', '/', '-'], "");
    let shorthand = !compact.is_empty()
        && compact.len() <= 4
        && compact.chars().all(|character| "rgbw".contains(character));
    let has =
        |full: &str, short: char| name.contains(full) || (shorthand && compact.contains(short));
    let red = has("red", 'r');
    let green = has("green", 'g');
    let blue = has("blue", 'b');
    let white = has("white", 'w');
    (red || green || blue || white).then_some([
        red as u8 as f32,
        green as u8 as f32,
        blue as u8 as f32,
        white as u8 as f32,
    ])
}

fn rotation_channel_value(value: u32, channel: &MappedChannel) -> u32 {
    let automatic = value >= 128;
    let preferred = channel.functions.iter().find(|function| {
        if function.semantic != DmxSemantic::Rotation {
            return false;
        }
        let name = format!("{} {}", function.name, function.subattribute).to_ascii_lowercase();
        if automatic {
            name.contains("auto") || name.contains("rot")
        } else {
            name.contains("manual") || !name.contains("auto")
        }
    });
    preferred.map_or_else(
        || safe_idle_value(channel),
        |function| {
            let normalized = if automatic {
                value.saturating_sub(128) as f32 / 127.0
            } else {
                value.min(127) as f32 / 127.0
            };
            function.normalized_value(normalized)
        },
    )
}

fn effect_pattern_value(pattern: u32, speed: u32, channel: &MappedChannel) -> u32 {
    if pattern == 0 {
        return safe_idle_value(channel);
    }
    let sets = channel
        .functions
        .iter()
        .filter(|function| function.semantic == DmxSemantic::EffectPattern)
        .flat_map(|function| &function.channel_sets)
        .filter(|set| {
            let name = set.name.to_ascii_lowercase();
            !name.contains("sound") && !name.contains("off")
        })
        .collect::<Vec<_>>();
    if sets.is_empty() {
        return semantic_function(channel, DmxSemantic::EffectPattern).map_or_else(
            || safe_idle_value(channel),
            |function| function.normalized_value(pattern.min(255) as f32 / 255.0),
        );
    }
    let index = pattern.saturating_sub(1) as usize % sets.len();
    sets[index].from_dmx
        + (sets[index].to_dmx.saturating_sub(sets[index].from_dmx) as f32 * speed.min(255) as f32
            / 255.0)
            .round() as u32
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
    state.effect_speed = 0;
    state.effect_pattern = 0;
    state.effect_rotation = 0.0;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        config::default_show_config,
        grandma2::{LIXADA_MINI_BUTTERFLY_ID, PURELIGHT_MUVY_WASHQ_ID, SHOWTEC_TECHNO_DERBY_ID},
    };

    fn validated(config: crate::proto::v1::ShowConfig) -> ValidatedShowConfig {
        ValidatedShowConfig::new(config, true).expect("test configuration should validate")
    }

    fn test_fixture(
        id: &str,
        start_channel: u32,
        position: u32,
        fixture_type_id: &str,
    ) -> FixtureConfig {
        FixtureConfig {
            id: id.into(),
            name: id.into(),
            fixture_type_id: fixture_type_id.into(),
            start_channel,
            position,
            intensity_scale: 1.0,
            movement_pan_min: 0.0,
            movement_pan_max: 1.0,
            movement_tilt_min: 0.0,
            movement_tilt_max: 1.0,
            stage_placement: None,
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
    fn color_macro_fixture_uses_its_idle_range_when_dark() {
        let config = validated(default_show_config(true));
        let fixture_type = config
            .grandma2()
            .get(SHOWTEC_TECHNO_DERBY_ID)
            .expect("Techno Derby fixture type");
        let channel = fixture_type
            .channels
            .iter()
            .find(|channel| channel.has_semantic(DmxSemantic::ColorMacro))
            .expect("Techno Derby color macro");

        assert_eq!(
            channel_value(&FixtureState::default(), channel, fixture_type),
            0
        );
    }

    #[test]
    fn techno_derby_pattern_uses_the_configured_slow_to_fast_position() {
        let mut config = default_show_config(true);
        let settings = config.effects.as_mut().expect("effects configuration");
        settings.strobe_effect_mode = StrobeEffectMode::Effect1 as i32;
        settings.strobe_effect_speed = 0.75;
        let config = validated(config);
        let fixture_type = config
            .grandma2()
            .get(SHOWTEC_TECHNO_DERBY_ID)
            .expect("Techno Derby fixture type");
        let channel = fixture_type
            .channels
            .iter()
            .find(|channel| channel.has_semantic(DmxSemantic::EffectPattern))
            .expect("Techno Derby pattern channel");

        assert_eq!(effect_pattern_value(1, 0, channel), 10);
        assert_eq!(effect_pattern_value(1, 128, channel), 15);
        assert_eq!(effect_pattern_value(1, 255, channel), 19);
        assert_eq!(effect_pattern_value(18, 255, channel), 255);

        let mut engine = EffectsEngine::default();
        engine.ensure_fixtures(&config);
        engine.process_effect_fixtures(
            &config,
            &AudioAnalysis::default(),
            false,
            false,
            REFERENCE_FRAME_SECONDS,
        );
        let state = engine
            .states
            .get("techno-derby")
            .expect("Techno Derby state");
        assert_eq!(state.effect_speed, dmx(0.75));
        assert_eq!(channel_value(state, channel, fixture_type), 17);
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
        approx::assert_abs_diff_eq!(engine.preview_rotation(&config), 0.25, epsilon = 0.0001);

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
    fn every_visualization_and_movement_mode_produces_bounded_output() {
        for visualization in VISUALIZATION_MODES {
            for movement in MOVEMENT_MODES {
                let mut config = default_show_config(true);
                config.fixtures = vec![FixtureConfig {
                    id: "moving-head".into(),
                    name: "Moving head".into(),
                    fixture_type_id: PURELIGHT_MUVY_WASHQ_ID.into(),
                    start_channel: 1,
                    position: 0,
                    intensity_scale: 0.85,
                    movement_pan_min: 16.0 / 255.0,
                    movement_pan_max: 240.0 / 255.0,
                    movement_tilt_min: 32.0 / 255.0,
                    movement_tilt_max: 224.0 / 255.0,
                    stage_placement: None,
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
    fn grand_ma2_combined_dimmer_strobe_and_reset_channel_map_safely() {
        let mut config = default_show_config(true);
        config.fixtures = vec![test_fixture("muvy", 1, 0, PURELIGHT_MUVY_WASHQ_ID)];
        let config = validated(config);
        let mut engine = EffectsEngine::default();
        engine.ensure_fixtures(&config);
        let state = engine.smoothed.get_mut("muvy").expect("smoothed state");
        state.dimmer = 255;
        state.strobe = 0;
        assert_eq!(engine.map_universe(&config, 512)[5], 240);
        assert_eq!(engine.map_universe(&config, 512)[13], 0);

        engine
            .smoothed
            .get_mut("muvy")
            .expect("smoothed state")
            .strobe = 128;
        assert!((186..=188).contains(&engine.map_universe(&config, 512)[5]));
    }

    #[test]
    fn movement_phase_is_stable_across_output_rates() {
        fn phase_after_one_second(fps: u32) -> f32 {
            let mut config = default_show_config(true);
            let settings = config.effects.as_mut().expect("effects configuration");
            settings.movement_enabled = true;
            settings.movement_mode = MovementMode::Circle as i32;
            let config = validated(config);
            let fixture = config
                .fixtures
                .iter()
                .find(|fixture| fixture.fixture_type_id == PURELIGHT_MUVY_WASHQ_ID)
                .map(fixture_key)
                .expect("default show should contain a moving head");
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
            engine.circle_phase[&fixture]
        }

        approx::assert_abs_diff_eq!(
            phase_after_one_second(20),
            phase_after_one_second(40),
            epsilon = 0.0001
        );
    }

    #[test]
    fn crazy_motion_waveform_is_continuous_across_phase_wraps() {
        for offset in [0.0, PI * 0.7, PI * 1.4] {
            let before_wrap = crazy_axis_factors(TAU - 0.0001, TAU - 0.0001, offset);
            let after_wrap = crazy_axis_factors(0.0, 0.0, offset);
            approx::assert_abs_diff_eq!(before_wrap.0, after_wrap.0, epsilon = 0.001);
            approx::assert_abs_diff_eq!(before_wrap.1, after_wrap.1, epsilon = 0.001);
        }
    }

    #[test]
    fn offset_sweep_tilt_is_continuous_across_phase_wraps() {
        for phase_offset in [0.0, 1.0, 2.0] {
            approx::assert_abs_diff_eq!(
                sweep_tilt_factor(1.0 - 0.0001, phase_offset),
                sweep_tilt_factor(0.0, phase_offset),
                epsilon = 0.001
            );
        }
    }

    #[test]
    fn ballyhoo_reverses_velocity_without_flipping_position() {
        let mut config = default_show_config(true);
        config.fixtures = vec![test_fixture("muvy", 1, 0, PURELIGHT_MUVY_WASHQ_ID)];
        let config = validated(config);
        let fixture = &config.fixtures[0];
        let mut engine = EffectsEngine::default();
        engine.ensure_fixtures(&config);
        engine.ballyhoo_phase.insert("muvy".into(), 1.25);
        engine.ballyhoo_direction.insert("muvy".into(), 1.0);

        engine.movement_target(
            fixture,
            &AudioAnalysis {
                energy: 0.8,
                bass: 0.9,
                tempo: 120.0,
                ..Default::default()
            },
            true,
            false,
            MovementMode::Ballyhoo,
            1.0,
            true,
            true,
            0,
            1,
            REFERENCE_FRAME_SECONDS,
        );

        let expected_phase = 1.25 + 0.025 * 0.12 * 1.09 * TAU;
        approx::assert_abs_diff_eq!(
            engine.ballyhoo_phase["muvy"],
            expected_phase,
            epsilon = 0.0001
        );
        assert_eq!(engine.ballyhoo_direction["muvy"], -1.0);
    }

    #[test]
    fn crazy_motion_keeps_valid_low_tilt_phases() {
        let mut config = default_show_config(true);
        config.fixtures = vec![test_fixture("muvy", 1, 0, PURELIGHT_MUVY_WASHQ_ID)];
        let settings = config.effects.as_mut().expect("effects configuration");
        settings.movement_mode = MovementMode::Crazy as i32;
        settings.movement_speed = 1.0;
        let config = validated(config);
        let fixture = &config.fixtures[0];
        let mut engine = EffectsEngine::default();
        engine.ensure_fixtures(&config);
        let motion = engine
            .crazy_motion
            .get_mut("muvy")
            .expect("crazy motion state");
        motion.tilt_phase = 0.25;
        motion.tilt_velocity = 1.0;

        engine.movement_target(
            fixture,
            &AudioAnalysis {
                energy: 0.8,
                bass: 0.4,
                tempo: 120.0,
                ..Default::default()
            },
            false,
            false,
            MovementMode::Crazy,
            1.0,
            true,
            true,
            0,
            1,
            REFERENCE_FRAME_SECONDS,
        );

        let tilt_phase = engine.crazy_motion["muvy"].tilt_phase;
        assert!((0.25..0.5).contains(&tilt_phase));
    }

    #[test]
    fn every_movement_mode_obeys_the_axis_slew_limit() {
        for mode in MOVEMENT_MODES {
            let mut config = default_show_config(true);
            config.fixtures = vec![test_fixture("muvy", 1, 0, PURELIGHT_MUVY_WASHQ_ID)];
            let settings = config.effects.as_mut().expect("effects configuration");
            settings.movement_mode = mode as i32;
            settings.movement_speed = 1.0;
            let config = validated(config);
            let fixture = &config.fixtures[0];
            let mut engine = EffectsEngine::default();
            engine.ensure_fixtures(&config);
            engine.target_pan.insert("muvy".into(), 255.0);
            engine.target_tilt.insert("muvy".into(), 0.0);
            let before = engine.states["muvy"].clone();

            engine.interpolate_position(
                &config,
                fixture,
                mode,
                1.0,
                120.0,
                REFERENCE_FRAME_SECONDS,
            );

            let after = &engine.states["muvy"];
            let max_delta = axis_slew_delta(
                find_fixture_type(&config, fixture),
                DmxSemantic::Pan,
                255.0,
                mode,
                1.0,
                120.0,
                REFERENCE_FRAME_SECONDS,
            );
            let max_tilt_delta = axis_slew_delta(
                find_fixture_type(&config, fixture),
                DmxSemantic::Tilt,
                255.0,
                mode,
                1.0,
                120.0,
                REFERENCE_FRAME_SECONDS,
            );
            let pan_delta = (fine_axis_position(after.pan, after.pan_fine)
                - fine_axis_position(before.pan, before.pan_fine))
            .abs();
            let tilt_delta = (fine_axis_position(after.tilt, after.tilt_fine)
                - fine_axis_position(before.tilt, before.tilt_fine))
            .abs();
            assert!(pan_delta <= max_delta + 0.01, "{mode:?} pan moved too far");
            assert!(
                tilt_delta <= max_tilt_delta + 0.01,
                "{mode:?} tilt moved too far"
            );
        }
    }

    #[test]
    fn axis_slew_uses_grandma2_physical_ranges() {
        let config = validated(default_show_config(true));
        let fixture = config
            .fixtures
            .iter()
            .find(|fixture| fixture.fixture_type_id == PURELIGHT_MUVY_WASHQ_ID)
            .expect("default show should contain a moving head");
        let fixture_type = find_fixture_type(&config, fixture);
        let pan_delta = axis_slew_delta(
            fixture_type,
            DmxSemantic::Pan,
            255.0,
            MovementMode::Standard,
            1.0,
            120.0,
            REFERENCE_FRAME_SECONDS,
        );
        let tilt_delta = axis_slew_delta(
            fixture_type,
            DmxSemantic::Tilt,
            255.0,
            MovementMode::Standard,
            1.0,
            120.0,
            REFERENCE_FRAME_SECONDS,
        );

        approx::assert_abs_diff_eq!(tilt_delta, pan_delta * 2.0, epsilon = 0.0001);
    }

    #[test]
    fn movement_uses_fine_channels_for_sub_coarse_positions() {
        let mut config = default_show_config(true);
        config.fixtures = vec![test_fixture("muvy", 1, 0, PURELIGHT_MUVY_WASHQ_ID)];
        let config = validated(config);
        let fixture = &config.fixtures[0];
        let mut engine = EffectsEngine::default();
        engine.ensure_fixtures(&config);
        engine.target_pan.insert("muvy".into(), 128.5);

        engine.interpolate_position(
            &config,
            fixture,
            MovementMode::Subtle,
            1.0,
            120.0,
            REFERENCE_FRAME_SECONDS,
        );

        assert_ne!(engine.states["muvy"].pan_fine, 0);
    }

    #[test]
    fn beat_fallback_moves_every_fixture_independent_of_show_order() {
        let mut config = default_show_config(true);
        config.fixtures = vec![
            test_fixture("first-mover", 1, 0, PURELIGHT_MUVY_WASHQ_ID),
            test_fixture("second-mover", 15, 1, PURELIGHT_MUVY_WASHQ_ID),
        ];
        let settings = config.effects.as_mut().expect("effects configuration");
        settings.movement_mode = MovementMode::Standard as i32;
        settings.movement_speed = 1.0;
        let config = validated(config);
        let mut engine = EffectsEngine::default();
        engine.ensure_fixtures(&config);
        engine.beats_since_move = 4;

        engine.apply_movement(
            &config,
            &AudioAnalysis {
                energy: 0.1,
                tempo: 120.0,
                estimated_beat: 1,
                ..Default::default()
            },
            true,
            false,
            REFERENCE_FRAME_SECONDS,
        );

        approx::assert_abs_diff_eq!(
            engine.target_tilt["first-mover"],
            engine.target_tilt["second-mover"],
            epsilon = 0.0001
        );
        assert_ne!(engine.target_tilt["first-mover"], 127.5);
        assert_eq!(engine.beats_since_move, 0);
    }

    #[test]
    fn chase_uses_only_controllable_movers_in_show_order() {
        let mut config = default_show_config(true);
        config.fixtures = vec![
            test_fixture("static-light", 1, 0, SHOWTEC_TECHNO_DERBY_ID),
            test_fixture("later-mover", 5, 30, PURELIGHT_MUVY_WASHQ_ID),
            test_fixture("earlier-mover", 19, 10, PURELIGHT_MUVY_WASHQ_ID),
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
            test_fixture("static-before", 1, 0, SHOWTEC_TECHNO_DERBY_ID),
            test_fixture("only-mover", 5, 1, PURELIGHT_MUVY_WASHQ_ID),
            test_fixture("static-after", 19, 2, LIXADA_MINI_BUTTERFLY_ID),
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
            test_fixture("static-light", 1, 0, SHOWTEC_TECHNO_DERBY_ID),
            test_fixture("right-mover", 5, 30, PURELIGHT_MUVY_WASHQ_ID),
            test_fixture("left-mover", 19, 10, PURELIGHT_MUVY_WASHQ_ID),
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

    #[test]
    fn beat_sensitivity_scales_accents_and_runtime_telemetry() {
        fn beat_frame(sensitivity: f32) -> EffectOutput {
            let mut config = default_show_config(true);
            let settings = config.effects.as_mut().expect("effects configuration");
            settings.mode = VisualizationMode::Energy as i32;
            settings.beat_sensitivity = sensitivity;
            settings.smooth_factor = 0.0;
            let config = validated(config);
            EffectsEngine::default().process(
                &config,
                &AudioAnalysis {
                    energy: 0.4,
                    bass: 1.0,
                    tempo: 120.0,
                    estimated_beat: 1,
                    ..Default::default()
                },
                &[],
                false,
                Duration::from_millis(25),
            )
        }

        let disabled = beat_frame(0.0);
        let full = beat_frame(1.0);

        assert!(!disabled.runtime.beat_accent_active);
        assert_eq!(disabled.runtime.beat_response, 0.0);
        assert!(full.runtime.beat_accent_active);
        assert_eq!(full.runtime.beat_response, 2.0);
        assert!(full.fixture_states[0].dimmer > disabled.fixture_states[0].dimmer);
    }
}
