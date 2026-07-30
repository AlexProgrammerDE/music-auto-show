use std::{
    array,
    collections::VecDeque,
    path::{Path, PathBuf},
    sync::{Arc, Mutex, OnceLock},
    time::SystemTime,
};

use candle_core::{DType, Device, Shape};
use candle_nn::VarBuilder;
use rand::{Rng, SeedableRng, rngs::StdRng};
use realfft::{RealFftPlanner, RealToComplex, num_complex::Complex};
use thiserror::Error;

const SAMPLE_RATE: usize = 22_050;
const WINDOW_SIZE: usize = 1_764;
const HOP_SIZE: usize = 441;
const FEATURE_BANDS: usize = 144;
const FEATURE_SIZE: usize = FEATURE_BANDS * 2;
const HIDDEN_SIZE: usize = 150;
const LSTM_GATES: usize = HIDDEN_SIZE * 4;
const LSTM_LAYERS: usize = 4;
const MAX_CHECKPOINT_BYTES: u64 = 64 * 1024 * 1024;

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
    pub meter: u8,
    pub beat_index: u8,
    pub estimated_beat: u64,
    pub estimated_bar: u64,
    pub beat_activation: f32,
    pub downbeat_activation: f32,
    pub tracking_confidence: f32,
}

/// Fully causal BeatNet+ inference pipeline.
///
/// The neural network topology and 288-dimensional log-spectrogram features
/// match the official model. A two-stage causal particle filter jointly tracks
/// beat phase, tempo, bar position, and meter without requiring Python or
/// madmom.
pub struct BeatNetPlus {
    model_path: PathBuf,
    feature_extractor: FeatureExtractor,
    network: BeatNetNetwork,
    decoder: ParticleDecoder,
}

impl BeatNetPlus {
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
            let estimate = self.decoder.update(activations[0], activations[1]);
            latest = Some(merge_frame_estimate(latest, estimate));
        }
        Ok(latest)
    }

    pub fn reset(&mut self) {
        self.feature_extractor.reset();
        self.network.reset();
        self.decoder.reset();
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
    fft_input: Vec<f32>,
    fft_output: Vec<Complex<f32>>,
    magnitudes: Vec<f32>,
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
        let fft = planner.plan_fft_forward(WINDOW_SIZE);
        let fft_input = fft.make_input_vec();
        let fft_output = fft.make_output_vec();
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
            fft,
            fft_input,
            fft_output,
            magnitudes: vec![0.0; WINDOW_SIZE / 2],
        })
    }

    fn push(&mut self, samples: &[f32]) -> Result<Vec<[f32; FEATURE_SIZE]>, BeatNetError> {
        let mut frames = Vec::with_capacity(samples.len() / HOP_SIZE + 1);
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
        for ((input, sample), window) in self
            .fft_input
            .iter_mut()
            .zip(&self.samples)
            .zip(&self.window)
        {
            *input = sample * window;
        }
        self.fft
            .process(&mut self.fft_input, &mut self.fft_output)
            .map_err(|error| BeatNetError::FeatureExtraction(error.to_string()))?;
        // madmom excludes the Nyquist bin for an even-sized real FFT.
        for (magnitude, value) in self.magnitudes.iter_mut().zip(&self.fft_output) {
            *magnitude = value.norm();
        }
        let mut log_bands = [0.0_f32; FEATURE_BANDS];
        for (index, filter) in self.filters.iter().enumerate() {
            let filtered = filter
                .weights
                .iter()
                .enumerate()
                .map(|(offset, weight)| self.magnitudes[filter.start + offset] * weight)
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
    weights: Arc<BeatNetWeights>,
    hidden: [Vec<f32>; LSTM_LAYERS],
    cell: [Vec<f32>; LSTM_LAYERS],
}

struct BeatNetWeights {
    conv_weight: Vec<f32>,
    conv_bias: Vec<f32>,
    input_weight: Vec<f32>,
    input_bias: Vec<f32>,
    lstm: [LstmWeights; LSTM_LAYERS],
    output_weight: Vec<f32>,
    output_bias: Vec<f32>,
}

struct CachedBeatNetWeights {
    key: BeatNetCacheKey,
    weights: Arc<BeatNetWeights>,
}

#[derive(Eq, PartialEq)]
struct BeatNetCacheKey {
    path: PathBuf,
    length: u64,
    modified: Option<SystemTime>,
}

static BEATNET_WEIGHTS_CACHE: OnceLock<Mutex<Option<CachedBeatNetWeights>>> = OnceLock::new();

struct LstmWeights {
    input: Vec<f32>,
    recurrent: Vec<f32>,
    input_bias: Vec<f32>,
    recurrent_bias: Vec<f32>,
}

impl BeatNetNetwork {
    fn load(path: &Path) -> Result<Self, BeatNetError> {
        let weights = load_cached_weights(path)?;
        Ok(Self {
            weights,
            hidden: array::from_fn(|_| vec![0.0; HIDDEN_SIZE]),
            cell: array::from_fn(|_| vec![0.0; HIDDEN_SIZE]),
        })
    }

    fn infer(&mut self, features: &[f32; FEATURE_SIZE]) -> [f32; 3] {
        let weights = Arc::clone(&self.weights);
        let conv_length = FEATURE_SIZE - 10 + 1;
        let pooled_length = conv_length / 2;
        let mut pooled = [0.0_f32; 278];
        for channel in 0..2 {
            for output in 0..pooled_length {
                let conv = |position: usize| {
                    let value = (0..10)
                        .map(|kernel| {
                            features[position + kernel] * weights.conv_weight[channel * 10 + kernel]
                        })
                        .sum::<f32>()
                        + weights.conv_bias[channel];
                    value.max(0.0)
                };
                pooled[channel * pooled_length + output] =
                    conv(output * 2).max(conv(output * 2 + 1));
            }
        }

        let mut current = [0.0_f32; HIDDEN_SIZE];
        dense_into(
            &pooled,
            &weights.input_weight,
            &weights.input_bias,
            &mut current,
        );
        for layer in 0..LSTM_LAYERS {
            let layer_weights = &weights.lstm[layer];
            let mut gates = [0.0_f32; LSTM_GATES];
            for (gate, value) in gates.iter_mut().enumerate() {
                let input_sum = dot_row(&layer_weights.input, gate, HIDDEN_SIZE, &current);
                let hidden_sum = dot_row(
                    &layer_weights.recurrent,
                    gate,
                    HIDDEN_SIZE,
                    &self.hidden[layer],
                );
                *value = input_sum
                    + hidden_sum
                    + layer_weights.input_bias[gate]
                    + layer_weights.recurrent_bias[gate];
            }
            let mut next_hidden = [0.0; HIDDEN_SIZE];
            let mut next_cell = [0.0; HIDDEN_SIZE];
            for index in 0..HIDDEN_SIZE {
                let input_gate = sigmoid(gates[index]);
                let forget_gate = sigmoid(gates[HIDDEN_SIZE + index]);
                let candidate = gates[HIDDEN_SIZE * 2 + index].tanh();
                let output_gate = sigmoid(gates[HIDDEN_SIZE * 3 + index]);
                next_cell[index] = forget_gate * self.cell[layer][index] + input_gate * candidate;
                next_hidden[index] = output_gate * next_cell[index].tanh();
            }
            self.cell[layer].copy_from_slice(&next_cell);
            self.hidden[layer].copy_from_slice(&next_hidden);
            current = next_hidden;
        }
        let mut logits = [0.0; 3];
        dense_into(
            &current,
            &weights.output_weight,
            &weights.output_bias,
            &mut logits,
        );
        softmax3([logits[0], logits[1], logits[2]])
    }

    fn reset(&mut self) {
        for layer in 0..LSTM_LAYERS {
            self.hidden[layer].fill(0.0);
            self.cell[layer].fill(0.0);
        }
    }
}

fn load_cached_weights(path: &Path) -> Result<Arc<BeatNetWeights>, BeatNetError> {
    let metadata = path
        .metadata()
        .map_err(|error| BeatNetError::InvalidCheckpoint(error.to_string()))?;
    let key = BeatNetCacheKey {
        path: path.canonicalize().unwrap_or_else(|_| path.to_owned()),
        length: metadata.len(),
        modified: metadata.modified().ok(),
    };
    let cache = BEATNET_WEIGHTS_CACHE.get_or_init(|| Mutex::new(None));
    let mut cache = match cache.lock() {
        Ok(cache) => cache,
        Err(poisoned) => poisoned.into_inner(),
    };
    if let Some(cached) = cache.as_ref()
        && key.modified.is_some()
        && cached.key == key
    {
        return Ok(Arc::clone(&cached.weights));
    }
    let weights = Arc::new(load_weights(path)?);
    *cache = Some(CachedBeatNetWeights {
        key,
        weights: Arc::clone(&weights),
    });
    Ok(weights)
}

fn load_weights(path: &Path) -> Result<BeatNetWeights, BeatNetError> {
    let checkpoint_size = path
        .metadata()
        .map_err(|error| BeatNetError::InvalidCheckpoint(error.to_string()))?
        .len();
    if checkpoint_size > MAX_CHECKPOINT_BYTES {
        return Err(BeatNetError::InvalidCheckpoint(format!(
            "checkpoint is {checkpoint_size} bytes; maximum supported size is {MAX_CHECKPOINT_BYTES} bytes"
        )));
    }
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
            recurrent_bias: load_tensor(&builder, LSTM_GATES, &format!("lstm.bias_hh_l{layer}"))?,
        });
    }
    let lstm: [LstmWeights; LSTM_LAYERS] = lstm
        .try_into()
        .map_err(|_| BeatNetError::InvalidCheckpoint("wrong LSTM layer count".into()))?;
    Ok(BeatNetWeights {
        conv_weight: load_tensor(&builder, (2, 1, 10), "conv1.weight")?,
        conv_bias: load_tensor(&builder, 2, "conv1.bias")?,
        input_weight: load_tensor(&builder, (HIDDEN_SIZE, 278), "linear0.weight")?,
        input_bias: load_tensor(&builder, HIDDEN_SIZE, "linear0.bias")?,
        lstm,
        output_weight: load_tensor(&builder, (3, HIDDEN_SIZE), "output_linear.weight")?,
        output_bias: load_tensor(&builder, 3, "output_linear.bias")?,
    })
}

