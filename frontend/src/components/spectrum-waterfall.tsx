import { useEffect, useEffectEvent, useRef } from "react"

import { Badge } from "@/components/ui/badge"
import type { AudioAnalysis } from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { resizeCanvas, type CanvasSurface } from "@/lib/canvas"
import { magmaColor } from "@/lib/perceptual-colormap"

type SpectrumFocus = "spectrum" | "spectrogram"

function themeColor(variable: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(variable).trim()
}

function frequencyPosition(frequency: number, minimum: number, maximum: number, width: number) {
  return (Math.log(frequency / minimum) / Math.log(maximum / minimum)) * width
}

function drawFrequencyLabels(
  context: CanvasRenderingContext2D,
  width: number,
  y: number,
  minimum: number,
  maximum: number,
  color: string,
) {
  context.save()
  context.fillStyle = color
  context.font = '500 9px "Public Sans Variable", sans-serif'
  context.textBaseline = "top"
  for (const [frequency, label] of [
    [60, "60"],
    [250, "250"],
    [1_000, "1k"],
    [4_000, "4k"],
    [16_000, "16k"],
  ] as const) {
    if (frequency < minimum || frequency > maximum) continue
    const x = frequencyPosition(frequency, minimum, maximum, width)
    context.globalAlpha = 0.45
    context.strokeStyle = color
    context.beginPath()
    context.moveTo(x, 0)
    context.lineTo(x, y)
    context.stroke()
    context.globalAlpha = 0.8
    context.textAlign = x < 20 ? "left" : x > width - 20 ? "right" : "center"
    context.fillText(label, x, y + 3)
  }
  context.restore()
}

function draw(
  surface: CanvasSurface,
  analysis: AudioAnalysis | undefined,
  peaks: number[],
  smoothed: number[],
  focus: SpectrumFocus,
) {
  const { context, width, height } = surface
  const foreground = themeColor("--foreground")
  const muted = themeColor("--muted-foreground")
  const border = themeColor("--border")
  context.clearRect(0, 0, width, height)
  context.fillStyle = themeColor("--background")
  context.fillRect(0, 0, width, height)
  const spectrumRatio = focus === "spectrum" ? 0.58 : 0.32
  const spectrumHeight = height * spectrumRatio
  const axisY = spectrumHeight - 18
  const minimum = Math.max(20, analysis?.spectrumMinHz || 43)
  const maximum = Math.max(minimum + 1, analysis?.spectrumMaxHz || 16_000)
  const values = analysis?.spectrum ?? []

  context.save()
  context.strokeStyle = border
  context.globalAlpha = 0.5
  for (const db of [-72, -52, -32, -12]) {
    const normalized = (db + 72) / 60
    const y = axisY - normalized * (axisY - 12)
    context.beginPath()
    context.moveTo(0, y)
    context.lineTo(width, y)
    context.stroke()
    context.fillStyle = muted
    context.font = '500 9px "Public Sans Variable", sans-serif'
    context.textAlign = "left"
    context.fillText(`${db}`, 4, y - 3)
  }
  context.restore()

  if (values.length > 1) {
    context.save()
    context.beginPath()
    context.strokeStyle = themeColor("--chart-2")
    context.lineWidth = 1.5
    values.forEach((value, index) => {
      const x = (index / (values.length - 1)) * width
      const y = axisY - (smoothed[index] ?? value) * (axisY - 12)
      if (index === 0) context.moveTo(x, y)
      else context.lineTo(x, y)
    })
    context.stroke()
    context.beginPath()
    context.strokeStyle = foreground
    context.globalAlpha = 0.55
    context.lineWidth = 1
    values.forEach((value, index) => {
      const x = (index / (values.length - 1)) * width
      const y = axisY - (peaks[index] ?? value) * (axisY - 12)
      if (index === 0) context.moveTo(x, y)
      else context.lineTo(x, y)
    })
    context.stroke()
    context.restore()
  }

  drawFrequencyLabels(context, width, axisY, minimum, maximum, muted)
  const waterfallTop = spectrumHeight + 2
  const waterfallHeight = height - waterfallTop
  const frames = analysis?.spectrogram ?? []
  if (frames.length > 0) {
    const rowHeight = waterfallHeight / frames.length
    frames.forEach((frame, frameIndex) => {
      const binWidth = width / Math.max(1, frame.bins.length)
      frame.bins.forEach((value, binIndex) => {
        context.fillStyle = magmaColor(value)
        context.fillRect(
          binIndex * binWidth,
          waterfallTop + frameIndex * rowHeight,
          Math.ceil(binWidth),
          Math.ceil(rowHeight),
        )
      })
    })
  } else {
    context.strokeStyle = border
    context.strokeRect(0.5, waterfallTop + 0.5, width - 1, waterfallHeight - 1)
  }
  context.fillStyle = muted
  context.font = '500 9px "Public Sans Variable", sans-serif'
  context.textAlign = "right"
  context.fillText("5 s", width - 4, waterfallTop + 11)
  context.fillText("now", width - 4, height - 5)
}

export function SpectrumWaterfall({
  analysis,
  focus,
}: {
  readonly analysis: AudioAnalysis | undefined
  readonly focus: SpectrumFocus
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const surfaceRef = useRef<CanvasSurface | undefined>(undefined)
  const peaksRef = useRef<number[]>([])
  const smoothedRef = useRef<number[]>([])
  const render = useEffectEvent(() => {
    const surface = surfaceRef.current
    if (!surface) return
    draw(surface, analysis, peaksRef.current, smoothedRef.current, focus)
  })
  useEffect(() => {
    const values = analysis?.spectrum ?? []
    peaksRef.current = values.map((value, index) =>
      Math.max(value, (peaksRef.current[index] ?? 0) - 0.012),
    )
    smoothedRef.current = values.map(
      (value, index) => (smoothedRef.current[index] ?? value) * 0.72 + value * 0.28,
    )
    render()
  }, [analysis, focus])
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const resizeObserver = new ResizeObserver(([entry]) => {
      if (!entry) return
      surfaceRef.current = resizeCanvas(canvas, entry.contentRect.width, entry.contentRect.height)
      render()
    })
    const themeObserver = new MutationObserver(render)
    resizeObserver.observe(canvas)
    themeObserver.observe(document.documentElement, { attributeFilter: ["class"] })
    return () => {
      resizeObserver.disconnect()
      themeObserver.disconnect()
    }
  }, [])
  return (
    <figure className="relative min-h-80 overflow-hidden border bg-background">
      <canvas
        ref={canvasRef}
        width={960}
        height={360}
        className="absolute inset-0 size-full"
        aria-label={`Live logarithmic ${focus} from ${Math.round(analysis?.spectrumMinHz || 43)} hertz to ${Math.round(analysis?.spectrumMaxHz || 16_000)} hertz. Input RMS ${Math.round(analysis?.rmsDbfs ?? -120)} dBFS and peak ${Math.round(analysis?.peakDbfs ?? -120)} dBFS.`}
      />
      <figcaption className="absolute top-2 right-2 flex gap-1.5">
        <Badge variant="outline">RMS {(analysis?.rmsDbfs ?? -120).toFixed(1)} dBFS</Badge>
        <Badge variant={analysis?.clipping ? "destructive" : "outline"}>
          {analysis?.clipping ? "Clipping" : `Peak ${(analysis?.peakDbfs ?? -120).toFixed(1)} dBFS`}
        </Badge>
      </figcaption>
    </figure>
  )
}
