use std::{
    array,
    collections::VecDeque,
    path::{Path, PathBuf},
};

use candle_core::{DType, Device, Shape};
use candle_nn::VarBuilder;
use rand::{Rng, SeedableRng, rngs::StdRng};
use realfft::{RealFftPlanner, RealToComplex};
use thiserror::Error;

const SAMPLE_RATE: usize = 22_050;
const WINDOW_SIZE: usize = 1_764;
const HOP_SIZE: usize = 441;
const FEATURE_BANDS: usize = 144;
const FEATURE_SIZE: usize = FEATURE_BANDS * 2;
const HIDDEN_SIZE: usize = 150;
const LSTM_GATES: usize = HIDDEN_SIZE * 4;
const LSTM_LAYERS: usize = 4;

#[derive(Debug, Error)]
pub enum BeatNetError {
    #[error("BeatNet+ model path is empty")]
    MissingModelPath,
    #[error("BeatNet+ model does not exist: {0}")]
    ModelNotFound(PathBuf),
    #[error("BeatNet+ checkpoint is incompatible: {0}")]
    InvalidCheckpoint(String),
    #[error("BeatNet+ feature extraction failed: {0}")]
    FeatureExtraction(String),
}

#[derive(Debug, Clone, Copy, Default)]
pub struct BeatEstimate {
    pub tempo: f32,
    pub beat: bool,
    pub downbeat: bool,
    pub confidence: f32,
    pub beat_position: f32,
    pub bar_position: f32,
    pub estimated_beat: u64,
    pub estimated_bar: u64,
    pub beat_activation: f32,
    pub downbeat_activation: f32,
}

/// Fully causal BeatNet+ inference pipeline.
///
/// The neural network topology and 288-dimensional log-spectrogram features
/// match the official model. A causal particle filter tracks tempo, phase, and
/// meter from the model activations without requiring Python or madmom.
pub struct BeatNetPlus {
    model_path: PathBuf,
    feature_extractor: FeatureExtractor,
    network: BeatNetNetwork,
    decoder: ParticleDecoder,
    rolling_tempo: RollingTempoEstimator,
}

impl BeatNetPlus {
    pub const TEMPO_WINDOW_SECONDS: f32 = 8.0;

    pub fn load(path: impl AsRef<Path>) -> Result<Self, BeatNetError> {
        let path = path.as_ref();
        if path.as_os_str().is_empty() {
            return Err(BeatNetError::MissingModelPath);
        }
        if !path.is_file() {
            return Err(BeatNetError::ModelNotFound(path.to_owned()));
        }
        Ok(Self {
            model_path: path.to_owned(),
            feature_extractor: FeatureExtractor::new()?,
            network: BeatNetNetwork::load(path)?,
            decoder: ParticleDecoder::new(),
            rolling_tempo: RollingTempoEstimator::new(),
        })
    }

    pub fn model_path(&self) -> &Path {
        &self.model_path
    }

    pub fn push_resampled_samples(
        &mut self,
        samples_22khz: &[f32],
    ) -> Result<Option<BeatEstimate>, BeatNetError> {
        let mut latest = None;
        for features in self.feature_extractor.push(samples_22khz)? {
            let activations = self.network.infer(&features);
            if let Some(tempo) = self.rolling_tempo.push(activations) {
                self.decoder.align_tempo(tempo);
            }
            let mut estimate = self.decoder.update(activations[0], activations[1]);
            if self.rolling_tempo.has_refreshed() {
                estimate.tempo = self.rolling_tempo.tempo().unwrap_or(0.0);
            }
            latest = Some(merge_frame_estimate(latest, estimate));
        }
        Ok(latest)
    }

    pub fn reset(&mut self) {
        self.feature_extractor.reset();
        self.network.reset();
        self.decoder.reset();
        self.rolling_tempo.reset();
    }
}

fn merge_frame_estimate(previous: Option<BeatEstimate>, mut latest: BeatEstimate) -> BeatEstimate {
    if let Some(previous) = previous {
        latest.beat |= previous.beat;
        latest.downbeat |= previous.downbeat;
        latest.confidence = latest.confidence.max(previous.confidence);
        latest.beat_activation = latest.beat_activation.max(previous.beat_activation);
        latest.downbeat_activation = latest.downbeat_activation.max(previous.downbeat_activation);
    }
    latest
}

