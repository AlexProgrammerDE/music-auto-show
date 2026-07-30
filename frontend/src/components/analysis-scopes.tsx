import { useEffect, useEffectEvent, useRef } from "react"

import { Badge } from "@/components/ui/badge"
import {
  MusicSection,
  Tonality,
  type AudioAnalysis,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { resizeCanvas, type CanvasSurface } from "@/lib/canvas"
import { formatEnumLabel } from "@/lib/format"
import {
  interpolatedAudio,
  latestLiveFrame,
  projectBeat,
  sampleLiveFrame,
} from "@/lib/live-frame-store"
import { magmaColor } from "@/lib/perceptual-colormap"

function themeColor(variable: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(variable).trim()
}

function useCanvasRender(
  renderCanvas: (surface: CanvasSurface, now: number) => void,
  animate = false,
) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const surfaceRef = useRef<CanvasSurface | undefined>(undefined)
  const render = useEffectEvent((now: number) => {
    const surface = surfaceRef.current
    if (surface) renderCanvas(surface, now)
  })
  useEffect(() => {
    if (!animate) render(performance.now())
  })
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    let animationFrame = 0
    const drawFrame = (now: number) => {
      render(now)
      animationFrame = window.requestAnimationFrame(drawFrame)
    }
    const resizeObserver = new ResizeObserver(([entry]) => {
      if (!entry) return
      surfaceRef.current = resizeCanvas(canvas, entry.contentRect.width, entry.contentRect.height)
      render(performance.now())
    })
    const themeObserver = new MutationObserver(() => render(performance.now()))
    resizeObserver.observe(canvas)
    themeObserver.observe(document.documentElement, { attributeFilter: ["class"] })
    if (animate) animationFrame = window.requestAnimationFrame(drawFrame)
    return () => {
      window.cancelAnimationFrame(animationFrame)
      resizeObserver.disconnect()
      themeObserver.disconnect()
    }
  }, [animate])
  return canvasRef
}

export function WaveformScope({ analysis }: { readonly analysis: AudioAnalysis | undefined }) {
  const canvasRef = useCanvasRender(({ context, width, height }, now) => {
    const liveAudio = interpolatedAudio(sampleLiveFrame(now))
    const values = liveAudio?.waveform.length ? liveAudio.waveform : (analysis?.waveform ?? [])
    const clipping = liveAudio?.clipping ?? analysis?.clipping ?? false
    const center = height / 2
    context.clearRect(0, 0, width, height)
    context.fillStyle = themeColor("--background")
    context.fillRect(0, 0, width, height)
    context.strokeStyle = themeColor("--border")
    context.globalAlpha = 0.65
    for (const y of [height * 0.08, center, height * 0.92]) {
      context.beginPath()
      context.moveTo(0, y)
      context.lineTo(width, y)
      context.stroke()
    }
    if (values.length > 1) {
      context.globalAlpha = 0.22
      context.fillStyle = clipping ? themeColor("--destructive") : themeColor("--chart-1")
      context.beginPath()
      values.forEach((value, index) => {
        const x = (index / (values.length - 1)) * width
        const y = center - Math.abs(value) * height * 0.42
        if (index === 0) context.moveTo(x, y)
        else context.lineTo(x, y)
      })
      for (let index = values.length - 1; index >= 0; index -= 1) {
        const x = (index / (values.length - 1)) * width
        const y = center + Math.abs(values[index] ?? 0) * height * 0.42
        context.lineTo(x, y)
      }
      context.closePath()
      context.fill()
      context.globalAlpha = 1
      context.strokeStyle = clipping ? themeColor("--destructive") : themeColor("--chart-2")
      context.lineWidth = 1.5
      context.beginPath()
      values.forEach((value, index) => {
        const x = (index / (values.length - 1)) * width
        const y = center - value * height * 0.42
        if (index === 0) context.moveTo(x, y)
        else context.lineTo(x, y)
      })
      context.stroke()
    }
    context.globalAlpha = 1
  }, true)
  return (
    <figure className="relative min-h-72 overflow-hidden border bg-background">
      <canvas
        ref={canvasRef}
        width={960}
        height={320}
        className="absolute inset-0 size-full"
        aria-label={`Live waveform centered at zero. RMS ${(analysis?.rmsDbfs ?? -120).toFixed(1)} dBFS, peak ${(analysis?.peakDbfs ?? -120).toFixed(1)} dBFS${analysis?.clipping ? ", clipping detected" : ""}.`}
      />
      <figcaption className="absolute top-2 right-2 flex gap-1.5">
        <Badge variant="outline">0 center</Badge>
        <Badge variant={analysis?.clipping ? "destructive" : "outline"}>
          {analysis?.clipping ? "Clipping" : "Headroom OK"}
        </Badge>
      </figcaption>
    </figure>
  )
}

