"""Tests for POST /api/jobs - language_mode handling in create_job_service.

Covers the auto-mode language_code override and the output-path ordering
fix (the default-language setting must be applied before the output path
is generated, not after).

media_path is a plain string under /movies rather than a real file - path
validation and output-path generation are pure string operations that don't
require the file to exist. validate_media_path only enforces structural
safety (absolute, no traversal, no control chars); there is no longer a
fixed root directory, so arbitrary absolute paths are accepted.
"""

import json
from datetime import datetime, timezone

from sqlalchemy import select

from audio_to_subs.db.models import BazarrCache, Job, JobLog, LogLevel, Setting

MEDIA_PATH = "/movies/Test Movie (2024)/movie.mkv"


def test_create_job_auto_mode_ignores_language_code(authenticated_client):
    """language_mode='auto' must force language_code to None server-side,
    even if the client sends one."""
    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": MEDIA_PATH,
            "language_mode": "auto",
            "language_code": "en",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["language_mode"] == "auto"
    assert data["language_code"] is None
    # No language suffix yet - the real language isn't known until the
    # worker finishes transcribing.
    assert data["output_path"] == "/movies/Test Movie (2024)/movie.srt"


def test_create_job_writes_info_job_log(authenticated_client, sync_session):
    """Job creation persists an INFO job_log entry visible in the UI's
    activity log, not just an HTTP response."""
    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": MEDIA_PATH,
            "language_mode": "auto",
        },
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]

    log = sync_session.execute(
        select(JobLog).where(JobLog.job_id == job_id)
    ).scalar_one()
    assert log.level == LogLevel.INFO
    assert "Job created" in log.message


def test_create_job_explicit_mode_keeps_language_code(authenticated_client):
    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": MEDIA_PATH,
            "language_mode": "explicit",
            "language_code": "fr",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["language_mode"] == "explicit"
    assert data["language_code"] == "fr"
    assert data["output_path"] == "/movies/Test Movie (2024)/movie.fr.srt"


def test_create_job_rejects_manual_path_traversal(authenticated_client):
    """Manual media_path containing '..' must be rejected (M6.a)."""
    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": "/movies/Test Movie (2024)/../escape.mkv",
        },
    )
    assert response.status_code == 400, response.text
    assert "traversal" in response.text.lower()


def test_create_job_accepts_manual_path_outside_old_roots(authenticated_client):
    """A manual media_path anywhere on disk is accepted - there is no longer a
    fixed movies/series root. Only structural safety (absolute, no traversal,
    no control chars) is enforced."""
    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": "/data/media/arbitrary/location/movie.mkv",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["output_path"] == (
        "/data/media/arbitrary/location/movie.srt"
    )


def test_create_job_rejects_traversal_output_path(authenticated_client):
    """A user-supplied output_path containing '..' must be rejected (M6.a)."""
    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": MEDIA_PATH,
            "output_path": "/movies/Test Movie (2024)/../evil.srt",
        },
    )
    assert response.status_code == 400, response.text
    assert "traversal" in response.text.lower()


def test_create_job_accepts_clean_manual_path(authenticated_client):
    """A clean manual media_path under the configured root is accepted."""
    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": MEDIA_PATH,
        },
    )
    assert response.status_code == 201, response.text


def test_create_job_auto_mode_does_not_apply_default_language(
    authenticated_client, sync_session
):
    """Auto mode must never fall back to the default_language setting -
    that would defeat the point of letting Mistral auto-detect."""
    sync_session.add(Setting(key="default_language", value_json=json.dumps("es")))
    sync_session.commit()

    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": MEDIA_PATH,
            "language_mode": "auto",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["language_code"] is None
    assert data["output_path"] == "/movies/Test Movie (2024)/movie.srt"


def test_create_job_explicit_mode_no_code_applies_default_language_to_path(
    authenticated_client, sync_session
):
    """Regression test for a pre-existing ordering bug: the output path used
    to be generated from the raw (un-defaulted) language_code, so a job
    relying on the default-language setting got a path with no language
    suffix even though the persisted DB row had one. The path must reflect
    the *final*, defaulted language code."""
    sync_session.add(Setting(key="default_language", value_json=json.dumps("es")))
    sync_session.commit()

    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": MEDIA_PATH,
            "language_mode": "explicit",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["language_code"] == "es"
    assert data["output_path"] == "/movies/Test Movie (2024)/movie.es.srt"

    sync_session.expire_all()
    job = sync_session.get(Job, data["id"])
    assert job.language_code == "es"
    assert job.output_path == "/movies/Test Movie (2024)/movie.es.srt"