struct FeatureExtractor {
    samples: VecDeque<f32>,
    samples_since_frame: usize,
    previous_log_bands: [f32; FEATURE_BANDS],
    window: Vec<f32>,
    filters: Vec<Filter>,
    fft: std::sync::Arc<dyn RealToComplex<f32>>,
}

#[derive(Clone)]
struct Filter {
    start: usize,
    weights: Vec<f32>,
}

impl FeatureExtractor {
    fn new() -> Result<Self, BeatNetError> {
        let mut planner = RealFftPlanner::<f32>::new();
        let filters = logarithmic_filterbank();
        if filters.len() != FEATURE_BANDS {
            return Err(BeatNetError::FeatureExtraction(format!(
                "expected {FEATURE_BANDS} filters, generated {}",
                filters.len()
            )));
        }
        Ok(Self {
            samples: VecDeque::with_capacity(WINDOW_SIZE + HOP_SIZE),
            samples_since_frame: 0,
            previous_log_bands: [0.0; FEATURE_BANDS],
            window: (0..WINDOW_SIZE)
                .map(|index| {
                    0.5 - 0.5
                        * (std::f32::consts::TAU * index as f32 / (WINDOW_SIZE - 1) as f32).cos()
                })
                .collect(),
            filters,
            fft: planner.plan_fft_forward(WINDOW_SIZE),
        })
    }

    fn push(&mut self, samples: &[f32]) -> Result<Vec<[f32; FEATURE_SIZE]>, BeatNetError> {
        let mut frames = Vec::new();
        for &sample in samples {
            self.samples.push_back(sample);
            if self.samples.len() > WINDOW_SIZE {
                self.samples.pop_front();
            }
            self.samples_since_frame += 1;
            if self.samples.len() == WINDOW_SIZE && self.samples_since_frame >= HOP_SIZE {
                self.samples_since_frame = 0;
                frames.push(self.extract_frame()?);
            }
        }
        Ok(frames)
    }

    fn extract_frame(&mut self) -> Result<[f32; FEATURE_SIZE], BeatNetError> {
        let mut input: Vec<f32> = self
            .samples
            .iter()
            .zip(&self.window)
            .map(|(sample, window)| sample * window)
            .collect();
        let mut output = self.fft.make_output_vec();
        self.fft
            .process(&mut input, &mut output)
            .map_err(|error| BeatNetError::FeatureExtraction(error.to_string()))?;
        // madmom excludes the Nyquist bin for an even-sized real FFT.
        let magnitudes: Vec<f32> = output
            .iter()
            .take(WINDOW_SIZE / 2)
            .map(|value| value.norm())
            .collect();
        let mut log_bands = [0.0_f32; FEATURE_BANDS];
        for (index, filter) in self.filters.iter().enumerate() {
            let filtered = filter
                .weights
                .iter()
                .enumerate()
                .map(|(offset, weight)| magnitudes[filter.start + offset] * weight)
                .sum::<f32>();
            log_bands[index] = (filtered + 1.0).log10();
        }
        let mut features = [0.0_f32; FEATURE_SIZE];
        features[..FEATURE_BANDS].copy_from_slice(&log_bands);
        for index in 0..FEATURE_BANDS {
            features[FEATURE_BANDS + index] =
                (log_bands[index] - self.previous_log_bands[index]).max(0.0);
        }
        self.previous_log_bands = log_bands;
        Ok(features)
    }

    fn reset(&mut self) {
        self.samples.clear();
        self.samples_since_frame = 0;
        self.previous_log_bands.fill(0.0);
    }
}

