import { create } from "@bufbuild/protobuf"
import { describe, expect, it } from "vitest"

import { FixtureStateSchema } from "@/gen/music_auto_show/v1/music_auto_show_pb"
import {
  beamAngleFromZoom,
  beamTargetFromDirection,
  fixtureBrightness,
  fixtureColor,
  physicalAxisValue,
  rotatedEffectDirection,
  strobePatternLevel,
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
    expect(physicalAxisValue(0, 0, -270, 270)).toBe(-270)
    const belowCenter = physicalAxisValue(127, 255, -270, 270)
    const aboveCenter = physicalAxisValue(128, 0, -270, 270)
    expect(belowCenter).toBeCloseTo(-aboveCenter)
    expect(Math.abs(belowCenter)).toBeLessThan(0.005)
    expect(physicalAxisValue(255, 255, -270, 270)).toBe(270)
    expect(physicalAxisValue(0, 0, 270, -270)).toBe(270)
    expect(physicalAxisValue(255, 255, 270, -270)).toBe(-270)
  })

  it("maps live zoom through ordered physical optics", () => {
    expect(beamAngleFromZoom(25, 0, 10, 50)).toBe(10)
    expect(beamAngleFromZoom(25, 255, 10, 50)).toBe(50)
    expect(beamAngleFromZoom(25, 0, 50, 10)).toBe(50)
    expect(beamAngleFromZoom(25, 255, 50, 10)).toBe(10)
    expect(beamAngleFromZoom(25, 128, 0, 0)).toBe(25)
  })

  it("rotates an effect fan around its forward optical axis", () => {
    const direction = { x: 0.4, y: 0.2, z: 1 }
    const rotated = rotatedEffectDirection(direction, 0.25)
    const magnitude = Math.hypot(rotated.x, rotated.y, rotated.z)

    expect(rotated.x).toBeCloseTo(-0.1826, 4)
    expect(rotated.y).toBeCloseTo(0.3651, 4)
    expect(rotated.z).toBeGreaterThan(0.9)
    expect(magnitude).toBeCloseTo(1)
  })

  it("shows continuous Techno Derby strobe as steady under reduced motion", () => {
    expect(
      Array.from({ length: 16 }, (_, emitterIndex) =>
        strobePatternLevel(18, emitterIndex, 16, 0, 0.5, true),
      ),
    ).toEqual(Array.from({ length: 16 }, () => 0.6))
  })

  it("previews Techno Derby chase patterns as deterministic selective groups", () => {
    const firstFrame = Array.from({ length: 16 }, (_, emitterIndex) =>
      strobePatternLevel(1, emitterIndex, 16, 0.5, 0.5),
    )
    const repeatedFrame = Array.from({ length: 16 }, (_, emitterIndex) =>
      strobePatternLevel(1, emitterIndex, 16, 0.5, 0.5),
    )

    expect(firstFrame).toEqual(repeatedFrame)
    expect(firstFrame.filter((level) => level === 1)).toHaveLength(1)
    expect(strobePatternLevel(0, 0, 16, 0.5, 0.5)).toBe(0)
  })

  it("uses configured speed for continuous Techno Derby strobe timing", () => {
    expect(strobePatternLevel(18, 0, 16, 0.1, 0)).toBe(1)
    expect(strobePatternLevel(18, 0, 16, 0.1, 1)).toBe(0)
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
