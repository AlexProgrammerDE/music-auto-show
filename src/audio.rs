use std::{
    collections::VecDeque,
    io::Read,
    process::{Child, Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver},
    },
    thread,
    time::{Duration, Instant},
};

use anyhow::{Context, Result, bail};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use realfft::RealFftPlanner;

use crate::{
    beatnet::{BeatEstimate, BeatNetPlus},
    proto::v1::{
        AudioAnalysis, AudioConfig, AudioDevice, AudioInputMode, AudioRuntimeStatus, BeatNetStatus,
        Recording, RecordingStatus, SpectrogramFrame,
    },
};

const ANALYSIS_RATE: u32 = 44_100;
const FFT_SIZE: usize = 1_024;
const MAX_RECORDING_SECONDS: f32 = 30.0;

pub struct AudioCapture {
    pub receiver: Receiver<Vec<f32>>,
    pub status: AudioRuntimeStatus,
    _guard: CaptureGuard,
}

enum CaptureGuard {
    Cpal {
        _stream: cpal::Stream,
    },
    Pulse {
        child: Arc<Mutex<Child>>,
        running: Arc<AtomicBool>,
        thread: Option<thread::JoinHandle<()>>,
    },
}

impl Drop for CaptureGuard {
    fn drop(&mut self) {
        if let Self::Pulse {
            child,
            running,
            thread,
        } = self
        {
            running.store(false, Ordering::Relaxed);
            if let Ok(mut child) = child.lock() {
                let _ = child.kill();
                let _ = child.wait();
            }
            if let Some(thread) = thread.take() {
                let _ = thread.join();
            }
        }
    }
}

pub fn list_devices() -> Vec<AudioDevice> {
    let host = cpal::default_host();
    let default_name = host
        .default_input_device()
        .and_then(|device| device.name().ok());
    let host_name = format!("{:?}", host.id());
    let mut devices = Vec::new();
    if let Ok(inputs) = host.input_devices() {
        for (index, device) in inputs.enumerate() {
            let Ok(name) = device.name() else { continue };
            let configuration = device.default_input_config().ok();
            let channels = configuration
                .as_ref()
                .map_or(0, |config| config.channels() as u32);
            let sample_rate = configuration
                .as_ref()
                .map_or(0, |config| config.sample_rate().0);
            let lowered = name.to_lowercase();
            let device_type = if lowered.contains("monitor") || lowered.contains("loopback") {
                "monitor"
            } else if lowered.contains("line") {
                "line_in"
            } else {
                "microphone"
            };
            devices.push(AudioDevice {
                id: format!("cpal:{index}"),
                name: name.clone(),
                source_name: String::new(),
                device_type: device_type.into(),
                channels,
                output_channels: 0,
                sample_rate,
                host_api: host_name.clone(),
                is_default: default_name.as_deref() == Some(name.as_str()),
                is_default_loopback: false,
            });
        }
    }
    for source in pulse_sources() {
        let is_monitor = source.ends_with(".monitor");
        if !devices.iter().any(|device| device.source_name == source) {
            devices.push(AudioDevice {
                id: format!("pulse:{source}"),
                name: source.clone(),
                source_name: source,
                device_type: if is_monitor { "monitor" } else { "microphone" }.into(),
                channels: 2,
                output_channels: 0,
                sample_rate: ANALYSIS_RATE,
                host_api: "PipeWire/PulseAudio".into(),
                is_default: false,
                is_default_loopback: is_monitor,
            });
        }
    }
    devices
}

pub fn start_capture(config: &AudioConfig) -> Result<AudioCapture> {
    let mode = AudioInputMode::try_from(config.mode).unwrap_or(AudioInputMode::Auto);
    if matches!(
        mode,
        AudioInputMode::SystemAudio | AudioInputMode::PipewireSink
    ) || (mode == AudioInputMode::Auto
        && !pulse_sources()
            .iter()
            .all(|name| !name.ends_with(".monitor")))
    {
        return start_pulse_capture(config, mode);
    }
    start_cpal_capture(config, mode)
}

