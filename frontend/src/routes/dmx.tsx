import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"

import { PageSkeleton } from "@/components/page-skeleton"
import { SectionPanel } from "@/components/section-panel"
import { Badge } from "@/components/ui/badge"
import { generateN } from "@/lib/format"
import { snapshotQueryOptions } from "@/lib/queries"
import { deriveDmxPresentation } from "@/lib/runtime-status"
import { cn } from "@/lib/utils"

const dmxChannels = generateN(512)

export const Route = createFileRoute("/dmx")({
  loader: ({ context }) => context.queryClient.ensureQueryData(snapshotQueryOptions),
  pendingComponent: PageSkeleton,
  component: DmxUniversePage,
})

function DmxUniversePage() {
  const { data: snapshot } = useSuspenseQuery(snapshotQueryOptions)
  const universe = snapshot.dmxUniverse
  const activeChannels = universe.reduce((count, value) => count + (value > 0 ? 1 : 0), 0)
  const dmx = deriveDmxPresentation(snapshot.dmxRuntime)

  return (
    <div className="grid gap-5">
      <div>
        <h1 className="font-heading text-xl font-semibold tracking-tight">DMX universe</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Live output for all 512 channels. Values update from the gRPC-Web stream.
        </p>
      </div>

      <section className="grid border bg-card sm:grid-cols-3">
        <div className="border-r p-4">
          <p className="text-xs text-muted-foreground">Interface</p>
          <p className="mt-1 font-heading text-sm font-semibold">
            {snapshot.dmxRuntime?.interfaceType || "Not connected"}
          </p>
        </div>
        <div className="border-r p-4">
          <p className="text-xs text-muted-foreground">Port</p>
          <p className="mt-1 truncate font-heading text-sm font-semibold">
            {snapshot.dmxRuntime?.port || "Automatic"}
          </p>
        </div>
        <div className="p-4">
          <p className="text-xs text-muted-foreground">Active channels</p>
          <p className="mt-1 font-heading text-sm font-semibold tabular-nums">
            {activeChannels} / 512
          </p>
        </div>
      </section>

      <SectionPanel
        title="Universe 1"
        description="Open DMX output, channels 1 through 512"
        action={
          <Badge variant={dmx.failed ? "destructive" : dmx.active ? "secondary" : "outline"}>
            {dmx.label}
          </Badge>
        }
      >
        <div className="grid grid-cols-8 gap-px bg-border p-px sm:grid-cols-16 lg:grid-cols-24 xl:grid-cols-32">
          {dmxChannels.map((channel) => {
            const value = universe[channel - 1] ?? 0
            return (
              <div
                key={channel}
                className={cn(
                  "group relative flex aspect-square min-w-0 flex-col justify-between bg-background p-1.5 transition-colors",
                  value > 0 && "bg-chart-2/10",
                  value > 127 && "bg-chart-2/20",
                  value > 223 && "bg-chart-2/35",
                )}
                title={`Channel ${channel}: ${value}`}
              >
                <span className="text-[8px] leading-none text-muted-foreground tabular-nums">
                  {channel}
                </span>
                <span className="self-end text-[10px] leading-none font-semibold tabular-nums">
                  {value}
                </span>
              </div>
            )
          })}
        </div>
      </SectionPanel>
    </div>
  )
}
