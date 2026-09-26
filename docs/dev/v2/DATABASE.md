# Database

## Engine & file location

- **SQLite**, single file at `/data/parolesub.db` inside the container, on a named volume shared between backend and worker.
- **WAL mode** mandatory with pragmas: journal_mode=WAL, synchronous=NORMAL, busy_timeout=5000, foreign_keys=ON.
- DSN: `sqlite+aiosqlite:////data/parolesub.db` for the API; `sqlite:////data/parolesub.db` for the worker.
- SQLAlchemy 2.x ORM. Postgres upgrade path: only `DATABASE_URL` changes + `alembic upgrade head`.

## Schema

### users

Single row expected at runtime; schema is multi-user-ready.

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PK AUTOINCREMENT` | |
| `username` | `TEXT NOT NULL UNIQUE` | |
| `password_hash` | `TEXT NOT NULL` | argon2id |
| `created_at` | `TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP` | |
| `last_login_at` | `TIMESTAMP NULL` | |

### jobs

| Column | Type | Notes |
|---|---|---|
| `id` | `TEXT PK` | UUIDv4 string |
| `status` | `TEXT NOT NULL` | queued, running, done, failed, cancelled |
| `source` | `TEXT NOT NULL` | bazarr_movie, bazarr_episode, manual |
| `source_ref` | `TEXT NULL` | radarrId, sonarrEpisodeId, or NULL |
| `media_path` | `TEXT NOT NULL` | Resolved local path |
| `title` | `TEXT NULL` | One-time snapshot of `bazarr_cache.title` at job creation (migration 0008); never refreshed afterwards, unlike `bazarr_cache.title` below, which is updated on every sync. NULL for manual jobs. A freshly-created Bazarr job always has this set (job creation 404s instead if no cache row matches); only migration 0008's historical backfill of a pre-existing job can leave it NULL, when that job's cache row was already gone by the time the migration ran. |
| `series_title` | `TEXT NULL` | Same one-time snapshot, of `bazarr_cache.series_title`; episode jobs only, NULL for movie/manual jobs (migration 0008) |
| `season_number` | `INTEGER NULL` | Same one-time snapshot, of `bazarr_cache.season_number`; can be NULL even for an otherwise-matched episode job if Bazarr's own data never had a season number for it (migration 0008) |
| `episode_number` | `INTEGER NULL` | Same one-time snapshot, of `bazarr_cache.episode_number`; same NULL caveat as `season_number` (migration 0008) |
| `output_path` | `TEXT NULL` | Final subtitle path |
| `language_code` | `TEXT NULL` | ISO 639-1 |
| `output_format` | `TEXT NOT NULL DEFAULT 'srt'` | |
| `priority` | `INTEGER NOT NULL DEFAULT 0` | Higher = sooner |
| `progress_percent` | `INTEGER NOT NULL DEFAULT 0` | 0-100, debounced |
| `progress_message` | `TEXT NULL` | |
| `cancel_requested` | `INTEGER NOT NULL DEFAULT 0` | |
| `worker_id` | `TEXT NULL` | Set on claim |
| `audio_duration_seconds` | `REAL NULL` | |
| `mistral_usage_json` | `TEXT NULL` | Raw usage from Mistral |
| `estimated_cost_usd` | `REAL NULL` | Computed in core/cost.py |
| `error_message` | `TEXT NULL` | On failed |
| `created_at` | `TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP` | |
| `started_at` | `TIMESTAMP NULL` | |
| `finished_at` | `TIMESTAMP NULL` | |
| `updated_at` | `TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP` | Bumped on progress writes |

### job_logs

Append-only milestone log; backs the Logs page.

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PK AUTOINCREMENT` | |
| `job_id` | `TEXT NULL FK → jobs(id) ON DELETE CASCADE` | Nullable for server-wide |
| `ts` | `TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP` | |
| `level` | `TEXT NOT NULL` | debug, info, warning, error |
| `message` | `TEXT NOT NULL` | |

### settings

| Column | Type | Notes |
|---|---|---|
| `key` | `TEXT PK` | |
| `value_json` | `TEXT NOT NULL` | Always JSON |
| `updated_at` | `TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP` | |

### bazarr_cache

