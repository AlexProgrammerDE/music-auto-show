import { Skeleton } from "@/components/ui/skeleton"
import { generateN } from "@/lib/format"

const placeholderPanels = generateN(3)
const placeholderRows = generateN(4)

export function PageSkeleton() {
  return (
    <div className="grid gap-5" aria-label="Loading page" aria-live="polite">
      <div className="grid gap-2">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-4 w-full max-w-md" />
      </div>
      <div className="grid gap-5 lg:grid-cols-3">
        {placeholderPanels.map((panel) => (
          <Skeleton key={panel} className="h-24 rounded-none" />
        ))}
      </div>
      <div className="border bg-card p-4">
        <div className="grid gap-4">
          {placeholderRows.map((row) => (
            <div key={row} className="flex items-center gap-4">
              <Skeleton className="size-8 shrink-0" />
              <Skeleton className="h-4 flex-1" />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
