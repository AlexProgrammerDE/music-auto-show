import { queryOptions } from "@tanstack/react-query"
import { Effect } from "effect"

import { ShowApi, runShowApi } from "@/lib/show-api"

export const showQueryKeys = {
  snapshot: ["show", "snapshot"] as const,
  config: ["show", "config"] as const,
  audioDevices: ["show", "audio-devices"] as const,
  bluetoothReceiver: ["show", "bluetooth-receiver"] as const,
  grandMa2FixtureTypes: ["show", "grandma2-fixture-types"] as const,
}

export const snapshotQueryOptions = queryOptions({
  queryKey: showQueryKeys.snapshot,
  queryFn: () => runShowApi(Effect.flatMap(ShowApi, (api) => api.getSnapshot)),
  refetchInterval: 2_000,
})

export const configQueryOptions = queryOptions({
  queryKey: showQueryKeys.config,
  queryFn: () => runShowApi(Effect.flatMap(ShowApi, (api) => api.getConfig)),
})

export const audioDevicesQueryOptions = queryOptions({
  queryKey: showQueryKeys.audioDevices,
  queryFn: () => runShowApi(Effect.flatMap(ShowApi, (api) => api.listAudioDevices)),
})

export const bluetoothReceiverQueryOptions = queryOptions({
  queryKey: showQueryKeys.bluetoothReceiver,
  queryFn: () => runShowApi(Effect.flatMap(ShowApi, (api) => api.getBluetoothReceiverStatus)),
  refetchInterval: 2_000,
})

export const grandMa2FixtureTypesQueryOptions = queryOptions({
  queryKey: showQueryKeys.grandMa2FixtureTypes,
  queryFn: () => runShowApi(Effect.flatMap(ShowApi, (api) => api.listGrandMa2FixtureTypes)),
})
