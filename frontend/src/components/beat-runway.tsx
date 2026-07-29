import { useEffect, useEffectEvent, useRef } from "react"

import { Badge } from "@/components/ui/badge"
import type {
  AudioAnalysis,
  EffectRuntimeStatus,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import {
  projectBeatRunwayFrame,
  reconcileBeatRunwaySample,
  type BeatRunwayFrame,
  type BeatRunwaySample,
} from "@/lib/beat-runway"
import { resizeCanvas, type CanvasSurface } from "@/lib/canvas"

interface RunwayPalette {
  readonly accent: string
  readonly bass: string
  readonly border: string
  readonly foreground: string
  readonly high: string
  readonly mid: string
  readonly muted: string
}

function themeColor(variable: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(variable).trim()
}

function readPalette(): RunwayPalette {
  return {
    accent: themeColor("--chart-2"),
    bass: themeColor("--chart-2"),
    border: themeColor("--border"),
    foreground: themeColor("--foreground"),
    high: themeColor("--chart-4"),
    mid: themeColor("--chart-3"),
    muted: themeColor("--muted-foreground"),
  }
}

function drawSignalHistory(
  surface: CanvasSurface,
  analysis: AudioAnalysis | undefined,
  palette: RunwayPalette,
) {
  const { context, width, height } = surface
  const frames = analysis?.history ?? []
  if (frames.length < 2) return
  const channels = [
    { color: palette.bass, value: (index: number) => frames[index]?.bass ?? 0 },
    { color: palette.mid, value: (index: number) => frames[index]?.mid ?? 0 },
    { color: palette.high, value: (index: number) => frames[index]?.high ?? 0 },
  ]
  context.save()
  context.globalAlpha = 0.2
  for (const channel of channels) {
    context.beginPath()
    context.strokeStyle = channel.color
    context.lineWidth = 1.25
    frames.forEach((_, index) => {
      const x = (index / (frames.length - 1)) * width
      const y = height * 0.78 - channel.value(index) * height * 0.42
      if (index === 0) context.moveTo(x, y)
      else context.lineTo(x, y)
    })
    context.stroke()
  }
  context.restore()
}

function drawRunway(
  surface: CanvasSurface,
  frame: BeatRunwayFrame,
  analysis: AudioAnalysis | undefined,
  palette: RunwayPalette,
) {
  const { context, width, height } = surface
  const centerX = width / 2
  const baselineY = height * 0.58
  const spacing = width / (frame.meter * 2 + 2)
  context.clearRect(0, 0, width, height)
  drawSignalHistory(surface, analysis, palette)

  context.save()
  context.strokeStyle = palette.border
  context.globalAlpha = 0.65
  context.lineWidth = 1
  context.beginPath()
  context.moveTo(0, baselineY)
  context.lineTo(width, baselineY)
  context.stroke()

  const onset = analysis?.onsetHistory ?? []
  if (onset.length > 1) {
    context.strokeStyle = palette.accent
    context.globalAlpha = 0.5
    context.beginPath()
    onset.forEach((value, index) => {
      const x = (index / (onset.length - 1)) * centerX
      const y = baselineY - Math.max(0, Math.min(1, value)) * height * 0.25
      if (index === 0) context.moveTo(x, y)
      else context.lineTo(x, y)
    })
    context.stroke()
  }

  context.font = '600 10px "Public Sans Variable", sans-serif'
  context.textAlign = "center"
  context.textBaseline = "middle"
  for (let offset = -frame.meter - 1; offset <= frame.meter + 1; offset += 1) {
    const x = centerX + (offset - frame.beatPosition) * spacing
    if (x < -8 || x > width + 8) continue
    const beatNumber =
      ((((frame.beatIndex - 1 + offset) % frame.meter) + frame.meter) % frame.meter) + 1
    const downbeat = beatNumber === 1
    const future = offset > 0
    context.globalAlpha = downbeat ? 0.85 : 0.5
    context.strokeStyle = downbeat ? palette.foreground : palette.muted
    context.lineWidth = downbeat ? 2 : 1
    context.setLineDash(future ? [3, 4] : [])
    context.beginPath()
    context.moveTo(x, baselineY - (downbeat ? 34 : 22))
    context.lineTo(x, baselineY + (downbeat ? 28 : 18))
    context.stroke()
    context.setLineDash([])
    context.fillStyle = downbeat ? palette.foreground : palette.muted
    context.fillText(String(beatNumber), x, baselineY + 38)
  }

  context.globalAlpha = frame.active ? 1 : 0.45
  context.strokeStyle = palette.accent
  context.lineWidth = 2
  context.beginPath()
  context.moveTo(centerX, baselineY - 48)
  context.lineTo(centerX, baselineY + 30)
  context.stroke()
  context.fillStyle = palette.accent
  context.beginPath()
  context.moveTo(centerX - 5, baselineY - 48)
  context.lineTo(centerX + 5, baselineY - 48)
  context.lineTo(centerX, baselineY - 40)
  context.closePath()
  context.fill()
  context.restore()
}

export function BeatRunway({
  active,
  analysis,
  effectRuntime,
}: {
  readonly active: boolean
  readonly analysis: AudioAnalysis | undefined
  readonly effectRuntime: EffectRuntimeStatus | undefined
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const surfaceRef = useRef<CanvasSurface | undefined>(undefined)
  const paletteRef = useRef<RunwayPalette | undefined>(undefined)
  const analysisRef = useRef(analysis)
  const sampleRef = useRef<BeatRunwaySample>({
    active: false,
    beatIndex: 1,
    beatPosition: 0,
    estimatedBeat: 0,
    meter: 4,
    sampledAt: 0,
    tempo: 0,
  })
  const tempo = analysis?.tempo ?? 0
  const tracking = active && tempo > 0
  const meter = analysis?.meter || 4
  const confidence = analysis?.trackingConfidence ?? 0
  const summary = tracking
    ? `${Math.round(tempo)} BPM, ${meter}/4, beat ${analysis?.beatIndex || 1} of ${meter}, ${Math.round(confidence * 100)} percent tracking confidence`
    : active
      ? "Finding tempo and meter"
      : "Audio stopped"

  const render = useEffectEvent((now: number) => {
    const surface = surfaceRef.current
    const palette = paletteRef.current
    if (!surface || !palette) return
    drawRunway(
      surface,
      projectBeatRunwayFrame(sampleRef.current, now),
      analysisRef.current,
      palette,
    )
  })

  useEffect(() => {
    analysisRef.current = analysis
    const sampledAt = performance.now()
    sampleRef.current = reconcileBeatRunwaySample(sampleRef.current, {
      active: tracking,
      beatIndex: analysis?.beatIndex || 1,
      beatPosition: analysis?.beatPosition ?? 0,
      estimatedBeat: Number(analysis?.estimatedBeat ?? 0n),
      meter,
      sampledAt,
      tempo,
    })
    render(sampledAt)
  }, [analysis, meter, tempo, tracking])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)")
    let animationFrame: number | undefined
    const draw = (now: number) => render(now)
    const animate = (now: number) => {
      draw(now)
      animationFrame = window.requestAnimationFrame(animate)
    }
    const stop = () => {
      if (animationFrame !== undefined) window.cancelAnimationFrame(animationFrame)
      animationFrame = undefined
    }
    const start = () => {
      stop()
      if (tracking && !reducedMotion.matches) {
        animationFrame = window.requestAnimationFrame(animate)
      } else {
        draw(performance.now())
      }
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
    reducedMotion.addEventListener("change", start)
    start()
    return () => {
      stop()
      resizeObserver.disconnect()
      themeObserver.disconnect()
      reducedMotion.removeEventListener("change", start)
    }
  }, [tracking])

  return (
    <div className="grid min-w-0">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b px-4 py-3">
        <div className="flex items-baseline gap-3">
          <span className="font-heading text-3xl font-semibold tabular-nums">
            {tracking ? Math.round(tempo) : "–"}
          </span>
          <span className="text-xs text-muted-foreground">BPM</span>
          <span className="font-heading text-sm font-semibold tabular-nums">
            {tracking ? `${meter}/4` : "Meter"}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={confidence >= 0.45 ? "secondary" : "outline"}>
            {tracking ? `${Math.round(confidence * 100)}% lock` : "Acquiring"}
          </Badge>
          <Badge variant="outline">
            Bar {tracking ? Number(analysis?.estimatedBar ?? 0n) + 1 : "–"}
          </Badge>
          {effectRuntime ? (
            <Badge variant="outline">Cycle {effectRuntime.effectCyclePosition}/32</Badge>
          ) : null}
        </div>
      </div>
      <figure className="relative min-h-44 bg-background">
        <canvas
          ref={canvasRef}
          width={960}
          height={176}
          className="absolute inset-0 size-full"
          aria-label={`Beat runway. ${summary}. Solid markers are observed beats; dashed markers are projected.`}
        />
        <figcaption className="absolute right-3 bottom-2 text-[10px] text-muted-foreground">
          observed · projected
        </figcaption>
      </figure>
    </div>
  )
}