export function BeatSignalScope({ analysis }: { readonly analysis: AudioAnalysis | undefined }) {
  const canvasRef = useCanvasRender(({ context, width, height }, now) => {
    const frames = analysis?.history ?? []
    const foreground = themeColor("--foreground")
    const border = themeColor("--border")
    context.clearRect(0, 0, width, height)
    context.fillStyle = themeColor("--background")
    context.fillRect(0, 0, width, height)
    context.strokeStyle = border
    context.globalAlpha = 0.6
    const thresholdY = height * 0.8
    context.setLineDash([4, 4])
    context.beginPath()
    context.moveTo(0, thresholdY)
    context.lineTo(width, thresholdY)
    context.stroke()
    context.setLineDash([])
    if (frames.length > 1) {
      for (const signal of [
        {
          color: themeColor("--chart-2"),
          value: (index: number) => frames[index]?.beatActivation ?? 0,
        },
        {
          color: foreground,
          value: (index: number) => frames[index]?.downbeatActivation ?? 0,
        },
      ]) {
        context.globalAlpha = 0.9
        context.strokeStyle = signal.color
        context.lineWidth = 1.5
        context.beginPath()
        frames.forEach((_, index) => {
          const x = (index / (frames.length - 1)) * width
          const y = height - 18 - signal.value(index) * (height - 36)
          if (index === 0) context.moveTo(x, y)
          else context.lineTo(x, y)
        })
        context.stroke()
      }
    }
    const latest = latestLiveFrame()
    const projected =
      latest?.frame.audio === undefined
        ? undefined
        : projectBeat(latest.frame.audio, latest.capturedAt, now)
    const barX = (projected?.barPosition ?? analysis?.barPosition ?? 0) * width
    context.globalAlpha = 0.8
    context.strokeStyle = themeColor("--chart-1")
    context.lineWidth = 2
    context.beginPath()
    context.moveTo(barX, 0)
    context.lineTo(barX, height)
    context.stroke()
    context.globalAlpha = 1
  }, true)
  return (
    <figure className="relative min-h-72 overflow-hidden border bg-background">
      <canvas
        ref={canvasRef}
        width={960}
        height={320}
        className="absolute inset-0 size-full"
        aria-label={`Beat signal history. Beat activation ${(analysis?.beatActivation ?? 0).toFixed(2)}, downbeat activation ${(analysis?.downbeatActivation ?? 0).toFixed(2)}, detected meter ${analysis?.meter || 4}/4.`}
      />
      <figcaption className="absolute top-2 right-2 flex gap-1.5">
        <Badge variant="secondary">Beat</Badge>
        <Badge variant="outline">Downbeat</Badge>
        <Badge variant="outline">
          {analysis?.beatIndex || 1}/{analysis?.meter || 4}
        </Badge>
      </figcaption>
    </figure>
  )
}

const pitchNames = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"] as const

