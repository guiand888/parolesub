"""Tests for API wanted endpoints."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from audio_to_subs.core.library_sort import library_sort_key
from audio_to_subs.db.models import BazarrCache, JobLog, JobStatus, LogLevel


class TestWantedItemModel:
    """Test WantedItem model."""

    def test_wanted_item_fields(self):
        """Test that WantedItem has all required fields."""
        from audio_to_subs.api.routes.wanted import WantedItem

        # Check that the model has all expected fields
        fields = WantedItem.model_fields

        assert "id" in fields
        assert "kind" in fields
        assert "ext_id" in fields
        assert "title" in fields
        assert "media_path" in fields
        assert "has_any_subs" in fields
        assert "missing_subtitles" in fields
        assert "last_polled" in fields
        assert "active_job_id" in fields
        assert "active_job_status" in fields
        assert "active_job_progress" in fields


class TestWantedListResponseModel:
    """Test WantedListResponse model."""

    def test_wanted_list_response_fields(self):
        """Test that WantedListResponse has all required fields."""
        from audio_to_subs.api.routes.wanted import WantedListResponse

        fields = WantedListResponse.model_fields

        assert "items" in fields
        assert "total" in fields
        assert "last_refreshed_at" in fields


class TestWantedItemType:
    """Test WantedItemType enum."""

    def test_wanted_item_type_values(self):
        """Test WantedItemType enum values."""
        from audio_to_subs.api.routes.wanted import WantedItemType

        assert WantedItemType.ALL.value == "all"
        assert WantedItemType.MOVIE.value == "movie"
        assert WantedItemType.EPISODE.value == "episode"


class TestListWantedEndpoint:
    """Test GET /api/wanted endpoint."""

    def test_list_wanted_empty(self, authenticated_client):
        """Test list_wanted with empty cache."""
        response = authenticated_client.get("/api/wanted")

        assert response.status_code == 200
        data = response.json()

        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)

    def test_list_wanted_with_type_filter(self, authenticated_client):
        """Test list_wanted with type filter."""
        response = authenticated_client.get("/api/wanted?item_type=movie")

        assert response.status_code == 200
        data = response.json()

        assert "items" in data
        assert "total" in data

    def test_list_wanted_with_pagination(self, authenticated_client):
        """Test list_wanted with pagination."""
        response = authenticated_client.get("/api/wanted?page=1&page_size=50")

        assert response.status_code == 200
        data = response.json()

        assert "items" in data
        assert "total" in data
        assert len(data["items"]) <= 50

    def test_list_wanted_batches_active_job_status_per_item(
        self, sync_session, authenticated_client
    ):
        """Each item's active_job_status/progress must come from its OWN job.

        Regression test for the N+1 fix: list_wanted used to issue one
        SELECT per item to resolve active job status; it now issues a single
        batched query keyed by job id. This verifies the batching didn't mix
        up which job belongs to which item, and that an item whose job has
        already finished (DONE) correctly shows no active job status.
        """
        from tests.conftest import make_job

        # Distinct media paths so the M6.g duplicate-active guard (unique
        # index on media_path+language_code+output_format for active jobs)
        # doesn't reject the seed rows; the test validates per-item active
        # job status mapping, which is independent of media_path.
        queued_job = make_job(
            status=JobStatus.QUEUED,
            progress_percent=0,
            media_path="/test/video-queued.mp4",
        )
        running_job = make_job(
            status=JobStatus.RUNNING,
            progress_percent=42,
            media_path="/test/video-running.mp4",
        )
        done_job = make_job(
            status=JobStatus.DONE,
            progress_percent=100,
            media_path="/test/video-done.mp4",
        )
        sync_session.add_all([queued_job, running_job, done_job])
        sync_session.flush()

        sync_session.add_all(
            [
                BazarrCache(
                    id="movie:1",
                    kind="movie",
                    ext_id=1,
                    title="Movie One",
                    media_path="/data/movies/one.mkv",
                    has_any_subs=False,
                    missing_subtitles=[],
                    last_polled=datetime.now(timezone.utc),
                    active_job_id=queued_job.id,
                ),
                BazarrCache(
                    id="movie:2",
                    kind="movie",
                    ext_id=2,
                    title="Movie Two",
                    media_path="/data/movies/two.mkv",
                    has_any_subs=False,
                    missing_subtitles=[],
                    last_polled=datetime.now(timezone.utc),
                    active_job_id=running_job.id,
                ),
                BazarrCache(
                    id="movie:3",
                    kind="movie",
                    ext_id=3,
                    title="Movie Three (job finished)",
                    media_path="/data/movies/three.mkv",
                    has_any_subs=False,
                    missing_subtitles=[],
                    last_polled=datetime.now(timezone.utc),
                    active_job_id=done_job.id,
                ),
            ]
        )
        sync_session.commit()

        response = authenticated_client.get("/api/wanted")

        assert response.status_code == 200
        items = {item["id"]: item for item in response.json()["items"]}

        assert items["movie:1"]["active_job_status"] == "queued"
        assert items["movie:1"]["active_job_progress"] == 0

        assert items["movie:2"]["active_job_status"] == "running"
        assert items["movie:2"]["active_job_progress"] == 42

        # done_job isn't QUEUED/RUNNING, so it's excluded by the batched
        # query's status filter, same as before the N+1 fix.
        assert items["movie:3"]["active_job_status"] is None
        assert items["movie:3"]["active_job_progress"] is None

    def _seed_titles(self, sync_session):
        sync_session.add_all(
            [
                BazarrCache(
                    id="movie:1",
                    kind="movie",
                    ext_id=1,
                    title="The Matrix",
                    media_path="/data/movies/matrix.mkv",
                    has_any_subs=False,
                    missing_subtitles=[{"code2": "en"}],
                    last_polled=datetime.now(timezone.utc),
                ),
                BazarrCache(
                    id="movie:2",
                    kind="movie",
                    ext_id=2,
                    title="Matrix Reloaded",
                    media_path="/data/movies/reloaded.mkv",
                    has_any_subs=True,
                    missing_subtitles=[{"code2": "fr"}],
                    last_polled=datetime.now(timezone.utc),
                ),
                BazarrCache(
                    id="episode:1",
                    kind="episode",
                    ext_id=1,
                    title="Breaking Bad S01E01",
                    media_path="/data/series/bb.mkv",
                    has_any_subs=False,
                    missing_subtitles=[{"code2": "en"}],
                    last_polled=datetime.now(timezone.utc),
                ),
            ]
        )
        sync_session.commit()

    def test_list_wanted_search_matches_across_all_pages(
        self, sync_session, authenticated_client
    ):
        """Search must run server-side, not just over the displayed page."""
        self._seed_titles(sync_session)
        response = authenticated_client.get("/api/wanted?search=matrix")
        assert response.status_code == 200
        titles = {i["title"] for i in response.json()["items"]}
        assert titles == {"The Matrix", "Matrix Reloaded"}

    def test_list_wanted_search_is_case_insensitive(
        self, sync_session, authenticated_client
    ):
        self._seed_titles(sync_session)
        response = authenticated_client.get("/api/wanted?search=BREAKING")
        assert response.status_code == 200
        titles = {i["title"] for i in response.json()["items"]}
        assert titles == {"Breaking Bad S01E01"}

    def test_list_wanted_scope_no_subs_filter(self, sync_session, authenticated_client):
        self._seed_titles(sync_session)
        response = authenticated_client.get("/api/wanted?scope=no_subs")
        assert response.status_code == 200
        items = response.json()["items"]
        assert items == [i for i in items if not i["has_any_subs"]]
        assert {i["title"] for i in items} == {"The Matrix", "Breaking Bad S01E01"}

    def test_list_wanted_scope_missing_filter(self, sync_session, authenticated_client):
        self._seed_titles(sync_session)
        response = authenticated_client.get("/api/wanted?scope=missing")
        assert response.status_code == 200
        titles = {i["title"] for i in response.json()["items"]}
        # All three seeded items carry a non-empty missing_subtitles list.
        assert titles == {"The Matrix", "Matrix Reloaded", "Breaking Bad S01E01"}

    def test_list_wanted_scope_all_returns_everything(
        self, sync_session, authenticated_client
    ):
        self._seed_titles(sync_session)
        response = authenticated_client.get("/api/wanted?scope=all")
        assert response.status_code == 200
        titles = {i["title"] for i in response.json()["items"]}
        assert titles == {"The Matrix", "Matrix Reloaded", "Breaking Bad S01E01"}

    def test_list_wanted_default_scope_is_all(self, sync_session, authenticated_client):
        """Omitting scope must behave the same as scope=all (no filter)."""
        self._seed_titles(sync_session)
        response = authenticated_client.get("/api/wanted")
        assert response.status_code == 200
        titles = {i["title"] for i in response.json()["items"]}
        assert titles == {"The Matrix", "Matrix Reloaded", "Breaking Bad S01E01"}

    def test_list_wanted_search_composes_with_other_filters(
        self, sync_session, authenticated_client
    ):
        """Search must respect item_type, language, and scope filters."""
        self._seed_titles(sync_session)
        # "matrix" + Movies + no subs + missing lang en -> only "The Matrix"
        response = authenticated_client.get(
            "/api/wanted?search=matrix&item_type=movie&scope=no_subs&language=en"
        )
        assert response.status_code == 200
        titles = {i["title"] for i in response.json()["items"]}
        assert titles == {"The Matrix"}

    @pytest.mark.parametrize(
        ("scope", "expected_titles"),
        [
            ("all", {"The Matrix", "Matrix Reloaded"}),
            ("missing", {"The Matrix", "Matrix Reloaded"}),
            ("no_subs", {"The Matrix"}),
        ],
    )
    def test_list_wanted_scope_composes_with_item_type(
        self, sync_session, authenticated_client, scope, expected_titles
    ):
        """scope must apply on top of item_type, not replace it."""
        self._seed_titles(sync_session)
        response = authenticated_client.get(
            f"/api/wanted?scope={scope}&item_type=movie"
        )
        assert response.status_code == 200
        titles = {i["title"] for i in response.json()["items"]}
        assert titles == expected_titles

    @pytest.mark.parametrize("scope", ["all", "missing", "no_subs"])
    def test_list_wanted_scope_composes_with_language(
        self, sync_session, authenticated_client, scope
    ):
        """scope must apply on top of the language filter, not replace it.

        language=en matches "The Matrix" and "Breaking Bad S01E01" (both
        missing an "en" subtitle); "Matrix Reloaded" is missing "fr" and is
        excluded regardless of scope. Both matches also happen to have
        has_any_subs=False, so all three scopes agree here - this still
        exercises the combined language+scope Python-filter code path.
        """
        self._seed_titles(sync_session)
        response = authenticated_client.get(f"/api/wanted?scope={scope}&language=en")
        assert response.status_code == 200
        titles = {i["title"] for i in response.json()["items"]}
        assert titles == {"The Matrix", "Breaking Bad S01E01"}

    def test_list_wanted_scope_no_subs_pagination(
        self, sync_session, authenticated_client
    ):
        """scope=no_subs filters in SQL directly; `total` must still reflect
        the filtered count, not the unfiltered library size."""
        self._seed_titles(sync_session)
        response = authenticated_client.get("/api/wanted?scope=no_subs&page_size=1")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2  # The Matrix + Breaking Bad S01E01
        assert len(data["items"]) == 1

    def test_list_wanted_scope_missing_pagination_recomputed_after_python_filter(
        self, sync_session, authenticated_client
    ):
        """scope=missing filters in Python (SQLite has no json_contains over
        missing_subtitles); `total` must reflect the filtered count, not the
        unfiltered SQL count, mirroring the language filter's fix."""
        self._seed_titles(sync_session)
        response = authenticated_client.get("/api/wanted?scope=missing&page_size=1")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3  # all three seeded items are missing >=1 language
        assert len(data["items"]) == 1

    # --- Ordering regression tests (migration 0007 sort fields) -----------

    _ORDERING_EXPECTED_IDS = [
        "movie:1",
        "episode:101",
        "episode:102",
        "episode:103",
        "episode:104",
        "episode:105",
        "episode:201",
    ]

    def _seed_ordering_series(self, sync_session):
        """Seed a movie plus two series, interleaved, to exercise the full
        ORDER BY chain: sort_key -> kind -> series_ext_id -> season bucket
        (regular < specials/season 0 < unknown) -> season_number ->
        episode_number -> id.

        sort_key is computed with library_sort_key (never hardcoded) so this
        test tracks the real algorithm (including article-stripping: "The
        Example Show" sorts as "example show", ahead of "Zeta" but
        behind "Avatar").
        """
        example_sort_key = library_sort_key("The Example Show")
        zeta_sort_key = library_sort_key("Zeta")
        avatar_sort_key = library_sort_key("Avatar")
        now = datetime.now(timezone.utc)

        items = [
            BazarrCache(
                id="movie:1",
                kind="movie",
                ext_id=1,
                title="Avatar",
                media_path="/data/movies/avatar.mkv",
                has_any_subs=False,
                missing_subtitles=[],
                last_polled=now,
                sort_key=avatar_sort_key,
            ),
            # Deliberately inserted out of expected order to prove the DB
            # query - not insertion order - determines the response order.
            BazarrCache(
                id="episode:103",
                kind="episode",
                ext_id=103,
                title="Episode 1",
                series_title="The Example Show",
                series_ext_id=100,
                season_number=4,
                episode_number=1,
                media_path="/data/series/example/s04e01.mkv",
                has_any_subs=False,
                missing_subtitles=[],
                last_polled=now,
                sort_key=example_sort_key,
            ),
            BazarrCache(
                id="episode:201",
                kind="episode",
                ext_id=201,
                title="Episode 1",
                series_title="Zeta",
                series_ext_id=200,
                season_number=1,
                episode_number=1,
                media_path="/data/series/zeta/s01e01.mkv",
                has_any_subs=False,
                missing_subtitles=[],
                last_polled=now,
                sort_key=zeta_sort_key,
            ),
            BazarrCache(
                id="episode:102",
                kind="episode",
                ext_id=102,
                title="Episode 10",
                series_title="The Example Show",
                series_ext_id=100,
                season_number=1,
                episode_number=10,
                media_path="/data/series/example/s01e10.mkv",
                has_any_subs=False,
                missing_subtitles=[],
                last_polled=now,
                sort_key=example_sort_key,
            ),
            BazarrCache(
                id="episode:105",
                kind="episode",
                ext_id=105,
                title="Unresolved Episode",
                series_title="The Example Show",
                series_ext_id=100,
                season_number=None,
                episode_number=None,
                media_path="/data/series/example/unknown.mkv",
                has_any_subs=False,
                missing_subtitles=[],
                last_polled=now,
                sort_key=example_sort_key,
            ),
            BazarrCache(
                id="episode:104",
                kind="episode",
                ext_id=104,
                title="Special: Behind the Scenes",
                series_title="The Example Show",
                series_ext_id=100,
                season_number=0,
                episode_number=1,
                media_path="/data/series/example/s00e01.mkv",
                has_any_subs=False,
                missing_subtitles=[],
                last_polled=now,
                sort_key=example_sort_key,
            ),
            BazarrCache(
                id="episode:101",
                kind="episode",
                ext_id=101,
                title="Episode 2",
                series_title="The Example Show",
                series_ext_id=100,
                season_number=1,
                episode_number=2,
                media_path="/data/series/example/s01e02.mkv",
                has_any_subs=False,
                missing_subtitles=[],
                last_polled=now,
                sort_key=example_sort_key,
            ),
        ]
        sync_session.add_all(items)
        sync_session.commit()
        return items

    def test_list_wanted_orders_by_sort_key_kind_season_episode(
        self, sync_session, authenticated_client
    ):
        """Regression test for the ordering bug: with real sort/season/
        episode data, results must come back as Avatar, then the Example
        Show's episodes in season/episode order (regular seasons before
        the season-0 special before the unknown-season episode), then
        Zeta - never lexicographic-by-bare-title order."""
        self._seed_ordering_series(sync_session)

        response = authenticated_client.get("/api/wanted")
        assert response.status_code == 200
        ids = [i["id"] for i in response.json()["items"]]
        assert ids == self._ORDERING_EXPECTED_IDS

    def test_list_wanted_order_preserved_across_pages(
        self, sync_session, authenticated_client
    ):
        """Pagination must not reshuffle rows: concatenating small pages
        must reproduce the same global order as a single unpaged request."""
        self._seed_ordering_series(sync_session)

        collected: list[str] = []
        page = 1
        while len(collected) < len(self._ORDERING_EXPECTED_IDS):
            response = authenticated_client.get(
                "/api/wanted", params={"page": page, "page_size": 2}
            )
            assert response.status_code == 200
            page_items = response.json()["items"]
            if not page_items:
                break
            collected.extend(i["id"] for i in page_items)
            page += 1

        assert collected == self._ORDERING_EXPECTED_IDS

    @pytest.mark.parametrize("params", [{"scope": "missing"}, {"language": "en"}])
    def test_list_wanted_order_preserved_on_python_filtered_path(
        self, sync_session, authenticated_client, params
    ):
        """scope=missing and language=<code> run through the Python-side
        filter branch of list_wanted, which loads `all_items` from the base
        query before slicing in Python. That base query must carry the same
        ORDER BY as the plain SQL path, or this branch silently reverts to
        whatever order SQLite happens to return rows in."""
        items = self._seed_ordering_series(sync_session)
        for item in items:
            item.missing_subtitles = [{"code2": "en"}]
        sync_session.commit()

        response = authenticated_client.get("/api/wanted", params=params)
        assert response.status_code == 200
        ids = [i["id"] for i in response.json()["items"]]
        assert ids == self._ORDERING_EXPECTED_IDS

    def test_list_wanted_null_sort_key_sorts_last_not_first(
        self, sync_session, authenticated_client
    ):
        """A row with a NULL sort_key (not yet touched by a sync since
        migration 0007) must sort AFTER every row with a real sort_key, not
        before.

        SQLite's default ASC ordering puts NULL first, which would put an
        un-backfilled row at the very top of page 1 - ahead of everything,
        including a movie like "Avatar" - the opposite of the deliberate
        "unknowns last" treatment `_WANTED_ORDER` already applies to
        `season_number` via `SEASON_ORDER_BUCKET_SQL`. `_WANTED_ORDER` must
        wrap `sort_key` in `nulls_last(...)` to get this right.
        """
        now = datetime.now(timezone.utc)
        sync_session.add_all(
            [
                BazarrCache(
                    id="movie:1",
                    kind="movie",
                    ext_id=1,
                    title="Avatar",
                    media_path="/data/movies/avatar.mkv",
                    has_any_subs=False,
                    missing_subtitles=[],
                    last_polled=now,
                    sort_key=library_sort_key("Avatar"),
                ),
                # Not yet backfilled: no sort_key, no structured fields at
                # all - just like a row synced by a pre-0007 worker that
                # hasn't been re-polled since the upgrade.
                BazarrCache(
                    id="episode:900",
                    kind="episode",
                    ext_id=900,
                    title="Episode 1",
                    media_path="/data/series/unbackfilled/e1.mkv",
                    has_any_subs=False,
                    missing_subtitles=[],
                    last_polled=now,
                    sort_key=None,
                ),
                BazarrCache(
                    id="movie:2",
                    kind="movie",
                    ext_id=2,
                    title="Zeta",
                    media_path="/data/movies/zeta.mkv",
                    has_any_subs=False,
                    missing_subtitles=[],
                    last_polled=now,
                    sort_key=library_sort_key("Zeta"),
                ),
            ]
        )
        sync_session.commit()

        response = authenticated_client.get("/api/wanted")
        assert response.status_code == 200
        ids = [i["id"] for i in response.json()["items"]]
        assert ids == ["movie:1", "movie:2", "episode:900"]

    def test_list_wanted_orders_by_kind_and_series_when_sort_key_collides(
        self, sync_session, authenticated_client
    ):
        """Two different series/movies that happen to share an identical
        sort_key must still be ordered deterministically by kind, then
        series_ext_id, then season/episode - proving those terms are
        actually load-bearing in `_WANTED_ORDER`, not merely decorative.

        `_seed_ordering_series`'s multi-row fixture can't catch a regression
        here: every one of its "The Example Show" rows already shares
        the same kind AND series_ext_id, so dropping either term from
        `_WANTED_ORDER` wouldn't change that fixture's expected order at
        all. This test forges a same-sort_key collision between two
        genuinely distinct items to close that gap.
        """
        now = datetime.now(timezone.utc)
        shared_key = library_sort_key("Same Name")
        sync_session.add_all(
            [
                BazarrCache(
                    id="episode:20",
                    kind="episode",
                    ext_id=20,
                    title="Episode 1",
                    series_title="Same Name",
                    series_ext_id=200,  # higher series_ext_id
                    season_number=1,
                    episode_number=1,
                    media_path="/data/series/b/s01e01.mkv",
                    has_any_subs=False,
                    missing_subtitles=[],
                    last_polled=now,
                    sort_key=shared_key,
                ),
                BazarrCache(
                    id="episode:10",
                    kind="episode",
                    ext_id=10,
                    title="Episode 1",
                    series_title="Same Name",
                    series_ext_id=100,  # lower series_ext_id
                    season_number=1,
                    episode_number=1,
                    media_path="/data/series/a/s01e01.mkv",
                    has_any_subs=False,
                    missing_subtitles=[],
                    last_polled=now,
                    sort_key=shared_key,
                ),
                BazarrCache(
                    id="movie:30",
                    kind="movie",
                    ext_id=30,
                    title="Same Name",
                    media_path="/data/movies/same-name.mkv",
                    has_any_subs=False,
                    missing_subtitles=[],
                    last_polled=now,
                    sort_key=shared_key,
                ),
            ]
        )
        sync_session.commit()

        response = authenticated_client.get("/api/wanted")
        assert response.status_code == 200
        ids = [i["id"] for i in response.json()["items"]]
        # "episode" < "movie" lexicographically, so the two episodes (lower
        # series_ext_id first) come before the movie.
        assert ids == ["episode:10", "episode:20", "movie:30"]

    def test_list_wanted_response_includes_series_and_season_fields(
        self, sync_session, authenticated_client
    ):
        """Episode items expose series_title/season_number/episode_number;
        movie items report them as null."""
        self._seed_ordering_series(sync_session)

        response = authenticated_client.get("/api/wanted")
        assert response.status_code == 200
        items = {i["id"]: i for i in response.json()["items"]}

        episode = items["episode:101"]
        assert episode["series_title"] == "The Example Show"
        assert episode["season_number"] == 1
        assert episode["episode_number"] == 2

        movie = items["movie:1"]
        assert movie["series_title"] is None
        assert movie["season_number"] is None
        assert movie["episode_number"] is None

    def test_list_wanted_order_by_is_satisfied_by_the_index(self, sync_session):
        """`ix_bazarr_cache_sort` must actually back `_WANTED_ORDER`, not
        just exist.

        Regression guard for a real perf gap: `_WANTED_ORDER`'s season-bucket
        term used to be built with SQLAlchemy's `case()`, whose THEN/ELSE
        values compile to bound `?` parameters. SQLite's planner never
        matches a bound parameter to an expression index, so it silently
        fell back to `SCAN bazarr_cache` + `USE TEMP B-TREE FOR ORDER BY` -
        a full table scan and full sort on every request, index present or
        not. Building the term from `text(SEASON_ORDER_BUCKET_SQL)` instead
        (the same literal SQL the index itself is built from - see
        db/models.py) lets SQLite satisfy the whole ORDER BY straight from
        the index. Confirmed with EXPLAIN QUERY PLAN, not just by reading
        the code - a future edit to either side could silently reintroduce
        the mismatch.
        """
        from sqlalchemy import select, text
        from sqlalchemy.dialects import sqlite as sqlite_dialect

        from audio_to_subs.api.routes.wanted import _WANTED_ORDER

        # Enough rows that SQLite's planner has a real table to reason
        # about, rather than trivially preferring whatever for a handful
        # of rows.
        for n in range(1, 501):
            season = None if n % 37 == 0 else (0 if n % 11 == 0 else (n % 6) + 1)
            sync_session.add(
                BazarrCache(
                    id=f"episode:{n}",
                    kind="episode",
                    ext_id=n,
                    title=f"Episode {n}",
                    media_path=f"/tv/show{n % 20}/e{n}.mkv",
                    has_any_subs=False,
                    missing_subtitles=[],
                    series_title=f"Show {n % 20}",
                    series_ext_id=n % 20,
                    season_number=season,
                    episode_number=n,
                    sort_key=library_sort_key(f"Show {n % 20}"),
                )
            )
        sync_session.commit()
        sync_session.connection().exec_driver_sql("ANALYZE")

        query = select(BazarrCache).order_by(*_WANTED_ORDER)
        sql = str(
            query.compile(
                dialect=sqlite_dialect.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )

        plan_rows = sync_session.execute(text("EXPLAIN QUERY PLAN " + sql)).all()
        plan = " | ".join(str(row) for row in plan_rows)

        assert "USING INDEX ix_bazarr_cache_sort" in plan, plan
        assert "TEMP B-TREE" not in plan, plan

    # --- Episode-code search tests -----------------------------------------

    def test_list_wanted_search_season_code_only(
        self, sync_session, authenticated_client
    ):
        """search=s04 matches only items in season 4 (any series)."""
        self._seed_ordering_series(sync_session)
        response = authenticated_client.get("/api/wanted", params={"search": "s04"})
        assert response.status_code == 200
        ids = {i["id"] for i in response.json()["items"]}
        assert ids == {"episode:103"}

    def test_list_wanted_search_season_episode_code(
        self, sync_session, authenticated_client
    ):
        """search=S04E01 matches exactly that one episode."""
        self._seed_ordering_series(sync_session)
        response = authenticated_client.get("/api/wanted", params={"search": "S04E01"})
        assert response.status_code == 200
        ids = {i["id"] for i in response.json()["items"]}
        assert ids == {"episode:103"}

    def test_list_wanted_search_nxn_code(self, sync_session, authenticated_client):
        """The "4x01" shorthand is equivalent to S04E01."""
        self._seed_ordering_series(sync_session)
        response = authenticated_client.get("/api/wanted", params={"search": "4x01"})
        assert response.status_code == 200
        ids = {i["id"] for i in response.json()["items"]}
        assert ids == {"episode:103"}

    def test_list_wanted_search_text_plus_season_code(
        self, sync_session, authenticated_client
    ):
        """search="example s01" matches the season-1 episodes of the series
        whose name (not bare episode title) contains "example"."""
        self._seed_ordering_series(sync_session)
        response = authenticated_client.get(
            "/api/wanted", params={"search": "example s01"}
        )
        assert response.status_code == 200
        ids = {i["id"] for i in response.json()["items"]}
        assert ids == {"episode:101", "episode:102"}

    def test_list_wanted_search_matches_episode_own_title_not_series(
        self, sync_session, authenticated_client
    ):
        """Free-text search also matches an episode's own bare title, even
        when that text doesn't appear in the series name."""
        self._seed_ordering_series(sync_session)
        response = authenticated_client.get(
            "/api/wanted", params={"search": "Behind the Scenes"}
        )
        assert response.status_code == 200
        ids = {i["id"] for i in response.json()["items"]}
        assert ids == {"episode:104"}

    def test_list_wanted_search_escapes_percent_literal(
        self, sync_session, authenticated_client
    ):
        """A literal '%' in the search text must not act as a SQL LIKE
        wildcard (reuses the escaping logic already used elsewhere in this
        route)."""
        sync_session.add_all(
            [
                BazarrCache(
                    id="movie:501",
                    kind="movie",
                    ext_id=501,
                    title="50% Off",
                    media_path="/data/movies/50off.mkv",
                    has_any_subs=False,
                    missing_subtitles=[],
                    last_polled=datetime.now(timezone.utc),
                ),
                BazarrCache(
                    id="movie:502",
                    kind="movie",
                    ext_id=502,
                    title="50XOff Anything",
                    media_path="/data/movies/50xoff.mkv",
                    has_any_subs=False,
                    missing_subtitles=[],
                    last_polled=datetime.now(timezone.utc),
                ),
            ]
        )
        sync_session.commit()

        response = authenticated_client.get("/api/wanted", params={"search": "50%"})
        assert response.status_code == 200
        titles = {i["title"] for i in response.json()["items"]}
        assert titles == {"50% Off"}


class TestGetWantedItemEndpoint:
    """Test GET /api/wanted/{item_id} endpoint."""

    def test_get_wanted_item_not_found(self, authenticated_client):
        """Test get_wanted_item with nonexistent ID."""
        response = authenticated_client.get("/api/wanted/movie:999999")

        assert response.status_code == 404

    def test_get_wanted_item_with_active_job(self, sync_session, authenticated_client):
        """Test get_wanted_item returns correct item with active job status.

        Verifies that get_wanted_item:
        1. Returns the requested item
        2. Includes active_job_status/progress for QUEUED/RUNNING jobs
        3. Excludes jobs in DONE state (same filter as list_wanted)
        """
        from tests.conftest import make_job

        # Create a running job
        running_job = make_job(status=JobStatus.RUNNING, progress_percent=50)
        sync_session.add(running_job)
        sync_session.flush()

        # Create a cached item with the running job
        sync_session.add(
            BazarrCache(
                id="episode:123",
                kind="episode",
                ext_id=123,
                title="Test Episode",
                media_path="/data/show/s01e01.mkv",
                has_any_subs=False,
                missing_subtitles=[],
                last_polled=datetime.now(timezone.utc),
                active_job_id=running_job.id,
            )
        )
        sync_session.commit()

        response = authenticated_client.get("/api/wanted/episode:123")

        assert response.status_code == 200
        item = response.json()

        assert item["id"] == "episode:123"
        assert item["kind"] == "episode"
        assert item["ext_id"] == 123
        assert item["title"] == "Test Episode"
        assert item["active_job_id"] == running_job.id
        assert item["active_job_status"] == "running"
        assert item["active_job_progress"] == 50

    def test_get_wanted_item_with_finished_job_hidden(
        self, sync_session, authenticated_client
    ):
        """Test get_wanted_item hides DONE jobs (same filter as list_wanted).

        Regression test: both list_wanted and get_wanted_item must filter
        jobs by status (only QUEUED/RUNNING are "active"), so an item pointing
        to a DONE job should show no active_job_status/progress.
        """
        from tests.conftest import make_job

        done_job = make_job(status=JobStatus.DONE, progress_percent=100)
        sync_session.add(done_job)
        sync_session.flush()

        sync_session.add(
            BazarrCache(
                id="movie:999",
                kind="movie",
                ext_id=999,
                title="Finished Movie",
                media_path="/data/movies/finished.mkv",
                has_any_subs=False,
                missing_subtitles=[],
                last_polled=datetime.now(timezone.utc),
                active_job_id=done_job.id,
            )
        )
        sync_session.commit()

        response = authenticated_client.get("/api/wanted/movie:999")

        assert response.status_code == 200
        item = response.json()

        # The item exists
        assert item["id"] == "movie:999"
        # But no active job status is shown (because DONE is filtered out)
        assert item["active_job_status"] is None
        assert item["active_job_progress"] is None


class TestPathTranslation:
    """Test path translation functionality."""

    @pytest.mark.asyncio
    async def test_get_path_map_applies_configured_mapping(self, mock_db_session):
        """_get_path_map must actually load and apply the path_mappings
        setting (regression: B12 — selecting Setting.value_json returns the
        scalar string, and code that then reads `.value_json` off it raises
        AttributeError, silently swallowed, so mappings were never applied).
        """
        import json

        from audio_to_subs.api.routes.wanted import _get_path_map
        from audio_to_subs.db.models import Setting

        mock_db_session.add(
            Setting(
                key="path_mappings",
                value_json=json.dumps(
                    [{"bazarr_prefix": "/data/movies", "local_prefix": "/movies"}]
                ),
            )
        )
        await mock_db_session.flush()
        await mock_db_session.commit()

        path_map = await _get_path_map(mock_db_session)

        assert path_map.translate("/data/movies/foo.mp4") == "/movies/foo.mp4"

    def test_translate_paths(self):
        """Test _translate_paths function."""
        from audio_to_subs.api.routes.wanted import _translate_paths

        # Verify function exists
        assert callable(_translate_paths)


class TestLastRefreshed:
    """Test last refreshed time functionality."""

    def test_get_last_refreshed(self):
        """Test _get_last_refreshed function."""
        from audio_to_subs.api.routes.wanted import _get_last_refreshed

        # Verify function exists
        assert callable(_get_last_refreshed)


class TestWantedRefreshEndpoint:
    """Test POST /api/wanted/refresh endpoint."""

    def test_refresh_endpoint_exists(self, authenticated_client):
        """Test that the refresh endpoint exists and returns success."""
        response = authenticated_client.post("/api/wanted/refresh")

        # The endpoint should exist and return 200
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["started", "completed", "failed"]
        assert "movies_processed" in data
        assert "episodes_processed" in data
        assert isinstance(data["movies_processed"], int)
        assert isinstance(data["episodes_processed"], int)

    def test_refresh_all(self, authenticated_client):
        """Test refresh with all items (default)."""
        response = authenticated_client.post(
            "/api/wanted/refresh", json={"item_type": "all"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["started", "completed", "failed"]
        assert "movies_processed" in data
        assert "episodes_processed" in data
        assert isinstance(data["movies_processed"], int)
        assert isinstance(data["episodes_processed"], int)

    def test_refresh_movies_only(self, authenticated_client):
        """Test refresh with movies only filter."""
        response = authenticated_client.post(
            "/api/wanted/refresh", json={"item_type": "movie"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["started", "completed", "failed"]
        assert "movies_processed" in data
        assert "episodes_processed" in data
        assert isinstance(data["movies_processed"], int)
        assert isinstance(data["episodes_processed"], int)

    def test_refresh_episodes_only(self, authenticated_client):
        """Test refresh with episodes only filter."""
        response = authenticated_client.post(
            "/api/wanted/refresh", json={"item_type": "episode"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["started", "completed", "failed"]
        assert "movies_processed" in data
        assert "episodes_processed" in data
        assert isinstance(data["movies_processed"], int)
        assert isinstance(data["episodes_processed"], int)

    def test_refresh_without_item_type(self, authenticated_client):
        """Test refresh without item_type parameter (should default to all)."""
        response = authenticated_client.post("/api/wanted/refresh")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["started", "completed", "failed"]
        assert "movies_processed" in data
        assert "episodes_processed" in data
        assert isinstance(data["movies_processed"], int)
        assert isinstance(data["episodes_processed"], int)

    def test_refresh_client_init_failure_writes_error_job_log(
        self, authenticated_client, sync_session
    ):
        """If initializing the Bazarr client raises, the failure is persisted
        to the UI's activity log, not just returned in the HTTP response."""
        with patch(
            "audio_to_subs.bazarr.poller.get_bazarr_client_with_settings",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            response = authenticated_client.post("/api/wanted/refresh")

        assert response.status_code == 200
        assert response.json()["status"] == "failed"

        log = sync_session.execute(
            select(JobLog).where(JobLog.level == LogLevel.ERROR)
        ).scalar_one()
        assert log.job_id is None
        assert "Bazarr sync failed" in log.message


class TestWantedRefreshStatusEndpoint:
    """Tests for GET /api/wanted/refresh/{refresh_id}.

    This endpoint is the recovery path for clients that missed the terminal
    SSE event - the persisted snapshot lets the frontend's watchdog
    finalize rather than hang on "Refreshing...".
    """

    def test_get_status_returns_404_for_unknown_id(self, authenticated_client):
        """Unknown or expired refresh ids return 404 so the client can
        surface a clear error instead of guessing.

        The Redis miss path is covered by test_queue_events; here we patch
        get_refresh_state to return None and verify the route maps that to
        a 404 with a useful detail message.
        """
        with patch(
            "audio_to_subs.queue_.events.get_refresh_state",
            new=AsyncMock(return_value=None),
        ):
            response = authenticated_client.get(f"/api/wanted/refresh/{uuid4()}")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_status_returns_404_for_non_uuid_id(self, authenticated_client):
        """Non-UUID ids are rejected at the route level (path validation)."""
        response = authenticated_client.get("/api/wanted/refresh/not-a-uuid")
        assert response.status_code == 422

    def test_get_status_returns_persisted_snapshot(self, authenticated_client):
        """When the persisted state exists, GET returns it as a typed
        WantedRefreshStatusResponse.

        The Redis round-trip is covered by test_queue_events; here we patch
        get_refresh_state to focus on the route's response shaping.
        """
        refresh_id = str(uuid4())
        snapshot = {
            "refresh_id": refresh_id,
            "status": "completed",
            "processed": 7,
            "total": None,
            "percent": 100,
            "stage": "done",
            "movies_processed": 4,
            "episodes_processed": 3,
            "error": None,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        with patch(
            "audio_to_subs.queue_.events.get_refresh_state",
            new=AsyncMock(return_value=snapshot),
        ):
            response = authenticated_client.get(f"/api/wanted/refresh/{refresh_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["refresh_id"] == refresh_id
        assert data["status"] == "completed"
        assert data["processed"] == 7
        assert data["movies_processed"] == 4
        assert data["episodes_processed"] == 3


class TestWantedRefreshIdAcceptance:
    """The route accepts an optional client-supplied refresh_id so the
    frontend can subscribe to SSE *before* POSTing (closing the race)."""

    def test_refresh_accepts_client_supplied_refresh_id(self, authenticated_client):
        """POST with a refresh_id uses that id verbatim (the client has
        already opened its SSE subscription filtered to that id)."""
        client_id = str(uuid4())
        # Stub Bazarr probe so the route reaches the "started" path.
        fake_client = MagicMock()
        fake_client.close = AsyncMock()
        with patch(
            "audio_to_subs.bazarr.poller.get_bazarr_client_with_settings",
            new=AsyncMock(return_value=(fake_client, "http://bazarr", "key", 30)),
        ):
            response = authenticated_client.post(
                "/api/wanted/refresh",
                json={"item_type": "all", "refresh_id": client_id},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["refresh_id"] == client_id

    def test_refresh_generates_id_when_client_omits_it(self, authenticated_client):
        """POST without refresh_id keeps working (back-compat for any caller
        that doesn't participate in the SSE protocol)."""
        fake_client = MagicMock()
        fake_client.close = AsyncMock()
        with patch(
            "audio_to_subs.bazarr.poller.get_bazarr_client_with_settings",
            new=AsyncMock(return_value=(fake_client, "http://bazarr", "key", 30)),
        ):
            response = authenticated_client.post(
                "/api/wanted/refresh", json={"item_type": "all"}
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        # Server-generated id is a non-empty UUID string.
        assert data["refresh_id"]
        assert data["refresh_id"] != ""

    def test_refresh_rejects_non_uuid_refresh_id(self, authenticated_client):
        """A malformed refresh_id fails Pydantic validation (422) rather
        than being silently coerced."""
        response = authenticated_client.post(
            "/api/wanted/refresh",
            json={"item_type": "all", "refresh_id": "not-a-uuid"},
        )
        assert response.status_code == 422
