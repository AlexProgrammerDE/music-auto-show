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
    FixtureConfig, FixtureProfile, VisualizationMode, FixtureType,
    ChannelType, ChannelConfig, ShowConfig, FIXTURE_PRESETS, StrobeEffectMode,
    RotationMode, ColorMixingType, EffectFixtureMode
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
    
    # Light array effect patterns (distinct from color strobe)
    effect_pattern: int = 0  # LED array pattern (e.g., Techno Derby Ch4)
    
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
        
        # Effect fixture rotation state (for smooth rotation on Channel 3)
        self._rotation_phase: float = 0.0  # Current rotation phase (0.0 - 1.0)
        self._rotation_target: int = 64  # Target position for manual modes (1-127)
        self._smoothed_rotation: float = 64.0  # Smoothed rotation value
        
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
    
    def _get_fixture_type(self, fixture: FixtureConfig) -> FixtureType:
        """Get the fixture type for a fixture."""
        profile = self._get_profile(fixture)
        if profile:
            return profile.fixture_type
        return FixtureType.OTHER
    
    def get_fixtures_by_type(self, fixture_type: FixtureType) -> list[FixtureConfig]:
        """Get all fixtures of a specific type."""
        return [f for f in self.config.fixtures if self._get_fixture_type(f) == fixture_type]
    
    def get_rgb_fixtures(self) -> list[FixtureConfig]:
        """Get fixtures that support RGB color mixing (Moving Heads, PARs)."""
        return [f for f in self.config.fixtures 
                if self._get_fixture_type(f) in (FixtureType.MOVING_HEAD, FixtureType.PAR)]
    
    def get_effect_fixtures(self) -> list[FixtureConfig]:
        """Get effect-type fixtures (Derby, Moonflower, etc.)."""
        return [f for f in self.config.fixtures 
                if self._get_fixture_type(f) == FixtureType.EFFECT]
    
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
        
        # If no music is playing (no energy or no tempo), fade to black and stop all effects
        # This prevents movement and color cycling when there's silence
        if data.features.energy < 0.01 or data.features.tempo <= 0:
            self._fade_to_idle()
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
        
        # Process effect-type fixtures (Derby, Moonflower with color macros)
        self._process_effect_lights(data, beat_triggered, bar_triggered)
        
        # Process effect channels on ALL fixtures (motor position, etc.)
        self._process_effect_channels(data, beat_triggered, bar_triggered)
        
        # Apply movement if enabled - only on specific beats, not constantly
        if self.config.effects.movement_enabled:
            apply_movement(self, data, beat_triggered, bar_triggered)
        
        # Apply smoothing and output
        self._apply_smoothing()
        self._output_to_dmx()
        
        return self._smoothed_values.copy()
    
    def _process_effect_lights(self, data: AnalysisData, beat_triggered: bool, bar_triggered: bool) -> None:
        """
        Process effect lights (Derby, Moonflower, etc.) separately from RGB fixtures.
        
        Showtec Techno Derby channels:
        - Channel 1 (color_macro): Color selection (0-215 colors, 216-255 auto change)
        - Channel 2 (strobe): Strobe speed (0-5 off, 6-255 slow to fast)
        - Channel 3 (effect): Pattern rotation (0 off, 1-127 manual position, 128-255 auto speed)
        - Channel 4 (effect_speed): Strobe effect patterns (effects 1-18)
        """
        effect_fixtures = self.get_effect_fixtures()
        if not effect_fixtures:
            return
        
        # Update rotation state (shared across all effect fixtures)
        self._update_rotation_state(data, beat_triggered, bar_triggered)
        
        for i, fixture in enumerate(effect_fixtures):
            state = self._states[fixture.name]
            profile = self._get_profile(fixture)
            
            # Get current RGB from the visualization mode (already calculated)
            r, g, b = state.red, state.green, state.blue
            
            # Handle color output based on fixture's color mixing type
            if profile and profile.color_mixing == ColorMixingType.DUAL_COLOR_CHANNELS:
                # Dual-color fixture: map target color to dual-color channels
                self._apply_dual_color_mapping(state, profile, self._current_hue)
            else:
                # Standard color macro fixture (Derby, etc.)
                state.color_macro = self._rgb_to_color_macro(r, g, b, data.features.energy)
            
            # Channel 2: Strobe speed
            state.strobe = self._get_strobe_value(data, beat_triggered)
            
            # Channel 3: Pattern rotation (respects channel's max_value)
            effect_channel = self._get_channel_config(fixture, profile, ChannelType.EFFECT)
            state.effect = self._get_rotation_value_for_channel(effect_channel)
            
            # Channel 4: LED array effect patterns (light movement patterns)
            state.effect_pattern = self._get_strobe_effect_value(data, beat_triggered, bar_triggered)
    
    def _process_effect_channels(self, data: AnalysisData, beat_triggered: bool, bar_triggered: bool) -> None:
        """
        Process EFFECT type channels on all fixtures (not just EFFECT type fixtures).
        
        This handles motor position, rotation, and other effect channels that may exist
        on PAR lights, moving heads, or other fixture types.
        """
        # Update rotation state once (shared across all fixtures)
        # Only update if not already updated by _process_effect_lights
        effect_fixtures = self.get_effect_fixtures()
        if not effect_fixtures:
            # No EFFECT type fixtures, so we need to update rotation state here
            self._update_rotation_state(data, beat_triggered, bar_triggered)
        
        for fixture in self.config.fixtures:
            # Skip EFFECT type fixtures - they're handled by _process_effect_lights
            if self._get_fixture_type(fixture) == FixtureType.EFFECT:
                continue
            
            state = self._states[fixture.name]
            profile = self._get_profile(fixture)
            
            # Check if this fixture has an EFFECT channel
            effect_channel = self._get_channel_config(fixture, profile, ChannelType.EFFECT)
            if effect_channel:
                state.effect = self._get_rotation_value_for_channel(effect_channel)
    
    def _get_strobe_value(self, data: AnalysisData, beat_triggered: bool) -> int:
        """
        Get Channel 2 (Strobe) value.
        0-5: Strobe off (but light stays on), 6-255: Strobe speed slow to fast.
        
        IMPORTANT: We return 0 for "no strobe" - the fixture color stays on,
        only the strobe effect is disabled. The color is controlled by Channel 1.
        
        Respects effect_fixture_mode:
        - STROBE_FOCUS / STROBE_ONLY: More aggressive strobe
        - MOVEMENT_FOCUS / MOVEMENT_ONLY: Minimal or no strobe
        - BALANCED: Normal behavior
        """
        mode = self.config.effects.effect_fixture_mode
        
        # Movement-only mode: no strobe at all (light stays on via color channel)
        if mode == EffectFixtureMode.MOVEMENT_ONLY:
            return 0
        
        # Movement-focus mode: only strobe on drops
        if mode == EffectFixtureMode.MOVEMENT_FOCUS:
            if self._strobe_active:
                return 150  # Reduced strobe on drops
            return 0
        
        # Calculate base strobe value for BALANCED, STROBE_FOCUS, STROBE_ONLY
        strobe_value = 0
        
        if self._strobe_active:
            # Drop strobe - fast
            strobe_value = 200
        
        # Apply mode-specific strobe behavior
        if mode == EffectFixtureMode.STROBE_ONLY:
            # Maximum strobe - always some strobe when there's any energy
            if strobe_value == 0:
                if beat_triggered and data.features.bass > 0.5:
                    strobe_value = int(80 + data.features.bass * 120)
                elif data.features.energy > 0.2:
                    strobe_value = int(6 + data.features.energy * 100)
        elif mode == EffectFixtureMode.STROBE_FOCUS:
            # More strobe - triggers on beats with bass
            if strobe_value == 0:
                if beat_triggered and data.features.bass > 0.6:
                    strobe_value = int(60 + data.features.bass * 100)
                elif data.features.energy > 0.5:
                    strobe_value = int(6 + (data.features.energy - 0.5) * 60)
        else:
            # BALANCED mode - only strobe on very strong beats, otherwise off
            if strobe_value == 0 and beat_triggered and data.features.bass > 0.8:
                strobe_value = int(80 + data.features.bass * 80)  # Quick burst on heavy bass
        
        return strobe_value
    
    def _update_rotation_state(self, data: AnalysisData, beat_triggered: bool, bar_triggered: bool) -> None:
        """
        Update the rotation state using normalized phase values (0-1).
        
        The rotation phase is abstract and gets scaled to each fixture's
        actual channel constraints when output. This allows different fixtures
        to have different ranges while sharing the same animation logic.
        
        Movement speed is scaled by tempo (slower songs = slower movement).
        
        - _rotation_phase: 0-1 cyclic value for position/animation
        - _rotation_intensity: 0-1 value for speed/intensity (used in auto modes)
        """
        # Stop rotation when no music is playing (no energy or no tempo)
        if data.features.energy < 0.01 or data.features.tempo <= 0:
            self._rotation_phase = 0.0
            self._smoothed_rotation = 0.0
            return
        
        mode = self.config.effects.rotation_mode
        effect_mode = self.config.effects.effect_fixture_mode
        dt = 1.0 / max(1, self.config.dmx.fps)  # Time delta per frame
        
        # Calculate tempo scale for movement (same as moving heads)
        # 60 BPM = 0.5x speed, 120 BPM = 1.0x, 180 BPM = 1.5x
        tempo = data.features.tempo
        if tempo > 0:
            tempo_scale = max(0.4, min(1.8, tempo / 120.0))
        else:
            tempo_scale = 1.0
        
        # Effect fixture mode affects movement amount
        # STROBE_ONLY: minimal movement, MOVEMENT_ONLY: maximum movement
        if effect_mode == EffectFixtureMode.STROBE_ONLY:
            movement_scale = 0.2  # Very slow movement
        elif effect_mode == EffectFixtureMode.STROBE_FOCUS:
            movement_scale = 0.5  # Reduced movement
        elif effect_mode == EffectFixtureMode.MOVEMENT_FOCUS:
            movement_scale = 1.3  # Enhanced movement
        elif effect_mode == EffectFixtureMode.MOVEMENT_ONLY:
            movement_scale = 1.5  # Maximum movement
        else:  # BALANCED
            movement_scale = 1.0
        
        # Combined scale factor
        scale = tempo_scale * movement_scale
        
        if mode == RotationMode.OFF:
            self._rotation_phase = 0.0
            self._smoothed_rotation = 0
            return
        
        if mode == RotationMode.MANUAL_SLOW:
            # Slow continuous sweep - complete cycle every ~8 seconds at 120 BPM
            base_cycle_time = 8.0
            self._rotation_phase = (self._rotation_phase + dt / base_cycle_time * scale) % 1.0
            self._smoothed_rotation = self._rotation_phase * 127  # Legacy compatibility
        
        elif mode == RotationMode.MANUAL_BEAT:
            # Jump to new position on beats (8 discrete positions)
            if beat_triggered:
                target_phase = (data.estimated_beat % 8) / 8.0
                # Smooth transition to target - speed affected by tempo
                diff = target_phase - self._rotation_phase
                # Handle wrap-around
                if diff > 0.5:
                    diff -= 1.0
                elif diff < -0.5:
                    diff += 1.0
                self._rotation_phase += diff * 0.15 * scale
                self._rotation_phase = self._rotation_phase % 1.0
            self._smoothed_rotation = self._rotation_phase * 127  # Legacy compatibility
        
        elif mode == RotationMode.AUTO_SLOW:
            # Slow continuous phase for auto speed
            self._rotation_phase = (self._rotation_phase + dt / 10.0 * scale) % 1.0
            self._smoothed_rotation = 140  # Legacy compatibility
        
        elif mode == RotationMode.AUTO_MEDIUM:
            # Medium continuous phase for auto speed
            self._rotation_phase = (self._rotation_phase + dt / 6.0 * scale) % 1.0
            self._smoothed_rotation = 180  # Legacy compatibility
        
        elif mode == RotationMode.AUTO_FAST:
            # Fast continuous phase for auto speed
            self._rotation_phase = (self._rotation_phase + dt / 3.0 * scale) % 1.0
            self._smoothed_rotation = 230  # Legacy compatibility
        
        elif mode == RotationMode.AUTO_MUSIC:
            # Phase speed follows music energy (also tempo-scaled)
            base_speed = 0.05 * scale  # Base phase increment
            energy_boost = data.features.energy * 0.15
            tempo_factor = min(1.0, data.features.tempo / 150.0) * 0.05
            phase_speed = base_speed + energy_boost + tempo_factor
            self._rotation_phase = (self._rotation_phase + dt * phase_speed * 10) % 1.0
            # Legacy smoothed rotation for backward compatibility
            target = 140 + data.features.energy * 80 + tempo_factor * 20
            self._smoothed_rotation += (target - self._smoothed_rotation) * 0.02
    
    def _get_channel_config(self, fixture: FixtureConfig, profile: Optional[FixtureProfile], 
                            channel_type: ChannelType) -> Optional[ChannelConfig]:
        """Get the channel config for a specific channel type."""
        channels = fixture.get_channels(profile)
        for ch in channels:
            if ch.channel_type == channel_type:
                return ch
        return None
    
    def _get_rotation_value(self) -> int:
        """Get the smoothed rotation value for Channel 3 (legacy, uses full 0-255 range)."""
        return int(max(0, min(255, self._smoothed_rotation)))
    
    def _get_rotation_value_for_channel(self, channel: Optional[ChannelConfig]) -> int:
        """
        Get the rotation value scaled to the channel's constraints.
        
        Uses the channel's capability definitions to determine valid ranges:
        - If in AUTO mode and channel has usable auto range, use that
        - If in MANUAL mode or auto not available, use manual range
        - Falls back to usable range if no specific ranges defined
        """
        if channel is None:
            return int(max(0, min(255, self._smoothed_rotation)))
        
        mode = self.config.effects.rotation_mode
        
        # Check if we're requesting auto rotation
        is_auto_mode = mode in (RotationMode.AUTO_SLOW, RotationMode.AUTO_MEDIUM, 
                                RotationMode.AUTO_FAST, RotationMode.AUTO_MUSIC)
        
        auto_range = channel.get_auto_range()
        manual_range = channel.get_manual_range()
        
        if is_auto_mode and auto_range is not None:
            # Use auto range - scale the rotation phase to auto speed
            # For auto modes, use energy/tempo to control speed within range
            return channel.scale_to_auto_range(self._rotation_phase)
        elif manual_range is not None:
            # Use manual range - use sine wave for smooth position sweep
            position = 0.5 + 0.5 * math.sin(self._rotation_phase * 2 * math.pi)
            return channel.scale_to_manual_range(position)
        else:
            # Fallback to usable range with sine sweep
            position = 0.5 + 0.5 * math.sin(self._rotation_phase * 2 * math.pi)
            return channel.scale_to_usable_range(position)
    
    def _get_strobe_effect_value(self, data: AnalysisData, beat_triggered: bool, bar_triggered: bool) -> int:
        """
        Get the DMX value for Channel 4 (Strobe Effects) based on settings.
        
        Techno Derby Channel 4 ranges:
        0-9: No function
        10-19: Effect 1 (slow to fast)
        20-29: Effect 2 (slow to fast)
        ... (each effect is a 10-value range)
        170-179: Effect 17 (slow to fast)
        180-255: Effect 18 (strobe always on)
        
        Returns a DMX value 0-255 for the strobe effect channel.
        """
        effects_config = self.config.effects
        
        # If strobe effects are disabled, return 0 (no function)
        if not effects_config.strobe_effect_enabled:
            return 0
        
        mode = effects_config.strobe_effect_mode
        speed = effects_config.strobe_effect_speed  # 0.0 to 1.0
        
        # Handle specific effect modes
        if mode == StrobeEffectMode.OFF:
            return 0
        
        if mode == StrobeEffectMode.EFFECT_18_STROBE:
            # Effect 18 is always on strobe (180-255)
            return 180 + int(speed * 75)  # 180-255 range
        
        if mode == StrobeEffectMode.AUTO:
            # Auto mode: cycle through effects based on music
            return self._get_auto_strobe_effect(data, beat_triggered, bar_triggered, speed)
        
        # Specific effect mode (EFFECT_1 through EFFECT_17)
        effect_map = {
            StrobeEffectMode.EFFECT_1: 1,
            StrobeEffectMode.EFFECT_2: 2,
            StrobeEffectMode.EFFECT_3: 3,
            StrobeEffectMode.EFFECT_4: 4,
            StrobeEffectMode.EFFECT_5: 5,
            StrobeEffectMode.EFFECT_6: 6,
            StrobeEffectMode.EFFECT_7: 7,
            StrobeEffectMode.EFFECT_8: 8,
            StrobeEffectMode.EFFECT_9: 9,
            StrobeEffectMode.EFFECT_10: 10,
            StrobeEffectMode.EFFECT_11: 11,
            StrobeEffectMode.EFFECT_12: 12,
            StrobeEffectMode.EFFECT_13: 13,
            StrobeEffectMode.EFFECT_14: 14,
            StrobeEffectMode.EFFECT_15: 15,
            StrobeEffectMode.EFFECT_16: 16,
            StrobeEffectMode.EFFECT_17: 17,
        }
        
        effect_num = effect_map.get(mode, 1)
        # Calculate DMX value: base + speed within the 10-value range
        # Effect 1 = 10-19, Effect 2 = 20-29, etc.
        base_value = 10 + (effect_num - 1) * 10
        speed_offset = int(speed * 9)  # 0-9 within the range
        return base_value + speed_offset
    
    def _get_auto_strobe_effect(self, data: AnalysisData, beat_triggered: bool, 
                                 bar_triggered: bool, base_speed: float) -> int:
        """
        Automatically select strobe effect based on music analysis.
        
        - Low energy: subtle effects (1-6)
        - Medium energy: moderate effects (7-12)
        - High energy: intense effects (13-17)
        - Drops: strobe always on (effect 18)
        """
        energy = data.features.energy
        bass = data.features.bass
        
        # On drops, use strobe always on
        if self._is_drop:
            return 180 + int(base_speed * 75)  # Effect 18 range
        
        # Determine effect range based on energy
        if energy < 0.4:
            # Low energy: effects 1-6 (subtle patterns)
            effect_range = (1, 6)
        elif energy < 0.7:
            # Medium energy: effects 7-12
            effect_range = (7, 12)
        else:
            # High energy: effects 13-17 (intense patterns)
            effect_range = (13, 17)
        
        # Select specific effect within range based on bar number
        effect_span = effect_range[1] - effect_range[0] + 1
        effect_num = effect_range[0] + (data.estimated_bar % effect_span)
        
        # Speed within effect based on bass and configured speed
        # Stronger bass = faster movement within the effect
        dynamic_speed = base_speed * 0.5 + bass * 0.5
        
        # Calculate DMX value
        base_value = 10 + (effect_num - 1) * 10
        speed_offset = int(dynamic_speed * 9)
        return base_value + speed_offset
    
    def _apply_dual_color_mapping(self, state: FixtureState, profile: FixtureProfile, target_hue: float) -> None:
        """
        Map a target hue to dual-color channel fixtures.
        
        For fixtures where each color channel controls two different colored LEDs,
        we calculate how much each channel should contribute based on how close
        the target hue is to the colors that channel can produce.
        
        Args:
            state: Fixture state to update (red, green, blue values)
            profile: Fixture profile with dual_color_map
            target_hue: Target hue (0-1, where 0=red, 0.33=green, 0.67=blue)
        """
        if not profile.dual_color_map or len(profile.dual_color_map) < 3:
            return
        
        # Get the brightness from current state
        max_brightness = max(state.red, state.green, state.blue, 1)
        brightness = max_brightness / 255.0
        
        # Calculate contribution for each dual-color channel
        channel_values = []
        
        for primary_hue, secondary_hue in profile.dual_color_map:
            # Calculate how well this channel matches the target hue
            contribution = 0.0
            
            # Check primary color match
            if primary_hue is not None:
                primary_dist = self._hue_distance(target_hue, primary_hue)
                # Use a steeper falloff - only contribute if hue is close
                # Distance of 0.167 (60 degrees) = 0 contribution
                primary_contrib = max(0, 1.0 - primary_dist * 6.0)
                contribution = max(contribution, primary_contrib)
            
            # Check secondary color match
            if secondary_hue is not None:
                secondary_dist = self._hue_distance(target_hue, secondary_hue)
                secondary_contrib = max(0, 1.0 - secondary_dist * 6.0)
                contribution = max(contribution, secondary_contrib)
            
            # White (None) only contributes when we're close to the primary color
            # Don't add white contribution independently
            
            channel_values.append(contribution)
        
        # Don't normalize - let channels that don't match stay dark
        # This ensures red/orange only lights up the Red/Yellow channel
        
        # Apply brightness and convert to 0-255
        state.red = int(channel_values[0] * brightness * 255) if len(channel_values) > 0 else 0
        state.green = int(channel_values[1] * brightness * 255) if len(channel_values) > 1 else 0
        state.blue = int(channel_values[2] * brightness * 255) if len(channel_values) > 2 else 0
    
    def _hue_distance(self, hue1: float, hue2: float) -> float:
        """Calculate the shortest distance between two hues (0-1 scale, wraps around)."""
        diff = abs(hue1 - hue2)
        return min(diff, 1.0 - diff)
    
    def _rgb_to_color_macro(self, r: int, g: int, b: int, energy: float) -> int:
        """
        Map RGB values to Techno Derby color macro values.
        
        IMPORTANT: Never returns 0-5 (no function/off). Effect fixtures should
        always show a color - use strobe channel for brightness control instead.
        
        Returns a value in the color macro range based on which colors are active.
        The Techno Derby has these color options on Channel 1:
        0-5: No function (NEVER USE - keep color on)
        6-20: Red
        21-35: Green
        36-50: Blue
        51-65: White
        66-80: Red + Green
        81-95: Red + Blue
        96-110: Red + White
        111-125: Green + Blue
        126-140: Green + White
        141-155: Blue + White
        156-170: Red + Green + Blue
        171-185: Red + Green + White
        186-200: Green + Blue + White
        201-215: Red + Green + Blue + White
        216-229: Slow color change
        230-255: Fast color change
        """
        # Use lower thresholds since we want to detect any color intent
        threshold = 50  # Lower threshold to catch dim colors
        
        red_on = r > threshold
        green_on = g > threshold
        blue_on = b > threshold
        
        # Check for white-like appearance (all channels bright and balanced)
        min_val = min(r, g, b)
        max_val = max(r, g, b)
        is_balanced = (max_val - min_val) < 60  # Colors are similar
        is_bright = min_val > 150
        white_like = is_balanced and is_bright
        
        # High energy = use auto color change for variety
        if energy > 0.85:
            return 230 + int((energy - 0.85) * 166)  # 230-255 fast color change
        
        # Count active colors
        color_count = sum([red_on, green_on, blue_on])
        
        # Map to color macros (use middle of each range for stability)
        if white_like:
            # Pure white appearance - use white or RGBW
            if color_count == 3:
                return 208  # RGBW middle of 201-215
            return 58   # White middle of 51-65
        elif color_count == 3:
            # All three colors on but not balanced = RGB
            return 163  # RGB middle of 156-170
        elif red_on and green_on:
            # Red + Green (yellow-ish)
            if (r + g) > 350:
                return 178  # R+G+W middle of 171-185
            return 73   # R+G middle of 66-80
        elif red_on and blue_on:
            # Red + Blue (magenta)
            return 88   # R+B middle of 81-95
        elif green_on and blue_on:
            # Green + Blue (cyan)
            if (g + b) > 350:
                return 193  # G+B+W middle of 186-200
            return 118  # G+B middle of 111-125
        elif blue_on:
            # Solo blue
            if b > 200:
                return 148  # B+W middle of 141-155
            return 43   # Blue middle of 36-50
        elif green_on:
            # Solo green
            if g > 200:
                return 133  # G+W middle of 126-140
            return 28   # Green middle of 21-35
        elif red_on:
            # Solo red
            if r > 200:
                return 103  # R+W middle of 96-110
            return 13   # Red middle of 6-20
        else:
            # No strong color detected - use slow color change to keep fixture visible
            # This ensures the effect fixture never goes dark (0-5 range)
            # The slow color change provides ambient light even at low energy
            return 222  # Slow color change - always visible
    
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
            smoothed.effect_pattern = current.effect_pattern
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
                    # Clamp to channel's allowed range (respects min_value/max_value)
                    value = max(ch.min_value, min(ch.max_value, value))
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
        elif ct == ChannelType.EFFECT_PATTERN:
            return state.effect_pattern
        elif ct == ChannelType.EFFECT_PATTERN_SPEED:
            return state.effect_pattern  # Same value, speed is encoded in pattern
        
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
    
    def _fade_to_idle(self) -> None:
        """
        Fade fixtures to idle state when no music is playing.
        
        Unlike blackout, this uses smoothing to gradually fade colors to black
        while keeping fixtures responsive. Movement and effects stop immediately
        but colors fade smoothly.
        """
        for fixture in self.config.fixtures:
            state = self._states[fixture.name]
            # Set target colors to zero - smoothing will fade them
            state.red = 0
            state.green = 0
            state.blue = 0
            state.white = 0
            state.amber = 0
            state.uv = 0
            state.dimmer = 0
            state.strobe = 0
            # Stop effect rotation
            state.effect = 0
            state.effect_pattern = 0
            state.color_macro = 0
        
        # Reset rotation state
        self._rotation_phase = 0.0
        self._smoothed_rotation = 0.0
        
        # Apply smoothing for gradual fade
        self._apply_smoothing()
    
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