fn logarithmic_filterbank() -> Vec<Filter> {
    let bands_per_octave = 24.0_f64;
    let reference = 440.0_f64;
    let minimum = 30.0_f64;
    let maximum = 17_000.0_f64;
    let left = (f64::log2(minimum / reference) * bands_per_octave).floor() as i32;
    let right = (f64::log2(maximum / reference) * bands_per_octave).ceil() as i32;
    let mut bins = Vec::new();
    for exponent in left..right {
        let frequency = reference * 2.0_f64.powf(exponent as f64 / bands_per_octave);
        if !(minimum..=maximum).contains(&frequency) {
            continue;
        }
        let bin = (frequency * WINDOW_SIZE as f64 / SAMPLE_RATE as f64).round() as usize;
        let bin = bin.min(WINDOW_SIZE / 2 - 1);
        if bins.last() != Some(&bin) {
            bins.push(bin);
        }
    }

    bins.windows(3)
        .map(|window| {
            let start = window[0];
            let mut center = window[1];
            let mut stop = window[2];
            if stop.saturating_sub(start) < 2 {
                center = start;
                stop = start + 1;
            }
            let center_offset = center - start;
            let length = stop - start;
            let mut weights = vec![0.0_f32; length];
            for (offset, weight) in weights.iter_mut().enumerate() {
                *weight = if offset < center_offset {
                    offset as f32 / center_offset.max(1) as f32
                } else {
                    1.0 - (offset - center_offset) as f32 / (length - center_offset).max(1) as f32
                };
            }
            let sum = weights.iter().sum::<f32>();
            if sum > 0.0 {
                for weight in &mut weights {
                    *weight /= sum;
                }
            }
            Filter { start, weights }
        })
        .collect()
}

struct BeatNetNetwork {
    conv_weight: Vec<f32>,
    conv_bias: Vec<f32>,
    input_weight: Vec<f32>,
    input_bias: Vec<f32>,
    lstm: [LstmWeights; LSTM_LAYERS],
    output_weight: Vec<f32>,
    output_bias: Vec<f32>,
    hidden: [Vec<f32>; LSTM_LAYERS],
    cell: [Vec<f32>; LSTM_LAYERS],
}

struct LstmWeights {
    input: Vec<f32>,
    recurrent: Vec<f32>,
    input_bias: Vec<f32>,
    recurrent_bias: Vec<f32>,
}

impl BeatNetNetwork {
    fn load(path: &Path) -> Result<Self, BeatNetError> {
        let builder = VarBuilder::from_pth(path, DType::F32, &Device::Cpu)
            .map_err(|error| BeatNetError::InvalidCheckpoint(error.to_string()))?;
        let mut lstm = Vec::with_capacity(LSTM_LAYERS);
        for layer in 0..LSTM_LAYERS {
            lstm.push(LstmWeights {
                input: load_tensor(
                    &builder,
                    (LSTM_GATES, HIDDEN_SIZE),
                    &format!("lstm.weight_ih_l{layer}"),
                )?,
                recurrent: load_tensor(
                    &builder,
                    (LSTM_GATES, HIDDEN_SIZE),
                    &format!("lstm.weight_hh_l{layer}"),
                )?,
                input_bias: load_tensor(&builder, LSTM_GATES, &format!("lstm.bias_ih_l{layer}"))?,
                recurrent_bias: load_tensor(
                    &builder,
                    LSTM_GATES,
                    &format!("lstm.bias_hh_l{layer}"),
                )?,
            });
        }
        let lstm: [LstmWeights; LSTM_LAYERS] = lstm
            .try_into()
            .map_err(|_| BeatNetError::InvalidCheckpoint("wrong LSTM layer count".into()))?;
        Ok(Self {
            conv_weight: load_tensor(&builder, (2, 1, 10), "conv1.weight")?,
            conv_bias: load_tensor(&builder, 2, "conv1.bias")?,
            input_weight: load_tensor(&builder, (HIDDEN_SIZE, 278), "linear0.weight")?,
            input_bias: load_tensor(&builder, HIDDEN_SIZE, "linear0.bias")?,
            lstm,
            output_weight: load_tensor(&builder, (3, HIDDEN_SIZE), "output_linear.weight")?,
            output_bias: load_tensor(&builder, 3, "output_linear.bias")?,
            hidden: array::from_fn(|_| vec![0.0; HIDDEN_SIZE]),
            cell: array::from_fn(|_| vec![0.0; HIDDEN_SIZE]),
        })
    }

