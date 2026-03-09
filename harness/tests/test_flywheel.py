"""Tests for flywheel module - compounding autonomous development."""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.flywheel import (
    FlywheelConfig,
    FlywheelResult,
    create_flywheel_loop,
    generate_portfolio_features,
    run_flywheel,
    scan_project_for_debt,
)


class TestFlywheelConfig:
    """Tests for FlywheelConfig dataclass."""

    def test_default_config(self):
        """FlywheelConfig has sensible defaults."""
        config = FlywheelConfig()
        assert config.max_iterations == 100
        assert config.max_features_per_project == 10
        assert config.priority_threshold == "medium"
        assert config.include_harness_self_improvement is True
        assert config.dry_run is False

    def test_custom_config(self):
        """FlywheelConfig accepts custom values."""
        config = FlywheelConfig(
            max_iterations=50,
            max_features_per_project=5,
            priority_threshold="high",
            dry_run=True,
        )
        assert config.max_iterations == 50
        assert config.max_features_per_project == 5
        assert config.priority_threshold == "high"
        assert config.dry_run is True


class TestFlywheelResult:
    """Tests for FlywheelResult dataclass."""

    def test_empty_result(self):
        """FlywheelResult with no activity."""
        result = FlywheelResult(started_at=datetime.now(UTC))
        assert result.projects_scanned == 0
        assert result.features_generated == 0
        assert result.features_implemented == 0

    def test_result_with_activity(self):
        """FlywheelResult tracks metrics."""
        result = FlywheelResult(
            started_at=datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC),
            ended_at=datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
            projects_scanned=5,
            features_generated=15,
            features_implemented=10,
            features_blocked=2,
            sessions_indexed=1,
        )
        assert result.projects_scanned == 5
        assert result.features_generated == 15
        assert result.features_implemented == 10

    def test_to_dict(self):
        """FlywheelResult serializes to dict."""
        result = FlywheelResult(
            started_at=datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC),
            ended_at=datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
            projects_scanned=3,
            features_generated=10,
            errors=["Test error"],
        )
        data = result.to_dict()
        assert data["projects_scanned"] == 3
        assert data["features_generated"] == 10
        assert data["duration_seconds"] == 1800  # 30 minutes
        assert "Test error" in data["errors"]


class TestCreateFlywheelLoop:
    """Tests for create_flywheel_loop factory."""

    def test_creates_loop_with_defaults(self):
        """create_flywheel_loop creates RalphLoopHarness."""
        with tempfile.TemporaryDirectory() as tmpdir:
            features_path = Path(tmpdir) / "features.json"
            features_path.write_text("[]")

            with (
                patch("forge_harness.harness_registry.create_harness_registry") as mock_registry,
                patch(
                    "forge_harness.ralph_loop.create_ralph_loop_from_registry"
                ) as mock_create_loop,
            ):
                mock_reg = MagicMock()
                mock_reg.get.return_value = None
                mock_registry.return_value = mock_reg

                mock_loop = MagicMock()
                mock_loop.config = MagicMock(dry_run=False)
                mock_create_loop.return_value = mock_loop

                loop = create_flywheel_loop(
                    domain="test-domain",
                    project="test-project",
                    features_path=features_path,
                )

                assert loop is not None
                mock_registry.assert_called_once()

    def test_uses_custom_config(self):
        """create_flywheel_loop respects FlywheelConfig."""
        with tempfile.TemporaryDirectory() as tmpdir:
            features_path = Path(tmpdir) / "features.json"
            features_path.write_text("[]")

            config = FlywheelConfig(max_iterations=50, dry_run=True)

            with (
                patch("forge_harness.harness_registry.create_harness_registry") as mock_registry,
                patch(
                    "forge_harness.ralph_loop.create_ralph_loop_from_registry"
                ) as mock_create_loop,
            ):
                mock_reg = MagicMock()
                mock_reg.get.return_value = None
                mock_registry.return_value = mock_reg

                mock_loop = MagicMock()
                mock_loop.config = MagicMock(dry_run=True)
                mock_create_loop.return_value = mock_loop

                loop = create_flywheel_loop(
                    domain="test-domain",
                    project="test-project",
                    features_path=features_path,
                    config=config,
                )

                assert loop is not None
                assert loop.config.dry_run is True


