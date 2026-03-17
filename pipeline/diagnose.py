"""
Failure Diagnosis Engine.

Analyses workflow run logs to classify the root cause of a CI/CD failure.
Supported categories:
    - TEST_FAILURE        : unit/integration tests reported errors
    - BUILD_ERROR         : compilation / packaging step failed
    - DEPENDENCY_ISSUE    : missing or incompatible packages
    - INFRA_OUTAGE        : runner/service unavailability or timeout
    - CONFIGURATION_ERROR : bad workflow YAML, missing secrets/envvars
    - UNKNOWN             : could not determine root cause
"""

import logging
import re
from enum import Enum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class FailureType(Enum):
    TEST_FAILURE = "test_failure"
    BUILD_ERROR = "build_error"
    DEPENDENCY_ISSUE = "dependency_issue"
    INFRA_OUTAGE = "infra_outage"
    CONFIGURATION_ERROR = "configuration_error"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Pattern registry – order matters (first match wins)
# ---------------------------------------------------------------------------

_PATTERNS: List[Tuple[FailureType, List[str]]] = [
    (
        FailureType.DEPENDENCY_ISSUE,
        [
            r"ModuleNotFoundError",
            r"ImportError",
            r"Cannot find module",
            r"npm ERR!.*not found",
            r"pip.*No matching distribution",
            r"Could not find a version that satisfies",
            r"Package .* not found",
            r"dependency resolution failed",
            r"ENOENT.*node_modules",
            r"unable to resolve dependency",
            r"ResolutionImpossible",
            r"requirements\.txt.*not found",
            r"go: .*cannot find",
        ],
    ),
    (
        FailureType.BUILD_ERROR,
        [
            r"SyntaxError",
            r"CompileError",
            r"FAILED.*build",
            r"Build failed",
            r"error.*compile",
            r"make.*Error",
            r"cargo build.*error",
            r"mvn.*BUILD FAILURE",
            r"gradle.*FAILED",
            r"npm run build.*error",
            r"tsc.*error TS",
            r"error TS\d+",
            r"clang.*error:",
            r"gcc.*error:",
            r"exit code 1.*build",
        ],
    ),
    (
        FailureType.TEST_FAILURE,
        [
            r"FAILED.*test",
            r"test.*FAILED",
            r"AssertionError",
            r"FAILED \[",
            r"pytest.*failed",
            r"failures=\d+",
            r"Tests failed",
            r"test suite.*failed",
            r"ERROR.*test",
            r"FAIL\s+\w",  # Go test output
            r"npm test.*failed",
            r"jest.*FAIL",
            r"mocha.*failing",
        ],
    ),
    (
        FailureType.INFRA_OUTAGE,
        [
            r"timed.?out",
            r"runner.*disconnected",
            r"The hosted runner.*lost",
            r"Connection refused",
            r"ECONNREFUSED",
            r"ETIMEDOUT",
            r"ECONNRESET",
            r"503 Service Unavailable",
            r"502 Bad Gateway",
            r"500 Internal Server Error",
            r"OutOfMemoryError",
            r"OOM",
            r"No space left on device",
            r"lost communication",
            r"Unable to connect",
        ],
    ),
    (
        FailureType.CONFIGURATION_ERROR,
        [
            r"secret.*not set",
            r"variable.*required",
            r"InvalidWorkflowFile",
            r"yaml.*parse error",
            r"Unrecognized.*option",
            r"Error.*token.*invalid",
            r"Error.*credentials",
            r"Permission denied",
            r"403 Forbidden",
            r"401 Unauthorized",
            r"environment.*variable.*missing",
            r"env var.*not found",
        ],
    ),
]

# Pre-compile for performance
_COMPILED_PATTERNS: List[Tuple[FailureType, List[re.Pattern]]] = [
    (ft, [re.compile(p, re.IGNORECASE) for p in patterns])
    for ft, patterns in _PATTERNS
]


