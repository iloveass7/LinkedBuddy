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

Ctrl-C stops everything. Tests: `uv run pytest` (needs `TEST_DATABASE_URL`; the full run takes a
few minutes, so run it in chunks such as `pytest tests/media tests/drafts`).

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
