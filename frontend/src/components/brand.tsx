import { cn } from "@/lib/utils"

/** The circuit monogram from the Stitch "Lowkey Monogram Logo" screen. `inverted` is for dark grounds. */
export function LogoMark({ className, inverted = false }: { className?: string; inverted?: boolean }) {
  return (
    <svg viewBox="0 0 100 100" fill="none" aria-hidden className={cn(inverted ? "text-sky" : "text-cobalt", className)}>
      <circle cx="50" cy="50" r="44" stroke="currentColor" strokeWidth="2" strokeDasharray="4 2" opacity="0.4" />
      <circle cx="28" cy="50" r="6" fill="currentColor" />
      <circle cx="50" cy="30" r="6" fill="currentColor" />
      <circle cx="72" cy="50" r="7" className={inverted ? "fill-paper" : "fill-cobalt-vivid"} stroke="currentColor" strokeWidth="2" />
      <circle cx="50" cy="70" r="5" fill="currentColor" opacity="0.6" />
      <path d="M28 50 C38 50, 40 30, 50 30 C60 30, 62 50, 72 50" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      <path
        d="M28 50 C38 50, 40 70, 50 70 C60 70, 62 50, 72 50"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray="3 3"
      />
      <circle cx="72" cy="50" r="14" className={inverted ? "stroke-paper" : "stroke-cobalt-vivid"} strokeWidth="1" strokeDasharray="2 3" opacity="0.7" />
    </svg>
  )
}

/** The logo in its framed tile, as used in the app header and the footers. */
export function LogoTile({ className, inverted = false }: { className?: string; inverted?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid size-9 shrink-0 place-items-center border-2 transition-transform duration-300 hover:rotate-6",
        inverted ? "border-cobalt-vivid bg-cobalt/20" : "border-ink bg-paper shadow-hard-sm",
        className,
      )}
    >
      <LogoMark inverted={inverted} className="size-[90%]" />
    </span>
  )
}
