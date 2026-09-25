"""Tests for the worker's liveness heartbeat (audio_to_subs.worker.heartbeat).

The heartbeat backs the worker's Docker healthcheck (see
audio_to_subs.healthcheck), replacing the API's HEALTHCHECK that the worker
used to inherit unmodified. It must prove the claim loop is actually
advancing, not merely that the event loop is alive - see the "stale progress"
tests below.
"""

import asyncio
import time

import pytest

from audio_to_subs.worker.heartbeat import WorkerHeartbeat


class TestWorkerHeartbeatTouch:
    def test_touch_creates_file_when_progressing(self, tmp_path):
        path = tmp_path / "heartbeat"
        heartbeat = WorkerHeartbeat(str(path), interval_seconds=10)
        heartbeat.mark_progress()

        heartbeat.touch(fallback_seconds=30)

        assert path.exists()

    def test_touch_refreshes_mtime(self, tmp_path):
        path = tmp_path / "heartbeat"
        heartbeat = WorkerHeartbeat(str(path), interval_seconds=10)
        heartbeat.mark_progress()
        heartbeat.touch(fallback_seconds=30)
        first_mtime = path.stat().st_mtime

        time.sleep(0.01)
        heartbeat.mark_progress()
        heartbeat.touch(fallback_seconds=30)

        assert path.stat().st_mtime >= first_mtime

    def test_touch_does_nothing_once_progress_is_stale(self, tmp_path):
        path = tmp_path / "heartbeat"
        heartbeat = WorkerHeartbeat(str(path), interval_seconds=1)
        heartbeat.mark_progress()
        # Simulate the claim loop having been wedged well past the
        # fallback + 2*interval staleness threshold.
        heartbeat._last_progress -= 1000

        heartbeat.touch(fallback_seconds=1)

        assert not path.exists()

    def test_touch_keeps_refreshing_while_a_job_is_in_flight(self, tmp_path):
        """A long-running transcription must not be mistaken for a stuck
        claim loop: job_started() overrides the staleness check."""
        path = tmp_path / "heartbeat"
        heartbeat = WorkerHeartbeat(str(path), interval_seconds=1)
        heartbeat.job_started()
        # The claim loop itself hasn't advanced in a long time - fine, a job
        # is running.
        heartbeat._last_progress -= 1000

        heartbeat.touch(fallback_seconds=1)

        assert path.exists()

    def test_job_finished_stops_overriding_staleness(self, tmp_path):
        path = tmp_path / "heartbeat"
        heartbeat = WorkerHeartbeat(str(path), interval_seconds=1)
        heartbeat.job_started()
        heartbeat.job_finished()
        heartbeat._last_progress -= 1000

        heartbeat.touch(fallback_seconds=1)

        assert not path.exists()

    def test_reset_removes_a_stale_file_from_a_previous_run(self, tmp_path):
        path = tmp_path / "heartbeat"
        path.write_text("stale")

        WorkerHeartbeat(str(path), interval_seconds=10).reset()

        assert not path.exists()

    def test_reset_is_safe_when_no_file_exists(self, tmp_path):
        path = tmp_path / "heartbeat"
        WorkerHeartbeat(str(path), interval_seconds=10).reset()  # must not raise
        assert not path.exists()


class TestWorkerHeartbeatRun:
    @pytest.mark.asyncio
    async def test_run_writes_the_first_beat_immediately(self, tmp_path):
        path = tmp_path / "heartbeat"
        heartbeat = WorkerHeartbeat(str(path), interval_seconds=10)
        heartbeat.mark_progress()
        shutdown = asyncio.Event()

        task = asyncio.create_task(heartbeat.run(30, shutdown))
        await asyncio.sleep(0.05)
        shutdown.set()
        await asyncio.wait_for(task, timeout=1)

        assert path.exists()

    @pytest.mark.asyncio
    async def test_run_stops_promptly_on_shutdown(self, tmp_path):
        path = tmp_path / "heartbeat"
        heartbeat = WorkerHeartbeat(str(path), interval_seconds=10)
        heartbeat.mark_progress()
        shutdown = asyncio.Event()

        task = asyncio.create_task(heartbeat.run(30, shutdown))
        await asyncio.sleep(0.05)
        shutdown.set()

        await asyncio.wait_for(
            task, timeout=1
        )  # would time out if run() ignored shutdown
