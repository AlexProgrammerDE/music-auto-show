export interface BeatRunwaySample {
  readonly active: boolean
  readonly beatIndex: number
  readonly beatPosition: number
  readonly estimatedBeat: number
  readonly meter: number
  readonly sampledAt: number
  readonly sourceTempo?: number
  readonly tempo: number
}

export interface BeatRunwayFrame extends BeatRunwaySample {
  readonly absoluteBeat: number
}

const HARMONIC_TEMPO_TOLERANCE = 0.08
const MAX_PHASE_CORRECTION_RATIO = 0.25
const PHASE_CORRECTION_WINDOW_MS = 900
const TEMPO_SMOOTHING_MS = 450

export function normalizeMeter(meter: number) {
  return meter === 2 || meter === 3 || meter === 4 ? meter : 4
}

function advanceBeatIndex(beatIndex: number, beats: number, meter: number) {
  return ((((beatIndex - 1 + beats) % meter) + meter) % meter) + 1
}

function shortestPhaseDelta(from: number, to: number) {
  let delta = to - from
  if (delta > 0.5) delta -= 1
  if (delta < -0.5) delta += 1
  return delta
}

function relativeTempoError(reference: number, candidate: number) {
  return Math.abs(candidate / reference - 1)
}

function tempoNearReference(reference: number, incoming: number) {
  if (reference <= 0 || incoming <= 0) {
    return { harmonic: false, tempo: incoming }
  }
  const candidates = [
    { harmonic: false, tempo: incoming },
    { harmonic: true, tempo: incoming * 0.5 },
    { harmonic: true, tempo: incoming * 2 },
  ]
  const closest = candidates.reduce((best, candidate) =>
    relativeTempoError(reference, candidate.tempo) < relativeTempoError(reference, best.tempo)
      ? candidate
      : best,
  )
  if (
    closest.harmonic &&
    relativeTempoError(reference, closest.tempo) <= HARMONIC_TEMPO_TOLERANCE
  ) {
    return closest
  }
  return { harmonic: false, tempo: incoming }
}

export function stabilizeBeatTempo(reference: number, incoming: number) {
  const finiteReference = Number.isFinite(reference) ? Math.max(0, reference) : 0
  const finiteIncoming = Number.isFinite(incoming) ? Math.max(0, incoming) : 0
  return tempoNearReference(finiteReference, finiteIncoming).tempo
}

export function reconcileBeatRunwaySample(
  previous: BeatRunwaySample,
  incoming: BeatRunwaySample,
): BeatRunwaySample {
  const meter = normalizeMeter(incoming.meter)
  const tempo = Number.isFinite(incoming.tempo) ? Math.max(0, incoming.tempo) : 0
  const normalizedIncoming = {
    ...incoming,
    beatIndex: advanceBeatIndex(Math.max(1, incoming.beatIndex), 0, meter),
    meter,
    sourceTempo: tempo,
    tempo,
  }
  if (
    !normalizedIncoming.active ||
    normalizedIncoming.tempo <= 0 ||
    !previous.active ||
    previous.meter !== meter
  ) {
    return normalizedIncoming
  }
  if (normalizedIncoming.sampledAt <= previous.sampledAt) return previous

  const projected = projectBeatRunwayFrame(previous, incoming.sampledAt)
  const tempoTarget = tempoNearReference(previous.tempo, normalizedIncoming.tempo)
  const sourceTempoTarget = tempoNearReference(
    previous.sourceTempo ?? previous.tempo,
    normalizedIncoming.tempo,
  )
  const reconciledTarget = (() => {
    if (tempoTarget.harmonic || !sourceTempoTarget.harmonic) return tempoTarget
    const tempoTargetError = relativeTempoError(previous.tempo, tempoTarget.tempo)
    const sourceTargetError = relativeTempoError(previous.tempo, sourceTempoTarget.tempo)
    return {
      harmonic: true,
      tempo: tempoTargetError <= sourceTargetError ? tempoTarget.tempo : sourceTempoTarget.tempo,
    }
  })()
  const phaseDelta = reconciledTarget.harmonic
    ? 0
    : shortestPhaseDelta(projected.beatPosition, normalizedIncoming.beatPosition)
  const maximumCorrection = reconciledTarget.tempo * MAX_PHASE_CORRECTION_RATIO
  const phaseCorrection = Math.max(
    -maximumCorrection,
    Math.min(maximumCorrection, (phaseDelta * 60_000) / PHASE_CORRECTION_WINDOW_MS),
  )
  const elapsed = normalizedIncoming.sampledAt - previous.sampledAt
  const blend = 1 - Math.exp(-elapsed / TEMPO_SMOOTHING_MS)
  const steeredTempo = reconciledTarget.tempo + phaseCorrection
  const reconciledTempo = previous.tempo + (steeredTempo - previous.tempo) * blend
  return {
    active: normalizedIncoming.active,
    beatIndex: projected.beatIndex,
    beatPosition: projected.beatPosition,
    estimatedBeat: projected.estimatedBeat,
    meter,
    sampledAt: normalizedIncoming.sampledAt,
    sourceTempo: normalizedIncoming.tempo,
    tempo: reconciledTempo,
  }
}

export function projectBeatRunwayFrame(sample: BeatRunwaySample, now: number): BeatRunwayFrame {
  const meter = normalizeMeter(sample.meter)
  if (!sample.active || sample.tempo <= 0) {
    return { ...sample, meter, absoluteBeat: sample.estimatedBeat }
  }
  const elapsedBeats = (Math.max(0, now - sample.sampledAt) * sample.tempo) / 60_000
  const totalPhase = sample.beatPosition + elapsedBeats
  const crossedBeats = Math.floor(totalPhase)
  const beatPosition = totalPhase - crossedBeats
  const estimatedBeat = sample.estimatedBeat + crossedBeats
  const beatIndex = advanceBeatIndex(Math.max(1, sample.beatIndex), crossedBeats, meter)
  return {
    ...sample,
    beatIndex,
    beatPosition,
    estimatedBeat,
    meter,
    absoluteBeat: estimatedBeat + beatPosition,
  }
}
