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

function advanceBeatIndex(beatIndex: number, beats: number, meter: number) {
  return ((((beatIndex - 1 + beats) % meter) + meter) % meter) + 1
}

function shortestPhaseDelta(from: number, to: number) {
  let delta = to - from
  if (delta > 0.5) delta -= 1
  if (delta < -0.5) delta += 1
  return delta
}

export function reconcileBeatRunwaySample(
  previous: BeatRunwaySample,
  incoming: BeatRunwaySample,
): BeatRunwaySample {
  const meter = normalizeMeter(incoming.meter)
  const normalizedIncoming = {
    ...incoming,
    beatIndex: advanceBeatIndex(Math.max(1, incoming.beatIndex), 0, meter),
    meter,
  }
  if (
    !normalizedIncoming.active ||
    normalizedIncoming.tempo <= 0 ||
    !previous.active ||
    previous.meter !== meter ||
    normalizedIncoming.sampledAt <= previous.sampledAt
  ) {
    return normalizedIncoming
  }

  const projected = projectBeatRunwayFrame(previous, incoming.sampledAt)
  const phaseDelta = shortestPhaseDelta(projected.beatPosition, normalizedIncoming.beatPosition)
  const incomingCrossedBeats = Math.floor(projected.beatPosition + phaseDelta)
  const expectedBeatIndex = advanceBeatIndex(projected.beatIndex, incomingCrossedBeats, meter)
  const counterDiscontinuity =
    Math.abs(projected.estimatedBeat - normalizedIncoming.estimatedBeat) > 1
  if (
    Math.abs(phaseDelta) >= 0.12 ||
    normalizedIncoming.beatIndex !== expectedBeatIndex ||
    counterDiscontinuity
  ) {
    return normalizedIncoming
  }

  const correctedPhase = projected.beatPosition + phaseDelta * 0.4
  const correctedCrossedBeats = Math.floor(correctedPhase)
  return {
    ...normalizedIncoming,
    beatIndex: advanceBeatIndex(projected.beatIndex, correctedCrossedBeats, meter),
    beatPosition: ((correctedPhase % 1) + 1) % 1,
    estimatedBeat: projected.estimatedBeat + correctedCrossedBeats,
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
