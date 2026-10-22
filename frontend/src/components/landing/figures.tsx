/*
 * The three hero carousel figures from the Stitch landing screen. They show the kind of change
 * LinkedBuddy turns into a post: a merged PR, a pipeline diagram, a benchmark. Sample content.
 */

import { useEffect, useState } from "react"
import { AnimatePresence, motion } from "motion/react"

const mono = { fontFamily: "Space Mono, monospace" }

export type Figure = {
  label: string
  repo: string
  badge: string
  badgeTone: "high" | "dark" | "medium"
  caption: string
  art: React.ReactNode
}

const commitNodes = [
  { sha: "7b4e99c", msg: "base: initial benchmark harness" },
  { sha: "92df10a", msg: "eval: added low-resource Bengali NER suite" },
  { sha: "c491aa2", msg: "perf: streaming chunk reader" },
  { sha: "3f098cd", msg: "release: v1.4.2 verification passed" },
]

function CommitGraph() {
  const [picked, setPicked] = useState<number | null>(null)
  useEffect(() => {
    if (picked === null) return
    const t = window.setTimeout(() => setPicked(null), 3500)
    return () => window.clearTimeout(t)
  }, [picked])
  const node = (i: number) => ({
    role: "button",
    tabIndex: 0,
    "aria-label": `Commit ${commitNodes[i].sha}: ${commitNodes[i].msg}`,
    onClick: (e: React.MouseEvent) => {
      e.stopPropagation()
      setPicked(i)
    },
    onKeyDown: (e: React.KeyboardEvent) => e.key === "Enter" && setPicked(i),
    className: "cursor-pointer outline-none transition-transform duration-300 ease-expo hover:scale-110 focus-visible:scale-110",
    style: { transformBox: "fill-box", transformOrigin: "center" } as React.CSSProperties,
  })
  return (
    <>
      <svg viewBox="0 0 600 340" fill="none" className="h-full w-full">
        <path d="M60 170 H540" className="stroke-cobalt" strokeWidth={2.5} opacity={0.4} />
        <path d="M150 170 C190 170 210 90 270 90 H370 C430 90 450 170 490 170" className="stroke-cobalt-vivid" strokeWidth={2.5} />
        <path d="M230 170 C270 170 290 250 350 250 H410 C460 250 480 170 510 170" className="stroke-cobalt-vivid animate-flow-dash" strokeWidth={2} strokeDasharray="4 3" opacity={0.75} />
        <g {...node(0)}>
          <circle cx={90} cy={170} r={14} className="fill-paper stroke-cobalt" strokeWidth={3} />
          <circle cx={90} cy={170} r={5} className="fill-cobalt" />
        </g>
        <text x={90} y={200} textAnchor="middle" fontSize={9} fontWeight={700} className="fill-cobalt" style={mono}>#7b4e99c</text>
        <g {...node(1)}>
          <circle cx={320} cy={90} r={18} className="fill-cobalt" />
          <circle cx={320} cy={90} r={6} className="fill-paper" />
        </g>
        <circle cx={320} cy={90} r={28} className="pointer-events-none stroke-sky animate-ping-soft" strokeWidth={1.5} strokeDasharray="3 3" opacity={0.8} style={{ transformBox: "fill-box", transformOrigin: "center" }} />
        <text x={320} y={60} textAnchor="middle" fontSize={10} fontWeight={700} className="fill-cobalt" style={mono}>PR #48 MERGED</text>
        <text x={320} y={125} textAnchor="middle" fontSize={9} className="fill-cobalt-vivid" style={mono}>sha: 92df10a</text>
        <g {...node(2)}>
          <circle cx={380} cy={250} r={13} className="fill-paper stroke-cobalt-vivid" strokeWidth={2.5} />
          <circle cx={380} cy={250} r={4.5} className="fill-cobalt-vivid" />
        </g>
        <text x={380} y={280} textAnchor="middle" fontSize={9} className="fill-ink-muted" style={mono}>sha: c491aa2</text>
        <g {...node(3)}>
          <circle cx={510} cy={170} r={16} className="fill-signal-high stroke-signal-high-ink" strokeWidth={3} />
          <path d="M503 170 l5 5 l9 -10" className="stroke-paper" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" />
        </g>
        <text x={510} y={200} textAnchor="middle" fontSize={9} fontWeight={700} className="fill-signal-high-ink" style={mono}>MAIN</text>
      </svg>
      <AnimatePresence>
        {picked !== null && (
          <motion.p
            role="status"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 6 }}
            className="absolute bottom-2.5 left-3 z-20 rounded bg-ink/90 px-2 py-1 font-mono text-badge text-cobalt-tint backdrop-blur-sm"
          >
            NODE SHA: {commitNodes[picked].sha} // {commitNodes[picked].msg}
          </motion.p>
        )}
      </AnimatePresence>
    </>
  )
}

function Box({ x, y, w, title, sub, filled = false }: { x: number; y: number; w: number; title: string; sub?: string; filled?: boolean }) {
  return (
    <g className="transition-transform duration-300 ease-expo hover:-translate-y-1">
      <rect x={x} y={y} width={w} height={sub ? 44 : 40} className={filled ? "fill-cobalt stroke-ink" : "fill-paper stroke-cobalt"} strokeWidth={2} />
      <text x={x + w / 2} y={y + (sub ? 19 : 25)} textAnchor="middle" fontSize={10} fontWeight={700} className={filled ? "fill-paper" : "fill-cobalt"} style={mono}>
        {title}
      </text>
      {sub && (
        <text x={x + w / 2} y={y + 34} textAnchor="middle" fontSize={8} className={filled ? "fill-cobalt-tint" : "fill-ink-muted"} style={mono}>
          {sub}
        </text>
      )}
    </g>
  )
}

