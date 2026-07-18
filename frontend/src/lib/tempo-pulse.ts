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

function finiteOrZero(value: number) {
  return Number.isFinite(value) ? value : 0
}

function wrapUnit(value: number) {
  const wrapped = finiteOrZero(value) % 1
  return wrapped < 0 ? wrapped + 1 : wrapped
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
