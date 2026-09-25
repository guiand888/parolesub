"""Tests for the stdlib-only Docker healthcheck entry point.

audio_to_subs.healthcheck must import nothing else from this project (see its
module docstring) - the whole point is to replace the old HEALTHCHECK, which
imported the entire FastAPI app just to print "OK". These tests run it
as a real subprocess for that reason: importing it in-process wouldn't catch
a regression that pulls in a heavy dependency, and would let a leftover
sys.modules entry from another test mask exactly the bug this module exists
to avoid.
"""

import http.server
import socketserver
import subprocess
import sys
import threading
import time

MODULE = "audio_to_subs.healthcheck"


def _run(args: list[str], env: dict[str, str]) -> int:
    return subprocess.run(
        [sys.executable, "-m", MODULE, *args],
        env=env,
        capture_output=True,
        timeout=10,
    ).returncode


class TestUsage:
    def test_no_args_is_usage_error(self):
        assert _run([], {}) == 2

    def test_unknown_mode_is_usage_error(self):
        assert _run(["bogus"], {}) == 2


class TestWorkerMode:
    def test_fresh_heartbeat_is_healthy(self, tmp_path):
        path = tmp_path / "heartbeat"
        path.write_text("")
        env = {
            "WORKER_HEARTBEAT_PATH": str(path),
            "WORKER_HEARTBEAT_MAX_AGE_SECONDS": "60",
        }
        assert _run(["worker"], env) == 0

    def test_stale_heartbeat_is_unhealthy(self, tmp_path):
        path = tmp_path / "heartbeat"
        path.write_text("")
        old = time.time() - 120
        import os

        os.utime(path, (old, old))
        env = {
            "WORKER_HEARTBEAT_PATH": str(path),
            "WORKER_HEARTBEAT_MAX_AGE_SECONDS": "60",
        }
        assert _run(["worker"], env) == 1

    def test_missing_heartbeat_is_unhealthy(self, tmp_path):
        path = tmp_path / "does-not-exist"
        env = {"WORKER_HEARTBEAT_PATH": str(path)}
        assert _run(["worker"], env) == 1

    def test_defaults_match_settings_defaults(self):
        from audio_to_subs.api.settings import Settings
        from audio_to_subs.healthcheck import (
            _DEFAULT_HEARTBEAT_PATH,
            _DEFAULT_MAX_AGE_SECONDS,
        )

        settings = Settings(SESSION_SECRET="test-secret-not-real")
        assert _DEFAULT_HEARTBEAT_PATH == settings.WORKER_HEARTBEAT_PATH
        assert (
            float(_DEFAULT_MAX_AGE_SECONDS) == settings.WORKER_HEARTBEAT_MAX_AGE_SECONDS
        )


class _OkHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: object) -> None:  # silence test output
        pass


class _ServiceUnavailableHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(503)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


class TestApiMode:
    def _serve(self, handler_cls: type) -> socketserver.TCPServer:
        server = socketserver.TCPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server

    def test_200_is_healthy(self):
        server = self._serve(_OkHandler)
        try:
            port = server.server_address[1]
            env = {"HEALTHCHECK_API_URL": f"http://127.0.0.1:{port}/api/healthz"}
            assert _run(["api"], env) == 0
        finally:
            server.shutdown()

    def test_503_is_unhealthy(self):
        server = self._serve(_ServiceUnavailableHandler)
        try:
            port = server.server_address[1]
            env = {"HEALTHCHECK_API_URL": f"http://127.0.0.1:{port}/api/healthz"}
            assert _run(["api"], env) == 1
        finally:
            server.shutdown()

    def test_unreachable_port_is_unhealthy(self):
        env = {
            "HEALTHCHECK_API_URL": "http://127.0.0.1:1/api/healthz",
            "HEALTHCHECK_API_TIMEOUT_SECONDS": "1",
        }
        assert _run(["api"], env) == 1


class TestNoProjectImports:
    def test_only_stdlib_modules_are_imported(self):
        """Guard against a future edit reintroducing a heavy import.

        Runs the module in a subprocess and inspects sys.modules for
        anything under audio_to_subs.* other than the module itself and the
        top-level package (whose __init__ only does a metadata.version()
        lookup).
        """
        code = (
            "import sys; import audio_to_subs.healthcheck; "
            "print(','.join(m for m in sys.modules if m.startswith('audio_to_subs')))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=10
        )
        modules = {m for m in result.stdout.strip().split(",") if m}
        assert modules <= {"audio_to_subs", "audio_to_subs.healthcheck"}
