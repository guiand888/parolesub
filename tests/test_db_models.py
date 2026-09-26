"""Tests for database models."""

import pytest
from sqlalchemy import select

from audio_to_subs.db.models import (
    BazarrCache,
    Job,
    JobLog,
    JobSource,
    JobStatus,
    LogLevel,
    OutputFormat,
    Setting,
    User,
)


class TestJobStatus:
    """Test JobStatus enum."""

    def test_job_status_values(self):
        """Test JobStatus enum has expected values."""
        assert JobStatus.QUEUED.value == "queued"
        assert JobStatus.RUNNING.value == "running"
        assert JobStatus.DONE.value == "done"
        assert JobStatus.FAILED.value == "failed"
        assert JobStatus.CANCELLED.value == "cancelled"


class TestJobSource:
    """Test JobSource enum."""

    def test_job_source_values(self):
        """Test JobSource enum has expected values."""
        assert JobSource.BAZARR_MOVIE.value == "bazarr_movie"
        assert JobSource.BAZARR_EPISODE.value == "bazarr_episode"
        assert JobSource.MANUAL.value == "manual"


class TestOutputFormat:
    """Test OutputFormat enum."""

    def test_output_format_values(self):
        """Test OutputFormat enum has expected values."""
        assert OutputFormat.SRT.value == "srt"
        assert OutputFormat.VTT.value == "vtt"
        assert OutputFormat.WEBVTT.value == "webvtt"
        assert OutputFormat.SBV.value == "sbv"


class TestLogLevel:
    """Test LogLevel enum."""

    def test_log_level_values(self):
        """Test LogLevel enum has expected values."""
        assert LogLevel.DEBUG.value == "debug"
        assert LogLevel.INFO.value == "info"
        assert LogLevel.WARNING.value == "warning"
        assert LogLevel.ERROR.value == "error"


