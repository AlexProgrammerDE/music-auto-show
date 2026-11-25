import { Effect, Fiber } from "effect"
import { describe, expect, it } from "vitest"

import { reconnectSnapshotStream } from "@/lib/snapshot-stream"

describe("reconnectSnapshotStream", () => {
  it("opens another stream after a graceful server restart", async () => {
    let connections = 0
    const fiber = Effect.sync(() => {
      connections += 1
    }).pipe(reconnectSnapshotStream, Effect.runFork)

    await new Promise((resolve) => {
      setTimeout(resolve, 620)
    })
    await fiber.pipe(Fiber.interrupt, Effect.runPromise)

    expect(connections).toBeGreaterThanOrEqual(3)
  })
})
