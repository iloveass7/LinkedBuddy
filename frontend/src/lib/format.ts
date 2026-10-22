import type { EventType, Tone, RepoMode, PrivacyMode } from "@/lib/api"

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" })

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never"
  const seconds = (new Date(iso).getTime() - Date.now()) / 1000
  const steps: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["week", 604_800],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ]
  for (const [unit, size] of steps) {
    if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit)
  }
  return "just now"
}

export const eventTypeLabel: Record<EventType, string> = {
  new_repo: "New repository",
  release: "Release",
  pr_merged: "PR merged",
  push_group: "Push group",
  manual: "Manual range",
  labeled: "Marked [post]",
}

export const toneLabel: Record<Tone, string> = {
  professional: "Professional",
  casual: "Casual",
  technical: "Technical",
  storytelling: "Storytelling",
}

export const repoModeLabel: Record<RepoMode, string> = {
  always_suggest: "Always suggest",
  releases_and_large: "Releases and large changes",
  manual_only: "Manual trigger only",
  muted: "Muted",
}

export const privacyModes: { value: PrivacyMode; title: string; body: string }[] = [
  { value: "full", title: "Full diff", body: "Metadata and raw diffs go to the model. The most technical detail." },
  { value: "metadata_plus_readme", title: "Metadata + README", body: "Commit messages, stats and the README. No code diffs." },
  { value: "metadata_only", title: "Metadata only", body: "Commit messages, tags and stats. No file contents at all." },
  { value: "never_send", title: "Never send", body: "Nothing reaches the model. Drafting is off." },
]

export const shortSha = (sha: string | null | undefined) => (sha ? sha.slice(0, 7) : "—")

export const repoName = (fullName: string) => fullName.split("/").pop() ?? fullName

export const plural = (n: number, word: string) =>
  `${n} ${n === 1 ? word : word.endsWith("y") && !/[aeiou]y$/.test(word) ? `${word.slice(0, -1)}ies` : `${word}s`}`
