"""Wanted API routes for Bazarr integration."""

import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, nulls_last, select, text

from audio_to_subs.api.deps import SettingsDep, get_db, get_redis
from audio_to_subs.api.routes._helpers import UTCAwareModel
from audio_to_subs.api.services.wanted_search import parse_wanted_search
from audio_to_subs.bazarr.pathmap import PathMap
from audio_to_subs.db.job_logs import write_job_log
from audio_to_subs.db.models import (
    SEASON_ORDER_BUCKET_SQL,
    BazarrCache,
    Job,
    JobStatus,
    LogLevel,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wanted", tags=["wanted"])

# Keep strong references to in-flight background refresh tasks: asyncio only
# holds a weak reference to a task created via create_task, so an unreferenced
# task is eligible for GC mid-run (see asyncio docs on create_task).
_background_refresh_tasks: set[asyncio.Task[Any]] = set()

# Default ordering for GET /api/wanted, applied on every code path that
# builds the base query (see `list_wanted`). Sorting purely by
# `func.lower(BazarrCache.title)` (the pre-migration-0007 behavior) breaks
# down two ways: (1) it's lexicographic, not numeric, so "Episode 10" sorts
# before "Episode 2"; (2) `title` is now the item's own bare title (e.g.
# every season's generic "Episode 1"), so unrelated episodes across
# different series/seasons with the same bare title come back in
# arbitrary order across pages. The tie-break chain below sorts by the
# series/movie's natural display order first (`sort_key`, precomputed by
# `library_sort_key()`), then groups a series' own rows together
# (`kind`, `series_ext_id`), then orders within a series by season
# (regular seasons before specials/season 0 before unknown-season rows),
# then by episode number, and finally by `id` as a deterministic
# tie-breaker so pagination never reshuffles rows between requests.
#
# `sort_key` is NULL for a row not yet touched by a sync since migration
# 0007 (or, in principle, if `library_sort_key()` were ever given an empty
# name). SQLite sorts NULL before every non-NULL value in ascending order,
# so a plain `ORDER BY sort_key` would put those rows at the very top of
# page 1 - ahead of "Avatar" - which is the opposite of the "unknowns last"
# treatment already applied to `season_number` below. `nulls_last()` fixes
# that; confirmed with EXPLAIN QUERY PLAN that it doesn't cost the
# index-satisfied ordering below (NULLs are already contiguous at one end
# of the index, so SQLite just scans from the other end - no extra sort
# step either way).
#
# The season-bucket term is built from `text(SEASON_ORDER_BUCKET_SQL)` -
# the exact same literal SQL string the `ix_bazarr_cache_sort` index (see
# db/models.py) is built from - rather than a SQLAlchemy `case()`
# construct. `case()` compiles its THEN/ELSE values as bound `?`
# parameters by default, which SQLite's planner never recognizes as
# matching an expression index (confirmed with EXPLAIN QUERY PLAN, see
# tests/test_api_wanted.py): it falls back to a full table scan plus a
# temp-B-tree sort of every row. The literal `text()` form lets SQLite
# satisfy this entire ORDER BY straight from the index instead.
_WANTED_ORDER = (
    nulls_last(BazarrCache.sort_key),
    BazarrCache.kind,
    BazarrCache.series_ext_id,
    text(SEASON_ORDER_BUCKET_SQL),
    BazarrCache.season_number,
    BazarrCache.episode_number,
    BazarrCache.id,
)


class WantedItemType(str, Enum):
    ALL = "all"
    MOVIE = "movie"
    EPISODE = "episode"


class WantedScope(str, Enum):
    """Display-only filter over the already-synced full library.

    Unlike WantedItemType (which also scopes what a refresh re-syncs), this
    never narrows what gets polled - the poller always ingests the entire
    library within the active item_type. It only filters what's shown from
    the cache.
    """

    ALL = "all"
    MISSING = "missing"
    NO_SUBS = "no_subs"


class WantedItem(UTCAwareModel):
    """Wanted item with job status."""

    model_config = {"from_attributes": True}

    id: str = Field(description="Cache ID (e.g., 'movie:123' or 'episode:456')")
    kind: str = Field(description="Item kind: 'movie' or 'episode'")
    ext_id: int = Field(description="External ID (Radarr or Sonarr ID)")
    title: str = Field(description="Item title")
    series_title: str | None = Field(
        default=None, description="Series name (episodes only; null for movies)"
    )
    season_number: int | None = Field(
        default=None, description="Season number (episodes only; null for movies)"
    )
    episode_number: int | None = Field(
        default=None, description="Episode number (episodes only; null for movies)"
    )
    media_path: str = Field(description="Translated media file path")
    has_any_subs: bool = Field(description="Whether item has any subtitles")
    missing_subtitles: list[dict[str, Any]] = Field(
        default_factory=list, description="List of missing subtitles"
    )
    audio_language: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Audio languages Bazarr reports for this item",
    )
    last_polled: datetime = Field(description="When this item was last polled")
    active_job_id: str | None = Field(
        default=None, description="ID of active job for this item"
    )
    active_job_status: str | None = Field(
        default=None, description="Status of active job if present"
    )
    active_job_progress: int | None = Field(
        default=None, description="Progress percent of active job"
    )


