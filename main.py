"""
Neural Mesh – Self-Healing CI/CD Pipeline
==========================================
Entry point that starts:
  1. The monitoring / healing loop (background thread)
  2. The Flask dashboard web server (foreground)

Usage
-----
    python main.py

Environment variables (see pipeline/config.py for full list):
    GITHUB_TOKEN        – required for GitHub API access
    GITHUB_OWNER        – GitHub username to monitor (default: Garrettc123)
    POLL_INTERVAL       – seconds between scans (default: 60)
    SLACK_WEBHOOK_URL   – optional Slack incoming webhook
    DISCORD_WEBHOOK_URL – optional Discord webhook
    DASHBOARD_PORT      – HTTP port for the dashboard (default: 8080)
"""

import logging
import signal
import sys
import threading
import time

from pipeline.config import config
from pipeline.dashboard.app import create_app
from pipeline.diagnose import Diagnoser
from pipeline.healer import Healer
from pipeline.history import HealingHistory
from pipeline.monitor import WorkflowMonitor
from pipeline.notifier import Notifier

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_stop_event = threading.Event()


def _handle_signal(signum, frame):
    logger.info("Shutdown signal received (%s) – stopping…", signum)
    _stop_event.set()


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ---------------------------------------------------------------------------
# Monitor / heal loop
# ---------------------------------------------------------------------------

def run_pipeline_loop(
    monitor: WorkflowMonitor,
    diagnoser: Diagnoser,
    healer: Healer,
    stop_event: threading.Event,
) -> None:
    """Background thread: poll → diagnose → heal."""
    logger.info(
        "Pipeline monitor started. Poll interval: %ds | Owner: %s",
        config.poll_interval_seconds,
        config.github_owner,
    )

    if not config.github_token:
        logger.warning(
            "GITHUB_TOKEN is not set. Monitoring is disabled. "
            "Set the GITHUB_TOKEN environment variable to enable live monitoring."
        )
        # Keep thread alive so the dashboard remains accessible
        while not stop_event.is_set():
            stop_event.wait(timeout=30)
        return

    repos = None  # Will be refreshed periodically

    while not stop_event.is_set():
        try:
            # Refresh repo list every cycle (cheap when cached)
            repos = monitor.get_repos()
            new_failures = monitor.check_for_new_failures(repos)

            for run in new_failures:
                logger.info("Processing failure: %s / run %d", run.repo, run.id)

                # Gather logs from failed jobs for better diagnosis
                failed_jobs = monitor.get_failed_jobs(run.repo, run.id)
                combined_log = ""
                job_names = []
                for job in failed_jobs:
                    job_names.append(job.get("name", ""))
                    combined_log += monitor.get_job_log(run.repo, job["id"]) + "\n"

                diagnosis = diagnoser.diagnose(combined_log, job_names or None)
                logger.info(
                    "Diagnosis: %s (confidence %.0f%%) – %s",
                    diagnosis.failure_type.value,
                    diagnosis.confidence * 100,
                    diagnosis.summary,
                )

                healer.heal(run, diagnosis)

        except Exception as exc:
            logger.exception("Unexpected error in pipeline loop: %s", exc)

        stop_event.wait(timeout=config.poll_interval_seconds)

    logger.info("Pipeline monitor stopped.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not config.github_token:
        logger.warning(
            "⚠  GITHUB_TOKEN not set – dashboard will run but live monitoring is disabled."
        )

    # Shared components
    history = HealingHistory(config.history_file)
    monitor = WorkflowMonitor(config)
    notifier = Notifier(config)
    diagnoser = Diagnoser()
    healer = Healer(monitor=monitor, notifier=notifier, history=history, cfg=config)

    # Start monitoring loop in a background daemon thread
    pipeline_thread = threading.Thread(
        target=run_pipeline_loop,
        args=(monitor, diagnoser, healer, _stop_event),
        daemon=True,
        name="pipeline-monitor",
    )
    pipeline_thread.start()

    # Start dashboard
    app = create_app(history=history, monitor=monitor)

    logger.info(
        "Dashboard starting at http://%s:%d",
        config.dashboard_host,
        config.dashboard_port,
    )

    try:
        app.run(
            host=config.dashboard_host,
            port=config.dashboard_port,
            debug=config.dashboard_debug,
            use_reloader=False,  # reloader conflicts with background threads
        )
    finally:
        _stop_event.set()
        pipeline_thread.join(timeout=10)
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    main()
