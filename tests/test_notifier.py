"""Tests for the Notifier (Slack, Discord, email alerts)."""

import pytest
from unittest.mock import MagicMock, patch

from pipeline.notifier import Notifier
from pipeline.config import Config


@pytest.fixture
def cfg_with_slack():
    cfg = Config()
    cfg.slack_webhook_url = "https://hooks.slack.com/services/fake/fake/fake"
    cfg.discord_webhook_url = None
    cfg.smtp_host = None
    return cfg


@pytest.fixture
def cfg_with_discord():
    cfg = Config()
    cfg.slack_webhook_url = None
    cfg.discord_webhook_url = "https://discord.com/api/webhooks/fake/fake"
    cfg.smtp_host = None
    return cfg


@pytest.fixture
def cfg_no_webhooks():
    cfg = Config()
    cfg.slack_webhook_url = None
    cfg.discord_webhook_url = None
    cfg.smtp_host = None
    return cfg


@pytest.fixture
def cfg_with_email():
    cfg = Config()
    cfg.slack_webhook_url = None
    cfg.discord_webhook_url = None
    cfg.smtp_host = "smtp.example.com"
    cfg.smtp_port = 587
    cfg.smtp_user = "user@example.com"
    cfg.smtp_password = "secret"
    cfg.alert_email_to = "admin@example.com"
    return cfg


class TestSlackNotifier:
    def test_notify_slack_success(self, cfg_with_slack):
        notifier = Notifier(cfg_with_slack)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = notifier.notify_slack("Test subject", "Test body")
        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "Test subject" in call_kwargs[1]["json"]["text"]

    def test_notify_slack_http_error(self, cfg_with_slack):
        notifier = Notifier(cfg_with_slack)
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad request"
        with patch("requests.post", return_value=mock_resp):
            result = notifier.notify_slack("subject", "body")
        assert result is False

    def test_notify_slack_no_url(self, cfg_no_webhooks):
        notifier = Notifier(cfg_no_webhooks)
        result = notifier.notify_slack("subject", "body")
        assert result is False

    def test_notify_slack_network_exception(self, cfg_with_slack):
        import requests as req
        notifier = Notifier(cfg_with_slack)
        with patch("requests.post", side_effect=req.RequestException("timeout")):
            result = notifier.notify_slack("subject", "body")
        assert result is False


class TestDiscordNotifier:
    def test_notify_discord_success(self, cfg_with_discord):
        notifier = Notifier(cfg_with_discord)
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = notifier.notify_discord("Test subject", "Test body")
        assert result is True

    def test_notify_discord_200_also_ok(self, cfg_with_discord):
        notifier = Notifier(cfg_with_discord)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.post", return_value=mock_resp):
            result = notifier.notify_discord("subject", "body")
        assert result is True

    def test_notify_discord_no_url(self, cfg_no_webhooks):
        notifier = Notifier(cfg_no_webhooks)
        result = notifier.notify_discord("subject", "body")
        assert result is False

    def test_notify_discord_network_exception(self, cfg_with_discord):
        import requests as req
        notifier = Notifier(cfg_with_discord)
        with patch("requests.post", side_effect=req.RequestException("err")):
            result = notifier.notify_discord("subject", "body")
        assert result is False


class TestEmailNotifier:
    def test_notify_email_success(self, cfg_with_email):
        notifier = Notifier(cfg_with_email)
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)
        with patch("smtplib.SMTP", return_value=mock_smtp):
            result = notifier.notify_email("Test subject", "Test body")
        assert result is True
        mock_smtp.sendmail.assert_called_once()

    def test_notify_email_no_config(self, cfg_no_webhooks):
        notifier = Notifier(cfg_no_webhooks)
        result = notifier.notify_email("subject", "body")
        assert result is False

    def test_notify_email_smtp_exception(self, cfg_with_email):
        notifier = Notifier(cfg_with_email)
        with patch("smtplib.SMTP", side_effect=ConnectionRefusedError("refused")):
            result = notifier.notify_email("subject", "body")
        assert result is False


class TestHighLevelAlerts:
    def test_send_alert_calls_all_channels(self, cfg_no_webhooks):
        notifier = Notifier(cfg_no_webhooks)
        notifier.notify_slack = MagicMock(return_value=False)
        notifier.notify_discord = MagicMock(return_value=False)
        notifier.notify_email = MagicMock(return_value=False)

        notifier.send_alert("subject", "body")

        notifier.notify_slack.assert_called_once()
        notifier.notify_discord.assert_called_once()
        notifier.notify_email.assert_called_once()

    def test_send_failure_alert_includes_repo(self, cfg_no_webhooks):
        notifier = Notifier(cfg_no_webhooks)
        notifier.send_alert = MagicMock()

        notifier.send_failure_alert(
            repo="owner/repo",
            run_id=1,
            run_url="https://example.com/run/1",
            failure_type="test_failure",
            summary="Tests exploded",
        )

        call_body = notifier.send_alert.call_args[0][1]
        assert "owner/repo" in call_body
        assert "test_failure" in call_body

    def test_send_healing_result_success_icon(self, cfg_no_webhooks):
        notifier = Notifier(cfg_no_webhooks)
        notifier.send_alert = MagicMock()

        notifier.send_healing_result("owner/repo", 1, "retry_once", success=True)

        subject = notifier.send_alert.call_args[0][0]
        assert "✅" in subject or "succeeded" in subject.lower()

    def test_send_healing_result_failure_icon(self, cfg_no_webhooks):
        notifier = Notifier(cfg_no_webhooks)
        notifier.send_alert = MagicMock()

        notifier.send_healing_result("owner/repo", 1, "retry_once", success=False)

        subject = notifier.send_alert.call_args[0][0]
        assert "⚠️" in subject or "failed" in subject.lower()
