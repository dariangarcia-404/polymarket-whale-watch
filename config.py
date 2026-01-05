"""Configuration settings for Polymarket Whale Detector."""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
import os


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # API Configuration
    polymarket_gamma_api: str = "https://gamma-api.polymarket.com"
    polymarket_clob_api: str = "https://clob.polymarket.com"
    polymarket_data_api: str = "https://data-api.polymarket.com"
    
    # Detection Thresholds
    whale_trade_threshold_usd: float = Field(
        default=5000.0,
        description="Minimum trade size in USD to consider as whale activity"
    )
    new_wallet_age_hours: int = Field(
        default=72,
        description="Wallet age threshold in hours to consider 'new'"
    )
    suspicious_win_rate: float = Field(
        default=0.85,
        description="Win rate threshold to flag as suspicious"
    )
    min_trades_for_analysis: int = Field(
        default=3,
        description="Minimum trades before analyzing a wallet"
    )
    
    # Global Events Keywords (exclude sports)
    political_keywords: list[str] = Field(
        default=[
            # Politics
            "president", "election", "trump", "biden", "harris", "democrat",
            "republican", "congress", "senate", "governor", "primary",
            "nomination", "poll", "vote", "inauguration", "cabinet",
            "supreme court", "legislation", "bill", "impeach", "veto",
            "executive order", "administration", "campaign", "debate",
            "minister", "parliament", "brexit", "eu", "nato",
            # Geopolitical
            "war", "russia", "ukraine", "china", "taiwan", "israel", "iran",
            "ceasefire", "invasion", "military", "sanctions", "treaty",
            "nuclear", "missile", "coup", "assassination",
            # Economic
            "fed", "federal reserve", "interest rate", "inflation", "recession",
            "gdp", "tariff", "trade war", "stock market", "crash",
            # Tech/Business
            "elon", "musk", "tesla", "spacex", "openai", "google", "apple",
            "microsoft", "amazon", "meta", "facebook", "tiktok", "ban",
            "ceo", "merger", "acquisition", "ipo", "bankruptcy",
            # Crypto (but not price bets)
            "sec", "etf", "regulation", "binance", "coinbase",
            # Major Events
            "oscar", "emmy", "grammy", "nobel", "pandemic", "earthquake",
            "hurricane", "climate", "ai", "artificial intelligence"
        ]
    )
    
    # Sports keywords to EXCLUDE
    excluded_keywords: list[str] = Field(
        default=[
            "nba", "nfl", "mlb", "nhl", "mls", "fifa", "uefa", "premier league",
            "super bowl", "world series", "playoffs", "touchdown", "goal",
            "lakers", "celtics", "warriors", "cowboys", "patriots", "chiefs",
            "manchester", "liverpool", "chelsea", "arsenal", "barcelona", "madrid",
            "tennis", "golf", "boxing", "ufc", "mma", "f1", "formula",
            "olympics", "ncaa", "march madness", "slam dunk", "three-pointer"
        ]
    )
    
    # Monitoring Settings
    poll_interval_seconds: int = Field(
        default=30,
        description="How often to poll for new trades"
    )
    max_concurrent_requests: int = Field(
        default=10,
        description="Maximum concurrent API requests"
    )
    
    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///polymarket_whale_tracker.db"
    )
    
    # Desktop Notifications
    enable_desktop_notifications: bool = True
    enable_sound_alerts: bool = True
    
    # SMS Notifications (Twilio)
    enable_sms: bool = False
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    twilio_from_number: Optional[str] = None  # Your Twilio phone number
    twilio_to_number: Optional[str] = None    # Your personal phone number
    
    # Email Notifications
    enable_email: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: Optional[str] = None       # Your email address
    smtp_password: Optional[str] = None       # App password (not regular password)
    email_to: Optional[str] = None            # Where to send alerts
    
    # Pushover Notifications (phone push)
    enable_pushover: bool = False
    pushover_user_key: Optional[str] = None
    pushover_api_token: Optional[str] = None
    
    # Only send phone notifications for these severities
    phone_notify_severities: list[str] = Field(
        default=["HIGH", "CRITICAL"],
        description="Only send SMS/email for these severity levels"
    )
    
    # Logging
    log_level: str = "INFO"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

