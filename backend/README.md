# LinkedBuddy backend

FastAPI service that turns GitHub activity into LinkedIn drafts. Python 3.12, managed with `uv`.

## Setup

```bash
cp ../.env.example ../.env        # then edit: both DB URLs, APP_ENCRYPTION_KEY, OWNER_PASSWORD_HASH
uv sync
```

`DATABASE_URL` and `TEST_DATABASE_URL` must be two different Supabase projects (session pooler,
port 5432). Generate the encryption key with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

### Owner password hash

```bash
uv run python scripts/hash_password.py   # hidden prompt; paste the output into OWNER_PASSWORD_HASH
```

### Database

```bash
uv run alembic upgrade head
```

## Run

```bash
scripts/dev.sh    # API on 127.0.0.1:8000, job workers (general + render_video) and the scheduler
```

Ctrl-C stops everything, and so does any one process exiting. The API starts through
`python -m linkedbuddy.serve`, which refuses a non-loopback host (`HOST=0.0.0.0 scripts/dev.sh`)
unless `ALLOW_PUBLIC_BIND=true`; only set that with HTTPS in front (SECURITY.md 2.1). Running
`uvicorn` directly skips this check. `PORT` changes the port.

In dev, SQL statements slower than `SLOW_QUERY_MS` (default 100) are logged as `slow query <ms>:
<first 80 characters>`, never with parameters. The time includes the network round trip, which
to a remote Supabase is often 100-400 ms on its own, so raise the threshold if the log is noisy.

## Checks

```bash
make check        # ruff, ruff format --check, mypy, bandit, pip-audit, then the tests in chunks
make check UV=/path/to/uv   # when uv is not on PATH
```

Tests need `TEST_DATABASE_URL`. Against the remote test DB the whole suite takes over 10 minutes,
so `make test` runs it in five chunks; run a single directory while iterating
(`uv run pytest tests/media`). `tests/test_e2e.py` drives the whole pipeline (sync, poll, rate,
generate, render, approve, publish) with mocked GitHub and Gemini; it commits real rows to the
test DB and deletes them afterwards.

## First real run

Not done yet: it needs a real Supabase project, a Gemini free-tier key and a GitHub token. With
the owner present:

1. Create the real Supabase project (separate from the test project). In `../.env` set
   `DATABASE_URL` to its session pooler URI (port 5432, `postgresql+asyncpg://`), and set
   `APP_ENCRYPTION_KEY`, `OWNER_PASSWORD_HASH`, `GEMINI_API_KEY` (free tier) and
   `APP_ENV=dev`. Leave `GITHUB_TOKEN` unset; the token goes in through the API in step 5.
2. `uv run alembic upgrade head`
3. `scripts/dev.sh` (needs `uv` on PATH). Expect `Uvicorn running on http://127.0.0.1:8000`, two
   `worker kinds=...` lines and scheduler `tick` lines. `HOST=0.0.0.0 scripts/dev.sh` must
   refuse to start.
4. Log in and keep the cookie:
   `curl -c jar -H 'X-Requested-With: linkedbuddy' -H 'Content-Type: application/json'
   -d '{"password": "..."}' http://127.0.0.1:8000/api/auth/login` (204).
5. Store a fine-grained, read-only GitHub token:
   `curl -b jar -X PUT -H 'X-Requested-With: linkedbuddy' -H 'Content-Type: application/json'
   -d '{"token": "github_pat_..."}' http://127.0.0.1:8000/api/connections/github` (204).
6. `curl -b jar -X POST -H 'X-Requested-With: linkedbuddy' http://127.0.0.1:8000/api/repos/sync`
   returns `{"synced": N}`. `GET /api/repos` lists them; make sure the public repo you want to
   test with is not `muted` or `manual_only` (`PATCH /api/repos/{id}` with `{"mode":
   "always_suggest"}`).
7. Wait for the scheduler (one tick a minute) to queue `poll_repo`, or call
   `POST /api/repos/{id}/poll`. Then `GET /api/inbox` should show the repo's events with a
   `rating` once `rate_event` has run (watch the worker log).
8. `POST /api/events/{id}/generate` (202), poll `GET /api/jobs/{job_id}` until `done`, then
   `GET /api/events/{id}/drafts` shows 2-3 drafts; the diagram's media turns `ready` (or
   `failed` with a reason if Chromium cannot start; see below).
9. Optional: approve one draft (`PATCH /api/drafts/{id}` with `{"status": "approved"}`) and
   record a manual publish (`POST /api/drafts/{id}/publish` with `{"final_text": "...",
   "method": "manual"}`); the event becomes `posted`. Nothing is sent to LinkedIn.
10. Check `GET /api/inbox` response headers for `Server-Timing` (ARCHITECTURE.md 5.5). Note:
    that middleware is not implemented yet, so the header is expected to be missing for now.
11. Ctrl-C and confirm no `linkedbuddy` processes are left (`pgrep -fa linkedbuddy`).

## Diagram rendering (mermaid-cli)

Draft diagrams are rendered to PNG by `mmdc`, run as a subprocess with the browser sandbox on.
It is installed locally under `tools/` (not globally):

```bash
npm install --prefix tools @mermaid-js/mermaid-cli
```

Puppeteer downloads its own Chromium during that install. If you already have a Chromium (for
example Playwright's), skip the download and point Puppeteer at it:

```bash
PUPPETEER_SKIP_DOWNLOAD=1 npm install --prefix tools @mermaid-js/mermaid-cli
echo '{"executablePath": "/path/to/chrome"}' > tools/puppeteer-config.json   # git-ignored
```

That file only supplies `executablePath`: each render writes its own Puppeteer config in a private
temp dir that also routes all browser traffic (loopback included) to a closed proxy, so a diagram
cannot make the renderer fetch URLs. Never add `--no-sandbox` (it is stripped if present). If Chromium cannot start with its sandbox on this machine,
diagrams are marked `failed` with a reason and the rest of the app keeps working.

Settings: `MMDC_PATH` (default `tools/node_modules/.bin/mmdc`, falling back to `mmdc` on `PATH`)
and `MMDC_PUPPETEER_CONFIG` (default `tools/puppeteer-config.json` when it exists). Rendered and
uploaded images live under `MEDIA_ROOT` (default `var/media`, relative to this directory) and are
served only through the authenticated `GET /api/media/{id}/file`.