class DiagnosisResult:
    """Result of diagnosing a workflow failure."""

    def __init__(
        self,
        failure_type: FailureType,
        confidence: float,
        matched_patterns: List[str],
        summary: str,
        raw_log_snippet: str = "",
    ):
        self.failure_type = failure_type
        self.confidence = confidence  # 0.0 – 1.0
        self.matched_patterns = matched_patterns
        self.summary = summary
        self.raw_log_snippet = raw_log_snippet

    def __repr__(self) -> str:
        return (
            f"<DiagnosisResult type={self.failure_type.value!r} "
            f"confidence={self.confidence:.0%} summary={self.summary!r}>"
        )


class Diagnoser:
    """Classifies CI/CD failures from log text."""

    def diagnose(self, log_text: str, job_names: Optional[List[str]] = None) -> DiagnosisResult:
        """
        Analyse *log_text* and return the most likely failure category.

        :param log_text:  Raw log output (plain text, possibly very long).
        :param job_names: Optional list of failed job names to aid diagnosis.
        :returns: :class:`DiagnosisResult`
        """
        if not log_text.strip():
            return DiagnosisResult(
                failure_type=FailureType.UNKNOWN,
                confidence=0.0,
                matched_patterns=[],
                summary="No log text available for diagnosis.",
            )

        # Work on last 200 lines for efficiency (errors usually at the end)
        lines = log_text.splitlines()
        tail = "\n".join(lines[-200:]) if len(lines) > 200 else log_text

        scores: dict = {ft: 0 for ft in FailureType}
        hits: dict = {ft: [] for ft in FailureType}

        for failure_type, compiled in _COMPILED_PATTERNS:
            for pattern in compiled:
                match = pattern.search(tail)
                if match:
                    scores[failure_type] += 1
                    hits[failure_type].append(pattern.pattern)

        # Pick the category with the most hits; fall back to UNKNOWN
        best_type, best_score = FailureType.UNKNOWN, 0
        for ft, score in scores.items():
            if ft == FailureType.UNKNOWN:
                continue
            if score > best_score:
                best_type, best_score = ft, score

        if best_score == 0:
            return DiagnosisResult(
                failure_type=FailureType.UNKNOWN,
                confidence=0.0,
                matched_patterns=[],
                summary="Could not determine failure cause from logs.",
                raw_log_snippet=tail[-500:],
            )

        total_patterns = sum(len(p) for _, p in _COMPILED_PATTERNS)
        confidence = min(1.0, best_score / max(1, len(_COMPILED_PATTERNS[0][1])))

        snippet = self._extract_snippet(tail, hits[best_type])
        summary = self._build_summary(best_type, hits[best_type], job_names)

        return DiagnosisResult(
            failure_type=best_type,
            confidence=confidence,
            matched_patterns=hits[best_type],
            summary=summary,
            raw_log_snippet=snippet,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_snippet(log_tail: str, patterns: List[str]) -> str:
        """Extract a short context snippet around the first matched line."""
        if not patterns:
            return log_tail[-300:]
        first_pattern = re.compile(patterns[0], re.IGNORECASE)
        lines = log_tail.splitlines()
        for i, line in enumerate(lines):
            if first_pattern.search(line):
                start = max(0, i - 2)
                end = min(len(lines), i + 5)
                return "\n".join(lines[start:end])
        return log_tail[-300:]

    @staticmethod
    def _build_summary(
        failure_type: FailureType,
        patterns: List[str],
        job_names: Optional[List[str]],
    ) -> str:
        jobs_str = f" in job(s): {', '.join(job_names)}" if job_names else ""
        base = {
            FailureType.TEST_FAILURE: f"Test failure detected{jobs_str}.",
            FailureType.BUILD_ERROR: f"Build/compilation error detected{jobs_str}.",
            FailureType.DEPENDENCY_ISSUE: f"Missing or incompatible dependency{jobs_str}.",
            FailureType.INFRA_OUTAGE: f"Infrastructure / runner issue detected{jobs_str}.",
            FailureType.CONFIGURATION_ERROR: f"Workflow configuration error{jobs_str}.",
            FailureType.UNKNOWN: "Unknown failure cause.",
        }
        return base.get(failure_type, "Unknown failure cause.")
