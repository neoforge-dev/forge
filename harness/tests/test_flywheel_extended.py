"""Extended tests for flywheel module targeting uncovered lines.

Targets the following uncovered line ranges:
- 167-172: create_flywheel_loop with None features_path (project exists/not-exists)
- 223-358: scan_project_for_debt Atlas prioritizer integration
- 470: quality_report_to_features security finding with no file_path
- 585, 591, 593: generate_portfolio_features domain filtering (non-dir, underscore, include/exclude)
- 605, 607: project non-dir items and hidden project dirs
- 617: project with no valid markers
- 628-629, 633-651: scan exception handling; harness self-improve success/failure
- 718-721: run_flywheel orchestrator ImportError and generic Exception paths
- 739-750: features.json dict-with-features, dict-without-features, JSONDecodeError
- 754: existing_features not a list guard
- 781-787: write features preserving wrapper format
- 793-794: command_center working_dir override
"""

from __future__ import annotations

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
    quality_report_to_features,
    run_flywheel,
    scan_project_for_debt,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_loop(features_completed: int = 0, features_blocked: int = 0) -> MagicMock:
    loop = MagicMock()
    loop.run = AsyncMock(
        return_value=MagicMock(
            features_completed=features_completed,
            features_blocked=features_blocked,
        )
    )
    return loop


def _patch_ralph_registry():
    """Return both patches needed for create_flywheel_loop calls."""
    return (
        patch("forge_harness.harness_registry.create_harness_registry"),
        patch("forge_harness.ralph_loop.create_ralph_loop_from_registry"),
    )


# ===========================================================================
# Lines 167-172: create_flywheel_loop with features_path=None
# ===========================================================================


class TestCreateFlywheelLoopFeaturesPathNone:
    """Tests for create_flywheel_loop when features_path is not provided."""

    def test_features_path_none_project_exists(self):
        """When features_path is None and the project directory exists,
        features_path defaults to <project_path>/features.json (line 170)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "my-domain"
            project = "my-project"

            # Create the project directory so project_path.exists() is True
            project_dir = forge_root / domain / project
            project_dir.mkdir(parents=True)

            p1, p2 = _patch_ralph_registry()
            with p1 as mock_registry, p2 as mock_create_loop:
                mock_registry.return_value = MagicMock()
                mock_loop = MagicMock()
                mock_create_loop.return_value = mock_loop

                with patch.dict("os.environ", {"FORGE_ROOT": str(forge_root)}):
                    result = create_flywheel_loop(
                        domain=domain,
                        project=project,
                        features_path=None,  # Trigger the auto-detection path
                    )

                assert result is not None
                # features_path passed to create_ralph_loop_from_registry
                call_kwargs = mock_create_loop.call_args[1]
                expected = project_dir / "features.json"
                assert call_kwargs["features_path"] == expected

    def test_features_path_none_project_not_exists(self):
        """When features_path is None and project dir doesn't exist,
        features_path defaults to Path('features.json') (line 172)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "missing-domain"
            project = "missing-project"
            # Do NOT create the project directory

            p1, p2 = _patch_ralph_registry()
            with p1 as mock_registry, p2 as mock_create_loop:
                mock_registry.return_value = MagicMock()
                mock_create_loop.return_value = MagicMock()

                with patch.dict("os.environ", {"FORGE_ROOT": str(forge_root)}):
                    create_flywheel_loop(
                        domain=domain,
                        project=project,
                        features_path=None,
                    )

                call_kwargs = mock_create_loop.call_args[1]
                assert call_kwargs["features_path"] == Path("features.json")

    def test_forge_root_defaults_to_cwd_when_env_unset(self):
        """FORGE_ROOT env var is not set — falls back to Path.cwd()."""
        p1, p2 = _patch_ralph_registry()
        with p1 as mock_registry, p2 as mock_create_loop:
            mock_registry.return_value = MagicMock()
            mock_create_loop.return_value = MagicMock()

            # Ensure FORGE_ROOT is absent
            with patch.dict("os.environ", {}, clear=False):
                import os
                os.environ.pop("FORGE_ROOT", None)

                create_flywheel_loop(
                    domain="d",
                    project="p",
                    features_path=None,
                )

            # The call must have been made (no exception)
            mock_create_loop.assert_called_once()


# ===========================================================================
# Lines 223-358: scan_project_for_debt — Atlas prioritizer integration
# ===========================================================================


