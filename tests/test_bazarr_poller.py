"""Tests for Bazarr poller."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import select

from audio_to_subs.bazarr.client import BazarrClient
from audio_to_subs.bazarr.pathmap import PathMap
from audio_to_subs.bazarr.poller import (
    ProgressReporter,
    _delete_stale,
    _get_poll_interval,
    _poll_all_episodes,
    _poll_all_movies,
    _process_episode,
    _process_movie,
    get_bazarr_client,
    get_bazarr_client_with_settings,
    get_path_map,
    get_settings_value,
    poll_bazarr_manually,
    poll_once,
    run_bazarr_poller,
    start_poller,
    stop_poller,
)
from audio_to_subs.bazarr.schemas import Episode, Movie, Series, SubtitleLanguage
from audio_to_subs.core.library_sort import library_sort_key
from audio_to_subs.db.models import BazarrCache, Setting


class TestGetBazarrClient:
    """Test get_bazarr_client function."""

    @pytest.mark.asyncio
    async def test_get_client_with_config(self):
        """Test getting client with valid config."""
        client = await get_bazarr_client(
            bazarr_url="http://test:6767",
            bazarr_api_key="test-key",
        )

        assert client is not None
        assert client.base_url == "http://test:6767"
        assert client.api_key == "test-key"

        await client.close()

    @pytest.mark.asyncio
    async def test_get_client_with_timeout(self):
        """Test getting client with custom timeout."""
        client = await get_bazarr_client(
            bazarr_url="http://test:6767",
            bazarr_api_key="test-key",
            bazarr_timeout=60.0,
        )

        assert client is not None
        assert client.base_url == "http://test:6767"
        assert client.api_key == "test-key"
        # Note: timeout is stored in the client but not directly accessible

        await client.close()

    @pytest.mark.asyncio
    async def test_get_client_missing_url(self):
        """Test getting client with missing URL."""
        client = await get_bazarr_client(
            bazarr_url=None,
            bazarr_api_key="test-key",
        )

        assert client is None

    @pytest.mark.asyncio
    async def test_get_client_missing_api_key(self):
        """Test getting client with missing API key."""
        client = await get_bazarr_client(
            bazarr_url="http://test:6767",
            bazarr_api_key=None,
        )

        assert client is None

    @pytest.mark.asyncio
    async def test_get_client_missing_both(self):
        """Test getting client with both missing."""
        client = await get_bazarr_client(
            bazarr_url=None,
            bazarr_api_key=None,
        )

        assert client is None


class TestGetBazarrClientWithSettings:
    """Test get_bazarr_client_with_settings function."""

    @pytest.mark.asyncio
    async def test_get_client_with_database_settings(self, mock_db_session):
        """Test getting client with database settings."""
        # Set up database settings
        db_settings = [
            Setting(key="bazarr_url", value_json=json.dumps("http://db-bazarr:6767")),
            Setting(key="bazarr_api_key", value_json=json.dumps("db-api-key")),
            Setting(key="bazarr_timeout", value_json=json.dumps(60.0)),
        ]
        for setting in db_settings:
            mock_db_session.add(setting)
        await mock_db_session.commit()

        client, url, api_key, timeout = await get_bazarr_client_with_settings(
            mock_db_session
        )

        assert client is not None
        assert client.base_url == "http://db-bazarr:6767"
        assert client.api_key == "db-api-key"
        assert url == "http://db-bazarr:6767"
        assert api_key == "db-api-key"
        assert timeout == 60.0

        await client.close()

    @pytest.mark.asyncio
    async def test_get_client_fallback_to_env_settings(self, mock_db_session):
        """Test getting client falls back to environment settings when database is empty."""
        # Create mock environment settings
        from audio_to_subs.api.settings import Settings

        mock_env_settings = Settings(
            BAZARR_URL="http://env-bazarr:6767",
            BAZARR_API_KEY="env-api-key",
            BAZARR_TIMEOUT=90.0,
        )

        client, url, api_key, timeout = await get_bazarr_client_with_settings(
            mock_db_session, mock_env_settings
        )

        assert client is not None
        assert client.base_url == "http://env-bazarr:6767"
        assert client.api_key == "env-api-key"
        assert url == "http://env-bazarr:6767"
        assert api_key == "env-api-key"
        assert timeout == 90.0

        await client.close()

    @pytest.mark.asyncio
    async def test_get_client_database_priority_over_env(self, mock_db_session):
        """Test that database settings take priority over environment settings."""
        # Set up database settings
        db_settings = [
            Setting(key="bazarr_url", value_json=json.dumps("http://db-bazarr:6767")),
            Setting(key="bazarr_api_key", value_json=json.dumps("db-api-key")),
            Setting(key="bazarr_timeout", value_json=json.dumps(60.0)),
        ]
        for setting in db_settings:
            mock_db_session.add(setting)
        await mock_db_session.commit()

        # Create mock environment settings with different values
        from audio_to_subs.api.settings import Settings

        mock_env_settings = Settings(
            BAZARR_URL="http://env-bazarr:6767",  # Should be ignored
            BAZARR_API_KEY="env-api-key",  # Should be ignored
            BAZARR_TIMEOUT=90.0,  # Should be ignored
        )

        client, url, api_key, timeout = await get_bazarr_client_with_settings(
            mock_db_session, mock_env_settings
        )

        assert client is not None
        assert client.base_url == "http://db-bazarr:6767"  # DB takes priority
        assert client.api_key == "db-api-key"  # DB takes priority
        assert url == "http://db-bazarr:6767"
        assert api_key == "db-api-key"
        assert timeout == 60.0  # DB takes priority

        await client.close()

    @pytest.mark.asyncio
    async def test_get_client_empty_string_url_no_fallback(self, mock_db_session):
        """Test that empty string URL in DB does NOT fall back to env (sentinel fix)."""
        # Set DB setting to empty string (user explicitly cleared it)
        db_settings = [
            Setting(key="bazarr_url", value_json=json.dumps("")),
            Setting(key="bazarr_api_key", value_json=json.dumps("db-api-key")),
        ]
        for setting in db_settings:
            mock_db_session.add(setting)
        await mock_db_session.commit()

        # Create mock environment settings with non-empty values
        from audio_to_subs.api.settings import Settings

        mock_env_settings = Settings(
            BAZARR_URL="http://env-bazarr:6767",  # Should NOT be used
            BAZARR_API_KEY="env-api-key",
        )

        client, url, api_key, timeout = await get_bazarr_client_with_settings(
            mock_db_session, mock_env_settings
        )

        # Client should be None because URL is empty string (falsy but explicitly set)
        assert client is None
        assert url == ""  # Empty string from DB, not env fallback

    @pytest.mark.asyncio
    async def test_get_client_timeout_30_no_fallback(self, mock_db_session):
        """Test that timeout=30.0 in DB does NOT fall back to env (sentinel fix)."""
        # Set DB setting to 30.0 (the default value)
        db_settings = [
            Setting(key="bazarr_url", value_json=json.dumps("http://db-bazarr:6767")),
            Setting(key="bazarr_api_key", value_json=json.dumps("db-api-key")),
            Setting(key="bazarr_timeout", value_json=json.dumps(30.0)),
        ]
        for setting in db_settings:
            mock_db_session.add(setting)
        await mock_db_session.commit()

        # Create mock environment settings with different timeout
        from audio_to_subs.api.settings import Settings

        mock_env_settings = Settings(
            BAZARR_URL="http://db-bazarr:6767",
            BAZARR_API_KEY="db-api-key",
            BAZARR_TIMEOUT=90.0,  # Should NOT be used
        )

        client, url, api_key, timeout = await get_bazarr_client_with_settings(
            mock_db_session, mock_env_settings
        )

        assert client is not None
        assert timeout == 30.0  # DB value, not env fallback

        await client.close()

    @pytest.mark.asyncio
    async def test_get_client_seeded_null_falls_back_to_env(self, mock_db_session):
        """Test that seeded DEFAULT_SETTINGS rows (JSON null) still fall back to env.

        Reproduces the scenario created by `_seed_default_settings`, which is
        called on every `GET /api/settings` and inserts a row for every key in
        DEFAULT_SETTINGS -- including bazarr_url=None and bazarr_api_key=None --
        the first time the settings table is empty. Before the fix, a DB row
        holding JSON `null` decoded to a real `None`, which was indistinguishable
        from an explicit "disable Bazarr" and therefore never fell back to env.

        bazarr_timeout is seeded to a real value (30.0, DEFAULT_SETTINGS'
        default), not None, so it's correctly treated as explicitly set and
        stays sticky -- only bazarr_url/bazarr_api_key (seeded as None) should
        fall back to env here.
        """
        from audio_to_subs.api.routes.settings import DEFAULT_SETTINGS

        for key, value in DEFAULT_SETTINGS.items():
            mock_db_session.add(Setting(key=key, value_json=json.dumps(value)))
        await mock_db_session.commit()

        from audio_to_subs.api.settings import Settings

        mock_env_settings = Settings(
            BAZARR_URL="http://env-bazarr:6767",
            BAZARR_API_KEY="env-api-key",
            BAZARR_TIMEOUT=45.0,
        )

        client, url, api_key, timeout = await get_bazarr_client_with_settings(
            mock_db_session, mock_env_settings
        )

        assert client is not None
        assert url == "http://env-bazarr:6767"
        assert api_key == "env-api-key"
        assert timeout == 30.0  # seeded DB value, not env fallback

        await client.close()


class TestGetPathMap:
    """Test get_path_map function."""

    @pytest.mark.asyncio
    async def test_get_path_map_empty_db(self, mock_db_session):
        """Test get_path_map with empty database."""
        path_map = await get_path_map(mock_db_session)

        assert isinstance(path_map, PathMap)
        assert len(path_map.get_mappings()) == 0

    @pytest.mark.asyncio
    async def test_get_path_map_with_settings(self, mock_db_session):
        """Test get_path_map with settings in database."""
        # Create a setting with path mappings
        path_mappings = [
            {"bazarr_prefix": "/bazarr/movies", "local_prefix": "/local/movies"},
        ]

        setting = Setting(
            key="path_mappings",
            value_json=json.dumps(path_mappings),
        )
        mock_db_session.add(setting)
        await mock_db_session.commit()

        path_map = await get_path_map(mock_db_session)

        assert isinstance(path_map, PathMap)
        assert len(path_map.get_mappings()) == 1


class TestGetSettingsValue:
    """Test get_settings_value function."""

    @pytest.mark.asyncio
    async def test_get_settings_value_existing(self, mock_db_session):
        """Test get_settings_value with existing setting."""
        setting = Setting(
            key="test_setting",
            value_json=json.dumps("test_value"),
        )
        mock_db_session.add(setting)
        await mock_db_session.commit()

        value = await get_settings_value(mock_db_session, "test_setting", "default")

        assert value == "test_value"

    @pytest.mark.asyncio
    async def test_get_settings_value_missing(self, mock_db_session):
        """Test get_settings_value with missing setting."""
        value = await get_settings_value(
            mock_db_session, "nonexistent", "default_value"
        )

        assert value == "default_value"


class TestProcessMovie:
    """Test _process_movie function against Bazarr's full `/api/movies` Movie
    schema (full-sync ingest - no separate wanted/detail split anymore)."""

    @pytest.mark.asyncio
    async def test_process_movie_new_entry(self, mock_db_session):
        """Test processing a movie creates a new cache entry."""
        movie = Movie(
            title="Inception",
            radarrId=123,
            sceneName="/bazarr/movies/Inception.mkv",
        )
        path_map = PathMap([("/bazarr/movies", "/local/movies")])
        started_at = datetime.now(timezone.utc)

        await _process_movie(mock_db_session, movie, path_map, started_at)

        # Check that the entry was created
        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "movie:123")
        )
        entry = result.scalar_one_or_none()

        assert entry is not None
        assert entry.kind == "movie"
        assert entry.ext_id == 123
        assert entry.title == "Inception"
        # Path should be translated
        assert "/local/movies" in entry.media_path

    @pytest.mark.asyncio
    async def test_process_movie_update_existing(self, mock_db_session):
        """Test processing a movie updates existing cache entry."""
        # Create existing entry
        existing = BazarrCache(
            id="movie:123",
            kind="movie",
            ext_id=123,
            title="Old Title",
            media_path="/old/path.mkv",
            has_any_subs=False,
            missing_subtitles=[],
            last_polled=datetime.now(timezone.utc) - timedelta(days=1),
        )
        mock_db_session.add(existing)
        await mock_db_session.commit()

        movie = Movie(
            title="New Title",
            radarrId=123,
            sceneName="/bazarr/movies/NewTitle.mkv",
        )
        path_map = PathMap([("/bazarr/movies", "/local/movies")])
        started_at = datetime.now(timezone.utc)

        await _process_movie(mock_db_session, movie, path_map, started_at)

        # Check that the entry was updated
        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "movie:123")
        )
        entry = result.scalar_one_or_none()

        assert entry is not None
        assert entry.title == "New Title"
        assert "/local/movies/NewTitle.mkv" in entry.media_path

    @pytest.mark.asyncio
    async def test_process_movie_stores_audio_language(self, mock_db_session):
        """The movie's own audio_language (carried by `/api/movies`) is
        stored on the cache row - no separate detail fetch is needed."""
        movie = Movie(
            title="French Film",
            radarrId=321,
            sceneName="/bazarr/movies/French Film.mkv",
            audio_language=[SubtitleLanguage(name="French", code2="fr", code3="fre")],
        )
        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        await _process_movie(mock_db_session, movie, path_map, started_at)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "movie:321")
        )
        entry = result.scalar_one_or_none()

        assert entry is not None
        assert entry.audio_language == [
            {
                "name": "French",
                "code2": "fr",
                "code3": "fre",
                "forced": False,
                "hi": False,
            }
        ]

    @pytest.mark.asyncio
    async def test_process_movie_no_audio_language_defaults_to_empty(
        self, mock_db_session
    ):
        """When Bazarr can't report an audio language, the cache stores []
        (not None) - this is what drives the frontend's Auto-only dropdown."""
        movie = Movie(
            title="Unknown Audio Film",
            radarrId=322,
            sceneName="/bazarr/movies/Unknown.mkv",
        )
        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        await _process_movie(mock_db_session, movie, path_map, started_at)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "movie:322")
        )
        entry = result.scalar_one_or_none()

        assert entry is not None
        assert entry.audio_language == []

    @pytest.mark.asyncio
    async def test_process_movie_prefers_path_over_sceneName(self, mock_db_session):
        """`/api/movies`'s `path` field is authoritative; `sceneName` is only
        the release name and is often null. `path` must win when both are
        present."""
        movie = Movie(
            title="Inception",
            radarrId=700,
            sceneName="Inception.2010.1080p.BluRay.x264-GROUP",
            path="/bazarr/movies/Inception (2010)/Inception.mkv",
        )
        path_map = PathMap([("/bazarr/movies", "/local/movies")])
        started_at = datetime.now(timezone.utc)

        await _process_movie(mock_db_session, movie, path_map, started_at)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "movie:700")
        )
        entry = result.scalar_one()
        # The `path` field was translated and stored, not the sceneName.
        assert entry.media_path == "/local/movies/Inception (2010)/Inception.mkv"

    @pytest.mark.asyncio
    async def test_process_movie_null_path_falls_back_to_sceneName(
        self, mock_db_session
    ):
        """When `path` is null, media_path falls back to sceneName."""
        movie = Movie(
            title="The Players",
            radarrId=701,
            sceneName="/movies/The Players (2012)/The Players 720p H264.mkv",
            path=None,
        )
        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        await _process_movie(mock_db_session, movie, path_map, started_at)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "movie:701")
        )
        entry = result.scalar_one()
        assert entry.media_path == (
            "/movies/The Players (2012)/The Players 720p H264.mkv"
        )

    @pytest.mark.asyncio
    async def test_process_movie_has_any_subs_from_present_subtitles(
        self, mock_db_session
    ):
        """has_any_subs reflects PRESENT subtitle files, not the missing list.

        A movie still missing English but already carrying a French
        subtitle file must report has_any_subs=True.
        """
        movie = Movie(
            title="Mixed Movie",
            radarrId=4242,
            sceneName="/bazarr/movies/Mixed Movie.mkv",
            missing_subtitles=[SubtitleLanguage(name="English", code2="en")],
            subtitles=[SubtitleLanguage(name="French", code2="fr", code3="fre")],
        )
        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        await _process_movie(mock_db_session, movie, path_map, started_at)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "movie:4242")
        )
        entry = result.scalar_one_or_none()

        assert entry is not None
        assert entry.has_any_subs is True

    @pytest.mark.asyncio
    async def test_process_movie_has_any_subs_false_when_no_present_subs(
        self, mock_db_session
    ):
        """Without any present subtitle file, has_any_subs is False."""
        movie = Movie(
            title="No Subs Movie",
            radarrId=4243,
            sceneName="/bazarr/movies/No Subs Movie.mkv",
        )
        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        await _process_movie(mock_db_session, movie, path_map, started_at)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "movie:4243")
        )
        entry = result.scalar_one_or_none()

        assert entry is not None
        assert entry.has_any_subs is False

    @pytest.mark.asyncio
    async def test_process_movie_stores_missing_subtitles(self, mock_db_session):
        """missing_subtitles (now present on the full `/api/movies` schema)
        round-trips onto the cache row - full sync no longer loses this."""
        movie = Movie(
            title="Partially Subbed",
            radarrId=4244,
            sceneName="/bazarr/movies/Partial.mkv",
            missing_subtitles=[SubtitleLanguage(name="German", code2="de")],
        )
        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        await _process_movie(mock_db_session, movie, path_map, started_at)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "movie:4244")
        )
        entry = result.scalar_one()
        assert entry.missing_subtitles == [
            {
                "name": "German",
                "code2": "de",
                "code3": None,
                "forced": False,
                "hi": False,
            }
        ]

    @pytest.mark.asyncio
    async def test_process_movie_sets_sort_key_and_leaves_episode_fields_null(
        self, mock_db_session
    ):
        """A movie's sort_key comes from its own title, and the
        episode-only structured fields (series_title, series_ext_id,
        season_number, episode_number) stay None - a movie has no series."""
        movie = Movie(
            title="The Movie Title",
            radarrId=5001,
            sceneName="/bazarr/movies/The Movie Title.mkv",
        )
        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        await _process_movie(mock_db_session, movie, path_map, started_at)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "movie:5001")
        )
        entry = result.scalar_one()

        assert entry.sort_key == library_sort_key("The Movie Title")
        assert entry.series_title is None
        assert entry.series_ext_id is None
        assert entry.season_number is None
        assert entry.episode_number is None