function RerankPipeline() {
  return (
    <svg viewBox="0 0 600 340" fill="none" className="h-full w-full" aria-hidden>
      <path d="M150 170 C190 170 190 100 245 100" className="stroke-cobalt animate-flow-dash" strokeWidth={2} strokeDasharray="6 4" />
      <path d="M150 170 C190 170 190 240 245 240" className="stroke-cobalt animate-flow-dash" strokeWidth={2} strokeDasharray="6 4" />
      <path d="M375 100 C420 100 420 170 450 170" className="stroke-cobalt" strokeWidth={2} />
      <path d="M375 240 C420 240 420 170 450 170" className="stroke-cobalt" strokeWidth={2} />
      <path d="M478 170 L500 170" className="stroke-cobalt" strokeWidth={2} />
      <Box x={40} y={150} w={110} title="QUERY" />
      <Box x={245} y={78} w={130} title="Dense vector" sub="HNSW · 768-dim" />
      <Box x={245} y={218} w={130} title="Sparse BM25" sub="inverted postings" />
      <circle cx={464} cy={170} r={14} className="fill-cobalt-vivid" />
      <circle cx={464} cy={170} r={22} className="stroke-cobalt animate-ping-soft" strokeWidth={1.5} strokeDasharray="3 3" style={{ transformBox: "fill-box", transformOrigin: "center" }} />
      <Box x={500} y={148} w={80} title="RERANK" sub="+18.4% MRR" filled />
    </svg>
  )
}

function ZeroCopy() {
  return (
    <svg viewBox="0 0 600 340" fill="none" className="h-full w-full" aria-hidden>
      <rect x={40} y={80} width={160} height={68} className="fill-paper stroke-cobalt" strokeWidth={2} />
      <text x={120} y={110} textAnchor="middle" fontSize={10} fontWeight={700} className="fill-cobalt" style={mono}>MMAP BUFFER</text>
      <text x={120} y={130} textAnchor="middle" fontSize={8} className="fill-ink-muted" style={mono}>&amp;[u8] borrowed</text>
      <path d="M200 114 L290 114" className="stroke-cobalt animate-flow-dash" strokeWidth={2.5} strokeDasharray="6 4" />
      <polygon points="290,110 300,114 290,118" className="fill-cobalt" />
      <rect x={300} y={76} width={140} height={76} className="fill-cobalt" />
      <text x={370} y={110} textAnchor="middle" fontSize={11} fontWeight={700} className="fill-paper" style={mono}>BORROWED</text>
      <text x={370} y={128} textAnchor="middle" fontSize={8} className="fill-cobalt-tint" style={mono}>Record&lt;'a&gt; · 0 heap</text>
      <circle cx={490} cy={114} r={26} className="fill-paper stroke-signal-high" strokeWidth={3} />
      <text x={490} y={112} textAnchor="middle" fontSize={11} fontWeight={700} className="fill-signal-high-ink" style={mono}>0 ALLOC</text>
      <text x={490} y={125} textAnchor="middle" fontSize={7.5} className="fill-ink-muted" style={mono}>-68% GC</text>
      <g transform="translate(40 190)">
        <rect width={510} height={76} className="fill-paper stroke-schematic" strokeWidth={1} />
        <text x={12} y={20} fontSize={8.5} fontWeight={700} className="fill-cobalt-deep" style={mono}>THROUGHPUT, MB/s ON A 4 GB LOG</text>
        <text x={12} y={42} fontSize={8} className="fill-ink-muted" style={mono}>serde_json</text>
        <rect x={150} y={32} width={120} height={13} className="fill-schematic" />
        <text x={280} y={42} fontSize={8} className="fill-ink-muted" style={mono}>310</text>
        <text x={12} y={64} fontSize={8} fontWeight={700} className="fill-cobalt" style={mono}>zero-copy mmap</text>
        <rect x={150} y={54} width={290} height={13} className="fill-cobalt-vivid" />
        <text x={448} y={64} fontSize={8} fontWeight={700} className="fill-cobalt" style={mono}>1,840</text>
      </g>
    </svg>
  )
}

export const figures: Figure[] = [
  {
    label: "Fig 01. Commit graph diff",
    repo: "bangla-ner-benchmark",
    badge: "eval table in README",
    badgeTone: "high",
    caption: "Benchmark suite for low-resource Bengali NER, with a reproducible notebook",
    art: <CommitGraph />,
  },
  {
    label: "Fig 02. Hybrid reranker pipeline",
    repo: "retrieval-pipeline",
    badge: "diff +640 / −82 · 14.2 ms p95",
    badgeTone: "dark",
    caption: "Two-stage vector search with a cross-encoder reranker (+18.4% MRR@10 on tech docs)",
    art: <RerankPipeline />,
  },
  {
    label: "Fig 03. Zero-copy deserializer",
    repo: "rust-kv-engine",
    badge: "bench: 0 alloc in hot path",
    badgeTone: "medium",
    caption: "Zero-copy deserializer for append-log records, cutting GC pauses by 68%",
    art: <ZeroCopy />,
  },
]
