import type { ReactNode } from "react"
import { motion } from "motion/react"
import { stagger } from "@/lib/motion"

/** The white header slab at the top of every app screen: stage tag, title, one line, actions. */
export function PageHeader({
  stage,
  title,
  description,
  meta,
  actions,
}: {
  stage: string
  title: string
  description?: ReactNode
  meta?: ReactNode
  actions?: ReactNode
}) {
  return (
    <motion.section
      variants={stagger.container}
      initial="hidden"
      animate="show"
      className="flex flex-col gap-6 border-b-2 border-ink bg-paper p-5 sm:p-8 lg:flex-row lg:items-end lg:justify-between"
    >
      <div className="min-w-0">
        <motion.p variants={stagger.item} className="label-technical flex flex-wrap items-center gap-3 text-cobalt">
          <span className="flex items-center gap-2">
            <span aria-hidden className="size-2.5 bg-cobalt" />
            {stage}
          </span>
          {meta}
        </motion.p>
        <motion.h1 variants={stagger.item} className="mt-3 font-display text-headline-xl-sm text-ink sm:text-headline-xl">
          {title}
        </motion.h1>
        {description && (
          <motion.p variants={stagger.item} className="mt-2 max-w-2xl text-body-lg text-ink-soft">
            {description}
          </motion.p>
        )}
      </div>
      {actions && (
        <motion.div variants={stagger.item} className="flex shrink-0 flex-wrap gap-3">
          {actions}
        </motion.div>
      )}
    </motion.section>
  )
}
