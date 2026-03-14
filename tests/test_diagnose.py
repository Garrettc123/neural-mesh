"""Tests for the Diagnoser failure classification engine."""

import pytest
from pipeline.diagnose import Diagnoser, FailureType, DiagnosisResult


@pytest.fixture
def diagnoser():
    return Diagnoser()


class TestDiagnoserEmptyLog:
    def test_empty_log_returns_unknown(self, diagnoser):
        result = diagnoser.diagnose("")
        assert result.failure_type == FailureType.UNKNOWN
        assert result.confidence == 0.0

    def test_whitespace_log_returns_unknown(self, diagnoser):
        result = diagnoser.diagnose("   \n  ")
        assert result.failure_type == FailureType.UNKNOWN


class TestDiagnoserTestFailure:
    def test_pytest_failures(self, diagnoser):
        log = """
collecting ... done
FAILED tests/test_app.py::test_something - AssertionError: expected 200 got 404
failures=3
"""
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.TEST_FAILURE

    def test_assertion_error(self, diagnoser):
        log = "E   AssertionError: assert 1 == 2"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.TEST_FAILURE

    def test_go_test_fail(self, diagnoser):
        log = "--- FAIL TestMyFunction (0.00s)\nFAIL\t github.com/owner/repo\t0.001s"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.TEST_FAILURE

    def test_jest_fail(self, diagnoser):
        log = " FAIL src/__tests__/app.test.js\n  ● Test suite failed to run"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.TEST_FAILURE


class TestDiagnoserBuildError:
    def test_syntax_error(self, diagnoser):
        log = "SyntaxError: invalid syntax\n  File 'main.py', line 42"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.BUILD_ERROR

    def test_typescript_error(self, diagnoser):
        log = "src/index.ts(42,5): error TS2345: Argument of type 'string' is not assignable"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.BUILD_ERROR

    def test_build_failed_keyword(self, diagnoser):
        log = "Error: Build failed with exit code 1"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.BUILD_ERROR


class TestDiagnoserDependencyIssue:
    def test_module_not_found(self, diagnoser):
        log = "ModuleNotFoundError: No module named 'numpy'"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.DEPENDENCY_ISSUE

    def test_import_error(self, diagnoser):
        log = "ImportError: cannot import name 'something' from 'mypackage'"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.DEPENDENCY_ISSUE

    def test_pip_no_matching_distribution(self, diagnoser):
        log = "ERROR: Could not find a version that satisfies the requirement numpy==99.0"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.DEPENDENCY_ISSUE

    def test_npm_not_found(self, diagnoser):
        log = "npm ERR! 404 Not Found - GET https://registry.npmjs.org/nonexistent-package"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.DEPENDENCY_ISSUE


class TestDiagnoserInfraOutage:
    def test_timed_out(self, diagnoser):
        log = "Error: The operation timed out after 360000ms"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.INFRA_OUTAGE

    def test_runner_disconnected(self, diagnoser):
        log = "The hosted runner: ubuntu-latest lost communication with the server."
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.INFRA_OUTAGE

    def test_connection_refused(self, diagnoser):
        log = "Error: connect ECONNREFUSED 127.0.0.1:5432"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.INFRA_OUTAGE

    def test_oom(self, diagnoser):
        log = "OOM killer: process was terminated due to OOM"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.INFRA_OUTAGE


class TestDiagnoserConfigError:
    def test_permission_denied(self, diagnoser):
        log = "Error: Permission denied – check your repository settings"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.CONFIGURATION_ERROR

    def test_401_unauthorized(self, diagnoser):
        log = "HTTP 401 Unauthorized – authentication failed"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.CONFIGURATION_ERROR

    def test_secret_not_set(self, diagnoser):
        log = "Error: secret MY_SECRET_TOKEN not set in repository settings"
        result = diagnoser.diagnose(log)
        assert result.failure_type == FailureType.CONFIGURATION_ERROR


class TestDiagnoserJobNames:
    def test_job_names_appear_in_summary(self, diagnoser):
        log = "AssertionError: assert False"
        result = diagnoser.diagnose(log, job_names=["test-unit", "test-integration"])
        assert "test-unit" in result.summary or "test-integration" in result.summary

    def test_no_job_names(self, diagnoser):
        log = "AssertionError: assert False"
        result = diagnoser.diagnose(log)
        assert isinstance(result.summary, str)


class TestDiagnoserResult:
    def test_result_repr(self, diagnoser):
        log = "AssertionError: assert 1 == 2"
        result = diagnoser.diagnose(log)
        r = repr(result)
        assert "DiagnosisResult" in r

    def test_matched_patterns_not_empty_for_known_failure(self, diagnoser):
        log = "AssertionError: assert 1 == 2"
        result = diagnoser.diagnose(log)
        assert len(result.matched_patterns) > 0

    def test_snippet_populated(self, diagnoser):
        log = "ModuleNotFoundError: No module named 'pandas'"
        result = diagnoser.diagnose(log)
        assert len(result.raw_log_snippet) > 0
