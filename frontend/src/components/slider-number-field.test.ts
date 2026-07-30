import { describe, expect, it } from "vitest"

import { snapSliderValue } from "@/components/slider-number-field"

describe("snapSliderValue", () => {
  it("snaps manual values to the nearest configured increment", () => {
    expect(snapSliderValue(3.35, 0.1, 10, 0.1)).toBe(3.4)
    expect(snapSliderValue(3.34, 0.1, 10, 0.1)).toBe(3.3)
  })

  it("keeps stepped values stable and within their bounds", () => {
    expect(snapSliderValue(3.3, 0.1, 10, 0.1)).toBe(3.3)
    expect(snapSliderValue(-1, 0.1, 10, 0.1)).toBe(0.1)
    expect(snapSliderValue(11, 0.1, 10, 0.1)).toBe(10)
  })
})