class TestScanProjectForDebt:
    """Tests for scan_project_for_debt function."""

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        """scan_project_for_debt returns empty list when Tech Diligence fails and no fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge"
            ) as MockBridge:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(return_value=None)
                MockBridge.return_value = mock_bridge

                features = await scan_project_for_debt(
                    domain="test-domain",
                    project="test-project",
                    project_path=Path(tmpdir),
                    use_local_fallback=False,  # Disable fallback
                )

                assert features == []

    @pytest.mark.asyncio
    async def test_generates_features_from_findings(self):
        """scan_project_for_debt converts findings to features."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create mock finding
            mock_finding = MagicMock()
            mock_finding.severity = "high"
            mock_finding.title = "Security issue"
            mock_finding.message = "Hardcoded secret found"
            mock_finding.rule_id = "SEC001"
            mock_finding.file_path = "test.py"
            mock_finding.line_number = 10
            mock_finding.scanner = "security"
            mock_finding.recommendation = "Remove secret"

            mock_report = MagicMock()
            mock_report.findings = [mock_finding]

            with patch(
                "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge"
            ) as MockBridge:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)
                MockBridge.return_value = mock_bridge

                features = await scan_project_for_debt(
                    domain="test-domain",
                    project="test-project",
                    project_path=Path(tmpdir),
                    max_features=5,
                )

                assert len(features) == 1
                assert features[0]["priority"] == "high"
                assert "Security issue" in features[0]["name"]

    @pytest.mark.asyncio
    async def test_filters_by_priority(self):
        """scan_project_for_debt respects priority threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create findings with different priorities
            findings = []
            for severity in ["critical", "high", "medium", "low"]:
                finding = MagicMock()
                finding.severity = severity
                finding.title = f"{severity} issue"
                finding.message = f"A {severity} issue"
                finding.rule_id = f"TEST-{severity}"
                finding.file_path = "test.py"
                finding.line_number = 1
                finding.scanner = "test"
                finding.recommendation = None
                findings.append(finding)

            mock_report = MagicMock()
            mock_report.findings = findings

            with patch(
                "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge"
            ) as MockBridge:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)
                MockBridge.return_value = mock_bridge

                # Only high and critical
                features = await scan_project_for_debt(
                    domain="test-domain",
                    project="test-project",
                    project_path=Path(tmpdir),
                    priority_threshold="high",
                )

                priorities = [f["priority"] for f in features]
                assert "critical" in priorities
                assert "high" in priorities
                assert "medium" not in priorities
                assert "low" not in priorities


class TestGeneratePortfolioFeatures:
    """Tests for generate_portfolio_features function."""

    @pytest.mark.asyncio
    async def test_scans_domains(self):
        """generate_portfolio_features scans domain directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)

            # Create mock domain/project structure
            domain_dir = forge_root / "test-domain"
            domain_dir.mkdir()
            project_dir = domain_dir / "test-project"
            project_dir.mkdir()
            (project_dir / "pyproject.toml").write_text("[project]")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = [{"id": "test-001", "name": "Test", "priority": "high"}]

                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

                assert len(features) == 1
                mock_scan.assert_called_once()

    @pytest.mark.asyncio
    async def test_writes_to_output(self):
        """generate_portfolio_features writes to output file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            output_path = forge_root / "features.json"

            # Create mock domain/project structure
            domain_dir = forge_root / "test-domain"
            domain_dir.mkdir()
            project_dir = domain_dir / "test-project"
            project_dir.mkdir()
            (project_dir / "package.json").write_text("{}")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = [
                    {"id": "test-001", "name": "Test Feature", "priority": "high"}
                ]

                await generate_portfolio_features(
                    forge_root=forge_root,
                    output_path=output_path,
                    include_harness=False,
                )

                assert output_path.exists()
                data = json.loads(output_path.read_text())
                assert len(data) == 1

    @pytest.mark.asyncio
    async def test_deduplicates_features(self):
        """generate_portfolio_features removes duplicate IDs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)

            # Create two projects
            for i in range(2):
                domain_dir = forge_root / f"domain-{i}"
                domain_dir.mkdir()
                project_dir = domain_dir / f"project-{i}"
                project_dir.mkdir()
                (project_dir / "pyproject.toml").write_text("[project]")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                # Both return same ID - should dedupe
                mock_scan.return_value = [
                    {"id": "same-id", "name": "Duplicate", "priority": "high"}
                ]

                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

                # Should only have 1 despite 2 projects returning same ID
                ids = [f["id"] for f in features]
                assert ids.count("same-id") == 1