class TestProcessEpisode:
    """Test _process_episode function against Bazarr's full `/api/episodes`
    Episode schema (full-sync ingest - no separate wanted/detail split
    anymore). `series` is now an explicit caller-supplied argument (the
    resolved Series, or None) since a per-episode payload doesn't carry its
    series' title - `_process_episode` derives `series_title`/`sort_key`
    from it and stores `episode.title` bare."""

    @pytest.mark.asyncio
    async def test_process_episode_new_entry(self, mock_db_session):
        """Test processing an episode creates a new cache entry."""
        episode = Episode(
            sonarrEpisodeId=456,
            sonarrSeriesId=789,
            title="Pilot",
            sceneName="/bazarr/tv/Test Show/Pilot.mkv",
            missing_subtitles=[SubtitleLanguage(name="English", code2="en")],
        )
        series = Series(sonarrSeriesId=789, title="Test Show", path="/tv/Test Show")
        path_map = PathMap([("/bazarr/tv", "/local/tv")])
        started_at = datetime.now(timezone.utc)

        await _process_episode(mock_db_session, episode, path_map, started_at, series)

        # Check that the entry was created
        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "episode:456")
        )
        entry = result.scalar_one_or_none()

        assert entry is not None
        assert entry.kind == "episode"
        assert entry.ext_id == 456
        # `title` is the episode's own bare title now, never flattened with
        # the series title.
        assert entry.title == "Pilot"
        assert entry.series_title == "Test Show"
        assert entry.series_ext_id == 789
        assert entry.sort_key == library_sort_key("Test Show")

    @pytest.mark.asyncio
    async def test_process_episode_backfills_a_pre_0007_flattened_row(
        self, mock_db_session
    ):
        """A row synced by a pre-migration-0007 worker has the old
        flattened "<series> - <episode>" title and NULL structured fields.
        The very next sync must overwrite it with the bare title and the
        real structured fields - this is the mechanism migration 0007's
        docstring promises ("existing rows are backfilled by the next full
        Bazarr sync"), and nothing else in this file exercises the UPDATE
        branch for an episode (only `test_process_movie_update_existing`
        covers an update, and only for a movie).
        """
        pre_0007_row = BazarrCache(
            id="episode:456",
            kind="episode",
            ext_id=456,
            title="Test Show - Pilot",
            media_path="/old/tv/Test Show/Pilot.mkv",
            has_any_subs=False,
            missing_subtitles=[],
            last_polled=datetime.now(timezone.utc) - timedelta(days=1),
            series_title=None,
            series_ext_id=None,
            season_number=None,
            episode_number=None,
            sort_key=None,
        )
        mock_db_session.add(pre_0007_row)
        await mock_db_session.commit()

        episode = Episode(
            sonarrEpisodeId=456,
            sonarrSeriesId=789,
            title="Pilot",
            season=1,
            episode=1,
            sceneName="/bazarr/tv/Test Show/Pilot.mkv",
        )
        series = Series(sonarrSeriesId=789, title="Test Show", path="/tv/Test Show")
        path_map = PathMap([("/bazarr/tv", "/local/tv")])
        started_at = datetime.now(timezone.utc)

        await _process_episode(mock_db_session, episode, path_map, started_at, series)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "episode:456")
        )
        entry = result.scalar_one_or_none()

        assert entry is not None
        assert entry.title == "Pilot"
        assert entry.series_title == "Test Show"
        assert entry.series_ext_id == 789
        assert entry.season_number == 1
        assert entry.episode_number == 1
        assert entry.sort_key == library_sort_key("Test Show")

    @pytest.mark.asyncio
    async def test_process_episode_persists_season_and_episode_number(
        self, mock_db_session
    ):
        """season_number/episode_number are persisted straight from
        Episode.season/Episode.episode, unpadded/undisplayed - the fix for
        the Wanted page showing every episode of a season as an
        indistinguishable "<series> - <episode title>" row."""
        episode = Episode(
            sonarrEpisodeId=457,
            sonarrSeriesId=789,
            title="Episode 2",
            sceneName="/bazarr/tv/Test Show/Episode 2.mkv",
            season=3,
            episode=12,
        )
        series = Series(sonarrSeriesId=789, title="Test Show", path="/tv/Test Show")
        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        await _process_episode(mock_db_session, episode, path_map, started_at, series)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "episode:457")
        )
        entry = result.scalar_one()

        assert entry.season_number == 3
        assert entry.episode_number == 12

    @pytest.mark.asyncio
    async def test_process_episode_unknown_series_fallback(self, mock_db_session):
        """When the caller can't resolve the episode's series (e.g. its
        sonarrSeriesId doesn't match any series in the current page),
        `_process_episode` still stores a usable series_title/series_ext_id/
        sort_key instead of leaving them null."""
        episode = Episode(
            sonarrEpisodeId=458,
            sonarrSeriesId=999,
            title="Orphan Episode",
            sceneName="/bazarr/tv/Orphan/Orphan Episode.mkv",
            season=1,
            episode=1,
        )
        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        await _process_episode(mock_db_session, episode, path_map, started_at, None)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "episode:458")
        )
        entry = result.scalar_one()

        assert entry.title == "Orphan Episode"
        assert entry.series_title == "Unknown series"
        # sonarrSeriesId is a required field on every Episode regardless of
        # whether the series lookup succeeded.
        assert entry.series_ext_id == 999
        assert entry.sort_key is not None
        assert entry.sort_key == library_sort_key("Unknown series")

    @pytest.mark.asyncio
    async def test_process_episode_has_any_subs_from_present_subtitles(
        self, mock_db_session
    ):
        """has_any_subs reflects PRESENT subtitle files, not the missing list.

        This pins that an episode which is still missing English but
        already HAS a French subtitle is reported as has_any_subs=True.
        """
        episode = Episode(
            sonarrEpisodeId=999,
            sonarrSeriesId=1,
            title="Mixed",
            sceneName="/bazarr/tv/Test Show/Mixed.mkv",
            missing_subtitles=[SubtitleLanguage(name="English", code2="en")],
            subtitles=[SubtitleLanguage(name="French", code2="fr", code3="fre")],
        )
        series = Series(sonarrSeriesId=1, title="Test Show", path="/tv/Test Show")
        path_map = PathMap([])
        started_at = datetime.now(timezone.utc)

        await _process_episode(mock_db_session, episode, path_map, started_at, series)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "episode:999")
        )
        entry = result.scalar_one_or_none()

        assert entry is not None
        assert entry.has_any_subs is True

    @pytest.mark.asyncio
    async def test_process_episode_has_any_subs_false_when_no_present_subs(
        self, mock_db_session
    ):
        """Without any present subtitle file, has_any_subs is False."""
        episode = Episode(
            sonarrEpisodeId=1000,
            sonarrSeriesId=1,
            title="Complete",
            sceneName="/bazarr/tv/Test Show/Complete.mkv",
        )
        series = Series(sonarrSeriesId=1, title="Test Show", path="/tv/Test Show")
        path_map = PathMap([])
        started_at = datetime.now(timezone.utc)

        await _process_episode(mock_db_session, episode, path_map, started_at, series)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "episode:1000")
        )
        entry = result.scalar_one_or_none()

        assert entry is not None
        assert entry.has_any_subs is False

    @pytest.mark.asyncio
    async def test_process_episode_stores_audio_language(self, mock_db_session):
        """The episode's own audio_language (carried by `/api/episodes`) is
        stored on the cache row - no separate detail fetch is needed."""
        episode = Episode(
            sonarrEpisodeId=1001,
            sonarrSeriesId=2001,
            title="Pilot",
            sceneName="/bazarr/tv/German Show/Pilot.mkv",
            audio_language=[SubtitleLanguage(name="German", code2="de", code3="ger")],
        )
        series = Series(
            sonarrSeriesId=2001, title="German Show", path="/tv/German Show"
        )
        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        await _process_episode(mock_db_session, episode, path_map, started_at, series)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "episode:1001")
        )
        entry = result.scalar_one_or_none()

        assert entry is not None
        assert entry.audio_language == [
            {
                "name": "German",
                "code2": "de",
                "code3": "ger",
                "forced": False,
                "hi": False,
            }
        ]


