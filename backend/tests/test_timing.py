"""Server-Timing header (ARCHITECTURE.md 5.5): app and DB time, nothing else."""

import re

from httpx import AsyncClient

TIMING = re.compile(r"^app;dur=(\d+(?:\.\d)?), db;dur=(\d+(?:\.\d)?)$")


def durations(header: str | None) -> tuple[float, float]:
    assert header is not None
    match = TIMING.fullmatch(header)
    assert match, header
    return float(match[1]), float(match[2])


async def test_health_reports_app_and_db_time(client: AsyncClient) -> None:
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    app_ms, db_ms = durations(resp.headers.get("server-timing"))
    assert db_ms > 0
    assert app_ms >= db_ms


async def test_route_without_db_reports_zero_db_time(plain_client: AsyncClient) -> None:
    resp = await plain_client.get("/api/no-such-route")
    assert resp.status_code == 404
    assert durations(resp.headers.get("server-timing"))[1] == 0


async def test_csrf_rejection_is_timed(plain_client: AsyncClient) -> None:
    resp = await plain_client.post("/api/auth/login", json={"password": "x"})
    assert resp.status_code == 403
    assert durations(resp.headers.get("server-timing"))[1] == 0
