import { WarningOctagonIcon } from "@phosphor-icons/react"
import { useQueryState } from "nuqs"

import {
  BeatSignalScope,
  HarmonicWheel,
  SectionTimeline,
  WaveformScope,
} from "@/components/analysis-scopes"
import { SectionPanel } from "@/components/section-panel"
import { SpectrumWaterfall } from "@/components/spectrum-waterfall"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Progress, ProgressLabel, ProgressValue } from "@/components/ui/progress"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type {
  AudioAnalysis,
  AudioRuntimeStatus,
  BeatNetStatus,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { useLiveAudio } from "@/hooks/use-live-audio"
import { analyzerScopeParser } from "@/lib/dashboard-search"
import { formatPercent } from "@/lib/format"
import { liveAnalysisHistory, type LiveAnalysisSample } from "@/lib/live-frame-store"
import { cn } from "@/lib/utils"

function normalizedDb(value: number) {
  return Math.max(0, Math.min(1, (value + 60) / 60))
}

function peakOf(select: (frame: LiveAnalysisSample) => number) {
  return Math.max(0, ...liveAnalysisHistory().map(select))
}

function AnalysisLevel({
  label,
  value,
  peak,
  threshold,
  color,
  display,
}: {
  readonly label: string
  readonly value: number
  readonly peak: number
  readonly threshold: number
  readonly color: string
  readonly display?: string
}) {
  return (
    <Progress
      value={value * 100}
      aria-label={`${label}: ${display ?? formatPercent(value)}, recent peak ${formatPercent(peak)}, trigger guide ${formatPercent(threshold)}`}
      className={cn("gap-x-3 gap-y-1", color)}
    >
      <ProgressLabel className="font-heading text-[11px]">{label}</ProgressLabel>
      <ProgressValue className="text-[11px]">{() => display ?? formatPercent(value)}</ProgressValue>
      <span className="order-3 w-full text-[9px] text-muted-foreground">
        peak {formatPercent(peak)} · guide {formatPercent(threshold)}
      </span>
    </Progress>
  )
}

function AnalysisMeters({ analysis }: { readonly analysis: AudioAnalysis | undefined }) {
  const liveAudio = useLiveAudio()
  const current = liveAudio ?? analysis
  const rms = normalizedDb(current?.rmsDbfs ?? -120)
  const peak = normalizedDb(current?.peakDbfs ?? -120)
  return (
    <SectionPanel
      title="Signal meters"
      description="Current value, recent peak, and response guide"
    >
      <div className="grid gap-5 p-4 md:grid-cols-2">
        <AnalysisLevel
          label="Bass"
          value={current?.bass ?? 0}
          peak={peakOf((frame) => frame.bass)}
          threshold={0.5}
          color="[&_[data-slot=progress-indicator]]:bg-chart-2"
        />
        <AnalysisLevel
          label="Mid"
          value={current?.mid ?? 0}
          peak={peakOf((frame) => frame.mid)}
          threshold={0.5}
          color="[&_[data-slot=progress-indicator]]:bg-chart-3"
        />
        <AnalysisLevel
          label="High"
          value={current?.high ?? 0}
          peak={peakOf((frame) => frame.high)}
          threshold={0.5}
          color="[&_[data-slot=progress-indicator]]:bg-chart-4"
        />
        <AnalysisLevel
          label="Energy"
          value={current?.energy ?? 0}
          peak={peakOf((frame) => frame.energy)}
          threshold={0.6}
          color="[&_[data-slot=progress-indicator]]:bg-chart-1"
        />
        <AnalysisLevel
          label="RMS"
          value={rms}
          peak={rms}
          threshold={0.8}
          color="[&_[data-slot=progress-indicator]]:bg-primary"
          display={`${(current?.rmsDbfs ?? -120).toFixed(1)} dBFS`}
        />
        <AnalysisLevel
          label="Peak"
          value={peak}
          peak={peak}
          threshold={0.98}
          color={
            current?.clipping
              ? "[&_[data-slot=progress-indicator]]:bg-destructive"
              : "[&_[data-slot=progress-indicator]]:bg-chart-5"
          }
          display={`${(current?.peakDbfs ?? -120).toFixed(1)} dBFS`}
        />
      </div>
    </SectionPanel>
  )
}

function BeatDiagnostics({ analysis }: { readonly analysis: AudioAnalysis | undefined }) {
  const liveAudio = useLiveAudio()
  const current = liveAudio ?? analysis
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs md:grid-cols-4">
      <dt className="text-muted-foreground">Decoder</dt>
      <dd className="text-right">Cascade PF</dd>
      <dt className="text-muted-foreground">Beat phase</dt>
      <dd className="text-right tabular-nums">{formatPercent(current?.beatPosition ?? 0)}</dd>
      <dt className="text-muted-foreground">Bar phase</dt>
      <dd className="text-right tabular-nums">{formatPercent(current?.barPosition ?? 0)}</dd>
      <dt className="text-muted-foreground">Meter</dt>
      <dd className="text-right tabular-nums">{current?.meter || 4}/4</dd>
      <dt className="text-muted-foreground">Tracking lock</dt>
      <dd className="text-right tabular-nums">{formatPercent(current?.trackingConfidence ?? 0)}</dd>
      <dt className="text-muted-foreground">Beat activation</dt>
      <dd className="text-right tabular-nums">{(current?.beatActivation ?? 0).toFixed(3)}</dd>
      <dt className="text-muted-foreground">Downbeat activation</dt>
      <dd className="text-right tabular-nums">{(current?.downbeatActivation ?? 0).toFixed(3)}</dd>
    </dl>
  )
}

