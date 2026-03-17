"""
Configuration management for the self-healing CI/CD pipeline.
All settings can be overridden via environment variables or a .env file.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    """Central configuration for the pipeline system."""

    # GitHub settings
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    github_owner: str = field(default_factory=lambda: os.getenv("GITHUB_OWNER", "Garrettc123"))

    # Monitoring settings
    poll_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("POLL_INTERVAL", "60"))
    )
    # How far back (in seconds) to look for runs on startup
    lookback_seconds: int = field(
        default_factory=lambda: int(os.getenv("LOOKBACK_SECONDS", "3600"))
    )

    # Notification webhooks (optional)
    slack_webhook_url: Optional[str] = field(
        default_factory=lambda: os.getenv("SLACK_WEBHOOK_URL")
    )
    discord_webhook_url: Optional[str] = field(
        default_factory=lambda: os.getenv("DISCORD_WEBHOOK_URL")
    )

    # Email settings (optional)
    smtp_host: Optional[str] = field(default_factory=lambda: os.getenv("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    smtp_user: Optional[str] = field(default_factory=lambda: os.getenv("SMTP_USER"))
    smtp_password: Optional[str] = field(default_factory=lambda: os.getenv("SMTP_PASSWORD"))
    alert_email_to: Optional[str] = field(default_factory=lambda: os.getenv("ALERT_EMAIL_TO"))

    # History / persistence
    history_file: str = field(
        default_factory=lambda: os.getenv("HISTORY_FILE", "healing_history.json")
    )

    # Dashboard
    dashboard_host: str = field(default_factory=lambda: os.getenv("DASHBOARD_HOST", "0.0.0.0"))
    dashboard_port: int = field(
        default_factory=lambda: int(os.getenv("DASHBOARD_PORT", "8080"))
    )
    dashboard_debug: bool = field(
        default_factory=lambda: os.getenv("DASHBOARD_DEBUG", "false").lower() == "true"
    )

    # Healing settings
    max_retry_attempts: int = field(
        default_factory=lambda: int(os.getenv("MAX_RETRY_ATTEMPTS", "3"))
    )


# Singleton instance used throughout the application
config = Config()
