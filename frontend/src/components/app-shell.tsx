import {
  BroadcastIcon,
  FadersHorizontalIcon,
  GridFourIcon,
  LightbulbFilamentIcon,
  MoonIcon,
  SlidersHorizontalIcon,
  SunIcon,
} from "@phosphor-icons/react"
import { Link, useRouterState } from "@tanstack/react-router"
import type { ReactNode } from "react"

import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

const navigation = [
  { to: "/", label: "Live", icon: BroadcastIcon },
  { to: "/fixtures", label: "Fixtures", icon: LightbulbFilamentIcon },
  { to: "/dmx", label: "DMX", icon: GridFourIcon },
  { to: "/settings", label: "Settings", icon: SlidersHorizontalIcon },
] as const

export function AppShell({ children }: { readonly children: ReactNode }) {
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const { theme, setTheme } = useTheme()
  const dark = theme === "dark"

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur-sm">
        <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-4 px-4 lg:px-6">
          <Link to="/" className="flex shrink-0 items-center gap-2.5">
            <span className="flex size-8 items-center justify-center border bg-foreground text-background">
              <FadersHorizontalIcon className="size-4" weight="bold" />
            </span>
            <span className="hidden leading-none sm:block">
              <span className="block font-heading text-sm font-semibold tracking-tight">
                Music Auto Show
              </span>
              <span className="block pt-1 text-[10px] tracking-[0.16em] text-muted-foreground uppercase">
                Lighting control
              </span>
            </span>
          </Link>

          <Separator orientation="vertical" className="hidden h-6 sm:block" />

          <nav className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
            {navigation.map((item) => {
              const Icon = item.icon
              const active = item.to === "/" ? pathname === "/" : pathname.startsWith(item.to)
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className={cn(
                    "flex h-8 items-center gap-2 border border-transparent px-2.5 font-heading text-xs font-medium text-muted-foreground transition-colors hover:text-foreground",
                    active && "border-border bg-muted text-foreground",
                  )}
                >
                  <Icon className="size-4" weight={active ? "fill" : "regular"} />
                  <span className="hidden sm:inline">{item.label}</span>
                </Link>
              )
            })}
          </nav>

          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Switch to ${dark ? "light" : "dark"} mode`}
                  onClick={() => setTheme(dark ? "light" : "dark")}
                />
              }
            >
              {dark ? <SunIcon /> : <MoonIcon />}
            </TooltipTrigger>
            <TooltipContent>
              Toggle theme <kbd>D</kbd>
            </TooltipContent>
          </Tooltip>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1600px] px-4 py-5 lg:px-6 lg:py-7">{children}</main>
    </div>
  )
}