    fn infer(&mut self, features: &[f32; FEATURE_SIZE]) -> [f32; 3] {
        let conv_length = FEATURE_SIZE - 10 + 1;
        let pooled_length = conv_length / 2;
        let mut pooled = vec![0.0_f32; pooled_length * 2];
        for channel in 0..2 {
            for output in 0..pooled_length {
                let conv = |position: usize| {
                    let value = (0..10)
                        .map(|kernel| {
                            features[position + kernel] * self.conv_weight[channel * 10 + kernel]
                        })
                        .sum::<f32>()
                        + self.conv_bias[channel];
                    value.max(0.0)
                };
                pooled[channel * pooled_length + output] =
                    conv(output * 2).max(conv(output * 2 + 1));
            }
        }

        let mut current = dense(&pooled, &self.input_weight, &self.input_bias, HIDDEN_SIZE);
        for layer in 0..LSTM_LAYERS {
            let weights = &self.lstm[layer];
            let mut gates = vec![0.0_f32; LSTM_GATES];
            for (gate, value) in gates.iter_mut().enumerate() {
                let input_sum = dot_row(&weights.input, gate, HIDDEN_SIZE, &current);
                let hidden_sum =
                    dot_row(&weights.recurrent, gate, HIDDEN_SIZE, &self.hidden[layer]);
                *value = input_sum
                    + hidden_sum
                    + weights.input_bias[gate]
                    + weights.recurrent_bias[gate];
            }
            let mut next_hidden = vec![0.0; HIDDEN_SIZE];
            let mut next_cell = vec![0.0; HIDDEN_SIZE];
            for index in 0..HIDDEN_SIZE {
                let input_gate = sigmoid(gates[index]);
                let forget_gate = sigmoid(gates[HIDDEN_SIZE + index]);
                let candidate = gates[HIDDEN_SIZE * 2 + index].tanh();
                let output_gate = sigmoid(gates[HIDDEN_SIZE * 3 + index]);
                next_cell[index] = forget_gate * self.cell[layer][index] + input_gate * candidate;
                next_hidden[index] = output_gate * next_cell[index].tanh();
            }
            self.cell[layer] = next_cell;
            self.hidden[layer] = next_hidden.clone();
            current = next_hidden;
        }
        let logits = dense(&current, &self.output_weight, &self.output_bias, 3);
        softmax3([logits[0], logits[1], logits[2]])
    }

    fn reset(&mut self) {
        for layer in 0..LSTM_LAYERS {
            self.hidden[layer].fill(0.0);
            self.cell[layer].fill(0.0);
        }
    }
}

fn load_tensor<S: Into<Shape>>(
    builder: &VarBuilder<'_>,
    shape: S,
    name: &str,
) -> Result<Vec<f32>, BeatNetError> {
    builder
        .get(shape, name)
        .and_then(|tensor| tensor.flatten_all()?.to_vec1::<f32>())
        .map_err(|error| BeatNetError::InvalidCheckpoint(format!("{name}: {error}")))
}

fn dense(input: &[f32], weight: &[f32], bias: &[f32], outputs: usize) -> Vec<f32> {
    (0..outputs)
        .map(|row| dot_row(weight, row, input.len(), input) + bias[row])
        .collect()
}

fn dot_row(weight: &[f32], row: usize, width: usize, input: &[f32]) -> f32 {
    weight[row * width..(row + 1) * width]
        .iter()
        .zip(input)
        .map(|(weight, value)| weight * value)
        .sum()
}

fn sigmoid(value: f32) -> f32 {
    1.0 / (1.0 + (-value).exp())
}

fn softmax3(values: [f32; 3]) -> [f32; 3] {
    let maximum = values.into_iter().fold(f32::NEG_INFINITY, f32::max);
    let exponentials = values.map(|value| (value - maximum).exp());
    let sum = exponentials.iter().sum::<f32>();
    exponentials.map(|value| value / sum)
}

#[derive(Clone, Copy)]
struct Particle {
    tempo: f32,
    phase: f32,
    beat_in_bar: u8,
    meter: u8,
    weight: f32,
    wrapped: bool,
}

