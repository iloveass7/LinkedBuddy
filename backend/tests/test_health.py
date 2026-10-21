from httpx import ASGITransport, AsyncClient

from linkedbuddy.config import Settings
from linkedbuddy.main import create_app


async def test_health_ok(client: AsyncClient) -> None:
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_docs_enabled_in_dev(client: AsyncClient) -> None:
    assert (await client.get("/docs")).status_code == 200


async def test_docs_disabled_in_prod(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"app_env": "prod"}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/docs")).status_code == 404
        assert (await c.get("/openapi.json")).status_code == 404
        assert (await c.get("/redoc")).status_code == 404
