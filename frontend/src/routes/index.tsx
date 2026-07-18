import {
  DownloadSimpleIcon,
  PauseIcon,
  PlayIcon,
  RecordIcon,
  StopIcon,
  TrashIcon,
  WarningOctagonIcon,
} from "@phosphor-icons/react"
import { useMutation, useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Effect } from "effect"
import { lazy, Suspense, useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import { AudioScope } from "@/components/audio-scope"
import { ConfirmCredenza } from "@/components/confirm-credenza"
import { MediaPanel } from "@/components/media-panel"
import { PageSkeleton } from "@/components/page-skeleton"
import { SectionPanel } from "@/components/section-panel"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Progress } from "@/components/ui/progress"
import { Spinner } from "@/components/ui/spinner"
import { RunState, ShowCommand } from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { formatDuration, formatPercent } from "@/lib/format"
import {
  configQueryOptions,
  fixtureProfilesQueryOptions,
  showQueryKeys,
  snapshotQueryOptions,
} from "@/lib/queries"
import { deriveRuntimePresentation } from "@/lib/runtime-status"
import { ShowApi, runShowApi } from "@/lib/show-api"
import { cn } from "@/lib/utils"

const StageView = lazy(() =>
  import("@/components/stage-view").then((module) => ({ default: module.StageView })),
)

export const Route = createFileRoute("/")({
  loader: async ({ context }) => {
    await Promise.all([
      context.queryClient.ensureQueryData(snapshotQueryOptions),
      context.queryClient.ensureQueryData(configQueryOptions),
      context.queryClient.ensureQueryData(fixtureProfilesQueryOptions),
    ])
  },
  pendingComponent: PageSkeleton,
  component: LiveDashboard,
})

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="border-r px-4 py-3 last:border-r-0">
      <p className="font-heading text-[10px] font-semibold tracking-[0.14em] text-muted-foreground uppercase">
        {label}
      </p>
      <p className="mt-1.5 text-xl leading-none font-semibold tabular-nums">{value}</p>
      {detail ? <p className="mt-1 text-[11px] text-muted-foreground">{detail}</p> : null}
    </div>
  )
}

function Level({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="grid grid-cols-[3.5rem_1fr_2.5rem] items-center gap-3">
      <span className="font-heading text-[11px] font-medium text-muted-foreground">{label}</span>
      <Progress value={value * 100} className={cn(color)} />
      <span className="text-right text-[11px] tabular-nums">{formatPercent(value)}</span>
    </div>
  )
}