| Column | Type | Notes |
|---|---|---|
| `id` | `TEXT PK` | "movie:{radarrId}" or "episode:{sonarrEpisodeId}" |
| `kind` | `TEXT NOT NULL` | movie, episode |
| `ext_id` | `INTEGER NOT NULL` | radarrId or sonarrEpisodeId |
| `title` | `TEXT NOT NULL` | For episodes, this is now the bare episode title — see `series_title` for the series name — as of migration 0007; previously this was a flattened `"<series> - <episode>"` string. |
| `subtitle_display` | `TEXT NULL` | "1x01 - Pilot" for episodes |
| `media_path_bazarr` | `TEXT NULL` | Raw path from Bazarr |
| `missing_subtitles_json` | `TEXT NOT NULL` | JSON array of subtitle info |
| `has_any_subs` | `INTEGER NOT NULL` | 0/1 |
| `raw_json` | `TEXT NOT NULL` | Full Bazarr payload |
| `fetched_at` | `TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP` | |
| `series_title` | `TEXT NULL` | Episode's series name; NULL for movies and for pre-0007 rows not yet re-synced (migration 0007) |
| `series_ext_id` | `INTEGER NULL` | Sonarr series id; NULL for movies (migration 0007) |
| `season_number` | `INTEGER NULL` | From Bazarr's episode payload; NULL for movies (migration 0007) |
| `episode_number` | `INTEGER NULL` | From Bazarr's episode payload; NULL for movies (migration 0007) |
| `sort_key` | `TEXT NULL` | Article-insensitive, natural-number-aware sort key over the series/movie display name, from `library_sort_key()` (`audio_to_subs/core/library_sort.py`); recomputed on every sync (migration 0007) |

## Indexes

```sql
CREATE INDEX ix_jobs_claim ON jobs(status, priority DESC, created_at);
CREATE INDEX ix_jobs_history ON jobs(status, finished_at DESC);
CREATE INDEX ix_jobs_dedupe ON jobs(source, source_ref);
CREATE INDEX ix_job_logs_job_ts ON job_logs(job_id, ts);
CREATE INDEX ix_bazarr_cache_kind_hasany ON bazarr_cache(kind, has_any_subs);
CREATE INDEX ix_bazarr_cache_sort ON bazarr_cache(
    sort_key, kind, series_ext_id,
    (CASE WHEN season_number IS NULL THEN 2 WHEN season_number = 0 THEN 1 ELSE 0 END),
    season_number, episode_number, id
);
```

## Alembic

Alembic from day 1. Migration commands run inside the backend container. FastAPI lifespan and worker boot both run `alembic upgrade head`.

Latest migration: `0008_job_media_label_snapshot` — adds `jobs.title` / `series_title` / `season_number` / `episode_number` (all nullable; see the `jobs` table above), a one-time snapshot of the matching `bazarr_cache` row copied when a job is created and never refreshed afterwards — unlike `bazarr_cache`'s own columns of the same name, which are recomputed on every sync. Backfills existing `bazarr_movie`/`bazarr_episode` jobs from the current `bazarr_cache` with a single correlated UPDATE, keyed the same way `BazarrCache.make_id()` builds a cache id (`"movie:{id}"` / `"episode:{id}"`); a job whose cache row is already gone, or whose `source_ref` is non-numeric, simply keeps all four columns NULL, not an error. A database still at revision 0006, upgraded straight to 0008 (skipping 0007's re-sync), backfills its jobs from a `bazarr_cache` that migration 0007 has added columns to but that hasn't been re-synced yet - those jobs' snapshots get the old flattened `title` ("<series> - <episode>") with no `series_title`/`season_number`/`episode_number`, permanently, since the snapshot is never refreshed even once the cache itself later backfills. `downgrade()` drops the four columns, which deletes the snapshot; re-running `upgrade()` afterwards re-backfills from whatever the cache looks like at that later time, not the original values.

Previous migration: `0007_bazarr_cache_structured_episode` — adds `bazarr_cache.series_title` / `series_ext_id` / `season_number` / `episode_number` / `sort_key` (all nullable; see the `bazarr_cache` table above) and the `ix_bazarr_cache_sort` index. Additive-only; existing rows are backfilled by the next full Bazarr sync (runs at startup), not by the migration itself.

`ix_bazarr_cache_sort` covers every term of the Wanted list's default ORDER BY (`sort_key`, `kind`, `series_ext_id`, a season bucket, `season_number`, `episode_number`, `id`), so SQLite satisfies the whole sort straight from the index with no temp-B-tree step. The bucket term has to be the literal SQL string above (also in `SEASON_ORDER_BUCKET_SQL`, `audio_to_subs/db/models.py`) rather than a SQLAlchemy `case()` construct in the ORM query — `case()`'s branch values compile to bound `?` parameters by default, which SQLite's planner never matches to an expression index, so it silently falls back to a full scan and sort instead.
