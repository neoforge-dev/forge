"""Tests for dependency audit quality gate."""

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from forge_harness.quality_gates.dependency_gate import (
    AuditResult,
    DependencyGate,
    DependencyGateConfig,
    VulnerabilityInfo,
    create_dependency_gate,
)


class TestVulnerabilityInfo:
    """Tests for VulnerabilityInfo dataclass."""

    def test_create_vulnerability_info(self):
        """Test creating VulnerabilityInfo instance."""
        vuln = VulnerabilityInfo(
            id="CVE-2023-1234",
            package="requests",
            version="2.28.0",
            severity="high",
            description="Security issue",
            fix_version="2.28.1",
        )
        assert vuln.id == "CVE-2023-1234"
        assert vuln.package == "requests"
        assert vuln.version == "2.28.0"
        assert vuln.severity == "high"
        assert vuln.description == "Security issue"
        assert vuln.fix_version == "2.28.1"

    def test_vulnerability_info_optional_fix_version(self):
        """Test VulnerabilityInfo with no fix version."""
        vuln = VulnerabilityInfo(
            id="CVE-2023-5678",
            package="django",
            version="3.2.0",
            severity="critical",
            description="Critical vulnerability",
        )
        assert vuln.fix_version is None


class TestAuditResult:
    """Tests for AuditResult dataclass."""

    def test_create_audit_result(self):
        """Test creating AuditResult instance."""
        result = AuditResult(
            passed=False,
            critical_count=1,
            high_count=2,
            medium_count=3,
            low_count=4,
            audit_type="pip",
        )
        assert result.passed is False
        assert result.critical_count == 1
        assert result.high_count == 2
        assert result.medium_count == 3
        assert result.low_count == 4
        assert result.audit_type == "pip"
        assert result.vulnerabilities == []
        assert result.error is None
        assert result.cached is False

    def test_audit_result_with_vulnerabilities(self):
        """Test AuditResult with vulnerability list."""
        vuln = VulnerabilityInfo(
            id="CVE-2023-1234",
            package="requests",
            version="2.28.0",
            severity="high",
            description="Security issue",
        )
        result = AuditResult(
            passed=False,
            vulnerabilities=[vuln],
            high_count=1,
            audit_type="pip",
        )
        assert len(result.vulnerabilities) == 1
        assert result.vulnerabilities[0].id == "CVE-2023-1234"

    def test_audit_result_with_error(self):
        """Test AuditResult with error."""
        result = AuditResult(
            passed=False,
            error="Command not found",
            audit_type="npm",
        )
        assert result.error == "Command not found"
        assert result.passed is False


