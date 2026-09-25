"""Regression guard for the docker-compose <-> Dockerfile contract.

The worker previously had no healthcheck of its own and, under Docker (not
Podman, which drops image-level HEALTHCHECK), silently inherited the API's -
a full FastAPI app import every 30s. Fixed by giving the image a tini
ENTRYPOINT (so compose overrides must use `command:`, not `entrypoint:`,
or they bypass it) and giving each role its own healthcheck. These tests
parse the actual shipped files with PyYAML rather than re-deriving the
intent from source, so a future edit that silently reintroduces the old
`entrypoint:` override or drops a healthcheck fails here instead of only
showing up as CPU noise in production.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.docker.yml"]


def _load_compose(filename: str) -> dict:
    with open(REPO_ROOT / filename) as f:
        return yaml.safe_load(f)


@pytest.fixture(params=COMPOSE_FILES)
def compose(request) -> dict:
    return _load_compose(request.param)


class TestWorkerServiceContract:
    def test_worker_uses_command_not_entrypoint(self, compose):
        """Overriding `entrypoint:` would replace the image's tini PID 1,
        bypassing the zombie-reaping fix it exists for."""
        worker = compose["services"]["worker"]
        assert "entrypoint" not in worker
        assert worker["command"] == ["python", "-m", "audio_to_subs.worker"]

    def test_worker_has_its_own_healthcheck(self, compose):
        """The worker has no HTTP server, so it can't share the API's check
        - and must not be left to silently inherit it from the image."""
        worker = compose["services"]["worker"]
        assert worker["healthcheck"]["test"] == [
            "CMD",
            "python",
            "-m",
            "audio_to_subs.healthcheck",
            "worker",
        ]


class TestBackendServiceContract:
    def test_backend_uses_the_stdlib_healthcheck_module(self, compose):
        backend = compose["services"]["backend"]
        assert backend["healthcheck"]["test"] == [
            "CMD",
            "python",
            "-m",
            "audio_to_subs.healthcheck",
            "api",
        ]


class TestComposeFilesAgree:
    """docker-compose.yml (Podman secrets) and docker-compose.docker.yml
    (env-file) intentionally differ in how secrets are supplied, but the
    worker/backend process-supervision contract must be identical."""

    def test_worker_command_and_healthcheck_match_across_files(self):
        configs = {name: _load_compose(name) for name in COMPOSE_FILES}
        workers = {name: cfg["services"]["worker"] for name, cfg in configs.items()}
        commands = {name: w["command"] for name, w in workers.items()}
        healthchecks = {name: w["healthcheck"] for name, w in workers.items()}

        assert len(set(map(tuple, commands.values()))) == 1, commands
        assert len(set(map(str, healthchecks.values()))) == 1, healthchecks

    def test_backend_healthcheck_matches_across_files(self):
        configs = {name: _load_compose(name) for name in COMPOSE_FILES}
        backends = {name: cfg["services"]["backend"] for name, cfg in configs.items()}
        healthchecks = {name: b["healthcheck"] for name, b in backends.items()}

        assert len(set(map(str, healthchecks.values()))) == 1, healthchecks


class TestDockerfileContract:
    @pytest.fixture
    def dockerfile_text(self) -> str:
        return (REPO_ROOT / "Dockerfile").read_text()

    def test_no_image_level_healthcheck(self, dockerfile_text):
        """Each role defines its own check in compose (see above) - a
        single image-level HEALTHCHECK is exactly what let the worker
        silently inherit the API's. Checks for the actual instruction (start
        of a non-comment line), not just the word, since the Dockerfile's
        own comments explain this in prose."""
        instructions = (
            line.strip() for line in dockerfile_text.splitlines() if line.strip()
        )
        assert not any(
            line.startswith("HEALTHCHECK")
            for line in instructions
            if not line.startswith("#")
        )

    def test_tini_is_the_entrypoint(self, dockerfile_text):
        assert 'ENTRYPOINT ["/sbin/tini", "--"]' in dockerfile_text

    def test_no_source_copy_into_app(self, dockerfile_text):
        """The app must be imported only from site-packages. A second copy
        under /app, ahead of site-packages on sys.path via WORKDIR /app, is
        what made every python invocation there recompile from source under
        a uid that doesn't own /app."""
        assert "COPY audio_to_subs/ /app" not in dockerfile_text

    def test_bytecode_is_precompiled(self, dockerfile_text):
        assert "compileall" in dockerfile_text
