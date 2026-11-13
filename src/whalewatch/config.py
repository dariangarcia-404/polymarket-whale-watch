"""Settings, read from environment variables or a .env file."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Polymarket public APIs (no key needed)
    data_api: str = "https://data-api.polymarket.com/v2"
    gamma_api: str = "https://gamma-api.polymarket.com"

    # Detection
    whale_trade_threshold_usd: float = Field(5000.0, gt=0)
    new_wallet_age_hours: float = Field(72.0, gt=0)
    longshot_price: float = Field(0.10, gt=0, lt=0.5)
    pre_resolution_hours: float = Field(48.0, gt=0)
    quiet_hours_utc: tuple[int, int] = (5, 10)  # 1 to 6 a.m. US Eastern (EST)
    market_filter: Literal["events", "all"] = "events"

    # Polling
    poll_interval_seconds: float = Field(30.0, ge=5)
    max_pages_per_poll: int = Field(5, ge=1, le=50)
    backfill_minutes: float = Field(10.0, ge=0)  # on startup, ignore trades older than this

    database_url: str = "sqlite+aiosqlite:///whalewatch.db"
    log_level: str = "INFO"

    # Notifications
    enable_desktop_notifications: bool = True
    notify_severities: list[Severity] = ["HIGH", "CRITICAL"]  # for SMS, email, Pushover

    enable_sms: bool = False
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    twilio_to_number: str | None = None

    enable_email: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    email_to: str | None = None

    enable_pushover: bool = False
    pushover_user_key: str | None = None
    pushover_api_token: str | None = None
