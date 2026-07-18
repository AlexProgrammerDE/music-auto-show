import { describe, expect, it } from "vitest"

import {
  formatDuration,
  formatEnumLabel,
  formatEnumValue,
  formatPercent,
  generateN,
} from "@/lib/format"

describe("display formatting", () => {
  it("formats protobuf enum labels for controls", () => {
    expect(formatEnumLabel("POSITION_PAN_FINE")).toBe("Position Pan Fine")
    expect(
      formatEnumValue(
        [
          ["AUTO", 1],
          ["PIPEWIRE", 2],
        ],
        2,
      ),
    ).toBe("Pipewire")
  })

  it("clamps percentages and recording durations", () => {
    expect(formatPercent(1.2)).toBe("100%")
    expect(formatPercent(-0.2)).toBe("0%")
    expect(formatDuration(125.9)).toBe("2:05")
  })

  it("generates stable one-based skeleton identities", () => {
    expect(generateN(3)).toEqual([1, 2, 3])
    expect(generateN(-1)).toEqual([])
  })
})