fn load_tensor<S: Into<Shape>>(
    builder: &VarBuilder<'_>,
    shape: S,
    name: &str,
) -> Result<Vec<f32>, BeatNetError> {
    let values = builder
        .get(shape, name)
        .and_then(|tensor| tensor.flatten_all()?.to_vec1::<f32>())
        .map_err(|error| BeatNetError::InvalidCheckpoint(format!("{name}: {error}")))?;
    if values.iter().any(|value| !value.is_finite()) {
        return Err(BeatNetError::InvalidCheckpoint(format!(
            "{name}: tensor contains a non-finite value"
        )));
    }
    Ok(values)
}

fn dense_into(input: &[f32], weight: &[f32], bias: &[f32], output: &mut [f32]) {
    for (row, value) in output.iter_mut().enumerate() {
        *value = dot_row(weight, row, input.len(), input) + bias[row];
    }
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
    weight: f32,
    wrapped: bool,
}

#[derive(Clone, Copy)]
struct MeterParticle {
    beat_in_bar: u8,
    meter: u8,
    weight: f32,
}

#[derive(Clone, Copy, Default)]
struct TempoCluster {
    bpm: f32,
    confidence: f32,
}

#[derive(Clone, Copy)]
struct MeterPosterior {
    beat_probabilities: [[f32; 4]; 3],
    probabilities: [f32; 3],
}

impl MeterPosterior {
    fn meter_probability(&self, meter: u8) -> f32 {
        self.probabilities[usize::from(meter.clamp(2, 4) - 2)]
    }

    fn strongest_meter(&self) -> u8 {
        self.probabilities
            .iter()
            .enumerate()
            .max_by(|(_, first), (_, second)| first.total_cmp(second))
            .map_or(4, |(index, _)| index as u8 + 2)
    }

    fn strongest_beat(&self, meter: u8) -> u8 {
        let meter = meter.clamp(2, 4);
        self.beat_probabilities[usize::from(meter - 2)][..usize::from(meter)]
            .iter()
            .enumerate()
            .max_by(|(_, first), (_, second)| first.total_cmp(second))
            .map_or(0, |(index, _)| index as u8)
    }

    fn beat_confidence(&self, meter: u8) -> f32 {
        let meter = meter.clamp(2, 4);
        let meter_probability = self.meter_probability(meter);
        if meter_probability <= f32::EPSILON {
            return 0.0;
        }
        self.beat_probabilities[usize::from(meter - 2)][..usize::from(meter)]
            .iter()
            .copied()
            .fold(0.0, f32::max)
            / meter_probability
    }
}

struct TempoCommitment {
    candidate: Option<f32>,
    candidate_beats: u8,
    published: Option<f32>,
}