export function HarmonicWheel({ analysis }: { readonly analysis: AudioAnalysis | undefined }) {
  const canvasRef = useCanvasRender(({ context, width, height }) => {
    const chroma = analysis?.chroma ?? []
    const centerX = width / 2
    const centerY = height / 2
    const radius = Math.min(width, height) * 0.34
    context.clearRect(0, 0, width, height)
    context.fillStyle = themeColor("--background")
    context.fillRect(0, 0, width, height)
    context.font = '600 10px "Public Sans Variable", sans-serif'
    context.textAlign = "center"
    context.textBaseline = "middle"
    for (let position = 0; position < 12; position += 1) {
      const pitchClass = (position * 7) % 12
      const value = chroma[pitchClass] ?? 0
      const angle = (position / 12) * Math.PI * 2 - Math.PI / 2
      const x = centerX + Math.cos(angle) * radius
      const y = centerY + Math.sin(angle) * radius
      context.fillStyle = magmaColor(value)
      context.beginPath()
      context.arc(x, y, 10 + value * 7, 0, Math.PI * 2)
      context.fill()
      context.fillStyle = value > 0.55 ? "black" : themeColor("--foreground")
      context.fillText(pitchNames[pitchClass] ?? "", x, y)
    }
  })
  const known = (analysis?.harmonicConfidence ?? 0) >= 0.08
  const keyName = pitchNames[analysis?.keyPitchClass ?? 0]
  const tonality = formatEnumLabel(Tonality[analysis?.tonality ?? Tonality.UNSPECIFIED] ?? "")
  return (
    <div className="grid border bg-background md:grid-cols-[15rem_1fr]">
      <figure className="relative min-h-56 border-b md:border-r md:border-b-0">
        <canvas
          ref={canvasRef}
          width={300}
          height={240}
          className="absolute inset-0 size-full"
          aria-label={`Harmonic pitch-class wheel. ${known ? `Detected ${keyName} ${tonality}` : "No stable key detected"}.`}
        />
      </figure>
      <div className="grid content-center gap-3 p-5">
        <span className="font-heading text-[10px] font-semibold tracking-[0.12em] text-muted-foreground uppercase">
          Harmonic estimate
        </span>
        <div className="flex items-baseline gap-2">
          <span className="font-heading text-3xl font-semibold">
            {known ? keyName : "Listening"}
          </span>
          {known ? <span className="text-sm text-muted-foreground">{tonality}</span> : null}
        </div>
        <p className="text-xs text-muted-foreground">
          {Math.round((analysis?.harmonicConfidence ?? 0) * 100)}% confidence · native 12-bin chroma
        </p>
      </div>
    </div>
  )
}

export function SectionTimeline({ analysis }: { readonly analysis: AudioAnalysis | undefined }) {
  const current = analysis?.section ?? MusicSection.UNSPECIFIED
  return (
    <div className="grid gap-3 border bg-background p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-heading text-xs font-semibold">Confirmed structure</span>
        <Badge variant={current === MusicSection.DROP ? "secondary" : "outline"}>
          {formatEnumLabel(MusicSection[current] ?? "Listening")} ·{" "}
          {Math.round((analysis?.sectionConfidence ?? 0) * 100)}%
        </Badge>
      </div>
      <div className="flex min-h-12 items-stretch overflow-x-auto border">
        {analysis?.sectionHistory.length ? (
          analysis.sectionHistory.map((marker) => (
            <div
              key={marker.sequence.toString()}
              className="grid min-w-28 content-center border-r px-3 py-2 last:border-r-0"
            >
              <span className="font-heading text-[10px] font-semibold uppercase">
                {formatEnumLabel(MusicSection[marker.section] ?? "Unknown")}
              </span>
              <span className="text-[10px] text-muted-foreground tabular-nums">
                Bar {Number(marker.estimatedBar) + 1} · {Math.round(marker.confidence * 100)}%
              </span>
            </div>
          ))
        ) : (
          <span className="self-center px-3 text-xs text-muted-foreground">
            Structure markers appear after a section is confirmed.
          </span>
        )}
      </div>
      <p className="text-[10px] text-muted-foreground">
        This timeline shows observed sections only. It does not invent future phrase boundaries.
      </p>
    </div>
  )
}
