#!/usr/bin/env bash
# Starts all backend processes for local development: the API (loopback only unless
# ALLOW_PUBLIC_BIND=true), the general job worker, the video worker and the scheduler.
# Ctrl-C stops them all, and so does any one of them exiting (e.g. the bind gate refusing HOST).
set -euo pipefail
cd "$(dirname "$0")/.."
trap 'exit 130' INT TERM
trap 'trap "" TERM; kill 0 2>/dev/null || true' EXIT

uv run python -m linkedbuddy.serve --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" --reload &
uv run python -m linkedbuddy.worker &
uv run python -m linkedbuddy.worker --kinds render_video --concurrency 1 &
uv run python -m linkedbuddy.scheduler &

wait -n
