"""
Effects engine that converts audio analysis to DMX values.
Supports multiple visualization modes and fixture configurations.
"""
import math
import time
import colorsys
from typing import Optional
from dataclasses import dataclass, field

from config import (
    FixtureConfig, FixtureProfile, EffectsConfig, VisualizationMode,
    ChannelType, ChannelConfig, ShowConfig, FIXTURE_PRESETS
)
from audio_analyzer import AnalysisData
from dmx_controller import DMXController


@dataclass
class FixtureState:
    """Current state of a fixture - all possible channel values."""
    # Color
    red: int = 0
    green: int = 0
    blue: int = 0
    white: int = 0
    amber: int = 0
    uv: int = 0
    cyan: int = 0
    magenta: int = 0
    yellow: int = 0
    
    # Intensity
    dimmer: int = 255
    strobe: int = 0
    
    # Position
    pan: int = 128
    pan_fine: int = 0
    tilt: int = 128
    tilt_fine: int = 0
    pt_speed: int = 0
    
    # Effects
    color_macro: int = 0
    effect: int = 0
    effect_speed: int = 0
    
    # Other
    gobo: int = 0
    prism: int = 0
    zoom: int = 128
    focus: int = 128
    iris: int = 255
    
    def get_rgb(self) -> tuple[int, int, int]:
        return (self.red, self.green, self.blue)
    
    def set_rgb(self, r: int, g: int, b: int) -> None:
        self.red = max(0, min(255, int(r)))
        self.green = max(0, min(255, int(g)))
        self.blue = max(0, min(255, int(b)))
    
    def set_from_hsv(self, h: float, s: float, v: float) -> None:
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
        self.red = int(r * 255)
        self.green = int(g * 255)
        self.blue = int(b * 255)


