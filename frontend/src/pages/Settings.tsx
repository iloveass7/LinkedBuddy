import { useMemo, useState, type FormEvent, type ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AnimatePresence, motion } from "motion/react"
import { ArrowsClockwise, FloppyDisk, GithubLogo, Key, LockKey, MagnifyingGlass, Minus, Plus, Robot, ShieldCheck, VideoCamera, X } from "@phosphor-icons/react"
import { api, type EmojiPolicy, type PostLength, type Repo, type RepoMode, type Settings as SettingsData, type Tone } from "@/lib/api"
import { plural, privacyModes, repoModeLabel, timeAgo, toneLabel } from "@/lib/format"
import { ease, stagger } from "@/lib/motion"
import { cn } from "@/lib/utils"
import { meQuery } from "@/components/RequireAuth"
import { PageHeader } from "@/components/PageHeader"
import { Schematic } from "@/components/Schematic"
import { Button } from "@/components/ui/button"
import { Modal } from "@/components/ui/modal"
import { FieldLabel, Panel, PanelTitle, StatusDot, Tag, inputClass } from "@/components/ui/primitives"
import { errorText, toast } from "@/components/ui/toast"
import keycap from "@/assets/img/keycap.webp"

const tones: Tone[] = ["technical", "professional", "casual", "storytelling"]
const lengths: { value: PostLength; label: string }[] = [
  { value: "short", label: "Short" },
  { value: "medium", label: "Medium" },
  { value: "long", label: "Long" },
]
const emojis: { value: EmojiPolicy; label: string }[] = [
  { value: "none", label: "None" },
  { value: "light", label: "A few" },
  { value: "any", label: "Any" },
]
const modes = Object.keys(repoModeLabel) as RepoMode[]