class TestScanProjectForDebtAtlasIntegration:
    """Tests for the Code Atlas + incremental scan paths in scan_project_for_debt.

    The function does local imports from forge_harness.meta_learning.bridges.*
    and forge_harness.meta_learning.config.  We patch those module attributes
    directly so the patching is reliable regardless of import order.
    """

    def _make_prioritizer(
        self,
        is_available: bool = False,
        recently_changed: list[str] | None = None,
    ) -> MagicMock:
        """Build a mock AtlasPrioritizer."""
        mock_prioritizer = MagicMock()
        mock_prioritizer.is_available = AsyncMock(return_value=is_available)
        mock_prioritizer.get_recently_changed_files = AsyncMock(
            return_value=recently_changed or []
        )
        mock_prioritizer.filter_by_historical_success = AsyncMock(
            side_effect=lambda findings: findings
        )
        mock_prioritizer.boost_hotspot_priorities = AsyncMock(
            side_effect=lambda findings, domain, project: findings
        )
        return mock_prioritizer

    def _atlas_context(self, mock_bridge, mock_prioritizer):
        """Patch all three lazy imports inside scan_project_for_debt."""
        mock_config = MagicMock()
        mock_atlas_cls = MagicMock()
        mock_atlas_cls.from_config.return_value = MagicMock()
        mock_prioritizer_cls = MagicMock(return_value=mock_prioritizer)

        return (
            patch(
                "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge",
                return_value=mock_bridge,
            ),
            patch(
                "forge_harness.meta_learning.bridges.atlas_prioritizer.AtlasPrioritizer",
                mock_prioritizer_cls,
            ),
            patch(
                "forge_harness.meta_learning.bridges.code_atlas.CodeAtlasBridge",
                mock_atlas_cls,
            ),
            # Patch 'get_config' into the config module namespace so the
            # `from .meta_learning.config import get_config` line succeeds
            patch(
                "forge_harness.meta_learning.config",
                **{"get_config": MagicMock(return_value=mock_config)},
            ),
        )

    @pytest.mark.asyncio
    async def test_incremental_scan_when_atlas_available_with_changed_files(self):
        """When Atlas is available and returns recently-changed files,
        scan_scope is populated (line 241-244)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            mock_finding = MagicMock()
            mock_finding.severity = "high"
            mock_finding.title = "Test issue"
            mock_finding.message = "A test message"
            mock_finding.rule_id = "T001"
            mock_finding.file_path = "test.py"
            mock_finding.line_number = 1
            mock_finding.scanner = "lint"

            mock_report = MagicMock()
            mock_report.findings = [mock_finding]

            mock_bridge = MagicMock()
            mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)

            mock_prioritizer = self._make_prioritizer(
                is_available=True,
                recently_changed=["src/main.py", "src/util.py"],
            )

            with (
                patch(
                    "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge",
                    return_value=mock_bridge,
                ),
                patch(
                    "forge_harness.meta_learning.bridges.atlas_prioritizer.AtlasPrioritizer",
                    return_value=mock_prioritizer,
                ),
                patch(
                    "forge_harness.meta_learning.bridges.code_atlas.CodeAtlasBridge"
                ) as mock_atlas_cls,
                patch(
                    "forge_harness.flywheel.scan_project_for_debt.__module__",
                    create=True,
                ),
            ):
                mock_config_obj = MagicMock()
                mock_atlas_cls.from_config.return_value = MagicMock()

                import forge_harness.meta_learning.config as _cfg_mod
                _cfg_mod.get_config = MagicMock(return_value=mock_config_obj)  # type: ignore[attr-defined]

                try:
                    features = await scan_project_for_debt(
                        domain="test-domain",
                        project="test-project",
                        project_path=project_path,
                        priority_threshold="high",
                    )
                finally:
                    # Restore
                    if hasattr(_cfg_mod, "get_config"):
                        del _cfg_mod.get_config  # type: ignore[attr-defined]

            # The incremental-scan path was hit; features should come back
            assert isinstance(features, list)
            mock_prioritizer.get_recently_changed_files.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_recently_changed_files_logs_debug(self):
        """When Atlas is available but no recently-changed files are returned,
        the full-scan path executes (line 246)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            mock_report = MagicMock()
            mock_report.findings = []

            mock_bridge = MagicMock()
            mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)

            mock_prioritizer = self._make_prioritizer(
                is_available=True,
                recently_changed=[],  # Empty — full scan path
            )

            mock_atlas_cls = MagicMock()
            mock_atlas_cls.from_config.return_value = MagicMock()
            mock_config_obj = MagicMock()

            import forge_harness.meta_learning.config as _cfg_mod
            _cfg_mod.get_config = MagicMock(return_value=mock_config_obj)  # type: ignore[attr-defined]

            try:
                with (
                    patch(
                        "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge",
                        return_value=mock_bridge,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.atlas_prioritizer.AtlasPrioritizer",
                        return_value=mock_prioritizer,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.code_atlas.CodeAtlasBridge",
                        mock_atlas_cls,
                    ),
                ):
                    features = await scan_project_for_debt(
                        domain="test-domain",
                        project="test-project",
                        project_path=project_path,
                    )
            finally:
                if hasattr(_cfg_mod, "get_config"):
                    del _cfg_mod.get_config  # type: ignore[attr-defined]

            assert features == []
            mock_prioritizer.get_recently_changed_files.assert_called_once()

    @pytest.mark.asyncio
    async def test_atlas_enhancements_applied_when_available(self):
        """When Atlas prioritizer is available, filter_by_historical_success and
        boost_hotspot_priorities are called (lines 297-308)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            mock_finding = MagicMock()
            mock_finding.severity = "medium"
            mock_finding.title = "Issue"
            mock_finding.message = "Some issue"
            mock_finding.rule_id = "X001"
            mock_finding.file_path = "app.py"
            mock_finding.line_number = 10
            mock_finding.scanner = "tool"

            mock_report = MagicMock()
            mock_report.findings = [mock_finding]

            mock_bridge = MagicMock()
            mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)

            mock_prioritizer = self._make_prioritizer(is_available=True)

            mock_atlas_cls = MagicMock()
            mock_atlas_cls.from_config.return_value = MagicMock()
            mock_config_obj = MagicMock()

            import forge_harness.meta_learning.config as _cfg_mod
            _cfg_mod.get_config = MagicMock(return_value=mock_config_obj)  # type: ignore[attr-defined]

            try:
                with (
                    patch(
                        "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge",
                        return_value=mock_bridge,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.atlas_prioritizer.AtlasPrioritizer",
                        return_value=mock_prioritizer,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.code_atlas.CodeAtlasBridge",
                        mock_atlas_cls,
                    ),
                ):
                    features = await scan_project_for_debt(
                        domain="test-domain",
                        project="test-project",
                        project_path=project_path,
                        priority_threshold="medium",
                    )
            finally:
                if hasattr(_cfg_mod, "get_config"):
                    del _cfg_mod.get_config  # type: ignore[attr-defined]

            assert mock_prioritizer.filter_by_historical_success.call_count == 1
            assert mock_prioritizer.boost_hotspot_priorities.call_count == 1
            assert isinstance(features, list)

    @pytest.mark.asyncio
    async def test_atlas_not_available_skips_enhancements(self):
        """When Atlas prioritizer is NOT available, enhancements are skipped
        and the 'Code Atlas not available' debug path executes (line 310)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            mock_finding = MagicMock()
            mock_finding.severity = "high"
            mock_finding.title = "Issue"
            mock_finding.message = "High severity issue"
            mock_finding.rule_id = "H001"
            mock_finding.file_path = "src.py"
            mock_finding.line_number = 5
            mock_finding.scanner = "tool"

            mock_report = MagicMock()
            mock_report.findings = [mock_finding]

            mock_bridge = MagicMock()
            mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)

            # Atlas NOT available
            mock_prioritizer = self._make_prioritizer(is_available=False)

            mock_atlas_cls = MagicMock()
            mock_atlas_cls.from_config.return_value = MagicMock()
            mock_config_obj = MagicMock()

            import forge_harness.meta_learning.config as _cfg_mod
            _cfg_mod.get_config = MagicMock(return_value=mock_config_obj)  # type: ignore[attr-defined]

            try:
                with (
                    patch(
                        "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge",
                        return_value=mock_bridge,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.atlas_prioritizer.AtlasPrioritizer",
                        return_value=mock_prioritizer,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.code_atlas.CodeAtlasBridge",
                        mock_atlas_cls,
                    ),
                ):
                    features = await scan_project_for_debt(
                        domain="test-domain",
                        project="test-project",
                        project_path=project_path,
                        priority_threshold="high",
                    )
            finally:
                if hasattr(_cfg_mod, "get_config"):
                    del _cfg_mod.get_config  # type: ignore[attr-defined]

            mock_prioritizer.filter_by_historical_success.assert_not_called()
            mock_prioritizer.boost_hotspot_priorities.assert_not_called()
            assert len(features) == 1

    @pytest.mark.asyncio
    async def test_finding_with_metadata_merges_atlas_metadata(self):
        """When finding_dict contains a 'metadata' key from Atlas,
        it is merged into base_metadata (lines 332-333)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            mock_finding = MagicMock()
            mock_finding.severity = "high"
            mock_finding.title = "Security issue"
            mock_finding.message = "Hardcoded secret"
            mock_finding.rule_id = "SEC001"
            mock_finding.file_path = "config.py"
            mock_finding.line_number = 42
            mock_finding.scanner = "bandit"

            mock_report = MagicMock()
            mock_report.findings = [mock_finding]

            mock_bridge = MagicMock()
            mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)

            def add_metadata(findings):
                for f in findings:
                    f["metadata"] = {"past_solutions": [{"solution": "Use env vars"}]}
                return findings

            mock_prioritizer = self._make_prioritizer(is_available=True)
            mock_prioritizer.filter_by_historical_success = AsyncMock(side_effect=add_metadata)

            mock_atlas_cls = MagicMock()
            mock_atlas_cls.from_config.return_value = MagicMock()
            mock_config_obj = MagicMock()

            import forge_harness.meta_learning.config as _cfg_mod
            _cfg_mod.get_config = MagicMock(return_value=mock_config_obj)  # type: ignore[attr-defined]

            try:
                with (
                    patch(
                        "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge",
                        return_value=mock_bridge,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.atlas_prioritizer.AtlasPrioritizer",
                        return_value=mock_prioritizer,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.code_atlas.CodeAtlasBridge",
                        mock_atlas_cls,
                    ),
                ):
                    features = await scan_project_for_debt(
                        domain="test-domain",
                        project="test-project",
                        project_path=project_path,
                        priority_threshold="high",
                    )
            finally:
                if hasattr(_cfg_mod, "get_config"):
                    del _cfg_mod.get_config  # type: ignore[attr-defined]

            assert len(features) == 1
            assert "past_solutions" in features[0]["metadata"]

    @pytest.mark.asyncio
    async def test_finding_with_null_title_uses_message_truncated(self):
        """When finding.title is None/falsy, message[:60] is used (line 287)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            mock_finding = MagicMock()
            mock_finding.severity = "high"
            mock_finding.title = None  # Falsy → use message[:60]
            mock_finding.message = "A" * 80  # Longer than 60 chars
            mock_finding.rule_id = "T001"
            mock_finding.file_path = "x.py"
            mock_finding.line_number = 1
            mock_finding.scanner = "tool"

            mock_report = MagicMock()
            mock_report.findings = [mock_finding]

            mock_bridge = MagicMock()
            mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)

            mock_prioritizer = self._make_prioritizer(is_available=False)

            mock_atlas_cls = MagicMock()
            mock_atlas_cls.from_config.return_value = MagicMock()
            mock_config_obj = MagicMock()

            import forge_harness.meta_learning.config as _cfg_mod
            _cfg_mod.get_config = MagicMock(return_value=mock_config_obj)  # type: ignore[attr-defined]

            try:
                with (
                    patch(
                        "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge",
                        return_value=mock_bridge,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.atlas_prioritizer.AtlasPrioritizer",
                        return_value=mock_prioritizer,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.code_atlas.CodeAtlasBridge",
                        mock_atlas_cls,
                    ),
                ):
                    features = await scan_project_for_debt(
                        domain="d",
                        project="p",
                        project_path=project_path,
                        priority_threshold="high",
                    )
            finally:
                if hasattr(_cfg_mod, "get_config"):
                    del _cfg_mod.get_config  # type: ignore[attr-defined]

            assert len(features) == 1
            assert "A" * 60 in features[0]["name"]

    @pytest.mark.asyncio
    async def test_finding_with_null_file_path_uses_codebase_placeholder(self):
        """When finding.file_path is None, acceptance criteria uses 'codebase' (line 342)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            mock_finding = MagicMock()
            mock_finding.severity = "high"
            mock_finding.title = "Issue"
            mock_finding.message = "Some message"
            mock_finding.rule_id = "T002"
            mock_finding.file_path = None  # No file path
            mock_finding.line_number = None
            mock_finding.scanner = "tool"

            mock_report = MagicMock()
            mock_report.findings = [mock_finding]

            mock_bridge = MagicMock()
            mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)

            mock_prioritizer = self._make_prioritizer(is_available=False)

            mock_atlas_cls = MagicMock()
            mock_atlas_cls.from_config.return_value = MagicMock()
            mock_config_obj = MagicMock()

            import forge_harness.meta_learning.config as _cfg_mod
            _cfg_mod.get_config = MagicMock(return_value=mock_config_obj)  # type: ignore[attr-defined]

            try:
                with (
                    patch(
                        "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge",
                        return_value=mock_bridge,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.atlas_prioritizer.AtlasPrioritizer",
                        return_value=mock_prioritizer,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.code_atlas.CodeAtlasBridge",
                        mock_atlas_cls,
                    ),
                ):
                    features = await scan_project_for_debt(
                        domain="d",
                        project="p",
                        project_path=project_path,
                        priority_threshold="high",
                    )
            finally:
                if hasattr(_cfg_mod, "get_config"):
                    del _cfg_mod.get_config  # type: ignore[attr-defined]

            assert len(features) == 1
            assert any("codebase" in c for c in features[0]["acceptance_criteria"])

    @pytest.mark.asyncio
    async def test_scan_scope_logged_when_not_empty(self):
        """When scan_scope is non-empty, the debug log with [:5] slice executes (line 254)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            mock_report = MagicMock()
            mock_report.findings = []

            mock_bridge = MagicMock()
            mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)

            mock_prioritizer = self._make_prioritizer(
                is_available=True,
                recently_changed=["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"],
            )

            mock_atlas_cls = MagicMock()
            mock_atlas_cls.from_config.return_value = MagicMock()
            mock_config_obj = MagicMock()

            import forge_harness.meta_learning.config as _cfg_mod
            _cfg_mod.get_config = MagicMock(return_value=mock_config_obj)  # type: ignore[attr-defined]

            try:
                with (
                    patch(
                        "forge_harness.meta_learning.bridges.tech_diligence.TechDiligenceBridge",
                        return_value=mock_bridge,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.atlas_prioritizer.AtlasPrioritizer",
                        return_value=mock_prioritizer,
                    ),
                    patch(
                        "forge_harness.meta_learning.bridges.code_atlas.CodeAtlasBridge",
                        mock_atlas_cls,
                    ),
                ):
                    features = await scan_project_for_debt(
                        domain="d",
                        project="p",
                        project_path=project_path,
                    )
            finally:
                if hasattr(_cfg_mod, "get_config"):
                    del _cfg_mod.get_config  # type: ignore[attr-defined]

            assert features == []