impl TempoCommitment {
    fn new() -> Self {
        Self {
            candidate: None,
            candidate_beats: 0,
            published: None,
        }
    }

    fn reset(&mut self) {
        self.candidate = None;
        self.candidate_beats = 0;
        self.published = None;
    }

    fn update(&mut self, cluster: TempoCluster) {
        const MIN_ACQUISITION_CONFIDENCE: f32 = 0.18;
        const LARGE_CHANGE_RATIO: f32 = 0.08;
        const LARGE_CHANGE_CONFIDENCE: f32 = 0.52;
        const LARGE_CHANGE_CONFIRMATION_BEATS: u8 = 4;
        const CANDIDATE_MATCH_RATIO: f32 = 0.04;
        const MAX_CHANGE_PER_BEAT: f32 = 0.02;
        const SMALL_CHANGE_GAIN: f32 = 0.35;
        const TEMPO_DEADBAND_RATIO: f32 = 0.0035;

        if cluster.bpm <= 0.0 || cluster.confidence < MIN_ACQUISITION_CONFIDENCE {
            return;
        }
        let Some(current) = self.published else {
            self.published = Some(cluster.bpm);
            return;
        };
        let relative_change = ((cluster.bpm / current) - 1.0).abs();
        if relative_change <= LARGE_CHANGE_RATIO {
            self.candidate = None;
            self.candidate_beats = 0;
            if relative_change <= TEMPO_DEADBAND_RATIO {
                return;
            }
            let maximum_step = (current * MAX_CHANGE_PER_BEAT).max(0.25);
            self.published = Some(
                current
                    + ((cluster.bpm - current) * SMALL_CHANGE_GAIN)
                        .clamp(-maximum_step, maximum_step),
            );
            return;
        }
        if cluster.confidence < LARGE_CHANGE_CONFIDENCE {
            self.candidate = None;
            self.candidate_beats = 0;
            return;
        }
        let matches_candidate = self.candidate.is_some_and(|candidate| {
            ((cluster.bpm / candidate) - 1.0).abs() <= CANDIDATE_MATCH_RATIO
        });
        if matches_candidate {
            self.candidate_beats = self.candidate_beats.saturating_add(1);
            self.candidate = Some(self.candidate.map_or(cluster.bpm, |candidate| {
                candidate + (cluster.bpm - candidate) * 0.25
            }));
        } else {
            self.candidate = Some(cluster.bpm);
            self.candidate_beats = 1;
        }
        if self.candidate_beats >= LARGE_CHANGE_CONFIRMATION_BEATS {
            self.published = self.candidate;
            self.candidate = None;
            self.candidate_beats = 0;
        }
    }

    fn tempo(&self) -> f32 {
        self.published.unwrap_or(0.0)
    }
}

struct MeterCommitment {
    beat_in_bar: u8,
    candidate: Option<u8>,
    candidate_beats: u8,
    locked: bool,
    meter: u8,
    observed_beats: u16,
}

impl MeterCommitment {
    fn new() -> Self {
        Self {
            beat_in_bar: 0,
            candidate: None,
            candidate_beats: 0,
            locked: false,
            meter: 4,
            observed_beats: 0,
        }
    }

    fn reset(&mut self) {
        *self = Self::new();
    }

    fn advance(&mut self, posterior: MeterPosterior) {
        const ALIGNMENT_CONFIDENCE: f32 = 0.8;
        const SWITCH_PROBABILITY: f32 = 0.65;
        const SWITCH_ODDS: f32 = 4.0;

        self.observed_beats = self.observed_beats.saturating_add(1);
        self.beat_in_bar = (self.beat_in_bar + 1) % self.meter;
        if posterior.meter_probability(self.meter) >= 0.2
            && posterior.beat_confidence(self.meter) >= ALIGNMENT_CONFIDENCE
        {
            self.beat_in_bar = posterior.strongest_beat(self.meter);
        }

        let strongest = posterior.strongest_meter();
        let strongest_probability = posterior.meter_probability(strongest);
        let current_probability = posterior.meter_probability(self.meter);
        if strongest == self.meter {
            self.candidate = None;
            self.candidate_beats = 0;
            if !self.locked
                && strongest_probability >= SWITCH_PROBABILITY
                && self.observed_beats >= u16::from(self.meter) * 2
            {
                self.locked = true;
            }
            return;
        }

        let has_switch_evidence = strongest_probability >= SWITCH_PROBABILITY
            && strongest_probability >= current_probability.max(f32::EPSILON) * SWITCH_ODDS;
        if !has_switch_evidence {
            self.candidate = None;
            self.candidate_beats = 0;
            return;
        }
        if self.candidate == Some(strongest) {
            self.candidate_beats = self.candidate_beats.saturating_add(1);
        } else {
            self.candidate = Some(strongest);
            self.candidate_beats = 1;
        }
        if self.candidate_beats < strongest * 2 || posterior.strongest_beat(strongest) != 0 {
            return;
        }

        self.meter = strongest;
        self.beat_in_bar = posterior.strongest_beat(strongest);
        self.candidate = None;
        self.candidate_beats = 0;
        self.locked = true;
    }

    fn confidence(&self, posterior: MeterPosterior) -> f32 {
        let confidence = posterior.meter_probability(self.meter);
        if self.locked {
            confidence
        } else {
            confidence * 0.5
        }
    }
}

struct ParticleDecoder {
    particles: Vec<Particle>,
    meter_particles: Vec<MeterParticle>,
    rng: StdRng,
    tempo: TempoCommitment,
    meter: MeterCommitment,
    beat_count: u64,
    bar_count: u64,
    contradictory_onsets: u8,
    frames_since_contradictory_onset: usize,
    frames_since_grid_boundary: usize,
    last_beat_probability: f32,
    last_phase: Option<f32>,
    onset_armed: bool,
    recent_beat_peak: f32,
    recent_downbeat_peak: f32,
}

impl ParticleDecoder {
    const PARTICLES: usize = 1_500;
    const METER_PARTICLES: usize = 250;
    const FPS: f32 = 50.0;
    const MIN_TEMPO: f32 = 55.0;
    const MAX_TEMPO: f32 = 215.0;
    const CONTRADICTORY_ONSETS_TO_REACQUIRE: u8 = 3;
    const CONTRADICTION_RETENTION_FRAMES: usize = Self::FPS as usize * 4;
    const MIN_CONTRADICTORY_ONSET_FRAMES: usize = 8;
    const MIN_GRID_BOUNDARY_FRAMES: usize = 6;

