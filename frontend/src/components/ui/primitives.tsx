import type { ComponentProps, ReactNode } from "react"
import { cn } from "@/lib/utils"
import type { Rating } from "@/lib/api"

/** A white surface. `hard` is the design system's ink-bordered card with the hard offset shadow. */
export function Panel({ hard = false, className, ...props }: ComponentProps<"section"> & { hard?: boolean }) {
  return (
    <section
      className={cn(
        "bg-paper",
        hard ? "border-[1.5px] border-ink shadow-hard" : "ring-1 ring-schematic/70",
        className,
      )}
      {...props}
    />
  )
}

/** Square ink marker + Anton title: the section heading used on every app screen. */
export function PanelTitle({
  children,
  aside,
  as: Tag = "h2",
  className,
}: {
  children: ReactNode
  aside?: ReactNode
  as?: "h1" | "h2" | "h3"
  className?: string
}) {
  return (
    <div className={cn("flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1", className)}>
      <Tag className="flex items-center gap-3 font-display text-headline-md text-ink">
        <span aria-hidden className="size-3 shrink-0 bg-ink" />
        {children}
      </Tag>
      {aside && <div className="label-technical text-cobalt">{aside}</div>}
    </div>
  )
}

/** Pill selector: the one rounded shape in the system. */
export function Pill({ active = false, className, ...props }: ComponentProps<"button"> & { active?: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={cn(
        "label-technical h-9 cursor-pointer rounded-full px-4 transition-transform duration-200 ease-expo active:scale-95",
        active ? "bg-cobalt text-paper" : "text-ink ring-1 ring-schematic hover:ring-cobalt",
        className,
      )}
      {...props}
    />
  )
}

/** Small mono tag: SHAs, repo handles, file refs. */
export function Tag({ tone = "plain", className, ...props }: ComponentProps<"span"> & { tone?: "plain" | "cobalt" | "dark" | "high" | "medium" | "danger" }) {
  return (
    <span
      className={cn(
        "label-badge inline-flex items-center gap-1.5 px-2 py-1 normal-case",
        {
          plain: "bg-surface-low text-ink-soft",
          cobalt: "bg-cobalt-tint text-cobalt-deep",
          dark: "bg-ink text-paper",
          high: "bg-signal-high/12 text-signal-high-ink",
          medium: "bg-signal-medium/15 text-signal-medium-ink",
          danger: "bg-danger-tint text-danger",
        }[tone],
        className,
      )}
      {...props}
    />
  )
}

export function StatusDot({ tone = "high", live = false, className }: { tone?: "high" | "medium" | "low" | "cobalt"; live?: boolean; className?: string }) {
  const color = { high: "bg-signal-high", medium: "bg-signal-medium", low: "bg-signal-low", cobalt: "bg-cobalt-vivid" }[tone]
  return (
    <span aria-hidden className={cn("relative inline-flex size-2 shrink-0", className)}>
      {live && <span className={cn("absolute inset-0 rounded-full animate-ping-soft", color)} />}
      <span className={cn("relative size-2 rounded-full", color)} />
    </span>
  )
}

export const ratingTone: Record<Rating, "high" | "medium" | "low"> = { high: "high", medium: "medium", low: "low" }
export const ratingBar: Record<Rating, string> = {
  high: "bg-signal-high",
  medium: "bg-signal-medium",
  low: "bg-signal-low",
}

/** Field label in the technical label style. */
export function FieldLabel({ className, ...props }: ComponentProps<"label">) {
  return <label className={cn("label-technical mb-2 block text-ink", className)} {...props} />
}

export const inputClass =
  "w-full border-2 border-ink bg-paper px-3 py-2.5 font-sans text-body-lg text-ink placeholder:text-ink-muted/80 focus:border-cobalt focus:outline-none focus-visible:outline-none"
