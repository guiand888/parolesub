"""Worker entry point.

Runs as a background process that claims jobs from the queue and executes
them through the pipeline. Progress updates are published via Redis and stored
in the database.

Usage:
    python -m audio_to_subs.worker

Environment variables:
    DATABASE_URL: Database connection URL (default: sqlite+aiosqlite:////data/parolesub.db)
    REDIS_URL: Redis connection URL (default: redis://localhost:6379/0)
    MISTRAL_API_KEY: Mistral API key (or MISTRAL_API_KEY_FILE)
    WORKER_ID: Optional worker identifier (auto-generated if not provided)
    WORKER_POLL_FALLBACK_SECONDS: Safety-net poll interval when idle (default: 30)
    WORKER_HEARTBEAT_PATH: Liveness heartbeat file (default: /tmp/parolesub-worker.heartbeat)
    WORKER_HEARTBEAT_INTERVAL_SECONDS: How often the heartbeat file is touched (default: 10)
"""

import asyncio
import contextlib
import logging
import os
import signal
import socket
import uuid
from datetime import datetime, timezone
from types import FrameType
from typing import Any

from redis.asyncio import Redis

from audio_to_subs.api.settings import Settings, get_settings
from audio_to_subs.core.logging_config import configure_logging_from_env
from audio_to_subs.db.session import get_async_session
from audio_to_subs.queue_.claim import ClaimedJob, claim_one
from audio_to_subs.queue_.events import CHANNEL_NEW
from audio_to_subs.queue_.reaper import reap_stale_running
from audio_to_subs.worker.heartbeat import WorkerHeartbeat
from audio_to_subs.worker.runner import WorkerDeps, persist_result, run_job

logger = logging.getLogger(__name__)


