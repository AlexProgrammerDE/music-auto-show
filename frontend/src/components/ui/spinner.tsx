import { cn } from "@/lib/utils"
import { SpinnerIcon } from "@phosphor-icons/react"

function Spinner({ className, color, ...props }: React.ComponentProps<"svg">) {
  return (
    <SpinnerIcon data-slot="spinner" role="status" aria-label="Loading" className={cn("size-4 animate-spin", className)} {...props} {...(color === undefined ? {} : { color })} />
  )
}

export { Spinner }
