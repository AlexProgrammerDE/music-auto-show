"""
Movement mode implementations for the effects engine.

Each mode defines how pan/tilt fixtures move in response to audio analysis.

Available modes:
- SUBTLE: Minimal movement, small adjustments on bars only
- STANDARD: Moderate movement on beats and bars (original behavior)
- DRAMATIC: Full range, aggressive movement utilizing entire pan/tilt range
- WALL_WASH: Targets walls/corners, good for closed room effects
- SWEEP: Slow continuous sweeping motion, theatrical
- RANDOM: Unpredictable positions for variety

Dynamic show modes (more visual impact):
- CIRCLE: Circular motion with phase offset per fixture - classic show effect
- FIGURE_8: Elegant figure-8/lemniscate pattern - smooth infinity loops
- BALLYHOO: Fast sweeping wave motion across fixtures - high energy
- FAN: Fixtures fan in/out from center point - dramatic reveals
- CHASE: Sequential position chase - beams "chase" across fixtures
- STROBE_POSITION: Fast snappy beat-synced position jumps - aggressive
- CRAZY: Wild full-range movement - showcases entire 0-255 pan/tilt capability
"""
import random
from typing import TYPE_CHECKING

from config import MovementMode

if TYPE_CHECKING:
    from effects_engine import EffectsEngine, FixtureState
    from config import FixtureConfig
    from audio_analyzer import AnalysisData


def apply_movement(engine: "EffectsEngine", data: "AnalysisData", 
                   beat_triggered: bool, bar_triggered: bool) -> None:
    """
    Apply movement to fixtures based on the selected movement mode.
    
    Movement modes:
    - SUBTLE: Minimal movement, small adjustments on bars only
    - STANDARD: Moderate movement on beats and bars (original behavior)
    - DRAMATIC: Full range, aggressive movement utilizing entire pan/tilt range
    - WALL_WASH: Targets walls/corners, good for closed room effects
    - SWEEP: Slow continuous sweeping motion, theatrical
    - RANDOM: Unpredictable positions for variety
    
    Dynamic show modes (faster, more visual impact):
    - CIRCLE: Beams trace circles with phase offset per fixture
    - FIGURE_8: Elegant figure-8 lemniscate pattern
    - BALLYHOO: Fast sweeping wave motion across fixtures
    - FAN: Fixtures fan in/out from center point
    - CHASE: Sequential position chase across fixtures
    - STROBE_POSITION: Fast snappy beat-synced position jumps
    - CRAZY: Wild full-range movement showcasing entire pan/tilt capability
    """
    from config import ChannelType
    
    mode = engine.config.effects.movement_mode
    speed = engine.config.effects.movement_speed
    bass = data.features.bass
    energy = data.features.energy
    
    for fixture in engine.config.fixtures:
        profile = engine._get_profile(fixture)
        channels = fixture.get_channels(profile)
        
        has_pan = any(ch.channel_type == ChannelType.POSITION_PAN for ch in channels)
        has_tilt = any(ch.channel_type == ChannelType.POSITION_TILT for ch in channels)
        
        if not (has_pan or has_tilt):
            continue
        
        state = engine._states[fixture.name]
        
        # Get fixture's pan/tilt ranges
        pan_range = fixture.pan_max - fixture.pan_min
        pan_center = (fixture.pan_max + fixture.pan_min) / 2
        tilt_range = fixture.tilt_max - fixture.tilt_min
        tilt_center = (fixture.tilt_max + fixture.tilt_min) / 2
        
        # Dispatch to the appropriate movement mode handler
        if mode == MovementMode.SUBTLE:
            _apply_subtle_movement(
                engine, fixture, state, data, beat_triggered, bar_triggered,
                has_pan, has_tilt, pan_range, pan_center, tilt_range, tilt_center,
                speed, energy, bass
            )
        elif mode == MovementMode.STANDARD:
            _apply_standard_movement(
                engine, fixture, state, data, beat_triggered, bar_triggered,
                has_pan, has_tilt, pan_range, pan_center, tilt_range, tilt_center,
                speed, energy, bass
            )
        elif mode == MovementMode.DRAMATIC:
            _apply_dramatic_movement(
                engine, fixture, state, data, beat_triggered, bar_triggered,
                has_pan, has_tilt, pan_range, pan_center, tilt_range, tilt_center,
                speed, energy, bass
            )
        elif mode == MovementMode.WALL_WASH:
            _apply_wall_wash_movement(
                engine, fixture, state, data, beat_triggered, bar_triggered,
                has_pan, has_tilt, pan_range, pan_center, tilt_range, tilt_center,
                speed, energy, bass
            )
        elif mode == MovementMode.SWEEP:
            _apply_sweep_movement(
                engine, fixture, state, data, beat_triggered, bar_triggered,
                has_pan, has_tilt, pan_range, pan_center, tilt_range, tilt_center,
                speed, energy, bass
            )
        elif mode == MovementMode.RANDOM:
            _apply_random_movement(
                engine, fixture, state, data, beat_triggered, bar_triggered,
                has_pan, has_tilt, pan_range, pan_center, tilt_range, tilt_center,
                speed, energy, bass
            )
        # === New dynamic show modes ===
        elif mode == MovementMode.CIRCLE:
            _apply_circle_movement(
                engine, fixture, state, data, beat_triggered, bar_triggered,
                has_pan, has_tilt, pan_range, pan_center, tilt_range, tilt_center,
                speed, energy, bass
            )
        elif mode == MovementMode.FIGURE_8:
            _apply_figure_8_movement(
                engine, fixture, state, data, beat_triggered, bar_triggered,
                has_pan, has_tilt, pan_range, pan_center, tilt_range, tilt_center,
                speed, energy, bass
            )
        elif mode == MovementMode.BALLYHOO:
            _apply_ballyhoo_movement(
                engine, fixture, state, data, beat_triggered, bar_triggered,
                has_pan, has_tilt, pan_range, pan_center, tilt_range, tilt_center,
                speed, energy, bass
            )
        elif mode == MovementMode.FAN:
            _apply_fan_movement(
                engine, fixture, state, data, beat_triggered, bar_triggered,
                has_pan, has_tilt, pan_range, pan_center, tilt_range, tilt_center,
                speed, energy, bass
            )
        elif mode == MovementMode.CHASE:
            _apply_chase_movement(
                engine, fixture, state, data, beat_triggered, bar_triggered,
                has_pan, has_tilt, pan_range, pan_center, tilt_range, tilt_center,
                speed, energy, bass
            )
        elif mode == MovementMode.STROBE_POSITION:
            _apply_strobe_position_movement(
                engine, fixture, state, data, beat_triggered, bar_triggered,
                has_pan, has_tilt, pan_range, pan_center, tilt_range, tilt_center,
                speed, energy, bass
            )
        elif mode == MovementMode.CRAZY:
            _apply_crazy_movement(
                engine, fixture, state, data, beat_triggered, bar_triggered,
                has_pan, has_tilt, pan_range, pan_center, tilt_range, tilt_center,
                speed, energy, bass
            )
        
        # Smoothly interpolate toward targets - speed varies by mode
        _interpolate_position(engine, fixture, state, mode, speed)


