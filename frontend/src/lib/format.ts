export function formatEnumLabel(value: string) {
  return value
    .toLowerCase()
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
}

/**
 * Formats the name of a protobuf enum number. Protobuf enums are open, so a newer backend can send
 * a number this build does not know; that case uses the fallback label.
 */
export function formatEnumName(
  names: Readonly<Record<number, string>>,
  value: number,
  fallback: string,
) {
  return formatEnumLabel(names[value] ?? fallback)
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

export function enumEntries(values: Record<string, string | number>) {
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
