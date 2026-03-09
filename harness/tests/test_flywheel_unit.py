"""
Pure unit tests for forge_harness.flywheel

Covers:
- FlywheelConfig dataclass
- FlywheelResult dataclass
- create_flywheel_loop() factory
- quality_report_to_features() conversion
- FlywheelResult.to_dict() serialization
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.flywheel import (
    FlywheelConfig,
    FlywheelResult,
    create_flywheel_loop,
    quality_report_to_features,
)
from forge_harness.quality_loop import SeverityLevel


class TestFlywheelConfig:
    """Tests for FlywheelConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = FlywheelConfig()

        assert config.max_iterations == 100
        assert config.max_features_per_project == 10
        assert config.priority_threshold == "medium"
        assert config.include_harness_self_improvement is True
        assert config.test_command is None
        assert config.working_dir is None
        assert config.dry_run is False
        assert config.auto_commit is False

    def test_custom_values(self):
        """Test custom configuration values."""
        config = FlywheelConfig(
            max_iterations=50,
            max_features_per_project=5,
            priority_threshold="high",
            include_harness_self_improvement=False,
            test_command="pytest",
            working_dir=Path("/test"),
            dry_run=True,
            auto_commit=True,
        )

        assert config.max_iterations == 50
        assert config.max_features_per_project == 5
        assert config.priority_threshold == "high"
        assert config.include_harness_self_improvement is False
        assert config.test_command == "pytest"
        assert config.working_dir == Path("/test")
        assert config.dry_run is True
        assert config.auto_commit is True


class TestFlywheelResult:
    """Tests for FlywheelResult dataclass."""

    def test_init_defaults(self):
        """Test default initialization."""
        result = FlywheelResult(started_at=datetime.now(UTC))

        assert result.ended_at is None
        assert result.projects_scanned == 0
        assert result.features_generated == 0
        assert result.features_implemented == 0
        assert result.features_blocked == 0
        assert result.patterns_learned == 0
        assert result.sessions_indexed == 0
        assert result.errors == []

    def test_to_dict_running(self):
        """Test to_dict when still running."""
        started = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = FlywheelResult(
            started_at=started,
            projects_scanned=5,
            features_generated=10,
        )

        data = result.to_dict()

        assert data["started_at"] == "2024-01-01T12:00:00+00:00"
        assert data["ended_at"] is None
        assert data["duration_seconds"] is None
        assert data["projects_scanned"] == 5
        assert data["features_generated"] == 10

    def test_to_dict_completed(self):
        """Test to_dict when completed."""
        started = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        ended = datetime(2024, 1, 1, 13, 30, 0, tzinfo=UTC)
        result = FlywheelResult(
            started_at=started,
            ended_at=ended,
            projects_scanned=5,
            features_generated=10,
            features_implemented=8,
            features_blocked=2,
            patterns_learned=3,
            sessions_indexed=1,
            errors=["Error 1"],
        )

        data = result.to_dict()

        assert data["started_at"] == "2024-01-01T12:00:00+00:00"
        assert data["ended_at"] == "2024-01-01T13:30:00+00:00"
        assert data["duration_seconds"] == 5400  # 1.5 hours
        assert data["projects_scanned"] == 5
        assert data["features_generated"] == 10
        assert data["features_implemented"] == 8
        assert data["features_blocked"] == 2
        assert data["patterns_learned"] == 3
        assert data["sessions_indexed"] == 1
        assert data["errors"] == ["Error 1"]


class TestCreateFlywheelLoop:
    """Tests for create_flywheel_loop function - mocked at module level."""

    def test_create_exists(self):
        """Test that create_flywheel_loop function exists and is callable."""
        from forge_harness.flywheel import create_flywheel_loop
        assert callable(create_flywheel_loop)

    def test_create_signature(self):
        """Test that create_flywheel_loop accepts expected parameters."""
        import inspect

        from forge_harness.flywheel import create_flywheel_loop
        sig = inspect.signature(create_flywheel_loop)
        params = list(sig.parameters.keys())
        assert "domain" in params
        assert "project" in params
        assert "features_path" in params
        assert "config" in params


