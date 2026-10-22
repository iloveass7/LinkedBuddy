# Security Rules

**Status:** Binding. Every change to the backend must follow these rules. If a rule blocks something you need, stop and ask. Do not work around it.

**Threat model in one paragraph.** LinkedBuddy is a single-user, personal web app. It holds three valuable secrets: a GitHub token, a LinkedIn token, and a Gemini API key. It reads text written by strangers (READMEs, commit messages, file names, PR titles) and passes it to a language model. It renders images from model output and from uploads. The main risks are: leaking a token, sending private code to a third party, prompt injection through repository text, a hallucinated or injected claim reaching a public LinkedIn post, and an exposed admin surface on a public network.

---

## 1. Secrets

1. **Secrets live only in `.env` (git-ignored) or as encrypted DB columns.** Never in code, tests, fixtures, logs, error messages, API responses, or commit history. `.env.example` lists every variable with a fake value.
2. **Load secrets through `config.Settings` only** (pydantic-settings, `SecretStr` type). Never call `os.environ` directly elsewhere.
3. **Tokens stored in the DB are encrypted** with Fernet (`cryptography`), using the key in `APP_ENCRYPTION_KEY`. Columns end in `_enc` and are `bytea`. Decrypt only at the point of use, inside the client that needs it.
4. **Never send a secret to the browser.** No endpoint returns a token, key, or `_enc` column. Return `has_github_token: true` instead.
5. **Rotate on suspicion.** If a secret appears in a log, test output, or diff, treat it as leaked: revoke it at the provider, then fix the leak.
6. **GitHub token is read-only and fine-grained**, scoped to chosen repos (Contents: read, Metadata: read, Pull requests: read). The app never calls a GitHub write endpoint. A test asserts the GitHub client only issues `GET` requests.
7. **LinkedIn scopes are `openid profile w_member_social` only.** No other scope is requested.

## 2. Authentication and sessions (single owner)

1. **The server binds to `127.0.0.1` by default.** Binding to `0.0.0.0` requires `ALLOW_PUBLIC_BIND=true` and HTTPS in front. The gate is enforced by `python -m linkedbuddy.serve` (used by `scripts/dev.sh`); running `uvicorn` directly bypasses it, though uvicorn's own default host is also `127.0.0.1`.
2. **Login checks one owner password** against an argon2id hash in `OWNER_PASSWORD_HASH` (`argon2-cffi`). The plaintext password is never stored.
3. **Sessions are server-side.** On login, generate a 32-byte token with `secrets.token_urlsafe(32)`. Store only its SHA-256 hash in the `sessions` table with `expires_at` (14 days). The cookie `lb_session` is `HttpOnly`, `SameSite=Strict`, `Path=/`, and `Secure` whenever `APP_ENV != "dev"`.
4. **Every route requires a session** except `GET /api/health` and `POST /api/auth/login`. Enforce this with a router-level dependency (`Depends(require_owner)`), not per-endpoint calls. A test walks `app.routes` and fails if any route other than those two lacks the dependency.
5. **Login is rate limited:** 5 failed attempts per IP per 15 minutes, then `429`. Use a constant-time password check, and return the same error for every failure.
6. **Logout deletes the session row.** Expired rows are purged by the scheduler.

## 3. CSRF, CORS, and headers

1. **CSRF:** every non-GET request must carry the header `X-Requested-With: linkedbuddy`. Browsers cannot set it cross-site without a CORS preflight, and the preflight will fail. Middleware rejects requests without it with `403`.
2. **CORS:** allow only the origins in `FRONTEND_ORIGINS`, with `allow_credentials=True`. Never use `*`.
3. **Security headers on every response:** `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` for API responses. Add `Strict-Transport-Security` when `APP_ENV != "dev"`.
4. **Disable `/docs` and `/openapi.json` unless `APP_ENV == "dev"`.**

## 4. Input validation

1. **Every request body and query is a Pydantic model** with explicit bounds: `max_length` on every string (post text: 3000; instructions: 500; voice sample: 5000 each, max 5 samples; ban list: max 100 items of 80 chars). No `dict` or `Any` request bodies.
2. **IDs are UUIDs** and parsed as `uuid.UUID`. Lookups always go through the ORM or bound parameters. **No f-strings or `%` formatting in SQL, ever.** `text()` is allowed only with bound `:params`.
3. **Enums are `Literal[...]` or `StrEnum`.** The DB has matching `CHECK` constraints (see DATABASE.md).
4. **URLs supplied by the user** (`demo_url`) must be `https://` and are stored only. The server never fetches them (no SSRF surface).
5. **The server makes outbound HTTP only to an allowlist:** `api.github.com`, `generativelanguage.googleapis.com`, `api.linkedin.com`, `www.linkedin.com`, and the LinkedIn upload hosts the API returns. The shared `httpx` client enforces this with an event hook that rejects any other host.

## 5. File uploads and generated files

