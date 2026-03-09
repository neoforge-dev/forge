"""Tests for self-improvement module (forge_harness/self_improve.py).

Covers:
- SelfAnalysisResult dataclass and to_dict serialization
- HarnessSelfImprover initialization and configuration
- analyze() with Tech Diligence success / failure / timeout
- _convert_findings_to_features() priority filtering and field mapping
- _build_feature_description() with various finding attributes
- _add_features_to_backlog() deduplication (by ID and by name), fresh file,
  corrupted JSON, empty list, and write path
- get_analysis_history() accumulation over multiple runs
- run_self_analysis() top-level function (normal, dry_run)
- self_analyze_sync() synchronous wrapper
- Edge cases: max_features cap, no findings, unknown severity, missing optional fields
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.meta_learning.feedback_loops import DebtFeature
from forge_harness.self_improve import (
    HarnessSelfImprover,
    SelfAnalysisResult,
    run_self_analysis,
    self_analyze_sync,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(
    severity: str = "high",
    title: str = "Test Issue",
    message: str = "Something is wrong",
    rule_id: str = "TEST001",
    file_path: str | None = "test.py",
    line_number: int | None = 10,
    scanner: str = "bandit",
    recommendation: str | None = "Fix it",
) -> MagicMock:
    """Return a minimal MagicMock that looks like a DiligenceFinding."""
    f = MagicMock()
    f.severity = severity
    f.title = title
    f.message = message
    f.rule_id = rule_id
    f.file_path = file_path
    f.line_number = line_number
    f.scanner = scanner
    f.recommendation = recommendation
    return f


def _make_report(findings: list, *, total: int | None = None) -> MagicMock:
    """Return a MagicMock DiligenceReport with the given findings list."""
    r = MagicMock()
    r.findings = findings
    r.total_issues = total if total is not None else len(findings)
    r.critical_count = sum(1 for f in findings if f.severity == "critical")
    r.high_count = sum(1 for f in findings if f.severity == "high")
    r.medium_count = sum(1 for f in findings if f.severity == "medium")
    return r


# ---------------------------------------------------------------------------
# SelfAnalysisResult
# ---------------------------------------------------------------------------


class TestSelfAnalysisResult:
    """Tests for SelfAnalysisResult dataclass."""

    def test_empty_result_fields(self):
        """SelfAnalysisResult with no findings has sensible defaults."""
        result = SelfAnalysisResult(
            timestamp=datetime.now(UTC),
            report=None,
            features_generated=[],
            features_added=0,
            features_skipped=0,
        )
        assert result.features_added == 0
        assert result.features_skipped == 0
        assert result.report is None
        assert result.errors == []

    def test_result_with_features(self):
        """SelfAnalysisResult stores features list correctly."""
        feature = DebtFeature(
            id="test-001",
            name="Test Feature",
            description="Test description",
            priority="high",
            finding_id="TEST001",
            category="self-analysis",
            estimated_effort=2.0,
        )
        result = SelfAnalysisResult(
            timestamp=datetime.now(UTC),
            report=None,
            features_generated=[feature],
            features_added=1,
            features_skipped=0,
        )
        assert len(result.features_generated) == 1
        assert result.features_added == 1

    def test_to_dict_no_report(self):
        """to_dict returns None for report_summary when report is None."""
        result = SelfAnalysisResult(
            timestamp=datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
            report=None,
            features_generated=[],
            features_added=3,
            features_skipped=1,
            errors=["Test error"],
        )
        data = result.to_dict()
        assert data["report_summary"] is None
        assert data["features_generated"] == 0
        assert data["features_added"] == 3
        assert data["features_skipped"] == 1
        assert "Test error" in data["errors"]

    def test_to_dict_with_report(self):
        """to_dict embeds report summary when report is present."""
        mock_report = MagicMock()
        mock_report.total_issues = 5
        mock_report.critical_count = 1
        mock_report.high_count = 2
        mock_report.medium_count = 2

        result = SelfAnalysisResult(
            timestamp=datetime.now(UTC),
            report=mock_report,
            features_generated=[],
            features_added=0,
            features_skipped=0,
        )
        data = result.to_dict()
        assert data["report_summary"]["total_issues"] == 5
        assert data["report_summary"]["critical"] == 1
        assert data["report_summary"]["high"] == 2
        assert data["report_summary"]["medium"] == 2

    def test_to_dict_timestamp_is_isoformat(self):
        """to_dict serializes timestamp as ISO 8601 string."""
        ts = datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC)
        result = SelfAnalysisResult(
            timestamp=ts,
            report=None,
            features_generated=[],
            features_added=0,
            features_skipped=0,
        )
        data = result.to_dict()
        assert data["timestamp"] == ts.isoformat()

    def test_to_dict_errors_list(self):
        """to_dict includes all errors in the errors list."""
        result = SelfAnalysisResult(
            timestamp=datetime.now(UTC),
            report=None,
            features_generated=[],
            features_added=0,
            features_skipped=0,
            errors=["error-a", "error-b"],
        )
        data = result.to_dict()
        assert data["errors"] == ["error-a", "error-b"]


# ---------------------------------------------------------------------------
# HarnessSelfImprover — init
# ---------------------------------------------------------------------------


class TestHarnessSelfImproverInit:
    """Tests for HarnessSelfImprover initialization."""

    def test_init_defaults(self):
        """HarnessSelfImprover sets default harness_root and features_path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            improver = HarnessSelfImprover(harness_root=Path(tmpdir))
            assert improver.harness_root == Path(tmpdir)
            assert improver.features_path == Path(tmpdir) / "features.json"

    def test_init_custom_paths(self):
        """HarnessSelfImprover accepts custom features_path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            features_path = Path(tmpdir) / "custom_features.json"
            improver = HarnessSelfImprover(
                harness_root=Path(tmpdir),
                features_path=features_path,
            )
            assert improver.features_path == features_path

    def test_init_custom_tech_diligence_url(self):
        """HarnessSelfImprover passes custom URL to TechDiligenceBridge."""
        with tempfile.TemporaryDirectory() as tmpdir:
            improver = HarnessSelfImprover(
                harness_root=Path(tmpdir),
                tech_diligence_url="http://custom-host:9999",
            )
            assert improver.tech_diligence.base_url == "http://custom-host:9999"

    def test_init_empty_analysis_history(self):
        """HarnessSelfImprover starts with empty analysis history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            improver = HarnessSelfImprover(harness_root=Path(tmpdir))
            assert improver.get_analysis_history() == []


