import { MusicNotesIcon, PauseIcon, PlayIcon } from "@phosphor-icons/react"
import { useEffect, useEffectEvent, useRef, useState } from "react"

import { BeatRunway } from "@/components/beat-runway"
import { Badge } from "@/components/ui/badge"
import type {
  AudioAnalysis,
  EffectRuntimeStatus,
  MediaInfo,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { resizeCanvas, type CanvasSurface } from "@/lib/canvas"

const fallbackPalette = [
  [124, 58, 237],
  [79, 70, 229],
  [8, 145, 178],
  [5, 150, 105],
  [217, 119, 6],
] as const

function Artwork({ media }: { readonly media: MediaInfo | undefined }) {
  const artworkUrl = media?.artworkUrl ?? ""
  const [failedArtworkUrl, setFailedArtworkUrl] = useState("")
  if (!artworkUrl || failedArtworkUrl === artworkUrl) {
    return (
      <span className="flex size-20 shrink-0 items-center justify-center border bg-muted">
        <MusicNotesIcon className="size-6" aria-hidden="true" />
      </span>
    )
  }
  return (
    <img
      src={artworkUrl}
      width={80}
      height={80}
      alt={media?.trackName ? `${media.trackName} cover artwork` : "Current track cover artwork"}
      className="size-20 shrink-0 border object-cover"
      decoding="async"
      onError={() => setFailedArtworkUrl(artworkUrl)}
    />
  )
}

function LightingPalette({
  media,
  activeIndex,
}: {
  readonly media: MediaInfo | undefined
  readonly activeIndex: number
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const surfaceRef = useRef<CanvasSurface | undefined>(undefined)
  const render = useEffectEvent(() => {
    const surface = surfaceRef.current
    if (!surface) return
    const { context, width, height } = surface
    const colors = media?.albumColors.length
      ? media.albumColors
      : fallbackPalette.map(([red, green, blue]) => ({ red, green, blue }))
    const selected = activeIndex % colors.length
    context.clearRect(0, 0, width, height)
    const swatchWidth = width / colors.length
    colors.forEach((color, index) => {
      context.fillStyle = `rgb(${color.red} ${color.green} ${color.blue})`
      context.fillRect(index * swatchWidth, 0, Math.ceil(swatchWidth), height)
      if (index === selected) {
        context.strokeStyle = "white"
        context.lineWidth = 3
        context.strokeRect(index * swatchWidth + 2, 2, swatchWidth - 4, height - 4)
        context.strokeStyle = "black"
        context.lineWidth = 1
        context.strokeRect(index * swatchWidth + 4, 4, swatchWidth - 8, height - 8)
      }
    })
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
    return () => observer.disconnect()
  }, [])
  useEffect(() => render(), [activeIndex, media])
  return (
    <div className="grid gap-1.5">
      <div className="flex items-center justify-between gap-3">
        <span className="font-heading text-[10px] font-semibold tracking-[0.12em] text-muted-foreground uppercase">
          Lighting palette
        </span>
        <span className="text-[10px] text-muted-foreground">Active {activeIndex + 1}</span>
      </div>
      <canvas
        ref={canvasRef}
        width={220}
        height={36}
        className="h-9 w-full min-w-44 border"
        aria-label={`Lighting palette with swatch ${activeIndex + 1} active`}
      />
    </div>
  )
}

export function PerformanceDeck({
  active,
  analysis,
  effectRuntime,
  media,
}: {
  readonly active: boolean
  readonly analysis: AudioAnalysis | undefined
  readonly effectRuntime: EffectRuntimeStatus | undefined
  readonly media: MediaInfo | undefined
}) {
  return (
    <section className="grid min-w-0 border bg-card xl:grid-cols-[20rem_minmax(0,1fr)]">
      <div className="flex flex-col gap-4 border-b p-4 xl:border-r xl:border-b-0">
        <div className="flex items-center gap-3">
          <Artwork media={media} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <p className="text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
                Now playing
              </p>
              <Badge variant="outline">
                {active && media?.isPlaying ? (
                  <PlayIcon weight="fill" aria-hidden="true" />
                ) : (
                  <PauseIcon weight="fill" aria-hidden="true" />
                )}
                {active ? (media?.isPlaying ? "Playing" : "Listening") : "Idle"}
              </Badge>
            </div>
            <p className="mt-2 truncate font-heading text-base font-semibold">
              {media?.trackName || "No track detected"}
            </p>
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              {media?.artistName || "System media session"}
            </p>
          </div>
        </div>
        <LightingPalette media={media} activeIndex={effectRuntime?.paletteIndex ?? 0} />
      </div>
      <BeatRunway active={active} analysis={analysis} effectRuntime={effectRuntime} />
    </section>
  )
}
