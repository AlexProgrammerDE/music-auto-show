import { clone, create } from "@bufbuild/protobuf"
import { PencilSimpleIcon, PlusIcon, TrashIcon } from "@phosphor-icons/react"
import { useForm } from "@tanstack/react-form"
import { useMutation, useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table"
import { Effect } from "effect"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import {
  Credenza,
  CredenzaContent,
  CredenzaDescription,
  CredenzaFooter,
  CredenzaHeader,
  CredenzaTitle,
} from "@/components/credenza"
import { FixtureEditor } from "@/components/fixture-editor"
import { SectionPanel } from "@/components/section-panel"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  ChannelConfigSchema,
  FixtureConfigSchema,
  ShowConfigSchema,
  type FixtureState,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import {
  configQueryOptions,
  fixtureProfilesQueryOptions,
  showQueryKeys,
  snapshotQueryOptions,
} from "@/lib/queries"
import { ShowApi, runShowApi } from "@/lib/show-api"

type FixtureRow = {
  readonly id: string
  readonly name: string
  readonly profile: string
  readonly address: string
  readonly intensity: number
  readonly state: FixtureState | undefined
  readonly edit: () => void
  readonly remove: () => void
}

const features = tableFeatures({})
const columnHelper = createColumnHelper<typeof features, FixtureRow>()
const columns = columnHelper.columns([
  columnHelper.accessor("name", {
    header: "Fixture",
    cell: (context) => <span className="font-heading font-semibold">{context.getValue()}</span>,
  }),
  columnHelper.accessor("profile", { header: "Profile" }),
  columnHelper.accessor("address", {
    header: "DMX address",
    cell: (context) => <span className="tabular-nums">{context.getValue()}</span>,
  }),
  columnHelper.accessor("intensity", {
    header: "Intensity scale",
    cell: (context) => (
      <div className="flex min-w-28 items-center gap-2">
        <Progress value={context.getValue() * 100} />
        <span className="w-9 text-right text-xs tabular-nums">
          {Math.round(context.getValue() * 100)}%
        </span>
      </div>
    ),
  }),
  columnHelper.accessor((row) => row.state?.dimmer ?? 0, {
    id: "output",
    header: "Live output",
    cell: (context) => (
      <div className="flex min-w-28 items-center gap-2">
        <Progress
          value={(context.getValue() / 255) * 100}
          className="[&_[data-slot=progress-indicator]]:bg-chart-2"
        />
        <span className="w-7 text-right text-xs tabular-nums">{context.getValue()}</span>
      </div>
    ),
  }),
  columnHelper.display({
    id: "actions",
    header: "",
    cell: (context) => (
      <div className="flex justify-end gap-1">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`Edit ${context.row.original.name}`}
          onClick={context.row.original.edit}
        >
          <PencilSimpleIcon />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`Remove ${context.row.original.name}`}
          onClick={context.row.original.remove}
        >
          <TrashIcon />
        </Button>
      </div>
    ),
  }),
])

export const Route = createFileRoute("/fixtures")({
  loader: async ({ context }) => {
    await Promise.all([
      context.queryClient.ensureQueryData(configQueryOptions),
      context.queryClient.ensureQueryData(snapshotQueryOptions),
      context.queryClient.ensureQueryData(fixtureProfilesQueryOptions),
    ])
  },
  component: FixturesPage,
})