# ===========================================================================
# Line 470: quality_report_to_features — security finding with no file_path
# ===========================================================================


class TestQualityReportToFeaturesNoFilePath:
    """Tests for quality_report_to_features with edge-case findings."""

    def test_security_finding_without_file_path(self):
        """Security finding with file_path=None still generates valid feature (line 470)."""
        from forge_harness.quality_loop import SeverityLevel

        finding = MagicMock()
        finding.severity = SeverityLevel.HIGH
        finding.rule_id = "SEC999"
        finding.message = "Dangerous operation"
        finding.file_path = None  # No file path — exercises the no-location branch
        finding.line_number = None
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
        assert "SEC999" in features[0]["id"]
        # No location line appended when file_path is None
        assert "**Location:**" not in features[0]["description"]

    def test_security_finding_with_file_path_no_line_number(self):
        """Security finding with file_path but no line_number."""
        from forge_harness.quality_loop import SeverityLevel

        finding = MagicMock()
        finding.severity = SeverityLevel.MEDIUM
        finding.rule_id = "SEC888"
        finding.message = "SQL injection risk"
        finding.file_path = "db/queries.py"  # Has file but no line
        finding.line_number = None
        finding.tool = "safety"
        finding.confidence = "medium"

        report = MagicMock()
        report.domain = "d"
        report.project_name = "p"
        report.security_findings = [finding]
        report.quality_score = 85
        report.issues = []
        report.recommendations = []

        features = quality_report_to_features(report, priority_threshold="medium")

        assert len(features) == 1
        assert "**Location:** `db/queries.py`" in features[0]["description"]
        # No line number appended
        assert "db/queries.py`:" not in features[0]["description"]

    def test_debt_feature_medium_priority_for_score_between_50_and_70(self):
        """Quality score between 50-70 produces medium priority debt feature (line 524)."""
        report = MagicMock()
        report.domain = "d"
        report.project_name = "p"
        report.security_findings = []
        report.quality_score = 60  # 50 <= score < 70 → "medium"
        report.issues = ["Issue A"]
        report.recommendations = ["Fix A"]

        features = quality_report_to_features(report)

        assert len(features) == 1
        assert features[0]["priority"] == "medium"

    def test_no_debt_feature_when_score_is_70_or_above(self):
        """No debt feature is added when quality score >= 70."""
        report = MagicMock()
        report.domain = "d"
        report.project_name = "p"
        report.security_findings = []
        report.quality_score = 70  # Exactly 70 — no debt feature
        report.issues = []
        report.recommendations = []

        features = quality_report_to_features(report)

        assert features == []