    fn new() -> Self {
        let mut decoder = Self {
            particles: Vec::with_capacity(Self::PARTICLES),
            meter_particles: Vec::with_capacity(Self::METER_PARTICLES),
            rng: StdRng::seed_from_u64(0x4245_4154_4e45_542b),
            tempo: TempoCommitment::new(),
            meter: MeterCommitment::new(),
            beat_count: 0,
            bar_count: 0,
            contradictory_onsets: 0,
            frames_since_contradictory_onset: Self::MIN_CONTRADICTORY_ONSET_FRAMES,
            frames_since_grid_boundary: Self::MIN_GRID_BOUNDARY_FRAMES,
            last_beat_probability: 0.0,
            last_phase: None,
            onset_armed: true,
            recent_beat_peak: 0.0,
            recent_downbeat_peak: 0.0,
        };
        decoder.reset();
        decoder
    }

    fn reset(&mut self) {
        self.reset_tracking_state();
    }

    fn reset_tracking_state(&mut self) {
        self.seed_beat_hypotheses(None, None);
        self.meter_particles.clear();
        let meter_counts = [
            (0..Self::METER_PARTICLES)
                .filter(|index| index % 3 == 0)
                .count(),
            (0..Self::METER_PARTICLES)
                .filter(|index| index % 3 == 1)
                .count(),
            (0..Self::METER_PARTICLES)
                .filter(|index| index % 3 == 2)
                .count(),
        ];
        for index in 0..Self::METER_PARTICLES {
            let meter_index = index % 3;
            let meter = meter_index as u8 + 2;
            self.meter_particles.push(MeterParticle {
                beat_in_bar: self.rng.random_range(0..meter),
                meter,
                weight: 1.0 / 3.0 / meter_counts[meter_index] as f32,
            });
        }
        self.tempo.reset();
        self.meter.reset();
        self.beat_count = 0;
        self.bar_count = 0;
        self.contradictory_onsets = 0;
        self.frames_since_contradictory_onset = Self::MIN_CONTRADICTORY_ONSET_FRAMES;
        self.frames_since_grid_boundary = Self::MIN_GRID_BOUNDARY_FRAMES;
        self.last_beat_probability = 0.0;
        self.last_phase = None;
        self.onset_armed = true;
        self.recent_beat_peak = 0.0;
        self.recent_downbeat_peak = 0.0;
    }

    fn seed_beat_hypotheses(&mut self, retained_tempo: Option<f32>, phase_anchor: Option<f32>) {
        const RETAINED_TEMPO_FRACTION: f32 = 0.8;
        const RETAINED_TEMPO_JITTER: f32 = 0.035;
        const PHASE_JITTER: f32 = 0.03;

        self.particles.clear();
        let retained_particles = (Self::PARTICLES as f32 * RETAINED_TEMPO_FRACTION) as usize;
        for index in 0..Self::PARTICLES {
            let tempo = if index < retained_particles {
                retained_tempo
                    .filter(|tempo| *tempo > 0.0 && tempo.is_finite())
                    .map(|tempo| {
                        (tempo
                            * (1.0
                                + self
                                    .rng
                                    .random_range(-RETAINED_TEMPO_JITTER..=RETAINED_TEMPO_JITTER)))
                        .clamp(Self::MIN_TEMPO, Self::MAX_TEMPO)
                    })
                    .unwrap_or_else(|| random_log_tempo(&mut self.rng))
            } else {
                random_log_tempo(&mut self.rng)
            };
            let phase = phase_anchor
                .filter(|phase| phase.is_finite())
                .map(|phase| {
                    (phase + self.rng.random_range(-PHASE_JITTER..=PHASE_JITTER)).rem_euclid(1.0)
                })
                .unwrap_or_else(|| self.rng.random());
            self.particles.push(Particle {
                tempo,
                phase,
                weight: 1.0 / Self::PARTICLES as f32,
                wrapped: false,
            });
        }
    }

    fn reacquire_beat_hypotheses(&mut self, phase_anchor: f32) {
        let retained_tempo = (self.tempo.tempo() > 0.0).then(|| self.tempo.tempo());
        self.seed_beat_hypotheses(retained_tempo, Some(phase_anchor));
        self.contradictory_onsets = 0;
        self.frames_since_contradictory_onset = 0;
        self.frames_since_grid_boundary = 0;
        self.last_beat_probability = 0.0;
        self.last_phase = None;
        self.recent_beat_peak = 0.0;
        self.recent_downbeat_peak = 0.0;
    }

    #[cfg(test)]
    fn seed_tempo(&mut self, tempo: f32) {
        let tempo = tempo.clamp(Self::MIN_TEMPO, Self::MAX_TEMPO);
        for particle in &mut self.particles {
            particle.tempo =
                (tempo + self.rng.random_range(-0.5..=0.5)).clamp(Self::MIN_TEMPO, Self::MAX_TEMPO);
        }
    }

