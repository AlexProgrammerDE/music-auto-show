import { ArrowsInIcon, ArrowsOutIcon } from "@phosphor-icons/react"
import { useQueryState } from "nuqs"
import { useEffect, useEffectEvent, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  MusicSection,
  type AudioAnalysis,
  type EffectRuntimeStatus,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { resizeCanvas, type CanvasSurface } from "@/lib/canvas"
import { ambientPresetParser } from "@/lib/dashboard-search"
import { formatEnumLabel } from "@/lib/format"
import {
  LIVE_HISTORY_DURATION_MS,
  latestLiveFrame,
  liveSpectrogramFrames,
  projectBeat,
  type LiveSpectrogramFrame,
} from "@/lib/live-frame-store"
import { magmaColor } from "@/lib/perceptual-colormap"

type AmbientPreset = "radial" | "led" | "mirror" | "peak" | "luminance" | "waterfall"

interface WaterfallMotion {
  readonly frames: readonly LiveSpectrogramFrame[]
  readonly now: number
}

function themeColor(variable: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(variable).trim()
}

function clear(context: CanvasRenderingContext2D, width: number, height: number) {
  context.clearRect(0, 0, width, height)
  context.fillStyle = themeColor("--background")
  context.fillRect(0, 0, width, height)
}

function drawRadial(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  values: readonly number[],
  phase: number,
) {
  const centerX = width / 2
  const centerY = height / 2
  const baseRadius = Math.min(width, height) * 0.18
  const maximum = Math.min(width, height) * 0.28
  context.save()
  context.translate(centerX, centerY)
  values.forEach((value, index) => {
    const angle = (index / Math.max(1, values.length)) * Math.PI * 2 - Math.PI / 2
    const length = 8 + value * maximum
    context.strokeStyle = magmaColor(value * 0.85 + phase * 0.15)
    context.lineWidth = Math.max(2, Math.min(width, height) / 180)
    context.beginPath()
    context.moveTo(Math.cos(angle) * baseRadius, Math.sin(angle) * baseRadius)
    context.lineTo(Math.cos(angle) * (baseRadius + length), Math.sin(angle) * (baseRadius + length))
    context.stroke()
  })
  context.strokeStyle = themeColor("--foreground")
  context.globalAlpha = 0.3
  context.lineWidth = 1
  context.beginPath()
  context.arc(0, 0, baseRadius, 0, Math.PI * 2)
  context.stroke()
  context.restore()
}

function drawLedGrid(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  values: readonly number[],
) {
  const columns = Math.max(12, Math.min(32, values.length))
  const rows = 12
  const gap = 3
  const cellWidth = (width - gap * (columns + 1)) / columns
  const cellHeight = (height - gap * (rows + 1)) / rows
  for (let column = 0; column < columns; column += 1) {
    const value = values[Math.floor((column / columns) * values.length)] ?? 0
    for (let row = 0; row < rows; row += 1) {
      const threshold = 1 - row / rows
      const active = value >= threshold
      context.fillStyle = active ? magmaColor(value) : themeColor("--muted")
      context.globalAlpha = active ? 0.95 : 0.2
      context.fillRect(
        gap + column * (cellWidth + gap),
        gap + row * (cellHeight + gap),
        cellWidth,
        cellHeight,
      )
    }
  }
  context.globalAlpha = 1
}

function drawMirror(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  values: readonly number[],
) {
  const center = height / 2
  const barWidth = width / Math.max(1, values.length)
  values.forEach((value, index) => {
    const barHeight = value * height * 0.42
    context.fillStyle = magmaColor(value)
    context.fillRect(index * barWidth, center - barHeight, Math.max(1, barWidth - 2), barHeight)
    context.globalAlpha = 0.45
    context.fillRect(index * barWidth, center, Math.max(1, barWidth - 2), barHeight)
    context.globalAlpha = 1
  })
}

function drawPeakLine(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  values: readonly number[],
) {
  context.beginPath()
  context.strokeStyle = themeColor("--foreground")
  context.lineWidth = 2
  values.forEach((value, index) => {
    const x = (index / Math.max(1, values.length - 1)) * width
    const y = height - 20 - value * (height - 40)
    if (index === 0) context.moveTo(x, y)
    else context.lineTo(x, y)
  })
  context.stroke()
  context.globalAlpha = 0.25
  context.strokeStyle = themeColor("--chart-2")
  context.lineWidth = 8
  context.stroke()
  context.globalAlpha = 1
}

function drawLuminance(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  values: readonly number[],
) {
  const barWidth = width / Math.max(1, values.length)
  values.forEach((value, index) => {
    context.fillStyle = themeColor("--foreground")
    context.globalAlpha = 0.12 + value * 0.88
    const barHeight = 12 + value * (height - 24)
    context.fillRect(index * barWidth, height - barHeight, Math.max(1, barWidth - 2), barHeight)
  })
  context.globalAlpha = 1
}

function drawWaterfall(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  motion: WaterfallMotion,
) {
  const { frames, now } = motion
  if (frames.length === 0) return
  frames.forEach((frame, row) => {
    const nextCapturedAt = frames[row + 1]?.capturedAt ?? now
    const intervalStart = Math.max(frame.capturedAt, now - LIVE_HISTORY_DURATION_MS)
    const intervalEnd = Math.min(now, nextCapturedAt)
    if (intervalEnd <= intervalStart) return
    const rowTop = height - ((now - intervalStart) / LIVE_HISTORY_DURATION_MS) * height
    const rowBottom = height - ((now - intervalEnd) / LIVE_HISTORY_DURATION_MS) * height
    const rowHeight = Math.max(1, rowBottom - rowTop)
    const binWidth = width / Math.max(1, frame.bins.length)
    frame.bins.forEach((value, bin) => {
      context.fillStyle = magmaColor(value)
      context.fillRect(bin * binWidth, rowTop, Math.ceil(binWidth), Math.ceil(rowHeight))
    })
  })
}

function drawAmbient(
  surface: CanvasSurface,
  preset: AmbientPreset,
  liveSpectrum: readonly number[],
  beatPosition: number,
  waterfall: WaterfallMotion,
) {
  const { context, width, height } = surface
  const values = liveSpectrum
  clear(context, width, height)
  switch (preset) {
    case "led":
      drawLedGrid(context, width, height, values)
      break
    case "mirror":
      drawMirror(context, width, height, values)
      break
    case "peak":
      drawPeakLine(context, width, height, values)
      break
    case "luminance":
      drawLuminance(context, width, height, values)
      break
    case "waterfall":
      drawWaterfall(context, width, height, waterfall)
      break
    default:
      drawRadial(context, width, height, values, beatPosition)
  }
}

export default function AmbientVisualizer({
  analysis,
  effectRuntime,
}: {
  readonly analysis: AudioAnalysis | undefined
  readonly effectRuntime: EffectRuntimeStatus | undefined
}) {
  const [preset, setPreset] = useQueryState(
    "ambient",
    ambientPresetParser.withOptions({ history: "push" }),
  )
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const surfaceRef = useRef<CanvasSurface | undefined>(undefined)
  const [fullscreen, setFullscreen] = useState(false)
  const render = useEffectEvent((now: number) => {
    const surface = surfaceRef.current
    if (!surface) return
    const latest = latestLiveFrame()
    const liveAudio = latest?.frame.audio
    const beat =
      latest?.frame.audio === undefined
        ? undefined
        : projectBeat(latest.frame.audio, latest.capturedAt, now)
    drawAmbient(surface, preset, liveAudio?.spectrum ?? [], beat?.beatPosition ?? 0, {
      frames: liveSpectrogramFrames(),
      now,
    })
  })
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    let animationFrame = 0
    const animate = (now: number) => {
      render(now)
      animationFrame = window.requestAnimationFrame(animate)
    }
    const resizeObserver = new ResizeObserver(([entry]) => {
      if (!entry) return
      surfaceRef.current = resizeCanvas(canvas, entry.contentRect.width, entry.contentRect.height)
      render(performance.now())
    })
    const themeObserver = new MutationObserver(() => render(performance.now()))
    resizeObserver.observe(canvas)
    themeObserver.observe(document.documentElement, { attributeFilter: ["class"] })
    animationFrame = window.requestAnimationFrame(animate)
    return () => {
      window.cancelAnimationFrame(animationFrame)
      resizeObserver.disconnect()
      themeObserver.disconnect()
    }
  }, [])
  useEffect(() => {
    const handleFullscreen = () =>
      setFullscreen(document.fullscreenElement === containerRef.current)
    document.addEventListener("fullscreenchange", handleFullscreen)
    return () => document.removeEventListener("fullscreenchange", handleFullscreen)
  }, [])
  const section = formatEnumLabel(
    MusicSection[analysis?.section ?? MusicSection.UNSPECIFIED] ?? "Listening",
  )
  return (
    <section ref={containerRef} className="grid min-w-0 border bg-card">
      <div className="flex min-w-0 flex-col gap-3 border-b p-3 lg:flex-row lg:items-center lg:justify-between">
        <Tabs
          value={preset}
          onValueChange={(value) => void setPreset(value as typeof preset, { history: "push" })}
          className="min-w-0"
        >
          <TabsList variant="line" className="w-full max-w-full justify-start overflow-x-auto">
            <TabsTrigger value="radial">Radial</TabsTrigger>
            <TabsTrigger value="led">LED grid</TabsTrigger>
            <TabsTrigger value="mirror">Mirror</TabsTrigger>
            <TabsTrigger value="peak">Peak line</TabsTrigger>
            <TabsTrigger value="luminance">Luminance</TabsTrigger>
            <TabsTrigger value="waterfall">Waterfall</TabsTrigger>
          </TabsList>
        </Tabs>
        <Button
          size="sm"
          variant="outline"
          className="self-start"
          onClick={() => {
            if (fullscreen) void document.exitFullscreen()
            else void containerRef.current?.requestFullscreen()
          }}
        >
          {fullscreen ? (
            <ArrowsInIcon data-icon="inline-start" aria-hidden="true" />
          ) : (
            <ArrowsOutIcon data-icon="inline-start" aria-hidden="true" />
          )}
          {fullscreen ? "Exit full screen" : "Full screen"}
        </Button>
      </div>
      <figure className="relative min-h-[28rem] overflow-hidden bg-background lg:min-h-[38rem]">
        <canvas
          ref={canvasRef}
          width={1440}
          height={720}
          className="absolute inset-0 size-full"
          aria-label={`${formatEnumLabel(preset)} ambient audio visualizer. Current section ${section}, energy ${Math.round((analysis?.energy ?? 0) * 100)} percent.`}
        />
        <figcaption className="absolute inset-x-0 bottom-0 flex flex-wrap items-end justify-between gap-3 border-t bg-background/85 px-4 py-3 backdrop-blur-sm">
          <div>
            <p className="font-heading text-xs font-semibold">{section}</p>
            <p className="text-[10px] text-muted-foreground">
              {Math.round((analysis?.sectionConfidence ?? 0) * 100)}% structure confidence
            </p>
          </div>
          <div className="text-right">
            <p className="font-heading text-xs font-semibold">
              Cycle {effectRuntime?.effectCyclePosition ?? 1}/32
            </p>
            <p className="text-[10px] text-muted-foreground">
              Reduced motion follows system preference
            </p>
          </div>
        </figcaption>
      </figure>
    </section>
  )
}
