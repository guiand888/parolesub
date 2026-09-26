"""Database models for v2.

SQLAlchemy 2.x ORM models.
"""

import json
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from audio_to_subs.core.ids import generate_job_id
from audio_to_subs.db.base import Base

if TYPE_CHECKING:
    pass


# Enums
class JobStatus(str, Enum):
    """Job status enum."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobSource(str, Enum):
    """Job source enum."""

    BAZARR_MOVIE = "bazarr_movie"
    BAZARR_EPISODE = "bazarr_episode"
    MANUAL = "manual"


class LogLevel(str, Enum):
    """Log level enum."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class OutputFormat(str, Enum):
    """Output format enum."""

    SRT = "srt"
    VTT = "vtt"
    WEBVTT = "webvtt"
    SBV = "sbv"


# Models
class User(Base):
    """User model."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    jobs: Mapped[list["Job"]] = relationship(
        "Job", back_populates="user", foreign_keys="Job.user_id"
    )

    __table_args__ = (
        CheckConstraint("username IS NOT NULL", name="users_username_not_null"),
    )


class Job(Base):
    """Job model for transcription jobs."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_job_id, unique=True
    )
    status: Mapped[JobStatus] = mapped_column(
        String(20), nullable=False, default=JobStatus.QUEUED
    )
    source: Mapped[JobSource] = mapped_column(
        String(20), nullable=False, default=JobSource.MANUAL
    )
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_path: Mapped[str] = mapped_column(Text, nullable=False)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    language_mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default="explicit"
    )
    mistral_detected_language: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )
    needs_language_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    output_format: Mapped[OutputFormat] = mapped_column(
        String(20), nullable=False, default=OutputFormat.SRT
    )
    overwrite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    progress_step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_step_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    audio_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    mistral_usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    # Relationships
    user: Mapped["User | None"] = relationship(
        "User", back_populates="jobs", foreign_keys=[user_id]
    )
    logs: Mapped[list["JobLog"]] = relationship(
        "JobLog", back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','done','failed','cancelled')",
            name="jobs_status_check",
        ),
        CheckConstraint(
            "source IN ('bazarr_movie','bazarr_episode','manual')",
            name="jobs_source_check",
        ),
        # M6.g duplicate-active guard. Declared on the model so `create_all`
        # (tests) builds it; the alembic migration 0005 creates the same index
        # in production. COALESCE collapses NULL language_code (auto-detect
        # jobs) to '' so they participate in the guard. Partial: only active
        # (queued/running) jobs are constrained.
        Index(
            "ix_jobs_active_dupguard",
            "media_path",
            text("COALESCE(language_code, '')"),
            "output_format",
            unique=True,
            sqlite_where=text("status IN ('queued','running')"),
        ),
    )

    def get_mistral_usage(self) -> dict[str, Any] | None:
        """Parse mistral_usage_json field."""
        if self.mistral_usage_json is None:
            return None
        try:
            usage: dict[str, Any] = json.loads(self.mistral_usage_json)
            return usage
        except json.JSONDecodeError:
            return None

    @property
    def is_terminal(self) -> bool:
        """Check if job is in terminal state."""
        return self.status in (
            JobStatus.DONE,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        )

    @property
    def runtime_seconds(self) -> float | None:
        """Wall-clock time the job spent processing, in seconds.

        Returns ``None`` when either timestamp is missing.
        """
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


class JobLog(Base):
    """Job log model for milestone logging."""

    __tablename__ = "job_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    level: Mapped[LogLevel] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    job: Mapped["Job | None"] = relationship(
        "Job", back_populates="logs", foreign_keys=[job_id]
    )

    __table_args__ = (
        CheckConstraint(
            "level IN ('debug','info','warning','error')",
            name="job_logs_level_check",
        ),
    )