fn start_cpal_capture(config: &AudioConfig, mode: AudioInputMode) -> Result<AudioCapture> {
    let host = cpal::default_host();
    let selected = if config.device_name.is_empty() {
        host.default_input_device()
    } else {
        host.input_devices()?
            .find(|device| device.name().is_ok_and(|name| name == config.device_name))
            .or_else(|| host.default_input_device())
    }
    .context("no audio input device is available")?;
    let name = selected.name().unwrap_or_else(|_| "Unknown input".into());
    let supported = selected.default_input_config()?;
    let channels = supported.channels() as usize;
    let sample_rate = supported.sample_rate().0;
    let stream_config: cpal::StreamConfig = supported.clone().into();
    let (sender, receiver) = mpsc::sync_channel(32);
    let error_callback = |error| tracing::error!(%error, "audio input stream failed");
    let stream = match supported.sample_format() {
        cpal::SampleFormat::F32 => selected.build_input_stream(
            &stream_config,
            move |data: &[f32], _| send_mono(data, channels, &sender, |sample| sample),
            error_callback,
            None,
        )?,
        cpal::SampleFormat::I16 => selected.build_input_stream(
            &stream_config,
            move |data: &[i16], _| {
                send_mono(data, channels, &sender, |sample| sample as f32 / 32_768.0)
            },
            error_callback,
            None,
        )?,
        cpal::SampleFormat::U16 => selected.build_input_stream(
            &stream_config,
            move |data: &[u16], _| {
                send_mono(data, channels, &sender, |sample| {
                    sample as f32 / 32_768.0 - 1.0
                })
            },
            error_callback,
            None,
        )?,
        format => bail!("unsupported input sample format {format:?}"),
    };
    stream.play()?;
    let missing = if !config.device_name.is_empty() && config.device_name != name {
        config.device_name.clone()
    } else {
        String::new()
    };
    Ok(AudioCapture {
        receiver,
        status: AudioRuntimeStatus {
            configured_device_name: config.device_name.clone(),
            configured_mode: mode as i32,
            actual_mode: if mode == AudioInputMode::Auto {
                AudioInputMode::Microphone as i32
            } else {
                mode as i32
            },
            device_name: name,
            device_type: "microphone".into(),
            host_api: format!("{:?}", host.id()),
            channels: channels as u32,
            sample_rate,
            selection_reason: if missing.is_empty() {
                "configured_or_default"
            } else {
                "configured_device_missing_fallback"
            }
            .into(),
            missing_device_name: missing,
            running: true,
            last_error: String::new(),
            simulated: false,
        },
        _guard: CaptureGuard::Cpal { _stream: stream },
    })
}

fn send_mono<T: Copy>(
    data: &[T],
    channels: usize,
    sender: &mpsc::SyncSender<Vec<f32>>,
    convert: impl Fn(T) -> f32,
) {
    let mono = data
        .chunks(channels.max(1))
        .map(|frame| frame.iter().copied().map(&convert).sum::<f32>() / frame.len() as f32)
        .collect();
    let _ = sender.try_send(mono);
}

