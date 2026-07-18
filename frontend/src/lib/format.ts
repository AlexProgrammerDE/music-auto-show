export function formatEnumLabel(value: string) {
  return value
    .toLowerCase()
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
}

export function formatDuration(seconds: number) {
  const safeSeconds = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(safeSeconds / 60)
  const remainder = safeSeconds % 60
  return `${minutes}:${remainder.toString().padStart(2, "0")}`
}

export function formatPercent(value: number) {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`
}

export function enumEntries<T extends Record<string, string | number>>(values: T) {
  return Object.entries(values).filter(
    (entry): entry is [string, number] =>
      typeof entry[1] === "number" && entry[0] !== "UNSPECIFIED",
  )
}

export function formatEnumValue(entries: ReadonlyArray<readonly [string, number]>, value: number) {
  const entry = entries.find(([, candidate]) => candidate === value)
  return entry ? formatEnumLabel(entry[0]) : "Unknown"
}

export function generateN(count: number) {
  return Array.from({ length: Math.max(0, Math.floor(count)) }, (_, position) => position + 1)
}
