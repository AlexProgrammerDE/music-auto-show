export function snapSliderValue(value: number, minimum: number, maximum: number, step: number) {
  const clamped = Math.max(minimum, Math.min(maximum, value))
  const steps = Math.round((clamped - minimum) / step)
  return Number((minimum + steps * step).toFixed(10))
}