# ===========================================================================
# Lines 585, 591, 593: generate_portfolio_features domain filtering
# ===========================================================================


class TestGeneratePortfolioFeaturesDomainFiltering:
    """Tests for domain-level filtering in generate_portfolio_features."""

    @pytest.mark.asyncio
    async def test_skips_non_directory_items_in_forge_root(self):
        """Files (non-directories) in forge_root are skipped (line 585)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            # Create a FILE, not a directory
            (forge_root / "not-a-directory.txt").write_text("data")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = []
                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

            mock_scan.assert_not_called()
            assert features == []

    @pytest.mark.asyncio
    async def test_skips_underscore_prefixed_directories(self):
        """Directories starting with '_' are skipped (line 586)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            underscore_dir = forge_root / "_private"
            underscore_dir.mkdir()
            project_dir = underscore_dir / "my-project"
            project_dir.mkdir()
            (project_dir / "pyproject.toml").write_text("[project]")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = []
                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

            mock_scan.assert_not_called()
            assert features == []

    @pytest.mark.asyncio
    async def test_include_domains_filters_to_specified_only(self):
        """Only domains in include_domains are scanned (line 591)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)

            # Two domains
            for name in ("included-domain", "excluded-domain"):
                d = forge_root / name
                d.mkdir()
                p = d / "proj"
                p.mkdir()
                (p / "pyproject.toml").write_text("[project]")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = [{"id": "f1", "name": "F", "priority": "high"}]
                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_domains=["included-domain"],
                    include_harness=False,
                )

            # Only one call for the included domain
            assert mock_scan.call_count == 1
            call_kwargs = mock_scan.call_args[1]
            assert call_kwargs["domain"] == "included-domain"

    @pytest.mark.asyncio
    async def test_exclude_domains_skips_specified_domains(self):
        """Domains in exclude_domains are skipped (line 593)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)

            for name in ("good-domain", "bad-domain"):
                d = forge_root / name
                d.mkdir()
                p = d / "proj"
                p.mkdir()
                (p / "pyproject.toml").write_text("[project]")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = [{"id": "f1", "name": "F", "priority": "high"}]
                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    exclude_domains=["bad-domain"],
                    include_harness=False,
                )

            # Only the good domain was scanned
            assert mock_scan.call_count == 1
            call_kwargs = mock_scan.call_args[1]
            assert call_kwargs["domain"] == "good-domain"


