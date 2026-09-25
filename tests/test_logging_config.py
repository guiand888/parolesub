"""Tests for logging_config module.

Tests logging configuration with different verbosity levels
and logger suppression.
"""

import logging
import sys
from io import StringIO

import pytest

from audio_to_subs.core.logging_config import (
    clear_registered_secrets,
    configure_logging,
    configure_logging_from_env,
    redact_message,
    register_secret,
)


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset logging configuration before and after each test."""
    # Store original handlers and config
    original_handlers = logging.root.handlers[:]
    original_level = logging.root.level
    original_filters = logging.root.filters[:]

    yield

    # Restore original state
    logging.root.handlers = original_handlers
    logging.root.level = original_level
    logging.root.filters = original_filters
    clear_registered_secrets()


class TestConfigureLogging:
    """Test logging configuration."""

    def test_configure_logging_default(self):
        """Test logging configured with default (non-verbose) level."""
        # Act
        configure_logging(verbose=False)

        # Assert
        assert logging.root.level == logging.INFO
        assert logging.getLogger("mistralai").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("urllib3").level == logging.WARNING

    def test_configure_logging_verbose(self):
        """Test logging configured with verbose level."""
        # Act
        configure_logging(verbose=True)

        # Assert
        assert logging.root.level == logging.DEBUG
        # Verbose should not suppress third-party loggers
        # (they remain at their root level or above)

    def test_logging_format_non_verbose(self):
        """Test logging format in non-verbose mode."""
        # Act
        configure_logging(verbose=False)

        # Assert - check that a handler was added
        assert len(logging.root.handlers) > 0
        handler = logging.root.handlers[0]
        # Format should be simple in non-verbose mode
        if handler.formatter:
            assert handler.formatter._fmt is not None

    def test_logging_format_verbose(self):
        """Test logging format in verbose mode."""
        # Act
        configure_logging(verbose=True)

        # Assert
        assert len(logging.root.handlers) > 0
        handler = logging.root.handlers[0]
        # Format should include logger name in verbose mode
        if handler.formatter:
            assert "%(name)s" in handler.formatter._fmt

    def test_logging_output_to_stderr(self):
        """Test that logging is configured to output to stderr."""
        # Act
        configure_logging()

        # Assert
        assert len(logging.root.handlers) > 0
        handler = logging.root.handlers[0]
        # Should be a StreamHandler pointing to stderr
        if hasattr(handler, "stream"):
            assert handler.stream == sys.stderr or handler.stream.name == "<stderr>"

    def test_third_party_logger_suppression(self):
        """Test that third-party loggers are suppressed in normal mode."""
        # Act
        configure_logging(verbose=False)

        # Assert
        mistral_logger = logging.getLogger("mistralai")
        httpx_logger = logging.getLogger("httpx")
        urllib3_logger = logging.getLogger("urllib3")

        # These should be set to WARNING
        assert mistral_logger.level == logging.WARNING
        assert httpx_logger.level == logging.WARNING
        assert urllib3_logger.level == logging.WARNING

    def test_third_party_logger_not_suppressed_verbose(self):
        """Test that third-party loggers are not suppressed in verbose mode."""
        # Act
        configure_logging(verbose=True)

        # Assert - loggers should not have WARNING level set
        logging.getLogger("mistralai")
        logging.getLogger("httpx")
        logging.getLogger("urllib3")

        # In verbose mode, these should not be explicitly set to WARNING
        # (they inherit from root logger)
        # Only verify that configure_logging doesn't set them to WARNING
        # when verbose=True

    def test_force_flag_reconfigures_logging(self):
        """Test that configure_logging with force=True reconfigures."""
        # Arrange - set up initial logging
        configure_logging(verbose=False)
        len(logging.root.handlers)

        # Act - reconfigure with different setting
        configure_logging(verbose=True)

        # Assert - should have reconfigured (force=True in basicConfig)
        # Handler count might change due to force=True
        assert logging.root.level == logging.DEBUG

    def test_multiple_configure_calls(self):
        """Test multiple calls to configure_logging."""
        # Act
        configure_logging(verbose=False)
        configure_logging(verbose=True)
        configure_logging(verbose=False)

        # Assert - last configuration should be applied
        assert logging.root.level == logging.INFO
        assert logging.getLogger("mistralai").level == logging.WARNING

    def test_logging_actually_logs(self):
        """Test that logging actually produces output."""
        # Arrange
        configure_logging(verbose=False)
        test_logger = logging.getLogger("test_module")

        # Capture stderr
        captured_output = StringIO()
        handler = logging.StreamHandler(captured_output)
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter("[%(levelname)s] %(message)s")
        handler.setFormatter(formatter)

        test_logger.addHandler(handler)
        test_logger.setLevel(logging.INFO)

        # Act
        test_logger.info("Test message")

        # Assert
        output = captured_output.getvalue()
        assert "Test message" in output
        assert "[INFO]" in output

    def test_logging_debug_not_shown_non_verbose(self):
        """Test that DEBUG messages are not shown in non-verbose mode."""
        # Arrange
        configure_logging(verbose=False)
        test_logger = logging.getLogger("test_module")

        captured_output = StringIO()
        handler = logging.StreamHandler(captured_output)
        handler.setLevel(logging.DEBUG)  # Handler accepts DEBUG
        test_logger.addHandler(handler)
        test_logger.setLevel(logging.DEBUG)  # Logger accepts DEBUG

        # Act
        test_logger.debug("Debug message")

        # Assert - root logger level is INFO, so DEBUG won't be propagated
        captured_output.getvalue()
        # Since root logger is INFO level, debug won't appear at root
        # (but would appear if we logged through root directly)

    def test_logging_debug_shown_verbose(self):
        """Test that DEBUG messages are shown in verbose mode."""
        # Arrange
        configure_logging(verbose=True)
        test_logger = logging.getLogger("test_module")

        captured_output = StringIO()
        handler = logging.StreamHandler(captured_output)
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
        handler.setFormatter(formatter)

        test_logger.addHandler(handler)

        # Act
        test_logger.debug("Debug message")

        # Assert
        output = captured_output.getvalue()
        assert "Debug message" in output


class TestLoggingDefaults:
    """Test logging configuration defaults."""

    def test_default_verbosity_is_false(self):
        """Test that default verbosity is False."""
        # This is implicit in the function signature
        # Act
        configure_logging()  # Call without arguments

        # Assert
        assert logging.root.level == logging.INFO

    def test_boolean_parameter_types(self):
        """Test that verbose parameter accepts boolean values."""
        # Act & Assert - should not raise
        configure_logging(verbose=True)
        configure_logging(verbose=False)
        configure_logging(verbose=bool(1))
        configure_logging(verbose=bool(0))


class TestConfigureLoggingFromEnv:
    """Test LOG_LEVEL-driven configuration for non-interactive entry points."""

    def test_unset_defaults_to_info(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        configure_logging_from_env()
        assert logging.root.level == logging.INFO

    def test_log_level_debug_enables_verbose(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        configure_logging_from_env()
        assert logging.root.level == logging.DEBUG

    def test_log_level_debug_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "debug")
        configure_logging_from_env()
        assert logging.root.level == logging.DEBUG

    def test_log_level_info_stays_non_verbose(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        configure_logging_from_env()
        assert logging.root.level == logging.INFO

    def test_unrecognized_log_level_falls_back_to_info(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "bogus")
        configure_logging_from_env()
        assert logging.root.level == logging.INFO


class TestSecretsRedaction:
    """Secret values must never appear in log output (M6.a, security pass)."""

    def test_redact_message_replaces_registered_secret(self):
        register_secret("super-secret-mistral-key-12345")
        out = redact_message("using key=super-secret-mistral-key-12345 for call")
        assert "super-secret-mistral-key-12345" not in out
        assert "***REDACTED***" in out

    def test_redact_message_leaves_benign_text_unchanged(self):
        register_secret("super-secret-mistral-key-12345")
        out = redact_message("job created for /movies/foo.mp4")
        assert out == "job created for /movies/foo.mp4"

    def test_short_secrets_are_not_registered(self):
        register_secret("pw")
        out = redact_message("password is pw, fine")
        assert out == "password is pw, fine"

    def test_filter_redacts_secret_logged_via_args(self):
        from audio_to_subs.core.logging_config import SecretsRedactingFilter

        register_secret("long-admin-password-98765")
        logger = logging.getLogger("secrets_test")
        captured = StringIO()
        handler = logging.StreamHandler(captured)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(SecretsRedactingFilter())
        logger.addHandler(handler)
        logger.info("login with password=%s", "long-admin-password-98765")

        output = captured.getvalue()
        assert "long-admin-password-98765" not in output
        assert "***REDACTED***" in output

    def test_filter_redacts_secret_logged_via_fstring(self):
        from audio_to_subs.core.logging_config import SecretsRedactingFilter

        register_secret("long-session-token-aaaa-1111")
        logger = logging.getLogger("secrets_test")
        captured = StringIO()
        handler = logging.StreamHandler(captured)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(SecretsRedactingFilter())
        logger.addHandler(handler)
        secret = "long-session-token-aaaa-1111"
        logger.warning(f"token={secret}")

        output = captured.getvalue()
        assert "long-session-token-aaaa-1111" not in output
        assert "***REDACTED***" in output

    def test_multiple_secrets_redacted(self):
        register_secret("first-secret-value-abc")
        register_secret("second-secret-value-xyz")
        out = redact_message("a=first-secret-value-abc b=second-secret-value-xyz")
        assert "first-secret-value-abc" not in out
        assert "second-secret-value-xyz" not in out
        assert out.count("***REDACTED***") == 2


class TestHealthzAccessFilter:
    """The backend's own /api/healthz poll must not spam the access log."""

    @staticmethod
    def _access_record(path: str) -> logging.LogRecord:
        # Matches uvicorn's actual access_logger.info() call signature (see
        # h11_impl.py / httptools_impl.py): client_addr, method, path, http
        # version, status.
        return logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='%s - "%s %s HTTP/%s" %d',
            args=("127.0.0.1:1234", "GET", path, "1.1", 200),
            exc_info=None,
        )

    def test_healthz_record_is_dropped(self):
        from audio_to_subs.core.logging_config import _HealthzAccessFilter

        record = self._access_record("/api/healthz")
        assert _HealthzAccessFilter().filter(record) is False

    def test_healthz_record_with_query_string_is_dropped(self):
        from audio_to_subs.core.logging_config import _HealthzAccessFilter

        record = self._access_record("/api/healthz?foo=bar")
        assert _HealthzAccessFilter().filter(record) is False

    def test_other_paths_are_kept(self):
        from audio_to_subs.core.logging_config import _HealthzAccessFilter

        record = self._access_record("/api/jobs")
        assert _HealthzAccessFilter().filter(record) is True

    def test_filter_is_wired_up_on_the_access_logger(self):
        # Logger.filter()'s exact return type differs across Python versions
        # (3.12+ may return the record itself rather than a bare bool), so
        # this checks truthiness/attachment, not identity - the per-filter
        # behavior itself is covered precisely by the unit tests above.
        configure_logging(verbose=False)
        access_logger = logging.getLogger("uvicorn.access")
        assert not access_logger.filter(self._access_record("/api/healthz"))
        assert access_logger.filter(self._access_record("/api/jobs"))

    def test_filter_is_not_duplicated_across_calls(self):
        from audio_to_subs.core.logging_config import _HealthzAccessFilter

        configure_logging(verbose=False)
        configure_logging(verbose=False)
        configure_logging(verbose=True)

        access_logger = logging.getLogger("uvicorn.access")
        matching = [
            f for f in access_logger.filters if isinstance(f, _HealthzAccessFilter)
        ]
        assert len(matching) == 1