class WantedListResponse(UTCAwareModel):
    """Response for wanted items list."""

    items: list[WantedItem] = Field(description="List of wanted items")
    total: int = Field(description="Total count of items")
    last_refreshed_at: datetime | None = Field(
        default=None, description="When the cache was last refreshed"
    )


async def _get_path_map(db: "AsyncSession") -> PathMap:
    """Get PathMap from database settings."""
    return await PathMap.load_from_db(db)


async def _translate_paths(
    db: "AsyncSession",
    items: list[BazarrCache],
) -> list[BazarrCache]:
    """Translate media paths using path mappings.

    Args:
        db: Database session
        items: List of BazarrCache items

    Returns:
        List of items with translated paths
    """
    path_map = await _get_path_map(db)

    for item in items:
        if item.media_path:
            item.media_path = path_map.translate(item.media_path)

    return items


async def _get_last_refreshed(db: "AsyncSession") -> datetime | None:
    """Get the most recent poll time from cache.

    Args:
        db: Database session

    Returns:
        Most recent last_polled timestamp or None
    """
    result = await db.execute(select(func.max(BazarrCache.last_polled)))
    max_polled = result.scalar_one_or_none()
    return max_polled if max_polled else None


def _to_wanted_item(
    item: BazarrCache,
    active_job_status: str | None,
    active_job_progress: int | None,
) -> WantedItem:
    """Build a WantedItem response from a BazarrCache row plus job status.

    Args:
        item: BazarrCache row (with path already translated)
        active_job_status: Status of the item's active job, if any
        active_job_progress: Progress percent of the item's active job, if any

    Returns:
        Assembled WantedItem
    """
    return WantedItem(
        id=item.id,
        kind=item.kind,
        ext_id=item.ext_id,
        title=item.title,
        series_title=item.series_title,
        season_number=item.season_number,
        episode_number=item.episode_number,
        media_path=item.media_path,
        has_any_subs=item.has_any_subs,
        missing_subtitles=item.missing_subtitles,
        audio_language=item.audio_language,
        last_polled=item.last_polled,
        active_job_id=str(item.active_job_id) if item.active_job_id else None,
        active_job_status=active_job_status,
        active_job_progress=active_job_progress,
    )


