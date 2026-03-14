"""Tests for WorkflowMonitor."""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from pipeline.monitor import WorkflowMonitor, WorkflowRun
from pipeline.config import Config


@pytest.fixture
def mock_config():
    cfg = Config()
    cfg.github_token = "fake-token"
    cfg.github_owner = "Garrettc123"
    cfg.lookback_seconds = 3600
    cfg.max_retry_attempts = 3
    return cfg


@pytest.fixture
def monitor(mock_config):
    return WorkflowMonitor(cfg=mock_config)


class TestWorkflowRun:
    def _make_run(self, **kwargs):
        defaults = {
            "id": 1,
            "name": "CI",
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/owner/repo/actions/runs/1",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:05:00Z",
            "head_branch": "main",
            "head_sha": "abc123",
            "workflow_id": 10,
            "_repo": "owner/repo",
        }
        defaults.update(kwargs)
        return WorkflowRun(defaults)

    def test_failed_on_failure_conclusion(self):
        run = self._make_run(conclusion="failure")
        assert run.failed is True

    def test_failed_on_timed_out(self):
        run = self._make_run(conclusion="timed_out")
        assert run.failed is True

    def test_failed_on_startup_failure(self):
        run = self._make_run(conclusion="startup_failure")
        assert run.failed is True

    def test_not_failed_on_success(self):
        run = self._make_run(conclusion="success")
        assert run.failed is False

    def test_not_failed_on_cancelled(self):
        run = self._make_run(conclusion="cancelled")
        assert run.failed is False

    def test_completed_true(self):
        run = self._make_run(status="completed")
        assert run.completed is True

    def test_completed_false(self):
        run = self._make_run(status="in_progress")
        assert run.completed is False

    def test_repo_field(self):
        run = self._make_run(_repo="owner/my-repo")
        assert run.repo == "owner/my-repo"

    def test_repr(self):
        run = self._make_run()
        r = repr(run)
        assert "WorkflowRun" in r
        assert "failure" in r


class TestWorkflowMonitorGetRepos:
    def test_get_repos_success(self, monitor):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {"full_name": "Garrettc123/repo-a"},
            {"full_name": "Garrettc123/repo-b"},
        ]

        with patch.object(monitor._session, "get", return_value=mock_response):
            repos = monitor.get_repos()

        assert "Garrettc123/repo-a" in repos
        assert "Garrettc123/repo-b" in repos

    def test_get_repos_empty(self, monitor):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = []

        with patch.object(monitor._session, "get", return_value=mock_response):
            repos = monitor.get_repos()

        assert repos == []

    def test_get_repos_network_error(self, monitor):
        import requests
        with patch.object(monitor._session, "get", side_effect=requests.RequestException("timeout")):
            repos = monitor.get_repos()
        assert repos == []


class TestWorkflowMonitorGetRecentRuns:
    def _make_run_data(self, run_id=1, conclusion="failure", repo="owner/repo"):
        return {
            "id": run_id,
            "name": "CI",
            "status": "completed",
            "conclusion": conclusion,
            "html_url": f"https://github.com/{repo}/actions/runs/{run_id}",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:05:00Z",
            "head_branch": "main",
            "head_sha": "abc",
            "workflow_id": 1,
        }

    def test_get_recent_runs_returns_workflow_runs(self, monitor):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "workflow_runs": [
                self._make_run_data(1, "failure"),
                self._make_run_data(2, "success"),
            ]
        }

        with patch.object(monitor._session, "get", return_value=mock_response):
            runs = monitor.get_recent_runs("owner/repo")

        assert len(runs) == 2
        assert isinstance(runs[0], WorkflowRun)
        assert runs[0].repo == "owner/repo"

    def test_get_recent_runs_network_error(self, monitor):
        import requests
        with patch.object(monitor._session, "get", side_effect=requests.RequestException("err")):
            runs = monitor.get_recent_runs("owner/repo")
        assert runs == []


class TestWorkflowMonitorCheckFailures:
    def test_check_for_new_failures_deduplicates(self, monitor):
        failed_run_data = {
            "id": 99,
            "name": "CI",
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/o/r/actions/runs/99",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:05:00Z",
            "head_branch": "main",
            "head_sha": "abc",
            "workflow_id": 1,
            "_repo": "owner/repo",
        }
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"workflow_runs": [failed_run_data]}

        with patch.object(monitor._session, "get", return_value=mock_response):
            first = monitor.check_for_new_failures(["owner/repo"])
            second = monitor.check_for_new_failures(["owner/repo"])

        assert len(first) == 1
        assert len(second) == 0  # deduplicated

    def test_check_for_new_failures_ignores_success(self, monitor):
        success_run_data = {
            "id": 100,
            "name": "CI",
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.com/o/r/actions/runs/100",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:05:00Z",
            "head_branch": "main",
            "head_sha": "abc",
            "workflow_id": 1,
            "_repo": "owner/repo",
        }
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"workflow_runs": [success_run_data]}

        with patch.object(monitor._session, "get", return_value=mock_response):
            failures = monitor.check_for_new_failures(["owner/repo"])

        assert failures == []


class TestWorkflowMonitorRerun:
    def test_rerun_failed_jobs_success(self, monitor):
        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch.object(monitor._session, "post", return_value=mock_response):
            result = monitor.rerun_failed_jobs("owner/repo", 123)

        assert result is True

    def test_rerun_failed_jobs_204(self, monitor):
        mock_response = MagicMock()
        mock_response.status_code = 204

        with patch.object(monitor._session, "post", return_value=mock_response):
            result = monitor.rerun_failed_jobs("owner/repo", 123)

        assert result is True

    def test_rerun_failed_jobs_api_error(self, monitor):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        with patch.object(monitor._session, "post", return_value=mock_response):
            result = monitor.rerun_failed_jobs("owner/repo", 123)

        assert result is False

    def test_rerun_failed_jobs_network_exception(self, monitor):
        import requests
        with patch.object(monitor._session, "post", side_effect=requests.RequestException("err")):
            result = monitor.rerun_failed_jobs("owner/repo", 123)
        assert result is False
