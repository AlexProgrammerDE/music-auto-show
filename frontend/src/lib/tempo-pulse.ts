export const BEATS_PER_BAR = 4

export interface TempoSample {
  readonly active: boolean
  readonly barPosition: number
  readonly beatPosition: number
  readonly sampledAt: number
  readonly tempo: number
}

export interface TempoFrame {
  readonly active: boolean
  readonly barPosition: number
  readonly beatIndex: number
  readonly beatPosition: number
  readonly impact: number
  readonly tempo: number
}

const PHASE_CORRECTION_GAIN = 0.25
const MAX_BEAT_PHASE_CORRECTION = 0.02
const MAX_BAR_PHASE_CORRECTION = MAX_BEAT_PHASE_CORRECTION / BEATS_PER_BAR
const TEMPO_CORRECTION_GAIN = 0.2
const MAX_TEMPO_CORRECTION = 2
const MAX_RECONCILIATION_GAP_MS = 2_000

function finiteOrZero(value: number) {
  return Number.isFinite(value) ? value : 0
}

function wrapUnit(value: number) {
  const wrapped = finiteOrZero(value) % 1
  return wrapped < 0 ? wrapped + 1 : wrapped
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.max(minimum, Math.min(maximum, value))
}

function phaseCorrection(current: number, observed: number, maximum: number) {
  const error = wrapUnit(observed - current + 0.5) - 0.5
  return clamp(error * PHASE_CORRECTION_GAIN, -maximum, maximum)
}

function normalizeSample(sample: TempoSample): TempoSample {
  const tempo = Math.max(0, finiteOrZero(sample.tempo))
  return {
    active: sample.active && tempo > 0,
    barPosition: wrapUnit(sample.barPosition),
    beatPosition: wrapUnit(sample.beatPosition),
    sampledAt: finiteOrZero(sample.sampledAt),
    tempo,
  }
}

export function projectTempoFrame(sample: TempoSample, now: number): TempoFrame {
  const tempo = Math.max(0, finiteOrZero(sample.tempo))
  const active = sample.active && tempo > 0
  const elapsedMilliseconds = active ? Math.max(0, finiteOrZero(now) - sample.sampledAt) : 0
  const elapsedBeats = (elapsedMilliseconds / 60_000) * tempo
  const beatPosition = wrapUnit(sample.beatPosition + elapsedBeats)
  const barPosition = wrapUnit(sample.barPosition + elapsedBeats / BEATS_PER_BAR)
  const beatIndex = Math.min(BEATS_PER_BAR - 1, Math.floor(barPosition * BEATS_PER_BAR))
  const impact = active ? Math.max(0, 1 - beatPosition / 0.28) ** 3 : 0

  return {
    active,
    barPosition,
    beatIndex,
    beatPosition,
    impact,
    tempo,
  }
}

export function reconcileTempoSample(
  currentSample: TempoSample,
  observedSample: TempoSample,
): TempoSample {
  const current = normalizeSample(currentSample)
  const observed = normalizeSample(observedSample)
  const elapsed = observed.sampledAt - current.sampledAt
  if (!current.active || !observed.active || elapsed < 0 || elapsed > MAX_RECONCILIATION_GAP_MS) {
    return observed
  }

  const projected = projectTempoFrame(current, observed.sampledAt)
  const tempoCorrection = clamp(
    (observed.tempo - projected.tempo) * TEMPO_CORRECTION_GAIN,
    -MAX_TEMPO_CORRECTION,
    MAX_TEMPO_CORRECTION,
  )

  return {
    active: true,
    barPosition: wrapUnit(
      projected.barPosition +
        phaseCorrection(projected.barPosition, observed.barPosition, MAX_BAR_PHASE_CORRECTION),
    ),
    beatPosition: wrapUnit(
      projected.beatPosition +
        phaseCorrection(projected.beatPosition, observed.beatPosition, MAX_BEAT_PHASE_CORRECTION),
    ),
    sampledAt: observed.sampledAt,
    tempo: Math.max(0, projected.tempo + tempoCorrection),
  }
}