export function AnalyzerView({
  analysis,
  audioRuntime,
  beatnet,
}: {
  readonly analysis: AudioAnalysis | undefined
  readonly audioRuntime: AudioRuntimeStatus | undefined
  readonly beatnet: BeatNetStatus | undefined
}) {
  const [scope, setScope] = useQueryState(
    "scope",
    analyzerScopeParser.withOptions({ history: "push" }),
  )
  const beatnetFailed = Boolean(beatnet?.lastError)
  return (
    <div className="grid gap-5">
      <SectionPanel
        title="Audio analyzer"
        description={audioRuntime?.deviceName || "No active input device"}
        action={
          <Badge variant="outline">
            {audioRuntime?.sampleRate
              ? `${audioRuntime.sampleRate.toLocaleString()} Hz`
              : "Waiting"}
          </Badge>
        }
      >
        <Tabs
          value={scope}
          onValueChange={(value) => void setScope(value as typeof scope, { history: "push" })}
          className="gap-0"
        >
          <TabsList
            variant="line"
            className="h-auto w-full justify-start overflow-x-auto border-b px-3 py-2"
          >
            <TabsTrigger value="waveform">Waveform</TabsTrigger>
            <TabsTrigger value="spectrum">Spectrum</TabsTrigger>
            <TabsTrigger value="spectrogram">Spectrogram</TabsTrigger>
            <TabsTrigger value="beat">Beat signal</TabsTrigger>
          </TabsList>
          <div className="p-3">
            {scope === "waveform" ? <WaveformScope analysis={analysis} /> : null}
            {scope === "spectrum" ? (
              <SpectrumWaterfall analysis={analysis} focus="spectrum" />
            ) : null}
            {scope === "spectrogram" ? (
              <SpectrumWaterfall analysis={analysis} focus="spectrogram" />
            ) : null}
            {scope === "beat" ? <BeatSignalScope analysis={analysis} /> : null}
          </div>
        </Tabs>
      </SectionPanel>

      <AnalysisMeters analysis={analysis} />

      <div className="grid gap-5 xl:grid-cols-2">
        <SectionPanel title="Musical structure" description="Confirmed streaming section changes">
          <div className="p-3">
            <SectionTimeline analysis={analysis} />
          </div>
        </SectionPanel>
        <SectionPanel title="Harmony" description="Smoothed pitch-class energy and key estimate">
          <div className="p-3">
            <HarmonicWheel analysis={analysis} />
          </div>
        </SectionPanel>
      </div>

      <SectionPanel
        title="BeatNet+ diagnostics"
        description={beatnet?.modelName || "Native causal beat detector"}
        action={
          <Badge
            variant={beatnetFailed ? "destructive" : beatnet?.available ? "secondary" : "outline"}
          >
            {beatnetFailed ? "Unavailable" : beatnet?.status || "Idle"}
          </Badge>
        }
      >
        {beatnetFailed ? (
          <Alert variant="destructive" className="m-4 mb-0">
            <WarningOctagonIcon aria-hidden="true" />
            <AlertTitle>BeatNet+ is unavailable</AlertTitle>
            <AlertDescription>
              {beatnet?.lastError} The show continues with bounded fallback analysis.
            </AlertDescription>
          </Alert>
        ) : null}
        <Accordion defaultValue={[]}>
          <AccordionItem value="beatnet-details" className="px-4">
            <AccordionTrigger>Detector and decoder details</AccordionTrigger>
            <AccordionContent>
              <BeatDiagnostics analysis={analysis} />
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </SectionPanel>
    </div>
  )
}
