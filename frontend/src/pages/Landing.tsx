import { useRef, useState } from "react"
import { Link } from "react-router"
import { useQuery } from "@tanstack/react-query"
import { gsap } from "gsap"
import { ScrollTrigger } from "gsap/ScrollTrigger"
import { useGSAP } from "@gsap/react"
import { ArrowCounterClockwise, GithubLogo } from "@phosphor-icons/react"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import { LogoMark } from "@/components/brand"
import { buttonVariants } from "@/components/ui/button"
import { StatusDot } from "@/components/ui/primitives"
import { Schematic } from "@/components/Schematic"
import { HeroCarousel } from "@/components/landing/HeroCarousel"
import { EvidenceSection } from "@/components/landing/EvidenceSection"
import { useRipple } from "@/components/landing/useRipple"
import { LandingFooter } from "@/components/landing/LandingFooter"

gsap.registerPlugin(useGSAP, ScrollTrigger)

const stages = [
  { name: "Commits", note: "Stage 01 / Reading new commits, PRs and releases" },
  { name: "Triage inbox", note: "Stage 02 / Heuristic gate active" },
  { name: "Evidence linter", note: "Stage 03 / Every claim needs a commit or a file" },
  { name: "Media tray", note: "Stage 04 / Diagrams and screenshots from the repo" },
  { name: "Publish", note: "Stage 05 / You copy, paste and post it yourself" },
]

