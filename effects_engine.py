"""
Effects engine that converts audio analysis to DMX values.
Supports multiple visualization modes and fixture configurations.
"""
import math
import time
import colorsys
from typing import Optional
from dataclasses import dataclass

from config import FixtureConfig, EffectsConfig, VisualizationMode, ChannelType
from spotify_analyzer import AnalysisData
from dmx_controller import DMXController


@dataclass
class FixtureState:
    """Current state of a fixture."""
    red: int = 0
    green: int = 0
    blue: int = 0
    white: int = 0
    dimmer: int = 255
    pan: int = 128
    tilt: int = 128
    speed: int = 0
    strobe: int = 0
    
    def get_rgb(self) -> tuple[int, int, int]:
        """Get RGB values."""
        return (self.red, self.green, self.blue)
    
    def set_rgb(self, r: int, g: int, b: int) -> None:
        """Set RGB values."""
        self.red = max(0, min(255, r))
        self.green = max(0, min(255, g))
        self.blue = max(0, min(255, b))
    
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
    
    def __init__(self, dmx_controller: DMXController, fixtures: list[FixtureConfig], effects: EffectsConfig):
        self.dmx = dmx_controller
        self.fixtures = fixtures
        self.effects = effects
        
        # State for each fixture
        self._states: dict[str, FixtureState] = {}
        for fixture in fixtures:
            self._states[fixture.name] = FixtureState()
        
        # Smoothing state
        self._smoothed_values: dict[str, FixtureState] = {}
        for fixture in fixtures:
            self._smoothed_values[fixture.name] = FixtureState()
        
        # Animation state
        self._time = 0.0
        self._last_beat = 0
        self._last_bar = 0
        self._color_phase = 0.0
        self._wave_phase = 0.0
        self._strobe_active = False
        self._strobe_state = False
        self._last_energy = 0.5
    
    def update_config(self, fixtures: list[FixtureConfig], effects: EffectsConfig) -> None:
        """Update configuration."""
        self.fixtures = fixtures
        self.effects = effects
        
        # Add states for new fixtures
        for fixture in fixtures:
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
        mode = self.effects.mode
        
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
        if self.effects.movement_enabled:
            self._apply_movement(data)
        
        # Apply strobe on drop if enabled
        if self.effects.strobe_on_drop:
            self._check_energy_drop(data)
        
        # Apply strobe settings from fixtures
        self._apply_fixture_strobe()
        
        # Apply smoothing
        self._apply_smoothing()
        
        # Output to DMX
        self._output_to_dmx()
        
        return self._smoothed_values.copy()
    
    def _apply_energy_mode(self, data: AnalysisData) -> None:
        """Energy-based visualization - intensity maps to brightness."""
        energy = data.features.energy
        intensity = energy * self.effects.intensity
        
        # Use valence for color (happy = warm, sad = cool)
        hue = 0.0 + data.features.valence * 0.3  # Red to yellow range
        
        for fixture in self.fixtures:
            state = self._states[fixture.name]
            # Beat pulse effect
            pulse = 1.0 - (data.beat_position * 0.3)
            brightness = intensity * pulse
            
            state.set_from_hsv(hue, 1.0, brightness)
            state.dimmer = int(255 * brightness)
    
    def _apply_frequency_split_mode(self, data: AnalysisData) -> None:
        """Split fixtures into bass/mid/high frequency bands."""
        num_fixtures = len(self.fixtures)
        if num_fixtures == 0:
            return
        
        # Sort fixtures by position
        sorted_fixtures = sorted(self.fixtures, key=lambda f: f.position)
        
        # Divide into thirds (bass/mid/high)
        third = max(1, num_fixtures // 3)
        
        # Estimate frequency band intensities from audio features
        bass_intensity = data.features.energy * (1.0 - data.features.acousticness)
        mid_intensity = data.features.danceability
        high_intensity = data.features.speechiness + data.features.liveness * 0.5
        
        for i, fixture in enumerate(sorted_fixtures):
            state = self._states[fixture.name]
            
            if i < third:
                # Bass - red/orange
                intensity = bass_intensity * self.effects.intensity
                state.set_from_hsv(0.0, 1.0, intensity)
            elif i < third * 2:
                # Mid - green/cyan
                intensity = mid_intensity * self.effects.intensity
                state.set_from_hsv(0.33, 1.0, intensity)
            else:
                # High - blue/purple
                intensity = high_intensity * self.effects.intensity
                state.set_from_hsv(0.66, 1.0, intensity)
            
            state.dimmer = int(255 * intensity)
    
    def _apply_beat_pulse_mode(self, data: AnalysisData, beat_triggered: bool) -> None:
        """Pulse on beats."""
        for fixture in self.fixtures:
            state = self._states[fixture.name]
            
            if beat_triggered:
                # Full brightness on beat
                brightness = self.effects.intensity
            else:
                # Decay between beats
                brightness = (1.0 - data.beat_position) * self.effects.intensity
            
            # Color based on energy
            hue = data.features.energy * 0.3
            state.set_from_hsv(hue, 1.0, brightness)
            state.dimmer = int(255 * brightness)
    
    def _apply_color_cycle_mode(self, data: AnalysisData) -> None:
        """Cycle colors based on tempo."""
        # Advance color phase based on tempo
        beat_interval = data.beat_interval_ms / 1000.0
        if beat_interval > 0:
            self._color_phase += (1.0 / beat_interval) * 0.05 * self.effects.color_speed
        self._color_phase %= 1.0
        
        for fixture in self.fixtures:
            state = self._states[fixture.name]
            
            # Offset hue by fixture position
            position_offset = fixture.position / max(1, len(self.fixtures))
            hue = (self._color_phase + position_offset) % 1.0
            
            brightness = data.features.energy * self.effects.intensity
            state.set_from_hsv(hue, 1.0, brightness)
            state.dimmer = int(255 * brightness)
    
    def _apply_rainbow_wave_mode(self, data: AnalysisData) -> None:
        """Rainbow wave effect across fixtures."""
        # Advance wave phase
        self._wave_phase += 0.02 * self.effects.color_speed
        self._wave_phase %= 1.0
        
        num_fixtures = max(1, len(self.fixtures))
        sorted_fixtures = sorted(self.fixtures, key=lambda f: f.position)
        
        for i, fixture in enumerate(sorted_fixtures):
            state = self._states[fixture.name]
            
            # Create wave across fixtures
            position = i / num_fixtures
            hue = (self._wave_phase + position) % 1.0
            
            # Brightness from energy with beat pulse
            pulse = 1.0 - (data.beat_position * 0.2)
            brightness = data.features.energy * self.effects.intensity * pulse
            
            state.set_from_hsv(hue, 1.0, brightness)
            state.dimmer = int(255 * brightness)
    
    def _apply_strobe_beat_mode(self, data: AnalysisData, beat_triggered: bool) -> None:
        """Strobe on beats."""
        for fixture in self.fixtures:
            state = self._states[fixture.name]
            
            if beat_triggered:
                # Flash white on beat
                state.red = 255
                state.green = 255
                state.blue = 255
                state.dimmer = int(255 * self.effects.intensity)
                state.strobe = 200  # Fast strobe
            else:
                # Quick decay
                decay = max(0, 1.0 - data.beat_position * 4)
                brightness = int(255 * decay * self.effects.intensity)
                state.red = brightness
                state.green = brightness
                state.blue = brightness
                state.dimmer = brightness
                state.strobe = 0 if data.beat_position > 0.25 else 200
    
    def _apply_random_flash_mode(self, data: AnalysisData, beat_triggered: bool) -> None:
        """Random fixture flashes on beats."""
        import random
        
        if beat_triggered and self.fixtures:
            # Pick random fixture(s) to flash
            num_to_flash = max(1, len(self.fixtures) // 3)
            flashing = random.sample(self.fixtures, min(num_to_flash, len(self.fixtures)))
            flash_names = {f.name for f in flashing}
        else:
            flash_names = set()
        
        for fixture in self.fixtures:
            state = self._states[fixture.name]
            
            if fixture.name in flash_names:
                # Random bright color
                hue = random.random()
                state.set_from_hsv(hue, 1.0, self.effects.intensity)
                state.dimmer = int(255 * self.effects.intensity)
            else:
                # Decay
                decay = max(0, 1.0 - data.beat_position * 3)
                current_brightness = state.dimmer / 255.0
                new_brightness = current_brightness * decay
                state.dimmer = int(255 * new_brightness)
                state.red = int(state.red * decay)
                state.green = int(state.green * decay)
                state.blue = int(state.blue * decay)
    
    def _apply_movement(self, data: AnalysisData) -> None:
        """Apply pan/tilt movement to fixtures."""
        speed = self.effects.movement_speed
        
        for fixture in self.fixtures:
            state = self._states[fixture.name]
            
            # Calculate movement based on beat position and bar
            bar_phase = data.bar_position * math.pi * 2
            beat_phase = data.beat_position * math.pi * 2
            
            # Pan: slow sweep based on bar
            pan_range = fixture.pan_max - fixture.pan_min
            pan_center = (fixture.pan_max + fixture.pan_min) / 2
            pan_offset = math.sin(bar_phase) * (pan_range / 2) * speed
            state.pan = int(pan_center + pan_offset)
            
            # Tilt: bob based on beat
            tilt_range = fixture.tilt_max - fixture.tilt_min
            tilt_center = (fixture.tilt_max + fixture.tilt_min) / 2
            tilt_offset = math.sin(beat_phase) * (tilt_range / 4) * speed
            state.tilt = int(tilt_center + tilt_offset)
            
            # Movement speed channel
            state.speed = int(128 + 127 * (1.0 - speed))
    
    def _check_energy_drop(self, data: AnalysisData) -> None:
        """Check for energy drops to trigger strobe."""
        current_energy = data.features.energy
        
        # Detect significant energy increase (drop/build)
        if current_energy - self._last_energy > 0.3:
            self._strobe_active = True
        elif current_energy < self._last_energy - 0.1:
            self._strobe_active = False
        
        self._last_energy = current_energy
        
        if self._strobe_active:
            for fixture in self.fixtures:
                self._states[fixture.name].strobe = 200
    
    def _apply_fixture_strobe(self) -> None:
        """Apply per-fixture strobe settings."""
        for fixture in self.fixtures:
            if fixture.strobe_enabled:
                self._states[fixture.name].strobe = fixture.strobe_speed
    
    def _apply_smoothing(self) -> None:
        """Apply smoothing to prevent jarring transitions."""
        factor = self.effects.smooth_factor
        inverse = 1.0 - factor
        
        for fixture in self.fixtures:
            current = self._states[fixture.name]
            smoothed = self._smoothed_values[fixture.name]
            
            # Smooth each value
            smoothed.red = int(smoothed.red * factor + current.red * inverse)
            smoothed.green = int(smoothed.green * factor + current.green * inverse)
            smoothed.blue = int(smoothed.blue * factor + current.blue * inverse)
            smoothed.white = int(smoothed.white * factor + current.white * inverse)
            smoothed.dimmer = int(smoothed.dimmer * factor + current.dimmer * inverse)
            smoothed.pan = int(smoothed.pan * factor + current.pan * inverse)
            smoothed.tilt = int(smoothed.tilt * factor + current.tilt * inverse)
            smoothed.speed = current.speed  # Don't smooth speed
            smoothed.strobe = current.strobe  # Don't smooth strobe
    
    def _output_to_dmx(self) -> None:
        """Output fixture states to DMX controller."""
        for fixture in self.fixtures:
            state = self._smoothed_values[fixture.name]
            
            for channel_config in fixture.channels:
                channel = channel_config.channel
                channel_type = channel_config.channel_type
                
                value = 0
                if channel_type == ChannelType.RED:
                    value = state.red
                elif channel_type == ChannelType.GREEN:
                    value = state.green
                elif channel_type == ChannelType.BLUE:
                    value = state.blue
                elif channel_type == ChannelType.WHITE:
                    value = state.white
                elif channel_type == ChannelType.DIMMER:
                    value = state.dimmer
                elif channel_type == ChannelType.PAN:
                    value = state.pan
                elif channel_type == ChannelType.PAN_FINE:
                    value = 0  # Could add fine control
                elif channel_type == ChannelType.TILT:
                    value = state.tilt
                elif channel_type == ChannelType.TILT_FINE:
                    value = 0
                elif channel_type == ChannelType.SPEED:
                    value = state.speed
                elif channel_type == ChannelType.STROBE:
                    value = state.strobe
                
                self.dmx.set_channel(channel, value)
    
    def blackout(self) -> None:
        """Blackout all fixtures."""
        for fixture in self.fixtures:
            state = self._states[fixture.name]
            state.red = 0
            state.green = 0
            state.blue = 0
            state.white = 0
            state.dimmer = 0
            state.strobe = 0
        
        self._output_to_dmx()
        self.dmx.blackout()
    
    def get_fixture_states(self) -> dict[str, FixtureState]:
        """Get current fixture states for visualization."""
        return self._smoothed_values.copy()