class TestProcessEpisodePath:
    """Test _process_episode media_path resolution from `/api/episodes`."""

    @pytest.mark.asyncio
    async def test_process_episode_null_path_falls_back_to_sceneName(
        self, mock_db_session
    ):
        """When `path` is null, media_path falls back to sceneName."""
        episode = Episode(
            sonarrEpisodeId=324,
            sonarrSeriesId=5,
            title="Jupiter",
            sceneName="/tv/Baron Noir/S01E03.mkv",
            path=None,
        )
        series = Series(sonarrSeriesId=5, title="Baron Noir", path="/tv/Baron Noir")
        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        await _process_episode(mock_db_session, episode, path_map, started_at, series)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "episode:324")
        )
        entry = result.scalar_one()
        assert entry.media_path == "/tv/Baron Noir/S01E03.mkv"

    @pytest.mark.asyncio
    async def test_process_episode_prefers_path_over_sceneName(self, mock_db_session):
        """`path` wins over sceneName when both are present."""
        episode = Episode(
            sonarrEpisodeId=702,
            sonarrSeriesId=9,
            title="Pilot",
            sceneName="Test.Show.S01E01.1080p.WEB.x264-GROUP",
            path="/bazarr/tv/Test Show/S01E01.mkv",
        )
        series = Series(sonarrSeriesId=9, title="Test Show", path="/tv/Test Show")
        path_map = PathMap([("/bazarr/tv", "/local/tv")])
        started_at = datetime.now(timezone.utc)

        await _process_episode(mock_db_session, episode, path_map, started_at, series)

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.id == "episode:702")
        )
        entry = result.scalar_one()
        assert entry.media_path == "/local/tv/Test Show/S01E01.mkv"


