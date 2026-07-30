import { followEnvelope } from "@/lib/live-frame-store"

const ATTACK_MILLISECONDS = 12
const RELEASE_MILLISECONDS = 65
const PEAK_DECAY_PER_MILLISECOND = 0.0009
const ADJACENT_BIN_WEIGHT = 0.1

export function smoothSpectrumBins(values: readonly number[]) {
  return values.map((value, index) => {
    const previous = values[index - 1] ?? value
    const next = values[index + 1] ?? value
    return value * (1 - ADJACENT_BIN_WEIGHT * 2) + (previous + next) * ADJACENT_BIN_WEIGHT
  })
}

export function followSpectrumBin(current: number, target: number, deltaMilliseconds: number) {
  return followEnvelope(
    current,
    target,
    deltaMilliseconds,
    ATTACK_MILLISECONDS,
    RELEASE_MILLISECONDS,
  )
}

export function followSpectrumPeak(current: number, target: number, deltaMilliseconds: number) {
  return Math.max(target, current - Math.max(0, deltaMilliseconds) * PEAK_DECAY_PER_MILLISECOND)
}
