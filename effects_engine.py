"""
Effects engine that converts audio analysis to DMX values.
Uses proper audio-reactive algorithms based on professional lighting design principles:

- Bass (20-250Hz) → Intensity, movement triggers, pulse effects
- Mids (250-4000Hz) → Color changes, smooth transitions  
- Highs (4000Hz+) → Fast effects, strobes, sparkle
- Beat detection → Synchronized movement and color changes
- Energy levels → Overall brightness and effect intensity

Movement happens on musical events (beats, drops), not constantly.
Colors shift smoothly based on musical mood, not randomly.
"""
import math
import time
import colorsys
from typing import Optional, List, Tuple
from dataclasses import dataclass

from config import (
    FixtureConfig, FixtureProfile, VisualizationMode,
    ChannelType, ChannelConfig, ShowConfig, FIXTURE_PRESETS
)
from audio_analyzer import AnalysisData
from dmx_controller import DMXController

# Import visualization and movement modes
from visualization_modes import (
    apply_energy_mode,
    apply_frequency_split_mode,
    apply_beat_pulse_mode,
    apply_color_cycle_mode,
    apply_rainbow_wave_mode,
    apply_strobe_beat_mode,
    apply_random_flash_mode,
)
from movement_modes import apply_movement


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
    pt_speed: int = 0  # 0 = fast for FAST_SLOW type
    
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
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v)))
        self.red = int(r * 255)
        self.green = int(g * 255)
        self.blue = int(b * 255)


