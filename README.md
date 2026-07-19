# Music Auto Show

Music Auto Show turns live system audio into real-time DMX lighting. A Rust service captures and analyzes audio, runs the original visualization and movement algorithms, drives Open DMX hardware, and serves a bundled Vite single-page app. The browser and gRPC-Web API share one port.

## What is included

- Native BeatNet+ inference with the official 288-feature network shape and causal beat, downbeat, tempo, phase, and bar tracking
- System audio, Bluetooth receiver, microphone, and deterministic simulation inputs through CPAL
- Energy, frequency split, beat pulse, color cycle, rainbow wave, strobe beat, and random flash visualizations
- All movement modes from the original app, including sweeps, circles, figure eight, ballyhoo, fan, chase, strobe position, and crazy movement
- Per-fixture channel mapping, fixed values, channel ranges, movement limits, intensity scaling, and built-in profiles
- Open DMX USB output with auto-discovery, break and mark-after-break timing, live universe inspection, and simulation
- Live waveform, spectrum, five-second spectrogram, frequency meters, stage beams, fixture output, media metadata, and album palette visualizations
- Audio input recording with preview and WAV download
- JSON configuration import, migration, export, and reset through the Rust API
- A Vite SPA built with TanStack Router, Query, Form, Table v9, Effect, shadcn/ui, Tailwind CSS variables, and Credenza

## Requirements

- Rust 1.88 or newer
- Bun 1.3.13 or a compatible newer release
- Network access on the first run if the BeatNet+ checkpoint is not already available

Linux builds need ALSA, udev, and PipeWire development libraries. CPAL builds its native PipeWire backend with bindgen, so Clang is also required. Audio capture does not shell out to command-line utilities.

```bash
# Debian or Ubuntu
sudo apt install clang libasound2-dev libdbus-1-dev libpipewire-0.3-dev libudev-dev pkg-config

# Fedora
sudo dnf install alsa-lib-devel clang dbus-devel pipewire-devel pkgconf-pkg-config systemd-devel

# Arch Linux
sudo pacman -S alsa-lib clang dbus pipewire pkgconf systemd-libs
```

On Linux, add the hardware user to the serial-port group before using Open DMX:

```bash
sudo usermod -a -G dialout "$USER"
```

Log out and back in after changing group membership.

## Install and run

```bash
bun install --frozen-lockfile
bun install --cwd frontend --frozen-lockfile
cargo run --release -- --simulate
```

Open `http://127.0.0.1:3000`. Simulation exercises audio analysis, the full effects pipeline, live visualizations, and DMX output without hardware.

For real audio and DMX:

```bash
cargo run --release -- --config show.json --listen 127.0.0.1:3000
```

On Linux, Automatic and System Audio capture the current PipeWire default sink and follow later output-device changes. CPAL falls back to its native PulseAudio host, then ALSA, when PipeWire is unavailable. Manual selections are stored as CPAL device IDs instead of display names or PulseAudio source strings.

Bluetooth Receiver makes the host act as a Bluetooth speaker and analyzes the audio sent by a paired phone. On Linux, it controls the system BlueZ adapter and captures the A2DP sink exposed by WirePlumber. `bluetoothd` and the PipeWire BlueZ monitor must be running. This is the supported path on Raspberry Pi OS. On Windows, pair the phone in Bluetooth settings, connect it from the app, and keep the desired playback device selected as the default Windows output. The Settings page reports adapter, pairing, profile, and connection status on both platforms.

The configuration file is created with defaults when it does not exist. The Settings page can load an older JSON configuration, migrate it through Rust, save the active file, or export a portable copy.

### BeatNet+ checkpoint

The binary expects the checkpoint at `models/beatnet-plus.pt` by default. You can select another path in Settings. Before runtime tasks start, the application checks the configured path and downloads the official general-purpose `generic_weights.pt` checkpoint when the file is missing. The download is pinned to a known upstream revision, verified with SHA-256, and moved into place only after verification. Existing checkpoint files are never replaced.

Checkpoint files are intentionally not committed or embedded. The upstream repository does not currently provide a license for redistributing its published weights, so review its terms before use. If the download fails or the configured file is incompatible, the app keeps running and reports the detector error in the live BeatNet+ panel.

## Frontend development

Run the Rust service and Vite dev server in separate terminals:

```bash
cargo run -- --simulate
bun run frontend:dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to the Rust server on port 3000. Production uses one port because `rust-embed` includes `frontend/dist` in the executable and Axum falls back to the SPA shell for client routes. All gRPC-Web methods live below `/api`; every other path belongs to embedded assets or the SPA.

The protobuf contract lives in `proto/music_auto_show/v1/music_auto_show.proto`. Buf generates the TypeScript client definitions used by Connect gRPC-Web:

```bash
bun run proto:format
bun run proto:lint
bun run proto:generate
```

## Validate and build

```bash
# Protobuf, frontend formatting/types/lint/tests, then Rust tests
bun run check

# Build the SPA and embed it in a release binary
bun run build
```

Frontend formatting and linting use Oxfmt and Oxlint. `frontend/src/components/ui/**` is excluded because those files are owned by the shadcn preset and remain upstream-compatible. ESLint is not part of the project.

## Runtime options

```text
--listen <ADDRESS>            Address for the SPA and gRPC-Web API [default: 127.0.0.1:3000]
--config <PATH>               JSON configuration to load and save [default: config.json]
--simulate                    Use generated audio and in-memory DMX
--shutdown-timeout <SECONDS>  Maximum time to wait for a graceful shutdown [default: 10]
```

The same runtime settings can be supplied through `MUSIC_AUTO_SHOW_LISTEN`,
`MUSIC_AUTO_SHOW_CONFIG`, `MUSIC_AUTO_SHOW_SIMULATE`, and
`MUSIC_AUTO_SHOW_SHUTDOWN_TIMEOUT`. Command-line values take precedence. Set
`RUST_LOG` to change logging detail, for example
`RUST_LOG=music_auto_show=debug`.

Ctrl+C shuts the service down gracefully. On Unix, `SIGTERM`, `SIGQUIT`, and
`SIGHUP` do the same. Windows console close, logoff, shutdown, and Ctrl+Break
events are also handled. A second shutdown event or the configured timeout
forces the process to finish.

## Architecture

```text
Audio capture -> Rust analyzer -> BeatNet+ and audio features -> Effects engine -> DMX output
                                      |
                                      v
                              snapshot watch stream
                                      |
                                      v
Vite SPA <- protobuf and gRPC-Web <- tonic-web and Axum <- bundled SPA assets
```

- `src/audio.rs` captures, resamples, analyzes, and records audio.
- `src/beatnet.rs` implements BeatNet+ feature extraction, inference, and causal decoding.
- `src/effects.rs` contains the ported visualization, movement, fixture, and universe algorithms.
- `src/dmx.rs` owns Open DMX and simulated output.
- `src/app.rs` coordinates runtime state and publishes snapshots.
- `src/api.rs` implements the protobuf service.
- `frontend/src` contains the TanStack and Effect SPA.

See [the migration notes](docs/migration.md) for the parity contract and design decisions.

## License

The application is MIT licensed. BeatNet+ code and checkpoint terms are governed by their upstream project. The checkpoint is downloaded directly from upstream at runtime and is not redistributed with this application.
