import { useEffect, useState, type FormEvent } from "react"
import { Link, Navigate, useLocation, useNavigate } from "react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import { ArrowLeft, Eye, EyeSlash, LockKey } from "@phosphor-icons/react"
import { api, ApiError } from "@/lib/api"
import { ease, stagger } from "@/lib/motion"
import { cn } from "@/lib/utils"
import { LogoMark } from "@/components/brand"
import { Button } from "@/components/ui/button"
import { FieldLabel, StatusDot, inputClass } from "@/components/ui/primitives"
import { Schematic } from "@/components/Schematic"
import terminal from "@/assets/img/terminal-bench.webp"

type Phase = "idle" | "verifying" | "granted"

const sequence = ["Session opened", "Loading your repositories", "Opening your workspace"]

const wipeLabel = (path: string) =>
  path.includes("/review") ? "Review" : path.includes("/archive") ? "Archive" : path.includes("/settings") ? "Settings" : "Inbox"

function loginError(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) return "That password doesn't match. Check it and try again."
  if (error instanceof ApiError && error.status === 429) return "Too many attempts from this machine. Wait a few minutes, then try again."
  if (error instanceof ApiError && error.status === 422) return "Enter a password up to 200 characters."
  return "The server didn't answer. Start the backend (backend/scripts/dev.sh) and try again."
}

