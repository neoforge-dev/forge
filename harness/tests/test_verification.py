"""
Tests for forge_harness/verification.py - Post-Coding Verification Module

These tests cover the verification module using mocked subprocess calls.
"""

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from forge_harness.verification import VerificationResult, Verifier


class TestVerificationResult:
    """Tests for VerificationResult dataclass."""

    def test_summary_passed(self):
        """Test summary for passed verification."""
        result = VerificationResult(
            passed=True,
            tests_passed=10,
            tests_failed=0,
            coverage_backend=85.5,
            coverage_frontend=70.0,
            lint_errors=0,
        )

        summary = result.summary

        assert "PASSED" in summary
        assert "10 passed" in summary
        assert "0 failed" in summary
        assert "85.5%" in summary
        assert "70.0%" in summary

    def test_summary_failed_with_error_message(self):
        """Test summary for failed verification with error message."""
        result = VerificationResult(
            passed=False,
            tests_passed=8,
            tests_failed=2,
            coverage_backend=40.0,
            coverage_frontend=30.0,
            lint_errors=5,
            error_message="Quality gates: 2 test(s) failed",
        )

        summary = result.summary

        assert "FAILED" in summary
        assert "2 failed" in summary
        assert "Lint errors: 5" in summary
        assert "Quality gates" in summary

    def test_summary_with_checkpoints(self):
        """Test summary with checkpoint results."""
        result = VerificationResult(
            passed=True,
            tests_passed=5,
            tests_failed=0,
            coverage_backend=80.0,
            coverage_frontend=60.0,
            lint_errors=0,
            checkpoint_results={
                "- Tests must pass": True,
                "- Coverage >= 80%": True,
                "- No lint errors": True,
            },
        )

        summary = result.summary

        assert "Checkpoints:" in summary
        assert "✓" in summary
        assert "Tests must pass" in summary


@dataclass
class MockDomainConfig:
    """Mock domain configuration."""

    min_backend_coverage: float = 65.0
    min_frontend_coverage: float = 50.0


class TestVerifierInit:
    """Tests for Verifier initialization."""

    def test_init_with_defaults(self, tmp_path):
        """Test initializing Verifier with default config."""
        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        assert verifier.project_dir == tmp_path
        assert verifier.min_backend_coverage == 65.0
        assert verifier.min_frontend_coverage == 50.0
        assert verifier.skip_quality_gates is False

    def test_init_with_custom_thresholds(self, tmp_path):
        """Test initializing with custom coverage thresholds."""
        config = MockDomainConfig(min_backend_coverage=80.0, min_frontend_coverage=70.0)
        verifier = Verifier(tmp_path, config)

        assert verifier.min_backend_coverage == 80.0
        assert verifier.min_frontend_coverage == 70.0

    def test_init_skip_quality_gates(self, tmp_path):
        """Test skip_quality_gates flag."""
        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config, skip_quality_gates=True)

        assert verifier.skip_quality_gates is True


class TestRunBackendTests:
    """Tests for run_backend_tests method."""

    def test_no_backend_dir(self, tmp_path):
        """Test when backend directory doesn't exist."""
        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        passed, failed, coverage, errors = verifier.run_backend_tests()

        assert passed == 0
        assert failed == 0
        assert coverage == 0.0
        assert errors == []

    def test_successful_tests(self, tmp_path):
        """Test successful backend test run."""
        # Create backend directory
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        # Mock subprocess.run
        with patch("subprocess.run") as mock_run:
            # First call is uv sync
            mock_sync = MagicMock(returncode=0)
            # Second call is pytest
            mock_test = MagicMock(returncode=0, stdout="10 passed in 5.0s", stderr="")
            mock_run.side_effect = [mock_sync, mock_test]

            # Create coverage.json
            cov_data = {"totals": {"percent_covered": 85.5}}
            (backend_dir / "coverage.json").write_text(json.dumps(cov_data))

            passed, failed, coverage, errors = verifier.run_backend_tests()

        assert passed == 10
        assert failed == 0
        assert coverage == 85.5
        assert errors == []

    def test_failed_tests_with_errors(self, tmp_path):
        """Test backend test run with failures."""
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        with patch("subprocess.run") as mock_run:
            mock_sync = MagicMock(returncode=0)
            mock_test = MagicMock(
                returncode=1,
                stdout="8 passed, 2 failed\nFAILED test_api.py::test_login - AssertionError",
                stderr="",
            )
            mock_run.side_effect = [mock_sync, mock_test]

            (backend_dir / "coverage.json").write_text(
                json.dumps({"totals": {"percent_covered": 60.0}})
            )

            passed, failed, coverage, errors = verifier.run_backend_tests()

        assert passed == 8
        assert failed == 2
        assert coverage == 60.0
        assert any("FAILED" in e for e in errors)

    def test_timeout(self, tmp_path):
        """Test timeout handling."""
        import subprocess

        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        with patch("subprocess.run") as mock_run:
            # First call succeeds (uv sync), second times out
            mock_sync = MagicMock(returncode=0)
            mock_run.side_effect = [mock_sync, subprocess.TimeoutExpired(cmd="pytest", timeout=300)]

            passed, failed, coverage, errors = verifier.run_backend_tests()

        assert passed == 0
        assert failed == 1
        assert "timed out" in errors[0].lower()

    def test_uv_not_found(self, tmp_path):
        """Test when uv command is not found."""
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()

            passed, failed, coverage, errors = verifier.run_backend_tests()

        assert passed == 0
        assert failed == 0
        assert any("uv" in e.lower() for e in errors)


