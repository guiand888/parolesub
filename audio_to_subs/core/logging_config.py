"""Logging configuration for parolesub.

Provides structured logging setup with configurable verbosity levels and a
redaction filter that scrubs secret values (API keys, session secret, admin
password) from every log record so credentials never leak into logs.
"""

import logging
import os
import sys

# Secrets shorter than this are not redacted: a short literal (e.g. a 2-3 char
# dev password) would cause far too many false-positive redactions in normal
# log lines. Real secrets (Mistral key, session token, file-backed passwords)
# are always long enough to clear this bar.
_MIN_REDACT_LEN = 8

# Replacement token substituted for any redacted secret value.
_REDACTED = "***REDACTED***"

# Module-level registry of secret values to scrub. Populated lazily from
# Settings so both the API and worker processes redact the same secrets.
_secret_values: set[str] = set()


def register_secret(value: str | None) -> None:
    """Register a secret value to be redacted from all log records.

    Args:
        value: The literal secret value. ``None``/empty/short values are
            ignored (see ``_MIN_REDACT_LEN``).
    """
    if value and len(value) >= _MIN_REDACT_LEN:
        _secret_values.add(value)


def register_secrets_from_settings(settings: object) -> None:
    """Register all secret values exposed by the application Settings.

    Args:
        settings: The ``Settings`` instance; its ``session_secret``,
            ``mistral_api_key``, ``admin_password`` and ``bazarr_api_key``
            properties are read (each may be ``None``).
    """
    for attr in (
        "session_secret",
        "mistral_api_key",
        "admin_password",
        "bazarr_api_key",
    ):
        register_secret(getattr(settings, attr, None))


def clear_registered_secrets() -> None:
    """Clear the registered secret values (used by tests)."""
    _secret_values.clear()


def redact_message(message: str) -> str:
    """Return ``message`` with any registered secret value replaced.

    Args:
        message: The log message (already formatted).

    Returns:
        The message with secret literals scrubbed to ``***REDACTED***``.
    """
    for secret in _secret_values:
        if secret in message:
            message = message.replace(secret, _REDACTED)
    return message


class SecretsRedactingFilter(logging.Filter):
    """Logging filter that redacts registered secret values.

    Rewrites ``record.msg``/``record.args`` so the formatted message never
    contains a literal secret, regardless of how the caller logged it
    (``logger.info("key=%s", key)`` or ``logger.info(f"key={key}")``).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact secrets from the record, always returning ``True``.

        Args:
            record: The log record to sanitize.

        Returns:
            ``True`` so the record is always emitted (after redaction).
        """
        try:
            rendered = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        redacted = redact_message(rendered)
        if redacted != rendered:
            record.msg = redacted
            record.args = None
        return True


class _HealthzAccessFilter(logging.Filter):
    """Drops uvicorn access-log records for GET /api/healthz.

    The backend's own Docker healthcheck polls this endpoint every 10s,
    which otherwise fills the log with nothing but that one line forever.
    uvicorn's access logger always calls
    ``logger.info('%s - "%s %s HTTP/%s" %d', client_addr, method, path, ...)``
    (see uvicorn's h11/httptools protocol implementations), so the request
    path is ``record.args[2]``, with any query string still attached.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3:
            return True
        path = str(args[2]).split("?", 1)[0]
        return path != "/api/healthz"


def configure_logging(verbose: bool = False) -> None:
    """Configure Python logging for parolesub.

    Args:
        verbose: Enable DEBUG level logging for detailed output.
                If False, uses INFO level for normal operation.
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = (
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        if verbose
        else "%(asctime)s [%(levelname)s] %(message)s"
    )

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format=log_format,
        stream=sys.stderr,
        force=True,
    )

    # Redact secrets from every handler on the root logger. Filters are added
    # to handlers (not just the logger) because a logger's own filters do NOT
    # run for records that merely propagate up from a child logger — only the
    # handler's filters do.
    redacting_filter = SecretsRedactingFilter()
    for handler in logging.root.handlers:
        handler.addFilter(redacting_filter)
    logging.getLogger().addFilter(redacting_filter)

    # Suppress noisy third-party loggers unless in verbose mode
    if not verbose:
        logging.getLogger("mistralai").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)

    # Drop the backend's own healthcheck from the access log: polled every
    # 10s, forever, and was otherwise the only thing in the log on an idle
    # deployment. Attached directly to the logger object (not to a
    # handler) so it survives uvicorn's own logging.config.dictConfig call,
    # which replaces a configured logger's handlers but never clears filters
    # already present on the logger itself. Unconditional (not gated by
    # `verbose`): DEBUG mode wants more logging elsewhere, not a flood of one
    # repeated health probe. Guarded so repeated configure_logging() calls
    # (every test in this module does one) don't pile up duplicate filters.
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _HealthzAccessFilter) for f in access_logger.filters):
        access_logger.addFilter(_HealthzAccessFilter())


def configure_logging_from_env() -> None:
    """Configure logging using the LOG_LEVEL environment variable.

    Entry point for long-running processes (API, worker) that have no
    interactive --verbose flag. LOG_LEVEL=DEBUG enables verbose logging;
    any other value (including unset) falls back to normal INFO-level
    operation. See configure_logging() for the underlying behavior.
    """
    verbose = os.environ.get("LOG_LEVEL", "INFO").strip().upper() == "DEBUG"
    configure_logging(verbose=verbose)
    _register_secrets_from_app_settings()


def _register_secrets_from_app_settings() -> None:
    """Best-effort registration of secret values from app Settings.

    Safe to call outside the web app (e.g. in unit tests): if Settings can't
    be imported or constructed, registration is simply skipped.
    """
    try:
        from audio_to_subs.api.settings import get_settings
    except Exception:  # pragma: no cover - defensive
        return
    try:
        register_secrets_from_settings(get_settings())
    except Exception:  # pragma: no cover - defensive
        return