def _apply_subtle_movement(
    engine: "EffectsEngine", fixture: "FixtureConfig", state: "FixtureState",
    data: "AnalysisData", beat_triggered: bool, bar_triggered: bool,
    has_pan: bool, has_tilt: bool, pan_range: float, pan_center: float,
    tilt_range: float, tilt_center: float, speed: float, energy: float, bass: float
) -> None:
    """Minimal movement - small subtle adjustments, mostly stays centered."""
    # Only move on bar changes, with very small range
    if bar_triggered:
        bar_num = data.estimated_bar
        
        if has_pan:
            # Very subtle pan wobble - max 15% of range
            pan_positions = [0.0, 0.1, -0.05, 0.08, -0.1, 0.05, -0.08, 0.0]
            pan_factor = pan_positions[bar_num % len(pan_positions)]
            pan_offset = pan_factor * (pan_range / 2) * speed * 0.3
            engine._target_pan[fixture.name] = int(pan_center + pan_offset)
        
        if has_tilt:
            # Subtle tilt - mostly pointing forward/down
            tilt_positions = [0.2, 0.3, 0.15, 0.35, 0.25, 0.1, 0.3, 0.2]
            tilt_factor = tilt_positions[bar_num % len(tilt_positions)]
            tilt_offset = tilt_factor * (tilt_range / 2) * speed * 0.5
            engine._target_tilt[fixture.name] = int(tilt_center + tilt_offset)


def _apply_standard_movement(
    engine: "EffectsEngine", fixture: "FixtureConfig", state: "FixtureState",
    data: "AnalysisData", beat_triggered: bool, bar_triggered: bool,
    has_pan: bool, has_tilt: bool, pan_range: float, pan_center: float,
    tilt_range: float, tilt_center: float, speed: float, energy: float, bass: float
) -> None:
    """Standard club mode - moderate movement on beats and bars."""
    # Pan moves on bar changes
    if bar_triggered and has_pan:
        bar_num = data.estimated_bar
        pan_positions = [0.0, 0.5, 0.2, -0.4, 0.4, -0.5, -0.2, 0.0]
        pan_factor = pan_positions[bar_num % len(pan_positions)]
        pan_offset = pan_factor * (pan_range / 2) * speed * (0.5 + energy * 0.5)
        engine._target_pan[fixture.name] = int(pan_center + pan_offset)
    
    # Tilt moves on beats when there's energy
    should_tilt = beat_triggered and (energy > 0.25 or engine._beats_since_move >= 4)
    if should_tilt and has_tilt:
        beat_num = data.estimated_beat
        tilt_positions = [0.4, -0.2, 0.2, -0.4, 0.3, -0.1, 0.25, -0.3]
        tilt_factor = tilt_positions[beat_num % len(tilt_positions)]
        intensity_scale = 0.5 + bass * 0.5
        tilt_offset = tilt_factor * (tilt_range / 2) * speed * intensity_scale
        engine._target_tilt[fixture.name] = int(tilt_center + tilt_offset)
        engine._beats_since_move = 0