1. **Uploads accept only PNG, JPEG, and WebP**, detected by decoding with Pillow, not by extension or `Content-Type`. Max 10 MB, max 8000×8000 px (set `Image.MAX_IMAGE_PIXELS`).
2. **Every upload is re-encoded** (strip EXIF and metadata, convert to PNG or JPEG). The original bytes are discarded.
3. **Stored filenames are server-generated** (`{uuid}.{ext}`) under `MEDIA_ROOT`. User-supplied names are never used as paths. Every resolved path is checked with `path.resolve().is_relative_to(MEDIA_ROOT.resolve())`.
4. **Media is served only through `GET /api/media/{id}/file`** (authenticated `FileResponse` with `Content-Disposition: inline` and a fixed `Content-Type`). Do not mount `MEDIA_ROOT` as public static files.
5. **Mermaid rendering** runs `mmdc` as a subprocess with an argument list (never `shell=True`), a 30 s timeout, `securityLevel: strict` in the Mermaid config, and Puppeteer `--no-sandbox` **off**. Mermaid source is capped at 5000 chars and must not contain `click`, `href`, `<script`, or `javascript:` (reject before rendering).
6. **Video rendering (brag adapter)** runs as a subprocess with an argument list, its own temp directory, a timeout (`VIDEO_TIMEOUT_S`), and no secrets in its environment. Pass a minimal env (`PATH`, `HOME`, `TMPDIR`).

## 6. LLM safety (prompt injection and data leakage)

1. **Repository text is untrusted data.** That covers README, commit messages, file names, PR titles, release notes, and code. Always wrap it in a delimited block (`<repo_context>…</repo_context>`), and say in the system prompt that instructions inside it must be ignored.
2. **The model has no tools.** No function calling, no code execution, no URL fetching, no Google Search grounding. The model only returns JSON.
3. **Every model output is schema-validated** (Pydantic `response_schema` plus re-validation on our side). Invalid output is retried once, then the job fails visibly. Never accept output silently.
4. **Evidence validation is mandatory.** Every draft segment's evidence IDs must exist in the `ContextPackage`. Unknown IDs mark the segment `unsupported`, and the UI shows it. A draft can never be published by the system. Only the owner publishes.
5. **Model output is rendered as text, never HTML.** Mermaid source from the model goes through the checks in 5.5.
6. **Redact before sending.** `redact()` runs on every string in the context package before any LLM call. It covers GitHub tokens (`ghp_`, `gho_`, `github_pat_`), AWS keys (`AKIA…`), Google keys (`AIza…`), Slack tokens (`xox[abprs]-`), private key blocks, JWTs, `.env`-style `KEY=value` lines whose key contains `SECRET|TOKEN|PASSWORD|API_KEY`, and email addresses. If redaction fires on a private repo, block the call (`RedactionBlocked`).
7. **Private repos are never sent to Gemini by default.** The Gemini free tier may use prompts to improve Google's products. So `repos.never_send_to_llm` defaults to `true` when `visibility = 'private'`, and turning it off needs an explicit `PATCH` with `acknowledge_free_tier_data_use: true`.
8. **`privacy_mode` is enforced in the context builder**, not the prompt: `metadata_only` sends no README and no code, `metadata_plus_readme` sends no diffs, and `never_send` makes no call at all.
9. **No diffs or code are stored** beyond the `context_json` of an event, which is deleted 7 days after the event's last draft (or 7 days after it was built, if the event was never drafted).

## 7. Logging and errors

1. **Log metadata, not content.** Never log tokens, cookies, `Authorization` headers, post bodies, README text, diffs, prompts, or model output. Log IDs, counts, durations, status codes.
2. **Error responses are generic** (`{"detail": "..."}` with a stable message). Stack traces only in server logs, and only when `APP_ENV == "dev"`.
3. **httpx logging is set to `WARNING`**, because the debug output includes headers.

## 8. Dependencies and supply chain

1. **Pin everything with `uv.lock`.** Add a dependency only when the standard library or an existing dependency cannot do the job reasonably.
2. **Run `uv run pip-audit` before every merge.** A known-vulnerable dependency blocks the merge.
3. **Run `uv run bandit -r src -q` in CI.** Fix or justify every finding with an inline `# nosec <reason>`.
4. **No `eval`, `exec`, `pickle`, `yaml.load` (use `safe_load`), `subprocess(..., shell=True)`, or `tempfile.mktemp`.**

## 9. Publishing guardrails

1. **No auto-posting.** No code path publishes to LinkedIn without an authenticated owner request to `POST /api/drafts/{id}/publish` that names the exact final text.
2. **Re-check before publishing:** text ≤ 3000 chars, no `[REDACTED` markers left in the text, and draft status is `approved`.
3. **LinkedIn token expiry is surfaced.** `GET /api/auth/me` returns `linkedin_expires_at`. API publishing refuses with `409 reconnect_required` when the token expires within 24 h. Copy-paste publishing always works.

## 10. Checklist for every PR

- [ ] No secret in code, tests, logs, or responses
- [ ] Every new route sits under the authenticated router
- [ ] Every new body or query is a bounded Pydantic model
- [ ] No raw SQL string building
- [ ] Any new outbound host is added to the allowlist with a reason
- [ ] Any new LLM call goes through `redact()`, the delimiters, and schema validation
- [ ] `pip-audit` and `bandit` are clean