@router.get("", response_model=WantedListResponse)
async def list_wanted(  # noqa: C901
    db: Annotated["AsyncSession", Depends(get_db)],
    item_type: WantedItemType = Query(  # noqa: B008
        default=WantedItemType.ALL, description="Filter by item type"
    ),
    language: str | None = Query(
        default=None, description="Filter by missing language code"
    ),
    search: str | None = Query(
        default=None, description="Case-insensitive title search"
    ),
    scope: WantedScope = Query(  # noqa: B008
        default=WantedScope.ALL,
        description=(
            "Display filter over the synced library: all (no filter), "
            "missing (>=1 missing subtitle language), no_subs (no subtitle "
            "at all). Never re-triggers or narrows a sync."
        ),
    ),
    has_job: bool | None = Query(
        default=None, description="Filter by whether item has an active job"
    ),
    page: int = Query(default=1, ge=1, description="Page number"),  # noqa: B008
    page_size: int = Query(
        default=100, ge=1, le=1000, description="Items per page"
    ),  # noqa: B008
) -> WantedListResponse:
    """List items from the Bazarr cache (the full synced library).

    Returns filtered, paginated list of cached items. Never hits Bazarr
    directly - reads from local cache only.

    Query parameters:
    - item_type: Filter by type (all, movie, episode)
    - language: Filter by specific missing language code
    - search: Case-insensitive title search
    - scope: Display filter (all, missing, no_subs) over the full synced
      library - does not affect what a refresh re-syncs
    - has_job: Filter by whether item has an active job (true/false)
    - page: Page number (1-based)
    - page_size: Items per page
    """
    # Build base query. Ordered via `_WANTED_ORDER` (see its definition) so
    # the default sort is a true global order across pages, not just within
    # the page returned - the frontend's client-side sort only re-orders the
    # single page it receives, so pagination must already hand back pages in
    # the right order for cross-page results to look sorted.
    query = select(BazarrCache).order_by(*_WANTED_ORDER)

    # Apply type filter
    if item_type != WantedItemType.ALL:
        query = query.where(BazarrCache.kind == item_type.value)

    # Apply search filter. `search` may embed an episode code (e.g. "S04E01",
    # "4x01") alongside free text (e.g. "agency s04") - parse_wanted_search
    # splits those apart. Escape LIKE wildcards in the remaining free text so
    # a literal "%" or "_" in a title search matches literally instead of
    # acting as a pattern wildcard.
    if search:
        parsed_search = parse_wanted_search(search)
        if parsed_search.text:
            escaped_search = (
                parsed_search.text.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            query = query.where(
                BazarrCache.title.ilike(f"%{escaped_search}%", escape="\\")
                | BazarrCache.series_title.ilike(f"%{escaped_search}%", escape="\\")
            )
        if parsed_search.season is not None:
            # A season/episode code only makes sense for an episode, so this
            # combines with `item_type=movie` (an AND, not a fallback) to
            # produce a guaranteed-empty result rather than silently
            # dropping back to a plain-text search - the caller explicitly
            # asked for movies only *and* typed something that only matches
            # episodes, so an empty result is the honest answer.
            query = query.where(BazarrCache.kind == "episode")
            query = query.where(BazarrCache.season_number == parsed_search.season)
            if parsed_search.episode is not None:
                query = query.where(BazarrCache.episode_number == parsed_search.episode)

    # scope=no_subs can be expressed in SQL directly. scope=missing needs
    # Python-side filtering (see below) since SQLite has no json_contains
    # over missing_subtitles to test "list is non-empty" in SQL.
    if scope == WantedScope.NO_SUBS:
        query = query.where(BazarrCache.has_any_subs.is_(False))

    # Note: language filter and scope=missing are applied in Python below
    # (SQLite has no json_contains).

    # Apply has_job filter
    if has_job is not None:
        if has_job:
            query = query.where(BazarrCache.active_job_id.is_not(None))
        else:
            query = query.where(BazarrCache.active_job_id.is_(None))

    offset = (page - 1) * page_size

    if language or scope == WantedScope.MISSING:
        # The language filter and scope=missing can't be expressed in SQL
        # (SQLite has no json_contains over missing_subtitles), so they must
        # run in Python. That means `total` and the page slice have to be
        # computed from the *filtered* set here too - computing `total` from
        # a SQL count and then filtering only the already-paginated page
        # (the previous approach) desyncs "Page X of Y" from what's actually
        # returned and can drop matching items on later pages.
        all_items = list(await db.scalars(query))
        matched_items: list[BazarrCache] = []
        for item in all_items:
            if scope == WantedScope.MISSING and not item.missing_subtitles:
                continue
            if language:
                if not any(
                    isinstance(sub, dict) and sub.get("code2") == language
                    for sub in item.missing_subtitles
                ):
                    continue
            matched_items.append(item)
        total = len(matched_items)
        items = matched_items[offset : offset + page_size]
    else:
        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        result = await db.execute(count_query)
        total = result.scalar_one()

        # Get paginated items
        query = query.limit(page_size).offset(offset)
        # Use ``db.scalars`` (not ``db.execute(query)`` + ``result.scalars().all()``):
        # the latter made mypy unify the reused ``result`` variable's generic across
        # the earlier int-typed count query, forcing a ``# type: ignore[arg-type]`` on
        # the ``list[...]`` annotation (M5.7). ``db.scalars`` types the row type
        # directly from the ``select(BazarrCache)`` so no ignore is needed.
        items = list(await db.scalars(query))

    # Translate paths
    items = await _translate_paths(db, items)

    # Batch-load active job status/progress for all items in one query
    # (avoids one SELECT per item).
    active_job_ids = [item.active_job_id for item in items if item.active_job_id]
    jobs_by_id: dict[str, tuple[str, int | None]] = {}
    if active_job_ids:
        job_result = await db.execute(
            select(Job.id, Job.status, Job.progress_percent)
            .where(Job.id.in_(active_job_ids))
            .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        )
        jobs_by_id = {
            job_id: (job_status, progress_percent)
            for job_id, job_status, progress_percent in job_result.all()
        }

    # Get active job status for each item
    wanted_items: list[WantedItem] = []
    for item in items:
        active_job_status = None
        active_job_progress = None

        if item.active_job_id:
            job_info = jobs_by_id.get(item.active_job_id)
            if job_info:
                active_job_status, active_job_progress = job_info

        wanted_items.append(
            _to_wanted_item(item, active_job_status, active_job_progress)
        )

    # Get last refreshed time
    last_refreshed = await _get_last_refreshed(db)

    return WantedListResponse(
        items=wanted_items,
        total=total,
        last_refreshed_at=last_refreshed,
    )


@router.get("/{item_id}", response_model=WantedItem)
async def get_wanted_item(
    item_id: str,
    db: Annotated["AsyncSession", Depends(get_db)],
) -> WantedItem:
    """Get a specific wanted item by ID.

    Args:
        item_id: Cache ID (e.g., 'movie:123' or 'episode:456')

    Returns:
        Wanted item details
    """
    result = await db.execute(select(BazarrCache).where(BazarrCache.id == item_id))
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Wanted item {item_id} not found",
        )

    # Translate path
    item = (await _translate_paths(db, [item]))[0]

    # Get active job status
    active_job_status = None
    active_job_progress = None

    if item.active_job_id:
        job_result = await db.execute(
            select(Job.status, Job.progress_percent)
            .where(Job.id == item.active_job_id)
            .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        )
        job = job_result.one_or_none()
        if job:
            # job.status is stored as string in DB (Mapped[JobStatus] over String(20))
            active_job_status = job.status
            active_job_progress = job.progress_percent

    return _to_wanted_item(item, active_job_status, active_job_progress)