# ===========================================================================
# Lines 605, 607: project non-dir items and hidden project dirs
# ===========================================================================


class TestGeneratePortfolioFeaturesProjectFiltering:
    """Tests for project-level filtering inside domains."""

    @pytest.mark.asyncio
    async def test_skips_files_inside_domain_directory(self):
        """Files (non-directories) inside a domain dir are skipped (line 605)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain_dir = forge_root / "my-domain"
            domain_dir.mkdir()
            # A file, not a project dir
            (domain_dir / "README.md").write_text("docs")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = []
                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

            mock_scan.assert_not_called()
            assert features == []

    @pytest.mark.asyncio
    async def test_skips_hidden_project_directories(self):
        """Project directories starting with '.' are skipped (line 607)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain_dir = forge_root / "my-domain"
            domain_dir.mkdir()
            hidden_project = domain_dir / ".hidden-project"
            hidden_project.mkdir()
            (hidden_project / "pyproject.toml").write_text("[project]")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = []
                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

            mock_scan.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_project_without_valid_markers(self):
        """Project directories lacking any valid marker file are skipped (line 617)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain_dir = forge_root / "my-domain"
            domain_dir.mkdir()
            empty_project = domain_dir / "no-markers"
            empty_project.mkdir()
            # No CLAUDE.md, pyproject.toml, package.json, or Cargo.toml

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = []
                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

            mock_scan.assert_not_called()

    @pytest.mark.asyncio
    async def test_detects_cargo_toml_as_valid_marker(self):
        """Cargo.toml is a valid project marker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain_dir = forge_root / "rust-domain"
            domain_dir.mkdir()
            rust_project = domain_dir / "rust-project"
            rust_project.mkdir()
            (rust_project / "Cargo.toml").write_text("[package]")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = [{"id": "r1", "name": "R", "priority": "low"}]
                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

            mock_scan.assert_called_once()
            assert len(features) == 1

    @pytest.mark.asyncio
    async def test_detects_claude_md_as_valid_marker(self):
        """CLAUDE.md is a valid project marker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain_dir = forge_root / "some-domain"
            domain_dir.mkdir()
            project_dir = domain_dir / "some-project"
            project_dir.mkdir()
            (project_dir / "CLAUDE.md").write_text("# Instructions")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = [{"id": "c1", "name": "C", "priority": "medium"}]
                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

            mock_scan.assert_called_once()


# ===========================================================================
# Lines 628-629: scan exception handling in generate_portfolio_features
# ===========================================================================


class TestGeneratePortfolioFeaturesExceptionHandling:
    """Tests for exception handling within project scanning loop."""

    @pytest.mark.asyncio
    async def test_scan_exception_is_caught_and_logged(self):
        """Exception during scan_project_for_debt is caught, logged, and skipped (lines 628-629)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain_dir = forge_root / "test-domain"
            domain_dir.mkdir()
            project_dir = domain_dir / "test-project"
            project_dir.mkdir()
            (project_dir / "pyproject.toml").write_text("[project]")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.side_effect = RuntimeError("Scan exploded")
                # Should NOT raise — exception is swallowed
                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

            # Returns empty rather than propagating
            assert features == []

    @pytest.mark.asyncio
    async def test_scan_exception_does_not_stop_other_projects(self):
        """Exception for one project doesn't prevent scanning others."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain_dir = forge_root / "test-domain"
            domain_dir.mkdir()

            for name in ("project-a", "project-b"):
                pd = domain_dir / name
                pd.mkdir()
                (pd / "pyproject.toml").write_text("[project]")

            call_count = 0

            async def flaky_scan(**kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("First scan failed")
                return [{"id": f"feat-{call_count}", "name": "F", "priority": "high"}]

            with patch("forge_harness.flywheel.scan_project_for_debt", side_effect=flaky_scan):
                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

            # Second project should still produce a feature
            assert len(features) == 1


# ===========================================================================
# Lines 633-651: harness self-improve path in generate_portfolio_features
# ===========================================================================


class TestGeneratePortfolioFeaturesHarnessPath:
    """Tests for the harness self-improvement scanning path."""

    @pytest.mark.asyncio
    async def test_harness_self_improve_success(self):
        """When harness dir exists and HarnessSelfImprover works, features are added (lines 636-649)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            harness_dir = forge_root / "harness"
            harness_dir.mkdir()

            mock_feature = MagicMock()
            mock_feature.to_dict.return_value = {
                "id": "harness-001",
                "name": "Harness improvement",
                "priority": "medium",
            }

            mock_result = MagicMock()
            mock_result.features_generated = [mock_feature]

            mock_improver = MagicMock()
            mock_improver.analyze = AsyncMock(return_value=mock_result)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.self_improve.HarnessSelfImprover") as mock_cls,
            ):
                mock_scan.return_value = []
                mock_cls.return_value = mock_improver

                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=True,
                )

            assert len(features) == 1
            assert features[0]["id"] == "harness-001"
            mock_improver.analyze.assert_called_once()

    @pytest.mark.asyncio
    async def test_harness_self_improve_exception_is_caught(self):
        """Exception in harness self-improve is caught, logged, and skipped (line 651)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            harness_dir = forge_root / "harness"
            harness_dir.mkdir()

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = []

                # Make HarnessSelfImprover raise on import/construction
                with patch("forge_harness.self_improve.HarnessSelfImprover") as mock_cls:
                    mock_cls.side_effect = Exception("HarnessSelfImprover broken")

                    features = await generate_portfolio_features(
                        forge_root=forge_root,
                        include_harness=True,
                    )

            # Should not raise; returns what we have
            assert features == []

    @pytest.mark.asyncio
    async def test_harness_path_not_scanned_when_harness_dir_missing(self):
        """When harness dir doesn't exist, self-improve is skipped entirely."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            # harness/ dir does NOT exist

            mock_improver = MagicMock()
            mock_improver.analyze = AsyncMock()

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.self_improve.HarnessSelfImprover") as mock_cls,
            ):
                mock_scan.return_value = []
                mock_cls.return_value = mock_improver

                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=True,
                )

            # analyze should never be called since harness_path doesn't exist
            mock_improver.analyze.assert_not_called()