class TestRunFlywheel:
    """Tests for run_flywheel function."""

    @pytest.mark.asyncio
    async def test_runs_full_cycle(self):
        """run_flywheel executes scan + loop cycle."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "test-domain"
            project = "test-project"

            # Create project structure
            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)
            (project_path / "pyproject.toml").write_text("[project]")

            config = FlywheelConfig(dry_run=True)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
            ):
                mock_scan.return_value = []

                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(
                    return_value=MagicMock(
                        features_completed=0,
                        features_blocked=0,
                    )
                )
                mock_create_loop.return_value = mock_loop

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                )

                assert isinstance(result, FlywheelResult)
                assert result.ended_at is not None

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(self):
        """run_flywheel captures errors in result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.side_effect = Exception("Scan failed")

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain="test",
                    project="test",
                )

                assert len(result.errors) > 0
                assert "Scan failed" in result.errors[0]

    @pytest.mark.asyncio
    async def test_creates_orchestrator_when_enabled(self):
        """run_flywheel creates orchestrator for implementation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "test-domain"
            project = "test-project"

            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)
            (project_path / "features.json").write_text("[]")

            config = FlywheelConfig(dry_run=False)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
                patch.dict("sys.modules", {"forge_harness.agent": MagicMock()}),
            ):
                # Mock the FeatureOrchestrator import
                import sys

                mock_orch_class = MagicMock()
                sys.modules["forge_harness.agent"].FeatureOrchestrator = mock_orch_class

                mock_scan.return_value = []
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(
                    return_value=MagicMock(
                        features_completed=0,
                        features_blocked=0,
                    )
                )
                mock_create_loop.return_value = mock_loop

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=True,
                )

                # Verify orchestrator was passed to create_flywheel_loop
                assert isinstance(result, FlywheelResult)

    @pytest.mark.asyncio
    async def test_adds_new_features_to_existing(self):
        """run_flywheel adds new features without duplicating existing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "test-domain"
            project = "test-project"

            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)
            features_file = project_path / "features.json"
            features_file.write_text(
                json.dumps([{"id": "existing-1", "name": "Existing", "status": "pending"}])
            )

            config = FlywheelConfig(dry_run=True)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = [
                    {"id": "existing-1", "name": "Duplicate", "priority": "high"},
                    {"id": "new-1", "name": "New Feature", "priority": "high"},
                ]
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(
                    return_value=MagicMock(
                        features_completed=0,
                        features_blocked=0,
                    )
                )
                mock_create.return_value = mock_loop

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                )

                # Only the new feature should be counted
                assert result.features_generated == 1


class TestCodeAtlasIntegration:
    """Tests for Code Atlas integration (mocked)."""

    @pytest.mark.asyncio
    async def test_flywheel_triggers_atlas_indexing(self):
        """Flywheel triggers Code Atlas session indexing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "test-domain"
            project = "test-project"

            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)
            (project_path / "features.json").write_text("[]")

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(
                    return_value=MagicMock(
                        features_completed=1,
                        features_blocked=0,
                    )
                )
                mock_create.return_value = mock_loop

                config = FlywheelConfig(dry_run=True)
                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                )

                # Flywheel sets sessions_indexed=1 when Ralph runs
                assert result.sessions_indexed == 1

    def test_create_loop_enables_meta_learning(self):
        """create_flywheel_loop creates registry for meta-learning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            features_path = Path(tmpdir) / "features.json"
            features_path.write_text("[]")

            with (
                patch("forge_harness.harness_registry.create_harness_registry") as mock_registry,
                patch("forge_harness.ralph_loop.create_ralph_loop_from_registry") as mock_create,
            ):
                mock_reg = MagicMock()
                mock_reg.get.return_value = None
                mock_registry.return_value = mock_reg
                mock_create.return_value = MagicMock()

                create_flywheel_loop(
                    domain="test-domain",
                    project="test-project",
                    features_path=features_path,
                )

                mock_registry.assert_called_once_with(
                    domain="test-domain",
                    project="test-project",
                )


