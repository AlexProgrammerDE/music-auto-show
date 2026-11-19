import { useSyncExternalStore } from "react"

import { latestLiveAudio, subscribeLiveFrameStore } from "@/lib/live-frame-store"

const noLiveAudio = (): undefined => undefined

export function useLiveAudio() {
  return useSyncExternalStore(subscribeLiveFrameStore, latestLiveAudio, noLiveAudio)
}
