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
    ChannelFunction, ChannelMapping, ShowConfig, FIXTURE_PRESETS
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
    
    # Intensity
    dimmer: int = 255
    strobe: int = 0  # 0 = no strobe
    
    # Movement
    pan: int = 128
    pan_fine: int = 0
    tilt: int = 128
    tilt_fine: int = 0
    pt_speed: int = 0  # 0 = fast
    
    # Macros
    color_macro: int = 0
    macro_speed: int = 0
    effect_macro: int = 0
    
    # Control
    control: int = 0
    
    def get_rgb(self) -> tuple[int, int, int]:
        """Get RGB values."""
        return (self.red, self.green, self.blue)
    
    def set_rgb(self, r: int, g: int, b: int) -> None:
        """Set RGB values."""
        self.red = max(0, min(255, int(r)))
        self.green = max(0, min(255, int(g)))
        self.blue = max(0, min(255, int(b)))
    
    def set_from_hsv(self, h: float, s: float, v: float) -> None:
        """Set color from HSV (all values 0-1)."""
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
        self.red = int(r * 255)
        self.green = int(g * 255)
        self.blue = int(b * 255)


class EffectsEngine:
    """
    Engine that processes audio analysis and generates DMX output.
    """
    
    def __init__(self, dmx_controller: DMXController, config: ShowConfig):
        self.dmx = dmx_controller
        self.config = config
        
        # Cache profiles for quick lookup
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
        """Load fixture profiles from config and presets."""
        # Load built-in presets
        self._profiles.update(FIXTURE_PRESETS)
        
        # Load custom profiles from config
        for profile in self.config.profiles:
            self._profiles[profile.name] = profile
    
    def _init_fixture_states(self) -> None:
        """Initialize state objects for all fixtures."""
        for fixture in self.config.fixtures:
            self._states[fixture.name] = FixtureState()
            self._smoothed_values[fixture.name] = FixtureState()
    
    def _get_profile(self, fixture: FixtureConfig) -> Optional[FixtureProfile]:
        """Get the profile for a fixture."""
        return self._profiles.get(fixture.profile_name)
    
    def update_config(self, config: ShowConfig) -> None:
        """Update configuration."""
        self.config = config
        self._load_profiles()
        
        # Add states for new fixtures
        for fixture in config.fixtures:
            if fixture.name not in self._states:
                self._states[fixture.name] = FixtureState()
                self._smoothed_values[fixture.name] = FixtureState()
    
    def process(self, data: AnalysisData) -> dict[str, FixtureState]:
        """
        Process audio analysis data and update DMX output.
        
        Returns:
            Dictionary of fixture states for visualization.
        """
        self._time = time.time()
        
        # Detect beat
        beat_triggered = data.estimated_beat != self._last_beat
        bar_triggered = data.estimated_bar != self._last_bar
        self._last_beat = data.estimated_beat
        self._last_bar = data.estimated_bar
        
        # Apply visualization mode
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
        
        # Apply movement if enabled
        if self.config.effects.movement_enabled:
            self._apply_movement(data)
        
        # Apply strobe on drop if enabled
        if self.config.effects.strobe_on_drop:
            self._check_energy_drop(data)
        
        # Apply smoothing
        self._apply_smoothing()
        
        # Output to DMX
        self._output_to_dmx()
        
        return self._smoothed_values.copy()
    
    def _apply_energy_mode(self, data: AnalysisData) -> None:
        """Energy-based visualization - intensity maps to brightness."""
        energy = data.features.energy
        intensity = energy * self.config.effects.intensity
        
        # Use valence for color (happy = warm, sad = cool)
        hue = 0.0 + data.features.valence * 0.3
        
        for fixture in self.config.fixtures:
            state = self._states[fixture.name]
            # Beat pulse effect
            pulse = 1.0 - (data.beat_position * 0.3)
            brightness = intensity * pulse * fixture.intensity_scale
            
            state.set_from_hsv(hue, 1.0, brightness)
            state.dimmer = int(255 * brightness)
    
    def _apply_frequency_split_mode(self, data: AnalysisData) -> None:
        """Split fixtures into bass/mid/high frequency bands."""
        num_fixtures = len(self.config.fixtures)
        if num_fixtures == 0:
            return
        
        # Sort fixtures by position
        sorted_fixtures = sorted(self.config.fixtures, key=lambda f: f.position)
        
        # Divide into thirds
        third = max(1, num_fixtures // 3)
        
        # Use actual frequency band data
        bass_intensity = data.features.bass
        mid_intensity = data.features.mid
        high_intensity = data.features.high
        
        for i, fixture in enumerate(sorted_fixtures):
            state = self._states[fixture.name]
            scale = fixture.intensity_scale * self.config.effects.intensity
            
            if i < third:
                # Bass - red/orange
                intensity = bass_intensity * scale
                state.set_from_hsv(0.0, 1.0, intensity)
            elif i < third * 2:
                # Mid - green/cyan
                intensity = mid_intensity * scale
                state.set_from_hsv(0.33, 1.0, intensity)
            else:
                # High - blue/purple
                intensity = high_intensity * scale
                state.set_from_hsv(0.66, 1.0, intensity)
            
            state.dimmer = int(255 * intensity)
    
    def _apply_beat_pulse_mode(self, data: AnalysisData, beat_triggered: bool) -> None:
        """Pulse on beats."""
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
        """Cycle colors based on tempo."""
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
        """Rainbow wave effect across fixtures."""
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
        """Strobe on beats."""
        for fixture in self.config.fixtures:
            state = self._states[fixture.name]
            profile = self._get_profile(fixture)
            scale = fixture.intensity_scale * self.config.effects.intensity
            
            if beat_triggered:
                state.red = 255
                state.green = 255
                state.blue = 255
                state.dimmer = int(255 * scale)
                # Use strobe range if fixture supports it
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
        """Random fixture flashes on beats."""
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
                current_brightness = state.dimmer / 255.0
                new_brightness = current_brightness * decay
                state.dimmer = int(255 * new_brightness)
                state.red = int(state.red * decay)
                state.green = int(state.green * decay)
                state.blue = int(state.blue * decay)
    
    def _apply_movement(self, data: AnalysisData) -> None:
        """Apply pan/tilt movement to fixtures."""
        speed = self.config.effects.movement_speed
        
        for fixture in self.config.fixtures:
            profile = self._get_profile(fixture)
            if not profile or not (profile.has_pan or profile.has_tilt):
                continue
            
            state = self._states[fixture.name]
            
            bar_phase = data.bar_position * math.pi * 2
            beat_phase = data.beat_position * math.pi * 2
            
            # Pan: slow sweep based on bar
            if profile.has_pan:
                pan_range = fixture.pan_max - fixture.pan_min
                pan_center = (fixture.pan_max + fixture.pan_min) / 2
                pan_offset = math.sin(bar_phase) * (pan_range / 2) * speed
                state.pan = int(pan_center + pan_offset)
            
            # Tilt: bob based on beat
            if profile.has_tilt:
                tilt_range = fixture.tilt_max - fixture.tilt_min
                tilt_center = (fixture.tilt_max + fixture.tilt_min) / 2
                tilt_offset = math.sin(beat_phase) * (tilt_range / 4) * speed
                state.tilt = int(tilt_center + tilt_offset)
            
            # Movement speed (0=fast, higher=slower for Muvy WashQ style)
            state.pt_speed = int(255 * (1.0 - speed))
    
    def _check_energy_drop(self, data: AnalysisData) -> None:
        """Check for energy drops to trigger strobe."""
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
        """Apply smoothing to prevent jarring transitions."""
        factor = self.config.effects.smooth_factor
        inverse = 1.0 - factor
        
        for fixture in self.config.fixtures:
            current = self._states[fixture.name]
            smoothed = self._smoothed_values[fixture.name]
            
            smoothed.red = int(smoothed.red * factor + current.red * inverse)
            smoothed.green = int(smoothed.green * factor + current.green * inverse)
            smoothed.blue = int(smoothed.blue * factor + current.blue * inverse)
            smoothed.white = int(smoothed.white * factor + current.white * inverse)
            smoothed.dimmer = int(smoothed.dimmer * factor + current.dimmer * inverse)
            smoothed.pan = int(smoothed.pan * factor + current.pan * inverse)
            smoothed.pan_fine = current.pan_fine
            smoothed.tilt = int(smoothed.tilt * factor + current.tilt * inverse)
            smoothed.tilt_fine = current.tilt_fine
            smoothed.pt_speed = current.pt_speed
            smoothed.strobe = current.strobe
            smoothed.color_macro = current.color_macro
            smoothed.macro_speed = current.macro_speed
            smoothed.effect_macro = current.effect_macro
            smoothed.control = current.control
    
    def _output_to_dmx(self) -> None:
        """Output fixture states to DMX controller."""
        for fixture in self.config.fixtures:
            profile = self._get_profile(fixture)
            if not profile:
                continue
            
            state = self._smoothed_values[fixture.name]
            start_ch = fixture.start_channel
            
            for ch_mapping in profile.channels:
                dmx_channel = ch_mapping.get_dmx_channel(start_ch)
                value = self._get_channel_value(state, ch_mapping, profile)
                self.dmx.set_channel(dmx_channel, value)
    
    def _get_channel_value(self, state: FixtureState, ch_mapping: ChannelMapping, profile: FixtureProfile) -> int:
        """Get the DMX value for a channel based on fixture state."""
        func = ch_mapping.function
        
        # Color channels
        if func == ChannelFunction.RED:
            return state.red
        elif func == ChannelFunction.GREEN:
            return state.green
        elif func == ChannelFunction.BLUE:
            return state.blue
        elif func == ChannelFunction.WHITE:
            return state.white
        
        # Movement channels
        elif func == ChannelFunction.PAN:
            return state.pan
        elif func == ChannelFunction.PAN_FINE:
            return state.pan_fine
        elif func == ChannelFunction.TILT:
            return state.tilt
        elif func == ChannelFunction.TILT_FINE:
            return state.tilt_fine
        elif func == ChannelFunction.PT_SPEED:
            return state.pt_speed
        
        # Dimmer - handle range-based channels (like Muvy WashQ)
        elif func == ChannelFunction.DIMMER:
            return self._calculate_dimmer_value(state, ch_mapping)
        
        # Strobe (standalone channel)
        elif func == ChannelFunction.STROBE:
            return state.strobe
        
        # Macros
        elif func == ChannelFunction.COLOR_MACRO:
            return state.color_macro
        elif func == ChannelFunction.MACRO_SPEED:
            return state.macro_speed
        elif func == ChannelFunction.EFFECT_MACRO:
            return state.effect_macro
        
        # Control
        elif func == ChannelFunction.CONTROL:
            return state.control
        
        return ch_mapping.default_value
    
    def _calculate_dimmer_value(self, state: FixtureState, ch_mapping: ChannelMapping) -> int:
        """
        Calculate dimmer channel value, handling fixtures with combined dimmer/strobe channels.
        For Muvy WashQ: 0-7=closed, 8-134=dimmer, 135-239=strobe, 240-255=open
        """
        if not ch_mapping.ranges:
            # Simple dimmer, just return the value
            return state.dimmer
        
        # Find the dimmer range
        dimmer_range = None
        strobe_range = None
        open_range = None
        
        for r in ch_mapping.ranges:
            if r.name.lower() == "dimmer":
                dimmer_range = r
            elif r.name.lower() == "strobe":
                strobe_range = r
            elif r.name.lower() == "open":
                open_range = r
        
        # If strobe is active and we have a strobe range, use it
        if state.strobe > 0 and strobe_range:
            # Map strobe 0-255 to the strobe range
            strobe_span = strobe_range.max_value - strobe_range.min_value
            return strobe_range.min_value + int((state.strobe / 255.0) * strobe_span)
        
        # Otherwise use dimmer range or open
        if state.dimmer >= 250 and open_range:
            # Full brightness, use "open" range for no flicker
            return open_range.min_value
        
        if dimmer_range:
            # Map dimmer 0-255 to the dimmer range
            dimmer_span = dimmer_range.max_value - dimmer_range.min_value
            return dimmer_range.min_value + int((state.dimmer / 255.0) * dimmer_span)
        
        return state.dimmer
    
    def blackout(self) -> None:
        """Blackout all fixtures."""
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
        """Get current fixture states for visualization."""
        return self._smoothed_values.copy()
