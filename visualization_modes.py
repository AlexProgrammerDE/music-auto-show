"""
Visualization mode implementations for the effects engine.

Each mode defines how audio analysis data is mapped to fixture colors and intensities.
"""
import math
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from effects_engine import EffectsEngine
    from audio_analyzer import AnalysisData


def apply_energy_mode(engine: "EffectsEngine", data: "AnalysisData", 
                      beat_triggered: bool, bar_triggered: bool) -> None:
    """
    Energy mode: Bass drives intensity, mids drive color, movement on strong beats.
    When album colors are available, uses those instead of time-based color drift.
    """
    bass = data.features.bass
    mid = data.features.mid
    energy = data.features.energy
    
    # === COLOR: Use album colors if available, otherwise drift ===
    if engine._album_hues:
        # Cycle through album hues on bar changes
        if bar_triggered:
            engine._color_index = (engine._color_index + 1) % len(engine._album_hues)
        
        # Use album hue as base, with slight mid-frequency influence
        engine._base_hue = engine._album_hues[engine._color_index]
        mid_offset = mid * 0.08  # Subtle variation based on mids
        engine._target_hue = (engine._base_hue + mid_offset) % 1.0
    else:
        # Fallback: Base hue drifts slowly over time (full cycle every ~30 seconds)
        time_offset = (engine._time - engine._start_time) * 0.03 * engine.config.effects.color_speed
        engine._base_hue = time_offset % 1.0
        
        # Mids influence hue offset (higher mids = warmer colors)
        mid_offset = mid * 0.2  # Up to 0.2 hue shift based on mids
        
        # On bar changes, shift color more dramatically
        if bar_triggered:
            engine._target_hue = (engine._base_hue + mid_offset + 0.25) % 1.0
        else:
            engine._target_hue = (engine._base_hue + mid_offset) % 1.0
    
    # Smooth transition to target hue
    hue_diff = engine._target_hue - engine._current_hue
    # Handle wrap-around
    if hue_diff > 0.5:
        hue_diff -= 1.0
    elif hue_diff < -0.5:
        hue_diff += 1.0
    engine._current_hue = (engine._current_hue + hue_diff * 0.1) % 1.0
    
    # === INTENSITY: Based on bass and beat ===
    # Base intensity from overall energy
    engine._base_intensity = 0.3 + energy * 0.5
    
    # Pulse on beats, stronger pulse on bass-heavy beats
    if beat_triggered:
        pulse_strength = 0.3 + bass * 0.4  # Stronger pulse with more bass
        engine._pulse_intensity = pulse_strength
    else:
        # Decay pulse based on beat position (smooth decay within beat)
        decay = 1.0 - engine._ease_out_cubic(data.beat_position)
        engine._pulse_intensity *= decay
    
    # Total brightness
    brightness = min(1.0, engine._base_intensity + engine._pulse_intensity)
    
    # === SATURATION: Higher energy = more saturated ===
    saturation = 0.6 + energy * 0.4
    
    # === Apply to fixtures ===
    for fixture in engine.config.fixtures:
        state = engine._states[fixture.name]
        intensity = brightness * fixture.intensity_scale * engine.config.effects.intensity
        
        state.set_from_hsv(engine._current_hue, saturation, intensity)
        state.dimmer = int(255 * intensity)
        
        # High frequencies trigger subtle strobe on drops
        if engine._strobe_active and engine.config.effects.strobe_on_drop:
            state.strobe = 180
        else:
            state.strobe = 0


