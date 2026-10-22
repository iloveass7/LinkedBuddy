import { Link } from "react-router"
import { buttonVariants } from "@/components/ui/button"

export default function NotFound() {
  return (
    <main className="bg-blueprint grid min-h-dvh place-items-center px-margin-sm">
      <div className="max-w-lg text-center">
        <p aria-hidden className="font-display text-[clamp(6rem,24vw,14rem)] leading-[0.82] text-cobalt">404</p>
        <h1 className="mt-4 font-display text-headline-lg text-ink">No page at this address</h1>
        <p className="mt-3 text-body-lg text-ink-soft">The link may be old. The inbox is the place to start.</p>
        <Link to="/app/inbox" className={buttonVariants({ variant: "primary", size: "md", className: "mt-8" })}>
          Go to the inbox
        </Link>
      </div>
    </main>
  )
}
