import { useQueryClient } from "@tanstack/react-query"
import { Effect } from "effect"
import { useEffect } from "react"

import type { ShowSnapshot } from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { showQueryKeys } from "@/lib/queries"
import { ShowApi, runShowApi } from "@/lib/show-api"
import { reconnectSnapshotStream } from "@/lib/snapshot-stream"

export function useSnapshotStream() {
  const queryClient = useQueryClient()

  useEffect(() => {
    const controller = new AbortController()
    const program = Effect.flatMap(ShowApi, (api) =>
      reconnectSnapshotStream(
        api.watchSnapshots((snapshot) => {
          queryClient.setQueryData<ShowSnapshot>(showQueryKeys.snapshot, snapshot)
        }),
      ),
    )

    void runShowApi(program, { signal: controller.signal }).catch(() => {
      if (!controller.signal.aborted) {
        void queryClient.invalidateQueries({ queryKey: showQueryKeys.snapshot })
      }
    })

    return () => controller.abort()
  }, [queryClient])
}
