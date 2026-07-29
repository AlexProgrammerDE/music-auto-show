import { describe, expect, test } from "vitest"

import {
  normalizeMeter,
  projectBeatRunwayFrame,
  reconcileBeatRunwaySample,
} from "@/lib/beat-runway"

describe("beat runway projection", () => {
  test("preserves detected two, three, and four beat meters", () => {
    expect(normalizeMeter(2)).toBe(2)
    expect(normalizeMeter(3)).toBe(3)
    expect(normalizeMeter(4)).toBe(4)
    expect(normalizeMeter(0)).toBe(4)
  })

  test("projects phase and beat index between snapshots", () => {
    const frame = projectBeatRunwayFrame(
      {
        active: true,
        beatIndex: 3,
        beatPosition: 0.75,
        estimatedBeat: 10,
        meter: 3,
        sampledAt: 1_000,
        tempo: 120,
      },
      1_250,
    )
    expect(frame.beatPosition).toBeCloseTo(0.25)
    expect(frame.estimatedBeat).toBe(11)
    expect(frame.beatIndex).toBe(1)
  })

  test("smooths small phase corrections without hiding a new beat", () => {
    const previous = {
      active: true,
      beatIndex: 1,
      beatPosition: 0.1,
      estimatedBeat: 8,
      meter: 4,
      sampledAt: 1_000,
      tempo: 120,
    }
    expect(
      reconcileBeatRunwaySample(previous, {
        ...previous,
        beatPosition: 0.32,
        sampledAt: 1_100,
      }).beatPosition,
    ).toBeCloseTo(0.308)
    expect(
      reconcileBeatRunwaySample(previous, {
        ...previous,
        beatIndex: 2,
        beatPosition: 0.02,
        estimatedBeat: 9,
        sampledAt: 1_100,
      }).estimatedBeat,
    ).toBe(9)
  })
})
