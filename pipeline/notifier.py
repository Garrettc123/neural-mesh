"""
Notification system for the self-healing CI/CD pipeline.

Supports Slack, Discord, and SMTP email alerts.
All channels are optional; unconfigured channels are silently skipped.
"""

import logging
import smtplib
import textwrap
from email.mime.text import MIMEText
from typing import Optional

import requests

from .config import Config, config as default_config

logger = logging.getLogger(__name__)


class Notifier:
    """Sends pipeline event notifications to configured channels."""

    def __init__(self, cfg: Optional[Config] = None):
        self._cfg = cfg or default_config

    # ------------------------------------------------------------------
    # High-level interface
    # ------------------------------------------------------------------

    def send_alert(self, subject: str, body: str, emoji: str = "🔔") -> None:
        """Dispatch *subject* / *body* to all configured notification channels."""
        self.notify_slack(subject, body, emoji)
        self.notify_discord(subject, body, emoji)
        self.notify_email(subject, body)

    def send_failure_alert(
        self,
        repo: str,
        run_id: int,
        run_url: str,
        failure_type: str,
        summary: str,
    ) -> None:
        subject = f"❌ CI Failure: {repo}"
        body = (
            f"*Repository:* {repo}\n"
            f"*Run ID:* {run_id}\n"
            f"*URL:* {run_url}\n"
            f"*Failure type:* {failure_type}\n"
            f"*Diagnosis:* {summary}"
        )
        self.send_alert(subject, body, emoji="❌")

    def send_healing_started(
        self,
        repo: str,
        run_id: int,
        action: str,
    ) -> None:
        subject = f"🔧 Healing started: {repo}"
        body = (
            f"*Repository:* {repo}\n"
            f"*Run ID:* {run_id}\n"
            f"*Action:* {action}"
        )
        self.send_alert(subject, body, emoji="🔧")

    def send_healing_result(
        self,
        repo: str,
        run_id: int,
        action: str,
        success: bool,
    ) -> None:
        icon = "✅" if success else "⚠️"
        status = "succeeded" if success else "failed"
        subject = f"{icon} Healing {status}: {repo}"
        body = (
            f"*Repository:* {repo}\n"
            f"*Run ID:* {run_id}\n"
            f"*Action:* {action}\n"
            f"*Result:* {status.upper()}"
        )
        self.send_alert(subject, body, emoji=icon)

    # ------------------------------------------------------------------
    # Slack
    # ------------------------------------------------------------------

    def notify_slack(self, subject: str, body: str, emoji: str = "🔔") -> bool:
        """Post a message to Slack via incoming webhook. Returns True on success."""
        url = self._cfg.slack_webhook_url
        if not url:
            return False
        text = f"{emoji} *{subject}*\n{body}"
        try:
            resp = requests.post(url, json={"text": text}, timeout=10)
            if resp.status_code == 200:
                logger.debug("Slack notification sent: %s", subject)
                return True
            logger.warning("Slack returned %d: %s", resp.status_code, resp.text)
        except requests.RequestException as exc:
            logger.error("Slack notification failed: %s", exc)
        return False

    # ------------------------------------------------------------------
    # Discord
    # ------------------------------------------------------------------

    def notify_discord(self, subject: str, body: str, emoji: str = "🔔") -> bool:
        """Post a message to Discord via webhook. Returns True on success."""
        url = self._cfg.discord_webhook_url
        if not url:
            return False
        content = f"{emoji} **{subject}**\n{body}"
        try:
            resp = requests.post(url, json={"content": content}, timeout=10)
            if resp.status_code in (200, 204):
                logger.debug("Discord notification sent: %s", subject)
                return True
            logger.warning("Discord returned %d: %s", resp.status_code, resp.text)
        except requests.RequestException as exc:
            logger.error("Discord notification failed: %s", exc)
        return False

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------

    def notify_email(self, subject: str, body: str) -> bool:
        """Send an email alert via SMTP. Returns True on success."""
        cfg = self._cfg
        if not all([cfg.smtp_host, cfg.smtp_user, cfg.smtp_password, cfg.alert_email_to]):
            return False
        # Convert markdown-style bold to plain text
        plain_body = body.replace("*", "").replace("**", "")
        msg = MIMEText(textwrap.dedent(plain_body), "plain")
        msg["Subject"] = subject
        msg["From"] = cfg.smtp_user
        msg["To"] = cfg.alert_email_to
        try:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(cfg.smtp_user, cfg.smtp_password)
                server.sendmail(cfg.smtp_user, [cfg.alert_email_to], msg.as_string())
            logger.debug("Email notification sent: %s", subject)
            return True
        except Exception as exc:
            logger.error("Email notification failed: %s", exc)
        return False
