import { create } from "@bufbuild/protobuf"
import { QueryClient } from "@tanstack/react-query"
import { Effect } from "effect"
import { AtomRegistry } from "effect/reactivity"
import { afterEach, describe, expect, test, vi } from "vitest"

import {
  LiveFrameSchema,
  ShowSnapshotSchema,
  type LiveFrame,
  type ShowSnapshot,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { snapshotStreamAtom } from "@/hooks/use-snapshot-stream"
import { latestLiveFrame, resetLiveFrameStore } from "@/lib/live-frame-store"
import { showQueryKeys } from "@/lib/queries"

const streams = vi.hoisted(() => ({
  watchSnapshots: undefined as unknown,
  watchLiveFrames: undefined as unknown,
}))

vi.mock("@/lib/show-api", async () => {
  const { Context, Layer } = await import("effect")
  class ShowApi extends Context.Service<ShowApi, typeof streams>()(
    "frontend/hooks/use-snapshot-stream.test/ShowApi",
  ) {}
  return { ShowApi, showApiLayer: Layer.succeed(ShowApi)(streams) }
})

afterEach(resetLiveFrameStore)

function openStream<A>(value: A, interrupted: Set<string>, name: string) {
  return (emit: (value: A) => void) =>
    Effect.sync(() => {
      emit(value)
    }).pipe(
      Effect.andThen(Effect.never),
      Effect.onInterrupt(() =>
        Effect.sync(() => {
          interrupted.add(name)
        }),
      ),
    )
}

describe("snapshotStreamAtom", () => {
  test("runs both streams while mounted and interrupts them on unmount", async () => {
    const queryClient = new QueryClient()
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")
    const registry = AtomRegistry.make()
    const interrupted = new Set<string>()
    const snapshot = create(ShowSnapshotSchema, {})
    const frame = create(LiveFrameSchema, { capturedAtUnixMs: 1000n, sequence: 7n })
    streams.watchSnapshots = openStream<ShowSnapshot>(snapshot, interrupted, "snapshots")
    streams.watchLiveFrames = openStream<LiveFrame>(frame, interrupted, "live-frames")

    const unmount = registry.mount(snapshotStreamAtom(queryClient))
    await vi.waitFor(() => {
      expect(queryClient.getQueryData(showQueryKeys.snapshot)).toBe(snapshot)
      expect(latestLiveFrame()?.frame.sequence).toBe(7n)
    })
    expect(interrupted.size).toBe(0)

    unmount()
    await vi.waitFor(() => {
      expect([...interrupted].toSorted()).toEqual(["live-frames", "snapshots"])
    })
    expect(invalidate).not.toHaveBeenCalled()
  })

  test("invalidates the snapshot query when the snapshot stream dies", async () => {
    const queryClient = new QueryClient()
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")
    const registry = AtomRegistry.make()
    streams.watchSnapshots = () => Effect.die(new Error("stream broke"))
    streams.watchLiveFrames = () => Effect.never

    const unmount = registry.mount(snapshotStreamAtom(queryClient))
    await vi.waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: showQueryKeys.snapshot })
    })
    unmount()
  })
})
