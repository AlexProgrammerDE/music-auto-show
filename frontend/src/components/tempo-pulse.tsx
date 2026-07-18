import { useEffect, useEffectEvent, useRef } from "react"

import type { AudioAnalysis } from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { resizeCanvas, type CanvasSurface } from "@/lib/canvas"
import {
  BEATS_PER_BAR,
  projectTempoFrame,
  reconcileTempoSample,
  type TempoFrame,
  type TempoSample,
} from "@/lib/tempo-pulse"

interface PulsePalette {
  readonly accent: string
  readonly border: string
  readonly foreground: string
  readonly muted: string
  readonly secondaryAccent: string
}

function themeColor(variable: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(variable).trim()
}

function readPalette(): PulsePalette {
  return {
    accent: themeColor("--chart-2"),
    border: themeColor("--border"),
    foreground: themeColor("--foreground"),
    muted: themeColor("--muted-foreground"),
    secondaryAccent: themeColor("--chart-1"),
  }
}

function drawMeasure(
  surface: CanvasSurface,
  frame: TempoFrame,
  confidence: number,
  palette: PulsePalette,
) {
  const { context, width, height } = surface
  const centerX = width / 2
  const centerY = height / 2
  const radius = Math.max(18, Math.min(width, height) / 2 - 24)
  const phaseRadius = radius * 0.68
  const startAngle = -Math.PI / 2
  const fullCircle = Math.PI * 2
  const beatAngle = fullCircle / BEATS_PER_BAR
  const playheadAngle = startAngle + frame.beatPosition * fullCircle
  const signalStrength = 0.45 + Math.max(0, Math.min(1, confidence)) * 0.55

  context.clearRect(0, 0, width, height)
  context.save()

  context.globalAlpha = 0.2
  context.fillStyle = palette.border
  context.beginPath()
  context.arc(centerX, centerY, phaseRadius, 0, fullCircle)
  context.fill()

  if (frame.active && frame.beatPosition > 0) {
    context.globalAlpha = 0.28 * signalStrength
    context.fillStyle = palette.secondaryAccent
    context.beginPath()
    context.moveTo(centerX, centerY)
    context.arc(centerX, centerY, phaseRadius, startAngle, playheadAngle)
    context.closePath()
    context.fill()
  }

  context.globalAlpha = 0.65
  context.strokeStyle = palette.border
  context.lineWidth = 1
  context.beginPath()
  context.arc(centerX, centerY, phaseRadius, 0, fullCircle)
  context.stroke()

  for (let beat = 0; beat < BEATS_PER_BAR; beat += 1) {
    const segmentStart = startAngle + beat * beatAngle + 0.07
    const segmentEnd = startAngle + (beat + 1) * beatAngle - 0.07
    const activeBeat = frame.active && beat === frame.beatIndex
    const downbeat = beat === 0
    context.globalAlpha = activeBeat ? 0.95 : downbeat ? 0.55 : 0.3
    context.strokeStyle = activeBeat
      ? downbeat
        ? palette.foreground
        : palette.accent
      : palette.muted
    context.lineWidth = activeBeat ? 6 : 4
    context.beginPath()
    context.arc(centerX, centerY, radius, segmentStart, segmentEnd)
    context.stroke()
  }

  if (frame.active) {
    const downbeat = frame.beatIndex === 0
    if (frame.impact > 0.01) {
      context.globalAlpha = frame.impact * 0.45 * signalStrength
      context.strokeStyle = downbeat ? palette.foreground : palette.accent
      context.lineWidth = downbeat ? 2.5 : 2
      context.beginPath()
      context.arc(centerX, centerY, radius + 5 + frame.impact * (downbeat ? 10 : 7), 0, fullCircle)
      context.stroke()
    }

    context.globalAlpha = 0.95
    context.strokeStyle = palette.foreground
    context.lineWidth = 1.5
    context.beginPath()
    context.moveTo(centerX, centerY)
    context.lineTo(
      centerX + Math.cos(playheadAngle) * phaseRadius,
      centerY + Math.sin(playheadAngle) * phaseRadius,
    )
    context.stroke()

    context.fillStyle = palette.foreground
    context.beginPath()
    context.arc(
      centerX + Math.cos(playheadAngle) * phaseRadius,
      centerY + Math.sin(playheadAngle) * phaseRadius,
      3,
      0,
      fullCircle,
    )
    context.fill()
  }

  context.font = '600 10px "Public Sans Variable", sans-serif'
  context.textAlign = "center"
  context.textBaseline = "middle"
  for (let beat = 0; beat < BEATS_PER_BAR; beat += 1) {
    const labelAngle = startAngle + beat * beatAngle
    const activeBeat = frame.active && beat === frame.beatIndex
    context.globalAlpha = activeBeat ? 0.95 : 0.5
    context.fillStyle = activeBeat ? palette.foreground : palette.muted
    context.fillText(
      `${beat + 1}`,
      centerX + Math.cos(labelAngle) * (radius + 13),
      centerY + Math.sin(labelAngle) * (radius + 13),
    )
  }

  context.restore()
}

