import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.models import Job
from linkedbuddy.security.auth import create_session


async def test_get_job(client: AsyncClient, db: AsyncSession) -> None:
    job = Job(kind="poll_repo", payload={"repo_id": "x"}, result={"ok": True})
    db.add(job)
    client.cookies.set("lb_session", await create_session(db))  # commits (savepoint)

    resp = await client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    assert resp.json() == {
        "id": str(job.id),
        "kind": "poll_repo",
        "status": "queued",
        "attempts": 0,
        "error": None,
        "result": {"ok": True},
    }
    assert (await client.get(f"/api/jobs/{uuid.uuid4()}")).status_code == 404


async def test_get_job_requires_owner(client: AsyncClient) -> None:
    assert (await client.get(f"/api/jobs/{uuid.uuid4()}")).status_code == 401
