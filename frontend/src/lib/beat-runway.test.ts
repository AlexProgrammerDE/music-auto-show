import { describe, expect, test } from "vitest"

import {
  normalizeMeter,
  projectBeatRunwayFrame,
  reconcileBeatRunwaySample,
  stabilizeBeatTempo,
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

  test("steers tempo without moving the current phase backward", () => {
    const previous = {
      active: true,
      beatIndex: 1,
      beatPosition: 0.1,
      estimatedBeat: 8,
      meter: 4,
      sampledAt: 1_000,
      tempo: 120,
    }
    const projected = projectBeatRunwayFrame(previous, 1_100)
    const reconciled = reconcileBeatRunwaySample(previous, {
      ...previous,
      beatPosition: 0.32,
      sampledAt: 1_100,
    })

    expect(reconciled.beatPosition).toBeCloseTo(projected.beatPosition)
    expect(reconciled.estimatedBeat).toBe(projected.estimatedBeat)
    expect(reconciled.tempo).toBeGreaterThan(120)
    expect(reconciled.tempo).toBeLessThan(121)
  })

  test("keeps progression continuous across a corrected beat boundary", () => {
    const previous = {
      active: true,
      beatIndex: 1,
      beatPosition: 0.78,
      estimatedBeat: 8,
      meter: 4,
      sampledAt: 1_000,
      tempo: 120,
    }
    const projected = projectBeatRunwayFrame(previous, 1_100)
    const reconciled = reconcileBeatRunwaySample(previous, {
      active: true,
      beatIndex: 2,
      beatPosition: 0.02,
      estimatedBeat: 9,
      meter: 4,
      sampledAt: 1_100,
      tempo: 120,
    })

    expect(reconciled.beatPosition).toBeCloseTo(projected.beatPosition)
    expect(reconciled.beatIndex).toBe(1)
    const afterBoundary = projectBeatRunwayFrame(reconciled, 1_111)
    expect(afterBoundary.beatPosition).toBeGreaterThanOrEqual(0)
    expect(afterBoundary.beatPosition).toBeLessThan(0.01)
    expect(afterBoundary.beatIndex).toBe(2)
    expect(afterBoundary.estimatedBeat).toBe(9)
  })

  test("absorbs a tracker counter discontinuity without jumping the runway", () => {
    const incoming = {
      active: true,
      beatIndex: 3,
      beatPosition: 0.4,
      estimatedBeat: 40,
      meter: 4,
      sampledAt: 1_100,
      tempo: 120,
    }
    const reconciled = reconcileBeatRunwaySample(
      {
        ...incoming,
        beatIndex: 1,
        beatPosition: 0.1,
        estimatedBeat: 8,
        sampledAt: 1_000,
      },
      incoming,
    )

    expect(reconciled.beatPosition).toBeCloseTo(0.3)
    expect(reconciled.estimatedBeat).toBe(8)
    expect(reconciled.beatIndex).toBe(1)
  })

  test("ignores a stale sample instead of rewinding", () => {
    const previous = {
      active: true,
      beatIndex: 2,
      beatPosition: 0.6,
      estimatedBeat: 21,
      meter: 4,
      sampledAt: 1_100,
      tempo: 100,
    }

    expect(
      reconcileBeatRunwaySample(previous, {
        ...previous,
        beatIndex: 1,
        beatPosition: 0.1,
        estimatedBeat: 8,
        sampledAt: 1_050,
      }),
    ).toBe(previous)
  })

  test("treats half-time and double-time estimates as one display tempo", () => {
    expect(stabilizeBeatTempo(100, 200)).toBe(100)
    expect(stabilizeBeatTempo(200, 100)).toBe(200)
    expect(stabilizeBeatTempo(100, 150)).toBe(150)

    const drifted = reconcileBeatRunwaySample(
      {
        active: true,
        beatIndex: 1,
        beatPosition: 0.25,
        estimatedBeat: 20,
        meter: 4,
        sampledAt: 900,
        sourceTempo: 100,
        tempo: 125,
      },
      {
        active: true,
        beatIndex: 2,
        beatPosition: 0.8,
        estimatedBeat: 80,
        meter: 4,
        sampledAt: 1_000,
        tempo: 200,
      },
    )
    expect(drifted.tempo).toBeLessThan(125)

    let sample = {
      active: true,
      beatIndex: 1,
      beatPosition: 0.25,
      estimatedBeat: 20,
      meter: 4,
      sampledAt: 1_000,
      tempo: 100,
    }
    let previousAbsoluteBeat = sample.estimatedBeat + sample.beatPosition
    for (const [offset, tempo] of [
      [100, 200],
      [200, 100],
      [300, 199],
      [400, 101],
    ] as const) {
      sample = reconcileBeatRunwaySample(sample, {
        active: true,
        beatIndex: tempo > 150 ? 4 : 2,
        beatPosition: tempo > 150 ? 0.8 : 0.1,
        estimatedBeat: tempo > 150 ? 80 : 12,
        meter: 4,
        sampledAt: 1_000 + offset,
        tempo,
      })
      const absoluteBeat = sample.estimatedBeat + sample.beatPosition
      expect(absoluteBeat).toBeGreaterThan(previousAbsoluteBeat)
      expect(sample.tempo).toBeGreaterThan(98)
      expect(sample.tempo).toBeLessThan(102)
      previousAbsoluteBeat = absoluteBeat
    }
  })
})
