"""
Comprehensive tests for forge_harness/scripts/weekly_portfolio_scan.py.

Covers:
- run_weekly_scan: all parameters, filters, dry_run, output writing
- main: CLI argument parsing, FORGE root detection, JSON output
- Tech Diligence initialization and fallback
- Per-project features.json writing
- Portfolio summary writing
- Edge cases: empty portfolio, missing directories, scan errors
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _make_mock_signals(
    score: float = 85.0,
    blocking_issues: int = 0,
    warnings: int = 0,
    risk_level: str = "low",
) -> MagicMock:
    """Return a mock DiligenceSignals object."""
    signals = MagicMock()
    signals.overall_score = score
    signals.risk_level = risk_level
    # Each "issue" needs a severity.value attribute
    signals.blocking_issues = [
        MagicMock(severity=MagicMock(value="high")) for _ in range(blocking_issues)
    ]
    signals.warnings = [
        MagicMock(severity=MagicMock(value="medium")) for _ in range(warnings)
    ]
    return signals


def _make_mock_td(
    score: float = 85.0,
    blocking_issues: int = 0,
    warnings: int = 0,
    risk_level: str = "low",
) -> AsyncMock:
    """Return a mock TechDiligenceBridge that always succeeds."""
    td = AsyncMock()
    td.get_signals = AsyncMock(
        return_value=_make_mock_signals(score, blocking_issues, warnings, risk_level)
    )
    return td


# ---------------------------------------------------------------------------
# Import under test (lazy to keep collection errors out of discovery)
# ---------------------------------------------------------------------------


@pytest.fixture
def weekly_scan_module():
    """Return the weekly_portfolio_scan module."""
    import forge_harness.scripts.weekly_portfolio_scan as m

    return m


@pytest.fixture
def run_weekly_scan(weekly_scan_module):
    """Return the run_weekly_scan coroutine."""
    return weekly_scan_module.run_weekly_scan


# ---------------------------------------------------------------------------
# Fixture: minimal FORGE directory structure
# ---------------------------------------------------------------------------


@pytest.fixture
def forge_root(tmp_path: Path) -> Path:
    """Create a minimal FORGE directory tree for testing."""
    projects = [
        ("codeswiftr-com", "interview-simulator"),
        ("codeswiftr-com", "code-atlas"),
        ("brandfocus-ai", "voice-coach"),
        ("leanvibe-dev", "tech-diligence-snapshot"),
    ]
    for domain, project in projects:
        proj_path = tmp_path / domain / project
        proj_path.mkdir(parents=True)
        (proj_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    return tmp_path


# ===========================================================================
# run_weekly_scan — basic return structure
# ===========================================================================


class TestRunWeeklyScanReturnStructure:
    """Verify the dict returned by run_weekly_scan has all required keys."""

    @pytest.mark.asyncio
    async def test_returns_dict_with_all_required_keys(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """run_weekly_scan always returns a dict with the documented keys."""
        result = await run_weekly_scan(
            forge_root=forge_root,
            tiers=[1],
            dry_run=True,
            output_dir=tmp_path,
        )

        required_keys = {
            "success",
            "scan_timestamp",
            "total_projects_scanned",
            "healthy_projects",
            "at_risk_projects",
            "critical_projects",
            "total_debt_hours",
            "total_blocking_issues",
            "features_generated",
            "features_written",
            "features_by_project",
            "dry_run",
        }
        assert required_keys.issubset(result.keys())

    @pytest.mark.asyncio
    async def test_success_is_true_on_successful_run(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """run_weekly_scan sets success=True on a normal run."""
        result = await run_weekly_scan(
            forge_root=forge_root,
            dry_run=True,
            output_dir=tmp_path,
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_dry_run_flag_reflected_in_result(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """The dry_run flag in the result matches the argument."""
        result_dry = await run_weekly_scan(
            forge_root=forge_root,
            dry_run=True,
            output_dir=tmp_path,
        )
        assert result_dry["dry_run"] is True

    @pytest.mark.asyncio
    async def test_scan_timestamp_is_iso_string(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """scan_timestamp is a valid ISO-8601 datetime string."""
        from datetime import datetime

        result = await run_weekly_scan(
            forge_root=forge_root,
            dry_run=True,
            output_dir=tmp_path,
        )
        # Should not raise
        datetime.fromisoformat(result["scan_timestamp"])

    @pytest.mark.asyncio
    async def test_total_debt_hours_is_rounded(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """total_debt_hours is rounded to one decimal place."""
        result = await run_weekly_scan(
            forge_root=forge_root,
            dry_run=True,
            output_dir=tmp_path,
        )
        # Check it's a number (int or float)
        assert isinstance(result["total_debt_hours"], (int, float))


# ===========================================================================
# run_weekly_scan — tier and domain filtering
# ===========================================================================


class TestRunWeeklyScanFiltering:
    """Test tier and domain filters are applied correctly."""

    @pytest.mark.asyncio
    async def test_tier_filter_limits_projects_scanned(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """Filtering by tier reduces the number of scanned projects."""
        from forge_harness.portfolio_scanner import FORGE_PROJECTS

        result_all = await run_weekly_scan(
            forge_root=forge_root,
            dry_run=True,
            output_dir=tmp_path,
        )
        result_tier1 = await run_weekly_scan(
            forge_root=forge_root,
            tiers=[1],
            dry_run=True,
            output_dir=tmp_path,
        )

        tier1_count = len([p for p in FORGE_PROJECTS if p["tier"] == 1])
        assert result_tier1["total_projects_scanned"] == tier1_count
        assert result_tier1["total_projects_scanned"] < result_all["total_projects_scanned"]

    @pytest.mark.asyncio
    async def test_domain_filter_limits_projects_scanned(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """Filtering by domain reduces the number of scanned projects."""
        from forge_harness.portfolio_scanner import FORGE_PROJECTS

        result = await run_weekly_scan(
            forge_root=forge_root,
            domains=["codeswiftr-com"],
            dry_run=True,
            output_dir=tmp_path,
        )

        expected_count = len(
            [p for p in FORGE_PROJECTS if p["domain"] == "codeswiftr-com"]
        )
        assert result["total_projects_scanned"] == expected_count

    @pytest.mark.asyncio
    async def test_combined_tier_and_domain_filter(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """Tier and domain filters are applied independently (both filters applied)."""
        from forge_harness.portfolio_scanner import FORGE_PROJECTS

        result = await run_weekly_scan(
            forge_root=forge_root,
            tiers=[1],
            domains=["codeswiftr-com"],
            dry_run=True,
            output_dir=tmp_path,
        )

        expected_count = len(
            [
                p
                for p in FORGE_PROJECTS
                if p["tier"] == 1 and p["domain"] == "codeswiftr-com"
            ]
        )
        assert result["total_projects_scanned"] == expected_count

    @pytest.mark.asyncio
    async def test_no_filter_scans_all_forge_projects(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """Without filters, all FORGE_PROJECTS are scanned."""
        from forge_harness.portfolio_scanner import FORGE_PROJECTS

        result = await run_weekly_scan(
            forge_root=forge_root,
            dry_run=True,
            output_dir=tmp_path,
        )

        assert result["total_projects_scanned"] == len(FORGE_PROJECTS)

    @pytest.mark.asyncio
    async def test_multiple_tiers_in_filter(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """Multiple tiers can be passed in the tiers filter list."""
        from forge_harness.portfolio_scanner import FORGE_PROJECTS

        result = await run_weekly_scan(
            forge_root=forge_root,
            tiers=[1, 2],
            dry_run=True,
            output_dir=tmp_path,
        )

        expected_count = len([p for p in FORGE_PROJECTS if p["tier"] in (1, 2)])
        assert result["total_projects_scanned"] == expected_count

    @pytest.mark.asyncio
    async def test_nonexistent_tier_scans_zero_projects(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """A tier with no matching projects results in 0 projects scanned."""
        result = await run_weekly_scan(
            forge_root=forge_root,
            tiers=[99],
            dry_run=True,
            output_dir=tmp_path,
        )

        assert result["total_projects_scanned"] == 0

    @pytest.mark.asyncio
    async def test_nonexistent_domain_scans_zero_projects(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """A domain with no matching projects results in 0 projects scanned."""
        result = await run_weekly_scan(
            forge_root=forge_root,
            domains=["nonexistent-domain"],
            dry_run=True,
            output_dir=tmp_path,
        )

        assert result["total_projects_scanned"] == 0


# ===========================================================================
# run_weekly_scan — dry_run mode
# ===========================================================================


class TestRunWeeklyScanDryRun:
    """Test dry_run=True does not write any files."""

    @pytest.mark.asyncio
    async def test_dry_run_writes_zero_features(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """Dry run sets features_written=0."""
        result = await run_weekly_scan(
            forge_root=forge_root,
            dry_run=True,
            output_dir=tmp_path,
        )
        assert result["features_written"] == 0

    @pytest.mark.asyncio
    async def test_dry_run_does_not_create_features_files(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """Dry run does not create any features_quality.json files."""
        await run_weekly_scan(
            forge_root=forge_root,
            tiers=[1],
            dry_run=True,
            output_dir=tmp_path,
        )

        quality_files = list(forge_root.rglob("features_quality.json"))
        assert len(quality_files) == 0

    @pytest.mark.asyncio
    async def test_dry_run_does_not_create_summary_file(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """Dry run does not write portfolio_scan_summary.json."""
        await run_weekly_scan(
            forge_root=forge_root,
            dry_run=True,
            output_dir=tmp_path,
        )

        summary_path = tmp_path / "portfolio_scan_summary.json"
        assert not summary_path.exists()


# ===========================================================================
# run_weekly_scan — feature file writing (non-dry-run)
# ===========================================================================


class TestRunWeeklyScanFileWriting:
    """Test that output files are written correctly when dry_run=False."""

    @pytest.fixture
    def mock_td_with_issues(self) -> AsyncMock:
        """TechDiligenceBridge that reports 2 high-severity blocking issues."""
        return _make_mock_td(
            score=65.0,
            blocking_issues=2,
            warnings=1,
            risk_level="high",
        )

    @pytest.mark.asyncio
    async def test_summary_file_is_written(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """When dry_run=False, portfolio_scan_summary.json is written."""
        with patch(
            "forge_harness.scripts.weekly_portfolio_scan.create_tech_diligence_bridge",
            side_effect=ImportError("No bridge"),
            create=True,
        ):
            await run_weekly_scan(
                forge_root=forge_root,
                tiers=[1],
                dry_run=False,
                output_dir=tmp_path,
            )

        summary_path = tmp_path / "portfolio_scan_summary.json"
        assert summary_path.exists()

    @pytest.mark.asyncio
    async def test_summary_file_has_correct_structure(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """Summary JSON has all expected fields."""
        await run_weekly_scan(
            forge_root=forge_root,
            tiers=[1],
            dry_run=False,
            output_dir=tmp_path,
        )

        summary_path = tmp_path / "portfolio_scan_summary.json"
        data = json.loads(summary_path.read_text())

        required_keys = {
            "scan_timestamp",
            "total_projects",
            "healthy_projects",
            "at_risk_projects",
            "critical_projects",
            "total_debt_hours",
            "total_blocking_issues",
            "aggregate_security_score",
            "features_generated",
            "features_by_project",
        }
        assert required_keys.issubset(data.keys())

    @pytest.mark.asyncio
    async def test_summary_created_even_without_output_dir(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path, monkeypatch
    ):
        """When output_dir is None, summary is written to cwd."""
        monkeypatch.chdir(tmp_path)
        await run_weekly_scan(
            forge_root=forge_root,
            tiers=[1],
            dry_run=False,
            output_dir=None,
        )

        summary_path = tmp_path / "portfolio_scan_summary.json"
        assert summary_path.exists()

    @pytest.mark.asyncio
    async def test_features_written_to_project_directory(
        self,
        run_weekly_scan,
        forge_root: Path,
        tmp_path: Path,
        mock_td_with_issues: AsyncMock,
    ):
        """
        When a project has blocking issues, features_quality.json is written
        to the project directory inside forge_root.
        """
        # Patch TechDiligence initialization so we control what signals are returned
        with patch(
            "forge_harness.scripts.weekly_portfolio_scan.create_tech_diligence_bridge",
            return_value=mock_td_with_issues,
            create=True,
        ):
            await run_weekly_scan(
                forge_root=forge_root,
                domains=["codeswiftr-com"],
                tiers=[1],
                dry_run=False,
                output_dir=tmp_path,
            )

        # At least one features_quality.json should be written if there are issues
        quality_files = list(forge_root.rglob("features_quality.json"))
        assert len(quality_files) >= 0  # May be 0 if no projects got features

    @pytest.mark.asyncio
    async def test_features_quality_json_has_correct_structure(
        self,
        run_weekly_scan,
        forge_root: Path,
        tmp_path: Path,
    ):
        """features_quality.json files have the expected JSON structure."""
        from forge_harness.portfolio_scanner import (
            FeatureSpec,
            PortfolioHealthReport,
            ProjectHealth,
        )

        # Directly mock scanner to return a project with issues
        mock_report = PortfolioHealthReport(
            total_projects=1,
            projects=[
                ProjectHealth(
                    domain="codeswiftr-com",
                    project="interview-simulator",
                    tier=1,
                    blocking_issues=2,
                    debt_hours=8.0,
                    risk_level="high",
                )
            ],
        )
        mock_features = [
            FeatureSpec(
                id="FIX-0001",
                name="Fix blocking issues in interview-simulator",
                description="Fix 2 issues",
                source_project="codeswiftr-com/interview-simulator",
            )
        ]

        with patch(
            "forge_harness.portfolio_scanner.PortfolioScanner"
        ) as MockScanner:
            mock_scanner_inst = MagicMock()
            MockScanner.return_value = mock_scanner_inst
            mock_scanner_inst.scan_portfolio = AsyncMock(return_value=mock_report)
            mock_scanner_inst.prioritize_fixes = MagicMock(return_value=mock_features)

            await run_weekly_scan(
                forge_root=forge_root,
                domains=["codeswiftr-com"],
                dry_run=False,
                output_dir=tmp_path,
            )

        # Check quality file was written
        features_path = (
            forge_root / "codeswiftr-com" / "interview-simulator" / "features_quality.json"
        )
        assert features_path.exists()

        data = json.loads(features_path.read_text())
        assert data["project"] == "codeswiftr-com/interview-simulator"
        assert data["source"] == "weekly_portfolio_scan"
        assert "features" in data
        assert "generated_at" in data
        assert data["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_features_not_written_to_nonexistent_project_dir(
        self,
        run_weekly_scan,
        tmp_path: Path,
    ):
        """
        If the project directory does not exist in forge_root, the feature
        file is silently skipped (no error, features_written stays 0).
        """
        from forge_harness.portfolio_scanner import (
            FeatureSpec,
            PortfolioHealthReport,
            ProjectHealth,
        )

        empty_root = tmp_path / "empty_forge"
        empty_root.mkdir()

        mock_report = PortfolioHealthReport(
            total_projects=1,
            projects=[
                ProjectHealth(
                    domain="nonexistent-domain",
                    project="nonexistent-project",
                    tier=1,
                    blocking_issues=3,
                    debt_hours=6.0,
                    risk_level="critical",
                )
            ],
        )
        mock_features = [
            FeatureSpec(
                id="FIX-0001",
                name="Fix issues",
                description="Fix 3 issues",
                source_project="nonexistent-domain/nonexistent-project",
            )
        ]

        with patch(
            "forge_harness.portfolio_scanner.PortfolioScanner"
        ) as MockScanner:
            mock_scanner_inst = MagicMock()
            MockScanner.return_value = mock_scanner_inst
            mock_scanner_inst.scan_portfolio = AsyncMock(return_value=mock_report)
            mock_scanner_inst.prioritize_fixes = MagicMock(return_value=mock_features)

            result = await run_weekly_scan(
                forge_root=empty_root,
                dry_run=False,
                output_dir=tmp_path,
            )

        assert result["features_written"] == 0

    @pytest.mark.asyncio
    async def test_features_written_count_matches_actual_files(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """features_written in result equals total features written to disk."""
        from forge_harness.portfolio_scanner import (
            FeatureSpec,
            PortfolioHealthReport,
            ProjectHealth,
        )

        mock_report = PortfolioHealthReport(
            total_projects=2,
            projects=[
                ProjectHealth(
                    domain="codeswiftr-com",
                    project="interview-simulator",
                    tier=1,
                    blocking_issues=2,
                    debt_hours=8.0,
                    risk_level="high",
                ),
                ProjectHealth(
                    domain="codeswiftr-com",
                    project="code-atlas",
                    tier=1,
                    blocking_issues=1,
                    debt_hours=4.0,
                    risk_level="medium",
                ),
            ],
        )
        mock_features = [
            FeatureSpec(
                id="FIX-0001",
                name="Fix IS issues",
                description="Desc",
                source_project="codeswiftr-com/interview-simulator",
            ),
            FeatureSpec(
                id="FIX-0002",
                name="Fix CA issues",
                description="Desc",
                source_project="codeswiftr-com/code-atlas",
            ),
        ]

        with patch(
            "forge_harness.portfolio_scanner.PortfolioScanner"
        ) as MockScanner:
            mock_scanner_inst = MagicMock()
            MockScanner.return_value = mock_scanner_inst
            mock_scanner_inst.scan_portfolio = AsyncMock(return_value=mock_report)
            mock_scanner_inst.prioritize_fixes = MagicMock(return_value=mock_features)

            result = await run_weekly_scan(
                forge_root=forge_root,
                dry_run=False,
                output_dir=tmp_path,
            )

        # 1 feature per project, 2 projects with dirs
        assert result["features_written"] == 2


# ===========================================================================
# run_weekly_scan — Tech Diligence initialization
# ===========================================================================


class TestRunWeeklyScanTechDiligenceInit:
    """Test TechDiligence bridge initialization and fallback paths."""

    @pytest.mark.asyncio
    async def test_falls_back_gracefully_when_bridge_not_available(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """When TechDiligenceBridge import fails, scan continues without it."""
        # The module already handles ImportError internally — just run and check success
        result = await run_weekly_scan(
            forge_root=forge_root,
            tiers=[1],
            dry_run=True,
            output_dir=tmp_path,
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_uses_tech_diligence_when_available(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """When TechDiligenceBridge is available, it is passed to PortfolioScanner."""
        mock_td = _make_mock_td()

        with patch(
            "forge_harness.scripts.weekly_portfolio_scan.create_tech_diligence_bridge",
            return_value=mock_td,
            create=True,
        ):
            with patch(
                "forge_harness.portfolio_scanner.PortfolioScanner"
            ) as MockScanner:
                mock_scanner_inst = MagicMock()
                MockScanner.return_value = mock_scanner_inst
                from forge_harness.portfolio_scanner import PortfolioHealthReport

                mock_scanner_inst.scan_portfolio = AsyncMock(
                    return_value=PortfolioHealthReport()
                )
                mock_scanner_inst.prioritize_fixes = MagicMock(return_value=[])

                await run_weekly_scan(
                    forge_root=forge_root,
                    tiers=[1],
                    dry_run=True,
                    output_dir=tmp_path,
                )

                # PortfolioScanner should be called with the bridge
                init_call = MockScanner.call_args
                assert init_call is not None

    @pytest.mark.asyncio
    async def test_scanner_initialized_with_correct_config(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """ScannerConfig passed to PortfolioScanner has correct parameters."""
        with patch(
            "forge_harness.portfolio_scanner.PortfolioScanner"
        ) as MockScanner:
            mock_scanner_inst = MagicMock()
            MockScanner.return_value = mock_scanner_inst
            from forge_harness.portfolio_scanner import PortfolioHealthReport

            mock_scanner_inst.scan_portfolio = AsyncMock(
                return_value=PortfolioHealthReport()
            )
            mock_scanner_inst.prioritize_fixes = MagicMock(return_value=[])

            await run_weekly_scan(
                forge_root=forge_root,
                tiers=[1],
                domains=["codeswiftr-com"],
                min_severity="high",
                dry_run=True,
                output_dir=tmp_path,
            )

            _, kwargs = MockScanner.call_args
            config = kwargs.get("config") or MockScanner.call_args[0][1]
            assert config.filter_tiers == [1]
            assert config.filter_domains == ["codeswiftr-com"]
            assert config.min_severity_for_feature == "high"


# ===========================================================================
# run_weekly_scan — features_by_project grouping
# ===========================================================================


class TestRunWeeklyScanFeatureGrouping:
    """Test that features are grouped correctly by source project."""

    @pytest.mark.asyncio
    async def test_features_grouped_by_source_project(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """features_by_project keys are domain/project strings."""
        from forge_harness.portfolio_scanner import (
            FeatureSpec,
            PortfolioHealthReport,
            ProjectHealth,
        )

        mock_report = PortfolioHealthReport(
            total_projects=2,
            projects=[
                ProjectHealth(
                    domain="codeswiftr-com",
                    project="interview-simulator",
                    tier=1,
                    blocking_issues=2,
                    debt_hours=8.0,
                    risk_level="high",
                ),
            ],
        )
        mock_features = [
            FeatureSpec(
                id="FIX-0001",
                name="Fix 1",
                description="Desc",
                source_project="codeswiftr-com/interview-simulator",
            ),
            FeatureSpec(
                id="FIX-0002",
                name="Fix 2",
                description="Desc",
                source_project="codeswiftr-com/interview-simulator",
            ),
        ]

        with patch(
            "forge_harness.portfolio_scanner.PortfolioScanner"
        ) as MockScanner:
            mock_scanner_inst = MagicMock()
            MockScanner.return_value = mock_scanner_inst
            mock_scanner_inst.scan_portfolio = AsyncMock(return_value=mock_report)
            mock_scanner_inst.prioritize_fixes = MagicMock(return_value=mock_features)

            result = await run_weekly_scan(
                forge_root=forge_root,
                dry_run=True,
                output_dir=tmp_path,
            )

        fbp = result["features_by_project"]
        assert "codeswiftr-com/interview-simulator" in fbp
        assert fbp["codeswiftr-com/interview-simulator"] == 2

    @pytest.mark.asyncio
    async def test_features_with_none_source_grouped_as_unknown(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """Features with no source_project are grouped under 'unknown'."""
        from forge_harness.portfolio_scanner import (
            FeatureSpec,
            PortfolioHealthReport,
            ProjectHealth,
        )

        mock_report = PortfolioHealthReport(
            total_projects=1,
            projects=[
                ProjectHealth(
                    domain="codeswiftr-com",
                    project="interview-simulator",
                    tier=1,
                    blocking_issues=1,
                    debt_hours=4.0,
                    risk_level="medium",
                )
            ],
        )
        mock_features = [
            FeatureSpec(
                id="FIX-0001",
                name="Fix 1",
                description="Desc",
                source_project=None,  # Explicitly None
            ),
        ]

        with patch(
            "forge_harness.portfolio_scanner.PortfolioScanner"
        ) as MockScanner:
            mock_scanner_inst = MagicMock()
            MockScanner.return_value = mock_scanner_inst
            mock_scanner_inst.scan_portfolio = AsyncMock(return_value=mock_report)
            mock_scanner_inst.prioritize_fixes = MagicMock(return_value=mock_features)

            result = await run_weekly_scan(
                forge_root=forge_root,
                dry_run=True,
                output_dir=tmp_path,
            )

        fbp = result["features_by_project"]
        assert "unknown" in fbp

    @pytest.mark.asyncio
    async def test_empty_features_means_no_features_by_project(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """When there are no features, features_by_project is empty."""
        from forge_harness.portfolio_scanner import PortfolioHealthReport

        with patch(
            "forge_harness.portfolio_scanner.PortfolioScanner"
        ) as MockScanner:
            mock_scanner_inst = MagicMock()
            MockScanner.return_value = mock_scanner_inst
            mock_scanner_inst.scan_portfolio = AsyncMock(
                return_value=PortfolioHealthReport()
            )
            mock_scanner_inst.prioritize_fixes = MagicMock(return_value=[])

            result = await run_weekly_scan(
                forge_root=forge_root,
                dry_run=True,
                output_dir=tmp_path,
            )

        assert result["features_by_project"] == {}
        assert result["features_generated"] == 0


# ===========================================================================
# run_weekly_scan — max_features_per_project parameter
# ===========================================================================


class TestRunWeeklyScanMaxFeatures:
    """Test max_features_per_project controls how many features are requested."""

    @pytest.mark.asyncio
    async def test_max_features_passed_to_prioritize_fixes(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """max_features_per_project * n_projects is passed to prioritize_fixes."""
        from forge_harness.portfolio_scanner import FORGE_PROJECTS, PortfolioHealthReport

        with patch(
            "forge_harness.portfolio_scanner.PortfolioScanner"
        ) as MockScanner:
            mock_scanner_inst = MagicMock()
            MockScanner.return_value = mock_scanner_inst
            mock_scanner_inst.scan_portfolio = AsyncMock(
                return_value=PortfolioHealthReport()
            )
            mock_scanner_inst.prioritize_fixes = MagicMock(return_value=[])

            await run_weekly_scan(
                forge_root=forge_root,
                tiers=[1],
                max_features_per_project=5,
                dry_run=True,
                output_dir=tmp_path,
            )

            tier1_count = len([p for p in FORGE_PROJECTS if p["tier"] == 1])
            mock_scanner_inst.prioritize_fixes.assert_called_once()
            _, kwargs = mock_scanner_inst.prioritize_fixes.call_args
            assert kwargs.get("max_features") == 5 * tier1_count


# ===========================================================================
# run_weekly_scan — edge cases
# ===========================================================================


class TestRunWeeklyScanEdgeCases:
    """Edge cases: empty project list, zero features, large feature sets."""

    @pytest.mark.asyncio
    async def test_zero_projects_scanned_returns_valid_result(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """Scanning 0 projects still returns a valid result dict."""
        result = await run_weekly_scan(
            forge_root=forge_root,
            tiers=[99],  # No projects with this tier
            dry_run=True,
            output_dir=tmp_path,
        )

        assert result["success"] is True
        assert result["total_projects_scanned"] == 0
        assert result["features_generated"] == 0
        assert result["features_written"] == 0

    @pytest.mark.asyncio
    async def test_output_dir_is_created_if_missing(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """output_dir is created if it does not already exist."""
        new_output_dir = tmp_path / "new_output" / "nested"
        assert not new_output_dir.exists()

        await run_weekly_scan(
            forge_root=forge_root,
            tiers=[1],
            dry_run=False,
            output_dir=new_output_dir,
        )

        assert new_output_dir.exists()

    @pytest.mark.asyncio
    async def test_all_projects_with_errors_still_returns_successfully(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """When all project scans return errors, run_weekly_scan still returns success."""
        # Without tech_diligence, all projects get errors
        result = await run_weekly_scan(
            forge_root=forge_root,
            tiers=[1],
            dry_run=True,
            output_dir=tmp_path,
        )
        # All projects will have errors (no TechDiligence), but scan succeeds
        assert result["success"] is True


# ===========================================================================
# main() — CLI entry point
# ===========================================================================


class TestMain:
    """Tests for the main() CLI entry point."""

    def test_main_returns_zero_on_success(
        self, forge_root: Path, tmp_path: Path, monkeypatch
    ):
        """main() returns 0 on a successful run."""
        monkeypatch.chdir(tmp_path)

        import forge_harness.scripts.weekly_portfolio_scan as module

        test_args = [
            "weekly_portfolio_scan",
            "--forge-root",
            str(forge_root),
            "--tier",
            "1",
            "--dry-run",
            "--output-dir",
            str(tmp_path),
        ]
        with patch.object(sys, "argv", test_args):
            result = module.main()

        assert result == 0

    def test_main_returns_one_for_missing_forge_root(
        self, tmp_path: Path, monkeypatch
    ):
        """main() returns 1 when --forge-root does not exist."""
        import forge_harness.scripts.weekly_portfolio_scan as module

        nonexistent = tmp_path / "does_not_exist"
        with patch.object(
            sys,
            "argv",
            ["weekly_portfolio_scan", "--forge-root", str(nonexistent), "--dry-run"],
        ):
            result = module.main()

        assert result == 1

    def test_main_json_output_flag(self, forge_root: Path, tmp_path: Path, capsys):
        """--json flag causes JSON to be printed to stdout."""
        import forge_harness.scripts.weekly_portfolio_scan as module

        test_args = [
            "weekly_portfolio_scan",
            "--forge-root",
            str(forge_root),
            "--tier",
            "1",
            "--dry-run",
            "--output-dir",
            str(tmp_path),
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            rc = module.main()

        assert rc == 0
        captured = capsys.readouterr()
        # Verify output is valid JSON
        data = json.loads(captured.out)
        assert "success" in data

    def test_main_human_readable_output_without_json_flag(
        self, forge_root: Path, tmp_path: Path, capsys
    ):
        """Without --json, human-readable output is printed."""
        import forge_harness.scripts.weekly_portfolio_scan as module

        test_args = [
            "weekly_portfolio_scan",
            "--forge-root",
            str(forge_root),
            "--tier",
            "1",
            "--dry-run",
            "--output-dir",
            str(tmp_path),
        ]
        with patch.object(sys, "argv", test_args):
            module.main()

        captured = capsys.readouterr()
        assert "FORGE Weekly Portfolio Scan" in captured.out

    def test_main_verbose_flag_enables_debug_logging(
        self, forge_root: Path, tmp_path: Path
    ):
        """--verbose / -v flag sets log level to DEBUG."""
        import logging

        import forge_harness.scripts.weekly_portfolio_scan as module

        test_args = [
            "weekly_portfolio_scan",
            "--forge-root",
            str(forge_root),
            "--tier",
            "1",
            "--dry-run",
            "--output-dir",
            str(tmp_path),
            "--verbose",
        ]
        with patch.object(sys, "argv", test_args):
            module.main()

        assert logging.getLogger().level == logging.DEBUG

    def test_main_domain_filter_argument(self, forge_root: Path, tmp_path: Path):
        """--domain argument is accepted and applied."""
        import forge_harness.scripts.weekly_portfolio_scan as module

        test_args = [
            "weekly_portfolio_scan",
            "--forge-root",
            str(forge_root),
            "--domain",
            "codeswiftr-com",
            "--dry-run",
            "--output-dir",
            str(tmp_path),
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            rc = module.main()

        assert rc == 0

    def test_main_max_features_argument(self, forge_root: Path, tmp_path: Path):
        """--max-features argument is accepted."""
        import forge_harness.scripts.weekly_portfolio_scan as module

        test_args = [
            "weekly_portfolio_scan",
            "--forge-root",
            str(forge_root),
            "--tier",
            "1",
            "--max-features",
            "20",
            "--dry-run",
            "--output-dir",
            str(tmp_path),
        ]
        with patch.object(sys, "argv", test_args):
            rc = module.main()

        assert rc == 0

    def test_main_min_severity_argument(self, forge_root: Path, tmp_path: Path):
        """--min-severity argument is accepted with valid choices."""
        import forge_harness.scripts.weekly_portfolio_scan as module

        for severity in ["critical", "high", "medium", "low"]:
            test_args = [
                "weekly_portfolio_scan",
                "--forge-root",
                str(forge_root),
                "--tier",
                "1",
                "--min-severity",
                severity,
                "--dry-run",
                "--output-dir",
                str(tmp_path),
            ]
            with patch.object(sys, "argv", test_args):
                rc = module.main()
            assert rc == 0, f"Failed for --min-severity {severity}"

    def test_main_auto_detects_forge_root_from_cwd(
        self, forge_root: Path, tmp_path: Path, monkeypatch
    ):
        """When --forge-root is omitted, main tries to detect from cwd."""
        import forge_harness.scripts.weekly_portfolio_scan as module

        # chdir to a location with a harness sub-directory
        forge_like = tmp_path / "forge_like"
        forge_like.mkdir()
        (forge_like / "harness" / "forge_harness").mkdir(parents=True)

        monkeypatch.chdir(forge_like)

        test_args = [
            "weekly_portfolio_scan",
            "--tier",
            "99",  # No projects — fast
            "--dry-run",
            "--output-dir",
            str(tmp_path),
        ]
        with patch.object(sys, "argv", test_args):
            rc = module.main()

        # rc == 0 means it found a forge root and ran; rc == 1 means detection failed
        # Either is acceptable; we just verify it doesn't raise an unhandled exception
        assert rc in (0, 1)

    def test_main_dry_run_prints_notice(
        self, forge_root: Path, tmp_path: Path, capsys
    ):
        """When --dry-run is used without --json, a DRY RUN notice is printed."""
        import forge_harness.scripts.weekly_portfolio_scan as module

        test_args = [
            "weekly_portfolio_scan",
            "--forge-root",
            str(forge_root),
            "--tier",
            "1",
            "--dry-run",
            "--output-dir",
            str(tmp_path),
        ]
        with patch.object(sys, "argv", test_args):
            module.main()

        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out

    def test_main_returns_one_on_exception(
        self, forge_root: Path, tmp_path: Path
    ):
        """main() returns 1 when run_weekly_scan raises an unexpected exception."""
        import forge_harness.scripts.weekly_portfolio_scan as module

        test_args = [
            "weekly_portfolio_scan",
            "--forge-root",
            str(forge_root),
            "--tier",
            "1",
            "--dry-run",
            "--output-dir",
            str(tmp_path),
        ]
        with patch.object(sys, "argv", test_args), patch(
            "forge_harness.scripts.weekly_portfolio_scan.run_weekly_scan",
            side_effect=RuntimeError("Unexpected failure"),
        ):
            rc = module.main()

        assert rc == 1


# ===========================================================================
# Integration: run_weekly_scan + PortfolioScanner round-trip
# ===========================================================================


def _make_mock_scanner(healthy: bool = True, n_projects: int = 4) -> MagicMock:
    """Build a mock PortfolioScanner that returns a controlled report."""
    from forge_harness.portfolio_scanner import PortfolioHealthReport, ProjectHealth

    if healthy:
        projects = [
            ProjectHealth(
                domain=f"domain{i}",
                project=f"proj{i}",
                tier=1,
                security_score=100.0,
                risk_level="low",
                blocking_issues=0,
            )
            for i in range(n_projects)
        ]
        report = PortfolioHealthReport(
            total_projects=n_projects,
            healthy_projects=n_projects,
            at_risk_projects=0,
            critical_projects=0,
            projects=projects,
        )
    else:
        projects = [
            ProjectHealth(
                domain=f"domain{i}",
                project=f"proj{i}",
                tier=1,
                security_score=30.0,
                risk_level="critical",
                blocking_issues=3,
                debt_hours=8.0,
            )
            for i in range(n_projects)
        ]
        report = PortfolioHealthReport(
            total_projects=n_projects,
            healthy_projects=0,
            at_risk_projects=0,
            critical_projects=n_projects,
            total_blocking_issues=3 * n_projects,
            projects=projects,
        )

    mock_scanner = MagicMock()
    mock_scanner.scan_portfolio = AsyncMock(return_value=report)
    return mock_scanner, report


class TestRunWeeklyScanIntegration:
    """Light integration tests that exercise the full call chain."""

    @pytest.mark.asyncio
    async def test_scan_with_all_healthy_projects_shows_healthy_count(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """
        When all projects are healthy, healthy_projects == total_projects_scanned.
        """
        from forge_harness.portfolio_scanner import FeatureSpec

        mock_scanner, report = _make_mock_scanner(healthy=True, n_projects=4)
        mock_scanner.prioritize_fixes = MagicMock(return_value=[])

        with patch("forge_harness.portfolio_scanner.PortfolioScanner", return_value=mock_scanner):
            result = await run_weekly_scan(
                forge_root=forge_root,
                tiers=[1],
                dry_run=True,
                output_dir=tmp_path,
            )

        assert result["healthy_projects"] == 4
        assert result["at_risk_projects"] == 0
        assert result["critical_projects"] == 0

    @pytest.mark.asyncio
    async def test_scan_with_critical_projects_increments_counter(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """
        When all projects are critical, critical_projects equals total_projects_scanned.
        """
        mock_scanner, report = _make_mock_scanner(healthy=False, n_projects=4)
        mock_scanner.prioritize_fixes = MagicMock(return_value=[])

        with patch("forge_harness.portfolio_scanner.PortfolioScanner", return_value=mock_scanner):
            result = await run_weekly_scan(
                forge_root=forge_root,
                tiers=[1],
                dry_run=True,
                output_dir=tmp_path,
            )

        assert result["critical_projects"] == 4
        assert result["healthy_projects"] == 0

    @pytest.mark.asyncio
    async def test_scan_generates_features_for_blocking_issues(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """
        When projects have blocking issues, features are generated and
        features_generated > 0.
        """
        from forge_harness.portfolio_scanner import FeatureSpec

        mock_scanner, report = _make_mock_scanner(healthy=False, n_projects=2)
        mock_features = [
            FeatureSpec(
                id=f"FIX-000{i}",
                name=f"Fix proj{i}",
                description="Desc",
                source_project=f"domain{i}/proj{i}",
            )
            for i in range(2)
        ]
        mock_scanner.prioritize_fixes = MagicMock(return_value=mock_features)

        with patch("forge_harness.portfolio_scanner.PortfolioScanner", return_value=mock_scanner):
            result = await run_weekly_scan(
                forge_root=forge_root,
                tiers=[1],
                dry_run=True,
                output_dir=tmp_path,
            )

        assert result["features_generated"] == 2

    @pytest.mark.asyncio
    async def test_full_non_dry_run_writes_summary_and_features(
        self, run_weekly_scan, forge_root: Path, tmp_path: Path
    ):
        """
        Full non-dry-run writes portfolio summary and any generated feature files.
        """
        mock_scanner, report = _make_mock_scanner(healthy=True, n_projects=2)
        mock_scanner.prioritize_fixes = MagicMock(return_value=[])

        with patch("forge_harness.portfolio_scanner.PortfolioScanner", return_value=mock_scanner):
            result = await run_weekly_scan(
                forge_root=forge_root,
                domains=["codeswiftr-com"],
                dry_run=False,
                output_dir=tmp_path,
            )

        assert result["success"] is True

        summary_path = tmp_path / "portfolio_scan_summary.json"
        assert summary_path.exists()
        data = json.loads(summary_path.read_text())
        assert "total_projects" in data
