import type { DmxRuntimeStatus, ShowSnapshot } from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { RunState } from "@/gen/music_auto_show/v1/music_auto_show_pb"

export interface DmxPresentation {
  readonly active: boolean
  readonly failed: boolean
  readonly label: "Connected" | "Error" | "Offline" | "Simulated" | "Stopped"
}

export interface RuntimePresentation {
  readonly audioActive: boolean
  readonly beatnetAvailable: boolean
  readonly beatnetError: string
  readonly beatnetFailed: boolean
  readonly beatnetStatus: string
  readonly dmx: DmxPresentation
  readonly downbeatStatus: "Detected" | "Fallback" | "Idle" | "Tracking"
  readonly effectsActive: boolean
}

export function deriveDmxPresentation(runtime: DmxRuntimeStatus | undefined): DmxPresentation {
  if (!runtime?.running) {
    return { active: false, failed: false, label: "Stopped" }
  }
  if (runtime.lastError.trim()) {
    return { active: true, failed: true, label: "Error" }
  }
  if (runtime.simulated) {
    return { active: true, failed: false, label: "Simulated" }
  }
  if (runtime.isOpen) {
    return { active: true, failed: false, label: "Connected" }
  }
  return { active: true, failed: false, label: "Offline" }
}

export function deriveRuntimePresentation(snapshot: ShowSnapshot): RuntimePresentation {
  const audioActive = snapshot.audioRuntime?.running === true
  const beatnet = snapshot.beatnet
  const beatnetAvailable = audioActive && beatnet?.available === true
  const beatnetFailed = audioActive && beatnet !== undefined && !beatnet.available
  const beatnetStatus = !audioActive
    ? "Idle"
    : beatnetAvailable
      ? beatnet.status || (beatnet.processing ? "Processing" : "Ready")
      : beatnet?.status || "Unavailable"
  const beatnetError = beatnet?.lastError.trim() || "BeatNet+ could not load its checkpoint."
  const downbeatStatus = !audioActive
    ? "Idle"
    : beatnetFailed
      ? "Fallback"
      : snapshot.audio?.downbeatDetected
        ? "Detected"
        : "Tracking"

  return {
    audioActive,
    beatnetAvailable,
    beatnetError,
    beatnetFailed,
    beatnetStatus,
    dmx: deriveDmxPresentation(snapshot.dmxRuntime),
    downbeatStatus,
    effectsActive: snapshot.runState === RunState.RUNNING,
  }
}