# Refresh endpoint models
class WantedRefreshRequest(BaseModel):
    """Request model for wanted list refresh."""

    item_type: WantedItemType = Field(
        default=WantedItemType.ALL,
        description="Filter by item type: all, movie, or episode",
    )
    # Optional client-supplied id. When the client opens its SSE subscription
    # *before* POSTing (so it doesn't miss the refresh_done event on a fast
    # refresh), it generates the id itself and passes it here. When omitted,
    # the server generates one (back-compat for any caller that doesn't care
    # about progress streaming).
    refresh_id: UUID | None = Field(
        default=None,
        description="Client-supplied refresh id (UUID). If omitted, server generates one.",
    )


class WantedRefreshResponse(BaseModel):
    """Response model for wanted list refresh."""

    status: str = Field(description="Refresh status: started, completed, failed")
    refresh_id: str = Field(description="Unique id for this refresh run")
    movies_processed: int = Field(default=0, description="Number of movies processed")
    episodes_processed: int = Field(
        default=0, description="Number of episodes processed"
    )
    error: str | None = Field(default=None, description="Error message if failed")


class WantedRefreshStatusResponse(BaseModel):
    """Persisted snapshot of an in-flight or completed refresh.

    Returned by GET /api/wanted/refresh/{refresh_id} so clients that missed
    the terminal SSE event can recover (see useWantedRefresh's watchdog).
    """

    refresh_id: str = Field(description="Unique id for this refresh run")
    status: str = Field(
        description="Refresh status: started (in progress), completed, or failed"
    )
    processed: int = Field(default=0, description="Items processed so far")
    total: int | None = Field(
        default=None, description="Known total when stage has a denominator"
    )
    percent: int = Field(default=0, description="Progress percentage (0-100)")
    stage: str = Field(default="", description="Human-readable current stage")
    movies_processed: int = Field(default=0, description="Movies processed")
    episodes_processed: int = Field(default=0, description="Episodes processed")
    error: str | None = Field(default=None, description="Error message if failed")
    updated_at: datetime = Field(description="When this snapshot was written")


