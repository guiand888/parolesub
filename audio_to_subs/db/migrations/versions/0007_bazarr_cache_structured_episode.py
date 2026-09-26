"""Add structured series/season/episode columns and a sort key to bazarr_cache.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-26 00:00:00.000000

Before this migration, an episode's `title` was the flattened string
"<series title> - <episode title>", composed once by the poller
(bazarr/poller.py) and never decomposed again. Two problems followed:
season/episode numbers weren't stored at all (even though Bazarr's
`/api/episodes` payload includes them), and the Wanted list's ORDER BY was a
plain `lower(title)` lexicographic sort, so e.g. every season's "Episode 1"
row (many shows reuse a generic per-season episode title) collided in the
same sort position with no season visible to tell them apart, and "Episode
10" sorted before "Episode 2".

This migration adds the columns needed to store the pieces separately:
- `series_title` / `series_ext_id`: the episode's series, for display and
  for the search API to match against.
- `season_number` / `episode_number`: from Bazarr's `season/episode` fields,
  for display, ordering and "SxxEyy" search.
- `sort_key`: an article-insensitive, natural-number sort key over the
  series or movie display name (`library_sort_key()`,
  audio_to_subs/core/library_sort.py), so the SQL ORDER BY needs no
  collation extension.

`title` keeps its column but changes meaning: it becomes the item's own
title (movie title, or bare episode title) rather than the flattened
string. All five new columns are nullable - existing rows are backfilled by
the next full Bazarr sync, which the poller runs immediately at startup
(`run_bazarr_poller`), not by this migration. The API and frontend both
tolerate the NULL state until then. That backfill is guaranteed to clear
`sort_key` and (for an episode) `series_title`/`series_ext_id`, but not
necessarily `season_number`/`episode_number` - Bazarr's own payload allows
a null season/episode (e.g. a special with no assigned number), and the
poller passes that through as-is, so those two can stay NULL permanently
for a given episode even after a resync.

`ix_bazarr_cache_sort` covers every term of the Wanted list's default
ORDER BY (see `_WANTED_ORDER` in api/routes/wanted.py), including a CASE
expression bucketing `season_number` into regular seasons / specials /
unknown, so SQLite can satisfy the whole sort straight from the index with
no temp-B-tree step. The CASE is written out as the same literal SQL
string as `db/models.py`'s `SEASON_ORDER_BUCKET_SQL` (duplicated here
rather than imported, since migrations don't depend on application code -
`tests/test_db_migrations.py` asserts the two stay identical) rather than
built from a SQLAlchemy `case()` construct - `case()`'s THEN/ELSE values
compile to bound `?` parameters by default, which SQLite's planner never
matches to an expression index, silently falling back to a full scan and
sort (confirmed with EXPLAIN QUERY PLAN).

Downgrading a database that has real (non-NULL) data in these columns is
lossy: the columns and the index are dropped outright, and a pre-0007
worker restarted against the downgraded schema has no way to write them
back - episode titles revert to being indistinguishable from each other
within a season (no series/season/episode data at all) until this
migration is re-applied and a full sync runs again.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bazarr_cache", sa.Column("series_title", sa.Text, nullable=True))
    op.add_column("bazarr_cache", sa.Column("series_ext_id", sa.Integer, nullable=True))
    op.add_column("bazarr_cache", sa.Column("season_number", sa.Integer, nullable=True))
    op.add_column(
        "bazarr_cache", sa.Column("episode_number", sa.Integer, nullable=True)
    )
    op.add_column("bazarr_cache", sa.Column("sort_key", sa.Text, nullable=True))

    # Covers every term of the Wanted list's default ORDER BY (see module
    # docstring above). Matches the Index() declared on the BazarrCache
    # model (db/models.py) so `create_all` (tests) and this migration
    # (production) produce the same schema.
    op.create_index(
        "ix_bazarr_cache_sort",
        "bazarr_cache",
        [
            "sort_key",
            "kind",
            "series_ext_id",
            sa.text(
                "CASE WHEN season_number IS NULL THEN 2 "
                "WHEN season_number = 0 THEN 1 ELSE 0 END"
            ),
            "season_number",
            "episode_number",
            "id",
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_bazarr_cache_sort", table_name="bazarr_cache")
    op.drop_column("bazarr_cache", "sort_key")
    op.drop_column("bazarr_cache", "episode_number")
    op.drop_column("bazarr_cache", "season_number")
    op.drop_column("bazarr_cache", "series_ext_id")
    op.drop_column("bazarr_cache", "series_title")
