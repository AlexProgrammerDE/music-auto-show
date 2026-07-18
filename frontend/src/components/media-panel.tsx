import { MusicNotesIcon, PauseIcon, PlayIcon } from "@phosphor-icons/react"
import { useEffect, useRef } from "react"

import { Badge } from "@/components/ui/badge"
import type { MediaInfo } from "@/gen/music_auto_show/v1/music_auto_show_pb"

const fallbackPalette = [
  [124, 58, 237],
  [79, 70, 229],
  [8, 145, 178],
  [5, 150, 105],
  [217, 119, 6],
] as const

function Palette({ media }: { readonly media: MediaInfo | undefined }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = ref.current
    const context = canvas?.getContext("2d")
    if (!canvas || !context) return
    const colors = media?.albumColors.length
      ? media.albumColors
      : fallbackPalette.map(([red, green, blue]) => ({ red, green, blue }))
    const width = canvas.width / colors.length
    colors.forEach((color, position) => {
      context.fillStyle = `rgb(${color.red} ${color.green} ${color.blue})`
      context.fillRect(position * width, 0, Math.ceil(width), canvas.height)
    })
  }, [media])
  return (
    <canvas
      ref={ref}
      width={240}
      height={32}
      className="h-8 w-40 border"
      aria-label="Dominant album artwork colors"
    />
  )
}

export function MediaPanel({
  active,
  media,
  tempo,
}: {
  readonly active: boolean
  readonly media: MediaInfo | undefined
  readonly tempo: number
}) {
  return (
    <section className="flex flex-col gap-4 border bg-card p-4 sm:flex-row sm:items-center">
      <span className="flex size-10 shrink-0 items-center justify-center border bg-muted">
        <MusicNotesIcon className="size-5" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[10px] tracking-[0.14em] text-muted-foreground uppercase">Now playing</p>
        <p className="mt-1 truncate font-heading text-sm font-semibold">
          {media?.trackName || "No track detected"}
        </p>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {media?.artistName || "System media session"}
        </p>
      </div>
      <div className="flex items-center gap-3">
        <Palette media={media} />
        <Badge variant="outline">
          {active ? (
            media?.isPlaying ? (
              <PlayIcon weight="fill" aria-hidden="true" />
            ) : (
              <PauseIcon weight="fill" aria-hidden="true" />
            )
          ) : null}
          <span className="sr-only">
            {active ? (media?.isPlaying ? "Playing" : "Paused") : "Audio stopped"}
          </span>
          {active ? `${Math.round(tempo)} BPM` : "Idle"}
        </Badge>
      </div>
    </section>
  )
}
