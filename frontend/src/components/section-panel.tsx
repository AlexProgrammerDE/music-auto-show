import type { ReactNode } from "react"

import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { cn } from "@/lib/utils"

export function SectionPanel({
  title,
  description,
  action,
  children,
  className,
}: {
  readonly title: string
  readonly description?: string
  readonly action?: ReactNode
  readonly children: ReactNode
  readonly className?: string
}) {
  return (
    <section>
      <Card size="sm" className={cn("gap-0 rounded-none py-0", className)}>
        <CardHeader className="min-h-12 gap-0 border-b py-2.5">
          <CardTitle>
            <h2 className="text-sm font-semibold tracking-tight text-balance">{title}</h2>
          </CardTitle>
          {description ? (
            <CardDescription className="truncate text-xs">{description}</CardDescription>
          ) : null}
          {action ? <CardAction>{action}</CardAction> : null}
        </CardHeader>
        <CardContent className="p-0">{children}</CardContent>
      </Card>
    </section>
  )
}
