import { clone, create } from "@bufbuild/protobuf"
import {
  FileCodeIcon,
  LightbulbFilamentIcon,
  PencilSimpleIcon,
  PlusIcon,
  TrashIcon,
  UploadSimpleIcon,
  WarningIcon,
} from "@phosphor-icons/react"
import { useMutation, useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table"
import { Effect } from "effect"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { ConfirmCredenza } from "@/components/confirm-credenza"
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
import { FixtureEditor } from "@/components/fixture-editor"
import { PageSkeleton } from "@/components/page-skeleton"
import { SectionPanel } from "@/components/section-panel"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import { Spinner } from "@/components/ui/spinner"
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  FixtureConfigSchema,
  ShowConfigSchema,
  type FixtureConfig,
  type FixtureState,
  type GrandMa2FixtureType,
} from "@/gen/music_auto_show/v1/music_auto_show_pb"
import {
  configQueryOptions,
  grandMa2FixtureTypesQueryOptions,
  showQueryKeys,
  snapshotQueryOptions,
} from "@/lib/queries"
import { deriveDmxPresentation } from "@/lib/runtime-status"
import { ShowApi, runShowApi } from "@/lib/show-api"

type FixtureRow = {
  readonly id: string
  readonly name: string
  readonly fixtureType: string
  readonly mode: string
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
  columnHelper.accessor("fixtureType", { header: "grandMA2 type" }),
  columnHelper.accessor("mode", {
    header: "Mode",
    cell: (context) => <Badge variant="outline">{context.getValue()}</Badge>,
  }),
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
    header: () => <span className="sr-only">Actions</span>,
    cell: (context) => (
      <div className="flex justify-end gap-1">
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Edit ${context.row.original.name}`}
                onClick={context.row.original.edit}
              />
            }
          >
            <PencilSimpleIcon aria-hidden="true" />
          </TooltipTrigger>
          <TooltipContent>Edit fixture</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Remove ${context.row.original.name}`}
                onClick={context.row.original.remove}
              />
            }
          >
            <TrashIcon aria-hidden="true" />
          </TooltipTrigger>
          <TooltipContent>Remove fixture</TooltipContent>
        </Tooltip>
      </div>
    ),
  }),
])

export const Route = createFileRoute("/fixtures")({
  loader: async ({ context }) => {
    await Promise.all([
      context.queryClient.ensureQueryData(configQueryOptions),
      context.queryClient.ensureQueryData(snapshotQueryOptions),
      context.queryClient.ensureQueryData(grandMa2FixtureTypesQueryOptions),
    ])
  },
  pendingComponent: PageSkeleton,
  component: FixturesPage,
})

