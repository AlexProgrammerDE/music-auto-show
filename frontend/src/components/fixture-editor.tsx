import { clone, create } from "@bufbuild/protobuf"
import { PlusIcon, SlidersHorizontalIcon, TrashIcon } from "@phosphor-icons/react"
import { useForm } from "@tanstack/react-form"
import { useState } from "react"

import {
  Credenza,
  CredenzaBody,
  CredenzaClose,
  CredenzaContent,
  CredenzaDescription,
  CredenzaFooter,
  CredenzaHeader,
  CredenzaTitle,
} from "@/components/credenza"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
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
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
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
import {
  ChannelConfigSchema,
  FixtureConfigSchema,
  type ChannelConfig,
  type FixtureConfig,
  type FixtureProfile,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import { formatEnumLabel } from "@/lib/format"

const channelTypes = [
  "intensity",
  "intensity_master_dimmer",
  "intensity_dimmer",
  "intensity_red",
  "intensity_green",
  "intensity_blue",
  "intensity_white",
  "intensity_amber",
  "intensity_uv",
  "intensity_cyan",
  "intensity_magenta",
  "intensity_yellow",
  "intensity_hue",
  "intensity_saturation",
  "intensity_value",
  "position_pan",
  "position_pan_fine",
  "position_tilt",
  "position_tilt_fine",
  "speed_pan_tilt_fast_slow",
  "speed_pan_tilt_slow_fast",
  "color_wheel",
  "color_macro",
  "color_cto_mixer",
  "color_ctb_mixer",
  "gobo_wheel",
  "gobo_index",
  "shutter_strobe",
  "shutter_strobe_slow_fast",
  "shutter_strobe_fast_slow",
  "shutter_iris_min_to_max",
  "shutter_iris_max_to_min",
  "beam_zoom_small_big",
  "beam_zoom_big_small",
  "beam_focus_near_far",
  "beam_focus_far_near",
  "prism",
  "prism_rotation",
  "effect",
  "effect_speed",
  "effect_pattern",
  "effect_pattern_speed",
  "maintenance",
  "nothing",
  "fixed",
] as const

type FixtureEditorProps = {
  readonly fixture: FixtureConfig
  readonly profiles: readonly FixtureProfile[]
  readonly existingNames: readonly string[]
  readonly open: boolean
  readonly pending: boolean
  readonly onOpenChange: (open: boolean) => void
  readonly onSave: (fixture: FixtureConfig) => Promise<void>
}

function clampDmx(value: number) {
  return Math.max(0, Math.min(255, Number.isFinite(value) ? value : 0))
}

export function FixtureEditor({
  fixture,
  profiles,
  existingNames,
  open,
  pending,
  onOpenChange,
  onSave,
}: FixtureEditorProps) {
  const [channels, setChannels] = useState(() =>
    fixture.channels.map((channel) => clone(ChannelConfigSchema, channel)),
  )

  const updateChannel = (offset: number, update: (channel: ChannelConfig) => void) => {
    setChannels((current) =>
      current.map((channel) => {
        if (channel.offset !== offset) return channel
        const next = clone(ChannelConfigSchema, channel)
        update(next)
        return next
      }),
    )
  }

  const addChannel = () => {
    const offset = Math.max(0, ...channels.map((channel) => channel.offset)) + 1
    setChannels((current) => [
      ...current,
      create(ChannelConfigSchema, {
        offset,
        name: `Channel ${offset}`,
        channelType: "nothing",
        minValue: 0,
        maxValue: 255,
        enabled: true,
      }),
    ])
  }

  const form = useForm({
    defaultValues: {
      name: fixture.name,
      profileName: fixture.profileName,
      startChannel: fixture.startChannel,
      position: fixture.position,
      intensityScale: fixture.intensityScale,
      panMin: fixture.panMin,
      panMax: fixture.panMax,
      tiltMin: fixture.tiltMin,
      tiltMax: fixture.tiltMax,
    },
    onSubmit: async ({ value }) => {
      const next = clone(FixtureConfigSchema, fixture)
      next.name = value.name.trim()
      next.profileName = value.profileName
      next.startChannel = value.startChannel
      next.position = value.position
      next.intensityScale = value.intensityScale
      next.panMin = value.panMin
      next.panMax = value.panMax
      next.tiltMin = value.tiltMin
      next.tiltMax = value.tiltMax
      next.channels = channels.map((channel) => clone(ChannelConfigSchema, channel))
      await onSave(next)
    },
  })

  return (
    <Credenza open={open} onOpenChange={onOpenChange}>
      <CredenzaContent className="max-h-[90vh] sm:max-w-5xl">
        <CredenzaHeader>
          <CredenzaTitle>Edit {fixture.name}</CredenzaTitle>
          <CredenzaDescription>
            Patch, scale, and override the fixture channels used by the effects engine.
          </CredenzaDescription>
        </CredenzaHeader>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(event) => {
            event.preventDefault()
            event.stopPropagation()
            void form.handleSubmit()
          }}
        >
          <CredenzaBody className="grid gap-5 overflow-y-auto">
            <FieldGroup className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              <form.Field
                name="name"
                validators={{
                  onChange: ({ value }) => {
                    if (!value.trim()) return "Name is required"
                    if (
                      existingNames.some(
                        (name) => name.toLocaleLowerCase() === value.trim().toLocaleLowerCase(),
                      )
                    ) {
                      return "Name must be unique"
                    }
                    return undefined
                  },
                }}
              >
                {(field) => {
                  const invalid = field.state.meta.isTouched && !field.state.meta.isValid
                  return (
                    <Field className="md:col-span-2" data-invalid={invalid}>
                      <FieldLabel htmlFor={field.name}>Name</FieldLabel>
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
              <form.Field name="startChannel">
                {(field) => (
                  <Field>
                    <FieldLabel htmlFor={field.name}>Start channel</FieldLabel>
                    <Input
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
                  </Field>
                )}
              </form.Field>
              <form.Field name="position">
                {(field) => (
                  <Field>
                    <FieldLabel htmlFor={field.name}>Show position</FieldLabel>
                    <Input
                      id={field.name}
                      name={field.name}
                      type="number"
                      inputMode="numeric"
                      autoComplete="off"
                      min={0}
                      value={field.state.value}
                      onBlur={field.handleBlur}
                      onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                    />
                  </Field>
                )}
              </form.Field>
              <form.Field name="profileName">
                {(field) => (
                  <Field className="md:col-span-2">
                    <FieldLabel htmlFor={`${field.name}-trigger`}>Profile</FieldLabel>
                    <Select
                      name={field.name}
                      items={[
                        { label: "Custom", value: "__custom" },
                        ...profiles.map((profile) => ({
                          label: profile.name,
                          value: profile.name,
                        })),
                      ]}
                      value={field.state.value || "__custom"}
                      onValueChange={(value) => {
                        const profileName = value === "__custom" ? "" : (value ?? "")
                        field.handleChange(profileName)
                        const profile = profiles.find((candidate) => candidate.name === profileName)
                        setChannels(
                          profile
                            ? profile.channels.map((channel) => clone(ChannelConfigSchema, channel))
                            : [],
                        )
                      }}
                    >
                      <SelectTrigger id={`${field.name}-trigger`} className="w-full">
                        <SelectValue>{field.state.value || "Custom"}</SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          <SelectItem value="__custom">Custom</SelectItem>
                          {profiles.map((profile) => (
                            <SelectItem key={profile.name} value={profile.name}>
                              {profile.name}
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                    <FieldDescription>
                      Selecting a profile replaces the channel overrides below.
                    </FieldDescription>
                  </Field>
                )}
              </form.Field>
              <form.Field name="intensityScale">
                {(field) => (
                  <Field>
                    <FieldLabel htmlFor={field.name}>Intensity scale</FieldLabel>
                    <Input
                      id={field.name}
                      name={field.name}
                      type="number"
                      inputMode="decimal"
                      autoComplete="off"
                      min={0}
                      max={1}
                      step={0.01}
                      value={field.state.value}
                      onBlur={field.handleBlur}
                      onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                    />
                  </Field>
                )}
              </form.Field>
            </FieldGroup>

            <FieldSet className="border p-4">
              <FieldLegend>Movement limits</FieldLegend>
              <FieldGroup className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {(["panMin", "panMax", "tiltMin", "tiltMax"] as const).map((name) => (
                  <form.Field key={name} name={name}>
                    {(field) => (
                      <Field>
                        <FieldLabel htmlFor={field.name} className="capitalize">
                          {name.replace(/([A-Z])/g, " $1")}
                        </FieldLabel>
                        <Input
                          id={field.name}
                          name={field.name}
                          type="number"
                          inputMode="numeric"
                          autoComplete="off"
                          min={0}
                          max={255}
                          value={field.state.value}
                          onBlur={field.handleBlur}
                          onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                        />
                      </Field>
                    )}
                  </form.Field>
                ))}
              </FieldGroup>
            </FieldSet>

            <section className="grid gap-3">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                  <h3 className="font-heading text-sm font-semibold">Channel overrides</h3>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Disabled channels stay at zero. Fixed channels ignore live effects.
                  </p>
                </div>
                <Button type="button" size="sm" variant="outline" onClick={addChannel}>
                  <PlusIcon data-icon="inline-start" aria-hidden="true" /> Add Channel
                </Button>
              </div>

              {channels.length === 0 ? (
                <Empty className="border">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <SlidersHorizontalIcon aria-hidden="true" />
                    </EmptyMedia>
                    <EmptyTitle>No channel overrides</EmptyTitle>
                    <EmptyDescription>
                      Add a channel to configure a custom fixture profile.
                    </EmptyDescription>
                  </EmptyHeader>
                  <EmptyContent>
                    <Button type="button" size="sm" variant="outline" onClick={addChannel}>
                      <PlusIcon data-icon="inline-start" aria-hidden="true" /> Add Channel
                    </Button>
                  </EmptyContent>
                </Empty>
              ) : (
                <form.Subscribe selector={(state) => state.values.startChannel}>
                  {(startChannel) => (
                    <Accordion
                      key={channels.map((channel) => channel.offset).join("-")}
                      defaultValue={channels.map((channel) => String(channel.offset))}
                      className="border px-3"
                    >
                      {channels.map((channel) => (
                        <AccordionItem key={channel.offset} value={String(channel.offset)}>
                          <AccordionTrigger className="gap-3 hover:no-underline">
                            <span className="flex min-w-0 flex-1 items-center gap-3">
                              <span className="w-16 shrink-0 text-xs text-muted-foreground tabular-nums">
                                DMX {startChannel + channel.offset - 1}
                              </span>
                              <span className="truncate">{channel.name}</span>
                              <span className="hidden truncate text-xs font-normal text-muted-foreground sm:inline">
                                {formatEnumLabel(channel.channelType)}
                              </span>
                            </span>
                          </AccordionTrigger>
                          <AccordionContent className="grid gap-3 lg:grid-cols-[minmax(8rem,1fr)_minmax(13rem,1.4fr)_5rem_5rem_6rem_auto_auto] lg:items-end">
                            <Field>
                              <FieldLabel htmlFor={`channel-name-${channel.offset}`}>
                                Name
                              </FieldLabel>
                              <Input
                                id={`channel-name-${channel.offset}`}
                                name={`channel-name-${channel.offset}`}
                                autoComplete="off"
                                value={channel.name}
                                onChange={(event) =>
                                  updateChannel(channel.offset, (next) => {
                                    next.name = event.target.value
                                  })
                                }
                              />
                            </Field>
                            <Field>
                              <FieldLabel htmlFor={`channel-type-${channel.offset}`}>
                                Type
                              </FieldLabel>
                              <Combobox
                                items={[...channelTypes]}
                                value={channel.channelType}
                                itemToStringLabel={formatEnumLabel}
                                onValueChange={(value) =>
                                  updateChannel(channel.offset, (next) => {
                                    next.channelType = value ?? "nothing"
                                  })
                                }
                              >
                                <ComboboxInput
                                  id={`channel-type-${channel.offset}`}
                                  name={`channel-type-${channel.offset}`}
                                  autoComplete="off"
                                  placeholder="Search channel types…"
                                />
                                <ComboboxContent>
                                  <ComboboxEmpty>No channel type found.</ComboboxEmpty>
                                  <ComboboxList>
                                    {channelTypes.map((channelType) => (
                                      <ComboboxItem key={channelType} value={channelType}>
                                        {formatEnumLabel(channelType)}
                                      </ComboboxItem>
                                    ))}
                                  </ComboboxList>
                                </ComboboxContent>
                              </Combobox>
                            </Field>
                            {(["minValue", "maxValue"] as const).map((property) => (
                              <Field key={property}>
                                <FieldLabel htmlFor={`channel-${property}-${channel.offset}`}>
                                  {property === "minValue" ? "Min" : "Max"}
                                </FieldLabel>
                                <Input
                                  id={`channel-${property}-${channel.offset}`}
                                  name={`channel-${property}-${channel.offset}`}
                                  type="number"
                                  inputMode="numeric"
                                  autoComplete="off"
                                  min={0}
                                  max={255}
                                  value={channel[property]}
                                  onChange={(event) =>
                                    updateChannel(channel.offset, (next) => {
                                      next[property] = clampDmx(event.target.valueAsNumber)
                                    })
                                  }
                                />
                              </Field>
                            ))}
                            <Field>
                              <FieldLabel htmlFor={`channel-fixed-value-${channel.offset}`}>
                                Fixed value
                              </FieldLabel>
                              <Input
                                id={`channel-fixed-value-${channel.offset}`}
                                name={`channel-fixed-value-${channel.offset}`}
                                type="number"
                                inputMode="numeric"
                                autoComplete="off"
                                min={0}
                                max={255}
                                disabled={channel.fixedValue === undefined}
                                value={channel.fixedValue ?? channel.defaultValue}
                                onChange={(event) =>
                                  updateChannel(channel.offset, (next) => {
                                    next.fixedValue = clampDmx(event.target.valueAsNumber)
                                  })
                                }
                              />
                            </Field>
                            <Field orientation="horizontal" className="lg:pb-2">
                              <FieldContent>
                                <FieldLabel htmlFor={`channel-fixed-${channel.offset}`}>
                                  Fixed
                                </FieldLabel>
                              </FieldContent>
                              <Switch
                                id={`channel-fixed-${channel.offset}`}
                                checked={channel.fixedValue !== undefined}
                                onCheckedChange={(checked) =>
                                  updateChannel(channel.offset, (next) => {
                                    next.fixedValue = checked ? next.defaultValue : undefined
                                  })
                                }
                              />
                            </Field>
                            <div className="flex items-center justify-end gap-2 lg:pb-1">
                              <Field orientation="horizontal" className="w-auto">
                                <FieldLabel htmlFor={`channel-enabled-${channel.offset}`}>
                                  Enabled
                                </FieldLabel>
                                <Switch
                                  id={`channel-enabled-${channel.offset}`}
                                  checked={channel.enabled}
                                  onCheckedChange={(checked) =>
                                    updateChannel(channel.offset, (next) => {
                                      next.enabled = checked
                                    })
                                  }
                                />
                              </Field>
                              <Button
                                type="button"
                                size="icon-sm"
                                variant="ghost"
                                aria-label={`Remove ${channel.name}`}
                                onClick={() =>
                                  setChannels((current) =>
                                    current.filter(
                                      (candidate) => candidate.offset !== channel.offset,
                                    ),
                                  )
                                }
                              >
                                <TrashIcon aria-hidden="true" />
                              </Button>
                            </div>
                          </AccordionContent>
                        </AccordionItem>
                      ))}
                    </Accordion>
                  )}
                </form.Subscribe>
              )}
            </section>
          </CredenzaBody>

          <CredenzaFooter>
            <CredenzaClose type="button">Cancel</CredenzaClose>
            <form.Subscribe selector={(state) => [state.canSubmit, state.isSubmitting] as const}>
              {([canSubmit, isSubmitting]) => {
                const saving = isSubmitting || pending
                return (
                  <Button type="submit" disabled={!canSubmit || saving}>
                    {saving ? <Spinner data-icon="inline-start" /> : null}
                    {saving ? "Saving…" : "Save Fixture"}
                  </Button>
                )
              }}
            </form.Subscribe>
          </CredenzaFooter>
        </form>
      </CredenzaContent>
    </Credenza>
  )
}
