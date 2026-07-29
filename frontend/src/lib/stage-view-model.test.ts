import { create } from "@bufbuild/protobuf"
import { describe, expect, it } from "vitest"

import { FixtureStateSchema } from "@/gen/music_auto_show/v1/music_auto_show_pb"
import {
  beamTargetFromDirection,
  fixtureBrightness,
  fixtureColor,
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

  it("stops a transformed beam at the stage floor", () => {
    const target = beamTargetFromDirection({ x: 1, y: 3, z: 2 }, { x: 1, y: -2, z: 1 })
    expect(target.x).toBeCloseTo(2.5)
    expect(target.y).toBeCloseTo(0)
    expect(target.z).toBeCloseTo(3.5)
  })

  it("caps beams that do not point toward the floor", () => {
    const target = beamTargetFromDirection({ x: 0, y: 3, z: 0 }, { x: 0, y: 1, z: 0 }, 5)
    expect(target).toEqual({ x: 0, y: 8, z: 0 })
  })
})
