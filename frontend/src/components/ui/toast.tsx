import { useSyncExternalStore } from "react"
import { AnimatePresence, motion } from "motion/react"
import { CheckCircle, WarningCircle } from "@phosphor-icons/react"
import { ease } from "@/lib/motion"

type Toast = { id: number; tone: "ok" | "error"; text: string }

let toasts: Toast[] = []
let nextId = 1
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((l) => l())

/** Show a short confirmation. Names the result of the action ("Draft saved"), never "Success". */
export function toast(text: string, tone: Toast["tone"] = "ok") {
  const id = nextId++
  toasts = [...toasts, { id, tone, text }]
  emit()
  window.setTimeout(() => {
    toasts = toasts.filter((t) => t.id !== id)
    emit()
  }, tone === "error" ? 6000 : 2800)
}

export function Toaster() {
  const items = useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => toasts,
  )
  return (
    <div aria-live="polite" className="pointer-events-none fixed bottom-6 left-1/2 z-[60] flex w-[min(440px,calc(100vw-2rem))] -translate-x-1/2 flex-col gap-2">
      <AnimatePresence initial={false}>
        {items.map((t) => (
          <motion.div
            key={t.id}
            layout
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ duration: 0.35, ease: ease.expo }}
            className="pointer-events-auto flex items-start gap-3 border-2 border-ink bg-ink px-4 py-3 text-paper shadow-hard-cobalt"
            role={t.tone === "error" ? "alert" : "status"}
          >
            {t.tone === "ok" ? (
              <CheckCircle weight="fill" className="mt-0.5 size-5 shrink-0 text-signal-high" />
            ) : (
              <WarningCircle weight="fill" className="mt-0.5 size-5 shrink-0 text-unsupported" />
            )}
            <span className="text-body">{t.text}</span>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}

export const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong. Try again.")
