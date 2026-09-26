"""Bazarr poller for caching the full library's subtitle state.

Periodically polls the entire Bazarr library (movies + episodes) and
updates the local cache with each item's real has_any_subs/missing_subtitles
state, so the Wanted API can serve any display scope (all/missing/no_subs)
without re-polling.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, or_, select

from audio_to_subs.bazarr.client import BazarrClient
from audio_to_subs.bazarr.pathmap import PathMap
from audio_to_subs.core.library_sort import library_sort_key
from audio_to_subs.db.job_logs import write_job_log
from audio_to_subs.db.models import BazarrCache, LogLevel

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

    from audio_to_subs.api.settings import Settings
    from audio_to_subs.bazarr.schemas import (
        Episode,
        Movie,
        MoviesPage,
        Series,
        SeriesPage,
    )

logger = logging.getLogger(__name__)


class ProgressReporter:
    """Tracks refresh progress and emits throttled updates to a callback.

    The full-library sync's total is known up front for BOTH movies and
    episodes: Bazarr's ``/api/movies`` response carries an exact ``total``,
    and each series in ``/api/series`` carries ``episodeFileCount`` (a real
    per-series episode count) which can be summed across the listing without
    a separate ``/api/episodes`` call. ``poll_bazarr_manually`` computes this
    combined total and calls ``set_known_total`` once before either phase
    starts, so a single coherent denominator covers the whole sync - the bar
    advances continuously from movies into episodes instead of losing its
    denominator partway through.

    Updates are throttled (~1 Hz) so a fast poll loop doesn't flood the
    downstream pub/sub channel.
    """

    def __init__(
        self,
        callback: (
            Callable[[int, int | None, int, str], Coroutine[Any, Any, None]] | None
        ) = None,
        *,
        throttle_seconds: float = 1.0,
    ) -> None:
        self._callback = callback
        self._throttle = throttle_seconds
        self._processed = 0
        # None means "no denominator known yet" (e.g. before the up-front
        # movies+episodes fetch completes). Deliberately NOT 0 - 0 is a
        # legitimate total (an empty library) and must be distinguishable
        # from "not set yet" via `is not None`, not truthiness.
        self._known_total: int | None = None
        self._stage = "starting"
        self._last_emit = 0.0

    def set_known_total(self, total: int) -> None:
        """Set the denominator known up front (movies total + summed
        per-series episode counts - see class docstring)."""
        self._known_total = total

    def set_stage(self, stage: str) -> None:
        self._stage = stage

    def increment(self, by: int = 1) -> None:
        self._processed += by

    async def step(self, by: int = 1) -> None:
        """Increment processed count and emit a (throttled) progress update.

        Safe to call unconditionally; no-ops when no callback is configured.
        """
        self.increment(by)
        await self.report()

    @property
    def processed(self) -> int:
        return self._processed

    @property
    def total(self) -> int | None:
        # Report the known denominator once set, for as long as processed
        # hasn't drifted past it (e.g. Bazarr's episodeFileCount undercounting
        # relative to what /api/episodes actually returns) - in that drift
        # case, fall back to a live counter rather than a total that's
        # already been exceeded.
        if self._known_total is not None and self._processed <= self._known_total:
            return self._known_total
        return None

    @property
    def percent(self) -> int:
        total = self.total
        if not total:
            return 0
        return min(100, round(self._processed / total * 100))

    async def report(self, *, force: bool = False) -> None:
        if self._callback is None:
            return
        now = time.monotonic()
        if not force and (now - self._last_emit) < self._throttle:
            return
        self._last_emit = now
        await self._callback(self._processed, self.total, self.percent, self._stage)


class PollerState:
    """State for the Bazarr poller."""

    def __init__(self) -> None:
        self.shutdown = asyncio.Event()
        self.poll_task: asyncio.Task[Any] | None = None


async def get_bazarr_client(
    bazarr_url: str | None = None,
    bazarr_api_key: str | None = None,
    bazarr_timeout: float = 30.0,
) -> BazarrClient | None:
    """Get Bazarr client if configured.

    Args:
        bazarr_url: Bazarr API URL from settings
        bazarr_api_key: Bazarr API key from settings
        bazarr_timeout: Bazarr API timeout in seconds

    Returns:
        Configured BazarrClient or None if not configured
    """
    if not bazarr_url or not bazarr_api_key:
        logger.debug("Bazarr not configured (missing URL or API key)")
        return None

    return BazarrClient(
        base_url=bazarr_url,
        api_key=bazarr_api_key,
        timeout=bazarr_timeout,
    )


async def get_bazarr_client_with_settings(
    db: "AsyncSession",
    env_settings: "Settings | None" = None,
) -> tuple[BazarrClient | None, str | None, str | None, float]:
    """Get Bazarr client with settings from database first, then environment fallback.

    This function prioritizes database settings over environment variables. A
    missing DB row or a DB value of None (including the row seeded by
    `_seed_default_settings`, before a user has ever configured Bazarr) falls
    back to the environment; an explicit empty string in the DB is treated as
    the user deliberately disabling Bazarr and is not overridden.

    Args:
        db: Async database session
        env_settings: Optional environment settings (for testing)

    Returns:
        Tuple of (BazarrClient or None, bazarr_url, bazarr_api_key, bazarr_timeout)
        Returns None for client if not configured
    """
    import json

    from audio_to_subs.db.models import Setting

    keys = ("bazarr_url", "bazarr_api_key", "bazarr_timeout")
    db_values: dict[str, Any] = {}
    try:
        result = await db.execute(
            select(Setting.key, Setting.value_json).where(Setting.key.in_(keys))
        )
        for key, value_json in result.all():
            if value_json:
                db_values[key] = json.loads(value_json)
    except Exception as e:
        logger.warning("Failed to load Bazarr settings: %s", e)

    bazarr_url = db_values.get("bazarr_url")
    bazarr_api_key = db_values.get("bazarr_api_key")
    bazarr_timeout = db_values.get("bazarr_timeout")

    # Fall back to environment settings if database settings are not set
    if env_settings is None:
        from audio_to_subs.api.settings import get_settings

        env_settings = get_settings()

    # A DB value of None (row absent, or seeded default) falls back to env.
    # An explicit "" is a deliberate "disable Bazarr" signal and is kept as-is.
    if bazarr_url is None:
        bazarr_url = env_settings.BAZARR_URL
    if bazarr_api_key is None:
        # Use the property which handles file-based loading from BAZARR_API_KEY_FILE
        bazarr_api_key = env_settings.bazarr_api_key
    if bazarr_timeout is None:
        bazarr_timeout = env_settings.BAZARR_TIMEOUT

    client = await get_bazarr_client(bazarr_url, bazarr_api_key, bazarr_timeout)
    return client, bazarr_url, bazarr_api_key, bazarr_timeout


async def get_path_map(
    db: "AsyncSession",
) -> PathMap:
    """Get PathMap from settings.

    Args:
        db: Async database session

    Returns:
        PathMap configured from settings or empty PathMap
    """
    return await PathMap.load_from_db(db)


async def get_settings_value(
    db: "AsyncSession",
    key: str,
    default: Any = None,
) -> Any:
    """Get a setting value from the database.

    Args:
        db: Async database session
        key: Setting key
        default: Default value if not found

    Returns:
        Setting value or default
    """
    from audio_to_subs.db.models import Setting

    try:
        result = await db.execute(select(Setting.value_json).where(Setting.key == key))
        # scalar_one_or_none() returns the raw value_json string, not a Setting.
        value_json = result.scalar_one_or_none()
        if value_json:
            import json

            return json.loads(value_json)
    except Exception as e:
        logger.warning("Failed to load setting %s: %s", key, e)

    return default


async def poll_once(
    db: "AsyncSession",
    client: BazarrClient,
    path_map: PathMap,
) -> int:
    """Perform a single scheduled full-library poll of all Bazarr items.

    Fetches every movie and episode from Bazarr (not just Bazarr's "wanted"
    subset), translates paths, and updates the bazarr_cache table.

    Args:
        db: Async database session
        client: BazarrClient instance
        path_map: PathMap for path translation

    Returns:
        Number of items processed
    """
    movies_processed, episodes_processed = await poll_bazarr_manually(
        db, client, path_map, "all"
    )
    return movies_processed + episodes_processed


# Serializes full-library Bazarr syncs: the periodic poller (poll_once) and
# on-demand manual refreshes (the Wanted page's Refresh button) both call
# poll_bazarr_manually. Without this, two concurrent full syncs each issue
# thousands of short read/write transactions against the same SQLite file
# and can exceed busy_timeout, raising "database is locked" - which aborts
# the refresh and can collaterally starve the worker's own job-claiming
# query on the same file. At most one full sync now runs at a time; a
# second caller waits for the lock instead of racing the DB.
_full_sync_lock = asyncio.Lock()


async def _fetch_pages_and_set_total(
    client: BazarrClient,
    poll_movies: bool,
    poll_episodes: bool,
    reporter: ProgressReporter,
) -> tuple["MoviesPage | None", "SeriesPage | None"]:
    """Fetch the movies/series listings needed by this sync and seed the
    reporter with their combined total, before either phase starts.

    Each listing is already required by the phase that follows it, so
    fetching both up front (rather than letting `_poll_all_movies`/
    `_poll_all_episodes` fetch their own) costs no extra HTTP calls, and
    gives a single, accurate denominator for the WHOLE sync: movies.total is
    exact, and the episodes total is the sum of each series'
    episodeFileCount (a real per-series COUNT Bazarr already computes). This
    keeps one coherent percentage across the movies->episodes boundary
    instead of losing the denominator partway through.

    Returns:
        (movies_page, series_page) - either may be None if that type wasn't
        requested (`poll_movies`/`poll_episodes` False).
    """
    movies_page: MoviesPage | None = None
    series_page: SeriesPage | None = None
    if poll_movies:
        movies_page = await client.list_all_movies()
    if poll_episodes:
        series_page = await client.list_all_series()

    combined_total = 0
    if movies_page is not None:
        combined_total += movies_page.total
    if series_page is not None:
        combined_total += sum(series.episodeFileCount for series in series_page.data)
    reporter.set_known_total(combined_total)

    return movies_page, series_page


async def poll_bazarr_manually(
    db: "AsyncSession",
    client: BazarrClient,
    path_map: PathMap,
    item_type: str | None = None,
    reporter: ProgressReporter | None = None,
) -> tuple[int, int]:
    """Perform a full-library poll of Bazarr items, optionally scoped by type.

    Used both for the scheduled background poll (via poll_once, item_type="all")
    and for on-demand manual refreshes triggered from the API. Ingests every
    movie/episode within the active item_type - not just Bazarr's "wanted"
    (missing-subtitle) subset - persisting each item's real
    has_any_subs/missing_subtitles so the Wanted API can serve the
    all/missing/no_subs display scopes without a further poll. Returns
    separate counts for movies and episodes processed.

    Runs strictly sequentially: `db` is a single SQLAlchemy AsyncSession, which
    does not support concurrent use from multiple coroutines (interleaved
    `execute()`/`commit()` calls raise `IllegalStateChangeError`), so movies and
    episodes cannot be processed via asyncio.gather() against the same session.

    Args:
        db: Async database session
        client: BazarrClient instance
        path_map: PathMap for path translation
        item_type: Optional filter - one of "all", "movie", "episode".
            If None or "all", polls both movies and episodes.
            If "movie", polls only movies.
            If "episode", polls only episodes.
        reporter: Optional progress reporter. A no-op reporter is used when
            None so callers don't need to guard every progress call.

    Returns:
        Tuple of (movies_processed, episodes_processed) counts
    """
    if reporter is None:
        reporter = ProgressReporter()

    if _full_sync_lock.locked():
        logger.info(
            "Waiting for an in-progress Bazarr sync to finish before starting "
            "(item_type=%s)",
            item_type,
        )
        reporter.set_stage("waiting for another sync to finish")
        await reporter.report(force=True)

    async with _full_sync_lock:
        started_at = datetime.now(timezone.utc)
        logger.info(
            "Starting Bazarr poll at %s (item_type=%s)",
            started_at.isoformat(),
            item_type,
        )

        movies_processed = 0
        episodes_processed = 0

        try:
            # Determine which types to poll
            poll_movies = (
                item_type is None or item_type == "all" or item_type == "movie"
            )
            poll_episodes = (
                item_type is None or item_type == "all" or item_type == "episode"
            )

            reporter.set_stage("syncing library")
            await reporter.report(force=True)

            movies_page, series_page = await _fetch_pages_and_set_total(
                client, poll_movies, poll_episodes, reporter
            )

            if movies_page is not None:
                movies_processed = await _poll_all_movies(
                    db, movies_page, path_map, started_at, reporter
                )

            if series_page is not None:
                episodes_processed = await _poll_all_episodes(
                    db, client, series_page, path_map, started_at, reporter
                )

            # Delete stale items (no longer present in Bazarr) - only for
            # types that were actually polled
            deleted_count = await _delete_stale(
                db, started_at, poll_movies, poll_episodes
            )
            if deleted_count > 0:
                logger.info("Deleted %d stale items from cache", deleted_count)

            reporter.set_stage("done")
            await reporter.report(force=True)

            logger.info(
                "Bazarr poll complete: processed %d movies, %d episodes, "
                "deleted %d stale",
                movies_processed,
                episodes_processed,
                deleted_count,
            )
            await write_job_log(
                db,
                LogLevel.INFO,
                f"Bazarr sync completed: {movies_processed} movies, "
                f"{episodes_processed} episodes processed, {deleted_count} stale removed",
            )

        except Exception as e:
            logger.error("Error during Bazarr poll: %s", e, exc_info=True)
            await write_job_log(db, LogLevel.ERROR, f"Bazarr sync failed: {e}")
            reporter.set_stage("error")
            await reporter.report(force=True)
            raise

        return movies_processed, episodes_processed


def _build_language_list(languages: list[Any] | None) -> list[dict[str, Any]]:
    """Build a list of language dictionaries from Bazarr SubtitleLanguage objects.

    Shared by missing_subtitles and audio_language - both use Bazarr's
    {name, code2, code3, forced, hi} shape (audio entries always have
    forced=False, hi=False, matching SubtitleLanguage's defaults).

    Args:
        languages: List of language objects from Bazarr API, or None

    Returns:
        List of dictionaries with language details
    """
    return [
        {
            "name": lang.name,
            "code2": lang.code2,
            "code3": lang.code3,
            "forced": lang.forced,
            "hi": lang.hi,
        }
        for lang in (languages or [])
    ]


async def _upsert_cache_entry(
    db: "AsyncSession",
    cache_id: str,
    kind: str,
    ext_id: int | str,
    title: str,
    media_path: str,
    has_any_subs: bool,
    missing_subtitles: list[Any] | None,
    started_at: datetime,
    audio_language: list[Any] | None = None,
    *,
    series_title: str | None = None,
    series_ext_id: int | None = None,
    season_number: int | None = None,
    episode_number: int | None = None,
    sort_key: str | None = None,
) -> None:
    """Upsert a Bazarr cache entry (select-update/insert pattern).

    Does not commit - the caller commits once per processed chunk (see
    `_poll_all_movies`/`_poll_all_episodes`) so a full sync issues a handful
    of write transactions instead of one per item.

    Args:
        db: Async database session
        cache_id: Unique cache ID
        kind: "movie" or "episode"
        ext_id: External ID (radarrId or sonarrEpisodeId)
        title: The item's own bare title - the movie title, or the
            episode's own title (e.g. "Episode 1"/"Pilot"). Never the
            flattened "<series> - <episode>" string.
        media_path: Path to media file
        has_any_subs: Whether the item has any subtitles
        missing_subtitles: List of missing language objects
        started_at: Poll start time
        audio_language: List of audio language objects, if known
        series_title: Episode-only - the resolved series title (or "Unknown
            series"). None for movies.
        series_ext_id: Episode-only - the Sonarr series ID (episode.sonarrSeriesId).
            None for movies.
        season_number: Episode-only - the season number. None for movies.
        episode_number: Episode-only - the episode number. None for movies.
        sort_key: Article-insensitive, natural-number sort key
            (`library_sort_key()`) for the series (episodes) or movie title.
    """
    result = await db.execute(select(BazarrCache).where(BazarrCache.id == cache_id))
    existing = result.scalar_one_or_none()

    missing_subs_list = _build_language_list(missing_subtitles)
    audio_lang_list = _build_language_list(audio_language)

    if existing:
        # Update existing record
        existing.title = title
        existing.media_path = media_path
        existing.has_any_subs = has_any_subs
        existing.missing_subtitles = missing_subs_list
        existing.audio_language = audio_lang_list
        existing.last_polled = started_at
        existing.series_title = series_title
        existing.series_ext_id = series_ext_id
        existing.season_number = season_number
        existing.episode_number = episode_number
        existing.sort_key = sort_key
    else:
        # Insert new record
        cache_entry = BazarrCache(
            id=cache_id,
            kind=kind,
            ext_id=ext_id,
            title=title,
            media_path=media_path,
            has_any_subs=has_any_subs,
            missing_subtitles=missing_subs_list,
            audio_language=audio_lang_list,
            last_polled=started_at,
            active_job_id=None,
            series_title=series_title,
            series_ext_id=series_ext_id,
            season_number=season_number,
            episode_number=episode_number,
            sort_key=sort_key,
        )
        db.add(cache_entry)


async def _process_movie(
    db: "AsyncSession",
    movie: "Movie",
    path_map: PathMap,
    started_at: datetime,
) -> None:
    """Process a single movie from Bazarr's full `/api/movies` listing.

    Args:
        db: Async database session
        movie: Movie from Bazarr's full movies listing - carries its own
            audio_language, path, subtitles (present files), and
            missing_subtitles, so no separate detail fetch is needed.
        path_map: PathMap for path translation
        started_at: Poll start time
    """
    # Resolve media_path: prefer the authoritative `path` field, fall back to
    # sceneName (often null).
    media_path = movie.path or movie.sceneName or ""
    if media_path:
        media_path = path_map.translate(media_path)

    # Whether this movie actually has any subtitle file present, from the
    # `subtitles` list of files actually on disk (distinct from
    # `missing_subtitles`, which only reports what's absent). A movie can be
    # missing one language while already having a subtitle for another.
    has_any_subs = len(movie.subtitles) > 0

    cache_id = BazarrCache.make_id("movie", movie.radarrId)

    await _upsert_cache_entry(
        db,
        cache_id,
        "movie",
        movie.radarrId,
        movie.title,
        media_path,
        has_any_subs,
        movie.missing_subtitles,
        started_at,
        movie.audio_language,
        sort_key=library_sort_key(movie.title),
    )


async def _process_episode(
    db: "AsyncSession",
    episode: "Episode",
    path_map: PathMap,
    started_at: datetime,
    series: "Series | None",
) -> None:
    """Process a single episode from Bazarr's full `/api/episodes` listing.

    Args:
        db: Async database session
        episode: Episode from Bazarr's full episodes listing - carries its
            own audio_language, path, subtitles (present files), and
            missing_subtitles, so no separate detail fetch is needed.
        path_map: PathMap for path translation
        started_at: Poll start time
        series: The series this episode belongs to, resolved by the caller
            from the series listing (or None if it couldn't be resolved -
            e.g. an episode whose sonarrSeriesId doesn't match any series in
            the page). `episode.title` is stored as-is (the episode's own
            bare title, never flattened with the series title); the series'
            title is stored separately in `series_title` ("Unknown series"
            when `series` is None) so the Wanted page can group/display
            season and episode numbers per series.
    """
    # Resolve media_path: prefer the authoritative `path` field, fall back to
    # sceneName (often null).
    media_path = episode.path or episode.sceneName or ""
    if media_path:
        media_path = path_map.translate(media_path)

    # Whether this episode actually has any subtitle file present, from the
    # `subtitles` list of files actually on disk (distinct from
    # `missing_subtitles`, which only reports what's absent). An episode can
    # be missing one language while already having a subtitle for another.
    has_any_subs = len(episode.subtitles) > 0

    cache_id = BazarrCache.make_id("episode", episode.sonarrEpisodeId)

    series_title = series.title if series is not None else "Unknown series"

    await _upsert_cache_entry(
        db,
        cache_id,
        "episode",
        episode.sonarrEpisodeId,
        episode.title,
        media_path,
        has_any_subs,
        episode.missing_subtitles,
        started_at,
        episode.audio_language,
        series_title=series_title,
        # sonarrSeriesId is a required field on every Episode, so this is
        # always known even when the series lookup itself failed.
        series_ext_id=episode.sonarrSeriesId,
        season_number=episode.season,
        episode_number=episode.episode,
        sort_key=library_sort_key(series_title),
    )


# Batch size for both the episodes HTTP fetch (`/api/episodes?seriesid[]=...`,
# one call per batch of series) and the cache-commit granularity for both
# movies and episodes (one `db.commit()` per processed chunk instead of per
# item). Bazarr's episodes endpoint has no pagination and answers any-sized
# `seriesid[]` list with one `IN (...)` query, and 100 keeps that query
# string comfortably under typical proxy/header size limits while cutting a
# few-hundred-series library down to a handful of requests. Reusing the same
# size for commit chunking bounds each write transaction to ~100 items
# instead of the 1 item/commit before it - trading finer per-item failure
# isolation for far fewer SQLite write transactions per full sync (a sync
# with ~3,250 items previously issued ~3,250 transactions; this cuts it to a
# few dozen).
CACHE_COMMIT_BATCH_SIZE = 100


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    """Split `items` into consecutive chunks of at most `size` elements."""
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _poll_all_movies(
    db: "AsyncSession",
    movies_page: "MoviesPage",
    path_map: PathMap,
    started_at: datetime,
    reporter: ProgressReporter | None = None,
) -> int:
    """Upsert every movie in an already-fetched Bazarr movies listing.

    Ingests ALL movies regardless of subtitle state - both fully-subtitled
    and missing/no-subs items - so the cache carries enough state to serve
    every Wanted display scope (all/missing/no_subs) without re-polling.

    Takes the listing pre-fetched by the caller (`poll_bazarr_manually`,
    which needs it up front to compute the combined movies+episodes progress
    total before either phase starts) rather than fetching it itself, so
    there is exactly one `/api/movies` call per sync.

    Committed in `CACHE_COMMIT_BATCH_SIZE`-sized chunks rather than one
    commit per movie, to bound write-transaction count.

    Args:
        db: Async database session
        movies_page: Movies listing already fetched by the caller
        path_map: PathMap for path translation
        started_at: Poll start time
        reporter: Optional progress reporter. The caller has already set the
            known total; this only advances stage/processed.

    Returns:
        Number of movies processed during this pass.
    """
    processed = 0
    if reporter is not None:
        reporter.set_stage("syncing movies")
        await reporter.report(force=True)

    for chunk in _chunked(movies_page.data, CACHE_COMMIT_BATCH_SIZE):
        for movie in chunk:
            await _process_movie(db, movie, path_map, started_at)
            processed += 1
            if reporter is not None:
                await reporter.step()
        await db.commit()

    return processed


async def _poll_all_episodes(
    db: "AsyncSession",
    client: BazarrClient,
    series_page: "SeriesPage",
    path_map: PathMap,
    started_at: datetime,
    reporter: ProgressReporter | None = None,
) -> int:
    """Upsert every episode for an already-fetched Bazarr series listing.

    Ingests ALL episodes regardless of subtitle state, walking series in
    batches since Bazarr has no single "all episodes" endpoint (bounded by
    the number of batches, not one call per series or per episode - see
    `CACHE_COMMIT_BATCH_SIZE`).

    Takes the series listing pre-fetched by the caller (`poll_bazarr_manually`,
    which needs it up front to sum each series' `episodeFileCount` into the
    combined progress total before either phase starts) rather than fetching
    it itself, so there is exactly one `/api/series` call per sync.

    Each batch's episodes are upserted as they're fetched, then committed
    once as a group at the end of the batch (one write transaction per
    batch, not per episode), so no write transaction is ever held open
    across the next batch's HTTP call (short-transaction convention).

    A failure fetching one batch's episodes is tolerated - logged and
    skipped - so one bad batch doesn't abort the sync for every other
    series, mirroring the old per-series detail-fetch tolerance.

    Args:
        db: Async database session
        client: BazarrClient instance
        series_page: Series listing already fetched by the caller
        path_map: PathMap for path translation
        started_at: Poll start time
        reporter: Optional progress reporter. The caller has already set the
            known total (movies total + summed episodeFileCount); this only
            advances stage/processed.

    Returns:
        Number of episodes processed during this pass.
    """
    processed = 0
    series_by_id = {series.sonarrSeriesId: series for series in series_page.data}

    if reporter is not None:
        reporter.set_stage("syncing episodes")
        await reporter.report(force=True)

    for batch in _chunked(series_page.data, CACHE_COMMIT_BATCH_SIZE):
        series_ids = [series.sonarrSeriesId for series in batch]
        try:
            episodes_page = await client.list_episodes(seriesid=series_ids)
        except Exception as e:
            logger.warning(
                "Failed to fetch episodes for series batch %s: %s",
                series_ids,
                e,
            )
            continue

        if reporter is not None:
            # Heartbeat right after the (potentially slow) batch call
            # returns, independent of the throttled per-item stepping below -
            # bounds the max silent gap to one batch instead of one series.
            await reporter.report(force=True)

        for episode in episodes_page.data:
            series = series_by_id.get(episode.sonarrSeriesId)
            await _process_episode(db, episode, path_map, started_at, series)
            processed += 1
            if reporter is not None:
                await reporter.step()

        await db.commit()

    return processed


async def _delete_stale(
    db: "AsyncSession",
    started_at: datetime,
    poll_movies: bool = True,
    poll_episodes: bool = True,
) -> int:
    """Delete items no longer present in Bazarr's library.

    Removes items from cache where last_polled < started_at
    (meaning they were not seen in the current poll cycle).

    Args:
        db: Async database session
        started_at: Poll start time
        poll_movies: Whether movies were polled (delete stale movies if True)
        poll_episodes: Whether episodes were polled (delete stale episodes if True)

    Returns:
        Number of deleted items
    """
    # Only delete items of the types that were actually polled. If neither
    # flag is set, nothing was polled, so delete nothing rather than falling
    # back to an unconditional (both-kinds) delete.
    conditions = []
    if poll_movies:
        conditions.append(BazarrCache.kind == "movie")
    if poll_episodes:
        conditions.append(BazarrCache.kind == "episode")

    if not conditions:
        return 0

    query = delete(BazarrCache).where(
        BazarrCache.last_polled < started_at, or_(*conditions)
    )

    result = await db.execute(query)
    await db.commit()
    return result.rowcount


async def _get_poll_interval(db: "AsyncSession") -> int:
    """Get poll interval from settings.

    Args:
        db: Async database session

    Returns:
        Poll interval in seconds (default: 3600)
    """
    return int(await get_settings_value(db, "bazarr_poll_interval", 3600))


async def run_bazarr_poller(app: "FastAPI") -> None:
    """Run the Bazarr poller as an asyncio task.

    This is designed to be started in the FastAPI lifespan.
    Respects the app's shutdown signal.

    Args:
        app: FastAPI application instance
    """
    from audio_to_subs.api.settings import get_settings

    settings = get_settings()

    # Get DB session factory
    from audio_to_subs.db.session import get_async_session

    interval = 3600  # fallback if the session fails before _get_poll_interval runs

    while not app.state.shutdown.is_set():
        try:
            async with get_async_session(settings.DATABASE_URL) as db:
                interval = await _get_poll_interval(db)

                client, bazarr_url, bazarr_api_key, bazarr_timeout = (
                    await get_bazarr_client_with_settings(db, settings)
                )

                if client is None:
                    logger.debug(
                        "Bazarr not configured, skipping poll (waiting %d seconds)",
                        interval,
                    )
                else:
                    path_map = await get_path_map(db)
                    try:
                        await poll_once(db, client, path_map)
                    except Exception as e:
                        logger.warning("Bazarr poll failed: %s", e)
                    finally:
                        await client.close()

        except Exception as e:
            logger.error("Bazarr poller error: %s", e, exc_info=True)

        # Session is closed before this sleep — no write lock held during wait.
        try:
            await asyncio.wait_for(
                app.state.shutdown.wait(),
                timeout=interval,
            )
        except asyncio.TimeoutError:
            pass  # interval elapsed → next poll
        except asyncio.CancelledError:
            break


async def start_poller(app: "FastAPI") -> None:
    """Start the Bazarr poller task in the FastAPI app state.

    Args:
        app: FastAPI application instance
    """
    if not hasattr(app.state, "poller_task"):
        app.state.poller_task = None

    if app.state.poller_task is None or app.state.poller_task.done():
        app.state.poller_task = asyncio.create_task(run_bazarr_poller(app))
        logger.info("Bazarr poller task started")


async def stop_poller(app: "FastAPI") -> None:
    """Stop the Bazarr poller task.

    Args:
        app: FastAPI application instance
    """
    if hasattr(app.state, "poller_task") and app.state.poller_task:
        app.state.poller_task.cancel()
        try:
            await app.state.poller_task
        except asyncio.CancelledError:
            logger.info("Bazarr poller task cancelled")
        app.state.poller_task = None
