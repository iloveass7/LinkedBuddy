# `make check` runs every quality gate. uv must be on PATH, or pass UV=/path/to/uv.
# The suite runs in chunks: against the remote test DB a single run takes over 10 minutes.
UV ?= uv
PYTEST = $(UV) run pytest -q

.PHONY: check lint test

check: lint test

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy src
	$(UV) run bandit -r src -q
	$(UV) run pip-audit

test:
	$(PYTEST) tests/db tests/security tests/llm tests/github
	$(PYTEST) tests/jobs tests/repos tests/inbox tests/settings
	$(PYTEST) tests/drafts
	$(PYTEST) tests/triage tests/media
	$(PYTEST) tests/video tests/posts tests/test_*.py
