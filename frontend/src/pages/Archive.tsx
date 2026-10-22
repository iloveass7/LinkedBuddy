import { useMemo, useState } from "react"
import { Link } from "react-router"
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { AnimatePresence, motion } from "motion/react"
import { Archive as ArchiveIcon, CalendarCheck, ClockCounterClockwise, GitCommit, Pulse, Tray } from "@phosphor-icons/react"
import { api, type MetricName, type Post, type Repo } from "@/lib/api"
import { plural, repoName, shortSha, timeAgo } from "@/lib/format"
import { ease, stagger } from "@/lib/motion"
import { cn } from "@/lib/utils"
import { PageHeader } from "@/components/PageHeader"
import { Schematic } from "@/components/Schematic"
import { Button } from "@/components/ui/button"
import { Panel, PanelTitle, StatusDot, Tag } from "@/components/ui/primitives"
import { errorText, toast } from "@/components/ui/toast"
import nightDesk from "@/assets/img/night-desk.webp"

type PostWithRepo = Post & { repo: Repo }

const metricNames: { key: MetricName; label: string }[] = [
  { key: "impressions", label: "Impressions" },
  { key: "reactions", label: "Reactions" },
  { key: "comments", label: "Comments" },
  { key: "reposts", label: "Reposts" },
  { key: "saves", label: "Saves" },
]
const WEEK = 7 * 86_400_000
const WEEKS = 12

export default function Archive() {
  const repos = useQuery({ queryKey: ["repos"], queryFn: api.repos })
  const drafted = useQuery({ queryKey: ["inbox", "drafted"], queryFn: () => api.inbox({ status: "drafted" }) })
  const postQueries = useQueries({
    queries: (repos.data ?? []).map((repo) => ({
      queryKey: ["posts", repo.id],
      queryFn: () => api.repoPosts(repo.id),
      staleTime: 60_000,
    })),
  })

  const loading = repos.isPending || postQueries.some((q) => q.isPending)
  const posts = useMemo<PostWithRepo[]>(() => {
    const all = (repos.data ?? []).flatMap((repo, i) => (postQueries[i]?.data?.items ?? []).map((p) => ({ ...p, repo })))
    return all.sort((a, b) => b.posted_at.localeCompare(a.posted_at))
  }, [repos.data, postQueries])

  const [now] = useState(() => Date.now())
  const recent = posts.filter((p) => now - new Date(p.posted_at).getTime() < 4 * WEEK).length
  const reposWithPosts = new Set(posts.map((p) => p.repo.id)).size
  const waiting = drafted.data?.items.length ?? 0

  return (
    <div className="flex flex-col gap-8">
      <div>
        <PageHeader
          stage="Stage 03 // Archive & cadence"
          title="Archive and posting cadence"
          description="Everything you've published, the commits each post covered, and how it did. Add the numbers from LinkedIn by hand."
        />
        <motion.dl
          variants={stagger.container}
          initial="hidden"
          animate="show"
          className="grid grid-cols-1 gap-3 bg-paper p-5 pt-0 sm:grid-cols-2 sm:p-8 sm:pt-0 xl:grid-cols-4"
        >
          <Metric icon={ArchiveIcon} label="Posts published" value={loading ? "…" : String(posts.length)} note={`from ${plural(reposWithPosts, "repository")}`} />
          <Metric icon={Pulse} label="Cadence" value={loading ? "…" : (recent / 4).toFixed(1)} note="posts a week, last 4 weeks" tone={recent >= 4 ? "good" : undefined} />
          <Metric icon={ClockCounterClockwise} label="Last published" value={posts[0] ? timeAgo(posts[0].posted_at) : "never"} note={posts[0] ? `via ${repoName(posts[0].repo.full_name)}` : "nothing posted yet"} />
          <Link to="/app/review" className="block transition-transform duration-200 hover:-translate-y-0.5">
            <Metric icon={Tray} label="Drafts waiting" value={String(waiting)} note="ready in the review studio" accent />
          </Link>
        </motion.dl>
      </div>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,8fr)_minmax(0,4fr)]">
        <div className="flex flex-col gap-6">
          <Panel className="p-5 sm:p-6">
            <PanelTitle aside={<span>{plural(posts.length, "range")} recorded</span>}>
              <GitCommit weight="bold" className="text-cobalt" aria-hidden /> Covered commit ledger
            </PanelTitle>
            <div className="bg-blueprint mt-5 border border-cobalt/10 px-4 py-6">
              <Schematic className="mx-auto max-w-xl" />
            </div>
            <p className="mt-4 text-body text-ink-soft">
              Each post records the commit range it covered, so you can see what has already been told.
            </p>
          </Panel>

          {loading && [0, 1].map((i) => <div key={i} className="h-64 animate-pulse bg-paper ring-1 ring-schematic/50" />)}
          {!loading && posts.length === 0 && (
            <Panel className="flex flex-col items-start gap-4 p-8">
              <h2 className="font-display text-headline-lg text-ink">Nothing published yet</h2>
              <p className="max-w-lg text-body-lg text-ink-soft">
                When you mark a draft as published in the review studio, it lands here with its commit range.
              </p>
              <Button asChild>
                <Link to="/app/review">Open the review studio</Link>
              </Button>
            </Panel>
          )}
          {posts.map((post, i) => (
            <PostRecord key={post.id} post={post} number={posts.length - i} index={i} />
          ))}
        </div>

        <aside className="flex flex-col gap-6" aria-label="Cadence">
          <Panel className="p-5">
            <PanelTitle as="h2" className="[&_h2]:text-title">
              <CalendarCheck weight="bold" className="text-cobalt" aria-hidden /> Posting rhythm
            </PanelTitle>
            <p className="mt-2 text-body text-ink-soft">Posts per week, last {WEEKS} weeks.</p>
            <WeeklyBars posts={posts} />
          </Panel>

          <TopPosts posts={posts} />

          <Panel className="overflow-hidden">
            <img src={nightDesk} alt="An engineer working late at a multi-monitor desk" className="aspect-[16/10] w-full object-cover" loading="lazy" />
            <p className="flex justify-between gap-3 bg-midnight px-4 py-2 font-mono text-badge text-cobalt-tint uppercase">
              <span>Ship, then post</span>
              <span className="text-signal-high">one change at a time</span>
            </p>
          </Panel>
        </aside>
      </div>
    </div>
  )
}