class TestDeleteStale:
    """Test _delete_stale function."""

    @pytest.mark.asyncio
    async def test_delete_stale_removes_old_entries(self, mock_db_session):
        """Test that stale entries are deleted."""
        # Create old entries
        old_time = datetime.now(timezone.utc) - timedelta(days=1)

        old_entry1 = BazarrCache(
            id="movie:1",
            kind="movie",
            ext_id=1,
            title="Old Movie",
            media_path="/path.mkv",
            has_any_subs=False,
            missing_subtitles=[],
            last_polled=old_time,
        )
        old_entry2 = BazarrCache(
            id="movie:2",
            kind="movie",
            ext_id=2,
            title="Another Old Movie",
            media_path="/path2.mkv",
            has_any_subs=False,
            missing_subtitles=[],
            last_polled=old_time,
        )
        mock_db_session.add_all([old_entry1, old_entry2])
        await mock_db_session.commit()

        # Delete stale entries
        started_at = datetime.now(timezone.utc)
        deleted_count = await _delete_stale(mock_db_session, started_at)

        assert deleted_count == 2

    @pytest.mark.asyncio
    async def test_delete_stale_keeps_recent_entries(self, mock_db_session):
        """Test that entries polled AFTER started_at are kept."""
        # started_at is set in the past so that recent_entry.last_polled > started_at.
        started_at = datetime.now(timezone.utc) - timedelta(seconds=5)

        recent_entry = BazarrCache(
            id="movie:3",
            kind="movie",
            ext_id=3,
            title="Recent Movie",
            media_path="/path.mkv",
            has_any_subs=False,
            missing_subtitles=[],
            last_polled=datetime.now(timezone.utc),  # after started_at
        )
        mock_db_session.add(recent_entry)
        await mock_db_session.commit()

        deleted_count = await _delete_stale(mock_db_session, started_at)

        assert deleted_count == 0


class TestGetPollInterval:
    """Test _get_poll_interval function."""

    @pytest.mark.asyncio
    async def test_get_poll_interval_default(self, mock_db_session):
        """Test get_poll_interval returns default value."""
        interval = await _get_poll_interval(mock_db_session)

        assert interval == 3600

    @pytest.mark.asyncio
    async def test_get_poll_interval_custom(self, mock_db_session):
        """Test get_poll_interval returns custom value."""
        setting = Setting(
            key="bazarr_poll_interval",
            value_json=json.dumps(7200),
        )
        mock_db_session.add(setting)
        await mock_db_session.commit()

        interval = await _get_poll_interval(mock_db_session)

        assert interval == 7200


class TestPollerIntegration:
    """Integration tests for poller."""

    @pytest.mark.asyncio
    async def test_poll_once_with_mock_client(self, mock_db_session):
        """Test poll_once with a mock client (full-library sync)."""
        from audio_to_subs.bazarr.schemas import EpisodesPage, MoviesPage, SeriesPage

        # Create mock client
        mock_client = AsyncMock(spec=BazarrClient)

        # Mock empty library
        mock_client.list_all_movies.return_value = MoviesPage(data=[], total=0)
        mock_client.list_all_series.return_value = SeriesPage(data=[], total=0)
        mock_client.list_episodes.return_value = EpisodesPage(data=[])

        path_map = PathMap()

        # Call poll_once
        processed = await poll_once(mock_db_session, mock_client, path_map)

        assert processed == 0
        # Full sync: movies and series are each fetched exactly once.
        mock_client.list_all_movies.assert_awaited_once()
        mock_client.list_all_series.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_poller(self):
        """Test start_poller function."""
        mock_app = Mock()
        mock_app.state = Mock()
        mock_app.state.shutdown = asyncio.Event()
        mock_app.state.poller_task = None

        await start_poller(mock_app)

        assert mock_app.state.poller_task is not None

    @pytest.mark.asyncio
    async def test_stop_poller(self):
        """Test stop_poller cancels the running task and clears the state."""
        mock_app = Mock()
        mock_app.state = Mock()

        # Use a real asyncio task so stop_poller can call .cancel() and await it.
        async def _dummy():
            await asyncio.sleep(10)

        task = asyncio.create_task(_dummy())
        mock_app.state.poller_task = task

        await stop_poller(mock_app)

        assert task.cancelled()
        assert mock_app.state.poller_task is None

    @pytest.mark.asyncio
    async def test_run_bazarr_poller_exits_when_shutdown_set(self):
        """run_bazarr_poller must return promptly when shutdown is already set.

        Before the fix, run_bazarr_poller checked app.state.shutdown at the top
        of its loop and raised AttributeError because the lifespan never created
        the event.  After the fix the function reads the event, sees it is set,
        and returns immediately.
        """
        mock_app = Mock()
        mock_app.state = Mock()
        mock_app.state.shutdown = asyncio.Event()
        mock_app.state.shutdown.set()  # already shut down

        # Should return in well under 2 seconds; if it hangs the fix is broken.
        await asyncio.wait_for(run_bazarr_poller(mock_app), timeout=2.0)

    @pytest.mark.asyncio
    async def test_run_bazarr_poller_wait_for_no_suppress_timeout_kwarg(self):
        """The interval wait inside run_bazarr_poller must not raise TypeError.

        Before the fix, asyncio.wait_for was called with suppress_timeout=True
        which is not a valid kwarg and raises TypeError immediately after the
        first Bazarr-not-configured sleep attempt.
        After the fix the interval wait uses try/except asyncio.TimeoutError.
        """
        mock_app = Mock()
        mock_app.state = Mock()
        mock_app.state.shutdown = asyncio.Event()

        # Signal shutdown after a short delay so the poller exercises the
        # wait_for path (Bazarr not configured → interval wait) at least once.
        async def _trigger():
            await asyncio.sleep(0.2)
            mock_app.state.shutdown.set()

        asyncio.create_task(_trigger())

        # Patch the DB session so the poller can get the interval without a DB.
        with patch(
            "audio_to_subs.bazarr.poller._get_poll_interval",
            new=AsyncMock(return_value=60),
        ):
            # TypeError from suppress_timeout=True would propagate here and fail.
            await asyncio.wait_for(run_bazarr_poller(mock_app), timeout=3.0)

    @pytest.mark.asyncio
    async def test_poller_releases_session_before_sleep(self):
        """Regression: DB session must exit before the inter-poll wait.

        Before the fix, the asyncio.wait_for sleep (when Bazarr is not
        configured) ran inside the async-with get_async_session() block.
        This held a BEGIN IMMEDIATE write lock for the full poll interval
        (default 3600 s), blocking every other writer (reaper, health check).
        """
        mock_app = Mock()
        mock_app.state = Mock()
        mock_app.state.shutdown = asyncio.Event()
        lifecycle: list[str] = []

        # Capture the real wait_for before any patching to avoid self-recursion
        # when the patch replaces asyncio.wait_for on the shared asyncio module.
        real_wait_for = asyncio.wait_for

        @asynccontextmanager
        async def _tracked_session(_url: str):
            lifecycle.append("enter")
            yield Mock()
            lifecycle.append("exit")

        async def _tracked_wait_for(coro, timeout):
            lifecycle.append(f"wait:{int(timeout)}")
            # Trigger shutdown so the poller terminates after one iteration.
            mock_app.state.shutdown.set()
            await real_wait_for(coro, timeout=1.0)

        with (
            # get_async_session is imported locally inside run_bazarr_poller, so
            # patch at the source module (not the poller module attribute).
            patch("audio_to_subs.db.session.get_async_session", _tracked_session),
            patch(
                "audio_to_subs.bazarr.poller._get_poll_interval",
                new=AsyncMock(return_value=3600),
            ),
            patch(
                "audio_to_subs.bazarr.poller.get_bazarr_client",
                new=AsyncMock(return_value=None),
            ),
            patch("audio_to_subs.bazarr.poller.asyncio.wait_for", _tracked_wait_for),
        ):
            await real_wait_for(run_bazarr_poller(mock_app), timeout=5.0)

        exit_idx = lifecycle.index("exit")
        wait_idx = next(i for i, e in enumerate(lifecycle) if e.startswith("wait:3600"))
        assert (
            exit_idx < wait_idx
        ), f"Session must close before the inter-poll sleep; lifecycle={lifecycle}"