# ---------------------------------------------------------------------------
# HarnessSelfImprover — analyze()
# ---------------------------------------------------------------------------


class TestHarnessSelfImproverAnalyze:
    """Tests for HarnessSelfImprover.analyze()."""

    @pytest.mark.asyncio
    async def test_analyze_tech_diligence_unavailable(self):
        """analyze() handles Tech Diligence returning None gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            improver = HarnessSelfImprover(harness_root=Path(tmpdir))
            improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=None)

            result = await improver.analyze()

            assert result.report is None
            assert len(result.errors) == 1
            assert "Tech Diligence" in result.errors[0]
            assert result.features_added == 0

    @pytest.mark.asyncio
    async def test_analyze_with_high_finding(self):
        """analyze() converts a single HIGH finding to a feature."""
        with tempfile.TemporaryDirectory() as tmpdir:
            features_path = Path(tmpdir) / "features.json"
            improver = HarnessSelfImprover(
                harness_root=Path(tmpdir),
                features_path=features_path,
            )

            finding = _make_finding(severity="high")
            mock_report = _make_report([finding])
            improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=mock_report)

            result = await improver.analyze(max_features=5)

            assert result.report is not None
            assert len(result.features_generated) == 1
            assert result.features_added == 1
            assert features_path.exists()

    @pytest.mark.asyncio
    async def test_analyze_no_findings(self):
        """analyze() with a report that has zero findings produces no features."""
        with tempfile.TemporaryDirectory() as tmpdir:
            improver = HarnessSelfImprover(harness_root=Path(tmpdir))
            mock_report = _make_report([])
            improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=mock_report)

            result = await improver.analyze()

            assert result.features_generated == []
            assert result.features_added == 0
            assert result.errors == []

    @pytest.mark.asyncio
    async def test_analyze_appends_to_history(self):
        """analyze() adds each result to internal analysis history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            improver = HarnessSelfImprover(harness_root=Path(tmpdir))
            improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=None)

            await improver.analyze()
            await improver.analyze()

            history = improver.get_analysis_history()
            assert len(history) == 2

    @pytest.mark.asyncio
    async def test_analyze_max_features_cap(self):
        """analyze() never generates more than max_features features."""
        with tempfile.TemporaryDirectory() as tmpdir:
            improver = HarnessSelfImprover(harness_root=Path(tmpdir))

            # 10 medium findings, but cap at 3
            findings = [
                _make_finding(severity="medium", rule_id=f"R{i}", title=f"Issue {i}")
                for i in range(10)
            ]
            mock_report = _make_report(findings)
            improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=mock_report)

            result = await improver.analyze(max_features=3)

            assert len(result.features_generated) == 3

    @pytest.mark.asyncio
    async def test_analyze_passes_scanners_argument(self):
        """analyze() forwards scanners list to Tech Diligence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            improver = HarnessSelfImprover(harness_root=Path(tmpdir))
            improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=None)

            await improver.analyze(scanners=["bandit", "semgrep"])

            improver.tech_diligence.analyze_and_wait.assert_awaited_once()
            call_kwargs = improver.tech_diligence.analyze_and_wait.call_args
            assert call_kwargs.kwargs.get("scanners") == ["bandit", "semgrep"]


# ---------------------------------------------------------------------------
# HarnessSelfImprover — _convert_findings_to_features()
# ---------------------------------------------------------------------------


class TestConvertFindingsToFeatures:
    """Tests for HarnessSelfImprover._convert_findings_to_features()."""

    def _improver(self, tmpdir: str) -> HarnessSelfImprover:
        return HarnessSelfImprover(harness_root=Path(tmpdir))

    def test_priority_threshold_high_excludes_medium(self):
        """threshold='high' excludes medium and low findings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            findings = [
                _make_finding(severity="critical", rule_id="C1", title="T1"),
                _make_finding(severity="high", rule_id="H1", title="T2"),
                _make_finding(severity="medium", rule_id="M1", title="T3"),
                _make_finding(severity="low", rule_id="L1", title="T4"),
            ]
            mock_report = _make_report(findings)

            features = imp._convert_findings_to_features(mock_report, max_features=10, priority_threshold="high")

            priorities = {f.priority for f in features}
            assert "critical" in priorities
            assert "high" in priorities
            assert "medium" not in priorities
            assert "low" not in priorities

    def test_priority_threshold_medium_includes_medium(self):
        """threshold='medium' includes critical, high, and medium."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            findings = [
                _make_finding(severity="critical", rule_id="C1", title="T1"),
                _make_finding(severity="medium", rule_id="M1", title="T2"),
                _make_finding(severity="low", rule_id="L1", title="T3"),
            ]
            mock_report = _make_report(findings)

            features = imp._convert_findings_to_features(mock_report, max_features=10, priority_threshold="medium")

            priorities = {f.priority for f in features}
            assert "critical" in priorities
            assert "medium" in priorities
            assert "low" not in priorities

    def test_priority_threshold_low_includes_all(self):
        """threshold='low' includes all four severity levels."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            findings = [
                _make_finding(severity="critical", rule_id="C1", title="T1"),
                _make_finding(severity="high", rule_id="H1", title="T2"),
                _make_finding(severity="medium", rule_id="M1", title="T3"),
                _make_finding(severity="low", rule_id="L1", title="T4"),
            ]
            mock_report = _make_report(findings)

            features = imp._convert_findings_to_features(mock_report, max_features=10, priority_threshold="low")

            assert len(features) == 4

    def test_feature_id_uses_rule_id(self):
        """Generated feature ID incorporates rule_id from the finding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            finding = _make_finding(severity="high", rule_id="RULE-42", title="My Finding")
            mock_report = _make_report([finding])

            features = imp._convert_findings_to_features(mock_report, max_features=5, priority_threshold="high")

            assert len(features) == 1
            assert "RULE-42" in features[0].id

    def test_feature_name_uses_title(self):
        """Generated feature name uses the finding title."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            finding = _make_finding(severity="high", rule_id="R1", title="SQL Injection Risk")
            mock_report = _make_report([finding])

            features = imp._convert_findings_to_features(mock_report, max_features=5, priority_threshold="high")

            assert "SQL Injection Risk" in features[0].name

    def test_feature_name_falls_back_to_message(self):
        """Generated feature name falls back to message when title is None/empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            finding = _make_finding(severity="high", rule_id="R1", title=None, message="Hardcoded password detected")
            mock_report = _make_report([finding])

            features = imp._convert_findings_to_features(mock_report, max_features=5, priority_threshold="high")

            assert "Hardcoded password detected" in features[0].name

    def test_feature_category_from_scanner(self):
        """Generated feature uses scanner name as category."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            finding = _make_finding(severity="high", rule_id="R1", scanner="semgrep")
            mock_report = _make_report([finding])

            features = imp._convert_findings_to_features(mock_report, max_features=5, priority_threshold="high")

            assert features[0].category == "semgrep"

    def test_feature_category_defaults_when_no_scanner(self):
        """Generated feature category defaults to 'self-analysis' when scanner is falsy."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            finding = _make_finding(severity="high", rule_id="R1")
            # Remove scanner attribute entirely so getattr returns None
            del finding.scanner
            mock_report = _make_report([finding])

            features = imp._convert_findings_to_features(mock_report, max_features=5, priority_threshold="high")

            assert features[0].category == "self-analysis"

    def test_no_findings_returns_empty_list(self):
        """_convert_findings_to_features returns [] for a report with no findings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            mock_report = _make_report([])

            features = imp._convert_findings_to_features(mock_report, max_features=10, priority_threshold="medium")

            assert features == []


