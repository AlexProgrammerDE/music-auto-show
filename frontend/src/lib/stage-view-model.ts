import type { FixtureState, FixtureVisual } from "@/gen/music_auto_show/v1/music_auto_show_pb"

export type StageColor = {
  readonly red: number
  readonly green: number
  readonly blue: number
}

export type StagePoint = {
  readonly x: number
  readonly y: number
  readonly z: number
}

function clampChannel(value: number) {
  return Math.max(0, Math.min(255, value))
}

export function fixtureColor(state: FixtureState): StageColor {
  return {
    red: clampChannel(
      state.red + state.white + state.amber + state.uv * 0.35 + state.magenta + state.yellow,
    ),
    green: clampChannel(state.green + state.white + state.amber * 0.55 + state.cyan + state.yellow),
    blue: clampChannel(state.blue + state.white + state.uv + state.cyan + state.magenta),
  }
}

export function fixtureBrightness(state: FixtureState) {
  const color = fixtureColor(state)
  const emitterLevel = Math.max(color.red, color.green, color.blue) / 255
  return emitterLevel * (state.dimmer / 255)
}

export function physicalAxisValue(value: number, minimum: number, maximum: number) {
  const normalized = Math.max(0, Math.min(255, value)) / 255
  return minimum + (maximum - minimum) * normalized
}

export function movingBeamTarget(
  origin: StagePoint,
  visual: FixtureVisual,
  state: FixtureState,
  length = 6,
): StagePoint {
  const pan =
    (physicalAxisValue(
      state.pan + state.panFine / 255,
      visual.panMinDegrees,
      visual.panMaxDegrees,
    ) *
      Math.PI) /
    180
  const tilt =
    (physicalAxisValue(
      state.tilt + state.tiltFine / 255,
      visual.tiltMinDegrees,
      visual.tiltMaxDegrees,
    ) *
      Math.PI) /
    180
  const horizontal = Math.sin(tilt)
  return {
    x: origin.x + Math.sin(pan) * horizontal * length,
    y: origin.y - Math.cos(tilt) * length,
    z: origin.z + Math.cos(pan) * horizontal * length,
  }
}

export function effectBeamTarget(
  origin: StagePoint,
  rotation: number,
  beam: number,
  beamCount: number,
): StagePoint {
  const angle = rotation * Math.PI * 2 + (beam / Math.max(1, beamCount)) * Math.PI * 2
  const radius = 2.4 + (beam % 3) * 0.45
  return {
    x: origin.x + Math.sin(angle) * radius,
    y: 0,
    z: origin.z + Math.cos(angle) * radius,
  }
}

export function fixedBeamTarget(origin: StagePoint): StagePoint {
  return { x: origin.x, y: 0, z: origin.z + 0.7 }
}
