"""Tests for HealingHistory persistence and metrics."""

import json
import os
import pytest
from datetime import datetime, timezone, timedelta

from pipeline.history import HealingHistory, HealingEvent


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
        recovery_seconds=45.0,
    )
    defaults.update(kwargs)
    return HealingEvent(**defaults)


@pytest.fixture
def tmp_history(tmp_path):
    return HealingHistory(str(tmp_path / "history.json"))


class TestHealingHistoryRecord:
    def test_record_adds_to_in_memory(self, tmp_history):
        event = _make_event()
        tmp_history.record(event)
        assert len(tmp_history.all_events()) == 1

    def test_record_persists_to_file(self, tmp_path):
        fpath = str(tmp_path / "history.json")
        h = HealingHistory(fpath)
        event = _make_event()
        h.record(event)

        assert os.path.exists(fpath)
        with open(fpath) as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["repo"] == "owner/repo"

    def test_record_multiple_events(self, tmp_history):
        for i in range(5):
            tmp_history.record(_make_event(run_id=i))
        assert len(tmp_history.all_events()) == 5


class TestHealingHistoryPersistence:
    def test_reload_reads_existing_file(self, tmp_path):
        fpath = str(tmp_path / "history.json")

        # Write first instance
        h1 = HealingHistory(fpath)
        h1.record(_make_event(run_id=10))
        h1.record(_make_event(run_id=11))

        # Load second instance from same file
        h2 = HealingHistory(fpath)
        events = h2.all_events()
        assert len(events) == 2
        assert {e.run_id for e in events} == {10, 11}

    def test_malformed_lines_skipped(self, tmp_path):
        fpath = str(tmp_path / "history.json")
        with open(fpath, "w") as f:
            f.write("{invalid json}\n")
            f.write(json.dumps(_make_event(run_id=99).as_dict()) + "\n")

        h = HealingHistory(fpath)
        assert len(h.all_events()) == 1


class TestHealingHistoryEventsToday:
    def test_events_today_includes_current_day(self, tmp_history):
        event = _make_event(detected_at=datetime.now(timezone.utc).isoformat())
        tmp_history.record(event)
        assert len(tmp_history.events_today()) == 1

    def test_events_today_excludes_yesterday(self, tmp_history):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        event = _make_event(detected_at=yesterday)
        tmp_history.record(event)
        assert len(tmp_history.events_today()) == 0


class TestHealingHistoryMetrics:
    def test_success_rate_all_success(self, tmp_history):
        for i in range(4):
            tmp_history.record(_make_event(run_id=i, healing_success=True))
        assert tmp_history.success_rate() == 1.0

    def test_success_rate_all_failure(self, tmp_history):
        for i in range(3):
            tmp_history.record(_make_event(run_id=i, healing_success=False))
        assert tmp_history.success_rate() == 0.0

    def test_success_rate_mixed(self, tmp_history):
        tmp_history.record(_make_event(run_id=1, healing_success=True))
        tmp_history.record(_make_event(run_id=2, healing_success=False))
        assert tmp_history.success_rate() == 0.5

    def test_success_rate_empty(self, tmp_history):
        assert tmp_history.success_rate() == 0.0

    def test_mttr_with_successful_events(self, tmp_history):
        tmp_history.record(_make_event(run_id=1, healing_success=True, recovery_seconds=60.0))
        tmp_history.record(_make_event(run_id=2, healing_success=True, recovery_seconds=120.0))
        mttr = tmp_history.mttr_seconds()
        assert mttr == pytest.approx(90.0)

    def test_mttr_none_when_no_successes(self, tmp_history):
        tmp_history.record(_make_event(run_id=1, healing_success=False, recovery_seconds=None))
        assert tmp_history.mttr_seconds() is None

    def test_mttr_excludes_failed_healings(self, tmp_history):
        tmp_history.record(_make_event(run_id=1, healing_success=True, recovery_seconds=30.0))
        tmp_history.record(_make_event(run_id=2, healing_success=False, recovery_seconds=None))
        assert tmp_history.mttr_seconds() == pytest.approx(30.0)

    def test_mttr_empty(self, tmp_history):
        assert tmp_history.mttr_seconds() is None


class TestHealingHistorySummaryByRepo:
    def test_summary_by_repo_groups_correctly(self, tmp_history):
        tmp_history.record(_make_event(repo="owner/repo-a", run_id=1, healing_success=True))
        tmp_history.record(_make_event(repo="owner/repo-a", run_id=2, healing_success=False))
        tmp_history.record(_make_event(repo="owner/repo-b", run_id=3, healing_success=True))

        summary = tmp_history.summary_by_repo()
        assert "owner/repo-a" in summary
        assert "owner/repo-b" in summary
        assert summary["owner/repo-a"]["total"] == 2
        assert summary["owner/repo-b"]["total"] == 1

    def test_summary_success_rate(self, tmp_history):
        tmp_history.record(_make_event(repo="owner/repo", run_id=1, healing_success=True))
        tmp_history.record(_make_event(repo="owner/repo", run_id=2, healing_success=True))
        tmp_history.record(_make_event(repo="owner/repo", run_id=3, healing_success=False))

        summary = tmp_history.summary_by_repo()
        assert summary["owner/repo"]["success_rate"] == pytest.approx(66.7, rel=0.1)


class TestHealingHistoryHealingCountsToday:
    def test_counts_today(self, tmp_history):
        tmp_history.record(_make_event(run_id=1, healing_success=True))
        tmp_history.record(_make_event(run_id=2, healing_success=True))
        tmp_history.record(_make_event(run_id=3, healing_success=False))

        counts = tmp_history.healing_counts_today()
        assert counts["total"] == 3
        assert counts["successes"] == 2
        assert counts["failures"] == 1


class TestHealingEventDataclass:
    def test_as_dict_and_from_dict_roundtrip(self):
        event = _make_event(run_id=42, failure_type="build_error")
        d = event.as_dict()
        restored = HealingEvent.from_dict(d)
        assert restored.run_id == 42
        assert restored.failure_type == "build_error"
        assert restored.healing_success == event.healing_success