export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const reduced = useReducedMotion()
  const [password, setPassword] = useState("")
  const [reveal, setReveal] = useState(false)
  const [phase, setPhase] = useState<Phase>("idle")
  const [step, setStep] = useState(0)
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false })
  const destination = (location.state as { from?: string } | null)?.from ?? "/app/inbox"

  const login = useMutation({
    mutationFn: () => api.login(password),
    onMutate: () => setPhase("verifying"),
    onSuccess: async () => {
      setPassword("")
      await queryClient.invalidateQueries({ queryKey: ["me"] })
      if (reduced) {
        navigate(destination, { replace: true })
        return
      }
      setPhase("granted")
    },
    onError: () => setPhase("idle"),
  })

  // The granted sequence: three status lines tick over, then the cobalt wipe covers the screen.
  useEffect(() => {
    if (phase !== "granted") return
    if (step < sequence.length) {
      const t = window.setTimeout(() => setStep((s) => s + 1), 380)
      return () => window.clearTimeout(t)
    }
    const t = window.setTimeout(() => navigate(destination, { replace: true }), 700)
    return () => window.clearTimeout(t)
  }, [phase, step, navigate, destination])

  if (me.isSuccess && phase === "idle") return <Navigate to={destination} replace />

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!password || login.isPending) return
    login.mutate()
  }
  const granted = phase === "granted"

  return (
    <div className="relative grid min-h-dvh bg-canvas lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      {/* Cobalt side: brand, the pipeline, the photo */}
      <aside className="relative overflow-hidden bg-cobalt text-paper lg:[clip-path:polygon(0_0,100%_0,88%_100%,0_100%)]">
        <motion.div
          initial="hidden"
          animate="show"
          variants={stagger.container}
          className="relative flex h-full flex-col gap-8 px-margin-sm py-8 sm:px-margin lg:py-12 lg:pr-[16%]"
        >
          <motion.div variants={stagger.item} className="flex items-center justify-between">
            <Link to="/" className="label-technical flex items-center gap-2 text-cobalt-tint transition-transform hover:-translate-x-1">
              <ArrowLeft weight="bold" aria-hidden /> Back to the overview
            </Link>
            <span className="label-badge flex items-center gap-2 text-cobalt-tint">
              <StatusDot tone="high" live /> 127.0.0.1
            </span>
          </motion.div>

          <motion.p
            variants={stagger.item}
            aria-hidden
            className="font-display text-[clamp(3.5rem,11vw,8.5rem)] leading-[0.82] tracking-[-0.03em] text-paper"
          >
            Linked
            <br />
            Buddy
          </motion.p>

          <motion.div variants={stagger.item} className="border border-cobalt-tint/30 bg-cobalt-deep/80 p-4 sm:p-6">
            <p className="label-badge mb-4 flex justify-between text-cobalt-tint">
              <span>Fig 00. Your pipeline</span>
              <span className={cn("flex items-center gap-1.5", granted ? "text-signal-high" : "text-cobalt-tint")}>
                <StatusDot tone={granted ? "high" : "low"} live={granted} />
                {granted ? "Unlocked" : "Locked"}
              </span>
            </p>
            {/* Re-keyed on success so it draws itself again, end to end */}
            <Schematic key={granted ? "on" : "off"} tone="signal" play={granted ? true : undefined} />
          </motion.div>

          <motion.figure variants={stagger.item} className="relative mt-auto hidden aspect-[21/9] overflow-hidden lg:block">
            <img src={terminal} alt="Hands on a keyboard in front of a terminal running cargo bench" className="size-full object-cover" />
            <figcaption className="label-badge absolute bottom-2 left-2 bg-midnight/80 px-2 py-1 text-cobalt-tint normal-case">
              $ cargo bench -p engine-retrieval
            </figcaption>
          </motion.figure>
        </motion.div>
      </aside>

      {/* Form side */}
      <main className="bg-blueprint flex items-center justify-center px-margin-sm py-12 sm:px-margin">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: ease.expo, delay: 0.15 }}
          className="w-full max-w-md border-[1.5px] border-ink bg-paper p-6 shadow-hard sm:p-8"
        >
          <div className="flex items-center gap-3">
            <span className="grid size-10 place-items-center border border-cobalt/20">
              <LogoMark className="size-7" />
            </span>
            <p className="label-technical text-cobalt">Owner access</p>
          </div>
          <h1 className="mt-6 font-display text-headline-xl-sm text-ink sm:text-headline-xl">Log in</h1>
          <p className="mt-2 text-body-lg text-ink-soft">One owner, one password. Your session lasts 14 days on this browser.</p>

          <form onSubmit={submit} className="mt-8" noValidate>
            <FieldLabel htmlFor="password">Password</FieldLabel>
            <div className="relative">
              <input
                id="password"
                name="password"
                type={reveal ? "text" : "password"}
                autoComplete="current-password"
                autoFocus
                maxLength={200}
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value)
                  if (login.isError) login.reset()
                }}
                aria-invalid={login.isError}
                aria-describedby={login.isError ? "login-error" : undefined}
                disabled={phase !== "idle"}
                className={cn(inputClass, "pr-12 font-mono", login.isError && "border-unsupported")}
              />
              <button
                type="button"
                onClick={() => setReveal((r) => !r)}
                aria-label={reveal ? "Hide password" : "Show password"}
                className="absolute inset-y-0 right-0 grid w-12 cursor-pointer place-items-center text-ink-muted hover:text-cobalt"
              >
                {reveal ? <EyeSlash /> : <Eye />}
              </button>
            </div>

            <AnimatePresence>
              {login.isError && (
                <motion.p
                  id="login-error"
                  role="alert"
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: [0, -6, 6, -3, 0] }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.4 }}
                  className="mt-3 border-l-4 border-unsupported bg-danger-tint/50 px-3 py-2 text-body text-danger"
                >
                  {loginError(login.error)}
                </motion.p>
              )}
            </AnimatePresence>

            <Button type="submit" size="lg" className="mt-6 w-full overflow-hidden" disabled={!password || phase !== "idle"}>
              <LockKey weight="bold" aria-hidden />
              {phase === "idle" ? "Log in" : phase === "verifying" ? "Checking" : "Unlocked"}
              {phase === "verifying" && (
                <motion.span
                  aria-hidden
                  className="absolute bottom-0 left-0 h-1 w-2/5 bg-sky"
                  initial={{ x: "-100%" }}
                  animate={{ x: "250%" }}
                  transition={{ duration: 1.1, repeat: Infinity, ease: "easeInOut" }}
                />
              )}
            </Button>
          </form>

          <ol className="mt-6 min-h-[4.5rem] space-y-1 font-mono text-badge" aria-live="polite">
            {sequence.slice(0, granted ? step : 0).map((line) => (
              <motion.li
                key={line}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.35, ease: ease.expo }}
                className="flex items-center gap-2 text-signal-high-ink"
              >
                <span aria-hidden>✓</span> {line}
              </motion.li>
            ))}
          </ol>
        </motion.div>
      </main>

      {/* The cobalt wipe that carries you into the app */}
      <AnimatePresence>
        {granted && step >= sequence.length && (
          <motion.div
            key="wipe"
            aria-hidden
            className="fixed inset-0 z-50 flex origin-bottom items-end bg-cobalt px-margin-sm pb-10 sm:px-margin"
            initial={{ scaleY: 0 }}
            animate={{ scaleY: 1 }}
            transition={{ duration: 0.55, ease: ease.expo }}
          >
            <motion.span
              className="font-display text-[clamp(4rem,14vw,12rem)] leading-[0.82] text-paper"
              initial={{ y: 60, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              transition={{ duration: 0.5, ease: ease.expo, delay: 0.25 }}
            >
              {wipeLabel(destination)}
            </motion.span>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
