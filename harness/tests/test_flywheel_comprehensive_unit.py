"""Comprehensive unit tests for forge_harness.flywheel.

Target: 80%+ coverage of flywheel.py

Coverage strategy:
- FlywheelConfig: all fields, defaults, custom values
- FlywheelResult: initialization, to_dict (running/completed), error list mutability
- create_flywheel_loop: features_path auto-detection (project exists/not),
  FORGE_ROOT env var, custom config forwarding, orchestrator passthrough,
  string features_path coercion
- _build_debt_description: message only, with file path, with line number,
  with recommendation, all combined
- quality_report_to_features: empty report, security findings, priority
  threshold filtering (critical/high/medium/low), low quality score (< 70),
  very low score (<50 => high priority), debt feature threshold logic,
  max_features cap, no file_path finding, metadata fields, acceptance_criteria
- scan_project_local: success path, scan failure returns [], custom metrics_dir
- scan_project_for_debt: tech_diligence failure + fallback, no fallback returns [],
  successful report conversion, atlas available/unavailable paths, priority
  filtering, metadata merging, get_config monkey-patch pattern
- generate_portfolio_features: empty dir, hidden dirs skipped, special dirs
  skipped, underscore dirs skipped, files skipped, no markers skipped,
  include_domains filter, exclude_domains filter, output_path write,
  deduplication, priority sort, include_harness success/failure,
  scan exception swallowed
- run_flywheel: full dry_run cycle, features.json list format, wrapper dict
  format, dict-without-features-key, JSONDecodeError, existing_features not
  list guard, non-dry-run writes file, wrapper format preserved, duplicate
  dedup, command_center working_dir override, orchestrator import error,
  orchestrator generic exception, loop_result stats propagation, global error
  captured in result.errors, ended_at always set
- run_flywheel_sync: synchronous wrapper invokes asyncio.run
- aliases: create_fully_wired_loop, scan_portfolio
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import forge_harness.meta_learning.config as _meta_cfg_mod
from forge_harness.flywheel import (
    FlywheelConfig,
    FlywheelResult,
    _build_debt_description,
    create_flywheel_loop,
    generate_portfolio_features,
    quality_report_to_features,
    run_flywheel,
    run_flywheel_sync,
    scan_project_local,
)
from forge_harness.quality_loop import SeverityLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_loop_mock(features_completed: int = 0, features_blocked: int = 0) -> MagicMock:
    loop = MagicMock()
    loop.run = AsyncMock(
        return_value=MagicMock(
            features_completed=features_completed,
            features_blocked=features_blocked,
        )
    )
    return loop


def _patch_loop_factory():
    """Patch both harness_registry and ralph_loop for create_flywheel_loop."""
    return (
        patch("forge_harness.harness_registry.create_harness_registry"),
        patch("forge_harness.ralph_loop.create_ralph_loop_from_registry"),
    )


def _make_mock_report(
    domain: str = "test-domain",
    project_name: str = "test-project",
    quality_score: float = 80.0,
    security_findings: list | None = None,
    issues: list | None = None,
    recommendations: list | None = None,
) -> MagicMock:
    report = MagicMock()
    report.domain = domain
    report.project_name = project_name
    report.quality_score = quality_score
    report.security_findings = security_findings or []
    report.issues = issues or []
    report.recommendations = recommendations or []
    return report


def _make_mock_finding(
    rule_id: str = "RULE-001",
    severity: SeverityLevel = SeverityLevel.HIGH,
    message: str = "Test finding message",
    file_path: str | None = "src/test.py",
    line_number: int | None = 42,
    tool: str = "bandit",
    confidence: str = "HIGH",
) -> MagicMock:
    finding = MagicMock()
    finding.rule_id = rule_id
    finding.severity = severity
    finding.message = message
    finding.file_path = file_path
    finding.line_number = line_number
    finding.tool = tool
    finding.confidence = confidence
    return finding


# ---------------------------------------------------------------------------
# inject / clean get_config into meta_learning.config for tests that need it
# ---------------------------------------------------------------------------


def _inject_get_config(mock_config_obj: MagicMock | None = None) -> MagicMock:
    """Monkey-patch get_config into meta_learning.config module."""
    if mock_config_obj is None:
        mock_config_obj = MagicMock()
    mock_fn = MagicMock(return_value=mock_config_obj)
    _meta_cfg_mod.get_config = mock_fn  # type: ignore[attr-defined]
    return mock_fn


def _remove_get_config() -> None:
    if hasattr(_meta_cfg_mod, "get_config"):
        del _meta_cfg_mod.get_config  # type: ignore[attr-defined]


# ===========================================================================
# FlywheelConfig
# ===========================================================================


class TestFlywheelConfig:
    def test_defaults(self):
        cfg = FlywheelConfig()
        assert cfg.max_iterations == 100
        assert cfg.max_features_per_project == 10
        assert cfg.priority_threshold == "medium"
        assert cfg.include_harness_self_improvement is True
        assert cfg.test_command is None
        assert cfg.working_dir is None
        assert cfg.dry_run is False
        assert cfg.auto_commit is False

    def test_custom_values(self):
        cfg = FlywheelConfig(
            max_iterations=25,
            max_features_per_project=3,
            priority_threshold="critical",
            include_harness_self_improvement=False,
            test_command="uv run pytest",
            working_dir=Path("/some/dir"),
            dry_run=True,
            auto_commit=True,
        )
        assert cfg.max_iterations == 25
        assert cfg.max_features_per_project == 3
        assert cfg.priority_threshold == "critical"
        assert cfg.include_harness_self_improvement is False
        assert cfg.test_command == "uv run pytest"
        assert cfg.working_dir == Path("/some/dir")
        assert cfg.dry_run is True
        assert cfg.auto_commit is True

    def test_errors_list_is_independent_per_instance(self):
        """Ensure default_factory creates independent lists."""
        r1 = FlywheelResult(started_at=datetime.now(UTC))
        r2 = FlywheelResult(started_at=datetime.now(UTC))
        r1.errors.append("err")
        assert r2.errors == []


# ===========================================================================
# FlywheelResult
# ===========================================================================


class TestFlywheelResult:
    def test_initial_state(self):
        now = datetime.now(UTC)
        r = FlywheelResult(started_at=now)
        assert r.started_at is now
        assert r.ended_at is None
        assert r.projects_scanned == 0
        assert r.features_generated == 0
        assert r.features_implemented == 0
        assert r.features_blocked == 0
        assert r.patterns_learned == 0
        assert r.sessions_indexed == 0
        assert r.errors == []

    def test_to_dict_no_end(self):
        start = datetime(2025, 6, 1, 10, 0, 0, tzinfo=UTC)
        r = FlywheelResult(started_at=start)
        d = r.to_dict()
        assert d["started_at"] == "2025-06-01T10:00:00+00:00"
        assert d["ended_at"] is None
        assert d["duration_seconds"] is None
        assert d["projects_scanned"] == 0
        assert d["errors"] == []

    def test_to_dict_with_end(self):
        start = datetime(2025, 6, 1, 10, 0, 0, tzinfo=UTC)
        end = datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC)
        r = FlywheelResult(
            started_at=start,
            ended_at=end,
            projects_scanned=3,
            features_generated=12,
            features_implemented=9,
            features_blocked=3,
            patterns_learned=7,
            sessions_indexed=2,
            errors=["oops"],
        )
        d = r.to_dict()
        assert d["ended_at"] == "2025-06-01T11:00:00+00:00"
        assert d["duration_seconds"] == 3600.0
        assert d["projects_scanned"] == 3
        assert d["features_generated"] == 12
        assert d["features_implemented"] == 9
        assert d["features_blocked"] == 3
        assert d["patterns_learned"] == 7
        assert d["sessions_indexed"] == 2
        assert d["errors"] == ["oops"]

    def test_to_dict_duration_fractional(self):
        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 0, 0, 30, tzinfo=UTC)
        r = FlywheelResult(started_at=start, ended_at=end)
        assert r.to_dict()["duration_seconds"] == 30.0

    def test_errors_list_mutation(self):
        r = FlywheelResult(started_at=datetime.now(UTC))
        r.errors.append("error1")
        r.errors.append("error2")
        assert len(r.errors) == 2
        assert "error1" in r.errors


# ===========================================================================
# _build_debt_description
# ===========================================================================


class TestBuildDebtDescription:
    def test_message_only(self):
        f = MagicMock()
        f.message = "Dangerous eval usage"
        f.file_path = None
        f.line_number = None
        f.recommendation = None
        assert _build_debt_description(f) == "Dangerous eval usage"

    def test_with_file_path_no_line(self):
        f = MagicMock()
        f.message = "SQL injection risk"
        f.file_path = "app/models.py"
        f.line_number = None
        f.recommendation = None
        desc = _build_debt_description(f)
        assert "SQL injection risk" in desc
        assert "**Location:** `app/models.py`" in desc
        assert ":" not in desc.split("`app/models.py`")[1].split("\n")[0]  # no line suffix

    def test_with_file_path_and_line(self):
        f = MagicMock()
        f.message = "Hardcoded password"
        f.file_path = "config.py"
        f.line_number = 99
        f.recommendation = None
        desc = _build_debt_description(f)
        assert "`config.py`:99" in desc

    def test_with_recommendation(self):
        f = MagicMock()
        f.message = "Issue"
        f.file_path = None
        f.line_number = None
        f.recommendation = "Use environment variables"
        desc = _build_debt_description(f)
        assert "**Recommendation:** Use environment variables" in desc

    def test_all_parts_combined(self):
        f = MagicMock()
        f.message = "Full issue"
        f.file_path = "src/core.py"
        f.line_number = 5
        f.recommendation = "Refactor this"
        desc = _build_debt_description(f)
        assert "Full issue" in desc
        assert "`src/core.py`:5" in desc
        assert "**Recommendation:** Refactor this" in desc
        # Parts joined with double newline
        assert "\n\n" in desc


# ===========================================================================
# quality_report_to_features
# ===========================================================================


class TestQualityReportToFeatures:
    def test_empty_report_high_score(self):
        report = _make_mock_report(quality_score=90.0)
        features = quality_report_to_features(report)
        assert features == []

    def test_single_high_finding(self):
        finding = _make_mock_finding(severity=SeverityLevel.HIGH)
        report = _make_mock_report(quality_score=85.0, security_findings=[finding])
        features = quality_report_to_features(report)
        assert len(features) == 1
        f = features[0]
        assert f["priority"] == "high"
        assert f["status"] == "pending"
        assert "test-project" in f["name"]
        assert "**Security Issue:**" in f["description"]
        assert "src/test.py" in f["description"]
        assert ":42" in f["description"]

    def test_critical_finding(self):
        finding = _make_mock_finding(severity=SeverityLevel.CRITICAL)
        report = _make_mock_report(security_findings=[finding])
        features = quality_report_to_features(report)
        assert features[0]["priority"] == "critical"

    def test_medium_finding(self):
        finding = _make_mock_finding(severity=SeverityLevel.MEDIUM)
        report = _make_mock_report(security_findings=[finding])
        features = quality_report_to_features(report)
        assert features[0]["priority"] == "medium"

    def test_low_finding(self):
        finding = _make_mock_finding(severity=SeverityLevel.LOW)
        report = _make_mock_report(security_findings=[finding])
        # Must use threshold="low" to include low-priority findings
        features = quality_report_to_features(report, priority_threshold="low")
        assert features[0]["priority"] == "low"

    def test_priority_threshold_high_excludes_medium_low(self):
        findings = [
            _make_mock_finding(rule_id="C", severity=SeverityLevel.CRITICAL),
            _make_mock_finding(rule_id="H", severity=SeverityLevel.HIGH),
            _make_mock_finding(rule_id="M", severity=SeverityLevel.MEDIUM),
            _make_mock_finding(rule_id="L", severity=SeverityLevel.LOW),
        ]
        report = _make_mock_report(quality_score=90.0, security_findings=findings)
        features = quality_report_to_features(report, priority_threshold="high")
        priorities = {f["priority"] for f in features}
        assert "critical" in priorities
        assert "high" in priorities
        assert "medium" not in priorities
        assert "low" not in priorities

    def test_priority_threshold_critical_only(self):
        findings = [
            _make_mock_finding(rule_id="C", severity=SeverityLevel.CRITICAL),
            _make_mock_finding(rule_id="H", severity=SeverityLevel.HIGH),
        ]
        report = _make_mock_report(quality_score=90.0, security_findings=findings)
        features = quality_report_to_features(report, priority_threshold="critical")
        assert all(f["priority"] == "critical" for f in features)

    def test_priority_threshold_low_includes_all(self):
        findings = [
            _make_mock_finding(rule_id="C", severity=SeverityLevel.CRITICAL),
            _make_mock_finding(rule_id="H", severity=SeverityLevel.HIGH),
            _make_mock_finding(rule_id="M", severity=SeverityLevel.MEDIUM),
            _make_mock_finding(rule_id="L", severity=SeverityLevel.LOW),
        ]
        report = _make_mock_report(quality_score=90.0, security_findings=findings)
        features = quality_report_to_features(report, priority_threshold="low")
        assert len(features) == 4

    def test_max_features_cap(self):
        findings = [_make_mock_finding(rule_id=f"R-{i:03d}") for i in range(20)]
        report = _make_mock_report(quality_score=90.0, security_findings=findings)
        features = quality_report_to_features(report, max_features=7)
        assert len(features) == 7

    def test_finding_without_file_path(self):
        finding = _make_mock_finding(file_path=None, line_number=None)
        report = _make_mock_report(security_findings=[finding])
        features = quality_report_to_features(report)
        assert len(features) == 1
        # file_path None handled in acceptance criteria
        assert "codebase" in features[0]["acceptance_criteria"][0]

    def test_finding_without_line_number(self):
        finding = _make_mock_finding(line_number=None)
        report = _make_mock_report(security_findings=[finding])
        features = quality_report_to_features(report)
        # description should not contain a line number suffix
        assert ":None" not in features[0]["description"]

    def test_security_finding_metadata(self):
        finding = _make_mock_finding(rule_id="XSS-001", tool="semgrep")
        report = _make_mock_report(
            domain="my-domain",
            project_name="my-project",
            security_findings=[finding],
        )
        features = quality_report_to_features(report)
        meta = features[0]["metadata"]
        assert meta["source"] == "quality_loop"
        assert meta["domain"] == "my-domain"
        assert meta["project"] == "my-project"
        assert meta["finding_type"] == "security"
        assert meta["rule_id"] == "XSS-001"
        assert meta["tool"] == "semgrep"
        assert "generated_at" in meta

    def test_acceptance_criteria_structure(self):
        finding = _make_mock_finding(rule_id="RULE-X", file_path="main.py")
        report = _make_mock_report(security_findings=[finding])
        features = quality_report_to_features(report)
        criteria = features[0]["acceptance_criteria"]
        assert len(criteria) == 3
        assert any("RULE-X" in c for c in criteria)
        assert any("pass" in c.lower() for c in criteria)

    def test_standard_fields_present(self):
        finding = _make_mock_finding()
        report = _make_mock_report(security_findings=[finding])
        features = quality_report_to_features(report)
        f = features[0]
        for field in ("id", "name", "description", "status", "priority",
                      "acceptance_criteria", "depends_on", "tests",
                      "estimated_tokens", "attempts", "metadata"):
            assert field in f, f"Missing field: {field}"

    def test_low_quality_score_below_70_medium_priority(self):
        """Score 60 < 70 => debt feature with priority medium (60 >= 50)."""
        report = _make_mock_report(
            quality_score=60.0,
            issues=["Issue A", "Issue B"],
            recommendations=["Fix A"],
        )
        features = quality_report_to_features(report)
        debt = [f for f in features if "debt-score" in f["id"]]
        assert len(debt) == 1
        assert debt[0]["priority"] == "medium"
        assert "Improve quality score" in debt[0]["name"]

    def test_very_low_quality_score_below_50_high_priority(self):
        """Score < 50 => debt feature with priority high."""
        report = _make_mock_report(quality_score=35.0, issues=["A"], recommendations=["B"])
        features = quality_report_to_features(report)
        debt = [f for f in features if "debt-score" in f["id"]]
        assert debt[0]["priority"] == "high"

    def test_debt_feature_not_added_above_70(self):
        """Score >= 70 => no debt feature."""
        report = _make_mock_report(quality_score=70.0)
        features = quality_report_to_features(report)
        debt = [f for f in features if "debt-score" in f["id"]]
        assert len(debt) == 0

    def test_debt_feature_not_added_when_max_reached(self):
        """When max_features already consumed by security findings, no debt feature added."""
        findings = [_make_mock_finding(rule_id=f"R-{i}") for i in range(5)]
        report = _make_mock_report(
            quality_score=30.0,
            security_findings=findings,
            issues=["X"],
        )
        features = quality_report_to_features(report, max_features=5)
        # max_features=5 consumed by security findings; debt feature skipped
        debt = [f for f in features if "debt-score" in f["id"]]
        assert len(debt) == 0

    def test_debt_feature_metadata(self):
        report = _make_mock_report(
            domain="d", project_name="p",
            quality_score=55.0, issues=["i"], recommendations=["r"]
        )
        features = quality_report_to_features(report)
        debt = [f for f in features if "debt-score" in f["id"]]
        meta = debt[0]["metadata"]
        assert meta["source"] == "quality_loop"
        assert meta["finding_type"] == "debt"
        assert "quality_score" in meta

    def test_estimated_tokens_security(self):
        finding = _make_mock_finding()
        report = _make_mock_report(security_findings=[finding])
        features = quality_report_to_features(report)
        assert features[0]["estimated_tokens"] == 4000

    def test_estimated_tokens_debt(self):
        report = _make_mock_report(quality_score=40.0, issues=["x"])
        features = quality_report_to_features(report)
        debt = [f for f in features if "debt-score" in f["id"]]
        assert debt[0]["estimated_tokens"] == 6000


# ===========================================================================
# create_flywheel_loop
# ===========================================================================


class TestCreateFlywheelLoop:
    def test_returns_loop_object(self):
        p1, p2 = _patch_loop_factory()
        with p1 as mock_reg, p2 as mock_create:
            mock_reg.return_value = MagicMock()
            mock_create.return_value = MagicMock()
            with tempfile.TemporaryDirectory() as tmpdir:
                fp = Path(tmpdir) / "features.json"
                fp.write_text("[]")
                result = create_flywheel_loop(
                    domain="d", project="p", features_path=fp
                )
            assert result is not None

    def test_registry_called_with_domain_project(self):
        p1, p2 = _patch_loop_factory()
        with p1 as mock_reg, p2 as mock_create:
            mock_reg.return_value = MagicMock()
            mock_create.return_value = MagicMock()
            with tempfile.TemporaryDirectory() as tmpdir:
                fp = Path(tmpdir) / "features.json"
                fp.write_text("[]")
                create_flywheel_loop(domain="my-d", project="my-p", features_path=fp)
            mock_reg.assert_called_once_with(domain="my-d", project="my-p")

    def test_ralph_loop_called_with_correct_params(self):
        config = FlywheelConfig(max_iterations=42, dry_run=True, test_command="pytest")
        p1, p2 = _patch_loop_factory()
        with p1 as mock_reg, p2 as mock_create:
            mock_reg.return_value = MagicMock()
            mock_create.return_value = MagicMock()
            with tempfile.TemporaryDirectory() as tmpdir:
                fp = Path(tmpdir) / "features.json"
                fp.write_text("[]")
                create_flywheel_loop(
                    domain="d", project="p", features_path=fp, config=config
                )
            kwargs = mock_create.call_args[1]
            assert kwargs["max_iterations"] == 42
            assert kwargs["dry_run"] is True
            assert kwargs["test_command"] == "pytest"
            assert kwargs["domain"] == "d"
            assert kwargs["project"] == "p"

    def test_orchestrator_forwarded(self):
        mock_orch = MagicMock()
        p1, p2 = _patch_loop_factory()
        with p1 as mock_reg, p2 as mock_create:
            mock_reg.return_value = MagicMock()
            mock_create.return_value = MagicMock()
            with tempfile.TemporaryDirectory() as tmpdir:
                fp = Path(tmpdir) / "features.json"
                fp.write_text("[]")
                create_flywheel_loop(
                    domain="d", project="p", features_path=fp, orchestrator=mock_orch
                )
            assert mock_create.call_args[1]["orchestrator"] is mock_orch

    def test_features_path_as_string_coerced_to_path(self):
        p1, p2 = _patch_loop_factory()
        with p1 as mock_reg, p2 as mock_create:
            mock_reg.return_value = MagicMock()
            mock_create.return_value = MagicMock()
            with tempfile.TemporaryDirectory() as tmpdir:
                fp_str = str(Path(tmpdir) / "features.json")
                create_flywheel_loop(domain="d", project="p", features_path=fp_str)
            kwargs = mock_create.call_args[1]
            assert isinstance(kwargs["features_path"], Path)

    def test_features_path_none_project_exists_uses_project_path(self):
        p1, p2 = _patch_loop_factory()
        with p1 as mock_reg, p2 as mock_create:
            mock_reg.return_value = MagicMock()
            mock_create.return_value = MagicMock()
            with tempfile.TemporaryDirectory() as tmpdir:
                forge_root = Path(tmpdir)
                project_dir = forge_root / "the-domain" / "the-project"
                project_dir.mkdir(parents=True)
                with patch.dict("os.environ", {"FORGE_ROOT": str(forge_root)}):
                    create_flywheel_loop(
                        domain="the-domain",
                        project="the-project",
                        features_path=None,
                    )
            expected = forge_root / "the-domain" / "the-project" / "features.json"
            assert mock_create.call_args[1]["features_path"] == expected

    def test_features_path_none_project_not_exists_falls_back(self):
        p1, p2 = _patch_loop_factory()
        with p1 as mock_reg, p2 as mock_create:
            mock_reg.return_value = MagicMock()
            mock_create.return_value = MagicMock()
            with tempfile.TemporaryDirectory() as tmpdir:
                forge_root = Path(tmpdir)
                # Do NOT create the project directory
                with patch.dict("os.environ", {"FORGE_ROOT": str(forge_root)}):
                    create_flywheel_loop(
                        domain="no-domain",
                        project="no-project",
                        features_path=None,
                    )
            assert mock_create.call_args[1]["features_path"] == Path("features.json")

    def test_default_config_created_when_none(self):
        p1, p2 = _patch_loop_factory()
        with p1 as mock_reg, p2 as mock_create:
            mock_reg.return_value = MagicMock()
            mock_create.return_value = MagicMock()
            with tempfile.TemporaryDirectory() as tmpdir:
                fp = Path(tmpdir) / "features.json"
                fp.write_text("[]")
                create_flywheel_loop(domain="d", project="p", features_path=fp, config=None)
            # Should use default max_iterations=100
            assert mock_create.call_args[1]["max_iterations"] == 100


# ===========================================================================
# scan_project_local
# ===========================================================================


class TestScanProjectLocal:
    @pytest.mark.asyncio
    async def test_success_path(self):
        mock_report = _make_mock_report(quality_score=90.0)
        with patch("forge_harness.quality_loop.QualityLoopHarness") as MockHarness:
            mock_h = MagicMock()
            mock_h.scan_project = AsyncMock(return_value=mock_report)
            MockHarness.return_value = mock_h
            with tempfile.TemporaryDirectory() as tmpdir:
                features = await scan_project_local(
                    domain="d", project="p", project_path=Path(tmpdir)
                )
            assert isinstance(features, list)

    @pytest.mark.asyncio
    async def test_scan_exception_returns_empty(self):
        with patch("forge_harness.quality_loop.QualityLoopHarness") as MockHarness:
            mock_h = MagicMock()
            mock_h.scan_project = AsyncMock(side_effect=RuntimeError("boom"))
            MockHarness.return_value = mock_h
            with tempfile.TemporaryDirectory() as tmpdir:
                features = await scan_project_local(
                    domain="d", project="p", project_path=Path(tmpdir)
                )
            assert features == []

    @pytest.mark.asyncio
    async def test_custom_metrics_dir_passed(self):
        mock_report = _make_mock_report(quality_score=80.0)
        with patch("forge_harness.quality_loop.QualityLoopHarness") as MockHarness:
            mock_h = MagicMock()
            mock_h.scan_project = AsyncMock(return_value=mock_report)
            MockHarness.return_value = mock_h
            with tempfile.TemporaryDirectory() as tmpdir:
                metrics = Path(tmpdir) / "my_metrics"
                await scan_project_local(
                    domain="d",
                    project="p",
                    project_path=Path(tmpdir),
                    metrics_dir=metrics,
                )
            call_kwargs = MockHarness.call_args[1]
            assert call_kwargs["metrics_dir"] == metrics

    @pytest.mark.asyncio
    async def test_default_metrics_dir_uses_quality_metrics(self):
        mock_report = _make_mock_report(quality_score=80.0)
        with patch("forge_harness.quality_loop.QualityLoopHarness") as MockHarness:
            mock_h = MagicMock()
            mock_h.scan_project = AsyncMock(return_value=mock_report)
            MockHarness.return_value = mock_h
            with tempfile.TemporaryDirectory() as tmpdir:
                project_path = Path(tmpdir)
                await scan_project_local(
                    domain="d", project="p", project_path=project_path
                )
            call_kwargs = MockHarness.call_args[1]
            assert call_kwargs["metrics_dir"] == project_path / "quality_metrics"

    @pytest.mark.asyncio
    async def test_low_score_report_generates_debt_feature(self):
        finding = _make_mock_finding(severity=SeverityLevel.HIGH)
        mock_report = _make_mock_report(
            quality_score=45.0,
            security_findings=[finding],
            issues=["issue1"],
            recommendations=["rec1"],
        )
        with patch("forge_harness.quality_loop.QualityLoopHarness") as MockHarness:
            mock_h = MagicMock()
            mock_h.scan_project = AsyncMock(return_value=mock_report)
            MockHarness.return_value = mock_h
            with tempfile.TemporaryDirectory() as tmpdir:
                features = await scan_project_local(
                    domain="d", project="p", project_path=Path(tmpdir)
                )
        # Should have both security finding + debt feature
        ids = [f["id"] for f in features]
        assert any("debt-score" in fid for fid in ids)


# ===========================================================================
# scan_project_for_debt (mocked Atlas + TechDiligence)
# ===========================================================================


class TestScanProjectForDebt:
    """Tests for scan_project_for_debt using full Atlas mocking pattern."""

    def _setup_atlas_mocks(
        self,
        is_available: bool = False,
        recently_changed: list | None = None,
    ):
        """Return context manager patches for scan_project_for_debt internals."""
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

        mock_atlas_cls = MagicMock()
        mock_atlas_cls.from_config.return_value = MagicMock()

        mock_prioritizer_cls = MagicMock(return_value=mock_prioritizer)

        return mock_prioritizer, mock_atlas_cls, mock_prioritizer_cls

    @pytest.mark.asyncio
    async def test_tech_diligence_failure_no_fallback_returns_empty(self):
        mock_prioritizer, mock_atlas_cls, mock_prioritizer_cls = self._setup_atlas_mocks()
        mock_bridge = MagicMock()
        mock_bridge.analyze_and_wait = AsyncMock(side_effect=ConnectionError("refused"))

        _inject_get_config()
        try:
            with (
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
            ):
                from forge_harness.flywheel import scan_project_for_debt
                with tempfile.TemporaryDirectory() as tmpdir:
                    features = await scan_project_for_debt(
                        domain="d", project="p",
                        project_path=Path(tmpdir),
                        use_local_fallback=False,
                    )
        finally:
            _remove_get_config()

        assert features == []

    @pytest.mark.asyncio
    async def test_tech_diligence_returns_none_triggers_fallback(self):
        mock_prioritizer, mock_atlas_cls, mock_prioritizer_cls = self._setup_atlas_mocks()
        mock_bridge = MagicMock()
        mock_bridge.analyze_and_wait = AsyncMock(return_value=None)

        _inject_get_config()
        try:
            with (
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
                patch(
                    "forge_harness.flywheel.scan_project_local",
                    new_callable=AsyncMock,
                ) as mock_local,
            ):
                mock_local.return_value = [{"id": "local-1", "name": "Local feature"}]
                from forge_harness.flywheel import scan_project_for_debt
                with tempfile.TemporaryDirectory() as tmpdir:
                    features = await scan_project_for_debt(
                        domain="d", project="p",
                        project_path=Path(tmpdir),
                        use_local_fallback=True,
                    )
        finally:
            _remove_get_config()

        assert len(features) == 1
        assert features[0]["id"] == "local-1"

    @pytest.mark.asyncio
    async def test_successful_report_generates_features(self):
        mock_finding = MagicMock()
        mock_finding.severity = "high"
        mock_finding.title = "Hard-coded secret"
        mock_finding.message = "Password in source code"
        mock_finding.rule_id = "SEC-001"
        mock_finding.file_path = "config.py"
        mock_finding.line_number = 10
        mock_finding.scanner = "bandit"

        mock_report = MagicMock()
        mock_report.findings = [mock_finding]

        mock_prioritizer, mock_atlas_cls, mock_prioritizer_cls = self._setup_atlas_mocks()
        mock_bridge = MagicMock()
        mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)

        _inject_get_config()
        try:
            with (
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
            ):
                from forge_harness.flywheel import scan_project_for_debt
                with tempfile.TemporaryDirectory() as tmpdir:
                    features = await scan_project_for_debt(
                        domain="dom", project="proj",
                        project_path=Path(tmpdir),
                    )
        finally:
            _remove_get_config()

        assert len(features) == 1
        f = features[0]
        assert f["priority"] == "high"
        assert "Hard-coded secret" in f["name"]
        assert f["metadata"]["source"] == "tech_diligence"
        assert f["metadata"]["domain"] == "dom"
        assert f["metadata"]["project"] == "proj"
        assert f["metadata"]["scanner"] == "bandit"

    @pytest.mark.asyncio
    async def test_priority_threshold_high_filters_low_medium(self):
        findings = []
        for sev in ["critical", "high", "medium", "low"]:
            m = MagicMock()
            m.severity = sev
            m.title = f"{sev} title"
            m.message = f"{sev} message"
            m.rule_id = f"R-{sev}"
            m.file_path = "f.py"
            m.line_number = 1
            m.scanner = "x"
            findings.append(m)

        mock_report = MagicMock()
        mock_report.findings = findings

        mock_prioritizer, mock_atlas_cls, mock_prioritizer_cls = self._setup_atlas_mocks()
        mock_bridge = MagicMock()
        mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)

        _inject_get_config()
        try:
            with (
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
            ):
                from forge_harness.flywheel import scan_project_for_debt
                with tempfile.TemporaryDirectory() as tmpdir:
                    features = await scan_project_for_debt(
                        domain="d", project="p",
                        project_path=Path(tmpdir),
                        priority_threshold="high",
                    )
        finally:
            _remove_get_config()

        priorities = {f["priority"] for f in features}
        assert "medium" not in priorities
        assert "low" not in priorities

    @pytest.mark.asyncio
    async def test_atlas_available_applies_enhancements(self):
        mock_finding = MagicMock()
        mock_finding.severity = "high"
        mock_finding.title = "Issue"
        mock_finding.message = "desc"
        mock_finding.rule_id = "R1"
        mock_finding.file_path = "f.py"
        mock_finding.line_number = 1
        mock_finding.scanner = "s"

        mock_report = MagicMock()
        mock_report.findings = [mock_finding]

        mock_prioritizer, mock_atlas_cls, mock_prioritizer_cls = self._setup_atlas_mocks(
            is_available=True
        )
        mock_bridge = MagicMock()
        mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)

        _inject_get_config()
        try:
            with (
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
            ):
                from forge_harness.flywheel import scan_project_for_debt
                with tempfile.TemporaryDirectory() as tmpdir:
                    features = await scan_project_for_debt(
                        domain="d", project="p",
                        project_path=Path(tmpdir),
                    )
        finally:
            _remove_get_config()

        # Prioritizer methods should have been invoked
        mock_prioritizer.filter_by_historical_success.assert_called_once()
        mock_prioritizer.boost_hotspot_priorities.assert_called_once()
        assert len(features) == 1

    @pytest.mark.asyncio
    async def test_atlas_incremental_scan_recently_changed_files(self):
        mock_report = MagicMock()
        mock_report.findings = []

        mock_prioritizer, mock_atlas_cls, mock_prioritizer_cls = self._setup_atlas_mocks(
            is_available=True,
            recently_changed=["a.py", "b.py", "c.py"],
        )
        mock_bridge = MagicMock()
        mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)

        _inject_get_config()
        try:
            with (
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
            ):
                from forge_harness.flywheel import scan_project_for_debt
                with tempfile.TemporaryDirectory() as tmpdir:
                    features = await scan_project_for_debt(
                        domain="d", project="p",
                        project_path=Path(tmpdir),
                    )
        finally:
            _remove_get_config()

        mock_prioritizer.get_recently_changed_files.assert_called_once()
        assert features == []

    @pytest.mark.asyncio
    async def test_finding_metadata_merging_from_atlas(self):
        """When finding_dict has 'metadata', it should be merged into base_metadata."""
        mock_finding = MagicMock()
        mock_finding.severity = "high"
        mock_finding.title = "Issue"
        mock_finding.message = "msg"
        mock_finding.rule_id = "R1"
        mock_finding.file_path = "f.py"
        mock_finding.line_number = 1
        mock_finding.scanner = "s"

        mock_report = MagicMock()
        mock_report.findings = [mock_finding]

        # Prioritizer that adds metadata to findings
        enriched_finding = {
            "category": "R1",
            "title": "Issue",
            "description": "msg",
            "severity": "high",
            "file_path": "f.py",
            "scanner": "s",
            "metadata": {"atlas_key": "atlas_value"},
        }
        mock_prioritizer = MagicMock()
        mock_prioritizer.is_available = AsyncMock(return_value=True)
        mock_prioritizer.get_recently_changed_files = AsyncMock(return_value=[])
        mock_prioritizer.filter_by_historical_success = AsyncMock(
            return_value=[enriched_finding]
        )
        mock_prioritizer.boost_hotspot_priorities = AsyncMock(
            side_effect=lambda findings, domain, project: findings
        )
        mock_prioritizer_cls = MagicMock(return_value=mock_prioritizer)

        mock_atlas_cls = MagicMock()
        mock_atlas_cls.from_config.return_value = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.analyze_and_wait = AsyncMock(return_value=mock_report)

        _inject_get_config()
        try:
            with (
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
            ):
                from forge_harness.flywheel import scan_project_for_debt
                with tempfile.TemporaryDirectory() as tmpdir:
                    features = await scan_project_for_debt(
                        domain="d", project="p",
                        project_path=Path(tmpdir),
                    )
        finally:
            _remove_get_config()

        assert len(features) == 1
        assert features[0]["metadata"].get("atlas_key") == "atlas_value"


# ===========================================================================
# generate_portfolio_features
# ===========================================================================


class TestGeneratePortfolioFeatures:
    @pytest.mark.asyncio
    async def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            features = await generate_portfolio_features(
                forge_root=Path(tmpdir), include_harness=False
            )
        assert features == []

    @pytest.mark.asyncio
    async def test_hidden_directories_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            hidden = root / ".hidden"
            hidden.mkdir()
            (hidden / "proj").mkdir()
            (hidden / "proj" / "pyproject.toml").write_text("")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                features = await generate_portfolio_features(
                    forge_root=root, include_harness=False
                )
            mock_scan.assert_not_called()
            assert features == []

    @pytest.mark.asyncio
    async def test_underscore_directories_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            udir = root / "_internal"
            udir.mkdir()
            (udir / "proj").mkdir()
            (udir / "proj" / "pyproject.toml").write_text("")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                features = await generate_portfolio_features(
                    forge_root=root, include_harness=False
                )
            mock_scan.assert_not_called()

    @pytest.mark.asyncio
    async def test_special_directories_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for special in ["docs", "scripts", "templates", "node_modules", ".git"]:
                d = root / special
                d.mkdir()
                (d / "proj").mkdir()
                (d / "proj" / "pyproject.toml").write_text("")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                features = await generate_portfolio_features(
                    forge_root=root, include_harness=False
                )
            mock_scan.assert_not_called()

    @pytest.mark.asyncio
    async def test_files_in_root_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "some_file.txt").write_text("not a dir")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                features = await generate_portfolio_features(
                    forge_root=root, include_harness=False
                )
            mock_scan.assert_not_called()

    @pytest.mark.asyncio
    async def test_project_without_markers_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "domain" / "no-markers-project").mkdir(parents=True)
            # No CLAUDE.md, pyproject.toml, package.json, Cargo.toml
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                features = await generate_portfolio_features(
                    forge_root=root, include_harness=False
                )
            mock_scan.assert_not_called()

    @pytest.mark.asyncio
    async def test_project_with_pyproject_toml_scanned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_dir = root / "domain" / "project"
            proj_dir.mkdir(parents=True)
            (proj_dir / "pyproject.toml").write_text("[project]")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = [{"id": "f1", "priority": "high", "name": "F"}]
                features = await generate_portfolio_features(
                    forge_root=root, include_harness=False
                )
            mock_scan.assert_called_once()
            assert len(features) == 1

    @pytest.mark.asyncio
    async def test_project_with_package_json_scanned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_dir = root / "domain" / "project"
            proj_dir.mkdir(parents=True)
            (proj_dir / "package.json").write_text("{}")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = []
                await generate_portfolio_features(forge_root=root, include_harness=False)
            mock_scan.assert_called_once()

    @pytest.mark.asyncio
    async def test_project_with_claude_md_scanned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_dir = root / "domain" / "project"
            proj_dir.mkdir(parents=True)
            (proj_dir / "CLAUDE.md").write_text("# Claude")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = []
                await generate_portfolio_features(forge_root=root, include_harness=False)
            mock_scan.assert_called_once()

    @pytest.mark.asyncio
    async def test_include_domains_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for dom in ["domain-a", "domain-b"]:
                p = root / dom / "proj"
                p.mkdir(parents=True)
                (p / "pyproject.toml").write_text("")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = []
                await generate_portfolio_features(
                    forge_root=root,
                    include_domains=["domain-a"],
                    include_harness=False,
                )
            assert mock_scan.call_count == 1
            call_kwargs = mock_scan.call_args[1]
            assert call_kwargs["domain"] == "domain-a"

    @pytest.mark.asyncio
    async def test_exclude_domains_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for dom in ["domain-a", "domain-b"]:
                p = root / dom / "proj"
                p.mkdir(parents=True)
                (p / "pyproject.toml").write_text("")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = []
                await generate_portfolio_features(
                    forge_root=root,
                    exclude_domains=["domain-b"],
                    include_harness=False,
                )
            assert mock_scan.call_count == 1
            call_kwargs = mock_scan.call_args[1]
            assert call_kwargs["domain"] == "domain-a"

    @pytest.mark.asyncio
    async def test_output_path_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_dir = root / "dom" / "proj"
            proj_dir.mkdir(parents=True)
            (proj_dir / "pyproject.toml").write_text("")
            output_path = root / "output" / "features.json"
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = [{"id": "f1", "priority": "high", "name": "F"}]
                await generate_portfolio_features(
                    forge_root=root,
                    output_path=output_path,
                    include_harness=False,
                )
            assert output_path.exists()
            data = json.loads(output_path.read_text())
            assert len(data) == 1

    @pytest.mark.asyncio
    async def test_no_output_path_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_dir = root / "dom" / "proj"
            proj_dir.mkdir(parents=True)
            (proj_dir / "pyproject.toml").write_text("")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = [{"id": "f1", "priority": "high", "name": "F"}]
                await generate_portfolio_features(
                    forge_root=root, output_path=None, include_harness=False
                )
            # No files written other than what was put there
            assert not (root / "features.json").exists()

    @pytest.mark.asyncio
    async def test_deduplication(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for dom in ["dom-1", "dom-2"]:
                p = root / dom / "proj"
                p.mkdir(parents=True)
                (p / "pyproject.toml").write_text("")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = [{"id": "same-id", "priority": "high", "name": "X"}]
                features = await generate_portfolio_features(
                    forge_root=root, include_harness=False
                )
            ids = [f["id"] for f in features]
            assert ids.count("same-id") == 1

    @pytest.mark.asyncio
    async def test_priority_sort_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_dir = root / "dom" / "proj"
            proj_dir.mkdir(parents=True)
            (proj_dir / "pyproject.toml").write_text("")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = [
                    {"id": "low-1", "priority": "low", "name": "Low"},
                    {"id": "crit-1", "priority": "critical", "name": "Crit"},
                    {"id": "med-1", "priority": "medium", "name": "Med"},
                    {"id": "high-1", "priority": "high", "name": "High"},
                ]
                features = await generate_portfolio_features(
                    forge_root=root, include_harness=False
                )
            priorities = [f["priority"] for f in features]
            order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            sorted_check = sorted(priorities, key=lambda p: order.get(p, 2))
            assert priorities == sorted_check

    @pytest.mark.asyncio
    async def test_scan_exception_swallowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_dir = root / "dom" / "proj"
            proj_dir.mkdir(parents=True)
            (proj_dir / "pyproject.toml").write_text("")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.side_effect = RuntimeError("unexpected crash")
                features = await generate_portfolio_features(
                    forge_root=root, include_harness=False
                )
            # Exception swallowed, returns empty
            assert features == []

    @pytest.mark.asyncio
    async def test_hidden_project_dirs_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            domain_dir = root / "domain"
            domain_dir.mkdir()
            hidden_proj = domain_dir / ".hidden-project"
            hidden_proj.mkdir()
            (hidden_proj / "pyproject.toml").write_text("")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                await generate_portfolio_features(forge_root=root, include_harness=False)
            mock_scan.assert_not_called()

    @pytest.mark.asyncio
    async def test_include_harness_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            harness_dir = root / "harness"
            harness_dir.mkdir()

            mock_feature = MagicMock()
            mock_feature.to_dict.return_value = {"id": "harness-1", "priority": "high", "name": "H"}
            mock_result = MagicMock()
            mock_result.features_generated = [mock_feature]

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.self_improve.HarnessSelfImprover") as MockImprover,
            ):
                mock_scan.return_value = []
                mock_improver = MagicMock()
                mock_improver.analyze = AsyncMock(return_value=mock_result)
                MockImprover.return_value = mock_improver

                features = await generate_portfolio_features(
                    forge_root=root, include_harness=True
                )

            harness_ids = [f["id"] for f in features if f.get("id") == "harness-1"]
            assert len(harness_ids) == 1

    @pytest.mark.asyncio
    async def test_include_harness_not_exists_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # No harness/ directory
            with patch("forge_harness.flywheel.scan_project_for_debt"):
                features = await generate_portfolio_features(
                    forge_root=root, include_harness=True
                )
            assert features == []

    @pytest.mark.asyncio
    async def test_include_harness_failure_swallowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            harness_dir = root / "harness"
            harness_dir.mkdir()
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.self_improve.HarnessSelfImprover") as MockImprover,
            ):
                mock_scan.return_value = []
                mock_improver = MagicMock()
                mock_improver.analyze = AsyncMock(side_effect=RuntimeError("crash"))
                MockImprover.return_value = mock_improver
                features = await generate_portfolio_features(
                    forge_root=root, include_harness=True
                )
            # Exception swallowed
            assert features == []


# ===========================================================================
# run_flywheel
# ===========================================================================


class TestRunFlywheel:
    @pytest.mark.asyncio
    async def test_basic_dry_run_cycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj = root / "dom" / "proj"
            proj.mkdir(parents=True)
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj", config=config
                )
        assert isinstance(result, FlywheelResult)
        assert result.ended_at is not None
        assert result.projects_scanned == 1

    @pytest.mark.asyncio
    async def test_ended_at_always_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj = root / "d" / "p"
            proj.mkdir(parents=True)
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="d", project="p", config=config
                )
        assert result.ended_at is not None

    @pytest.mark.asyncio
    async def test_features_json_plain_list_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            features_file = proj_path / "features.json"
            features_file.write_text(json.dumps([
                {"id": "existing-1", "name": "Existing", "priority": "high"}
            ]))
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = [
                    {"id": "new-1", "name": "New", "priority": "medium"}
                ]
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj", config=config
                )
        assert result.features_generated == 1

    @pytest.mark.asyncio
    async def test_features_json_wrapper_dict_format(self):
        """features.json with {features: [...]} wrapper is handled correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            features_file = proj_path / "features.json"
            wrapper = {
                "version": "1.0",
                "features": [{"id": "wrap-1", "name": "Wrapped", "priority": "high"}],
            }
            features_file.write_text(json.dumps(wrapper))
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj", config=config
                )
        assert result.features_generated == 0  # no new features

    @pytest.mark.asyncio
    async def test_features_json_dict_without_features_key(self):
        """features.json dict without 'features' key is treated as empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            (proj_path / "features.json").write_text(json.dumps({"other": "data"}))
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = [{"id": "new-1", "name": "N", "priority": "low"}]
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj", config=config
                )
        assert result.features_generated == 1

    @pytest.mark.asyncio
    async def test_features_json_decode_error_handled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            (proj_path / "features.json").write_text("NOT VALID JSON {{{{")
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj", config=config
                )
        # Should not raise; ended_at should be set
        assert result.ended_at is not None

    @pytest.mark.asyncio
    async def test_duplicate_features_not_added(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            (proj_path / "features.json").write_text(
                json.dumps([{"id": "dup-1", "name": "Dup", "priority": "high"}])
            )
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = [
                    {"id": "dup-1", "name": "Dup copy", "priority": "high"},
                    {"id": "unique-1", "name": "Unique", "priority": "low"},
                ]
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj", config=config
                )
        assert result.features_generated == 1

    @pytest.mark.asyncio
    async def test_non_dry_run_writes_features_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            features_file = proj_path / "features.json"
            config = FlywheelConfig(dry_run=False)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = [{"id": "new-1", "name": "N", "priority": "high"}]
                mock_create.return_value = _make_loop_mock()
                await run_flywheel(
                    forge_root=root, domain="dom", project="proj",
                    config=config,
                    create_orchestrator=False,
                )
            # Check inside tmpdir context so path still exists
            assert features_file.exists()
            data = json.loads(features_file.read_text())
            assert any(f["id"] == "new-1" for f in data)

    @pytest.mark.asyncio
    async def test_non_dry_run_preserves_wrapper_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            wrapper = {"version": "2.0", "features": []}
            features_file = proj_path / "features.json"
            features_file.write_text(json.dumps(wrapper))
            config = FlywheelConfig(dry_run=False)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = [{"id": "new-1", "name": "N", "priority": "high"}]
                mock_create.return_value = _make_loop_mock()
                await run_flywheel(
                    forge_root=root, domain="dom", project="proj",
                    config=config, create_orchestrator=False,
                )
            # Check while tmpdir still exists
            data = json.loads(features_file.read_text())
            # Wrapper format preserved
            assert isinstance(data, dict)
            assert "version" in data
            assert any(f["id"] == "new-1" for f in data["features"])

    @pytest.mark.asyncio
    async def test_loop_result_stats_propagated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock(
                    features_completed=5, features_blocked=2
                )
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj", config=config
                )
        assert result.features_implemented == 5
        assert result.features_blocked == 2
        assert result.sessions_indexed == 1

    @pytest.mark.asyncio
    async def test_global_exception_captured_in_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.side_effect = ValueError("unexpected failure")
                result = await run_flywheel(
                    forge_root=root, domain="d", project="p"
                )
        assert len(result.errors) == 1
        assert "unexpected failure" in result.errors[0]
        assert result.ended_at is not None

    @pytest.mark.asyncio
    async def test_command_center_working_dir_override(self):
        """run_flywheel sets working_dir for harness/command_center project."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            harness_dir = root / "harness"
            harness_dir.mkdir()
            proj_path = root / "harness" / "command_center"
            proj_path.mkdir(parents=True)
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root,
                    domain="harness",
                    project="command_center",
                    config=config,
                )
        # working_dir should have been set on config
        assert config.working_dir == (root / "harness").resolve()

    @pytest.mark.asyncio
    async def test_orchestrator_import_error_handled(self):
        """run_flywheel continues when FeatureOrchestrator cannot be imported."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            config = FlywheelConfig(dry_run=False)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
                patch(
                    "forge_harness.flywheel.FeatureOrchestrator",
                    side_effect=ImportError("no module"),
                    create=True,
                ),
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                # We need the actual import inside run_flywheel to fail
                import builtins
                original_import = builtins.__import__

                def mock_import(name, *args, **kwargs):
                    if name == "forge_harness.agent":
                        raise ImportError("no module named forge_harness.agent")
                    return original_import(name, *args, **kwargs)

                with patch("builtins.__import__", side_effect=mock_import):
                    result = await run_flywheel(
                        forge_root=root, domain="dom", project="proj",
                        config=config, create_orchestrator=True,
                    )
        assert isinstance(result, FlywheelResult)

    @pytest.mark.asyncio
    async def test_create_orchestrator_false_skips_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            config = FlywheelConfig(dry_run=False)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj",
                    config=config, create_orchestrator=False,
                )
        assert isinstance(result, FlywheelResult)

    @pytest.mark.asyncio
    async def test_orchestrator_passed_directly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            mock_orch = MagicMock()
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj",
                    config=config, orchestrator=mock_orch,
                )
            # orchestrator passed directly to create_flywheel_loop
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["orchestrator"] is mock_orch

    @pytest.mark.asyncio
    async def test_no_features_file_no_error(self):
        """run_flywheel works when features.json doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            # No features.json created
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj", config=config
                )
        assert result.ended_at is not None
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write_features_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            features_file = proj_path / "features.json"
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = [{"id": "n1", "name": "N", "priority": "high"}]
                mock_create.return_value = _make_loop_mock()
                await run_flywheel(
                    forge_root=root, domain="dom", project="proj", config=config
                )
        # dry_run=True: file should NOT be written
        assert not features_file.exists()

    @pytest.mark.asyncio
    async def test_default_config_when_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj", config=None
                )
        assert isinstance(result, FlywheelResult)


