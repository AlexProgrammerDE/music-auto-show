import { clone, create } from "@bufbuild/protobuf"
import { PlusIcon, TrashIcon } from "@phosphor-icons/react"
import { useForm } from "@tanstack/react-form"
import { useState } from "react"

import {
  Credenza,
  CredenzaContent,
  CredenzaDescription,
  CredenzaFooter,
  CredenzaHeader,
  CredenzaTitle,
} from "@/components/credenza"
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
      <CredenzaContent className="max-h-[90vh] overflow-y-auto sm:max-w-5xl">
        <CredenzaHeader>
          <CredenzaTitle>Edit {fixture.name}</CredenzaTitle>
          <CredenzaDescription>
            Patch, scale, and override the fixture channels used by the effects engine.
          </CredenzaDescription>
        </CredenzaHeader>
        <form
          className="grid gap-5"
          onSubmit={(event) => {
            event.preventDefault()
            event.stopPropagation()
            void form.handleSubmit()
          }}
        >
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
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
              {(field) => (
                <Field className="md:col-span-2">
                  <FieldLabel htmlFor={field.name}>Name</FieldLabel>
                  <Input
                    id={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(event) => field.handleChange(event.target.value)}
                  />
                </Field>
              )}
            </form.Field>
            <form.Field name="startChannel">
              {(field) => (
                <Field>
                  <FieldLabel htmlFor={field.name}>Start channel</FieldLabel>
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
            <form.Field name="position">
              {(field) => (
                <Field>
                  <FieldLabel htmlFor={field.name}>Show position</FieldLabel>
                  <Input
                    id={field.name}
                    type="number"
                    min={0}
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                  />
                </Field>
              )}
            </form.Field>
            <form.Field name="profileName">
              {(field) => (
                <Field className="md:col-span-2">
                  <FieldLabel>Profile</FieldLabel>
                  <Select
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
                    <SelectTrigger className="w-full">
                      <SelectValue>{field.state.value || "Custom"}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__custom">Custom</SelectItem>
                      {profiles.map((profile) => (
                        <SelectItem key={profile.name} value={profile.name}>
                          {profile.name}
                        </SelectItem>
                      ))}
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
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                  />
                </Field>
              )}
            </form.Field>
          </div>

          <fieldset className="grid gap-4 border p-4">
            <legend className="px-2 font-heading text-sm font-semibold">Movement limits</legend>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {(["panMin", "panMax", "tiltMin", "tiltMax"] as const).map((name) => (
                <form.Field key={name} name={name}>
                  {(field) => (
                    <Field>
                      <FieldLabel htmlFor={field.name}>
                        {name.replace(/([A-Z])/g, " $1")}
                      </FieldLabel>
                      <Input
                        id={field.name}
                        type="number"
                        min={0}
                        max={255}
                        value={field.state.value}
                        onChange={(event) => field.handleChange(event.target.valueAsNumber)}
                      />
                    </Field>
                  )}
                </form.Field>
              ))}
            </div>
          </fieldset>

          <section className="grid gap-3">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h3 className="font-heading text-sm font-semibold">Channel overrides</h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  Disabled channels are left at zero. Fixed channels ignore live effects.
                </p>
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => {
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
                }}
              >
                <PlusIcon /> Add channel
              </Button>
            </div>

            {channels.length === 0 ? (
              <div className="border border-dashed p-6 text-center text-sm text-muted-foreground">
                No channels are configured. Add a channel for a custom fixture.
              </div>
            ) : (
              <div className="grid gap-2">
                {channels.map((channel) => (
                  <div
                    key={`channel-${channel.offset}`}
                    className="grid gap-3 border p-3 lg:grid-cols-[3rem_minmax(8rem,1fr)_minmax(12rem,1.25fr)_5rem_5rem_6rem_auto_auto] lg:items-end"
                  >
                    <form.Subscribe selector={(state) => state.values.startChannel}>
                      {(startChannel) => (
                        <Field>
                          <FieldLabel>DMX</FieldLabel>
                          <Input value={startChannel + channel.offset - 1} disabled />
                        </Field>
                      )}
                    </form.Subscribe>
                    <Field>
                      <FieldLabel htmlFor={`channel-name-${channel.offset}`}>Name</FieldLabel>
                      <Input
                        id={`channel-name-${channel.offset}`}
                        value={channel.name}
                        onChange={(event) =>
                          updateChannel(channel.offset, (next) => {
                            next.name = event.target.value
                          })
                        }
                      />
                    </Field>
                    <Field>
                      <FieldLabel>Type</FieldLabel>
                      <Select
                        value={channel.channelType}
                        onValueChange={(value) =>
                          updateChannel(channel.offset, (next) => {
                            next.channelType = value ?? "nothing"
                          })
                        }
                      >
                        <SelectTrigger className="w-full">
                          <SelectValue>{formatEnumLabel(channel.channelType)}</SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {channelTypes.map((channelType) => (
                            <SelectItem key={channelType} value={channelType}>
                              {formatEnumLabel(channelType)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </Field>
                    <Field>
                      <FieldLabel htmlFor={`channel-min-${channel.offset}`}>Min</FieldLabel>
                      <Input
                        id={`channel-min-${channel.offset}`}
                        type="number"
                        min={0}
                        max={255}
                        value={channel.minValue}
                        onChange={(event) =>
                          updateChannel(channel.offset, (next) => {
                            next.minValue = clampDmx(event.target.valueAsNumber)
                          })
                        }
                      />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor={`channel-max-${channel.offset}`}>Max</FieldLabel>
                      <Input
                        id={`channel-max-${channel.offset}`}
                        type="number"
                        min={0}
                        max={255}
                        value={channel.maxValue}
                        onChange={(event) =>
                          updateChannel(channel.offset, (next) => {
                            next.maxValue = clampDmx(event.target.valueAsNumber)
                          })
                        }
                      />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor={`channel-fixed-value-${channel.offset}`}>
                        Fixed value
                      </FieldLabel>
                      <Input
                        id={`channel-fixed-value-${channel.offset}`}
                        type="number"
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
                    <Field orientation="horizontal" className="pb-2">
                      <FieldLabel htmlFor={`channel-fixed-${channel.offset}`}>Fixed</FieldLabel>
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
                    <div className="flex items-center justify-end gap-1 pb-1">
                      <Switch
                        aria-label={`Enable ${channel.name}`}
                        checked={channel.enabled}
                        onCheckedChange={(checked) =>
                          updateChannel(channel.offset, (next) => {
                            next.enabled = checked
                          })
                        }
                      />
                      <Button
                        type="button"
                        size="icon-sm"
                        variant="ghost"
                        aria-label={`Remove ${channel.name}`}
                        onClick={() =>
                          setChannels((current) =>
                            current.filter((candidate) => candidate.offset !== channel.offset),
                          )
                        }
                      >
                        <TrashIcon />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <CredenzaFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <form.Subscribe selector={(state) => [state.canSubmit, state.isSubmitting] as const}>
              {([canSubmit, isSubmitting]) => (
                <Button type="submit" disabled={!canSubmit || isSubmitting || pending}>
                  Save fixture
                </Button>
              )}
            </form.Subscribe>
          </CredenzaFooter>
        </form>
      </CredenzaContent>
    </Credenza>
  )
}
