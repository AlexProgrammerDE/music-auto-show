export type CanvasSurface = {
  readonly context: CanvasRenderingContext2D
  readonly width: number
  readonly height: number
  readonly pixelRatio: number
}

const MAX_CANVAS_PIXEL_RATIO = 2

export function canvasBitmapSize(width: number, height: number, devicePixelRatio: number) {
  const pixelRatio = Math.min(
    MAX_CANVAS_PIXEL_RATIO,
    Math.max(1, Number.isFinite(devicePixelRatio) ? devicePixelRatio : 1),
  )
  return {
    width: Math.max(1, Math.round(width * pixelRatio)),
    height: Math.max(1, Math.round(height * pixelRatio)),
    pixelRatio,
  }
}

export function resizeCanvas(
  canvas: HTMLCanvasElement,
  width: number,
  height: number,
): CanvasSurface | undefined {
  const bitmap = canvasBitmapSize(width, height, window.devicePixelRatio)
  if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
    canvas.width = bitmap.width
    canvas.height = bitmap.height
  }
  const context = canvas.getContext("2d")
  if (!context) return undefined
  context.setTransform(bitmap.pixelRatio, 0, 0, bitmap.pixelRatio, 0, 0)
  return { context, width, height, pixelRatio: bitmap.pixelRatio }
}