def _apply_dramatic_movement(
    engine: "EffectsEngine", fixture: "FixtureConfig", state: "FixtureState",
    data: "AnalysisData", beat_triggered: bool, bar_triggered: bool,
    has_pan: bool, has_tilt: bool, pan_range: float, pan_center: float,
    tilt_range: float, tilt_center: float, speed: float, energy: float, bass: float
) -> None:
    """Full range dramatic movement - uses entire pan/tilt range."""
    # Pan moves more frequently and to extremes
    should_pan = bar_triggered or (beat_triggered and energy > 0.5)
    if should_pan and has_pan:
        bar_num = data.estimated_bar
        beat_num = data.estimated_beat
        # Full range positions
        pan_positions = [0.0, 0.9, -0.7, 0.5, -0.9, 0.7, -0.5, 0.8]
        idx = (bar_num * 4 + beat_num) % len(pan_positions)
        pan_factor = pan_positions[idx]
        pan_offset = pan_factor * (pan_range / 2) * speed
        engine._target_pan[fixture.name] = int(pan_center + pan_offset)
    
    # Tilt moves on every beat with high energy, or every 2 beats otherwise
    should_tilt = beat_triggered and (energy > 0.3 or engine._beats_since_move >= 2)
    if should_tilt and has_tilt:
        beat_num = data.estimated_beat
        # Full range tilt positions - from up to far down
        tilt_positions = [0.8, -0.6, 0.5, -0.8, 0.9, -0.4, 0.6, -0.7]
        tilt_factor = tilt_positions[beat_num % len(tilt_positions)]
        # Extra dramatic on bass hits
        intensity_scale = 0.7 + bass * 0.3
        tilt_offset = tilt_factor * (tilt_range / 2) * speed * intensity_scale
        engine._target_tilt[fixture.name] = int(tilt_center + tilt_offset)
        engine._beats_since_move = 0


def _apply_wall_wash_movement(
    engine: "EffectsEngine", fixture: "FixtureConfig", state: "FixtureState",
    data: "AnalysisData", beat_triggered: bool, bar_triggered: bool,
    has_pan: bool, has_tilt: bool, pan_range: float, pan_center: float,
    tilt_range: float, tilt_center: float, speed: float, energy: float, bass: float
) -> None:
    """
    Wall wash mode - targets walls and corners for a closed room setup.
    Lights sweep across walls creating dramatic wash effects.
    """
    # Define corner/wall positions as (pan_factor, tilt_factor)
    # These represent looking at different walls/corners
    wall_positions = [
        (-0.9, 0.7),   # Left wall, high
        (-0.9, 0.3),   # Left wall, mid
        (-0.5, 0.9),   # Left corner, low
        (0.0, 0.8),    # Front wall, low
        (0.0, 0.4),    # Front wall, mid
        (0.5, 0.9),    # Right corner, low
        (0.9, 0.3),    # Right wall, mid
        (0.9, 0.7),    # Right wall, high
    ]
    
    # Change wall target on bar changes or every 2 bars for variety
    if bar_triggered:
        # Move to next wall position
        current_idx = engine._wall_corner_index.get(fixture.name, 0)
        # Add fixture position offset so fixtures don't all point same direction
        fixture_offset = int(fixture.position * 2) % len(wall_positions)
        new_idx = (current_idx + 1) % len(wall_positions)
        engine._wall_corner_index[fixture.name] = new_idx
        
        # Get position with fixture offset
        actual_idx = (new_idx + fixture_offset) % len(wall_positions)
        pan_factor, tilt_factor = wall_positions[actual_idx]
        
        if has_pan:
            pan_offset = pan_factor * (pan_range / 2) * speed
            engine._target_pan[fixture.name] = int(pan_center + pan_offset)
        
        if has_tilt:
            # Tilt uses full range to hit walls
            tilt_offset = tilt_factor * (tilt_range / 2) * speed
            engine._target_tilt[fixture.name] = int(tilt_center + tilt_offset)
    
    # On strong beats, add small "kick" movement toward current wall
    if beat_triggered and bass > 0.6:
        if has_tilt:
            current_tilt = engine._target_tilt.get(fixture.name, int(tilt_center))
            # Push slightly further toward wall on bass
            kick = int((tilt_range / 2) * 0.1 * bass)
            engine._target_tilt[fixture.name] = min(fixture.tilt_max, current_tilt + kick)