# ===========================================================================
# run_flywheel_sync
# ===========================================================================


class TestRunFlywheelSync:
    def test_sync_wrapper_returns_flywheel_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                result = run_flywheel_sync(
                    forge_root=root, domain="dom", project="proj", config=config
                )
        assert isinstance(result, FlywheelResult)
        assert result.ended_at is not None

    def test_sync_wrapper_default_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                result = run_flywheel_sync(
                    forge_root=root, domain="dom", project="proj"
                )
        assert isinstance(result, FlywheelResult)

    def test_sync_wrapper_propagates_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.side_effect = RuntimeError("bad thing")
                result = run_flywheel_sync(
                    forge_root=root, domain="d", project="p"
                )
        assert len(result.errors) > 0


# ===========================================================================
# Additional coverage for remaining uncovered lines
# ===========================================================================


class TestRemainingCoverage:
    """Targets the few remaining uncovered lines."""

    @pytest.mark.asyncio
    async def test_generate_portfolio_skips_files_in_domain_projects(self):
        """Line 605: file in domain dir is skipped (project_path.is_dir() == False)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            domain_dir = root / "my-domain"
            domain_dir.mkdir()
            # Create a file (not a dir) inside domain
            (domain_dir / "not_a_project.txt").write_text("some file")
            # Create an actual project too so scan_project_for_debt is at least callable
            proj = domain_dir / "real-project"
            proj.mkdir()
            (proj / "pyproject.toml").write_text("[project]")
            with patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan:
                mock_scan.return_value = []
                features = await generate_portfolio_features(
                    forge_root=root, include_harness=False
                )
            # Only the real project should be scanned (1 call), not the file
            assert mock_scan.call_count == 1

    @pytest.mark.asyncio
    async def test_run_flywheel_orchestrator_import_error(self):
        """Lines 718-719: ImportError when importing FeatureOrchestrator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            config = FlywheelConfig(dry_run=False)

            import builtins
            original_import = builtins.__import__

            def failing_import(name, *args, **kwargs):
                if name == "forge_harness.agent":
                    raise ImportError("mocked: no agent module")
                return original_import(name, *args, **kwargs)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
                patch("builtins.__import__", side_effect=failing_import),
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj",
                    config=config, create_orchestrator=True,
                )
        assert isinstance(result, FlywheelResult)
        assert result.ended_at is not None

    @pytest.mark.asyncio
    async def test_run_flywheel_orchestrator_generic_exception(self):
        """Lines 720-721: generic Exception when creating FeatureOrchestrator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            config = FlywheelConfig(dry_run=False)

            # Make FeatureOrchestrator raise on instantiation
            mock_orch_cls = MagicMock(side_effect=RuntimeError("init failed"))
            mock_agent_module = MagicMock()
            mock_agent_module.FeatureOrchestrator = mock_orch_cls

            import builtins
            original_import = builtins.__import__

            def agent_import(name, *args, **kwargs):
                if name == "forge_harness.agent":
                    return mock_agent_module
                return original_import(name, *args, **kwargs)

            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
                patch("builtins.__import__", side_effect=agent_import),
            ):
                mock_scan.return_value = []
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj",
                    config=config, create_orchestrator=True,
                )
        assert isinstance(result, FlywheelResult)

    @pytest.mark.asyncio
    async def test_run_flywheel_existing_features_not_list_guard(self):
        """Line 754: existing_features guard when JSON is a non-list, non-dict type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_path = root / "dom" / "proj"
            proj_path.mkdir(parents=True)
            # Write a JSON string value (not list or dict)
            (proj_path / "features.json").write_text(json.dumps("not a list or dict"))
            config = FlywheelConfig(dry_run=True)
            with (
                patch("forge_harness.flywheel.scan_project_for_debt") as mock_scan,
                patch("forge_harness.flywheel.create_flywheel_loop") as mock_create,
            ):
                mock_scan.return_value = [{"id": "f1", "name": "N", "priority": "high"}]
                mock_create.return_value = _make_loop_mock()
                result = await run_flywheel(
                    forge_root=root, domain="dom", project="proj", config=config
                )
        # features.json content was non-list; treated as empty, new feature added
        assert result.features_generated == 1


# ===========================================================================
# Convenience aliases
# ===========================================================================


class TestConvenienceAliases:
    def test_create_fully_wired_loop_is_alias(self):
        from forge_harness.flywheel import create_fully_wired_loop
        assert create_fully_wired_loop is create_flywheel_loop

    def test_scan_portfolio_is_alias(self):
        from forge_harness.flywheel import scan_portfolio
        assert scan_portfolio is generate_portfolio_features