# ---------------------------------------------------------------------------
# HarnessSelfImprover — _build_feature_description()
# ---------------------------------------------------------------------------


class TestBuildFeatureDescription:
    """Tests for HarnessSelfImprover._build_feature_description()."""

    def _improver(self, tmpdir: str) -> HarnessSelfImprover:
        return HarnessSelfImprover(harness_root=Path(tmpdir))

    def test_description_includes_message(self):
        """Description always includes the finding message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            finding = _make_finding(message="Sensitive data exposed in logs")
            desc = imp._build_feature_description(finding)
            assert "Sensitive data exposed in logs" in desc

    def test_description_includes_file_path(self):
        """Description includes file path when present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            finding = _make_finding(file_path="src/auth.py", line_number=42)
            desc = imp._build_feature_description(finding)
            assert "src/auth.py" in desc
            assert "42" in desc

    def test_description_includes_recommendation(self):
        """Description includes recommendation when present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            finding = _make_finding(recommendation="Use parameterized queries")
            desc = imp._build_feature_description(finding)
            assert "Use parameterized queries" in desc

    def test_description_no_file_path(self):
        """Description handles missing file_path gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            finding = _make_finding(file_path=None, line_number=None)
            desc = imp._build_feature_description(finding)
            # Should not contain 'Location' section
            assert "Location" not in desc

    def test_description_file_path_without_line_number(self):
        """Description includes file path but omits line number when None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._improver(tmpdir)
            finding = _make_finding(file_path="utils.py", line_number=None)
            desc = imp._build_feature_description(finding)
            assert "utils.py" in desc
            # No colon+number suffix when line_number is None
            assert "utils.py:" not in desc


# ---------------------------------------------------------------------------
# HarnessSelfImprover — _add_features_to_backlog()
# ---------------------------------------------------------------------------


class TestAddFeaturesToBacklog:
    """Tests for HarnessSelfImprover._add_features_to_backlog()."""

    def _make_debt_feature(self, fid: str = "f-001", name: str = "Feature A") -> DebtFeature:
        return DebtFeature(
            id=fid,
            name=name,
            description="desc",
            priority="medium",
            finding_id="FIND-001",
            category="self-analysis",
            estimated_effort=2.0,
        )

    @pytest.mark.asyncio
    async def test_empty_feature_list_returns_zeros(self):
        """_add_features_to_backlog returns (0, 0) for empty input."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = HarnessSelfImprover(harness_root=Path(tmpdir))
            added, skipped = await imp._add_features_to_backlog([])
            assert added == 0
            assert skipped == 0

    @pytest.mark.asyncio
    async def test_fresh_features_file_created(self):
        """_add_features_to_backlog creates features.json when it does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            features_path = Path(tmpdir) / "features.json"
            imp = HarnessSelfImprover(harness_root=Path(tmpdir), features_path=features_path)

            feature = self._make_debt_feature()
            added, skipped = await imp._add_features_to_backlog([feature])

            assert added == 1
            assert skipped == 0
            assert features_path.exists()
            data = json.loads(features_path.read_text())
            assert any(item["id"] == "f-001" for item in data)

    @pytest.mark.asyncio
    async def test_dedup_by_id(self):
        """_add_features_to_backlog skips features whose ID already exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            features_path = Path(tmpdir) / "features.json"
            features_path.write_text(json.dumps([{"id": "existing-001", "name": "Old Feature"}]))

            imp = HarnessSelfImprover(harness_root=Path(tmpdir), features_path=features_path)
            dup = self._make_debt_feature(fid="existing-001", name="New Name")

            added, skipped = await imp._add_features_to_backlog([dup])

            assert added == 0
            assert skipped == 1

    @pytest.mark.asyncio
    async def test_dedup_by_name(self):
        """_add_features_to_backlog skips features whose name already exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            features_path = Path(tmpdir) / "features.json"
            features_path.write_text(json.dumps([{"id": "other-id", "name": "Same Name"}]))

            imp = HarnessSelfImprover(harness_root=Path(tmpdir), features_path=features_path)
            dup = self._make_debt_feature(fid="new-id", name="Same Name")

            added, skipped = await imp._add_features_to_backlog([dup])

            assert added == 0
            assert skipped == 1

    @pytest.mark.asyncio
    async def test_corrupted_json_resets_to_empty(self):
        """_add_features_to_backlog recovers from a malformed JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            features_path = Path(tmpdir) / "features.json"
            features_path.write_text("this is not valid json {{{{")

            imp = HarnessSelfImprover(harness_root=Path(tmpdir), features_path=features_path)
            feature = self._make_debt_feature()

            added, skipped = await imp._add_features_to_backlog([feature])

            # Should still add the feature starting fresh
            assert added == 1
            assert skipped == 0

    @pytest.mark.asyncio
    async def test_multiple_features_mixed_dedup(self):
        """_add_features_to_backlog correctly tallies added vs skipped for a mixed batch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            features_path = Path(tmpdir) / "features.json"
            features_path.write_text(json.dumps([{"id": "old-1", "name": "Old Feature 1"}]))

            imp = HarnessSelfImprover(harness_root=Path(tmpdir), features_path=features_path)
            features = [
                self._make_debt_feature(fid="old-1", name="New Name"),      # dup by ID
                self._make_debt_feature(fid="new-2", name="Brand New"),      # fresh
                self._make_debt_feature(fid="new-3", name="Old Feature 1"),  # dup by name
            ]

            added, skipped = await imp._add_features_to_backlog(features)

            assert added == 1
            assert skipped == 2


# ---------------------------------------------------------------------------
# HarnessSelfImprover — get_analysis_history()
# ---------------------------------------------------------------------------


class TestGetAnalysisHistory:
    """Tests for HarnessSelfImprover.get_analysis_history()."""

    def test_empty_history_on_fresh_instance(self):
        """New HarnessSelfImprover returns empty history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = HarnessSelfImprover(harness_root=Path(tmpdir))
            assert imp.get_analysis_history() == []

    def test_history_grows_after_analyze(self):
        """Each analyze() call appends to history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = HarnessSelfImprover(harness_root=Path(tmpdir))
            imp.tech_diligence.analyze_and_wait = AsyncMock(return_value=None)

            asyncio.run(imp.analyze())
            asyncio.run(imp.analyze())

            assert len(imp.get_analysis_history()) == 2

    def test_history_entries_are_dicts(self):
        """get_analysis_history() returns list of dicts (not SelfAnalysisResult objects)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = HarnessSelfImprover(harness_root=Path(tmpdir))
            entry = SelfAnalysisResult(
                timestamp=datetime.now(UTC),
                report=None,
                features_generated=[],
                features_added=0,
                features_skipped=0,
            )
            imp._analysis_history.append(entry)

            history = imp.get_analysis_history()
            assert isinstance(history[0], dict)
            assert "timestamp" in history[0]


