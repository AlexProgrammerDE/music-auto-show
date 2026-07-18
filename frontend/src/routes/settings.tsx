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

import { ConfirmCredenza } from "@/components/confirm-credenza"
import { PageSkeleton } from "@/components/page-skeleton"
import { SectionPanel } from "@/components/section-panel"
import { SliderNumberField } from "@/components/slider-number-field"
import { Button } from "@/components/ui/button"
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
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

type EnumOption = readonly [string, number]
type SettingsSection = "show" | "audio" | "dmx" | "lighting"

function parseSettingsSection(value: unknown): SettingsSection {
  switch (value) {
    case "audio":
    case "dmx":
    case "lighting":
      return value
    default:
      return "show"
  }
}

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
  return new Promise<{ readonly json: string; readonly name: string } | undefined>(
    (resolve, reject) => {
      const input = document.createElement("input")
      input.type = "file"
      input.accept = "application/json,.json"
      input.addEventListener("change", () => {
        const file = input.files?.[0]
        if (!file) {
          resolve(undefined)
          return
        }
        file.text().then((json) => resolve({ json, name: file.name }), reject)
      })
      input.click()
    },
  )
}

function downloadConfig(json: string, filename: string) {
  const url = URL.createObjectURL(new Blob([json], { type: "application/json" }))
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename || "show.json"
  anchor.click()
  URL.revokeObjectURL(url)
}

