/**
 * The only module that talks to the backend. Every request sends the session cookie;
 * every non-GET request also sends the CSRF header the backend requires (SECURITY.md 3.1).
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL.replace(/\/$/, "")

export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  /** Seconds from the `Retry-After` header, when the backend sent one (rate limits). */
  readonly retryAfter: number | null

  constructor(status: number, detail: string, retryAfter: number | null = null) {
    super(detail)
    this.status = status
    this.detail = detail
    this.retryAfter = retryAfter
  }
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE"
  json?: unknown
  form?: FormData
  query?: Record<string, string | number | undefined>
}

async function request<T>(path: string, { method = "GET", json, form, query }: RequestOptions = {}): Promise<T> {
  const url = new URL(BASE_URL + path, window.location.origin)
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value))
  }
  const headers: Record<string, string> = { Accept: "application/json" }
  if (method !== "GET") headers["X-Requested-With"] = "linkedbuddy"
  if (json !== undefined) headers["Content-Type"] = "application/json"

  const res = await fetch(url, {
    method,
    headers,
    credentials: "include",
    body: form ?? (json === undefined ? undefined : JSON.stringify(json)),
  })
  if (!res.ok) throw new ApiError(res.status, await errorDetail(res), retryAfter(res))
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

function retryAfter(res: Response): number | null {
  const seconds = Number(res.headers.get("Retry-After"))
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === "string") return body.detail
    if (Array.isArray(body.detail)) return "Some fields are invalid. Check them and try again."
  } catch {
    // not JSON: fall through to the status text
  }
  return res.statusText || `Request failed (${res.status})`
}

// ---- Types (mirror backend/src/linkedbuddy/**/router.py) ----

export type Tone = "professional" | "casual" | "technical" | "storytelling"
export type PostLength = "short" | "medium" | "long"
export type EmojiPolicy = "none" | "light" | "any"
export type PrivacyMode = "full" | "metadata_plus_readme" | "metadata_only" | "never_send"
export type RepoMode = "always_suggest" | "releases_and_large" | "manual_only" | "muted"
export type Rating = "high" | "medium" | "low"
export type EventType = "new_repo" | "release" | "pr_merged" | "push_group" | "manual" | "labeled"
export type EventStatus = "new" | "suggested" | "generating" | "drafted" | "dismissed" | "posted"
export type DraftStatus = "draft" | "approved" | "discarded" | "posted"
export type JobStatus = "queued" | "running" | "done" | "failed"
export type QuickEditPreset = "shorter" | "more_casual" | "less_buzzwordy"

export type Me = { github_connected: boolean; gemini_configured: boolean; brag_configured: boolean }

export type InboxItem = {
  id: string
  repo_full_name: string
  type: EventType
  rating: Rating | null
  rating_reason: string | null
  heuristic_score: number
  status: EventStatus
  created_at: string
  stats_summary: string
}
export type Page<T> = { items: T[]; next_cursor: string | null }

export type Repo = {
  id: string
  full_name: string
  visibility: "public" | "private"
  mode: RepoMode
  never_send_to_llm: boolean
  demo_url: string | null
  last_polled_at: string | null
}
export type RepoPatch = {
  mode?: RepoMode
  demo_url?: string | null
  never_send_to_llm?: boolean
  acknowledge_free_tier_data_use?: boolean
}

export type EventOut = {
  id: string
  repo_id: string
  type: EventType
  status: EventStatus
  base_sha: string | null
  head_sha: string | null
  heuristic_score: number
  stats: Record<string, unknown>
  created_at: string
}

export type Segment = { text: string; evidence: string[]; supported: boolean }
export type Lint = {
  banned_hits: string[]
  buzzword_count: number
  specificity_count: number
  char_count: number
  over_limit: boolean
}
export type Media = {
  id: string
  type: "screenshot" | "diagram" | "chart" | "pdf" | "video"
  source: "uploaded" | "generated" | "repo"
  status: "pending" | "ready" | "failed"
  alt_text: string | null
  position: number
  included: boolean
  mermaid_source: string | null
}
export type Draft = {
  id: string
  event_id: string
  parent_draft_id: string | null
  variant_index: number
  angle: string
  tone: Tone
  body: string
  segments: Segment[]
  alt_openings: string[]
  first_comment: string | null
  lint: Partial<Lint>
  unsupported_claims: number
  specificity_count: number
  status: DraftStatus
  media: Media[]
}

export type Job = {
  id: string
  kind: string
  status: JobStatus
  attempts: number
  error: string | null
  result: Record<string, unknown> | null
}
export type JobAccepted = { job_id: string }

