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
import { createStandardSchemaV1, useQueryState } from "nuqs"
import { lazy, Suspense, useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import { AnalyzerView } from "@/components/analyzer-view"
import { ConfirmCredenza } from "@/components/confirm-credenza"
import { PageSkeleton } from "@/components/page-skeleton"
import { PerformanceDeck } from "@/components/performance-deck"
import { SectionPanel } from "@/components/section-panel"
import { FixtureReactionMatrix, SignalToShow } from "@/components/signal-to-show"
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
import { Progress, ProgressLabel, ProgressValue } from "@/components/ui/progress"
import { Spinner } from "@/components/ui/spinner"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  EffectDriver,
  EnergyTier,
  RunState,
  ShowCommand,
  VisualizationMode,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { dashboardSearchParams, dashboardViewParser } from "@/lib/dashboard-search"
import { formatDuration, formatEnumLabel, formatPercent } from "@/lib/format"
import {
  configQueryOptions,
  grandMa2FixtureTypesQueryOptions,
  showQueryKeys,
  snapshotQueryOptions,
} from "@/lib/queries"
import { deriveRuntimePresentation, type RuntimePresentation } from "@/lib/runtime-status"
import { ShowApi, runShowApi } from "@/lib/show-api"
import { cn } from "@/lib/utils"

const StageView = lazy(() =>
  import("@/components/stage-view").then((module) => ({ default: module.StageView })),
)
const AmbientVisualizer = lazy(() => import("@/components/ambient-visualizer"))

export const Route = createFileRoute("/")({
  validateSearch: createStandardSchemaV1(dashboardSearchParams, { partialOutput: true }),
  loader: async ({ context }) => {
    await Promise.all([
      context.queryClient.ensureQueryData(snapshotQueryOptions),
      context.queryClient.ensureQueryData(configQueryOptions),
      context.queryClient.ensureQueryData(grandMa2FixtureTypesQueryOptions),
    ])
  },
  pendingComponent: PageSkeleton,
  component: LiveDashboard,
})

function Metric({
  label,
  value,
  detail,
}: {
  readonly label: string
  readonly value: string
  readonly detail?: string
}) {
  return (
    <div className="border-b px-4 py-3 last:border-b-0 sm:border-r sm:border-b-0 sm:last:border-r-0">
      <p className="font-heading text-[10px] font-semibold tracking-[0.08em] text-muted-foreground uppercase">
        {label}
      </p>
      <p className="mt-1.5 text-xl leading-none font-semibold tabular-nums">{value}</p>
      {detail ? <p className="mt-1 text-[10px] text-muted-foreground">{detail}</p> : null}
    </div>
  )
}

function ShowControls({
  blackoutPending,
  commandPending,
  onBlackout,
  onCommand,
  running,
  snapshotState,
  statusMessage,
  transitioning,
  blackout,
}: {
  readonly blackoutPending: boolean
  readonly commandPending: boolean
  readonly onBlackout: () => void
  readonly onCommand: () => void
  readonly running: boolean
  readonly snapshotState: RunState
  readonly statusMessage: string
  readonly transitioning: boolean
  readonly blackout: boolean
}) {
  const status = statusMessage || "Ready for audio input"

  return (
    <section className="flex flex-col gap-4 border bg-card p-4 lg:flex-row lg:items-center lg:justify-between">
      <div className="flex min-w-0 items-center gap-3">
        <span
          className={cn(
            "size-2.5 shrink-0 rounded-full",
            running && "bg-chart-1",
            snapshotState === RunState.ERROR && "bg-destructive",
            !running && snapshotState !== RunState.ERROR && "bg-muted-foreground/50",
          )}
        />
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="font-heading text-base font-semibold">Live show</h1>
            <Badge variant="outline">{formatEnumLabel(RunState[snapshotState] ?? "Stopped")}</Badge>
          </div>
          <p className="mt-0.5 truncate text-xs text-muted-foreground" title={status}>
            {status}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={blackout ? "destructive" : "outline"}
          disabled={blackoutPending}
          onClick={onBlackout}
        >
          {blackoutPending ? (
            <Spinner data-icon="inline-start" />
          ) : (
            <WarningOctagonIcon
              data-icon="inline-start"
              weight={blackout ? "fill" : "regular"}
              aria-hidden="true"
            />
          )}
          {blackoutPending ? "Updating…" : blackout ? "Release Blackout" : "Blackout"}
        </Button>
        <Button disabled={transitioning || commandPending} onClick={onCommand}>
          {commandPending || transitioning ? (
            <Spinner data-icon="inline-start" />
          ) : running ? (
            <PauseIcon data-icon="inline-start" weight="fill" aria-hidden="true" />
          ) : (
            <PlayIcon data-icon="inline-start" weight="fill" aria-hidden="true" />
          )}
          {commandPending || transitioning
            ? running
              ? "Stopping…"
              : "Starting…"
            : running
              ? "Stop Show"
              : "Start Show"}
        </Button>
      </div>
    </section>
  )
}

function OperationalHealth({
  runtime,
  effectsFps,
  energy,
  sequence,
}: {
  readonly runtime: RuntimePresentation
  readonly effectsFps: number
  readonly energy: number
  readonly sequence: bigint
}) {
  return (
    <SectionPanel title="Operational health" description="Compact runtime and output status">
      <div className="grid sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="Audio"
          value={runtime.audioActive ? "Live" : "Idle"}
          detail={runtime.beatnetStatus}
        />
        <Metric
          label="Energy"
          value={runtime.audioActive ? formatPercent(energy) : "Idle"}
          detail="normalized loudness"
        />
        <Metric
          label="Effects"
          value={runtime.effectsActive ? effectsFps.toFixed(1) : "Idle"}
          detail={runtime.effectsActive ? "frames/sec" : "show stopped"}
        />
        <Metric label="DMX" value={runtime.dmx.label} detail={`snapshot ${sequence.toString()}`} />
      </div>
    </SectionPanel>
  )
}

function EffectReadout({
  visualizationMode,
  drivers,
  tier,
  beatResponse,
  dropActive,
  strobeActive,
  renderedColor,
}: {
  readonly visualizationMode: VisualizationMode
  readonly drivers: readonly EffectDriver[]
  readonly tier: EnergyTier
  readonly beatResponse: number
  readonly dropActive: boolean
  readonly strobeActive: boolean
  readonly renderedColor: { readonly red: number; readonly green: number; readonly blue: number }
}) {
  return (
    <SectionPanel
      title="Lighting state"
      description="The active algorithm, drivers, and rendered result"
      action={
        <Badge variant="outline">
          {formatEnumLabel(VisualizationMode[visualizationMode] ?? "Energy")}
        </Badge>
      }
    >
      <div className="grid gap-4 p-4 lg:grid-cols-[1fr_auto] lg:items-center">
        <div className="flex flex-wrap gap-1.5">
          {drivers.map((driver) => (
            <Badge key={driver} variant="secondary">
              {formatEnumLabel(EffectDriver[driver] ?? "Signal")}
            </Badge>
          ))}
          {dropActive ? <Badge variant="secondary">Drop active</Badge> : null}
          {strobeActive ? <Badge variant="destructive">Strobe active</Badge> : null}
        </div>
        <dl className="grid grid-cols-3 gap-x-5 gap-y-1 text-right text-xs">
          <dt className="text-muted-foreground">Tier</dt>
          <dt className="text-muted-foreground">Beat</dt>
          <dt className="text-muted-foreground">RGB</dt>
          <dd>{formatEnumLabel(EnergyTier[tier] ?? "Low")}</dd>
          <dd className="tabular-nums">{Math.round(beatResponse * 100)}%</dd>
          <dd className="tabular-nums">
            {renderedColor.red}·{renderedColor.green}·{renderedColor.blue}
          </dd>
        </dl>
      </div>
    </SectionPanel>
  )
}

function RecordingPanel({
  clearOpen,
  onClearOpenChange,
  onRecord,
  pending,
  recording,
  recordingUrl,
}: {
  readonly clearOpen: boolean
  readonly onClearOpenChange: (open: boolean) => void
  readonly onRecord: (action: "start" | "stop" | "clear") => void
  readonly pending: boolean
  readonly recording:
    | {
        readonly durationSeconds: number
        readonly maxDurationSeconds: number
        readonly recording: boolean
        readonly hasRecording: boolean
      }
    | undefined
  readonly recordingUrl: string | undefined
}) {
  const progress = recording?.maxDurationSeconds
    ? (recording.durationSeconds / recording.maxDurationSeconds) * 100
    : 0
  return (
    <>
      <SectionPanel title="Recording" description="Capture the active analysis source">
        <div className="grid gap-4 p-4">
          <Progress
            value={progress}
            className={cn(
              "gap-x-3 gap-y-2",
              recording?.recording && "[&_[data-slot=progress-indicator]]:bg-destructive",
            )}
          >
            <ProgressLabel className="text-xs">Capture duration</ProgressLabel>
            <ProgressValue className="text-xs">
              {() =>
                `${formatDuration(recording?.durationSeconds ?? 0)} / ${formatDuration(
                  recording?.maxDurationSeconds ?? 0,
                )}`
              }
            </ProgressValue>
          </Progress>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant={recording?.recording ? "destructive" : "outline"}
              disabled={pending}
              onClick={() => onRecord(recording?.recording ? "stop" : "start")}
            >
              {pending ? (
                <Spinner data-icon="inline-start" />
              ) : recording?.recording ? (
                <StopIcon data-icon="inline-start" weight="fill" aria-hidden="true" />
              ) : (
                <RecordIcon data-icon="inline-start" weight="fill" aria-hidden="true" />
              )}
              {pending ? "Working…" : recording?.recording ? "Stop & Save" : "Record"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={!recording?.hasRecording || pending}
              onClick={() => onClearOpenChange(true)}
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
      <ConfirmCredenza
        open={clearOpen}
        title="Clear the captured audio?"
        description="This removes the current in-memory recording. Download it first if you need to keep it."
        confirmLabel="Clear Recording"
        icon={<TrashIcon aria-hidden="true" />}
        pending={pending}
        onOpenChange={onClearOpenChange}
        onConfirm={() => onRecord("clear")}
      />
    </>
  )
}

function LiveDashboard() {
  const { data: snapshot } = useSuspenseQuery(snapshotQueryOptions)
  const { data: config } = useSuspenseQuery(configQueryOptions)
  const { data: fixtureTypes } = useSuspenseQuery(grandMa2FixtureTypesQueryOptions)
  const queryClient = Route.useRouteContext({ select: (context) => context.queryClient })
  const [view, setView] = useQueryState(
    "view",
    dashboardViewParser.withOptions({ history: "push" }),
  )
  const running = snapshot.runState === RunState.RUNNING
  const transitioning =
    snapshot.runState === RunState.STARTING || snapshot.runState === RunState.STOPPING
  const runtime = deriveRuntimePresentation(snapshot)
  const audio = snapshot.audio
  const recording = snapshot.recording
  const effectRuntime = snapshot.effectRuntime
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
    () => config.fixtures.filter((fixture) => fixture.id !== ""),
    [config.fixtures],
  )

  const performance = (
    <div className="grid min-w-0 gap-5">
      <PerformanceDeck
        active={runtime.audioActive}
        analysis={audio}
        effectRuntime={effectRuntime}
        media={snapshot.media}
      />
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
            fixtureTypes={fixtureTypes}
            states={snapshot.fixtureStates}
          />
        </Suspense>
      </SectionPanel>
      <EffectReadout
        visualizationMode={effectRuntime?.visualizationMode ?? VisualizationMode.ENERGY}
        drivers={effectRuntime?.activeDrivers ?? []}
        tier={effectRuntime?.energyTier ?? EnergyTier.LOW}
        beatResponse={effectRuntime?.beatResponse ?? 0}
        dropActive={effectRuntime?.dropActive ?? false}
        strobeActive={effectRuntime?.strobeActive ?? false}
        renderedColor={effectRuntime?.renderedColor ?? { red: 0, green: 0, blue: 0 }}
      />
      <SignalToShow runtime={effectRuntime} />
      {fixtures.length > 0 ? (
        <FixtureReactionMatrix
          fixtures={fixtures}
          runtime={effectRuntime}
          states={snapshot.fixtureStates}
        />
      ) : (
        <SectionPanel
          title="Fixture reaction matrix"
          description="Patch fixtures to see assignments"
        >
          <Empty className="min-h-48 rounded-none">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <WarningOctagonIcon aria-hidden="true" />
              </EmptyMedia>
              <EmptyTitle>No stage output</EmptyTitle>
              <EmptyDescription>
                Add a fixture to inspect its live signal assignment and response.
              </EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <Button nativeButton={false} variant="outline" render={<Link to="/fixtures" />}>
                Configure Fixtures
              </Button>
            </EmptyContent>
          </Empty>
        </SectionPanel>
      )}
      <OperationalHealth
        runtime={runtime}
        effectsFps={snapshot.effectsFps}
        energy={audio?.energy ?? 0}
        sequence={snapshot.sequence}
      />
    </div>
  )

  return (
    <div className="grid gap-5">
      <ShowControls
        blackoutPending={blackoutMutation.isPending}
        commandPending={commandMutation.isPending}
        onBlackout={() => blackoutMutation.mutate(!snapshot.blackout)}
        onCommand={() => commandMutation.mutate(running ? ShowCommand.STOP : ShowCommand.START)}
        running={running}
        snapshotState={snapshot.runState}
        statusMessage={snapshot.statusMessage}
        transitioning={transitioning}
        blackout={snapshot.blackout}
      />

      {runtime.beatnetFailed ? (
        <Alert variant="destructive">
          <WarningOctagonIcon aria-hidden="true" />
          <AlertTitle>Beat mapping is running in fallback mode</AlertTitle>
          <AlertDescription>{runtime.beatnetError}</AlertDescription>
        </Alert>
      ) : null}

      <Tabs
        value={view}
        onValueChange={(value) => void setView(value as typeof view, { history: "push" })}
        className="min-w-0 gap-4"
      >
        <TabsList
          variant="line"
          className="h-auto w-full justify-start overflow-x-auto border-b px-1 pb-2"
          aria-label="Live show workspace"
        >
          <TabsTrigger value="performance">Performance</TabsTrigger>
          <TabsTrigger value="analyzer">Analyzer</TabsTrigger>
          <TabsTrigger value="ambient">Ambient</TabsTrigger>
        </TabsList>
        <TabsContent value="performance" className="min-w-0">
          {view === "performance" ? performance : null}
        </TabsContent>
        <TabsContent value="analyzer" className="min-w-0">
          {view === "analyzer" ? (
            <div className="grid gap-5">
              <AnalyzerView
                analysis={audio}
                audioRuntime={snapshot.audioRuntime}
                beatnet={snapshot.beatnet}
              />
              <RecordingPanel
                clearOpen={clearRecordingOpen}
                onClearOpenChange={setClearRecordingOpen}
                onRecord={(action) => recordingMutation.mutate(action)}
                pending={recordingMutation.isPending}
                recording={recording}
                recordingUrl={recordingUrl}
              />
            </div>
          ) : null}
        </TabsContent>
        <TabsContent value="ambient" className="min-w-0">
          {view === "ambient" ? (
            <Suspense
              fallback={
                <div className="flex min-h-[28rem] items-center justify-center border bg-card text-xs text-muted-foreground">
                  Preparing ambient visualizer
                </div>
              }
            >
              <AmbientVisualizer analysis={audio} effectRuntime={effectRuntime} />
            </Suspense>
          ) : null}
        </TabsContent>
      </Tabs>
    </div>
  )
}
