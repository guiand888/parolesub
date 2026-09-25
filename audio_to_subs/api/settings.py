"""Application settings using pydantic-settings."""

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_file_secret(path: str | None) -> str | None:
    """Read a stripped secret value from a file path, or None if unset/missing.

    Shared by the four *_FILE fallback validators and their corresponding
    convenience properties (admin_password, bazarr_api_key, mistral_api_key,
    session_secret) below.
    """
    if path is None:
        return None
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


class Settings(BaseSettings):
    """Application settings."""

    # Database
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:////data/parolesub.db",
        description="Database connection URL",
    )

    # Session — FILE must come before SECRET so it's available in the validator
    SESSION_SECRET_FILE: str | None = Field(
        default=None,
        description="Path to file containing session secret",
    )
    SESSION_SECRET: str | None = Field(
        default=None,
        description="Session secret key",
    )

    # Admin credentials — FILE must come before PASSWORD so it's available in the validator
    ADMIN_USERNAME: str | None = Field(
        default=None,
        description="Admin username for bootstrap",
    )
    ADMIN_PASSWORD_FILE: str | None = Field(
        default=None,
        description="Path to file containing admin password",
    )
    ADMIN_PASSWORD: str | None = Field(
        default=None,
        description="Admin password for bootstrap",
    )

    # Security
    BEHIND_TLS: bool = Field(
        default=False,
        description="Set Secure flag on cookies when behind TLS",
    )
    CORS_ORIGINS: list[str] = Field(
        default=["*"],
        description="CORS allowed origins. Use ['*'] for all or restrict to specific domains",
    )

    # Redis
    REDIS_URL: str = Field(
        default="redis://redis:6379/0",
        description="Redis connection URL",
    )

    # Bazarr — FILE must come before KEY so it's available in the validator
    BAZARR_URL: str | None = Field(
        default=None,
        description="Bazarr API URL",
    )
    BAZARR_API_KEY_FILE: str | None = Field(
        default=None,
        description="Path to file containing Bazarr API key",
    )
    BAZARR_API_KEY: str | None = Field(
        default=None,
        description="Bazarr API key",
    )
    BAZARR_TIMEOUT: float = Field(
        default=30.0,
        description="Bazarr API timeout in seconds",
    )

    # Mistral — FILE must come before KEY so it's available in the validator
    MISTRAL_API_KEY_FILE: str | None = Field(
        default=None,
        description="Path to file containing Mistral API key",
    )
    MISTRAL_API_KEY: str | None = Field(
        default=None,
        description="Mistral API key",
    )

    # Application
    DEBUG: bool = Field(
        default=False,
        description="Enable debug mode",
    )

    SUBTITLES_SAME_DIRECTORY: bool = Field(
        default=True,
        description="Save subtitles in same directory as source video files",
    )

    # Worker liveness. The worker claims jobs by subscribing to the jobs:new
    # Redis channel; WORKER_POLL_FALLBACK_SECONDS is only the safety-net poll
    # interval used when no notification arrives (Redis restart, missed
    # publish) rather than the normal wake mechanism.
    WORKER_POLL_FALLBACK_SECONDS: float = Field(
        default=30.0,
        description="Worker safety-net poll interval when idle, in seconds",
    )
    WORKER_HEARTBEAT_PATH: str = Field(
        default="/tmp/parolesub-worker.heartbeat",
        description="Path to the worker's liveness heartbeat file",
    )
    WORKER_HEARTBEAT_INTERVAL_SECONDS: float = Field(
        default=10.0,
        description="How often the worker touches its heartbeat file",
    )
    WORKER_HEARTBEAT_MAX_AGE_SECONDS: float = Field(
        default=60.0,
        description="Heartbeat age after which the worker healthcheck fails",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("ADMIN_PASSWORD", mode="before")
    @classmethod
    def load_admin_password_from_file(
        cls, v: str | None, info: ValidationInfo
    ) -> str | None:
        if v is not None:
            return v
        return _read_file_secret(info.data.get("ADMIN_PASSWORD_FILE"))

    @field_validator("BAZARR_API_KEY", mode="before")
    @classmethod
    def load_bazarr_api_key_from_file(
        cls, v: str | None, info: ValidationInfo
    ) -> str | None:
        if v is not None:
            return v
        return _read_file_secret(info.data.get("BAZARR_API_KEY_FILE"))

    @field_validator("MISTRAL_API_KEY", mode="before")
    @classmethod
    def load_mistral_api_key_from_file(
        cls, v: str | None, info: ValidationInfo
    ) -> str | None:
        if v is not None:
            return v
        return _read_file_secret(info.data.get("MISTRAL_API_KEY_FILE"))

    @field_validator("SESSION_SECRET", mode="before")
    @classmethod
    def load_session_secret_from_file(
        cls, v: str | None, info: ValidationInfo
    ) -> str | None:
        if v is not None:
            return v
        return _read_file_secret(info.data.get("SESSION_SECRET_FILE"))

    @model_validator(mode="after")
    def validate_session_secret_exists(self) -> "Settings":
        """Ensure SESSION_SECRET is available or can be loaded from file.

        Allows ``SESSION_SECRET`` to be ``None`` when ``SESSION_SECRET_FILE``
        is configured — the file may not exist yet at Settings instantiation
        time (first boot). ``_ensure_session_secret_file()`` in ``app.py``
        generates it before this validator runs in production. The hard error
        fires only when neither mechanism is configured at all.
        """
        if not self.SESSION_SECRET and not self.SESSION_SECRET_FILE:
            raise ValueError(
                "SESSION_SECRET must be set via environment variable or "
                "SESSION_SECRET_FILE must point to a valid file containing the secret"
            )
        return self

    @property
    def admin_password(self) -> str | None:
        """Get admin password (from env or file)."""
        if self.ADMIN_PASSWORD is not None:
            return self.ADMIN_PASSWORD
        return _read_file_secret(self.ADMIN_PASSWORD_FILE)

    @property
    def bazarr_api_key(self) -> str | None:
        """Get Bazarr API key (from env or file)."""
        if self.BAZARR_API_KEY is not None:
            return self.BAZARR_API_KEY
        return _read_file_secret(self.BAZARR_API_KEY_FILE)

    @property
    def mistral_api_key(self) -> str | None:
        """Get Mistral API key (from env or file)."""
        if self.MISTRAL_API_KEY is not None:
            return self.MISTRAL_API_KEY
        return _read_file_secret(self.MISTRAL_API_KEY_FILE)

    @property
    def session_secret(self) -> str | None:
        """Get session secret (from env or file)."""
        if self.SESSION_SECRET is not None:
            return self.SESSION_SECRET
        return _read_file_secret(self.SESSION_SECRET_FILE)


# Global settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get or create settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
