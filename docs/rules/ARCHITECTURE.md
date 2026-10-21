# Backend Architecture & Performance Rules

**Status:** Binding. These rules keep the backend fast, predictable, and cheap. "Fast" here means a quick event loop, quick DB queries, and no needless network or model calls. Slow external work (Gemini, rendering) is moved off the request path so the UI never waits on it unless the user explicitly asked for that one result.

## 1. Stack (free only)

| Layer | Choice | Why |
|---|---|---|
| Runtime | Python 3.12, `uv` for deps | Fast installs, lockfile |
| Web | FastAPI + `uvicorn[standard]` (uvloop, httptools) | Async, Rust-backed Pydantic v2 serialization |
| DB | Supabase-hosted Postgres (free tier, session pooler) + SQLAlchemy 2.0 async + asyncpg | See DATABASE.md |
| HTTP out | One shared `httpx.AsyncClient` | Connection pooling, HTTP keep-alive |
| LLM | Gemini API free tier via `google-genai` (`client.aio`) | Free; JSON schema output |
| Jobs | Postgres-backed queue (DATABASE.md §8), separate worker processes | No Redis, nothing paid |
| Media storage | Local disk under `MEDIA_ROOT` | Free, fastest |
| Diagrams | `mmdc` (mermaid-cli) subprocess | Free, text source the user edits |
| Video | `brag` skill/tool behind the `VideoRenderer` adapter | Opt-in only |

**Rejected for now:** paid tiers of anything, Redis, Celery, S3, LinkedIn partner APIs, paid LLMs, TTS. Re-adding any of these needs an explicit owner decision.

## 2. Process layout

```
uvicorn linkedbuddy.main:app        # API: HTTP only, never renders or calls Gemini in bulk
python -m linkedbuddy.worker        # kinds: poll_repo, rate_event, generate_draft, render_diagram  (concurrency 4)
python -m linkedbuddy.worker --kinds render_video --concurrency 1   # heavy, isolated
python -m linkedbuddy.scheduler     # enqueues polls, reclaims stale jobs, purges retention
```

All four start together with `backend/scripts/dev.sh` (one shell script with a `trap` that stops all of them on Ctrl-C; no Docker). The database is Supabase. One codebase and one package. Processes communicate only through Postgres (rows + `NOTIFY`).

## 3. Module boundaries

```
src/linkedbuddy/
  main.py            app factory + lifespan (engine, httpx client, gemini client created once)
  config.py          Settings (pydantic-settings); the only place env is read
  db/                engine.py, models.py, session dependency
  security/          auth.py (sessions, require_owner), crypto.py (Fernet), redact.py, middleware.py
  github/            client.py (ETag, pagination), classify.py (pure), ingest.py (poll → commits → events)
  triage/            score.py (pure heuristic + gate), rate.py (LLM judge job)
  llm/               provider.py (Protocol + errors), gemini.py, ratelimit.py, fake.py (tests)
  drafts/            context.py (ContextPackage), prompts.py, generate.py, validate.py, lint.py, router.py
  media/             storage.py, images.py, mermaid.py, router.py
  video/             renderer.py (Protocol + VideoScript), brag.py, router.py
  jobs/              queue.py, worker.py, handlers.py
  scheduler.py
  repos/router.py  inbox/router.py  settings/router.py  posts/router.py  auth/router.py
```

- **Pure logic stays pure.** `classify.py`, `score.py`, `validate.py`, `lint.py`, and `redact.py` take plain data, return plain data, and do no I/O. They are the hot path and are unit-tested without a DB.
- **Routers are thin.** They parse input, call one service function, and return a response model. No business logic, no SQL in routers beyond a single `session.get()`.
- **Interfaces never talk to each other directly.** The website and a future bot both call the same service functions.
- **Only `llm/gemini.py` imports `google.genai`.** Everything else depends on the `LLMProvider` protocol, so tests use `FakeLLM` and the provider can be swapped.

## 4. The pipeline

