# Music Auto Show

A cross-platform Python application that automatically visualizes music from Spotify to DMX-controlled lighting fixtures.

## Features

- **ENTTEC Open DMX USB Support**: Works with ENTTEC Open DMX USB and compatible FTDI-based DMX interfaces
- **Spotify Integration**: Real-time audio feature analysis (energy, tempo, beats, etc.)
- **Multiple Visualization Modes**:
  - Energy - Intensity based on track energy
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
- **GUI with Live Visualizer**: See fixture colors and positions in real-time
- **Headless Mode**: Run from JSON config without GUI
- **Cross-Platform**: Works on Windows, macOS, Linux
- **Simulation Mode**: Test without hardware or Spotify API

## Installation

```bash
# Clone or download the project
cd music-auto-show

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

**Required:**
- `pydantic` - Configuration validation
- `numpy` - Numerical operations

**Optional (but recommended):**
- `dearpygui` - GUI interface
- `pyftdi` - FTDI/ENTTEC Open DMX USB support
- `pyserial` - Generic serial DMX support
- `spotipy` - Spotify API integration

## Quick Start

### GUI Mode

```bash
python main.py
```

### Headless Mode

```bash
# Run with configuration file
python main.py --headless example_config.json

# Run with simulation (no hardware/API required)
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
  "spotify": {
    "client_id": "your_client_id",
    "client_secret": "your_client_secret",
    "redirect_uri": "http://127.0.0.1:8888/callback"
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
| `energy` | Track energy drives overall brightness |
| `frequency_split` | Split fixtures into bass/mid/high bands |
| `beat_pulse` | Pulse intensity on beats |
| `color_cycle` | Cycle through colors based on tempo |
| `rainbow_wave` | Rainbow effect waves across fixtures |
| `strobe_beat` | Strobe flash on beats |
| `random_flash` | Random fixtures flash on beats |

## Spotify Setup

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new application
3. Add `http://127.0.0.1:8888/callback` to Redirect URIs
4. Copy Client ID and Client Secret to your config
5. When first running, a browser will open for authentication

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
├── spotify_analyzer.py  # Spotify API integration
├── effects_engine.py    # Visualization engine
├── gui.py               # Dear PyGui interface
├── headless.py          # Headless mode runner
├── requirements.txt     # Dependencies
├── example_config.json  # Example configuration
└── README.md            # This file
```

## License

MIT License
