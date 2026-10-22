import { useState, type PointerEvent } from "react"
import { motion, useMotionValue, useReducedMotion, useSpring, useTransform } from "motion/react"
import { ArrowLeft, ArrowRight } from "@phosphor-icons/react"
import { cn } from "@/lib/utils"
import { ease } from "@/lib/motion"
import { Tag } from "@/components/ui/primitives"
import { figures, type Figure } from "./figures"

type Slot = "center" | "left" | "right"

// Slot transforms from the Stitch screen (.slot-center / .slot-left / .slot-right).
const slots = (wide: boolean) => ({
  center: { x: 0, z: 0, scale: 1, rotateY: 0, opacity: 1, zIndex: 30 },
  left: { x: wide ? -32 : -70, z: wide ? -80 : -100, scale: wide ? 0.86 : 0.8, rotateY: wide ? 12 : 16, opacity: 0.68, zIndex: 15 },
  right: { x: wide ? 32 : 70, z: wide ? -80 : -100, scale: wide ? 0.86 : 0.8, rotateY: wide ? -12 : -16, opacity: 0.68, zIndex: 15 },
})

const slotOf = (index: number, active: number, total: number): Slot =>
  index === active ? "center" : index === (active - 1 + total) % total ? "left" : "right"

export function HeroCarousel() {
  const [active, setActive] = useState(1)
  const total = figures.length
  const wide = typeof window === "undefined" || window.matchMedia("(min-width: 640px)").matches
  const shift = (delta: number) => setActive((i) => (i + delta + total) % total)

  return (
    <div
      className="perspective-stage relative mx-auto w-full max-w-[780px]"
      role="region"
      aria-roledescription="carousel"
      aria-label="Examples of changes LinkedBuddy drafts posts from"
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") shift(-1)
        if (e.key === "ArrowRight") shift(1)
      }}
    >
      {figures.map((figure, i) => {
        const slot = slotOf(i, active, total)
        return (
          <motion.article
            key={figure.label}
            aria-hidden={slot !== "center"}
            initial={false}
            animate={slots(wide)[slot]}
            transition={{ duration: 0.65, ease: ease.carousel }}
            onClick={() => slot !== "center" && setActive(i)}
            className={cn(
              "mx-auto w-[86%] sm:w-[600px]",
              slot === "center" ? "relative" : "absolute inset-y-0 cursor-pointer",
              slot === "left" && "left-0",
              slot === "right" && "right-0",
            )}
            style={{ transformStyle: "preserve-3d" }}
          >
            <TiltCard figure={figure} index={i} total={total} interactive={slot === "center"} onShift={shift} onGo={setActive} active={active} />
          </motion.article>
        )
      })}
    </div>
  )
}