# ===========================================================================
# Lines 718-721: run_flywheel orchestrator ImportError / Exception paths
# ===========================================================================


class TestRunFlywheelOrchestratorCreation:
    """Tests for the orchestrator creation paths in run_flywheel."""

    @pytest.mark.asyncio
    async def test_orchestrator_import_error_is_handled(self):
        """ImportError when creating FeatureOrchestrator is caught (line 718-719).

        Setting forge_harness.agent to None in sys.modules causes the lazy
        `from .agent import FeatureOrchestrator` to raise ImportError, which
        must be caught so run_flywheel completes normally.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "d"
            project = "p"
            (forge_root / domain / project).mkdir(parents=True)

            # dry_run=False triggers orchestrator-creation path
            config = FlywheelConfig(dry_run=False)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
                patch.dict("sys.modules", {"forge_harness.agent": None}),
            ):
                mock_scan.return_value = []
                mock_create_loop.return_value = _make_mock_loop()

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=True,
                    orchestrator=None,
                )

            # Should complete without raising; errors captured in result
            assert isinstance(result, FlywheelResult)

    @pytest.mark.asyncio
    async def test_orchestrator_generic_exception_is_handled(self):
        """Generic Exception when creating FeatureOrchestrator is caught (lines 720-721)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "d"
            project = "p"
            (forge_root / domain / project).mkdir(parents=True)

            config = FlywheelConfig(dry_run=False)

            # Mock agent module with a FeatureOrchestrator that raises on construction
            mock_agent_module = MagicMock()
            mock_agent_module.FeatureOrchestrator = MagicMock(
                side_effect=Exception("Cannot init orchestrator")
            )

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
                patch.dict("sys.modules", {"forge_harness.agent": mock_agent_module}),
            ):
                mock_scan.return_value = []
                mock_create_loop.return_value = _make_mock_loop()

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=True,
                    orchestrator=None,
                )

            # Should complete; orchestrator error is swallowed
            assert isinstance(result, FlywheelResult)
            # No orchestrator errors propagated to result
            assert result.ended_at is not None


# ===========================================================================
# Lines 739-754: run_flywheel features.json loading edge cases
# ===========================================================================


