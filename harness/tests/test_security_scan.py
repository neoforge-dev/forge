"""
Tests for Security Scan Gate
=============================

Tests the security scan quality gate functionality for dependency vulnerabilities.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.quality_gates.security_scan import (
    ScanResult,
    SecurityScanGate,
    Vulnerability,
    run_security_scan,
)


@pytest.fixture
def temp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory."""
    project_path = tmp_path / "test_project"
    project_path.mkdir()
    return project_path


@pytest.fixture
def python_project(temp_project: Path) -> Path:
    """Create a Python project with pyproject.toml."""
    pyproject = temp_project / "pyproject.toml"
    pyproject.write_text("""
[project]
name = "test-project"
version = "0.1.0"
dependencies = [
    "requests>=2.25.0",
]
""")
    return temp_project


@pytest.fixture
def javascript_project(temp_project: Path) -> Path:
    """Create a JavaScript project with package.json."""
    package_json = temp_project / "package.json"
    package_json.write_text(
        json.dumps(
            {"name": "test-project", "version": "1.0.0", "dependencies": {"lodash": "^4.17.0"}}
        )
    )
    return temp_project


@pytest.fixture
def security_gate(temp_project: Path) -> SecurityScanGate:
    """Create a SecurityScanGate instance."""
    return SecurityScanGate(project_path=temp_project)


class TestProjectDetection:
    """Test project type detection."""

    def test_detect_python_project(self, python_project: Path) -> None:
        """Test detection of Python project from pyproject.toml."""
        gate = SecurityScanGate(project_path=python_project)
        project_type = gate.detect_project_type()
        assert project_type == "python"

    def test_detect_javascript_project(self, javascript_project: Path) -> None:
        """Test detection of JavaScript project from package.json."""
        gate = SecurityScanGate(project_path=javascript_project)
        project_type = gate.detect_project_type()
        assert project_type == "javascript"

    def test_detect_unknown_project(self, temp_project: Path) -> None:
        """Test detection returns unknown for projects without config files."""
        gate = SecurityScanGate(project_path=temp_project)
        project_type = gate.detect_project_type()
        assert project_type == "unknown"


class TestPipAudit:
    """Test pip-audit integration."""

    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate._command_exists")
    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate._run_command")
    async def test_run_pip_audit_no_vulns(
        self,
        mock_run: AsyncMock,
        mock_exists: AsyncMock,
        python_project: Path,
    ) -> None:
        """Test running pip-audit with no vulnerabilities."""
        mock_exists.return_value = True
        mock_run.return_value = json.dumps({"dependencies": []})

        gate = SecurityScanGate(project_path=python_project)
        vulns, errors = await gate.run_pip_audit()

        assert vulns == []
        assert errors == []
        mock_run.assert_called_once()

    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate._command_exists")
    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate._run_command")
    async def test_run_pip_audit_with_vulns(
        self,
        mock_run: AsyncMock,
        mock_exists: AsyncMock,
        python_project: Path,
    ) -> None:
        """Test running pip-audit with vulnerabilities found."""
        mock_exists.return_value = True

        # Mock pip-audit JSON output
        audit_output = {
            "dependencies": [
                {
                    "name": "requests",
                    "version": "2.25.0",
                    "vulns": [
                        {
                            "id": "GHSA-xxxx-xxxx-xxxx",
                            "fix_versions": ["2.31.0"],
                            "description": "Security issue in requests",
                            "aliases": ["CVE-2023-12345"],
                        }
                    ],
                }
            ]
        }
        mock_run.return_value = json.dumps(audit_output)

        gate = SecurityScanGate(project_path=python_project)
        vulns, errors = await gate.run_pip_audit()

        assert len(vulns) == 1
        assert vulns[0].package == "requests"
        assert vulns[0].version == "2.25.0"
        assert vulns[0].vulnerability_id == "GHSA-xxxx-xxxx-xxxx"
        assert vulns[0].severity == "HIGH"  # Default severity
        assert vulns[0].fixed_version == "2.31.0"
        assert errors == []

    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate._command_exists")
    async def test_pip_audit_not_installed(
        self,
        mock_exists: AsyncMock,
        python_project: Path,
    ) -> None:
        """Test graceful handling when pip-audit is not installed."""
        mock_exists.return_value = False

        gate = SecurityScanGate(project_path=python_project)
        vulns, errors = await gate.run_pip_audit()

        assert vulns == []
        assert len(errors) == 1
        assert "pip-audit not installed" in errors[0]


