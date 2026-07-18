import { create } from "@bufbuild/protobuf"
import { describe, expect, it } from "vitest"

import {
  AudioRuntimeStatusSchema,
  BeatNetStatusSchema,
  DmxRuntimeStatusSchema,
  RunState,
  ShowSnapshotSchema,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { deriveDmxPresentation, deriveRuntimePresentation } from "@/lib/runtime-status"

describe("deriveRuntimePresentation", () => {
  it("treats stopped protobuf defaults as idle rather than failed", () => {
    const snapshot = create(ShowSnapshotSchema, {
      runState: RunState.STOPPED,
      audioRuntime: create(AudioRuntimeStatusSchema, { running: false }),
      beatnet: create(BeatNetStatusSchema, { status: "Idle" }),
      dmxRuntime: create(DmxRuntimeStatusSchema, { running: false, simulated: true }),
    })

    expect(deriveRuntimePresentation(snapshot)).toMatchObject({
      audioActive: false,
      beatnetAvailable: false,
      beatnetFailed: false,
      beatnetStatus: "Idle",
      downbeatStatus: "Idle",
      effectsActive: false,
      dmx: { active: false, failed: false, label: "Stopped" },
    })
  })

  it("surfaces the checkpoint error only while audio analysis is active", () => {
    const snapshot = create(ShowSnapshotSchema, {
      runState: RunState.RUNNING,
      audioRuntime: create(AudioRuntimeStatusSchema, { running: true }),
      beatnet: create(BeatNetStatusSchema, {
        status: "Model unavailable",
        lastError: "checkpoint shape is invalid",
      }),
    })

    expect(deriveRuntimePresentation(snapshot)).toMatchObject({
      audioActive: true,
      beatnetAvailable: false,
      beatnetFailed: true,
      beatnetStatus: "Model unavailable",
      beatnetError: "checkpoint shape is invalid",
      downbeatStatus: "Fallback",
    })
  })

  it("reports an active detector and connected output", () => {
    const snapshot = create(ShowSnapshotSchema, {
      runState: RunState.RUNNING,
      audioRuntime: create(AudioRuntimeStatusSchema, { running: true }),
      beatnet: create(BeatNetStatusSchema, { available: true, status: "Ready" }),
      dmxRuntime: create(DmxRuntimeStatusSchema, { running: true, isOpen: true }),
    })

    expect(deriveRuntimePresentation(snapshot)).toMatchObject({
      beatnetAvailable: true,
      beatnetFailed: false,
      beatnetStatus: "Ready",
      downbeatStatus: "Tracking",
      effectsActive: true,
      dmx: { active: true, failed: false, label: "Connected" },
    })
  })
})

describe("deriveDmxPresentation", () => {
  it("prioritizes an active output error over simulation labels", () => {
    const runtime = create(DmxRuntimeStatusSchema, {
      running: true,
      simulated: true,
      lastError: "write failed",
    })

    expect(deriveDmxPresentation(runtime)).toEqual({
      active: true,
      failed: true,
      label: "Error",
    })
  })
})
