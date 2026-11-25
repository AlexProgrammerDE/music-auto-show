import { useAtomMount } from "@effect/atom-react"
import { type QueryClient, useQueryClient } from "@tanstack/react-query"
import { Cause, Effect } from "effect"
import { Atom } from "effect/reactivity"

import type { ShowSnapshot } from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { publishLiveFrame, resetLiveFrameStore } from "@/lib/live-frame-store"
import { showQueryKeys } from "@/lib/queries"
import { ShowApi, showApiLayer } from "@/lib/show-api"
import { reconnectSnapshotStream } from "@/lib/snapshot-stream"

const snapshotStreamAtoms = new WeakMap<QueryClient, Atom.Atom<unknown>>()

/** Streams snapshots into the query cache and live frames into the frame store while mounted. */
export function snapshotStreamAtom(queryClient: QueryClient) {
  let atom = snapshotStreamAtoms.get(queryClient)
  if (!atom) {
    const snapshots = Effect.flatMap(ShowApi, (api) =>
      reconnectSnapshotStream(
        api.watchSnapshots((snapshot) => {
          queryClient.setQueryData<ShowSnapshot>(showQueryKeys.snapshot, snapshot)
        }),
      ),
    ).pipe(
      Effect.onError((cause) =>
        Cause.hasInterruptsOnly(cause)
          ? Effect.void
          : Effect.sync(() => {
              void queryClient.invalidateQueries({ queryKey: showQueryKeys.snapshot })
            }),
      ),
    )
    const liveFrames = Effect.flatMap(ShowApi, (api) =>
      reconnectSnapshotStream(api.watchLiveFrames(publishLiveFrame)),
    )
    atom = Atom.make(
      Effect.sync(resetLiveFrameStore).pipe(
        Effect.andThen(
          // Each stream ends on its own failure without stopping the other.
          Effect.all([Effect.ignoreCause(snapshots), Effect.ignoreCause(liveFrames)], {
            concurrency: "unbounded",
            discard: true,
          }),
        ),
        Effect.provide(showApiLayer),
      ),
    ).pipe(Atom.withLabel("snapshot-stream"))
    snapshotStreamAtoms.set(queryClient, atom)
  }
  return atom
}

export function useSnapshotStream() {
  useAtomMount(snapshotStreamAtom(useQueryClient()))
}