class Worker:
    """Background worker for job processing."""

    def __init__(self) -> None:
        """Initialize worker."""
        self._settings: Settings | None = None
        self._redis: Redis | None = None
        self._pubsub: Any | None = None
        self._shutdown = False
        # Constructed eagerly (not lazily in startup()) so handle_shutdown()
        # can register a signal handler that sets it even if a signal
        # arrives before startup() has run.
        self._shutdown_event = asyncio.Event()
        self._heartbeat: WorkerHeartbeat | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._worker_id: str = self._generate_worker_id()

    def _generate_worker_id(self) -> str:
        """Generate a unique worker identifier."""
        env_worker_id = os.environ.get("WORKER_ID")
        if env_worker_id:
            return env_worker_id
        return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"

    async def startup(self) -> None:
        """Initialize worker dependencies."""
        logger.info(f"Worker {self._worker_id} starting up...")

        # Load settings
        self._settings = get_settings()

        # Initialize Redis
        redis = Redis.from_url(self._settings.REDIS_URL)
        await redis.ping()
        self._redis = redis
        logger.info("Redis connected")

        # Subscribe to jobs:new before the first claim, so a job created
        # between "no queued job found" and "subscription established" can
        # never be missed - the notification would simply be buffered.
        await self._resubscribe()

        # Run reaper on startup to clean up any stale jobs
        async with get_async_session(self._settings.DATABASE_URL) as session:
            reaped = await reap_stale_running(session, stale_seconds=120)
            logger.info(f"Reaper cleanup: {reaped} stale jobs requeued")

        # Liveness heartbeat: the Docker healthcheck (see
        # audio_to_subs.healthcheck) checks this file's mtime instead of
        # importing the whole API app, which is what the worker inherited
        # unmodified from the image's HEALTHCHECK before.
        self._heartbeat = WorkerHeartbeat(
            self._settings.WORKER_HEARTBEAT_PATH,
            self._settings.WORKER_HEARTBEAT_INTERVAL_SECONDS,
        )
        self._heartbeat.reset()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat.run(
                self._settings.WORKER_POLL_FALLBACK_SECONDS, self._shutdown_event
            )
        )

    async def _resubscribe(self) -> None:
        """(Re)create the jobs:new subscription.

        Called at startup, and again after a pubsub failure - the fallback
        poll (see _wait_for_wake) keeps the worker claiming jobs even while
        this keeps failing, so a Redis outage degrades to polling rather
        than stalling the worker.
        """
        if self._redis is None:
            raise RuntimeError("Worker not started. Call startup() first.")
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(CHANNEL_NEW)

    async def shutdown(self) -> None:
        """Clean up worker resources."""
        logger.info(f"Worker {self._worker_id} shutting down...")
        self._shutdown_event.set()
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
        if self._redis:
            await self._redis.close()
        self._shutdown = True

    async def _wait_for_wake(self) -> None:
        """Block until a jobs:new notification, the fallback poll interval,
        or shutdown - whichever comes first.

        This replaces a plain ``asyncio.sleep(1)`` poll: the worker used to
        take a SQLite write lock (claim_one's BEGIN IMMEDIATE) once a
        second, forever, even with nothing queued. Now it only claims
        again when told to, or at most every WORKER_POLL_FALLBACK_SECONDS as
        a safety net (a missed publish, a Redis blip).
        """
        assert self._settings is not None
        try:
            await self._wait_for_wake_via_pubsub(
                self._settings.WORKER_POLL_FALLBACK_SECONDS
            )
        except Exception as e:
            logger.warning(
                f"Worker {self._worker_id} pubsub wait failed, "
                f"falling back to polling: {e}"
            )
            await asyncio.sleep(self._settings.WORKER_POLL_FALLBACK_SECONDS)
            try:
                await self._resubscribe()
            except Exception as resub_err:
                logger.warning(
                    f"Worker {self._worker_id} failed to resubscribe to "
                    f"{CHANNEL_NEW}: {resub_err}"
                )

    async def _wait_for_wake_via_pubsub(self, fallback_seconds: float) -> None:
        assert self._pubsub is not None
        message_task = asyncio.ensure_future(
            self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=fallback_seconds
            )
        )
        shutdown_task = asyncio.ensure_future(self._shutdown_event.wait())
        done, pending = await asyncio.wait(
            {message_task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if shutdown_task in done:
            return

        # message_task is done. asyncio.wait() never raises a completed
        # task's exception on its own - .result() both retrieves it (so a
        # pubsub error propagates to _wait_for_wake's except block instead
        # of being silently dropped) and would otherwise log "Task
        # exception was never retrieved" for a task nobody awaited.
        message_task.result()

        # Coalesce a burst of several jobs:new publishes (e.g. a bulk Bazarr
        # sync) into one wake instead of one claim_one query per message.
        with contextlib.suppress(Exception):
            while await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0
            ):
                pass

    async def claim_and_run(self) -> None:
        """Claim a job and run it.

        This is the main worker loop:
        1. Try to claim a job
        2. If claimed, run it (or mark failed if setup/execution raises)
        3. If no job available, wait for notification
        """
        if self._settings is None or self._redis is None or self._heartbeat is None:
            raise RuntimeError("Worker not started. Call startup() first.")

        dsn = self._settings.DATABASE_URL

        while not self._shutdown:
            claimed: ClaimedJob | None = None
            try:
                # Claim in a short-lived session so the SQLite write lock is
                # released immediately whether or not a job was available.
                async with get_async_session(dsn) as session:
                    claimed = await claim_one(session, self._worker_id)

                self._heartbeat.mark_progress()

                if claimed is None:
                    # No job: wait OUTSIDE any session so we never hold the
                    # write lock while idle.
                    logger.debug(f"Worker {self._worker_id} waiting for jobs...")
                    await self._wait_for_wake()
                    continue

                logger.info(f"Worker {self._worker_id} claimed job {claimed.id}")

                mistral_api_key = self._settings.mistral_api_key

                if mistral_api_key is None:
                    logger.error("MISTRAL_API_KEY not configured. Cannot run job.")
                    async with get_async_session(dsn) as session:
                        await self._mark_job_failed(
                            session, claimed.id, "Missing Mistral API key"
                        )
                    continue

                # Run the job with NO DB session held: run_job and the
                # ProgressBridge open their own short-lived sessions for each
                # write (settings fetch, progress, logs, rescan). Holding a
                # session open here would keep BEGIN IMMEDIATE active across
                # the minutes-long transcription and lock out every other
                # writer (progress updates, the API's History reads, etc.).
                deps = WorkerDeps(
                    session=None,  # no long-lived session; per-write sessions only
                    redis=self._redis,
                    settings=self._settings,
                    mistral_api_key=mistral_api_key,
                    database_url=dsn,
                )

                self._heartbeat.job_started()
                try:
                    result = await run_job(claimed, deps)
                finally:
                    self._heartbeat.job_finished()

                # Persist the result in a short-lived session.
                async with get_async_session(dsn) as session:
                    await persist_result(session, claimed.id, result)

                logger.info(
                    f"Worker {self._worker_id} completed job {claimed.id}: "
                    f"{result.status}"
                )

            except Exception as e:
                logger.exception(f"Worker {self._worker_id} error")
                # If we claimed a job but it failed during setup or execution,
                # mark it as failed (D15 fix: don't let the outer loop die).
                if claimed is not None:
                    try:
                        async with get_async_session(dsn) as session:
                            await self._mark_job_failed(
                                session, claimed.id, f"Worker error: {str(e)}"
                            )
                    except Exception as mark_failed_error:
                        logger.error(
                            f"Failed to mark job {claimed.id} as failed: {mark_failed_error}"
                        )
                await asyncio.sleep(1)

    async def _mark_job_failed(
        self, session: Any, job_id: str, error_message: str
    ) -> None:
        """Mark a job as failed in the database."""
        from audio_to_subs.db.models import Job, JobStatus

        job = await session.get(Job, job_id)
        if job:
            job.status = JobStatus.FAILED
            job.finished_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            job.error_message = error_message
            await session.commit()
        else:
            logger.error(f"Job {job_id} not found for failure marking")

    async def run(self) -> None:
        """Main worker loop."""
        await self.startup()
        try:
            while not self._shutdown:
                await self.claim_and_run()
        finally:
            await self.shutdown()


def handle_shutdown(worker: Worker) -> None:
    """Register graceful-shutdown handlers for SIGINT and SIGTERM.

    ``signal.signal`` ALWAYS invokes the handler as ``handler(signum, frame)``.
    A single-arg ``shutdown(signame: str)`` therefore raised
    ``TypeError: shutdown() takes 1 positional argument but 2 were given`` on a
    real SIGINT/SIGTERM — confirmed bug fixed in M5.7 (a unit test now invokes
    the handler with the OS signature). The handler must accept both positional
    arguments; ``frame`` is unused but required by the C signal-delivery ABI.

    Also sets ``worker._shutdown_event`` (in addition to the plain
    ``_shutdown`` flag), so a worker blocked in ``_wait_for_wake`` - which can
    wait up to WORKER_POLL_FALLBACK_SECONDS - wakes immediately on SIGTERM
    (forwarded by tini as PID 1) instead of at the next poll.
    """

    def shutdown(signum: int, frame: FrameType | None) -> None:
        signame = signal.Signals(signum).name
        logger.info(f"Received signal {signame}, shutting down...")
        worker._shutdown = True
        worker._shutdown_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)


async def main() -> None:
    """Worker entry point."""
    configure_logging_from_env()

    worker = Worker()
    handle_shutdown(worker)

    try:
        await worker.run()
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("Fatal error")
        raise


if __name__ == "__main__":
    asyncio.run(main())
