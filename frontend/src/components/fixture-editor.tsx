import { clone } from "@bufbuild/protobuf"
import { useForm } from "@tanstack/react-form"

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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Field,
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
import {
  FixtureConfigSchema,
  FixtureVisualKind,
  type FixtureConfig,
  type GrandMa2FixtureType,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"

type FixtureEditorProps = {
  readonly fixture: FixtureConfig
  readonly fixtureTypes: readonly GrandMa2FixtureType[]
  readonly existingNames: readonly string[]
  readonly title: string
  readonly description: string
  readonly submitLabel: string
  readonly open: boolean
  readonly pending: boolean
  readonly onOpenChange: (open: boolean) => void
  readonly onSave: (fixture: FixtureConfig) => Promise<void>
}

function fixtureTypeLabel(fixtureType: GrandMa2FixtureType) {
  const source = fixtureType.builtIn ? "bundled" : "imported"
  return `${fixtureType.manufacturer} ${fixtureType.name} · ${fixtureType.mode} · ${source}`
}

export function FixtureEditor({
  fixture,
  fixtureTypes,
  existingNames,
  title,
  description,
  submitLabel,
  open,
  pending,
  onOpenChange,
  onSave,
}: FixtureEditorProps) {
  const form = useForm({
    defaultValues: {
      name: fixture.name,
      fixtureTypeId: fixture.fixtureTypeId,
      startChannel: fixture.startChannel,
      position: fixture.position,
      intensityScale: fixture.intensityScale,
      movementPanMin: fixture.movementPanMin,
      movementPanMax: fixture.movementPanMax,
      movementTiltMin: fixture.movementTiltMin,
      movementTiltMax: fixture.movementTiltMax,
    },
    onSubmit: async ({ value }) => {
      const next = clone(FixtureConfigSchema, fixture)
      next.name = value.name.trim()
      next.fixtureTypeId = value.fixtureTypeId
      next.startChannel = value.startChannel
      next.position = value.position
      next.intensityScale = value.intensityScale
      next.movementPanMin = value.movementPanMin
      next.movementPanMax = value.movementPanMax
      next.movementTiltMin = value.movementTiltMin
      next.movementTiltMax = value.movementTiltMax
      await onSave(next)
    },
  })

  const typeItems = fixtureTypes.map((fixtureType) => ({
    label: fixtureTypeLabel(fixtureType),
    value: fixtureType.id,
  }))

  return (
    <Credenza open={open} onOpenChange={onOpenChange}>
      <CredenzaContent className="flex max-h-[calc(100dvh-2rem)] flex-col overflow-hidden sm:max-w-3xl">
        <CredenzaHeader className="shrink-0">
          <CredenzaTitle>{title}</CredenzaTitle>
          <CredenzaDescription>{description}</CredenzaDescription>
        </CredenzaHeader>
        <form
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
          onSubmit={(event) => {
            event.preventDefault()
            event.stopPropagation()
            void form.handleSubmit()
          }}
        >
          <CredenzaBody className="grid min-h-0 flex-1 gap-5 overflow-y-auto overscroll-contain">
            <FieldGroup className="grid gap-4 md:grid-cols-2">
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
                    <Field data-invalid={invalid}>
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

              <form.Field
                name="fixtureTypeId"
                validators={{
                  onChange: ({ value }) =>
                    fixtureTypes.some((fixtureType) => fixtureType.id === value)
                      ? undefined
                      : "Choose a grandMA2 fixture type",
                }}
              >
                {(field) => {
                  const selected = fixtureTypes.find(
                    (fixtureType) => fixtureType.id === field.state.value,
                  )
                  const invalid = field.state.meta.isTouched && !field.state.meta.isValid
                  return (
                    <Field data-invalid={invalid}>
                      <FieldLabel htmlFor={`${field.name}-trigger`}>
                        grandMA2 fixture type
                      </FieldLabel>
                      <Select
                        name={field.name}
                        items={typeItems}
                        value={field.state.value}
                        onValueChange={(value) => field.handleChange(value ?? "")}
                      >
                        <SelectTrigger
                          id={`${field.name}-trigger`}
                          className="w-full"
                          aria-invalid={invalid}
                        >
                          <SelectValue>
                            {selected ? fixtureTypeLabel(selected) : "Choose fixture type"}
                          </SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          <SelectGroup>
                            {fixtureTypes.map((fixtureType) => (
                              <SelectItem key={fixtureType.id} value={fixtureType.id}>
                                {fixtureTypeLabel(fixtureType)}
                              </SelectItem>
                            ))}
                          </SelectGroup>
                        </SelectContent>
                      </Select>
                      {selected ? (
                        <FieldDescription className="flex flex-wrap items-center gap-2">
                          <Badge variant="outline">{selected.channelCount} channels</Badge>
                          <span>
                            DMX functions and visualization metadata come from the grandMA2 file.
                          </span>
                        </FieldDescription>
                      ) : null}
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
                    <FieldLabel htmlFor={field.name}>Stage position</FieldLabel>
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
                    <FieldDescription>Applied after the global show intensity.</FieldDescription>
                  </Field>
                )}
              </form.Field>
            </FieldGroup>

            <form.Subscribe selector={(state) => state.values.fixtureTypeId}>
              {(fixtureTypeId) => {
                const fixtureType = fixtureTypes.find((candidate) => candidate.id === fixtureTypeId)
                if (fixtureType?.visual?.kind !== FixtureVisualKind.MOVING_HEAD) return null
                return (
                  <FieldSet className="border p-4">
                    <FieldLegend>Movement envelope</FieldLegend>
                    <FieldDescription>
                      Limit the portion of the file’s {fixtureType.visual.panMinDegrees}° to{" "}
                      {fixtureType.visual.panMaxDegrees}° pan and{" "}
                      {fixtureType.visual.tiltMinDegrees}° to {fixtureType.visual.tiltMaxDegrees}°
                      tilt ranges used by the show.
                    </FieldDescription>
                    <FieldGroup className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                      {(
                        [
                          ["movementPanMin", "Pan minimum"],
                          ["movementPanMax", "Pan maximum"],
                          ["movementTiltMin", "Tilt minimum"],
                          ["movementTiltMax", "Tilt maximum"],
                        ] as const
                      ).map(([name, label]) => (
                        <form.Field key={name} name={name}>
                          {(field) => (
                            <Field>
                              <FieldLabel htmlFor={field.name}>{label}</FieldLabel>
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
                      ))}
                    </FieldGroup>
                  </FieldSet>
                )
              }}
            </form.Subscribe>
          </CredenzaBody>

          <CredenzaFooter className="shrink-0">
            <CredenzaClose type="button">Cancel</CredenzaClose>
            <form.Subscribe selector={(state) => [state.canSubmit, state.isSubmitting] as const}>
              {([canSubmit, isSubmitting]) => {
                const saving = isSubmitting || pending
                return (
                  <Button type="submit" disabled={!canSubmit || saving}>
                    {saving ? <Spinner data-icon="inline-start" /> : null}
                    {saving ? "Saving…" : submitLabel}
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
