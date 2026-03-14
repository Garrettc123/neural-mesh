"""
GitHub Actions Workflow Monitor.

Continuously polls GitHub API to detect failed/errored workflow runs across
all repositories owned by the configured GitHub account.
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import requests

from .config import Config, config as default_config

logger = logging.getLogger(__name__)


class WorkflowRun:
    """Lightweight representation of a GitHub Actions workflow run."""

    def __init__(self, data: dict):
        self.id: int = data["id"]
        self.name: str = data.get("name") or data.get("display_title", "")
        self.status: str = data.get("status", "")
        self.conclusion: Optional[str] = data.get("conclusion")
        self.repo: str = data.get("_repo", "")
        self.html_url: str = data.get("html_url", "")
        self.created_at: str = data.get("created_at", "")
        self.updated_at: str = data.get("updated_at", "")
        self.head_branch: str = data.get("head_branch", "")
        self.head_sha: str = data.get("head_sha", "")
        self.workflow_id: int = data.get("workflow_id", 0)

    @property
    def failed(self) -> bool:
        return self.conclusion in ("failure", "timed_out", "startup_failure")

    @property
    def completed(self) -> bool:
        return self.status == "completed"

    def __repr__(self) -> str:
        return (
            f"<WorkflowRun id={self.id} repo={self.repo!r} "
            f"name={self.name!r} conclusion={self.conclusion!r}>"
        )


class WorkflowMonitor:
    """Polls GitHub for workflow failures across all monitored repositories."""

    GITHUB_API = "https://api.github.com"

    def __init__(self, cfg: Optional[Config] = None):
        self._cfg = cfg or default_config
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"token {self._cfg.github_token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        # Track which run IDs we've already seen to avoid duplicate processing
        self._seen_run_ids: set = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_repos(self) -> List[str]:
        """Return a list of repository full names (owner/repo) for the owner."""
        repos: List[str] = []
        page = 1
        while True:
            try:
                resp = self._session.get(
                    f"{self.GITHUB_API}/users/{self._cfg.github_owner}/repos",
                    params={"per_page": 100, "page": page, "type": "owner"},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                if not data:
                    break
                repos.extend(r["full_name"] for r in data)
                if len(data) < 100:
                    break
                page += 1
            except requests.RequestException as exc:
                logger.error("Failed to list repos for %s: %s", self._cfg.github_owner, exc)
                break
        logger.info("Discovered %d repositories for %s", len(repos), self._cfg.github_owner)
        return repos

    def get_recent_runs(self, repo_full_name: str, since: Optional[datetime] = None) -> List[WorkflowRun]:
        """Fetch recent workflow runs for *repo_full_name* (e.g. 'owner/repo')."""
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(seconds=self._cfg.lookback_seconds)

        runs: List[WorkflowRun] = []
        page = 1
        while True:
            try:
                resp = self._session.get(
                    f"{self.GITHUB_API}/repos/{repo_full_name}/actions/runs",
                    params={
                        "per_page": 100,
                        "page": page,
                        "created": f">={since.strftime('%Y-%m-%dT%H:%M:%SZ')}",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("workflow_runs", [])
                if not batch:
                    break
                for run_data in batch:
                    run_data["_repo"] = repo_full_name
                    runs.append(WorkflowRun(run_data))
                if len(batch) < 100:
                    break
                page += 1
            except requests.RequestException as exc:
                logger.warning("Failed to fetch runs for %s: %s", repo_full_name, exc)
                break
        return runs

    def get_run_logs(self, repo_full_name: str, run_id: int) -> str:
        """Download the plain-text logs for a workflow run (best-effort)."""
        try:
            resp = self._session.get(
                f"{self.GITHUB_API}/repos/{repo_full_name}/actions/runs/{run_id}/logs",
                timeout=30,
                allow_redirects=True,
            )
            if resp.status_code == 200:
                # Logs come as a zip; return raw bytes decoded loosely for diagnosis
                return resp.content.decode("utf-8", errors="replace")
        except requests.RequestException as exc:
            logger.debug("Could not fetch logs for run %d in %s: %s", run_id, repo_full_name, exc)
        return ""

    def get_failed_jobs(self, repo_full_name: str, run_id: int) -> List[dict]:
        """Return the list of failed jobs for a run."""
        jobs: List[dict] = []
        try:
            resp = self._session.get(
                f"{self.GITHUB_API}/repos/{repo_full_name}/actions/runs/{run_id}/jobs",
                params={"filter": "latest"},
                timeout=15,
            )
            resp.raise_for_status()
            for job in resp.json().get("jobs", []):
                if job.get("conclusion") in ("failure", "timed_out", "startup_failure"):
                    jobs.append(job)
        except requests.RequestException as exc:
            logger.warning("Failed to fetch jobs for run %d/%s: %s", run_id, repo_full_name, exc)
        return jobs

    def get_job_log(self, repo_full_name: str, job_id: int) -> str:
        """Fetch the log output for a single job."""
        try:
            resp = self._session.get(
                f"{self.GITHUB_API}/repos/{repo_full_name}/actions/jobs/{job_id}/logs",
                timeout=30,
                allow_redirects=True,
            )
            if resp.status_code == 200:
                return resp.text
        except requests.RequestException as exc:
            logger.debug("Could not fetch log for job %d: %s", job_id, exc)
        return ""

    def rerun_failed_jobs(self, repo_full_name: str, run_id: int) -> bool:
        """Trigger a re-run of only the failed jobs in a run."""
        try:
            resp = self._session.post(
                f"{self.GITHUB_API}/repos/{repo_full_name}/actions/runs/{run_id}/rerun-failed-jobs",
                timeout=15,
            )
            if resp.status_code in (201, 204):
                logger.info("Triggered re-run of failed jobs for run %d in %s", run_id, repo_full_name)
                return True
            logger.warning(
                "Re-run request returned %d for run %d in %s: %s",
                resp.status_code, run_id, repo_full_name, resp.text,
            )
        except requests.RequestException as exc:
            logger.error("Error triggering re-run for run %d in %s: %s", run_id, repo_full_name, exc)
        return False

    def check_for_new_failures(self, repos: List[str], since: Optional[datetime] = None) -> List[WorkflowRun]:
        """
        Scan *repos* for failed runs not yet seen.
        Returns only newly discovered failures.
        """
        new_failures: List[WorkflowRun] = []
        for repo in repos:
            runs = self.get_recent_runs(repo, since=since)
            for run in runs:
                if run.failed and run.id not in self._seen_run_ids:
                    self._seen_run_ids.add(run.id)
                    new_failures.append(run)
                    logger.info("New failure detected: %s in %s (run %d)", run.name, repo, run.id)
        return new_failures

    def mark_seen(self, run_id: int) -> None:
        """Mark a run as seen so it won't be reported again."""
        self._seen_run_ids.add(run_id)

    def poll_once(self, repos: Optional[List[str]] = None) -> List[WorkflowRun]:
        """
        One polling cycle: discover repos (if not provided) and check failures.
        Returns newly detected failed runs.
        """
        if repos is None:
            repos = self.get_repos()
        return self.check_for_new_failures(repos)
