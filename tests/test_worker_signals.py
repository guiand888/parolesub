"""Tests for worker signal-handler wiring (M5.7)."""

import os
import signal
import subprocess
import sys
import time

import pytest

from audio_to_subs.worker.__main__ import Worker, handle_shutdown


class TestWorkerSignalHandling:
    """The M5.6-era code registered ``shutdown(signame: str)`` directly, but
    ``signal.signal`` always calls handlers as ``handler(signum, frame)`` — so a
    real SIGINT/SIGTERM raised ``TypeError`` instead of shutting down. These
    tests pin the corrected ``(signum, frame)`` signature.
    """

    def test_handler_accepts_signum_and_frame(self):
        """Invoking the registered handler with the OS signature sets shutdown."""
        worker = Worker()
        assert worker._shutdown is False

        prev_int = signal.getsignal(signal.SIGINT)
        prev_term = signal.getsignal(signal.SIGTERM)
        try:
            handle_shutdown(worker)

            handler = signal.getsignal(signal.SIGTERM)
            # Replicate exactly how the C runtime delivers the signal.
            handler(signal.SIGTERM, None)

            assert worker._shutdown is True
            # Also wakes a worker blocked in _wait_for_wake (up to
            # WORKER_POLL_FALLBACK_SECONDS) immediately, instead of at the
            # next poll.
            assert worker._shutdown_event.is_set()
        finally:
            signal.signal(signal.SIGINT, prev_int)
            signal.signal(signal.SIGTERM, prev_term)

    def test_sigint_also_shuts_down(self):
        """SIGINT path uses the same corrected handler signature."""
        worker = Worker()
        prev_int = signal.getsignal(signal.SIGINT)
        prev_term = signal.getsignal(signal.SIGTERM)
        try:
            handle_shutdown(worker)
            handler = signal.getsignal(signal.SIGINT)
            handler(signal.SIGINT, None)
            assert worker._shutdown is True
        finally:
            signal.signal(signal.SIGINT, prev_int)
            signal.signal(signal.SIGTERM, prev_term)

    @pytest.mark.skipif(
        not os.environ.get("A2S_WORKER_SIGNAL_SMOKE"),
        reason="requires a live worker environment (redis + db); set "
        "A2S_WORKER_SIGNAL_SMOKE=1 to run",
    )
    def test_worker_exits_cleanly_on_sigterm(self):
        """Send a real SIGTERM to a running worker and confirm graceful exit."""
        env = dict(os.environ)
        env.setdefault("MISTRAL_API_KEY", "dummy")
        proc = subprocess.Popen(
            [sys.executable, "-m", "audio_to_subs.worker"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            # Give the worker a moment to reach its run loop.
            time.sleep(2.0)
            proc.send_signal(signal.SIGTERM)
            try:
                returncode = proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                pytest.fail("worker did not shut down within 15s of SIGTERM")
            assert returncode == 0
        finally:
            if proc.poll() is None:
                proc.kill()