def _apply_sweep_movement(
    engine: "EffectsEngine", fixture: "FixtureConfig", state: "FixtureState",
    data: "AnalysisData", beat_triggered: bool, bar_triggered: bool,
    has_pan: bool, has_tilt: bool, pan_range: float, pan_center: float,
    tilt_range: float, tilt_center: float, speed: float, energy: float, bass: float
) -> None:
    """
    Slow continuous sweeping motion - theatrical, smooth movement.
    The fixtures slowly sweep across the space continuously.
    """
    # Update sweep phase continuously based on time
    dt = 0.025  # Approximate frame time at 40fps
    sweep_rate = 0.03 * speed  # Base sweep rate, modified by speed setting
    
    current_phase = engine._sweep_phase.get(fixture.name, 0.0)
    direction = engine._sweep_direction.get(fixture.name, 1)
    
    # Advance phase
    current_phase += dt * sweep_rate * direction
    
    # Reverse direction at ends
    if current_phase >= 1.0:
        current_phase = 1.0
        direction = -1
    elif current_phase <= 0.0:
        current_phase = 0.0
        direction = 1
    
    engine._sweep_phase[fixture.name] = current_phase
    engine._sweep_direction[fixture.name] = direction
    
    # Use sine wave for smooth motion (ease in/out)
    smooth_phase = engine._ease_in_out_sine(current_phase)
    
    # Add fixture position offset for visual interest (fixtures sweep at different phases)
    fixture_offset = fixture.position * 0.25
    
    if has_pan:
        # Pan sweeps side to side
        pan_factor = (smooth_phase * 2.0 - 1.0)  # -1 to 1
        pan_offset = pan_factor * (pan_range / 2) * 0.85  # Use 85% of range
        engine._target_pan[fixture.name] = int(pan_center + pan_offset)
    
    if has_tilt:
        # Tilt follows a slower wave pattern, looking down at walls
        tilt_phase = (current_phase + fixture_offset) % 1.0
        tilt_smooth = engine._ease_in_out_sine(tilt_phase)
        # Bias toward tilted down (walls) rather than up
        tilt_factor = 0.3 + tilt_smooth * 0.5  # Range from 0.3 to 0.8
        tilt_offset = tilt_factor * (tilt_range / 2)
        engine._target_tilt[fixture.name] = int(tilt_center + tilt_offset)
    
    # Energy can modulate the sweep rate slightly
    if energy > 0.6:
        engine._sweep_phase[fixture.name] = current_phase + dt * sweep_rate * 0.3


def _apply_random_movement(
    engine: "EffectsEngine", fixture: "FixtureConfig", state: "FixtureState",
    data: "AnalysisData", beat_triggered: bool, bar_triggered: bool,
    has_pan: bool, has_tilt: bool, pan_range: float, pan_center: float,
    tilt_range: float, tilt_center: float, speed: float, energy: float, bass: float
) -> None:
    """Random unpredictable movement for variety."""
    # Change position on beats with some randomness
    should_move = beat_triggered and (random.random() < 0.4 + energy * 0.4)
    
    if should_move:
        if has_pan:
            # Random pan within configured range
            pan_factor = random.uniform(-0.9, 0.9)
            pan_offset = pan_factor * (pan_range / 2) * speed
            engine._target_pan[fixture.name] = int(pan_center + pan_offset)
        
        if has_tilt:
            # Random tilt, biased toward lower positions (walls)
            tilt_factor = random.uniform(-0.3, 0.9)
            tilt_offset = tilt_factor * (tilt_range / 2) * speed
            engine._target_tilt[fixture.name] = int(tilt_center + tilt_offset)
        
        engine._beats_since_move = 0


# =============================================================================
# NEW DYNAMIC SHOW MODES
# =============================================================================

def _apply_circle_movement(
    engine: "EffectsEngine", fixture: "FixtureConfig", state: "FixtureState",
    data: "AnalysisData", beat_triggered: bool, bar_triggered: bool,
    has_pan: bool, has_tilt: bool, pan_range: float, pan_center: float,
    tilt_range: float, tilt_center: float, speed: float, energy: float, bass: float
) -> None:
    """
    Circular motion - beams trace circles with phase offset per fixture.
    Classic moving head effect that looks amazing with multiple fixtures.
    Speed increases with energy, size pulses on beats.
    """
    import math
    
    dt = 0.025  # ~40fps frame time
    base_rate = 0.08 * speed  # Base rotation speed
    
    # Energy increases rotation speed
    rate = base_rate * (0.7 + energy * 0.6)
    
    # Get/update circle phase for this fixture
    current_phase = engine._sweep_phase.get(fixture.name, 0.0)
    current_phase += dt * rate * math.pi * 2  # Full rotation over time
    if current_phase > math.pi * 2:
        current_phase -= math.pi * 2
    engine._sweep_phase[fixture.name] = current_phase
    
    # Each fixture is offset in phase for wave effect
    fixture_offset = fixture.position * (math.pi / 3)  # 60 degree offset per fixture
    phase = current_phase + fixture_offset
    
    # Circle size - base size with beat pulse
    base_size = 0.5 * speed
    if beat_triggered:
        engine._sweep_direction[fixture.name] = 1.0  # Reset pulse
    
    # Decay the beat pulse
    pulse = engine._sweep_direction.get(fixture.name, 0.0)
    if isinstance(pulse, (int, float)):
        pulse = max(0, pulse - dt * 3)  # Fast decay
    else:
        pulse = 0.0
    engine._sweep_direction[fixture.name] = pulse
    
    size = base_size + pulse * 0.3 * bass  # Pulse grows circle on beats
    
    if has_pan:
        pan_factor = math.cos(phase) * size
        engine._target_pan[fixture.name] = int(pan_center + pan_factor * (pan_range / 2))
    
    if has_tilt:
        tilt_factor = math.sin(phase) * size * 0.7  # Slightly flatter tilt range
        engine._target_tilt[fixture.name] = int(tilt_center + tilt_factor * (tilt_range / 2))