class TestNpmAudit:
    """Test npm audit integration."""

    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate._command_exists")
    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate._run_command")
    async def test_run_npm_audit_no_vulns(
        self,
        mock_run: AsyncMock,
        mock_exists: AsyncMock,
        javascript_project: Path,
    ) -> None:
        """Test running npm audit with no vulnerabilities."""
        mock_exists.return_value = True
        mock_run.return_value = json.dumps({"vulnerabilities": {}})

        gate = SecurityScanGate(project_path=javascript_project)
        vulns, errors = await gate.run_npm_audit()

        assert vulns == []
        assert errors == []
        mock_run.assert_called_once()

    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate._command_exists")
    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate._run_command")
    async def test_run_npm_audit_with_vulns(
        self,
        mock_run: AsyncMock,
        mock_exists: AsyncMock,
        javascript_project: Path,
    ) -> None:
        """Test running npm audit with vulnerabilities found."""
        mock_exists.return_value = True

        # Mock npm audit JSON output
        audit_output = {
            "vulnerabilities": {
                "lodash": {
                    "name": "lodash",
                    "severity": "high",
                    "via": [
                        {
                            "source": 1234,
                            "name": "lodash",
                            "dependency": "lodash",
                            "title": "Prototype Pollution in lodash",
                            "url": "https://github.com/advisories/GHSA-jf85-cpcp-j695",
                            "severity": "high",
                            "range": "<4.17.21",
                        }
                    ],
                    "fixAvailable": True,
                }
            }
        }
        mock_run.return_value = json.dumps(audit_output)

        gate = SecurityScanGate(project_path=javascript_project)
        vulns, errors = await gate.run_npm_audit()

        assert len(vulns) == 1
        assert vulns[0].package == "lodash"
        assert vulns[0].version == "<4.17.21"
        assert vulns[0].vulnerability_id == "GHSA-jf85-cpcp-j695"
        assert vulns[0].severity == "HIGH"
        assert vulns[0].fixed_version == "available"
        assert errors == []

    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate._command_exists")
    async def test_npm_not_installed(
        self,
        mock_exists: AsyncMock,
        javascript_project: Path,
    ) -> None:
        """Test graceful handling when npm is not installed."""
        mock_exists.return_value = False

        gate = SecurityScanGate(project_path=javascript_project)
        vulns, errors = await gate.run_npm_audit()

        assert vulns == []
        assert len(errors) == 1
        assert "npm not installed" in errors[0]


class TestScanBehavior:
    """Test scan execution and blocking behavior."""

    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate.run_pip_audit")
    async def test_scan_blocks_on_critical(
        self,
        mock_audit: AsyncMock,
        python_project: Path,
    ) -> None:
        """Test that scan blocks on CRITICAL vulnerabilities."""
        critical_vuln = Vulnerability(
            package="test-pkg",
            version="1.0.0",
            vulnerability_id="CVE-2023-00001",
            severity="CRITICAL",
            description="Critical security issue",
        )
        mock_audit.return_value = ([critical_vuln], [])

        gate = SecurityScanGate(project_path=python_project)
        result = await gate.scan(use_cache=False)

        assert result.passed is False
        assert result.critical_count == 1
        assert result.blocking_count == 1

    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate.run_pip_audit")
    async def test_scan_blocks_on_high(
        self,
        mock_audit: AsyncMock,
        python_project: Path,
    ) -> None:
        """Test that scan blocks on HIGH vulnerabilities."""
        high_vuln = Vulnerability(
            package="test-pkg",
            version="1.0.0",
            vulnerability_id="CVE-2023-00002",
            severity="HIGH",
            description="High severity security issue",
        )
        mock_audit.return_value = ([high_vuln], [])

        gate = SecurityScanGate(project_path=python_project)
        result = await gate.scan(use_cache=False)

        assert result.passed is False
        assert result.high_count == 1
        assert result.blocking_count == 1

    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate.run_pip_audit")
    async def test_scan_warns_on_medium(
        self,
        mock_audit: AsyncMock,
        python_project: Path,
    ) -> None:
        """Test that scan passes but warns on MEDIUM vulnerabilities."""
        medium_vuln = Vulnerability(
            package="test-pkg",
            version="1.0.0",
            vulnerability_id="CVE-2023-00003",
            severity="MEDIUM",
            description="Medium severity security issue",
        )
        mock_audit.return_value = ([medium_vuln], [])

        gate = SecurityScanGate(project_path=python_project)
        result = await gate.scan(use_cache=False)

        assert result.passed is True  # Does not block
        assert result.medium_count == 1
        assert result.blocking_count == 0

    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate.run_pip_audit")
    async def test_scan_unknown_project(
        self,
        mock_audit: AsyncMock,
        temp_project: Path,
    ) -> None:
        """Test scan behavior for unknown project type."""
        gate = SecurityScanGate(project_path=temp_project)
        result = await gate.scan(use_cache=False)

        assert result.passed is True
        assert result.project_type == "unknown"
        assert len(result.errors) == 1
        assert "Unknown project type" in result.errors[0]
        mock_audit.assert_not_called()


