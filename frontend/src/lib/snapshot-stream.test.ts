import { Effect, Fiber } from "effect"
import { describe, expect, it } from "vitest"

import { reconnectSnapshotStream } from "@/lib/snapshot-stream"

describe("reconnectSnapshotStream", () => {
  it("opens another stream after a graceful server restart", async () => {
    let connections = 0
    const fiber = Effect.runFork(
      reconnectSnapshotStream(
        Effect.sync(() => {
          connections += 1
        }),
      ),
    )

    await new Promise((resolve) => setTimeout(resolve, 620))
    await Effect.runPromise(Fiber.interrupt(fiber))

    expect(connections).toBeGreaterThanOrEqual(3)
  })
})
