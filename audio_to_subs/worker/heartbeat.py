"""Liveness heartbeat for the background worker.

The worker's Docker healthcheck used to be the API's `HEALTHCHECK` (a full
FastAPI app import) inherited unmodified from the image - meaningless for a
process that serves no HTTP, and expensive on every run. This module
gives the worker its own liveness signal instead: a heartbeat file whose
mtime the healthcheck (see ``audio_to_subs.healthcheck``) can check cheaply,
with no project imports.

The heartbeat is tied to the claim loop actually advancing, not merely to the
event loop being alive: a watchdog task only refreshes the file while a job
is in flight, or while the main loop's last recorded progress is recent. A
process wedged in a non-yielding retry storm (event loop alive, claim loop
not actually making progress) goes stale and fails its healthcheck, instead
of looking falsely healthy forever.
"""

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkerHeartbeat:
    """Tracks worker liveness and periodically refreshes a heartbeat file."""

    def __init__(self, path: str, interval_seconds: float) -> None:
        self._path = Path(path)
        self._interval_seconds = interval_seconds
        self._job_in_flight = False
        self._last_progress = time.monotonic()

    def reset(self) -> None:
        """Remove any heartbeat file left over from a previous run.

        Called at startup, before the first beat is written, so a stale file
        from a killed container (same volume, fresh process) can never be
        mistaken for a live one during the brief startup window.
        """
        try:
            self._path.unlink(missing_ok=True)
        except OSError as e:  # pragma: no cover - defensive
            logger.warning("Failed to remove stale heartbeat file: %s", e)

    def mark_progress(self) -> None:
        """Record that the claim loop just advanced (claimed or found none)."""
        self._last_progress = time.monotonic()

    def job_started(self) -> None:
        """Record that a job has started running."""
        self._job_in_flight = True
        self.mark_progress()

    def job_finished(self) -> None:
        """Record that the in-flight job has finished (any outcome)."""
        self._job_in_flight = False
        self.mark_progress()

    def _is_healthy(self, fallback_seconds: float) -> bool:
        if self._job_in_flight:
            return True
        stale_after = fallback_seconds + 2 * self._interval_seconds
        return (time.monotonic() - self._last_progress) <= stale_after

    def touch(self, fallback_seconds: float) -> None:
        """Refresh the heartbeat file's mtime if the loop looks healthy.

        Deliberately does nothing (lets the file age) when unhealthy, so the
        healthcheck's staleness check catches it rather than papering over a
        stuck loop with a fresh mtime.
        """
        if not self._is_healthy(fallback_seconds):
            return
        try:
            self._path.touch(exist_ok=True)
        except OSError as e:  # pragma: no cover - defensive
            logger.warning("Failed to touch heartbeat file: %s", e)

    async def run(self, fallback_seconds: float, shutdown: "object") -> None:
        """Background task: touch the heartbeat file on a fixed interval.

        ``shutdown`` is an ``asyncio.Event``; typed loosely here to avoid an
        import-time dependency on asyncio in a module whose only other state
        is stdlib ``time``/``pathlib``.
        """
        import asyncio

        assert isinstance(shutdown, asyncio.Event)
        # Write the first beat immediately so a fresh start doesn't fail the
        # healthcheck's start_period on a slow first interval.
        self.touch(fallback_seconds)
        while not shutdown.is_set():
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=self._interval_seconds)
            except asyncio.TimeoutError:
                pass
            if shutdown.is_set():
                break
            self.touch(fallback_seconds)