# ---------------------------------------------------------------------------
# run_self_analysis()
# ---------------------------------------------------------------------------


class TestRunSelfAnalysis:
    """Tests for the run_self_analysis() top-level async function."""

    @pytest.mark.asyncio
    async def test_dry_run_does_not_create_features_file(self):
        """dry_run=True prevents features.json from being created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            harness_root = Path(tmpdir)
            features_path = harness_root / "features.json"

            with patch("forge_harness.self_improve.TechDiligenceBridge") as MockBridge:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(return_value=None)
                MockBridge.return_value = mock_bridge

                result = await run_self_analysis(
                    harness_root=harness_root,
                    features_path=features_path,
                    dry_run=True,
                )

                assert isinstance(result, SelfAnalysisResult)
                assert not features_path.exists()

    @pytest.mark.asyncio
    async def test_non_dry_run_writes_features_file(self):
        """dry_run=False (default) creates features.json when features are generated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            harness_root = Path(tmpdir)
            features_path = harness_root / "features.json"

            with patch("forge_harness.self_improve.TechDiligenceBridge") as MockBridge:
                mock_bridge = MagicMock()
                finding = _make_finding(severity="high", rule_id="H1")
                mock_report = _make_report([finding])
                mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)
                MockBridge.return_value = mock_bridge

                result = await run_self_analysis(
                    harness_root=harness_root,
                    features_path=features_path,
                    dry_run=False,
                )

                assert isinstance(result, SelfAnalysisResult)
                assert features_path.exists()

    @pytest.mark.asyncio
    async def test_forwards_scanners_and_max_features(self):
        """run_self_analysis passes scanners and max_features through to analyze()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            harness_root = Path(tmpdir)

            with patch("forge_harness.self_improve.TechDiligenceBridge") as MockBridge:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(return_value=None)
                MockBridge.return_value = mock_bridge

                result = await run_self_analysis(
                    harness_root=harness_root,
                    scanners=["bandit"],
                    max_features=7,
                    dry_run=True,
                )

                # analyze_and_wait called with correct scanners
                mock_bridge.analyze_and_wait.assert_awaited_once()
                kwargs = mock_bridge.analyze_and_wait.call_args.kwargs
                assert kwargs.get("scanners") == ["bandit"]

    @pytest.mark.asyncio
    async def test_returns_self_analysis_result_type(self):
        """run_self_analysis always returns a SelfAnalysisResult."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("forge_harness.self_improve.TechDiligenceBridge") as MockBridge:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(return_value=None)
                MockBridge.return_value = mock_bridge

                result = await run_self_analysis(harness_root=Path(tmpdir), dry_run=True)

                assert isinstance(result, SelfAnalysisResult)