def apply_frequency_split_mode(engine: "EffectsEngine", data: "AnalysisData", 
                               beat_triggered: bool) -> None:
    """
    Frequency split: Different fixtures respond to different frequency bands.
    """
    num_fixtures = len(engine.config.fixtures)
    if num_fixtures == 0:
        return
    
    sorted_fixtures = sorted(engine.config.fixtures, key=lambda f: f.position)
    third = max(1, num_fixtures // 3)
    
    bass = data.features.bass
    mid = data.features.mid
    high = data.features.high
    
    for i, fixture in enumerate(sorted_fixtures):
        state = engine._states[fixture.name]
        scale = fixture.intensity_scale * engine.config.effects.intensity
        
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


def apply_beat_pulse_mode(engine: "EffectsEngine", data: "AnalysisData", 
                          beat_triggered: bool) -> None:
    """
    Beat pulse: Flash on beats with color shifts on bars.
    Uses album colors when available.
    """
    bar_num = data.estimated_bar
    
    # Use album hues if available, otherwise use default palette
    if engine._album_hues:
        base_hue = engine._album_hues[bar_num % len(engine._album_hues)]
    else:
        palette_hues = [0.0, 0.15, 0.55, 0.75, 0.9]  # Red, orange, cyan, purple, magenta
        base_hue = palette_hues[bar_num % len(palette_hues)]
    
    for fixture in engine.config.fixtures:
        state = engine._states[fixture.name]
        scale = fixture.intensity_scale * engine.config.effects.intensity
        
        if beat_triggered:
            # Flash to full on beat
            brightness = scale
            # Slight hue variation per fixture
            hue = (base_hue + fixture.position * 0.05) % 1.0
        else:
            # Smooth decay based on beat position
            decay = 1.0 - engine._ease_out_cubic(data.beat_position)
            brightness = scale * decay * 0.8  # Don't go fully dark
            hue = base_hue
        
        state.set_from_hsv(hue, 0.85, brightness)
        state.dimmer = int(255 * brightness)


def apply_color_cycle_mode(engine: "EffectsEngine", data: "AnalysisData", 
                           beat_triggered: bool) -> None:
    """
    Color cycle: Smooth progression synced to tempo.
    Uses album colors when available, otherwise full rainbow.
    """
    if engine._album_hues:
        # Cycle through album colors, one color per bar
        bar_num = data.estimated_bar
        current_idx = bar_num % len(engine._album_hues)
        next_idx = (current_idx + 1) % len(engine._album_hues)
        
        # Interpolate between current and next album hue based on beat position
        t = data.beat_position
        current_hue = engine._album_hues[current_idx]
        next_hue = engine._album_hues[next_idx]
        
        # Handle hue wrap-around for smooth interpolation
        hue_diff = next_hue - current_hue
        if hue_diff > 0.5:
            hue_diff -= 1.0
        elif hue_diff < -0.5:
            hue_diff += 1.0
        base_hue = (current_hue + hue_diff * t) % 1.0
    else:
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
    
    num_fixtures = max(1, len(engine.config.fixtures))
    
    for fixture in engine.config.fixtures:
        state = engine._states[fixture.name]
        # Each fixture offset slightly in the cycle
        position_offset = fixture.position / num_fixtures * 0.3
        hue = (base_hue + position_offset) % 1.0
        
        brightness = (base_brightness + pulse) * fixture.intensity_scale * engine.config.effects.intensity
        state.set_from_hsv(hue, 0.9, brightness)
        state.dimmer = int(255 * brightness)


def apply_rainbow_wave_mode(engine: "EffectsEngine", data: "AnalysisData", 
                            beat_triggered: bool) -> None:
    """
    Rainbow wave: Colors flow across fixtures in a wave pattern.
    """
    # Wave position advances with time and tempo
    wave_speed = 0.5 * engine.config.effects.color_speed
    wave_position = ((engine._time - engine._start_time) * wave_speed) % 1.0
    
    energy = data.features.energy
    base_brightness = 0.3 + energy * 0.5
    
    num_fixtures = max(1, len(engine.config.fixtures))
    sorted_fixtures = sorted(engine.config.fixtures, key=lambda f: f.position)
    
    for i, fixture in enumerate(sorted_fixtures):
        state = engine._states[fixture.name]
        
        # Each fixture is at a different point in the wave
        fixture_phase = i / num_fixtures
        hue = (wave_position + fixture_phase) % 1.0
        
        # Brightness varies with wave too
        wave_brightness = 0.5 + 0.5 * math.sin((wave_position + fixture_phase) * math.pi * 2)
        brightness = base_brightness * wave_brightness * fixture.intensity_scale * engine.config.effects.intensity
        
        # Beat pulse
        if beat_triggered:
            brightness = min(1.0, brightness + 0.15)
        
        state.set_from_hsv(hue, 0.85, max(0.1, brightness))
        state.dimmer = int(255 * brightness)


def apply_strobe_beat_mode(engine: "EffectsEngine", data: "AnalysisData", 
                           beat_triggered: bool) -> None:
    """
    Strobe beat: Strobe effect on every beat.
    """
    for fixture in engine.config.fixtures:
        state = engine._states[fixture.name]
        scale = fixture.intensity_scale * engine.config.effects.intensity
        
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


def apply_random_flash_mode(engine: "EffectsEngine", data: "AnalysisData", 
                            beat_triggered: bool) -> None:
    """
    Random flash: Random fixtures flash on beats.
    """
    fixtures = engine.config.fixtures
    if beat_triggered and fixtures:
        num_to_flash = max(1, len(fixtures) // 3)
        flashing = random.sample(list(fixtures), min(num_to_flash, len(fixtures)))
        flash_names = {f.name for f in flashing}
        # Use album colors if available, otherwise random
        if engine._album_hues:
            flash_hue = random.choice(engine._album_hues)
        else:
            flash_hue = random.random()
    else:
        flash_names = set()
        flash_hue = 0
    
    for fixture in fixtures:
        state = engine._states[fixture.name]
        scale = fixture.intensity_scale * engine.config.effects.intensity
        
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
