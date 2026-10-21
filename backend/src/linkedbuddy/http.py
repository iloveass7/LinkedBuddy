"""Shared outbound HTTP client; every request is checked against the host allowlist."""

import httpx

# Each host needs a reason (SECURITY.md checklist).
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "api.github.com",  # read-only GitHub REST
        "generativelanguage.googleapis.com",  # Gemini API
        "api.linkedin.com",  # LinkedIn API
        "www.linkedin.com",  # LinkedIn OAuth
    }
)
# LinkedIn upload hosts are returned dynamically by the API; they are out of scope for now and
# must be added deliberately (not wildcarded) when LinkedIn API publishing is built.

TIMEOUT_S = 20.0


class DisallowedHost(Exception):
    """Raised before sending when a request targets a host outside ALLOWED_HOSTS."""


async def _enforce_allowlist(request: httpx.Request) -> None:
    if request.url.scheme != "https" or request.url.host not in ALLOWED_HOSTS:
        # Host only: URLs may carry secrets in query strings.
        raise DisallowedHost(f"outbound request blocked for host {request.url.host!r}")


def make_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT_S, event_hooks={"request": [_enforce_allowlist]})