    fn update(&mut self, beat_activation: f32, downbeat_activation: f32) -> BeatEstimate {
        let beat_activation = finite_activation(beat_activation);
        let downbeat_activation = finite_activation(downbeat_activation);
        let event_activation = beat_activation.max(downbeat_activation);
        let strong_onset = self.onset_armed && event_activation > 0.45;
        if event_activation <= 0.2 {
            self.onset_armed = true;
        } else if strong_onset {
            self.onset_armed = false;
        }
        self.frames_since_contradictory_onset =
            self.frames_since_contradictory_onset.saturating_add(1);

        self.propagate_beat_particles();
        let predictive_boundary = self
            .particles
            .iter()
            .map(|particle| {
                let distance = particle.phase.min(1.0 - particle.phase);
                particle.weight * (-0.5 * (distance / 0.055).powi(2)).exp()
            })
            .sum::<f32>();
        if strong_onset
            && phase_consistency(&self.particles) > 0.55
            && predictive_boundary < 0.04
            && self.frames_since_contradictory_onset >= Self::MIN_CONTRADICTORY_ONSET_FRAMES
        {
            if self.frames_since_contradictory_onset > Self::CONTRADICTION_RETENTION_FRAMES {
                self.contradictory_onsets = 0;
            }
            self.contradictory_onsets = self.contradictory_onsets.saturating_add(1);
            self.frames_since_contradictory_onset = 0;
            if self.contradictory_onsets >= Self::CONTRADICTORY_ONSETS_TO_REACQUIRE {
                self.reacquire_beat_hypotheses(0.0);
            }
        } else if strong_onset && predictive_boundary >= 0.1 {
            self.contradictory_onsets = 0;
        }

        let mut weight_sum = 0.0;
        let mut wrapped_weight = 0.0;
        for particle in &mut self.particles {
            let distance = particle.phase.min(1.0 - particle.phase);
            let boundary = (-0.5 * (distance / 0.055).powi(2)).exp();
            let likelihood =
                beat_observation_likelihood(beat_activation, downbeat_activation, boundary);
            particle.weight *= likelihood;
            weight_sum += particle.weight;
        }
        if !weight_sum.is_finite() || weight_sum <= f32::EPSILON {
            self.reacquire_beat_hypotheses(self.last_phase.unwrap_or(0.0));
            weight_sum = 1.0;
        }
        for particle in &mut self.particles {
            particle.weight /= weight_sum;
            if particle.wrapped {
                wrapped_weight += particle.weight;
            }
        }

        let event_probability = wrapped_weight * beat_activation.max(downbeat_activation);
        let beat = event_probability > 0.12 && self.last_beat_probability <= 0.12;
        self.last_beat_probability = event_probability;

        let effective = 1.0
            / self
                .particles
                .iter()
                .map(|particle| particle.weight.powi(2))
                .sum::<f32>();
        if effective < Self::PARTICLES as f32 * 0.5 {
            self.systematic_resample();
        }

        let phase = circular_phase(&self.particles);
        let phase_lock = phase_consistency(&self.particles);
        self.frames_since_grid_boundary = self.frames_since_grid_boundary.saturating_add(1);
        let grid_boundary = self
            .last_phase
            .is_some_and(|previous| previous > 0.75 && phase < 0.25)
            && self.frames_since_grid_boundary >= Self::MIN_GRID_BOUNDARY_FRAMES
            && phase_lock > 0.35;
        self.last_phase = Some(phase);
        self.recent_beat_peak = beat_activation.max(self.recent_beat_peak * 0.82);
        self.recent_downbeat_peak = downbeat_activation.max(self.recent_downbeat_peak * 0.82);
        if grid_boundary {
            self.frames_since_grid_boundary = 0;
            let meter_posterior =
                self.advance_meter_particles(self.recent_beat_peak, self.recent_downbeat_peak);
            self.meter.advance(meter_posterior);
            self.tempo.update(dominant_tempo_cluster(&self.particles));
            self.beat_count = self.beat_count.saturating_add(1);
            if self.meter.beat_in_bar == 0 {
                self.bar_count = self.bar_count.saturating_add(1);
            }
            self.recent_beat_peak = beat_activation;
            self.recent_downbeat_peak = downbeat_activation;
        }

        let meter_posterior = meter_posterior(&self.meter_particles);
        let downbeat = beat
            && self.meter.beat_in_bar == 0
            && downbeat_activation > beat_activation
            && downbeat_activation > 0.1;
        let tempo = self.tempo.tempo();
        let tempo_confidence = tempo_cluster_confidence(&self.particles, tempo);
        let meter_confidence = self.meter.confidence(meter_posterior);
        let tracking_confidence =
            (phase_lock * (tempo_confidence * 0.4 + meter_confidence * 0.6)).clamp(0.0, 1.0);
        BeatEstimate {
            tempo,
            beat,
            downbeat,
            confidence: event_probability.clamp(0.0, 1.0),
            beat_position: phase,
            bar_position: (self.meter.beat_in_bar as f32 + phase) / self.meter.meter as f32,
            meter: self.meter.meter,
            beat_index: self.meter.beat_in_bar + 1,
            estimated_beat: self.beat_count,
            estimated_bar: self.bar_count.saturating_sub(1),
            beat_activation,
            downbeat_activation,
            tracking_confidence,
        }
    }

