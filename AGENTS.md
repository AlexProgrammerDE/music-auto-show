# AGENTS.md - Coding Agent Guidelines

This document provides guidelines for AI coding agents working on the Music Auto Show codebase.

## Project Overview

Music Auto Show is a cross-platform Python application that visualizes system audio to DMX-controlled lighting fixtures in real-time. It uses audio analysis (beat detection, frequency bands, energy levels) to drive lighting effects.

## Build & Run Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run GUI mode
python main.py

# Run headless mode with config
python main.py --headless config.json

# Run with simulation (no hardware required)
python main.py --simulate

# Create example configuration
python main.py --create-example example_config.json

# Check dependencies
python main.py --check-deps
```

## Testing & Validation

```bash
# Syntax check a single file
python3 -m py_compile <filename>.py

# Type checking with pyright (configured in pyproject.toml)
pyright <filename>.py
pyright .  # Check all files

# Run the app in simulation mode to test changes
python main.py --simulate
```

**Note:** There are no formal unit tests in this project. Validation is done via:
1. Syntax checking with `py_compile`
2. Type checking with pyright (basic mode)
3. Manual testing with `--simulate` flag

## Code Style Guidelines

### File Structure
- Each module has a docstring at the top explaining its purpose
- Imports organized: stdlib → third-party → local
- Use `TYPE_CHECKING` blocks for imports only needed for type hints (avoids circular imports)

```python
"""
Module description here.
"""
import math
import threading
from typing import TYPE_CHECKING, Optional, List

from pydantic import BaseModel  # Third-party

from config import ShowConfig  # Local

if TYPE_CHECKING:
    from effects_engine import EffectsEngine
```

### Type Hints
- Use type hints for all function parameters and return values
- Use `Optional[T]` for values that can be None
- Use forward references with strings for circular imports: `"EffectsEngine"`
- The project uses pyright in "basic" mode (see pyproject.toml)

```python
def process(self, data: AnalysisData) -> dict[str, FixtureState]:
    """Process audio data and return fixture states."""
    ...

def _get_profile(self, fixture: FixtureConfig) -> Optional[FixtureProfile]:
    ...
```

### Naming Conventions
- **Classes**: PascalCase (`EffectsEngine`, `FixtureState`, `DMXController`)
- **Functions/Methods**: snake_case (`apply_movement`, `get_data`)
- **Private methods**: Leading underscore (`_apply_smoothing`, `_update_loop`)
- **Constants**: UPPER_SNAKE_CASE (`FIXTURE_PRESETS`, `DEARPYGUI_AVAILABLE`)
- **Enums**: PascalCase class, UPPER_SNAKE_CASE members (`MovementMode.CIRCLE`)

### Dataclasses & Pydantic
- Use `@dataclass` for simple data containers (e.g., `FixtureState`)
- Use Pydantic `BaseModel` for configuration with validation (e.g., `ShowConfig`, `FixtureConfig`)
- Pydantic models use `Field()` for descriptions and validation

```python
from dataclasses import dataclass
from pydantic import BaseModel, Field

@dataclass
class FixtureState:
    red: int = 0
    green: int = 0
    dimmer: int = 255

class EffectsConfig(BaseModel):
    mode: VisualizationMode = Field(default=VisualizationMode.ENERGY)
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
```

### Error Handling
- Use try/except for optional imports with fallback flags
- Log errors with the `logging` module, don't print
- Graceful degradation when hardware unavailable

```python
try:
    import dearpygui.dearpygui as dpg
    DEARPYGUI_AVAILABLE = True
except ImportError:
    DEARPYGUI_AVAILABLE = False

logger = logging.getLogger(__name__)
```

### Threading
- Use `threading.Thread` with `daemon=True` for background tasks
- Protect shared state with `threading.Lock()`
- Use `Optional[threading.Thread]` for thread references

### Enum Patterns
- Enums inherit from both `str` and `Enum` for JSON serialization
- Each enum value has a descriptive comment

```python
class MovementMode(str, Enum):
    """Movement modes for pan/tilt fixtures."""
    SUBTLE = "subtle"  # Minimal movement
    CIRCLE = "circle"  # Circular motion
```

## Architecture Patterns

### Module Organization
- `config.py` - All Pydantic models and enums
- `effects_engine.py` - Core processing, delegates to mode modules
- `visualization_modes.py` - Color/intensity effect implementations
- `movement_modes.py` - Pan/tilt movement implementations  
- `simulators.py` - Mock implementations for testing
- `gui.py` - Dear PyGui interface
- `gui_dialogs.py`, `gui_visualizer.py` - GUI components

### Adding New Movement Modes
1. Add enum value to `MovementMode` in `config.py`
2. Implement `_apply_<mode>_movement()` function in `movement_modes.py`
3. Add dispatch case in `apply_movement()` function
4. Add interpolation rate in `_interpolate_position()`

### Adding New Visualization Modes
1. Add enum value to `VisualizationMode` in `config.py`
2. Implement `apply_<mode>_mode()` function in `visualization_modes.py`
3. Add dispatch case in `EffectsEngine.process()`

## Key Data Structures

### Audio Analysis Data
```python
AnalysisData.features.bass    # 0.0-1.0, low frequency energy
AnalysisData.features.mid     # 0.0-1.0, mid frequency energy  
AnalysisData.features.high    # 0.0-1.0, high frequency energy
AnalysisData.features.energy  # 0.0-1.0, overall loudness
AnalysisData.features.tempo   # BPM
AnalysisData.beat_position    # 0.0-1.0, position within current beat
AnalysisData.estimated_beat   # Beat counter
AnalysisData.estimated_bar    # Bar counter (4 beats)
```

### Fixture State
```python
FixtureState.red, .green, .blue, .white  # Color channels 0-255
FixtureState.dimmer    # Master intensity 0-255
FixtureState.pan, .tilt  # Position 0-255
FixtureState.strobe    # Strobe speed 0-255
```

## Python Version & Dependencies

- **Python**: 3.10+ required (uses modern type hints)
- **Key deps**: pydantic>=2.0, numpy, madmom, dearpygui
- **Platform-specific**: PyAudioWPatch (Windows), PyAudio (Linux)

Note: madmom provides neural network beat tracking (RNN + DBN) with online mode for real-time processing.

## Common Gotchas

1. **Circular imports**: Use `TYPE_CHECKING` and string forward references
2. **DMX values**: Always 0-255 integers, use `int()` and clamp with `max(0, min(255, value))`
3. **Hue values**: 0.0-1.0 float, handle wrap-around at boundaries
4. **Thread safety**: Lock when accessing shared state from callbacks
5. **Simulation mode**: Always test with `--simulate` before hardware testing
