import { create } from "@bufbuild/protobuf"
import { describe, expect, it } from "vitest"

import {
  FixtureConfigSchema,
  FixtureStateSchema,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import {
  effectBeamTarget,
  fixtureBrightness,
  fixtureColor,
  movingBeamTarget,
  normalizeFixturePosition,
} from "@/lib/stage-view-model"

describe("stage view model", () => {
  it("treats a saturated color as full emitter intensity", () => {
    const state = create(FixtureStateSchema, { red: 255, dimmer: 255 })
    expect(fixtureColor(state)).toEqual({ red: 255, green: 0, blue: 0 })
    expect(fixtureBrightness(state)).toBe(1)
  })

  it("includes auxiliary color emitters", () => {
    const state = create(FixtureStateSchema, { white: 128, amber: 128, dimmer: 255 })
    expect(fixtureColor(state)).toEqual({ red: 255, green: 198.4, blue: 128 })
    expect(fixtureBrightness(state)).toBe(1)
  })

  it("normalizes configured fixture limits", () => {
    expect(normalizeFixturePosition(32, 32, 224)).toBe(-1)
    expect(normalizeFixturePosition(128, 32, 224)).toBe(0)
    expect(normalizeFixturePosition(224, 32, 224)).toBe(1)
  })

  it("maps movement and effect rotation into floor targets", () => {
    const fixture = create(FixtureConfigSchema, {
      panMin: 32,
      panMax: 224,
      tiltMin: 32,
      tiltMax: 224,
    })
    const state = create(FixtureStateSchema, { pan: 128, tilt: 128 })
    expect(movingBeamTarget(0, -1, fixture, undefined, state)).toEqual({
      x: 0,
      y: 0,
      z: 2.75,
    })
    const target = effectBeamTarget(0, 0, 0.25, 0)
    expect(target.x).toBeCloseTo(3.4)
    expect(target.z).toBeCloseTo(0)
  })
})
