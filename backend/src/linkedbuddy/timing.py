"""Pure ASGI middleware: `Server-Timing: app;dur=<ms>, db;dur=<ms>` (ARCHITECTURE.md 5.5).

`app` is the time from the request reaching the app to the response starting; `db` is the summed
time of the DB statements run for it. Nothing else goes in the header.
"""

import time

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from linkedbuddy.db.engine import DbTime, current_db_time


class ServerTimingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        db = DbTime()
        token = current_db_time.set(db)

        async def send_with_timing(message: Message) -> None:
            if message["type"] == "http.response.start":
                app_ms = (time.perf_counter() - started) * 1000
                headers = MutableHeaders(scope=message)
                headers["Server-Timing"] = f"app;dur={app_ms:.1f}, db;dur={db.ms:.1f}"
            await send(message)

        try:
            await self.app(scope, receive, send_with_timing)
        finally:
            current_db_time.reset(token)
