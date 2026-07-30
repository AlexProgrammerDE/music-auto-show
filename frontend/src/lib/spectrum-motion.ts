const PEAK_DECAY_PER_MILLISECOND = 0.0009

export function followSpectrumPeak(current: number, target: number, deltaMilliseconds: number) {
  return Math.max(target, current - Math.max(0, deltaMilliseconds) * PEAK_DECAY_PER_MILLISECOND)
}
