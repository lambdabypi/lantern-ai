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
    guest_messages_per_day: int = 30     # chat messages per guest per day (0 = unlimited)
    user_messages_per_day: int = 100     # chat messages per signed-in player per day (0 = unlimited)
    site_messages_per_day: int = 300     # all players together, per day (0 = unlimited)
    messages_per_ip_per_day: int = 100   # messages from one network per day, all its users together (0 = unlimited)
    site_tavily_credits_per_day: int = 60  # Tavily credits the whole site may spend per day (0 = unlimited)
    guests_per_ip_per_day: int = 5   # new guest accounts per network per day (0 = unlimited)
    site_model_dollars_per_day: float = 1.0  # estimated Token Factory spend the whole site may use per day (0 = no cap)
    user_ultra_per_day: int = 20     # "Think harder" (Ultra) turns per signed-in user per day (0 = unlimited)
    site_ultra_per_day: int = 40     # "Think harder" turns for everyone together per day (0 = unlimited)
    site_guests_per_day: int = 300   # new guest sessions for everyone together per day (0 = unlimited)
    signups_per_ip_per_day: int = 5  # new accounts per network per day (0 = unlimited)
    site_signups_per_day: int = 100  # new accounts for everyone together per day (0 = unlimited)
    logins_per_ip_per_day: int = 100  # sign-in attempts per network per day (0 = unlimited)
    trusted_proxy_hops: int = 1      # proxies that append to X-Forwarded-For (Cloud Run: 1); 0 = use the socket peer
    cron_secret: str = ""            # shared secret for /internal/recalls/run (Cloud Scheduler); empty = disabled
    model_balanced: str = ""         # Nemotron Super, writes answers from sources; empty = use MODEL_FAST
    model_vision: str = ""           # Nemotron Super VL, reads photos of product labels; empty = photos are off
    public_base_url: str = ""        # e.g. https://lantern.example.com (push notification contact URL)
    vapid_private_key: str = ""      # push notifications: base64 of a VAPID PEM key (python -m app.push); empty = off

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
        guest_messages_per_day=_as_int("GUEST_MESSAGES_PER_DAY", get("GUEST_MESSAGES_PER_DAY", "30")),
        user_messages_per_day=_as_int("USER_MESSAGES_PER_DAY", get("USER_MESSAGES_PER_DAY", "100")),
        site_messages_per_day=_as_int("SITE_MESSAGES_PER_DAY", get("SITE_MESSAGES_PER_DAY", "300")),
        messages_per_ip_per_day=_as_int("MESSAGES_PER_IP_PER_DAY", get("MESSAGES_PER_IP_PER_DAY", "100")),
        site_tavily_credits_per_day=_as_int("SITE_TAVILY_CREDITS_PER_DAY", get("SITE_TAVILY_CREDITS_PER_DAY", "150")),
        guests_per_ip_per_day=_as_int("GUESTS_PER_IP_PER_DAY", get("GUESTS_PER_IP_PER_DAY", "5")),
        site_model_dollars_per_day=_as_float("SITE_MODEL_DOLLARS_PER_DAY", get("SITE_MODEL_DOLLARS_PER_DAY", "1.0")),
        user_ultra_per_day=_as_int("USER_ULTRA_PER_DAY", get("USER_ULTRA_PER_DAY", "20")),
        site_ultra_per_day=_as_int("SITE_ULTRA_PER_DAY", get("SITE_ULTRA_PER_DAY", "40")),
        site_guests_per_day=_as_int("SITE_GUESTS_PER_DAY", get("SITE_GUESTS_PER_DAY", "300")),
        signups_per_ip_per_day=_as_int("SIGNUPS_PER_IP_PER_DAY", get("SIGNUPS_PER_IP_PER_DAY", "5")),
        site_signups_per_day=_as_int("SITE_SIGNUPS_PER_DAY", get("SITE_SIGNUPS_PER_DAY", "100")),
        logins_per_ip_per_day=_as_int("LOGINS_PER_IP_PER_DAY", get("LOGINS_PER_IP_PER_DAY", "100")),
        trusted_proxy_hops=_as_int("TRUSTED_PROXY_HOPS", get("TRUSTED_PROXY_HOPS", "1")),
        cron_secret=get("CRON_SECRET"),
        public_base_url=get("PUBLIC_BASE_URL").rstrip("/"),
        model_balanced=get("MODEL_BALANCED"),
        model_vision=get("MODEL_VISION"),
        vapid_private_key=get("VAPID_PRIVATE_KEY"),
    )


def _as_float(name: str, value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {value!r}.") from None
    if number < 0 or number != number:
        raise ConfigError(f"{name} must not be negative.")
    return number


def _as_int(name: str, value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {value!r}.") from None
    if number < 0:
        raise ConfigError(f"{name} must not be negative.")
    return number
