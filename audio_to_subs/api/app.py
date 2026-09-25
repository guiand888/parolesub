"""FastAPI application factory."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from audio_to_subs.core.logging_config import configure_logging_from_env

# Must run before any other audio_to_subs module logs anything, and before
# uvicorn (which never configures the root logger itself) starts emitting -
# otherwise logger.info/.debug calls throughout the API process are silently
# discarded.
configure_logging_from_env()

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from audio_to_subs import __version__  # noqa: E402
from audio_to_subs.api.routes import auth, healthz  # noqa: E402
from audio_to_subs.api.routes import version as version_route  # noqa: E402
from audio_to_subs.api.routes.history import router as history_router  # noqa: E402
from audio_to_subs.api.routes.jobs import router as jobs_router  # noqa: E402
from audio_to_subs.api.routes.logs import (  # noqa: E402
    global_logs_router,
)
from audio_to_subs.api.routes.logs import (  # noqa: E402
    router as jobs_logs_router,
)
from audio_to_subs.api.routes.settings import router as settings_router  # noqa: E402
from audio_to_subs.api.routes.stream import router as stream_router  # noqa: E402
from audio_to_subs.api.routes.wanted import router as wanted_router  # noqa: E402
from audio_to_subs.api.settings import get_settings  # noqa: E402
from audio_to_subs.auth.bootstrap import bootstrap_admin  # noqa: E402
from audio_to_subs.auth.secrets import refuse_placeholder_secrets  # noqa: E402
from audio_to_subs.bazarr.poller import start_poller, stop_poller  # noqa: E402
from audio_to_subs.queue_.reaper import reap_stale_running  # noqa: E402

if TYPE_CHECKING:
    from audio_to_subs.api.settings import Settings

logger = logging.getLogger(__name__)


def _ensure_session_secret_file() -> None:
    """Generate the session secret file before Settings is instantiated.

    Settings validates that ``SESSION_SECRET`` resolves to a non-None value.
    When the app is configured with ``SESSION_SECRET_FILE`` (container mode)
    and the file does not exist yet (first boot, fresh volume), Settings would
    crash because the file-based secret resolves to None.

    This function reads the env vars directly — before Settings exists — and
    creates the file so the subsequent ``get_settings()`` call succeeds. The
    lifespan no longer needs to handle generation; it only needs to *use* the
    secret that is now guaranteed to exist.
    """
    import os
    from pathlib import Path

    # If SESSION_SECRET is explicitly set, the file is irrelevant.
    if os.environ.get("SESSION_SECRET"):
        return

    secret_file = os.environ.get("SESSION_SECRET_FILE")
    if not secret_file:
        return

    if not Path(secret_file).exists():
        from audio_to_subs.auth.sessions import SessionManager

        SessionManager.write_secret_file(secret_file)
        logger.info("Pre-generated session secret at %s", secret_file)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler.

    Runs on startup and shutdown.
    """
    import asyncio
    import subprocess

    settings = get_settings()

    # Fail fast if any secret is still a known placeholder/default. This is the
    # bootstrap-level gate required by M6.c (security pass): a misconfigured
    # deploy must never start with public-knowledge credentials.
    refuse_placeholder_secrets(settings)

    # Startup
    logger.info("Starting up...")

    # Run migrations (Alembic is the single source of truth for the schema)
    # Run in thread pool to avoid blocking the event loop
    logger.info("Running database migrations...")
    result = await asyncio.to_thread(
        subprocess.run,
        ["alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        cwd=".",
    )
    if result.returncode != 0:
        logger.error("Failed to run migrations: %s", result.stderr)
        raise RuntimeError(f"Migration failed: {result.stderr}")
    logger.info("Database migrations applied")

    # Bootstrap admin user
    logger.info("Bootstrapping admin user...")
    from audio_to_subs.db.session import get_async_session

    async with get_async_session(settings.DATABASE_URL) as session:
        try:
            await bootstrap_admin(
                session,
                username=settings.ADMIN_USERNAME,
                password=settings.admin_password,
            )
        except ValueError as e:
            logger.error("Admin bootstrap failed: %s", str(e))
            raise RuntimeError(str(e)) from e

    # Start reaper task, scoped to this app instance via app.state (not a
    # module-level global) so multiple create_app() instances (e.g. in tests)
    # don't leak reaper tasks across each other.
    app.state.reaper_task = asyncio.create_task(
        _run_reaper_periodically(settings.DATABASE_URL, settings)
    )
    logger.info("Reaper task started")

    # Create the shutdown event that run_bazarr_poller watches, then start the
    # poller via the canonical helper that manages app.state.poller_task.
    # start_poller() already logs "Bazarr poller task started" itself (and
    # guards against double-starting), so nothing further is logged here.
    app.state.shutdown = asyncio.Event()
    await start_poller(app)

    logger.info("Startup complete")

    yield

    # Shutdown
    logger.info("Shutting down...")

    # Signal and await the Bazarr poller via the matching helper.
    app.state.shutdown.set()
    await stop_poller(app)

    # Cancel reaper task
    reaper_task = getattr(app.state, "reaper_task", None)
    if reaper_task:
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass

    logger.info("Shutdown complete")


async def _run_reaper_periodically(database_url: str, settings: "Settings") -> None:
    """Run the reaper periodically to clean up stale jobs.

    Publishes jobs:new when it requeues anything, so a worker blocked on the
    jobs:new subscription (see worker/__main__.py) picks the requeued job up
    immediately instead of waiting for its own fallback poll interval - the
    exact scenario a reap exists for (the worker that died mid-job is, by
    definition, not the one that will notice its job is queued again).
    """
    from audio_to_subs.api.routes._helpers import publish_job_event
    from audio_to_subs.queue_.events import publish_new

    while True:
        try:
            from audio_to_subs.db.session import get_async_session

            async with get_async_session(database_url) as session:
                reaped = await reap_stale_running(session, stale_seconds=120)
                if reaped > 0:
                    logger.info(f"Reaper: {reaped} stale jobs requeued")
                    await publish_job_event(
                        settings, publish_new, "reaper", "reaper requeue"
                    )
        except Exception:
            logger.exception("Reaper error")

        await asyncio.sleep(60)  # Run every 60 seconds


def create_app() -> FastAPI:
    """Create and configure FastAPI application.

    Returns:
        FastAPI application instance
    """
    _ensure_session_secret_file()
    settings = get_settings()

    app = FastAPI(
        title="parolesub v2 API",
        description="API for parolesub v2 transcription service",
        version=__version__,
        lifespan=lifespan,
        debug=settings.DEBUG,
    )

    # Configure CORS from settings
    cors_origins = getattr(settings, "CORS_ORIGINS", ["*"])
    # Cannot use allow_credentials=True with allow_origins=["*"]
    allow_credentials = cors_origins != ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers.
    # stream_router MUST come before jobs_router: /api/jobs/stream is a literal
    # path that would otherwise be shadowed by jobs_router's /{job_id} pattern.

    # Healthz and auth endpoints stay open (no authentication required)
    app.include_router(healthz.router)
    app.include_router(version_route.router)
    app.include_router(auth.router)

    # All other routers require authentication
    from audio_to_subs.auth.deps import get_current_user

    auth_dependency = Depends(get_current_user)

    app.include_router(settings_router, dependencies=[auth_dependency])
    app.include_router(wanted_router, dependencies=[auth_dependency])
    app.include_router(stream_router, dependencies=[auth_dependency])
    app.include_router(jobs_logs_router, dependencies=[auth_dependency])
    app.include_router(jobs_router, dependencies=[auth_dependency])
    app.include_router(global_logs_router, dependencies=[auth_dependency])
    app.include_router(history_router, dependencies=[auth_dependency])

    return app


# Global app instance
_app: FastAPI | None = None


def get_app() -> FastAPI:
    """Get or create application instance.

    Returns:
        FastAPI application instance
    """
    global _app
    if _app is None:
        _app = create_app()
    return _app


# For running with uvicorn: uvicorn audio_to_subs.api.app:app
# Defer app creation if running under pytest (autouse fixture will set up env)
import sys  # noqa: E402
from typing import Any, Protocol  # noqa: E402


class _SupportsASGI(Protocol):
    """The subset of the ASGI interface uvicorn needs from ``app``."""

    async def __call__(
        self, scope: dict[str, Any], receive: Any, send: Any
    ) -> None: ...


class _LazyApp:
    """Lazily resolve the FastAPI app on first use (pytest-safe).

    During pytest collection, ``create_app()`` may fail because the test
    environment is not yet configured by the autouse fixture. Attribute access
    (and the ASGI ``__call__``) is forwarded to a real ``FastAPI`` instance that
    is only built on first use, after fixtures have set up the environment.

    This replaces the previous ``_AppProxy`` hack, which assigned a proxy object
    to a ``FastAPI``-typed module global and silenced the resulting
    ``# type: ignore[assignment]``. Typing it via the ``_SupportsASGI`` Protocol
    (M5.7) keeps ``app`` statically correct for uvicorn while preserving the
    lazy, fixture-after-resolution behavior that tests rely on.
    """

    def __init__(self) -> None:
        self._resolved: FastAPI | None = None

    def _resolve(self) -> FastAPI:
        if self._resolved is None:
            self._resolved = get_app()
        return self._resolved

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        await self._resolve()(scope, receive, send)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


app: _SupportsASGI

if "pytest" not in sys.modules:
    app = create_app()
else:
    app = _LazyApp()