function EnumSelectField({
  id,
  name,
  label,
  value,
  options,
  onChange,
}: {
  readonly id: string
  readonly name: string
  readonly label: string
  readonly value: number
  readonly options: readonly EnumOption[]
  readonly onChange: (value: number) => void
}) {
  const items = options.map(([optionLabel, optionValue]) => ({
    label: formatEnumLabel(optionLabel),
    value: String(optionValue),
  }))

  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Select
        name={name}
        items={items}
        value={String(value)}
        onValueChange={(next) => onChange(Number(next))}
      >
        <SelectTrigger id={id} className="w-full">
          <SelectValue>{formatEnumValue(options, value)}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {options.map(([optionLabel, optionValue]) => (
              <SelectItem key={optionLabel} value={String(optionValue)}>
                {formatEnumLabel(optionLabel)}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </Field>
  )
}

export const Route = createFileRoute("/settings")({
  validateSearch: (search: Record<string, unknown>) => ({
    section: parseSettingsSection(search.section),
  }),
  loader: async ({ context }) => {
    await Promise.all([
      context.queryClient.ensureQueryData(configQueryOptions),
      context.queryClient.ensureQueryData(audioDevicesQueryOptions),
    ])
  },
  pendingComponent: PageSkeleton,
  component: SettingsPage,
})

function SettingsPage() {
  const { data: config } = useSuspenseQuery(configQueryOptions)
  const { data: audioDevices } = useSuspenseQuery(audioDevicesQueryOptions)
  const queryClient = Route.useRouteContext({ select: (context) => context.queryClient })
  const { section } = Route.useSearch()
  const navigate = Route.useNavigate()
  const [resetOpen, setResetOpen] = useState(false)
  const [pendingImport, setPendingImport] = useState<{
    readonly json: string
    readonly name: string
  }>()
  const audioDeviceNames = Array.from(new Set(audioDevices.map((device) => device.name)))
  const audioDeviceOptions = ["Automatic", ...audioDeviceNames]

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
    setPendingImport(undefined)
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
          <h1 className="font-heading text-xl font-semibold tracking-tight text-balance">
            Show Settings
          </h1>
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
              void chooseConfigFile()
                .then((selected) => {
                  if (selected) setPendingImport(selected)
                })
                .catch((error: Error) => toast.error(error.message))
            }}
          >
            {importMutation.isPending ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <UploadSimpleIcon data-icon="inline-start" aria-hidden="true" />
            )}
            {importMutation.isPending ? "Loading…" : "Load"}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={exportMutation.isPending}
            onClick={() => exportMutation.mutate()}
          >
            {exportMutation.isPending ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <DownloadSimpleIcon data-icon="inline-start" aria-hidden="true" />
            )}
            {exportMutation.isPending ? "Exporting…" : "Export"}
          </Button>
          <Button type="button" variant="outline" onClick={() => setResetOpen(true)}>
            <FilePlusIcon data-icon="inline-start" aria-hidden="true" /> New
          </Button>
          <form.Subscribe
            selector={(state) => [state.canSubmit, state.isSubmitting, state.isDirty] as const}
          >
            {([canSubmit, isSubmitting, isDirty]) => {
              const saving = isSubmitting || saveMutation.isPending
              return (
                <Button type="submit" disabled={!canSubmit || saving}>
                  {saving ? (
                    <Spinner data-icon="inline-start" />
                  ) : (
                    <FloppyDiskIcon data-icon="inline-start" aria-hidden="true" />
                  )}
                  {saving ? "Saving…" : isDirty ? "Save Changes" : "Save Settings"}
                </Button>
              )
            }}
          </form.Subscribe>
        </div>
      </div>

      <Tabs
        value={section}
        onValueChange={(value) => {
          void navigate({ search: { section: parseSettingsSection(value) }, replace: true })
        }}
      >
        <TabsList variant="line" className="max-w-full justify-start overflow-x-auto">
          <TabsTrigger type="button" value="show">
            Show
          </TabsTrigger>
          <TabsTrigger type="button" value="audio">
            Audio
          </TabsTrigger>
          <TabsTrigger type="button" value="dmx">
            DMX
          </TabsTrigger>
          <TabsTrigger type="button" value="lighting">
            Lighting
          </TabsTrigger>
        </TabsList>

        <TabsContent value="show">
          <SectionPanel title="Show" description="The configuration file identity">
            <FieldGroup className="max-w-xl p-4">
              <form.Field
                name="name"
                validators={{
                  onChange: ({ value }) => (value.trim() ? undefined : "Name is required"),
                }}
              >
                {(field) => {
                  const invalid = field.state.meta.isTouched && !field.state.meta.isValid
                  return (
                    <Field data-invalid={invalid}>
                      <FieldLabel htmlFor={field.name}>Show name</FieldLabel>
                      <Input
                        id={field.name}
                        name={field.name}
                        autoComplete="off"
                        aria-invalid={invalid}
                        value={field.state.value}
                        onBlur={field.handleBlur}
                        onChange={(event) => field.handleChange(event.target.value)}
                      />
                      {invalid ? (
                        <FieldError>{field.state.meta.errors.map(String).join(", ")}</FieldError>
                      ) : null}
                    </Field>
                  )
                }}
              </form.Field>
            </FieldGroup>
          </SectionPanel>
        </TabsContent>

        <TabsContent value="audio">
          <SectionPanel
            title="Audio Input"
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
                <ArrowClockwiseIcon data-icon="inline-start" aria-hidden="true" /> Refresh Devices
              </Button>
            }
          >
            <FieldGroup className="grid gap-5 p-4 sm:grid-cols-2">
              <form.Field name="audioMode">
                {(field) => (
                  <EnumSelectField
                    id={`${field.name}-trigger`}
                    name={field.name}
                    label="Input mode"
                    value={field.state.value}
                    options={audioModes}
                    onChange={field.handleChange}
                  />
                )}
              </form.Field>
              <form.Field name="audioDeviceName">
                {(field) => (
                  <Field>
                    <FieldLabel htmlFor={field.name}>Capture device</FieldLabel>
                    <Combobox
                      items={audioDeviceOptions}
                      value={field.state.value || "Automatic"}
                      onValueChange={(value) =>
                        field.handleChange(value === "Automatic" ? "" : (value ?? ""))
                      }
                    >
                      <ComboboxInput
                        id={field.name}
                        name={field.name}
                        autoComplete="off"
                        placeholder="Search capture devices…"
                      />
                      <ComboboxContent>
                        <ComboboxEmpty>No capture device found.</ComboboxEmpty>
                        <ComboboxList>
                          {audioDeviceOptions.map((deviceName) => (
                            <ComboboxItem key={deviceName} value={deviceName}>
                              {deviceName}
                            </ComboboxItem>
                          ))}
                        </ComboboxList>
                      </ComboboxContent>
                    </Combobox>
                  </Field>
                )}
              </form.Field>
              <form.Field name="pipewireSourceName">
                {(field) => (
                  <Field>
                    <FieldLabel htmlFor={field.name}>PipeWire source</FieldLabel>
                    <Input
                      id={field.name}
                      name={field.name}
                      autoComplete="off"
                      value={field.state.value}
                      placeholder="Default sink monitor…"
                      onBlur={field.handleBlur}
                      onChange={(event) => field.handleChange(event.target.value)}
                    />
                  </Field>
                )}
              </form.Field>
              <form.Field name="audioGain">
                {(field) => (
                  <Field>
                    <FieldLabel htmlFor={field.name}>Input gain</FieldLabel>
                    <InputGroup>
                      <InputGroupInput
                        id={field.name}
                        name={field.name}
                        type="number"
                        inputMode="decimal"
                        autoComplete="off"
                        min={0.1}
                        max={8}
                        step={0.05}
                        value={field.state.value}
                        onBlur={field.handleBlur}
                        onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                      />
                      <InputGroupAddon align="inline-end">×</InputGroupAddon>
                    </InputGroup>
                  </Field>
                )}
              </form.Field>
              <form.Field name="beatnetModelPath">
                {(field) => (
                  <Field className="sm:col-span-2">
                    <FieldLabel htmlFor={field.name}>BeatNet+ checkpoint</FieldLabel>
                    <Input
                      id={field.name}
                      name={field.name}
                      autoComplete="off"
                      value={field.state.value}
                      onBlur={field.handleBlur}
                      onChange={(event) => field.handleChange(event.target.value)}
                    />
                    <FieldDescription>
                      The checkpoint stays outside the binary and loads through native Rust
                      inference.
                    </FieldDescription>
                  </Field>
                )}
              </form.Field>
              <form.Field name="audioSimulate">
                {(field) => (
                  <Field orientation="horizontal" className="sm:col-span-2">
                    <FieldContent>
                      <FieldLabel htmlFor={field.name}>Simulate audio</FieldLabel>
                      <FieldDescription>
                        Generate deterministic audio features without capture hardware.
                      </FieldDescription>
                    </FieldContent>
                    <Switch
                      id={field.name}
                      checked={field.state.value}
                      onCheckedChange={field.handleChange}
                    />
                  </Field>
                )}
              </form.Field>
            </FieldGroup>
          </SectionPanel>
        </TabsContent>

        <TabsContent value="dmx">
          <SectionPanel title="DMX Output" description="Open DMX USB or simulation mode">
            <FieldGroup className="grid gap-5 p-4 sm:grid-cols-2">
              <form.Field name="dmxPort">
                {(field) => (
                  <Field className="sm:col-span-2">
                    <FieldLabel htmlFor={field.name}>Serial port</FieldLabel>
                    <Input
                      id={field.name}
                      name={field.name}
                      autoComplete="off"
                      value={field.state.value}
                      placeholder="auto…"
                      onBlur={field.handleBlur}
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
                    <InputGroup>
                      <InputGroupInput
                        id={field.name}
                        name={field.name}
                        type="number"
                        inputMode="numeric"
                        autoComplete="off"
                        min={1}
                        max={512}
                        value={field.state.value}
                        onBlur={field.handleBlur}
                        onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                      />
                      <InputGroupAddon align="inline-end">channels</InputGroupAddon>
                    </InputGroup>
                  </Field>
                )}
              </form.Field>
              <form.Field name="dmxFps">
                {(field) => (
                  <Field>
                    <FieldLabel htmlFor={field.name}>Refresh rate</FieldLabel>
                    <InputGroup>
                      <InputGroupInput
                        id={field.name}
                        name={field.name}
                        type="number"
                        inputMode="numeric"
                        autoComplete="off"
                        min={1}
                        max={44}
                        value={field.state.value}
                        onBlur={field.handleBlur}
                        onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                      />
                      <InputGroupAddon align="inline-end">FPS</InputGroupAddon>
                    </InputGroup>
                  </Field>
                )}
              </form.Field>
              <form.Field name="dmxSimulate">
                {(field) => (
                  <Field orientation="horizontal" className="sm:col-span-2">
                    <FieldContent>
                      <FieldLabel htmlFor={field.name}>Simulate DMX</FieldLabel>
                      <FieldDescription>
                        Run the effects pipeline without opening a serial port.
                      </FieldDescription>
                    </FieldContent>
                    <Switch
                      id={field.name}
                      checked={field.state.value}
                      onCheckedChange={field.handleChange}
                    />
                  </Field>
                )}
              </form.Field>
            </FieldGroup>
          </SectionPanel>
        </TabsContent>

        <TabsContent value="lighting">
          <SectionPanel
            title="Lighting Effects"
            description="Faithful controls for visualization and movement algorithms"
          >
            <FieldSet className="p-4">
              <FieldLegend variant="label">Programs</FieldLegend>
              <FieldGroup className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
                <form.Field name="visualizationMode">
                  {(field) => (
                    <EnumSelectField
                      id={`${field.name}-trigger`}
                      name={field.name}
                      label="Visualization"
                      value={field.state.value}
                      options={visualizationModes}
                      onChange={field.handleChange}
                    />
                  )}
                </form.Field>
                <form.Field name="movementMode">
                  {(field) => (
                    <EnumSelectField
                      id={`${field.name}-trigger`}
                      name={field.name}
                      label="Movement"
                      value={field.state.value}
                      options={movementModes}
                      onChange={field.handleChange}
                    />
                  )}
                </form.Field>
                <form.Field name="effectFixtureMode">
                  {(field) => (
                    <EnumSelectField
                      id={`${field.name}-trigger`}
                      name={field.name}
                      label="Fixture balance"
                      value={field.state.value}
                      options={effectFixtureModes}
                      onChange={field.handleChange}
                    />
                  )}
                </form.Field>
                <form.Field name="rotationMode">
                  {(field) => (
                    <EnumSelectField
                      id={`${field.name}-trigger`}
                      name={field.name}
                      label="Rotation"
                      value={field.state.value}
                      options={rotationModes}
                      onChange={field.handleChange}
                    />
                  )}
                </form.Field>
                <form.Field name="strobeEffectMode">
                  {(field) => (
                    <EnumSelectField
                      id={`${field.name}-trigger`}
                      name={field.name}
                      label="Strobe program"
                      value={field.state.value}
                      options={strobeModes}
                      onChange={field.handleChange}
                    />
                  )}
                </form.Field>
              </FieldGroup>
            </FieldSet>

            <FieldSet className="border-t p-4">
              <FieldLegend variant="label">Response</FieldLegend>
              <FieldGroup className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
                <form.Field name="intensity">
                  {(field) => (
                    <SliderNumberField
                      id={field.name}
                      name={field.name}
                      label="Intensity"
                      value={field.state.value}
                      min={0}
                      max={1}
                      step={0.05}
                      displayScale={100}
                      unit="%"
                      onBlur={field.handleBlur}
                      onChange={field.handleChange}
                    />
                  )}
                </form.Field>
                <form.Field name="colorSpeed">
                  {(field) => (
                    <SliderNumberField
                      id={field.name}
                      name={field.name}
                      label="Color speed"
                      value={field.state.value}
                      min={0.05}
                      max={8}
                      step={0.05}
                      unit="×"
                      onBlur={field.handleBlur}
                      onChange={field.handleChange}
                    />
                  )}
                </form.Field>
                <form.Field name="beatSensitivity">
                  {(field) => (
                    <SliderNumberField
                      id={field.name}
                      name={field.name}
                      label="Beat sensitivity"
                      value={field.state.value}
                      min={0}
                      max={1}
                      step={0.05}
                      displayScale={100}
                      unit="%"
                      onBlur={field.handleBlur}
                      onChange={field.handleChange}
                    />
                  )}
                </form.Field>
                <form.Field name="smoothFactor">
                  {(field) => (
                    <SliderNumberField
                      id={field.name}
                      name={field.name}
                      label="Smoothing"
                      value={field.state.value}
                      min={0}
                      max={1}
                      step={0.05}
                      displayScale={100}
                      unit="%"
                      onBlur={field.handleBlur}
                      onChange={field.handleChange}
                    />
                  )}
                </form.Field>
                <form.Field name="movementSpeed">
                  {(field) => (
                    <SliderNumberField
                      id={field.name}
                      name={field.name}
                      label="Movement speed"
                      value={field.state.value}
                      min={0.05}
                      max={8}
                      step={0.05}
                      unit="×"
                      onBlur={field.handleBlur}
                      onChange={field.handleChange}
                    />
                  )}
                </form.Field>
                <form.Field name="strobeEffectSpeed">
                  {(field) => (
                    <SliderNumberField
                      id={field.name}
                      name={field.name}
                      label="Strobe speed"
                      value={field.state.value}
                      min={0.05}
                      max={8}
                      step={0.05}
                      unit="×"
                      onBlur={field.handleBlur}
                      onChange={field.handleChange}
                    />
                  )}
                </form.Field>
              </FieldGroup>
            </FieldSet>

            <FieldGroup className="grid border-t md:grid-cols-2 xl:grid-cols-4">
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
                      <FieldContent>
                        <FieldLabel htmlFor={field.name}>{label}</FieldLabel>
                        <FieldDescription>{description}</FieldDescription>
                      </FieldContent>
                      <Switch
                        id={field.name}
                        checked={field.state.value}
                        onCheckedChange={field.handleChange}
                      />
                    </Field>
                  )}
                </form.Field>
              ))}
            </FieldGroup>
          </SectionPanel>
        </TabsContent>
      </Tabs>

      <ConfirmCredenza
        open={pendingImport !== undefined}
        title={`Load ${pendingImport?.name ?? "configuration"}?`}
        description="This replaces the active show configuration and discards unsaved form changes."
        confirmLabel="Load Configuration"
        icon={<UploadSimpleIcon aria-hidden="true" />}
        pending={importMutation.isPending}
        onOpenChange={(open) => {
          if (!open) setPendingImport(undefined)
        }}
        onConfirm={() => {
          if (pendingImport) importMutation.mutate(pendingImport.json)
        }}
      />
      <ConfirmCredenza
        open={resetOpen}
        title="Create a new show?"
        description="This replaces the active configuration on disk with the Rust defaults."
        confirmLabel="Create New Show"
        icon={<FilePlusIcon aria-hidden="true" />}
        pending={resetMutation.isPending}
        onOpenChange={setResetOpen}
        onConfirm={() => resetMutation.mutate()}
      />
    </form>
  )
}
