"""Initial schema (DATABASE.md section 6).

Revision ID: 20261021_0000
Revises:
Create Date: 2026-10-21 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "20261021_0000"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "sessions",
    "connections",
    "settings",
    "repos",
    "events",
    "commits",
    "series",
    "drafts",
    "media",
    "posts",
    "decisions",
    "jobs",
)

_NOW = sa.text("now()")
_UUID_DEFAULT = sa.text("gen_random_uuid()")


def _in(column: str, *values: str) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _uuid_pk() -> sa.Column[pg.UUID]:
    return sa.Column("id", pg.UUID(as_uuid=True), server_default=_UUID_DEFAULT, nullable=False)


def _ts(name: str, *, nullable: bool = False, default: bool = True) -> sa.Column[sa.DateTime]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=_NOW if default and not nullable else None,
        nullable=nullable,
    )


def _jsonb(name: str, default: str | None, *, nullable: bool = False) -> sa.Column[pg.JSONB]:
    server_default = sa.text(f"'{default}'::jsonb") if default is not None else None
    return sa.Column(name, pg.JSONB(), server_default=server_default, nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        _ts("created_at"),
        _ts("expires_at", default=False),
        sa.PrimaryKeyConstraint("token_hash", name="pk_sessions"),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "connections",
        _uuid_pk(),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column(
            "scopes",
            pg.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("member_urn", sa.Text(), nullable=True),
        _ts("expires_at", nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_connections"),
        sa.UniqueConstraint("provider", name="uq_connections_provider"),
        sa.CheckConstraint(_in("provider", "github", "linkedin"), name="ck_connections_provider"),
    )

    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("goals_text", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "default_tone", sa.Text(), server_default=sa.text("'professional'"), nullable=False
        ),
        sa.Column("custom_instructions", sa.Text(), server_default=sa.text("''"), nullable=False),
        _jsonb("ban_list", "[]"),
        _jsonb("voice_samples", "[]"),
        sa.Column("post_length", sa.Text(), server_default=sa.text("'medium'"), nullable=False),
        sa.Column("emoji_policy", sa.Text(), server_default=sa.text("'none'"), nullable=False),
        sa.Column("hashtag_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "privacy_mode",
            sa.Text(),
            server_default=sa.text("'metadata_plus_readme'"),
            nullable=False,
        ),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_settings"),
        sa.CheckConstraint("id = 1", name="ck_settings_singleton"),
        sa.CheckConstraint(
            _in("default_tone", "professional", "casual", "technical", "storytelling"),
            name="ck_settings_default_tone",
        ),
        sa.CheckConstraint(
            _in("post_length", "short", "medium", "long"), name="ck_settings_post_length"
        ),
        sa.CheckConstraint(
            _in("emoji_policy", "none", "light", "any"), name="ck_settings_emoji_policy"
        ),
        sa.CheckConstraint("hashtag_count BETWEEN 0 AND 5", name="ck_settings_hashtag_count"),
        sa.CheckConstraint(
            _in("privacy_mode", "full", "metadata_plus_readme", "metadata_only", "never_send"),
            name="ck_settings_privacy_mode",
        ),
    )
    op.execute("INSERT INTO settings (id) VALUES (1)")

    op.create_table(
        "repos",
        _uuid_pk(),
        sa.Column("github_repo_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.Text(), nullable=False),
        sa.Column("demo_url", sa.Text(), nullable=True),
        sa.Column(
            "never_send_to_llm", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        _ts("last_polled_at", nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_seen_sha", sa.Text(), nullable=True),
        _ts("pushed_at", nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_repos"),
        sa.UniqueConstraint("github_repo_id", name="uq_repos_github_repo_id"),
        sa.CheckConstraint(_in("visibility", "public", "private"), name="ck_repos_visibility"),
        sa.CheckConstraint(
            _in("mode", "always_suggest", "releases_and_large", "manual_only", "muted"),
            name="ck_repos_mode",
        ),
    )
    op.create_index("ix_repos_mode", "repos", ["mode"], postgresql_where=sa.text("mode <> 'muted'"))

    op.create_table(
        "events",
        _uuid_pk(),
        sa.Column("repo_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("base_sha", sa.Text(), nullable=True),
        sa.Column("head_sha", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        _jsonb("stats", "{}"),
        sa.Column("heuristic_score", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("rating", sa.Text(), nullable=True),
        sa.Column("rating_reason", sa.Text(), nullable=True),
        _jsonb("rating_json", None, nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'new'"), nullable=False),
        _jsonb("context_json", None, nullable=True),
        sa.Column("context_hash", sa.Text(), nullable=True),
        _ts("context_built_at", nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["repos.id"], name="fk_events_repo_id_repos", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("dedupe_key", name="uq_events_dedupe_key"),
        sa.CheckConstraint(
            _in("type", "new_repo", "release", "pr_merged", "push_group", "manual", "labeled"),
            name="ck_events_type",
        ),
        sa.CheckConstraint(_in("rating", "high", "medium", "low"), name="ck_events_rating"),
        sa.CheckConstraint(
            _in("status", "new", "suggested", "generating", "drafted", "dismissed", "posted"),
            name="ck_events_status",
        ),
    )
    op.create_index(
        "ix_events_status_rating_created_at",
        "events",
        ["status", "rating", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_events_repo_id_created_at", "events", ["repo_id", sa.text("created_at DESC")]
    )

    op.create_table(
        "commits",
        sa.Column("repo_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("sha", sa.Text(), nullable=False),
        sa.Column("event_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("meaningful_lines", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("author_login", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        _jsonb("files", "[]"),
        _ts("committed_at", default=False),
        sa.PrimaryKeyConstraint("repo_id", "sha", name="pk_commits"),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["repos.id"], name="fk_commits_repo_id_repos", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name="fk_commits_event_id_events", ondelete="SET NULL"
        ),
    )

    op.create_table(
        "series",
        _uuid_pk(),
        sa.Column("repo_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_series"),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["repos.id"], name="fk_series_repo_id_repos", ondelete="SET NULL"
        ),
    )

    op.create_table(
        "drafts",
        _uuid_pk(),
        sa.Column("event_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_draft_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("series_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("variant_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("angle", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("tone", sa.Text(), nullable=False),
        sa.Column("instructions_used", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        _jsonb("segments", "[]"),
        _jsonb("alt_openings", "[]"),
        sa.Column("first_comment", sa.Text(), nullable=True),
        sa.Column("specificity_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("unsupported_claims", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _jsonb("lint", "{}"),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_drafts"),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name="fk_drafts_event_id_events", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_draft_id"],
            ["drafts.id"],
            name="fk_drafts_parent_draft_id_drafts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"], ["series.id"], name="fk_drafts_series_id_series", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            _in("tone", "professional", "casual", "technical", "storytelling"),
            name="ck_drafts_tone",
        ),
        sa.CheckConstraint(
            _in("status", "draft", "approved", "discarded", "posted"), name="ck_drafts_status"
        ),
    )
    op.create_index(
        "ix_drafts_event_id_created_at", "drafts", ["event_id", sa.text("created_at DESC")]
    )

    op.create_table(
        "media",
        _uuid_pk(),
        sa.Column("draft_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("mime", sa.Text(), nullable=True),
        sa.Column("mermaid_source", sa.Text(), nullable=True),
        sa.Column("alt_text", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("included", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        _jsonb("meta", "{}"),
        sa.PrimaryKeyConstraint("id", name="pk_media"),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["drafts.id"], name="fk_media_draft_id_drafts", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            _in("type", "screenshot", "diagram", "chart", "pdf", "video"), name="ck_media_type"
        ),
        sa.CheckConstraint(_in("source", "uploaded", "generated", "repo"), name="ck_media_source"),
        sa.CheckConstraint(_in("status", "pending", "ready", "failed"), name="ck_media_status"),
    )
    op.create_index("ix_media_draft_id_position", "media", ["draft_id", "position"])

    op.create_table(
        "posts",
        _uuid_pk(),
        sa.Column("draft_id", pg.UUID(as_uuid=True), nullable=False),
        _ts("posted_at"),
        sa.Column("publish_method", sa.Text(), nullable=False),
        sa.Column("linkedin_post_urn", sa.Text(), nullable=True),
        sa.Column("final_text", sa.Text(), nullable=False),
        sa.Column("covered_base_sha", sa.Text(), nullable=True),
        sa.Column("covered_head_sha", sa.Text(), nullable=True),
        _jsonb("metrics", "{}"),
        sa.PrimaryKeyConstraint("id", name="pk_posts"),
        sa.ForeignKeyConstraint(["draft_id"], ["drafts.id"], name="fk_posts_draft_id_drafts"),
        sa.CheckConstraint(_in("publish_method", "manual", "api"), name="ck_posts_publish_method"),
    )
    op.create_index("ix_posts_draft_id", "posts", ["draft_id"])

    op.create_table(
        "decisions",
        _uuid_pk(),
        sa.Column("event_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("rating_at_time", sa.Text(), nullable=True),
        sa.Column("heuristic_at_time", sa.Float(), nullable=True),
        _ts("decided_at"),
        sa.PrimaryKeyConstraint("id", name="pk_decisions"),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name="fk_decisions_event_id_events", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            _in("action", "generate", "ignore", "post", "discard"), name="ck_decisions_action"
        ),
        sa.CheckConstraint(
            _in("rating_at_time", "high", "medium", "low"), name="ck_decisions_rating_at_time"
        ),
    )

    op.create_table(
        "jobs",
        _uuid_pk(),
        sa.Column("kind", sa.Text(), nullable=False),
        _jsonb("payload", "{}"),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        _ts("run_after"),
        _ts("locked_at", nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _jsonb("result", None, nullable=True),
        _ts("created_at"),
        _ts("finished_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.CheckConstraint(
            _in("status", "queued", "running", "done", "failed"), name="ck_jobs_status"
        ),
    )
    op.create_index(
        "ix_jobs_kind_run_after",
        "jobs",
        ["kind", "run_after"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "uq_jobs_dedupe_key",
        "jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )

    # Lock down Supabase's auto-generated REST API (DATABASE.md section 1): RLS on with no
    # policies, no grants to the public roles, and no default grants for future tables.
    # The guards make this a no-op on plain Postgres.
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(_for_public_roles("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I"))
    op.execute(
        _for_public_roles("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %I")
    )


def downgrade() -> None:
    # Restore Supabase's default grants for future tables, then drop everything (RLS, indexes and
    # the table-level grants go with the tables).
    op.execute(
        _for_public_roles("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO %I")
    )
    for table in reversed(TABLES):
        op.drop_table(table)


def _for_public_roles(statement: str) -> str:
    """Run ``statement`` (with one ``%I`` placeholder) once per existing Supabase public role."""
    return (
        "DO $$ DECLARE r text; BEGIN "  # noqa: S608 - only module constants are interpolated
        "FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP "
        "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN "
        f"EXECUTE format('{statement}', r); "
        "END IF; END LOOP; END $$"
    )
