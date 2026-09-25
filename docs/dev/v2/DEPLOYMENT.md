# Deployment

Four containers in `docker-compose.yml`: **backend**, **worker**, **frontend**, **redis**. Two shared volumes: `db_data` (SQLite file) and external `media` volume.

## Image strategy

Backend and worker share single image built from root `Dockerfile`. Frontend is separate image from `frontend/Dockerfile` (Nginx + Vite build). Redis is `redis:7-alpine`.

## Dockerfile (backend + worker)

Multi-stage build: python:3.11-alpine base, installs ffmpeg, libstdc++, ca-certificates, tini. Non-root user (UID 1000 by default, but the image is UID-agnostic: the app is installed only into site-packages with bytecode precompiled at build time, and nothing under `/app` or `/usr/local` needs to be writable at runtime, so `--user`/compose `user:` can be any uid). `tini` is `ENTRYPOINT` (PID 1: forwards signals, reaps orphaned processes); the backend and worker roles override `CMD`/compose `command:` — never `entrypoint:`, which would bypass tini. Worker uses sync SQLite driver, backend uses async.

## docker-compose.yml

Services: backend (FastAPI), worker (job processor), frontend (Nginx), redis (pub/sub). SQLite on shared volume. Media volume external: true.

Secrets mounted at `/run/secrets/<name>`. Python code reads `<KEY>` or `<KEY>_FILE` envs, preferring file when present.

Health checks: backend and worker each run `python -m audio_to_subs.healthcheck {api|worker}` (a tiny stdlib-only module — no per-run app import, unlike the shell/urllib one-liner it replaced). The backend checks `/api/healthz`; the worker has no HTTP server, so it checks its own liveness heartbeat file instead (`audio_to_subs/worker/heartbeat.py`) rather than sharing the backend's check. Redis uses `redis-cli ping`.

## First-run procedure

1. Create external media volume: `docker volume create media`
2. Drop secret files in ./.secrets/ with proper permissions
3. Set env (BAZARR_URL, PATH_MAPPINGS_JSON, ADMIN_USERNAME, FRONTEND_PORT)
4. Set `ADMIN_PASSWORD` (or `ADMIN_PASSWORD_FILE` secret) to a strong secret — the bootstrap refuses default/placeholder values. This is the sole mechanism for setting and rotating the admin password; a redeploy reconciles the stored hash automatically.
5. Bring up: `podman compose up -d`
6. Visit http://localhost:8080 and log in as admin

## Path mapping

Set `PATH_MAPPINGS_JSON` as JSON array: `[["/data/media","/mnt/media"]]`. Multiple pairs supported; first match wins.

## SQLite + multiple writers

WAL + BEGIN IMMEDIATE + busy_timeout=5000 handles 1 API + 1-3 workers safely. Claim SQL portable to Postgres.

**Important**: Any async task acquiring DB session must NOT hold it across await asyncio.sleep() - exit context before waiting.

## Backups

Backup `db_data` volume. Use SQLite `.backup` command for consistent snapshots.