class TestPollAllMovies:
    """Test _poll_all_movies implementation (full-library sync)."""

    @pytest.mark.asyncio
    async def test_poll_all_movies_caches_every_movie_regardless_of_subs(
        self, mock_db_session
    ):
        """_poll_all_movies caches EVERY movie - fully-subtitled included,
        not just missing-subtitle ones (the pre-M8 behaviour this replaces)."""
        from audio_to_subs.bazarr.schemas import MoviesPage, SubtitleLanguage

        fully_subbed = Movie(
            title="Fully Subbed",
            radarrId=1,
            sceneName="/movies/fully-subbed.mkv",
            subtitles=[SubtitleLanguage(name="English", code2="en")],
        )
        missing_sub = Movie(
            title="Missing Sub",
            radarrId=2,
            sceneName="/movies/missing-sub.mkv",
            missing_subtitles=[SubtitleLanguage(name="English", code2="en")],
        )
        movies_page = MoviesPage(data=[fully_subbed, missing_sub], total=2)

        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        processed = await _poll_all_movies(
            mock_db_session, movies_page, path_map, started_at
        )

        assert processed == 2
        cached = {
            c.ext_id: c
            for c in (
                await mock_db_session.execute(
                    select(BazarrCache).where(BazarrCache.kind == "movie")
                )
            ).scalars()
        }
        assert len(cached) == 2
        assert cached[1].has_any_subs is True
        assert cached[2].has_any_subs is False
        assert cached[2].missing_subtitles[0]["code2"] == "en"

    @pytest.mark.asyncio
    async def test_poll_all_movies_advances_processed_against_preset_total(
        self, mock_db_session
    ):
        """_poll_all_movies no longer seeds the reporter's known total itself
        (poll_bazarr_manually now sets one combined total for movies+episodes
        up front, before either phase runs) - it just advances `processed`
        against whatever total the caller already set."""
        from audio_to_subs.bazarr.schemas import MoviesPage

        movies_page = MoviesPage(data=[Movie(title="A", radarrId=1)], total=458)
        reporter = ProgressReporter()
        reporter.set_known_total(458)
        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        await _poll_all_movies(
            mock_db_session, movies_page, path_map, started_at, reporter
        )

        assert reporter.total == 458
        assert reporter.processed == 1

    @pytest.mark.asyncio
    async def test_poll_all_movies_commits_once_per_batch(self, mock_db_session):
        """Movies are committed in CACHE_COMMIT_BATCH_SIZE-sized chunks, not
        once per movie - bounds the number of SQLite write transactions per
        full sync instead of issuing one per item."""
        from audio_to_subs.bazarr.schemas import MoviesPage

        movies = [Movie(title=f"Movie {i}", radarrId=i) for i in range(1, 6)]
        movies_page = MoviesPage(data=movies, total=5)

        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        commit_mock = AsyncMock(wraps=mock_db_session.commit)
        mock_db_session.commit = commit_mock

        with patch("audio_to_subs.bazarr.poller.CACHE_COMMIT_BATCH_SIZE", 2):
            processed = await _poll_all_movies(
                mock_db_session, movies_page, path_map, started_at
            )

        # 5 movies at a batch size of 2 -> 3 commits (2, 2, 1), not 5.
        assert commit_mock.await_count == 3
        assert processed == 5

        cached = (
            (
                await mock_db_session.execute(
                    select(BazarrCache).where(BazarrCache.kind == "movie")
                )
            )
            .scalars()
            .all()
        )
        assert len(cached) == 5


class TestPollAllEpisodes:
    """Test _poll_all_episodes implementation (full-library sync)."""

    @pytest.mark.asyncio
    async def test_poll_all_episodes_implementation(self, mock_db_session):
        """_poll_all_episodes caches EVERY episode - fully-subtitled included,
        not just no-subs ones (the pre-M8 behaviour this replaces)."""
        from audio_to_subs.bazarr.client import BazarrClient
        from audio_to_subs.bazarr.pathmap import PathMap

        # Create mock client
        mock_client = AsyncMock(spec=BazarrClient)

        # Mock series and episodes responses
        from audio_to_subs.bazarr.schemas import (
            Episode,
            EpisodesPage,
            Series,
            SeriesPage,
        )

        mock_series = Series(
            sonarrSeriesId=1,
            title="Test Series",
            path="/tv/Test Series",
            tvdbId=123,
            imdbId=None,
            monitored=True,
            profileId=None,
            seriesType="standard",
            tags=[],
            alternativeTitles=[],
            ended=False,
            lastAired=None,
            fanart=None,
            poster=None,
            overview=None,
            year=None,
            audio_language=None,
        )

        mock_episode_no_subs = Episode(
            sonarrEpisodeId=100,
            sonarrSeriesId=789,
            title="Test Episode",
            subtitles=[],  # No subtitles
            season=1,
            episode=1,
            path="/bazarr/tv/Test Series/Season 01/Episode 01.mkv",
            sceneName="/bazarr/tv/Test Series/Season 01/Episode 01.mkv",
        )

        mock_episode_with_subs = Episode(
            sonarrEpisodeId=101,
            sonarrSeriesId=789,
            title="Test Episode 2",
            subtitles=[
                {
                    "code2": "en",
                    "code3": "eng",
                    "name": "English",
                    "forced": False,
                    "hi": False,
                }
            ],  # Has subtitles
            season=1,
            episode=2,
            path="/bazarr/tv/Test Series/Season 01/Episode 02.mkv",
            sceneName="/bazarr/tv/Test Series/Season 01/Episode 02.mkv",
        )

        series_page = SeriesPage(
            data=[mock_series],
            total=1,
        )
        mock_client.list_episodes.return_value = EpisodesPage(
            data=[mock_episode_no_subs, mock_episode_with_subs],
        )

        path_map = PathMap([("/bazarr/tv", "/local/tv")])
        started_at = datetime.now(timezone.utc)

        # Call the function
        processed = await _poll_all_episodes(
            mock_db_session, mock_client, series_page, path_map, started_at
        )

        # Verify BOTH episodes were cached (full sync, not a no-subs filter)
        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.kind == "episode")
        )
        cached = {c.ext_id: c for c in result.scalars().all()}

        assert processed == 2
        assert len(cached) == 2
        assert cached[100].has_any_subs is False
        assert "/local/tv" in cached[100].media_path  # Path was translated
        assert cached[101].has_any_subs is True
        assert "/local/tv" in cached[101].media_path

        # season_number/episode_number are persisted from the Episode
        # payload (Episode.season/Episode.episode), and `title` stays the
        # episode's own bare title.
        assert cached[100].title == "Test Episode"
        assert cached[100].season_number == 1
        assert cached[100].episode_number == 1
        assert cached[101].title == "Test Episode 2"
        assert cached[101].season_number == 1
        assert cached[101].episode_number == 2

        # Neither episode's sonarrSeriesId (789) matches the one series in
        # this page (sonarrSeriesId=1), so both fall back to "Unknown
        # series" - series_ext_id still comes from the episode itself
        # (a required field), and sort_key is still populated.
        assert cached[100].series_title == "Unknown series"
        assert cached[100].series_ext_id == 789
        assert cached[100].sort_key == library_sort_key("Unknown series")
        assert cached[101].series_title == "Unknown series"
        assert cached[101].series_ext_id == 789

    @pytest.mark.asyncio
    async def test_poll_all_episodes_survives_realistic_series_payload(
        self, mock_db_session, respx_mock
    ):
        """Drives a REAL BazarrClient through respx so the actual
        `/api/series` response Bazarr sends is parsed (not just mocked at
        the client-method level), proving the full sync survives it instead
        of silently logging a warning and skipping every series (the schema
        bug this test guards against)."""
        import httpx

        from tests.bazarr_fixtures import realistic_series_item

        respx_mock.get("http://poller-wire-test:6767/api/series").mock(
            return_value=httpx.Response(
                200,
                json={"data": [realistic_series_item(sonarrSeriesId=789)], "total": 1},
            )
        )
        respx_mock.get("http://poller-wire-test:6767/api/episodes").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "sonarrEpisodeId": 100,
                            "sonarrSeriesId": 789,
                            "title": "Test Episode",
                            "subtitles": [],
                            "season": 1,
                            "episode": 1,
                            "path": "/bazarr/tv/Test Series/Season 01/Episode 01.mkv",
                            "sceneName": "/bazarr/tv/Test Series/Season 01/Episode 01.mkv",
                        }
                    ],
                    "total": 1,
                },
            )
        )

        path_map = PathMap([("/bazarr/tv", "/local/tv")])
        started_at = datetime.now(timezone.utc)

        async with BazarrClient(
            base_url="http://poller-wire-test:6767",
            api_key="wire-test-key",
        ) as client:
            series_page = await client.list_all_series()
            await _poll_all_episodes(
                mock_db_session, client, series_page, path_map, started_at
            )

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.kind == "episode")
        )
        cached = result.scalars().all()

        assert len(cached) == 1
        assert cached[0].ext_id == 100
        assert cached[0].has_any_subs is False

    @pytest.mark.asyncio
    async def test_poll_all_episodes_batches_series_into_few_calls(
        self, mock_db_session
    ):
        """More series than CACHE_COMMIT_BATCH_SIZE are split across multiple
        batched calls, not fetched one series at a time - the fix for the
        N+1-per-series regression that starved the refresh watchdog of
        progress events on large libraries."""
        from audio_to_subs.bazarr.schemas import (
            Episode,
            EpisodesPage,
            Series,
            SeriesPage,
        )

        mock_client = AsyncMock(spec=BazarrClient)
        series = [
            Series(sonarrSeriesId=i, title=f"Series {i}", path=f"/tv/s{i}")
            for i in range(1, 6)
        ]
        series_page = SeriesPage(data=series, total=5)

        def list_episodes_side_effect(*, seriesid):
            return EpisodesPage(
                data=[
                    Episode(sonarrEpisodeId=sid * 10, sonarrSeriesId=sid, title="Ep1")
                    for sid in seriesid
                ]
            )

        mock_client.list_episodes.side_effect = list_episodes_side_effect

        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        with patch("audio_to_subs.bazarr.poller.CACHE_COMMIT_BATCH_SIZE", 2):
            processed = await _poll_all_episodes(
                mock_db_session, mock_client, series_page, path_map, started_at
            )

        # 5 series at a batch size of 2 -> 3 calls (2, 2, 1), not 5.
        assert mock_client.list_episodes.await_count == 3
        assert processed == 5

    @pytest.mark.asyncio
    async def test_poll_all_episodes_reports_heartbeat_per_batch(self, mock_db_session):
        """A forced progress report fires at the start of the episodes
        phase and again after every batch, independent of the throttled
        per-item stepping - so a slow/large library still emits
        refresh_progress events regularly instead of going silent for the
        whole phase (the root cause of the Wanted-refresh watchdog firing
        on large libraries)."""
        from audio_to_subs.bazarr.schemas import (
            Episode,
            EpisodesPage,
            Series,
            SeriesPage,
        )

        mock_client = AsyncMock(spec=BazarrClient)
        series = [
            Series(sonarrSeriesId=i, title=f"Series {i}", path=f"/tv/s{i}")
            for i in range(1, 4)
        ]
        series_page = SeriesPage(data=series, total=3)

        def list_episodes_side_effect(*, seriesid):
            return EpisodesPage(
                data=[Episode(sonarrEpisodeId=1, sonarrSeriesId=1, title="Ep")]
            )

        mock_client.list_episodes.side_effect = list_episodes_side_effect

        stages: list[str] = []

        async def callback(processed, total, percent, stage):
            stages.append(stage)

        reporter = ProgressReporter(callback=callback, throttle_seconds=999)
        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        with patch("audio_to_subs.bazarr.poller.CACHE_COMMIT_BATCH_SIZE", 1):
            await _poll_all_episodes(
                mock_db_session,
                mock_client,
                series_page,
                path_map,
                started_at,
                reporter,
            )

        # One force report entering the phase + one per batch (3 series,
        # batch size 1 => 3 batches) = 4, even with an effectively-infinite
        # throttle that would otherwise suppress every non-forced report.
        assert stages.count("syncing episodes") == 4

    @pytest.mark.asyncio
    async def test_poll_all_episodes_commits_once_per_batch(self, mock_db_session):
        """Episodes are committed once per series batch, not once per
        episode - bounds the number of SQLite write transactions per full
        sync instead of issuing one per item."""
        from audio_to_subs.bazarr.schemas import (
            Episode,
            EpisodesPage,
            Series,
            SeriesPage,
        )

        mock_client = AsyncMock(spec=BazarrClient)
        series = [
            Series(sonarrSeriesId=i, title=f"Series {i}", path=f"/tv/s{i}")
            for i in range(1, 6)
        ]
        series_page = SeriesPage(data=series, total=5)

        def list_episodes_side_effect(*, seriesid):
            return EpisodesPage(
                data=[
                    Episode(sonarrEpisodeId=sid * 10, sonarrSeriesId=sid, title="Ep1")
                    for sid in seriesid
                ]
            )

        mock_client.list_episodes.side_effect = list_episodes_side_effect

        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        commit_mock = AsyncMock(wraps=mock_db_session.commit)
        mock_db_session.commit = commit_mock

        with patch("audio_to_subs.bazarr.poller.CACHE_COMMIT_BATCH_SIZE", 2):
            processed = await _poll_all_episodes(
                mock_db_session, mock_client, series_page, path_map, started_at
            )

        # 5 series at a batch size of 2 -> 3 batches (2, 2, 1) -> 3 commits,
        # not 5 (one per episode, since each series here yields one episode).
        assert commit_mock.await_count == 3
        assert processed == 5

        cached = (
            (
                await mock_db_session.execute(
                    select(BazarrCache).where(BazarrCache.kind == "episode")
                )
            )
            .scalars()
            .all()
        )
        assert len(cached) == 5


