"""Load and validate settings from environment variables (and .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    token_factory_base_url: str
    token_factory_api_key: str
    model_fast: str
    model_reasoning: str
    tavily_api_key: str
    app_secret_key: str
    data_dir: Path
    cache_enabled: bool
    cache_dir: Path
    cookie_secure: bool = False      # true behind HTTPS in production
    guest_ttl_days: int = 7          # guest accounts and their data are deleted after this
    guest_ultra_per_day: int = 10    # "Think harder" (Ultra) turns per guest per day
    steam_api_key: str = ""          # optional: Steam Web API key for importing public Steam libraries
    model_balanced: str = ""         # Nemotron Super, writes answers from sources; empty = use MODEL_FAST
    public_base_url: str = ""        # e.g. https://lantern.example.com (Steam sign-in return address)

    def require_secret_key(self) -> None:
        """The session-signing key must be set and long enough to resist guessing."""
        self.require("app_secret_key")
        if len(self.app_secret_key) < 32:
            raise ConfigError(
                "APP_SECRET_KEY must be at least 32 characters. Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )

    def require(self, *names: str) -> None:
        """Raise a ConfigError naming every env var in `names` that is empty.

        Names are field names (e.g. "model_fast"); the error shows the env var
        (e.g. MODEL_FAST) so the user knows exactly what to set in .env.
        """
        missing = [n.upper() for n in names if not str(getattr(self, n)).strip()]
        if missing:
            raise ConfigError(
                "Missing required setting(s): "
                + ", ".join(missing)
                + ". Set them in .env (see .env.example)."
            )


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Read settings from the environment, loading `env_file` first if it exists.

    Real environment variables take precedence over values in the file.
    """
    if env_file and Path(env_file).exists():
        load_dotenv(env_file, override=False)

    def get(name: str, default: str = "") -> str:
        return os.environ.get(name, default).strip()

    return Settings(
        token_factory_base_url=get("TOKEN_FACTORY_BASE_URL"),
        token_factory_api_key=get("TOKEN_FACTORY_API_KEY"),
        model_fast=get("MODEL_FAST"),
        model_reasoning=get("MODEL_REASONING"),
        tavily_api_key=get("TAVILY_API_KEY"),
        app_secret_key=get("APP_SECRET_KEY"),
        data_dir=Path(get("DATA_DIR", "./data")),
        cache_enabled=_as_bool(get("CACHE_ENABLED", "true")),
        cache_dir=Path(get("CACHE_DIR", "./.cache")),
        cookie_secure=_as_bool(get("COOKIE_SECURE", "false")),
        guest_ttl_days=_as_int("GUEST_TTL_DAYS", get("GUEST_TTL_DAYS", "7")),
        guest_ultra_per_day=_as_int("GUEST_ULTRA_PER_DAY", get("GUEST_ULTRA_PER_DAY", "10")),
        steam_api_key=get("STEAM_API_KEY"),
        public_base_url=get("PUBLIC_BASE_URL").rstrip("/"),
        model_balanced=get("MODEL_BALANCED"),
    )


def _as_int(name: str, value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {value!r}.") from None
    if number < 0:
        raise ConfigError(f"{name} must not be negative.")
    return number
