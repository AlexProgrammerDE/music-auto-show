import { CaretRightIcon } from "@phosphor-icons/react"

import { SectionPanel } from "@/components/section-panel"
import { Badge } from "@/components/ui/badge"
import {
  EffectDriver,
  type EffectRuntimeStatus,
  type FixtureConfig,
  type FixtureState,
  VisualizationMode,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { formatEnumLabel } from "@/lib/format"
import { assignFrequencyBands, signalPath, type SignalStageId } from "@/lib/signal-to-show"

const stageCopy: Record<SignalStageId, { readonly label: string; readonly detail: string }> = {
  input: { label: "Audio input", detail: "Live mono stream" },
  analysis: { label: "Analysis", detail: "Bands, beat, form" },
  decision: { label: "Lighting logic", detail: "Mode and response" },
  fixture: { label: "Fixture map", detail: "Capabilities and patch" },
  dmx: { label: "DMX output", detail: "Bounded 8-bit values" },
}

function driverLabel(driver: EffectDriver) {
  return formatEnumLabel(EffectDriver[driver] ?? "Signal")
}

export function SignalToShow({ runtime }: { readonly runtime: EffectRuntimeStatus | undefined }) {
  const mode = runtime?.visualizationMode ?? VisualizationMode.ENERGY
  const active = new Set(runtime?.activeDrivers ?? [])
  return (
    <SectionPanel
      title="Signal to show"
      description="What is driving the rig right now"
      action={
        <Badge variant="outline">{formatEnumLabel(VisualizationMode[mode] ?? "Energy")}</Badge>
      }
    >
      <div className="grid gap-2 p-3 md:grid-cols-[1fr_auto_1fr_auto_1fr_auto_1fr_auto_1fr] md:items-stretch">
        {signalPath(mode).map((stage, index, stages) => {
          const copy = stageCopy[stage.id]
          return (
            <div className="contents" key={stage.id}>
              <div className="grid content-start gap-2 border bg-background p-3">
                <span className="font-heading text-xs font-semibold">{copy.label}</span>
                <span className="text-[11px] text-muted-foreground">{copy.detail}</span>
                {stage.drivers.length > 0 ? (
                  <div className="flex flex-wrap gap-1">
                    {stage.drivers.map((driver) => (
                      <Badge
                        key={driver}
                        variant={active.has(driver) ? "secondary" : "outline"}
                        className="text-[9px]"
                      >
                        {driverLabel(driver)}
                      </Badge>
                    ))}
                  </div>
                ) : null}
              </div>
              {index < stages.length - 1 ? (
                <CaretRightIcon
                  className="mx-auto size-4 rotate-90 self-center text-muted-foreground md:rotate-0"
                  aria-hidden="true"
                />
              ) : null}
            </div>
          )
        })}
      </div>
    </SectionPanel>
  )
}

function reactionForMode(mode: VisualizationMode, band: string | undefined) {
  switch (mode) {
    case VisualizationMode.FREQUENCY_SPLIT:
      return `${band ?? "high"} energy`
    case VisualizationMode.BEAT_PULSE:
      return "Beat + downbeat"
    case VisualizationMode.COLOR_CYCLE:
      return "32-beat cycle"
    case VisualizationMode.RAINBOW_WAVE:
      return "Time + energy"
    case VisualizationMode.STROBE_BEAT:
      return "Beat + bass"
    case VisualizationMode.RANDOM_FLASH:
      return "Beat selection"
    default:
      return "Energy + bass"
  }
}

export function FixtureReactionMatrix({
  fixtures,
  runtime,
  states,
}: {
  readonly fixtures: readonly FixtureConfig[]
  readonly runtime: EffectRuntimeStatus | undefined
  readonly states: readonly FixtureState[]
}) {
  const mode = runtime?.visualizationMode ?? VisualizationMode.ENERGY
  const bands = assignFrequencyBands(fixtures)
  const stateById = new Map(states.map((state) => [state.fixtureId, state]))
  return (
    <SectionPanel
      title="Fixture reaction matrix"
      description="Per-fixture input assignment and rendered response"
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-xl border-collapse text-left text-xs">
          <thead className="border-b bg-muted/40 font-heading text-[10px] tracking-[0.08em] text-muted-foreground uppercase">
            <tr>
              <th className="px-4 py-2 font-medium">Fixture</th>
              <th className="px-4 py-2 font-medium">Listens to</th>
              <th className="px-4 py-2 font-medium">Dimmer</th>
              <th className="px-4 py-2 font-medium">Rendered channels</th>
            </tr>
          </thead>
          <tbody>
            {fixtures.map((fixture) => {
              const state = stateById.get(fixture.id)
              return (
                <tr key={fixture.id} className="border-b last:border-b-0">
                  <td className="px-4 py-3 font-heading font-semibold">{fixture.name}</td>
                  <td className="px-4 py-3 capitalize">
                    {reactionForMode(mode, bands.get(fixture.id))}
                  </td>
                  <td className="px-4 py-3 tabular-nums">{state?.dimmer ?? 0} / 255</td>
                  <td className="px-4 py-3 text-muted-foreground tabular-nums">
                    RGB {state?.red ?? 0} · {state?.green ?? 0} · {state?.blue ?? 0}
                    {state?.strobe ? ` · Strobe ${state.strobe}` : ""}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </SectionPanel>
  )
}
