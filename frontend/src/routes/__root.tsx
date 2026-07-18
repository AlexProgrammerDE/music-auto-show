import { WarningCircleIcon } from "@phosphor-icons/react"
import type { QueryClient } from "@tanstack/react-query"
import { createRootRouteWithContext, Link, Outlet, useRouter } from "@tanstack/react-router"

import { AppShell } from "@/components/app-shell"
import { Button } from "@/components/ui/button"
import { useSnapshotStream } from "@/hooks/use-snapshot-stream"

type RouterContext = {
  readonly queryClient: QueryClient
}

function RootComponent() {
  useSnapshotStream()
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  )
}

function ErrorComponent({ error }: { readonly error: Error }) {
  const router = useRouter()
  return (
    <AppShell>
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="max-w-lg border bg-card p-6">
          <WarningCircleIcon className="mb-4 size-7 text-destructive" />
          <h1 className="font-heading text-lg font-semibold">The control surface failed</h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">{error.message}</p>
          <Button className="mt-5" onClick={() => void router.invalidate()}>
            Try again
          </Button>
        </div>
      </div>
    </AppShell>
  )
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: RootComponent,
  errorComponent: ErrorComponent,
  notFoundComponent: () => (
    <div className="py-20 text-center">
      <p className="font-heading text-lg font-semibold">Page not found</p>
      <Link to="/" className="mt-2 inline-block text-sm underline">
        Return to the live show
      </Link>
    </div>
  ),
})
