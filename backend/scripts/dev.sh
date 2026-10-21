#!/usr/bin/env bash
# Starts all backend processes for local development. Ctrl-C stops them all.
set -euo pipefail
cd "$(dirname "$0")/.."
trap 'kill 0' EXIT

uv run uvicorn linkedbuddy.main:app --host 127.0.0.1 --port 8000 --reload &
uv run python -m linkedbuddy.worker &
uv run python -m linkedbuddy.worker --kinds render_video --concurrency 1 &
uv run python -m linkedbuddy.scheduler &

wait
