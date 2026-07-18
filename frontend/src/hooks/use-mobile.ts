import { useSyncExternalStore } from "react"

const mobileQuery = "(max-width: 767px)"

function subscribe(onChange: () => void) {
  const media = window.matchMedia(mobileQuery)
  media.addEventListener("change", onChange)
  return () => media.removeEventListener("change", onChange)
}

function getSnapshot() {
  return window.matchMedia(mobileQuery).matches
}

export function useMobile() {
  return useSyncExternalStore(subscribe, getSnapshot, () => false)
}
