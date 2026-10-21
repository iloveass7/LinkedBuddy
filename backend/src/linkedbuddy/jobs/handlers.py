"""Job kind -> handler registry (ARCHITECTURE.md 7.3). One handler per kind; later tasks add them.

A handler returns a JSON-able dict (stored in `jobs.result`), raises `Defer` to retry later
without spending an attempt, or raises anything else to fail the attempt. Handlers must be
idempotent and restore any user-visible status themselves on failure.
"""

from linkedbuddy.jobs.context import Handler

HANDLERS: dict[str, Handler] = {}
