import { isRouteErrorResponse, Link, useRouteError } from "react-router"
import { buttonVariants } from "@/components/ui/button"

/** Shown when a page throws while rendering. */
export function RouteError() {
  const error = useRouteError()
  const detail = isRouteErrorResponse(error) ? `${error.status} ${error.statusText}` : error instanceof Error ? error.message : "Unknown error"
  return (
    <main className="bg-blueprint grid min-h-dvh place-items-center px-margin-sm">
      <div className="w-full max-w-lg border-[1.5px] border-ink bg-paper p-8 shadow-hard">
        <h1 className="font-display text-headline-lg text-ink">This page broke</h1>
        <p className="mt-3 text-body-lg text-ink-soft">Reload to try again. If it keeps happening, the message below says where.</p>
        <pre className="mt-4 overflow-x-auto bg-ink p-3 font-mono text-code text-cobalt-tint">{detail}</pre>
        <div className="mt-6 flex gap-3">
          <button type="button" onClick={() => window.location.reload()} className={buttonVariants({ variant: "primary" })}>
            Reload
          </button>
          <Link to="/app/inbox" className={buttonVariants({ variant: "ghost" })}>
            Go to the inbox
          </Link>
        </div>
      </div>
    </main>
  )
}
