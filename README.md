# Music Auto Show

A cross-platform Python application that automatically visualizes system audio to DMX-controlled lighting fixtures in real-time.

## Features

- **Real-Time Audio Analysis**: Captures system audio via WASAPI loopback (Windows) and analyzes it live
  - BPM/Tempo detection using Aubio
  - Beat tracking and onset detection
  - Energy/loudness levels
  - Frequency bands (bass/mid/high)
- **ENTTEC Open DMX USB Support**: Works with ENTTEC Open DMX USB and compatible FTDI-based DMX interfaces
- **Multiple Visualization Modes**:
  - Energy - Intensity based on audio energy
  - Frequency Split - Bass/mid/high across fixtures
  - Beat Pulse - Pulse on beat detection
  - Color Cycle - Tempo-based color cycling
  - Rainbow Wave - Animated rainbow across fixtures
  - Strobe Beat - Strobe on beats
  - Random Flash - Random fixture flashes
- **Fixture Configuration**:
  - Define raw DMX channel mappings
  - Support for RGB, RGBW, dimmer, strobe
  - Pan/tilt with movement limits
  - Position/orientation for effects ordering
- **GUI with Live Visualizer**: See fixture colors and audio analysis in real-time
- **Headless Mode**: Run from JSON config without GUI
- **Works with Any Audio Source**: Spotify, YouTube, local files, games - anything playing through your system

## Installation

### Option 1: Conda (Recommended)

Conda is recommended because `aubio` (the beat detection library) requires pre-compiled binaries that aren't available via pip for all Python versions.

**Windows:**
```bash
conda env create -f environment.yml
conda activate music-auto-show
```

**Linux:**
```bash
conda env create -f environment-linux.yml
conda activate music-auto-show
```

If you don't have conda, download Miniconda from: https://docs.conda.io/en/latest/miniconda.html

### Option 2: pip only (Python 3.10-3.12)

If you have Python 3.10, 3.11, or 3.12, aubio may install directly via pip:

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

> **Note:** pip install of aubio fails on Python 3.13+ on Windows due to missing pre-built wheels. Use conda instead.

### Option 3: Linux with system packages

On Debian/Ubuntu, you can install aubio system-wide:

```bash
sudo apt install python3-aubio python3-pyaudio

# Then install the rest
pip install pydantic numpy pyftdi pyserial dearpygui Pillow dbus-python
```

### Dependencies

**Required:**
- `pydantic` - Configuration validation
- `numpy` - Numerical operations
- `PyAudioWPatch` - WASAPI loopback audio capture (Windows)
- `PyAudio` - Audio capture (Linux/Mac)
- `aubio` - Real-time beat/tempo detection

**Optional (but recommended):**
- `dearpygui` - GUI interface
- `pyftdi` - FTDI/ENTTEC Open DMX USB support
- `pyserial` - Generic serial DMX support
- `Pillow` - Album art color extraction
- `winrt-*` (Windows) / `dbus-python` (Linux) - Now playing info

### Windows Audio Setup

The application captures system audio using WASAPI loopback. This works automatically on Windows - no additional configuration needed. The app will capture whatever audio is playing through your default speakers/headphones.

## Quick Start

### GUI Mode

```bash
python main.py
```

### Headless Mode

```bash
# Run with configuration file
python main.py --headless example_config.json

# Run with simulation (no hardware required)
python main.py --headless example_config.json --simulate
```

### Create Example Config

```bash
python main.py --create-example my_config.json
```

### Check Dependencies

```bash
python main.py --check-deps
```

## Configuration

Configuration is stored as JSON. Example structure:

```json
{
  "name": "My Light Show",
  "dmx": {
    "port": "",
    "universe_size": 512,
    "fps": 40
  },
  "effects": {
    "mode": "rainbow_wave",
    "intensity": 0.8,
    "color_speed": 1.0,
    "beat_sensitivity": 0.5,
    "smooth_factor": 0.3,
    "strobe_on_drop": false,
    "movement_enabled": true,
    "movement_speed": 0.5
  },
  "fixtures": [
    {
      "name": "Par Light 1",
      "start_channel": 1,
      "position": 0,
      "channels": [
        {"channel": 1, "channel_type": "red"},
        {"channel": 2, "channel_type": "green"},
        {"channel": 3, "channel_type": "blue"},
        {"channel": 4, "channel_type": "dimmer"}
      ]
    }
  ]
}
```

### Fixture Channel Types

| Type | Description |
|------|-------------|
| `red` | Red color channel |
| `green` | Green color channel |
| `blue` | Blue color channel |
| `white` | White color channel |
| `dimmer` | Master dimmer |
| `pan` | Pan position (0-255) |
| `pan_fine` | Pan fine control |
| `tilt` | Tilt position (0-255) |
| `tilt_fine` | Tilt fine control |
| `speed` | Movement speed |
| `strobe` | Strobe control |
| `color_wheel` | Color wheel position |
| `gobo` | Gobo selection |
| `none` | Unused channel |

### Visualization Modes

| Mode | Description |
|------|-------------|
| `energy` | Audio energy drives overall brightness |
| `frequency_split` | Split fixtures into bass/mid/high bands |
| `beat_pulse` | Pulse intensity on beats |
| `color_cycle` | Cycle through colors based on tempo |
| `rainbow_wave` | Rainbow effect waves across fixtures |
| `strobe_beat` | Strobe flash on beats |
| `random_flash` | Random fixtures flash on beats |

## Audio Analysis Features

The real-time audio analyzer provides:

| Feature | Description |
|---------|-------------|
| **Energy** | Overall loudness/intensity (0-1) |
| **Bass** | Low frequency energy (20-250 Hz) |
| **Mid** | Mid frequency energy (250-4000 Hz) |
| **High** | High frequency energy (4000-20000 Hz) |
| **Tempo** | Detected BPM (beats per minute) |
| **Beat** | Beat detection with timing |
| **Onset** | Note/hit detection |

## Hardware Setup

### ENTTEC Open DMX USB

1. Connect the ENTTEC Open DMX USB to your computer
2. The port will be auto-detected (or specify manually in config)
3. On Linux, you may need to add your user to the `dialout` group:
   ```bash
   sudo usermod -a -G dialout $USER
   ```

### Supported DMX Interfaces

- ENTTEC Open DMX USB (FT232R-based)
- Other FTDI FT232-based USB-DMX interfaces
- Generic serial DMX interfaces

## Project Structure

```
music-auto-show/
├── main.py              # Entry point
├── config.py            # Configuration models
├── dmx_controller.py    # DMX interface layer
├── audio_analyzer.py    # Real-time audio analysis
├── effects_engine.py    # Visualization engine
├── gui.py               # Dear PyGui interface
├── headless.py          # Headless mode runner
├── requirements.txt     # Dependencies
├── example_config.json  # Example configuration
└── README.md            # This file
```

## Troubleshooting

### No audio detected
- Make sure audio is playing through your default output device
- Check that PyAudioWPatch is installed: `pip install PyAudioWPatch`
- Try running with `--simulate-audio` to test without audio capture

### DMX not working
- Check the USB connection
- On Windows, ensure FTDI drivers are installed
- Try specifying the port manually in the config
- Use `--simulate-dmx` to test without hardware

## License

MIT License
