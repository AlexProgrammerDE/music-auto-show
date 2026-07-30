import { describe, expect, test } from "vitest"

import { followSpectrumPeak } from "@/lib/spectrum-motion"

describe("spectrum motion", () => {
  test("drops held peaks within a little over one second", () => {
    expect(followSpectrumPeak(1, 0, 1_000)).toBeCloseTo(0.1)
    expect(followSpectrumPeak(0.5, 0.8, 25)).toBe(0.8)
  })
})