struct ParticleDecoder {
    particles: Vec<Particle>,
    rng: StdRng,
    beat_count: u64,
    bar_count: u64,
    last_beat_probability: f32,
}

#[derive(Clone, Copy)]
struct ActivationFrame {
    beat: f32,
    downbeat: f32,
}

struct RollingTempoEstimator {
    activations: VecDeque<ActivationFrame>,
    frames_since_refresh: usize,
    has_refreshed: bool,
    tempo: Option<f32>,
}

impl RollingTempoEstimator {
    const REFRESH_FRAMES: usize = ParticleDecoder::FPS as usize * 2;
    const ANALYSIS_FRAMES: usize =
        ParticleDecoder::FPS as usize * BeatNetPlus::TEMPO_WINDOW_SECONDS as usize;
    const RETENTION_FRAMES: usize = ParticleDecoder::FPS as usize * 10;
    const MIN_PEAK_DISTANCE: f32 = 14.0;
    const MAX_PEAK_DISTANCE: f32 = 55.0;

    fn new() -> Self {
        Self {
            activations: VecDeque::with_capacity(Self::RETENTION_FRAMES),
            frames_since_refresh: 0,
            has_refreshed: false,
            tempo: None,
        }
    }

    fn push(&mut self, activations: [f32; 3]) -> Option<f32> {
        self.activations.push_back(ActivationFrame {
            beat: activations[0],
            downbeat: activations[1],
        });
        while self.activations.len() > Self::RETENTION_FRAMES {
            self.activations.pop_front();
        }

        self.frames_since_refresh += 1;
        if self.frames_since_refresh < Self::REFRESH_FRAMES {
            return None;
        }
        self.frames_since_refresh = 0;
        self.has_refreshed = true;

        let window: Vec<_> = self
            .activations
            .iter()
            .skip(self.activations.len().saturating_sub(Self::ANALYSIS_FRAMES))
            .copied()
            .collect();
        self.tempo = estimate_window_tempo(&window);
        self.tempo
    }

    fn has_refreshed(&self) -> bool {
        self.has_refreshed
    }

    fn tempo(&self) -> Option<f32> {
        self.tempo
    }

    fn reset(&mut self) {
        self.activations.clear();
        self.frames_since_refresh = 0;
        self.has_refreshed = false;
        self.tempo = None;
    }
}

fn estimate_window_tempo(activations: &[ActivationFrame]) -> Option<f32> {
    if activations.len() < RollingTempoEstimator::REFRESH_FRAMES {
        return None;
    }

    let strongest = activations
        .iter()
        .map(|activation| activation.beat.max(activation.downbeat))
        .fold(0.0_f32, f32::max);
    let threshold = (strongest * 0.4).max(0.1);
    let mut peaks: Vec<(f32, f32)> = Vec::new();
    for index in 1..activations.len().saturating_sub(1) {
        let previous = activations[index - 1]
            .beat
            .max(activations[index - 1].downbeat);
        let current = activations[index].beat.max(activations[index].downbeat);
        let next = activations[index + 1]
            .beat
            .max(activations[index + 1].downbeat);
        if current < threshold || current < previous || current <= next {
            continue;
        }

        let curvature = previous - 2.0 * current + next;
        let offset = if curvature.abs() > f32::EPSILON {
            (0.5 * (previous - next) / curvature).clamp(-0.5, 0.5)
        } else {
            0.0
        };
        let position = index as f32 + offset;

        if let Some((last_position, last_strength)) = peaks.last_mut()
            && position - *last_position < RollingTempoEstimator::MIN_PEAK_DISTANCE
        {
            if current > *last_strength {
                *last_position = position;
                *last_strength = current;
            }
            continue;
        }
        peaks.push((position, current));
    }

    let mut intervals: Vec<f32> = peaks
        .windows(2)
        .filter_map(|peaks| {
            let interval = peaks[1].0 - peaks[0].0;
            (RollingTempoEstimator::MIN_PEAK_DISTANCE..=RollingTempoEstimator::MAX_PEAK_DISTANCE)
                .contains(&interval)
                .then_some(interval)
        })
        .collect();
    if intervals.is_empty() {
        return None;
    }
    intervals.sort_by(f32::total_cmp);
    let middle = intervals.len() / 2;
    let median_interval = if intervals.len().is_multiple_of(2) {
        (intervals[middle - 1] + intervals[middle]) * 0.5
    } else {
        intervals[middle]
    };
    Some(
        (60.0 * ParticleDecoder::FPS / median_interval)
            .clamp(ParticleDecoder::MIN_TEMPO, ParticleDecoder::MAX_TEMPO),
    )
}