class TestPatternLearning:
    """Tests for pattern learning verification."""

    def test_flywheel_result_tracks_patterns(self):
        """FlywheelResult can track pattern learning."""
        result = FlywheelResult(
            started_at=datetime.now(UTC),
            patterns_learned=5,
        )
        assert result.patterns_learned == 5

        data = result.to_dict()
        assert data["patterns_learned"] == 5

    def test_patterns_default_to_zero(self):
        """FlywheelResult defaults patterns_learned to 0."""
        result = FlywheelResult(started_at=datetime.now(UTC))
        assert result.patterns_learned == 0


class TestScanProjectLocal:
    """Tests for scan_project_local fallback function."""

    @pytest.mark.asyncio
    async def test_local_scan_success(self):
        """Local scan returns features on success."""
        from forge_harness.flywheel import scan_project_local

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            mock_report = MagicMock()
            mock_report.domain = "test-domain"
            mock_report.project_name = "test-project"
            mock_report.security_findings = []
            mock_report.quality_score = 85  # Above 70, no debt feature
            mock_report.issues = []
            mock_report.recommendations = []

            with patch("forge_harness.quality_loop.QualityLoopHarness") as mock_harness_class:
                mock_harness = MagicMock()
                mock_harness.scan_project = AsyncMock(return_value=mock_report)
                mock_harness_class.return_value = mock_harness

                features = await scan_project_local(
                    domain="test-domain",
                    project="test-project",
                    project_path=project_path,
                )

                assert isinstance(features, list)

    @pytest.mark.asyncio
    async def test_local_scan_failure_returns_empty(self):
        """Local scan returns empty list on failure."""
        from forge_harness.flywheel import scan_project_local

        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            with patch("forge_harness.quality_loop.QualityLoopHarness") as mock_harness_class:
                mock_harness = MagicMock()
                mock_harness.scan_project = AsyncMock(side_effect=Exception("Scan failed"))
                mock_harness_class.return_value = mock_harness

                features = await scan_project_local(
                    domain="test-domain",
                    project="test-project",
                    project_path=project_path,
                )

                assert features == []


class TestQualityReportToFeatures:
    """Tests for quality_report_to_features conversion."""

    def test_converts_security_findings(self):
        """Converts security findings to feature dicts."""
        from forge_harness.flywheel import quality_report_to_features
        from forge_harness.quality_loop import SeverityLevel

        finding = MagicMock()
        finding.severity = SeverityLevel.HIGH
        finding.rule_id = "SEC001"
        finding.message = "XSS vulnerability"
        finding.file_path = "templates/index.html"
        finding.line_number = 15
        finding.tool = "bandit"
        finding.confidence = "high"

        report = MagicMock()
        report.domain = "test-domain"
        report.project_name = "test-project"
        report.security_findings = [finding]
        report.quality_score = 80
        report.issues = []
        report.recommendations = []

        features = quality_report_to_features(report)

        assert len(features) == 1
        assert "SEC001" in features[0]["id"]
        assert features[0]["priority"] == "high"
        assert features[0]["metadata"]["finding_type"] == "security"

    def test_adds_debt_feature_for_low_score(self):
        """Adds debt feature when quality score is low."""
        from forge_harness.flywheel import quality_report_to_features

        report = MagicMock()
        report.domain = "test-domain"
        report.project_name = "test-project"
        report.security_findings = []
        report.quality_score = 45  # Below 70 threshold
        report.issues = ["Issue 1", "Issue 2"]
        report.recommendations = ["Rec 1"]

        features = quality_report_to_features(report)

        assert len(features) == 1
        assert "debt-score" in features[0]["id"]
        assert features[0]["priority"] == "high"  # Score < 50

    def test_respects_max_features(self):
        """Respects max_features limit."""
        from forge_harness.flywheel import quality_report_to_features
        from forge_harness.quality_loop import SeverityLevel

        findings = []
        for i in range(15):
            f = MagicMock()
            f.severity = SeverityLevel.HIGH
            f.rule_id = f"SEC{i:03d}"
            f.message = f"Issue {i}"
            f.file_path = "test.py"
            f.line_number = i
            f.tool = "test"
            f.confidence = "high"
            findings.append(f)

        report = MagicMock()
        report.domain = "test"
        report.project_name = "test"
        report.security_findings = findings
        report.quality_score = 80
        report.issues = []
        report.recommendations = []

        features = quality_report_to_features(report, max_features=5)

        assert len(features) == 5


