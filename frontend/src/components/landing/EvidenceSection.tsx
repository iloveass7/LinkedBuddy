import { useEffect, useRef, useState } from "react"
import { Link } from "react-router"
import { AnimatePresence, animate, motion, useInView, useReducedMotion } from "motion/react"
import { gsap } from "gsap"
import { ScrollTrigger } from "gsap/ScrollTrigger"
import { useGSAP } from "@gsap/react"
import { CaretDown, Plus } from "@phosphor-icons/react"
import { cn } from "@/lib/utils"
import { ease } from "@/lib/motion"
import { Button } from "@/components/ui/button"
import { StatusDot, Tag } from "@/components/ui/primitives"
import { toast } from "@/components/ui/toast"
import { useRipple } from "./useRipple"
import keycap from "@/assets/img/keycap.webp"
import desk from "@/assets/img/desk-cobalt.webp"

gsap.registerPlugin(useGSAP, ScrollTrigger)

const sampleDraft =
  "We bumped throughput by 4.2% not with an LLM prompt trick, but by eliminating redundant serde deserialization passes in our worker loop. Here is the before/after memory profile:"

const queue = [
  {
    rating: "HIGH" as const,
    title: "PR #48: +4.2% F1 score in the README benchmark table.",
    diff: ["+ | Model: BanglaNER-Large (1.2M params)", "+ | Recall: 91.8% | F1: +4.2%", "- | Baseline: mBERT zero-shot (F1: 76.4%)"],
  },
  {
    rating: "MED" as const,
    title: "feat: added streaming chunk parser.",
    diff: ["+ pub fn stream_mmap_reader(path: &Path) -> Result<ChunkStream>", "// removes 3 allocation copies per chunk"],
  },
]

export function EvidenceSection({ signedIn }: { signedIn: boolean }) {
  const root = useRef<HTMLElement>(null)

  useGSAP(
    () => {
      const mm = gsap.matchMedia()
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        ScrollTrigger.batch("[data-reveal]", {
          start: "top 88%",
          once: true,
          onEnter: (els) => gsap.from(els, { y: 32, opacity: 0, duration: 0.8, ease: "expo.out", stagger: 0.09 }),
        })
      })
      return () => mm.revert()
    },
    { scope: root },
  )

  return (
    <section ref={root} aria-labelledby="evidence-title" className="relative overflow-hidden bg-paper px-margin-sm py-16 sm:px-margin lg:py-24">
      <span
        aria-hidden
        data-parallax="watermark"
        className="pointer-events-none absolute -bottom-10 -left-6 font-display text-[clamp(8rem,30vw,22rem)] leading-none text-cobalt-mist select-none"
      >
        Signal
      </span>

      <h2 id="evidence-title" className="sr-only">
        What a change goes through
      </h2>

      <div className="relative grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-10">
        <div className="flex flex-col gap-8">
          <LinterCard />
          <QueueCard />
        </div>
        <div className="flex flex-col gap-10 lg:pt-0">
          <DraftCard />
          <div data-reveal className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-6 lg:mt-6">
            <div>
              <h3 className="font-display text-headline-lg text-ink">Built for one builder who ships</h3>
              <p className="mt-3 max-w-sm font-mono text-code text-ink-muted">
                Your repositories in, drafts out. You stay the author of every post.
              </p>
              <p className="label-technical mt-5 flex flex-wrap gap-x-3 gap-y-2 text-cobalt">
                <Link to={signedIn ? "/app/inbox" : "/login"} className="hover:text-cobalt-vivid hover:underline">
                  [{signedIn ? "Inbox" : "Log in"}]
                </Link>
                <span aria-hidden>•</span>
                <Link to="/app/archive" className="hover:text-cobalt-vivid hover:underline">[Archive]</Link>
                <span aria-hidden>•</span>
                <Link to="/app/settings" className="hover:text-cobalt-vivid hover:underline">[Settings]</Link>
              </p>
            </div>
            <span aria-hidden className="writing-vertical font-display text-[4.5rem] leading-none text-cobalt">
              LinkedBuddy
            </span>
          </div>
        </div>
      </div>
    </section>
  )
}

type ScanState = "idle" | "scanning" | "passed"

