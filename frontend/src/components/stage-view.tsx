import { useEffect, useRef } from "react"

import type {
  FixtureConfig,
  FixtureProfile,
  FixtureState,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"

function rgb(state: FixtureState) {
  return `rgb(${state.red} ${state.green} ${state.blue})`
}

function themeColor(variable: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(variable).trim()
}

function fixtureBrightness(state: FixtureState) {
  return ((state.red + state.green + state.blue) / 765) * (state.dimmer / 255)
}

export function StageView({
  fixtures,
  profiles,
  states,
}: {
  readonly fixtures: readonly FixtureConfig[]
  readonly profiles: readonly FixtureProfile[]
  readonly states: readonly FixtureState[]
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const stateById = new Map(states.map((state) => [state.fixtureId, state]))
    const profileByName = new Map(profiles.map((profile) => [profile.name, profile]))
    const ordered = fixtures.toSorted((left, right) => left.position - right.position)

    const render = () => {
      const bounds = canvas.getBoundingClientRect()
      const scale = window.devicePixelRatio || 1
      canvas.width = Math.max(1, Math.floor(bounds.width * scale))
      canvas.height = Math.max(1, Math.floor(bounds.height * scale))
      const context = canvas.getContext("2d")
      if (!context) return
      context.scale(scale, scale)
      const width = bounds.width
      const height = bounds.height

      context.fillStyle = themeColor("--background")
      context.fillRect(0, 0, width, height)
      const horizon = height * 0.33
      context.fillStyle = themeColor("--card")
      context.beginPath()
      context.moveTo(width * 0.12, height)
      context.lineTo(width * 0.28, horizon)
      context.lineTo(width * 0.72, horizon)
      context.lineTo(width * 0.88, height)
      context.closePath()
      context.fill()

      context.globalAlpha = 0.35
      context.strokeStyle = themeColor("--border")
      context.lineWidth = 1
      for (let line = 1; line < 8; line += 1) {
        const y = horizon + (height - horizon) * (line / 8) ** 1.45
        const progress = (y - horizon) / (height - horizon)
        context.beginPath()
        context.moveTo(width * (0.28 - progress * 0.16), y)
        context.lineTo(width * (0.72 + progress * 0.16), y)
        context.stroke()
      }
      for (let line = -5; line <= 5; line += 1) {
        const topX = width * 0.5 + line * width * 0.044
        const bottomX = width * 0.5 + line * width * 0.076
        context.beginPath()
        context.moveTo(topX, horizon)
        context.lineTo(bottomX, height)
        context.stroke()
      }
      context.globalAlpha = 1

      context.strokeStyle = themeColor("--muted-foreground")
      context.lineWidth = 5
      context.beginPath()
      context.moveTo(width * 0.17, horizon * 0.56)
      context.lineTo(width * 0.83, horizon * 0.56)
      context.moveTo(width * 0.18, horizon * 0.56)
      context.lineTo(width * 0.18, horizon)
      context.moveTo(width * 0.82, horizon * 0.56)
      context.lineTo(width * 0.82, horizon)
      context.stroke()

      if (ordered.length === 0) {
        context.fillStyle = themeColor("--muted-foreground")
        context.font = "13px Public Sans Variable"
        context.textAlign = "center"
        context.fillText("Add fixtures to preview the stage", width / 2, height / 2)
        return
      }

      ordered.forEach((fixture, position) => {
        const x = width * (0.24 + (position + 0.5) * (0.52 / ordered.length))
        const y = horizon * 0.56
        const state = stateById.get(fixture.id)
        const profile = profileByName.get(fixture.profileName)
        const effectFixture = profile?.fixtureType.toLowerCase() === "effect"
        const activeState = state ?? {
          red: 0,
          green: 0,
          blue: 0,
          dimmer: 0,
          pan: 128,
          tilt: 128,
          strobe: 0,
          effect: 0,
        }
        const brightness = state ? fixtureBrightness(state) : 0
        const beamColor = state ? rgb(state) : themeColor("--muted")

        if (brightness > 0.03) {
          context.save()
          context.globalAlpha = Math.min(0.54, 0.12 + brightness * 0.45)
          context.fillStyle = beamColor
          if (effectFixture) {
            const rotation = (activeState.effect / 255) * Math.PI * 2
            for (let beam = 0; beam < 4; beam += 1) {
              const angle = rotation + (beam / 4) * Math.PI * 2
              const endX = x + Math.sin(angle) * width * 0.15
              const endY = height * 0.82 + Math.cos(angle) * height * 0.05
              context.beginPath()
              context.moveTo(x - 3, y + 9)
              context.lineTo(endX - 8, endY)
              context.lineTo(endX + 8, endY)
              context.lineTo(x + 3, y + 9)
              context.closePath()
              context.fill()
            }
          } else {
            const pan = (activeState.pan - 128) / 128
            const tilt = (activeState.tilt - 128) / 128
            const endX = x + pan * width * 0.17
            const endY = height * (0.78 + tilt * 0.1)
            context.beginPath()
            context.moveTo(x - 4, y + 10)
            context.lineTo(endX - 17, endY)
            context.lineTo(endX + 17, endY)
            context.lineTo(x + 4, y + 10)
            context.closePath()
            context.fill()
          }
          context.restore()
        }

        context.fillStyle = themeColor("--card")
        context.strokeStyle = themeColor("--border")
        context.lineWidth = 1
        context.fillRect(x - 10, y - 3, 20, 16)
        context.strokeRect(x - 10, y - 3, 20, 16)
        context.fillStyle = brightness > 0.03 ? beamColor : themeColor("--background")
        context.beginPath()
        context.arc(x, y + 10, 4, 0, Math.PI * 2)
        context.fill()
        context.fillStyle = themeColor("--muted-foreground")
        context.font = "10px Public Sans Variable"
        context.textAlign = "center"
        context.fillText(fixture.name, x, y - 9)
      })
    }

    render()
    const observer = new ResizeObserver(render)
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [fixtures, profiles, states])

  return (
    <canvas
      ref={canvasRef}
      className="block h-80 w-full bg-background"
      aria-label="Live stage preview showing fixtures, movement, color, intensity, and beams"
    />
  )
}