impl ParticleDecoder {
    const PARTICLES: usize = 1_500;
    const FPS: f32 = 50.0;
    const MIN_TEMPO: f32 = 55.0;
    const MAX_TEMPO: f32 = 215.0;

    fn new() -> Self {
        let mut decoder = Self {
            particles: Vec::with_capacity(Self::PARTICLES),
            rng: StdRng::seed_from_u64(0x4245_4154_4e45_542b),
            beat_count: 0,
            bar_count: 0,
            last_beat_probability: 0.0,
        };
        decoder.reset();
        decoder
    }

    fn reset(&mut self) {
        self.particles.clear();
        for _ in 0..Self::PARTICLES {
            self.particles.push(Particle {
                tempo: self.rng.random_range(Self::MIN_TEMPO..=Self::MAX_TEMPO),
                phase: self.rng.random(),
                beat_in_bar: self.rng.random_range(0..4),
                meter: self.rng.random_range(2..=4),
                weight: 1.0 / Self::PARTICLES as f32,
                wrapped: false,
            });
        }
        self.beat_count = 0;
        self.bar_count = 0;
        self.last_beat_probability = 0.0;
    }

    fn align_tempo(&mut self, tempo: f32) {
        let tempo = tempo.clamp(Self::MIN_TEMPO, Self::MAX_TEMPO);
        for particle in &mut self.particles {
            particle.tempo =
                (tempo + self.rng.random_range(-2.0..=2.0)).clamp(Self::MIN_TEMPO, Self::MAX_TEMPO);
        }
    }

    fn update(&mut self, beat_activation: f32, downbeat_activation: f32) -> BeatEstimate {
        let non_beat = (1.0 - beat_activation.max(downbeat_activation)).clamp(0.001, 1.0);
        let mut weight_sum = 0.0;
        let mut wrapped_weight = 0.0;
        let mut downbeat_weight = 0.0;
        for particle in &mut self.particles {
            particle.tempo = (particle.tempo + self.rng.random_range(-0.35..=0.35))
                .clamp(Self::MIN_TEMPO, Self::MAX_TEMPO);
            particle.phase += particle.tempo / 60.0 / Self::FPS;
            particle.wrapped = particle.phase >= 1.0;
            if particle.wrapped {
                particle.phase -= 1.0;
                particle.beat_in_bar = (particle.beat_in_bar + 1) % particle.meter;
            }
            let distance = particle.phase.min(1.0 - particle.phase);
            let boundary = (-0.5 * (distance / 0.055).powi(2)).exp();
            let downbeat_boundary = if particle.beat_in_bar == 0 {
                boundary
            } else {
                0.0
            };
            let likelihood = non_beat * (1.0 - boundary)
                + beat_activation * boundary
                + downbeat_activation * downbeat_boundary * 1.5
                + 1e-6;
            particle.weight *= likelihood;
            weight_sum += particle.weight;
        }
        if weight_sum <= f32::EPSILON {
            self.reset();
            weight_sum = 1.0;
        }
        for particle in &mut self.particles {
            particle.weight /= weight_sum;
            if particle.wrapped {
                wrapped_weight += particle.weight;
                if particle.beat_in_bar == 0 {
                    downbeat_weight += particle.weight;
                }
            }
        }

        let event_probability = wrapped_weight * beat_activation.max(downbeat_activation);
        let beat = event_probability > 0.12 && self.last_beat_probability <= 0.12;
        self.last_beat_probability = event_probability;
        let downbeat = beat && downbeat_weight > wrapped_weight * 0.45 && downbeat_activation > 0.1;
        if beat {
            self.beat_count += 1;
            if downbeat || self.beat_count == 1 {
                self.bar_count += 1;
            }
        }

        let effective = 1.0
            / self
                .particles
                .iter()
                .map(|particle| particle.weight.powi(2))
                .sum::<f32>();
        if effective < Self::PARTICLES as f32 * 0.5 {
            self.systematic_resample();
        }

        let tempo = self
            .particles
            .iter()
            .map(|particle| particle.tempo * particle.weight)
            .sum::<f32>();
        let phase = circular_phase(&self.particles);
        let meter = self.weighted_meter().max(2) as f32;
        let beat_in_bar = self.weighted_beat_in_bar() as f32;
        BeatEstimate {
            tempo,
            beat,
            downbeat,
            confidence: event_probability.clamp(0.0, 1.0),
            beat_position: phase,
            bar_position: (beat_in_bar + phase) / meter,
            estimated_beat: self.beat_count,
            estimated_bar: self.bar_count.saturating_sub(1),
            beat_activation,
            downbeat_activation,
        }
    }

