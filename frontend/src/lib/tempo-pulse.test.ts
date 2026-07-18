import { describe, expect, it } from "vitest"

import { projectTempoFrame } from "@/lib/tempo-pulse"

describe("projectTempoFrame", () => {
  it("advances beat and bar phase at the detected tempo", () => {
    const frame = projectTempoFrame(
      {
        active: true,
        tempo: 120,
        beatPosition: 0.25,
        barPosition: 0.0625,
        sampledAt: 1_000,
      },
      1_250,
    )

    expect(frame.beatPosition).toBeCloseTo(0.75)
    expect(frame.barPosition).toBeCloseTo(0.1875)
    expect(frame.beatIndex).toBe(0)
  })

  it("wraps into the correct beat of the measure", () => {
    const frame = projectTempoFrame(
      {
        active: true,
        tempo: 120,
        beatPosition: 0.25,
        barPosition: 0.0625,
        sampledAt: 1_000,
      },
      2_000,
    )

    expect(frame.beatPosition).toBeCloseTo(0.25)
    expect(frame.barPosition).toBeCloseTo(0.5625)
    expect(frame.beatIndex).toBe(2)
  })

  it("holds the supplied phase when tempo tracking is inactive", () => {
    const frame = projectTempoFrame(
      {
        active: false,
        tempo: 128,
        beatPosition: 1.25,
        barPosition: -0.25,
        sampledAt: 1_000,
      },
      20_000,
    )

    expect(frame.active).toBe(false)
    expect(frame.beatPosition).toBe(0.25)
    expect(frame.barPosition).toBe(0.75)
    expect(frame.beatIndex).toBe(3)
    expect(frame.impact).toBe(0)
  })

  it("creates a sharp pulse immediately after each beat", () => {
    const impact = projectTempoFrame(
      {
        active: true,
        tempo: 128,
        beatPosition: 0,
        barPosition: 0.5,
        sampledAt: 1_000,
      },
      1_000,
    )

    const decay = projectTempoFrame(
      {
        active: true,
        tempo: 128,
        beatPosition: 0.2,
        barPosition: 0.55,
        sampledAt: 1_000,
      },
      1_000,
    )

    expect(impact.impact).toBe(1)
    expect(decay.impact).toBeGreaterThan(0)
    expect(decay.impact).toBeLessThan(impact.impact)
  })
})
