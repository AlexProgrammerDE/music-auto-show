import { createContext, useContext, type ComponentProps, type ReactNode } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer"
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
    <Drawer open={open} onOpenChange={onOpenChange} showSwipeHandle>
      {content}
    </Drawer>
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
  return mobile ? (
    <DrawerContent className={cn("max-h-[calc(100dvh-2rem)]", className)}>{children}</DrawerContent>
  ) : (
    <DialogContent className={className}>{children}</DialogContent>
  )
}

export function CredenzaHeader({ className, ...props }: ComponentProps<"div">) {
  const { mobile } = useCredenza()
  return mobile ? (
    <DrawerHeader className={className} {...props} />
  ) : (
    <DialogHeader className={className} {...props} />
  )
}

export function CredenzaTitle({ className, ...props }: ComponentProps<typeof DialogTitle>) {
  const { mobile } = useCredenza()
  return mobile ? (
    <DrawerTitle className={className} {...props} />
  ) : (
    <DialogTitle className={className} {...props} />
  )
}

export function CredenzaDescription({
  className,
  ...props
}: ComponentProps<typeof DialogDescription>) {
  const { mobile } = useCredenza()
  return mobile ? (
    <DrawerDescription className={className} {...props} />
  ) : (
    <DialogDescription className={className} {...props} />
  )
}

export function CredenzaBody({ className, ...props }: ComponentProps<"div">) {
  const { mobile } = useCredenza()
  return (
    <div
      className={cn(
        "min-h-0",
        mobile && "flex-1 overflow-y-auto overscroll-contain px-4 py-1",
        className,
      )}
      {...props}
    />
  )
}

export function CredenzaFooter({ className, ...props }: ComponentProps<"div">) {
  const { mobile } = useCredenza()
  return mobile ? (
    <DrawerFooter className={cn("mt-4 border-t pt-4", className)} {...props} />
  ) : (
    <DialogFooter className={className} {...props} />
  )
}

export function CredenzaClose({
  className,
  variant = "outline",
  size = "default",
  ...props
}: ComponentProps<typeof Button>) {
  const { mobile } = useCredenza()
  const button = <Button className={className} variant={variant} size={size} />

  return mobile ? (
    <DrawerClose render={button} {...props} />
  ) : (
    <DialogClose render={button} {...props} />
  )
}
