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
    
    def process(self, data: AnalysisData) -> dict[str, FixtureState]:
        """Process audio data and update fixture states."""
        # If blackout is active, keep all channels at zero
        if self._blackout_active:
            self._output_to_dmx()
            return self._smoothed_values.copy()
        
        self._time = time.time()
        
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
            self._apply_energy_mode(data, beat_triggered, bar_triggered)
        elif mode == VisualizationMode.FREQUENCY_SPLIT:
            self._apply_frequency_split_mode(data, beat_triggered)
        elif mode == VisualizationMode.BEAT_PULSE:
            self._apply_beat_pulse_mode(data, beat_triggered)
        elif mode == VisualizationMode.COLOR_CYCLE:
            self._apply_color_cycle_mode(data, beat_triggered)
        elif mode == VisualizationMode.RAINBOW_WAVE:
            self._apply_rainbow_wave_mode(data, beat_triggered)
        elif mode == VisualizationMode.STROBE_BEAT:
            self._apply_strobe_beat_mode(data, beat_triggered)
        elif mode == VisualizationMode.RANDOM_FLASH:
            self._apply_random_flash_mode(data, beat_triggered)
        
        # Apply movement if enabled - only on specific beats, not constantly
        if self.config.effects.movement_enabled:
            self._apply_movement(data, beat_triggered, bar_triggered)
        
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
    
    def _apply_energy_mode(self, data: AnalysisData, beat_triggered: bool, bar_triggered: bool) -> None:
        """
        Energy mode: Bass drives intensity, mids drive color, movement on strong beats.
        """
        bass = data.features.bass
        mid = data.features.mid
        high = data.features.high
        energy = data.features.energy
        
        # === COLOR: Based on mids and slow drift ===
        # Base hue drifts slowly over time (full cycle every ~30 seconds)
        time_offset = (self._time - self._start_time) * 0.03 * self.config.effects.color_speed
        self._base_hue = time_offset % 1.0
        
        # Mids influence hue offset (higher mids = warmer colors)
        mid_offset = mid * 0.2  # Up to 0.2 hue shift based on mids
        
        # On bar changes, shift color more dramatically
        if bar_triggered:
            self._target_hue = (self._base_hue + mid_offset + 0.25) % 1.0
        else:
            self._target_hue = (self._base_hue + mid_offset) % 1.0
        
        # Smooth transition to target hue
        hue_diff = self._target_hue - self._current_hue
        # Handle wrap-around
        if hue_diff > 0.5:
            hue_diff -= 1.0
        elif hue_diff < -0.5:
            hue_diff += 1.0
        self._current_hue = (self._current_hue + hue_diff * 0.1) % 1.0
        
        # === INTENSITY: Based on bass and beat ===
        # Base intensity from overall energy
        self._base_intensity = 0.3 + energy * 0.5
        
        # Pulse on beats, stronger pulse on bass-heavy beats
        if beat_triggered:
            pulse_strength = 0.3 + bass * 0.4  # Stronger pulse with more bass
            self._pulse_intensity = pulse_strength
        else:
            # Decay pulse based on beat position (smooth decay within beat)
            decay = 1.0 - self._ease_out_cubic(data.beat_position)
            self._pulse_intensity *= decay
        
        # Total brightness
        brightness = min(1.0, self._base_intensity + self._pulse_intensity)
        
        # === SATURATION: Higher energy = more saturated ===
        saturation = 0.6 + energy * 0.4
        
        # === Apply to fixtures ===
        for fixture in self.config.fixtures:
            state = self._states[fixture.name]
            intensity = brightness * fixture.intensity_scale * self.config.effects.intensity
            
            state.set_from_hsv(self._current_hue, saturation, intensity)
            state.dimmer = int(255 * intensity)
            
            # High frequencies trigger subtle strobe on drops
            if self._strobe_active and self.config.effects.strobe_on_drop:
                state.strobe = 180
            else:
                state.strobe = 0
    
    def _apply_frequency_split_mode(self, data: AnalysisData, beat_triggered: bool) -> None:
        """
        Frequency split: Different fixtures respond to different frequency bands.
        """
        num_fixtures = len(self.config.fixtures)
        if num_fixtures == 0:
            return
        
        sorted_fixtures = sorted(self.config.fixtures, key=lambda f: f.position)
        third = max(1, num_fixtures // 3)
        
        bass = data.features.bass
        mid = data.features.mid
        high = data.features.high
        
        for i, fixture in enumerate(sorted_fixtures):
            state = self._states[fixture.name]
            scale = fixture.intensity_scale * self.config.effects.intensity
            
            if i < third:
                # Bass fixtures: Red/orange, pulse with bass
                intensity = (0.3 + bass * 0.7) * scale
                hue = 0.0 + bass * 0.1  # Red to orange
            elif i < third * 2:
                # Mid fixtures: Green/cyan, respond to mids
                intensity = (0.3 + mid * 0.7) * scale
                hue = 0.25 + mid * 0.15  # Yellow-green to cyan
            else:
                # High fixtures: Blue/purple, respond to highs
                intensity = (0.3 + high * 0.7) * scale
                hue = 0.6 + high * 0.15  # Blue to purple
            
            # Add pulse on beats
            if beat_triggered:
                intensity = min(1.0, intensity + 0.2)
            
            state.set_from_hsv(hue, 0.9, intensity)
            state.dimmer = int(255 * intensity)
    
    def _apply_beat_pulse_mode(self, data: AnalysisData, beat_triggered: bool) -> None:
        """
        Beat pulse: Flash on beats with color shifts on bars.
        """
        # Shift color palette every 4 beats (on bar)
        bar_num = data.estimated_bar
        palette_hues = [0.0, 0.15, 0.55, 0.75, 0.9]  # Red, orange, cyan, purple, magenta
        base_hue = palette_hues[bar_num % len(palette_hues)]
        
        for fixture in self.config.fixtures:
            state = self._states[fixture.name]
            scale = fixture.intensity_scale * self.config.effects.intensity
            
            if beat_triggered:
                # Flash to full on beat
                brightness = scale
                # Slight hue variation per fixture
                hue = (base_hue + fixture.position * 0.05) % 1.0
            else:
                # Smooth decay based on beat position
                decay = 1.0 - self._ease_out_cubic(data.beat_position)
                brightness = scale * decay * 0.8  # Don't go fully dark
                hue = base_hue
            
            state.set_from_hsv(hue, 0.85, brightness)
            state.dimmer = int(255 * brightness)
    
    def _apply_color_cycle_mode(self, data: AnalysisData, beat_triggered: bool) -> None:
        """
        Color cycle: Smooth rainbow progression synced to tempo.
        """
        # Color cycles through spectrum, one full cycle per 8 bars
        beats_per_cycle = 32  # 8 bars * 4 beats
        cycle_position = (data.estimated_beat % beats_per_cycle) / beats_per_cycle
        cycle_position += data.beat_position / beats_per_cycle
        
        base_hue = cycle_position
        
        # Brightness based on energy with beat pulse
        energy = data.features.energy
        base_brightness = 0.4 + energy * 0.4
        
        if beat_triggered:
            pulse = 0.2
        else:
            pulse = 0.2 * (1.0 - data.beat_position)
        
        num_fixtures = max(1, len(self.config.fixtures))
        
        for fixture in self.config.fixtures:
            state = self._states[fixture.name]
            # Each fixture offset slightly in the cycle
            position_offset = fixture.position / num_fixtures * 0.3
            hue = (base_hue + position_offset) % 1.0
            
            brightness = (base_brightness + pulse) * fixture.intensity_scale * self.config.effects.intensity
            state.set_from_hsv(hue, 0.9, brightness)
            state.dimmer = int(255 * brightness)
    
    def _apply_rainbow_wave_mode(self, data: AnalysisData, beat_triggered: bool) -> None:
        """
        Rainbow wave: Colors flow across fixtures in a wave pattern.
        """
        # Wave position advances with time and tempo
        wave_speed = 0.5 * self.config.effects.color_speed
        wave_position = ((self._time - self._start_time) * wave_speed) % 1.0
        
        energy = data.features.energy
        base_brightness = 0.3 + energy * 0.5
        
        num_fixtures = max(1, len(self.config.fixtures))
        sorted_fixtures = sorted(self.config.fixtures, key=lambda f: f.position)
        
        for i, fixture in enumerate(sorted_fixtures):
            state = self._states[fixture.name]
            
            # Each fixture is at a different point in the wave
            fixture_phase = i / num_fixtures
            hue = (wave_position + fixture_phase) % 1.0
            
            # Brightness varies with wave too
            wave_brightness = 0.5 + 0.5 * math.sin((wave_position + fixture_phase) * math.pi * 2)
            brightness = base_brightness * wave_brightness * fixture.intensity_scale * self.config.effects.intensity
            
            # Beat pulse
            if beat_triggered:
                brightness = min(1.0, brightness + 0.15)
            
            state.set_from_hsv(hue, 0.85, max(0.1, brightness))
            state.dimmer = int(255 * brightness)
    
    def _apply_strobe_beat_mode(self, data: AnalysisData, beat_triggered: bool) -> None:
        """
        Strobe beat: Strobe effect on every beat.
        """
        for fixture in self.config.fixtures:
            state = self._states[fixture.name]
            scale = fixture.intensity_scale * self.config.effects.intensity
            
            if beat_triggered:
                state.red = 255
                state.green = 255
                state.blue = 255
                state.dimmer = int(255 * scale)
                state.strobe = 200  # Fast strobe
            else:
                # Quick decay
                decay = max(0, 1.0 - data.beat_position * 3)
                brightness = int(255 * decay * scale)
                state.red = brightness
                state.green = brightness
                state.blue = brightness
                state.dimmer = brightness
                state.strobe = 200 if data.beat_position < 0.3 else 0
    
    def _apply_random_flash_mode(self, data: AnalysisData, beat_triggered: bool) -> None:
        """
        Random flash: Random fixtures flash on beats.
        """
        import random
        
        fixtures = self.config.fixtures
        if beat_triggered and fixtures:
            num_to_flash = max(1, len(fixtures) // 3)
            flashing = random.sample(list(fixtures), min(num_to_flash, len(fixtures)))
            flash_names = {f.name for f in flashing}
            # Random color for this beat
            flash_hue = random.random()
        else:
            flash_names = set()
            flash_hue = 0
        
        for fixture in fixtures:
            state = self._states[fixture.name]
            scale = fixture.intensity_scale * self.config.effects.intensity
            
            if fixture.name in flash_names:
                state.set_from_hsv(flash_hue, 1.0, scale)
                state.dimmer = int(255 * scale)
            else:
                # Decay
                decay = max(0, 1.0 - data.beat_position * 2.5)
                state.dimmer = int(state.dimmer * decay)
                state.red = int(state.red * decay)
                state.green = int(state.green * decay)
                state.blue = int(state.blue * decay)
    
    def _apply_movement(self, data: AnalysisData, beat_triggered: bool, bar_triggered: bool) -> None:
        """
        Apply movement to fixtures - but only on musical events, not constantly.
        
        Movement principles:
        - Pan moves on bar changes (slower, sweeping motion)
        - Tilt moves on strong beats (bass-driven)
        - Movement amount based on energy level
        - P/T speed is fast so fixture can reach position before next move
        """
        speed = self.config.effects.movement_speed
        bass = data.features.bass
        energy = data.features.energy
        
        for fixture in self.config.fixtures:
            profile = self._get_profile(fixture)
            channels = fixture.get_channels(profile)
            
            has_pan = any(ch.channel_type == ChannelType.POSITION_PAN for ch in channels)
            has_tilt = any(ch.channel_type == ChannelType.POSITION_TILT for ch in channels)
            
            if not (has_pan or has_tilt):
                continue
            
            state = self._states[fixture.name]
            
            # === PAN: Move on bar changes ===
            if bar_triggered and has_pan:
                pan_range = fixture.pan_max - fixture.pan_min
                pan_center = (fixture.pan_max + fixture.pan_min) / 2
                
                # Choose a new pan position based on bar number
                # Creates a pattern that feels intentional
                bar_num = data.estimated_bar
                pan_positions = [0.0, 0.7, 0.3, -0.5, 0.5, -0.7, -0.3, 0.0]
                pan_factor = pan_positions[bar_num % len(pan_positions)]
                
                # Scale by movement speed and energy
                pan_offset = pan_factor * (pan_range / 2) * speed * (0.5 + energy * 0.5)
                self._target_pan[fixture.name] = int(pan_center + pan_offset)
            
            # === TILT: Move more frequently based on energy and beats ===
            # Move on beats when there's decent energy, or every few beats regardless
            should_tilt = beat_triggered and (
                energy > 0.25 or  # Any decent energy
                self._beats_since_move >= 4  # Or at least every 4 beats
            )
            
            if should_tilt and has_tilt:
                tilt_range = fixture.tilt_max - fixture.tilt_min
                tilt_center = (fixture.tilt_max + fixture.tilt_min) / 2
                
                # Alternate between up and down positions with more variety
                beat_num = data.estimated_beat
                # More positions for variety, ranging from looking up to looking down
                tilt_positions = [0.6, -0.4, 0.3, -0.6, 0.5, -0.2, 0.4, -0.5]
                tilt_factor = tilt_positions[beat_num % len(tilt_positions)]
                
                # Scale by energy and bass (bass makes it more dramatic)
                intensity_scale = 0.5 + bass * 0.5  # 0.5 to 1.0 based on bass
                tilt_offset = tilt_factor * (tilt_range / 2) * speed * intensity_scale
                self._target_tilt[fixture.name] = int(tilt_center + tilt_offset)
                self._beats_since_move = 0
            
            # === Smoothly move toward targets ===
            # Pan moves slower (it's usually a bigger movement)
            current_pan = state.pan
            target_pan = self._target_pan.get(fixture.name, 128)
            pan_diff = target_pan - current_pan
            state.pan = int(current_pan + pan_diff * 0.15)  # Smooth interpolation
            
            # Tilt can move faster
            current_tilt = state.tilt
            target_tilt = self._target_tilt.get(fixture.name, 128)
            tilt_diff = target_tilt - current_tilt
            state.tilt = int(current_tilt + tilt_diff * 0.2)
            
            # P/T speed: fast so fixture can keep up
            # 0 = fastest for SPEED_PAN_TILT_FAST_SLOW
            state.pt_speed = int(20 * (1.0 - speed))  # 0-20 range, very fast
    
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
            
            # Position values smooth separately (handled in _apply_movement)
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
