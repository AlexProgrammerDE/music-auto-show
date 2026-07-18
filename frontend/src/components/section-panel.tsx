import type { ReactNode } from "react"

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
    <section className={cn("border bg-card", className)}>
      <header className="flex min-h-12 items-center justify-between gap-3 border-b px-4 py-2.5">
        <div className="min-w-0">
          <h2 className="font-heading text-sm font-semibold tracking-tight">{title}</h2>
          {description ? (
            <p className="mt-0.5 truncate text-xs text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {action}
      </header>
      {children}
    </section>
  )
}
