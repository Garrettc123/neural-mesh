"""
Healing History – persistent log of all healing events with success rates.

Events are stored as newline-delimited JSON (NDJSON) for append-only writes
and easy streaming reads. A companion in-memory cache is maintained for
dashboard queries.
"""

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class HealingEvent:
    """Record of a single healing attempt."""

    repo: str
    run_id: int
    run_url: str
    failure_type: str
    diagnosis_summary: str
    healing_action: str
    healing_success: bool
    # ISO-8601 strings stored as plain strings for easy JSON serialisation
    detected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    healed_at: Optional[str] = None
    # Recovery time in seconds (None if healing failed)
    recovery_seconds: Optional[float] = None
    retry_count: int = 0

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "HealingEvent":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class HealingHistory:
    """
    Append-only log of healing events.

    Thread-safe for concurrent read/write access from monitor + dashboard.
    """

    def __init__(self, history_file: str = "healing_history.json"):
        self._file = history_file
        self._lock = threading.Lock()
        self._events: List[HealingEvent] = []
        self._load()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(self, event: HealingEvent) -> None:
        """Persist *event* to the log file and in-memory cache."""
        with self._lock:
            self._events.append(event)
            self._append_to_file(event)
        logger.info(
            "Healing event recorded: repo=%s run_id=%d action=%s success=%s",
            event.repo,
            event.run_id,
            event.healing_action,
            event.healing_success,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def all_events(self) -> List[HealingEvent]:
        with self._lock:
            return list(self._events)

    def events_today(self) -> List[HealingEvent]:
        """Return events whose *detected_at* falls within today (UTC)."""
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self._lock:
            return [
                e for e in self._events
                if self._parse_dt(e.detected_at) >= start_of_day
            ]

    def events_for_repo(self, repo: str) -> List[HealingEvent]:
        with self._lock:
            return [e for e in self._events if e.repo == repo]

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def success_rate(self, events: Optional[List[HealingEvent]] = None) -> float:
        """Return success rate (0.0 – 1.0) across *events* (defaults to all)."""
        if events is None:
            events = self.all_events()
        if not events:
            return 0.0
        successes = sum(1 for e in events if e.healing_success)
        return successes / len(events)

    def mttr_seconds(self, events: Optional[List[HealingEvent]] = None) -> Optional[float]:
        """
        Mean Time to Recovery in seconds.
        Considers only *successful* healing events with a recorded recovery_seconds.
        Returns None if no such events exist.
        """
        if events is None:
            events = self.all_events()
        times = [e.recovery_seconds for e in events
                 if e.healing_success and e.recovery_seconds is not None]
        if not times:
            return None
        return sum(times) / len(times)

    def summary_by_repo(self) -> Dict[str, dict]:
        """Return per-repo statistics dict."""
        by_repo: Dict[str, List[HealingEvent]] = {}
        with self._lock:
            for event in self._events:
                by_repo.setdefault(event.repo, []).append(event)
        result = {}
        for repo, evts in by_repo.items():
            result[repo] = {
                "total": len(evts),
                "success_rate": round(self.success_rate(evts) * 100, 1),
                "mttr_seconds": self.mttr_seconds(evts),
                "last_event": evts[-1].detected_at if evts else None,
            }
        return result

    def healing_counts_today(self) -> dict:
        today = self.events_today()
        total = len(today)
        successes = sum(1 for e in today if e.healing_success)
        failures = total - successes
        return {"total": total, "successes": successes, "failures": failures}

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load existing events from the NDJSON file."""
        if not os.path.exists(self._file):
            return
        loaded = 0
        try:
            with open(self._file, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._events.append(HealingEvent.from_dict(json.loads(line)))
                        loaded += 1
                    except (json.JSONDecodeError, TypeError) as exc:
                        logger.warning("Skipping malformed history line: %s", exc)
        except OSError as exc:
            logger.error("Could not read history file %s: %s", self._file, exc)
        logger.info("Loaded %d healing events from %s", loaded, self._file)

    def _append_to_file(self, event: HealingEvent) -> None:
        """Append a single event to the NDJSON file."""
        try:
            with open(self._file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.as_dict()) + "\n")
        except OSError as exc:
            logger.error("Could not write to history file %s: %s", self._file, exc)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_dt(iso_str: str) -> datetime:
        """Parse an ISO-8601 datetime string into an aware datetime."""
        try:
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
