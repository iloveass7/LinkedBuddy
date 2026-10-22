import type { ReactNode } from "react"
import { LogoMark } from "@/components/brand"

/** Shown while a route's code or the session check loads. */
export function RouteFallback({ children }: { children?: ReactNode }) {
  return (
    <div className="bg-blueprint grid min-h-dvh place-items-center px-margin-sm">
      <div className="flex flex-col items-center gap-6 text-center">
        <LogoMark className="size-14 animate-breathe" />
        {children ?? <span className="sr-only">Loading</span>}
      </div>
    </div>
  )
}