@router.post(
    "/refresh",
    response_model=WantedRefreshResponse,
    status_code=status.HTTP_200_OK,
)
async def refresh_wanted_list(
    db: Annotated["AsyncSession", Depends(get_db)],
    settings: SettingsDep,
    refresh_request: WantedRefreshRequest = Body(  # noqa: B008
        default_factory=WantedRefreshRequest
    ),
) -> WantedRefreshResponse:
    """Trigger a manual refresh of the wanted list from Bazarr.

    The poll runs in the background; this endpoint returns immediately with
    ``status="started"`` and a ``refresh_id``. Progress is streamed to clients
    over the global SSE channel (``/api/jobs/stream``) as ``refresh_progress``
    events, and a final ``refresh_done`` event marks completion.

    The refresh:
    - Uses database settings first, falling back to environment variables
    - Respects the item_type filter (all, movies, episodes) - within that
      scope, ingests the ENTIRE library (not just Bazarr's "wanted" subset)
    - Updates the local cache with fresh data from Bazarr
    - Streams progress (processed / total) as it runs

    Args:
        item_type: Filter scope - "all" polls both movies and episodes,
                   "movie" polls only movies, "episode" polls only episodes

    Returns:
        Refresh handle with status "started" and a refresh_id
    """
    import uuid

    from audio_to_subs.api.deps import get_redis_client
    from audio_to_subs.bazarr.poller import (
        ProgressReporter,
        get_bazarr_client_with_settings,
        get_path_map,
        poll_bazarr_manually,
    )
    from audio_to_subs.db.session import get_async_session
    from audio_to_subs.queue_.events import (
        publish_refresh_done,
        publish_refresh_progress,
        set_refresh_state,
    )

    # Validate Bazarr is configured before spawning the background task, so we
    # can fail fast with a clear error rather than an orphaned task.
    client_probe = None
    try:
        client_probe, _, _, _ = await get_bazarr_client_with_settings(db, settings)
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to initialize Bazarr client: %s", type(e).__name__)
        await write_job_log(
            db,
            LogLevel.ERROR,
            "Bazarr sync failed: could not initialize Bazarr client",
        )
        return WantedRefreshResponse(
            status="failed",
            refresh_id="",
            movies_processed=0,
            episodes_processed=0,
            error="Bazarr client initialization failed",
        )
    finally:
        if client_probe is not None:
            await client_probe.close()

    if client_probe is None:
        return WantedRefreshResponse(
            status="failed",
            refresh_id="",
            movies_processed=0,
            episodes_processed=0,
            error="Bazarr not configured",
        )

    # Prefer the client-supplied refresh_id (the client opens its SSE
    # subscription *before* POSTing so it can't miss refresh_done on a fast
    # refresh). Fall back to a server-generated one for callers that don't
    # care about progress streaming.
    refresh_id = (
        str(refresh_request.refresh_id)
        if refresh_request.refresh_id is not None
        else str(uuid.uuid4())
    )
    item_type = refresh_request.item_type.value

    # Persist the initial "started" state before scheduling the task, so a
    # client that opens its EventSource slightly late can still recover via
    # GET /api/wanted/refresh/{refresh_id} even if the bg task finishes
    # before it subscribes.
    redis_for_state = get_redis_client()
    try:
        await set_refresh_state(
            redis_for_state,
            refresh_id,
            {
                "refresh_id": refresh_id,
                "status": "started",
                "processed": 0,
                "total": None,
                "percent": 0,
                "stage": "starting",
                "movies_processed": 0,
                "episodes_processed": 0,
                "error": None,
            },
        )
    finally:
        await redis_for_state.aclose()

    async def _run_refresh() -> None:
        redis = get_redis_client()
        try:
            async with get_async_session(settings.DATABASE_URL) as bg_db:
                client = None
                try:
                    client, _, _, _ = await get_bazarr_client_with_settings(
                        bg_db, settings
                    )
                    if client is None:
                        await publish_refresh_done(
                            redis, refresh_id, "failed", error="Bazarr not configured"
                        )
                        return

                    path_map = await get_path_map(bg_db)

                    reporter = ProgressReporter(
                        callback=lambda processed, total, percent, stage: publish_refresh_progress(
                            redis, refresh_id, processed, total, percent, stage
                        )
                    )

                    movies_processed, episodes_processed = await poll_bazarr_manually(
                        bg_db, client, path_map, item_type, reporter=reporter
                    )

                    await publish_refresh_done(
                        redis,
                        refresh_id,
                        "completed",
                        movies_processed=movies_processed,
                        episodes_processed=episodes_processed,
                    )
                except Exception as e:  # noqa: BLE001
                    # Covers failures anywhere in setup (client/path-map init)
                    # or polling, not just the poll call - otherwise a setup
                    # failure here skips publish_refresh_done entirely and the
                    # frontend's progress bar waits for an event that will
                    # never arrive.
                    logger.error("Background refresh failed: %s", e, exc_info=True)
                    await publish_refresh_done(
                        redis, refresh_id, "failed", error="Refresh failed"
                    )
                finally:
                    if client is not None:
                        await client.close()
        finally:
            await redis.aclose()

    task = asyncio.create_task(_run_refresh())
    _background_refresh_tasks.add(task)
    task.add_done_callback(_background_refresh_tasks.discard)

    return WantedRefreshResponse(
        status="started",
        refresh_id=refresh_id,
        movies_processed=0,
        episodes_processed=0,
        error=None,
    )