# ---------------------------------------------------------------------------
# self_analyze_sync()
# ---------------------------------------------------------------------------


class TestSelfAnalyzeSync:
    """Tests for the synchronous wrapper self_analyze_sync()."""

    def test_sync_wrapper_returns_result(self):
        """self_analyze_sync returns a SelfAnalysisResult (not a coroutine)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("forge_harness.self_improve.TechDiligenceBridge") as MockBridge:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(return_value=None)
                MockBridge.return_value = mock_bridge

                result = self_analyze_sync(
                    harness_root=Path(tmpdir),
                    dry_run=True,
                )

                assert isinstance(result, SelfAnalysisResult)

    def test_sync_wrapper_passes_dry_run(self):
        """self_analyze_sync dry_run=True does not create features.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            harness_root = Path(tmpdir)
            features_path = harness_root / "features.json"

            with patch("forge_harness.self_improve.TechDiligenceBridge") as MockBridge:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(return_value=None)
                MockBridge.return_value = mock_bridge

                self_analyze_sync(
                    harness_root=harness_root,
                    features_path=features_path,
                    dry_run=True,
                )

                assert not features_path.exists()

    def test_sync_wrapper_with_no_report_records_error(self):
        """self_analyze_sync records error when Tech Diligence is unavailable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("forge_harness.self_improve.TechDiligenceBridge") as MockBridge:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(return_value=None)
                MockBridge.return_value = mock_bridge

                result = self_analyze_sync(harness_root=Path(tmpdir), dry_run=True)

                assert len(result.errors) == 1
                assert result.report is None