class TestDependencyGateConfig:
    """Tests for DependencyGateConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = DependencyGateConfig()
        assert config.fail_on_critical is True
        assert config.fail_on_high is True
        assert config.fail_on_medium is False
        assert config.cache_duration_hours == 24
        assert config.timeout_seconds == 120

    def test_custom_config(self):
        """Test custom configuration values."""
        config = DependencyGateConfig(
            fail_on_critical=True,
            fail_on_high=False,
            fail_on_medium=True,
            cache_duration_hours=12,
            timeout_seconds=60,
        )
        assert config.fail_on_critical is True
        assert config.fail_on_high is False
        assert config.fail_on_medium is True
        assert config.cache_duration_hours == 12
        assert config.timeout_seconds == 60


class TestDependencyGate:
    """Tests for DependencyGate class."""

    @pytest.fixture
    def temp_project_dir(self, tmp_path):
        """Create a temporary project directory."""
        return tmp_path

    @pytest.fixture
    def gate(self, temp_project_dir):
        """Create a DependencyGate instance."""
        return DependencyGate(cwd=temp_project_dir)

    def test_init_default_config(self, temp_project_dir):
        """Test initialization with default config."""
        gate = DependencyGate(cwd=temp_project_dir)
        assert gate.config.fail_on_critical is True
        assert gate.config.fail_on_high is True
        assert gate.cwd == temp_project_dir

    def test_init_custom_config(self, temp_project_dir):
        """Test initialization with custom config."""
        config = DependencyGateConfig(fail_on_critical=False)
        gate = DependencyGate(config=config, cwd=temp_project_dir)
        assert gate.config.fail_on_critical is False

    def test_get_cache_key(self, gate, temp_project_dir):
        """Test cache key generation from lock file."""
        lock_file = temp_project_dir / "uv.lock"
        lock_file.write_text("dependency==1.0.0\n")

        cache_key = gate._get_cache_key("pip", lock_file)
        assert cache_key
        assert len(cache_key) == 32  # MD5 hash length

    def test_get_cache_key_missing_file(self, gate, temp_project_dir):
        """Test cache key generation with missing file."""
        lock_file = temp_project_dir / "nonexistent.lock"
        cache_key = gate._get_cache_key("pip", lock_file)
        assert cache_key == ""

    def test_save_and_get_cache(self, gate, temp_project_dir):
        """Test saving and retrieving cached results."""
        result = AuditResult(
            passed=True,
            critical_count=0,
            high_count=1,
            medium_count=2,
            low_count=3,
            audit_type="pip",
        )

        cache_key = "test_cache_key"
        gate._save_cache(cache_key, result)

        cached = gate._get_cached_result(cache_key, "pip")
        assert cached is not None
        assert cached.passed is True
        assert cached.critical_count == 0
        assert cached.high_count == 1
        assert cached.medium_count == 2
        assert cached.low_count == 3
        assert cached.cached is True

    def test_cache_expiration(self, gate, temp_project_dir):
        """Test cache expiration after duration."""
        result = AuditResult(passed=True, audit_type="pip")
        cache_key = "test_cache_key"
        gate._save_cache(cache_key, result)

        # Modify cache file to be old
        cache_file = gate._cache_dir / f"audit_pip_{cache_key}.json"
        data = json.loads(cache_file.read_text())
        old_time = datetime.now() - timedelta(hours=25)
        data["cached_at"] = old_time.isoformat()
        cache_file.write_text(json.dumps(data))

        cached = gate._get_cached_result(cache_key, "pip")
        assert cached is None

    def test_cache_with_vulnerabilities(self, gate):
        """Test caching results with vulnerabilities."""
        vuln = VulnerabilityInfo(
            id="CVE-2023-1234",
            package="requests",
            version="2.28.0",
            severity="high",
            description="Security issue",
            fix_version="2.28.1",
        )
        result = AuditResult(
            passed=False,
            vulnerabilities=[vuln],
            high_count=1,
            audit_type="pip",
        )

        cache_key = "test_cache_key"
        gate._save_cache(cache_key, result)

        cached = gate._get_cached_result(cache_key, "pip")
        assert cached is not None
        assert len(cached.vulnerabilities) == 1
        assert cached.vulnerabilities[0].id == "CVE-2023-1234"
        assert cached.vulnerabilities[0].package == "requests"

    @patch("subprocess.run")
    def test_audit_pip_success(self, mock_run, gate, temp_project_dir):
        """Test successful pip audit."""
        (temp_project_dir / "pyproject.toml").touch()

        mock_result = Mock()
        mock_result.stdout = json.dumps(
            [
                {
                    "name": "requests",
                    "version": "2.28.0",
                    "vulnerability": {
                        "id": "CVE-2023-1234",
                        "severity": "high",
                        "description": "Security issue",
                    },
                    "fix_versions": ["2.28.1"],
                }
            ]
        )
        mock_run.return_value = mock_result

        result = gate.audit_pip()
        assert result.audit_type == "pip"
        assert result.passed is False  # fail_on_high is True by default
        assert result.high_count == 1
        assert len(result.vulnerabilities) == 1
        assert result.vulnerabilities[0].package == "requests"

    @patch("subprocess.run")
    def test_audit_pip_no_vulnerabilities(self, mock_run, gate):
        """Test pip audit with no vulnerabilities."""
        mock_result = Mock()
        mock_result.stdout = json.dumps([])
        mock_run.return_value = mock_result

        result = gate.audit_pip()
        assert result.passed is True
        assert result.critical_count == 0
        assert result.high_count == 0
        assert len(result.vulnerabilities) == 0

    @patch("subprocess.run")
    def test_audit_pip_command_not_found(self, mock_run, gate):
        """Test pip audit when pip-audit is not installed."""
        mock_run.side_effect = FileNotFoundError()

        result = gate.audit_pip()
        assert result.passed is True
        assert result.error == "pip-audit not installed"

    @patch("subprocess.run")
    def test_audit_pip_timeout(self, mock_run, gate):
        """Test pip audit timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("pip-audit", 120)

        result = gate.audit_pip()
        assert result.passed is False
        assert result.error == "Audit timed out"

    @patch("subprocess.run")
    def test_audit_npm_success(self, mock_run, gate, temp_project_dir):
        """Test successful npm audit."""
        (temp_project_dir / "package.json").touch()
        (temp_project_dir / "package-lock.json").touch()

        mock_result = Mock()
        mock_result.stdout = json.dumps(
            {
                "advisories": {
                    "1234": {
                        "module_name": "lodash",
                        "severity": "critical",
                        "title": "Prototype pollution",
                        "findings": [{"version": "4.17.15"}],
                        "patched_versions": ">=4.17.19",
                    }
                }
            }
        )
        mock_run.return_value = mock_result

        result = gate.audit_npm()
        assert result.audit_type == "npm"
        assert result.passed is False  # fail_on_critical is True by default
        assert result.critical_count == 1
        assert len(result.vulnerabilities) == 1
        assert result.vulnerabilities[0].package == "lodash"

    @patch("subprocess.run")
    def test_audit_npm_no_vulnerabilities(self, mock_run, gate, temp_project_dir):
        """Test npm audit with no vulnerabilities."""
        (temp_project_dir / "package-lock.json").touch()

        mock_result = Mock()
        mock_result.stdout = json.dumps({"advisories": {}})
        mock_run.return_value = mock_result

        result = gate.audit_npm()
        assert result.passed is True
        assert result.critical_count == 0
        assert len(result.vulnerabilities) == 0

    def test_audit_npm_no_lock_file(self, gate, temp_project_dir):
        """Test npm audit when package-lock.json is missing."""
        result = gate.audit_npm()
        assert result.passed is True
        assert result.error == "No package-lock.json found"

    @patch("subprocess.run")
    def test_audit_npm_command_not_found(self, mock_run, gate, temp_project_dir):
        """Test npm audit when npm is not installed."""
        (temp_project_dir / "package-lock.json").touch()
        mock_run.side_effect = FileNotFoundError()

        result = gate.audit_npm()
        assert result.passed is True
        assert result.error == "npm not installed"

    @patch("subprocess.run")
    def test_audit_npm_timeout(self, mock_run, gate, temp_project_dir):
        """Test npm audit timeout."""
        (temp_project_dir / "package-lock.json").touch()
        mock_run.side_effect = subprocess.TimeoutExpired("npm", 120)

        result = gate.audit_npm()
        assert result.passed is False
        assert result.error == "Audit timed out"

    def test_check_passed_fail_on_critical(self, gate):
        """Test check_passed with critical vulnerabilities."""
        gate.config.fail_on_critical = True
        counts = {"critical": 1, "high": 0, "medium": 0, "low": 0}
        assert gate._check_passed(counts) is False

    def test_check_passed_fail_on_high(self, gate):
        """Test check_passed with high vulnerabilities."""
        gate.config.fail_on_high = True
        counts = {"critical": 0, "high": 1, "medium": 0, "low": 0}
        assert gate._check_passed(counts) is False

    def test_check_passed_fail_on_medium(self, gate):
        """Test check_passed with medium vulnerabilities."""
        gate.config.fail_on_medium = True
        counts = {"critical": 0, "high": 0, "medium": 1, "low": 0}
        assert gate._check_passed(counts) is False

    def test_check_passed_ignore_medium(self, gate):
        """Test check_passed ignoring medium vulnerabilities."""
        gate.config.fail_on_medium = False
        counts = {"critical": 0, "high": 0, "medium": 1, "low": 0}
        assert gate._check_passed(counts) is True

    def test_check_passed_low_only(self, gate):
        """Test check_passed with only low vulnerabilities."""
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 5}
        assert gate._check_passed(counts) is True

    def test_run_python_project(self, gate, temp_project_dir):
        """Test run method for Python project."""
        (temp_project_dir / "pyproject.toml").touch()

        with patch.object(gate, "audit_pip") as mock_audit:
            mock_audit.return_value = AuditResult(passed=True, audit_type="pip")
            results = gate.run()

        assert len(results) == 1
        assert results[0].audit_type == "pip"
        mock_audit.assert_called_once()

    def test_run_javascript_project(self, gate, temp_project_dir):
        """Test run method for JavaScript project."""
        (temp_project_dir / "package.json").touch()

        with patch.object(gate, "audit_npm") as mock_audit:
            mock_audit.return_value = AuditResult(passed=True, audit_type="npm")
            results = gate.run()

        assert len(results) == 1
        assert results[0].audit_type == "npm"
        mock_audit.assert_called_once()

    def test_run_mixed_project(self, gate, temp_project_dir):
        """Test run method for project with both Python and JavaScript."""
        (temp_project_dir / "pyproject.toml").touch()
        (temp_project_dir / "package.json").touch()

        with (
            patch.object(gate, "audit_pip") as mock_pip,
            patch.object(gate, "audit_npm") as mock_npm,
        ):
            mock_pip.return_value = AuditResult(passed=True, audit_type="pip")
            mock_npm.return_value = AuditResult(passed=True, audit_type="npm")
            results = gate.run()

        assert len(results) == 2
        assert results[0].audit_type == "pip"
        assert results[1].audit_type == "npm"
        mock_pip.assert_called_once()
        mock_npm.assert_called_once()

    def test_run_no_dependencies(self, gate, temp_project_dir):
        """Test run method when no dependencies found."""
        results = gate.run()

        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].error == "No dependencies found to audit"
        assert results[0].audit_type == "none"

    @patch("subprocess.run")
    def test_pip_audit_uses_cache(self, mock_run, gate, temp_project_dir):
        """Test that pip audit uses cache on second run."""
        lock_file = temp_project_dir / "uv.lock"
        lock_file.write_text("dependency==1.0.0\n")

        mock_result = Mock()
        mock_result.stdout = json.dumps([])
        mock_run.return_value = mock_result

        # First run - should call subprocess
        result1 = gate.audit_pip()
        assert result1.cached is False
        assert mock_run.call_count == 1

        # Second run - should use cache
        result2 = gate.audit_pip()
        assert result2.cached is True
        assert mock_run.call_count == 1  # No additional call

    @patch("subprocess.run")
    def test_npm_audit_uses_cache(self, mock_run, gate, temp_project_dir):
        """Test that npm audit uses cache on second run."""
        lock_file = temp_project_dir / "package-lock.json"
        lock_file.write_text('{"name": "test"}')

        mock_result = Mock()
        mock_result.stdout = json.dumps({"advisories": {}})
        mock_run.return_value = mock_result

        # First run - should call subprocess
        result1 = gate.audit_npm()
        assert result1.cached is False
        assert mock_run.call_count == 1

        # Second run - should use cache
        result2 = gate.audit_npm()
        assert result2.cached is True
        assert mock_run.call_count == 1  # No additional call


class TestCreateDependencyGate:
    """Tests for create_dependency_gate factory function."""

    def test_create_with_defaults(self, tmp_path):
        """Test factory function with default parameters."""
        gate = create_dependency_gate(cwd=tmp_path)
        assert isinstance(gate, DependencyGate)
        assert gate.config.fail_on_critical is True
        assert gate.config.fail_on_high is True
        assert gate.config.fail_on_medium is False
        assert gate.cwd == tmp_path

    def test_create_with_custom_params(self, tmp_path):
        """Test factory function with custom parameters."""
        gate = create_dependency_gate(
            fail_on_critical=False,
            fail_on_high=True,
            fail_on_medium=True,
            cwd=tmp_path,
        )
        assert gate.config.fail_on_critical is False
        assert gate.config.fail_on_high is True
        assert gate.config.fail_on_medium is True

    def test_create_without_cwd(self):
        """Test factory function without cwd parameter."""
        gate = create_dependency_gate()
        assert gate.cwd == Path.cwd()
