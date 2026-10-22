import { useState } from "react"
import { ChatCircle, Repeat, ThumbsUp } from "@phosphor-icons/react"
import { api, type Media } from "@/lib/api"
import { Modal } from "@/components/ui/modal"

const FOLD = 210 // LinkedIn cuts the feed preview around here

/** An approximation of how the post reads in the feed, with the "see more" fold. */
export function LinkedInPreview({
  open,
  onOpenChange,
  body,
  media,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  body: string
  media: Media[]
}) {
  const [expanded, setExpanded] = useState(false)
  const image = media.find((m) => m.included && m.status === "ready")
  const folded = !expanded && body.length > FOLD

  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Feed preview" description="Roughly how the post reads in the LinkedIn feed.">
      <article className="border border-schematic bg-paper">
        <header className="flex items-center gap-3 p-4">
          <span aria-hidden className="grid size-11 place-items-center rounded-full bg-cobalt font-display text-title text-paper">
            Y
          </span>
          <span>
            <span className="block font-semibold text-ink">You</span>
            <span className="block text-badge text-ink-muted">Now · Public</span>
          </span>
        </header>
        <p className="px-4 pb-3 text-body whitespace-pre-line text-ink">
          {folded ? body.slice(0, FOLD).trimEnd() : body}
          {folded && (
            <button type="button" onClick={() => setExpanded(true)} className="ml-1 cursor-pointer text-ink-muted hover:text-cobalt hover:underline">
              …see more
            </button>
          )}
        </p>
        {image && <img src={api.mediaFileUrl(image.id)} alt={image.alt_text ?? ""} className="aspect-[16/9] w-full bg-midnight object-contain" />}
        <footer className="flex justify-around border-t border-schematic py-2 text-label text-ink-soft">
          {[
            [ThumbsUp, "Like"],
            [ChatCircle, "Comment"],
            [Repeat, "Repost"],
          ].map(([Icon, label]) => {
            const I = Icon as typeof ThumbsUp
            return (
              <span key={label as string} className="flex items-center gap-1.5">
                <I aria-hidden /> {label as string}
              </span>
            )
          })}
        </footer>
      </article>
    </Modal>
  )
}