@router.get(
    "/refresh/{refresh_id}",
    response_model=WantedRefreshStatusResponse,
)
async def get_refresh_status(
    refresh_id: UUID,
    redis: Annotated["Redis", Depends(get_redis)],
) -> WantedRefreshStatusResponse:
    """Recover the latest state of a refresh run by id.

    Used by clients that missed the terminal ``refresh_done`` SSE event -
    typically because the EventSource was opened after the refresh finished,
    or the connection dropped mid-stream. The state is persisted in Redis
    with a 10-minute TTL keyed by ``refresh_id``.

    Returns 404 when the id is unknown or has expired, so the client can
    surface a generic "status unknown" error to the user.
    """
    from audio_to_subs.queue_.events import get_refresh_state

    state = await get_refresh_state(redis, str(refresh_id))
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Refresh {refresh_id} not found or expired",
        )

    # The persisted dict carries updated_at as an ISO string; Pydantic will
    # parse it back into datetime via the UTCAwareModel-aware field.
    return WantedRefreshStatusResponse(
        refresh_id=state.get("refresh_id", str(refresh_id)),
        status=state.get("status", "started"),
        processed=state.get("processed", 0),
        total=state.get("total"),
        percent=state.get("percent", 0),
        stage=state.get("stage", ""),
        movies_processed=state.get("movies_processed", 0),
        episodes_processed=state.get("episodes_processed", 0),
        error=state.get("error"),
        updated_at=state.get("updated_at", datetime.fromtimestamp(0).isoformat()),
    )
