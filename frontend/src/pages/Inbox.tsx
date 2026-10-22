import { useMemo, useState, type FormEvent } from "react"
import { useNavigate } from "react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AnimatePresence, motion } from "motion/react"
import { ArrowRight, ArrowsClockwise, CaretDown, Plus, ShieldCheck, Tray, Info } from "@phosphor-icons/react"
import { api, type EventStatus, type InboxItem } from "@/lib/api"
import { eventTypeLabel, plural, repoName, timeAgo } from "@/lib/format"
import { ease, stagger } from "@/lib/motion"
import { cn } from "@/lib/utils"
import { useTrackedJob } from "@/hooks/useJob"
import { PageHeader } from "@/components/PageHeader"
import { Schematic } from "@/components/Schematic"
import { Button } from "@/components/ui/button"
import { Modal } from "@/components/ui/modal"
import { FieldLabel, Panel, Pill, StatusDot, Tag, inputClass, ratingBar } from "@/components/ui/primitives"
import { errorText, toast } from "@/components/ui/toast"
import keyboard from "@/assets/img/drafting-keyboard.webp"

const views: { status: EventStatus | undefined; label: string }[] = [
  { status: undefined, label: "To review" },
  { status: "dismissed", label: "Dismissed" },
  { status: "posted", label: "Posted" },
]

const gateRules = [
  { title: "Chore-only pushes score zero", body: "Merges, dependency bumps, docs-only and noise commits on their own never pass." },
  { title: "Some events always pass", body: "New repositories, releases, commits marked [post] and manual ranges skip the gate." },
  { title: "Signals add up", body: "feat: commits +2, new files up to +3, a README change +1, plus the size of the meaningful diff." },
  { title: "Gate at 2.0", body: "Anything that scores 2.0 or more goes to AI triage, which rates it high, medium or low." },
]