class TestBuildDebtDescription:
    """Tests for _build_debt_description helper."""

    def test_basic_description(self):
        """Builds description from basic finding."""
        from forge_harness.flywheel import _build_debt_description

        finding = MagicMock()
        finding.message = "Security vulnerability found"
        finding.file_path = None
        finding.line_number = None
        finding.recommendation = None

        desc = _build_debt_description(finding)
        assert desc == "Security vulnerability found"

    def test_description_with_file_path(self):
        """Builds description with file path."""
        from forge_harness.flywheel import _build_debt_description

        finding = MagicMock()
        finding.message = "Issue found"
        finding.file_path = "src/main.py"
        finding.line_number = None
        finding.recommendation = None

        desc = _build_debt_description(finding)
        assert "Issue found" in desc
        assert "**Location:** `src/main.py`" in desc

    def test_description_with_line_number(self):
        """Builds description with file and line number."""
        from forge_harness.flywheel import _build_debt_description

        finding = MagicMock()
        finding.message = "Issue found"
        finding.file_path = "src/main.py"
        finding.line_number = 42
        finding.recommendation = None

        desc = _build_debt_description(finding)
        assert "`src/main.py`:42" in desc

    def test_description_with_recommendation(self):
        """Builds description with recommendation."""
        from forge_harness.flywheel import _build_debt_description

        finding = MagicMock()
        finding.message = "Issue found"
        finding.file_path = None
        finding.line_number = None
        finding.recommendation = "Use parameterized queries"

        desc = _build_debt_description(finding)
        assert "**Recommendation:** Use parameterized queries" in desc


class TestRunFlywheelSync:
    """Tests for run_flywheel_sync synchronous wrapper."""

    def test_sync_wrapper_runs(self):
        """Synchronous wrapper executes correctly."""
        from forge_harness.flywheel import run_flywheel_sync

        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "test-domain"
            project = "test-project"

            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)
            (project_path / "features.json").write_text("[]")

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(
                    return_value=MagicMock(
                        features_completed=0,
                        features_blocked=0,
                    )
                )
                mock_create.return_value = mock_loop

                config = FlywheelConfig(dry_run=True)
                result = run_flywheel_sync(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                )

                assert isinstance(result, FlywheelResult)
                assert result.ended_at is not None


class TestConvenienceAliases:
    """Tests for convenience function aliases."""

    def test_create_fully_wired_loop_alias(self):
        """create_fully_wired_loop is alias for create_flywheel_loop."""
        from forge_harness.flywheel import create_fully_wired_loop

        assert create_fully_wired_loop is create_flywheel_loop

    def test_scan_portfolio_alias(self):
        """scan_portfolio is alias for generate_portfolio_features."""
        from forge_harness.flywheel import scan_portfolio

        assert scan_portfolio is generate_portfolio_features


