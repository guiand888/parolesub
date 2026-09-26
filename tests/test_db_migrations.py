"""Tests for database migrations."""

import os
import tempfile

import pytest


class TestAlembicConfig:
    """Test Alembic configuration."""

    def test_alembic_ini_exists(self):
        """Test that alembic.ini exists."""
        assert os.path.exists("alembic.ini")

    def test_migrations_directory_exists(self):
        """Test that migrations directory exists."""
        assert os.path.exists("audio_to_subs/db/migrations")

    def test_env_py_exists(self):
        """Test that env.py exists."""
        assert os.path.exists("audio_to_subs/db/migrations/env.py")

    def test_versions_directory_exists(self):
        """Test that versions directory exists."""
        assert os.path.exists("audio_to_subs/db/migrations/versions")

    def test_initial_migration_exists(self):
        """Test that initial migration exists."""
        assert os.path.exists(
            "audio_to_subs/db/migrations/versions/0001_initial_schema.py"
        )


@pytest.mark.asyncio
async def test_migration_can_be_applied():
    """Test that migration can be applied to a fresh database."""
    import subprocess

    # Create temp file for database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        # Run alembic upgrade head
        result = subprocess.run(
            [
                "alembic",
                "-c",
                "alembic.ini",
                "upgrade",
                "head",
            ],
            capture_output=True,
            text=True,
            cwd=".",
            env={
                **os.environ,
                "DATABASE_URL": f"sqlite:///{db_path}",
            },
        )

        # Check that migration succeeded
        assert result.returncode == 0, f"Migration failed: {result.stderr}"

        # Verify tables exist
        import sqlite3

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()

        # Check expected tables exist
        expected_tables = {"users", "jobs", "job_logs", "settings", "bazarr_cache"}
        assert expected_tables.issubset(set(tables))

        # M5.8: jobs table must carry the persisted progress stage/step columns
        import sqlite3 as _sqlite3

        conn2 = _sqlite3.connect(db_path)
        cols = {r[1] for r in conn2.execute("PRAGMA table_info(jobs)")}
        conn2.close()
        assert {
            "progress_stage",
            "progress_step_index",
            "progress_step_total",
        }.issubset(cols)

    finally:
        # Cleanup
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_migration_0007_adds_structured_episode_columns_and_index():
    """After `alembic upgrade head`, bazarr_cache has the 5 new
    migration-0007 columns and the ix_bazarr_cache_sort index."""
    import subprocess

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        result = subprocess.run(
            ["alembic", "-c", "alembic.ini", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=".",
            env={**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"},
        )
        assert result.returncode == 0, f"Migration failed: {result.stderr}"

        import sqlite3

        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bazarr_cache)")}
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(bazarr_cache)")}
        conn.close()

        assert {
            "series_title",
            "series_ext_id",
            "season_number",
            "episode_number",
            "sort_key",
        }.issubset(cols)
        assert "ix_bazarr_cache_sort" in indexes

    finally:
        os.unlink(db_path)


def test_migration_0007_index_case_expression_matches_the_model_constant():
    """The literal CASE SQL string in migration 0007's `create_index` call
    is duplicated from `SEASON_ORDER_BUCKET_SQL` (db/models.py) rather than
    imported, since migrations don't depend on application code (see the
    migration's docstring). Nothing else catches the two drifting apart -
    the migration test above only checks the index's *name*, not what it
    actually indexes, and the ORM-declared `Index()` on `BazarrCache` is a
    separate object that `create_all`-backed tests exercise instead of this
    migration. This test parses the index's real SQL, as SQLite itself
    stored it after running the actual migration (not `create_all`), and
    asserts the CASE expression inside it is exactly the model's constant -
    so an edit to either copy that isn't mirrored in the other fails here.
    """
    import sqlite3
    import subprocess

    from audio_to_subs.db.models import SEASON_ORDER_BUCKET_SQL

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        result = subprocess.run(
            ["alembic", "-c", "alembic.ini", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=".",
            env={**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"},
        )
        assert result.returncode == 0, f"Migration failed: {result.stderr}"

        conn = sqlite3.connect(db_path)
        index_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'ix_bazarr_cache_sort'"
        ).fetchone()[0]
        conn.close()

        assert SEASON_ORDER_BUCKET_SQL in index_sql, index_sql
    finally:
        os.unlink(db_path)


def test_migration_0007_index_backs_wanted_order_on_the_real_migrated_schema():
    """`_WANTED_ORDER` must be satisfied by `ix_bazarr_cache_sort` on a
    schema built by the actual migration - not just on the ORM's
    `create_all`-built schema, which is what
    `tests/test_api_wanted.py::test_list_wanted_order_by_is_satisfied_by_the_index`
    exercises. The two schemas come from separately-maintained copies of
    the same index definition (see
    `test_migration_0007_index_case_expression_matches_the_model_constant`
    above), so only a migration-backed EXPLAIN QUERY PLAN actually proves
    what production - which always runs `alembic upgrade head`, never
    `create_all` - will do.
    """
    import sqlite3
    import subprocess

    from sqlalchemy import select
    from sqlalchemy.dialects import sqlite as sqlite_dialect

    from audio_to_subs.api.routes.wanted import _WANTED_ORDER
    from audio_to_subs.core.library_sort import library_sort_key
    from audio_to_subs.db.models import BazarrCache

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        result = subprocess.run(
            ["alembic", "-c", "alembic.ini", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=".",
            env={**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"},
        )
        assert result.returncode == 0, f"Migration failed: {result.stderr}"

        conn = sqlite3.connect(db_path)
        rows = []
        for n in range(1, 501):
            season = None if n % 37 == 0 else (0 if n % 11 == 0 else (n % 6) + 1)
            rows.append(
                (
                    f"episode:{n}",
                    "episode",
                    n,
                    f"Episode {n}",
                    f"/tv/show{n % 20}/e{n}.mkv",
                    0,
                    "[]",
                    "2026-01-01 00:00:00",
                    f"Show {n % 20}",
                    n % 20,
                    season,
                    n,
                    library_sort_key(f"Show {n % 20}"),
                )
            )
        conn.executemany(
            "INSERT INTO bazarr_cache (id, kind, ext_id, title, media_path, "
            "has_any_subs, missing_subtitles, last_polled, series_title, "
            "series_ext_id, season_number, episode_number, sort_key) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.execute("ANALYZE")
        conn.commit()

        query = select(BazarrCache).order_by(*_WANTED_ORDER)
        sql = str(
            query.compile(
                dialect=sqlite_dialect.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        plan = " | ".join(str(row) for row in conn.execute("EXPLAIN QUERY PLAN " + sql))
        conn.close()

        assert "USING INDEX ix_bazarr_cache_sort" in plan, plan
        assert "TEMP B-TREE" not in plan, plan
    finally:
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_migration_0007_downgrade_removes_structured_episode_columns():
    """`alembic downgrade` from head back to 0006 drops the 5 new columns
    and the index, and leaves alembic_version pointing at 0006."""
    import subprocess

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}

        upgrade_result = subprocess.run(
            ["alembic", "-c", "alembic.ini", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=".",
            env=env,
        )
        assert (
            upgrade_result.returncode == 0
        ), f"Upgrade failed: {upgrade_result.stderr}"

        downgrade_result = subprocess.run(
            ["alembic", "-c", "alembic.ini", "downgrade", "0006"],
            capture_output=True,
            text=True,
            cwd=".",
            env=env,
        )
        assert (
            downgrade_result.returncode == 0
        ), f"Downgrade failed: {downgrade_result.stderr}"

        import sqlite3

        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bazarr_cache)")}
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(bazarr_cache)")}
        version_rows = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchall()
        conn.close()

        assert (
            not {
                "series_title",
                "series_ext_id",
                "season_number",
                "episode_number",
                "sort_key",
            }
            & cols
        )
        assert "ix_bazarr_cache_sort" not in indexes
        assert version_rows == [("0006",)]

    finally:
        os.unlink(db_path)
