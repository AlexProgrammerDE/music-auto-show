import { HouseIcon, WarningCircleIcon } from "@phosphor-icons/react"
import type { QueryClient } from "@tanstack/react-query"
import { createRootRouteWithContext, Link, Outlet, useRouter } from "@tanstack/react-router"

import { AppShell } from "@/components/app-shell"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
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
        <Alert variant="destructive" className="max-w-lg p-4">
          <WarningCircleIcon aria-hidden="true" />
          <AlertTitle>
            <h1>The control surface failed</h1>
          </AlertTitle>
          <AlertDescription className="pr-24">{error.message}</AlertDescription>
          <AlertAction>
            <Button size="sm" variant="outline" onClick={() => void router.invalidate()}>
              Try again
            </Button>
          </AlertAction>
        </Alert>
      </div>
    </AppShell>
  )
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: RootComponent,
  errorComponent: ErrorComponent,
  notFoundComponent: () => (
    <AppShell>
      <Empty className="min-h-[50vh]">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <HouseIcon aria-hidden="true" />
          </EmptyMedia>
          <EmptyTitle>
            <h1>Page not found</h1>
          </EmptyTitle>
          <EmptyDescription>The requested control surface does not exist.</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button nativeButton={false} render={<Link to="/" />}>
            Return to Live Show
          </Button>
        </EmptyContent>
      </Empty>
    </AppShell>
  ),
})
