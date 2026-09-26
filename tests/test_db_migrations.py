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


@pytest.mark.asyncio
async def test_migration_0008_backfills_media_label_snapshot_from_bazarr_cache():
    """`alembic upgrade head` from a seeded revision-0007 database adds the
    4 new `jobs` columns and backfills them from `bazarr_cache` per the
    migration's correlated UPDATE, covering every scenario called out in its
    docstring: a fully-populated episode cache row, a movie cache row (no
    series/season/episode), a missing cache row, a manual job (untouched),
    an episode cache row with the old pre-0007 flattened title (no
    structured fields yet), and a non-numeric `source_ref` (CASTs to 0,
    matches nothing, no exception)."""
    import sqlite3
    import subprocess

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}

        # Build the DB up to 0007 only, so bazarr_cache already has the
        # structured columns but jobs does not yet have the snapshot ones.
        upgrade_0007 = subprocess.run(
            ["alembic", "-c", "alembic.ini", "upgrade", "0007"],
            capture_output=True,
            text=True,
            cwd=".",
            env=env,
        )
        assert (
            upgrade_0007.returncode == 0
        ), f"Upgrade to 0007 failed: {upgrade_0007.stderr}"

        conn = sqlite3.connect(db_path)

        # bazarr_cache seed rows.
        conn.execute(
            "INSERT INTO bazarr_cache (id, kind, ext_id, title, media_path, "
            "has_any_subs, missing_subtitles, series_title, season_number, "
            "episode_number) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "episode:100",
                "episode",
                100,
                "Pilot",
                "/tv/agency/s04e01.mkv",
                0,
                "[]",
                "The Parisian Agency",
                4,
                1,
            ),
        )
        conn.execute(
            "INSERT INTO bazarr_cache (id, kind, ext_id, title, media_path, "
            "has_any_subs, missing_subtitles) VALUES (?,?,?,?,?,?,?)",
            ("movie:200", "movie", 200, "A Movie", "/movies/a.mkv", 0, "[]"),
        )
        # Pre-0007 cache row: flattened title, no structured fields yet
        # (e.g. a database upgraded straight from 0006 with no resync).
        conn.execute(
            "INSERT INTO bazarr_cache (id, kind, ext_id, title, media_path, "
            "has_any_subs, missing_subtitles) VALUES (?,?,?,?,?,?,?)",
            (
                "episode:300",
                "episode",
                300,
                "Show - Episode 3",
                "/tv/show/old.mkv",
                0,
                "[]",
            ),
        )

        # jobs seed rows (priority/progress_percent/cancel_requested have no
        # SQL-level default - only a Python-side one - so they must be
        # supplied explicitly for a raw INSERT).
        jobs = [
            ("job-a", "bazarr_episode", "100", "/tv/agency/s04e01.mkv"),
            ("job-b", "bazarr_movie", "200", "/movies/a.mkv"),
            ("job-c", "bazarr_episode", "999", "/tv/show/gone.mkv"),
            ("job-d", "manual", None, "/manual/video.mp4"),
            ("job-e", "bazarr_episode", "300", "/tv/show/old.mkv"),
            ("job-f", "bazarr_episode", "not-a-number", "/tv/show/bad.mkv"),
        ]
        conn.executemany(
            "INSERT INTO jobs (id, source, source_ref, media_path, priority, "
            "progress_percent, cancel_requested) VALUES (?,?,?,?,0,0,0)",
            jobs,
        )
        conn.commit()
        conn.close()

        upgrade_head = subprocess.run(
            ["alembic", "-c", "alembic.ini", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=".",
            env=env,
        )
        assert (
            upgrade_head.returncode == 0
        ), f"Upgrade to head failed: {upgrade_head.stderr}"

        conn = sqlite3.connect(db_path)
        rows = {
            r[0]: r[1:]
            for r in conn.execute(
                "SELECT id, title, series_title, season_number, episode_number "
                "FROM jobs"
            )
        }
        conn.close()

        # (a) fully-populated episode cache row -> all 4 backfilled.
        assert rows["job-a"] == ("Pilot", "The Parisian Agency", 4, 1)
        # (b) movie cache row -> title only, no series/season/episode.
        assert rows["job-b"] == ("A Movie", None, None, None)
        # (c) source_ref matches no cache row -> all NULL.
        assert rows["job-c"] == (None, None, None, None)
        # (d) manual job -> all NULL (not even touched by the UPDATE).
        assert rows["job-d"] == (None, None, None, None)
        # (e) pre-0007 flattened title, no structured fields yet.
        assert rows["job-e"] == ("Show - Episode 3", None, None, None)
        # (f) non-numeric source_ref -> CASTs to 0, matches nothing, no error.
        assert rows["job-f"] == (None, None, None, None)

    finally:
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_migration_0008_downgrade_removes_media_label_snapshot_columns():
    """`alembic downgrade` from head back to 0007 drops the 4 new columns
    and leaves the job rows themselves (count and untouched data) intact."""
    import sqlite3
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

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO bazarr_cache (id, kind, ext_id, title, media_path, "
            "has_any_subs, missing_subtitles, series_title, season_number, "
            "episode_number) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "episode:400",
                "episode",
                400,
                "Episode Four",
                "/tv/agency/s04e04.mkv",
                0,
                "[]",
                "The Parisian Agency",
                4,
                4,
            ),
        )
        conn.execute(
            "INSERT INTO jobs (id, source, source_ref, media_path, priority, "
            "progress_percent, cancel_requested, title, series_title, "
            "season_number, episode_number) "
            "VALUES (?,?,?,?,0,0,0,?,?,?,?)",
            (
                "job-x",
                "bazarr_episode",
                "400",
                "/tv/agency/s04e04.mkv",
                "Episode Four",
                "The Parisian Agency",
                4,
                4,
            ),
        )
        conn.commit()
        conn.close()

        downgrade_result = subprocess.run(
            ["alembic", "-c", "alembic.ini", "downgrade", "0007"],
            capture_output=True,
            text=True,
            cwd=".",
            env=env,
        )
        assert (
            downgrade_result.returncode == 0
        ), f"Downgrade failed: {downgrade_result.stderr}"

        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        job_rows = conn.execute("SELECT id, media_path FROM jobs").fetchall()
        version_rows = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchall()
        conn.close()

        assert (
            not {
                "title",
                "series_title",
                "season_number",
                "episode_number",
            }
            & cols
        )
        assert job_rows == [("job-x", "/tv/agency/s04e04.mkv")]
        assert version_rows == [("0007",)]

    finally:
        os.unlink(db_path)


def test_migration_0008_jobs_columns_match_the_job_model():
    """After `alembic upgrade head`, the migrated `jobs` table's column set
    matches `Job.__table__.columns` exactly - a drift guard mirroring how
    the bazarr_cache/index tests above pin the migration against the model,
    so an edit to one that isn't mirrored in the other fails here rather
    than surfacing later as a runtime schema mismatch."""
    import sqlite3
    import subprocess

    from audio_to_subs.db.models import Job

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
        migrated_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        conn.close()

        model_cols = {c.name for c in Job.__table__.columns}
        assert migrated_cols == model_cols
    finally:
        os.unlink(db_path)
