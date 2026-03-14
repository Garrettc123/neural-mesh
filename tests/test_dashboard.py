"""Tests for the Flask dashboard."""

import json
import pytest
from unittest.mock import MagicMock

from pipeline.dashboard.app import create_app
from pipeline.history import HealingHistory, HealingEvent
from datetime import datetime, timezone


def _make_event(**kwargs):
    defaults = dict(
        repo="owner/repo",
        run_id=1,
        run_url="https://github.com/owner/repo/actions/runs/1",
        failure_type="test_failure",
        diagnosis_summary="Tests failed",
        healing_action="retry_failed_jobs",
        healing_success=True,
        detected_at=datetime.now(timezone.utc).isoformat(),
        healed_at=datetime.now(timezone.utc).isoformat(),
        recovery_seconds=42.0,
    )
    defaults.update(kwargs)
    return HealingEvent(**defaults)


@pytest.fixture
def history(tmp_path):
    h = HealingHistory(str(tmp_path / "history.json"))
    return h


@pytest.fixture
def mock_monitor():
    m = MagicMock()
    m.get_repos.return_value = ["owner/repo-a", "owner/repo-b"]
    return m


@pytest.fixture
def client(history, mock_monitor):
    app = create_app(history=history, monitor=mock_monitor)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_returns_ok(self, client):
        data = resp = client.get("/health").get_json()
        assert data["status"] == "ok"

    def test_health_has_timestamp(self, client):
        data = client.get("/health").get_json()
        assert "timestamp" in data


class TestIndexEndpoint:
    def test_index_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert b"Neural Mesh" in resp.data


class TestApiStatus:
    def test_status_returns_200(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200

    def test_status_has_expected_keys(self, client):
        data = client.get("/api/status").get_json()
        assert "repos_monitored" in data
        assert "healing_events_today" in data
        assert "success_rate_today" in data
        assert "mttr_display" in data
        assert "total_healing_events" in data

    def test_status_repos_count(self, client):
        data = client.get("/api/status").get_json()
        assert data["repos_monitored"] == 2

    def test_status_no_events(self, client):
        data = client.get("/api/status").get_json()
        assert data["total_healing_events"] == 0
        assert data["healing_events_today"] == 0

    def test_status_with_events(self, client, history):
        history.record(_make_event(run_id=1, healing_success=True))
        history.record(_make_event(run_id=2, healing_success=False))

        data = client.get("/api/status").get_json()
        assert data["total_healing_events"] == 2
        assert data["healing_events_today"] == 2

    def test_status_success_rate_calculation(self, client, history):
        history.record(_make_event(run_id=1, healing_success=True))
        history.record(_make_event(run_id=2, healing_success=True))
        history.record(_make_event(run_id=3, healing_success=False))

        data = client.get("/api/status").get_json()
        # 2 out of 3 = 66.7%
        assert abs(data["success_rate_today"] - 66.7) < 1

    def test_status_mttr_with_events(self, client, history):
        history.record(_make_event(run_id=1, healing_success=True, recovery_seconds=60.0))
        data = client.get("/api/status").get_json()
        assert data["mttr_seconds"] == pytest.approx(60.0)

    def test_status_no_history(self):
        app = create_app(history=None, monitor=None)
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/api/status")
            assert resp.status_code == 503


class TestApiHistory:
    def test_history_empty(self, client):
        data = client.get("/api/history").get_json()
        assert data == []

    def test_history_returns_events(self, client, history):
        history.record(_make_event(run_id=5))
        data = client.get("/api/history").get_json()
        assert len(data) == 1
        assert data[0]["run_id"] == 5

    def test_history_newest_first(self, client, history):
        history.record(_make_event(run_id=1))
        history.record(_make_event(run_id=2))
        history.record(_make_event(run_id=3))

        data = client.get("/api/history").get_json()
        assert data[0]["run_id"] == 3  # newest first


class TestApiRepos:
    def test_repos_empty(self, client):
        data = client.get("/api/repos").get_json()
        assert data == {}

    def test_repos_shows_per_repo_stats(self, client, history):
        history.record(_make_event(repo="owner/repo-a", run_id=1))
        history.record(_make_event(repo="owner/repo-b", run_id=2))

        data = client.get("/api/repos").get_json()
        assert "owner/repo-a" in data
        assert "owner/repo-b" in data

    def test_repos_no_history(self):
        app = create_app(history=None, monitor=None)
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/api/repos")
            assert resp.status_code == 200
            assert resp.get_json() == {}
