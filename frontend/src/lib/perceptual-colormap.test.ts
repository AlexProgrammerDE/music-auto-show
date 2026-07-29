import { describe, expect, test } from "vitest"

import { magmaColor } from "@/lib/perceptual-colormap"

describe("magmaColor", () => {
  test("clamps values and interpolates between perceptual color stops", () => {
    expect(magmaColor(-1)).toBe("rgb(0 0 4)")
    expect(magmaColor(1)).toBe("rgb(252 253 191)")
    expect(magmaColor(0.5)).toBe("rgb(181 54 122)")
  })
})