export type Post = {
  id: string
  draft_id: string
  posted_at: string
  publish_method: "manual" | "api"
  final_text: string
  covered_base_sha: string | null
  covered_head_sha: string | null
  metrics: Partial<Record<MetricName, number>>
}
export type MetricName = "impressions" | "reactions" | "comments" | "reposts" | "saves"

export type Settings = {
  goals_text: string
  default_tone: Tone
  custom_instructions: string
  ban_list: string[]
  voice_samples: string[]
  post_length: PostLength
  emoji_policy: EmojiPolicy
  hashtag_count: number
  privacy_mode: PrivacyMode
}

// ---- Endpoints ----

export const api = {
  health: () => request<{ status: string; db: string }>("/health"),
  login: (password: string) => request<void>("/auth/login", { method: "POST", json: { password } }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<Me>("/auth/me"),

  inbox: (params: { status?: EventStatus; cursor?: string; limit?: number } = {}) =>
    request<Page<InboxItem>>("/inbox", { query: params }),
  dismissEvent: (eventId: string) => request<void>(`/events/${eventId}/dismiss`, { method: "POST" }),

  repos: () => request<Repo[]>("/repos"),
  syncRepos: () => request<{ synced: number }>("/repos/sync", { method: "POST" }),
  patchRepo: (repoId: string, patch: RepoPatch) => request<Repo>(`/repos/${repoId}`, { method: "PATCH", json: patch }),
  pollRepo: (repoId: string) => request<JobAccepted>(`/repos/${repoId}/poll`, { method: "POST" }),
  createManualEvent: (repoId: string, range: { base_sha?: string; head_sha?: string }) =>
    request<EventOut>(`/repos/${repoId}/events`, { method: "POST", json: range }),
  connectGitHub: (token: string) => request<void>("/connections/github", { method: "PUT", json: { token } }),

  generate: (eventId: string, tone?: Tone) =>
    request<JobAccepted>(`/events/${eventId}/generate`, { method: "POST", json: tone ? { tone } : {} }),
  eventDrafts: (eventId: string) => request<Draft[]>(`/events/${eventId}/drafts`),
  draft: (draftId: string) => request<Draft>(`/drafts/${draftId}`),
  patchDraft: (draftId: string, patch: { body?: string; status?: "approved" | "discarded" | "draft" }) =>
    request<Draft>(`/drafts/${draftId}`, { method: "PATCH", json: patch }),
  regenerate: (draftId: string, tone: Tone) =>
    request<JobAccepted>(`/drafts/${draftId}/regenerate`, { method: "POST", json: { tone } }),
  // Inline Gemini call (ARCHITECTURE.md 5.3): answers 201 with the new child draft, not a job.
  quickEdit: (draftId: string, instruction: QuickEditPreset | string) =>
    request<Draft>(`/drafts/${draftId}/quick-edit`, { method: "POST", json: { instruction } }),

  uploadMedia: (draftId: string, file: File) => {
    const form = new FormData()
    form.append("file", file)
    return request<Media>(`/drafts/${draftId}/media`, { method: "POST", form })
  },
  patchMedia: (mediaId: string, patch: { alt_text?: string | null; position?: number; included?: boolean }) =>
    request<Media>(`/media/${mediaId}`, { method: "PATCH", json: patch }),
  updateMermaid: (mediaId: string, source: string) =>
    request<JobAccepted>(`/media/${mediaId}/mermaid`, { method: "PUT", json: { source } }),
  mediaFileUrl: (mediaId: string) => `${BASE_URL}/media/${mediaId}/file`,
  renderVideo: (draftId: string) =>
    request<JobAccepted & { media_id: string }>(`/drafts/${draftId}/video`, { method: "POST" }),

  job: (jobId: string) => request<Job>(`/jobs/${jobId}`),

  publish: (draftId: string, finalText: string) =>
    request<Post>(`/drafts/${draftId}/publish`, { method: "POST", json: { final_text: finalText, method: "manual" } }),
  repoPosts: (repoId: string, cursor?: string) => request<Page<Post>>(`/repos/${repoId}/posts`, { query: { cursor } }),
  patchMetrics: (postId: string, metrics: Partial<Record<MetricName, number>>) =>
    request<Post>(`/posts/${postId}/metrics`, { method: "PATCH", json: metrics }),

  settings: () => request<Settings>("/settings"),
  saveSettings: (settings: Settings) => request<Settings>("/settings", { method: "PUT", json: settings }),
}