class TestRunFrontendTests:
    """Tests for run_frontend_tests method."""

    def test_no_frontend_dir(self, tmp_path):
        """Test when frontend directory doesn't exist."""
        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        passed, failed, coverage, errors = verifier.run_frontend_tests()

        assert passed == 0
        assert failed == 0
        assert coverage == 0.0
        assert errors == []

    def test_successful_tests(self, tmp_path):
        """Test successful frontend test run."""
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        with patch("subprocess.run") as mock_run:
            mock_install = MagicMock(returncode=0)
            mock_test = MagicMock(
                returncode=0,
                stdout="15 passed, 0 failed\nAll files    |   75.5 |   60.2 |   80.1 |   72.5",
                stderr="",
            )
            mock_run.side_effect = [mock_install, mock_test]

            passed, failed, coverage, errors = verifier.run_frontend_tests()

        assert passed == 15
        assert failed == 0
        assert coverage == 72.5

    def test_timeout(self, tmp_path):
        """Test frontend timeout handling."""
        import subprocess

        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        with patch("subprocess.run") as mock_run:
            mock_install = MagicMock(returncode=0)
            mock_run.side_effect = [mock_install, subprocess.TimeoutExpired(cmd="npm", timeout=300)]

            passed, failed, coverage, errors = verifier.run_frontend_tests()

        assert passed == 0
        assert failed == 1
        assert any("timed out" in e.lower() for e in errors)


class TestRunLinting:
    """Tests for run_linting method."""

    def test_no_directories(self, tmp_path):
        """Test when neither backend nor frontend exists."""
        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        error_count, errors = verifier.run_linting()

        assert error_count == 0
        assert errors == []

    def test_backend_lint_success(self, tmp_path):
        """Test successful backend linting."""
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="All checks passed!", stderr="")

            error_count, errors = verifier.run_linting()

        assert error_count == 0
        assert errors == []

    def test_backend_lint_errors(self, tmp_path):
        """Test backend linting with errors."""
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="app/main.py:10:5: E501 line too long\napp/api.py:20:1: F401 unused import",
                stderr="",
            )

            error_count, errors = verifier.run_linting()

        assert error_count == 2
        assert any("Lint:" in e for e in errors)

    def test_frontend_lint_exceeded_warnings(self, tmp_path):
        """Test frontend linting exceeding warning threshold."""
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="  warning: Something problematic", stderr=""
            )

            error_count, errors = verifier.run_linting()

        assert error_count == 1
        assert any("exceeded" in e.lower() or "warning" in e.lower() for e in errors)


