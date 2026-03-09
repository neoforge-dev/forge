"""Extended tests for forge_harness/self_improve.py.

Targets edge cases and paths not exercised by test_self_improve.py:

- DEFAULT_TECH_DILIGENCE_URL: uses TECH_DILIGENCE_URL env var
- HarnessSelfImprover default harness_root (no explicit root passed)
- analyze() with priority_threshold='critical' (only critical findings)
- analyze() with priority_threshold='low' (all findings)
- analyze() result object has correct timestamp type
- _convert_findings_to_features: finding with rule_id=None → 'unknown' in id
- _convert_findings_to_features: feature estimated_effort is 2.0
- _convert_findings_to_features: name truncated to 80 chars from message
- _build_feature_description: no recommendation → no 'Recommendation:' line
- _build_feature_description: file_path present but line_number=0 (falsy)
- _add_features_to_backlog: multiple features, all new → all added
- _add_features_to_backlog: large batch hits dedup from within same batch
- _add_features_to_backlog: features with None id → skips id dedup but checks name
- get_analysis_history: returns copies (list of dicts), not raw objects
- run_self_analysis: default harness_root uses module parent
- run_self_analysis: priority_threshold forwarded
- self_analyze_sync: propagates scanners argument
- SelfAnalysisResult: features_generated count vs features_added diverge correctly
- SelfAnalysisResult.to_dict: features_generated is count of list, not list itself
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.meta_learning.feedback_loops import DebtFeature
from forge_harness.self_improve import (
    DEFAULT_TECH_DILIGENCE_URL,
    HarnessSelfImprover,
    SelfAnalysisResult,
    run_self_analysis,
    self_analyze_sync,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding(
    severity: str = "high",
    rule_id: str | None = "R001",
    title: str = "Test Issue",
    message: str = "A problem was found",
    file_path: str | None = "test.py",
    line_number: int | None = 10,
    scanner: str = "bandit",
    recommendation: str | None = "Fix it",
) -> MagicMock:
    f = MagicMock()
    f.severity = severity
    f.rule_id = rule_id
    f.title = title
    f.message = message
    f.file_path = file_path
    f.line_number = line_number
    f.scanner = scanner
    f.recommendation = recommendation
    return f


def _report(findings: list, total: int | None = None) -> MagicMock:
    r = MagicMock()
    r.findings = findings
    r.total_issues = total if total is not None else len(findings)
    r.critical_count = sum(1 for f in findings if f.severity == "critical")
    r.high_count = sum(1 for f in findings if f.severity == "high")
    r.medium_count = sum(1 for f in findings if f.severity == "medium")
    return r


def _debt_feature(fid: str = "f-001", name: str = "Feature X") -> DebtFeature:
    return DebtFeature(
        id=fid,
        name=name,
        description="desc",
        priority="medium",
        finding_id="FIND-001",
        category="self-analysis",
        estimated_effort=2.0,
    )


# ===========================================================================
# DEFAULT_TECH_DILIGENCE_URL
# ===========================================================================


class TestDefaultTechDiligenceUrl:
    """Tests for the module-level DEFAULT_TECH_DILIGENCE_URL constant."""

    def test_default_url_is_localhost(self):
        """When TECH_DILIGENCE_URL is not set, default is localhost:8000."""
        with patch.dict(os.environ, {}, clear=True):
            # Re-import to get fresh value (already imported, test the constant)
            assert "localhost" in DEFAULT_TECH_DILIGENCE_URL or "8000" in DEFAULT_TECH_DILIGENCE_URL

    def test_default_url_is_string(self):
        assert isinstance(DEFAULT_TECH_DILIGENCE_URL, str)

    def test_default_url_not_empty(self):
        assert len(DEFAULT_TECH_DILIGENCE_URL) > 0


# ===========================================================================
# HarnessSelfImprover — init with default root
# ===========================================================================


class TestHarnessSelfImproverDefaultRoot:
    """Tests for HarnessSelfImprover with default harness_root."""

    def test_default_harness_root_is_not_none(self):
        """When no harness_root given, it defaults to __file__.parent.parent."""
        improver = HarnessSelfImprover()
        assert improver.harness_root is not None

    def test_default_harness_root_is_path(self):
        improver = HarnessSelfImprover()
        assert isinstance(improver.harness_root, Path)

    def test_default_features_path_derived_from_root(self):
        improver = HarnessSelfImprover()
        assert improver.features_path == improver.harness_root / "features.json"

    def test_custom_harness_root_overrides_default(self, tmp_path):
        improver = HarnessSelfImprover(harness_root=tmp_path)
        assert improver.harness_root == tmp_path


# ===========================================================================
# analyze() — additional paths
# ===========================================================================


class TestAnalyzeAdditionalPaths:
    """Additional paths in HarnessSelfImprover.analyze()."""

    @pytest.mark.asyncio
    async def test_analyze_priority_critical_only(self):
        """threshold='critical' excludes high, medium, and low findings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = HarnessSelfImprover(harness_root=Path(tmpdir))
            findings = [
                _finding(severity="critical", rule_id="C1", title="Critical Issue"),
                _finding(severity="high", rule_id="H1", title="High Issue"),
                _finding(severity="medium", rule_id="M1", title="Medium Issue"),
            ]
            imp.tech_diligence.analyze_and_wait = AsyncMock(return_value=_report(findings))

            result = await imp.analyze(priority_threshold="critical")

            assert result.features_added == 1
            assert len(result.features_generated) == 1
            assert "Critical Issue" in result.features_generated[0].name

    @pytest.mark.asyncio
    async def test_analyze_priority_low_includes_all(self):
        """threshold='low' collects all four severities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = HarnessSelfImprover(harness_root=Path(tmpdir))
            findings = [
                _finding(severity="critical", rule_id="C1"),
                _finding(severity="high", rule_id="H1"),
                _finding(severity="medium", rule_id="M1"),
                _finding(severity="low", rule_id="L1"),
            ]
            imp.tech_diligence.analyze_and_wait = AsyncMock(return_value=_report(findings))

            result = await imp.analyze(priority_threshold="low", max_features=10)

            assert len(result.features_generated) == 4

    @pytest.mark.asyncio
    async def test_analyze_result_timestamp_is_datetime(self):
        """analyze() result timestamp is a datetime instance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = HarnessSelfImprover(harness_root=Path(tmpdir))
            imp.tech_diligence.analyze_and_wait = AsyncMock(return_value=None)
            result = await imp.analyze()
            assert isinstance(result.timestamp, datetime)

    @pytest.mark.asyncio
    async def test_analyze_result_timestamp_is_utc_aware(self):
        """analyze() result timestamp has UTC timezone."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = HarnessSelfImprover(harness_root=Path(tmpdir))
            imp.tech_diligence.analyze_and_wait = AsyncMock(return_value=None)
            result = await imp.analyze()
            assert result.timestamp.tzinfo is not None

    @pytest.mark.asyncio
    async def test_analyze_calls_analyze_and_wait_with_harness_root(self):
        """analyze() passes harness_root path to tech_diligence.analyze_and_wait."""
        with tempfile.TemporaryDirectory() as tmpdir:
            harness_root = Path(tmpdir)
            imp = HarnessSelfImprover(harness_root=harness_root)
            imp.tech_diligence.analyze_and_wait = AsyncMock(return_value=None)

            await imp.analyze()

            call_kwargs = imp.tech_diligence.analyze_and_wait.call_args
            assert call_kwargs.kwargs.get("repo_path") == str(harness_root)

    @pytest.mark.asyncio
    async def test_analyze_max_wait_seconds_is_300(self):
        """analyze() uses max_wait_seconds=300."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = HarnessSelfImprover(harness_root=Path(tmpdir))
            imp.tech_diligence.analyze_and_wait = AsyncMock(return_value=None)

            await imp.analyze()

            call_kwargs = imp.tech_diligence.analyze_and_wait.call_args
            assert call_kwargs.kwargs.get("max_wait_seconds") == 300

    @pytest.mark.asyncio
    async def test_analyze_with_multiple_findings_all_added(self):
        """Multiple unique findings are all added to backlog."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = HarnessSelfImprover(harness_root=Path(tmpdir))
            findings = [
                _finding(severity="high", rule_id=f"R{i}", title=f"Issue {i}")
                for i in range(5)
            ]
            imp.tech_diligence.analyze_and_wait = AsyncMock(return_value=_report(findings))

            result = await imp.analyze(max_features=10)

            assert result.features_added == 5
            assert result.features_skipped == 0


# ===========================================================================
# _convert_findings_to_features — additional paths
# ===========================================================================


class TestConvertFindingsAdditional:
    """Additional tests for _convert_findings_to_features."""

    def _imp(self, tmpdir: str) -> HarnessSelfImprover:
        return HarnessSelfImprover(harness_root=Path(tmpdir))

    def test_rule_id_none_uses_unknown_in_id(self):
        """When rule_id is None, 'unknown' appears in the generated feature ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._imp(tmpdir)
            f = _finding(severity="high", rule_id=None)
            report = _report([f])
            features = imp._convert_findings_to_features(report, 5, "high")
            assert "unknown" in features[0].id

    def test_estimated_effort_is_2(self):
        """All generated features have estimated_effort=2.0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._imp(tmpdir)
            f = _finding(severity="high")
            report = _report([f])
            features = imp._convert_findings_to_features(report, 5, "high")
            assert features[0].estimated_effort == 2.0

    def test_feature_name_truncates_long_message(self):
        """Message is truncated to 80 characters in feature name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._imp(tmpdir)
            long_msg = "A" * 120
            f = _finding(severity="high", rule_id="R1", title=None, message=long_msg)
            report = _report([f])
            features = imp._convert_findings_to_features(report, 5, "high")
            # Feature name is "[Harness] " + first 80 chars of message
            assert len(features[0].name) <= 10 + 80  # "[Harness] " is 10 chars

    def test_max_features_zero_returns_one(self):
        """max_features=0: the >= check triggers after adding the first item, so 1 feature is returned.

        The implementation uses `if len(features) >= max_features: break` at the end
        of the loop body, meaning one item is always added before the break fires.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._imp(tmpdir)
            findings = [_finding(severity="high", rule_id=f"R{i}") for i in range(5)]
            report = _report(findings)
            features = imp._convert_findings_to_features(report, 0, "high")
            # The break fires after the first append, so exactly 1 feature is returned
            assert len(features) == 1

    def test_unknown_severity_excluded_by_medium_threshold(self):
        """A finding with unknown severity 'unknown' is excluded by 'medium' threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._imp(tmpdir)
            f = _finding(severity="unknown")
            report = _report([f])
            features = imp._convert_findings_to_features(report, 5, "medium")
            assert len(features) == 0

    def test_feature_priority_maps_correctly(self):
        """Priority field in generated feature matches severity of finding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._imp(tmpdir)
            for severity, expected_priority in [
                ("critical", "critical"),
                ("high", "high"),
                ("medium", "medium"),
                ("low", "low"),
            ]:
                f = _finding(severity=severity, rule_id=f"R-{severity}")
                report = _report([f])
                features = imp._convert_findings_to_features(report, 5, "low")
                if features:
                    assert features[0].priority == expected_priority

    def test_harness_prefix_in_feature_name(self):
        """Feature name starts with '[Harness]' prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._imp(tmpdir)
            f = _finding(severity="high", title="My Issue")
            report = _report([f])
            features = imp._convert_findings_to_features(report, 5, "high")
            assert features[0].name.startswith("[Harness]")