function Metric({
  icon: Icon,
  label,
  value,
  note,
  tone,
  accent = false,
}: {
  icon: typeof ArchiveIcon
  label: string
  value: string
  note: string
  tone?: "good"
  accent?: boolean
}) {
  return (
    <motion.div variants={stagger.item} className={cn("h-full p-4", accent ? "bg-cobalt text-paper" : "bg-surface-low")}>
      <dt className={cn("flex items-center justify-between font-mono text-label uppercase", accent ? "text-cobalt-tint" : "text-ink-muted")}>
        {label}
        <Icon weight="bold" className={cn("size-5", accent ? "text-paper" : "text-cobalt")} aria-hidden />
      </dt>
      <dd className="mt-2 font-display text-headline-lg">{value}</dd>
      <dd className={cn("font-mono text-label uppercase", accent ? "text-cobalt-tint" : tone === "good" ? "text-signal-high-ink" : "text-ink-soft")}>{note}</dd>
    </motion.div>
  )
}

function PostRecord({ post, number, index }: { post: PostWithRepo; number: number; index: number }) {
  const queryClient = useQueryClient()
  const [title, ...rest] = post.final_text.split("\n").filter((l) => l.trim())
  const [open, setOpen] = useState(false)
  const [values, setValues] = useState<Partial<Record<MetricName, string>>>(() =>
    Object.fromEntries(metricNames.map(({ key }) => [key, post.metrics[key]?.toString() ?? ""])),
  )

  const changed = metricNames.filter(({ key }) => (values[key] ?? "") !== (post.metrics[key]?.toString() ?? "") && values[key] !== "")
  const save = useMutation({
    mutationFn: () => api.patchMetrics(post.id, Object.fromEntries(changed.map(({ key }) => [key, Number(values[key])]))),
    onSuccess: () => {
      toast("Metrics saved")
      void queryClient.invalidateQueries({ queryKey: ["posts", post.repo.id] })
    },
    onError: (e) => toast(errorText(e), "error"),
  })

  const range = post.covered_base_sha ? `${shortSha(post.covered_base_sha)}..${shortSha(post.covered_head_sha)}` : shortSha(post.covered_head_sha)

  return (
    <motion.article
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.2 }}
      transition={{ duration: 0.6, ease: ease.expo, delay: Math.min(index, 3) * 0.05 }}
      className="bg-paper p-5 ring-1 ring-schematic/70 sm:p-6"
    >
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-label uppercase">
        <span aria-hidden className="size-2.5 bg-signal-high" />
        <span className="font-bold text-ink">Post #{String(number).padStart(2, "0")} // published</span>
        <span aria-hidden className="text-ink-muted">•</span>
        <span className="text-ink-muted">{timeAgo(post.posted_at)}</span>
      </p>
      <p className="mt-3 flex flex-wrap items-center gap-2">
        <Tag tone="plain" className="text-label">{post.repo.full_name}</Tag>
        <Tag tone="cobalt" className="text-label">{range}</Tag>
      </p>
      <h2 className="mt-4 font-display text-headline-md text-ink">{title}</h2>
      {rest.length > 0 && (
        <div className="mt-2">
          <p className={cn("text-body-lg whitespace-pre-line text-ink-soft", !open && "line-clamp-3")}>{rest.join("\n")}</p>
          {rest.join("\n").length > 220 && (
            <button type="button" onClick={() => setOpen((o) => !o)} className="mt-1 cursor-pointer font-mono text-label text-cobalt uppercase hover:underline">
              {open ? "Show less" : "Show the full post"}
            </button>
          )}
        </div>
      )}

      <form
        className="mt-5 bg-surface-low p-4"
        onSubmit={(e) => {
          e.preventDefault()
          if (changed.length) save.mutate()
        }}
      >
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="label-technical text-ink">Performance, entered by hand</h3>
          <AnimatePresence>
            {changed.length > 0 && (
              <motion.span initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} className="font-mono text-label text-signal-medium-ink uppercase">
                {plural(changed.length, "unsaved change")}
              </motion.span>
            )}
          </AnimatePresence>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
          {metricNames.map(({ key, label }) => (
            <label key={key} className="bg-paper p-2">
              <span className="block font-mono text-badge text-ink-muted uppercase">{label}</span>
              <input
                inputMode="numeric"
                pattern="[0-9]*"
                value={values[key] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value.replace(/\D/g, "") }))}
                placeholder="—"
                className="mt-1 w-full border-b-2 border-ink bg-transparent font-display text-title text-ink tabular-nums focus:border-cobalt focus:outline-none"
              />
            </label>
          ))}
        </div>
        <Button type="submit" size="sm" className="mt-3 font-mono text-label" disabled={!changed.length || save.isPending}>
          {save.isPending ? "Saving" : "Save metrics"}
        </Button>
      </form>
    </motion.article>
  )
}

