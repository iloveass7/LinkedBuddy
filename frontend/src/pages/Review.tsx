import { useMemo, useState } from "react"
import { Link, useNavigate, useParams } from "react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AnimatePresence, motion } from "motion/react"
import {
  ArrowRight,
  ArrowsClockwise,
  CheckCircle,
  Copy,
  Eye,
  FloppyDisk,
  Info,
  PaperPlaneTilt,
  PencilSimpleLine,
  SealCheck,
  ShieldWarning,
} from "@phosphor-icons/react"
import { api, ApiError, type Draft, type QuickEditPreset, type Tone } from "@/lib/api"
import { plural, repoName, timeAgo, toneLabel } from "@/lib/format"
import { ease, stagger } from "@/lib/motion"
import { cn } from "@/lib/utils"
import { useTrackedJob } from "@/hooks/useJob"
import { meQuery } from "@/components/RequireAuth"
import { Button } from "@/components/ui/button"
import { Panel, Pill, StatusDot, Tag, inputClass } from "@/components/ui/primitives"
import { errorText, toast } from "@/components/ui/toast"
import { MediaTray } from "@/components/review/MediaTray"
import { LinkedInPreview } from "@/components/review/LinkedInPreview"

const MAX_CHARS = 3000
const tones: Tone[] = ["professional", "casual", "technical", "storytelling"]
const quickEdits: { preset: QuickEditPreset; label: string }[] = [
  { preset: "shorter", label: "Make it shorter" },
  { preset: "more_casual", label: "More casual" },
  { preset: "less_buzzwordy", label: "Fewer buzzwords" },
]

// Quick-edit calls Gemini inline, so its 503/504 reach the owner directly.
function quickEditErrorText(error: unknown) {
  if (error instanceof ApiError && error.status === 503) {
    if (error.detail === "ai not configured") return "AI isn't configured. Add a Gemini key on the backend."
    return error.retryAfter
      ? `Gemini is rate limited. Try again in ${plural(error.retryAfter, "second")}.`
      : "Gemini is rate limited. Try again in a minute."
  }
  if (error instanceof ApiError && error.status === 504) return "Gemini took too long to answer. Try again."
  return errorText(error)
}

export default function Review() {
  const { eventId } = useParams()
  return eventId ? <Studio eventId={eventId} /> : <DraftPicker />
}

/* ---------------- No event chosen: list events with drafts ---------------- */

function DraftPicker() {
  const drafted = useQuery({ queryKey: ["inbox", "drafted"], queryFn: () => api.inbox({ status: "drafted" }) })
  const items = drafted.data?.items ?? []
  return (
    <div className="flex flex-col gap-8">
      <section className="border-b-2 border-ink bg-paper p-5 sm:p-8">
        <p className="label-technical flex items-center gap-2 text-cobalt">
          <span aria-hidden className="size-2.5 bg-cobalt" /> Stage 02 // Review studio
        </p>
        <h1 className="mt-3 font-display text-headline-xl-sm text-ink sm:text-headline-xl">Pick a draft to review</h1>
        <p className="mt-2 max-w-2xl text-body-lg text-ink-soft">Events with drafts ready. Generate drafts from the triage inbox.</p>
      </section>
      {drafted.isPending && <div className="h-40 animate-pulse bg-paper" />}
      {drafted.isSuccess && items.length === 0 && (
        <Panel className="flex flex-col items-start gap-4 p-8">
          <h2 className="font-display text-headline-lg text-ink">No drafts waiting</h2>
          <p className="max-w-lg text-body-lg text-ink-soft">Open the inbox, choose an event with an outcome you can show, and generate drafts for it.</p>
          <Button asChild>
            <Link to="/app/inbox">
              Open the inbox <ArrowRight weight="bold" aria-hidden />
            </Link>
          </Button>
        </Panel>
      )}
      <motion.ul variants={stagger.container} initial="hidden" animate="show" className="grid gap-4 md:grid-cols-2">
        {items.map((it) => (
          <motion.li key={it.id} variants={stagger.item}>
            <Link
              to={`/app/review/${it.id}`}
              className="group flex h-full flex-col gap-3 border-[1.5px] border-ink bg-paper p-5 shadow-hard transition-transform duration-200 hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-hard-lift"
            >
              <Tag tone="cobalt" className="self-start">{it.repo_full_name}</Tag>
              <span className="font-display text-headline-md text-ink">{it.stats_summary}</span>
              <span className="mt-auto flex items-center justify-between font-mono text-label text-ink-muted uppercase">
                {timeAgo(it.created_at)}
                <ArrowRight weight="bold" className="text-cobalt transition-transform group-hover:translate-x-1" aria-hidden />
              </span>
            </Link>
          </motion.li>
        ))}
      </motion.ul>
    </div>
  )
}

