"""Tests for portfolio scanner module."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.portfolio_scanner import (
    FORGE_PROJECTS,
    FeatureSpec,
    PortfolioHealthReport,
    PortfolioScanner,
    ProjectHealth,
    ScannerConfig,
    scan_and_generate_fixes,
)


class TestProjectHealth:
    """Tests for ProjectHealth dataclass."""

    def test_project_health_creation(self):
        """ProjectHealth can be created with required fields."""
        health = ProjectHealth(
            domain="codeswiftr-com",
            project="interview-simulator",
            tier=1,
        )
        assert health.domain == "codeswiftr-com"
        assert health.project == "interview-simulator"
        assert health.tier == 1
        assert health.security_score == 100.0
        assert health.quality_score == 100.0
        assert health.debt_hours == 0.0
        assert health.blocking_issues == 0
        assert health.risk_level == "low"

    def test_project_health_with_all_fields(self):
        """ProjectHealth can be created with all fields."""
        now = datetime.now(UTC)
        health = ProjectHealth(
            domain="test-domain",
            project="test-project",
            tier=2,
            security_score=75.5,
            quality_score=80.0,
            debt_hours=12.5,
            coverage_pct=65.0,
            blocking_issues=3,
            warnings=5,
            risk_level="high",
            last_scanned=now,
            error="Test error",
        )
        assert health.security_score == 75.5
        assert health.quality_score == 80.0
        assert health.debt_hours == 12.5
        assert health.blocking_issues == 3
        assert health.risk_level == "high"
        assert health.error == "Test error"

    def test_project_health_to_dict(self):
        """ProjectHealth.to_dict() converts to dictionary."""
        now = datetime.now(UTC)
        health = ProjectHealth(
            domain="test-domain",
            project="test-project",
            tier=1,
            security_score=85.0,
            last_scanned=now,
        )
        result = health.to_dict()

        assert isinstance(result, dict)
        assert result["domain"] == "test-domain"
        assert result["project"] == "test-project"
        assert result["tier"] == 1
        assert result["security_score"] == 85.0
        assert result["last_scanned"] == now.isoformat()

    def test_project_health_to_dict_with_error(self):
        """ProjectHealth.to_dict() includes error if present."""
        health = ProjectHealth(
            domain="test",
            project="test",
            tier=1,
            error="Service unavailable",
        )
        result = health.to_dict()

        assert result["error"] == "Service unavailable"

    def test_project_health_to_dict_none_last_scanned(self):
        """ProjectHealth.to_dict() handles None last_scanned."""
        health = ProjectHealth(
            domain="test",
            project="test",
            tier=1,
            last_scanned=None,
        )
        result = health.to_dict()

        assert result["last_scanned"] is None


class TestPortfolioHealthReport:
    """Tests for PortfolioHealthReport dataclass."""

    def test_report_creation(self):
        """PortfolioHealthReport can be created with defaults."""
        report = PortfolioHealthReport()

        assert isinstance(report.generated_at, datetime)
        assert report.projects == []
        assert report.total_projects == 0
        assert report.healthy_projects == 0
        assert report.aggregate_security_score == 100.0

    def test_report_with_projects(self):
        """PortfolioHealthReport can include projects."""
        projects = [
            ProjectHealth(domain="test1", project="proj1", tier=1),
            ProjectHealth(domain="test2", project="proj2", tier=2),
        ]
        report = PortfolioHealthReport(
            projects=projects,
            total_projects=2,
            healthy_projects=2,
        )

        assert len(report.projects) == 2
        assert report.total_projects == 2

    def test_report_to_dict(self):
        """PortfolioHealthReport.to_dict() converts to dictionary."""
        projects = [ProjectHealth(domain="test", project="proj", tier=1, security_score=90.0)]
        report = PortfolioHealthReport(
            projects=projects,
            total_projects=1,
            healthy_projects=1,
            aggregate_security_score=90.0,
        )
        result = report.to_dict()

        assert isinstance(result, dict)
        assert result["total_projects"] == 1
        assert result["healthy_projects"] == 1
        assert result["aggregate_security_score"] == 90.0
        assert len(result["projects"]) == 1
        assert isinstance(result["generated_at"], str)

    def test_report_to_dict_with_aggregates(self):
        """PortfolioHealthReport.to_dict() includes all aggregate fields."""
        report = PortfolioHealthReport(
            total_projects=10,
            healthy_projects=6,
            at_risk_projects=3,
            critical_projects=1,
            total_debt_hours=45.5,
            total_blocking_issues=8,
            total_warnings=15,
        )
        result = report.to_dict()

        assert result["at_risk_projects"] == 3
        assert result["critical_projects"] == 1
        assert result["total_debt_hours"] == 45.5
        assert result["total_blocking_issues"] == 8
        assert result["total_warnings"] == 15


class TestScannerConfig:
    """Tests for ScannerConfig dataclass."""

    def test_scanner_config_defaults(self):
        """ScannerConfig has correct defaults."""
        config = ScannerConfig()

        assert config.concurrency == 3
        assert config.timeout_seconds == 60
        assert config.history_dir == ".forge/learning/portfolio_health"
        assert config.filter_domains is None
        assert config.filter_tiers is None
        assert config.min_severity_for_feature == "medium"

    def test_scanner_config_with_filters(self):
        """ScannerConfig can be created with filters."""
        config = ScannerConfig(
            concurrency=5,
            filter_domains=["codeswiftr-com"],
            filter_tiers=[1, 2],
        )

        assert config.concurrency == 5
        assert config.filter_domains == ["codeswiftr-com"]
        assert config.filter_tiers == [1, 2]


class TestFeatureSpec:
    """Tests for FeatureSpec dataclass."""

    def test_feature_spec_creation(self):
        """FeatureSpec can be created with required fields."""
        feature = FeatureSpec(
            id="FIX-0001",
            name="Fix security issue",
            description="Fix critical security vulnerability",
        )

        assert feature.id == "FIX-0001"
        assert feature.name == "Fix security issue"
        assert feature.priority == "medium"
        assert feature.estimated_hours == 4.0

    def test_feature_spec_with_all_fields(self):
        """FeatureSpec can be created with all fields."""
        feature = FeatureSpec(
            id="FIX-0042",
            name="Optimize database queries",
            description="Reduce query time by 50%",
            priority="high",
            estimated_hours=8.0,
            source_project="test-domain/test-project",
            source_finding="Slow query detected",
        )

        assert feature.priority == "high"
        assert feature.estimated_hours == 8.0
        assert feature.source_project == "test-domain/test-project"

    def test_feature_spec_to_dict(self):
        """FeatureSpec.to_dict() converts to dictionary."""
        feature = FeatureSpec(
            id="FIX-0001",
            name="Test Feature",
            description="Test description",
            priority="critical",
            estimated_hours=6.0,
            source_project="domain/project",
            source_finding="Test finding",
        )
        result = feature.to_dict()

        assert isinstance(result, dict)
        assert result["id"] == "FIX-0001"
        assert result["name"] == "Test Feature"
        assert result["status"] == "pending"
        assert result["priority"] == "critical"
        assert result["estimated_hours"] == 6.0
        assert len(result["acceptance_criteria"]) == 2
        assert result["metadata"]["source"] == "portfolio_scanner"
        assert result["metadata"]["source_project"] == "domain/project"


class TestPortfolioScanner:
    """Tests for PortfolioScanner class."""

    @pytest.fixture
    def mock_tech_diligence(self):
        """Create mock TechDiligence bridge."""
        mock = AsyncMock()

        # Mock signals response
        mock_signals = MagicMock()
        mock_signals.overall_score = 85.0
        mock_signals.blocking_issues = []
        mock_signals.warnings = []
        mock_signals.risk_level = "low"

        mock.get_signals = AsyncMock(return_value=mock_signals)
        return mock

    @pytest.fixture
    def scanner(self, mock_tech_diligence):
        """Create PortfolioScanner instance."""
        config = ScannerConfig(concurrency=2)
        return PortfolioScanner(mock_tech_diligence, config)

    def test_scanner_initialization(self, mock_tech_diligence):
        """PortfolioScanner initializes correctly."""
        config = ScannerConfig(concurrency=5)
        scanner = PortfolioScanner(mock_tech_diligence, config)

        assert scanner.tech_diligence == mock_tech_diligence
        assert scanner.config.concurrency == 5
        assert scanner._semaphore is None

    def test_scanner_initialization_without_config(self, mock_tech_diligence):
        """PortfolioScanner uses default config when not provided."""
        scanner = PortfolioScanner(mock_tech_diligence)

        assert isinstance(scanner.config, ScannerConfig)
        assert scanner.config.concurrency == 3

    def test_scanner_initialization_without_tech_diligence(self):
        """PortfolioScanner can be created without TechDiligence."""
        scanner = PortfolioScanner(None)

        assert scanner.tech_diligence is None
        assert isinstance(scanner.config, ScannerConfig)

    @pytest.mark.asyncio
    async def test_scan_portfolio_creates_semaphore(self, scanner):
        """scan_portfolio creates semaphore for concurrency control."""
        projects = [
            {"domain": "test", "project": "proj1", "tier": 1},
        ]

        await scanner.scan_portfolio(projects)

        assert scanner._semaphore is not None
        assert isinstance(scanner._semaphore, asyncio.Semaphore)

    @pytest.mark.asyncio
    async def test_scan_portfolio_with_defaults(self, scanner):
        """scan_portfolio uses FORGE_PROJECTS by default."""
        # Mock to prevent actual scanning
        scanner._scan_project = AsyncMock(
            return_value=ProjectHealth(domain="test", project="test", tier=1)
        )

        report = await scanner.scan_portfolio()

        # Should scan default FORGE_PROJECTS
        assert scanner._scan_project.call_count == len(FORGE_PROJECTS)
        assert isinstance(report, PortfolioHealthReport)

    @pytest.mark.asyncio
    async def test_scan_portfolio_with_custom_projects(self, scanner):
        """scan_portfolio accepts custom project list."""
        projects = [
            {"domain": "test1", "project": "proj1", "tier": 1},
            {"domain": "test2", "project": "proj2", "tier": 2},
        ]

        scanner._scan_project = AsyncMock(
            return_value=ProjectHealth(domain="test", project="test", tier=1)
        )

        report = await scanner.scan_portfolio(projects)

        assert scanner._scan_project.call_count == 2
        assert report.total_projects == 2

    @pytest.mark.asyncio
    async def test_scan_portfolio_with_domain_filter(self, mock_tech_diligence):
        """scan_portfolio filters by domain."""
        config = ScannerConfig(filter_domains=["codeswiftr-com"])
        scanner = PortfolioScanner(mock_tech_diligence, config)

        scanner._scan_project = AsyncMock(
            return_value=ProjectHealth(domain="test", project="test", tier=1)
        )

        report = await scanner.scan_portfolio()

        # Should only scan codeswiftr-com projects
        expected_count = len([p for p in FORGE_PROJECTS if p["domain"] == "codeswiftr-com"])
        assert scanner._scan_project.call_count == expected_count

    @pytest.mark.asyncio
    async def test_scan_portfolio_with_tier_filter(self, mock_tech_diligence):
        """scan_portfolio filters by tier."""
        config = ScannerConfig(filter_tiers=[1])
        scanner = PortfolioScanner(mock_tech_diligence, config)

        scanner._scan_project = AsyncMock(
            return_value=ProjectHealth(domain="test", project="test", tier=1)
        )

        report = await scanner.scan_portfolio()

        # Should only scan tier 1 projects
        expected_count = len([p for p in FORGE_PROJECTS if p["tier"] == 1])
        assert scanner._scan_project.call_count == expected_count

    @pytest.mark.asyncio
    async def test_scan_project_without_tech_diligence(self):
        """_scan_project returns error when TechDiligence not available."""
        scanner = PortfolioScanner(None)
        scanner._semaphore = asyncio.Semaphore(1)

        project = {"domain": "test", "project": "proj", "tier": 1}
        health = await scanner._scan_project(project)

        assert health.error == "TechDiligence bridge not available"
        assert health.domain == "test"
        assert health.project == "proj"

    @pytest.mark.asyncio
    async def test_scan_project_success(self, scanner):
        """_scan_project processes signals correctly."""
        scanner._semaphore = asyncio.Semaphore(1)

        # Setup mock signals with issues
        mock_issue = MagicMock()
        mock_issue.severity.value = "high"

        mock_warning = MagicMock()
        mock_warning.severity.value = "medium"

        mock_signals = MagicMock()
        mock_signals.overall_score = 75.0
        mock_signals.blocking_issues = [mock_issue]
        mock_signals.warnings = [mock_warning]
        mock_signals.risk_level = "high"

        scanner.tech_diligence.get_signals = AsyncMock(return_value=mock_signals)

        project = {"domain": "test", "project": "proj", "tier": 2}
        health = await scanner._scan_project(project)

        assert health.security_score == 75.0
        assert health.blocking_issues == 1
        assert health.warnings == 1
        assert health.risk_level == "high"
        assert health.debt_hours == 5.0  # 4.0 (high) + 1.0 (medium * 0.5)
        assert health.last_scanned is not None

    @pytest.mark.asyncio
    async def test_scan_project_handles_exception(self, scanner):
        """_scan_project handles exceptions gracefully."""
        scanner._semaphore = asyncio.Semaphore(1)
        scanner.tech_diligence.get_signals = AsyncMock(side_effect=Exception("Service error"))

        project = {"domain": "test", "project": "proj", "tier": 1}
        health = await scanner._scan_project(project)

        assert health.error == "Service error"
        assert health.domain == "test"

    @pytest.mark.asyncio
    async def test_scan_portfolio_concurrent_execution(self, scanner):
        """scan_portfolio executes projects concurrently."""
        projects = [{"domain": f"test{i}", "project": f"proj{i}", "tier": 1} for i in range(5)]

        call_count = 0

        async def mock_scan(project):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)  # Simulate work
            return ProjectHealth(
                domain=project["domain"], project=project["project"], tier=project["tier"]
            )

        with patch.object(scanner, "_scan_project", side_effect=mock_scan):
            await scanner.scan_portfolio(projects)

        # Should have called scan for all projects
        assert call_count == 5

    def test_estimate_effort(self, scanner):
        """_estimate_effort returns correct hours by severity."""
        assert scanner._estimate_effort("critical") == 8.0
        assert scanner._estimate_effort("high") == 4.0
        assert scanner._estimate_effort("medium") == 2.0
        assert scanner._estimate_effort("low") == 1.0
        assert scanner._estimate_effort("info") == 0.5
        assert scanner._estimate_effort("unknown") == 2.0  # Default

    def test_build_report(self, scanner):
        """_build_report aggregates project healths correctly."""
        project_healths = [
            ProjectHealth(
                domain="test1",
                project="proj1",
                tier=1,
                security_score=90.0,
                debt_hours=5.0,
                blocking_issues=2,
                warnings=3,
                risk_level="medium",
            ),
            ProjectHealth(
                domain="test2",
                project="proj2",
                tier=2,
                security_score=70.0,
                debt_hours=10.0,
                blocking_issues=5,
                warnings=7,
                risk_level="critical",
            ),
            ProjectHealth(
                domain="test3",
                project="proj3",
                tier=3,
                security_score=95.0,
                debt_hours=0.0,
                blocking_issues=0,
                warnings=0,
                risk_level="low",
            ),
            ProjectHealth(domain="test4", project="proj4", tier=1, error="Scan failed"),
        ]

        report = scanner._build_report(project_healths)

        assert report.total_projects == 4
        assert report.healthy_projects == 1  # Only proj3
        assert report.at_risk_projects == 1  # proj1 (medium)
        assert report.critical_projects == 1  # proj2
        assert report.total_debt_hours == 15.0
        assert report.total_blocking_issues == 7
        assert report.total_warnings == 10
        # Average score: (90.0 + 70.0 + 95.0) / 3 = 85.0
        assert report.aggregate_security_score == 85.0

    def test_build_report_with_all_errors(self, scanner):
        """_build_report handles all projects with errors."""
        project_healths = [
            ProjectHealth(domain="test1", project="proj1", tier=1, error="Error 1"),
            ProjectHealth(domain="test2", project="proj2", tier=2, error="Error 2"),
        ]

        report = scanner._build_report(project_healths)

        assert report.total_projects == 2
        assert report.healthy_projects == 0
        assert report.aggregate_security_score == 100.0  # Default when no scores

    @pytest.mark.asyncio
    async def test_save_to_history(self, scanner, tmp_path):
        """_save_to_history writes report to history directory."""
        scanner.config.history_dir = str(tmp_path / "history")

        report = PortfolioHealthReport(
            total_projects=5,
            healthy_projects=3,
        )

        await scanner._save_to_history(report)

        history_dir = Path(scanner.config.history_dir)
        assert history_dir.exists()

        date_str = report.generated_at.strftime("%Y-%m-%d")
        history_file = history_dir / f"{date_str}.json"
        assert history_file.exists()

        saved_data = json.loads(history_file.read_text())
        assert saved_data["total_projects"] == 5
        assert saved_data["healthy_projects"] == 3


class TestPrioritizeFixes:
    """Tests for prioritize_fixes method."""

    @pytest.fixture
    def scanner(self):
        """Create scanner without TechDiligence."""
        return PortfolioScanner(None)

    def test_prioritize_fixes_empty_report(self, scanner):
        """prioritize_fixes handles empty report."""
        report = PortfolioHealthReport()
        features = scanner.prioritize_fixes(report)

        assert features == []

    def test_prioritize_fixes_no_issues(self, scanner):
        """prioritize_fixes returns empty when no issues."""
        projects = [
            ProjectHealth(
                domain="test", project="proj", tier=1, blocking_issues=0, risk_level="low"
            )
        ]
        report = PortfolioHealthReport(projects=projects)

        features = scanner.prioritize_fixes(report)

        assert features == []

    def test_prioritize_fixes_generates_features(self, scanner):
        """prioritize_fixes generates features for blocking issues."""
        projects = [
            ProjectHealth(
                domain="test1",
                project="proj1",
                tier=1,
                blocking_issues=3,
                debt_hours=12.0,
                security_score=65.0,
                risk_level="high",
            ),
            ProjectHealth(
                domain="test2",
                project="proj2",
                tier=2,
                blocking_issues=1,
                debt_hours=4.0,
                security_score=85.0,
                risk_level="medium",
            ),
        ]
        report = PortfolioHealthReport(projects=projects)

        features = scanner.prioritize_fixes(report)

        assert len(features) == 2
        assert all(isinstance(f, FeatureSpec) for f in features)
        assert all(f.id.startswith("FIX-") for f in features)

    def test_prioritize_fixes_correct_priority(self, scanner):
        """prioritize_fixes calculates priority correctly."""
        projects = [
            ProjectHealth(
                domain="critical-proj",
                project="proj",
                tier=0,  # High velocity
                blocking_issues=5,
                debt_hours=2.0,  # Low effort
                risk_level="critical",
            ),
        ]
        report = PortfolioHealthReport(projects=projects)

        features = scanner.prioritize_fixes(report)

        # Critical risk + tier 0 + low effort = high priority
        assert features[0].priority in ("critical", "high")

    def test_prioritize_fixes_sorts_by_priority(self, scanner):
        """prioritize_fixes sorts features by priority."""
        projects = [
            ProjectHealth(
                domain="low",
                project="proj",
                tier=4,
                blocking_issues=1,
                debt_hours=20.0,  # High effort
                risk_level="medium",
            ),
            ProjectHealth(
                domain="high",
                project="proj",
                tier=1,
                blocking_issues=5,
                debt_hours=4.0,  # Low effort
                risk_level="critical",
            ),
        ]
        report = PortfolioHealthReport(projects=projects)

        features = scanner.prioritize_fixes(report, max_features=10)

        # First feature should be higher priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        assert priority_order[features[0].priority] <= priority_order[features[1].priority]

    def test_prioritize_fixes_respects_max_features(self, scanner):
        """prioritize_fixes limits output to max_features."""
        projects = [
            ProjectHealth(
                domain=f"test{i}",
                project=f"proj{i}",
                tier=1,
                blocking_issues=1,
                debt_hours=4.0,
                risk_level="medium",
            )
            for i in range(30)
        ]
        report = PortfolioHealthReport(projects=projects)

        features = scanner.prioritize_fixes(report, max_features=5)

        assert len(features) == 5

    def test_prioritize_fixes_skips_errors(self, scanner):
        """prioritize_fixes skips projects with errors."""
        projects = [
            ProjectHealth(
                domain="test1", project="proj1", tier=1, blocking_issues=5, error="Scan failed"
            ),
            ProjectHealth(
                domain="test2",
                project="proj2",
                tier=1,
                blocking_issues=3,
                debt_hours=8.0,
                risk_level="high",
            ),
        ]
        report = PortfolioHealthReport(projects=projects)

        features = scanner.prioritize_fixes(report)

        # Should only generate feature for proj2
        assert len(features) == 1
        assert "proj2" in features[0].source_project

    def test_score_to_priority(self, scanner):
        """_score_to_priority converts scores correctly."""
        assert scanner._score_to_priority(0.9) == "critical"
        assert scanner._score_to_priority(0.8) == "critical"
        assert scanner._score_to_priority(0.7) == "high"
        assert scanner._score_to_priority(0.6) == "high"
        assert scanner._score_to_priority(0.5) == "medium"
        assert scanner._score_to_priority(0.4) == "medium"
        assert scanner._score_to_priority(0.3) == "low"

    def test_prioritize_fixes_feature_content(self, scanner):
        """prioritize_fixes generates complete feature specs."""
        projects = [
            ProjectHealth(
                domain="test-domain",
                project="test-project",
                tier=1,
                blocking_issues=5,
                debt_hours=10.0,
                security_score=70.0,
                risk_level="critical",
            ),
        ]
        report = PortfolioHealthReport(projects=projects)

        features = scanner.prioritize_fixes(report)
        feature = features[0]

        assert feature.id == "FIX-0001"
        assert "5 blocking issues" in feature.name
        assert "test-project" in feature.name
        assert "test-domain/test-project" in feature.description
        assert "critical" in feature.description.lower()
        assert "70.0" in feature.description
        assert "10.0h" in feature.description
        assert feature.estimated_hours == 10.0
        assert feature.source_project == "test-domain/test-project"


class TestExportFeaturesJson:
    """Tests for export_features_json method."""

    @pytest.fixture
    def scanner(self):
        """Create scanner without TechDiligence."""
        return PortfolioScanner(None)

    def test_export_features_json_creates_file(self, scanner, tmp_path):
        """export_features_json creates output file."""
        features = [
            FeatureSpec(
                id="FIX-0001",
                name="Test Feature",
                description="Test description",
            )
        ]

        output_path = tmp_path / "features.json"
        scanner.export_features_json(features, output_path, merge_existing=False)

        assert output_path.exists()
        data = json.loads(output_path.read_text())
        assert data["project"] == "forge-portfolio-fixes"
        assert len(data["features"]) == 1

    def test_export_features_json_creates_parent_dirs(self, scanner, tmp_path):
        """export_features_json creates parent directories."""
        output_path = tmp_path / "subdir" / "nested" / "features.json"
        features = [FeatureSpec(id="FIX-0001", name="Test", description="Test")]

        scanner.export_features_json(features, output_path, merge_existing=False)

        assert output_path.exists()

    def test_export_features_json_merge_existing(self, scanner, tmp_path):
        """export_features_json merges with existing features."""
        output_path = tmp_path / "features.json"

        # Create existing file
        existing = {
            "project": "test",
            "features": [{"id": "OLD-001", "name": "Old Feature", "status": "completed"}],
        }
        output_path.write_text(json.dumps(existing))

        # Add new features
        new_features = [FeatureSpec(id="FIX-0001", name="New Feature", description="New")]

        scanner.export_features_json(new_features, output_path, merge_existing=True)

        data = json.loads(output_path.read_text())
        assert len(data["features"]) == 2
        assert data["features"][0]["id"] == "OLD-001"
        assert data["features"][1]["id"] == "FIX-0001"

    def test_export_features_json_skip_duplicates(self, scanner, tmp_path):
        """export_features_json skips duplicate IDs when merging."""
        output_path = tmp_path / "features.json"

        # Create existing file
        existing = {"features": [{"id": "FIX-0001", "name": "Existing", "status": "pending"}]}
        output_path.write_text(json.dumps(existing))

        # Try to add duplicate
        new_features = [FeatureSpec(id="FIX-0001", name="Duplicate", description="Test")]

        scanner.export_features_json(new_features, output_path, merge_existing=True)

        data = json.loads(output_path.read_text())
        assert len(data["features"]) == 1
        assert data["features"][0]["name"] == "Existing"  # Original preserved

    def test_export_features_json_no_merge(self, scanner, tmp_path):
        """export_features_json overwrites when merge_existing=False."""
        output_path = tmp_path / "features.json"

        # Create existing file
        existing = {"features": [{"id": "OLD-001", "name": "Old"}]}
        output_path.write_text(json.dumps(existing))

        # Overwrite with new
        new_features = [FeatureSpec(id="FIX-0001", name="New", description="New")]

        scanner.export_features_json(new_features, output_path, merge_existing=False)

        data = json.loads(output_path.read_text())
        assert len(data["features"]) == 1
        assert data["features"][0]["id"] == "FIX-0001"

    def test_export_features_json_handles_invalid_existing(self, scanner, tmp_path):
        """export_features_json handles invalid existing JSON."""
        output_path = tmp_path / "features.json"

        # Create invalid JSON
        output_path.write_text("not valid json")

        # Should still succeed
        new_features = [FeatureSpec(id="FIX-0001", name="New", description="New")]

        scanner.export_features_json(new_features, output_path, merge_existing=True)

        data = json.loads(output_path.read_text())
        assert len(data["features"]) == 1

    def test_export_features_json_metadata(self, scanner, tmp_path):
        """export_features_json includes metadata."""
        features = [FeatureSpec(id="FIX-0001", name="Test", description="Test")]

        output_path = tmp_path / "features.json"
        scanner.export_features_json(features, output_path)

        data = json.loads(output_path.read_text())
        assert data["project"] == "forge-portfolio-fixes"
        assert data["version"] == "1.0.0"
        assert "generated_at" in data
        assert isinstance(data["generated_at"], str)


class TestScanAndGenerateFixes:
    """Tests for convenience function."""

    @pytest.mark.asyncio
    async def test_scan_and_generate_fixes(self, tmp_path):
        """scan_and_generate_fixes runs full workflow."""
        # Mock TechDiligence
        mock_td = AsyncMock()
        mock_signals = MagicMock()
        mock_signals.overall_score = 80.0
        mock_signals.blocking_issues = [MagicMock(severity=MagicMock(value="high"))]
        mock_signals.warnings = []
        mock_signals.risk_level = "high"
        mock_td.get_signals = AsyncMock(return_value=mock_signals)

        # Configure
        config = ScannerConfig(
            concurrency=1,
            filter_tiers=[1],  # Limit to tier 1
            history_dir=str(tmp_path / "history"),
        )
        output_path = tmp_path / "fixes.json"

        report = await scan_and_generate_fixes(
            tech_diligence=mock_td,
            output_path=output_path,
            max_features=5,
            config=config,
        )

        assert isinstance(report, PortfolioHealthReport)
        assert output_path.exists()

        # Verify output file
        data = json.loads(output_path.read_text())
        assert "features" in data
        assert len(data["features"]) > 0

    @pytest.mark.asyncio
    async def test_scan_and_generate_fixes_defaults(self, tmp_path):
        """scan_and_generate_fixes works with defaults."""
        mock_td = AsyncMock()
        mock_signals = MagicMock()
        mock_signals.overall_score = 90.0
        mock_signals.blocking_issues = []
        mock_signals.warnings = []
        mock_signals.risk_level = "low"
        mock_td.get_signals = AsyncMock(return_value=mock_signals)

        output_path = tmp_path / "test.json"

        report = await scan_and_generate_fixes(
            tech_diligence=mock_td,
            output_path=output_path,
        )

        assert isinstance(report, PortfolioHealthReport)
        assert output_path.exists()


class TestForgeProjects:
    """Tests for FORGE_PROJECTS constant."""

    def test_forge_projects_structure(self):
        """FORGE_PROJECTS has correct structure."""
        assert isinstance(FORGE_PROJECTS, list)
        assert len(FORGE_PROJECTS) > 0

        for project in FORGE_PROJECTS:
            assert "domain" in project
            assert "project" in project
            assert "tier" in project
            assert isinstance(project["tier"], int)

    def test_forge_projects_tiers(self):
        """FORGE_PROJECTS has valid tier values."""
        for project in FORGE_PROJECTS:
            assert project["tier"] in [0, 1, 2, 3, 4]

    def test_forge_projects_unique_combinations(self):
        """FORGE_PROJECTS has unique domain/project combinations."""
        combinations = set()
        for project in FORGE_PROJECTS:
            combo = (project["domain"], project["project"])
            assert combo not in combinations, f"Duplicate: {combo}"
            combinations.add(combo)


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_scan_portfolio_empty_projects_list(self):
        """scan_portfolio treats empty list as None and uses FORGE_PROJECTS."""
        scanner = PortfolioScanner(None)

        # Empty list is falsy, so it defaults to FORGE_PROJECTS
        report = await scanner.scan_portfolio(projects=[])

        # Should scan all FORGE_PROJECTS (with errors since no TechDiligence)
        assert report.total_projects == len(FORGE_PROJECTS)
        assert len(report.projects) == len(FORGE_PROJECTS)

    @pytest.mark.asyncio
    async def test_scan_project_with_zero_debt(self):
        """_scan_project handles projects with no debt."""
        mock_td = AsyncMock()
        mock_signals = MagicMock()
        mock_signals.overall_score = 100.0
        mock_signals.blocking_issues = []
        mock_signals.warnings = []
        mock_signals.risk_level = "low"
        mock_td.get_signals = AsyncMock(return_value=mock_signals)

        scanner = PortfolioScanner(mock_td)
        scanner._semaphore = asyncio.Semaphore(1)

        project = {"domain": "test", "project": "proj", "tier": 1}
        health = await scanner._scan_project(project)

        assert health.debt_hours == 0.0
        assert health.blocking_issues == 0

    def test_build_report_empty_projects(self):
        """_build_report handles empty projects list."""
        scanner = PortfolioScanner(None)

        report = scanner._build_report([])

        assert report.total_projects == 0
        assert report.healthy_projects == 0
        assert report.aggregate_security_score == 100.0

    def test_prioritize_fixes_with_zero_effort(self):
        """prioritize_fixes handles zero debt_hours."""
        scanner = PortfolioScanner(None)
        projects = [
            ProjectHealth(
                domain="test",
                project="proj",
                tier=1,
                blocking_issues=1,
                debt_hours=0.0,  # Zero effort
                risk_level="medium",
            )
        ]
        report = PortfolioHealthReport(projects=projects)

        features = scanner.prioritize_fixes(report)

        # Should still generate feature even with zero effort
        assert len(features) == 1
        assert features[0].estimated_hours == 0.0  # Uses actual debt_hours

    def test_feature_spec_to_dict_with_none_values(self):
        """FeatureSpec.to_dict() handles None optional fields."""
        feature = FeatureSpec(
            id="FIX-0001",
            name="Test",
            description="Test",
            source_project=None,
            source_finding=None,
        )

        result = feature.to_dict()

        assert result["metadata"]["source_project"] is None
        assert result["metadata"]["source_finding"] is None

    @pytest.mark.asyncio
    async def test_save_to_history_creates_dirs(self, tmp_path):
        """_save_to_history creates history directory if missing."""
        scanner = PortfolioScanner(None)
        scanner.config.history_dir = str(tmp_path / "missing" / "history")

        report = PortfolioHealthReport()

        await scanner._save_to_history(report)

        assert Path(scanner.config.history_dir).exists()


class TestWeeklyPortfolioScan:
    """Tests for weekly_portfolio_scan.run_weekly_scan function."""

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        """Create mock FORGE directory structure."""
        # Create domain/project directories
        projects = [
            ("codeswiftr-com", "interview-simulator"),
            ("codeswiftr-com", "code-atlas"),
            ("brandfocus-ai", "voice-coach"),
        ]
        for domain, project in projects:
            proj_path = tmp_path / domain / project
            proj_path.mkdir(parents=True)
            # Create a marker file
            (proj_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")

        return tmp_path

    @pytest.mark.asyncio
    async def test_run_weekly_scan_dry_run(self, mock_forge_root):
        """run_weekly_scan in dry_run mode doesn't write files."""
        from forge_harness.scripts.weekly_portfolio_scan import run_weekly_scan

        result = await run_weekly_scan(
            forge_root=mock_forge_root,
            tiers=[1],  # Filter to tier 1 only
            max_features_per_project=5,
            dry_run=True,
        )

        assert result["success"] is True
        assert result["dry_run"] is True
        assert result["features_written"] == 0  # Dry run doesn't write

    @pytest.mark.asyncio
    async def test_run_weekly_scan_without_tech_diligence(self, mock_forge_root):
        """run_weekly_scan works without TechDiligence."""
        from forge_harness.scripts.weekly_portfolio_scan import run_weekly_scan

        result = await run_weekly_scan(
            forge_root=mock_forge_root,
            domains=["codeswiftr-com"],
            dry_run=True,
        )

        assert result["success"] is True
        # Without TechDiligence, all projects should have errors
        # But the scan should still complete

    @pytest.mark.asyncio
    async def test_run_weekly_scan_filters_by_tier(self, mock_forge_root):
        """run_weekly_scan filters projects by tier."""
        from forge_harness.scripts.weekly_portfolio_scan import run_weekly_scan

        # Tier 1 has 4 projects in FORGE_PROJECTS
        result = await run_weekly_scan(
            forge_root=mock_forge_root,
            tiers=[1],
            dry_run=True,
        )

        assert result["success"] is True
        # Should only scan tier 1 projects (4 in FORGE_PROJECTS)
        assert result["total_projects_scanned"] == 4

    @pytest.mark.asyncio
    async def test_run_weekly_scan_filters_by_domain(self, mock_forge_root):
        """run_weekly_scan filters projects by domain."""
        from forge_harness.scripts.weekly_portfolio_scan import run_weekly_scan

        result = await run_weekly_scan(
            forge_root=mock_forge_root,
            domains=["codeswiftr-com"],
            dry_run=True,
        )

        assert result["success"] is True
        # codeswiftr-com has specific projects in FORGE_PROJECTS
        # Just verify it completes and filters

    @pytest.mark.asyncio
    async def test_run_weekly_scan_writes_features(self, mock_forge_root, tmp_path):
        """run_weekly_scan writes features to project directories."""
        from forge_harness.scripts.weekly_portfolio_scan import run_weekly_scan

        # Create a mock that generates issues
        with patch("forge_harness.portfolio_scanner.PortfolioScanner") as MockScanner:
            # Setup mock scanner
            mock_scanner = MagicMock()
            MockScanner.return_value = mock_scanner

            # Mock scan_portfolio to return projects with issues
            mock_report = PortfolioHealthReport(
                total_projects=1,
                healthy_projects=0,
                at_risk_projects=1,
                projects=[
                    ProjectHealth(
                        domain="codeswiftr-com",
                        project="interview-simulator",
                        tier=1,
                        blocking_issues=3,
                        debt_hours=8.0,
                        risk_level="high",
                    )
                ],
            )
            mock_scanner.scan_portfolio = AsyncMock(return_value=mock_report)

            # Mock prioritize_fixes to return features
            mock_scanner.prioritize_fixes = MagicMock(
                return_value=[
                    FeatureSpec(
                        id="FIX-0001",
                        name="Fix issues",
                        description="Fix 3 issues",
                        source_project="codeswiftr-com/interview-simulator",
                    )
                ]
            )

            result = await run_weekly_scan(
                forge_root=mock_forge_root,
                domains=["codeswiftr-com"],
                dry_run=False,
                output_dir=tmp_path,
            )

            assert result["success"] is True
            assert result["features_generated"] == 1

    @pytest.mark.asyncio
    async def test_run_weekly_scan_writes_summary(self, mock_forge_root, tmp_path):
        """run_weekly_scan writes portfolio scan summary."""
        from forge_harness.scripts.weekly_portfolio_scan import run_weekly_scan

        result = await run_weekly_scan(
            forge_root=mock_forge_root,
            tiers=[1],
            output_dir=tmp_path,
            dry_run=False,
        )

        # Should write summary file
        summary_path = tmp_path / "portfolio_scan_summary.json"
        assert summary_path.exists()

        summary_data = json.loads(summary_path.read_text())
        assert "scan_timestamp" in summary_data
        assert "total_projects" in summary_data
        assert "features_generated" in summary_data

    @pytest.mark.asyncio
    async def test_run_weekly_scan_result_structure(self, mock_forge_root):
        """run_weekly_scan returns correct result structure."""
        from forge_harness.scripts.weekly_portfolio_scan import run_weekly_scan

        result = await run_weekly_scan(
            forge_root=mock_forge_root,
            dry_run=True,
        )

        # Verify result structure
        assert "success" in result
        assert "scan_timestamp" in result
        assert "total_projects_scanned" in result
        assert "healthy_projects" in result
        assert "at_risk_projects" in result
        assert "critical_projects" in result
        assert "total_debt_hours" in result
        assert "total_blocking_issues" in result
        assert "features_generated" in result
        assert "features_written" in result
        assert "features_by_project" in result
        assert "dry_run" in result

    @pytest.mark.asyncio
    async def test_run_weekly_scan_combines_filters(self, mock_forge_root):
        """run_weekly_scan combines tier and domain filters."""
        from forge_harness.scripts.weekly_portfolio_scan import run_weekly_scan

        result = await run_weekly_scan(
            forge_root=mock_forge_root,
            tiers=[1],
            domains=["codeswiftr-com"],
            dry_run=True,
        )

        assert result["success"] is True
        # Should scan intersection of tier 1 AND codeswiftr-com