    fn systematic_resample(&mut self) {
        let step = 1.0 / Self::PARTICLES as f32;
        let start = self.rng.random::<f32>() * step;
        let mut cumulative = self.particles[0].weight;
        let mut source = 0;
        let mut resampled = Vec::with_capacity(Self::PARTICLES);
        for index in 0..Self::PARTICLES {
            let target = start + index as f32 * step;
            while target > cumulative && source + 1 < self.particles.len() {
                source += 1;
                cumulative += self.particles[source].weight;
            }
            let mut particle = self.particles[source];
            particle.weight = step;
            particle.wrapped = false;
            resampled.push(particle);
        }
        self.particles = resampled;
    }

    fn weighted_meter(&self) -> u8 {
        (2..=4)
            .max_by(|first, second| {
                let weight = |meter| {
                    self.particles
                        .iter()
                        .filter(|particle| particle.meter == meter)
                        .map(|particle| particle.weight)
                        .sum::<f32>()
                };
                weight(*first).total_cmp(&weight(*second))
            })
            .unwrap_or(4)
    }

    fn weighted_beat_in_bar(&self) -> u8 {
        (0..self.weighted_meter())
            .max_by(|first, second| {
                let weight = |beat| {
                    self.particles
                        .iter()
                        .filter(|particle| particle.beat_in_bar == beat)
                        .map(|particle| particle.weight)
                        .sum::<f32>()
                };
                weight(*first).total_cmp(&weight(*second))
            })
            .unwrap_or(0)
    }
}

