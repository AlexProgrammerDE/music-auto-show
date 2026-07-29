import { parseAsStringLiteral } from "nuqs"

export const dashboardViewParser = parseAsStringLiteral([
  "performance",
  "analyzer",
  "ambient",
] as const).withDefault("performance")

export const analyzerScopeParser = parseAsStringLiteral([
  "waveform",
  "spectrum",
  "spectrogram",
  "beat",
] as const).withDefault("spectrum")

export const ambientPresetParser = parseAsStringLiteral([
  "radial",
  "led",
  "mirror",
  "peak",
  "luminance",
  "waterfall",
] as const).withDefault("radial")

export const dashboardSearchParams = {
  view: dashboardViewParser,
  scope: analyzerScopeParser,
  ambient: ambientPresetParser,
}