function LiveDashboard() {
  const { data: snapshot } = useSuspenseQuery(snapshotQueryOptions)
  const { data: config } = useSuspenseQuery(configQueryOptions)
  const { data: profiles } = useSuspenseQuery(fixtureProfilesQueryOptions)
  const queryClient = Route.useRouteContext({ select: (context) => context.queryClient })
  const running = snapshot.runState === RunState.RUNNING
  const transitioning =
    snapshot.runState === RunState.STARTING || snapshot.runState === RunState.STOPPING
  const runtime = deriveRuntimePresentation(snapshot)
  const audio = snapshot.audio
  const recording = snapshot.recording
  const [recordingUrl, setRecordingUrl] = useState<string>()
  const [clearRecordingOpen, setClearRecordingOpen] = useState(false)

  useEffect(
    () => () => {
      if (recordingUrl) URL.revokeObjectURL(recordingUrl)
    },
    [recordingUrl],
  )

  const commandMutation = useMutation({
    mutationFn: (command: ShowCommand) =>
      runShowApi(Effect.flatMap(ShowApi, (api) => api.controlShow(command))),
    onSuccess: (result) => {
      toast[result.success ? "success" : "error"](result.message)
      void queryClient.invalidateQueries({ queryKey: showQueryKeys.snapshot })
    },
    onError: (error) => toast.error(error.message),
  })

  const blackoutMutation = useMutation({
    mutationFn: (enabled: boolean) =>
      runShowApi(Effect.flatMap(ShowApi, (api) => api.setBlackout(enabled))),
    onSuccess: (result) => {
      toast[result.success ? "success" : "error"](result.message)
      void queryClient.invalidateQueries({ queryKey: showQueryKeys.snapshot })
    },
    onError: (error) => toast.error(error.message),
  })

  const recordingMutation = useMutation({
    mutationFn: async (action: "start" | "stop" | "clear") => {
      if (action === "start") {
        await runShowApi(Effect.flatMap(ShowApi, (api) => api.startRecording))
        return
      }
      if (action === "clear") {
        await runShowApi(Effect.flatMap(ShowApi, (api) => api.clearRecording))
        return
      }
      const result = await runShowApi(Effect.flatMap(ShowApi, (api) => api.stopRecording))
      if (result.wav.length > 0) {
        const bytes = Uint8Array.from(result.wav)
        const blob = new Blob([bytes.buffer], { type: "audio/wav" })
        const url = URL.createObjectURL(blob)
        setRecordingUrl((previous) => {
          if (previous) URL.revokeObjectURL(previous)
          return url
        })
      }
    },
    onSuccess: (_, action) => {
      if (action === "clear") {
        setRecordingUrl((previous) => {
          if (previous) URL.revokeObjectURL(previous)
          return undefined
        })
      }
      setClearRecordingOpen(false)
      toast.success(action === "stop" ? "Recording saved" : `Recording ${action}ed`)
      void queryClient.invalidateQueries({ queryKey: showQueryKeys.snapshot })
    },
    onError: (error) => toast.error(error.message),
  })

  const fixtures = useMemo(
    () => snapshot.fixtureStates.filter((fixture) => fixture.fixtureId !== ""),
    [snapshot.fixtureStates],
  )

  return (
    <div className="grid gap-5">
      <section className="flex flex-col gap-4 border bg-card p-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <span
            className={cn(
              "size-2.5 shrink-0 rounded-full",
              running && "bg-chart-1 shadow-sm",
              snapshot.runState === RunState.ERROR && "bg-destructive",
              !running && snapshot.runState !== RunState.ERROR && "bg-muted-foreground/50",
            )}
          />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="font-heading text-base font-semibold">Live show</h1>
              <Badge variant="outline">{RunState[snapshot.runState]}</Badge>
            </div>
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              {snapshot.statusMessage || "Ready for audio input"}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant={snapshot.blackout ? "destructive" : "outline"}
            disabled={blackoutMutation.isPending}
            onClick={() => blackoutMutation.mutate(!snapshot.blackout)}
          >
            {blackoutMutation.isPending ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <WarningOctagonIcon
                data-icon="inline-start"
                weight={snapshot.blackout ? "fill" : "regular"}
                aria-hidden="true"
              />
            )}
            {blackoutMutation.isPending
              ? "Updating…"
              : snapshot.blackout
                ? "Release Blackout"
                : "Blackout"}
          </Button>
          <Button
            disabled={transitioning || commandMutation.isPending}
            onClick={() => commandMutation.mutate(running ? ShowCommand.STOP : ShowCommand.START)}
          >
            {commandMutation.isPending || transitioning ? (
              <Spinner data-icon="inline-start" />
            ) : running ? (
              <PauseIcon data-icon="inline-start" weight="fill" aria-hidden="true" />
            ) : (
              <PlayIcon data-icon="inline-start" weight="fill" aria-hidden="true" />
            )}
            {commandMutation.isPending || transitioning
              ? running
                ? "Stopping…"
                : "Starting…"
              : running
                ? "Stop Show"
                : "Start Show"}
          </Button>
        </div>
      </section>

      <section className="grid border bg-card sm:grid-cols-2 lg:grid-cols-6">
        <Metric
          label="Tempo"
          value={runtime.audioActive ? `${Math.round(audio?.tempo ?? 0)}` : "Idle"}
          detail={runtime.audioActive ? "BPM" : "Audio stopped"}
        />
        <Metric
          label="Beat"
          value={runtime.audioActive ? `${audio?.estimatedBeat ?? 0n}` : "Idle"}
          detail={runtime.audioActive ? `Bar ${audio?.estimatedBar ?? 0n}` : "Audio stopped"}
        />
        <Metric
          label="Confidence"
          value={runtime.audioActive ? formatPercent(audio?.beatConfidence ?? 0) : "Idle"}
          detail={runtime.audioActive ? "BeatNet+" : "Audio stopped"}
        />
        <Metric
          label="Energy"
          value={runtime.audioActive ? formatPercent(audio?.energy ?? 0) : "Idle"}
          detail={runtime.audioActive ? undefined : "Audio stopped"}
        />
        <Metric
          label="Effects"
          value={runtime.effectsActive ? snapshot.effectsFps.toFixed(1) : "Idle"}
          detail={runtime.effectsActive ? "frames/sec" : "Show stopped"}
        />
        <Metric
          label="DMX"
          value={runtime.dmx.active ? `${snapshot.dmxRuntime?.sendCount ?? 0n}` : "Idle"}
          detail={runtime.dmx.active ? "frames sent" : "Output stopped"}
        />
      </section>

      <MediaPanel active={runtime.audioActive} media={snapshot.media} tempo={audio?.tempo ?? 0} />

      <SectionPanel
        title="3D stage view"
        description="Live color, intensity, movement, strobe, and effect beams"
      >
        <Suspense
          fallback={
            <div className="flex h-80 items-center justify-center bg-background text-xs text-muted-foreground">
              Preparing stage preview
            </div>
          }
        >
          <StageView
            fixtures={config.fixtures}
            profiles={profiles}
            states={snapshot.fixtureStates}
          />
        </Suspense>
      </SectionPanel>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.65fr)_minmax(290px,.75fr)]">
        <SectionPanel
          title="Audio analysis"
          description={snapshot.audioRuntime?.deviceName || "No active input device"}
          action={
            <Badge variant="outline">
              {snapshot.audioRuntime?.sampleRate
                ? `${snapshot.audioRuntime.sampleRate.toLocaleString()} Hz`
                : "Waiting"}
            </Badge>
          }
        >
          <div className="grid border-b md:grid-cols-2">
            <AudioScope analysis={audio} mode="waveform" label="Waveform" />
            <AudioScope analysis={audio} mode="spectrum" label="Spectrum" />
          </div>
          <AudioScope analysis={audio} mode="spectrogram" label="Spectrogram" />
        </SectionPanel>

        <div className="grid content-start gap-5">
          <SectionPanel title="Frequency bands" description="Normalized live energy">
            <div className="grid gap-4 p-4">
              <Level
                label="Bass"
                value={audio?.bass ?? 0}
                color="[&_[data-slot=progress-indicator]]:bg-chart-2"
              />
              <Level
                label="Mid"
                value={audio?.mid ?? 0}
                color="[&_[data-slot=progress-indicator]]:bg-chart-3"
              />
              <Level
                label="High"
                value={audio?.high ?? 0}
                color="[&_[data-slot=progress-indicator]]:bg-chart-4"
              />
              <Level
                label="RMS"
                value={audio?.rms ?? 0}
                color="[&_[data-slot=progress-indicator]]:bg-chart-1"
              />
              <Level
                label="Dance"
                value={audio?.danceability ?? 0}
                color="[&_[data-slot=progress-indicator]]:bg-chart-5"
              />
              <Level
                label="Valence"
                value={audio?.valence ?? 0}
                color="[&_[data-slot=progress-indicator]]:bg-primary"
              />
            </div>
          </SectionPanel>

          <SectionPanel
            title="BeatNet+"
            description={snapshot.beatnet?.modelName || "Native detector"}
            action={
              <Badge
                variant={
                  runtime.beatnetFailed
                    ? "destructive"
                    : runtime.beatnetAvailable
                      ? "secondary"
                      : "outline"
                }
              >
                {runtime.beatnetStatus}
              </Badge>
            }
          >
            {runtime.beatnetFailed ? (
              <Alert variant="destructive" className="m-4 mb-0">
                <WarningOctagonIcon aria-hidden="true" />
                <AlertTitle>BeatNet+ is unavailable</AlertTitle>
                <AlertDescription>
                  {runtime.beatnetError} The show continues with fallback analysis.
                </AlertDescription>
              </Alert>
            ) : null}
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 p-4 text-xs">
              <dt className="text-muted-foreground">Model buffer</dt>
              <dd className="text-right tabular-nums">
                {runtime.beatnetAvailable
                  ? `${(snapshot.beatnet?.bufferDurationSeconds ?? 0).toFixed(2)} s`
                  : runtime.beatnetStatus}
              </dd>
              <dt className="text-muted-foreground">Beat phase</dt>
              <dd className="text-right tabular-nums">
                {runtime.beatnetAvailable
                  ? formatPercent(audio?.beatPosition ?? 0)
                  : runtime.beatnetFailed
                    ? "Unavailable"
                    : "Idle"}
              </dd>
              <dt className="text-muted-foreground">Bar phase</dt>
              <dd className="text-right tabular-nums">
                {runtime.beatnetAvailable
                  ? formatPercent(audio?.barPosition ?? 0)
                  : runtime.beatnetFailed
                    ? "Unavailable"
                    : "Idle"}
              </dd>
              <dt className="text-muted-foreground">Downbeat</dt>
              <dd className="text-right">{runtime.downbeatStatus}</dd>
            </dl>
          </SectionPanel>

          <SectionPanel title="Recording" description="Capture the active analysis source">
            <div className="grid gap-3 p-4">
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="text-muted-foreground">Duration</span>
                <span className="tabular-nums">
                  {formatDuration(recording?.durationSeconds ?? 0)} /{" "}
                  {formatDuration(recording?.maxDurationSeconds ?? 0)}
                </span>
              </div>
              <Progress
                value={
                  recording?.maxDurationSeconds
                    ? (recording.durationSeconds / recording.maxDurationSeconds) * 100
                    : 0
                }
                className={cn(
                  recording?.recording && "[&_[data-slot=progress-indicator]]:bg-destructive",
                )}
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant={recording?.recording ? "destructive" : "outline"}
                  disabled={recordingMutation.isPending}
                  onClick={() => recordingMutation.mutate(recording?.recording ? "stop" : "start")}
                >
                  {recordingMutation.isPending ? (
                    <Spinner data-icon="inline-start" />
                  ) : recording?.recording ? (
                    <StopIcon data-icon="inline-start" weight="fill" aria-hidden="true" />
                  ) : (
                    <RecordIcon data-icon="inline-start" weight="fill" aria-hidden="true" />
                  )}
                  {recordingMutation.isPending
                    ? "Working…"
                    : recording?.recording
                      ? "Stop & Save"
                      : "Record"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={!recording?.hasRecording || recordingMutation.isPending}
                  onClick={() => setClearRecordingOpen(true)}
                >
                  <TrashIcon data-icon="inline-start" aria-hidden="true" /> Clear
                </Button>
                {recordingUrl ? (
                  <Button
                    nativeButton={false}
                    variant="ghost"
                    size="sm"
                    render={
                      <a
                        href={recordingUrl}
                        download={`music-auto-show-${new Date().toISOString().slice(0, 10)}.wav`}
                        aria-label="Download recorded audio"
                      />
                    }
                  >
                    <DownloadSimpleIcon data-icon="inline-start" aria-hidden="true" /> Download
                  </Button>
                ) : null}
              </div>
              {recordingUrl ? (
                <audio
                  controls
                  src={recordingUrl}
                  className="h-9 w-full"
                  aria-label="Recorded audio preview"
                />
              ) : null}
            </div>
          </SectionPanel>
        </div>
      </div>

      <SectionPanel
        title="Stage output"
        description={`${fixtures.length} configured fixture${fixtures.length === 1 ? "" : "s"}`}
      >
        {fixtures.length === 0 ? (
          <Empty className="min-h-48 rounded-none">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <WarningOctagonIcon aria-hidden="true" />
              </EmptyMedia>
              <EmptyTitle>No stage output</EmptyTitle>
              <EmptyDescription>
                Add a fixture to patch the universe and preview live output.
              </EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <Button nativeButton={false} variant="outline" render={<Link to="/fixtures" />}>
                Configure Fixtures
              </Button>
            </EmptyContent>
          </Empty>
        ) : (
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
            {fixtures.map((fixture) => (
              <div
                key={fixture.fixtureId}
                className="flex items-center gap-3 border-r border-b p-3 last:border-r-0"
              >
                <span className="grid size-10 shrink-0 grid-cols-3 items-end gap-0.5 border bg-muted p-1">
                  <Progress
                    value={(fixture.red / 255) * 100}
                    className="h-full items-end [&_[data-slot=progress-indicator]]:w-full [&_[data-slot=progress-indicator]]:bg-chart-2 [&_[data-slot=progress-track]]:h-full"
                  />
                  <Progress
                    value={(fixture.green / 255) * 100}
                    className="h-full items-end [&_[data-slot=progress-indicator]]:w-full [&_[data-slot=progress-indicator]]:bg-chart-3 [&_[data-slot=progress-track]]:h-full"
                  />
                  <Progress
                    value={(fixture.blue / 255) * 100}
                    className="h-full items-end [&_[data-slot=progress-indicator]]:w-full [&_[data-slot=progress-indicator]]:bg-chart-4 [&_[data-slot=progress-track]]:h-full"
                  />
                </span>
                <div className="min-w-0">
                  <p className="truncate font-heading text-xs font-semibold">
                    {fixture.fixtureName}
                  </p>
                  <p className="mt-1 text-[10px] text-muted-foreground tabular-nums">
                    RGB {fixture.red} · {fixture.green} · {fixture.blue}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </SectionPanel>

      <ConfirmCredenza
        open={clearRecordingOpen}
        title="Clear the recording?"
        description="This removes the captured audio from memory and revokes its download link."
        confirmLabel="Clear Recording"
        icon={<TrashIcon aria-hidden="true" />}
        destructive
        pending={recordingMutation.isPending}
        onOpenChange={setClearRecordingOpen}
        onConfirm={() => recordingMutation.mutate("clear")}
      />
    </div>
  )
}