class TestCaching:
    """Test caching behavior."""

    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate.run_pip_audit")
    async def test_cache_hit(
        self,
        mock_audit: AsyncMock,
        python_project: Path,
    ) -> None:
        """Test that cached results are used within TTL."""
        vuln = Vulnerability(
            package="test-pkg",
            version="1.0.0",
            vulnerability_id="CVE-2023-00004",
            severity="HIGH",
            description="Test vulnerability",
        )
        mock_audit.return_value = ([vuln], [])

        gate = SecurityScanGate(project_path=python_project)

        # First scan - should run audit
        result1 = await gate.scan(use_cache=True)
        assert result1.cached is False
        assert mock_audit.call_count == 1

        # Second scan - should use cache
        result2 = await gate.scan(use_cache=True)
        assert result2.cached is True
        assert result2.high_count == 1
        assert mock_audit.call_count == 1  # Not called again

    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate.run_pip_audit")
    async def test_cache_miss_after_ttl(
        self,
        mock_audit: AsyncMock,
        python_project: Path,
    ) -> None:
        """Test that cache expires after TTL."""
        mock_audit.return_value = ([], [])

        gate = SecurityScanGate(project_path=python_project)

        # First scan
        result1 = await gate.scan(use_cache=True)
        assert result1.cached is False

        # Manually expire cache by modifying TTL
        original_ttl = SecurityScanGate.CACHE_TTL_HOURS
        SecurityScanGate.CACHE_TTL_HOURS = 0  # Expire immediately

        # Second scan - should run fresh scan
        result2 = await gate.scan(use_cache=True)
        assert result2.cached is False
        assert mock_audit.call_count == 2

        # Restore TTL
        SecurityScanGate.CACHE_TTL_HOURS = original_ttl

    @patch("forge_harness.quality_gates.security_scan.SecurityScanGate.run_pip_audit")
    async def test_cache_disabled(
        self,
        mock_audit: AsyncMock,
        python_project: Path,
    ) -> None:
        """Test that cache can be disabled."""
        mock_audit.return_value = ([], [])

        gate = SecurityScanGate(project_path=python_project)

        # First scan with cache
        await gate.scan(use_cache=True)
        assert mock_audit.call_count == 1

        # Second scan with cache disabled
        result = await gate.scan(use_cache=False)
        assert result.cached is False
        assert mock_audit.call_count == 2


class TestScanResult:
    """Test ScanResult class."""

    def test_scan_result_passed(self) -> None:
        """Test ScanResult when passed."""
        result = ScanResult(
            passed=True,
            project_type="python",
        )
        assert result.passed is True
        assert result.total_count == 0
        assert result.blocking_count == 0

    def test_scan_result_with_vulnerabilities(self) -> None:
        """Test ScanResult with vulnerabilities."""
        vulns = [
            Vulnerability(
                package="pkg1",
                version="1.0.0",
                vulnerability_id="CVE-001",
                severity="CRITICAL",
                description="Critical issue",
            ),
            Vulnerability(
                package="pkg2",
                version="2.0.0",
                vulnerability_id="CVE-002",
                severity="HIGH",
                description="High issue",
            ),
            Vulnerability(
                package="pkg3",
                version="3.0.0",
                vulnerability_id="CVE-003",
                severity="MEDIUM",
                description="Medium issue",
            ),
        ]
        result = ScanResult(
            passed=False,
            project_type="python",
            vulnerabilities=vulns,
            critical_count=1,
            high_count=1,
            medium_count=1,
        )

        assert result.passed is False
        assert result.total_count == 3
        assert result.blocking_count == 2  # CRITICAL + HIGH

    def test_scan_result_summary(self) -> None:
        """Test summary generation."""
        result = ScanResult(
            passed=True,
            project_type="javascript",
            scan_time=1.5,
        )
        summary = result.summary()

        assert "PASSED" in summary
        assert "javascript" in summary
        assert "1.50s" in summary
        assert "No vulnerabilities found" in summary


class TestVulnerability:
    """Test Vulnerability class."""

    def test_vulnerability_str_with_fix(self) -> None:
        """Test string representation of Vulnerability with fix available."""
        vuln = Vulnerability(
            package="test-pkg",
            version="1.0.0",
            vulnerability_id="CVE-2023-12345",
            severity="HIGH",
            description="Test vulnerability",
            fixed_version="1.1.0",
        )

        vuln_str = str(vuln)
        assert "test-pkg@1.0.0" in vuln_str
        assert "CVE-2023-12345" in vuln_str
        assert "[HIGH]" in vuln_str
        assert "fix: 1.1.0" in vuln_str

    def test_vulnerability_str_no_fix(self) -> None:
        """Test string representation of Vulnerability without fix."""
        vuln = Vulnerability(
            package="test-pkg",
            version="1.0.0",
            vulnerability_id="CVE-2023-12345",
            severity="CRITICAL",
            description="Test vulnerability",
            fixed_version=None,
        )

        vuln_str = str(vuln)
        assert "no fix available" in vuln_str


@pytest.mark.asyncio
async def test_run_security_scan_factory() -> None:
    """Test run_security_scan factory function."""
    with patch("forge_harness.quality_gates.security_scan.SecurityScanGate") as mock_gate_class:
        mock_gate = MagicMock()
        mock_gate.scan = AsyncMock(return_value=ScanResult(passed=True, project_type="python"))
        mock_gate_class.return_value = mock_gate

        result = await run_security_scan()

        assert result.passed is True
        mock_gate.scan.assert_called_once_with(use_cache=True)
