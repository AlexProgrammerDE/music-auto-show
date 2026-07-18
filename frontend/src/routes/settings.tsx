import { clone, create } from "@bufbuild/protobuf"
import {
  ArrowClockwiseIcon,
  DownloadSimpleIcon,
  FilePlusIcon,
  FloppyDiskIcon,
  UploadSimpleIcon,
} from "@phosphor-icons/react"
import { useForm } from "@tanstack/react-form"
import { useMutation, useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Effect } from "effect"
import { useState } from "react"
import { toast } from "sonner"

import {
  Credenza,
  CredenzaContent,
  CredenzaDescription,
  CredenzaFooter,
  CredenzaHeader,
  CredenzaTitle,
} from "@/components/credenza"
import { SectionPanel } from "@/components/section-panel"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import {
  AudioConfigSchema,
  AudioInputMode,
  DmxConfigSchema,
  EffectFixtureMode,
  EffectsConfigSchema,
  MovementMode,
  RotationMode,
  ShowConfigSchema,
  StrobeEffectMode,
  VisualizationMode,
  type ShowConfig,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { enumEntries, formatEnumLabel, formatEnumValue } from "@/lib/format"
import { audioDevicesQueryOptions, configQueryOptions, showQueryKeys } from "@/lib/queries"
import { ShowApi, runShowApi } from "@/lib/show-api"

const audioModes = enumEntries(AudioInputMode)
const visualizationModes = enumEntries(VisualizationMode)
const movementModes = enumEntries(MovementMode)
const effectFixtureModes = enumEntries(EffectFixtureMode)
const rotationModes = enumEntries(RotationMode)
const strobeModes = enumEntries(StrobeEffectMode)

function cleanFloat(value: number) {
  return Math.round(value * 1000) / 1000
}

function configFormValues(config: ShowConfig) {
  return {
    name: config.name,
    dmxPort: config.dmx?.port ?? "auto",
    dmxUniverseSize: config.dmx?.universeSize ?? 512,
    dmxFps: config.dmx?.fps ?? 40,
    dmxSimulate: config.dmx?.simulate ?? false,
    audioMode: config.audio?.mode ?? AudioInputMode.AUTO,
    audioDeviceName: config.audio?.deviceName ?? "",
    pipewireSourceName: config.audio?.pipewireSourceName ?? "",
    audioSimulate: config.audio?.simulate ?? false,
    audioGain: cleanFloat(config.audio?.gain ?? 1),
    beatnetModelPath: config.audio?.beatnetModelPath || "models/beatnet-plus.pt",
    visualizationMode: config.effects?.mode ?? VisualizationMode.ENERGY,
    intensity: cleanFloat(config.effects?.intensity ?? 1),
    forceMaxBrightness: config.effects?.forceMaxBrightness ?? false,
    colorSpeed: cleanFloat(config.effects?.colorSpeed ?? 1),
    beatSensitivity: cleanFloat(config.effects?.beatSensitivity ?? 0.5),
    smoothFactor: cleanFloat(config.effects?.smoothFactor ?? 0.7),
    strobeOnDrop: config.effects?.strobeOnDrop ?? true,
    movementEnabled: config.effects?.movementEnabled ?? true,
    movementSpeed: cleanFloat(config.effects?.movementSpeed ?? 1),
    movementMode: config.effects?.movementMode ?? MovementMode.STANDARD,
    effectFixtureMode: config.effects?.effectFixtureMode ?? EffectFixtureMode.BALANCED,
    rotationMode: config.effects?.rotationMode ?? RotationMode.AUTO_MUSIC,
    strobeEffectEnabled: config.effects?.strobeEffectEnabled ?? true,
    strobeEffectMode: config.effects?.strobeEffectMode ?? StrobeEffectMode.AUTO,
    strobeEffectSpeed: cleanFloat(config.effects?.strobeEffectSpeed ?? 1),
  }
}

function chooseConfigFile() {
  return new Promise<string>((resolve, reject) => {
    const input = document.createElement("input")
    input.type = "file"
    input.accept = "application/json,.json"
    input.addEventListener("change", () => {
      const file = input.files?.[0]
      if (!file) {
        resolve("")
        return
      }
      file.text().then(resolve, reject)
    })
    input.click()
  })
}

function downloadConfig(json: string, filename: string) {
  const url = URL.createObjectURL(new Blob([json], { type: "application/json" }))
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename || "show.json"
  anchor.click()
  URL.revokeObjectURL(url)
}

export const Route = createFileRoute("/settings")({
  loader: async ({ context }) => {
    await Promise.all([
      context.queryClient.ensureQueryData(configQueryOptions),
      context.queryClient.ensureQueryData(audioDevicesQueryOptions),
    ])
  },
  component: SettingsPage,
})

function SettingsPage() {
  const { data: config } = useSuspenseQuery(configQueryOptions)
  const { data: audioDevices } = useSuspenseQuery(audioDevicesQueryOptions)
  const queryClient = Route.useRouteContext({ select: (context) => context.queryClient })
  const [resetOpen, setResetOpen] = useState(false)

  const saveMutation = useMutation({
    mutationFn: (nextConfig: typeof config) =>
      runShowApi(Effect.flatMap(ShowApi, (api) => api.updateConfig(nextConfig))),
    onSuccess: (saved) => {
      queryClient.setQueryData(showQueryKeys.config, saved)
      void queryClient.invalidateQueries({ queryKey: showQueryKeys.snapshot })
      toast.success("Show configuration saved")
    },
    onError: (error) => toast.error(error.message),
  })

  const form = useForm({
    defaultValues: configFormValues(config),
    onSubmit: async ({ value }) => {
      const next = clone(ShowConfigSchema, config)
      next.name = value.name.trim()
      next.dmx = create(DmxConfigSchema, {
        port: value.dmxPort.trim(),
        universeSize: value.dmxUniverseSize,
        fps: value.dmxFps,
        simulate: value.dmxSimulate,
      })
      next.audio = create(AudioConfigSchema, {
        mode: value.audioMode,
        deviceName: value.audioDeviceName,
        pipewireSourceName: value.pipewireSourceName,
        simulate: value.audioSimulate,
        gain: value.audioGain,
        beatnetModelPath: value.beatnetModelPath,
      })
      next.effects = create(EffectsConfigSchema, {
        mode: value.visualizationMode,
        intensity: value.intensity,
        forceMaxBrightness: value.forceMaxBrightness,
        colorSpeed: value.colorSpeed,
        beatSensitivity: value.beatSensitivity,
        smoothFactor: value.smoothFactor,
        strobeOnDrop: value.strobeOnDrop,
        movementEnabled: value.movementEnabled,
        movementSpeed: value.movementSpeed,
        movementMode: value.movementMode,
        effectFixtureMode: value.effectFixtureMode,
        rotationMode: value.rotationMode,
        strobeEffectEnabled: value.strobeEffectEnabled,
        strobeEffectMode: value.strobeEffectMode,
        strobeEffectSpeed: value.strobeEffectSpeed,
      })
      await saveMutation.mutateAsync(next)
    },
  })

  const applyLoadedConfig = (saved: ShowConfig, message: string) => {
    queryClient.setQueryData(showQueryKeys.config, saved)
    form.reset(configFormValues(saved))
    void queryClient.invalidateQueries({ queryKey: showQueryKeys.snapshot })
    setResetOpen(false)
    toast.success(message)
  }

  const importMutation = useMutation({
    mutationFn: (json: string) =>
      runShowApi(Effect.flatMap(ShowApi, (api) => api.importConfig(json))),
    onSuccess: (saved) => applyLoadedConfig(saved, `Loaded ${saved.name}`),
    onError: (error) => toast.error(error.message),
  })

  const resetMutation = useMutation({
    mutationFn: () => runShowApi(Effect.flatMap(ShowApi, (api) => api.resetConfig)),
    onSuccess: (saved) => applyLoadedConfig(saved, "New show configuration created"),
    onError: (error) => toast.error(error.message),
  })

  const exportMutation = useMutation({
    mutationFn: () => runShowApi(Effect.flatMap(ShowApi, (api) => api.exportConfig)),
    onSuccess: ({ json, filename }) => {
      downloadConfig(json, filename)
      toast.success(`Saved ${filename}`)
    },
    onError: (error) => toast.error(error.message),
  })

  return (
    <form
      className="grid gap-5"
      onSubmit={(event) => {
        event.preventDefault()
        event.stopPropagation()
        void form.handleSubmit()
      }}
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-heading text-xl font-semibold tracking-tight">Show settings</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Audio capture, BeatNet+, lighting behavior, and hardware output.
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={importMutation.isPending}
            onClick={() => {
              void chooseConfigFile().then((json) => {
                if (json) importMutation.mutate(json)
              })
            }}
          >
            <UploadSimpleIcon /> Load
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={exportMutation.isPending}
            onClick={() => exportMutation.mutate()}
          >
            <DownloadSimpleIcon /> Export
          </Button>
          <Button type="button" variant="outline" onClick={() => setResetOpen(true)}>
            <FilePlusIcon /> New
          </Button>
          <form.Subscribe
            selector={(state) => [state.canSubmit, state.isSubmitting, state.isDirty] as const}
          >
            {([canSubmit, isSubmitting, isDirty]) => (
              <Button type="submit" disabled={!canSubmit || isSubmitting || saveMutation.isPending}>
                <FloppyDiskIcon /> {isDirty ? "Save changes" : "Save settings"}
              </Button>
            )}
          </form.Subscribe>
        </div>
        <Credenza open={resetOpen} onOpenChange={setResetOpen}>
          <CredenzaContent>
            <CredenzaHeader>
              <CredenzaTitle>Create a new show?</CredenzaTitle>
              <CredenzaDescription>
                This replaces the active configuration on disk with the Rust defaults.
              </CredenzaDescription>
            </CredenzaHeader>
            <CredenzaFooter>
              <Button type="button" variant="outline" onClick={() => setResetOpen(false)}>
                Cancel
              </Button>
              <Button
                type="button"
                disabled={resetMutation.isPending}
                onClick={() => resetMutation.mutate()}
              >
                Create new show
              </Button>
            </CredenzaFooter>
          </CredenzaContent>
        </Credenza>
      </div>

      <SectionPanel title="Show" description="The configuration file identity">
        <div className="max-w-xl p-4">
          <form.Field
            name="name"
            validators={{
              onChange: ({ value }) => (value.trim() ? undefined : "Name is required"),
            }}
          >
            {(field) => (
              <Field>
                <FieldLabel htmlFor={field.name}>Show name</FieldLabel>
                <Input
                  id={field.name}
                  value={field.state.value}
                  onBlur={field.handleBlur}
                  onChange={(event) => field.handleChange(event.target.value)}
                />
              </Field>
            )}
          </form.Field>
        </div>
      </SectionPanel>

      <div className="grid gap-5 xl:grid-cols-2">
        <SectionPanel
          title="Audio input"
          description="System audio, microphone, or simulation"
          action={
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() =>
                void queryClient.invalidateQueries({ queryKey: showQueryKeys.audioDevices })
              }
            >
              <ArrowClockwiseIcon /> Refresh devices
            </Button>
          }
        >
          <div className="grid gap-5 p-4 sm:grid-cols-2">
            <form.Field name="audioMode">
              {(field) => (
                <Field>
                  <FieldLabel>Input mode</FieldLabel>
                  <Select
                    value={String(field.state.value)}
                    onValueChange={(value) => field.handleChange(Number(value))}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue>{formatEnumValue(audioModes, field.state.value)}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {audioModes.map(([label, value]) => (
                        <SelectItem key={label} value={String(value)}>
                          {formatEnumLabel(label)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
              )}
            </form.Field>
            <form.Field name="audioDeviceName">
              {(field) => (
                <Field>
                  <FieldLabel>Capture device</FieldLabel>
                  <Select
                    value={field.state.value || "__auto"}
                    onValueChange={(value) =>
                      field.handleChange(value === "__auto" ? "" : (value ?? ""))
                    }
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue>{field.state.value || "Automatic"}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__auto">Automatic</SelectItem>
                      {audioDevices.map((device) => (
                        <SelectItem key={device.id} value={device.name}>
                          {device.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
              )}
            </form.Field>
            <form.Field name="pipewireSourceName">
              {(field) => (
                <Field>
                  <FieldLabel htmlFor={field.name}>PipeWire source</FieldLabel>
                  <Input
                    id={field.name}
                    value={field.state.value}
                    placeholder="Default sink monitor"
                    onChange={(event) => field.handleChange(event.target.value)}
                  />
                </Field>
              )}
            </form.Field>
            <form.Field name="audioGain">
              {(field) => (
                <Field>
                  <FieldLabel htmlFor={field.name}>Input gain</FieldLabel>
                  <Input
                    id={field.name}
                    type="number"
                    min={0.1}
                    max={8}
                    step={0.05}
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                  />
                </Field>
              )}
            </form.Field>
            <form.Field name="beatnetModelPath">
              {(field) => (
                <Field className="sm:col-span-2">
                  <FieldLabel htmlFor={field.name}>BeatNet+ checkpoint</FieldLabel>
                  <Input
                    id={field.name}
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.value)}
                  />
                  <FieldDescription>
                    The checkpoint stays outside the binary and is loaded by the native Rust
                    inference path.
                  </FieldDescription>
                </Field>
              )}
            </form.Field>
            <form.Field name="audioSimulate">
              {(field) => (
                <Field orientation="horizontal" className="sm:col-span-2">
                  <div className="flex-1">
                    <FieldLabel htmlFor={field.name}>Simulate audio</FieldLabel>
                    <FieldDescription>
                      Generate deterministic audio features without capture hardware.
                    </FieldDescription>
                  </div>
                  <Switch
                    id={field.name}
                    checked={field.state.value}
                    onCheckedChange={field.handleChange}
                  />
                </Field>
              )}
            </form.Field>
          </div>
        </SectionPanel>

        <SectionPanel title="DMX output" description="Open DMX USB or simulation mode">
          <div className="grid gap-5 p-4 sm:grid-cols-2">
            <form.Field name="dmxPort">
              {(field) => (
                <Field className="sm:col-span-2">
                  <FieldLabel htmlFor={field.name}>Serial port</FieldLabel>
                  <Input
                    id={field.name}
                    value={field.state.value}
                    placeholder="auto"
                    onChange={(event) => field.handleChange(event.target.value)}
                  />
                  <FieldDescription>
                    Use auto to discover a compatible USB serial interface.
                  </FieldDescription>
                </Field>
              )}
            </form.Field>
            <form.Field name="dmxUniverseSize">
              {(field) => (
                <Field>
                  <FieldLabel htmlFor={field.name}>Universe size</FieldLabel>
                  <Input
                    id={field.name}
                    type="number"
                    min={1}
                    max={512}
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                  />
                </Field>
              )}
            </form.Field>
            <form.Field name="dmxFps">
              {(field) => (
                <Field>
                  <FieldLabel htmlFor={field.name}>Refresh rate</FieldLabel>
                  <Input
                    id={field.name}
                    type="number"
                    min={1}
                    max={44}
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                  />
                </Field>
              )}
            </form.Field>
            <form.Field name="dmxSimulate">
              {(field) => (
                <Field orientation="horizontal" className="sm:col-span-2">
                  <div className="flex-1">
                    <FieldLabel htmlFor={field.name}>Simulate DMX</FieldLabel>
                    <FieldDescription>
                      Run the complete effects pipeline without opening a serial port.
                    </FieldDescription>
                  </div>
                  <Switch
                    id={field.name}
                    checked={field.state.value}
                    onCheckedChange={field.handleChange}
                  />
                </Field>
              )}
            </form.Field>
          </div>
        </SectionPanel>
      </div>

      <SectionPanel
        title="Lighting effects"
        description="Faithful controls for every visualization and movement algorithm"
      >
        <div className="grid gap-5 p-4 md:grid-cols-2 xl:grid-cols-4">
          <form.Field name="visualizationMode">
            {(field) => (
              <Field>
                <FieldLabel>Visualization</FieldLabel>
                <Select
                  value={String(field.state.value)}
                  onValueChange={(value) => field.handleChange(Number(value))}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue>
                      {formatEnumValue(visualizationModes, field.state.value)}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {visualizationModes.map(([label, value]) => (
                      <SelectItem key={label} value={String(value)}>
                        {formatEnumLabel(label)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )}
          </form.Field>
          <form.Field name="movementMode">
            {(field) => (
              <Field>
                <FieldLabel>Movement</FieldLabel>
                <Select
                  value={String(field.state.value)}
                  onValueChange={(value) => field.handleChange(Number(value))}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue>{formatEnumValue(movementModes, field.state.value)}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {movementModes.map(([label, value]) => (
                      <SelectItem key={label} value={String(value)}>
                        {formatEnumLabel(label)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )}
          </form.Field>
          <form.Field name="effectFixtureMode">
            {(field) => (
              <Field>
                <FieldLabel>Fixture balance</FieldLabel>
                <Select
                  value={String(field.state.value)}
                  onValueChange={(value) => field.handleChange(Number(value))}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue>
                      {formatEnumValue(effectFixtureModes, field.state.value)}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {effectFixtureModes.map(([label, value]) => (
                      <SelectItem key={label} value={String(value)}>
                        {formatEnumLabel(label)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )}
          </form.Field>
          <form.Field name="rotationMode">
            {(field) => (
              <Field>
                <FieldLabel>Rotation</FieldLabel>
                <Select
                  value={String(field.state.value)}
                  onValueChange={(value) => field.handleChange(Number(value))}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue>{formatEnumValue(rotationModes, field.state.value)}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {rotationModes.map(([label, value]) => (
                      <SelectItem key={label} value={String(value)}>
                        {formatEnumLabel(label)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )}
          </form.Field>
          <form.Field name="intensity">
            {(field) => (
              <Field>
                <FieldLabel htmlFor={field.name}>Intensity</FieldLabel>
                <Input
                  id={field.name}
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={field.state.value}
                  onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                />
              </Field>
            )}
          </form.Field>
          <form.Field name="colorSpeed">
            {(field) => (
              <Field>
                <FieldLabel htmlFor={field.name}>Color speed</FieldLabel>
                <Input
                  id={field.name}
                  type="number"
                  min={0.05}
                  max={8}
                  step={0.05}
                  value={field.state.value}
                  onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                />
              </Field>
            )}
          </form.Field>
          <form.Field name="beatSensitivity">
            {(field) => (
              <Field>
                <FieldLabel htmlFor={field.name}>Beat sensitivity</FieldLabel>
                <Input
                  id={field.name}
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={field.state.value}
                  onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                />
              </Field>
            )}
          </form.Field>
          <form.Field name="smoothFactor">
            {(field) => (
              <Field>
                <FieldLabel htmlFor={field.name}>Smoothing</FieldLabel>
                <Input
                  id={field.name}
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={field.state.value}
                  onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                />
              </Field>
            )}
          </form.Field>
          <form.Field name="movementSpeed">
            {(field) => (
              <Field>
                <FieldLabel htmlFor={field.name}>Movement speed</FieldLabel>
                <Input
                  id={field.name}
                  type="number"
                  min={0.05}
                  max={8}
                  step={0.05}
                  value={field.state.value}
                  onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                />
              </Field>
            )}
          </form.Field>
          <form.Field name="strobeEffectMode">
            {(field) => (
              <Field>
                <FieldLabel>Strobe program</FieldLabel>
                <Select
                  value={String(field.state.value)}
                  onValueChange={(value) => field.handleChange(Number(value))}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue>{formatEnumValue(strobeModes, field.state.value)}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {strobeModes.map(([label, value]) => (
                      <SelectItem key={label} value={String(value)}>
                        {formatEnumLabel(label)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )}
          </form.Field>
          <form.Field name="strobeEffectSpeed">
            {(field) => (
              <Field>
                <FieldLabel htmlFor={field.name}>Strobe speed</FieldLabel>
                <Input
                  id={field.name}
                  type="number"
                  min={0.05}
                  max={8}
                  step={0.05}
                  value={field.state.value}
                  onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                />
              </Field>
            )}
          </form.Field>
        </div>
        <div className="grid border-t md:grid-cols-2 xl:grid-cols-5">
          {(
            [
              [
                "forceMaxBrightness",
                "Maximum brightness",
                "Force the master intensity channel to full.",
              ],
              ["strobeOnDrop", "Strobe on drop", "Trigger strobe accents on musical drops."],
              [
                "movementEnabled",
                "Movement enabled",
                "Drive pan and tilt from the movement algorithm.",
              ],
              [
                "strobeEffectEnabled",
                "Effect program",
                "Enable the fixture-specific strobe effect channel.",
              ],
            ] as const
          ).map(([name, label, description]) => (
            <form.Field key={name} name={name}>
              {(field) => (
                <Field orientation="horizontal" className="border-r p-4">
                  <div className="flex-1">
                    <FieldLabel htmlFor={field.name}>{label}</FieldLabel>
                    <FieldDescription>{description}</FieldDescription>
                  </div>
                  <Switch
                    id={field.name}
                    checked={field.state.value}
                    onCheckedChange={field.handleChange}
                  />
                </Field>
              )}
            </form.Field>
          ))}
        </div>
      </SectionPanel>
    </form>
  )
}