export default function Landing() {
  const root = useRef<HTMLDivElement>(null)
  const timeline = useRef<gsap.core.Timeline | null>(null)
  const [stage, setStage] = useState(1)
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false })
  const signedIn = me.isSuccess

  // The Stitch entrance choreography: title rises (0.1s), the cobalt slab rises (0.8s),
  // the carousel slides in from the right (1.7s), the evidence section follows (1.9s).
  useGSAP(
    () => {
      const mm = gsap.matchMedia()
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const ease = "expo.out"
        timeline.current = gsap
          .timeline()
          .from("[data-entrance=title]", { y: 40, opacity: 0, duration: 0.7, ease }, 0.1)
          .from("[data-entrance=blue]", { y: 120, opacity: 0, duration: 0.9, ease }, 0.8)
          .from("[data-entrance=boxes]", { x: 120, opacity: 0, duration: 0.85, ease }, 1.7)
          .from("[data-entrance=lower]", { y: 30, opacity: 0, duration: 0.8, ease }, 1.9)

        // Scroll-linked: the rotated rails drift against the scroll.
        gsap.to("[data-parallax=rail]", {
          yPercent: -18,
          ease: "none",
          scrollTrigger: { trigger: "[data-entrance=blue]", start: "top bottom", end: "bottom top", scrub: true },
        })
        gsap.fromTo(
          "[data-parallax=watermark]",
          { xPercent: -6 },
          {
            xPercent: 6,
            ease: "none",
            scrollTrigger: { trigger: "[data-entrance=lower]", start: "top bottom", end: "bottom top", scrub: true },
          },
        )
      })
      return () => mm.revert()
    },
    { scope: root },
  )

  const replay = () => timeline.current?.restart()

  return (
    <div ref={root} className="min-h-dvh bg-ink sm:py-12">
      <main className="relative mx-auto max-w-[1080px] overflow-hidden bg-canvas">
        {/* left rail */}
        <div className="absolute top-0 left-0 z-40 hidden h-[560px] w-14 flex-col items-center gap-6 border-r border-cobalt/10 pt-10 lg:flex">
          <GithubLogo weight="bold" className="size-5 text-cobalt" aria-hidden />
          <span className="label-badge writing-vertical text-cobalt/60">GitHub to LinkedIn signal</span>
        </div>

        <header className="relative z-30 border-b border-cobalt/10 bg-paper/70 px-margin-sm pt-6 pb-4 backdrop-blur-sm sm:px-margin lg:pl-24">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-cobalt/10 pb-5">
            <Link to="/" className="flex items-center gap-3">
              <span className="grid size-9 place-items-center rounded border border-cobalt/20 bg-paper/90 transition-transform duration-300 hover:rotate-6">
                <LogoMark className="size-6" />
              </span>
              <span className="label-technical text-cobalt">LinkedBuddy</span>
            </Link>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={replay}
                className="label-badge flex cursor-pointer items-center gap-1.5 border border-cobalt/30 bg-cobalt-mist px-2.5 py-1.5 text-cobalt transition-transform hover:-translate-y-0.5 active:translate-y-px"
              >
                <ArrowCounterClockwise weight="bold" className="size-3" aria-hidden /> Replay sequence
              </button>
              <Link
                to={signedIn ? "/app/inbox" : "/login"}
                className="label-badge flex items-center gap-1.5 border border-signal-high/40 bg-signal-high/10 px-2.5 py-1.5 text-signal-high-ink transition-transform hover:-translate-y-0.5"
              >
                <StatusDot tone="high" live={signedIn} />
                {signedIn ? "Open inbox" : "Log in"}
              </Link>
            </div>
          </div>

          <nav aria-label="Workflow stages" className="no-scrollbar -mx-margin-sm mt-4 flex items-center gap-x-2 overflow-x-auto px-margin-sm pb-1 sm:mx-0 sm:flex-wrap sm:justify-center sm:overflow-visible sm:px-0">
            {stages.map((s, i) => (
              <span key={s.name} className="flex shrink-0 items-center gap-2">
                {i > 0 && <span aria-hidden className="size-1 rounded-full bg-cobalt/30" />}
                <button
                  type="button"
                  aria-pressed={stage === i}
                  onClick={() => setStage(i)}
                  className={cn(
                    "label-technical cursor-pointer border-2 px-3 py-1.5 transition-transform duration-200 hover:-translate-y-0.5 active:translate-y-px",
                    stage === i ? "border-cobalt bg-cobalt-mist text-cobalt" : "border-transparent text-ink-muted hover:text-cobalt",
                  )}
                >
                  {stage === i ? `[ ${s.name} ]` : s.name}
                </button>
              </span>
            ))}
          </nav>
          <p className="mt-3 text-center font-mono text-label" aria-live="polite">
            <em className="text-cobalt">pick a workflow stage:</em>{" "}
            <span className="font-bold tracking-[0.08em] text-ink uppercase">{stages[stage].note}</span>
          </p>
        </header>

        {/* Hero */}
        <section aria-labelledby="hero-title" className="bg-dot-paper relative overflow-hidden px-margin-sm pt-6 pb-28 sm:px-margin lg:pb-40">
          {/* The giant title sits behind the carousel: the cards cover its lower half, as on the Stitch screen. */}
          <div data-entrance="title" className="relative z-0 overflow-hidden select-none">
            <h1
              id="hero-title"
              className="text-center font-display text-[19vw] leading-none tracking-[-0.04em] text-cobalt transition-transform duration-300 hover:scale-[1.01] sm:text-[clamp(6rem,17vw,11.5rem)]"
            >
              LinkedBuddy
            </h1>
          </div>

          <div data-entrance="boxes" className="relative z-10 -mt-[6.5vw] sm:-mt-[clamp(3rem,7vw,5.25rem)]">
            <HeroCarousel />
          </div>

          <p className="mx-auto mt-10 max-w-xl text-center text-body-lg text-ink-soft">
            Turns your GitHub commits into LinkedIn drafts where every claim points at a diff.
          </p>
          <div className="mt-6 flex justify-center">
            <CtaLink to={signedIn ? "/app/inbox" : "/login"} variant="bracket">
              [ {signedIn ? "Open the inbox" : "Log in to your inbox"} ]
            </CtaLink>
          </div>
        </section>

        {/* Manifesto: cobalt slab cut on a diagonal */}
        <section data-entrance="blue" aria-labelledby="manifesto-title" className="relative -mt-24 lg:-mt-32">
          <div className="clip-slash-top bg-cobalt px-margin-sm pt-[calc(14vw+3rem)] pb-14 text-paper sm:px-margin lg:pt-[170px]">
            <div className="grid grid-cols-1 gap-10 lg:grid-cols-[80px_minmax(0,1fr)_minmax(0,1.15fr)] lg:gap-12">
              <div data-parallax="rail" className="hidden items-start justify-end gap-3 overflow-hidden lg:flex">
                <span className="label-badge writing-vertical text-cobalt-tint/80">GitHub log → LinkedIn signal</span>
                <span aria-hidden className="writing-vertical font-display text-[3.4rem] leading-none whitespace-nowrap text-paper">
                  We are LinkedBuddy
                </span>
              </div>

              <figure className="group self-start rounded-glass border border-cobalt-tint/40 bg-cobalt-deep/40 p-5 shadow-[inset_0_2px_12px_color-mix(in_srgb,var(--color-midnight)_25%,transparent)] backdrop-blur-md transition-colors duration-300 hover:border-paper/50">
                <figcaption className="label-badge mb-3 flex justify-between gap-3 text-cobalt-tint">
                  <span>Fig 01. Pipeline</span>
                  <span className="flex items-center gap-1.5 text-signal-high">
                    <StatusDot tone="high" live /> Ready to draft
                  </span>
                </figcaption>
                <div className="py-6">
                  <Schematic tone="signal" />
                </div>
                <p className="font-mono text-badge text-cobalt-tint">
                  // The linter rejects "humbled and honored", "paradigm shift" and "supercharged".
                </p>
              </figure>

              <div>
                <h2 id="manifesto-title" className="font-display text-headline-xl-sm text-paper lg:hidden">
                  We are LinkedBuddy
                </h2>
                <p className="mt-4 text-[1.5rem] leading-[1.35] font-medium lg:mt-0">
                  Your work lives in the commit graph, the breakthrough in the diff, and the proof in the terminal. LinkedBuddy
                  writes from there.
                </p>
                <p className="mt-6 text-body-lg text-cobalt-tint">
                  It does two things. A triage engine skips version bumps and lockfile churn and surfaces changes with an
                  outcome you can show. An evidence-first writer drafts posts where every sentence links back to a commit, a
                  file or a benchmark.
                </p>
                <p className="mt-4 text-body-lg text-cobalt-tint">
                  Nothing posts in the background. You review each draft, edit it, and publish it yourself.
                </p>
                <CtaLink to={signedIn ? "/app/inbox" : "/login"} variant="bracket-light" className="mt-8">
                  [ {signedIn ? "Open the inbox" : "Log in"} ]
                </CtaLink>
              </div>
            </div>

            <div className="mt-12 border-t border-cobalt-tint/25 pt-5 lg:ml-[128px]">
              <p className="label-technical text-paper">Stage checklist // inspection tray</p>
              <p className="mt-1 font-mono text-badge text-cobalt-tint">
                What a change goes through before it becomes a draft.
              </p>
            </div>
          </div>
        </section>

        <div data-entrance="lower">
          <EvidenceSection signedIn={signedIn} />
        </div>
        <LandingFooter />
      </main>
    </div>
  )
}

/** Bracketed call to action with the Stitch tactile press: lift on hover, ripple on press, frosted fill. */
function CtaLink({ to, variant, className, children }: { to: string; variant: "bracket" | "bracket-light"; className?: string; children: React.ReactNode }) {
  const { onPointerDown, ripples } = useRipple(variant === "bracket" ? "dark" : "light")
  return (
    <Link
      to={to}
      onPointerDown={onPointerDown}
      className={cn(
        buttonVariants({ variant, size: "lg" }),
        "relative overflow-hidden backdrop-blur-sm",
        variant === "bracket" ? "bg-paper/70 shadow-glass-sm" : "bg-paper/5",
        className,
      )}
    >
      {ripples}
      <span className="relative">{children}</span>
    </Link>
  )
}
