import { Drawer as DrawerPrimitive } from "@base-ui/react/drawer"
import { createContext, useContext, type ComponentProps, type ReactNode } from "react"

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useMobile } from "@/hooks/use-mobile"
import { cn } from "@/lib/utils"

type CredenzaContextValue = {
  readonly mobile: boolean
}

const CredenzaContext = createContext<CredenzaContextValue | undefined>(undefined)

function useCredenza() {
  const context = useContext(CredenzaContext)
  if (!context) throw new Error("Credenza components must be nested inside Credenza")
  return context
}

type CredenzaProps = {
  readonly children: ReactNode
  readonly open?: boolean
  readonly onOpenChange?: (open: boolean) => void
}

export function Credenza({ children, open, onOpenChange }: CredenzaProps) {
  const mobile = useMobile()
  const content = <CredenzaContext.Provider value={{ mobile }}>{children}</CredenzaContext.Provider>

  return mobile ? (
    <DrawerPrimitive.Root open={open} onOpenChange={onOpenChange} swipeDirection="down" modal>
      {content}
    </DrawerPrimitive.Root>
  ) : (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {content}
    </Dialog>
  )
}

export function CredenzaContent({
  className,
  children,
}: {
  readonly className?: string
  readonly children: ReactNode
}) {
  const { mobile } = useCredenza()
  if (!mobile) return <DialogContent className={className}>{children}</DialogContent>

  return (
    <DrawerPrimitive.Portal>
      <DrawerPrimitive.Backdrop className="fixed inset-0 z-50 bg-foreground/10 backdrop-blur-xs transition-opacity data-ending-style:opacity-0 data-starting-style:opacity-0" />
      <DrawerPrimitive.Viewport className="pointer-events-none fixed inset-0 z-50 select-none">
        <DrawerPrimitive.Popup
          className={cn(
            "pointer-events-auto fixed inset-x-0 bottom-0 flex max-h-[calc(100dvh-2rem)] min-h-0 flex-col rounded-t-xl border-t bg-popover text-popover-foreground shadow-lg transition-transform duration-300 outline-none data-ending-style:translate-y-full data-starting-style:translate-y-full",
            className,
          )}
        >
          <div className="flex h-5 shrink-0 items-center justify-center" aria-hidden="true">
            <span className="h-1 w-10 rounded-full bg-muted-foreground/30" />
          </div>
          <DrawerPrimitive.Content className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pt-1">
            {children}
          </DrawerPrimitive.Content>
        </DrawerPrimitive.Popup>
      </DrawerPrimitive.Viewport>
    </DrawerPrimitive.Portal>
  )
}

export function CredenzaHeader({ className, ...props }: ComponentProps<"div">) {
  const { mobile } = useCredenza()
  return mobile ? (
    <div className={cn("flex flex-col gap-2 text-center", className)} {...props} />
  ) : (
    <DialogHeader className={className} {...props} />
  )
}

export function CredenzaTitle({ className, ...props }: DrawerPrimitive.Title.Props) {
  const { mobile } = useCredenza()
  return mobile ? (
    <DrawerPrimitive.Title
      className={cn("font-heading text-base leading-none font-medium", className)}
      {...props}
    />
  ) : (
    <DialogTitle className={className} {...props} />
  )
}

export function CredenzaDescription({ className, ...props }: DrawerPrimitive.Description.Props) {
  const { mobile } = useCredenza()
  return mobile ? (
    <DrawerPrimitive.Description
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  ) : (
    <DialogDescription className={className} {...props} />
  )
}

export function CredenzaBody({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("min-h-0", className)} {...props} />
}

export function CredenzaFooter({ className, ...props }: ComponentProps<"div">) {
  const { mobile } = useCredenza()
  return mobile ? (
    <div
      className={cn("mt-4 flex shrink-0 flex-col-reverse gap-2 border-t pt-4", className)}
      {...props}
    />
  ) : (
    <DialogFooter className={className} {...props} />
  )
}
