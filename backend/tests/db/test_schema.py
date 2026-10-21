import asyncio

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import select

from conftest import alembic_config, check_test_db_url
from linkedbuddy.db.engine import make_engine
from linkedbuddy.db.models import Base, Event, Job, Repo, SettingsRow


async def _repo(db: AsyncSession) -> Repo:
    repo = Repo(
        github_repo_id=1,
        full_name="me/proj",
        visibility="public",
        mode="always_suggest",
        default_branch="main",
    )
    db.add(repo)
    await db.flush()
    return repo


async def test_settings_singleton(db: AsyncSession) -> None:
    assert (await db.get(SettingsRow, 1)) is not None  # inserted by the migration
    db.add(SettingsRow(id=2))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_event_status_check(db: AsyncSession) -> None:
    repo = await _repo(db)
    db.add(Event(repo_id=repo.id, type="manual", dedupe_key="k1", status="bogus"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_job_dedupe_partial_unique(db: AsyncSession) -> None:
    db.add_all(
        [
            Job(kind="poll", dedupe_key="poll:x", status="done"),
            Job(kind="poll", dedupe_key="poll:x"),
        ]
    )
    await db.flush()  # one done + one queued is fine
    db.add(Job(kind="poll", dedupe_key="poll:x"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_lazy_raise(db: AsyncSession) -> None:
    repo = await _repo(db)
    db.add(Event(repo_id=repo.id, type="manual", dedupe_key="k2"))
    await db.commit()  # commit() inside the test transaction only releases a savepoint
    db.expunge_all()
    event = (await db.execute(select(Event))).scalar_one()
    with pytest.raises(InvalidRequestError):
        _ = event.repo
    loaded = (await db.execute(select(Event).options(selectinload(Event.repo)))).scalar_one()
    assert loaded.repo.full_name == "me/proj"


async def test_public_api_locked_down(db: AsyncSession) -> None:
    roles = [
        r
        for r in ("anon", "authenticated")
        if (await db.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": r}))
    ]
    rows = (
        await db.execute(
            text(
                "SELECT c.relname, c.relrowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = ANY(:names)"
            ),
            {"names": list(Base.metadata.tables)},
        )
    ).all()
    assert {name for name, _ in rows} == set(Base.metadata.tables)
    assert all(rls for _, rls in rows)
    for table in Base.metadata.tables:
        for role in roles:
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                allowed = await db.scalar(
                    text("SELECT has_table_privilege(:role, :tbl, :priv)"),
                    {"role": role, "tbl": f"public.{table}", "priv": priv},
                )
                assert not allowed, f"{role} has {priv} on {table}"
    # New tables must not be granted to the public roles either.
    leaked = await db.scalar(
        text(
            "SELECT count(*) FROM pg_default_acl d "
            "JOIN pg_namespace n ON n.oid = d.defaclnamespace, "
            "aclexplode(d.defaclacl) a JOIN pg_roles g ON g.oid = a.grantee "
            "WHERE n.nspname = 'public' AND d.defaclobjtype = 'r' "
            "AND d.defaclrole = (SELECT oid FROM pg_roles WHERE rolname = current_user) "
            "AND g.rolname IN ('anon', 'authenticated')"
        )
    )
    assert leaked == 0


def test_conftest_guard() -> None:
    real = "postgresql+asyncpg://u:p@db.example.com:5432/postgres"
    test = "postgresql+asyncpg://u:p@test.example.com:5432/postgres"
    check_test_db_url(test, real)
    with pytest.raises(RuntimeError):
        check_test_db_url(None, real)
    with pytest.raises(RuntimeError):
        check_test_db_url(real, real)
    with pytest.raises(RuntimeError):
        check_test_db_url("postgresql+asyncpg://u:p@test.example.com:6543/postgres", real)


def test_make_engine_rejects_transaction_pooler() -> None:
    with pytest.raises(ValueError):
        make_engine("postgresql+asyncpg://u:p@test.example.com:6543/postgres")


async def test_migration_roundtrip(engine: AsyncEngine, test_database_url: str) -> None:
    """Keep this test last: it rebuilds the schema, then leaves it at head."""
    cfg = alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    await engine.dispose()  # drop pooled connections that saw the old tables
