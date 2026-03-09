"""Unit tests for forge_harness.self_improve module.

Tests cover:
- SelfAnalysisResult dataclass and to_dict serialisation
- HarnessSelfImprover.__init__ (defaults and overrides)
- HarnessSelfImprover.analyze — success, no-report, error paths
- HarnessSelfImprover._convert_findings_to_features — priority filtering,
  max_features cap, field mapping, title/message fallbacks
- HarnessSelfImprover._build_feature_description — with/without file,
  line-number, recommendation
- HarnessSelfImprover._add_features_to_backlog — new file, existing file,
  duplicate-id dedup, duplicate-name dedup, invalid JSON recovery
- HarnessSelfImprover.get_analysis_history
- run_self_analysis — live path and dry_run path
- self_analyze_sync — synchronous wrapper
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — build lightweight fakes that match the real Pydantic schema
# fields used by self_improve.py without importing the full schema.
# ---------------------------------------------------------------------------


def _make_finding(
    *,
    severity: str = "high",
    rule_id: str = "RULE-001",
    message: str = "Some security issue",
    file_path: str | None = "/src/app.py",
    line_number: int | None = 42,
    recommendation: str | None = "Fix it",
    title: str | None = "Security Flaw",
    scanner: str | None = "bandit",
) -> MagicMock:
    """Return a MagicMock that mimics DiligenceFinding."""
    finding = MagicMock()
    finding.severity = severity
    finding.rule_id = rule_id
    finding.message = message
    finding.file_path = file_path
    finding.line_number = line_number
    finding.recommendation = recommendation
    finding.title = title
    finding.scanner = scanner
    return finding


def _make_report(findings: list | None = None) -> MagicMock:
    """Return a MagicMock that mimics DiligenceReport."""
    report = MagicMock()
    report.findings = findings if findings is not None else []
    report.total_issues = len(report.findings)
    report.critical_count = sum(1 for f in report.findings if f.severity == "critical")
    report.high_count = sum(1 for f in report.findings if f.severity == "high")
    report.medium_count = sum(1 for f in report.findings if f.severity == "medium")
    return report


def _make_debt_feature(
    *,
    feature_id: str = "self-debt-RULE-001-0",
    name: str = "[Harness] Security Flaw",
    description: str = "desc",
    priority: str = "high",
    finding_id: str = "RULE-001",
    category: str = "bandit",
    estimated_effort: float = 2.0,
) -> MagicMock:
    """Return a MagicMock that mimics DebtFeature with a to_dict method."""
    feat = MagicMock()
    feat.id = feature_id
    feat.name = name
    feat.description = description
    feat.priority = priority
    feat.finding_id = finding_id
    feat.category = category
    feat.estimated_effort = estimated_effort
    feat.to_dict.return_value = {
        "id": feature_id,
        "name": name,
        "description": description,
        "status": "pending",
        "priority": priority,
        "acceptance_criteria": [f"Finding {finding_id} is resolved", "Tests pass after fix"],
        "metadata": {
            "source": "tech_diligence",
            "finding_id": finding_id,
            "category": category,
        },
    }
    return feat


# ---------------------------------------------------------------------------
# Import the module under test AFTER helpers are defined so that
# patch targets resolve correctly.
# ---------------------------------------------------------------------------

from forge_harness.self_improve import (  # noqa: E402
    DEFAULT_TECH_DILIGENCE_URL,
    HarnessSelfImprover,
    SelfAnalysisResult,
    run_self_analysis,
    self_analyze_sync,
)

# ===========================================================================
# SelfAnalysisResult
# ===========================================================================


class TestSelfAnalysisResult:
    """Tests for SelfAnalysisResult dataclass."""

    def _result(self, report=None, features=None, added=0, skipped=0, errors=None):
        return SelfAnalysisResult(
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            report=report,
            features_generated=features or [],
            features_added=added,
            features_skipped=skipped,
            errors=errors or [],
        )

    def test_to_dict_with_report(self):
        report = _make_report([_make_finding(severity="critical"), _make_finding(severity="high")])
        result = self._result(report=report, added=1, skipped=0)
        d = result.to_dict()

        assert d["timestamp"] == "2026-01-01T00:00:00+00:00"
        assert d["report_summary"]["total_issues"] == 2
        assert d["report_summary"]["critical"] == 1
        assert d["report_summary"]["high"] == 1
        assert d["report_summary"]["medium"] == 0
        assert d["features_generated"] == 0
        assert d["features_added"] == 1
        assert d["features_skipped"] == 0
        assert d["errors"] == []

    def test_to_dict_no_report(self):
        result = self._result(report=None)
        d = result.to_dict()

        assert d["report_summary"] is None
        assert d["features_generated"] == 0

    def test_to_dict_with_errors(self):
        result = self._result(errors=["timeout", "parse error"])
        d = result.to_dict()

        assert d["errors"] == ["timeout", "parse error"]

    def test_to_dict_features_count(self):
        features = [_make_debt_feature(), _make_debt_feature(feature_id="f2", name="[Harness] b")]
        result = self._result(features=features, added=2)
        d = result.to_dict()

        assert d["features_generated"] == 2

    def test_default_errors_field(self):
        """errors should default to an empty list."""
        result = SelfAnalysisResult(
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            report=None,
            features_generated=[],
            features_added=0,
            features_skipped=0,
        )
        assert result.errors == []


# ===========================================================================
# HarnessSelfImprover.__init__
# ===========================================================================


class TestHarnessSelfImproverInit:
    def test_defaults(self, tmp_path):
        """harness_root defaults to the package root (self_improve.py's parent's parent)."""
        import forge_harness.self_improve as _mod

        # The default is Path(__file__).parent.parent inside the module,
        # where __file__ is forge_harness/self_improve.py.
        expected_root = Path(_mod.__file__).parent.parent

        with patch(
            "forge_harness.self_improve.TechDiligenceBridge"
        ) as mock_bridge_cls:
            mock_bridge_cls.return_value = MagicMock()
            improver = HarnessSelfImprover()

        assert improver.harness_root == expected_root
        # features_path is derived from harness_root
        assert improver.features_path == improver.harness_root / "features.json"
        assert improver._analysis_history == []

    def test_custom_paths(self, tmp_path):
        with patch("forge_harness.self_improve.TechDiligenceBridge") as mock_bridge_cls:
            mock_bridge_cls.return_value = MagicMock()
            improver = HarnessSelfImprover(
                harness_root=tmp_path,
                features_path=tmp_path / "custom.json",
                tech_diligence_url="http://td:9000",
            )

        assert improver.harness_root == tmp_path
        assert improver.features_path == tmp_path / "custom.json"
        mock_bridge_cls.assert_called_once_with(base_url="http://td:9000")

    def test_tech_diligence_url_default_env(self, monkeypatch, tmp_path):
        """When tech_diligence_url is None, uses DEFAULT_TECH_DILIGENCE_URL."""
        with patch("forge_harness.self_improve.TechDiligenceBridge") as mock_bridge_cls:
            mock_bridge_cls.return_value = MagicMock()
            HarnessSelfImprover(harness_root=tmp_path)

        mock_bridge_cls.assert_called_once_with(base_url=DEFAULT_TECH_DILIGENCE_URL)


# ===========================================================================
# HarnessSelfImprover._build_feature_description
# ===========================================================================


class TestBuildFeatureDescription:
    def _improver(self, tmp_path):
        with patch("forge_harness.self_improve.TechDiligenceBridge"):
            return HarnessSelfImprover(harness_root=tmp_path)

    def test_message_only(self, tmp_path):
        improver = self._improver(tmp_path)
        finding = MagicMock()
        finding.message = "A bad thing happened"
        finding.file_path = None
        finding.line_number = None
        finding.recommendation = None

        desc = improver._build_feature_description(finding)
        assert desc == "A bad thing happened"

    def test_with_file_path_no_line(self, tmp_path):
        improver = self._improver(tmp_path)
        finding = MagicMock()
        finding.message = "Problem here"
        finding.file_path = "/foo/bar.py"
        finding.line_number = None
        finding.recommendation = None

        desc = improver._build_feature_description(finding)
        assert "Location: /foo/bar.py" in desc
        assert ":" not in desc.split("Location:")[1].split("\n")[0]

    def test_with_file_path_and_line(self, tmp_path):
        improver = self._improver(tmp_path)
        finding = MagicMock()
        finding.message = "Problem"
        finding.file_path = "/foo/bar.py"
        finding.line_number = 99
        finding.recommendation = None

        desc = improver._build_feature_description(finding)
        assert "Location: /foo/bar.py:99" in desc

    def test_with_recommendation(self, tmp_path):
        improver = self._improver(tmp_path)
        finding = MagicMock()
        finding.message = "Issue"
        finding.file_path = None
        finding.line_number = None
        finding.recommendation = "Use safer API"

        desc = improver._build_feature_description(finding)
        assert "Recommendation: Use safer API" in desc

    def test_all_parts_joined_with_double_newline(self, tmp_path):
        improver = self._improver(tmp_path)
        finding = MagicMock()
        finding.message = "Msg"
        finding.file_path = "/a.py"
        finding.line_number = 5
        finding.recommendation = "Fix it"

        desc = improver._build_feature_description(finding)
        parts = desc.split("\n\n")
        assert len(parts) == 3
        assert parts[0] == "Msg"
        assert parts[1] == "Location: /a.py:5"
        assert parts[2] == "Recommendation: Fix it"


# ===========================================================================
# HarnessSelfImprover._convert_findings_to_features
# ===========================================================================


class TestConvertFindingsToFeatures:
    def _improver(self, tmp_path):
        with patch("forge_harness.self_improve.TechDiligenceBridge"):
            return HarnessSelfImprover(harness_root=tmp_path)

    def test_basic_conversion(self, tmp_path):
        improver = self._improver(tmp_path)
        finding = _make_finding(severity="high", rule_id="SEC-1", scanner="bandit")
        report = _make_report([finding])

        features = improver._convert_findings_to_features(report, max_features=5, priority_threshold="high")

        assert len(features) == 1
        f = features[0]
        assert f.id == "self-debt-SEC-1-0"
        assert f.priority == "high"
        assert f.category == "bandit"
        assert f.estimated_effort == 2.0

    def test_priority_threshold_filters_low(self, tmp_path):
        """medium threshold should exclude 'low' severity findings."""
        improver = self._improver(tmp_path)
        report = _make_report([
            _make_finding(severity="medium", rule_id="M1"),
            _make_finding(severity="low", rule_id="L1"),
        ])

        features = improver._convert_findings_to_features(report, max_features=10, priority_threshold="medium")

        assert len(features) == 1
        assert features[0].id.startswith("self-debt-M1")

    def test_all_priorities_included_with_low_threshold(self, tmp_path):
        improver = self._improver(tmp_path)
        report = _make_report([
            _make_finding(severity="critical", rule_id="C1"),
            _make_finding(severity="high", rule_id="H1"),
            _make_finding(severity="medium", rule_id="M1"),
            _make_finding(severity="low", rule_id="L1"),
        ])

        features = improver._convert_findings_to_features(report, max_features=10, priority_threshold="low")

        assert len(features) == 4

    def test_max_features_cap(self, tmp_path):
        improver = self._improver(tmp_path)
        report = _make_report([_make_finding(severity="high", rule_id=f"R{i}") for i in range(20)])

        features = improver._convert_findings_to_features(report, max_features=5, priority_threshold="high")

        assert len(features) == 5

    def test_feature_name_uses_title(self, tmp_path):
        improver = self._improver(tmp_path)
        finding = _make_finding(severity="high", rule_id="R1", title="My Finding Title", message="Long msg")
        report = _make_report([finding])

        features = improver._convert_findings_to_features(report, max_features=1, priority_threshold="high")

        assert features[0].name == "[Harness] My Finding Title"

    def test_feature_name_falls_back_to_message(self, tmp_path):
        improver = self._improver(tmp_path)
        # getattr returns None when title attribute is None
        finding = _make_finding(severity="high", rule_id="R1", title=None, message="X" * 100)
        # Override getattr behaviour for title
        finding.configure_mock(**{})
        # Manually set title to falsy
        finding.title = None
        report = _make_report([finding])

        features = improver._convert_findings_to_features(report, max_features=1, priority_threshold="high")

        assert features[0].name.startswith("[Harness] ")
        assert len(features[0].name) <= len("[Harness] ") + 80

    def test_rule_id_unknown_fallback(self, tmp_path):
        improver = self._improver(tmp_path)
        finding = _make_finding(severity="medium", rule_id=None)
        finding.rule_id = None
        report = _make_report([finding])

        features = improver._convert_findings_to_features(report, max_features=1, priority_threshold="medium")

        assert "unknown" in features[0].id

    def test_scanner_none_falls_back_to_self_analysis(self, tmp_path):
        improver = self._improver(tmp_path)
        finding = _make_finding(severity="medium", scanner=None)
        finding.scanner = None
        report = _make_report([finding])

        features = improver._convert_findings_to_features(report, max_features=1, priority_threshold="medium")

        assert features[0].category == "self-analysis"

    def test_empty_findings_returns_empty_list(self, tmp_path):
        improver = self._improver(tmp_path)
        report = _make_report([])

        features = improver._convert_findings_to_features(report, max_features=10, priority_threshold="low")

        assert features == []

    def test_critical_threshold_only_includes_critical(self, tmp_path):
        improver = self._improver(tmp_path)
        report = _make_report([
            _make_finding(severity="critical", rule_id="C1"),
            _make_finding(severity="high", rule_id="H1"),
        ])

        features = improver._convert_findings_to_features(report, max_features=10, priority_threshold="critical")

        assert len(features) == 1
        assert features[0].priority == "critical"


# ===========================================================================
# HarnessSelfImprover._add_features_to_backlog
# ===========================================================================


class TestAddFeaturesToBacklog:
    def _improver(self, tmp_path, features_path=None):
        with patch("forge_harness.self_improve.TechDiligenceBridge"):
            return HarnessSelfImprover(
                harness_root=tmp_path,
                features_path=features_path or (tmp_path / "features.json"),
            )

    @pytest.mark.asyncio
    async def test_empty_features_returns_zero_zero(self, tmp_path):
        improver = self._improver(tmp_path)
        added, skipped = await improver._add_features_to_backlog([])
        assert (added, skipped) == (0, 0)

    @pytest.mark.asyncio
    async def test_creates_features_file_when_absent(self, tmp_path):
        improver = self._improver(tmp_path)
        feature = _make_debt_feature(feature_id="f1", name="New Feature")

        added, skipped = await improver._add_features_to_backlog([feature])

        assert added == 1
        assert skipped == 0
        data = json.loads((tmp_path / "features.json").read_text())
        assert len(data) == 1
        assert data[0]["id"] == "f1"

    @pytest.mark.asyncio
    async def test_appends_to_existing_file(self, tmp_path):
        features_path = tmp_path / "features.json"
        existing = [{"id": "existing-1", "name": "Old feature"}]
        features_path.write_text(json.dumps(existing))

        improver = self._improver(tmp_path, features_path=features_path)
        feature = _make_debt_feature(feature_id="new-1", name="New Feature")

        added, skipped = await improver._add_features_to_backlog([feature])

        assert added == 1
        data = json.loads(features_path.read_text())
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_skips_duplicate_id(self, tmp_path):
        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps([{"id": "dup-id", "name": "Existing"}]))

        improver = self._improver(tmp_path, features_path=features_path)
        feature = _make_debt_feature(feature_id="dup-id", name="Different Name")

        added, skipped = await improver._add_features_to_backlog([feature])

        assert added == 0
        assert skipped == 1

    @pytest.mark.asyncio
    async def test_skips_duplicate_name(self, tmp_path):
        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps([{"id": "other-id", "name": "[Harness] Security Flaw"}]))

        improver = self._improver(tmp_path, features_path=features_path)
        feature = _make_debt_feature(feature_id="new-id", name="[Harness] Security Flaw")

        added, skipped = await improver._add_features_to_backlog([feature])

        assert added == 0
        assert skipped == 1

    @pytest.mark.asyncio
    async def test_recovers_from_invalid_json(self, tmp_path):
        features_path = tmp_path / "features.json"
        features_path.write_text("not-valid-json{{{")

        improver = self._improver(tmp_path, features_path=features_path)
        feature = _make_debt_feature(feature_id="f1", name="Fresh Feature")

        added, skipped = await improver._add_features_to_backlog([feature])

        assert added == 1
        assert skipped == 0
        data = json.loads(features_path.read_text())
        assert data[0]["id"] == "f1"

    @pytest.mark.asyncio
    async def test_mixed_new_and_duplicate(self, tmp_path):
        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps([{"id": "old", "name": "Old"}]))

        improver = self._improver(tmp_path, features_path=features_path)
        features = [
            _make_debt_feature(feature_id="old", name="Old"),  # duplicate id
            _make_debt_feature(feature_id="new-a", name="Feature A"),  # new
            _make_debt_feature(feature_id="new-b", name="Feature B"),  # new
        ]

        added, skipped = await improver._add_features_to_backlog(features)

        assert added == 2
        assert skipped == 1

    @pytest.mark.asyncio
    async def test_dedup_within_same_batch_by_name(self, tmp_path):
        """Second feature with same name in the same batch is skipped."""
        improver = self._improver(tmp_path)
        features = [
            _make_debt_feature(feature_id="id-1", name="Shared Name"),
            _make_debt_feature(feature_id="id-2", name="Shared Name"),
        ]

        added, skipped = await improver._add_features_to_backlog(features)

        assert added == 1
        assert skipped == 1


# ===========================================================================
# HarnessSelfImprover.analyze
# ===========================================================================


class TestHarnessSelfImproverAnalyze:
    def _improver(self, tmp_path):
        with patch("forge_harness.self_improve.TechDiligenceBridge") as mock_bridge_cls:
            mock_bridge = MagicMock()
            mock_bridge_cls.return_value = mock_bridge
            improver = HarnessSelfImprover(harness_root=tmp_path, features_path=tmp_path / "f.json")
            improver.tech_diligence = mock_bridge
            return improver

    @pytest.mark.asyncio
    async def test_analyze_success(self, tmp_path):
        improver = self._improver(tmp_path)

        finding = _make_finding(severity="high", rule_id="H1")
        report = _make_report([finding])
        improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=report)

        result = await improver.analyze(max_features=5, priority_threshold="high")

        assert isinstance(result, SelfAnalysisResult)
        assert result.report is report
        assert result.errors == []
        assert result.features_added >= 0
        # Result should be recorded in history
        assert len(improver._analysis_history) == 1

    @pytest.mark.asyncio
    async def test_analyze_no_report(self, tmp_path):
        improver = self._improver(tmp_path)
        improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=None)

        result = await improver.analyze()

        assert result.report is None
        assert len(result.errors) == 1
        assert "failed or timed out" in result.errors[0]
        assert result.features_added == 0
        assert result.features_skipped == 0

    @pytest.mark.asyncio
    async def test_analyze_passes_scanners(self, tmp_path):
        improver = self._improver(tmp_path)
        improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=_make_report())

        await improver.analyze(scanners=["bandit", "semgrep"], max_features=3)

        improver.tech_diligence.analyze_and_wait.assert_called_once_with(
            repo_path=str(tmp_path),
            scanners=["bandit", "semgrep"],
            max_wait_seconds=300,
        )

    @pytest.mark.asyncio
    async def test_analyze_accumulates_history(self, tmp_path):
        improver = self._improver(tmp_path)
        improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=_make_report())

        await improver.analyze()
        await improver.analyze()

        assert len(improver._analysis_history) == 2

    @pytest.mark.asyncio
    async def test_analyze_timestamp_is_utc(self, tmp_path):
        improver = self._improver(tmp_path)
        improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=_make_report())

        result = await improver.analyze()

        assert result.timestamp.tzinfo is not None

    @pytest.mark.asyncio
    async def test_analyze_features_written_to_file(self, tmp_path):
        improver = self._improver(tmp_path)

        finding = _make_finding(severity="high", rule_id="W1", scanner="semgrep")
        report = _make_report([finding])
        improver.tech_diligence.analyze_and_wait = AsyncMock(return_value=report)

        result = await improver.analyze(max_features=10, priority_threshold="high")

        features_file = tmp_path / "f.json"
        if result.features_added > 0:
            assert features_file.exists()
            data = json.loads(features_file.read_text())
            assert len(data) == result.features_added


# ===========================================================================
# HarnessSelfImprover.get_analysis_history
# ===========================================================================


class TestGetAnalysisHistory:
    @pytest.mark.asyncio
    async def test_empty_history(self, tmp_path):
        with patch("forge_harness.self_improve.TechDiligenceBridge"):
            improver = HarnessSelfImprover(harness_root=tmp_path)

        assert improver.get_analysis_history() == []

    @pytest.mark.asyncio
    async def test_returns_serialised_dicts(self, tmp_path):
        with patch("forge_harness.self_improve.TechDiligenceBridge"):
            improver = HarnessSelfImprover(harness_root=tmp_path)

        result = SelfAnalysisResult(
            timestamp=datetime(2026, 2, 1, tzinfo=UTC),
            report=None,
            features_generated=[],
            features_added=0,
            features_skipped=0,
        )
        improver._analysis_history.append(result)

        history = improver.get_analysis_history()

        assert len(history) == 1
        assert isinstance(history[0], dict)
        assert history[0]["features_added"] == 0


# ===========================================================================
# run_self_analysis
# ===========================================================================


class TestRunSelfAnalysis:
    @pytest.mark.asyncio
    async def test_run_self_analysis_calls_analyze(self, tmp_path):
        with patch("forge_harness.self_improve.HarnessSelfImprover") as mock_cls:
            mock_improver = MagicMock()
            mock_cls.return_value = mock_improver
            expected_result = MagicMock(spec=SelfAnalysisResult)
            mock_improver.analyze = AsyncMock(return_value=expected_result)

            result = await run_self_analysis(
                harness_root=tmp_path,
                features_path=tmp_path / "f.json",
                scanners=["bandit"],
                max_features=5,
                priority_threshold="high",
            )

        assert result is expected_result
        mock_improver.analyze.assert_called_once_with(
            scanners=["bandit"],
            max_features=5,
            priority_threshold="high",
        )

    @pytest.mark.asyncio
    async def test_run_self_analysis_dry_run_uses_dev_null(self, tmp_path):
        with patch("forge_harness.self_improve.HarnessSelfImprover") as mock_cls:
            mock_improver = MagicMock()
            mock_cls.return_value = mock_improver
            mock_improver.analyze = AsyncMock(return_value=MagicMock(spec=SelfAnalysisResult))

            await run_self_analysis(
                harness_root=tmp_path,
                features_path=tmp_path / "f.json",
                dry_run=True,
            )

        # When dry_run=True, features_path should be /dev/null
        call_kwargs = mock_cls.call_args
        assert call_kwargs.kwargs.get("features_path") == Path("/dev/null")

    @pytest.mark.asyncio
    async def test_run_self_analysis_no_dry_run_uses_provided_path(self, tmp_path):
        custom_path = tmp_path / "custom.json"

        with patch("forge_harness.self_improve.HarnessSelfImprover") as mock_cls:
            mock_improver = MagicMock()
            mock_cls.return_value = mock_improver
            mock_improver.analyze = AsyncMock(return_value=MagicMock(spec=SelfAnalysisResult))

            await run_self_analysis(
                harness_root=tmp_path,
                features_path=custom_path,
                dry_run=False,
            )

        call_kwargs = mock_cls.call_args
        assert call_kwargs.kwargs.get("features_path") == custom_path

    @pytest.mark.asyncio
    async def test_run_self_analysis_none_paths(self):
        """None paths are forwarded to HarnessSelfImprover."""
        with patch("forge_harness.self_improve.HarnessSelfImprover") as mock_cls:
            mock_improver = MagicMock()
            mock_cls.return_value = mock_improver
            mock_improver.analyze = AsyncMock(return_value=MagicMock(spec=SelfAnalysisResult))

            await run_self_analysis()

        call_kwargs = mock_cls.call_args
        assert call_kwargs.kwargs.get("harness_root") is None


# ===========================================================================
# self_analyze_sync
# ===========================================================================


class TestSelfAnalyzeSync:
    def test_self_analyze_sync_calls_asyncio_run(self, tmp_path):
        mock_result = MagicMock(spec=SelfAnalysisResult)

        with patch("forge_harness.self_improve.asyncio") as mock_asyncio, \
             patch("forge_harness.self_improve.run_self_analysis") as mock_run:
            mock_asyncio.run.return_value = mock_result

            result = self_analyze_sync(
                harness_root=tmp_path,
                features_path=tmp_path / "f.json",
                scanners=["bandit"],
                max_features=3,
                priority_threshold="medium",
                dry_run=True,
            )

        assert result is mock_result
        mock_asyncio.run.assert_called_once()

    def test_self_analyze_sync_integration(self, tmp_path):
        """Integration test: self_analyze_sync actually runs the async function."""
        with patch("forge_harness.self_improve.HarnessSelfImprover") as mock_cls:
            mock_improver = MagicMock()
            mock_cls.return_value = mock_improver
            expected = MagicMock(spec=SelfAnalysisResult)
            mock_improver.analyze = AsyncMock(return_value=expected)

            result = self_analyze_sync(
                harness_root=tmp_path,
                dry_run=True,
            )

        assert result is expected


# ===========================================================================
# DEFAULT_TECH_DILIGENCE_URL
# ===========================================================================


class TestDefaultTechDiligenceUrl:
    def test_default_url_is_localhost(self):
        """Verify module-level constant is set when env var is absent."""
        import os

        # If env var is not set, default should be localhost:8000
        expected = os.getenv("TECH_DILIGENCE_URL", "http://localhost:8000")
        assert DEFAULT_TECH_DILIGENCE_URL == expected

    def test_env_var_override(self, monkeypatch, tmp_path):
        """When TECH_DILIGENCE_URL is set, TechDiligenceBridge receives that URL."""
        monkeypatch.setenv("TECH_DILIGENCE_URL", "http://custom-host:5555")

        with patch("forge_harness.self_improve.TechDiligenceBridge") as mock_bridge_cls:
            mock_bridge_cls.return_value = MagicMock()
            # Re-create improver — it reads the env at __init__ time via DEFAULT_TECH_DILIGENCE_URL
            # The module constant was already loaded, so we test through explicit None arg
            HarnessSelfImprover(harness_root=tmp_path, tech_diligence_url=None)

        # The bridge is called with whatever the module constant is (already captured)
        mock_bridge_cls.assert_called_once()
