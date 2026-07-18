import { describe, expect, it } from "vitest"

import { projectTempoFrame, reconcileTempoSample } from "@/lib/tempo-pulse"

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

describe("reconcileTempoSample", () => {
  it("adopts the first active observation immediately", () => {
    const observed = {
      active: true,
      tempo: 128,
      beatPosition: 0.4,
      barPosition: 0.6,
      sampledAt: 2_000,
    }

    expect(
      reconcileTempoSample(
        {
          active: false,
          tempo: 0,
          beatPosition: 0,
          barPosition: 0,
          sampledAt: 1_000,
        },
        observed,
      ),
    ).toEqual(observed)
  })

  it("bounds phase and tempo corrections between live observations", () => {
    const reconciled = reconcileTempoSample(
      {
        active: true,
        tempo: 120,
        beatPosition: 0.2,
        barPosition: 0.05,
        sampledAt: 1_000,
      },
      {
        active: true,
        tempo: 140,
        beatPosition: 0.1,
        barPosition: 0.025,
        sampledAt: 1_050,
      },
    )

    expect(reconciled.beatPosition).toBeCloseTo(0.28)
    expect(reconciled.barPosition).toBeCloseTo(0.07)
    expect(reconciled.tempo).toBe(122)
  })

  it("uses the shortest correction across phase wraparound", () => {
    const reconciled = reconcileTempoSample(
      {
        active: true,
        tempo: 60,
        beatPosition: 0.98,
        barPosition: 0.245,
        sampledAt: 1_000,
      },
      {
        active: true,
        tempo: 60,
        beatPosition: 0.04,
        barPosition: 0.26,
        sampledAt: 1_010,
      },
    )

    expect(reconciled.beatPosition).toBeCloseTo(0.0025)
    expect(reconciled.barPosition).toBeCloseTo(0.250625)
  })

  it("resynchronizes after a long observation gap", () => {
    const observed = {
      active: true,
      tempo: 90,
      beatPosition: 0.7,
      barPosition: 0.9,
      sampledAt: 4_000,
    }

    expect(
      reconcileTempoSample(
        {
          active: true,
          tempo: 120,
          beatPosition: 0.1,
          barPosition: 0.2,
          sampledAt: 1_000,
        },
        observed,
      ),
    ).toEqual(observed)
  })
})