/** Single-series bar chart: posts per week. One hue, square marks, hover tooltip, table for screen readers. */
function WeeklyBars({ posts }: { posts: Post[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const [now] = useState(() => Date.now())
  const weeks = useMemo(() => {
    const counts = Array.from({ length: WEEKS }, (_, i) => ({ start: new Date(now - (WEEKS - i) * WEEK), n: 0 }))
    for (const p of posts) {
      const age = Math.floor((now - new Date(p.posted_at).getTime()) / WEEK)
      if (age >= 0 && age < WEEKS) counts[WEEKS - 1 - age].n++
    }
    return counts
  }, [posts, now])
  const max = Math.max(2, ...weeks.map((w) => w.n))
  const fmt = new Intl.DateTimeFormat("en", { month: "short", day: "numeric" })

  return (
    <figure className="mt-4">
      <div className="relative flex h-36 items-end gap-1 border-b-2 border-ink" onMouseLeave={() => setHover(null)}>
        {weeks.map((w, i) => (
          <button
            type="button"
            key={i}
            aria-label={`Week of ${fmt.format(w.start)}: ${plural(w.n, "post")}`}
            onMouseEnter={() => setHover(i)}
            onFocus={() => setHover(i)}
            className="group relative flex h-full flex-1 cursor-default items-end"
          >
            <motion.span
              className={cn("block w-full origin-bottom", w.n ? "bg-cobalt" : "bg-schematic", hover === i && "bg-cobalt-vivid")}
              style={{ height: `${Math.max(w.n / max, 0.03) * 100}%` }}
              initial={{ scaleY: 0 }}
              whileInView={{ scaleY: 1 }}
              viewport={{ once: true }}
              transition={{ duration: 0.6, ease: ease.expo, delay: i * 0.03 }}
            />
          </button>
        ))}
        <AnimatePresence>
          {hover !== null && (
            <motion.div
              key="tip"
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="pointer-events-none absolute -top-2 z-10 -translate-y-full bg-ink px-2 py-1 font-mono text-badge whitespace-nowrap text-paper"
              style={{ left: `${((hover + 0.5) / WEEKS) * 100}%`, translateX: "-50%" }}
            >
              {fmt.format(weeks[hover].start)}: {plural(weeks[hover].n, "post")}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <figcaption className="mt-2 flex justify-between font-mono text-badge text-ink-muted uppercase">
        <span>{fmt.format(weeks[0].start)}</span>
        <span>this week</span>
      </figcaption>
      <table className="sr-only">
        <caption>Posts per week</caption>
        <tbody>
          {weeks.map((w, i) => (
            <tr key={i}>
              <th scope="row">Week of {fmt.format(w.start)}</th>
              <td>{w.n}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  )
}

function TopPosts({ posts }: { posts: PostWithRepo[] }) {
  const ranked = posts.filter((p) => p.metrics.impressions !== undefined).sort((a, b) => (b.metrics.impressions ?? 0) - (a.metrics.impressions ?? 0)).slice(0, 3)
  return (
    <Panel className="p-5">
      <h2 className="label-technical flex items-center gap-2 text-ink">
        <StatusDot tone="high" /> Best reach so far
      </h2>
      {ranked.length === 0 ? (
        <p className="mt-3 text-body text-ink-soft">Add impressions to a post and the strongest ones show up here.</p>
      ) : (
        <ol className="mt-3 flex flex-col gap-2">
          {ranked.map((p) => (
            <li key={p.id} className="flex items-baseline justify-between gap-3 bg-surface-low px-3 py-2.5">
              <span className="min-w-0 truncate text-body text-ink">{p.final_text.split("\n")[0]}</span>
              <span className="shrink-0 font-display text-title text-cobalt tabular-nums">{p.metrics.impressions!.toLocaleString()}</span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  )
}