fn circular_phase(particles: &[Particle]) -> f32 {
    let (sine, cosine) = particles
        .iter()
        .fold((0.0, 0.0), |(sine, cosine), particle| {
            let angle = particle.phase * std::f32::consts::TAU;
            (
                sine + angle.sin() * particle.weight,
                cosine + angle.cos() * particle.weight,
            )
        });
    sine.atan2(cosine).rem_euclid(std::f32::consts::TAU) / std::f32::consts::TAU
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn feature_bank_matches_beatnet_plus_dimension() {
        assert_eq!(logarithmic_filterbank().len(), FEATURE_BANDS);
    }

    #[test]
    fn feature_extractor_emits_at_twenty_millisecond_hops() {
        let mut extractor = FeatureExtractor::new().expect("feature extractor builds");
        let frames = extractor
            .push(&vec![0.0; WINDOW_SIZE + HOP_SIZE * 2])
            .expect("silence processes");
        assert_eq!(frames.len(), 3);
        assert!(frames.iter().flatten().all(|value| *value == 0.0));
    }

    #[test]
    fn softmax_is_normalized() {
        let probabilities = softmax3([1.0, 2.0, 3.0]);
        approx::assert_abs_diff_eq!(probabilities.iter().sum::<f32>(), 1.0, epsilon = 1e-6);
    }

    #[test]
    fn batched_frames_preserve_transient_beat_events() {
        let previous = BeatEstimate {
            beat: true,
            downbeat: true,
            confidence: 0.8,
            beat_activation: 0.9,
            downbeat_activation: 0.7,
            ..Default::default()
        };
        let latest = BeatEstimate {
            tempo: 128.0,
            beat_position: 0.12,
            bar_position: 0.03,
            estimated_beat: 12,
            estimated_bar: 3,
            confidence: 0.1,
            beat_activation: 0.2,
            downbeat_activation: 0.05,
            ..Default::default()
        };

        let merged = merge_frame_estimate(Some(previous), latest);

        assert!(merged.beat);
        assert!(merged.downbeat);
        assert_eq!(merged.confidence, 0.8);
        assert_eq!(merged.beat_activation, 0.9);
        assert_eq!(merged.downbeat_activation, 0.7);
        assert_eq!(merged.tempo, 128.0);
        assert_eq!(merged.beat_position, 0.12);
        assert_eq!(merged.estimated_beat, 12);
    }

    #[test]
    fn rolling_window_relearns_a_changed_tempo() {
        let mut rolling = RollingTempoEstimator::new();
        let mut phase = 0.0;
        let mut beat_count = 0_u64;

        let initial = feed_synthetic_tempo(
            &mut rolling,
            &mut phase,
            &mut beat_count,
            80.0,
            ParticleDecoder::FPS as usize * 16,
        );
        let initial_tempo = *initial
            .last()
            .expect("the initial rolling window should produce an estimate");
        approx::assert_abs_diff_eq!(initial_tempo, 80.0, epsilon = 5.0);

        let changed = feed_synthetic_tempo(
            &mut rolling,
            &mut phase,
            &mut beat_count,
            120.0,
            ParticleDecoder::FPS as usize * 10,
        );
        let changed_tempo = *changed
            .last()
            .expect("the changed rolling window should produce an estimate");
        approx::assert_abs_diff_eq!(changed_tempo, 120.0, epsilon = 5.0);
        assert!(rolling.activations.len() <= RollingTempoEstimator::RETENTION_FRAMES);
    }

    #[test]
    fn rolling_window_refreshes_every_two_seconds_and_forgets_silence() {
        let mut rolling = RollingTempoEstimator::new();
        let mut phase = 0.0;
        let mut beat_count = 0_u64;

        feed_synthetic_tempo(
            &mut rolling,
            &mut phase,
            &mut beat_count,
            120.0,
            RollingTempoEstimator::REFRESH_FRAMES - 1,
        );
        assert!(!rolling.has_refreshed());

        rolling.push([0.01, 0.01, 0.98]);
        assert!(rolling.has_refreshed());
        assert!(rolling.tempo().is_some());

        for _ in 0..RollingTempoEstimator::RETENTION_FRAMES {
            rolling.push([0.01, 0.01, 0.98]);
        }
        assert_eq!(rolling.tempo(), None);
    }

    #[test]
    fn rolling_tempo_recenters_live_particles() {
        let mut decoder = ParticleDecoder::new();
        decoder.align_tempo(120.0);

        let tempo = decoder
            .particles
            .iter()
            .map(|particle| particle.tempo * particle.weight)
            .sum::<f32>();
        approx::assert_abs_diff_eq!(tempo, 120.0, epsilon = 0.2);
    }

    fn feed_synthetic_tempo(
        rolling: &mut RollingTempoEstimator,
        phase: &mut f32,
        beat_count: &mut u64,
        tempo: f32,
        frames: usize,
    ) -> Vec<f32> {
        let mut estimates = Vec::new();
        for _ in 0..frames {
            *phase += tempo / 60.0 / ParticleDecoder::FPS;
            let beat = *phase >= 1.0;
            if beat {
                *phase -= 1.0;
                *beat_count += 1;
            }
            let downbeat = beat && (*beat_count - 1).is_multiple_of(4);
            let activations = [
                if beat { 0.95 } else { 0.02 },
                if downbeat { 0.9 } else { 0.01 },
                if beat { 0.04 } else { 0.97 },
            ];
            if let Some(tempo) = rolling.push(activations) {
                estimates.push(tempo);
            }
        }
        estimates
    }
}