function LinterCard() {
  const [scan, setScan] = useState<ScanState>("idle")
  const reduced = useReducedMotion()
  // One pass of the beam, then the verdict (the Stitch "simulated laser scan").
  useEffect(() => {
    if (scan !== "scanning") return
    const t = window.setTimeout(() => setScan("passed"), reduced ? 300 : 1800)
    return () => window.clearTimeout(t)
  }, [scan, reduced])

  return (
    <article
      data-reveal
      className="group relative overflow-hidden rounded-glass border border-cobalt-vivid/40 bg-paper/70 shadow-glass-sm backdrop-blur-md transition-colors duration-300 hover:border-cobalt/70"
    >
      <header className="flex items-center justify-between bg-cobalt px-4 py-3 text-paper">
        <span className="label-technical flex items-center gap-2">
          Evidence linter
          <span
            className={cn(
              "label-badge rounded px-1.5 py-0.5 font-bold",
              scan === "idle" && "bg-ink/30",
              scan === "scanning" && "animate-pulse bg-sky text-midnight",
              scan === "passed" && "bg-signal-high text-midnight",
            )}
          >
            {scan === "idle" ? "Idle" : scan === "scanning" ? "Scanning…" : "Passed (0 slop)"}
          </span>
        </span>
        <button
          type="button"
          aria-label="Run the linter scan"
          disabled={scan === "scanning"}
          onClick={() => setScan("scanning")}
          className="grid size-7 cursor-pointer place-items-center transition-transform duration-200 hover:rotate-90 active:scale-90"
        >
          <Plus weight="bold" />
        </button>
      </header>
      <div className="relative aspect-square overflow-hidden bg-surface-low">
        <img src={keycap} alt="Mechanical keyboard with a glowing blue keycap" className="size-full object-cover transition-transform duration-700 group-hover:scale-[1.03]" loading="lazy" />
        {scan === "scanning" && !reduced && <ScanTrack />}
        <Tag tone="dark" className="absolute bottom-3 left-3 rounded bg-midnight/80 backdrop-blur-sm">
          <StatusDot tone="cobalt" live={scan === "scanning"} /> diff inspection · sha 7b4e99c
        </Tag>
        <span className="label-badge absolute top-2 right-2 rounded bg-midnight/70 px-1.5 py-0.5 text-cobalt-tint opacity-0 transition-opacity group-hover:opacity-100">
          press + to scan
        </span>
      </div>
      <footer className="border-t border-cobalt-tint bg-paper/70 px-4 py-3 backdrop-blur-sm">
        <p className="flex justify-between font-mono text-badge text-ink-muted">
          <span>FILE: engine/triage.rs</span>
          <span className="text-cobalt">+184 / −12</span>
        </p>
        <p aria-live="polite" className={cn("mt-1 text-body", scan === "passed" ? "font-semibold text-signal-high-ink" : scan === "scanning" ? "text-cobalt-vivid" : "text-ink")}>
          {scan === "scanning"
            ? "Checking the diff against your banned-phrase rules…"
            : scan === "passed"
              ? "Verified: no unsupported superlatives in 3 commits."
              : "Press + to lint the latest draft against its diff."}
        </p>
      </footer>
    </article>
  )
}

/** A full-height track so the beam can travel with transforms only. */
function ScanTrack() {
  return (
    <motion.span
      aria-hidden
      className="pointer-events-none absolute inset-0"
      initial={{ y: "-100%" }}
      animate={{ y: "100%" }}
      transition={{ duration: 1.8, ease: "easeInOut" }}
    >
      <span className="absolute inset-x-0 bottom-0 h-0.5 bg-sky shadow-[0_0_12px_2px_var(--color-sky)]" />
    </motion.span>
  )
}