def _apply_figure_8_movement(
    engine: "EffectsEngine", fixture: "FixtureConfig", state: "FixtureState",
    data: "AnalysisData", beat_triggered: bool, bar_triggered: bool,
    has_pan: bool, has_tilt: bool, pan_range: float, pan_center: float,
    tilt_range: float, tilt_center: float, speed: float, energy: float, bass: float
) -> None:
    """
    Figure-8/lemniscate pattern - elegant infinity loop motion.
    Beautiful flowing movement that fills the space gracefully.
    Uses parametric equations for smooth lemniscate curve.
    """
    import math
    
    dt = 0.025
    base_rate = 0.06 * speed  # Slightly slower for elegance
    
    # Energy affects speed
    rate = base_rate * (0.6 + energy * 0.5)
    
    # Update phase
    current_phase = engine._sweep_phase.get(fixture.name, 0.0)
    current_phase += dt * rate * math.pi * 2
    if current_phase > math.pi * 2:
        current_phase -= math.pi * 2
    engine._sweep_phase[fixture.name] = current_phase
    
    # Fixture offset for variation
    fixture_offset = fixture.position * (math.pi / 4)  # 45 degree offset
    phase = current_phase + fixture_offset
    
    # Figure-8 (lemniscate) parametric equations
    # x = cos(t), y = sin(t) * cos(t) = sin(2t)/2
    size = 0.6 * speed * (0.8 + energy * 0.2)
    
    if has_pan:
        # Wide horizontal sweep
        pan_factor = math.cos(phase) * size
        engine._target_pan[fixture.name] = int(pan_center + pan_factor * (pan_range / 2))
    
    if has_tilt:
        # Figure-8 vertical component (crosses at center)
        tilt_factor = math.sin(2 * phase) * 0.5 * size
        # Add slight downward bias for better room coverage
        tilt_bias = 0.2 * size
        engine._target_tilt[fixture.name] = int(tilt_center + (tilt_factor + tilt_bias) * (tilt_range / 2))


def _apply_ballyhoo_movement(
    engine: "EffectsEngine", fixture: "FixtureConfig", state: "FixtureState",
    data: "AnalysisData", beat_triggered: bool, bar_triggered: bool,
    has_pan: bool, has_tilt: bool, pan_range: float, pan_center: float,
    tilt_range: float, tilt_center: float, speed: float, energy: float, bass: float
) -> None:
    """
    Ballyhoo - fast sweeping wave motion across fixtures.
    Classic professional lighting effect where beams sweep across
    in a coordinated wave pattern. Very dynamic and impressive.
    """
    import math
    
    dt = 0.025
    # Ballyhoo is fast! Base rate is aggressive
    base_rate = 0.12 * speed
    rate = base_rate * (0.8 + energy * 0.4)
    
    # Update phase
    current_phase = engine._sweep_phase.get(fixture.name, 0.0)
    current_phase += dt * rate * math.pi * 2
    if current_phase > math.pi * 2:
        current_phase -= math.pi * 2
    engine._sweep_phase[fixture.name] = current_phase
    
    # Large phase offset creates the wave "rolling" across fixtures
    fixture_offset = fixture.position * (math.pi / 2)  # 90 degree offset - quarter phase
    phase = current_phase + fixture_offset
    
    # Full range movement
    size = 0.85 * speed
    
    if has_pan:
        # Sweeping pan - full range side to side
        pan_factor = math.sin(phase) * size
        engine._target_pan[fixture.name] = int(pan_center + pan_factor * (pan_range / 2))
    
    if has_tilt:
        # Tilt follows with slight delay creating "wave" look
        # Use cosine for phase offset from pan
        tilt_phase = phase + math.pi / 4  # 45 degree delay
        tilt_factor = math.sin(tilt_phase) * size * 0.6
        # Add energy-based tilt offset
        tilt_energy_offset = 0.2 * energy
        engine._target_tilt[fixture.name] = int(tilt_center + (tilt_factor + tilt_energy_offset) * (tilt_range / 2))
    
    # On strong beats, reverse direction for extra drama
    if beat_triggered and bass > 0.7:
        engine._sweep_phase[fixture.name] = math.pi * 2 - current_phase