class Setting(Base):
    """Setting model for application settings."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )

    def get_value(self) -> Any:
        """Parse value_json field."""
        return json.loads(self.value_json)

    def set_value(self, value: Any) -> None:
        """Set value_json field."""
        self.value_json = json.dumps(value)


# Buckets `bazarr_cache.season_number` for ordering: regular seasons (1, 2, …)
# first, then specials (season 0), then rows whose season is still unknown
# (NULL - not yet backfilled by a sync). Shared, byte-identical, between the
# `Index` below and `_WANTED_ORDER` in api/routes/wanted.py - see the Index's
# comment for why this has to be a literal SQL string rather than a
# SQLAlchemy `case()` construct.
SEASON_ORDER_BUCKET_SQL = (
    "CASE WHEN season_number IS NULL THEN 2 " "WHEN season_number = 0 THEN 1 ELSE 0 END"
)


class BazarrCache(Base):
    """Bazarr cache model for caching Bazarr items."""

    __tablename__ = "bazarr_cache"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    # "movie:{radarrId}" or "episode:{sonarrEpisodeId}"
    kind: Mapped[Literal["movie", "episode"]] = mapped_column(String(20))
    ext_id: Mapped[int] = mapped_column(Integer)
    # The item's own title: the movie title, or the episode's own title for
    # an episode (e.g. "Episode 1", or a real episode name). For episodes,
    # the series name lives in `series_title` instead - this column no
    # longer holds the flattened "<series> - <episode>" string it did before
    # migration 0007.
    title: Mapped[str] = mapped_column(Text)
    media_path: Mapped[str] = mapped_column(Text, nullable=False)
    has_any_subs: Mapped[bool] = mapped_column(Boolean, default=False)
    missing_subtitles: Mapped[list[dict[str, Any]]] = mapped_column(
        SQLiteJSON, default=[]
    )
    audio_language: Mapped[list[dict[str, Any]]] = mapped_column(SQLiteJSON, default=[])
    last_polled: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now()
    )
    active_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # --- Structured series/season/episode data (migration 0007) ---
    # Episode-only; NULL for movies. Also NULL, transiently, for an episode
    # row synced by a pre-0007 worker that hasn't been re-polled yet
    # (backfilled by the next full sync, which runs immediately on startup
    # - see `run_bazarr_poller`).
    series_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    series_ext_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # season_number/episode_number can ALSO be NULL permanently, not just
    # transiently: Bazarr's own `/api/episodes` payload allows a null
    # season/episode (e.g. malformed metadata, or a special with no
    # assigned number - see `Episode.season`/`Episode.episode` in
    # bazarr/schemas.py), and the poller passes that through as-is. A
    # resync does not guarantee these become non-NULL.
    season_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Article-insensitive, natural-number sort key for the series or movie
    # display name, from `library_sort_key()`
    # (audio_to_subs/core/library_sort.py). Recomputed on every sync, so it
    # never needs its own migration when the algorithm changes. NULL only
    # transiently pre-backfill (see above) - `library_sort_key()` always
    # returns a string, so a synced row never has a permanently-NULL
    # `sort_key` the way `season_number`/`episode_number` can.
    sort_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("kind IN ('movie','episode')", name="bazarr_cache_kind_check"),
        # Covers every term of the Wanted list's default ORDER BY (see
        # `SEASON_ORDER_BUCKET_SQL` below and `_WANTED_ORDER` in
        # api/routes/wanted.py), so SQLite can satisfy the whole sort
        # straight from the index with no temp-B-tree step - verified with
        # EXPLAIN QUERY PLAN in tests/test_api_wanted.py. `text(...)` is
        # required for the CASE term: SQLAlchemy's `case()` binds its
        # THEN/ELSE values as `?` parameters by default, which never
        # structurally matches this index's literal-integer expression, so
        # `_WANTED_ORDER` must build its CASE term from this exact same SQL
        # string too, not from `case()`.
        Index(
            "ix_bazarr_cache_sort",
            "sort_key",
            "kind",
            "series_ext_id",
            text(SEASON_ORDER_BUCKET_SQL),
            "season_number",
            "episode_number",
            "id",
        ),
    )

    @classmethod
    def make_id(cls, kind: str, ext_id: int) -> str:
        """Create cache ID from kind and external ID."""
        return f"{kind}:{ext_id}"
