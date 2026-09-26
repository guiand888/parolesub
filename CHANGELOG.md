# Changelog

All notable changes to this project are documented in this file. Versions follow [Semantic Versioning](https://semver.org/) with `v2.0.0-beta.*` pre-releases leading to the stable v2.0.0 release.

## v2.7.0 - 2026-09-26

Fixes the Wanted library page flattening series and episode into a single title with no season visible, which put every season's generically-titled episode (e.g. each season's "Episode 1") in the same sort position with no way to tell them apart.

### Added

- **Structured series/season/episode data for the Wanted library**: the Bazarr sync now tracks an episode's series, season number, and episode number as their own fields instead of only baking them into a flattened title string. The Wanted page's "Title" column is replaced with a "Series / Movie" column and a new "S/E" column (e.g. "S04E01"), with the episode's bare title available as a hover tooltip.
- **Episode-code search**: `GET /api/wanted`'s `search` parameter now also recognizes episode codes such as `S04E01`, `s4e1`, or `4x01`, and can combine a code with free text in the same query (e.g. `"agency s04"` matches the series/episode name and filters to season 4). It also now matches the series name, not just the item's own title.

### Fixed

- **Wanted list sort order**: the list was previously ordered by a plain, buggy `lower(title)` lexicographic sort over the flattened "<series> - <episode>" string. Series with a generic per-season episode title collided in the same sort position with no season to distinguish them, and numeric ordering was purely lexicographic (e.g. "Episode 10" sorted before "Episode 2"). The list is now ordered by an article-insensitive, natural-number-aware sort key over the series/movie name, then kind, then series, then season (regular seasons first, specials/season 0 next, unknown-season episodes last), then episode number, then id as a final tie-breaker - a stable order across pages, not just within one.

### Upgrade Notes

- Includes a database migration (`0007`) adding structured series/season/episode fields and a sort key to the Bazarr cache. It runs automatically on startup like the others - no manual step required.
- The new columns are nullable and backfilled by the next full Bazarr sync, which runs immediately at startup, not by the migration itself. Existing library rows show full series/season/episode data once that first post-upgrade sync completes, not instantly on restart. `season_number`/`episode_number` can stay null for a given episode even after that sync, if Bazarr's own data lacks a season/episode number for it.

## v2.6.2 - 2026-09-26

Fixes the deployment inheriting a heavy, HTTP-only healthcheck onto the background worker, and the worker taking a database write lock once a second while idle - together the source of continuous CPU load and log/process noise on an otherwise idle deployment.

### Fixed

- **Worker healthcheck**: the worker previously had no healthcheck of its own and, on Docker (not Podman, which drops image-level `HEALTHCHECK`), silently inherited the API's - a full FastAPI app import every 30s, costing several seconds of CPU each time. The worker now has its own liveness heartbeat and healthcheck; the backend's healthcheck is also switched to the same lightweight, stdlib-only module (`audio_to_subs.healthcheck`), replacing the inline `python -c "import urllib.request; ..."`/app-import one-liners.
- **Zombie processes**: the worker ran as PID 1 with no init, so any process that became orphaned onto it (e.g. a healthcheck run that hit its timeout) was never reaped. The image now runs under `tini`.
- **Idle CPU/database load**: the worker polled for new jobs with a bare `asyncio.sleep(1)`, which took SQLite's single-writer lock (`BEGIN IMMEDIATE`) once a second forever, even with nothing queued. It now blocks on the `jobs:new` Redis notification the API already publishes, with a 30s poll as a safety net (also configurable, see `.env.example`) rather than polling every second regardless.
- **`/api/healthz` write-lock contention**: the health probe went through the same `BEGIN IMMEDIATE` path as every writer, so it could queue behind the worker or the Bazarr poller. It now runs a deferred, read-only transaction that never takes the write lock.
- **`/api/healthz` access-log spam**: the backend's own healthcheck polls this endpoint every 10s; it's now dropped from the access log (other requests are unaffected).
- Bytecode caching: the image previously copied the application source to `/app` in addition to installing it, and `WORKDIR /app` put that copy ahead of site-packages on `sys.path`. Combined with a container uid that doesn't own `/app` (a common non-default deployment, e.g. to match host volume ownership), every fresh Python process (including the healthcheck above) had to recompile the whole package from source on every run. The image now installs the app only into site-packages, with bytecode precompiled at build time, and runs correctly under any uid.