def _apply_fan_movement(
    engine: "EffectsEngine", fixture: "FixtureConfig", state: "FixtureState",
    data: "AnalysisData", beat_triggered: bool, bar_triggered: bool,
    has_pan: bool, has_tilt: bool, pan_range: float, pan_center: float,
    tilt_range: float, tilt_center: float, speed: float, energy: float, bass: float
) -> None:
    """
    Fan mode - fixtures fan in and out from center point.
    Creates dramatic reveals and contractions. On bars, fixtures
    either spread wide or converge to center. Beat pulses affect tilt.
    """
    import math
    
    # Fan state: 0.0 = converged at center, 1.0 = fully fanned out
    fan_amount = engine._sweep_phase.get(fixture.name, 0.5)
    target_fan = engine._sweep_direction.get(fixture.name, 0.5)
    
    # On bar changes, alternate between fanned out and converged
    if bar_triggered:
        bar_num = data.estimated_bar
        if bar_num % 2 == 0:
            target_fan = 0.9  # Fan out wide
        else:
            target_fan = 0.2  # Converge to center
        engine._sweep_direction[fixture.name] = target_fan
    
    # Smoothly interpolate toward target fan amount
    dt = 0.025
    interp_rate = 0.08 * speed
    fan_diff = target_fan - fan_amount
    fan_amount += fan_diff * interp_rate
    engine._sweep_phase[fixture.name] = fan_amount
    
    # Calculate fixture's position in the fan
    # Fixtures spread evenly across the fan angle
    total_fixtures = max(1, len([f for f in engine.config.fixtures]))
    fixture_idx = fixture.position
    
    # Normalized position: -1 to 1 across all fixtures
    if total_fixtures > 1:
        normalized_pos = (fixture_idx / (total_fixtures - 1)) * 2 - 1
    else:
        normalized_pos = 0
    
    if has_pan:
        # Pan spreads based on fan amount and fixture position
        pan_factor = normalized_pos * fan_amount * speed * 0.85
        engine._target_pan[fixture.name] = int(pan_center + pan_factor * (pan_range / 2))
    
    if has_tilt:
        # Tilt - when fanned out, tilt down; when converged, tilt up
        base_tilt = 0.3 - fan_amount * 0.4
        # Add beat pulse to tilt
        if beat_triggered and bass > 0.4:
            beat_tilt_pulse = bass * 0.2
        else:
            beat_tilt_pulse = 0
        tilt_factor = (base_tilt + beat_tilt_pulse) * speed
        engine._target_tilt[fixture.name] = int(tilt_center + tilt_factor * (tilt_range / 2))


def _apply_chase_movement(
    engine: "EffectsEngine", fixture: "FixtureConfig", state: "FixtureState",
    data: "AnalysisData", beat_triggered: bool, bar_triggered: bool,
    has_pan: bool, has_tilt: bool, pan_range: float, pan_center: float,
    tilt_range: float, tilt_center: float, speed: float, energy: float, bass: float
) -> None:
    """
    Chase mode - sequential position chase where beams "chase" across fixtures.
    One fixture at a time points to a highlighted position while others
    stay neutral. Creates exciting sequential scanning effect.
    """
    import math
    
    # Chase index tracks which fixture is currently "active"
    # Stored in first fixture's wall_corner_index
    chase_idx = engine._wall_corner_index.get("__chase_index__", 0)
    total_fixtures = len(engine.config.fixtures)
    
    # Advance chase on beats
    if beat_triggered:
        chase_idx = (chase_idx + 1) % max(1, total_fixtures)
        engine._wall_corner_index["__chase_index__"] = chase_idx
    
    # Define chase positions (dramatic points)
    chase_positions = [
        (0.8, 0.6),    # Far right, down
        (0.5, 0.8),    # Right, far down
        (0.0, 0.7),    # Center, down
        (-0.5, 0.8),   # Left, far down
        (-0.8, 0.6),   # Far left, down
        (-0.6, 0.3),   # Left, mid
        (0.0, 0.2),    # Center, up
        (0.6, 0.3),    # Right, mid
    ]
    
    fixture_idx = fixture.position
    is_active = (fixture_idx % total_fixtures) == chase_idx
    
    if is_active:
        # Active fixture goes to dramatic chase position
        pos_idx = (chase_idx + data.estimated_bar) % len(chase_positions)
        pan_factor, tilt_factor = chase_positions[pos_idx]
        
        if has_pan:
            engine._target_pan[fixture.name] = int(pan_center + pan_factor * (pan_range / 2) * speed)
        if has_tilt:
            engine._target_tilt[fixture.name] = int(tilt_center + tilt_factor * (tilt_range / 2) * speed)
    else:
        # Non-active fixtures return to neutral but slightly spread
        spread = (fixture_idx / max(1, total_fixtures - 1) - 0.5) * 0.3
        
        if has_pan:
            engine._target_pan[fixture.name] = int(pan_center + spread * (pan_range / 2) * speed)
        if has_tilt:
            # Neutral tilt, slightly down
            engine._target_tilt[fixture.name] = int(tilt_center + 0.2 * (tilt_range / 2) * speed)