function FixturesPage() {
  const { data: config } = useSuspenseQuery(configQueryOptions)
  const { data: snapshot } = useSuspenseQuery(snapshotQueryOptions)
  const { data: profiles } = useSuspenseQuery(fixtureProfilesQueryOptions)
  const queryClient = Route.useRouteContext({ select: (context) => context.queryClient })
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingFixtureId, setEditingFixtureId] = useState<string>()

  const updateMutation = useMutation({
    mutationFn: (nextConfig: typeof config) =>
      runShowApi(Effect.flatMap(ShowApi, (api) => api.updateConfig(nextConfig))),
    onSuccess: (saved) => {
      queryClient.setQueryData(showQueryKeys.config, saved)
      void queryClient.invalidateQueries({ queryKey: showQueryKeys.snapshot })
      setDialogOpen(false)
      toast.success("Fixture configuration saved")
    },
    onError: (error) => toast.error(error.message),
  })

  const rows = useMemo<FixtureRow[]>(() => {
    const states = new Map(snapshot.fixtureStates.map((state) => [state.fixtureId, state]))
    return config.fixtures.map((fixture) => ({
      id: fixture.id,
      name: fixture.name,
      profile: fixture.profileName,
      address: `${fixture.startChannel}–${fixture.startChannel + Math.max(0, fixture.channels.length - 1)}`,
      intensity: fixture.intensityScale,
      state: states.get(fixture.id),
      edit: () => setEditingFixtureId(fixture.id),
      remove: () => {
        const next = clone(ShowConfigSchema, config)
        next.fixtures = next.fixtures.filter((candidate) => candidate.id !== fixture.id)
        updateMutation.mutate(next)
      },
    }))
  }, [config, snapshot.fixtureStates, updateMutation])

  const table = useTable({ features, columns, data: rows })

  const nextStartChannel = Math.min(
    512,
    Math.max(
      1,
      ...config.fixtures.map((fixture) => {
        const profile = profiles.find((candidate) => candidate.name === fixture.profileName)
        const channels = fixture.channels.length > 0 ? fixture.channels : (profile?.channels ?? [])
        const highestOffset = Math.max(1, ...channels.map((channel) => channel.offset))
        return fixture.startChannel + highestOffset
      }),
    ),
  )

  const form = useForm({
    defaultValues: {
      name: `New Fixture ${config.fixtures.length + 1}`,
      profileName: profiles[0]?.name ?? "",
      startChannel: nextStartChannel,
      position: config.fixtures.length,
      intensityScale: 1,
      panMin: 0,
      panMax: 255,
      tiltMin: 0,
      tiltMax: 255,
    },
    onSubmit: async ({ value }) => {
      const profile = profiles.find((candidate) => candidate.name === value.profileName)
      if (
        config.fixtures.some(
          (fixture) => fixture.name.toLocaleLowerCase() === value.name.trim().toLocaleLowerCase(),
        )
      ) {
        throw new Error("Fixture names must be unique")
      }
      const next = clone(ShowConfigSchema, config)
      next.fixtures.push(
        create(FixtureConfigSchema, {
          id: crypto.randomUUID(),
          name: value.name.trim(),
          profileName: profile?.name ?? "",
          startChannel: value.startChannel,
          position: value.position,
          intensityScale: value.intensityScale,
          panMin: value.panMin,
          panMax: value.panMax,
          tiltMin: value.tiltMin,
          tiltMax: value.tiltMax,
          channels: profile?.channels.map((channel) => clone(ChannelConfigSchema, channel)) ?? [],
        }),
      )
      await updateMutation.mutateAsync(next)
      form.reset()
    },
  })

  return (
    <div className="grid gap-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-heading text-xl font-semibold tracking-tight">Fixtures</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Patch lights to DMX addresses and inspect their live output.
          </p>
        </div>
        <>
          <Button onClick={() => setDialogOpen(true)}>
            <PlusIcon /> Add fixture
          </Button>
          <Credenza open={dialogOpen} onOpenChange={setDialogOpen}>
            <CredenzaContent>
              <CredenzaHeader>
                <CredenzaTitle>Add fixture</CredenzaTitle>
                <CredenzaDescription>
                  Choose a fixture profile and its first channel in the universe.
                </CredenzaDescription>
              </CredenzaHeader>
              <form
                className="grid gap-4"
                onSubmit={(event) => {
                  event.preventDefault()
                  event.stopPropagation()
                  void form.handleSubmit()
                }}
              >
                <form.Field
                  name="name"
                  validators={{
                    onChange: ({ value }) => (value.trim() ? undefined : "Name is required"),
                  }}
                >
                  {(field) => (
                    <Field>
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
                <form.Field name="profileName">
                  {(field) => (
                    <Field>
                      <FieldLabel>Profile</FieldLabel>
                      <Select
                        value={field.state.value || "__custom"}
                        onValueChange={(value) =>
                          field.handleChange(value === "__custom" ? "" : (value ?? ""))
                        }
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
                    </Field>
                  )}
                </form.Field>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
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
                        <FieldLabel htmlFor={field.name}>Position</FieldLabel>
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
                  <form.Field name="intensityScale">
                    {(field) => (
                      <Field>
                        <FieldLabel htmlFor={field.name}>Intensity scale</FieldLabel>
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
                </div>
                <fieldset className="grid grid-cols-2 gap-3 border p-3 sm:grid-cols-4">
                  <legend className="px-2 text-xs font-medium text-muted-foreground">
                    Movement limits
                  </legend>
                  {(["panMin", "panMax", "tiltMin", "tiltMax"] as const).map((name) => (
                    <form.Field key={name} name={name}>
                      {(field) => (
                        <Field>
                          <FieldLabel htmlFor={`add-${field.name}`}>
                            {name.replace(/([A-Z])/g, " $1")}
                          </FieldLabel>
                          <Input
                            id={`add-${field.name}`}
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
                </fieldset>
                <CredenzaFooter>
                  <form.Subscribe
                    selector={(state) => [state.canSubmit, state.isSubmitting] as const}
                  >
                    {([canSubmit, isSubmitting]) => (
                      <Button
                        type="submit"
                        disabled={!canSubmit || isSubmitting || updateMutation.isPending}
                      >
                        Add fixture
                      </Button>
                    )}
                  </form.Subscribe>
                </CredenzaFooter>
              </form>
            </CredenzaContent>
          </Credenza>
        </>
      </div>

      <section className="grid border bg-card sm:grid-cols-3">
        <div className="border-r p-4">
          <p className="text-xs text-muted-foreground">Fixtures</p>
          <p className="mt-1 font-heading text-lg font-semibold tabular-nums">
            {config.fixtures.length}
          </p>
        </div>
        <div className="border-r p-4">
          <p className="text-xs text-muted-foreground">Profiles available</p>
          <p className="mt-1 font-heading text-lg font-semibold tabular-nums">{profiles.length}</p>
        </div>
        <div className="p-4">
          <p className="text-xs text-muted-foreground">Output status</p>
          <Badge className="mt-1" variant={snapshot.dmxRuntime?.running ? "secondary" : "outline"}>
            {snapshot.dmxRuntime?.running ? "Active" : "Stopped"}
          </Badge>
        </div>
      </section>

      <SectionPanel
        title="Fixture patch"
        description="Configured output order and current intensity"
      >
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              {table.getHeaderGroups().map((group) => (
                <TableRow key={group.id}>
                  {group.headers.map((header) => (
                    <TableHead key={header.id}>
                      {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                    </TableHead>
                  ))}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {table.getRowModel().rows.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={columns.length}
                    className="h-28 text-center text-muted-foreground"
                  >
                    No fixtures configured. Add one to begin patching the universe.
                  </TableCell>
                </TableRow>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <TableRow key={row.original.id}>
                    {row.getAllCells().map((cell) => (
                      <TableCell key={cell.id}>
                        <table.FlexRender cell={cell} />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </SectionPanel>

      {editingFixtureId ? (
        <FixtureEditor
          key={editingFixtureId}
          fixture={config.fixtures.find((fixture) => fixture.id === editingFixtureId)!}
          profiles={profiles}
          existingNames={config.fixtures
            .filter((fixture) => fixture.id !== editingFixtureId)
            .map((fixture) => fixture.name)}
          open
          pending={updateMutation.isPending}
          onOpenChange={(open) => {
            if (!open) setEditingFixtureId(undefined)
          }}
          onSave={async (fixture) => {
            const next = clone(ShowConfigSchema, config)
            next.fixtures = next.fixtures.map((candidate) =>
              candidate.id === fixture.id ? fixture : candidate,
            )
            await updateMutation.mutateAsync(next)
            setEditingFixtureId(undefined)
          }}
        />
      ) : null}
    </div>
  )
}
