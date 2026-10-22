import { Link } from "react-router"
import { useQuery } from "@tanstack/react-query"
import { LogoTile } from "@/components/brand"
import { StatusDot } from "@/components/ui/primitives"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

const facts = [
  { label: "// where it runs", value: "127.0.0.1", note: "The API binds to loopback. Nothing listens on your network." },
  { label: "// model", value: "Gemini free tier", note: "Your privacy mode decides what code, if any, reaches it." },
  { label: "// publishing", value: "Copy and paste", note: "Drafts never post on their own. You publish each one." },
]

export function LandingFooter() {
  const status = useQuery({ queryKey: ["health"], queryFn: api.health, retry: false, refetchInterval: 30_000 })
  const online = status.isSuccess && status.data.db === "ok"

  return (
    <footer className="border-t-2 border-cobalt bg-midnight px-margin-sm pt-10 text-paper sm:px-margin">
      <div className="flex flex-wrap items-start justify-between gap-6 border-b border-paper/10 pb-8">
        <div className="flex items-start gap-4">
          <LogoTile inverted className="size-11" />
          <div>
            <h2 className="font-display text-headline-md">Built by a builder, for one</h2>
            <p className="mt-1 font-mono text-code text-ink-muted">A personal tool. One owner, no accounts, no tiers.</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-3">
          <Link to="/login" className="label-technical border border-cobalt-vivid bg-cobalt/25 px-4 py-2.5 transition-transform hover:-translate-y-0.5">
            [ Log in ]
          </Link>
          <Link to="/app/archive" className="label-technical border border-paper/25 px-4 py-2.5 text-cobalt-tint transition-transform hover:-translate-y-0.5">
            [ Open the archive ]
          </Link>
        </div>
      </div>

      <dl className="grid gap-4 py-8 md:grid-cols-3">
        {facts.map((f) => (
          <div key={f.label} className="rounded border border-paper/10 bg-ink/80 p-4 transition-colors hover:border-paper/25">
            <dt className="label-badge text-ink-muted normal-case">{f.label}</dt>
            <dd className="mt-2 font-mono text-code font-bold text-cobalt-tint">{f.value}</dd>
            <dd className="mt-1 text-body text-ink-muted">{f.note}</dd>
          </div>
        ))}
      </dl>

      <div className="-mx-margin-sm flex flex-wrap items-center justify-between gap-3 border-t border-paper/10 px-margin-sm py-4 sm:-mx-margin sm:px-margin">
        <p className="label-technical flex items-center gap-2" aria-live="polite">
          <StatusDot tone={online ? "high" : status.isPending ? "low" : "medium"} live={online} />
          <span className={cn(!online && "text-ink-muted")}>
            {online ? "System online" : status.isPending ? "Checking the API" : "API offline"}
          </span>
        </p>
        <p className="label-technical text-cobalt-tint">Diff is truth · no unverified claims</p>
      </div>
    </footer>
  )
}
