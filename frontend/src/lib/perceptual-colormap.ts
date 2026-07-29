const MAGMA_STOPS = [
  [0, 0, 4],
  [28, 16, 68],
  [79, 18, 123],
  [129, 37, 129],
  [181, 54, 122],
  [229, 80, 100],
  [251, 135, 97],
  [254, 194, 135],
  [252, 253, 191],
] as const

export function magmaColor(value: number) {
  const normalized = Math.max(0, Math.min(1, value))
  const position = normalized * (MAGMA_STOPS.length - 1)
  const start = Math.min(MAGMA_STOPS.length - 2, Math.floor(position))
  const fraction = position - start
  const from = MAGMA_STOPS[start]
  const to = MAGMA_STOPS[start + 1]
  const channel = (index: 0 | 1 | 2) =>
    Math.round(from[index] + (to[index] - from[index]) * fraction)
  return `rgb(${channel(0)} ${channel(1)} ${channel(2)})`
}
