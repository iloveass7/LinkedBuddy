import { motion, useReducedMotion, type Variants } from "motion/react"
import { cn } from "@/lib/utils"
import { ease } from "@/lib/motion"

/*
 * The pipeline schematic that recurs across the Stitch screens: GitHub activity forks into the
 * heuristic gate (dashed) and AI triage + lint (solid), converges, and lands on LinkedIn.
 * It draws itself when it scrolls into view, or when `play` turns true.
 */

type Tone = "light" | "dark" | "signal"

const palette: Record<Tone, { line: string; node: string; fill: string; text: string; ring: string }> = {
  light: { line: "stroke-cobalt", node: "fill-paper stroke-cobalt", fill: "fill-cobalt", text: "fill-cobalt", ring: "stroke-cobalt-vivid" },
  dark: { line: "stroke-sky", node: "fill-midnight stroke-sky", fill: "fill-sky", text: "fill-sky", ring: "stroke-sky" },
  signal: { line: "stroke-cobalt-tint", node: "fill-ink stroke-cobalt-tint", fill: "fill-signal-medium", text: "fill-cobalt-tint", ring: "stroke-cobalt-tint" },
}

const draw: Variants = {
  hidden: { pathLength: 0, opacity: 0 },
  shown: (i: number) => ({
    pathLength: 1,
    opacity: 1,
    transition: { pathLength: { delay: 0.15 + i * 0.12, duration: 0.7, ease: ease.expo }, opacity: { delay: 0.15 + i * 0.12, duration: 0.01 } },
  }),
}
// pathLength drawing replaces stroke-dasharray, so dashed lines fade in instead.
const fade: Variants = {
  hidden: { opacity: 0 },
  shown: (i: number) => ({ opacity: 1, transition: { delay: 0.15 + i * 0.12, duration: 0.5 } }),
}
const pop: Variants = {
  hidden: { scale: 0, opacity: 0 },
  shown: (i: number) => ({
    scale: 1,
    opacity: 1,
    transition: { delay: 0.1 + i * 0.09, type: "spring", stiffness: 380, damping: 22 },
  }),
}

const upper = "M175 100 C195 100 200 50 215 50 L360 50 C380 50 405 100 430 100"
const lower = "M175 100 C195 100 200 150 215 150 L360 150 C380 150 405 100 430 100"

export function Schematic({
  tone = "light",
  play,
  labels = true,
  className,
  title = "Pipeline: GitHub activity passes the heuristic gate and AI triage before it becomes a LinkedIn draft",
}: {
  tone?: Tone
  play?: boolean
  labels?: boolean
  className?: string
  title?: string
}) {
  const c = palette[tone]
  const reduced = useReducedMotion()
  const controlled = play !== undefined
  const motionProps = reduced
    ? { initial: "shown" as const }
    : controlled
      ? { initial: "hidden" as const, animate: play ? "shown" : "hidden" }
      : { initial: "hidden" as const, whileInView: "shown", viewport: { once: true, amount: 0.4 } }

  const node = (cx: number, cy: number, r: number, i: number, filled = false) => (
    <motion.circle
      key={`${cx}-${cy}`}
      cx={cx}
      cy={cy}
      r={r}
      custom={i}
      variants={pop}
      strokeWidth={2.5}
      className={filled ? c.fill : c.node}
      style={{ transformBox: "fill-box", transformOrigin: "center" }}
    />
  )

  return (
    <motion.svg viewBox="0 0 600 200" role="img" aria-label={title} className={cn("w-full", className)} {...motionProps}>
      {/* spine into the fork, and the hand-off to LinkedIn */}
      <motion.path d="M40 100 L175 100" custom={0} variants={fade} className={cn(c.line, "fill-none animate-flow-dash")} strokeWidth={2} strokeDasharray="6 4" />
      <motion.path d={upper} custom={1} variants={fade} className={cn(c.line, "fill-none")} strokeWidth={2} strokeDasharray="5 5" />
      <motion.path d={lower} custom={2} variants={draw} className={cn(c.line, "fill-none")} strokeWidth={2.5} />
      <motion.path d="M430 100 L540 100" custom={3} variants={fade} className={cn(c.line, "fill-none animate-flow-dash")} strokeWidth={2} strokeDasharray="6 4" />

      {node(40, 100, 12, 0)}
      {node(108, 100, 7, 1, true)}
      {node(175, 100, 11, 2)}
      {node(215, 50, 11, 3)}
      {node(288, 50, 8, 4, true)}
      {node(360, 50, 11, 5)}
      {node(215, 150, 11, 3)}
      {node(288, 150, 8, 4, true)}
      {node(360, 150, 11, 5)}
      {node(430, 100, 15, 6, true)}
      {node(540, 100, 17, 7)}

      {/* radar rings on the LinkedIn target */}
      <motion.g custom={8} variants={pop} style={{ transformBox: "fill-box", transformOrigin: "center" }}>
        <circle cx={540} cy={100} r={26} className={cn(c.ring, "fill-none")} strokeWidth={1} strokeDasharray="2 3" opacity={0.7} />
        <circle cx={540} cy={100} r={34} className={cn(c.ring, "fill-none animate-ping-soft")} strokeWidth={1} strokeDasharray="2 4" opacity={0.4} style={{ transformBox: "fill-box", transformOrigin: "center" }} />
      </motion.g>

      {labels && (
        <motion.g custom={9} variants={pop} className={cn(c.text, "font-mono font-bold")} fontSize={12} letterSpacing="0.06em">
          <text x={40} y={130} textAnchor="middle">GITHUB</text>
          <text x={288} y={28} textAnchor="middle">HEURISTIC GATE</text>
          <text x={288} y={182} textAnchor="middle">AI TRIAGE + LINT</text>
          <text x={540} y={140} textAnchor="middle">LINKEDIN</text>
        </motion.g>
      )}
    </motion.svg>
  )
}
