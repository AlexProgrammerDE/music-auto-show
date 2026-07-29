import { describe, expect, test } from "vitest"

import { EffectDriver, VisualizationMode } from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { assignFrequencyBands, signalPath } from "@/lib/signal-to-show"

describe("signal-to-show model", () => {
  test("describes the live drivers for each lighting algorithm", () => {
    const split = signalPath(VisualizationMode.FREQUENCY_SPLIT)[1]
    expect(split?.drivers).toEqual([
      EffectDriver.BASS,
      EffectDriver.MID,
      EffectDriver.HIGH,
      EffectDriver.BEAT,
    ])
    expect(signalPath(VisualizationMode.BEAT_PULSE)[1]?.drivers).toContain(EffectDriver.DOWNBEAT)
  })

  test("partitions fixtures by stage order with stable identities", () => {
    const assignments = assignFrequencyBands([
      { id: "right", position: 30 },
      { id: "left", position: 10 },
      { id: "center", position: 20 },
      { id: "rear", position: 40 },
    ])
    expect(assignments.get("left")).toBe("bass")
    expect(assignments.get("center")).toBe("mid")
    expect(assignments.get("right")).toBe("high")
    expect(assignments.get("rear")).toBe("high")
  })
})
