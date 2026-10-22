import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"
import { cn } from "@/lib/utils"

/*
 * Buttons per the Stitch design system: sharp rectangles, 2px ink border, hard 4px offset shadow.
 * Hover lifts the button (transform only) and swaps to the cobalt lift shadow; press pushes it in.
 */
const buttonVariants = cva(
  [
    "group/button relative inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 whitespace-nowrap select-none",
    "transition-transform duration-200 ease-expo will-change-transform",
    "disabled:pointer-events-none disabled:opacity-45",
    "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  ],
  {
    variants: {
      variant: {
        primary:
          "border-2 border-ink bg-cobalt font-display tracking-[0.03em] text-paper uppercase shadow-hard hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-hard-lift active:translate-x-0.5 active:translate-y-0.5 active:shadow-none",
        secondary:
          "border-2 border-ink bg-paper font-display tracking-[0.03em] text-ink uppercase shadow-hard hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-hard-lift active:translate-x-0.5 active:translate-y-0.5 active:shadow-none",
        bracket:
          "border-2 border-cobalt bg-transparent font-mono font-bold tracking-[0.12em] text-cobalt uppercase hover:-translate-y-0.5 hover:bg-cobalt hover:text-paper active:translate-y-px",
        "bracket-light":
          "border-2 border-paper bg-transparent font-mono font-bold tracking-[0.12em] text-paper uppercase hover:-translate-y-0.5 hover:bg-paper hover:text-cobalt active:translate-y-px",
        quiet:
          "bg-surface-low font-mono text-ink uppercase hover:bg-surface active:translate-y-px",
        ghost: "font-mono text-ink-soft uppercase hover:text-cobalt-vivid active:translate-y-px",
        danger:
          "border-2 border-danger bg-paper font-mono font-bold text-danger uppercase hover:-translate-y-0.5 hover:bg-danger hover:text-paper active:translate-y-px",
      },
      size: {
        sm: "h-8 px-3 text-label",
        md: "h-11 px-5 text-[0.95rem]",
        lg: "h-14 px-7 text-title",
        icon: "size-9",
      },
    },
    compoundVariants: [
      { variant: ["bracket", "bracket-light", "quiet", "ghost", "danger"], size: "md", className: "text-label" },
      { variant: ["bracket", "bracket-light"], size: "lg", className: "text-[0.9rem]" },
    ],
    defaultVariants: { variant: "primary", size: "md" },
  },
)

function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "button"
  return <Comp data-slot="button" className={cn(buttonVariants({ variant, size, className }))} {...props} />
}

export { Button, buttonVariants }