### Upgrade Notes

- A custom compose file that overrides the worker's `entrypoint:` must switch to `command:` instead - the image's `ENTRYPOINT` is now `tini`, and overriding it bypasses the zombie-reaping fix above. `docker-compose.yml` and `docker-compose.docker.yml` have been updated; a fork of either must apply the same change and add the worker's own `healthcheck:` block.
- Not part of this release, but observed on the production host while investigating: a host-level Netdata `go.d.plugin` collector polls every reachable Redis instance (including this app's) roughly once a second, which shows up as background Redis CPU/network usage unrelated to this application. Tune Netdata's own Redis collector interval if that matters on your host.

## v2.6.0-beta.1 - 2026-07-23

Consolidated feature release covering milestones M8 and M10–M13 (the full v2.1–v2.6 line planned in `MILESTONES.md`). All changes are backward-compatible except the removal of the `set-password` CLI subcommand noted below.

### Added

- **Bazarr full library sync (M8)**: the poller now syncs the entire Bazarr library (movies + series/episodes) instead of only missing-subtitle items. The old `bazarr_track_no_subs` toggle is replaced by a 3-way scope filter (`all` / `missing` / `no_subs`) derived at query time from the wanted cache. An Alembic migration (`0006`) cleans up the orphaned settings row on upgrade.
- **History Retry action (M12)**: failed jobs that aren't already covered by Overwrite & retry or Rename now show a Retry button that re-queues the job with `overwrite: true`.
- **History filter buttons (M13)**: Apply and Reset buttons for the filter form, ordered Apply-first/Reset-second as an equal-width matched pair.
- **ParoleSub logo (M13)**: brand lockup added to the sidebar (mark-only when collapsed, full lockup when expanded) and the login page, with light/dark variants.

### Changed

- **Admin password reconcile on boot (M10)**: the stored admin password hash is now reconciled against the resolved secret (`ADMIN_PASSWORD` / `ADMIN_PASSWORD_FILE`) on every startup. Rotating the Podman secret or `.env` value and redeploying is the sole mechanism for changing the admin password — the hash is updated automatically on the next boot.
- **Queue auto-refresh default On (M11)**: the Queue panel's Auto-refresh toggle now defaults to On so a freshly opened page live-updates without manual intervention.
- Renumbered the v3.0 Redis milestone (M11→M14 in `MILESTONES.md`) after the v2.x feature line.

### Removed

- `parolesub admin set-password` / `python -m audio_to_subs.admin set-password` subcommand removed. The environment secret is now the single source of truth for the admin password; the CLI command conflicted with the reconcile path (a CLI-set password would be silently reverted to the env value on the next boot).

### Upgrade Notes

- Deployments that previously used `set-password` to set a real password while leaving `ADMIN_PASSWORD` set to a placeholder (`admin`, `changeme`, etc.) in the environment must update the env to a strong secret before upgrading. Otherwise the reconcile path will refuse to start with a `ValueError`, since it will detect the stored hash no longer matches the placeholder env and refuse to rotate to a default/placeholder value.

---

## v2.0.0 - 2026-07-20

Stable release. No functional changes since v2.0.0-beta.13 — this release finalizes the version bump and documentation.

### Changed

- Bumped `VERSION` to `v2.0.0` and updated compose `PAROLESUB_TAG` build-context pins to match, so a default deploy builds the released ref
- Reorganized all markdown documentation under `docs/` (`docs/dev/v1/`, `docs/dev/v2/`, `docs/dev/reference/`, `docs/dev/archive/`, `docs/v3/`) and rewrote the v2 dev docs (architecture, API, database, queue, frontend, Bazarr integration, auth, deployment, testing) to describe the actual shipped system
- Rewrote `CHANGELOG.md` to be version-based instead of milestone-based
- Rewrote `README.md` to be [standard-readme](https://github.com/RichardLitt/standard-readme) compliant
- Renamed `CONTAINER_GUIDE.md` to `CONTRIBUTING.md` at the repo root
- `make release` now creates annotated tags so `git push --follow-tags` pushes them

### Removed

- Deleted `README_v2.md` and `DEPLOY_QUICKSTART.md` (superseded by the reorganized docs)

---

## v2.0.0-beta.13 - 2026-07-19

### Fixed

- Version display now reads from single source of truth (`VERSION` file baked at build time)
- Resolved nix mypy crash and cleared remaining `--strict` errors in `audio_to_subs/`
- Fixed version synchronization between compose, API, and UI
- Fixed unknown version display issue

### Changed

- Shortened job IDs to 12-character base62 encoding for better readability

---

## v2.0.0-beta.12 - 2026-07-19

### Fixed

- Frontend: use route ID instead of full path for job detail `useParams`
- Release: keep compose `APP_VERSION` fallbacks in sync

---

## v2.0.0-beta.11 - 2026-07-19

### Added

- Single-source app version surfaced in both API (`/api/version`) and UI
- Job detail page with expanded job information

### Fixed

- Investigated and resolved job ID issues

---

## v2.0.0-beta.10 - 2026-07-19

### Fixed

- Frontend: dropped unused `resolveRefresh` in WantedPage test

---

## v2.0.0-beta.9 - 2026-07-19

### Fixed

- Docker volume mount points: `chown /data`, `/movies`, `/tv` for proper permissions

---

## v2.0.0-beta.8 - 2026-07-18

### Changed

- Dropped fixed root paths in favor of configurable path mappings

---

## v2.0.0-beta.7 - 2026-07-18

### Changed

- Theme reskin to Ayu Tint navy palette

### Fixed

- Logs: removed dead `opt.color` reference in level filter dropdown
- Make: escaped `#` in `FRONTEND_RUN` so frontend targets actually execute

---

## v2.0.0-beta.6 - 2026-07-18

### Fixed

- Queue: don't resurrect a dismissed job on `merge()`; dropped dead branch
- Wanted: finalize refresh only on terminal SSE status
- Queue: progress total now persists through job completion
- Bazarr: fetch wanted pages only once per poll cycle
- Wanted: corrected pagination with language filter; hardened refresh endpoint

### Added

- Sort wanted items by title (natural sort with embedded numbers)
- Collapsible left sidebar
- Auto-refresh queue UI
- History: add Rename action button to Jobs Actions column
- Username moved to topbar-right account cluster with avatar
- Auto-refresh now actually updates progress
- Semantic sorting for episodes

---

## v2.0.0-beta.5 - 2026-07-18

### Fixed

- Docker errors investigation and resolution

---

## v2.0.0-beta.4 - 2026-07-18

### Fixed

- Deployment: stop publishing backend/redis ports, fixed env-override bug, added Caddy frontend

---

## v2.0.0-beta.3 - 2026-07-18

### Changed

- Deployment: build production images from GitHub repo, keep local build for development
- ParoleSub rebranding: session cookie and sidebar brand updated

---

## v2.0.0-beta.2 - 2026-07-18

### Added

- ParoleSub rebranding complete (product renamed to "ParoleSub")
- Post-M6 hardening batch: deployment hardening, volume mount alignment

---

## v2.0.0-beta.1 - 2026-07-12

This is the first v2 beta release, representing the culmination of milestones M0 through M6.

### Major Features

- **Web Application**: Complete self-hosted web UI with React/Tailwind/shadcn
- **Bazarr Integration**: Automatic detection of missing subtitles, polling, path mapping
- **Job Queue System**: SQLite-based durability with Redis pub/sub for live updates
- **Worker Architecture**: Separate worker containers for job processing
- **Real-time Progress**: SSE streaming for live job progress updates
- **Multi-page UI**: Login, Wanted, Queue, History, Logs, Settings pages
- **Cost Tracking**: Automatic cost calculation from Mistral usage data
- **History & Logging**: Complete job history with aggregates and server-wide logs

### Core System (M0)

- Repo restructure: `src/` → `audio_to_subs/` package
- CLI compatibility preserved during transition

### Database Foundation + Auth (M1)

- SQLite with WAL mode and proper pragmas
- SQLAlchemy 2.x ORM with Alembic migrations
- User model with argon2 password hashing
- Signed cookie sessions using itsdangerous
- Admin bootstrap with password blocklist
- FastAPI scaffold with auth dependencies

### Worker + Queue + Cost (M2)

- Worker container architecture
- Atomic job claiming with SQLite `RETURNING`
- `CancelToken` for graceful cancellation
- Progress callback system with debouncing
- Cost computation from Mistral usage data
- Duration × rate fallback when usage unavailable

### Bazarr Integration (M3)

- Bazarr client with httpx
- Path mapping system for container volume alignment
- Background poller for wanted lists
- `/api/wanted` endpoint with caching
- Job enqueuing from Bazarr items
- Settings API for runtime configuration

### Frontend Foundation (M4)

- Vite + React 18 + TypeScript
- Tailwind CSS + shadcn/ui components
- TanStack Query for server state
- TanStack Router for navigation
- Zustand for SSE state management
- Light/dark/auto theming
- Core pages: Login, Wanted, Queue

### History + Logs + Settings (M5)

- `/api/history` endpoint with aggregates
- `/api/logs` global endpoint
- `/api/jobs/{id}/notify-bazarr` for manual rescan
- Real Bazarr rescan API implementation
- History page with cost/duration aggregates
- Logs page with level filtering
- Settings page with all configuration options

### Polish + Security (M6)

- Input validation tightening
- Error message improvements
- Session management hardening
- Dead field removal
- Quality checks: black, ruff, mypy --strict
- >=80% test coverage maintained
- Security: path traversal rejection, session cookie security flags, secret redaction from logs
- Bootstrap: refuse placeholder passwords and session secrets

### Post-M6 Fixes

- **M5.6.1**: Session release before sleep regression fix, UUID binding, path resolution fixes
- **M5.8**: Structured progress reporting with stage/step tracking, SSE architecture improvements
- **M6.i**: ParoleSub rebranding, deployment hardening, volume mount alignment

---

## v1.0.0 - 2025-11-15

Initial MVP release.

- CLI-based video to subtitle transcription
- Mistral Voxtral Mini integration
- Basic file-based configuration

---

## Milestone Mapping

For reference, the v2 development milestones map approximately to the following version ranges:

| Milestone | Version Range | Completion Date |
|-----------|---------------|-----------------|
| M0 - Repo restructure | v2.0.0-beta.1 | 2026-06-02 |
| M0.5 - Mistral usage probe | v2.0.0-beta.1 | 2026-06-02 |
| M1 - DB foundation + auth | v2.0.0-beta.1 | 2026-06-02 |
| M2 - Worker + queue + cost | v2.0.0-beta.1 | 2026-06-02 |
| M3 - Bazarr + `/api/wanted` | v2.0.0-beta.1 | 2026-06-02 |
| M4 - Frontend foundation | v2.0.0-beta.1 | 2026-06-02 |
| M5 - History + Logs + Settings | v2.0.0-beta.1 | 2026-06-03 |
| M5.1 - M5 cleanup | v2.0.0-beta.1 | 2026-07-01 |
| M5.2 - Volume mount alignment | v2.0.0-beta.1 | 2026-07-01 |
| M5.3 - Structural refactor | v2.0.0-beta.1 | 2026-07-05 |
| M5.4 - Refactor cleanup | v2.0.0-beta.1 | 2026-07-06 |
| M5.5 - Settings regression + Bazarr fix | v2.0.0-beta.1 | 2026-07-07 |
| M5.6 - Schema tightening | v2.0.0-beta.1 | 2026-07-07 |
| M5.6.1 - Post-M5.6 fixes | v2.0.0-beta.1 | 2026-07-10 |
| M5.7 - Deep mypy cleanup | v2.0.0-beta.1 | 2026-07-10 |
| M5.8 - Queue progress reporting | v2.0.0-beta.1 | 2026-07-10 |
| M6 - Polish + security | v2.0.0-beta.1 | 2026-07-12 |
| M6.i - Post-M6 hardening | v2.0.0-beta.2 to v2.0.0-beta.13 | 2026-07-18 |
| M7 - Documentation rewrite | In progress | - |

Note: Milestones M0-M6 were completed before the version tagging system was established. The beta tags were applied retroactively to mark specific deployment states. All v2 functionality from M0-M6 is included in v2.0.0-beta.1 and later.