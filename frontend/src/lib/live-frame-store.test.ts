import { create } from "@bufbuild/protobuf"
import { afterEach, describe, expect, test } from "vitest"

import {
  FixtureStateSchema,
  LiveAudioFrameSchema,
  LiveFrameSchema,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import {
  followEnvelope,
  interpolateFixtureState,
  interpolatePhase,
  projectBeat,
  publishLiveFrame,
  resetLiveFrameStore,
  sampleLiveFrame,
} from "@/lib/live-frame-store"

afterEach(resetLiveFrameStore)

describe("live frame timing", () => {
  test("calibrates clock offset and preserves transport jitter in the local timeline", () => {
    publishLiveFrame(create(LiveFrameSchema, { capturedAtUnixMs: 1_000n, sequence: 1n }), 100, 900)
    publishLiveFrame(create(LiveFrameSchema, { capturedAtUnixMs: 1_025n, sequence: 2n }), 135, 900)

    const sample = sampleLiveFrame(145, 30)

    expect(sample?.previous.frame.sequence).toBe(1n)
    expect(sample?.current.frame.sequence).toBe(2n)
    expect(sample?.alpha).toBeCloseTo(0.6)
  })

  test("projects beat and bar phase from capture time to the current frame", () => {
    const audio = create(LiveAudioFrameSchema, {
      tempo: 120,
      beatPosition: 0.25,
      barPosition: 0.3125,
      beatIndex: 2,
      meter: 4,
      estimatedBeat: 17n,
    })

    const projected = projectBeat(audio, 1_000, 1_250)

    expect(projected.beatPosition).toBeCloseTo(0.75)
    expect(projected.barPosition).toBeCloseTo(0.4375)
    expect(projected.beatIndex).toBe(2)
    expect(projected.estimatedBeat).toBe(17)
  })

  test("interpolates wrapped phases and sixteen-bit fixture axes without jumps", () => {
    const previous = create(FixtureStateSchema, {
      fixtureId: "fixture",
      effectRotation: 0.98,
      pan: 0,
      panFine: 0,
      red: 0,
    })
    const current = create(FixtureStateSchema, {
      fixtureId: "fixture",
      effectRotation: 0.02,
      pan: 1,
      panFine: 0,
      red: 200,
    })

    const state = interpolateFixtureState(previous, current, 0.5)

    expect(interpolatePhase(0.98, 0.02, 0.5)).toBeCloseTo(0)
    expect(state.pan).toBe(0)
    expect(state.panFine).toBe(128)
    expect(state.red).toBe(100)
  })

  test("uses a faster attack than release for responsive stable envelopes", () => {
    const attack = followEnvelope(0, 1, 20)
    const release = followEnvelope(1, 0, 20)

    expect(attack).toBeGreaterThan(0.5)
    expect(release).toBeGreaterThan(attack)
  })
})
