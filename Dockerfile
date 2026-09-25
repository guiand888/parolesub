# Multi-stage build for minimal production image

# Application version is baked at build time from the repo-root VERSION file
# (via setup.py -> package metadata). No build arg / env var is required.

# Stage 1: Builder
FROM docker.io/library/python:3.11.9-alpine3.19 AS builder

# Install build dependencies
RUN apk add --no-cache \
    gcc \
    musl-dev \
    linux-headers \
    ca-certificates

# Set working directory
WORKDIR /build

# Copy dependency files
COPY requirements.txt ./
COPY VERSION ./

# Install dependencies to temporary location
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Copy application source
COPY audio_to_subs/ ./audio_to_subs/
COPY pyproject.toml setup.py ./

# Install application
RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime
FROM docker.io/library/python:3.11.9-alpine3.19

# Build arguments for user configuration. Only the image's OWN default user
# is created from these; the container may still be run as any other uid via
# `docker run --user`/compose `user:` — see the UID-agnostic note below.
ARG USER_UID=1000
ARG USER_GID=1000

# Install only runtime dependencies. tini is PID 1's job: it forwards signals
# and reaps zombies, which the bare `uvicorn`/`python -m audio_to_subs.worker`
# entrypoints never did on their own - this is what let orphaned
# healthcheck processes accumulate as permanent zombies under the worker.
RUN apk add --no-cache \
    ffmpeg \
    libstdc++ \
    ca-certificates \
    tini \
    # For uvicorn
    libc6-compat

# Copy installed packages from builder. This is the ONLY copy of the
# application code in the runtime image — there is no second copy under
# /app. A prior version also COPYed audio_to_subs/ to /app/audio_to_subs and
# relied on WORKDIR /app putting that copy ahead of site-packages on
# sys.path; combined with a container uid that didn't own /app, every
# `python -c`/`python -m` invocation (including the old HEALTHCHECK) had to
# recompile the whole package from source on every run. Importing only from
# site-packages, precompiled below, fixes that for any uid.
COPY --from=builder /install /usr/local

# Precompile bytecode into site-packages while still root, so it's fully
# usable read-only from any uid the container runs as (including a uid that
# doesn't exist in this image, e.g. compose `user: "1001:1001"` on a host
# where volumes are owned by uid 1001). unchecked-hash pycs are valid
# regardless of the source .py mtimes, which don't survive the COPY above.
RUN python -m compileall -q --invalidation-mode unchecked-hash \
    /usr/local/lib/python3.11/site-packages

# alembic.ini is the only file the app still reads relative to its working
# directory (the lifespan and admin CLI run `alembic upgrade head` with
# cwd=/app); the migration scripts themselves resolve from the installed
# package via alembic.ini's `script_location = audio_to_subs:db/migrations`
# (see alembic.ini), not from a file on disk here.
COPY alembic.ini /app/alembic.ini

# Set working directory
WORKDIR /app

# Create non-root user with configurable UID/GID. Nothing under /app or
# /usr/local needs to be writable at runtime (no bytecode cache, no code
# copy), so nothing there is chowned to appuser — least privilege, and the
# image behaves the same under any --user.
RUN addgroup -g ${USER_GID} appgroup && \
    adduser -D -u ${USER_UID} -G appgroup appuser

# Create directories for input/output, plus placeholders for named-volume
# mount points (/data, /movies, /tv) so a fresh empty volume mounted over
# them inherits appuser ownership instead of the root:root default Docker/
# Podman assign to newly created mount points.
RUN mkdir -p /input /output /tmp/parolesub /data /movies /tv && \
    chown -R appuser:appgroup /input /output /tmp/parolesub /data /movies /tv

USER appuser

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV TMPDIR=/tmp/parolesub
# Defensive: the runtime uid can't write next to site-packages anyway (see
# above), but this makes CPython skip even attempting it.
ENV PYTHONDONTWRITEBYTECODE=1

# tini is PID 1. It forwards signals to the real process below and reaps any
# process that gets orphaned onto it (e.g. a healthcheck run that times out),
# so containers built from this image never accumulate zombies regardless of
# which role (API or worker) they run. There is no image-level HEALTHCHECK:
# the API and worker roles need different checks (HTTP liveness vs. a
# background-loop heartbeat), and a single inherited check previously meant
# the worker silently ran the API's — compose defines each role's check
# instead (see docker-compose*.yml).
ENTRYPOINT ["/sbin/tini", "--"]

# Default command: API mode. The worker role overrides this via compose
# `command:` (not `entrypoint:`, which would bypass tini).
CMD ["uvicorn", "audio_to_subs.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