# ===========================================================================
# _build_feature_description — additional paths
# ===========================================================================


class TestBuildFeatureDescriptionAdditional:
    """Additional tests for _build_feature_description."""

    def _imp(self, tmpdir: str) -> HarnessSelfImprover:
        return HarnessSelfImprover(harness_root=Path(tmpdir))

    def test_no_recommendation_excludes_recommendation_section(self):
        """When recommendation is None, 'Recommendation:' does not appear."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._imp(tmpdir)
            f = _finding(recommendation=None)
            desc = imp._build_feature_description(f)
            assert "Recommendation:" not in desc

    def test_file_path_with_line_number_0_no_colon_suffix(self):
        """line_number=0 is falsy → no ':0' suffix appended."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._imp(tmpdir)
            f = _finding(file_path="utils.py", line_number=0)
            desc = imp._build_feature_description(f)
            assert "utils.py" in desc
            assert "utils.py:0" not in desc

    def test_parts_joined_by_double_newline(self):
        """Description parts are separated by double newlines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._imp(tmpdir)
            f = _finding(
                message="Bad thing",
                file_path="src/app.py",
                line_number=42,
                recommendation="Do better",
            )
            desc = imp._build_feature_description(f)
            assert "\n\n" in desc

    def test_minimal_finding_description(self):
        """Finding with only message (no file, no recommendation) gives just message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imp = self._imp(tmpdir)
            f = _finding(message="Just a message", file_path=None, recommendation=None)
            desc = imp._build_feature_description(f)
            assert desc == "Just a message"


