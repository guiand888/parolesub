"""Stdlib-only Docker healthcheck entry point.

Run as ``python -m audio_to_subs.healthcheck api`` or ``... worker``.

Deliberately imports nothing else from this project: not the FastAPI app
(a full import of ``audio_to_subs.api.app`` used to be the Dockerfile's
``HEALTHCHECK``, and the worker inherited it unmodified even though it
serves no HTTP), not ``Settings`` (whose session-secret validator can
refuse to construct outside a fully configured environment), and not the
worker package (whose ``__init__`` pulls in the transcription pipeline).
Every check here costs a bare CPython interpreter startup, nothing more.

Reads its configuration directly from the environment rather than
``audio_to_subs.api.settings.Settings``, for the same reason: constructing
``Settings`` here would reintroduce the exact per-run cost and fragility
this module exists to avoid. Defaults match ``Settings``'s own defaults.
"""

import os
import sys
import time
import urllib.error
import urllib.request

_DEFAULT_API_URL = "http://127.0.0.1:8000/api/healthz"
_DEFAULT_HEARTBEAT_PATH = "/tmp/parolesub-worker.heartbeat"
_DEFAULT_MAX_AGE_SECONDS = "60"


def _check_api() -> int:
    """Probe the API's /api/healthz endpoint. 0 on HTTP 200, 1 otherwise."""
    url = os.environ.get("HEALTHCHECK_API_URL", _DEFAULT_API_URL)
    timeout = float(os.environ.get("HEALTHCHECK_API_TIMEOUT_SECONDS", "3"))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, OSError, ValueError):
        return 1


def _check_worker() -> int:
    """Check the worker's heartbeat file mtime. 0 if fresh, 1 if stale/missing."""
    path = os.environ.get("WORKER_HEARTBEAT_PATH", _DEFAULT_HEARTBEAT_PATH)
    max_age = float(
        os.environ.get("WORKER_HEARTBEAT_MAX_AGE_SECONDS", _DEFAULT_MAX_AGE_SECONDS)
    )
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return 1
    return 0 if age <= max_age else 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in ("api", "worker"):
        print(
            "usage: python -m audio_to_subs.healthcheck {api|worker}", file=sys.stderr
        )
        return 2
    return _check_api() if args[0] == "api" else _check_worker()


if __name__ == "__main__":
    sys.exit(main())