function FixturesPage() {
  const { data: config } = useSuspenseQuery(configQueryOptions)
  const { data: snapshot } = useSuspenseQuery(snapshotQueryOptions)
  const { data: fixtureTypes } = useSuspenseQuery(grandMa2FixtureTypesQueryOptions)
  const dmx = deriveDmxPresentation(snapshot.dmxRuntime)
  const queryClient = Route.useRouteContext({ select: (context) => context.queryClient })
  const [newFixture, setNewFixture] = useState<FixtureConfig>()
  const [editingFixtureId, setEditingFixtureId] = useState<string>()
  const [removingFixtureId, setRemovingFixtureId] = useState<string>()
  const [importOpen, setImportOpen] = useState(false)
  const [importFile, setImportFile] = useState<File>()

  const typeById = useMemo(
    () => new Map(fixtureTypes.map((fixtureType) => [fixtureType.id, fixtureType])),
    [fixtureTypes],
  )

  const updateMutation = useMutation({
    mutationFn: (nextConfig: typeof config) =>
      runShowApi(Effect.flatMap(ShowApi, (api) => api.updateConfig(nextConfig))),
    onSuccess: (saved) => {
      queryClient.setQueryData(showQueryKeys.config, saved)
      void queryClient.invalidateQueries({ queryKey: showQueryKeys.snapshot })
      setNewFixture(undefined)
      setRemovingFixtureId(undefined)
      toast.success("Fixture patch saved")
    },
    onError: (error) => toast.error(error.message),
  })

  const importMutation = useMutation({
    mutationFn: async (file: File) => {
      const xml = new Uint8Array(await file.arrayBuffer())
      return runShowApi(Effect.flatMap(ShowApi, (api) => api.importGrandMa2Fixture(file.name, xml)))
    },
    onSuccess: (result) => {
      queryClient.setQueryData(showQueryKeys.config, result.config)
      void queryClient.invalidateQueries({ queryKey: showQueryKeys.grandMa2FixtureTypes })
      setImportOpen(false)
      setImportFile(undefined)
      const names = result.fixtureTypes.map((fixtureType) => fixtureType.name).join(", ")
      toast.success(names ? `Imported ${names}` : "grandMA2 fixture imported")
    },
    onError: (error) => toast.error(error.message),
  })

  const rows = useMemo<FixtureRow[]>(() => {
    const states = new Map(snapshot.fixtureStates.map((state) => [state.fixtureId, state]))
    return config.fixtures.map((fixture) => {
      const fixtureType = typeById.get(fixture.fixtureTypeId)
      const footprint = fixtureType?.channelCount ?? 0
      return {
        id: fixture.id,
        name: fixture.name,
        fixtureType: fixtureType
          ? `${fixtureType.manufacturer} ${fixtureType.name}`
          : "Missing fixture type",
        mode: fixtureType?.mode ?? "Unknown",
        address:
          footprint > 0
            ? `${fixture.startChannel}–${fixture.startChannel + footprint - 1}`
            : `${fixture.startChannel}`,
        intensity: fixture.intensityScale,
        state: states.get(fixture.id),
        edit: () => setEditingFixtureId(fixture.id),
        remove: () => setRemovingFixtureId(fixture.id),
      }
    })
  }, [config.fixtures, snapshot.fixtureStates, typeById])

  const table = useTable({ features, columns, data: rows })
  const removingFixture = config.fixtures.find((fixture) => fixture.id === removingFixtureId)
  const editingFixture = config.fixtures.find((fixture) => fixture.id === editingFixtureId)
  const warningCount = fixtureTypes.reduce(
    (total, fixtureType) => total + fixtureType.warnings.length,
    0,
  )

  const createFixture = () => {
    const nextStartChannel = Math.min(
      512,
      Math.max(
        1,
        ...config.fixtures.map((fixture) => {
          const fixtureType = typeById.get(fixture.fixtureTypeId)
          return fixture.startChannel + Math.max(1, fixtureType?.channelCount ?? 1)
        }),
      ),
    )
    setNewFixture(
      create(FixtureConfigSchema, {
        id: crypto.randomUUID(),
        name: `New fixture ${config.fixtures.length + 1}`,
        fixtureTypeId: fixtureTypes[0]?.id ?? "",
        startChannel: nextStartChannel,
        position: config.fixtures.length,
        intensityScale: 1,
        movementPanMin: 0,
        movementPanMax: 1,
        movementTiltMin: 0,
        movementTiltMax: 1,
      }),
    )
  }

  return (
    <div className="grid gap-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-heading text-2xl font-semibold tracking-tight">Fixtures</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Patch grandMA2 fixture types. Channel functions and 3D metadata come directly from each
            XML file.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => setImportOpen(true)}>
            <UploadSimpleIcon data-icon="inline-start" aria-hidden="true" />
            Import grandMA2 XML
          </Button>
          <Button disabled={fixtureTypes.length === 0} onClick={createFixture}>
            <PlusIcon data-icon="inline-start" aria-hidden="true" />
            Add fixture
          </Button>
        </div>
      </div>

      <section className="grid border bg-card sm:grid-cols-4">
        <Metric label="Patched fixtures" value={`${config.fixtures.length}`} />
        <Metric label="Fixture types" value={`${fixtureTypes.length}`} />
        <Metric label="Imported files" value={`${config.importedFixtureFiles.length}`} />
        <div className="p-4">
          <p className="text-xs text-muted-foreground">Output status</p>
          <Badge
            className="mt-1"
            variant={dmx.failed ? "destructive" : dmx.active ? "secondary" : "outline"}
          >
            {dmx.label}
          </Badge>
        </div>
      </section>

      <SectionPanel
        title="Fixture patch"
        description="DMX ranges come from each parsed grandMA2 mode"
      >
        <Table>
          <TableCaption className="sr-only">
            Patched grandMA2 fixtures, addresses, intensity scales, live output, and actions.
          </TableCaption>
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
                <TableCell colSpan={columns.length} className="p-0">
                  <Empty className="min-h-40 rounded-none">
                    <EmptyHeader>
                      <EmptyMedia variant="icon">
                        <LightbulbFilamentIcon aria-hidden="true" />
                      </EmptyMedia>
                      <EmptyTitle>No fixtures patched</EmptyTitle>
                      <EmptyDescription>
                        Add a fixture type to start using the DMX universe.
                      </EmptyDescription>
                    </EmptyHeader>
                  </Empty>
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
      </SectionPanel>

      <SectionPanel
        title="grandMA2 library"
        description="Bundled and imported XML fixture types available to this show"
        action={
          warningCount > 0 ? (
            <Badge variant="outline">
              <WarningIcon data-icon="inline-start" aria-hidden="true" />
              {warningCount} {warningCount === 1 ? "warning" : "warnings"}
            </Badge>
          ) : undefined
        }
      >
        <div className="divide-y">
          {fixtureTypes.map((fixtureType) => (
            <FixtureTypeRow key={fixtureType.id} fixtureType={fixtureType} />
          ))}
        </div>
      </SectionPanel>

      <Credenza open={importOpen} onOpenChange={setImportOpen}>
        <CredenzaContent className="sm:max-w-lg">
          <CredenzaHeader>
            <CredenzaTitle>Import grandMA2 fixture</CredenzaTitle>
            <CredenzaDescription>
              Select an unencrypted grandMA2 XML fixture file. The server validates its channels,
              physical ranges, body dimensions, emitters, and embedded model before saving it.
            </CredenzaDescription>
          </CredenzaHeader>
          <CredenzaBody>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="grandma2-file">Fixture XML</FieldLabel>
                <Input
                  id="grandma2-file"
                  type="file"
                  accept=".xml,application/xml,text/xml"
                  onChange={(event) => setImportFile(event.target.files?.[0])}
                />
                <FieldDescription>
                  Maximum file size is 2 MB. grandMA2 XMLP files are encrypted and are not
                  supported.
                </FieldDescription>
              </Field>
            </FieldGroup>
          </CredenzaBody>
          <CredenzaFooter>
            <CredenzaClose type="button">Cancel</CredenzaClose>
            <Button
              disabled={!importFile || importMutation.isPending}
              onClick={() => {
                if (importFile) importMutation.mutate(importFile)
              }}
            >
              {importMutation.isPending ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <UploadSimpleIcon data-icon="inline-start" aria-hidden="true" />
              )}
              {importMutation.isPending ? "Importing…" : "Import fixture"}
            </Button>
          </CredenzaFooter>
        </CredenzaContent>
      </Credenza>

      <ConfirmCredenza
        open={removingFixture !== undefined}
        title={`Remove ${removingFixture?.name ?? "fixture"}?`}
        description="This removes the fixture instance from the active patch. Its grandMA2 fixture type stays in the library."
        confirmLabel="Remove fixture"
        icon={<TrashIcon aria-hidden="true" />}
        destructive
        pending={updateMutation.isPending}
        onOpenChange={(open) => {
          if (!open) setRemovingFixtureId(undefined)
        }}
        onConfirm={() => {
          if (!removingFixture) return
          const next = clone(ShowConfigSchema, config)
          next.fixtures = next.fixtures.filter((candidate) => candidate.id !== removingFixture.id)
          updateMutation.mutate(next)
        }}
      />

      {newFixture ? (
        <FixtureEditor
          key={newFixture.id}
          fixture={newFixture}
          fixtureTypes={fixtureTypes}
          existingNames={config.fixtures.map((fixture) => fixture.name)}
          title="Add fixture"
          description="Choose a parsed grandMA2 mode and patch it into the DMX universe."
          submitLabel="Add fixture"
          open
          pending={updateMutation.isPending}
          onOpenChange={(open) => {
            if (!open) setNewFixture(undefined)
          }}
          onSave={async (fixture) => {
            const next = clone(ShowConfigSchema, config)
            next.fixtures.push(fixture)
            await updateMutation.mutateAsync(next)
          }}
        />
      ) : null}

      {editingFixture ? (
        <FixtureEditor
          key={editingFixture.id}
          fixture={editingFixture}
          fixtureTypes={fixtureTypes}
          existingNames={config.fixtures
            .filter((fixture) => fixture.id !== editingFixture.id)
            .map((fixture) => fixture.name)}
          title={`Edit ${editingFixture.name}`}
          description="Change the fixture type, patch address, stage order, or output envelope."
          submitLabel="Save fixture"
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

function Metric({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <div className="border-b p-4 last:border-b-0 sm:border-r sm:border-b-0 sm:last:border-r-0">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 font-heading text-lg font-semibold tabular-nums">{value}</p>
    </div>
  )
}

function FixtureTypeRow({ fixtureType }: { readonly fixtureType: GrandMa2FixtureType }) {
  return (
    <div className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
      <div className="flex min-w-0 items-start gap-3">
        <FileCodeIcon className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-heading text-sm font-semibold">
              {fixtureType.manufacturer} {fixtureType.name}
            </p>
            <Badge variant={fixtureType.builtIn ? "secondary" : "outline"}>
              {fixtureType.builtIn ? "Bundled" : "Imported"}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {fixtureType.mode} · {fixtureType.channelCount} channels ·{" "}
            {fixtureType.visual?.emitters.length ?? 0} emitters
          </p>
          {fixtureType.warnings.length > 0 ? (
            <p className="mt-1 text-xs text-muted-foreground">{fixtureType.warnings.join(" ")}</p>
          ) : null}
        </div>
      </div>
      <code className="truncate text-xs text-muted-foreground">{fixtureType.id}</code>
    </div>
  )
}