class TestGenerateFixSuggestions:
    """Tests for generate_fix_suggestions method."""

    def test_no_errors(self, tmp_path):
        """Test with no errors returns empty suggestions."""
        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        suggestions = verifier.generate_fix_suggestions([])

        assert suggestions == []

    def test_with_basic_pattern_db(self, tmp_path):
        """Test fix suggestions using basic FailurePatternDB."""
        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        # Simulate errors that might match patterns
        errors = [
            "ModuleNotFoundError: No module named 'requests'",
            "ImportError: cannot import name 'foo'",
        ]

        # This will use the default FailurePatternDB
        suggestions = verifier.generate_fix_suggestions(errors)

        # Should generate some suggestions (specific content depends on pattern DB)
        # At minimum, suggestions list should be returned
        assert isinstance(suggestions, list)

    def test_with_enhanced_pattern_db(self, tmp_path):
        """Test fix suggestions using enhanced pattern DB."""
        config = MockDomainConfig()

        # Create mock enhanced DB
        mock_db = MagicMock()
        mock_suggestion = MagicMock()
        mock_suggestion.fix = "Install missing dependency with pip install"
        # Use sync method (Verifier uses get_fix_suggestions_sync to avoid async)
        mock_db.get_fix_suggestions_sync.return_value = [mock_suggestion]

        verifier = Verifier(tmp_path, config, failure_pattern_db=mock_db)

        errors = ["ModuleNotFoundError: No module named 'requests'"]
        suggestions = verifier.generate_fix_suggestions(errors)

        assert len(suggestions) == 1
        assert "FIX:" in suggestions[0]
        mock_db.get_fix_suggestions_sync.assert_called_once_with(errors)


class TestVerifyCheckpoint:
    """Tests for verify_checkpoint method."""

    def test_test_pass_criterion(self, tmp_path):
        """Test parsing 'tests must pass' criterion."""
        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        checkpoint = "- All tests must pass"
        context = {"tests_failed": 0}

        results = verifier.verify_checkpoint(checkpoint, context)

        assert results["- All tests must pass"] is True

    def test_test_fail_criterion(self, tmp_path):
        """Test 'tests pass' criterion when tests fail."""
        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        checkpoint = "- All tests must pass"
        context = {"tests_failed": 2}

        results = verifier.verify_checkpoint(checkpoint, context)

        assert results["- All tests must pass"] is False

    def test_coverage_criterion(self, tmp_path):
        """Test coverage criterion."""
        config = MockDomainConfig(min_backend_coverage=80.0)
        verifier = Verifier(tmp_path, config)

        checkpoint = "- Code coverage >= 80%"
        context = {"coverage_backend": 85.0}

        results = verifier.verify_checkpoint(checkpoint, context)

        assert results["- Code coverage >= 80%"] is True

    def test_coverage_criterion_fails(self, tmp_path):
        """Test coverage criterion when coverage is too low."""
        config = MockDomainConfig(min_backend_coverage=80.0)
        verifier = Verifier(tmp_path, config)

        checkpoint = "- Code coverage >= 80%"
        context = {"coverage_backend": 70.0}

        results = verifier.verify_checkpoint(checkpoint, context)

        assert results["- Code coverage >= 80%"] is False

    def test_lint_criterion(self, tmp_path):
        """Test lint errors criterion."""
        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        checkpoint = "- No lint errors"
        context = {"lint_errors": 0}

        results = verifier.verify_checkpoint(checkpoint, context)

        assert results["- No lint errors"] is True

    def test_multiple_criteria(self, tmp_path):
        """Test multiple criteria at once."""
        config = MockDomainConfig(min_backend_coverage=65.0)
        verifier = Verifier(tmp_path, config)

        checkpoint = """
- All tests must pass
- Code coverage >= 65%
- No lint errors
- Documentation complete
        """
        context = {
            "tests_failed": 0,
            "coverage_backend": 70.0,
            "lint_errors": 0,
        }

        results = verifier.verify_checkpoint(checkpoint, context)

        assert len(results) == 4
        assert all(results.values())  # All should pass


