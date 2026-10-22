import { NavLink, useLocation, useNavigate, useOutlet } from "react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AnimatePresence, motion } from "motion/react"
import { SignOut } from "@phosphor-icons/react"
import { api } from "@/lib/api"
import { ease } from "@/lib/motion"
import { cn } from "@/lib/utils"
import { LogoTile } from "@/components/brand"
import { StatusDot } from "@/components/ui/primitives"
import { meQuery } from "@/components/RequireAuth"

const tabs = [
  { to: "/app/inbox", label: "Triage inbox", stage: "Stage 01 // Triage inbox" },
  { to: "/app/review", label: "Review studio", stage: "Stage 02 // Review studio" },
  { to: "/app/archive", label: "Archive & cadence", stage: "Stage 03 // Archive & cadence" },
  { to: "/app/settings", label: "Settings & privacy", stage: "Stage 04 // Settings & privacy" },
]

export default function AppShell() {
  const location = useLocation()
  const outlet = useOutlet()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const me = useQuery(meQuery)
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, retry: false, refetchInterval: 60_000 })
  const current = tabs.find((t) => location.pathname.startsWith(t.to)) ?? tabs[0]

  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      queryClient.clear()
      navigate("/login", { replace: true })
    },
  })

  const github = me.data?.github_connected
  const online = health.isSuccess && health.data.db === "ok"

  return (
    <div className="bg-blueprint flex min-h-dvh flex-col">
      <motion.header
        initial={{ y: -24, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.6, ease: ease.expo }}
        className="sticky top-0 z-40"
      >
        <div className="border-b border-schematic bg-paper">
          <div className="mx-auto flex h-16 max-w-[1440px] items-center gap-4 px-margin-sm sm:px-margin">
            <NavLink to="/" className="flex shrink-0 items-center gap-3" aria-label="LinkedBuddy overview">
              <LogoTile />
              <span className="hidden leading-none sm:block">
                <span className="block font-display text-title tracking-[0.02em] text-ink">LinkedBuddy</span>
                <span className="label-badge mt-1 block text-ink-muted">personal workspace</span>
              </span>
            </NavLink>

            <nav aria-label="Workspace" className="mx-auto hidden gap-1.5 lg:flex">
              {tabs.map((t) => (
                <Tab key={t.to} {...t} />
              ))}
            </nav>

            <div className="ml-auto flex items-center gap-2 lg:ml-0">
              <NavLink
                to="/app/settings"
                className={cn(
                  "label-badge hidden items-center gap-2 border px-2.5 py-1.5 transition-transform hover:-translate-y-0.5 md:flex",
                  github ? "border-signal-high/40 bg-signal-high/10" : "border-signal-medium/50 bg-signal-medium/10",
                )}
              >
                <StatusDot tone={github ? "high" : "medium"} live={github} />
                <span className="font-bold text-ink">github.com</span>
                <span className="text-ink-muted">/</span>
                <span className={github ? "text-signal-high-ink" : "text-signal-medium-ink"}>{github ? "connected" : "not connected"}</span>
              </NavLink>
              <button
                type="button"
                onClick={() => logout.mutate()}
                disabled={logout.isPending}
                className="label-technical flex h-9 cursor-pointer items-center gap-2 border-2 border-ink px-3 transition-transform hover:-translate-y-0.5 hover:bg-ink hover:text-paper active:translate-y-px"
              >
                <SignOut weight="bold" aria-hidden /> <span className="hidden sm:inline">Log out</span>
                <span className="sr-only sm:hidden">Log out</span>
              </button>
            </div>
          </div>

          <nav aria-label="Workspace" className="no-scrollbar flex gap-1.5 overflow-x-auto border-t border-schematic px-margin-sm py-2 lg:hidden">
            {tabs.map((t) => (
              <Tab key={t.to} {...t} />
            ))}
          </nav>
        </div>

        <div className="border-b border-schematic bg-surface-low">
          <div className="mx-auto flex h-8 max-w-[1440px] items-center justify-between gap-4 px-margin-sm font-mono text-badge sm:px-margin">
            <p className="truncate uppercase">
              <span className="font-bold text-cobalt">&gt;&gt; workflow stage:</span>{" "}
              <span className="text-ink-soft">{current.stage}</span>
            </p>
            <p className="hidden shrink-0 gap-4 uppercase sm:flex">
              <span className="text-ink-muted">
                ai: <span className={me.data?.gemini_configured ? "text-signal-high-ink" : "text-signal-medium-ink"}>{me.data?.gemini_configured ? "gemini ready" : "no key"}</span>
              </span>
              <span className="text-ink-muted">
                video: <span className="text-ink">{me.data?.brag_configured ? "on" : "off"}</span>
              </span>
            </p>
          </div>
        </div>
      </motion.header>

      <AnimatePresence mode="wait">
        <motion.main
          key={location.pathname.split("/").slice(0, 3).join("/")}
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.4, ease: ease.expo }}
          className="mx-auto w-full max-w-[1440px] flex-1 px-margin-sm py-8 sm:px-margin lg:py-10"
        >
          {outlet}
        </motion.main>
      </AnimatePresence>

      <footer className="border-t-2 border-cobalt bg-midnight text-paper">
        <div className="mx-auto grid max-w-[1440px] gap-6 px-margin-sm py-8 sm:px-margin md:grid-cols-[1.4fr_1fr]">
          <div>
            <p className="flex items-center gap-3 font-display text-title tracking-[0.02em]">
              <LogoTile inverted /> LinkedBuddy repository engine
            </p>
            <p className="mt-3 max-w-md text-body text-ink-muted">
              Turns commits, pull requests and releases into LinkedIn drafts that cite their evidence. Nothing posts without you.
            </p>
          </div>
          <div className="flex flex-wrap content-start gap-2 md:justify-end">
            {["FastAPI", "React 19", "Gemini free tier", "Postgres"].map((s) => (
              <span key={s} className="border border-paper/15 px-2 py-1 font-mono text-badge text-cobalt-tint">
                {s}
              </span>
            ))}
          </div>
        </div>
        <div className="border-t border-paper/10">
          <div className="mx-auto flex max-w-[1440px] flex-wrap items-center justify-between gap-3 px-margin-sm py-3 sm:px-margin">
            <p className="label-technical flex items-center gap-2">
              <StatusDot tone={online ? "high" : "medium"} live={online} />
              {online ? "System online" : "API unreachable"}
            </p>
            <p className="label-technical text-cobalt-tint">Diff is truth · no unverified claims</p>
          </div>
        </div>
      </footer>
    </div>
  )
}

function Tab({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "label-technical shrink-0 border-2 px-3 py-2 whitespace-nowrap transition-transform duration-200 hover:-translate-y-0.5 active:translate-y-px",
          isActive ? "border-ink bg-cobalt text-paper shadow-hard-sm" : "border-schematic bg-paper text-ink hover:border-cobalt",
        )
      }
    >
      {label}
    </NavLink>
  )
}
