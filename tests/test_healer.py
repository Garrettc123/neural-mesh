"""Tests for the Healer automated fix engine."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from pipeline.healer import Healer
from pipeline.diagnose import DiagnosisResult, FailureType
from pipeline.monitor import WorkflowRun
from pipeline.history import HealingHistory, HealingEvent
from pipeline.notifier import Notifier
from pipeline.config import Config


def _make_run(conclusion="failure", run_id=42, repo="Garrettc123/test-repo"):
    data = {
        "id": run_id,
        "name": "CI",
        "status": "completed",
        "conclusion": conclusion,
        "html_url": f"https://github.com/{repo}/actions/runs/{run_id}",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:05:00Z",
        "head_branch": "main",
        "head_sha": "abc123",
        "workflow_id": 1,
        "_repo": repo,
    }
    return WorkflowRun(data)


def _make_diagnosis(failure_type: FailureType, summary="test failure summary"):
    return DiagnosisResult(
        failure_type=failure_type,
        confidence=0.8,
        matched_patterns=["AssertionError"],
        summary=summary,
    )


@pytest.fixture
def mock_config(tmp_path):
    cfg = Config()
    cfg.github_token = "fake-token"
    cfg.max_retry_attempts = 2
    cfg.history_file = str(tmp_path / "history.json")
    cfg.slack_webhook_url = None
    cfg.discord_webhook_url = None
    cfg.smtp_host = None
    return cfg


@pytest.fixture
def mock_monitor():
    m = MagicMock()
    m.rerun_failed_jobs.return_value = True
    return m


@pytest.fixture
def mock_notifier():
    return MagicMock(spec=Notifier)


@pytest.fixture
def history(mock_config):
    return HealingHistory(mock_config.history_file)


@pytest.fixture
def healer(mock_config, mock_monitor, mock_notifier, history):
    return Healer(
        monitor=mock_monitor,
        notifier=mock_notifier,
        history=history,
        cfg=mock_config,
    )


class TestHealerTestFailure:
    def test_heal_test_failure_records_event(self, healer, history):
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.TEST_FAILURE)
        healer.heal(run, diagnosis)
        events = history.all_events()
        assert len(events) == 1
        assert events[0].failure_type == "test_failure"

    def test_heal_test_failure_success(self, healer, mock_monitor):
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.TEST_FAILURE)
        result = healer.heal(run, diagnosis)
        assert result is True
        mock_monitor.rerun_failed_jobs.assert_called()

    def test_heal_test_failure_rerun_fails(self, healer, mock_monitor, history):
        mock_monitor.rerun_failed_jobs.return_value = False
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.TEST_FAILURE)

        with patch("time.sleep"):  # skip actual waits
            result = healer.heal(run, diagnosis)

        assert result is False
        events = history.all_events()
        assert events[0].healing_success is False


class TestHealerBuildError:
    def test_heal_build_error_retries(self, healer, mock_monitor):
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.BUILD_ERROR)
        result = healer.heal(run, diagnosis)
        assert result is True
        mock_monitor.rerun_failed_jobs.assert_called_once()


class TestHealerDependencyIssue:
    def test_heal_dependency_retries(self, healer, mock_monitor, history):
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.DEPENDENCY_ISSUE)
        result = healer.heal(run, diagnosis)
        assert result is True
        events = history.all_events()
        assert events[0].healing_action == "retry_dependency_fix"


class TestHealerInfraOutage:
    def test_heal_infra_retries_with_backoff(self, healer, mock_monitor, history):
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.INFRA_OUTAGE)

        with patch("time.sleep"):
            result = healer.heal(run, diagnosis)

        assert result is True
        events = history.all_events()
        assert events[0].healing_action == "retry_infra_backoff"

    def test_heal_infra_all_retries_fail(self, healer, mock_monitor, history):
        mock_monitor.rerun_failed_jobs.return_value = False
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.INFRA_OUTAGE)

        with patch("time.sleep"):
            result = healer.heal(run, diagnosis)

        assert result is False


class TestHealerConfigError:
    def test_heal_config_error_does_not_retry(self, healer, mock_monitor, history):
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.CONFIGURATION_ERROR)
        result = healer.heal(run, diagnosis)
        # Config errors should escalate, not retry
        assert result is False
        mock_monitor.rerun_failed_jobs.assert_not_called()
        events = history.all_events()
        assert events[0].healing_action == "escalate_config_error"


class TestHealerUnknown:
    def test_heal_unknown_retries_once(self, healer, mock_monitor, history):
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.UNKNOWN)
        result = healer.heal(run, diagnosis)
        assert result is True
        events = history.all_events()
        assert events[0].healing_action == "retry_once"


class TestHealerNotifications:
    def test_failure_alert_sent(self, healer, mock_notifier):
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.TEST_FAILURE)
        healer.heal(run, diagnosis)
        mock_notifier.send_failure_alert.assert_called_once()

    def test_healing_result_sent(self, healer, mock_notifier):
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.TEST_FAILURE)
        healer.heal(run, diagnosis)
        mock_notifier.send_healing_result.assert_called_once()

    def test_healing_started_sent(self, healer, mock_notifier):
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.TEST_FAILURE)
        healer.heal(run, diagnosis)
        mock_notifier.send_healing_started.assert_called_once()


class TestHealerRecoveryTime:
    def test_recovery_seconds_set_on_success(self, healer, history):
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.TEST_FAILURE)
        healer.heal(run, diagnosis)
        events = history.all_events()
        assert events[0].recovery_seconds is not None
        assert events[0].recovery_seconds >= 0

    def test_recovery_seconds_none_on_failure(self, healer, mock_monitor, history):
        mock_monitor.rerun_failed_jobs.return_value = False
        run = _make_run()
        diagnosis = _make_diagnosis(FailureType.BUILD_ERROR)
        healer.heal(run, diagnosis)
        events = history.all_events()
        assert events[0].recovery_seconds is None