fn start_pulse_capture(config: &AudioConfig, mode: AudioInputMode) -> Result<AudioCapture> {
    let sources = pulse_sources();
    let source = if !config.pipewire_source_name.is_empty() {
        sources
            .iter()
            .find(|source| **source == config.pipewire_source_name)
            .cloned()
    } else if !config.device_name.is_empty() {
        sources
            .iter()
            .find(|source| source.contains(&config.device_name))
            .cloned()
    } else {
        sources
            .iter()
            .find(|source| source.ends_with(".monitor"))
            .cloned()
    }
    .context("no PipeWire/PulseAudio monitor source is available")?;

    let mut child = Command::new("parec")
        .args([
            "--raw",
            "--format=float32le",
            "--rate=44100",
            "--channels=2",
            "--device",
            &source,
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .with_context(|| format!("failed to start parec for {source}"))?;
    let mut stdout = child.stdout.take().context("parec did not expose stdout")?;
    let child = Arc::new(Mutex::new(child));
    let running = Arc::new(AtomicBool::new(true));
    let running_thread = Arc::clone(&running);
    let (sender, receiver) = mpsc::sync_channel(32);
    let thread = thread::Builder::new()
        .name("pipewire-audio-capture".into())
        .spawn(move || {
            let mut bytes = vec![0_u8; 8_192];
            while running_thread.load(Ordering::Relaxed) {
                let Ok(read) = stdout.read(&mut bytes) else {
                    break;
                };
                if read == 0 {
                    break;
                }
                let samples: Vec<f32> = bytes[..read - read % 8]
                    .chunks_exact(8)
                    .map(|stereo| {
                        let left = f32::from_le_bytes(stereo[..4].try_into().expect("four bytes"));
                        let right = f32::from_le_bytes(stereo[4..].try_into().expect("four bytes"));
                        (left + right) * 0.5
                    })
                    .collect();
                if sender.send(samples).is_err() {
                    break;
                }
            }
        })?;
    Ok(AudioCapture {
        receiver,
        status: AudioRuntimeStatus {
            configured_device_name: config.device_name.clone(),
            configured_mode: mode as i32,
            actual_mode: AudioInputMode::PipewireSink as i32,
            device_name: source.clone(),
            device_type: "monitor".into(),
            host_api: "PipeWire/PulseAudio".into(),
            channels: 2,
            sample_rate: ANALYSIS_RATE,
            selection_reason: "pipewire_monitor_source".into(),
            missing_device_name: String::new(),
            running: true,
            last_error: String::new(),
            simulated: false,
        },
        _guard: CaptureGuard::Pulse {
            child,
            running,
            thread: Some(thread),
        },
    })
}

fn pulse_sources() -> Vec<String> {
    Command::new("pactl")
        .args(["list", "short", "sources"])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| {
            String::from_utf8_lossy(&output.stdout)
                .lines()
                .filter_map(|line| line.split_whitespace().nth(1).map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

pub struct AudioAnalyzer {
    sample_rate: u32,
    gain: f32,
    beatnet: Option<BeatNetPlus>,
    beatnet_status: BeatNetStatus,
    resampler: LinearResampler,
    energy_history: VecDeque<f32>,
    bass_history: VecDeque<f32>,
    mid_history: VecDeque<f32>,
    high_history: VecDeque<f32>,
    onset_history: VecDeque<f32>,
    spectrogram: VecDeque<Vec<f32>>,
    last_spectrogram: Instant,
    recording: Recorder,
    last_analysis: AudioAnalysis,
}

impl AudioAnalyzer {
    pub fn new(sample_rate: u32, gain: f32, model_path: &str) -> Self {
        let (beatnet, beatnet_status) = match BeatNetPlus::load(model_path) {
            Ok(model) => (
                Some(model),
                BeatNetStatus {
                    available: true,
                    processing: false,
                    model_name: "BeatNet+ generic".into(),
                    model_path: model_path.into(),
                    status: "Ready".into(),
                    last_error: String::new(),
                    buffer_duration_seconds: 0.08,
                },
            ),
            Err(error) => (
                None,
                BeatNetStatus {
                    available: false,
                    processing: false,
                    model_name: "BeatNet+".into(),
                    model_path: model_path.into(),
                    status: "Model unavailable".into(),
                    last_error: error.to_string(),
                    buffer_duration_seconds: 0.0,
                },
            ),
        };
        Self {
            sample_rate,
            gain,
            beatnet,
            beatnet_status,
            resampler: LinearResampler::new(sample_rate, 22_050),
            energy_history: VecDeque::with_capacity(10),
            bass_history: VecDeque::with_capacity(5),
            mid_history: VecDeque::with_capacity(5),
            high_history: VecDeque::with_capacity(5),
            onset_history: VecDeque::with_capacity(64),
            spectrogram: VecDeque::with_capacity(50),
            last_spectrogram: Instant::now() - Duration::from_secs(1),
            recording: Recorder::new(sample_rate),
            last_analysis: AudioAnalysis::default(),
        }
    }

    pub fn process(&mut self, samples: &[f32]) -> AudioAnalysis {
        if samples.is_empty() {
            return self.last_analysis.clone();
        }
        self.recording.capture(samples);
        let rms = (samples.iter().map(|sample| sample * sample).sum::<f32>()
            / samples.len() as f32)
            .sqrt();
        push_bounded(&mut self.energy_history, rms, 10);
        let fft = fft_power(samples, FFT_SIZE);
        let signal_power = rms * rms;
        let band = |low, high| band_power(&fft, self.sample_rate, FFT_SIZE, low, high);
        let (bass, mid, high) = normalize_bands(
            band(20.0, 250.0),
            band(250.0, 4_000.0),
            band(4_000.0, 16_000.0),
            signal_power,
            self.gain,
        );
        push_bounded(&mut self.bass_history, bass, 5);
        push_bounded(&mut self.mid_history, mid, 5);
        push_bounded(&mut self.high_history, high, 5);

        let resampled = self.resampler.process(samples);
        let beat = self
            .beatnet
            .as_mut()
            .and_then(|model| model.push_resampled_samples(&resampled).ok().flatten())
            .unwrap_or_else(|| fallback_beat(&self.last_analysis));
        self.beatnet_status.processing = self.beatnet.is_some();
        self.onset_history
            .push_back(beat.beat_activation.max(beat.downbeat_activation));
        if self.onset_history.len() > 64 {
            self.onset_history.pop_front();
        }

        let average_rms = average(&self.energy_history);
        let gain_db = 20.0 * self.gain.max(0.0001).log10();
        let energy = if average_rms > 1e-10 {
            ((20.0 * average_rms.log10() + gain_db + 60.0) / 60.0).clamp(0.0, 1.0)
        } else {
            0.0
        };
        let bass = average(&self.bass_history);
        let mid = average(&self.mid_history);
        let high = average(&self.high_history);
        let danceability = (bass * 0.5 + energy * 0.5).min(1.0);
        let valence = if bass + mid + high > 0.0 {
            ((mid + high * 2.0) / (bass + mid + high + 0.001)).min(1.0)
        } else {
            0.0
        };

        if self.last_spectrogram.elapsed() >= Duration::from_millis(100) {
            self.spectrogram.push_back(spectrogram_frame(
                &fft,
                self.sample_rate,
                FFT_SIZE,
                self.gain,
            ));
            if self.spectrogram.len() > 50 {
                self.spectrogram.pop_front();
            }
            self.last_spectrogram = Instant::now();
        }
        self.last_analysis = AudioAnalysis {
            energy,
            rms,
            bass,
            mid,
            high,
            tempo: beat.tempo,
            beat_detected: beat.beat,
            downbeat_detected: beat.downbeat,
            beat_confidence: beat.confidence,
            beat_position: beat.beat_position,
            bar_position: beat.bar_position,
            estimated_beat: beat.estimated_beat,
            estimated_bar: beat.estimated_bar,
            danceability,
            valence,
            waveform: waveform(samples, self.gain),
            spectrum: spectrum(&fft, self.sample_rate, FFT_SIZE, self.gain),
            spectrogram: self
                .spectrogram
                .iter()
                .cloned()
                .map(|bins| SpectrogramFrame { bins })
                .collect(),
            onset_history: self.onset_history.iter().copied().collect(),
        };
        self.last_analysis.clone()
    }

    pub fn process_simulated(&mut self, samples: &[f32], beat: BeatEstimate) -> AudioAnalysis {
        let mut analysis = self.process(samples);
        analysis.tempo = beat.tempo;
        analysis.beat_detected = beat.beat;
        analysis.downbeat_detected = beat.downbeat;
        analysis.beat_confidence = beat.confidence;
        analysis.beat_position = beat.beat_position;
        analysis.bar_position = beat.bar_position;
        analysis.estimated_beat = beat.estimated_beat;
        analysis.estimated_bar = beat.estimated_bar;
        self.last_analysis = analysis.clone();
        analysis
    }

    pub fn beatnet_status(&self) -> BeatNetStatus {
        self.beatnet_status.clone()
    }
    pub fn start_recording(&mut self) -> bool {
        self.recording.start()
    }
    pub fn stop_recording(&mut self) -> Recording {
        self.recording.stop()
    }
    pub fn clear_recording(&mut self) {
        self.recording.clear()
    }
    pub fn recording_status(&self) -> RecordingStatus {
        self.recording.status()
    }
}

fn fallback_beat(previous: &AudioAnalysis) -> BeatEstimate {
    BeatEstimate {
        tempo: previous.tempo,
        beat_position: previous.beat_position,
        bar_position: previous.bar_position,
        estimated_beat: previous.estimated_beat,
        estimated_bar: previous.estimated_bar,
        ..Default::default()
    }
}

fn fft_power(samples: &[f32], size: usize) -> Vec<f32> {
    let mut input = vec![0.0_f32; size];
    let copy = samples.len().min(size);
    input[..copy].copy_from_slice(&samples[..copy]);
    for (index, value) in input.iter_mut().enumerate() {
        *value *= 0.5 - 0.5 * (std::f32::consts::TAU * index as f32 / (size - 1) as f32).cos();
    }
    let mut planner = RealFftPlanner::<f32>::new();
    let fft = planner.plan_fft_forward(size);
    let mut output = fft.make_output_vec();
    if fft.process(&mut input, &mut output).is_err() {
        return vec![0.0; size / 2 + 1];
    }
    output.iter().map(|value| value.norm_sqr()).collect()
}

fn band_power(fft: &[f32], sample_rate: u32, size: usize, low: f32, high: f32) -> f32 {
    let low = (low * size as f32 / sample_rate as f32) as usize;
    let high = (high * size as f32 / sample_rate as f32) as usize;
    let low = low.min(fft.len().saturating_sub(1));
    let high = high.clamp(low + 1, fft.len());
    fft[low..high].iter().sum::<f32>() / (high - low) as f32
}

fn normalize_bands(bass: f32, mid: f32, high: f32, signal: f32, gain: f32) -> (f32, f32, f32) {
    if signal < 1e-10 {
        return (0.0, 0.0, 0.0);
    }
    let normalized = |power: f32, offset: f32| {
        let db = 10.0 * (power / signal + 1e-10).log10();
        ((db - offset + 30.0) / 60.0 + (gain - 1.0) * 0.2).clamp(0.0, 1.0)
    };
    (
        normalized(bass, 35.0),
        normalized(mid, 25.0),
        normalized(high, 5.0),
    )
}

fn waveform(samples: &[f32], gain: f32) -> Vec<f32> {
    let chunk = (samples.len() / 100).max(1);
    (0..100)
        .map(|index| {
            let start = index * chunk;
            let end = (start + chunk).min(samples.len());
            if start >= samples.len() {
                0.0
            } else {
                (samples[start..end]
                    .iter()
                    .map(|sample| sample.abs())
                    .fold(0.0_f32, f32::max)
                    / 0.3
                    * gain)
                    .min(1.0)
            }
        })
        .collect()
}

fn spectrum(fft: &[f32], sample_rate: u32, size: usize, gain: f32) -> Vec<f32> {
    logarithmic_bands(32, 20.0, 16_000.0_f32.min(sample_rate as f32 / 2.0))
        .map(|(low, high)| (band_power(fft, sample_rate, size, low, high) / 0.01 * gain).min(1.0))
        .collect()
}

fn spectrogram_frame(fft: &[f32], sample_rate: u32, size: usize, gain: f32) -> Vec<f32> {
    let gain_db = 20.0 * gain.max(0.0001).log10();
    logarithmic_bands(64, 20.0, 16_000.0_f32.min(sample_rate as f32 / 2.0))
        .map(|(low, high)| {
            let db =
                10.0 * (band_power(fft, sample_rate, size, low, high) + 1e-12).log10() + gain_db;
            ((db + 92.0) / 74.0).clamp(0.0, 1.0)
        })
        .collect()
}

fn logarithmic_bands(count: usize, minimum: f32, maximum: f32) -> impl Iterator<Item = (f32, f32)> {
    let ratio = (maximum / minimum).powf(1.0 / count as f32);
    (0..count).map(move |index| {
        (
            minimum * ratio.powi(index as i32),
            minimum * ratio.powi(index as i32 + 1),
        )
    })
}

fn push_bounded(values: &mut VecDeque<f32>, value: f32, limit: usize) {
    values.push_back(value);
    if values.len() > limit {
        values.pop_front();
    }
}

fn average(values: &VecDeque<f32>) -> f32 {
    if values.is_empty() {
        0.0
    } else {
        values.iter().sum::<f32>() / values.len() as f32
    }
}

struct LinearResampler {
    ratio: f64,
    position: f64,
    previous: f32,
    initialized: bool,
}

impl LinearResampler {
    fn new(input_rate: u32, output_rate: u32) -> Self {
        Self {
            ratio: input_rate as f64 / output_rate as f64,
            position: 0.0,
            previous: 0.0,
            initialized: false,
        }
    }

    fn process(&mut self, input: &[f32]) -> Vec<f32> {
        if input.is_empty() {
            return Vec::new();
        }
        if !self.initialized {
            self.previous = input[0];
            self.initialized = true;
        }
        let mut extended = Vec::with_capacity(input.len() + 1);
        extended.push(self.previous);
        extended.extend_from_slice(input);
        let mut output = Vec::new();
        while self.position + 1.0 < extended.len() as f64 {
            let index = self.position.floor() as usize;
            let fraction = (self.position - index as f64) as f32;
            output.push(extended[index] * (1.0 - fraction) + extended[index + 1] * fraction);
            self.position += self.ratio;
        }
        self.position -= input.len() as f64;
        self.previous = *input.last().expect("non-empty input");
        output
    }
}

struct Recorder {
    recording: bool,
    sample_rate: u32,
    samples: Vec<f32>,
    sum_squares: f64,
    peak: f32,
    clipped: u64,
}

impl Recorder {
    fn new(sample_rate: u32) -> Self {
        Self {
            recording: false,
            sample_rate,
            samples: Vec::new(),
            sum_squares: 0.0,
            peak: 0.0,
            clipped: 0,
        }
    }
    fn start(&mut self) -> bool {
        self.clear();
        self.recording = true;
        true
    }
    fn clear(&mut self) {
        self.recording = false;
        self.samples.clear();
        self.sum_squares = 0.0;
        self.peak = 0.0;
        self.clipped = 0;
    }
    fn capture(&mut self, samples: &[f32]) {
        if !self.recording {
            return;
        }
        let maximum = (self.sample_rate as f32 * MAX_RECORDING_SECONDS) as usize;
        let remaining = maximum.saturating_sub(self.samples.len());
        for sample in samples.iter().take(remaining).copied() {
            self.samples.push(sample);
            self.sum_squares += (sample * sample) as f64;
            self.peak = self.peak.max(sample.abs());
            self.clipped += u64::from(sample.abs() >= 0.99);
        }
        if self.samples.len() >= maximum {
            self.recording = false;
        }
    }
    fn status(&self) -> RecordingStatus {
        RecordingStatus {
            recording: self.recording,
            has_recording: !self.samples.is_empty(),
            duration_seconds: self.samples.len() as f32 / self.sample_rate as f32,
            max_duration_seconds: MAX_RECORDING_SECONDS,
            sample_rate: self.sample_rate,
            channels: 1,
            peak: self.peak,
            rms: if self.samples.is_empty() {
                0.0
            } else {
                (self.sum_squares / self.samples.len() as f64).sqrt() as f32
            },
            clipped_samples: self.clipped,
            source: "Analysis mono stream".into(),
            error: String::new(),
        }
    }
    fn stop(&mut self) -> Recording {
        self.recording = false;
        let mut wav = Vec::new();
        {
            let cursor = std::io::Cursor::new(&mut wav);
            if let Ok(mut writer) = hound::WavWriter::new(
                cursor,
                hound::WavSpec {
                    channels: 1,
                    sample_rate: self.sample_rate,
                    bits_per_sample: 16,
                    sample_format: hound::SampleFormat::Int,
                },
            ) {
                for sample in &self.samples {
                    let _ = writer.write_sample((sample.clamp(-1.0, 1.0) * i16::MAX as f32) as i16);
                }
                let _ = writer.finalize();
            }
        }
        Recording {
            status: Some(self.status()),
            wav,
        }
    }
}

pub fn simulated_audio(elapsed: f32, beat_count: u64) -> (Vec<f32>, BeatEstimate) {
    let tempo = 128.0;
    let interval = 60.0 / tempo;
    let phase = (elapsed % interval) / interval;
    let beat = phase < 0.06;
    let energy = 0.5 + 0.3 * (elapsed * 0.2).sin();
    let sample_count = (ANALYSIS_RATE as f32 * 0.025) as usize;
    let samples = (0..sample_count)
        .map(|index| {
            let time = elapsed + index as f32 / ANALYSIS_RATE as f32;
            let amplitude = (0.12 + energy * 0.28 + (1.0 - phase) * 0.1).min(0.85);
            amplitude
                * (0.48 * (std::f32::consts::TAU * 90.0 * time).sin()
                    + 0.32 * (std::f32::consts::TAU * 440.0 * time).sin()
                    + 0.18 * (std::f32::consts::TAU * 1_800.0 * time).sin()
                    + 0.08 * (std::f32::consts::TAU * 6_200.0 * time).sin())
        })
        .collect();
    (
        samples,
        BeatEstimate {
            tempo,
            beat,
            downbeat: beat && beat_count.is_multiple_of(4),
            confidence: 0.9,
            beat_position: phase,
            bar_position: ((beat_count % 4) as f32 + phase) / 4.0,
            estimated_beat: beat_count,
            estimated_bar: beat_count / 4,
            beat_activation: if beat { 0.9 } else { 0.1 },
            downbeat_activation: if beat && beat_count.is_multiple_of(4) {
                0.9
            } else {
                0.05
            },
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn diagnostic_recording_is_a_valid_wav() {
        let mut recorder = Recorder::new(44_100);
        assert!(recorder.start());
        recorder.capture(&vec![0.25; 4_410]);
        let recording = recorder.stop();
        assert!(recording.wav.starts_with(b"RIFF"));
        approx::assert_abs_diff_eq!(
            recording.status.expect("status").duration_seconds,
            0.1,
            epsilon = 0.001
        );
    }

    #[test]
    fn spectrogram_retains_five_seconds_at_ten_hertz() {
        let mut analyzer = AudioAnalyzer::new(44_100, 1.0, "");
        analyzer.spectrogram = (0..50).map(|_| vec![0.0; 64]).collect();
        analyzer.spectrogram.push_back(vec![1.0; 64]);
        if analyzer.spectrogram.len() > 50 {
            analyzer.spectrogram.pop_front();
        }
        assert_eq!(analyzer.spectrogram.len(), 50);
    }
}
