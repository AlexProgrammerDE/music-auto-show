import { create } from "@bufbuild/protobuf"
import { describe, expect, it } from "vitest"

import {
  FixtureStateSchema,
  FixtureVisualSchema,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import {
  effectBeamTarget,
  fixtureBrightness,
  fixtureColor,
  movingBeamTarget,
  physicalAxisValue,
} from "@/lib/stage-view-model"

describe("stage view model", () => {
  it("combines direct and auxiliary emitters into a preview color", () => {
    const state = create(FixtureStateSchema, {
      red: 255,
      white: 64,
      amber: 32,
      dimmer: 255,
    })
    expect(fixtureColor(state)).toEqual({ red: 255, green: 81.6, blue: 64 })
    expect(fixtureBrightness(state)).toBe(1)
  })

  it("maps DMX values through physical grandMA2 axis ranges", () => {
    expect(physicalAxisValue(0, -270, 270)).toBe(-270)
    expect(physicalAxisValue(127.5, -270, 270)).toBe(0)
    expect(physicalAxisValue(255, -270, 270)).toBe(270)
  })

  it("uses physical pan and tilt metadata for a moving beam", () => {
    const visual = create(FixtureVisualSchema, {
      panMinDegrees: -270,
      panMaxDegrees: 270,
      tiltMinDegrees: -135,
      tiltMaxDegrees: 135,
    })
    const state = create(FixtureStateSchema, {
      pan: 127,
      panFine: 128,
      tilt: 127,
      tiltFine: 128,
    })
    const target = movingBeamTarget({ x: 0, y: 3, z: 0 }, visual, state)
    expect(target.x).toBeCloseTo(0)
    expect(target.y).toBeCloseTo(-3)
    expect(target.z).toBeCloseTo(0)
  })

  it("fans effect emitters according to their parsed count", () => {
    const first = effectBeamTarget({ x: 0, y: 3, z: 0 }, 0.25, 0, 4)
    const opposite = effectBeamTarget({ x: 0, y: 3, z: 0 }, 0.25, 2, 4)
    expect(first.x).toBeCloseTo(2.4)
    expect(first.z).toBeCloseTo(0)
    expect(opposite.x).toBeCloseTo(-3.3)
    expect(opposite.z).toBeCloseTo(0)
  })
})