class TestRunFlywheelFeaturesJsonLoading:
    """Tests for the features.json loading logic inside run_flywheel."""

    @pytest.mark.asyncio
    async def test_loads_wrapper_format_with_features_key(self):
        """Dict with 'features' key is parsed correctly (lines 740-742)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "d"
            project = "p"
            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)

            existing = {"version": "1.0", "features": [{"id": "f1", "name": "Existing"}]}
            (project_path / "features.json").write_text(json.dumps(existing))

            config = FlywheelConfig(dry_run=True)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
            ):
                mock_scan.return_value = []
                mock_create_loop.return_value = _make_mock_loop()

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=False,
                )

            assert result.projects_scanned == 1

    @pytest.mark.asyncio
    async def test_loads_dict_without_features_key_treats_as_empty(self):
        """Dict without 'features' key triggers warning and uses empty list (lines 744-748)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "d"
            project = "p"
            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)

            bad_dict = {"metadata": {"version": "unknown"}}
            (project_path / "features.json").write_text(json.dumps(bad_dict))

            config = FlywheelConfig(dry_run=True)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
            ):
                mock_scan.return_value = []
                mock_create_loop.return_value = _make_mock_loop()

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=False,
                )

            # Should complete; existing_features treated as empty
            assert result.projects_scanned == 1
            assert result.features_generated == 0

    @pytest.mark.asyncio
    async def test_handles_json_decode_error_in_features_file(self):
        """JSONDecodeError in features.json is caught and execution continues (line 749-750)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "d"
            project = "p"
            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)

            (project_path / "features.json").write_text("INVALID JSON {{{")

            config = FlywheelConfig(dry_run=True)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
            ):
                mock_scan.return_value = []
                mock_create_loop.return_value = _make_mock_loop()

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=False,
                )

            # Continues normally after JSONDecodeError
            assert isinstance(result, FlywheelResult)
            assert result.ended_at is not None

    @pytest.mark.asyncio
    async def test_non_list_existing_features_reset_to_empty(self):
        """When existing_features resolves to non-list value, it resets to [] (line 754)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "d"
            project = "p"
            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)

            # Write features.json with a dict that has a non-list 'features' value
            bad_format = {"features": "not-a-list"}
            (project_path / "features.json").write_text(json.dumps(bad_format))

            config = FlywheelConfig(dry_run=True)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
            ):
                mock_scan.return_value = []
                mock_create_loop.return_value = _make_mock_loop()

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=False,
                )

            # Should complete; non-list features treated as empty
            assert isinstance(result, FlywheelResult)


# ===========================================================================
# Lines 781-787: write features preserving wrapper format
# ===========================================================================


class TestRunFlywheelWriteFeatures:
    """Tests for the feature-writing phase of run_flywheel."""

    @pytest.mark.asyncio
    async def test_writes_features_preserving_wrapper_format(self):
        """When features_wrapper is not None, the wrapper format is preserved on write (lines 781-786)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "d"
            project = "p"
            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)

            wrapper = {"version": "2.0", "owner": "test-team", "features": []}
            features_file = project_path / "features.json"
            features_file.write_text(json.dumps(wrapper))

            config = FlywheelConfig(dry_run=False)  # Not dry_run so it writes

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
            ):
                mock_scan.return_value = [
                    {"id": "new-feat-001", "name": "New Feature", "priority": "high"}
                ]
                mock_create_loop.return_value = _make_mock_loop(features_completed=1)

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=False,
                )

            assert result.features_generated == 1

            # Verify wrapper format is preserved on disk
            saved = json.loads(features_file.read_text())
            assert saved["version"] == "2.0"
            assert saved["owner"] == "test-team"
            assert len(saved["features"]) == 1
            assert saved["features"][0]["id"] == "new-feat-001"

    @pytest.mark.asyncio
    async def test_writes_features_as_plain_list_when_no_wrapper(self):
        """When features_wrapper is None (plain list format), writes a plain list (line 785)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "d"
            project = "p"
            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)

            # Start with plain list format
            features_file = project_path / "features.json"
            features_file.write_text("[]")

            config = FlywheelConfig(dry_run=False)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
            ):
                mock_scan.return_value = [
                    {"id": "plain-001", "name": "Plain Feature", "priority": "medium"}
                ]
                mock_create_loop.return_value = _make_mock_loop()

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=False,
                )

            assert result.features_generated == 1

            # Verify it was written as a plain list
            saved = json.loads(features_file.read_text())
            assert isinstance(saved, list)
            assert len(saved) == 1
            assert saved[0]["id"] == "plain-001"

    @pytest.mark.asyncio
    async def test_no_write_when_dry_run(self):
        """In dry_run mode, features.json is not written even if new features were found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "d"
            project = "p"
            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)

            features_file = project_path / "features.json"
            features_file.write_text("[]")
            mtime_before = features_file.stat().st_mtime

            config = FlywheelConfig(dry_run=True)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
            ):
                mock_scan.return_value = [
                    {"id": "dry-001", "name": "Dry Run Feature", "priority": "low"}
                ]
                mock_create_loop.return_value = _make_mock_loop()

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=False,
                )

            assert result.features_generated == 1
            # File must NOT have been modified
            assert features_file.stat().st_mtime == mtime_before

    @pytest.mark.asyncio
    async def test_no_write_when_no_new_features(self):
        """Features file is not written when there are no new (non-duplicate) features."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "d"
            project = "p"
            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)

            existing = [{"id": "existing-001", "name": "E", "priority": "high"}]
            features_file = project_path / "features.json"
            features_file.write_text(json.dumps(existing))
            mtime_before = features_file.stat().st_mtime

            config = FlywheelConfig(dry_run=False)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
            ):
                # Return the same existing feature — should be deduped (added == 0)
                mock_scan.return_value = [
                    {"id": "existing-001", "name": "Duplicate", "priority": "high"}
                ]
                mock_create_loop.return_value = _make_mock_loop()

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=False,
                )

            assert result.features_generated == 0
            # No write because added == 0
            assert features_file.stat().st_mtime == mtime_before


