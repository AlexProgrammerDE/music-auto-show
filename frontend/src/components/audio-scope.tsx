import { useEffect, useEffectEvent, useRef } from "react"

import type { AudioAnalysis } from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { resizeCanvas, type CanvasSurface } from "@/lib/canvas"

type ScopeMode = "waveform" | "spectrum" | "spectrogram"

function themeColor(variable: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(variable).trim()
}

function drawGrid(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  borderColor: string,
) {
  context.globalAlpha = 0.35
  context.strokeStyle = borderColor
  context.lineWidth = 1
  for (let x = 0; x <= width; x += 32) {
    context.beginPath()
    context.moveTo(x, 0)
    context.lineTo(x, height)
    context.stroke()
  }
  for (let y = 0; y <= height; y += 24) {
    context.beginPath()
    context.moveTo(0, y)
    context.lineTo(width, y)
    context.stroke()
  }
  context.globalAlpha = 1
}

function drawWaveform(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  values: readonly number[],
  borderColor: string,
  waveformColor: string,
) {
  drawGrid(context, width, height, borderColor)
  if (values.length < 2) return
  context.beginPath()
  context.strokeStyle = waveformColor
  context.lineWidth = 1.5
  values.forEach((value, position) => {
    const x = (position / (values.length - 1)) * width
    const y = height / 2 - value * height * 0.42
    if (position === 0) context.moveTo(x, y)
    else context.lineTo(x, y)
  })
  context.stroke()
}

function drawSpectrum(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  values: readonly number[],
  borderColor: string,
) {
  drawGrid(context, width, height, borderColor)
  if (values.length === 0) return
  const barWidth = width / values.length
  values.forEach((value, position) => {
    const normalized = Math.max(0, Math.min(1, value))
    const barHeight = normalized * height
    const hue = 265 - (position / values.length) * 80
    context.fillStyle = `hsl(${hue} 72% 62%)`
    context.fillRect(position * barWidth, height - barHeight, Math.max(1, barWidth - 1), barHeight)
  })
}

function drawSpectrogram(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  analysis: AudioAnalysis,
  borderColor: string,
) {
  const frames = analysis.spectrogram
  if (frames.length === 0) {
    drawGrid(context, width, height, borderColor)
    return
  }
  const frameWidth = width / frames.length
  frames.forEach((frame, framePosition) => {
    const binHeight = height / Math.max(1, frame.bins.length)
    frame.bins.forEach((value, binPosition) => {
      const normalized = Math.max(0, Math.min(1, value))
      const hue = 270 - normalized * 210
      context.fillStyle = `hsl(${hue} ${50 + normalized * 40}% ${8 + normalized * 55}%)`
      context.fillRect(
        framePosition * frameWidth,
        height - (binPosition + 1) * binHeight,
        Math.ceil(frameWidth),
        Math.ceil(binHeight),
      )
    })
  })
}

export function AudioScope({
  analysis,
  mode,
  label,
}: {
  readonly analysis: AudioAnalysis | undefined
  readonly mode: ScopeMode
  readonly label: string
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const surfaceRef = useRef<CanvasSurface | undefined>(undefined)

  const render = useEffectEvent(() => {
    const surface = surfaceRef.current
    if (!surface) return
    const { context, width, height } = surface
    const borderColor = themeColor("--border")
    context.clearRect(0, 0, width, height)
    if (!analysis) {
      drawGrid(context, width, height, borderColor)
      return
    }
    if (mode === "waveform") {
      drawWaveform(context, width, height, analysis.waveform, borderColor, themeColor("--chart-2"))
    } else if (mode === "spectrum") {
      drawSpectrum(context, width, height, analysis.spectrum, borderColor)
    } else {
      drawSpectrogram(context, width, height, analysis, borderColor)
    }
  })

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return
      surfaceRef.current = resizeCanvas(canvas, entry.contentRect.width, entry.contentRect.height)
      render()
    })
    observer.observe(canvas)
    const themeObserver = new MutationObserver(render)
    themeObserver.observe(document.documentElement, { attributeFilter: ["class"] })
    return () => {
      observer.disconnect()
      themeObserver.disconnect()
    }
  }, [])

  useEffect(() => render(), [analysis, mode])

  return (
    <figure className="relative min-h-36 overflow-hidden bg-background">
      <canvas ref={canvasRef} className="absolute inset-0 size-full" aria-label={label} />
      <figcaption className="absolute top-2 left-3 font-heading text-[10px] font-semibold tracking-[0.14em] text-muted-foreground uppercase">
        {label}
      </figcaption>
      {mode === "spectrogram" ? (
        <span className="absolute right-3 bottom-2 text-[10px] text-muted-foreground/70">
          5 second history
        </span>
      ) : null}
    </figure>
  )
}