class TestManualPolling:
    """Test manual polling functionality with type filtering (full sync)."""

    @pytest.mark.asyncio
    async def test_manual_poll_all(self, mock_db_session):
        """Test manual polling without type filter polls both movies and episodes."""
        from audio_to_subs.bazarr.schemas import (
            EpisodesPage,
            MoviesPage,
            Series,
            SeriesPage,
        )

        mock_client = AsyncMock(spec=BazarrClient)

        mock_client.list_all_movies.return_value = MoviesPage(
            data=[Movie(title="Test Movie", radarrId=123, sceneName="/m.mkv")],
            total=1,
        )
        mock_client.list_all_series.return_value = SeriesPage(
            data=[
                Series(sonarrSeriesId=1, title="Test Series", path="/tv/Test Series")
            ],
            total=1,
        )
        mock_client.list_episodes.return_value = EpisodesPage(
            data=[Episode(sonarrEpisodeId=456, sonarrSeriesId=1, title="Pilot")]
        )

        path_map = PathMap()

        movies_processed, episodes_processed = await poll_bazarr_manually(
            mock_db_session, mock_client, path_map, None
        )

        # Full sync: each endpoint fetched exactly once per type.
        mock_client.list_all_movies.assert_awaited_once()
        mock_client.list_all_series.assert_awaited_once()
        mock_client.list_episodes.assert_awaited_once()

        assert movies_processed == 1
        assert episodes_processed == 1

        await mock_client.close()

    @pytest.mark.asyncio
    async def test_manual_poll_all_uses_combined_movies_and_episodes_total(
        self, mock_db_session
    ):
        """poll_bazarr_manually sets ONE denominator (movies total + summed
        per-series episodeFileCount) before either phase starts, so the
        percentage stays coherent across the movies->episodes boundary
        instead of losing its denominator once movies finish (the root
        cause of the Wanted-refresh progress bar reverting to a bare "N
        items" counter for the episodes phase - the majority of a full
        sync's duration)."""
        from audio_to_subs.bazarr.schemas import (
            EpisodesPage,
            MoviesPage,
            Series,
            SeriesPage,
        )

        mock_client = AsyncMock(spec=BazarrClient)
        mock_client.list_all_movies.return_value = MoviesPage(
            data=[
                Movie(title="Movie 1", radarrId=1),
                Movie(title="Movie 2", radarrId=2),
            ],
            total=2,
        )
        mock_client.list_all_series.return_value = SeriesPage(
            data=[
                Series(
                    sonarrSeriesId=1,
                    title="Series 1",
                    path="/tv/s1",
                    episodeFileCount=3,
                )
            ],
            total=1,
        )
        mock_client.list_episodes.return_value = EpisodesPage(
            data=[
                Episode(sonarrEpisodeId=10, sonarrSeriesId=1, title="Ep1"),
                Episode(sonarrEpisodeId=11, sonarrSeriesId=1, title="Ep2"),
                Episode(sonarrEpisodeId=12, sonarrSeriesId=1, title="Ep3"),
            ]
        )

        totals_seen: list[int | None] = []

        async def callback(processed, total, percent, stage):
            totals_seen.append(total)

        reporter = ProgressReporter(callback=callback, throttle_seconds=0)
        path_map = PathMap()

        movies_processed, episodes_processed = await poll_bazarr_manually(
            mock_db_session, mock_client, path_map, None, reporter=reporter
        )

        assert movies_processed == 2
        assert episodes_processed == 3

        # Combined total: 2 movies + 3 episodes (summed episodeFileCount) = 5.
        assert reporter.total == 5
        # Every reported total across the whole run (once set) is the same
        # combined value - it never reverts to None partway through, unlike
        # the old movies-only total that vanished once the episodes phase
        # started.
        non_none_totals = [t for t in totals_seen if t is not None]
        assert non_none_totals
        assert all(t == 5 for t in non_none_totals)

    @pytest.mark.asyncio
    async def test_manual_poll_movies_only(self, mock_db_session):
        """Test manual polling for movies only skips episode API calls."""
        from audio_to_subs.api.routes.wanted import WantedItemType
        from audio_to_subs.bazarr.schemas import MoviesPage

        mock_client = AsyncMock(spec=BazarrClient)

        mock_client.list_all_movies.return_value = MoviesPage(
            data=[Movie(title="Test Movie", radarrId=123, sceneName="/m.mkv")],
            total=1,
        )

        path_map = PathMap()

        movies_processed, episodes_processed = await poll_bazarr_manually(
            mock_db_session, mock_client, path_map, WantedItemType.MOVIE
        )

        mock_client.list_all_movies.assert_awaited_once()
        mock_client.list_all_series.assert_not_awaited()
        mock_client.list_episodes.assert_not_awaited()

        assert movies_processed == 1
        assert episodes_processed == 0

        await mock_client.close()

    @pytest.mark.asyncio
    async def test_manual_poll_episodes_only(self, mock_db_session):
        """Test manual polling for episodes only skips movie API calls."""
        from audio_to_subs.api.routes.wanted import WantedItemType
        from audio_to_subs.bazarr.schemas import EpisodesPage, Series, SeriesPage

        mock_client = AsyncMock(spec=BazarrClient)

        mock_client.list_all_series.return_value = SeriesPage(
            data=[
                Series(sonarrSeriesId=1, title="Test Series", path="/tv/Test Series")
            ],
            total=1,
        )
        mock_client.list_episodes.return_value = EpisodesPage(
            data=[Episode(sonarrEpisodeId=456, sonarrSeriesId=1, title="Pilot")]
        )

        path_map = PathMap()

        movies_processed, episodes_processed = await poll_bazarr_manually(
            mock_db_session, mock_client, path_map, WantedItemType.EPISODE
        )

        mock_client.list_all_series.assert_awaited_once()
        mock_client.list_episodes.assert_awaited_once()
        mock_client.list_all_movies.assert_not_awaited()

        assert movies_processed == 0
        assert episodes_processed == 1

        await mock_client.close()

    @pytest.mark.asyncio
    async def test_manual_poll_ingests_fully_subtitled_and_missing_together(
        self, mock_db_session
    ):
        """The headline M8 behaviour: one poll ingests BOTH a fully-subtitled
        item and a missing-subtitle item - not just Bazarr's "wanted"
        (missing-subtitle) subset, which is all the pre-M8 poller cached."""
        from audio_to_subs.bazarr.schemas import (
            Episode,
            EpisodesPage,
            Movie,
            MoviesPage,
            Series,
            SeriesPage,
            SubtitleLanguage,
        )

        mock_client = AsyncMock(spec=BazarrClient)

        fully_subbed_movie = Movie(
            title="Fully Subbed Movie",
            radarrId=1,
            sceneName="/movies/fully-subbed.mkv",
            subtitles=[SubtitleLanguage(name="English", code2="en")],
            missing_subtitles=[],
        )
        missing_sub_movie = Movie(
            title="Missing Sub Movie",
            radarrId=2,
            sceneName="/movies/missing-sub.mkv",
            subtitles=[],
            missing_subtitles=[SubtitleLanguage(name="English", code2="en")],
        )
        mock_client.list_all_movies.return_value = MoviesPage(
            data=[fully_subbed_movie, missing_sub_movie], total=2
        )

        series = Series(sonarrSeriesId=1, title="Test Series", path="/tv/Test Series")
        mock_client.list_all_series.return_value = SeriesPage(data=[series], total=1)

        fully_subbed_episode = Episode(
            sonarrEpisodeId=100,
            sonarrSeriesId=1,
            title="Fully Subbed Episode",
            subtitles=[SubtitleLanguage(name="English", code2="en")],
            missing_subtitles=[],
        )
        missing_sub_episode = Episode(
            sonarrEpisodeId=101,
            sonarrSeriesId=1,
            title="Missing Sub Episode",
            subtitles=[],
            missing_subtitles=[SubtitleLanguage(name="English", code2="en")],
        )
        mock_client.list_episodes.return_value = EpisodesPage(
            data=[fully_subbed_episode, missing_sub_episode]
        )

        path_map = PathMap()
        movies_processed, episodes_processed = await poll_bazarr_manually(
            mock_db_session, mock_client, path_map, None
        )

        assert movies_processed == 2
        assert episodes_processed == 2

        cached = {
            c.id: c
            for c in (await mock_db_session.execute(select(BazarrCache))).scalars()
        }

        assert cached["movie:1"].has_any_subs is True
        assert cached["movie:1"].missing_subtitles == []
        assert cached["movie:2"].has_any_subs is False
        assert cached["movie:2"].missing_subtitles[0]["code2"] == "en"

        assert cached["episode:100"].has_any_subs is True
        assert cached["episode:100"].missing_subtitles == []
        assert cached["episode:101"].has_any_subs is False
        assert cached["episode:101"].missing_subtitles[0]["code2"] == "en"

    @pytest.mark.asyncio
    async def test_manual_poll_handles_client_error(
        self, mock_db_session, sync_session
    ):
        """Test manual polling propagates Bazarr client errors instead of masking them as success."""
        from sqlalchemy import select

        from audio_to_subs.bazarr.client import BazarrServerError
        from audio_to_subs.db.models import JobLog, LogLevel

        # Create mock client that raises error
        mock_client = AsyncMock(spec=BazarrClient)
        mock_client.list_all_movies.side_effect = BazarrServerError("Server error")

        path_map = PathMap()

        # A failure fetching movies must propagate, not be swallowed and
        # reported as a successful poll with 0 items processed.
        with pytest.raises(BazarrServerError):
            await poll_bazarr_manually(mock_db_session, mock_client, path_map, None)

        await mock_client.close()

        # The failure must also be visible in the UI's activity log, not just
        # propagated as an exception.
        log = sync_session.execute(select(JobLog)).scalar_one()
        assert log.job_id is None
        assert log.level == LogLevel.ERROR
        assert "Bazarr sync failed" in log.message

    @pytest.mark.asyncio
    async def test_manual_poll_tolerates_one_bad_series(self, mock_db_session):
        """A failure fetching one batch of series' episodes is logged and
        skipped, not fatal to the whole episode sync - other batches still
        land. Forces a batch size of 1 so each series gets its own batch/call,
        preserving per-series fault isolation for this test."""
        from audio_to_subs.bazarr.client import BazarrServerError
        from audio_to_subs.bazarr.schemas import (
            Episode,
            EpisodesPage,
            Series,
            SeriesPage,
        )

        mock_client = AsyncMock(spec=BazarrClient)
        good_series = Series(sonarrSeriesId=1, title="Good Series", path="/tv/good")
        bad_series = Series(sonarrSeriesId=2, title="Bad Series", path="/tv/bad")
        mock_client.list_all_series.return_value = SeriesPage(
            data=[bad_series, good_series], total=2
        )

        async def list_episodes_side_effect(*, seriesid):
            if seriesid == [2]:
                raise BazarrServerError("boom")
            return EpisodesPage(
                data=[Episode(sonarrEpisodeId=10, sonarrSeriesId=1, title="Ep1")]
            )

        mock_client.list_episodes.side_effect = list_episodes_side_effect

        path_map = PathMap()
        with patch("audio_to_subs.bazarr.poller.CACHE_COMMIT_BATCH_SIZE", 1):
            _, episodes_processed = await poll_bazarr_manually(
                mock_db_session, mock_client, path_map, "episode"
            )

        assert episodes_processed == 1
        entry = (
            await mock_db_session.execute(
                select(BazarrCache).where(BazarrCache.id == "episode:10")
            )
        ).scalar_one()
        assert entry.title == "Ep1"
        assert entry.series_title == "Good Series"

        await mock_client.close()

    @pytest.mark.asyncio
    async def test_manual_poll_returns_counts(self, mock_db_session, sync_session):
        """Test manual polling returns accurate counts."""
        from sqlalchemy import select

        from audio_to_subs.bazarr.schemas import (
            Episode,
            EpisodesPage,
            Movie,
            MoviesPage,
            Series,
            SeriesPage,
        )
        from audio_to_subs.db.models import JobLog, LogLevel

        mock_client = AsyncMock(spec=BazarrClient)

        movies = [
            Movie(title=f"Movie {i}", radarrId=i, sceneName=f"/m{i}.mkv")
            for i in range(1, 4)
        ]
        episodes = [
            Episode(sonarrEpisodeId=100 + i, sonarrSeriesId=1, title=f"Ep{i}")
            for i in range(1, 3)
        ]

        mock_client.list_all_movies.return_value = MoviesPage(data=movies, total=3)
        mock_client.list_all_series.return_value = SeriesPage(
            data=[Series(sonarrSeriesId=1, title="Series 1", path="/tv/series1")],
            total=1,
        )
        mock_client.list_episodes.return_value = EpisodesPage(data=episodes)

        path_map = PathMap()

        movies_processed, episodes_processed = await poll_bazarr_manually(
            mock_db_session, mock_client, path_map, None
        )

        assert movies_processed == 3
        assert episodes_processed == 2

        # A successful sync must also be visible in the UI's activity log,
        # with the counts in the message.
        log = sync_session.execute(select(JobLog)).scalar_one()
        assert log.job_id is None
        assert log.level == LogLevel.INFO
        assert "3 movies" in log.message
        assert "2 episodes" in log.message


