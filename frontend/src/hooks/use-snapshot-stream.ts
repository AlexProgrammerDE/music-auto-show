import { useQueryClient } from "@tanstack/react-query"
import { Effect } from "effect"
import { useEffect } from "react"

import type { ShowSnapshot } from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { publishLiveFrame, resetLiveFrameStore } from "@/lib/live-frame-store"
import { showQueryKeys } from "@/lib/queries"
import { ShowApi, runShowApi } from "@/lib/show-api"
import { reconnectSnapshotStream } from "@/lib/snapshot-stream"

export function useSnapshotStream() {
  const queryClient = useQueryClient()

  useEffect(() => {
    const controller = new AbortController()
    resetLiveFrameStore()
    const snapshotProgram = Effect.flatMap(ShowApi, (api) =>
      reconnectSnapshotStream(
        api.watchSnapshots((snapshot) => {
          queryClient.setQueryData<ShowSnapshot>(showQueryKeys.snapshot, snapshot)
        }),
      ),
    )
    const liveFrameProgram = Effect.flatMap(ShowApi, (api) =>
      reconnectSnapshotStream(api.watchLiveFrames(publishLiveFrame)),
    )
    void runShowApi(snapshotProgram, { signal: controller.signal }).catch(() => {
      if (!controller.signal.aborted) {
        void queryClient.invalidateQueries({ queryKey: showQueryKeys.snapshot })
      }
    })
    void runShowApi(liveFrameProgram, { signal: controller.signal }).catch(() => undefined)

    return () => controller.abort()
  }, [queryClient])
}
