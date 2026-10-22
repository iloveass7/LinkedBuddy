import type { ReactNode } from "react"
import { Dialog } from "radix-ui"
import { AnimatePresence, motion } from "motion/react"
import { X } from "@phosphor-icons/react"
import { ease } from "@/lib/motion"
import { cn } from "@/lib/utils"

/** Floating cutout per the design system: ink outline with a cobalt rim, no blur. */
export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  className,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children: ReactNode
  className?: string
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild forceMount>
              <motion.div
                className="fixed inset-0 z-50 bg-midnight/70"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
              />
            </Dialog.Overlay>
            <Dialog.Content asChild forceMount>
              <motion.div
                data-lenis-prevent
                className={cn(
                  "fixed top-1/2 left-1/2 z-50 max-h-[88dvh] w-[min(560px,calc(100vw-2rem))] overflow-y-auto border-[1.5px] border-ink bg-paper p-6 shadow-hard-lift focus:outline-none",
                  className,
                )}
                initial={{ opacity: 0, x: "-50%", y: "-46%", scale: 0.97 }}
                animate={{ opacity: 1, x: "-50%", y: "-50%", scale: 1 }}
                exit={{ opacity: 0, x: "-50%", y: "-48%", scale: 0.98 }}
                transition={{ duration: 0.35, ease: ease.expo }}
              >
                <div className="flex items-start justify-between gap-4">
                  <Dialog.Title className="font-display text-headline-md text-ink">{title}</Dialog.Title>
                  <Dialog.Close
                    aria-label="Close"
                    className="grid size-8 shrink-0 cursor-pointer place-items-center border-2 border-ink transition-transform hover:rotate-90 active:scale-90"
                  >
                    <X weight="bold" />
                  </Dialog.Close>
                </div>
                {description ? (
                  <Dialog.Description className="mt-2 text-body text-ink-soft">{description}</Dialog.Description>
                ) : (
                  <Dialog.Description className="sr-only">{title}</Dialog.Description>
                )}
                <div className="mt-5">{children}</div>
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  )
}