class TestVerify:
    """Tests for the main verify method."""

    def test_verify_all_pass(self, tmp_path):
        """Test verify when all checks pass."""
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        with patch.object(verifier, "run_backend_tests", return_value=(10, 0, 80.0, [])):
            with patch.object(verifier, "run_frontend_tests", return_value=(5, 0, 70.0, [])):
                with patch.object(verifier, "run_linting", return_value=(0, [])):
                    result = verifier.verify()

        assert result.passed is True
        assert result.tests_passed == 15
        assert result.tests_failed == 0
        assert result.coverage_backend == 80.0
        assert result.coverage_frontend == 70.0
        assert result.lint_errors == 0

    def test_verify_test_failures(self, tmp_path):
        """Test verify when tests fail."""
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        with patch.object(
            verifier, "run_backend_tests", return_value=(8, 2, 70.0, ["FAILED test_api"])
        ):
            with patch.object(verifier, "run_frontend_tests", return_value=(5, 0, 60.0, [])):
                with patch.object(verifier, "run_linting", return_value=(0, [])):
                    result = verifier.verify()

        assert result.passed is False
        assert result.tests_failed == 2
        assert "failed" in result.error_message.lower()

    def test_verify_coverage_too_low(self, tmp_path):
        """Test verify when coverage is below threshold."""
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig(min_backend_coverage=80.0)
        verifier = Verifier(tmp_path, config)

        with patch.object(verifier, "run_backend_tests", return_value=(10, 0, 50.0, [])):
            with patch.object(verifier, "run_frontend_tests", return_value=(5, 0, 40.0, [])):
                with patch.object(verifier, "run_linting", return_value=(0, [])):
                    result = verifier.verify()

        assert result.passed is False
        assert "coverage" in result.error_message.lower()

    def test_verify_skip_quality_gates(self, tmp_path):
        """Test verify with skip_quality_gates=True."""
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config, skip_quality_gates=True)

        # All failures should be ignored when skip_quality_gates=True
        with patch.object(verifier, "run_backend_tests", return_value=(0, 10, 0.0, [])):
            with patch.object(verifier, "run_frontend_tests", return_value=(0, 5, 0.0, [])):
                with patch.object(verifier, "run_linting", return_value=(100, [])):
                    result = verifier.verify()

        assert result.passed is True
        assert result.error_message is None

    def test_verify_with_checkpoint(self, tmp_path):
        """Test verify with checkpoint criteria."""
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        checkpoint = "- All tests must pass"

        with patch.object(verifier, "run_backend_tests", return_value=(10, 0, 80.0, [])):
            with patch.object(verifier, "run_frontend_tests", return_value=(5, 0, 70.0, [])):
                with patch.object(verifier, "run_linting", return_value=(0, [])):
                    result = verifier.verify(checkpoint=checkpoint)

        assert result.passed is True
        assert "- All tests must pass" in result.checkpoint_results
        assert result.checkpoint_results["- All tests must pass"] is True

    def test_verify_lint_errors(self, tmp_path):
        """Test verify when lint errors exist."""
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        with patch.object(verifier, "run_backend_tests", return_value=(10, 0, 80.0, [])):
            with patch.object(verifier, "run_frontend_tests", return_value=(5, 0, 70.0, [])):
                with patch.object(verifier, "run_linting", return_value=(5, ["Lint: E501"])):
                    result = verifier.verify()

        assert result.passed is False
        assert result.lint_errors == 5
        assert "lint" in result.error_message.lower()

    def test_verify_no_tests_no_coverage_failure(self, tmp_path):
        """Test that missing tests doesn't fail coverage check."""
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig(min_backend_coverage=80.0)
        verifier = Verifier(tmp_path, config)

        # No tests ran (0 passed, 0 failed), coverage N/A
        with patch.object(verifier, "run_backend_tests", return_value=(0, 0, 0.0, [])):
            with patch.object(verifier, "run_frontend_tests", return_value=(0, 0, 0.0, [])):
                with patch.object(verifier, "run_linting", return_value=(0, [])):
                    result = verifier.verify()

        # Should pass because no tests = coverage not applicable
        assert result.passed is True

    def test_verify_generates_fix_suggestions(self, tmp_path):
        """Test that verify generates fix suggestions on errors."""
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        config = MockDomainConfig()
        verifier = Verifier(tmp_path, config)

        errors = ["ModuleNotFoundError: No module named 'requests'"]

        with patch.object(verifier, "run_backend_tests", return_value=(8, 2, 70.0, errors)):
            with patch.object(verifier, "run_frontend_tests", return_value=(0, 0, 0.0, [])):
                with patch.object(verifier, "run_linting", return_value=(0, [])):
                    result = verifier.verify()

        assert result.passed is False
        assert len(result.error_details) >= 1
        # fix_suggestions may or may not have content depending on pattern matches
        assert isinstance(result.fix_suggestions, list)