def test_create_job_bazarr_source_empty_media_path_clear_error(
    authenticated_client, sync_session
):
    """A bazarr source whose cache entry has an empty media_path (which is
    what the wanted endpoint produces when the real path wasn't joined in)
    must fail with a clear, actionable message rather than the generic
    root-directory validation error."""
    sync_session.add(
        BazarrCache(
            id="movie:42",
            kind="movie",
            ext_id=42,
            title="Pathless Movie",
            media_path="",  # empty - the bug condition
            has_any_subs=False,
            missing_subtitles=[],
            last_polled=datetime.now(timezone.utc),
        )
    )
    sync_session.commit()

    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "bazarr_movie",
            "source_ref": "42",
            "media_path": "",  # frontend sends the empty cached path
        },
    )

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "No media file path is available" in detail


def test_create_job_manual_traversal_rejected(authenticated_client):
    """M6.c: a manual source whose media_path contains '..' must be rejected
    (path traversal protection)."""
    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": "/data/../etc/passwd",
        },
    )
    assert response.status_code == 400, response.text
    assert "Invalid media path" in response.json()["detail"]


def test_create_job_manual_dotdot_rejected(authenticated_client):
    """M6.c: explicit '..' segments in a manual media_path must be rejected
    even before root containment is checked."""
    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": "/movies/../../etc/passwd",
        },
    )
    assert response.status_code == 400, response.text


def test_create_job_manual_relative_path_rejected(authenticated_client):
    """M6.c: relative media paths are rejected at the request boundary.

    Pydantic validation errors surface as 422 with the message in the body.
    """
    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": "../etc/passwd",
        },
    )
    assert response.status_code == 422, response.text
    assert "absolute" in response.text


def test_create_job_manual_output_traversal_rejected(authenticated_client):
    """M6.c: a provided output_path that contains traversal must be rejected."""
    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": MEDIA_PATH,
            "output_path": "/movies/../etc/out.srt",
        },
    )
    assert response.status_code == 400, response.text
    assert "Invalid output path" in response.json()["detail"]


def test_manual_job_rejects_traversal_with_configured_roots(authenticated_client):
    """The always-on check also catches '..' that would otherwise resolve
    inside a configured root (proving it is not merely the root gate)."""
    response = authenticated_client.post(
        "/api/jobs",
        json={"source": "manual", "media_path": "/movies/show/../other.mkv"},
    )
    assert response.status_code == 400, response.text
    assert "traversal" in response.text.lower()


def test_create_job_episode_snapshots_full_media_label(
    authenticated_client, sync_session
):
    """A bazarr_episode job snapshots the full structured label (title,
    series_title, season_number, episode_number) from the matching
    BazarrCache row (migration 0008)."""
    sync_session.add(
        BazarrCache(
            id="episode:501",
            kind="episode",
            ext_id=501,
            title="Episode 1",
            series_title="The Example Show",
            season_number=4,
            episode_number=1,
            media_path="/tv/The Example Show/S04E01.mkv",
            has_any_subs=False,
            missing_subtitles=[],
            audio_language=[],
            last_polled=datetime.now(timezone.utc),
        )
    )
    sync_session.commit()

    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "bazarr_episode",
            "source_ref": "501",
            "media_path": "",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["title"] == "Episode 1"
    assert data["series_title"] == "The Example Show"
    assert data["season_number"] == 4
    assert data["episode_number"] == 1


def test_create_job_movie_snapshots_title_only(authenticated_client, sync_session):
    """A bazarr_movie job only snapshots `title` - a movie has no
    series/season/episode."""
    sync_session.add(
        BazarrCache(
            id="movie:77",
            kind="movie",
            ext_id=77,
            title="Dune (2021)",
            media_path="/movies/Dune (2021)/movie.mkv",
            has_any_subs=False,
            missing_subtitles=[],
            audio_language=[],
            last_polled=datetime.now(timezone.utc),
        )
    )
    sync_session.commit()

    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "bazarr_movie",
            "source_ref": "77",
            "media_path": "",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["title"] == "Dune (2021)"
    assert data["series_title"] is None
    assert data["season_number"] is None
    assert data["episode_number"] is None