def _apply_strobe_position_movement(
    engine: "EffectsEngine", fixture: "FixtureConfig", state: "FixtureState",
    data: "AnalysisData", beat_triggered: bool, bar_triggered: bool,
    has_pan: bool, has_tilt: bool, pan_range: float, pan_center: float,
    tilt_range: float, tilt_center: float, speed: float, energy: float, bass: float
) -> None:
    """
    Strobe Position mode - fast snappy beat-synced position jumps.
    Fixtures jump instantly to new positions on every beat.
    Very aggressive, high-energy look. Uses full range.
    """
    import math
    
    # This mode uses FAST interpolation - positions change on every beat
    if beat_triggered:
        beat_num = data.estimated_beat
        bar_num = data.estimated_bar
        
        # Predefined dramatic positions - full range, aggressive
        positions = [
            (0.9, 0.9),     # Far right, far down
            (-0.9, 0.9),    # Far left, far down
            (0.0, -0.5),    # Center, up (into crowd)
            (0.7, 0.0),     # Right, center
            (-0.7, 0.0),    # Left, center
            (0.9, -0.3),    # Far right, slightly up
            (-0.9, -0.3),   # Far left, slightly up
            (0.0, 0.9),     # Center, far down
        ]
        
        # Each fixture gets different position based on beat and fixture offset
        fixture_offset = fixture.position * 3
        pos_idx = (beat_num + fixture_offset + bar_num) % len(positions)
        pan_factor, tilt_factor = positions[pos_idx]
        
        # Add randomness scaled by energy for chaos
        if energy > 0.6:
            pan_factor += random.uniform(-0.2, 0.2)
            tilt_factor += random.uniform(-0.1, 0.2)
        
        # Clamp to valid range
        pan_factor = max(-1.0, min(1.0, pan_factor))
        tilt_factor = max(-1.0, min(1.0, tilt_factor))
        
        if has_pan:
            engine._target_pan[fixture.name] = int(pan_center + pan_factor * (pan_range / 2) * speed)
        if has_tilt:
            engine._target_tilt[fixture.name] = int(tilt_center + tilt_factor * (tilt_range / 2) * speed)
        
        engine._beats_since_move = 0


def _apply_crazy_movement(
    engine: "EffectsEngine", fixture: "FixtureConfig", state: "FixtureState",
    data: "AnalysisData", beat_triggered: bool, bar_triggered: bool,
    has_pan: bool, has_tilt: bool, pan_range: float, pan_center: float,
    tilt_range: float, tilt_center: float, speed: float, energy: float, bass: float
) -> None:
    """
    CRAZY mode - wild full-range movement showcasing the entire pan/tilt capability.
    
    This is the most extreme movement mode - rapid, large movements across
    the full 0-255 range for both pan and tilt. Perfect for high-energy drops,
    showcasing fixtures, or creating absolute chaos. Still synced to music
    with beat-reactive direction changes and energy-modulated speed.
    
    Features:
    - Full 0-255 range utilization for both pan and tilt
    - Multiple overlapping oscillation patterns (Lissajous-like)
    - Beat-synced direction reversals and position jumps
    - Energy-driven speed modulation
    - Per-fixture phase offset for varied patterns
    - Random chaos injection on strong beats
    """
    import math
    
    dt = 0.025  # ~40fps frame time
    
    # Base oscillation rates - intentionally using non-harmonic ratios
    # for complex Lissajous-like patterns
    base_pan_rate = 0.18 * speed   # Fast pan oscillation
    base_tilt_rate = 0.23 * speed  # Slightly different tilt rate for complexity
    
    # Energy dramatically increases speed - go wild!
    energy_boost = 0.5 + energy * 1.5  # Up to 2x speed at full energy
    pan_rate = base_pan_rate * energy_boost
    tilt_rate = base_tilt_rate * energy_boost
    
    # Get/update phases for this fixture
    # We use sweep_phase for pan, and a separate stored value for tilt
    pan_phase = engine._sweep_phase.get(fixture.name, random.random() * math.pi * 2)
    tilt_phase = engine._sweep_direction.get(fixture.name, random.random() * math.pi * 2)
    
    # Ensure tilt_phase is a float (not direction indicator)
    if not isinstance(tilt_phase, float) or abs(tilt_phase) <= 1.0:
        tilt_phase = random.random() * math.pi * 2
    
    # Advance phases
    pan_phase += dt * pan_rate * math.pi * 2
    tilt_phase += dt * tilt_rate * math.pi * 2
    
    # Wrap phases
    if pan_phase > math.pi * 2:
        pan_phase -= math.pi * 2
    if tilt_phase > math.pi * 2:
        tilt_phase -= math.pi * 2
    
    # Per-fixture offset creates varied patterns across fixtures
    fixture_offset = fixture.position * (math.pi * 0.7)  # ~126 degrees offset
    
    # On beats, add chaos:
    # - Reverse one or both directions
    # - Jump to random phase
    # - Add extra phase boost
    if beat_triggered:
        chaos_roll = random.random()
        
        if bass > 0.7 and chaos_roll < 0.4:
            # Strong bass hit - reverse pan direction
            pan_phase = math.pi * 2 - pan_phase
        
        if energy > 0.6 and chaos_roll < 0.5:
            # High energy - big phase jump
            pan_phase += random.uniform(0.5, 1.5)
            tilt_phase += random.uniform(0.3, 1.0)
        
        if chaos_roll < 0.25:
            # Random chaos - jump to completely new position
            pan_phase = random.random() * math.pi * 2
            tilt_phase = random.random() * math.pi * 2
    
    # On bar changes, potentially swap movement patterns
    if bar_triggered:
        if random.random() < 0.3:
            # Sometimes reverse tilt direction on bars
            tilt_phase = math.pi * 2 - tilt_phase
    
    # Store updated phases
    engine._sweep_phase[fixture.name] = pan_phase
    engine._sweep_direction[fixture.name] = tilt_phase
    
    # Calculate pan position using compound sine waves for complexity
    # Main wave + harmonic for more interesting motion
    pan_main = math.sin(pan_phase + fixture_offset)
    pan_harmonic = math.sin(pan_phase * 2.7 + fixture_offset) * 0.3
    pan_factor = (pan_main + pan_harmonic) / 1.3  # Normalize to roughly -1 to 1
    
    # Use FULL range - from fixture min to max (not centered)
    if has_pan:
        # Map -1..1 to 0..255 (or fixture.pan_min..pan_max)
        pan_normalized = (pan_factor + 1.0) / 2.0  # 0 to 1
        target_pan = int(fixture.pan_min + pan_normalized * pan_range)
        engine._target_pan[fixture.name] = max(fixture.pan_min, min(fixture.pan_max, target_pan))
    
    # Calculate tilt with different compound pattern
    tilt_main = math.sin(tilt_phase + fixture_offset * 0.5)
    tilt_harmonic = math.cos(tilt_phase * 1.9) * 0.4  # Cosine for offset pattern
    tilt_factor = (tilt_main + tilt_harmonic) / 1.4  # Normalize
    
    if has_tilt:
        # Map -1..1 to 0..255 (full range)
        tilt_normalized = (tilt_factor + 1.0) / 2.0  # 0 to 1
        target_tilt = int(fixture.tilt_min + tilt_normalized * tilt_range)
        engine._target_tilt[fixture.name] = max(fixture.tilt_min, min(fixture.tilt_max, target_tilt))
    
    engine._beats_since_move = 0