class EffectsEngine:
    """
    Engine that processes audio analysis and generates DMX output.
    
    Design principles:
    - Bass drives intensity and triggers movement
    - Mids drive color palette selection
    - Highs drive fast accent effects
    - Movement happens on beats, not constantly
    - Smooth transitions with proper easing
    """
    
    def __init__(self, dmx_controller: DMXController, config: ShowConfig):
        self.dmx = dmx_controller
        self.config = config
        
        # Cache profiles
        self._profiles: dict[str, FixtureProfile] = {}
        self._load_profiles()
        
        # State for each fixture
        self._states: dict[str, FixtureState] = {}
        self._smoothed_values: dict[str, FixtureState] = {}
        
        # Movement state - positions are set on beats, then fixture moves there
        # (must be initialized before _init_fixture_states)
        self._target_pan: dict[str, int] = {}
        self._target_tilt: dict[str, int] = {}
        
        # Movement state for sweep/continuous modes
        # (must be initialized before _init_fixture_states)
        self._sweep_phase: dict[str, float] = {}
        self._sweep_direction: dict[str, float] = {}  # Can be float for smooth animations
        self._wall_corner_index: dict[str, int] = {}
        
        self._init_fixture_states()
        
        # ===== Animation state =====
        self._time = 0.0
        self._start_time = time.time()
        
        # Beat tracking
        self._last_beat = 0
        self._last_bar = 0
        self._beats_since_move = 0  # Track beats to trigger movement
        
        # Color state - hue cycles slowly, shifts on musical events
        self._base_hue = 0.0  # Base color that drifts slowly
        self._target_hue = 0.0  # Target hue for smooth transitions
        self._current_hue = 0.0  # Actual displayed hue
        
        # Album color palette (from album art)
        self._album_colors: List[Tuple[int, int, int]] = []
        self._album_hues: List[float] = []  # Hue values extracted from album colors
        self._color_index = 0  # Current index in album color palette
        
        self._movement_triggered = False
        
        # Intensity state
        self._base_intensity = 0.5
        self._pulse_intensity = 0.0  # Decays after beats
        
        # Energy tracking for drop detection
        self._energy_history: list[float] = []
        self._avg_energy = 0.5
        self._is_buildup = False
        self._is_drop = False
        self._drop_time = 0.0
        
        # Strobe state
        self._strobe_active = False
        self._strobe_end_time = 0.0
        
        # Blackout state
        self._blackout_active = False
        
        # Time tracking for position updates
        self._last_position_update = 0.0
    
    def _load_profiles(self) -> None:
        self._profiles.update(FIXTURE_PRESETS)
        for profile in self.config.profiles:
            self._profiles[profile.name] = profile
    
    def _init_fixture_states(self) -> None:
        for fixture in self.config.fixtures:
            self._states[fixture.name] = FixtureState()
            self._smoothed_values[fixture.name] = FixtureState()
            # Initialize movement targets to center
            self._target_pan[fixture.name] = 128
            self._target_tilt[fixture.name] = 128
            # Initialize sweep/continuous movement state
            self._sweep_phase[fixture.name] = 0.0
            self._sweep_direction[fixture.name] = 1.0
            self._wall_corner_index[fixture.name] = 0
    
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
                self._target_pan[fixture.name] = 128
                self._target_tilt[fixture.name] = 128
                self._sweep_phase[fixture.name] = 0.0
                self._sweep_direction[fixture.name] = 1.0
                self._wall_corner_index[fixture.name] = 0
    
    def process(self, data: AnalysisData) -> dict[str, FixtureState]:
        """Process audio data and update fixture states."""
        # If blackout is active, keep all channels at zero
        if self._blackout_active:
            self._output_to_dmx()
            return self._smoothed_values.copy()
        
        self._time = time.time()
        
        # Update album colors if they changed
        if data.album_colors != self._album_colors:
            self._album_colors = list(data.album_colors)
            # Convert RGB colors to HSV for easier color manipulation
            self._album_hues = []
            for r, g, b in self._album_colors:
                h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                if s > 0.2 and v > 0.2:  # Only use saturated, visible colors
                    self._album_hues.append(h)
            self._color_index = 0
        
        # Detect beat and bar changes
        beat_triggered = data.estimated_beat != self._last_beat
        bar_triggered = data.estimated_bar != self._last_bar
        self._last_beat = data.estimated_beat
        self._last_bar = data.estimated_bar
        
        if beat_triggered:
            self._beats_since_move += 1
        
        # Update energy tracking for drop detection
        self._update_energy_tracking(data)
        
        # Process based on visualization mode
        mode = self.config.effects.mode
        
        if mode == VisualizationMode.ENERGY:
            apply_energy_mode(self, data, beat_triggered, bar_triggered)
        elif mode == VisualizationMode.FREQUENCY_SPLIT:
            apply_frequency_split_mode(self, data, beat_triggered)
        elif mode == VisualizationMode.BEAT_PULSE:
            apply_beat_pulse_mode(self, data, beat_triggered)
        elif mode == VisualizationMode.COLOR_CYCLE:
            apply_color_cycle_mode(self, data, beat_triggered)
        elif mode == VisualizationMode.RAINBOW_WAVE:
            apply_rainbow_wave_mode(self, data, beat_triggered)
        elif mode == VisualizationMode.STROBE_BEAT:
            apply_strobe_beat_mode(self, data, beat_triggered)
        elif mode == VisualizationMode.RANDOM_FLASH:
            apply_random_flash_mode(self, data, beat_triggered)
        
        # Apply movement if enabled - only on specific beats, not constantly
        if self.config.effects.movement_enabled:
            apply_movement(self, data, beat_triggered, bar_triggered)
        
        # Apply smoothing and output
        self._apply_smoothing()
        self._output_to_dmx()
        
        return self._smoothed_values.copy()
    
    def _update_energy_tracking(self, data: AnalysisData) -> None:
        """Track energy for detecting drops and builds."""
        energy = data.features.energy
        
        # Keep rolling history
        self._energy_history.append(energy)
        if len(self._energy_history) > 40:  # ~1 second at 40fps
            self._energy_history.pop(0)
        
        # Calculate average energy
        if self._energy_history:
            self._avg_energy = sum(self._energy_history) / len(self._energy_history)
        
        # Detect drops: sudden increase in energy after low period
        recent_avg = sum(self._energy_history[-10:]) / max(1, len(self._energy_history[-10:]))
        older_avg = sum(self._energy_history[:-10]) / max(1, len(self._energy_history[:-10])) if len(self._energy_history) > 10 else recent_avg
        
        # Drop detection: energy jumps significantly
        if recent_avg > older_avg + 0.3 and energy > 0.6:
            if not self._is_drop:
                self._is_drop = True
                self._drop_time = self._time
                self._strobe_active = True
                self._strobe_end_time = self._time + 0.5  # Strobe for 0.5s on drops
        elif self._time - self._drop_time > 2.0:
            self._is_drop = False
        
        # Buildup detection: energy steadily increasing
        if len(self._energy_history) >= 20:
            first_half = sum(self._energy_history[:10]) / 10
            second_half = sum(self._energy_history[10:20]) / 10
            self._is_buildup = second_half > first_half + 0.1
        
        # End strobe after duration
        if self._strobe_active and self._time > self._strobe_end_time:
            self._strobe_active = False
    
    def _ease_out_cubic(self, t: float) -> float:
        """Cubic ease-out function for smooth decay."""
        return 1 - pow(1 - t, 3)
    
    def _ease_in_out_sine(self, t: float) -> float:
        """Sine ease-in-out for smooth oscillation."""
        return -(math.cos(math.pi * t) - 1) / 2
    
    def _apply_smoothing(self) -> None:
        """Apply smoothing to fixture values for smooth transitions."""
        factor = self.config.effects.smooth_factor
        inverse = 1.0 - factor
        
        for fixture in self.config.fixtures:
            current = self._states[fixture.name]
            smoothed = self._smoothed_values[fixture.name]
            
            # Smooth color values
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
            
            # Position values smooth separately (handled in movement_modes)
            smoothed.pan = current.pan
            smoothed.pan_fine = current.pan_fine
            smoothed.tilt = current.tilt
            smoothed.tilt_fine = current.tilt_fine
            smoothed.pt_speed = current.pt_speed
            
            # Instant values (no smoothing)
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
        """Output current fixture states to DMX."""
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
        """Get the DMX value for a channel based on fixture state."""
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
        elif ct == ChannelType.SPEED_PAN_TILT_FAST_SLOW:
            return state.pt_speed
        elif ct == ChannelType.SPEED_PAN_TILT_SLOW_FAST:
            return 255 - state.pt_speed
        
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
        """Handle combined dimmer/strobe channels (like Muvy WashQ ch6).
        
        Channel 6 layout for Muvy WashQ:
        - 0-7: No function (OFF)
        - 8-134: Dimmer 0-100%
        - 135-239: Strobe slow to fast
        - 240-255: Open (100%, no strobe)
        """
        if not ch.capabilities:
            return state.dimmer
        
        # Find capability ranges
        dimmer_cap = None
        strobe_cap = None
        open_cap = None
        off_cap = None
        
        for cap in ch.capabilities:
            name_lower = cap.name.lower()
            if "dimmer" in name_lower:
                dimmer_cap = cap
            elif "strobe" in name_lower:
                strobe_cap = cap
            elif "open" in name_lower:
                open_cap = cap
            elif "off" in name_lower or "no function" in name_lower:
                off_cap = cap
        
        # If dimmer is essentially off (very low), use OFF range to prevent flicker
        if state.dimmer < 5:
            if off_cap:
                return off_cap.min_value  # Return 0 (OFF)
            return 0
        
        # Strobe active - use strobe range (only if explicitly enabled)
        if state.strobe > 0 and strobe_cap:
            strobe_span = strobe_cap.max_value - strobe_cap.min_value
            return strobe_cap.min_value + int((state.strobe / 255.0) * strobe_span)
        
        # Full brightness - use open range (100% without strobe)
        if state.dimmer >= 250 and open_cap:
            return open_cap.min_value
        
        # Normal dimmer range
        if dimmer_cap:
            # Map 5-249 to dimmer range (8-134)
            # Ensure we never output below 8 (which is where dimmer starts)
            dimmer_normalized = (state.dimmer - 5) / (249 - 5)  # 0.0 to 1.0
            dimmer_span = dimmer_cap.max_value - dimmer_cap.min_value
            return dimmer_cap.min_value + int(dimmer_normalized * dimmer_span)
        
        return state.dimmer
    
    def blackout(self) -> None:
        """Activate blackout - all fixtures to zero."""
        self._blackout_active = True
        
        for fixture in self.config.fixtures:
            state = self._states[fixture.name]
            state.red = 0
            state.green = 0
            state.blue = 0
            state.white = 0
            state.amber = 0
            state.uv = 0
            state.dimmer = 0
            state.strobe = 0
            
            # Also zero the smoothed values immediately
            smoothed = self._smoothed_values[fixture.name]
            smoothed.red = 0
            smoothed.green = 0
            smoothed.blue = 0
            smoothed.white = 0
            smoothed.amber = 0
            smoothed.uv = 0
            smoothed.dimmer = 0
            smoothed.strobe = 0
        
        self._output_to_dmx()
        self.dmx.blackout()
    
    def unblackout(self) -> None:
        """Deactivate blackout - resume normal operation."""
        self._blackout_active = False
    
    def is_blackout(self) -> bool:
        """Check if blackout is active."""
        return self._blackout_active
    
    def toggle_blackout(self) -> bool:
        """Toggle blackout state. Returns new state."""
        if self._blackout_active:
            self.unblackout()
        else:
            self.blackout()
        return self._blackout_active
    
    def get_fixture_states(self) -> dict[str, FixtureState]:
        """Get current fixture states."""
        return self._smoothed_values.copy()
