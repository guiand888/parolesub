"""Snapshot a readable media label onto each job.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-26 00:00:00.000000

Before this migration, a job's only identifying fields were `source` and
`source_ref` (e.g. "bazarr_episode" / "4242", the Sonarr episode id), which
is exactly what the Queue, History and job-detail pages showed: unreadable
strings like "bazarr_episode #4242" with no series, season, episode or
title anywhere.

This migration adds four nullable columns - `title`, `series_title`,
`season_number`, `episode_number` - mirroring `BazarrCache`'s columns of the
same name (migration 0007). They are a snapshot, not a live join:
`api/services/jobs.py`'s `resolve_bazarr_source` already loads the matching
`BazarrCache` row when a job is created (to resolve `media_path`) and used
to discard it; it now copies these four fields onto the `Job` row instead,
and they are never updated again for that job's lifetime.

That's deliberate, not an oversight. A job is a historical record - History
keeps finished jobs long after their Bazarr item may have been renamed or
removed from the library (`_delete_stale` in bazarr/poller.py deletes cache
rows for items no longer in Bazarr, but never touches jobs). A live join
would make old History rows change or go blank on their own; a snapshot
keeps them stable. These four columns are NULL for a manual job. A
freshly-created Bazarr job is never missing its label: `resolve_bazarr_source`
raises a 404 instead of creating the job at all when no cache row matches
`source_ref`, so a live job either gets real label data or doesn't exist.
The one way a Bazarr job DOES end up all-NULL is through this migration's
own backfill below, for an *existing* job whose cache row is already gone
by the time this migration runs (e.g. the item has since left Bazarr).
season_number/episode_number can also be individually NULL on an otherwise
fully-labeled job, if Bazarr's own data never had a number for that episode
- see the matching comment on `BazarrCache` in db/models.py.

`upgrade()` backfills existing jobs from `bazarr_cache` with a single
correlated UPDATE, matched the same way `BazarrCache.make_id()` builds a
cache id: "movie:{radarr_id}" or "episode:{sonarr_episode_id}". A
non-numeric `source_ref` (never produced by this codebase, but not
guaranteed by the schema either) makes the CAST evaluate to the leading
numeric prefix it can parse, or 0 if there is none (SQLite's CAST-to-INTEGER
semantics), which is virtually certain to match no cache row and leave the
columns NULL - the same graceful-degradation outcome as a genuinely missing
cache row.

One upgrade path is worth calling out: a database still at revision 0006,
upgraded straight to 0008, has a `bazarr_cache` that migration 0007 has
added columns to but that hasn't been re-synced yet (that only happens once
the poller's next startup sync runs). Jobs backfilled from such a database
get their old flattened `title` ("<series> - <episode>") with no
`series_title`/`season_number`/`episode_number` - and because this snapshot
is never refreshed, that flattened title is what those specific jobs will
show forever, even after the cache itself backfills. This does not affect
this project's own production database, which was re-synced under v2.7.0
before this migration ever runs. `downgrade()` simply drops the four
columns; re-running `upgrade()` afterwards re-backfills from whatever the
cache looks like at that later time.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Mirrors BazarrCache.make_id()'s "movie:{id}" / "episode:{id}" scheme.
# CAST(... AS INTEGER) on a non-numeric source_ref never raises in SQLite -
# it evaluates to the leading numeric prefix, or 0 if there is none - so a
# malformed source_ref just matches no cache row (or, in the vanishingly
# unlikely case of a numeric-prefixed one, the wrong row) rather than
# failing the migration. Neither this codebase nor its schema constraints
# ever produce anything but a clean stringified int here.

_CACHE_ID_SQL = (
    "(CASE source WHEN 'bazarr_movie' THEN 'movie:' ELSE 'episode:' END "
    "|| CAST(source_ref AS INTEGER))"
)


def upgrade() -> None:
    op.add_column("jobs", sa.Column("title", sa.Text, nullable=True))
    op.add_column("jobs", sa.Column("series_title", sa.Text, nullable=True))
    op.add_column("jobs", sa.Column("season_number", sa.Integer, nullable=True))
    op.add_column("jobs", sa.Column("episode_number", sa.Integer, nullable=True))

    op.execute(
        f"""
        UPDATE jobs SET
            title = (
                SELECT c.title FROM bazarr_cache c WHERE c.id = {_CACHE_ID_SQL}
            ),
            series_title = (
                SELECT c.series_title FROM bazarr_cache c
                WHERE c.id = {_CACHE_ID_SQL}
            ),
            season_number = (
                SELECT c.season_number FROM bazarr_cache c
                WHERE c.id = {_CACHE_ID_SQL}
            ),
            episode_number = (
                SELECT c.episode_number FROM bazarr_cache c
                WHERE c.id = {_CACHE_ID_SQL}
            )
        WHERE source IN ('bazarr_movie', 'bazarr_episode') AND source_ref IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("jobs", "episode_number")
    op.drop_column("jobs", "season_number")
    op.drop_column("jobs", "series_title")
    op.drop_column("jobs", "title")