def _interpolate_position(engine: "EffectsEngine", fixture: "FixtureConfig", 
                          state: "FixtureState", mode: MovementMode, speed: float) -> None:
    """
    Smoothly interpolate fixture position toward target.
    Interpolation speed varies by movement mode for appropriate feel.
    """
    # Define interpolation rates for each mode
    # Lower = smoother/slower movement, higher = snappier/faster
    interp_rates = {
        MovementMode.SUBTLE: (0.06, 0.06),      # Very smooth
        MovementMode.STANDARD: (0.12, 0.15),    # Moderate
        MovementMode.DRAMATIC: (0.18, 0.22),    # Snappier
        MovementMode.WALL_WASH: (0.08, 0.10),   # Smooth sweeps
        MovementMode.SWEEP: (0.05, 0.05),       # Continuous smooth
        MovementMode.RANDOM: (0.10, 0.12),      # Moderate
        # New dynamic show modes
        MovementMode.CIRCLE: (0.15, 0.15),      # Smooth continuous circle
        MovementMode.FIGURE_8: (0.12, 0.12),    # Elegant flowing motion
        MovementMode.BALLYHOO: (0.25, 0.25),    # Fast sweeping waves
        MovementMode.FAN: (0.10, 0.12),         # Smooth fan in/out
        MovementMode.CHASE: (0.20, 0.22),       # Snappy chase effect
        MovementMode.STROBE_POSITION: (0.45, 0.45),  # Very fast/snappy jumps
        MovementMode.CRAZY: (0.55, 0.55),             # Maximum speed - wild movements
    }
    
    pan_rate, tilt_rate = interp_rates.get(mode, (0.12, 0.15))
    
    # Apply speed modifier (higher speed = faster interpolation)
    pan_rate *= (0.5 + speed * 0.5)
    tilt_rate *= (0.5 + speed * 0.5)
    
    # Interpolate pan
    current_pan = state.pan
    target_pan = engine._target_pan.get(fixture.name, 128)
    pan_diff = target_pan - current_pan
    state.pan = int(current_pan + pan_diff * pan_rate)
    
    # Interpolate tilt
    current_tilt = state.tilt
    target_tilt = engine._target_tilt.get(fixture.name, 128)
    tilt_diff = target_tilt - current_tilt
    state.tilt = int(current_tilt + tilt_diff * tilt_rate)
    
    # Set P/T speed channel appropriately for mode
    # For continuous modes, we want slower motor speed for smoothness
    if mode in (MovementMode.SWEEP, MovementMode.SUBTLE, MovementMode.FIGURE_8):
        state.pt_speed = int(60 * (1.0 - speed))  # Slower motor
    elif mode in (MovementMode.DRAMATIC, MovementMode.STROBE_POSITION, MovementMode.BALLYHOO, MovementMode.CRAZY):
        state.pt_speed = int(10 * (1.0 - speed))  # Fast motor
    elif mode in (MovementMode.CIRCLE, MovementMode.CHASE):
        state.pt_speed = int(20 * (1.0 - speed))  # Moderately fast motor
    else:
        state.pt_speed = int(30 * (1.0 - speed))  # Moderate
