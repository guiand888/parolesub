"""Job service layer for job creation and management."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from audio_to_subs.bazarr.pathmap import PathMap
from audio_to_subs.core.ids import generate_job_id
from audio_to_subs.core.path_utils import generate_output_path, validate_media_path
from audio_to_subs.db.job_logs import write_job_log
from audio_to_subs.db.models import (
    BazarrCache,
    Job,
    JobSource,
    JobStatus,
    LogLevel,
    OutputFormat,
    Setting,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from audio_to_subs.api.settings import Settings

logger = logging.getLogger(__name__)


async def get_path_map(db: "AsyncSession") -> PathMap:
    """Get PathMap from database settings.

    Args:
        db: Async database session

    Returns:
        PathMap configured from database
    """
    return await PathMap.load_from_db(db)


@dataclass(frozen=True)
class ResolvedSource:
    """Everything resolved from a job's `source`/`source_ref` at creation.

    `media_path`/`source_ref` feed the job's execution as before. The four
    label fields are a one-time snapshot of the matching `BazarrCache` row
    (see migration 0008's docstring for why a snapshot rather than a live
    join), copied onto the `Job` row unchanged and never refreshed.

    All four are `None` for a manual source. They are NEVER None for a
    freshly-created Bazarr source: `resolve_bazarr_source` below raises a
    404 (see its docstring) before this dataclass is even constructed if
    the cache row is missing, so a `Job` with a Bazarr source is either
    created with real label data or not created at all. A Bazarr job can
    still end up with all four `None` later in its life - not from this
    code path, but from migration 0008's one-time historical backfill,
    which runs against *existing* jobs and leaves one NULL if its cache
    row is gone *by the time the migration runs* (e.g. the item has since
    left Bazarr) - see that migration's docstring.
    """

    media_path: str
    source_ref: str | None
    title: str | None
    series_title: str | None
    season_number: int | None
    episode_number: int | None


async def resolve_bazarr_source(
    db: "AsyncSession",
    source: JobSource,
    source_ref: str | None,
    requested_media_path: str | None,
    path_map: PathMap,
) -> ResolvedSource:
    """Resolve a job's source to a media path plus a label snapshot.

    Args:
        db: Database session
        source: Job source
        source_ref: Reference ID (e.g., Radarr or Sonarr ID)
        requested_media_path: Optional requested media path (for manual override)
        path_map: PathMap for path translation

    Returns:
        A `ResolvedSource` with the media path and the label fields to copy
        onto the new `Job`.

    Raises:
        HTTPException: If source is bazarr but source_ref not found in cache
    """
    if source in (JobSource.BAZARR_MOVIE, JobSource.BAZARR_EPISODE):
        if source_ref is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"source_ref is required for source={source.value}",
            )

        # Look up in bazarr_cache
        cache_id = BazarrCache.make_id(
            "movie" if source == JobSource.BAZARR_MOVIE else "episode",
            int(source_ref),
        )

        result = await db.execute(select(BazarrCache).where(BazarrCache.id == cache_id))
        cache_entry = result.scalar_one_or_none()

        if cache_entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Bazarr item {cache_id} not found in cache. "
                "Please ensure Bazarr poller has run and the item exists.",
            )

        # Use the cached media_path (already translated by poller)
        media_path = cache_entry.media_path

        # If requested_media_path is provided, use it (allows override). The
        # label snapshot below is unaffected - a path override doesn't
        # change what item this job is for.
        if requested_media_path:
            media_path = path_map.translate(requested_media_path)
            logger.info(
                "Using requested media_path override for %s: %s",
                cache_id,
                media_path,
            )

        return ResolvedSource(
            media_path=media_path,
            source_ref=source_ref,
            title=cache_entry.title,
            series_title=cache_entry.series_title,
            season_number=cache_entry.season_number,
            episode_number=cache_entry.episode_number,
        )

    else:
        # Manual source - use provided media_path, no label snapshot
        if requested_media_path is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="media_path is required for manual source",
            )
        return ResolvedSource(
            media_path=requested_media_path,
            source_ref=source_ref,
            title=None,
            series_title=None,
            season_number=None,
            episode_number=None,
        )


async def get_default_language_code(db: "AsyncSession") -> str | None:
    """Get default language code from settings.

    Args:
        db: Database session

    Returns:
        Default language code or None
    """

    try:
        result = await db.execute(
            select(Setting.value_json).where(Setting.key == "default_language")
        )
        value_json = result.scalar_one_or_none()
        if value_json:
            return str(json.loads(value_json))
    except Exception:
        pass

    return None


async def get_default_output_format(db: "AsyncSession") -> OutputFormat | None:
    """Get default output format from settings.

    Args:
        db: Database session

    Returns:
        Default output format or None
    """

    try:
        result = await db.execute(
            select(Setting.value_json).where(Setting.key == "default_output_format")
        )
        value_json = result.scalar_one_or_none()
        if value_json:
            default_format = json.loads(value_json)
            if default_format and default_format != "srt":
                try:
                    return OutputFormat(default_format)
                except ValueError:
                    pass  # Invalid format, keep default
    except Exception:
        pass

    return None


async def _apply_language_and_format_defaults(
    db: "AsyncSession",
    language_code: str | None,
    output_format: OutputFormat,
    language_mode: str,
) -> tuple[str | None, OutputFormat]:
    """Resolve the final language code and output format from settings defaults.

    Auto mode must never pick up the default-language setting - the real
    language isn't known until the worker finishes transcribing.
    """
    final_language_code = language_code
    if final_language_code is None and language_mode != "auto":
        final_language_code = await get_default_language_code(db)

    final_output_format = output_format
    if final_output_format == OutputFormat.SRT:
        default_format = await get_default_output_format(db)
        if default_format:
            final_output_format = default_format

    return final_language_code, final_output_format


async def _preflight_subtitle_exists(
    final_language_code: str | None,
    final_output_path: str | None,
    overwrite: bool,
) -> None:
    """M6.g fast-path pre-flight (UX nicety, layered on the authoritative
    write-time guard in the worker): for an explicit-language job we already
    know the deterministic output path, so check for an existing subtitle
    before enqueuing and surface a 409 subtitle_exists (with the existing
    file's path + mtime) instead of waiting for the job to run and fail.
    Auto-detect jobs can't be checked here (the language/final path are only
    resolved after transcription), so they rely on the worker guard.
    """
    if (
        not overwrite
        and final_language_code is not None
        and final_output_path
        and Path(final_output_path).exists()
    ):
        existing_mtime = datetime.fromtimestamp(
            Path(final_output_path).stat().st_mtime, tz=timezone.utc
        ).isoformat()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "subtitle_exists",
                "message": (
                    f"A {final_language_code} subtitle already exists at "
                    f"{final_output_path}"
                ),
                "existing_path": final_output_path,
                "existing_mtime": existing_mtime,
                "language_code": final_language_code,
            },
        )


async def create_job_service(
    db: "AsyncSession",
    settings: "Settings",
    source: JobSource,
    source_ref: str | None,
    media_path: str | None,
    output_path: str | None,
    language_code: str | None,
    output_format: OutputFormat,
    priority: int,
    language_mode: str = "explicit",
    overwrite: bool = False,
) -> Job:
    """Create a new transcription job.

    Creates a job in 'queued' state. The caller is responsible for publishing
    the job notification to Redis.

    Args:
        db: Async database session
        settings: Settings object
        source: Job source
        source_ref: Reference to external source (e.g., Bazarr ID)
        media_path: Path to the media file to process
        output_path: Path where output subtitles should be written
        language_code: Language code for transcription
        output_format: Output subtitle format
        priority: Job priority
        language_mode: "auto" or "explicit". When "auto", language_code is
            ignored (forced to None) - the real language isn't known until
            the worker finishes transcribing, and auto mode must never pick
            up the default-language setting.
        overwrite: When True, allow the worker to replace an existing output
            subtitle (M6.g overwrite guard). Default False.

    Returns:
        Created Job object

    Raises:
        HTTPException: If validation fails or source resolution fails
        HTTPException(409): If an active (queued/running) job already exists
            for the same (media_path, language_code, output_format), or if a
            subtitle already exists at the resolved output path for an explicit
            language job (subtitle_exists).
    """
    if language_mode == "auto":
        language_code = None

    # Get path map for path translation
    path_map = await get_path_map(db)

    # Resolve media_path (and, for a Bazarr source, a label snapshot) based
    # on source
    try:
        resolved = await resolve_bazarr_source(
            db,
            source,
            source_ref,
            media_path,
            path_map,
        )
        resolved_media_path = resolved.media_path
        resolved_source_ref = resolved.source_ref
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to resolve Bazarr source: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to resolve source. Please check your source configuration.",
        ) from e

    # An empty media_path can't be transcribed. For bazarr sources this
    # happens when the item is cached without a usable file path (e.g.
    # Bazarr's wanted endpoint reports no sceneName and the full-detail
    # path is also missing). Surface a clear, actionable error rather than
    # the generic root-directory validation message below.
    if not resolved_media_path or not resolved_media_path.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No media file path is available for this item from Bazarr. "
                "Refresh the wanted list and try again; if the path is still "
                "missing, the media file may not exist on disk."
            ),
        )

    # Validate media_path is structurally safe (validate_media_path rejects
    # path traversal, control characters, and relative paths). Location is not
    # constrained to a fixed root: Radarr/Sonarr allow multiple root folders
    # per item, and Bazarr locates files via configured path mappings.
    is_valid, error_msg = validate_media_path(resolved_media_path)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid media path: {error_msg}",
        )

    # Apply defaults from settings if not provided.
    final_language_code, final_output_format = (
        await _apply_language_and_format_defaults(
            db, language_code, output_format, language_mode
        )
    )

    # Auto-generate output_path if not provided and subtitles_same_directory is
    # enabled. Uses final_language_code (post-defaulting), not the raw
    # parameter, so a job relying on the default-language setting gets a path
    # with the right language suffix instead of none.
    subtitles_same_dir = getattr(settings, "SUBTITLES_SAME_DIRECTORY", True)
    final_output_path = output_path
    if not final_output_path and subtitles_same_dir:
        final_output_path = generate_output_path(
            resolved_media_path,
            final_language_code,
            final_output_format.value,
            subtitles_same_dir,
        )
    elif final_output_path:
        # Validate provided output_path is structurally safe (same rules as the
        # media path - no traversal, control chars, relative paths).
        is_valid, error_msg = validate_media_path(final_output_path)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid output path: {error_msg}",
            )

    # M6.g fast-path pre-flight subtitle_exists check (explicit language only).
    await _preflight_subtitle_exists(final_language_code, final_output_path, overwrite)

    # Create job
    job = Job(
        id=generate_job_id(),
        status=JobStatus.QUEUED,
        source=source,
        source_ref=resolved_source_ref or source_ref,
        media_path=resolved_media_path,
        # Label snapshot (migration 0008) - never refreshed after creation.
        # A retry ("Overwrite & retry" / History retry) goes back through
        # this same function via a fresh POST /api/jobs, so it re-snapshots
        # from the current cache automatically; nothing should ever copy a
        # label from an old Job row instead.
        title=resolved.title,
        series_title=resolved.series_title,
        season_number=resolved.season_number,
        episode_number=resolved.episode_number,
        output_path=final_output_path,
        language_code=final_language_code,
        language_mode=language_mode,
        output_format=final_output_format,
        priority=priority,
        overwrite=overwrite,
        progress_percent=0,
        progress_message="Job created, waiting for worker",
        cancel_requested=False,
    )

    db.add(job)
    try:
        await db.commit()
    except IntegrityError as err:
        # M6.g duplicate guard: the partial unique index
        # (ix_jobs_active_dupguard) rejects a second active job for the same
        # (media_path, language_code, output_format). Roll back and surface a
        # clean 409 the frontend's existing dead toast handler expects.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "job_already_active"},
        ) from err
    await db.refresh(job)

    await write_job_log(
        db,
        LogLevel.INFO,
        f"Job created: source={source.value}, media_path={resolved_media_path}",
        job_id=job.id,
    )

    return job