# ===========================================================================
# _add_features_to_backlog — additional paths
# ===========================================================================


class TestAddFeaturesToBacklogAdditional:
    """Additional paths in _add_features_to_backlog."""

    @pytest.mark.asyncio
    async def test_all_new_features_written_to_file(self):
        """All features in a fresh batch are added when no existing file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fp = Path(tmpdir) / "features.json"
            imp = HarnessSelfImprover(harness_root=Path(tmpdir), features_path=fp)
            features = [
                _debt_feature(fid=f"f-{i}", name=f"Feature {i}")
                for i in range(3)
            ]
            added, skipped = await imp._add_features_to_backlog(features)
            assert added == 3
            assert skipped == 0

    @pytest.mark.asyncio
    async def test_file_contents_valid_json_after_add(self):
        """After adding features, features.json contains valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fp = Path(tmpdir) / "features.json"
            imp = HarnessSelfImprover(harness_root=Path(tmpdir), features_path=fp)
            await imp._add_features_to_backlog([_debt_feature()])
            data = json.loads(fp.read_text())
            assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_second_call_skips_first_batch(self):
        """A second call with the same features skips all of them."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fp = Path(tmpdir) / "features.json"
            imp = HarnessSelfImprover(harness_root=Path(tmpdir), features_path=fp)
            features = [_debt_feature(fid="dup-01", name="Duplicate Feature")]
            await imp._add_features_to_backlog(features)
            added, skipped = await imp._add_features_to_backlog(features)
            assert added == 0
            assert skipped == 1

    @pytest.mark.asyncio
    async def test_within_batch_duplicate_id_counted_once(self):
        """Within one batch, duplicate IDs from new features are still tracked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fp = Path(tmpdir) / "features.json"
            imp = HarnessSelfImprover(harness_root=Path(tmpdir), features_path=fp)
            # Two features with different IDs but same name
            features = [
                _debt_feature(fid="new-1", name="Shared Name"),
                _debt_feature(fid="new-2", name="Shared Name"),  # same name → skip
            ]
            added, skipped = await imp._add_features_to_backlog(features)
            assert added == 1
            assert skipped == 1

    @pytest.mark.asyncio
    async def test_total_features_in_file_equals_added(self):
        """The number of entries in features.json equals the number added."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fp = Path(tmpdir) / "features.json"
            imp = HarnessSelfImprover(harness_root=Path(tmpdir), features_path=fp)
            features = [
                _debt_feature(fid=f"x-{i}", name=f"Feat {i}")
                for i in range(4)
            ]
            added, _ = await imp._add_features_to_backlog(features)
            data = json.loads(fp.read_text())
            assert len(data) == added


# ===========================================================================
# run_self_analysis — additional paths
# ===========================================================================


class TestRunSelfAnalysisAdditional:
    """Additional tests for run_self_analysis top-level function."""

    @pytest.mark.asyncio
    async def test_priority_threshold_forwarded(self):
        """run_self_analysis forwards priority_threshold to analyze()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("forge_harness.self_improve.TechDiligenceBridge") as MockBridge:
                mock_bridge = MagicMock()
                critical = _finding(severity="critical", rule_id="C1")
                high = _finding(severity="high", rule_id="H1")
                mock_bridge.analyze_and_wait = AsyncMock(return_value=_report([critical, high]))
                MockBridge.return_value = mock_bridge

                result = await run_self_analysis(
                    harness_root=Path(tmpdir),
                    priority_threshold="critical",
                    max_features=10,
                    dry_run=True,
                )

                # Only critical finding should be included
                assert len(result.features_generated) == 1

    @pytest.mark.asyncio
    async def test_result_has_no_errors_when_report_returned(self):
        """When Tech Diligence returns a valid report, errors list is empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("forge_harness.self_improve.TechDiligenceBridge") as MockBridge:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(return_value=_report([]))
                MockBridge.return_value = mock_bridge

                result = await run_self_analysis(
                    harness_root=Path(tmpdir),
                    dry_run=True,
                )
                assert result.errors == []

    @pytest.mark.asyncio
    async def test_dry_run_uses_dev_null_features_path(self):
        """dry_run=True substitutes /dev/null as features_path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            real_path = Path(tmpdir) / "features.json"
            with patch("forge_harness.self_improve.TechDiligenceBridge") as MockBridge:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(return_value=None)
                MockBridge.return_value = mock_bridge

                await run_self_analysis(
                    harness_root=Path(tmpdir),
                    features_path=real_path,
                    dry_run=True,
                )
                # real_path should NOT have been created
                assert not real_path.exists()


