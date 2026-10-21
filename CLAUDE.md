# LinkedBuddy

Personal tool: GitHub activity → triaged inbox → evidence-grounded LinkedIn drafts → owner reviews and publishes. The product spec is `github-linkedin-post-app-pitch.md`.

## Binding rules — read before any backend work

- `docs/rules/SECURITY.md` — secrets, auth, input validation, uploads, LLM prompt-injection and data-leak rules
- `docs/rules/DATABASE.md` — Postgres schema, migrations, query and index rules, job queue
- `docs/rules/ARCHITECTURE.md` — stack, process layout, pipeline, performance budgets, external-call rules

Each file ends with a PR checklist. Run through it before you call work done.

## Current decisions

- Backend: **FastAPI** (Python 3.12, uv). Code lives in `backend/`.
- **Personal use, single owner. No paid services or tiers.** Ignore the spec's pricing, multi-user, and paid-tier material.
- AI: **Gemini API free tier** (`google-genai`). Video: **brag** behind the `VideoRenderer` adapter, opt-in only.
- Publishing: copy-and-paste for now. LinkedIn API publishing comes in a later plan.
- Implementation plan: `docs/superpowers/plans/2026-10-21-linkedbuddy-backend.md`.