    fn propagate_beat_particles(&mut self) {
        let rng = &mut self.rng;
        for particle in &mut self.particles {
            particle.phase += particle.tempo / 60.0 / Self::FPS;
            particle.wrapped = particle.phase >= 1.0;
            if particle.wrapped {
                particle.phase -= 1.0;
                transition_tempo(particle, rng);
            }
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

    fn advance_meter_particles(
        &mut self,
        beat_activation: f32,
        downbeat_activation: f32,
    ) -> MeterPosterior {
        const METER_CHANGE_PROBABILITY: f32 = 0.005;
        let mut weight_sum = 0.0;
        for particle in &mut self.meter_particles {
            particle.beat_in_bar = (particle.beat_in_bar + 1) % particle.meter;
            if particle.beat_in_bar == 0 && self.rng.random::<f32>() < METER_CHANGE_PROBABILITY {
                let alternative = self.rng.random_range(0..2);
                particle.meter = match (particle.meter, alternative) {
                    (2, 0) | (3, 0) => 4,
                    (2, _) | (4, 0) => 3,
                    (3, _) | (4, _) => 2,
                    _ => 4,
                };
                particle.beat_in_bar = 0;
            }
            let likelihood = meter_observation_likelihood(
                beat_activation,
                downbeat_activation,
                1.0,
                particle.beat_in_bar == 0,
            )
            .max(0.003);
            particle.weight *= likelihood;
            weight_sum += particle.weight;
        }
        if weight_sum <= f32::EPSILON {
            let weight = 1.0 / Self::METER_PARTICLES as f32;
            for particle in &mut self.meter_particles {
                particle.weight = weight;
            }
        } else {
            for particle in &mut self.meter_particles {
                particle.weight /= weight_sum;
            }
        }

        let effective = 1.0
            / self
                .meter_particles
                .iter()
                .map(|particle| particle.weight.powi(2))
                .sum::<f32>();
        if effective < Self::METER_PARTICLES as f32 * 0.65 {
            self.systematic_meter_resample();
        }
        meter_posterior(&self.meter_particles)
    }

    fn systematic_meter_resample(&mut self) {
        let step = 1.0 / Self::METER_PARTICLES as f32;
        let start = self.rng.random::<f32>() * step;
        let mut cumulative = self.meter_particles[0].weight;
        let mut source = 0;
        let mut resampled = Vec::with_capacity(Self::METER_PARTICLES);
        for index in 0..Self::METER_PARTICLES {
            let target = start + index as f32 * step;
            while target > cumulative && source + 1 < self.meter_particles.len() {
                source += 1;
                cumulative += self.meter_particles[source].weight;
            }
            let mut particle = self.meter_particles[source];
            particle.weight = step;
            resampled.push(particle);
        }
        self.meter_particles = resampled;
    }
}

fn beat_observation_likelihood(
    beat_activation: f32,
    downbeat_activation: f32,
    boundary: f32,
) -> f32 {
    let non_beat = (1.0 - beat_activation - downbeat_activation).clamp(0.001, 1.0);
    let event = beat_activation.max(downbeat_activation);
    non_beat * (1.0 - boundary) + event * boundary + 1e-6
}

fn meter_observation_likelihood(
    beat_activation: f32,
    downbeat_activation: f32,
    boundary: f32,
    predicts_downbeat: bool,
) -> f32 {
    let non_beat = (1.0 - beat_activation - downbeat_activation).clamp(0.001, 1.0);
    let boundary_activation = if predicts_downbeat {
        downbeat_activation
    } else {
        beat_activation
    };
    non_beat * (1.0 - boundary) + boundary_activation * boundary + 1e-6
}

fn finite_activation(activation: f32) -> f32 {
    if activation.is_finite() {
        activation.clamp(0.0, 1.0)
    } else {
        0.0
    }
}

fn random_log_tempo(rng: &mut StdRng) -> f32 {
    let minimum = ParticleDecoder::MIN_TEMPO.ln();
    let maximum = ParticleDecoder::MAX_TEMPO.ln();
    rng.random_range(minimum..=maximum).exp()
}

fn transition_tempo(particle: &mut Particle, rng: &mut StdRng) {
    const LOCAL_TRANSITION_LAMBDA: f32 = 100.0;
    const MAX_LOCAL_LOG_CHANGE: f32 = 0.05;
    const HARMONIC_PROPOSAL_PROBABILITY: f32 = 0.003;
    const GLOBAL_PROPOSAL_PROBABILITY: f32 = 0.001;

    let proposal = rng.random::<f32>();
    if proposal < GLOBAL_PROPOSAL_PROBABILITY {
        particle.tempo = random_log_tempo(rng);
        return;
    }
    if proposal < GLOBAL_PROPOSAL_PROBABILITY + HARMONIC_PROPOSAL_PROBABILITY {
        let harmonic = if rng.random::<bool>() {
            particle.tempo * 2.0
        } else {
            particle.tempo * 0.5
        };
        if (ParticleDecoder::MIN_TEMPO..=ParticleDecoder::MAX_TEMPO).contains(&harmonic) {
            particle.tempo = harmonic;
            return;
        }
    }

    let uniform = rng.random_range(-0.5_f32..0.5_f32);
    let log_change = -uniform.signum() * (1.0 - 2.0 * uniform.abs()).max(f32::EPSILON).ln()
        / LOCAL_TRANSITION_LAMBDA;
    particle.tempo = (particle.tempo
        * log_change
            .clamp(-MAX_LOCAL_LOG_CHANGE, MAX_LOCAL_LOG_CHANGE)
            .exp())
    .clamp(ParticleDecoder::MIN_TEMPO, ParticleDecoder::MAX_TEMPO);
}

fn dominant_tempo_cluster(particles: &[Particle]) -> TempoCluster {
    const BINS: usize = 100;
    const CLUSTER_RADIUS: usize = 2;
    let minimum = ParticleDecoder::MIN_TEMPO.ln();
    let span = ParticleDecoder::MAX_TEMPO.ln() - minimum;
    let mut weights = [0.0_f32; BINS];
    for particle in particles {
        let normalized = ((particle.tempo.ln() - minimum) / span).clamp(0.0, 1.0);
        let index = ((normalized * BINS as f32) as usize).min(BINS - 1);
        weights[index] += particle.weight;
    }
    let winning_bin = (0..BINS)
        .max_by(|first, second| {
            let cluster_weight = |index: usize| {
                let start = index.saturating_sub(CLUSTER_RADIUS);
                let end = (index + CLUSTER_RADIUS + 1).min(BINS);
                weights[start..end].iter().sum::<f32>()
            };
            cluster_weight(*first).total_cmp(&cluster_weight(*second))
        })
        .unwrap_or(0);
    let start = winning_bin.saturating_sub(CLUSTER_RADIUS);
    let end = (winning_bin + CLUSTER_RADIUS + 1).min(BINS);
    let mut weighted_tempo = 0.0;
    let mut cluster_weight = 0.0;
    for particle in particles {
        let normalized = ((particle.tempo.ln() - minimum) / span).clamp(0.0, 1.0);
        let index = ((normalized * BINS as f32) as usize).min(BINS - 1);
        if (start..end).contains(&index) {
            weighted_tempo += particle.tempo * particle.weight;
            cluster_weight += particle.weight;
        }
    }
    TempoCluster {
        bpm: if cluster_weight > f32::EPSILON {
            weighted_tempo / cluster_weight
        } else {
            0.0
        },
        confidence: cluster_weight,
    }
}

fn tempo_cluster_confidence(particles: &[Particle], tempo: f32) -> f32 {
    if tempo <= 0.0 {
        return 0.0;
    }
    particles
        .iter()
        .filter(|particle| ((particle.tempo / tempo) - 1.0).abs() <= 0.05)
        .map(|particle| particle.weight)
        .sum()
}

fn meter_posterior(particles: &[MeterParticle]) -> MeterPosterior {
    let mut posterior = MeterPosterior {
        beat_probabilities: [[0.0; 4]; 3],
        probabilities: [0.0; 3],
    };
    for particle in particles {
        let meter_index = usize::from(particle.meter - 2);
        posterior.probabilities[meter_index] += particle.weight;
        posterior.beat_probabilities[meter_index][usize::from(particle.beat_in_bar)] +=
            particle.weight;
    }
    posterior
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

fn phase_consistency(particles: &[Particle]) -> f32 {
    let (sine, cosine) = particles
        .iter()
        .fold((0.0, 0.0), |(sine, cosine), particle| {
            let angle = particle.phase * std::f32::consts::TAU;
            (
                sine + angle.sin() * particle.weight,
                cosine + angle.cos() * particle.weight,
            )
        });
    sine.hypot(cosine).clamp(0.0, 1.0)
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
    fn seeded_tempo_initializes_a_narrow_live_hypothesis() {
        let mut decoder = ParticleDecoder::new();
        decoder.seed_tempo(120.0);

        let tempo = decoder
            .particles
            .iter()
            .map(|particle| particle.tempo * particle.weight)
            .sum::<f32>();
        approx::assert_abs_diff_eq!(tempo, 120.0, epsilon = 0.2);
    }

    #[test]
    fn tempo_cluster_does_not_average_half_and_double_time_hypotheses() {
        let mut particles = Vec::new();
        for _ in 0..750 {
            particles.push(Particle {
                tempo: 75.0,
                phase: 0.0,
                weight: 1.0 / 1_500.0,
                wrapped: false,
            });
            particles.push(Particle {
                tempo: 150.0,
                phase: 0.0,
                weight: 1.0 / 1_500.0,
                wrapped: false,
            });
        }

        let cluster = dominant_tempo_cluster(&particles);

        assert!(
            (cluster.bpm - 75.0).abs() < 2.0 || (cluster.bpm - 150.0).abs() < 2.0,
            "tempo cluster should preserve a harmonic hypothesis, got {}",
            cluster.bpm
        );
        assert!((cluster.bpm - 112.5).abs() > 20.0);
    }

    #[test]
    fn tempo_commitment_slews_small_changes_and_confirms_large_ones() {
        let mut commitment = TempoCommitment::new();
        commitment.update(TempoCluster {
            bpm: 100.0,
            confidence: 0.9,
        });
        commitment.update(TempoCluster {
            bpm: 105.0,
            confidence: 0.9,
        });
        approx::assert_abs_diff_eq!(commitment.tempo(), 101.75, epsilon = 0.001);
        commitment.update(TempoCluster {
            bpm: 101.9,
            confidence: 0.9,
        });
        approx::assert_abs_diff_eq!(commitment.tempo(), 101.75, epsilon = 0.001);

        for _ in 0..3 {
            commitment.update(TempoCluster {
                bpm: 140.0,
                confidence: 0.9,
            });
            approx::assert_abs_diff_eq!(commitment.tempo(), 101.75, epsilon = 0.001);
        }
        commitment.update(TempoCluster {
            bpm: 140.0,
            confidence: 0.9,
        });
        approx::assert_abs_diff_eq!(commitment.tempo(), 140.0, epsilon = 0.001);
    }

    #[test]
    fn published_tempo_changes_only_on_inferred_beat_boundaries() {
        let mut decoder = ParticleDecoder::new();
        decoder.seed_tempo(120.0);
        let estimates = feed_synthetic_rhythm(&mut decoder, 120.0, 4, 12, 0);

        for estimates in estimates.windows(2) {
            if (estimates[1].tempo - estimates[0].tempo).abs() > f32::EPSILON {
                assert!(
                    estimates[1].estimated_beat > estimates[0].estimated_beat,
                    "tempo changed between beat boundaries"
                );
            }
        }
        approx::assert_abs_diff_eq!(
            estimates.last().expect("estimate").tempo,
            120.0,
            epsilon = 2.0
        );
    }

    #[test]
    fn abrupt_tempo_change_relocks_without_averaging_the_songs() {
        let mut decoder = ParticleDecoder::new();
        decoder.seed_tempo(80.0);
        let initial = feed_synthetic_rhythm(&mut decoder, 80.0, 4, 12, 0);
        approx::assert_abs_diff_eq!(
            initial.last().expect("initial estimate").tempo,
            80.0,
            epsilon = 3.0
        );

        let changed = feed_synthetic_rhythm(&mut decoder, 120.0, 4, 6, 0);
        let relocked_frame = changed
            .iter()
            .position(|estimate| (estimate.tempo - 120.0).abs() <= 4.0)
            .expect("changed tempo should relock");
        assert!(
            relocked_frame <= ParticleDecoder::FPS as usize * 8,
            "tempo took more than eight seconds to relock"
        );
        approx::assert_abs_diff_eq!(
            changed.last().expect("changed estimate").tempo,
            120.0,
            epsilon = 4.0
        );
        assert!(
            changed
                .iter()
                .skip(
                    changed
                        .len()
                        .saturating_sub(ParticleDecoder::FPS as usize * 2)
                )
                .all(|estimate| (estimate.tempo - 100.0).abs() > 8.0),
            "tracker should commit to a tempo cluster instead of averaging tracks"
        );
    }

    #[test]
    fn particle_consensus_exposes_phase_lock() {
        let mut decoder = ParticleDecoder::new();
        let weight = 1.0 / decoder.particles.len() as f32;
        for particle in &mut decoder.particles {
            particle.phase = 0.25;
            particle.weight = weight;
        }

        approx::assert_abs_diff_eq!(phase_consistency(&decoder.particles), 1.0, epsilon = 0.0001);
    }

    #[test]
    fn meter_observations_penalize_false_downbeats() {
        let ordinary_beat = meter_observation_likelihood(0.95, 0.01, 1.0, false);
        let false_downbeat = meter_observation_likelihood(0.95, 0.01, 1.0, true);
        let actual_downbeat = meter_observation_likelihood(0.01, 0.95, 1.0, true);
        let missed_downbeat = meter_observation_likelihood(0.01, 0.95, 1.0, false);

        assert!(ordinary_beat > false_downbeat * 50.0);
        assert!(actual_downbeat > missed_downbeat * 50.0);
    }

    #[test]
    fn meter_particles_begin_with_equal_meter_priors() {
        let decoder = ParticleDecoder::new();
        let posterior = meter_posterior(&decoder.meter_particles);

        for probability in posterior.probabilities {
            approx::assert_abs_diff_eq!(probability, 1.0 / 3.0, epsilon = 0.0001);
        }
    }

    #[test]
    fn meter_commitment_changes_only_on_the_challenger_downbeat() {
        let mut commitment = MeterCommitment::new();
        for _ in 0..8 {
            commitment.advance(certain_meter_posterior(3, 1));
        }
        assert_eq!(commitment.meter, 4);

        commitment.advance(certain_meter_posterior(3, 0));
        assert_eq!(commitment.meter, 3);
        assert_eq!(commitment.beat_in_bar, 0);
    }

    #[test]
    fn downbeat_patterns_select_the_matching_meter() {
        for expected_meter in 2_u8..=4 {
            let mut decoder = ParticleDecoder::new();
            decoder.seed_tempo(120.0);
            let estimates = feed_synthetic_rhythm(&mut decoder, 120.0, expected_meter, 16, 0);
            let estimate = estimates.last().expect("meter estimate");

            assert_eq!(estimate.meter, expected_meter);
            assert!(estimate.tracking_confidence > 0.6);
        }
    }

    #[test]
    fn ambiguous_downbeats_do_not_flip_a_locked_meter() {
        let mut decoder = ParticleDecoder::new();
        decoder.seed_tempo(120.0);
        feed_synthetic_rhythm(&mut decoder, 120.0, 4, 16, 0);
        assert_eq!(decoder.meter.meter, 4);
        assert!(decoder.meter.locked);

        let frames_per_beat = (ParticleDecoder::FPS * 0.5) as usize;
        let mut meters = Vec::new();
        for frame in 0..frames_per_beat * 32 {
            let beat = frame % frames_per_beat == 0;
            let beat_number = frame / frames_per_beat;
            let ambiguous_downbeat =
                beat && (beat_number.is_multiple_of(3) || beat_number.is_multiple_of(4));
            let estimate = decoder.update(
                if beat { 0.52 } else { 0.01 },
                if ambiguous_downbeat { 0.42 } else { 0.01 },
            );
            meters.push(estimate.meter);
        }

        assert!(meters.into_iter().all(|meter| meter == 4));
    }

    #[test]
    fn sustained_new_meter_evidence_switches_only_after_commitment() {
        let mut decoder = ParticleDecoder::new();
        decoder.seed_tempo(120.0);
        feed_synthetic_rhythm(&mut decoder, 120.0, 3, 16, 0);
        assert_eq!(decoder.meter.meter, 3);

        let changed = feed_synthetic_rhythm(&mut decoder, 120.0, 4, 8, 0);
        let switched_frame = changed
            .iter()
            .position(|estimate| estimate.meter == 4)
            .expect("new meter should commit");
        let switches = changed
            .windows(2)
            .filter(|estimates| estimates[0].meter != estimates[1].meter)
            .count();

        assert!(
            switched_frame <= ParticleDecoder::FPS as usize * 8,
            "meter took more than eight seconds to commit"
        );
        assert_eq!(changed.last().expect("changed meter").meter, 4);
        assert_eq!(switches, 1);
    }

    #[test]
    fn reset_discards_previous_song_tempo_and_meter_state() {
        let mut decoder = ParticleDecoder::new();
        decoder.seed_tempo(90.0);
        feed_synthetic_rhythm(&mut decoder, 90.0, 3, 12, 0);
        assert_eq!(decoder.meter.meter, 3);

        decoder.reset();
        decoder.seed_tempo(128.0);
        let estimates = feed_synthetic_rhythm(&mut decoder, 128.0, 4, 6, 0);
        let estimate = estimates.last().expect("new song estimate");

        assert_eq!(estimate.meter, 4);
        approx::assert_abs_diff_eq!(estimate.tempo, 128.0, epsilon = 3.0);
        assert!(estimate.estimated_beat < 4 * 8);
    }

    #[test]
    fn a_wide_off_grid_activation_does_not_count_as_multiple_onsets() {
        let mut decoder = ParticleDecoder::new();
        decoder.seed_tempo(120.0);
        decoder.tempo.published = Some(120.0);
        decoder.beat_count = 32;
        decoder.bar_count = 8;
        let weight = 1.0 / decoder.particles.len() as f32;
        for particle in &mut decoder.particles {
            particle.phase = 0.45;
            particle.weight = weight;
        }

        let estimates = (0..6)
            .map(|_| decoder.update(0.95, 0.01))
            .collect::<Vec<_>>();

        assert!(estimates.iter().all(|estimate| estimate.tempo > 0.0));
        assert_eq!(decoder.contradictory_onsets, 1);
        assert!(decoder.beat_count >= 32);
        assert!(decoder.bar_count >= 8);
    }

    #[test]
    fn distinct_off_grid_onsets_reanchor_without_clearing_the_live_clock() {
        let mut decoder = ParticleDecoder::new();
        decoder.seed_tempo(120.0);
        decoder.tempo.published = Some(120.0);
        decoder.meter.locked = true;
        decoder.beat_count = 32;
        decoder.bar_count = 8;
        let weight = 1.0 / decoder.particles.len() as f32;
        let mut estimate = BeatEstimate::default();

        for _ in 0..ParticleDecoder::CONTRADICTORY_ONSETS_TO_REACQUIRE {
            for particle in &mut decoder.particles {
                particle.phase = 0.45;
                particle.weight = weight;
            }
            estimate = decoder.update(0.95, 0.01);
            for _ in 0..ParticleDecoder::MIN_CONTRADICTORY_ONSET_FRAMES {
                decoder.update(0.01, 0.01);
            }
        }

        approx::assert_abs_diff_eq!(estimate.tempo, 120.0, epsilon = 0.001);
        assert!(estimate.beat_position < 0.1 || estimate.beat_position > 0.9);
        assert!(decoder.beat_count >= 32);
        assert!(decoder.bar_count >= 8);
        assert!(decoder.meter.locked);
        assert_eq!(decoder.contradictory_onsets, 0);
    }

    #[test]
    fn a_quiet_breakdown_does_not_unpublish_the_live_tempo() {
        let mut decoder = ParticleDecoder::new();
        decoder.seed_tempo(128.0);
        decoder.tempo.published = Some(128.0);
        decoder.beat_count = 48;

        let estimates = (0..ParticleDecoder::FPS as usize * 5)
            .map(|_| decoder.update(0.01, 0.01))
            .collect::<Vec<_>>();

        assert!(estimates.iter().all(|estimate| estimate.tempo > 0.0));
        assert!(
            estimates
                .windows(2)
                .all(|estimates| estimates[1].estimated_beat >= estimates[0].estimated_beat)
        );
        assert!(decoder.beat_count >= 48);
    }

    fn certain_meter_posterior(meter: u8, beat_in_bar: u8) -> MeterPosterior {
        let mut posterior = MeterPosterior {
            beat_probabilities: [[0.0; 4]; 3],
            probabilities: [0.025, 0.025, 0.025],
        };
        let meter_index = usize::from(meter - 2);
        posterior.probabilities[meter_index] = 0.95;
        posterior.beat_probabilities[meter_index][usize::from(beat_in_bar)] = 0.95;
        posterior
    }

    fn feed_synthetic_rhythm(
        decoder: &mut ParticleDecoder,
        tempo: f32,
        meter: u8,
        bars: usize,
        starting_beat: usize,
    ) -> Vec<BeatEstimate> {
        let frames_per_beat = (ParticleDecoder::FPS * 60.0 / tempo).round() as usize;
        let frames = frames_per_beat * usize::from(meter) * bars;
        let mut estimates = Vec::new();
        for frame in 0..frames {
            let beat = frame.is_multiple_of(frames_per_beat);
            let beat_number = starting_beat + frame / frames_per_beat;
            let downbeat = beat && beat_number.is_multiple_of(usize::from(meter));
            estimates.push(decoder.update(
                if beat && !downbeat { 0.95 } else { 0.01 },
                if downbeat { 0.95 } else { 0.01 },
            ));
        }
        estimates
    }
}