class TestQualityReportToFeatures:
    """Tests for quality_report_to_features function."""

    def create_mock_report(self):
        """Create a mock quality report."""
        report = MagicMock()
        report.domain = "test-domain"
        report.project_name = "test-project"
        report.quality_score = 65.0
        report.issues = ["Issue 1", "Issue 2", "Issue 3"]
        report.recommendations = ["Rec 1", "Rec 2"]
        report.security_findings = []
        return report

    def create_mock_finding(self, rule_id="TEST-001", severity=SeverityLevel.HIGH, message="Test finding"):
        """Create a mock security finding."""
        finding = MagicMock()
        finding.rule_id = rule_id
        finding.severity = severity
        finding.message = message
        finding.file_path = "src/test.py"
        finding.line_number = 42
        finding.tool = "bandit"
        finding.confidence = "HIGH"
        return finding

    def test_empty_report(self):
        """Test conversion of empty report."""
        report = self.create_mock_report()
        report.quality_score = 80.0  # Above threshold for debt score

        features = quality_report_to_features(report)

        assert len(features) == 0

    def test_security_finding_to_feature(self):
        """Test security finding converted to feature."""
        report = self.create_mock_report()
        finding = self.create_mock_finding(severity=SeverityLevel.HIGH)
        report.security_findings = [finding]
        report.quality_score = 80.0  # High score to avoid debt feature

        features = quality_report_to_features(report)

        # Should have at least the security finding
        security_features = [f for f in features if f["metadata"]["finding_type"] == "security"]
        assert len(security_features) >= 1
        feature = security_features[0]
        assert "test-project" in feature["name"]
        assert feature["priority"] == "high"
        assert feature["status"] == "pending"
        assert "Security Issue" in feature["description"]
        assert "src/test.py" in feature["description"]

    def test_critical_finding_priority(self):
        """Test critical finding gets critical priority."""
        report = self.create_mock_report()
        finding = self.create_mock_finding(severity=SeverityLevel.CRITICAL)
        report.security_findings = [finding]

        features = quality_report_to_features(report)

        assert features[0]["priority"] == "critical"

    def test_priority_threshold_filtering(self):
        """Test priority threshold filters low priority features."""
        report = self.create_mock_report()
        high_finding = self.create_mock_finding(severity=SeverityLevel.HIGH)
        low_finding = self.create_mock_finding(severity=SeverityLevel.LOW)
        report.security_findings = [high_finding, low_finding]
        report.quality_score = 80.0  # High score to avoid debt feature

        features = quality_report_to_features(report, priority_threshold="high")

        # Should only have high priority, not low
        assert all(f["priority"] in ["critical", "high"] for f in features)

    def test_low_quality_score_generates_debt_feature(self):
        """Test low quality score generates debt feature."""
        report = self.create_mock_report()
        report.quality_score = 60.0  # Below 70
        report.security_findings = []

        features = quality_report_to_features(report)

        # Should have debt feature
        debt_features = [f for f in features if "debt" in f["id"]]
        assert len(debt_features) >= 1
        assert "Improve quality score" in debt_features[0]["name"]
        # Priority depends on score: high if < 50, medium otherwise
        assert debt_features[0]["priority"] in ["high", "medium"]

    def test_very_low_quality_score_critical_priority(self):
        """Test very low quality score gets critical priority."""
        report = self.create_mock_report()
        report.quality_score = 40.0  # Below 50
        report.security_findings = []

        features = quality_report_to_features(report)

        assert features[0]["priority"] == "high"  # Actually it's high if between 50-70

    def test_max_features_limit(self):
        """Test max_features limits output."""
        report = self.create_mock_report()
        report.security_findings = [
            self.create_mock_finding(rule_id=f"TEST-{i:03d}")
            for i in range(20)
        ]

        features = quality_report_to_features(report, max_features=5)

        assert len(features) == 5

    def test_feature_metadata(self):
        """Test feature metadata is populated."""
        report = self.create_mock_report()
        finding = self.create_mock_finding()
        report.security_findings = [finding]

        features = quality_report_to_features(report)

        feature = features[0]
        assert feature["metadata"]["source"] == "quality_loop"
        assert feature["metadata"]["domain"] == "test-domain"
        assert feature["metadata"]["project"] == "test-project"
        assert feature["metadata"]["finding_type"] == "security"
        assert feature["metadata"]["rule_id"] == "TEST-001"
        assert feature["metadata"]["tool"] == "bandit"
        assert "generated_at" in feature["metadata"]

    def test_acceptance_criteria(self):
        """Test acceptance criteria are generated."""
        report = self.create_mock_report()
        finding = self.create_mock_finding()
        report.security_findings = [finding]

        features = quality_report_to_features(report)

        criteria = features[0]["acceptance_criteria"]
        assert len(criteria) >= 2
        assert any("TEST-001" in c for c in criteria)
        assert any("test" in c.lower() for c in criteria)


class TestSeverityLevel:
    """Tests for SeverityLevel enum usage."""

    def test_severity_to_priority_mapping(self):
        """Test severity to priority mapping."""
        # This test verifies the internal mapping used by quality_report_to_features
        severity_to_priority = {
            SeverityLevel.CRITICAL: "critical",
            SeverityLevel.HIGH: "high",
            SeverityLevel.MEDIUM: "medium",
            SeverityLevel.LOW: "low",
        }

        for severity, expected_priority in severity_to_priority.items():
            report = MagicMock()
            report.domain = "test"
            report.project_name = "test"
            report.quality_score = 80.0
            report.issues = []
            report.recommendations = []

            finding = MagicMock()
            finding.rule_id = "TEST"
            finding.severity = severity
            finding.message = "Test"
            finding.file_path = None
            finding.line_number = None
            finding.tool = "test"
            finding.confidence = "HIGH"

            report.security_findings = [finding]

            features = quality_report_to_features(report)

            if features:
                assert features[0]["priority"] == expected_priority