export default function Inbox() {
  const [view, setView] = useState(0)
  const [repoFilter, setRepoFilter] = useState<string | null>(null)
  const [manualOpen, setManualOpen] = useState(false)
  const [lowOpen, setLowOpen] = useState(false)
  const queryClient = useQueryClient()
  const status = views[view].status
  const inbox = useQuery({ queryKey: ["inbox", status ?? "actionable"], queryFn: () => api.inbox({ status, limit: 200 }) })

  const sync = useMutation({
    mutationFn: api.syncRepos,
    onSuccess: (r) => {
      toast(`Synced ${plural(r.synced, "repository")} from GitHub`)
      void queryClient.invalidateQueries({ queryKey: ["repos"] })
    },
    onError: (e) => toast(errorText(e), "error"),
  })

  const items = useMemo(() => inbox.data?.items ?? [], [inbox.data])
  const repos = useMemo(() => {
    const counts = new Map<string, { n: number; best: InboxItem["rating"] }>()
    for (const it of items) {
      const c = counts.get(it.repo_full_name) ?? { n: 0, best: null }
      counts.set(it.repo_full_name, { n: c.n + 1, best: c.best ?? it.rating })
    }
    return [...counts.entries()]
  }, [items])
  const visible = items.filter((i) => !repoFilter || i.repo_full_name === repoFilter)
  const main = visible.filter((i) => i.rating !== "low")
  const low = visible.filter((i) => i.rating === "low")

  return (
    <div className="flex flex-col gap-8">
      <div>
        <PageHeader
          stage="Stage 01 // Triage inbox"
          meta={
            <Tag tone="plain" className="label-badge">
              <StatusDot tone="high" live /> {inbox.isPending ? "loading" : plural(items.length, "candidate")}
            </Tag>
          }
          title="Candidate events"
          description="New GitHub activity, filtered by the heuristic gate and rated by AI triage. Draft the ones with an outcome you can show."
          actions={
            <>
              <Button size="lg" onClick={() => setManualOpen(true)}>
                <Plus weight="bold" aria-hidden /> Draft a commit range
              </Button>
              <Button variant="quiet" size="lg" className="h-14 font-mono text-label" onClick={() => sync.mutate()} disabled={sync.isPending}>
                <ArrowsClockwise weight="bold" className={cn(sync.isPending && "animate-spin")} aria-hidden />
                {sync.isPending ? "Syncing" : "Sync repositories"}
              </Button>
            </>
          }
        />

        <div className="flex flex-col gap-3 bg-surface-low p-3 sm:p-4">
          <div className="flex flex-wrap gap-2" role="group" aria-label="Which events">
            {views.map((v, i) => (
              <Pill key={v.label} active={view === i} onClick={() => setView(i)}>
                {v.label}
              </Pill>
            ))}
          </div>
          {repos.length > 0 && (
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by repository">
              <RepoChip active={!repoFilter} onClick={() => setRepoFilter(null)}>
                All repositories [{items.length}]
              </RepoChip>
              {repos.map(([name, { n, best }]) => (
                <RepoChip key={name} active={repoFilter === name} onClick={() => setRepoFilter(repoFilter === name ? null : name)}>
                  {best && <StatusDot tone={best} />} {repoName(name)} <span className="text-ink-muted">[{n}]</span>
                </RepoChip>
              ))}
            </div>
          )}
          <p className="font-mono text-badge text-ink-muted uppercase">
            Sorted by: <span className="font-bold text-ink">rating, then newest</span>
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,8fr)_minmax(0,4fr)]">
        <section aria-label="Events" className="flex flex-col gap-6">
          {inbox.isPending && <SkeletonCards />}
          {inbox.isError && (
            <Panel className="p-6 text-body-lg text-danger">The inbox didn't load: {errorText(inbox.error)}</Panel>
          )}
          {inbox.isSuccess && visible.length === 0 && <EmptyInbox view={views[view].label} onManual={() => setManualOpen(true)} />}

          <AnimatePresence initial={false} mode="popLayout">
            {main.map((item, i) => (
              <EventCard key={item.id} item={item} index={i} />
            ))}
          </AnimatePresence>

          {low.length > 0 && (
            <motion.div layout className="bg-surface">
              <button
                type="button"
                aria-expanded={lowOpen}
                onClick={() => setLowOpen((o) => !o)}
                className="flex w-full cursor-pointer items-center gap-3 p-4 text-left font-mono text-body text-ink-soft uppercase"
              >
                <CaretDown weight="bold" className={cn("size-4 shrink-0 transition-transform duration-300", lowOpen && "rotate-180")} />
                // Low-priority activity: {plural(low.length, "event")}
              </button>
              <AnimatePresence initial={false}>
                {lowOpen && (
                  <motion.div
                    initial={{ opacity: 0, y: -8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.3, ease: ease.expo }}
                    className="flex flex-col gap-4 p-4 pt-0"
                  >
                    {low.map((item, i) => (
                      <EventCard key={item.id} item={item} index={i} />
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          )}
        </section>

        <aside className="flex flex-col gap-6" aria-label="How triage works">
          <Panel className="relative p-5">
            <div className="flex items-start justify-between gap-3">
              <h2 className="label-technical text-cobalt">Heuristic pipeline</h2>
              <Tag tone="high">2 stages</Tag>
            </div>
            <div className="bg-blueprint mt-4 border border-cobalt/10 p-3">
              <Schematic />
            </div>
            <p className="mt-4 text-body text-ink-soft">
              The free heuristic gate runs first. Only what passes costs a model call.
            </p>
          </Panel>

          <Panel className="p-5">
            <h2 className="label-technical flex items-center gap-2 text-ink">
              <ShieldCheck weight="bold" className="text-cobalt" aria-hidden /> Gate rules
            </h2>
            <motion.ul variants={stagger.container} initial="hidden" whileInView="show" viewport={{ once: true }} className="mt-4 flex flex-col gap-2">
              {gateRules.map((r) => (
                <motion.li variants={stagger.item} key={r.title} className="bg-surface-low px-3 py-2.5">
                  <p className="font-mono text-label font-bold text-ink uppercase">{r.title}</p>
                  <p className="mt-0.5 text-body text-ink-soft">{r.body}</p>
                </motion.li>
              ))}
            </motion.ul>
          </Panel>

          <Panel className="overflow-hidden">
            <img src={keyboard} alt="Mechanical keyboard with a glowing keycap on a drafting table" className="aspect-[16/10] w-full object-cover" loading="lazy" />
            <p className="p-4 text-body text-ink-soft">
              Put <code className="bg-surface-low px-1 font-mono text-code">[post]</code> in a commit message to send it straight past the gate.
            </p>
          </Panel>
        </aside>
      </div>

      <ManualTriggerModal open={manualOpen} onOpenChange={setManualOpen} />
    </div>
  )
}

function RepoChip({ active, className, ...props }: React.ComponentProps<"button"> & { active: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={cn(
        "flex cursor-pointer items-center gap-2 px-3 py-1.5 font-mono text-label uppercase transition-transform duration-200 active:translate-y-px",
        active ? "bg-ink text-paper" : "bg-paper text-ink hover:-translate-y-0.5",
        className,
      )}
      {...props}
    />
  )
}

function EventCard({ item, index }: { item: InboxItem; index: number }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const drafting = useTrackedJob((job) => {
    void queryClient.invalidateQueries({ queryKey: ["inbox"] })
    if (job.status === "done") {
      toast("Drafts ready")
      navigate(`/app/review/${item.id}`)
    } else {
      toast(job.error ? `Drafting failed: ${job.error}` : "Drafting failed. Try again in a minute.", "error")
    }
  })
  const rating = item.rating ?? "low"

  const generate = useMutation({
    mutationFn: () => api.generate(item.id),
    onSuccess: (r) => drafting.start(r.job_id),
    onError: (e) => toast(errorText(e), "error"),
  })
  const dismiss = useMutation({
    mutationFn: () => api.dismissEvent(item.id),
    onSuccess: () => {
      toast(`Dismissed ${repoName(item.repo_full_name)}`)
      void queryClient.invalidateQueries({ queryKey: ["inbox"] })
    },
    onError: (e) => toast(errorText(e), "error"),
  })

  const working = generate.isPending || drafting.running || item.status === "generating"
  const drafted = item.status === "drafted"

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0, transition: { duration: 0.6, ease: ease.expo, delay: Math.min(index, 6) * 0.06 } }}
      exit={{ opacity: 0, x: 80, transition: { duration: 0.35, ease: ease.expo } }}
      className="relative overflow-hidden bg-paper ring-1 ring-schematic/70"
    >
      <span aria-hidden className={cn("absolute inset-y-0 left-0 w-1.5", ratingBar[rating])} />
      <div className="flex flex-col gap-4 p-5 pl-7 sm:p-7 sm:pl-9">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-label uppercase">
          <span className="font-bold text-ink">Repo:</span>
          <Tag tone="cobalt" className="text-label normal-case">{item.repo_full_name}</Tag>
          <span aria-hidden className="text-ink-muted">•</span>
          <span className="font-bold text-ink">{eventTypeLabel[item.type]}</span>
          <span aria-hidden className="text-ink-muted">•</span>
          <span className="text-ink-muted">{timeAgo(item.created_at)}</span>
        </p>

        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-display text-headline-md text-ink">{item.stats_summary || eventTypeLabel[item.type]}</h2>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span
            className={cn(
              "label-technical inline-flex items-center gap-2 px-3 py-1.5",
              rating === "high" ? "bg-cobalt text-paper" : rating === "medium" ? "bg-signal-medium/15 text-signal-medium-ink" : "bg-surface text-ink-soft",
            )}
          >
            Rating: {item.rating ?? "unrated"}
          </span>
          <span className="font-mono text-badge text-ink-muted">heuristic score {item.heuristic_score.toFixed(1)}</span>
          {drafted && <Tag tone="high">drafts ready</Tag>}
        </div>

        {item.rating_reason && (
          <p className="flex gap-3 bg-surface-low p-4 text-body-lg text-ink">
            <Info weight="bold" className={cn("mt-1 size-4 shrink-0", rating === "medium" ? "text-signal-medium" : "text-cobalt")} aria-hidden />
            <span>
              <strong className={cn("font-semibold", rating === "medium" ? "text-signal-medium-ink" : "text-cobalt")}>Why: </strong>
              {item.rating_reason}
            </span>
          </p>
        )}

        <AnimatePresence>
          {working && (
            <motion.div
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="relative overflow-hidden bg-ink px-4 py-3 font-mono text-label text-cobalt-tint uppercase"
              role="status"
            >
              {drafting.status === "running" ? "Writing three variants from the evidence" : "Queued for drafting"}
              <motion.span
                aria-hidden
                className="absolute bottom-0 left-0 h-0.5 w-1/3 bg-sky"
                initial={{ x: "-100%" }}
                animate={{ x: "300%" }}
                transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
              />
            </motion.div>
          )}
        </AnimatePresence>

        <div className="flex flex-wrap items-center gap-3">
          {drafted ? (
            <Button onClick={() => navigate(`/app/review/${item.id}`)}>
              Open the drafts <ArrowRight weight="bold" aria-hidden />
            </Button>
          ) : (
            <Button variant={rating === "high" ? "primary" : "secondary"} onClick={() => generate.mutate()} disabled={working}>
              {working ? "Drafting" : "Generate post draft"} <ArrowRight weight="bold" aria-hidden />
            </Button>
          )}
          {item.status !== "dismissed" && item.status !== "posted" && (
            <Button variant="ghost" className="ml-auto" onClick={() => dismiss.mutate()} disabled={dismiss.isPending || working}>
              <Tray weight="bold" aria-hidden /> Dismiss
            </Button>
          )}
        </div>
      </div>
    </motion.article>
  )
}