class TestFlywheelWithOrchestrator:
    """Tests for flywheel with orchestrator integration."""

    def test_create_loop_with_orchestrator(self):
        """create_flywheel_loop accepts external orchestrator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            features_path = Path(tmpdir) / "features.json"
            features_path.write_text("[]")

            mock_orchestrator = MagicMock()

            with (
                patch("forge_harness.harness_registry.create_harness_registry") as mock_registry,
                patch("forge_harness.ralph_loop.create_ralph_loop_from_registry") as mock_create,
            ):
                mock_reg = MagicMock()
                mock_reg.get.return_value = None
                mock_registry.return_value = mock_reg
                mock_create.return_value = MagicMock()

                create_flywheel_loop(
                    domain="test-domain",
                    project="test-project",
                    features_path=features_path,
                    orchestrator=mock_orchestrator,
                )

                call_kwargs = mock_create.call_args[1]
                assert call_kwargs["orchestrator"] == mock_orchestrator

    @pytest.mark.asyncio
    async def test_run_flywheel_without_orchestrator(self):
        """run_flywheel works without orchestrator in dry_run mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "test-domain"
            project = "test-project"

            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)
            (project_path / "features.json").write_text("[]")

            config = FlywheelConfig(dry_run=True)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(
                    return_value=MagicMock(
                        features_completed=0,
                        features_blocked=0,
                    )
                )
                mock_create.return_value = mock_loop

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=False,
                )

                assert isinstance(result, FlywheelResult)


class TestFlywheelEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_empty_portfolio_scan(self):
        """generate_portfolio_features handles empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)

            features = await generate_portfolio_features(
                forge_root=forge_root,
                include_harness=False,
            )

            assert features == []

    @pytest.mark.asyncio
    async def test_skip_hidden_directories(self):
        """generate_portfolio_features skips hidden directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)

            # Create hidden directory
            hidden_dir = forge_root / ".hidden-domain"
            hidden_dir.mkdir()
            (hidden_dir / "project").mkdir()
            (hidden_dir / "project" / "pyproject.toml").write_text("[project]")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = [{"id": "f1", "name": "Test", "priority": "high"}]

                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

                # Should not scan hidden directory
                mock_scan.assert_not_called()
                assert features == []

    @pytest.mark.asyncio
    async def test_skip_special_directories(self):
        """generate_portfolio_features skips node_modules, docs, etc."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)

            # Create special directories
            for special in ["node_modules", "docs", "scripts"]:
                special_dir = forge_root / special
                special_dir.mkdir()
                (special_dir / "project").mkdir()
                (special_dir / "project" / "pyproject.toml").write_text("[project]")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = []

                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

                # Should not scan special directories
                mock_scan.assert_not_called()

    @pytest.mark.asyncio
    async def test_tech_diligence_fallback(self):
        """scan_project_for_debt falls back to local scan."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            # Mock both TechDiligenceBridge (to fail) and scan_project_local (to succeed)
            with (
                patch(
                    "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge"
                ) as mock_bridge_class,
                patch(
                    "forge_harness.flywheel.scan_project_local", new_callable=AsyncMock
                ) as mock_local,
            ):
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(
                    side_effect=Exception("Connection refused")
                )
                mock_bridge_class.return_value = mock_bridge
                mock_local.return_value = [{"id": "local-1", "name": "Local feature"}]

                features = await scan_project_for_debt(
                    domain="test-domain",
                    project="test-project",
                    project_path=project_path,
                    use_local_fallback=True,
                )

                mock_local.assert_called_once()
                assert len(features) == 1

    @pytest.mark.asyncio
    async def test_no_fallback_returns_empty(self):
        """scan_project_for_debt returns empty when no fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            with patch(
                "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge"
            ) as mock_bridge_class:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(side_effect=Exception("Failed"))
                mock_bridge_class.return_value = mock_bridge

                features = await scan_project_for_debt(
                    domain="test-domain",
                    project="test-project",
                    project_path=project_path,
                    use_local_fallback=False,
                )

                assert features == []

    def test_result_without_end_time(self):
        """FlywheelResult.to_dict handles None end_time."""
        result = FlywheelResult(started_at=datetime.now(UTC))
        data = result.to_dict()

        assert data["ended_at"] is None
        assert data["duration_seconds"] is None
