import { useRef, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { AnimatePresence, motion } from "motion/react"
import { FileArrowUp, FilmSlate, Graph, Image as ImageIcon, PencilSimple } from "@phosphor-icons/react"
import { api, type Draft, type Media } from "@/lib/api"
import { ease } from "@/lib/motion"
import { cn } from "@/lib/utils"
import { useTrackedJob } from "@/hooks/useJob"
import { Button } from "@/components/ui/button"
import { Modal } from "@/components/ui/modal"
import { Panel, Tag, inputClass } from "@/components/ui/primitives"
import { errorText, toast } from "@/components/ui/toast"

const typeIcon = { diagram: Graph, screenshot: ImageIcon, chart: Graph, pdf: FileArrowUp, video: FilmSlate }

export function MediaTray({ draft, videoEnabled }: { draft: Draft; videoEnabled: boolean }) {
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["drafts", draft.event_id] })
  const video = useTrackedJob((job) => {
    void refresh()
    const ok = job.status === "done"
    toast(ok ? "Video rendered" : "The video didn't render. Check that brag is installed.", ok ? "ok" : "error")
  })

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadMedia(draft.id, file),
    onSuccess: () => {
      toast("Image added to the tray")
      void refresh()
    },
    onError: (e) => toast(errorText(e), "error"),
  })
  const renderVideo = useMutation({
    mutationFn: () => api.renderVideo(draft.id),
    onSuccess: (r) => {
      video.start(r.job_id)
      void refresh()
    },
    onError: (e) => toast(errorText(e), "error"),
  })
  const media = [...draft.media].sort((a, b) => a.position - b.position)

  return (
    <Panel className="p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="label-technical text-ink">Evidence media tray</h2>
        <span className="font-mono text-badge text-cobalt uppercase">{media.filter((m) => m.included).length} attached</span>
      </div>

      <div className="mt-4 flex flex-col gap-4">
        <AnimatePresence initial={false}>
          {media.map((m, i) => (
            <MediaItem key={m.id} media={m} index={i} onChanged={refresh} />
          ))}
        </AnimatePresence>
        {media.length === 0 && (
          <p className="bg-surface-low p-4 text-body text-ink-soft">
            No media yet. Add a screenshot of the result, a benchmark chart, or the diagram LinkedBuddy drew.
          </p>
        )}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <input
          ref={fileInput}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="sr-only"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) upload.mutate(file)
            e.target.value = ""
          }}
        />
        <Button variant="quiet" size="sm" onClick={() => fileInput.current?.click()} disabled={upload.isPending}>
          <FileArrowUp weight="bold" aria-hidden /> {upload.isPending ? "Uploading" : "Add an image"}
        </Button>
        {videoEnabled && (
          <Button variant="quiet" size="sm" onClick={() => renderVideo.mutate()} disabled={renderVideo.isPending || video.running}>
            <FilmSlate weight="bold" aria-hidden /> {video.running ? "Rendering video" : "Render a short video"}
          </Button>
        )}
      </div>
    </Panel>
  )
}

function MediaItem({ media, index, onChanged }: { media: Media; index: number; onChanged: () => void }) {
  const [alt, setAlt] = useState(media.alt_text ?? "")
  const [broken, setBroken] = useState(false)
  const [editing, setEditing] = useState(false)
  const Icon = typeIcon[media.type]

  const patch = useMutation({
    mutationFn: (p: Parameters<typeof api.patchMedia>[1]) => api.patchMedia(media.id, p),
    onSuccess: onChanged,
    onError: (e) => toast(errorText(e), "error"),
  })

  return (
    <motion.figure
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ duration: 0.45, ease: ease.expo, delay: index * 0.05 }}
      className="bg-surface-low p-3"
    >
      <figcaption className="flex items-center justify-between gap-3 pb-2">
        <span className="label-technical flex items-center gap-2 text-ink">
          <Icon weight="bold" className="text-cobalt" aria-hidden />
          Asset {String(index + 1).padStart(2, "0")} // {media.type}
        </span>
        <span className="flex items-center gap-2">
          {media.status !== "ready" && <Tag tone={media.status === "failed" ? "danger" : "medium"}>{media.status}</Tag>}
          {media.mermaid_source !== null && (
            <button type="button" onClick={() => setEditing(true)} className="label-badge flex cursor-pointer items-center gap-1 text-cobalt hover:underline">
              <PencilSimple weight="bold" aria-hidden /> Edit diagram
            </button>
          )}
        </span>
      </figcaption>

      <div className="relative grid aspect-[16/9] place-items-center overflow-hidden bg-midnight">
        {media.status === "ready" && !broken ? (
          <img
            src={api.mediaFileUrl(media.id)}
            alt={media.alt_text ?? ""}
            onError={() => setBroken(true)}
            className="size-full object-contain"
            loading="lazy"
          />
        ) : media.mermaid_source ? (
          <pre data-lenis-prevent className="size-full overflow-auto p-3 font-mono text-badge text-cobalt-tint">
            {media.mermaid_source}
          </pre>
        ) : (
          <span className="font-mono text-label text-ink-muted uppercase">{media.status === "pending" ? "Rendering" : "Preview unavailable"}</span>
        )}
      </div>

      <div className="mt-3 flex flex-col gap-2">
        <label className="flex cursor-pointer items-center gap-2 font-mono text-label text-ink uppercase">
          <input
            type="checkbox"
            checked={media.included}
            onChange={(e) => patch.mutate({ included: e.target.checked })}
            className="size-4 accent-cobalt"
          />
          Include in the post
        </label>
        <input
          aria-label={`Alt text for asset ${index + 1}`}
          value={alt}
          maxLength={1000}
          placeholder="Alt text: describe what the image shows"
          onChange={(e) => setAlt(e.target.value)}
          onBlur={() => alt !== (media.alt_text ?? "") && patch.mutate({ alt_text: alt || null })}
          className={cn(inputClass, "border-[1.5px] py-1.5 text-body")}
        />
      </div>

      <MermaidEditor media={media} open={editing} onOpenChange={setEditing} onSaved={onChanged} />
    </motion.figure>
  )
}

function MermaidEditor({ media, open, onOpenChange, onSaved }: { media: Media; open: boolean; onOpenChange: (o: boolean) => void; onSaved: () => void }) {
  const [source, setSource] = useState(media.mermaid_source ?? "")
  const redraw = useTrackedJob((job) => {
    const ok = job.status === "done"
    onSaved()
    toast(ok ? "Diagram redrawn" : "The diagram didn't render. Check the Mermaid syntax.", ok ? "ok" : "error")
    if (ok) onOpenChange(false)
  })
  const save = useMutation({
    mutationFn: () => api.updateMermaid(media.id, source),
    onSuccess: (r) => redraw.start(r.job_id),
  })
  const busy = save.isPending || redraw.running
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Edit the diagram" description="Mermaid source. Saving redraws the image." className="w-[min(720px,calc(100vw-2rem))]">
      <textarea
        data-lenis-prevent
        value={source}
        onChange={(e) => setSource(e.target.value)}
        rows={12}
        spellCheck={false}
        className={cn(inputClass, "font-mono text-code")}
      />
      {save.isError && <p role="alert" className="mt-3 text-body text-danger">{errorText(save.error)}</p>}
      <div className="mt-4 flex justify-end gap-3">
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button onClick={() => save.mutate()} disabled={!source.trim() || busy}>
          {busy ? "Redrawing" : "Save and redraw"}
        </Button>
      </div>
    </Modal>
  )
}
