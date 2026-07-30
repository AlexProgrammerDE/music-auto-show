import { describe, expect, test } from "vitest"

import { followSpectrumBin, followSpectrumPeak, smoothSpectrumBins } from "@/lib/spectrum-motion"

describe("spectrum motion", () => {
  test("smooths across frequency without flattening a constant spectrum", () => {
    expect(smoothSpectrumBins([0.5, 0.5, 0.5])).toEqual([0.5, 0.5, 0.5])
    expect(smoothSpectrumBins([0, 1, 0])).toEqual([0.1, 0.8, 0.1])
  })

  test("responds quickly while releasing more gently", () => {
    const attack = followSpectrumBin(0, 1, 25)
    const release = followSpectrumBin(1, 0, 25)

    expect(attack).toBeGreaterThan(0.85)
    expect(release).toBeLessThan(0.7)
    expect(release).toBeLessThan(attack)
  })

  test("drops held peaks within a little over one second", () => {
    expect(followSpectrumPeak(1, 0, 1_000)).toBeCloseTo(0.1)
    expect(followSpectrumPeak(0.5, 0.8, 25)).toBe(0.8)
  })
})
