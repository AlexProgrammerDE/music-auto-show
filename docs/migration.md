# Rust and Vite migration

## Outcome

Music Auto Show now runs as one Rust executable. The executable hosts the gRPC-Web API and a Vite SPA on the same address, with the production frontend embedded in the binary. The Python and NiceGUI runtime is no longer part of the repository.

The migration keeps the lighting behavior and operational controls while replacing the implementation boundary:

```text
Before: Python capture, madmom, effects, DMX, NiceGUI
After:  Rust capture, BeatNet+, effects, DMX, tonic-web + Vite SPA
```

## Feature parity contract

| Surface | Rust and Vite implementation |
| --- | --- |
| Audio capture | Native CPAL capture with stable device IDs, PipeWire or PulseAudio sink monitoring on Linux, and WASAPI loopback on Windows |
| Simulation | Deterministic generated audio and an in-memory DMX universe |
| Audio features | Energy, RMS, bass, mid, high, spectrum, waveform, onset history, danceability, and valence |
| Beat tracking | Native [BeatNet+](https://github.com/mjhydri/BeatNet-Plus) feature extraction and inference with causal tempo, phase, beat, downbeat, and bar decoding |
| Visualizations | Energy, frequency split, beat pulse, color cycle, rainbow wave, strobe beat, and random flash |
| Movement | Subtle, standard, dramatic, wall wash, sweep, random, circle, figure eight, ballyhoo, fan, chase, strobe position, and crazy |
| Fixture behavior | Smoothing, intensity scaling, pan and tilt limits, color mixing, dual-color mapping, fixed channels, capabilities, strobe programs, rotation, and blackout |
| Fixture library | Purelight Muvy WashQ, generic RGB/RGBW profiles, Showtec Techno Derby, and Lixada DJ Projektor |
| DMX | 512-channel inspection, Open DMX serial output, adapter discovery, status counters, and simulation |
| Media | Cross-platform now-playing metadata and album-art palette extraction |
| Recording | Input check while stopped, 30-second capture, metering, WAV preview, clear, and download |
| Live UI | Waveform, spectrum, five-second spectrogram, meters, BeatNet+ state, stage beams, fixture output, media palette, and I/O status |
| Configuration | Server-side validation, legacy JSON migration, import, export, reset, active-file persistence, profiles, and fixture channel editing |

## BeatNet+ boundary

The neural network matches the official model shape:

- 22,050 Hz mono analysis input
- 1,764-sample Hann windows and 441-sample hops
- 144 log-frequency bands plus their positive differences, for 288 features
- convolution, projection, four unidirectional LSTM layers, and three output classes
- causal particle tracking for beat phase, tempo, and meter

The detector uses Candle to read the PyTorch state dictionary directly. It does not launch Python. Checkpoints remain external and are ignored by Git because the upstream BeatNet+ repository does not publish redistribution terms for its weights.

## API and process model

The protobuf file is the source of truth. Tonic implements the Rust service, `tonic-web` accepts gRPC-Web requests, and Buf generates the browser types. Connect's web transport sends binary protobuf messages from the SPA.

Unary calls control the show, persist settings, manage recording, and list devices. A server-streaming call publishes live snapshots. There is no authentication at this stage. The SPA uses `/api` on the current origin, while every non-API path resolves to an embedded asset or the SPA shell, so production traffic stays on one port.

The relevant upstream references are:

- [tonic-web](https://docs.rs/tonic-web/latest/tonic_web/)
- [Buf generated code](https://buf.build/docs/generate/)
- [Connect for web](https://connectrpc.com/docs/web/getting-started/)
- [TanStack Table v9 taking form](https://tanstack.com/blog/tanstack-table-v9-taking-form)
- [Credenza](https://github.com/redpangilinan/credenza)

## Frontend ownership rules

The SPA was initialized from shadcn preset `b2fms620zo`. Files in `frontend/src/components/ui/**` are upstream-owned and must not be edited. Application composition, Credenza integration, visualizations, and feature-specific controls live outside that directory.

Tailwind utilities and the preset's CSS variables own application styling. Canvas drawing code may calculate colors and geometry because those values represent live visualization data, not page styling.

Oxfmt and Oxlint skip the upstream UI directory, generated protobuf code, and generated TanStack route tree. ESLint is intentionally absent.

## Configuration migration

`src/config.rs` fills settings introduced by the Rust version and maps older values such as `fallback_mode`, `audio_gain`, and `figure_8`. Import runs this migration on the server before validation and persistence.

The active configuration defaults to `config.json`. Use the Settings page to load an existing file, then export the normalized Rust schema if a portable migrated copy is needed.
