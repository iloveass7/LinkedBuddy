import { useState, type PointerEvent } from "react"

type Ripple = { id: number; x: number; y: number; size: number }
let nextId = 0

/** The Stitch "tactile" press: a circle grows out of the pointer and fades. The host needs `relative overflow-hidden`. */
export function useRipple(tone: "light" | "dark" = "light") {
  const [ripples, setRipples] = useState<Ripple[]>([])

  const onPointerDown = (e: PointerEvent<HTMLElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const size = Math.max(rect.width, rect.height)
    const ripple = { id: nextId++, x: e.clientX - rect.left - size / 2, y: e.clientY - rect.top - size / 2, size }
    setRipples((r) => [...r, ripple])
    window.setTimeout(() => setRipples((r) => r.filter((x) => x.id !== ripple.id)), 650)
  }

  const layer = ripples.map((r) => (
    <span
      key={r.id}
      aria-hidden
      className={`pointer-events-none absolute animate-ripple rounded-full ${tone === "light" ? "bg-paper/40" : "bg-cobalt/25"}`}
      style={{ left: r.x, top: r.y, width: r.size, height: r.size }}
    />
  ))

  return { onPointerDown, ripples: layer }
}
