export interface BeatRunwaySample {
  readonly active: boolean
  readonly beatIndex: number
  readonly beatPosition: number
  readonly estimatedBeat: number
  readonly meter: number
  readonly sampledAt: number
  readonly tempo: number
}

export interface BeatRunwayFrame extends BeatRunwaySample {
  readonly absoluteBeat: number
}

export function normalizeMeter(meter: number) {
  return meter === 2 || meter === 3 || meter === 4 ? meter : 4
}

export function reconcileBeatRunwaySample(
  previous: BeatRunwaySample,
  incoming: BeatRunwaySample,
): BeatRunwaySample {
  if (!incoming.active || incoming.tempo <= 0) return incoming
  const projected = projectBeatRunwayFrame(previous, incoming.sampledAt)
  const phaseError = Math.abs(projected.beatPosition - incoming.beatPosition)
  const wrappedError = Math.min(phaseError, 1 - phaseError)
  if (previous.active && previous.estimatedBeat === incoming.estimatedBeat && wrappedError < 0.12) {
    return {
      ...incoming,
      beatPosition: projected.beatPosition * 0.6 + incoming.beatPosition * 0.4,
    }
  }
  return incoming
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
  const beatIndex = ((Math.max(1, sample.beatIndex) - 1 + crossedBeats) % meter) + 1
  return {
    ...sample,
    beatIndex,
    beatPosition,
    estimatedBeat,
    meter,
    absoluteBeat: estimatedBeat + beatPosition,
  }
}
