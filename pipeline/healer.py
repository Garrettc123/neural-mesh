"""
Auto-Healing Engine.

Applies automated fixes based on the diagnosis result:
  - TEST_FAILURE        → retry the failed jobs (flaky test mitigation)
  - BUILD_ERROR         → retry once; emit actionable log
  - DEPENDENCY_ISSUE    → retry once (runner cache might be stale)
  - INFRA_OUTAGE        → retry with exponential back-off
  - CONFIGURATION_ERROR → notify only (human intervention required)
  - UNKNOWN             → retry once as a best-effort
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from .config import Config, config as default_config
from .diagnose import DiagnosisResult, FailureType
from .history import HealingEvent, HealingHistory
from .monitor import WorkflowMonitor, WorkflowRun
from .notifier import Notifier

logger = logging.getLogger(__name__)


class Healer:
    """Dispatches healing strategies and records outcomes."""

    def __init__(
        self,
        monitor: Optional[WorkflowMonitor] = None,
        notifier: Optional[Notifier] = None,
        history: Optional[HealingHistory] = None,
        cfg: Optional[Config] = None,
    ):
        self._cfg = cfg or default_config
        self._monitor = monitor or WorkflowMonitor(self._cfg)
        self._notifier = notifier or Notifier(self._cfg)
        self._history = history or HealingHistory(self._cfg.history_file)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def heal(self, run: WorkflowRun, diagnosis: DiagnosisResult) -> bool:
        """
        Apply the appropriate healing action for *run* based on *diagnosis*.
        Records the event and sends notifications.
        Returns True if healing was (likely) successful.
        """
        detected_at = datetime.now(timezone.utc)

        # Always notify about the detected failure
        self._notifier.send_failure_alert(
            repo=run.repo,
            run_id=run.id,
            run_url=run.html_url,
            failure_type=diagnosis.failure_type.value,
            summary=diagnosis.summary,
        )

        action, success = self._dispatch(run, diagnosis)

        healed_at = datetime.now(timezone.utc)
        recovery_seconds = (healed_at - detected_at).total_seconds() if success else None

        event = HealingEvent(
            repo=run.repo,
            run_id=run.id,
            run_url=run.html_url,
            failure_type=diagnosis.failure_type.value,
            diagnosis_summary=diagnosis.summary,
            healing_action=action,
            healing_success=success,
            detected_at=detected_at.isoformat(),
            healed_at=healed_at.isoformat() if success else None,
            recovery_seconds=recovery_seconds,
        )
        self._history.record(event)

        self._notifier.send_healing_result(
            repo=run.repo,
            run_id=run.id,
            action=action,
            success=success,
        )
        return success

    # ------------------------------------------------------------------
    # Dispatch table
    # ------------------------------------------------------------------

    def _dispatch(self, run: WorkflowRun, diagnosis: DiagnosisResult):
        """Route to the correct healing strategy. Returns (action_name, success)."""
        ft = diagnosis.failure_type
        if ft == FailureType.TEST_FAILURE:
            return self._retry_flaky_tests(run)
        elif ft == FailureType.BUILD_ERROR:
            return self._retry_build(run)
        elif ft == FailureType.DEPENDENCY_ISSUE:
            return self._retry_with_cache_bust(run)
        elif ft == FailureType.INFRA_OUTAGE:
            return self._retry_with_backoff(run)
        elif ft == FailureType.CONFIGURATION_ERROR:
            return self._escalate_config_error(run, diagnosis)
        else:
            return self._retry_once(run)

    # ------------------------------------------------------------------
    # Healing strategies
    # ------------------------------------------------------------------

    def _retry_flaky_tests(self, run: WorkflowRun):
        """Re-run only the failed test jobs."""
        action = "retry_failed_jobs"
        logger.info("[%s] Retrying flaky tests for run %d", run.repo, run.id)
        self._notifier.send_healing_started(run.repo, run.id, action)

        max_attempts = self._cfg.max_retry_attempts
        for attempt in range(1, max_attempts + 1):
            success = self._monitor.rerun_failed_jobs(run.repo, run.id)
            if success:
                logger.info("[%s] Retry %d/%d triggered for run %d",
                            run.repo, attempt, max_attempts, run.id)
                return action, True
            logger.warning("[%s] Retry attempt %d/%d failed for run %d",
                           run.repo, attempt, max_attempts, run.id)
            if attempt < max_attempts:
                time.sleep(5 * attempt)  # brief back-off between attempts
        return action, False

    def _retry_build(self, run: WorkflowRun):
        """Retry a build failure once."""
        action = "retry_build"
        logger.info("[%s] Retrying build for run %d", run.repo, run.id)
        self._notifier.send_healing_started(run.repo, run.id, action)
        success = self._monitor.rerun_failed_jobs(run.repo, run.id)
        return action, success

    def _retry_with_cache_bust(self, run: WorkflowRun):
        """
        Retry failed jobs.  Dependency issues are often caused by stale caches;
        re-running without a cache typically resolves them.
        """
        action = "retry_dependency_fix"
        logger.info("[%s] Retrying dependency fix for run %d", run.repo, run.id)
        self._notifier.send_healing_started(run.repo, run.id, action)
        success = self._monitor.rerun_failed_jobs(run.repo, run.id)
        return action, success

    def _retry_with_backoff(self, run: WorkflowRun):
        """Retry an infrastructure outage with exponential back-off."""
        action = "retry_infra_backoff"
        logger.info("[%s] Waiting before retrying infra issue for run %d", run.repo, run.id)
        self._notifier.send_healing_started(run.repo, run.id, action)

        delays = [10, 30, 60]  # seconds
        for i, delay in enumerate(delays, start=1):
            logger.info("[%s] Infra retry %d/%d – waiting %ds", run.repo, i, len(delays), delay)
            time.sleep(delay)
            success = self._monitor.rerun_failed_jobs(run.repo, run.id)
            if success:
                return action, True
        return action, False

    def _escalate_config_error(self, run: WorkflowRun, diagnosis: DiagnosisResult):
        """
        Configuration errors require human intervention.
        We notify loudly but do not attempt an automated fix.
        """
        action = "escalate_config_error"
        logger.warning(
            "[%s] Configuration error in run %d – manual intervention required: %s",
            run.repo, run.id, diagnosis.summary,
        )
        self._notifier.send_alert(
            subject=f"🚨 Manual Fix Required: {run.repo}",
            body=(
                f"*Repository:* {run.repo}\n"
                f"*Run ID:* {run.id}\n"
                f"*URL:* {run.html_url}\n"
                f"*Issue:* {diagnosis.summary}\n\n"
                "This failure requires manual intervention."
            ),
            emoji="🚨",
        )
        return action, False

    def _retry_once(self, run: WorkflowRun):
        """Best-effort single retry for unknown failures."""
        action = "retry_once"
        logger.info("[%s] Best-effort retry for run %d", run.repo, run.id)
        self._notifier.send_healing_started(run.repo, run.id, action)
        success = self._monitor.rerun_failed_jobs(run.repo, run.id)
        return action, success
