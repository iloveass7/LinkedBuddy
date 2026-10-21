#!/usr/bin/env bash
# Starts all backend processes for local development. Ctrl-C stops them all.
# The worker and scheduler lines are added in later tasks.
set -euo pipefail
cd "$(dirname "$0")/.."
trap 'kill 0' EXIT

uv run uvicorn linkedbuddy.main:app --host 127.0.0.1 --port 8000 --reload &

wait
