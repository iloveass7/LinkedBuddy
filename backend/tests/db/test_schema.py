import asyncio
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import delete, func, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import select

from conftest import alembic_config, check_test_db_url
from linkedbuddy.db.engine import make_engine
from linkedbuddy.db.models import Base, Draft, Event, Job, Post, Repo, SettingsRow


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


async def test_repo_delete_cascades_to_posts(db: AsyncSession) -> None:
    repo = await _repo(db)
    event = Event(repo_id=repo.id, type="manual", dedupe_key="k-cascade")
    db.add(event)
    await db.flush()
    draft = Draft(event_id=event.id, tone="professional", body="b", model="m", prompt_version="1")
    db.add(draft)
    await db.flush()
    db.add(Post(draft_id=draft.id, publish_method="manual", final_text="t"))
    await db.flush()
    assert await db.scalar(select(func.count()).select_from(Post)) == 1
    await db.execute(delete(Repo).where(Repo.id == repo.id))
    assert await db.scalar(select(func.count()).select_from(Post)) == 0
    assert await db.scalar(select(func.count()).select_from(Draft)) == 0


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
    roles = ["anon", "authenticated"]
    for role in roles:  # a missing role would make the privilege checks below vacuous
        assert await db.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}), (
            f"role {role} must exist on the (Supabase) test DB"
        )
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


POOLER = "aws-0-ap-northeast-1.pooler.supabase.com"


@pytest.mark.parametrize(
    ("test_url", "real_url"),
    [
        # same pooler host, different project refs: fine
        (
            f"postgresql+asyncpg://postgres.aaaa:p@{POOLER}:5432/postgres",
            f"postgresql+asyncpg://postgres.bbbb:p@{POOLER}:5432/postgres",
        ),
        # Supabase test URL vs a placeholder real URL on the same shared pooler host: fine
        (
            f"postgresql+asyncpg://postgres.aaaa:p@{POOLER}:5432/postgres",
            f"postgresql+asyncpg://postgres:p@{POOLER}:5432/postgres",
        ),
        # plain Postgres on different hosts: fine
        (
            "postgresql+asyncpg://u:p@test.example.com:5432/d",
            "postgresql+asyncpg://u:p@db.example.com:5432/d",
        ),
    ],
)
def test_conftest_guard_accepts_separate_databases(test_url: str, real_url: str) -> None:
    check_test_db_url(test_url, real_url)


@pytest.mark.parametrize(
    ("test_url", "real_url"),
    [
        (None, "postgresql+asyncpg://u:p@db.example.com:5432/d"),
        ("", "postgresql+asyncpg://u:p@db.example.com:5432/d"),
        # identical
        (
            "postgresql+asyncpg://u:p@db.example.com:5432/d",
            "postgresql+asyncpg://u:p@db.example.com:5432/d",
        ),
        # same Supabase ref via the same pooler
        (
            f"postgresql+asyncpg://postgres.aaaa:p@{POOLER}:5432/postgres",
            f"postgresql+asyncpg://postgres.aaaa:q@{POOLER}:5432/postgres",
        ),
        # same Supabase ref via pooler and via the direct host
        (
            f"postgresql+asyncpg://postgres.aaaa:p@{POOLER}:5432/postgres",
            "postgresql+asyncpg://postgres:p@db.aaaa.supabase.co:5432/postgres",
        ),
        (
            "postgresql+asyncpg://postgres:p@db.aaaa.supabase.co:5432/postgres",
            f"postgresql+asyncpg://postgres.aaaa:p@{POOLER}:5432/postgres",
        ),
        # plain Postgres: same host/port/db with a different role is still the same database
        (
            "postgresql+asyncpg://tester:p@db.example.com:5432/d",
            "postgresql+asyncpg://owner:p@db.example.com:5432/d",
        ),
        # transaction pooler
        (
            f"postgresql+asyncpg://postgres.aaaa:p@{POOLER}:6543/postgres",
            f"postgresql+asyncpg://postgres.bbbb:p@{POOLER}:5432/postgres",
        ),
    ],
)
def test_conftest_guard_rejects(test_url: str | None, real_url: str) -> None:
    with pytest.raises(RuntimeError) as exc:
        check_test_db_url(test_url, real_url)
    assert "@" not in str(exc.value)  # never leaks a URL


def test_make_engine_rejects_transaction_pooler() -> None:
    with pytest.raises(ValueError):
        make_engine("postgresql+asyncpg://u:p@test.example.com:6543/postgres")


def test_every_foreign_key_is_indexed() -> None:
    """DATABASE.md 7.4: an FK column without an index makes parent deletes and joins scan."""
    missing = []
    for table in Base.metadata.sorted_tables:
        leading = {idx.columns.values()[0].name for idx in table.indexes}
        leading.add(table.primary_key.columns.values()[0].name)
        for fk in table.foreign_key_constraints:
            if fk.columns.values()[0].name not in leading:
                missing.append(f"{table.name}.{fk.columns.values()[0].name}")
    assert missing == []


async def test_database_matches_models(engine: AsyncEngine) -> None:
    """The migrated schema has every index and column the models declare (no autogenerate diff)."""

    def diff(conn: Connection) -> list[Any]:
        return compare_metadata(MigrationContext.configure(conn), Base.metadata)

    async with engine.connect() as conn:
        assert await conn.run_sync(diff) == []


async def test_migration_roundtrip(engine: AsyncEngine, test_database_url: str) -> None:
    """Keep this test last: it rebuilds the schema, then leaves it at head."""
    cfg = alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    await engine.dispose()  # drop pooled connections that saw the old tables