class EffectsEngine:
    """Engine that processes audio analysis and generates DMX output."""
    
    def __init__(self, dmx_controller: DMXController, config: ShowConfig):
        self.dmx = dmx_controller
        self.config = config
        
        # Cache profiles
        self._profiles: dict[str, FixtureProfile] = {}
        self._load_profiles()
        
        # State for each fixture
        self._states: dict[str, FixtureState] = {}
        self._smoothed_values: dict[str, FixtureState] = {}
        self._init_fixture_states()
        
        # Animation state
        self._time = 0.0
        self._last_beat = 0
        self._last_bar = 0
        self._color_phase = 0.0
        self._wave_phase = 0.0
        self._strobe_active = False
        self._last_energy = 0.5
    
    def _load_profiles(self) -> None:
        self._profiles.update(FIXTURE_PRESETS)
        for profile in self.config.profiles:
            self._profiles[profile.name] = profile
    
    def _init_fixture_states(self) -> None:
        for fixture in self.config.fixtures:
            self._states[fixture.name] = FixtureState()
            self._smoothed_values[fixture.name] = FixtureState()
    
    def _get_profile(self, fixture: FixtureConfig) -> Optional[FixtureProfile]:
        if fixture.profile_name:
            return self._profiles.get(fixture.profile_name)
        return None
    
    def update_config(self, config: ShowConfig) -> None:
        self.config = config
        self._load_profiles()
        for fixture in config.fixtures:
            if fixture.name not in self._states:
                self._states[fixture.name] = FixtureState()
                self._smoothed_values[fixture.name] = FixtureState()
    
    def process(self, data: AnalysisData) -> dict[str, FixtureState]:
        self._time = time.time()
        
        beat_triggered = data.estimated_beat != self._last_beat
        bar_triggered = data.estimated_bar != self._last_bar
        self._last_beat = data.estimated_beat
        self._last_bar = data.estimated_bar
        
        mode = self.config.effects.mode
        
        if mode == VisualizationMode.ENERGY:
            self._apply_energy_mode(data)
        elif mode == VisualizationMode.FREQUENCY_SPLIT:
            self._apply_frequency_split_mode(data)
        elif mode == VisualizationMode.BEAT_PULSE:
            self._apply_beat_pulse_mode(data, beat_triggered)
        elif mode == VisualizationMode.COLOR_CYCLE:
            self._apply_color_cycle_mode(data)
        elif mode == VisualizationMode.RAINBOW_WAVE:
            self._apply_rainbow_wave_mode(data)
        elif mode == VisualizationMode.STROBE_BEAT:
            self._apply_strobe_beat_mode(data, beat_triggered)
        elif mode == VisualizationMode.RANDOM_FLASH:
            self._apply_random_flash_mode(data, beat_triggered)
        
        if self.config.effects.movement_enabled:
            self._apply_movement(data)
        
        if self.config.effects.strobe_on_drop:
            self._check_energy_drop(data)
        
        self._apply_smoothing()
        self._output_to_dmx()
        
        return self._smoothed_values.copy()
    
    def _apply_energy_mode(self, data: AnalysisData) -> None:
        energy = data.features.energy
        intensity = energy * self.config.effects.intensity
        hue = 0.0 + data.features.valence * 0.3
        
        for fixture in self.config.fixtures:
            state = self._states[fixture.name]
            pulse = 1.0 - (data.beat_position * 0.3)
            brightness = intensity * pulse * fixture.intensity_scale
            state.set_from_hsv(hue, 1.0, brightness)
            state.dimmer = int(255 * brightness)
    
    def _apply_frequency_split_mode(self, data: AnalysisData) -> None:
        num_fixtures = len(self.config.fixtures)
        if num_fixtures == 0:
            return
        
        sorted_fixtures = sorted(self.config.fixtures, key=lambda f: f.position)
        third = max(1, num_fixtures // 3)
        
        bass_intensity = data.features.bass
        mid_intensity = data.features.mid
        high_intensity = data.features.high
        
        for i, fixture in enumerate(sorted_fixtures):
            state = self._states[fixture.name]
            scale = fixture.intensity_scale * self.config.effects.intensity
            
            if i < third:
                intensity = bass_intensity * scale
                state.set_from_hsv(0.0, 1.0, intensity)
            elif i < third * 2:
                intensity = mid_intensity * scale
                state.set_from_hsv(0.33, 1.0, intensity)
            else:
                intensity = high_intensity * scale
                state.set_from_hsv(0.66, 1.0, intensity)
            
            state.dimmer = int(255 * intensity)
    
    def _apply_beat_pulse_mode(self, data: AnalysisData, beat_triggered: bool) -> None:
        for fixture in self.config.fixtures:
            state = self._states[fixture.name]
            scale = fixture.intensity_scale * self.config.effects.intensity
            
            if beat_triggered:
                brightness = scale
            else:
                brightness = (1.0 - data.beat_position) * scale
            
            hue = data.features.energy * 0.3
            state.set_from_hsv(hue, 1.0, brightness)
            state.dimmer = int(255 * brightness)
    
    def _apply_color_cycle_mode(self, data: AnalysisData) -> None:
        beat_interval = data.beat_interval_ms / 1000.0
        if beat_interval > 0:
            self._color_phase += (1.0 / beat_interval) * 0.05 * self.config.effects.color_speed
        self._color_phase %= 1.0
        
        num_fixtures = max(1, len(self.config.fixtures))
        
        for fixture in self.config.fixtures:
            state = self._states[fixture.name]
            position_offset = fixture.position / num_fixtures
            hue = (self._color_phase + position_offset) % 1.0
            brightness = data.features.energy * fixture.intensity_scale * self.config.effects.intensity
            state.set_from_hsv(hue, 1.0, brightness)
            state.dimmer = int(255 * brightness)
    
    def _apply_rainbow_wave_mode(self, data: AnalysisData) -> None:
        self._wave_phase += 0.02 * self.config.effects.color_speed
        self._wave_phase %= 1.0
        
        num_fixtures = max(1, len(self.config.fixtures))
        sorted_fixtures = sorted(self.config.fixtures, key=lambda f: f.position)
        
        for i, fixture in enumerate(sorted_fixtures):
            state = self._states[fixture.name]
            position = i / num_fixtures
            hue = (self._wave_phase + position) % 1.0
            pulse = 1.0 - (data.beat_position * 0.2)
            brightness = data.features.energy * fixture.intensity_scale * self.config.effects.intensity * pulse
            state.set_from_hsv(hue, 1.0, brightness)
            state.dimmer = int(255 * brightness)
    
    def _apply_strobe_beat_mode(self, data: AnalysisData, beat_triggered: bool) -> None:
        for fixture in self.config.fixtures:
            state = self._states[fixture.name]
            scale = fixture.intensity_scale * self.config.effects.intensity
            
            if beat_triggered:
                state.red = 255
                state.green = 255
                state.blue = 255
                state.dimmer = int(255 * scale)
                state.strobe = 200
            else:
                decay = max(0, 1.0 - data.beat_position * 4)
                brightness = int(255 * decay * scale)
                state.red = brightness
                state.green = brightness
                state.blue = brightness
                state.dimmer = brightness
                state.strobe = 0 if data.beat_position > 0.25 else 200
    
    def _apply_random_flash_mode(self, data: AnalysisData, beat_triggered: bool) -> None:
        import random
        
        fixtures = self.config.fixtures
        if beat_triggered and fixtures:
            num_to_flash = max(1, len(fixtures) // 3)
            flashing = random.sample(list(fixtures), min(num_to_flash, len(fixtures)))
            flash_names = {f.name for f in flashing}
        else:
            flash_names = set()
        
        for fixture in fixtures:
            state = self._states[fixture.name]
            scale = fixture.intensity_scale * self.config.effects.intensity
            
            if fixture.name in flash_names:
                hue = random.random()
                state.set_from_hsv(hue, 1.0, scale)
                state.dimmer = int(255 * scale)
            else:
                decay = max(0, 1.0 - data.beat_position * 3)
                state.dimmer = int(state.dimmer * decay)
                state.red = int(state.red * decay)
                state.green = int(state.green * decay)
                state.blue = int(state.blue * decay)
    
    def _apply_movement(self, data: AnalysisData) -> None:
        speed = self.config.effects.movement_speed
        
        for fixture in self.config.fixtures:
            profile = self._get_profile(fixture)
            channels = fixture.get_channels(profile)
            
            has_pan = any(ch.channel_type == ChannelType.POSITION_PAN for ch in channels)
            has_tilt = any(ch.channel_type == ChannelType.POSITION_TILT for ch in channels)
            
            if not (has_pan or has_tilt):
                continue
            
            state = self._states[fixture.name]
            bar_phase = data.bar_position * math.pi * 2
            beat_phase = data.beat_position * math.pi * 2
            
            if has_pan:
                pan_range = fixture.pan_max - fixture.pan_min
                pan_center = (fixture.pan_max + fixture.pan_min) / 2
                pan_offset = math.sin(bar_phase) * (pan_range / 2) * speed
                state.pan = int(pan_center + pan_offset)
            
            if has_tilt:
                tilt_range = fixture.tilt_max - fixture.tilt_min
                tilt_center = (fixture.tilt_max + fixture.tilt_min) / 2
                tilt_offset = math.sin(beat_phase) * (tilt_range / 4) * speed
                state.tilt = int(tilt_center + tilt_offset)
            
            state.pt_speed = int(255 * (1.0 - speed))
    
    def _check_energy_drop(self, data: AnalysisData) -> None:
        current_energy = data.features.energy
        
        if current_energy - self._last_energy > 0.3:
            self._strobe_active = True
        elif current_energy < self._last_energy - 0.1:
            self._strobe_active = False
        
        self._last_energy = current_energy
        
        if self._strobe_active:
            for fixture in self.config.fixtures:
                self._states[fixture.name].strobe = 200
    
    def _apply_smoothing(self) -> None:
        factor = self.config.effects.smooth_factor
        inverse = 1.0 - factor
        
        for fixture in self.config.fixtures:
            current = self._states[fixture.name]
            smoothed = self._smoothed_values[fixture.name]
            
            smoothed.red = int(smoothed.red * factor + current.red * inverse)
            smoothed.green = int(smoothed.green * factor + current.green * inverse)
            smoothed.blue = int(smoothed.blue * factor + current.blue * inverse)
            smoothed.white = int(smoothed.white * factor + current.white * inverse)
            smoothed.amber = int(smoothed.amber * factor + current.amber * inverse)
            smoothed.uv = int(smoothed.uv * factor + current.uv * inverse)
            smoothed.cyan = int(smoothed.cyan * factor + current.cyan * inverse)
            smoothed.magenta = int(smoothed.magenta * factor + current.magenta * inverse)
            smoothed.yellow = int(smoothed.yellow * factor + current.yellow * inverse)
            smoothed.dimmer = int(smoothed.dimmer * factor + current.dimmer * inverse)
            smoothed.pan = int(smoothed.pan * factor + current.pan * inverse)
            smoothed.pan_fine = current.pan_fine
            smoothed.tilt = int(smoothed.tilt * factor + current.tilt * inverse)
            smoothed.tilt_fine = current.tilt_fine
            smoothed.pt_speed = current.pt_speed
            smoothed.strobe = current.strobe
            smoothed.color_macro = current.color_macro
            smoothed.effect = current.effect
            smoothed.effect_speed = current.effect_speed
            smoothed.gobo = current.gobo
            smoothed.prism = current.prism
            smoothed.zoom = int(smoothed.zoom * factor + current.zoom * inverse)
            smoothed.focus = int(smoothed.focus * factor + current.focus * inverse)
            smoothed.iris = current.iris
    
    def _output_to_dmx(self) -> None:
        for fixture in self.config.fixtures:
            profile = self._get_profile(fixture)
            state = self._smoothed_values[fixture.name]
            start_ch = fixture.start_channel
            channels = fixture.get_channels(profile)
            
            if not channels:
                continue
            
            for ch in channels:
                if not ch.enabled:
                    continue
                
                dmx_channel = ch.get_dmx_channel(start_ch)
                
                # Fixed value takes priority
                if ch.channel_type == ChannelType.FIXED and ch.fixed_value is not None:
                    self.dmx.set_channel(dmx_channel, ch.fixed_value)
                elif ch.fixed_value is not None:
                    self.dmx.set_channel(dmx_channel, ch.fixed_value)
                else:
                    value = self._get_channel_value(state, ch, profile)
                    self.dmx.set_channel(dmx_channel, value)
    
    def _get_channel_value(self, state: FixtureState, ch: ChannelConfig, profile: Optional[FixtureProfile]) -> int:
        ct = ch.channel_type
        
        # Intensity channels
        if ct in (ChannelType.INTENSITY, ChannelType.INTENSITY_DIMMER):
            return state.dimmer
        elif ct == ChannelType.INTENSITY_MASTER_DIMMER:
            return self._calculate_dimmer_value(state, ch)
        elif ct == ChannelType.INTENSITY_RED:
            return state.red
        elif ct == ChannelType.INTENSITY_GREEN:
            return state.green
        elif ct == ChannelType.INTENSITY_BLUE:
            return state.blue
        elif ct == ChannelType.INTENSITY_WHITE:
            return state.white
        elif ct == ChannelType.INTENSITY_AMBER:
            return state.amber
        elif ct == ChannelType.INTENSITY_UV:
            return state.uv
        elif ct == ChannelType.INTENSITY_CYAN:
            return state.cyan
        elif ct == ChannelType.INTENSITY_MAGENTA:
            return state.magenta
        elif ct == ChannelType.INTENSITY_YELLOW:
            return state.yellow
        
        # Position channels
        elif ct == ChannelType.POSITION_PAN:
            return state.pan
        elif ct == ChannelType.POSITION_PAN_FINE:
            return state.pan_fine
        elif ct == ChannelType.POSITION_TILT:
            return state.tilt
        elif ct == ChannelType.POSITION_TILT_FINE:
            return state.tilt_fine
        
        # Speed channels
        elif ct in (ChannelType.SPEED_PAN_TILT_FAST_SLOW, ChannelType.SPEED_PAN_TILT_SLOW_FAST):
            return state.pt_speed
        
        # Shutter/strobe
        elif ct in (ChannelType.SHUTTER_STROBE, ChannelType.SHUTTER_STROBE_SLOW_FAST, ChannelType.SHUTTER_STROBE_FAST_SLOW):
            return state.strobe
        
        # Color
        elif ct == ChannelType.COLOR_MACRO:
            return state.color_macro
        elif ct == ChannelType.COLOR_WHEEL:
            return state.color_macro
        
        # Effects
        elif ct == ChannelType.EFFECT:
            return state.effect
        elif ct == ChannelType.EFFECT_SPEED:
            return state.effect_speed
        
        # Gobo
        elif ct in (ChannelType.GOBO_WHEEL, ChannelType.GOBO_INDEX):
            return state.gobo
        
        # Prism
        elif ct in (ChannelType.PRISM, ChannelType.PRISM_ROTATION):
            return state.prism
        
        # Beam
        elif ct in (ChannelType.BEAM_ZOOM_SMALL_BIG, ChannelType.BEAM_ZOOM_BIG_SMALL):
            return state.zoom
        elif ct in (ChannelType.BEAM_FOCUS_NEAR_FAR, ChannelType.BEAM_FOCUS_FAR_NEAR):
            return state.focus
        elif ct in (ChannelType.SHUTTER_IRIS_MIN_TO_MAX, ChannelType.SHUTTER_IRIS_MAX_TO_MIN):
            return state.iris
        
        # Maintenance / Nothing - use default
        elif ct in (ChannelType.MAINTENANCE, ChannelType.NOTHING):
            return ch.default_value
        
        return ch.default_value
    
    def _calculate_dimmer_value(self, state: FixtureState, ch: ChannelConfig) -> int:
        """Handle combined dimmer/strobe channels (like Muvy WashQ ch6)."""
        if not ch.capabilities:
            return state.dimmer
        
        # Find capability ranges
        dimmer_cap = None
        strobe_cap = None
        open_cap = None
        
        for cap in ch.capabilities:
            name_lower = cap.name.lower()
            if "dimmer" in name_lower:
                dimmer_cap = cap
            elif "strobe" in name_lower:
                strobe_cap = cap
            elif "open" in name_lower:
                open_cap = cap
        
        # Strobe active - use strobe range
        if state.strobe > 0 and strobe_cap:
            strobe_span = strobe_cap.max_value - strobe_cap.min_value
            return strobe_cap.min_value + int((state.strobe / 255.0) * strobe_span)
        
        # Full brightness - use open range
        if state.dimmer >= 250 and open_cap:
            return open_cap.min_value
        
        # Normal dimmer
        if dimmer_cap:
            dimmer_span = dimmer_cap.max_value - dimmer_cap.min_value
            return dimmer_cap.min_value + int((state.dimmer / 255.0) * dimmer_span)
        
        return state.dimmer
    
    def blackout(self) -> None:
        for fixture in self.config.fixtures:
            state = self._states[fixture.name]
            state.red = 0
            state.green = 0
            state.blue = 0
            state.white = 0
            state.dimmer = 0
            state.strobe = 0
        
        self._apply_smoothing()
        self._output_to_dmx()
        self.dmx.blackout()
    
    def get_fixture_states(self) -> dict[str, FixtureState]:
        return self._smoothed_values.copy()