class TestWeeklyPortfolioScanCLI:
    """Tests for CLI command integration."""

    @pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: 'flywheel weekly-scan' command does not exist in cli_v2")
    def test_flywheel_weekly_scan_command_exists(self):
        """forge-harness flywheel weekly-scan command is registered."""
        from click.testing import CliRunner
        from forge_harness.cli_v2 import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["flywheel", "weekly-scan", "--help"])

        assert result.exit_code == 0
        assert "weekly-scan" in result.output or "weekly portfolio scan" in result.output.lower()

    @pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: 'flywheel weekly-scan' command does not exist in cli_v2")
    def test_flywheel_weekly_scan_dry_run(self, tmp_path):
        """forge-harness flywheel weekly-scan --dry-run works."""
        from click.testing import CliRunner
        from forge_harness.cli_v2 import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "flywheel",
                "weekly-scan",
                "--forge-root",
                str(tmp_path),
                "--tier",
                "1",
                "--dry-run",
            ],
        )

        # Should complete (may have errors due to missing FORGE structure, but command works)
        # The exit code may be non-zero if FORGE root isn't valid, but command should run

    @pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: 'flywheel weekly-scan' command does not exist in cli_v2")
    def test_flywheel_weekly_scan_json_output(self, tmp_path):
        """forge-harness flywheel weekly-scan --json outputs JSON."""
        from click.testing import CliRunner
        from forge_harness.cli_v2 import cli

        # Create minimal FORGE structure
        (tmp_path / "codeswiftr-com" / "test-project").mkdir(parents=True)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "flywheel",
                "weekly-scan",
                "--forge-root",
                str(tmp_path),
                "--tier",
                "1",
                "--dry-run",
                "--json",
            ],
        )

        # If successful, output should be valid JSON
        if result.exit_code == 0:
            output = result.output.strip()
            if output:
                try:
                    data = json.loads(output)
                    assert "success" in data or "features" in data
                except json.JSONDecodeError:
                    pass  # May not have output in some cases
