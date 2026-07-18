import { Duration, Effect, Schedule } from "effect"

const retrySchedule = Schedule.exponential("250 millis", 1.5).pipe(
  Schedule.modifyDelay(({ duration }) =>
    Effect.succeed(Duration.min(duration, Duration.seconds(5))),
  ),
)
const reconnectSchedule = Schedule.spaced(250)

export function reconnectSnapshotStream<E>(stream: Effect.Effect<void, E>) {
  return stream.pipe(Effect.retry(retrySchedule), Effect.repeat(reconnectSchedule))
}
