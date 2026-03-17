"""
Flask dashboard for the self-healing CI/CD pipeline.

Endpoints:
  GET /              – HTML dashboard
  GET /api/status    – JSON overview (health cards)
  GET /api/history   – JSON list of healing events
  GET /api/repos     – JSON per-repo statistics
  GET /health        – liveness probe
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from flask import Flask, jsonify, render_template, current_app

logger = logging.getLogger(__name__)


def create_app(history=None, monitor=None) -> Flask:
    """
    Application factory.

    :param history: :class:`~pipeline.history.HealingHistory` instance.
    :param monitor: :class:`~pipeline.monitor.WorkflowMonitor` instance.
    """
    app = Flask(__name__, template_folder="templates")

    # Store shared state on the app object so blueprints / routes can access it
    app.history = history  # type: ignore[attr-defined]
    app.monitor = monitor  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/status")
    def api_status():
        h = current_app.history
        if h is None:
            return jsonify({"error": "history not initialised"}), 503

        today = h.events_today()
        all_events = h.all_events()
        mttr = h.mttr_seconds()
        mttr_display = f"{mttr:.0f}s" if mttr is not None else "N/A"

        # Count repos being monitored
        repos_count = 0
        mon = current_app.monitor
        if mon is not None:
            try:
                repos_count = len(mon.get_repos())
            except Exception:
                pass

        return jsonify(
            {
                "repos_monitored": repos_count,
                "total_healing_events": len(all_events),
                "healing_events_today": len(today),
                "success_rate_today": round(h.success_rate(today) * 100, 1),
                "success_rate_all_time": round(h.success_rate() * 100, 1),
                "mttr_seconds": mttr,
                "mttr_display": mttr_display,
                "healing_counts_today": h.healing_counts_today(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    @app.route("/api/history")
    def api_history():
        h = current_app.history
        if h is None:
            return jsonify([])
        events = h.all_events()
        # Return newest-first
        return jsonify([e.as_dict() for e in reversed(events[-100:])])

    @app.route("/api/repos")
    def api_repos():
        h = current_app.history
        if h is None:
            return jsonify({})
        return jsonify(h.summary_by_repo())

    return app