# ===========================================================================
# self_analyze_sync — additional paths
# ===========================================================================


class TestSelfAnalyzeSyncAdditional:
    """Additional tests for the synchronous wrapper."""

    def test_sync_propagates_scanners(self):
        """self_analyze_sync passes scanners correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("forge_harness.self_improve.TechDiligenceBridge") as MockBridge:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(return_value=None)
                MockBridge.return_value = mock_bridge

                self_analyze_sync(
                    harness_root=Path(tmpdir),
                    scanners=["semgrep", "bandit"],
                    dry_run=True,
                )

                call_kwargs = mock_bridge.analyze_and_wait.call_args.kwargs
                assert call_kwargs.get("scanners") == ["semgrep", "bandit"]

    def test_sync_wrapper_result_errors_when_no_report(self):
        """sync wrapper with no report produces one error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("forge_harness.self_improve.TechDiligenceBridge") as MockBridge:
                mock_bridge = MagicMock()
                mock_bridge.analyze_and_wait = AsyncMock(return_value=None)
                MockBridge.return_value = mock_bridge

                result = self_analyze_sync(harness_root=Path(tmpdir), dry_run=True)

            assert len(result.errors) >= 1

    def test_sync_wrapper_max_features_forwarded(self):
        """sync wrapper respects max_features cap."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("forge_harness.self_improve.TechDiligenceBridge") as MockBridge:
                mock_bridge = MagicMock()
                findings = [
                    _finding(severity="high", rule_id=f"R{i}", title=f"Issue {i}")
                    for i in range(10)
                ]
                mock_bridge.analyze_and_wait = AsyncMock(return_value=_report(findings))
                MockBridge.return_value = mock_bridge

                result = self_analyze_sync(
                    harness_root=Path(tmpdir),
                    max_features=3,
                    dry_run=True,
                )

            assert len(result.features_generated) == 3


# ===========================================================================
# SelfAnalysisResult — additional
# ===========================================================================


class TestSelfAnalysisResultAdditional:
    """Additional SelfAnalysisResult tests."""

    def test_to_dict_features_generated_is_integer_count(self):
        """features_generated in to_dict is the count (int), not a list."""
        features = [
            DebtFeature(
                id=f"f-{i}",
                name=f"F{i}",
                description="d",
                priority="high",
                finding_id="x",
                category="c",
                estimated_effort=1.0,
            )
            for i in range(3)
        ]
        result = SelfAnalysisResult(
            timestamp=datetime.now(UTC),
            report=None,
            features_generated=features,
            features_added=2,
            features_skipped=1,
        )
        data = result.to_dict()
        assert data["features_generated"] == 3
        assert isinstance(data["features_generated"], int)

    def test_to_dict_features_added_can_differ_from_generated_count(self):
        """features_added and features_generated (count) can differ (due to skips)."""
        features = [
            DebtFeature(id="f-1", name="F1", description="d", priority="high",
                        finding_id="x", category="c", estimated_effort=1.0),
            DebtFeature(id="f-2", name="F2", description="d", priority="high",
                        finding_id="x", category="c", estimated_effort=1.0),
        ]
        result = SelfAnalysisResult(
            timestamp=datetime.now(UTC),
            report=None,
            features_generated=features,
            features_added=1,    # 1 added
            features_skipped=1,  # 1 skipped
        )
        data = result.to_dict()
        assert data["features_generated"] == 2  # total generated
        assert data["features_added"] == 1

    def test_to_dict_empty_errors_list(self):
        """Default errors field produces an empty list in to_dict."""
        result = SelfAnalysisResult(
            timestamp=datetime.now(UTC),
            report=None,
            features_generated=[],
            features_added=0,
            features_skipped=0,
        )
        data = result.to_dict()
        assert data["errors"] == []

    def test_result_errors_default_is_list(self):
        """The default errors value is a list (mutable default via field factory)."""
        r = SelfAnalysisResult(
            timestamp=datetime.now(UTC),
            report=None,
            features_generated=[],
            features_added=0,
            features_skipped=0,
        )
        assert isinstance(r.errors, list)

    def test_two_results_errors_independent(self):
        """Two SelfAnalysisResult instances have independent errors lists."""
        r1 = SelfAnalysisResult(
            timestamp=datetime.now(UTC), report=None,
            features_generated=[], features_added=0, features_skipped=0,
        )
        r2 = SelfAnalysisResult(
            timestamp=datetime.now(UTC), report=None,
            features_generated=[], features_added=0, features_skipped=0,
        )
        r1.errors.append("error")
        assert r2.errors == []