/* ---------------- The studio ---------------- */

function Studio({ eventId }: { eventId: string }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const me = useQuery(meQuery)
  const drafts = useQuery({ queryKey: ["drafts", eventId], queryFn: () => api.eventDrafts(eventId) })
  const inbox = useQuery({ queryKey: ["inbox", "actionable"], queryFn: () => api.inbox({ limit: 200 }) })
  const event = inbox.data?.items.find((i) => i.id === eventId)

  const live = useMemo(() => (drafts.data ?? []).filter((d) => d.status !== "discarded"), [drafts.data])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const draft = live.find((d) => d.id === selectedId) ?? live[0]

  const [mode, setMode] = useState<"evidence" | "edit">("evidence")
  // Local edits per variant; a variant without an entry shows its saved body.
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [toneChoice, setToneChoice] = useState<Partial<Record<string, Tone>>>({})
  const [instruction, setInstruction] = useState("")
  const [preview, setPreview] = useState(false)

  const text = draft ? (edits[draft.id] ?? draft.body) : ""
  const setText = (value: string) => draft && setEdits((e) => ({ ...e, [draft.id]: value }))
  const tone: Tone = draft ? (toneChoice[draft.id] ?? draft.tone) : "technical"
  const setTone = (value: Tone) => draft && setToneChoice((t) => ({ ...t, [draft.id]: value }))
  const dirty = draft !== undefined && text !== draft.body
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["drafts", eventId] })

  const rewrite = useTrackedJob(async (job) => {
    await refresh()
    if (job.status === "done") {
      const fresh = queryClient.getQueryData<Draft[]>(["drafts", eventId]) ?? []
      const newest = fresh.filter((d) => d.status !== "discarded").at(-1)
      if (newest) setSelectedId(newest.id)
      toast("New variant ready")
    } else {
      toast(job.error ? `The rewrite failed: ${job.error}` : "The rewrite failed. Try again in a minute.", "error")
    }
  })

  const regenerate = useMutation({
    mutationFn: () => api.regenerate(draft!.id, tone),
    onSuccess: (r) => rewrite.start(r.job_id),
    onError: (e) => toast(errorText(e), "error"),
  })
  const quickEdit = useMutation({
    mutationFn: (i: QuickEditPreset | string) => api.quickEdit(draft!.id, i),
    // The backend answers inline with the new child draft; the list is newest first.
    onSuccess: (child) => {
      queryClient.setQueryData<Draft[]>(["drafts", eventId], (old) => [child, ...(old ?? []).filter((d) => d.id !== child.id)])
      void refresh()
      setSelectedId(child.id)
      setInstruction("")
      toast("New variant ready")
    },
    onError: (e) => toast(quickEditErrorText(e), "error"),
  })
  const save = useMutation({
    mutationFn: () => api.patchDraft(draft!.id, { body: text }),
    onSuccess: (saved) => {
      toast("Draft saved")
      setEdits((e) => {
        const next = { ...e }
        delete next[saved.id]
        return next
      })
      void refresh()
    },
    onError: (e) => toast(errorText(e), "error"),
  })
  const setStatus = useMutation({
    mutationFn: (status: "approved" | "draft" | "discarded") => api.patchDraft(draft!.id, { status, ...(dirty ? { body: text } : {}) }),
    onSuccess: (d) => {
      toast(d.status === "approved" ? "Draft approved" : d.status === "discarded" ? "Variant discarded" : "Approval removed")
      void refresh()
    },
    onError: (e) => toast(errorText(e), "error"),
  })
  const publish = useMutation({
    mutationFn: () => api.publish(draft!.id, text),
    onSuccess: () => {
      toast("Published. It's in the archive now.")
      void queryClient.invalidateQueries({ queryKey: ["inbox"] })
      void queryClient.invalidateQueries({ queryKey: ["posts"] })
      navigate("/app/archive")
    },
    onError: (e) => toast(errorText(e), "error"),
  })

  const copy = async (value: string, what: string) => {
    await navigator.clipboard.writeText(value)
    toast(`${what} copied`)
  }

  if (drafts.isPending) return <div className="h-96 animate-pulse bg-paper" />
  if (drafts.isError) return <Panel className="p-8 text-body-lg text-danger">The drafts didn't load: {errorText(drafts.error)}</Panel>
  if (!draft) {
    return (
      <Panel className="flex flex-col items-start gap-4 p-8">
        <h1 className="font-display text-headline-lg text-ink">No drafts for this event yet</h1>
        <p className="text-body-lg text-ink-soft">Generate drafts from the inbox. They show up here when the job finishes.</p>
        <Button asChild>
          <Link to="/app/inbox">Back to the inbox</Link>
        </Button>
      </Panel>
    )
  }

  const segments = draft.segments
  const supported = segments.filter((s) => s.supported).length
  const words = text.trim() ? text.trim().split(/\s+/).length : 0
  const chars = text.length
  const busy = rewrite.running || regenerate.isPending || quickEdit.isPending
  const approved = draft.status === "approved"

  return (
    <div className="flex flex-col gap-8">
      {/* Stage header */}
      <motion.section
        variants={stagger.container}
        initial="hidden"
        animate="show"
        className="flex flex-col gap-5 border-b-2 border-ink bg-paper p-5 sm:p-8 lg:flex-row lg:items-start lg:justify-between"
      >
        <div className="min-w-0">
          <motion.p variants={stagger.item} className="flex flex-wrap items-center gap-x-3 gap-y-2 font-display text-title tracking-[0.02em] uppercase">
            <span className="text-cobalt">Stage 02 // Review studio</span>
            <span aria-hidden className="text-ink-muted">•</span>
            <span className="text-ink">Repo: {event ? repoName(event.repo_full_name) : "loading"}</span>
            {event && <Tag tone="cobalt" className="text-label">{event.stats_summary}</Tag>}
          </motion.p>
          <motion.h1 variants={stagger.item} className="sr-only">
            Review drafts for {event?.repo_full_name ?? "this event"}
          </motion.h1>
          <motion.p variants={stagger.item} className="mt-3 flex flex-wrap items-center gap-3 font-mono text-label text-ink uppercase">
            <span className="flex items-center gap-2">
              <StatusDot tone={approved ? "high" : "cobalt"} live /> Variant {draft.variant_index + 1} · {draft.status}
            </span>
            <Tag tone={draft.unsupported_claims ? "danger" : "high"} className="text-label">
              {draft.unsupported_claims ? plural(draft.unsupported_claims, "unverified claim") : "0 unverified claims"}
            </Tag>
          </motion.p>
        </div>
        <motion.div variants={stagger.item} className="flex flex-wrap gap-3">
          <Button variant="quiet" className="font-mono text-label" onClick={() => regenerate.mutate()} disabled={busy}>
            <ArrowsClockwise weight="bold" className={cn(busy && "animate-spin")} aria-hidden /> {busy ? "Rewriting" : "Regenerate"}
          </Button>
          <Button variant="quiet" className="font-mono text-label" onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
            <FloppyDisk weight="bold" aria-hidden /> {save.isPending ? "Saving" : "Save edits"}
          </Button>
          <Button onClick={() => setPreview(true)}>
            <Eye weight="bold" aria-hidden /> Preview in feed
          </Button>
        </motion.div>
      </motion.section>

      {/* Variant tabs */}
      {live.length > 1 && (
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Variants">
          {live.map((d) => (
            <button
              key={d.id}
              type="button"
              role="tab"
              aria-selected={d.id === draft.id}
              onClick={() => setSelectedId(d.id)}
              className={cn(
                "relative cursor-pointer px-4 py-2.5 text-left transition-transform duration-200 hover:-translate-y-0.5",
                d.id === draft.id ? "text-paper" : "bg-paper text-ink ring-1 ring-schematic",
              )}
            >
              {d.id === draft.id && <motion.span layoutId="variant-tab" className="absolute inset-0 bg-ink" transition={{ duration: 0.4, ease: ease.expo }} />}
              <span className="relative block font-mono text-badge uppercase opacity-70">
                Variant {d.variant_index + 1} · {toneLabel[d.tone]}
              </span>
              <span className="relative block text-body font-medium">{d.angle}</span>
            </button>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <div className="flex flex-col gap-6">
          {/* Tone engine */}
          <Panel className="p-5">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 className="label-technical text-ink">Tone engine // refinement</h2>
              <span className="font-mono text-badge text-cobalt uppercase">{draft.specificity_count} specifics cited</span>
            </div>
            <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="Tone">
              {tones.map((t) => (
                <Pill key={t} active={tone === t} onClick={() => setTone(t)}>
                  {toneLabel[t]}
                </Pill>
              ))}
              <Button size="sm" variant="ghost" onClick={() => regenerate.mutate()} disabled={busy} className="text-label">
                Rewrite in this tone <ArrowRight weight="bold" aria-hidden />
              </Button>
            </div>
            <form
              className="mt-4 flex gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                if (instruction.trim()) quickEdit.mutate(instruction.trim())
              }}
            >
              <input
                aria-label="Instruction for the rewrite"
                value={instruction}
                maxLength={200}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder="Sound like the engineer who shipped the fix, not a marketer"
                className={cn(inputClass, "border-[1.5px] text-body")}
              />
              <Button type="submit" size="md" variant="secondary" disabled={!instruction.trim() || busy} className="shrink-0 font-mono text-label shadow-none">
                Apply
              </Button>
            </form>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="font-mono text-label text-ink-muted uppercase">Refine:</span>
              {quickEdits.map((q) => (
                <Button key={q.preset} size="sm" variant="quiet" onClick={() => quickEdit.mutate(q.preset)} disabled={busy}>
                  {q.label}
                </Button>
              ))}
            </div>
            <AnimatePresence>{busy && <WorkingBar label="Writing a new variant from the same evidence" />}</AnimatePresence>
          </Panel>

          {/* Editor */}
          <Panel className="overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-3 bg-surface-low px-5 py-3">
              <h2 className="label-technical flex items-center gap-2 text-ink">
                <StatusDot tone="high" live /> LinkedIn draft // live
              </h2>
              <div className="flex items-center gap-3">
                <div className="flex bg-paper p-0.5 ring-1 ring-schematic" role="group" aria-label="Editor view">
                  {(["evidence", "edit"] as const).map((m) => (
                    <button
                      key={m}
                      type="button"
                      aria-pressed={mode === m}
                      onClick={() => setMode(m)}
                      className={cn("relative cursor-pointer px-3 py-1 font-mono text-badge uppercase", mode === m ? "text-paper" : "text-ink-soft")}
                    >
                      {mode === m && <motion.span layoutId="editor-mode" className="absolute inset-0 bg-cobalt" transition={{ duration: 0.3, ease: ease.expo }} />}
                      <span className="relative">{m === "evidence" ? "Evidence" : "Edit text"}</span>
                    </button>
                  ))}
                </div>
                <span className={cn("font-display text-title tabular-nums", chars > MAX_CHARS ? "text-danger" : "text-ink")}>
                  {chars.toLocaleString()} / {MAX_CHARS.toLocaleString()}
                </span>
              </div>
            </div>

            <AnimatePresence mode="wait" initial={false}>
              {mode === "evidence" && !dirty ? (
                <motion.ol
                  key="evidence"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="flex flex-col gap-3 p-5"
                >
                  {segments.map((s, i) => (
                    <motion.li
                      key={`${draft.id}-${i}`}
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.4, ease: ease.expo, delay: i * 0.05 }}
                      className={cn("p-3 text-body-lg", s.supported ? "bg-surface-low text-ink" : "bg-danger-tint/30 text-ink")}
                    >
                      <span className={cn(!s.supported && "decoration-unsupported underline decoration-dashed decoration-2 underline-offset-4")}>{s.text}</span>{" "}
                      {s.supported ? (
                        s.evidence.map((e) => (
                          <Tag key={e} tone="cobalt" className="mt-1 align-middle">
                            <SealCheck weight="bold" aria-hidden /> {e}
                          </Tag>
                        ))
                      ) : (
                        <Tag tone="danger" className="mt-1 align-middle">
                          <ShieldWarning weight="bold" aria-hidden /> no evidence: cut it or back it up
                        </Tag>
                      )}
                    </motion.li>
                  ))}
                </motion.ol>
              ) : (
                <motion.div key="edit" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }} className="p-5">
                  {dirty && mode === "evidence" && (
                    <p className="mb-3 flex items-center gap-2 bg-signal-medium/10 px-3 py-2 text-body text-signal-medium-ink">
                      <Info weight="bold" aria-hidden /> You have unsaved edits. Evidence links show again after you save and regenerate.
                    </p>
                  )}
                  <label htmlFor="draft-body" className="sr-only">
                    Post text
                  </label>
                  <textarea
                    id="draft-body"
                    data-lenis-prevent
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    rows={14}
                    className={cn(inputClass, "leading-relaxed")}
                  />
                </motion.div>
              )}
            </AnimatePresence>

            <p className="flex flex-wrap gap-x-4 gap-y-1 border-t border-schematic px-5 py-3 font-mono text-label text-ink-muted uppercase">
              <span>Words: {words}</span>
              <span>Reading time: {Math.max(1, Math.round(words / 3.6))}s</span>
              <span className={supported === segments.length ? "text-signal-high-ink" : "text-signal-medium-ink"}>
                Evidence ratio: {segments.length ? Math.round((supported / segments.length) * 100) : 0}%
              </span>
            </p>
          </Panel>

          <AuditPanel draft={draft} />

          {draft.alt_openings.length > 0 && (
            <Panel className="p-5">
              <h2 className="label-technical text-ink">Other ways to open</h2>
              <ul className="mt-3 flex flex-col gap-2">
                {draft.alt_openings.map((o) => (
                  <li key={o} className="flex items-start justify-between gap-3 bg-surface-low p-3">
                    <span className="text-body text-ink">{o}</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="shrink-0"
                      onClick={() => {
                        const first = segments[0]?.text ?? ""
                        setText(first && text.startsWith(first) ? o + text.slice(first.length) : `${o}\n\n${text}`)
                        setMode("edit")
                      }}
                    >
                      <PencilSimpleLine weight="bold" aria-hidden /> Use it
                    </Button>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>

        <div className="flex flex-col gap-6">
          <MediaTray draft={draft} videoEnabled={me.data?.brag_configured ?? false} />

          {draft.first_comment && (
            <Panel className="p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="label-technical text-ink">First comment</h2>
                <span className="font-mono text-badge text-signal-high-ink uppercase">links go here, not in the post</span>
              </div>
              <p className="mt-3 text-body text-ink-soft">LinkedIn shows fewer people posts with outbound links. Put the repo link in the first comment.</p>
              <p className="mt-3 bg-surface-low p-3 font-mono text-code text-ink">{draft.first_comment}</p>
              <Button size="sm" variant="ghost" className="mt-2 px-0" onClick={() => copy(draft.first_comment!, "First comment")}>
                <Copy weight="bold" aria-hidden /> Copy first comment
              </Button>
            </Panel>
          )}

          {/* Publish bar */}
          <section aria-label="Publish" className="border-2 border-ink bg-midnight p-5 text-paper shadow-hard-cobalt">
            <p className="label-technical flex items-center gap-2">
              <StatusDot tone={approved ? "high" : "medium"} live />
              {approved ? "Approved. Post it on LinkedIn, then mark it published." : "Approve the draft when every claim checks out."}
            </p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <Button variant="secondary" onClick={() => copy(text, "Draft")} className="shadow-none">
                <Copy weight="bold" aria-hidden /> Copy draft
              </Button>
              {approved ? (
                <Button onClick={() => publish.mutate()} disabled={publish.isPending || chars > MAX_CHARS}>
                  <PaperPlaneTilt weight="bold" aria-hidden /> {publish.isPending ? "Saving" : "Mark as published"}
                </Button>
              ) : (
                <Button onClick={() => setStatus.mutate("approved")} disabled={setStatus.isPending || chars > MAX_CHARS || !text.trim()}>
                  <CheckCircle weight="bold" aria-hidden /> Approve draft
                </Button>
              )}
            </div>
            <div className="mt-3 flex justify-between gap-3">
              {approved && (
                <button type="button" onClick={() => setStatus.mutate("draft")} className="cursor-pointer font-mono text-badge text-cobalt-tint uppercase hover:underline">
                  Undo approval
                </button>
              )}
              <button type="button" onClick={() => setStatus.mutate("discarded")} className="ml-auto cursor-pointer font-mono text-badge text-ink-muted uppercase hover:text-unsupported">
                Discard this variant
              </button>
            </div>
          </section>
        </div>
      </div>

      <LinkedInPreview open={preview} onOpenChange={setPreview} body={text} media={draft.media} />
    </div>
  )
}

function WorkingBar({ label }: { label: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      role="status"
      className="relative mt-4 overflow-hidden bg-ink px-4 py-3 font-mono text-label text-cobalt-tint uppercase"
    >
      {label}
      <motion.span
        aria-hidden
        className="absolute bottom-0 left-0 h-0.5 w-1/3 bg-sky"
        initial={{ x: "-100%" }}
        animate={{ x: "300%" }}
        transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
      />
    </motion.div>
  )
}

function AuditPanel({ draft }: { draft: Draft }) {
  const banned = draft.lint.banned_hits ?? []
  const buzz = draft.lint.buzzword_count ?? 0
  const checks = [
    { value: draft.specificity_count, label: "Specific facts", note: "numbers, names, files" },
    { value: draft.unsupported_claims, label: "Unbacked claims", note: draft.unsupported_claims ? "fix before posting" : "every line cites evidence", bad: draft.unsupported_claims > 0 },
    { value: buzz, label: "Buzzwords", note: buzz ? "worth a rewrite" : "none found", bad: buzz > 0 },
  ]
  const score = 3 - checks.filter((c) => c.bad).length - (banned.length ? 1 : 0)

  return (
    <Panel className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="label-technical flex items-center gap-2 text-ink">
          <SealCheck weight="bold" className="text-signal-high" aria-hidden /> Authenticity audit
        </h2>
        <Tag tone={score >= 3 ? "cobalt" : "medium"} className="text-label">
          {Math.max(0, score)} / 3 checks passed
        </Tag>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {checks.map((c) => (
          <div key={c.label} className="bg-surface-low p-4">
            <p className={cn("font-display text-headline-md", c.bad ? "text-danger" : "text-cobalt")}>{c.value}</p>
            <p className="label-technical mt-1 text-ink">{c.label}</p>
            <p className="mt-1 text-body text-ink-soft">{c.note}</p>
          </div>
        ))}
      </div>
      <div className="mt-4 bg-surface p-4">
        <p className="label-technical flex justify-between gap-3 text-ink">
          Banned phrase monitor
          <span className={banned.length ? "text-danger" : "text-signal-high-ink"}>{plural(banned.length, "hit")}</span>
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          {banned.length ? (
            banned.map((b) => (
              <Tag key={b} tone="danger" className="line-through">
                "{b}"
              </Tag>
            ))
          ) : (
            <span className="text-body text-ink-soft">None of your banned phrases appear in this draft.</span>
          )}
        </div>
      </div>
      <p className="mt-4 flex gap-3 bg-cobalt-mist p-4 text-body text-ink">
        <Info weight="bold" className="mt-0.5 size-4 shrink-0 text-cobalt" aria-hidden />
        If the repo has no numbers, the draft has no numbers. Every metric links to a diff.
      </p>
    </Panel>
  )
}