def test_create_job_manual_has_no_media_label(authenticated_client):
    """A manual job never has a media label - there's no BazarrCache row to
    snapshot from."""
    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "manual",
            "media_path": MEDIA_PATH,
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["title"] is None
    assert data["series_title"] is None
    assert data["season_number"] is None
    assert data["episode_number"] is None


def test_create_job_episode_pre_sync_cache_snapshots_title_only(
    authenticated_client, sync_session
):
    """A cache row from before migration 0007's first resync has a flattened
    `title` ("<series> - <episode>") and NULL series_title/season_number/
    episode_number. The snapshot must copy whatever the cache row currently
    holds, flattened title and all, rather than assuming the structured
    fields are populated."""
    sync_session.add(
        BazarrCache(
            id="episode:502",
            kind="episode",
            ext_id=502,
            title="The Example Show - Episode 3",
            series_title=None,
            season_number=None,
            episode_number=None,
            media_path="/tv/The Example Show/S04E03.mkv",
            has_any_subs=False,
            missing_subtitles=[],
            audio_language=[],
            last_polled=datetime.now(timezone.utc),
        )
    )
    sync_session.commit()

    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "bazarr_episode",
            "source_ref": "502",
            "media_path": "",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["title"] == "The Example Show - Episode 3"
    assert data["series_title"] is None
    assert data["season_number"] is None
    assert data["episode_number"] is None


def test_create_job_media_path_override_still_snapshots(
    authenticated_client, sync_session
):
    """An explicit media_path override only changes the file path the job
    processes - it must not skip the label snapshot, since the job is still
    for the same cached item."""
    sync_session.add(
        BazarrCache(
            id="episode:503",
            kind="episode",
            ext_id=503,
            title="Episode 1",
            series_title="The Example Show",
            season_number=4,
            episode_number=1,
            media_path="/tv/The Example Show/S04E01.mkv",
            has_any_subs=False,
            missing_subtitles=[],
            audio_language=[],
            last_polled=datetime.now(timezone.utc),
        )
    )
    sync_session.commit()

    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "bazarr_episode",
            "source_ref": "503",
            "media_path": "/tv/The Example Show/S04E01.override.mkv",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["media_path"] == "/tv/The Example Show/S04E01.override.mkv"
    assert data["title"] == "Episode 1"
    assert data["series_title"] == "The Example Show"
    assert data["season_number"] == 4
    assert data["episode_number"] == 1


def test_create_job_episode_snapshots_season_zero_as_a_real_value(
    authenticated_client, sync_session
):
    """season_number=0 (a special) is a real, distinct value elsewhere in
    this codebase (SEASON_ORDER_BUCKET_SQL buckets it separately from NULL/
    "unknown") - the snapshot must carry it through as 0, not silently drop
    it to null."""
    sync_session.add(
        BazarrCache(
            id="episode:504",
            kind="episode",
            ext_id=504,
            title="Christmas Special",
            series_title="The Example Show",
            season_number=0,
            episode_number=1,
            media_path="/tv/The Example Show/S00E01.mkv",
            has_any_subs=False,
            missing_subtitles=[],
            audio_language=[],
            last_polled=datetime.now(timezone.utc),
        )
    )
    sync_session.commit()

    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "bazarr_episode",
            "source_ref": "504",
            "media_path": "",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["season_number"] == 0
    assert data["episode_number"] == 1


def test_create_job_bazarr_source_with_no_cache_row_is_rejected_not_labelless(
    authenticated_client,
):
    """resolve_bazarr_source 404s when source_ref matches no BazarrCache row
    - a freshly-created Bazarr job is NEVER created with a blank label; it
    either gets real label data or doesn't exist at all. (The all-NULL
    label state is reachable only via migration 0008's one-time historical
    backfill of a pre-existing job - see that migration's docstring - not
    through this endpoint.) This is a regression test for a doc/behavior
    mismatch a fresh-eyes review found: every comment describing this
    feature had assumed a Bazarr job *could* be created with a NULL label
    at creation time, which this endpoint has never allowed."""
    response = authenticated_client.post(
        "/api/jobs",
        json={
            "source": "bazarr_episode",
            "source_ref": "999999",
            "media_path": "",
        },
    )

    assert response.status_code == 404, response.text
    assert "not found in cache" in response.json()["detail"]
