import { createClient } from "@connectrpc/connect"
import { createGrpcWebTransport } from "@connectrpc/connect-web"
import { Context, Effect, Layer, ManagedRuntime, Schema } from "effect"

import type {
  AudioDevice,
  CommandResult,
  FixtureProfile,
  Recording,
  RecordingStatus,
  ShowConfig,
  ShowSnapshot,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { MusicAutoShowService, type ShowCommand } from "@/gen/music_auto_show/v1/music_auto_show_pb"

export class RpcFailure extends Schema.TaggedErrorClass<RpcFailure>()("RpcFailure", {
  operation: Schema.String,
  message: Schema.String,
}) {}

type ShowApiShape = {
  readonly getSnapshot: Effect.Effect<ShowSnapshot, RpcFailure>
  readonly getConfig: Effect.Effect<ShowConfig, RpcFailure>
  readonly updateConfig: (config: ShowConfig) => Effect.Effect<ShowConfig, RpcFailure>
  readonly exportConfig: Effect.Effect<
    { readonly json: string; readonly filename: string },
    RpcFailure
  >
  readonly importConfig: (json: string) => Effect.Effect<ShowConfig, RpcFailure>
  readonly resetConfig: Effect.Effect<ShowConfig, RpcFailure>
  readonly listAudioDevices: Effect.Effect<readonly AudioDevice[], RpcFailure>
  readonly listFixtureProfiles: Effect.Effect<readonly FixtureProfile[], RpcFailure>
  readonly controlShow: (command: ShowCommand) => Effect.Effect<CommandResult, RpcFailure>
  readonly setBlackout: (enabled: boolean) => Effect.Effect<CommandResult, RpcFailure>
  readonly startRecording: Effect.Effect<RecordingStatus, RpcFailure>
  readonly stopRecording: Effect.Effect<Recording, RpcFailure>
  readonly clearRecording: Effect.Effect<RecordingStatus, RpcFailure>
  readonly watchSnapshots: (
    onSnapshot: (snapshot: ShowSnapshot) => void,
  ) => Effect.Effect<void, RpcFailure>
}

export class ShowApi extends Context.Service<ShowApi, ShowApiShape>()("music-auto-show/ShowApi") {}

const transport = createGrpcWebTransport({
  baseUrl: `${window.location.origin}/api`,
  useBinaryFormat: true,
})
const client = createClient(MusicAutoShowService, transport)

function rpcFailure(operation: string, cause: unknown) {
  return new RpcFailure({
    operation,
    message: cause instanceof Error ? cause.message : String(cause),
  })
}

function requireMessage<T>(operation: string, value: T | undefined): T {
  if (value === undefined) {
    throw new Error(`${operation} returned an incomplete response`)
  }
  return value
}

const liveApi: ShowApiShape = {
  getSnapshot: Effect.tryPromise({
    try: async (signal) =>
      requireMessage("GetSnapshot", (await client.getSnapshot({}, { signal })).snapshot),
    catch: (cause) => rpcFailure("GetSnapshot", cause),
  }),
  getConfig: Effect.tryPromise({
    try: async (signal) =>
      requireMessage("GetConfig", (await client.getConfig({}, { signal })).config),
    catch: (cause) => rpcFailure("GetConfig", cause),
  }),
  updateConfig: (config) =>
    Effect.tryPromise({
      try: async (signal) =>
        requireMessage("UpdateConfig", (await client.updateConfig({ config }, { signal })).config),
      catch: (cause) => rpcFailure("UpdateConfig", cause),
    }),
  exportConfig: Effect.tryPromise({
    try: async (signal) => {
      const response = await client.exportConfig({}, { signal })
      return { json: response.json, filename: response.filename }
    },
    catch: (cause) => rpcFailure("ExportConfig", cause),
  }),
  importConfig: (json) =>
    Effect.tryPromise({
      try: async (signal) =>
        requireMessage("ImportConfig", (await client.importConfig({ json }, { signal })).config),
      catch: (cause) => rpcFailure("ImportConfig", cause),
    }),
  resetConfig: Effect.tryPromise({
    try: async (signal) =>
      requireMessage("ResetConfig", (await client.resetConfig({}, { signal })).config),
    catch: (cause) => rpcFailure("ResetConfig", cause),
  }),
  listAudioDevices: Effect.tryPromise({
    try: async (signal) => (await client.listAudioDevices({}, { signal })).devices,
    catch: (cause) => rpcFailure("ListAudioDevices", cause),
  }),
  listFixtureProfiles: Effect.tryPromise({
    try: async (signal) => (await client.listFixtureProfiles({}, { signal })).profiles,
    catch: (cause) => rpcFailure("ListFixtureProfiles", cause),
  }),
  controlShow: (command) =>
    Effect.tryPromise({
      try: async (signal) =>
        requireMessage("ControlShow", (await client.controlShow({ command }, { signal })).result),
      catch: (cause) => rpcFailure("ControlShow", cause),
    }),
  setBlackout: (enabled) =>
    Effect.tryPromise({
      try: async (signal) =>
        requireMessage("SetBlackout", (await client.setBlackout({ enabled }, { signal })).result),
      catch: (cause) => rpcFailure("SetBlackout", cause),
    }),
  startRecording: Effect.tryPromise({
    try: async (signal) =>
      requireMessage("StartRecording", (await client.startRecording({}, { signal })).status),
    catch: (cause) => rpcFailure("StartRecording", cause),
  }),
  stopRecording: Effect.tryPromise({
    try: async (signal) =>
      requireMessage("StopRecording", (await client.stopRecording({}, { signal })).recording),
    catch: (cause) => rpcFailure("StopRecording", cause),
  }),
  clearRecording: Effect.tryPromise({
    try: async (signal) =>
      requireMessage("ClearRecording", (await client.clearRecording({}, { signal })).status),
    catch: (cause) => rpcFailure("ClearRecording", cause),
  }),
  watchSnapshots: (onSnapshot) =>
    Effect.tryPromise({
      try: async (signal) => {
        for await (const response of client.watchSnapshots({ intervalMs: 50 }, { signal })) {
          if (response.snapshot !== undefined) {
            onSnapshot(response.snapshot)
          }
        }
      },
      catch: (cause) => rpcFailure("WatchSnapshots", cause),
    }),
}

export const showApiRuntime = ManagedRuntime.make(Layer.succeed(ShowApi)(liveApi))

export function runShowApi<A, E>(
  effect: Effect.Effect<A, E, ShowApi>,
  options?: Effect.RunOptions,
) {
  return showApiRuntime.runPromise(effect, options)
}
