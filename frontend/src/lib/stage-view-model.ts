import type { FixtureState } from "@/gen/music_auto_show/v1/music_auto_show_pb"

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

export function physicalAxisValue(coarse: number, fine: number, minimum: number, maximum: number) {
  const coarseValue = Math.max(0, Math.min(255, Math.round(coarse)))
  const fineValue = Math.max(0, Math.min(255, Math.round(fine)))
  const normalized = (coarseValue * 256 + fineValue) / 65_535
  return minimum + (maximum - minimum) * normalized
}

export function rotatedEffectDirection(direction: StagePoint, phase: number): StagePoint {
  const magnitude = Math.hypot(direction.x, direction.y, direction.z)
  if (magnitude <= Number.EPSILON) return { x: 0, y: 0, z: 1 }

  const angle = ((phase % 1) + 1) % 1
  const radians = angle * Math.PI * 2
  const cosine = Math.cos(radians)
  const sine = Math.sin(radians)
  return {
    x: (direction.x * cosine - direction.y * sine) / magnitude,
    y: (direction.x * sine + direction.y * cosine) / magnitude,
    z: direction.z / magnitude,
  }
}

export function beamTargetFromDirection(
  origin: StagePoint,
  direction: StagePoint,
  maxLength = 7,
  floorY = 0,
): StagePoint {
  const magnitude = Math.hypot(direction.x, direction.y, direction.z)
  if (magnitude <= Number.EPSILON) return origin

  const unit = {
    x: direction.x / magnitude,
    y: direction.y / magnitude,
    z: direction.z / magnitude,
  }
  const floorDistance = unit.y < -0.001 ? Math.max(0, origin.y - floorY) / -unit.y : maxLength
  const length = Math.min(maxLength, Math.max(0.05, floorDistance))
  return {
    x: origin.x + unit.x * length,
    y: origin.y + unit.y * length,
    z: origin.z + unit.z * length,
  }
}