export function TempoPulse({
  active,
  analysis,
}: {
  readonly active: boolean
  readonly analysis: AudioAnalysis | undefined
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const surfaceRef = useRef<CanvasSurface | undefined>(undefined)
  const paletteRef = useRef<PulsePalette | undefined>(undefined)
  const sampleRef = useRef<TempoSample>({
    active: false,
    barPosition: 0,
    beatPosition: 0,
    sampledAt: 0,
    tempo: 0,
  })
  const confidenceRef = useRef(0)
  const tempo = analysis?.tempo ?? 0
  const beatPosition = analysis?.beatPosition ?? 0
  const barPosition = analysis?.barPosition ?? 0
  const confidence = analysis?.beatConfidence ?? 0
  const tracking = active && tempo > 0
  const roundedTempo = tracking ? Math.round(tempo) : undefined
  const confidencePercent = Math.round(Math.max(0, Math.min(1, confidence)) * 100)
  const status = !active ? "Audio stopped" : tracking ? "Tracking beat phase" : "Finding tempo"
  const description = tracking
    ? `${roundedTempo} BPM, four-beat measure, ${confidencePercent}% beat signal`
    : status

  const render = useEffectEvent((now: number) => {
    const surface = surfaceRef.current
    const palette = paletteRef.current
    if (!surface || !palette) return
    drawMeasure(surface, projectTempoFrame(sampleRef.current, now), confidenceRef.current, palette)
  })

  useEffect(() => {
    const sampledAt = performance.now()
    sampleRef.current = reconcileTempoSample(sampleRef.current, {
      active,
      barPosition,
      beatPosition,
      sampledAt,
      tempo,
    })
    confidenceRef.current = confidence
    render(sampledAt)
  }, [active, barPosition, beatPosition, confidence, tempo])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const motion = window.matchMedia("(prefers-reduced-motion: reduce)")
    let animationFrame: number | undefined

    const draw = (now: number) => render(now)
    const animate = (now: number) => {
      draw(now)
      animationFrame = window.requestAnimationFrame(animate)
    }
    const startAnimation = () => {
      if (motion.matches || !tracking) {
        draw(performance.now())
      } else if (animationFrame === undefined) {
        animationFrame = window.requestAnimationFrame(animate)
      }
    }
    const stopAnimation = () => {
      if (animationFrame === undefined) return
      window.cancelAnimationFrame(animationFrame)
      animationFrame = undefined
    }
    const handleMotionChange = () => {
      stopAnimation()
      startAnimation()
    }
    const resizeObserver = new ResizeObserver(([entry]) => {
      if (!entry) return
      surfaceRef.current = resizeCanvas(canvas, entry.contentRect.width, entry.contentRect.height)
      draw(performance.now())
    })
    const themeObserver = new MutationObserver(() => {
      paletteRef.current = readPalette()
      draw(performance.now())
    })

    paletteRef.current = readPalette()
    resizeObserver.observe(canvas)
    themeObserver.observe(document.documentElement, { attributeFilter: ["class"] })
    motion.addEventListener("change", handleMotionChange)
    startAnimation()

    return () => {
      stopAnimation()
      resizeObserver.disconnect()
      themeObserver.disconnect()
      motion.removeEventListener("change", handleMotionChange)
    }
  }, [tracking])

  return (
    <div className="grid min-h-36 grid-cols-[8.5rem_minmax(0,1fr)] border-b sm:col-span-3 lg:col-span-1 lg:border-r lg:border-b-0">
      <div className="flex flex-col justify-center border-r px-4 py-3">
        <p className="font-heading text-xs font-medium text-muted-foreground">Tempo</p>
        <div className="mt-1 flex items-baseline gap-1.5">
          <strong className="font-heading text-4xl leading-none font-semibold tracking-tight tabular-nums">
            {roundedTempo ?? "--"}
          </strong>
          <span className="text-[11px] text-muted-foreground">BPM</span>
        </div>
        <p className="mt-2 text-[11px] text-muted-foreground">{status}</p>
      </div>
      <figure className="relative min-h-36 overflow-hidden bg-background">
        <canvas ref={canvasRef} className="absolute inset-0 size-full" aria-label={description} />
        <figcaption className="sr-only">{description}</figcaption>
      </figure>
    </div>
  )
}