# ===========================================================================
# Lines 793-794: command_center working_dir override
# ===========================================================================


class TestRunFlywheelCommandCenterOverride:
    """Tests for the command_center special-case working_dir logic."""

    @pytest.mark.asyncio
    async def test_command_center_sets_working_dir_to_harness(self):
        """domain='harness' + project='command_center' sets config.working_dir
        to forge_root/harness (lines 792-794)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "harness"
            project = "command_center"

            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)
            (project_path / "features.json").write_text("[]")

            config = FlywheelConfig(dry_run=True, working_dir=None)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
            ):
                mock_scan.return_value = []
                mock_create_loop.return_value = _make_mock_loop()

                result = await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=False,
                )

            # working_dir should have been set to forge_root/harness
            expected_working_dir = (forge_root / "harness").resolve()
            assert config.working_dir == expected_working_dir
            assert isinstance(result, FlywheelResult)

    @pytest.mark.asyncio
    async def test_command_center_does_not_override_existing_working_dir(self):
        """If working_dir is already set, command_center does not override it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "harness"
            project = "command_center"

            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)
            (project_path / "features.json").write_text("[]")

            custom_dir = Path("/tmp")
            config = FlywheelConfig(dry_run=True, working_dir=custom_dir)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
            ):
                mock_scan.return_value = []
                mock_create_loop.return_value = _make_mock_loop()

                await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=False,
                )

            # working_dir should NOT have been changed
            assert config.working_dir == custom_dir

    @pytest.mark.asyncio
    async def test_non_command_center_project_does_not_override_working_dir(self):
        """Non command_center projects leave config.working_dir unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            domain = "other-domain"
            project = "other-project"

            project_path = forge_root / domain / project
            project_path.mkdir(parents=True)
            (project_path / "features.json").write_text("[]")

            config = FlywheelConfig(dry_run=True, working_dir=None)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create_loop,
            ):
                mock_scan.return_value = []
                mock_create_loop.return_value = _make_mock_loop()

                await run_flywheel(
                    forge_root=forge_root,
                    domain=domain,
                    project=project,
                    config=config,
                    create_orchestrator=False,
                )

            # working_dir stays None for non-command_center projects
            assert config.working_dir is None


# ===========================================================================
# Portfolio feature sorting / dedup
# ===========================================================================


class TestGeneratePortfolioFeaturesSortingAndDedup:
    """Tests for priority sorting and deduplication in generate_portfolio_features."""

    @pytest.mark.asyncio
    async def test_features_sorted_by_priority(self):
        """Output features are sorted critical > high > medium > low."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)

            for i, priority in enumerate(["low", "critical", "medium", "high"]):
                d = forge_root / f"domain-{i}"
                d.mkdir()
                p = d / "project"
                p.mkdir()
                (p / "pyproject.toml").write_text("[project]")

            # Each project returns one feature with a different priority
            call_count = [0]
            priorities_out = ["low", "critical", "medium", "high"]

            async def mock_scan(**kwargs):
                idx = call_count[0]
                call_count[0] += 1
                return [
                    {"id": f"feat-{idx}", "name": f"Feature {idx}", "priority": priorities_out[idx]}
                ]

            with patch("forge_harness.flywheel.scan_project_for_debt", side_effect=mock_scan):
                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

            assert len(features) == 4
            assert features[0]["priority"] == "critical"
            assert features[-1]["priority"] == "low"

    @pytest.mark.asyncio
    async def test_priority_sort_with_unknown_priority(self):
        """Features with unknown priority values are sorted as 'medium'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            d = forge_root / "domain"
            d.mkdir()
            p = d / "project"
            p.mkdir()
            (p / "pyproject.toml").write_text("[project]")

            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = [
                    {"id": "u1", "name": "Unknown", "priority": "unknown-value"},
                    {"id": "c1", "name": "Critical", "priority": "critical"},
                ]
                features = await generate_portfolio_features(
                    forge_root=forge_root,
                    include_harness=False,
                )

            assert len(features) == 2
            # Critical should be first
            assert features[0]["priority"] == "critical"


# ===========================================================================
# FlywheelConfig: test_command and working_dir fields
# ===========================================================================


class TestFlywheelConfigAdditionalFields:
    """Tests for FlywheelConfig fields not covered by basic tests."""

    def test_test_command_field(self):
        """FlywheelConfig stores test_command."""
        config = FlywheelConfig(test_command="uv run pytest -x")
        assert config.test_command == "uv run pytest -x"

    def test_working_dir_field(self):
        """FlywheelConfig stores working_dir as Path."""
        wd = Path("/tmp/harness")
        config = FlywheelConfig(working_dir=wd)
        assert config.working_dir == wd

    def test_auto_commit_field(self):
        """FlywheelConfig stores auto_commit flag."""
        config = FlywheelConfig(auto_commit=True)
        assert config.auto_commit is True

    def test_all_fields_default(self):
        """All FlywheelConfig fields have correct defaults."""
        config = FlywheelConfig()
        assert config.test_command is None
        assert config.working_dir is None
        assert config.auto_commit is False
        assert config.include_harness_self_improvement is True