function TiltCard({
  figure,
  index,
  total,
  interactive,
  onShift,
  onGo,
  active,
}: {
  figure: Figure
  index: number
  total: number
  interactive: boolean
  onShift: (delta: number) => void
  onGo: (i: number) => void
  active: number
}) {
  const reduced = useReducedMotion()
  const px = useMotionValue(0.5)
  const py = useMotionValue(0.5)
  const rotateX = useSpring(useTransform(py, [0, 1], [6, -6]), { stiffness: 220, damping: 22 })
  const rotateY = useSpring(useTransform(px, [0, 1], [-6, 6]), { stiffness: 220, damping: 22 })
  const scale = useSpring(1, { stiffness: 260, damping: 24 })
  // Glare: a soft white light that follows the pointer (moved by transform, faded by opacity).
  const glareX = useTransform(px, (v) => `${v * 100 - 50}%`)
  const glareY = useTransform(py, (v) => `${v * 100 - 50}%`)
  const glare = useSpring(0, { stiffness: 200, damping: 26 })

  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!interactive || reduced || e.pointerType !== "mouse") return
    const r = e.currentTarget.getBoundingClientRect()
    px.set((e.clientX - r.left) / r.width)
    py.set((e.clientY - r.top) / r.height)
    scale.set(1.01)
    glare.set(1)
  }
  const reset = () => {
    px.set(0.5)
    py.set(0.5)
    scale.set(1)
    glare.set(0)
  }
  const pad = (n: number) => String(n).padStart(2, "0")

  return (
    <motion.div
      onPointerMove={onMove}
      onPointerLeave={reset}
      style={{ rotateX, rotateY, scale, transformPerspective: 1000 }}
      className={cn(
        "relative flex flex-col gap-3 overflow-hidden rounded-glass border-2 border-cobalt/80 bg-paper/95 p-3 backdrop-blur-md sm:p-4",
        interactive ? "shadow-glass" : "shadow-glass-sm",
      )}
    >
      <motion.span
        aria-hidden
        className="pointer-events-none absolute top-0 left-0 z-40 size-full rounded-full bg-[radial-gradient(circle,color-mix(in_srgb,var(--color-sky)_22%,transparent)_0%,color-mix(in_srgb,var(--color-paper)_30%,transparent)_30%,transparent_62%)]"
        style={{ x: glareX, y: glareY, opacity: glare }}
      />
      <header className="flex items-center justify-between gap-3 border-b border-cobalt/20 pb-2.5">
        <span className="label-technical flex min-w-0 items-center gap-2 text-cobalt">
          <span aria-hidden className="size-2 shrink-0 rounded-full bg-cobalt" />
          <span className="truncate">{figure.label}</span>
        </span>
        <div className="flex shrink-0 items-center gap-1.5">
          <ArrowButton label="Previous figure" disabled={!interactive} onClick={() => onShift(-1)}>
            <ArrowLeft weight="bold" />
          </ArrowButton>
          <span className="label-technical hidden rounded border border-cobalt/60 px-2 py-1 text-cobalt sm:inline">
            [ {pad(index + 1)} / {pad(total)} ]
          </span>
          <ArrowButton label="Next figure" disabled={!interactive} onClick={() => onShift(1)}>
            <ArrowRight weight="bold" />
          </ArrowButton>
        </div>
      </header>

      <div className="bg-blueprint relative aspect-[600/340] overflow-hidden rounded border border-cobalt/20 bg-cobalt-mist/50">
        <span className="label-badge absolute top-2.5 left-3 z-10 rounded border border-cobalt/40 bg-paper/95 px-2 py-0.5 font-bold text-cobalt normal-case backdrop-blur-sm">
          repo: {figure.repo}
        </span>
        {figure.art}
        <Tag tone={figure.badgeTone === "dark" ? "dark" : figure.badgeTone} className="absolute right-3 bottom-2.5 z-10 hidden rounded backdrop-blur-sm sm:inline-flex">
          {figure.badge}
        </Tag>
      </div>

      <footer className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between sm:gap-4">
        <div className="min-w-0">
          <p className="font-mono text-badge text-ink sm:text-code">{figure.caption}</p>
          <p className="mt-1 font-mono text-badge text-ink-muted">// lint: no unsupported superlatives</p>
        </div>
        <div className="flex shrink-0 gap-1.5 pb-1" role="tablist" aria-label="Choose a figure">
          {Array.from({ length: total }, (_, i) => (
            <button
              key={i}
              type="button"
              role="tab"
              aria-selected={i === active}
              aria-label={`Figure ${i + 1}`}
              disabled={!interactive}
              onClick={() => onGo(i)}
              className="group/dot grid h-4 w-6 cursor-pointer place-items-center"
            >
              <span
                className={cn(
                  "h-1.5 w-6 rounded-full transition-transform duration-300 ease-expo",
                  i === active ? "scale-x-100 bg-cobalt" : "scale-x-25 bg-cobalt/30 group-hover/dot:bg-cobalt/60",
                )}
              />
            </button>
          ))}
        </div>
      </footer>
    </motion.div>
  )
}

function ArrowButton({ label, disabled, onClick, children }: { label: string; disabled: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      tabIndex={disabled ? -1 : 0}
      onClick={(e) => {
        e.stopPropagation()
        if (!disabled) onClick()
      }}
      className="grid size-7 cursor-pointer place-items-center rounded border border-cobalt/60 bg-paper/80 text-cobalt transition-transform duration-200 hover:bg-cobalt hover:text-paper active:scale-90"
    >
      {children}
    </button>
  )
}
