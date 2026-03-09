"""Tests for security scanning quality gate."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from forge_harness.quality_gates.security_gate import (
    SecurityResult,
    SecurityScanner,
    SecurityScannerConfig,
    Severity,
    Vulnerability,
    create_security_scanner,
)


@pytest.fixture
def mock_bandit_output():
    """Mock bandit JSON output."""
    return {
        "results": [
            {
                "filename": "test.py",
                "line_number": 10,
                "test_id": "B101",
                "issue_severity": "HIGH",
                "issue_text": "Use of assert detected",
                "cwe": {"id": "CWE-703"},
            },
            {
                "filename": "test.py",
                "line_number": 20,
                "test_id": "B102",
                "issue_severity": "MEDIUM",
                "issue_text": "Insecure function call",
                "cwe": {"id": "CWE-20"},
            },
            {
                "filename": "test.py",
                "line_number": 30,
                "test_id": "B103",
                "issue_severity": "LOW",
                "issue_text": "Possible hardcoded password",
                "cwe": {},
            },
        ]
    }


@pytest.fixture
def security_scanner():
    """Create a security scanner instance."""
    return SecurityScanner()


@pytest.fixture
def custom_config():
    """Create a custom security scanner config."""
    return SecurityScannerConfig(
        severity_threshold=Severity.HIGH,
        ignore_patterns=["test_*.py"],
        skip_rules=["B101"],
        timeout=60,
    )


class TestSecurityScanner:
    def test_scanner_initialization_default_config(self):
        """Test scanner initializes with default config."""
        scanner = SecurityScanner()
        assert scanner.config is not None
        assert scanner.config.severity_threshold == Severity.MEDIUM
        assert scanner.config.timeout == 300

    def test_scanner_initialization_custom_config(self, custom_config):
        """Test scanner initializes with custom config."""
        scanner = SecurityScanner(custom_config)
        assert scanner.config.severity_threshold == Severity.HIGH
        assert scanner.config.timeout == 60

    def test_create_security_scanner_factory(self):
        """Test factory function creates scanner."""
        scanner = create_security_scanner()
        assert isinstance(scanner, SecurityScanner)

    def test_parse_output_success(self, security_scanner, mock_bandit_output):
        """Test parsing valid bandit output."""
        from datetime import datetime

        start = datetime.now()
        output = json.dumps(mock_bandit_output)

        result = security_scanner._parse_output(output, start)

        assert isinstance(result, SecurityResult)
        assert len(result.vulnerabilities) == 3
        assert result.severity_counts["HIGH"] == 1
        assert result.severity_counts["MEDIUM"] == 1
        assert result.severity_counts["LOW"] == 1
        assert result.duration >= 0

    def test_parse_output_fails_on_threshold(self, mock_bandit_output):
        """Test result fails when vulnerabilities exceed threshold."""
        from datetime import datetime

        config = SecurityScannerConfig(severity_threshold=Severity.MEDIUM)
        scanner = SecurityScanner(config)
        start = datetime.now()
        output = json.dumps(mock_bandit_output)

        result = scanner._parse_output(output, start)

        # Should fail because we have HIGH and MEDIUM severity issues
        assert result.success is False

    def test_parse_output_succeeds_below_threshold(self, mock_bandit_output):
        """Test result succeeds when vulnerabilities below threshold."""
        from datetime import datetime

        config = SecurityScannerConfig(severity_threshold=Severity.HIGH)
        scanner = SecurityScanner(config)
        start = datetime.now()

        # Remove HIGH severity issue
        mock_bandit_output["results"] = [
            r for r in mock_bandit_output["results"] if r["issue_severity"] != "HIGH"
        ]
        output = json.dumps(mock_bandit_output)

        result = scanner._parse_output(output, start)

        # Should succeed because no HIGH severity issues
        assert result.success is True

    def test_parse_output_invalid_json(self, security_scanner):
        """Test parsing handles invalid JSON gracefully."""
        from datetime import datetime

        start = datetime.now()
        output = "invalid json"

        result = security_scanner._parse_output(output, start)

        assert result.success is True
        assert len(result.vulnerabilities) == 0

    def test_vulnerability_dataclass(self):
        """Test Vulnerability dataclass creation."""
        vuln = Vulnerability(
            file="test.py",
            line=10,
            code="B101",
            severity=Severity.HIGH,
            message="Test issue",
            cwe_id="CWE-703",
        )

        assert vuln.file == "test.py"
        assert vuln.line == 10
        assert vuln.severity == Severity.HIGH
        assert vuln.cwe_id == "CWE-703"

    @pytest.mark.asyncio
    async def test_scan_files_timeout(self, custom_config):
        """Test scan_files handles timeout."""
        scanner = SecurityScanner(custom_config)

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            result = await scanner.scan_files([Path("test.py")])

            assert result.success is False
            assert result.duration == custom_config.timeout

    @pytest.mark.asyncio
    async def test_scan_staged_no_python_files(self, security_scanner):
        """Test scan_staged with no Python files."""
        mock_result = Mock()
        mock_result.stdout = "README.md\npackage.json\n"

        with patch("subprocess.run", return_value=mock_result):
            result = await security_scanner.scan_staged()

            assert result.success is True
            assert result.duration == 0.0
            assert len(result.vulnerabilities) == 0

    @pytest.mark.asyncio
    async def test_scan_staged_with_python_files(self, security_scanner, mock_bandit_output):
        """Test scan_staged with Python files."""
        mock_git_result = Mock()
        mock_git_result.stdout = "test.py\nmodule.py\n"

        with patch("subprocess.run", return_value=mock_git_result):
            with patch.object(
                security_scanner, "scan_files", return_value=SecurityResult(success=True)
            ) as mock_scan:
                result = await security_scanner.scan_staged()

                assert result.success is True
                mock_scan.assert_called_once()
                # Verify it was called with Python files
                call_args = mock_scan.call_args[0][0]
                assert len(call_args) == 2
                assert all(str(p).endswith(".py") for p in call_args)

    def test_severity_enum_values(self):
        """Test Severity enum has correct values (IntEnum)."""
        assert Severity.LOW.value == 1
        assert Severity.MEDIUM.value == 2
        assert Severity.HIGH.value == 3
        # Check string representation
        assert str(Severity.LOW) == "LOW"
        assert str(Severity.MEDIUM) == "MEDIUM"
        assert str(Severity.HIGH) == "HIGH"

    def test_severity_enum_ordering(self):
        """Test Severity enum comparison."""
        # IntEnum supports direct comparison
        assert Severity.LOW < Severity.MEDIUM
        assert Severity.MEDIUM < Severity.HIGH
        assert Severity.LOW < Severity.HIGH

    def test_security_result_dataclass_defaults(self):
        """Test SecurityResult dataclass defaults."""
        result = SecurityResult(success=True)

        assert result.success is True
        assert result.vulnerabilities == []
        assert result.severity_counts == {}
        assert result.duration == 0.0


@pytest.mark.integration
class TestSecurityScannerIntegration:
    """Integration tests requiring bandit installed."""

    @pytest.mark.asyncio
    async def test_scan_real_file(self, tmp_path):
        """Test scanning a real Python file."""
        # Create a test file with a known vulnerability
        test_file = tmp_path / "vulnerable.py"
        test_file.write_text("""
import pickle
import os

# B301: Use of pickle (insecure deserialization)
data = pickle.loads(b"data")

# B605: Possible shell injection
os.system("ls -la")
""")

        scanner = SecurityScanner()
        result = await scanner.scan_files([test_file])

        # Should detect vulnerabilities
        assert len(result.vulnerabilities) > 0
        assert result.duration > 0