```
scheduler ──every POLL_INTERVAL_MINUTES──▶ enqueue poll_repo(repo)   [dedupe_key=poll:<repo_id>]
poll_repo:  GET commits page (If-None-Match), cut at last_seen_sha ── 304 → done, zero cost
            200 → fetch commit details concurrently (Semaphore 8) → classify (pure)
                → upsert commits (one statement) → group into event (dedupe_key) → heuristic score (pure)
                → apply repo mode + gate → status suggested|new
                → if passed gate and repo allows LLM: enqueue rate_event
rate_event: build compact summary (no diff) → redact → Gemini fast model → validate → store rating
user:       GET /inbox (one indexed query) → POST /events/{id}/generate → 202 {job_id}
generate_draft: build ContextPackage (cached on event by context_hash) → redact → Gemini quality model
                → validate evidence ids → lint → insert drafts → enqueue render_diagram
render_diagram: validate mermaid → mmdc subprocess → media row ready
user:       review, quick-edit (inline), regenerate (job), upload screenshots, optional video (job)
publish:    owner sends final text → post row with covered sha range → event posted
```

**The cost rule (from the pitch, enforced):** no LLM call happens for an event unless it passed the free heuristic gate (rating) or the owner clicked something (drafts, edits, video). Nothing else may call Gemini.

## 5. Request-path rules

1. **Never block the event loop.** No sync I/O in `async def` (no `requests`, no sync DB driver, no `open()` on large files, no `time.sleep`). CPU work over ~5 ms (Pillow re-encode, big JSON diffing) goes to `await asyncio.to_thread(...)`. Subprocesses use `asyncio.create_subprocess_exec`.
2. **Declare `response_model` (or a typed return) on every endpoint**, so FastAPI serializes through pydantic-core (Rust) and drops unknown fields.
3. **What may run inline in a request:** DB reads and writes, and **one** Gemini call that the owner is actively waiting for and that has no side effects beyond inserting its result (only `POST /drafts/{id}/quick-edit`), with a 30 s timeout. Everything else that touches Gemini, GitHub, `mmdc`, or video goes through a job and returns `202 {job_id}`.
4. **Job status polling is cheap.** `GET /api/jobs/{id}` is a primary-key lookup, so the frontend polls it every 1 s while a job runs.
5. **Round-trip budgets.** The DB is remote (Supabase), so every query costs one network round trip (~20–150 ms). Speed comes from **counting round trips**, not micro-optimising: inbox list = **1** query; draft detail = **≤ 2** queries; job enqueue = **1** statement; `GET /api/jobs/{id}` = **1** query. Tests assert these counts with a statement-counting fixture. A timing middleware adds a `Server-Timing: app;dur=<ms>, db;dur=<ms>` header so the network share stays visible. Choose the Supabase region closest to you.
6. **Startup is lazy-free.** Clients (DB engine, httpx, Gemini) are created once in `lifespan` and stored on `app.state`. No per-request client construction.

## 6. External-call rules

