import {
  BluetoothIcon,
  BroadcastIcon,
  DeviceMobileIcon,
  LinkBreakIcon,
  LinkIcon,
  TrashSimpleIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Effect } from "effect"
import { toast } from "sonner"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Spinner } from "@/components/ui/spinner"
import type { BluetoothReceiverStatus } from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { bluetoothReceiverQueryOptions, showQueryKeys } from "@/lib/queries"
import { ShowApi, runShowApi } from "@/lib/show-api"

type DeviceAction = "connect" | "disconnect" | "forget"

export function BluetoothReceiverPanel() {
  const queryClient = useQueryClient()
  const receiver = useQuery(bluetoothReceiverQueryOptions)

  const publishStatus = (status: BluetoothReceiverStatus) => {
    queryClient.setQueryData(showQueryKeys.bluetoothReceiver, status)
    void queryClient.invalidateQueries({ queryKey: showQueryKeys.audioDevices })
  }

  const pairingMutation = useMutation({
    mutationFn: (enabled: boolean) =>
      runShowApi(Effect.flatMap(ShowApi, (api) => api.setBluetoothReceiverPairing(enabled))),
    onSuccess: (status) => {
      publishStatus(status)
      toast.success(status.statusMessage)
    },
    onError: (error) => toast.error(error.message),
  })

  const deviceMutation = useMutation({
    mutationFn: ({
      action,
      deviceId,
    }: {
      readonly action: DeviceAction
      readonly deviceId: string
    }) =>
      runShowApi(
        Effect.flatMap(ShowApi, (api) => {
          switch (action) {
            case "connect":
              return api.connectBluetoothReceiverDevice(deviceId)
            case "disconnect":
              return api.disconnectBluetoothReceiverDevice(deviceId)
            case "forget":
              return api.forgetBluetoothReceiverDevice(deviceId)
          }
        }),
      ),
    onSuccess: (status) => {
      publishStatus(status)
      toast.success(status.statusMessage)
    },
    onError: (error) => toast.error(error.message),
  })

  if (receiver.isPending) {
    return (
      <div className="flex min-h-28 items-center justify-center rounded-md border sm:col-span-2">
        <Spinner className="size-5" />
        <span className="sr-only">Loading Bluetooth receiver</span>
      </div>
    )
  }

  if (receiver.isError) {
    return (
      <Alert variant="destructive" className="sm:col-span-2">
        <WarningCircleIcon aria-hidden="true" />
        <AlertTitle>Bluetooth status could not be loaded</AlertTitle>
        <AlertDescription>{receiver.error.message}</AlertDescription>
      </Alert>
    )
  }

  const status = receiver.data
  const opensWindowsSettings = status.platform.startsWith("Windows")
  const pairingEnabled = status.discoverable && status.pairable
  const pairingLabel = opensWindowsSettings
    ? "Open Bluetooth settings"
    : pairingEnabled
      ? "Close pairing"
      : "Pair a phone"

  return (
    <div className="grid gap-3 rounded-md border p-3 sm:col-span-2">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-2.5">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-foreground">
            <BluetoothIcon className="size-4" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-medium">{status.adapterName || "Bluetooth receiver"}</h3>
              <Badge variant={status.receiverReady ? "secondary" : "destructive"}>
                {status.receiverReady ? "Receiver ready" : "Setup required"}
              </Badge>
              {pairingEnabled ? <Badge variant="outline">Pairing open</Badge> : null}
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">{status.statusMessage}</p>
          </div>
        </div>
        {status.supported ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={pairingMutation.isPending}
            onClick={() => pairingMutation.mutate(opensWindowsSettings || !pairingEnabled)}
          >
            {pairingMutation.isPending ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <BroadcastIcon data-icon="inline-start" aria-hidden="true" />
            )}
            {pairingLabel}
          </Button>
        ) : null}
      </div>

      {status.lastError ? (
        <Alert variant="destructive">
          <WarningCircleIcon aria-hidden="true" />
          <AlertTitle>Receiver unavailable</AlertTitle>
          <AlertDescription>{status.lastError}</AlertDescription>
        </Alert>
      ) : !status.receiverReady ? (
        <Alert>
          <WarningCircleIcon aria-hidden="true" />
          <AlertTitle>Bluetooth audio profile unavailable</AlertTitle>
          <AlertDescription>{status.setupHint}</AlertDescription>
        </Alert>
      ) : (
        <p className="text-xs text-muted-foreground">{status.setupHint}</p>
      )}

      {status.devices.length === 0 ? (
        <Empty className="min-h-32 border py-5">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <DeviceMobileIcon aria-hidden="true" />
            </EmptyMedia>
            <EmptyTitle>No paired audio sources</EmptyTitle>
            <EmptyDescription>
              Open pairing, select {status.adapterName || "this computer"} on a phone, then start
              playing audio.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <div className="grid gap-2">
          {status.devices.map((device) => {
            const pendingDevice =
              deviceMutation.isPending && deviceMutation.variables.deviceId === device.id
            return (
              <div
                key={device.id}
                className="flex flex-col gap-2 rounded-md border bg-card px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="truncate text-sm font-medium">{device.name || device.id}</p>
                    {device.connected ? <Badge variant="secondary">Connected</Badge> : null}
                  </div>
                  <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                    {device.id}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap gap-1.5">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={deviceMutation.isPending}
                    onClick={() =>
                      deviceMutation.mutate({
                        action: device.connected ? "disconnect" : "connect",
                        deviceId: device.id,
                      })
                    }
                  >
                    {pendingDevice && deviceMutation.variables.action !== "forget" ? (
                      <Spinner data-icon="inline-start" />
                    ) : device.connected ? (
                      <LinkBreakIcon data-icon="inline-start" aria-hidden="true" />
                    ) : (
                      <LinkIcon data-icon="inline-start" aria-hidden="true" />
                    )}
                    {device.connected ? "Disconnect" : "Connect"}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={deviceMutation.isPending}
                    onClick={() => deviceMutation.mutate({ action: "forget", deviceId: device.id })}
                  >
                    {pendingDevice && deviceMutation.variables.action === "forget" ? (
                      <Spinner data-icon="inline-start" />
                    ) : (
                      <TrashSimpleIcon data-icon="inline-start" aria-hidden="true" />
                    )}
                    Forget
                  </Button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