export default function Settings() {
  const queryClient = useQueryClient()
  const saved = useQuery({ queryKey: ["settings"], queryFn: api.settings })
  // null = no local changes; the form shows the saved settings.
  const [draft, setForm] = useState<SettingsData | null>(null)
  const form = draft ?? saved.data ?? null

  const dirty = useMemo(() => form !== null && saved.data !== undefined && JSON.stringify(form) !== JSON.stringify(saved.data), [form, saved.data])
  const save = useMutation({
    mutationFn: () => api.saveSettings(form!),
    onSuccess: (data) => {
      queryClient.setQueryData(["settings"], data)
      setForm(null)
      toast("Settings saved")
    },
    onError: (e) => toast(errorText(e), "error"),
  })
  const set = <K extends keyof SettingsData>(key: K, value: SettingsData[K]) => setForm((f) => { const base = f ?? saved.data; return base ? { ...base, [key]: value } : f })

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        stage="Stage 04 // Settings & privacy"
        meta={<Tag tone="high">keys stay on the server</Tag>}
        title="Settings and privacy guard"
        description="Connections, what the model is allowed to see, and how your posts should sound."
        actions={
          <Button size="lg" onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
            <FloppyDisk weight="bold" aria-hidden /> {save.isPending ? "Saving" : dirty ? "Save changes" : "All changes saved"}
          </Button>
        }
      />

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,8fr)_minmax(0,4fr)]">
        <div className="flex flex-col gap-8">
          <Connections />

          <Panel hard className="p-5 sm:p-6">
            <PanelTitle aside="what runs where">Pipeline</PanelTitle>
            <div className="bg-blueprint mt-5 border border-cobalt/10 px-4 py-6">
              <Schematic className="mx-auto max-w-xl" />
            </div>
          </Panel>

          {form ? (
            <>
              <PrivacyGuard value={form.privacy_mode} onChange={(v) => set("privacy_mode", v)} />
              <Voice form={form} set={set} />
            </>
          ) : saved.isError ? (
            <Panel className="p-6 text-body-lg text-danger">Settings didn't load: {errorText(saved.error)}</Panel>
          ) : (
            <div className="h-96 animate-pulse bg-paper" />
          )}

          <AnimatePresence>
            {dirty && (
              <motion.div
                initial={{ y: 40, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                exit={{ y: 40, opacity: 0 }}
                transition={{ duration: 0.4, ease: ease.expo }}
                className="sticky bottom-4 z-30 flex flex-wrap items-center justify-between gap-3 border-2 border-ink bg-midnight px-5 py-3 text-paper shadow-hard-cobalt"
              >
                <span className="label-technical flex items-center gap-2">
                  <StatusDot tone="medium" live /> Unsaved changes
                </span>
                <span className="flex gap-2">
                  <Button variant="ghost" size="sm" className="text-cobalt-tint" onClick={() => setForm(null)}>
                    Discard
                  </Button>
                  <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>
                    {save.isPending ? "Saving" : "Save changes"}
                  </Button>
                </span>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <aside className="flex flex-col gap-6" aria-label="About keys">
          <Panel hard className="p-5">
            <PanelTitle as="h2" className="[&_h2]:text-title">Where your keys live</PanelTitle>
            <ul className="mt-4 flex flex-col gap-3 text-body text-ink-soft">
              <li className="flex gap-3">
                <LockKey weight="bold" className="mt-0.5 size-4 shrink-0 text-cobalt" aria-hidden />
                The GitHub token is encrypted in the database. This page can replace it but never shows it.
              </li>
              <li className="flex gap-3">
                <Key weight="bold" className="mt-0.5 size-4 shrink-0 text-cobalt" aria-hidden />
                <span>
                  The Gemini key lives in the backend's <code className="bg-surface-low px-1 font-mono text-code">.env</code>. The browser never sees it.
                </span>
              </li>
              <li className="flex gap-3">
                <ShieldCheck weight="bold" className="mt-0.5 size-4 shrink-0 text-cobalt" aria-hidden />
                Secrets are scrubbed from diffs before any prompt is built.
              </li>
            </ul>
          </Panel>
          <Panel className="overflow-hidden">
            <img src={keycap} alt="Keyboard with a glowing blue keycap" className="aspect-square w-full object-cover" loading="lazy" />
          </Panel>
        </aside>
      </div>
    </div>
  )
}

/* ---------------- 01 Connections ---------------- */

function Connections() {
  const queryClient = useQueryClient()
  const me = useQuery(meQuery)
  const [token, setToken] = useState("")
  const connect = useMutation({
    mutationFn: () => api.connectGitHub(token),
    onSuccess: () => {
      setToken("")
      toast("GitHub token saved")
      void queryClient.invalidateQueries({ queryKey: ["me"] })
    },
    onError: (e) => toast(errorText(e), "error"),
  })
  const sync = useMutation({
    mutationFn: api.syncRepos,
    onSuccess: (r) => {
      toast(`Synced ${plural(r.synced, "repository")}`)
      void queryClient.invalidateQueries({ queryKey: ["repos"] })
    },
    onError: (e) => toast(errorText(e), "error"),
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (token.trim()) connect.mutate()
  }
  const github = me.data?.github_connected ?? false

  return (
    <section aria-labelledby="connections-title">
      <PanelTitle as="h2">
        <span id="connections-title">Connections</span>
      </PanelTitle>
      <motion.div variants={stagger.container} initial="hidden" whileInView="show" viewport={{ once: true }} className="mt-5 grid gap-5 md:grid-cols-2">
        <ConnectionCard
          icon={<GithubLogo weight="bold" />}
          kicker="Source"
          title="GitHub (read-only)"
          ok={github}
          okLabel="Connected"
          offLabel="Not connected"
        >
          <p className="text-body text-ink-soft">A fine-grained token with read access to contents, pull requests and metadata.</p>
          <form onSubmit={submit} className="mt-3 flex gap-2">
            <label htmlFor="gh-token" className="sr-only">
              GitHub token
            </label>
            <input
              id="gh-token"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={github ? "Paste a new token to replace it" : "github_pat_…"}
              className={cn(inputClass, "border-[1.5px] py-1.5 font-mono text-body")}
            />
            <Button type="submit" size="sm" className="h-auto shrink-0 font-mono text-label" disabled={!token.trim() || connect.isPending}>
              {connect.isPending ? "Saving" : "Save"}
            </Button>
          </form>
          <Button variant="ghost" size="sm" className="mt-2 px-0" onClick={() => sync.mutate()} disabled={!github || sync.isPending}>
            <ArrowsClockwise weight="bold" className={cn(sync.isPending && "animate-spin")} aria-hidden /> Sync repositories
          </Button>
        </ConnectionCard>

        <ConnectionCard icon={<Robot weight="bold" />} kicker="Model" title="Gemini free tier" ok={me.data?.gemini_configured ?? false} okLabel="Key set" offLabel="No key">
          <p className="text-body text-ink-soft">
            Set <code className="bg-surface-low px-1 font-mono text-code">GEMINI_API_KEY</code> in the backend's .env and restart it. Free-tier prompts may be used by Google, so check your privacy mode.
          </p>
          <p className="mt-3 flex items-center gap-2 font-mono text-label text-ink uppercase">
            <VideoCamera weight="bold" className="text-cobalt" aria-hidden /> Video (brag):{" "}
            <span className={me.data?.brag_configured ? "text-signal-high-ink" : "text-ink-muted"}>{me.data?.brag_configured ? "on" : "off"}</span>
          </p>
        </ConnectionCard>
      </motion.div>
    </section>
  )
}

function ConnectionCard({
  icon,
  kicker,
  title,
  ok,
  okLabel,
  offLabel,
  children,
}: {
  icon: ReactNode
  kicker: string
  title: string
  ok: boolean
  okLabel: string
  offLabel: string
  children: ReactNode
}) {
  return (
    <motion.div variants={stagger.item} className={cn("relative border-[1.5px] border-ink bg-paper p-5 shadow-hard", "before:absolute before:inset-y-0 before:left-0 before:w-1.5", ok ? "before:bg-signal-high" : "before:bg-signal-medium")}>
      <div className="flex items-start justify-between gap-3 pl-2">
        <div>
          <p className="font-mono text-badge text-ink-muted uppercase">{kicker}</p>
          <h3 className="mt-1 flex items-center gap-2 font-display text-title tracking-[0.02em] text-ink">
            <span className="text-cobalt">{icon}</span> {title}
          </h3>
        </div>
        <Tag tone={ok ? "high" : "medium"} className="border border-current">
          {ok ? okLabel : offLabel}
        </Tag>
      </div>
      <div className="mt-4 pl-2">{children}</div>
    </motion.div>
  )
}

/* ---------------- 02 Privacy guard ---------------- */

function PrivacyGuard({ value, onChange }: { value: SettingsData["privacy_mode"]; onChange: (v: SettingsData["privacy_mode"]) => void }) {
  return (
    <Panel hard className="p-5 sm:p-6">
      <PanelTitle aside="before any prompt">Privacy guard</PanelTitle>
      <p className="mt-3 max-w-2xl text-body-lg text-ink-soft">Choose how much of a repository reaches the model. This applies to every repo unless you block one below.</p>
      <div role="radiogroup" aria-label="Privacy mode" className="mt-5 grid gap-3 sm:grid-cols-2">
        {privacyModes.map((m) => {
          const active = value === m.value
          return (
            <button
              key={m.value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onChange(m.value)}
              className={cn(
                "relative cursor-pointer p-4 text-left transition-transform duration-200 hover:-translate-y-0.5",
                active ? "bg-cobalt-mist ring-2 ring-cobalt" : "bg-surface-low ring-1 ring-schematic",
              )}
            >
              <span className="flex items-start justify-between gap-2">
                <span className={cn("label-technical", active ? "text-cobalt" : "text-ink")}>{m.title}</span>
                <span className={cn("grid size-4 shrink-0 place-items-center rounded-full ring-2", active ? "ring-cobalt" : "ring-ink-muted")}>
                  {active && <motion.span layoutId="privacy-dot" className="size-2 rounded-full bg-cobalt" />}
                </span>
              </span>
              <span className="mt-2 block text-body text-ink-soft">{m.body}</span>
            </button>
          )
        })}
      </div>
      <RepoTable />
    </Panel>
  )
}

function RepoTable() {
  const queryClient = useQueryClient()
  const repos = useQuery({ queryKey: ["repos"], queryFn: api.repos })
  const [filter, setFilter] = useState("")
  const [pendingAck, setPendingAck] = useState<Repo | null>(null)

  const patch = useMutation({
    mutationFn: ({ repo, change }: { repo: Repo; change: Parameters<typeof api.patchRepo>[1] }) => api.patchRepo(repo.id, change),
    onSuccess: (updated) => {
      queryClient.setQueryData<Repo[]>(["repos"], (old) => old?.map((r) => (r.id === updated.id ? updated : r)))
      toast(`Updated ${updated.full_name}`)
    },
    onError: (e) => toast(errorText(e), "error"),
  })

  const toggleModel = (repo: Repo) => {
    const allow = repo.never_send_to_llm
    if (allow && repo.visibility === "private") {
      setPendingAck(repo)
      return
    }
    patch.mutate({ repo, change: { never_send_to_llm: !allow } })
  }

  const rows = (repos.data ?? []).filter((r) => r.full_name.toLowerCase().includes(filter.toLowerCase()))

  return (
    <div className="mt-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h3 className="label-technical text-ink">Per-repository controls</h3>
        <label className="relative">
          <span className="sr-only">Find a repository</span>
          <MagnifyingGlass className="absolute top-1/2 left-3 -translate-y-1/2 text-ink-muted" aria-hidden />
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Find a repository" className={cn(inputClass, "w-64 border-[1.5px] py-1.5 pl-9 text-body")} />
        </label>
      </div>
      <div className="mt-3 overflow-x-auto ring-1 ring-schematic" data-lenis-prevent>
        <table className="w-full min-w-[640px] border-collapse text-left">
          <thead className="bg-surface-low">
            <tr className="font-mono text-badge text-ink uppercase">
              <th scope="col" className="px-3 py-2.5">Repository</th>
              <th scope="col" className="px-3 py-2.5">Visibility</th>
              <th scope="col" className="px-3 py-2.5">Triage</th>
              <th scope="col" className="px-3 py-2.5">Model may read it</th>
              <th scope="col" className="px-3 py-2.5">Last polled</th>
            </tr>
          </thead>
          <tbody>
            {repos.isPending && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-body text-ink-muted">
                  Loading repositories
                </td>
              </tr>
            )}
            {rows.map((repo) => (
              <tr key={repo.id} className={cn("border-t border-schematic text-body", repo.never_send_to_llm && "bg-danger-tint/20")}>
                <td className="px-3 py-2.5 font-mono text-code">
                  <span className="text-cobalt">{repo.full_name.split("/")[0]}/</span>
                  <span className="text-ink">{repo.full_name.split("/")[1]}</span>
                </td>
                <td className="px-3 py-2.5">
                  <Tag tone={repo.visibility === "private" ? "dark" : "plain"}>{repo.visibility}</Tag>
                </td>
                <td className="px-3 py-2.5">
                  <select
                    aria-label={`Triage mode for ${repo.full_name}`}
                    value={repo.mode}
                    onChange={(e) => patch.mutate({ repo, change: { mode: e.target.value as RepoMode } })}
                    className="cursor-pointer border-[1.5px] border-ink bg-paper px-2 py-1 text-body"
                  >
                    {modes.map((m) => (
                      <option key={m} value={m}>
                        {repoModeLabel[m]}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="px-3 py-2.5">
                  <Switch checked={!repo.never_send_to_llm} onChange={() => toggleModel(repo)} label={`Let the model read ${repo.full_name}`} />
                </td>
                <td className="px-3 py-2.5 font-mono text-badge text-ink-muted uppercase">{timeAgo(repo.last_polled_at)}</td>
              </tr>
            ))}
            {repos.isSuccess && rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-body text-ink-muted">
                  {filter ? `No repository matches “${filter}”.` : "No repositories yet. Connect GitHub and sync."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Modal
        open={pendingAck !== null}
        onOpenChange={(o) => !o && setPendingAck(null)}
        title="Send a private repo to Gemini?"
        description="The Gemini free tier may use prompts to improve Google's products. Your privacy mode still limits what is sent."
      >
        <div className="flex justify-end gap-3">
          <Button variant="ghost" onClick={() => setPendingAck(null)}>
            Keep it blocked
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              if (pendingAck) patch.mutate({ repo: pendingAck, change: { never_send_to_llm: false, acknowledge_free_tier_data_use: true } })
              setPendingAck(null)
            }}
          >
            Allow it
          </Button>
        </div>
      </Modal>
    </div>
  )
}

function Switch({ checked, onChange, label }: { checked: boolean; onChange: () => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={onChange}
      className={cn("flex h-6 w-11 cursor-pointer items-center rounded-full p-0.5", checked ? "bg-signal-high" : "bg-schematic")}
    >
      <motion.span layout transition={{ type: "spring", stiffness: 500, damping: 32 }} className={cn("size-5 rounded-full bg-paper shadow-hard-sm", checked && "ml-auto")} />
    </button>
  )
}

/* ---------------- 03 Voice ---------------- */

function Voice({ form, set }: { form: SettingsData; set: <K extends keyof SettingsData>(key: K, value: SettingsData[K]) => void }) {
  const [phrase, setPhrase] = useState("")
  const [sample, setSample] = useState("")
  const addPhrase = (e: FormEvent) => {
    e.preventDefault()
    const p = phrase.trim().toLowerCase()
    if (p && !form.ban_list.includes(p) && form.ban_list.length < 100) set("ban_list", [...form.ban_list, p])
    setPhrase("")
  }

  return (
    <Panel hard className="p-5 sm:p-6">
      <PanelTitle aside="no marketing fluff">Voice and anti-slop</PanelTitle>

      <div className="mt-5">
        <FieldLabel htmlFor="goals">Goals and audience</FieldLabel>
        <textarea id="goals" data-lenis-prevent rows={3} maxLength={2000} value={form.goals_text} onChange={(e) => set("goals_text", e.target.value)} className={inputClass} />
      </div>

      <fieldset className="mt-6">
        <legend className="label-technical mb-2 text-ink">Default tone</legend>
        <Segmented options={tones.map((t) => ({ value: t, label: toneLabel[t] }))} value={form.default_tone} onChange={(v) => set("default_tone", v)} name="tone" />
      </fieldset>

      <div className="mt-6">
        <FieldLabel htmlFor="instructions">Standing instructions</FieldLabel>
        <input id="instructions" maxLength={500} value={form.custom_instructions} onChange={(e) => set("custom_instructions", e.target.value)} placeholder="e.g. Lead with the number. No questions at the end." className={inputClass} />
      </div>

      <div className="mt-6 grid gap-6 sm:grid-cols-3">
        <fieldset>
          <legend className="label-technical mb-2 text-ink">Post length</legend>
          <Segmented options={lengths} value={form.post_length} onChange={(v) => set("post_length", v)} name="length" />
        </fieldset>
        <fieldset>
          <legend className="label-technical mb-2 text-ink">Emoji</legend>
          <Segmented options={emojis} value={form.emoji_policy} onChange={(v) => set("emoji_policy", v)} name="emoji" />
        </fieldset>
        <div>
          <p className="label-technical mb-2 text-ink" id="hashtags-label">Hashtags</p>
          <div className="flex items-center gap-2" role="group" aria-labelledby="hashtags-label">
            <Button variant="quiet" size="icon" aria-label="Fewer hashtags" onClick={() => set("hashtag_count", Math.max(0, form.hashtag_count - 1))} disabled={form.hashtag_count === 0}>
              <Minus weight="bold" />
            </Button>
            <motion.span key={form.hashtag_count} initial={{ y: -8, opacity: 0 }} animate={{ y: 0, opacity: 1 }} className="w-8 text-center font-display text-headline-md tabular-nums" aria-live="polite">
              {form.hashtag_count}
            </motion.span>
            <Button variant="quiet" size="icon" aria-label="More hashtags" onClick={() => set("hashtag_count", Math.min(5, form.hashtag_count + 1))} disabled={form.hashtag_count === 5}>
              <Plus weight="bold" />
            </Button>
          </div>
        </div>
      </div>

      <div className="mt-8">
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="label-technical text-ink">Voice samples</h3>
          <span className="font-mono text-badge text-ink-muted uppercase">{form.voice_samples.length} / 5</span>
        </div>
        <p className="mt-1 text-body text-ink-soft">Posts you wrote yourself. Drafts borrow their rhythm, not their content.</p>
        <ul className="mt-3 flex flex-col gap-2">
          <AnimatePresence initial={false}>
            {form.voice_samples.map((s, i) => (
              <motion.li
                key={s}
                layout
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, x: 40 }}
                transition={{ duration: 0.3, ease: ease.expo }}
                className="flex items-start gap-3 bg-surface-low p-3"
              >
                <span className="flex-1 text-body text-ink-soft italic">"{s}"</span>
                <button
                  type="button"
                  aria-label={`Remove sample ${i + 1}`}
                  onClick={() => set("voice_samples", form.voice_samples.filter((_, j) => j !== i))}
                  className="grid size-7 shrink-0 cursor-pointer place-items-center text-ink-muted hover:text-unsupported"
                >
                  <X weight="bold" />
                </button>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
        {form.voice_samples.length < 5 && (
          <div className="mt-2 flex flex-col gap-2">
            <label htmlFor="new-sample" className="sr-only">
              New voice sample
            </label>
            <textarea id="new-sample" data-lenis-prevent rows={2} maxLength={5000} value={sample} onChange={(e) => setSample(e.target.value)} placeholder="Paste a post you wrote" className={cn(inputClass, "border-[1.5px] text-body")} />
            <Button
              variant="ghost"
              size="sm"
              className="self-start px-0"
              disabled={!sample.trim()}
              onClick={() => {
                set("voice_samples", [...form.voice_samples, sample.trim()])
                setSample("")
              }}
            >
              <Plus weight="bold" aria-hidden /> Add sample
            </Button>
          </div>
        )}
      </div>

      <div className="mt-8">
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="label-technical text-danger">Banned phrases</h3>
          <span className="font-mono text-badge text-ink-muted uppercase">{plural(form.ban_list.length, "phrase")}</span>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 bg-surface-low p-3">
          <AnimatePresence initial={false}>
            {form.ban_list.map((p) => (
              <motion.span
                key={p}
                layout
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.8 }}
                transition={{ duration: 0.25, ease: ease.expo }}
                className="flex items-center gap-1.5 border-[1.5px] border-danger bg-paper py-1 pr-1 pl-2.5 font-mono text-code text-danger"
              >
                {p}
                <button type="button" aria-label={`Remove “${p}”`} onClick={() => set("ban_list", form.ban_list.filter((x) => x !== p))} className="grid size-5 cursor-pointer place-items-center hover:bg-danger-tint">
                  <X weight="bold" className="size-3" />
                </button>
              </motion.span>
            ))}
          </AnimatePresence>
          <form onSubmit={addPhrase} className="flex">
            <label htmlFor="new-phrase" className="sr-only">
              Add a banned phrase
            </label>
            <input id="new-phrase" value={phrase} maxLength={80} onChange={(e) => setPhrase(e.target.value)} placeholder="+ add a phrase" className="w-40 border-[1.5px] border-dashed border-ink-muted bg-paper px-2 py-1 font-mono text-code focus:border-cobalt focus:outline-none" />
          </form>
        </div>
        <p className="mt-2 text-body text-ink-muted">"Excited to announce", "game-changer", "thrilled" and "delighted to share" are always banned.</p>
      </div>
    </Panel>
  )
}

function Segmented<T extends string>({ options, value, onChange, name }: { options: { value: T; label: string }[]; value: T; onChange: (v: T) => void; name: string }) {
  return (
    <div className="inline-flex flex-wrap bg-surface-low p-1" role="radiogroup">
      {options.map((o) => {
        const active = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.value)}
            className={cn("relative cursor-pointer px-3 py-1.5 font-mono text-label uppercase", active ? "text-paper" : "text-ink-soft hover:text-ink")}
          >
            {active && <motion.span layoutId={`seg-${name}`} className="absolute inset-0 bg-cobalt" transition={{ duration: 0.35, ease: ease.expo }} />}
            <span className="relative">{o.label}</span>
          </button>
        )
      })}
    </div>
  )
}
