import { describe, expect, it } from "vitest"

import { canvasBitmapSize } from "@/lib/canvas"

describe("canvasBitmapSize", () => {
  it("matches the CSS size at standard pixel density", () => {
    expect(canvasBitmapSize(320, 144, 1)).toEqual({
      width: 320,
      height: 144,
      pixelRatio: 1,
    })
  })

  it("renders sharply on high-density displays without unbounded allocation", () => {
    expect(canvasBitmapSize(320, 144, 2)).toEqual({
      width: 640,
      height: 288,
      pixelRatio: 2,
    })
    expect(canvasBitmapSize(320, 144, 4)).toEqual({
      width: 640,
      height: 288,
      pixelRatio: 2,
    })
  })
})