### GitHub
- **Conditional requests always:** store `etag` per repo and send `If-None-Match`. A `304` does not count against the rate limit.
- **Fetch only what changed (sha-based):** `commits?sha=<default_branch>&per_page=100` (no `since`, because GitHub filters on committer date, which drops late-pushed commits; the stable URL also keeps the ETag useful). Cut the list at `last_seen_sha`. If `last_seen_sha` is not on the page, call `compare`, and treat `None` as a force-push. Then `GET /commits/{sha}` per new commit, run concurrently with `asyncio.Semaphore(8)`. The release check runs on every poll, including a 304.
- **Cap work per poll:** at most 100 new commits per repo per poll (older ones are classified as one `push_group` with `stats.truncated=true`). At most 300 files per commit (GitHub's own cap).
- **Respect rate limits:** read `x-ratelimit-remaining`. When it drops below 100, stop the poll and re-enqueue the job with `run_after = x-ratelimit-reset`. On `403`/`429` with `retry-after`, re-enqueue with that delay.
- **Known edge cases:** `409` on commits (empty repo) means no events, no error. `404` on compare (force-push) means falling back to a `push_group` with `base_sha = NULL`. A missing README (`404`) leaves `readme = None`.

### Gemini (free tier)
- **Two tiers, configurable:** `GEMINI_MODEL_FAST` (default `gemini-flash-lite-latest`) for classification and rating, and `GEMINI_MODEL_QUALITY` (default `gemini-flash-latest`) for drafts, video scripts, and quick edits. Never hard-code a model name outside `config.py`.
- **Client-side rate limiter:** an async token bucket per model tier (`GEMINI_RPM_FAST`, `GEMINI_RPM_QUALITY`, default 10 and 5). Free-tier limits change, so they are config, not constants.
- **On `429`:** raise `LLMRateLimited(retry_after)`. A job re-enqueues itself with `run_after = now + retry_after` (exponential, starting at 10 s, max 10 min) and does **not** count it as a failed attempt. An inline quick-edit returns `503` with `Retry-After`.
- **Structured output only:** `response_mime_type="application/json"` + `response_schema=<PydanticModel>`, then re-validate with `Model.model_validate_json`. One retry on invalid output, then `LLMInvalidOutput`.
- **Token discipline:** the context package has a hard budget of `CONTEXT_CHAR_BUDGET = 24_000` chars. The rating summary is ≤ 4,000 chars. The README is trimmed to its first 150 lines plus any table blocks. Diffs are never sent in the rating call.
- **Cache:** `ContextPackage` is stored on the event (`context_json`, `context_hash`). Regenerate, tone switches, and quick edits reuse it and do not re-fetch GitHub.
- **Temperatures:** rating 0.1, drafts 0.8, quick edits 0.5, video script 0.6.

### LinkedIn (later plan)
- Copy-and-paste is the default publishing path and needs no API. API publishing is an adapter added in a later plan.

## 7. Worker rules

1. **Handlers are idempotent.** Re-running a job with the same payload must not duplicate rows (upserts, dedupe keys, checking the current status first).
2. **Handlers update user-visible status at the start and the end.** `generate_draft` sets the event to `generating` first, then `drafted` on success. On final failure it restores the previous status and stores `jobs.error`, so an event is never stuck in `generating`.
3. **One handler, one job kind**, registered in `jobs/handlers.py: HANDLERS: dict[str, Handler]`.
4. **Video gets its own process with concurrency 1**, so a long render can never starve polls or drafts.
5. **Graceful shutdown:** on SIGTERM, stop claiming and finish (or time out at 30 s) the current jobs. Unfinished ones are reclaimed by the stale-lock rule.

## 8. Caching rules

- **Settings** are cached in-process for 30 s (a single row, read on every draft). Writes invalidate the local cache. The worker accepts up to 30 s of staleness.
- **No HTTP caching layer, no Redis.** Postgres + ETags + `context_json` cover every real need for one user.

## 9. Observability (free)

- Structured JSON logs (stdlib `logging` + a JSON formatter): `event`, `job_id`, `kind`, `duration_ms`, `status`. No content (SECURITY.md §7).
- `GET /api/health` checks the DB with `SELECT 1` and returns the queue depth per kind.
- Every job records `duration_ms` in `jobs.result`. Gemini calls log the model, tier, latency, and token counts from `usage_metadata`.

## 10. Code style

- `ruff` (lint + format) and `mypy --strict` on `src/`. Both must pass.
- Tests: `pytest` + `pytest-asyncio` (auto mode), `respx` for HTTP mocks, `FakeLLM` for Gemini. **Tests never call the real Gemini, GitHub, or LinkedIn.**
- Each task follows TDD: failing test → minimal code → passing test → commit.

## 11. Checklist for every PR

- [ ] No sync I/O or CPU-heavy work in an `async def` on the request path
- [ ] Slow external work is in a job, or it is the single allowed inline quick-edit
- [ ] Any new Gemini call is gated by the cost rule, rate-limited, and schema-validated
- [ ] Any new GitHub call is conditional (ETag) or bounded
- [ ] New queries are indexed and paginated (DATABASE.md §7)
- [ ] Handlers are idempotent and restore status on failure