class TestFullSyncSerialization:
    """poll_bazarr_manually serializes concurrent full syncs (periodic
    poller vs. manual refresh) via a lock. Two concurrent full syncs each
    issue thousands of short read/write transactions against the same
    SQLite file and can exceed busy_timeout, crashing with 'database is
    locked' - this is a regression test for that production incident."""

    @pytest.mark.asyncio
    async def test_concurrent_calls_never_overlap(self, mock_db_session):
        from audio_to_subs.bazarr.schemas import MoviesPage, SeriesPage

        concurrent_count = 0
        max_concurrent = 0

        async def list_all_movies_side_effect(*args, **kwargs):
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.05)
            concurrent_count -= 1
            return MoviesPage(data=[], total=0)

        mock_client = AsyncMock(spec=BazarrClient)
        mock_client.list_all_movies.side_effect = list_all_movies_side_effect
        mock_client.list_all_series.return_value = SeriesPage(data=[], total=0)

        path_map = PathMap()

        await asyncio.gather(
            poll_bazarr_manually(mock_db_session, mock_client, path_map, "movie"),
            poll_bazarr_manually(mock_db_session, mock_client, path_map, "movie"),
        )

        # The two calls' list_all_movies invocations must never be in
        # flight at the same time - proves the lock actually serializes
        # them rather than letting both proceed concurrently.
        assert max_concurrent == 1
        assert mock_client.list_all_movies.await_count == 2