function SkeletonCards() {
  return (
    <>
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-56 animate-pulse bg-paper ring-1 ring-schematic/50" />
      ))}
    </>
  )
}

function EmptyInbox({ view, onManual }: { view: string; onManual: () => void }) {
  return (
    <Panel className="flex flex-col items-start gap-4 p-8">
      <h2 className="font-display text-headline-lg text-ink">Nothing in “{view.toLowerCase()}”</h2>
      <p className="max-w-lg text-body-lg text-ink-soft">
        New commits, pull requests and releases show up here after the next poll. To draft something right now, pick a repository and a commit range.
      </p>
      <Button onClick={onManual}>
        <Plus weight="bold" aria-hidden /> Draft a commit range
      </Button>
    </Panel>
  )
}

function ManualTriggerModal({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const queryClient = useQueryClient()
  const repos = useQuery({ queryKey: ["repos"], queryFn: api.repos, enabled: open })
  const [repoId, setRepoId] = useState("")
  const [base, setBase] = useState("")
  const [head, setHead] = useState("")

  const create = useMutation({
    mutationFn: () => api.createManualEvent(repoId, { base_sha: base.trim() || undefined, head_sha: head.trim() || undefined }),
    onSuccess: () => {
      toast("Commit range added to the inbox")
      void queryClient.invalidateQueries({ queryKey: ["inbox"] })
      onOpenChange(false)
      setBase("")
      setHead("")
    },
  })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (repoId) create.mutate()
  }

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Draft a commit range"
      description="Pick a repository and the commits to cover. Leave both SHAs empty to use the latest changes."
    >
      <form onSubmit={submit} className="flex flex-col gap-5">
        <div>
          <FieldLabel htmlFor="manual-repo">Repository</FieldLabel>
          <select id="manual-repo" required value={repoId} onChange={(e) => setRepoId(e.target.value)} className={cn(inputClass, "font-mono text-body")}>
            <option value="">{repos.isPending ? "Loading repositories" : "Choose a repository"}</option>
            {repos.data?.map((r) => (
              <option key={r.id} value={r.id}>
                {r.full_name}
              </option>
            ))}
          </select>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <FieldLabel htmlFor="manual-base">From (older SHA)</FieldLabel>
            <input id="manual-base" value={base} onChange={(e) => setBase(e.target.value)} placeholder="e.g. 4a81b2c" pattern="[0-9a-fA-F]{4,64}" className={cn(inputClass, "font-mono")} />
          </div>
          <div>
            <FieldLabel htmlFor="manual-head">To (newer SHA)</FieldLabel>
            <input id="manual-head" value={head} onChange={(e) => setHead(e.target.value)} placeholder="e.g. 7b4e99c" pattern="[0-9a-fA-F]{4,64}" className={cn(inputClass, "font-mono")} />
          </div>
        </div>
        {create.isError && <p role="alert" className="border-l-4 border-unsupported bg-danger-tint/50 px-3 py-2 text-body text-danger">{errorText(create.error)}</p>}
        <div className="flex justify-end gap-3">
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" disabled={!repoId || create.isPending}>
            {create.isPending ? "Adding" : "Add to the inbox"}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
