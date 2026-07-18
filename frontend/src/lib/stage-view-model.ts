import type {
  FixtureConfig,
  FixtureProfile,
  FixtureState,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"

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

export function normalizeFixturePosition(value: number, minimum: number, maximum: number) {
  if (maximum <= minimum) return 0
  return Math.max(-1, Math.min(1, ((value - minimum) / (maximum - minimum)) * 2 - 1))
}

export function movingBeamTarget(
  fixtureX: number,
  fixtureZ: number,
  fixture: FixtureConfig,
  profile: FixtureProfile | undefined,
  state: FixtureState,
): StagePoint {
  const pan = normalizeFixturePosition(state.pan, fixture.panMin, fixture.panMax)
  const tilt = normalizeFixturePosition(state.tilt, fixture.tiltMin, fixture.tiltMax)
  const panTravel = Math.min(360, Math.max(90, profile?.panMaxDegrees || 180))
  const panAngle = pan * ((panTravel * Math.PI) / 360)
  const distance = 2.25 + ((tilt + 1) / 2) * 3
  return {
    x: fixtureX + Math.sin(panAngle) * distance,
    y: 0,
    z: fixtureZ + Math.cos(panAngle) * distance,
  }
}

export function effectBeamTarget(
  fixtureX: number,
  fixtureZ: number,
  rotation: number,
  beam: number,
): StagePoint {
  const angle = rotation * Math.PI * 2 + (beam / 4) * Math.PI * 2
  return {
    x: fixtureX + Math.sin(angle) * 3.4,
    y: 0,
    z: fixtureZ + Math.cos(angle) * 3.4,
  }
}