class TestFullSyncBounding:
    """Full sync must remain bounded: one call for movies (not one per
    movie), one call per distinct series (not one per episode)."""

    @pytest.mark.asyncio
    async def test_movies_fetched_in_a_single_call(self, mock_db_session):
        from audio_to_subs.bazarr.schemas import Movie, MoviesPage, SubtitleLanguage

        mock_client = AsyncMock(spec=BazarrClient)
        mock_client.list_all_movies.return_value = MoviesPage(
            data=[
                Movie(
                    title="Movie A",
                    radarrId=1,
                    audio_language=[
                        SubtitleLanguage(name="French", code2="fr", code3="fre")
                    ],
                ),
                Movie(
                    title="Movie B",
                    radarrId=2,
                    audio_language=[
                        SubtitleLanguage(name="German", code2="de", code3="ger")
                    ],
                ),
            ],
            total=2,
        )

        path_map = PathMap()
        await poll_bazarr_manually(mock_db_session, mock_client, path_map, "movie")

        # Bounded: exactly one call for this poll, not one per movie.
        mock_client.list_all_movies.assert_awaited_once_with()

        entry_a = (
            await mock_db_session.execute(
                select(BazarrCache).where(BazarrCache.id == "movie:1")
            )
        ).scalar_one()
        entry_b = (
            await mock_db_session.execute(
                select(BazarrCache).where(BazarrCache.id == "movie:2")
            )
        ).scalar_one()
        assert entry_a.audio_language[0]["code2"] == "fr"
        assert entry_b.audio_language[0]["code2"] == "de"

    @pytest.mark.asyncio
    async def test_episodes_fetched_in_one_batched_call_per_batch(
        self, mock_db_session
    ):
        """Distinct series are batched into `seriesid[]` lists (up to
        CACHE_COMMIT_BATCH_SIZE per call), not fetched one series - or one
        episode - at a time."""
        from audio_to_subs.bazarr.schemas import (
            Episode,
            EpisodesPage,
            Series,
            SeriesPage,
            SubtitleLanguage,
        )

        mock_client = AsyncMock(spec=BazarrClient)
        # Two distinct series, three episodes total across them.
        mock_client.list_all_series.return_value = SeriesPage(
            data=[
                Series(sonarrSeriesId=10, title="Series 1", path="/tv/series1"),
                Series(sonarrSeriesId=20, title="Series 2", path="/tv/series2"),
            ],
            total=2,
        )

        def list_episodes_side_effect(*, seriesid):
            assert set(seriesid) == {10, 20}
            return EpisodesPage(
                data=[
                    Episode(
                        sonarrEpisodeId=101,
                        sonarrSeriesId=10,
                        title="Ep1",
                        audio_language=[SubtitleLanguage(name="English", code2="en")],
                    ),
                    Episode(
                        sonarrEpisodeId=102,
                        sonarrSeriesId=10,
                        title="Ep2",
                        audio_language=[SubtitleLanguage(name="English", code2="en")],
                    ),
                    Episode(
                        sonarrEpisodeId=201,
                        sonarrSeriesId=20,
                        title="Ep1",
                        audio_language=[SubtitleLanguage(name="Spanish", code2="es")],
                    ),
                ]
            )

        mock_client.list_episodes.side_effect = list_episodes_side_effect

        path_map = PathMap()
        await poll_bazarr_manually(mock_db_session, mock_client, path_map, "episode")

        # Bounded by the number of batches (1, both series fit in the
        # default CACHE_COMMIT_BATCH_SIZE), not by series or episode count.
        assert mock_client.list_episodes.await_count == 1

        entry_101 = (
            await mock_db_session.execute(
                select(BazarrCache).where(BazarrCache.id == "episode:101")
            )
        ).scalar_one()
        entry_201 = (
            await mock_db_session.execute(
                select(BazarrCache).where(BazarrCache.id == "episode:201")
            )
        ).scalar_one()
        assert entry_101.title == "Ep1"
        assert entry_101.series_title == "Series 1"
        assert entry_101.audio_language[0]["code2"] == "en"
        assert entry_201.title == "Ep1"
        assert entry_201.series_title == "Series 2"
        assert entry_201.audio_language[0]["code2"] == "es"


class TestFullLibrarySyncRealisticWireFormat:
    """Drives a REAL BazarrClient through respx against Bazarr's actual wire
    format (see tests/bazarr_fixtures.py), proving the headline M8 behaviour:
    a single poll ingests a fully-subtitled item AND a missing-subtitle item
    together - the pre-M8 poller only ever cached the latter (Bazarr's
    "wanted" subset)."""

    @pytest.mark.asyncio
    async def test_full_poll_caches_fully_subtitled_and_missing_movie(
        self, mock_db_session, respx_mock
    ):
        import httpx

        from tests.bazarr_fixtures import realistic_movie_item

        fully_subbed = realistic_movie_item(
            radarrId=1,
            title="Fully Subbed Movie",
            subtitles=[
                {
                    "name": "English",
                    "code2": "en",
                    "code3": "eng",
                    "forced": False,
                    "hi": False,
                }
            ],
            missing_subtitles=[],
        )
        missing_sub = realistic_movie_item(
            radarrId=2,
            title="Missing Sub Movie",
            subtitles=[],
            missing_subtitles=[
                {
                    "name": "English",
                    "code2": "en",
                    "code3": "eng",
                    "forced": False,
                    "hi": False,
                }
            ],
        )
        respx_mock.get("http://full-sync-test:6767/api/movies").mock(
            return_value=httpx.Response(
                200, json={"data": [fully_subbed, missing_sub], "total": 2}
            )
        )

        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        async with BazarrClient(
            base_url="http://full-sync-test:6767",
            api_key="wire-test-key",
        ) as client:
            movies_page = await client.list_all_movies()
            processed = await _poll_all_movies(
                mock_db_session, movies_page, path_map, started_at
            )

        assert processed == 2

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.kind == "movie")
        )
        cached = {c.ext_id: c for c in result.scalars().all()}

        assert len(cached) == 2
        # The fully-subtitled item is cached (the pre-M8 poller would have
        # dropped it entirely - only "wanted" i.e. missing-subtitle items
        # were ever ingested).
        assert cached[1].has_any_subs is True
        assert cached[1].missing_subtitles == []
        # The missing-subtitle item is cached too, side by side.
        assert cached[2].has_any_subs is False
        assert cached[2].missing_subtitles[0]["code2"] == "en"

    @pytest.mark.asyncio
    async def test_full_poll_caches_fully_subtitled_and_missing_episode(
        self, mock_db_session, respx_mock
    ):
        import httpx

        from tests.bazarr_fixtures import realistic_series_item

        respx_mock.get("http://full-sync-test:6767/api/series").mock(
            return_value=httpx.Response(
                200,
                json={"data": [realistic_series_item(sonarrSeriesId=789)], "total": 1},
            )
        )
        respx_mock.get("http://full-sync-test:6767/api/episodes").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "sonarrEpisodeId": 100,
                            "sonarrSeriesId": 789,
                            "title": "Fully Subbed Episode",
                            "subtitles": [
                                {
                                    "name": "English",
                                    "code2": "en",
                                    "code3": "eng",
                                    "forced": False,
                                    "hi": False,
                                }
                            ],
                            "missing_subtitles": [],
                            "season": 1,
                            "episode": 1,
                            "path": "/bazarr/tv/Test Series/Episode 01.mkv",
                            "sceneName": None,
                        },
                        {
                            "sonarrEpisodeId": 101,
                            "sonarrSeriesId": 789,
                            "title": "Missing Sub Episode",
                            "subtitles": [],
                            "missing_subtitles": [
                                {
                                    "name": "English",
                                    "code2": "en",
                                    "code3": "eng",
                                    "forced": False,
                                    "hi": False,
                                }
                            ],
                            "season": 1,
                            "episode": 2,
                            "path": "/bazarr/tv/Test Series/Episode 02.mkv",
                            "sceneName": None,
                        },
                    ],
                    "total": 2,
                },
            )
        )

        path_map = PathMap()
        started_at = datetime.now(timezone.utc)

        async with BazarrClient(
            base_url="http://full-sync-test:6767",
            api_key="wire-test-key",
        ) as client:
            series_page = await client.list_all_series()
            processed = await _poll_all_episodes(
                mock_db_session, client, series_page, path_map, started_at
            )

        assert processed == 2

        result = await mock_db_session.execute(
            select(BazarrCache).where(BazarrCache.kind == "episode")
        )
        cached = {c.ext_id: c for c in result.scalars().all()}

        assert len(cached) == 2
        assert cached[100].has_any_subs is True
        assert cached[100].missing_subtitles == []
        assert cached[101].has_any_subs is False
        assert cached[101].missing_subtitles[0]["code2"] == "en"

        # This series' sonarrSeriesId (789) DOES match both episodes'
        # sonarrSeriesId, so this - unlike the mismatched-ID mock fixture in
        # TestPollAllEpisodes - exercises the resolved-series path through a
        # real HTTP response: `title` is the episode's own bare title,
        # `series_title`/`series_ext_id`/`sort_key` come from the resolved
        # Series, and season/episode numbers round-trip from the wire JSON.
        assert cached[100].title == "Fully Subbed Episode"
        assert cached[100].series_title == "Test Series"
        assert cached[100].series_ext_id == 789
        assert cached[100].season_number == 1
        assert cached[100].episode_number == 1
        assert cached[100].sort_key == library_sort_key("Test Series")
        assert cached[101].title == "Missing Sub Episode"
        assert cached[101].series_title == "Test Series"
        assert cached[101].series_ext_id == 789
        assert cached[101].season_number == 1
        assert cached[101].episode_number == 2
        assert cached[101].sort_key == library_sort_key("Test Series")