function QueueCard() {
  const [open, setOpen] = useState<number | null>(0)
  return (
    <article data-reveal className="relative rounded-glass border border-cobalt-tint/40 bg-cobalt/90 p-4 text-paper shadow-glass backdrop-blur-md transition-colors duration-300 hover:border-paper/60">
      <header className="label-technical mb-3 flex justify-between gap-3">
        <span className="whitespace-nowrap">Triage queue [{queue.length} pending]</span>
        <span className="label-badge whitespace-nowrap text-cobalt-tint">tap to expand</span>
      </header>
      <ul className="flex flex-col gap-2">
        {queue.map((item, i) => (
          <motion.li layout key={item.title} className={cn("rounded border transition-colors", open === i ? "border-cobalt-tint/60 bg-cobalt-deep/80" : "border-cobalt-tint/25 bg-cobalt-deep/50 hover:bg-cobalt-deep/70")}>
            <button
              type="button"
              aria-expanded={open === i}
              onClick={() => setOpen(open === i ? null : i)}
              className="flex w-full cursor-pointer items-start justify-between gap-3 p-3 text-left font-mono text-code"
            >
              <span>
                <span className={item.rating === "HIGH" ? "text-signal-high" : "text-signal-medium"}>[{item.rating}]</span>{" "}
                {item.title}
              </span>
              <CaretDown weight="bold" className={cn("mt-0.5 size-4 shrink-0 transition-transform duration-300", open === i && "rotate-180")} />
            </button>
            <AnimatePresence initial={false}>
              {open === i && (
                <motion.pre
                  key="diff"
                  initial={{ opacity: 0, y: -6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={{ duration: 0.3, ease: ease.expo }}
                  className="overflow-x-auto rounded-b bg-midnight/60 px-3 py-2 font-mono text-badge leading-5"
                >
                  {item.diff.map((line) => (
                    <div key={line} className={line.startsWith("+") ? "text-signal-high" : line.startsWith("-") ? "text-unsupported" : "text-cobalt-tint"}>
                      {line}
                    </div>
                  ))}
                </motion.pre>
              )}
            </AnimatePresence>
          </motion.li>
        ))}
      </ul>
    </article>
  )
}

function TypedCommand({ text }: { text: string }) {
  const ref = useRef<HTMLSpanElement>(null)
  const inView = useInView(ref, { once: true })
  const reduced = useReducedMotion()
  const [shown, setShown] = useState(reduced ? text.length : 0)
  useEffect(() => {
    if (!inView || reduced) return
    const controls = animate(0, text.length, { duration: 1.6, ease: "linear", onUpdate: (v) => setShown(Math.round(v)) })
    return () => controls.stop()
  }, [inView, reduced, text.length])
  return (
    <span ref={ref} aria-label={text}>
      <span aria-hidden>{text.slice(0, shown)}</span>
      <span aria-hidden className="animate-blink">_</span>
    </span>
  )
}

function DraftCard() {
  const [copied, setCopied] = useState(false)
  const [diffOpen, setDiffOpen] = useState(false)
  const copyRipple = useRipple("light")
  const diffRipple = useRipple("dark")
  const copy = async () => {
    await navigator.clipboard.writeText(sampleDraft)
    setCopied(true)
    toast("Draft copied to the clipboard")
    window.setTimeout(() => setCopied(false), 2200)
  }
  return (
    <article data-reveal className="group relative overflow-hidden rounded-glass border border-cobalt-vivid/40 bg-paper/70 shadow-glass backdrop-blur-md transition-colors duration-300 hover:border-cobalt/70">
      <div className="relative aspect-[16/9] overflow-hidden bg-midnight">
        <motion.img
          src={desk}
          alt="Developer at a desk with three monitors lit in cobalt"
          className="size-full object-cover"
          loading="lazy"
          initial={{ scale: 1.08 }}
          whileInView={{ scale: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 1.6, ease: ease.expo }}
        />
        <div aria-hidden className="absolute inset-0 bg-[linear-gradient(to_top,color-mix(in_srgb,var(--color-midnight)_85%,transparent),color-mix(in_srgb,var(--color-midnight)_30%,transparent)_45%,transparent)]" />
        <div className="absolute inset-x-0 bottom-0 flex items-end justify-between gap-3 px-4 py-3 text-paper">
          <div>
            <p className="label-badge flex items-center gap-1.5 text-cobalt-tint">
              <StatusDot tone="high" live /> Human review
            </p>
            <p className="mt-1 font-mono text-code font-bold">
              <TypedCommand text="$ linkedbuddy triage --since 7d" />
            </p>
          </div>
          <Tag tone="cobalt" className="rounded bg-cobalt text-paper">stage 04</Tag>
        </div>
      </div>
      <div className="border-t border-cobalt-tint bg-paper/70 p-5 backdrop-blur-md">
        <p className="label-technical flex flex-wrap justify-between gap-2">
          <span className="flex items-center gap-2 text-cobalt">
            <StatusDot tone="cobalt" /> Generated draft
          </span>
          <span className="text-ink-muted">format: technical carousel</span>
        </p>
        <blockquote className="mt-3 text-body-lg text-ink">"{sampleDraft}"</blockquote>
        <AnimatePresence initial={false}>
          {diffOpen && (
            <motion.pre
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.3, ease: ease.expo }}
              className="mt-3 overflow-x-auto bg-ink px-3 py-2 font-mono text-badge leading-5 text-cobalt-tint"
            >
              <div className="text-ink-muted">src/worker.rs · line 14</div>
              <div className="text-unsupported">- let rec: Record = serde_json::from_slice(&buf)?;</div>
              <div className="text-signal-high">+ let rec: Record&lt;'_&gt; = Record::borrow(&buf)?;</div>
            </motion.pre>
          )}
        </AnimatePresence>
        <div className="mt-4 flex flex-wrap gap-3">
          <Button variant="primary" size="sm" className="relative overflow-hidden font-mono text-label" onClick={copy} onPointerDown={copyRipple.onPointerDown}>
            {copyRipple.ripples}
            <span className="relative">[ {copied ? "Copied" : "Copy draft"} ]</span>
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="relative overflow-hidden bg-paper/80 font-mono text-label shadow-none"
            aria-expanded={diffOpen}
            onClick={() => setDiffOpen((o) => !o)}
            onPointerDown={diffRipple.onPointerDown}
          >
            {diffRipple.ripples}
            <span className="relative">[ {diffOpen ? "Hide diff" : "View diff line #14"} ]</span>
          </Button>
        </div>
      </div>
    </article>
  )
}