@pytest.mark.asyncio
async def test_user_model():
    """Test User model."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        from audio_to_subs.db.base import Base

        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        # Create user
        user = User(username="testuser", password_hash="hashed_password")
        session.add(user)
        await session.commit()
        await session.refresh(user)

        assert user.id is not None
        assert user.username == "testuser"
        assert user.password_hash == "hashed_password"
        assert user.created_at is not None

        # Fetch user
        result = await session.execute(select(User).where(User.username == "testuser"))
        fetched_user = result.scalar_one_or_none()
        assert fetched_user is not None
        assert fetched_user.username == "testuser"

    await engine.dispose()


@pytest.mark.asyncio
async def test_job_model():
    """Test Job model."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        from audio_to_subs.db.base import Base

        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        # Create job
        job = Job(
            media_path="/input/video.mp4",
            output_format=OutputFormat.SRT,
            status=JobStatus.QUEUED,
            source=JobSource.MANUAL,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        assert job.id is not None
        assert job.media_path == "/input/video.mp4"
        assert job.status == JobStatus.QUEUED
        assert job.source == JobSource.MANUAL
        assert job.output_format == OutputFormat.SRT
        assert job.progress_percent == 0
        assert job.cancel_requested is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_job_is_terminal():
    """Test Job.is_terminal property."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        from audio_to_subs.db.base import Base

        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        # Create terminal job
        done_job = Job(
            media_path="/input/video.mp4",
            status=JobStatus.DONE,
            source=JobSource.MANUAL,
        )
        session.add(done_job)
        await session.commit()
        await session.refresh(done_job)

        assert done_job.is_terminal is True

        # Create non-terminal job
        running_job = Job(
            media_path="/input/video.mp4",
            status=JobStatus.RUNNING,
            source=JobSource.MANUAL,
        )
        session.add(running_job)
        await session.commit()
        await session.refresh(running_job)

        assert running_job.is_terminal is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_job_log_model():
    """Test JobLog model."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        from audio_to_subs.db.base import Base

        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        # Create job log
        log = JobLog(
            level=LogLevel.INFO,
            message="Test log message",
        )
        session.add(log)
        await session.commit()
        await session.refresh(log)

        assert log.id is not None
        assert log.level == LogLevel.INFO
        assert log.message == "Test log message"
        assert log.ts is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_setting_model():
    """Test Setting model."""
    import json

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        from audio_to_subs.db.base import Base

        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        # Create setting
        setting = Setting(key="test_key", value_json=json.dumps({"value": 42}))
        session.add(setting)
        await session.commit()
        await session.refresh(setting)

        assert setting.key == "test_key"
        assert setting.get_value() == {"value": 42}

        # Update setting
        setting.set_value({"value": 100})
        await session.commit()
        await session.refresh(setting)

        assert setting.get_value() == {"value": 100}

    await engine.dispose()


@pytest.mark.asyncio
async def test_bazarr_cache_model():
    """Test BazarrCache model."""

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        from audio_to_subs.db.base import Base

        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        # Create cache entry
        cache = BazarrCache(
            id="movie:123",
            kind="movie",
            ext_id=123,
            title="Test Movie",
            media_path="/movies/test.mkv",
            has_any_subs=False,
            missing_subtitles=[{"code2": "en", "code3": "eng"}],
        )
        session.add(cache)
        await session.commit()
        await session.refresh(cache)

        assert cache.id == "movie:123"
        assert cache.kind == "movie"
        assert cache.ext_id == 123
        assert cache.media_path == "/movies/test.mkv"
        assert cache.has_any_subs is False
        assert cache.missing_subtitles == [{"code2": "en", "code3": "eng"}]

    await engine.dispose()


@pytest.mark.asyncio
async def test_bazarr_cache_make_id():
    """Test BazarrCache.make_id method."""
    assert BazarrCache.make_id("movie", 123) == "movie:123"
    assert BazarrCache.make_id("episode", 456) == "episode:456"


@pytest.mark.asyncio
async def test_bazarr_cache_structured_episode_fields_round_trip():
    """The migration-0007 columns (series_title, series_ext_id,
    season_number, episode_number, sort_key) persist and re-fetch
    correctly for an episode row."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        from audio_to_subs.db.base import Base

        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        cache = BazarrCache(
            id="episode:789",
            kind="episode",
            ext_id=789,
            title="Episode 1",
            series_title="Test Series",
            series_ext_id=42,
            season_number=3,
            episode_number=1,
            sort_key="test series",
            media_path="/tv/test-series/s03e01.mkv",
            has_any_subs=False,
            missing_subtitles=[{"code2": "en", "code3": "eng"}],
        )
        session.add(cache)
        await session.commit()
        await session.refresh(cache)

        # Fetch fresh from the DB (not just the in-memory object) to make
        # sure the values actually round-trip through the columns.
        result = await session.execute(
            select(BazarrCache).where(BazarrCache.id == "episode:789")
        )
        fetched = result.scalar_one_or_none()
        assert fetched is not None
        assert fetched.series_title == "Test Series"
        assert fetched.series_ext_id == 42
        assert fetched.season_number == 3
        assert fetched.episode_number == 1
        assert fetched.sort_key == "test series"

    await engine.dispose()


@pytest.mark.asyncio
async def test_bazarr_cache_structured_episode_fields_default_to_none():
    """For a movie row (or any row that omits them), the migration-0007
    columns default to NULL/None rather than requiring a value."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        from audio_to_subs.db.base import Base

        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        cache = BazarrCache(
            id="movie:321",
            kind="movie",
            ext_id=321,
            title="Test Movie",
            media_path="/movies/test-movie.mkv",
            has_any_subs=False,
            missing_subtitles=[],
        )
        session.add(cache)
        await session.commit()
        await session.refresh(cache)

        result = await session.execute(
            select(BazarrCache).where(BazarrCache.id == "movie:321")
        )
        fetched = result.scalar_one_or_none()
        assert fetched is not None
        assert fetched.series_title is None
        assert fetched.series_ext_id is None
        assert fetched.season_number is None
        assert fetched.episode_number is None
        assert fetched.sort_key is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_job_media_label_snapshot_round_trip():
    """The migration-0008 columns (title, series_title, season_number,
    episode_number) persist and re-fetch correctly for a bazarr_episode
    job."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        from audio_to_subs.db.base import Base

        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        job = Job(
            media_path="/tv/agency/s04e01.mkv",
            output_format=OutputFormat.SRT,
            status=JobStatus.QUEUED,
            source=JobSource.BAZARR_EPISODE,
            source_ref="4242",
            title="Episode 1",
            series_title="The Example Show",
            season_number=4,
            episode_number=1,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        # Fetch fresh from the DB (not just the in-memory object) to make
        # sure the values actually round-trip through the columns.
        result = await session.execute(select(Job).where(Job.id == job.id))
        fetched = result.scalar_one_or_none()
        assert fetched is not None
        assert fetched.title == "Episode 1"
        assert fetched.series_title == "The Example Show"
        assert fetched.season_number == 4
        assert fetched.episode_number == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_job_media_label_snapshot_defaults_to_none():
    """For a manual job (or any job that omits them), the migration-0008
    columns default to NULL/None rather than requiring a value."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        from audio_to_subs.db.base import Base

        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        job = Job(
            media_path="/manual/video.mp4",
            output_format=OutputFormat.SRT,
            status=JobStatus.QUEUED,
            source=JobSource.MANUAL,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        result = await session.execute(select(Job).where(Job.id == job.id))
        fetched = result.scalar_one_or_none()
        assert fetched is not None
        assert fetched.title is None
        assert fetched.series_title is None
        assert fetched.season_number is None
        assert fetched.episode_number is None

    await engine.dispose()
