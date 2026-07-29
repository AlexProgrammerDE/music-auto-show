import { EffectDriver, VisualizationMode } from "@/gen/music_auto_show/v1/music_auto_show_pb"

export type SignalStageId = "input" | "analysis" | "decision" | "fixture" | "dmx"

export interface SignalStage {
  readonly id: SignalStageId
  readonly drivers: readonly EffectDriver[]
}

const modeDrivers: Record<VisualizationMode, readonly EffectDriver[]> = {
  [VisualizationMode.UNSPECIFIED]: [
    EffectDriver.ENERGY,
    EffectDriver.BASS,
    EffectDriver.MID,
    EffectDriver.BEAT,
    EffectDriver.PALETTE,
  ],
  [VisualizationMode.ENERGY]: [
    EffectDriver.ENERGY,
    EffectDriver.BASS,
    EffectDriver.MID,
    EffectDriver.BEAT,
    EffectDriver.PALETTE,
  ],
  [VisualizationMode.FREQUENCY_SPLIT]: [
    EffectDriver.BASS,
    EffectDriver.MID,
    EffectDriver.HIGH,
    EffectDriver.BEAT,
  ],
  [VisualizationMode.BEAT_PULSE]: [EffectDriver.BEAT, EffectDriver.DOWNBEAT, EffectDriver.PALETTE],
  [VisualizationMode.COLOR_CYCLE]: [EffectDriver.BEAT, EffectDriver.PALETTE, EffectDriver.TIME],
  [VisualizationMode.RAINBOW_WAVE]: [EffectDriver.ENERGY, EffectDriver.BEAT, EffectDriver.TIME],
  [VisualizationMode.STROBE_BEAT]: [EffectDriver.BEAT, EffectDriver.BASS],
  [VisualizationMode.RANDOM_FLASH]: [EffectDriver.BEAT, EffectDriver.PALETTE],
}

export function signalPath(mode: VisualizationMode): readonly SignalStage[] {
  return [
    { id: "input", drivers: [] },
    { id: "analysis", drivers: modeDrivers[mode] ?? modeDrivers[VisualizationMode.ENERGY] },
    { id: "decision", drivers: modeDrivers[mode] ?? modeDrivers[VisualizationMode.ENERGY] },
    { id: "fixture", drivers: [] },
    { id: "dmx", drivers: [] },
  ]
}

export type FrequencyBand = "bass" | "mid" | "high"

export function assignFrequencyBands<T extends { readonly id: string; readonly position: number }>(
  fixtures: readonly T[],
) {
  const ordered = fixtures.toSorted(
    (first, second) => first.position - second.position || first.id.localeCompare(second.id),
  )
  const third = Math.max(1, Math.floor(ordered.length / 3))
  return new Map(
    ordered.map(
      (fixture, index) =>
        [fixture.id, index < third ? "bass" : index < third * 2 ? "mid" : "high"] as const,
    ),
  )
}
