import type { ReactNode } from "react"

import {
  Credenza,
  CredenzaClose,
  CredenzaContent,
  CredenzaDescription,
  CredenzaFooter,
  CredenzaHeader,
  CredenzaTitle,
} from "@/components/credenza"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { useMobile } from "@/hooks/use-mobile"

type ConfirmCredenzaProps = {
  readonly open: boolean
  readonly title: string
  readonly description: string
  readonly confirmLabel: string
  readonly icon?: ReactNode
  readonly pending?: boolean
  readonly destructive?: boolean
  readonly onOpenChange: (open: boolean) => void
  readonly onConfirm: () => void
}

export function ConfirmCredenza({
  open,
  title,
  description,
  confirmLabel,
  icon,
  pending = false,
  destructive = false,
  onOpenChange,
  onConfirm,
}: ConfirmCredenzaProps) {
  const mobile = useMobile()
  const actionContent = pending ? (
    <>
      <Spinner data-icon="inline-start" /> Working…
    </>
  ) : (
    confirmLabel
  )

  if (!mobile) {
    return (
      <AlertDialog open={open} onOpenChange={onOpenChange}>
        <AlertDialogContent>
          <AlertDialogHeader>
            {icon ? <AlertDialogMedia>{icon}</AlertDialogMedia> : null}
            <AlertDialogTitle>{title}</AlertDialogTitle>
            <AlertDialogDescription>{description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant={destructive ? "destructive" : "default"}
              disabled={pending}
              onClick={onConfirm}
            >
              {actionContent}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    )
  }

  return (
    <Credenza open={open} onOpenChange={onOpenChange}>
      <CredenzaContent className="flex max-h-[calc(100dvh-2rem)] flex-col overflow-hidden">
        <CredenzaHeader className="min-h-0 overflow-y-auto overscroll-contain">
          <CredenzaTitle>{title}</CredenzaTitle>
          <CredenzaDescription>{description}</CredenzaDescription>
        </CredenzaHeader>
        <CredenzaFooter className="shrink-0">
          <CredenzaClose disabled={pending}>Cancel</CredenzaClose>
          <Button
            variant={destructive ? "destructive" : "default"}
            disabled={pending}
            onClick={onConfirm}
          >
            {actionContent}
          </Button>
        </CredenzaFooter>
      </CredenzaContent>
    </Credenza>
  )
}
