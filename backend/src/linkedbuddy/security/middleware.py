"""Pure ASGI middleware: CSRF header check and security headers (SECURITY.md 3.1, 3.3)."""

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSRF_HEADER = b"x-requested-with"
CSRF_VALUE = b"linkedbuddy"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# The Swagger/ReDoc pages (dev only) need scripts and styles that this CSP forbids.
_CSP_EXEMPT = ("/docs", "/redoc")
_CSP = "default-src 'none'; frame-ancestors 'none'"
_HSTS = "max-age=31536000; includeSubDomains"


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                if not scope["path"].startswith(_CSP_EXEMPT):
                    headers["Content-Security-Policy"] = _CSP
                if self.hsts:
                    headers["Strict-Transport-Security"] = _HSTS
            await send(message)

        if scope["method"] not in SAFE_METHODS and not _has_csrf_header(scope):
            response = JSONResponse({"detail": "missing CSRF header"}, status_code=403)
            await response(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)


def _has_csrf_header(scope: Scope) -> bool:
    return any(k == CSRF_HEADER and v == CSRF_VALUE for k, v in scope["headers"])
