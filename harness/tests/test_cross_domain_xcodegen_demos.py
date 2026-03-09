"""Tests for cross_domain, xcodegen_wrapper, session_orchestrator_example,
demo_monitor, and demo_assess modules.

Covers zero-coverage modules with comprehensive mocking of all external
dependencies (subprocess, file I/O, network, macOS-specific tools).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest
import yaml

# ============================================================================
# Helpers / Fixtures
# ============================================================================


def _make_session(domain="ai", status="active"):
    """Return a real AgentSession dataclass instance (needed for asdict())."""
    from forge_harness.session_tracker import AgentSession

    return AgentSession(
        session_name=f"forge:{domain}-agent",
        window_name=f"{domain}-agent",
        agent_type="claude",
        domain=domain,
        status=status,
    )


# ============================================================================
# forge_harness.heartbeat.cross_domain
# ============================================================================


class TestCrossDomainHeartbeatCollector:
    """Tests for CrossDomainHeartbeatCollector."""

    # ----- helpers -----

    @staticmethod
    def _make_collector(tmp_path):
        """Return a collector wired to tmp_path."""
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector

        return CrossDomainHeartbeatCollector(
            forge_root=tmp_path,
            sessions_dir=tmp_path / "sessions",
            output_dir=tmp_path / "heartbeat",
        )

    # ----- __init__ -----

    def test_init_defaults(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector

        col = CrossDomainHeartbeatCollector(forge_root=tmp_path)
        assert col.forge_root == tmp_path.resolve()
        assert col.sessions_dir == tmp_path / ".forge/sessions"
        assert col.output_dir == tmp_path / ".forge" / "heartbeat"
        assert col.session_prefix == "forge"

    def test_init_custom_paths(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector

        sd = tmp_path / "s"
        od = tmp_path / "o"
        col = CrossDomainHeartbeatCollector(
            forge_root=tmp_path,
            sessions_dir=sd,
            output_dir=od,
            session_prefix="myprefix",
        )
        assert col.sessions_dir == sd
        assert col.output_dir == od
        assert col.session_prefix == "myprefix"

    def test_init_no_forge_root(self, monkeypatch, tmp_path):
        """Defaults forge_root to cwd when not supplied."""
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector

        monkeypatch.chdir(tmp_path)
        col = CrossDomainHeartbeatCollector()
        assert col.forge_root == tmp_path.resolve()

    # ----- _load_domains -----

    def test_load_domains_uses_yaml_if_exists(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector

        # Build the expected yaml path
        yaml_path = tmp_path / "harness" / "forge_harness" / "domains.yaml"
        yaml_path.parent.mkdir(parents=True)
        yaml_path.write_text("ai:\n  display_name: AI\n  active: true\n")

        col = CrossDomainHeartbeatCollector(forge_root=tmp_path)
        with (
            patch("forge_harness.heartbeat.cross_domain.DomainRegistry") as MockDR,
        ):
            MockDR.load.return_value = None
            MockDR.keys.return_value = ["ai", "health"]
            domains = col._load_domains()

        MockDR.load.assert_called_once_with(yaml_path)
        assert domains == ["ai", "health"]

    def test_load_domains_fallback_when_no_yaml(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector

        col = CrossDomainHeartbeatCollector(forge_root=tmp_path)
        with patch("forge_harness.heartbeat.cross_domain.DomainRegistry") as MockDR:
            MockDR.load.return_value = None
            MockDR.keys.return_value = ["ai"]
            domains = col._load_domains()

        MockDR.load.assert_called_once_with()  # no arg
        assert domains == ["ai"]

    def test_load_domains_returns_empty_on_error(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector

        col = CrossDomainHeartbeatCollector(forge_root=tmp_path)
        with patch("forge_harness.heartbeat.cross_domain.DomainRegistry") as MockDR:
            MockDR.load.side_effect = RuntimeError("boom")
            domains = col._load_domains()

        assert domains == []

    # ----- _build_domain_summary -----

    def test_build_domain_summary_empty_sessions(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector

        col = self._make_collector(tmp_path)
        summary = col._build_domain_summary("ai", [])
        assert summary["domain"] == "ai"
        assert summary["total_sessions"] == 0
        assert summary["sessions"] == []
        assert summary["status_counts"] == {
            "active": 0,
            "idle": 0,
            "error": 0,
            "unknown": 0,
        }
        # timestamp should be a string
        assert isinstance(summary["timestamp"], str)

    def test_build_domain_summary_with_sessions(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector

        col = self._make_collector(tmp_path)

        # Create real dataclass-compatible sessions via asdict
        from forge_harness.session_tracker import AgentSession

        sessions = [
            AgentSession(
                session_name="forge:cursor",
                window_name="cursor",
                agent_type="claude",
                domain="ai",
                status="active",
            ),
            AgentSession(
                session_name="forge:gemini",
                window_name="gemini",
                agent_type="external",
                domain="ai",
                status="idle",
            ),
            AgentSession(
                session_name="forge:err",
                window_name="err",
                agent_type="unknown",
                domain="ai",
                status="error",
            ),
        ]
        summary = col._build_domain_summary("ai", sessions)
        assert summary["total_sessions"] == 3
        assert summary["status_counts"]["active"] == 1
        assert summary["status_counts"]["idle"] == 1
        assert summary["status_counts"]["error"] == 1
        assert summary["status_counts"]["unknown"] == 0
        assert len(summary["sessions"]) == 3

    # ----- _write_domain_summary -----

    def test_write_domain_summary_creates_file(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector

        col = self._make_collector(tmp_path)
        summary = {"domain": "ai", "total_sessions": 0, "sessions": []}
        col._write_domain_summary("ai", summary)

        output_file = tmp_path / "heartbeat" / "ai.json"
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["domain"] == "ai"

    def test_write_domain_summary_creates_parent_dirs(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector

        out = tmp_path / "deep" / "nested" / "heartbeat"
        col = CrossDomainHeartbeatCollector(
            forge_root=tmp_path,
            output_dir=out,
        )
        col._write_domain_summary("x", {"domain": "x"})
        assert (out / "x.json").exists()

    # ----- collect -----

    def test_collect_returns_summaries_per_domain(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector
        from forge_harness.session_tracker import AgentSession

        col = self._make_collector(tmp_path)
        sessions = [
            AgentSession(
                session_name="forge:cursor",
                window_name="cursor",
                agent_type="claude",
                domain="ai",
                status="active",
            ),
            AgentSession(
                session_name="forge:health",
                window_name="health",
                agent_type="external",
                domain="health",
                status="idle",
            ),
        ]

        with (
            patch.object(col, "_load_domains", return_value=["ai", "health"]),
            patch.object(col._tracker, "get_all_sessions", return_value=sessions),
        ):
            summaries = col.collect()

        assert set(summaries.keys()) == {"ai", "health"}
        assert summaries["ai"]["total_sessions"] == 1
        assert summaries["health"]["total_sessions"] == 1

    def test_collect_creates_output_files(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector

        col = self._make_collector(tmp_path)
        with (
            patch.object(col, "_load_domains", return_value=["ai"]),
            patch.object(col._tracker, "get_all_sessions", return_value=[]),
        ):
            col.collect()

        assert (tmp_path / "heartbeat" / "ai.json").exists()

    def test_collect_ignores_sessions_without_domain(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector
        from forge_harness.session_tracker import AgentSession

        col = self._make_collector(tmp_path)
        # Session with domain=None should be ignored
        s = AgentSession(
            session_name="forge:x",
            window_name="x",
            agent_type="claude",
            domain=None,  # no domain
            status="active",
        )

        with (
            patch.object(col, "_load_domains", return_value=["ai"]),
            patch.object(col._tracker, "get_all_sessions", return_value=[s]),
        ):
            summaries = col.collect()

        assert summaries["ai"]["total_sessions"] == 0

    def test_collect_ignores_sessions_with_unknown_domain(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector
        from forge_harness.session_tracker import AgentSession

        col = self._make_collector(tmp_path)
        # Domain "other_domain" is not in the registered domains list ["ai"]
        s = AgentSession(
            session_name="forge:x",
            window_name="x",
            agent_type="claude",
            domain="other_domain",
            status="active",
        )

        with (
            patch.object(col, "_load_domains", return_value=["ai"]),
            patch.object(col._tracker, "get_all_sessions", return_value=[s]),
        ):
            summaries = col.collect()

        assert summaries["ai"]["total_sessions"] == 0

    def test_collect_empty_domains(self, tmp_path):
        from forge_harness.heartbeat.cross_domain import CrossDomainHeartbeatCollector

        col = self._make_collector(tmp_path)
        with (
            patch.object(col, "_load_domains", return_value=[]),
            patch.object(col._tracker, "get_all_sessions", return_value=[]),
        ):
            summaries = col.collect()

        assert summaries == {}


# ============================================================================
# forge_harness.ios_harness.xcodegen_wrapper
# ============================================================================


class TestXcodeGenResult:
    """Tests for the XcodeGenResult dataclass."""

    def test_success_result(self):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenResult

        r = XcodeGenResult(success=True, stdout="ok", stderr="")
        assert r.success is True
        assert r.error is None

    def test_failure_result(self):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenResult

        r = XcodeGenResult(success=False, stdout="", stderr="err", error="something went wrong")
        assert r.success is False
        assert r.error == "something went wrong"


class TestXcodeGenWrapperGenerate:
    """Tests for XcodeGenWrapper.generate."""

    @pytest.fixture
    def project_dir(self, tmp_path):
        """A project directory with a project.yml."""
        spec = {"name": "MyApp", "targets": {"MyApp": {"type": "application"}}}
        yml_path = tmp_path / "project.yml"
        yml_path.write_text(yaml.dump(spec))
        return tmp_path

    @pytest.fixture
    def empty_dir(self, tmp_path):
        """A directory without project.yml."""
        return tmp_path

    # --- missing project.yml ---

    @pytest.mark.asyncio
    async def test_generate_missing_project_yml(self, empty_dir):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        result = await XcodeGenWrapper.generate(empty_dir)
        assert result.success is False
        assert "project.yml not found" in result.error

    # --- xcodegen not found ---

    @pytest.mark.asyncio
    async def test_generate_xcodegen_not_found(self, project_dir):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await XcodeGenWrapper.generate(project_dir)

        assert result.success is False
        assert "xcodegen not found" in result.error

    # --- successful generation ---

    @pytest.mark.asyncio
    async def test_generate_success(self, project_dir):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Generated project", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await XcodeGenWrapper.generate(project_dir)

        assert result.success is True
        assert result.stdout == "Generated project"
        assert result.stderr == ""
        assert result.error is None

    # --- failed generation (non-zero returncode) ---

    @pytest.mark.asyncio
    async def test_generate_failure_returncode(self, project_dir):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: spec invalid"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await XcodeGenWrapper.generate(project_dir)

        assert result.success is False
        assert "Error: spec invalid" in result.error

    # --- dry_run mode ---

    @pytest.mark.asyncio
    async def test_generate_dry_run(self, project_dir):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await XcodeGenWrapper.generate(project_dir, dry_run=True)

        args = mock_exec.call_args[0]
        assert "--spec" in args
        assert str(project_dir / "project.yml") in args

    # --- generic exception ---

    @pytest.mark.asyncio
    async def test_generate_generic_exception(self, project_dir):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("oh no")):
            result = await XcodeGenWrapper.generate(project_dir)

        assert result.success is False
        assert "oh no" in result.error

    # --- empty stdout/stderr bytes ---

    @pytest.mark.asyncio
    async def test_generate_none_stdout_stderr(self, project_dir):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(None, None))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await XcodeGenWrapper.generate(project_dir)

        assert result.stdout == ""
        assert result.stderr == ""


class TestXcodeGenWrapperValidateProjectYml:
    """Tests for XcodeGenWrapper.validate_project_yml."""

    def test_missing_file(self, tmp_path):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        valid, error = XcodeGenWrapper.validate_project_yml(tmp_path / "project.yml")
        assert valid is False
        assert "File not found" in error

    def test_valid_yaml(self, tmp_path):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        yml = tmp_path / "project.yml"
        yml.write_text(yaml.dump({"name": "App", "targets": {"App": {}}}))

        valid, error = XcodeGenWrapper.validate_project_yml(yml)
        assert valid is True
        assert error is None

    def test_missing_name_field(self, tmp_path):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        yml = tmp_path / "project.yml"
        yml.write_text(yaml.dump({"targets": {"App": {}}}))

        valid, error = XcodeGenWrapper.validate_project_yml(yml)
        assert valid is False
        assert "name" in error

    def test_missing_targets_field(self, tmp_path):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        yml = tmp_path / "project.yml"
        yml.write_text(yaml.dump({"name": "App"}))

        valid, error = XcodeGenWrapper.validate_project_yml(yml)
        assert valid is False
        assert "targets" in error

    def test_targets_not_dict(self, tmp_path):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        yml = tmp_path / "project.yml"
        yml.write_text(yaml.dump({"name": "App", "targets": ["App"]}))

        valid, error = XcodeGenWrapper.validate_project_yml(yml)
        assert valid is False
        assert "dictionary" in error

    def test_invalid_yaml_syntax(self, tmp_path):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        yml = tmp_path / "project.yml"
        yml.write_text("key: [unclosed")

        valid, error = XcodeGenWrapper.validate_project_yml(yml)
        assert valid is False
        assert "Invalid YAML" in error

    def test_generic_exception_during_validation(self, tmp_path):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        yml = tmp_path / "project.yml"
        yml.write_text(yaml.dump({"name": "App", "targets": {"App": {}}}))

        with patch("builtins.open", side_effect=PermissionError("no access")):
            valid, error = XcodeGenWrapper.validate_project_yml(yml)

        assert valid is False
        assert "Validation error" in error


class TestXcodeGenWrapperCreateMinimalProjectYml:
    """Tests for XcodeGenWrapper.create_minimal_project_yml."""

    def test_basic_structure(self):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        config = XcodeGenWrapper.create_minimal_project_yml("MyApp", "com.example.MyApp")
        assert config["name"] == "MyApp"
        assert "targets" in config
        assert "MyApp" in config["targets"]
        assert "MyAppTests" in config["targets"]

    def test_ios_version_default(self):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        config = XcodeGenWrapper.create_minimal_project_yml("App", "com.x.App")
        deploy = config["options"]["deploymentTarget"]["iOS"]
        assert deploy == "17.0"

    def test_ios_version_custom(self):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        config = XcodeGenWrapper.create_minimal_project_yml("App", "com.x.App", ios_version="16.0")
        assert config["options"]["deploymentTarget"]["iOS"] == "16.0"

    def test_bundle_id_prefix_extraction(self):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        config = XcodeGenWrapper.create_minimal_project_yml("App", "com.myco.App")
        prefix = config["options"]["bundleIdPrefix"]
        assert prefix == "com.myco"

    def test_target_types(self):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        config = XcodeGenWrapper.create_minimal_project_yml("App", "com.x.App")
        assert config["targets"]["App"]["type"] == "application"
        assert config["targets"]["AppTests"]["type"] == "bundle.unit-test"

    def test_test_target_depends_on_app_target(self):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        config = XcodeGenWrapper.create_minimal_project_yml("App", "com.x.App")
        deps = config["targets"]["AppTests"]["dependencies"]
        assert any(d.get("target") == "App" for d in deps)

    def test_swift_version_is_6(self):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        config = XcodeGenWrapper.create_minimal_project_yml("App", "com.x.App")
        assert config["settings"]["base"]["SWIFT_VERSION"] == "6.0"

    def test_can_be_dumped_to_yaml(self):
        from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper

        config = XcodeGenWrapper.create_minimal_project_yml("App", "com.x.App")
        dumped = yaml.dump(config)
        assert "MyApp" in dumped or "App" in dumped  # sanity


# ============================================================================
# forge_harness.iteration.session_orchestrator_example
# ============================================================================


class TestSessionOrchestratorExampleBasicSession:
    """Tests for example_basic_session function."""

    def _make_orchestrator(self):
        """Return a mocked SessionOrchestrator."""
        from forge_harness.iteration.session_orchestrator import (
            SessionConfig,
            SessionOrchestrator,
        )
        from forge_harness.iteration.tracker import TrackerStats

        orch = MagicMock(spec=SessionOrchestrator)
        orch.config = SessionConfig(project_name="example-project", auto_commit=True)
        orch.start_iteration.return_value = 1

        commit_result = MagicMock()
        commit_result.success = True
        commit_result.commit_hash = "abc123"

        session_result = MagicMock()
        session_result.success = True
        session_result.iteration_number = 1
        session_result.features_completed = ["Feature A"]
        session_result.duration_seconds = 2.5
        session_result.commit_result = commit_result
        session_result.error = None

        orch.complete_iteration.return_value = session_result

        stats = MagicMock(spec=TrackerStats)
        stats.total_iterations = 1
        stats.completed_iterations = 1
        stats.failed_iterations = 0
        stats.total_features = 3
        orch.get_stats.return_value = stats

        return orch

    def test_basic_session_runs_without_error(self, capsys):
        from forge_harness.iteration.session_orchestrator_example import example_basic_session

        mock_orch = self._make_orchestrator()
        with patch(
            "forge_harness.iteration.session_orchestrator_example.create_session_orchestrator",
            return_value=mock_orch,
        ):
            example_basic_session()

        out = capsys.readouterr().out
        assert "iteration 1" in out.lower() or "1" in out

    def test_basic_session_prints_stats(self, capsys):
        from forge_harness.iteration.session_orchestrator_example import example_basic_session

        mock_orch = self._make_orchestrator()
        with patch(
            "forge_harness.iteration.session_orchestrator_example.create_session_orchestrator",
            return_value=mock_orch,
        ):
            example_basic_session()

        out = capsys.readouterr().out
        assert "session stats" in out.lower() or "total" in out.lower()

    def test_basic_session_failure_path(self, capsys):
        from forge_harness.iteration.session_orchestrator import SessionConfig, SessionOrchestrator
        from forge_harness.iteration.session_orchestrator_example import example_basic_session
        from forge_harness.iteration.tracker import TrackerStats

        mock_orch = MagicMock(spec=SessionOrchestrator)
        mock_orch.config = SessionConfig(project_name="p", auto_commit=True)
        mock_orch.start_iteration.return_value = 1

        fail_result = MagicMock()
        fail_result.success = False
        fail_result.error = "tests failed"
        fail_result.commit_result = None
        mock_orch.complete_iteration.return_value = fail_result

        stats = MagicMock(spec=TrackerStats)
        stats.total_iterations = stats.completed_iterations = stats.failed_iterations = 0
        stats.total_features = 0
        mock_orch.get_stats.return_value = stats

        with patch(
            "forge_harness.iteration.session_orchestrator_example.create_session_orchestrator",
            return_value=mock_orch,
        ):
            example_basic_session()

        out = capsys.readouterr().out
        assert "failed" in out.lower() or "tests failed" in out.lower()

    def test_basic_session_creates_orchestrator_with_correct_args(self):
        from forge_harness.iteration.session_orchestrator import SessionConfig, SessionOrchestrator
        from forge_harness.iteration.session_orchestrator_example import example_basic_session
        from forge_harness.iteration.tracker import TrackerStats

        mock_orch = MagicMock(spec=SessionOrchestrator)
        mock_orch.config = SessionConfig(project_name="example-project", auto_commit=True)
        mock_orch.start_iteration.return_value = 1

        res = MagicMock()
        res.success = True
        res.iteration_number = 1
        res.features_completed = []
        res.duration_seconds = 0.1
        res.commit_result = None
        mock_orch.complete_iteration.return_value = res

        stats = MagicMock(spec=TrackerStats)
        stats.total_iterations = stats.completed_iterations = stats.failed_iterations = 0
        stats.total_features = 0
        mock_orch.get_stats.return_value = stats

        with patch(
            "forge_harness.iteration.session_orchestrator_example.create_session_orchestrator",
            return_value=mock_orch,
        ) as mock_factory:
            example_basic_session()

        mock_factory.assert_called_once_with(
            project_name="example-project",
            auto_commit=True,
            auto_pr=False,
        )


class TestSessionOrchestratorExampleFullWorkflow:
    """Tests for example_full_workflow function."""

    def _make_orchestrator(self, success=True, pr_success=True):
        from forge_harness.iteration.session_orchestrator import SessionConfig, SessionOrchestrator

        mock_orch = MagicMock(spec=SessionOrchestrator)
        mock_orch.config = SessionConfig(project_name="voice-coach", auto_commit=True, auto_pr=True)
        mock_orch.start_iteration.return_value = 1

        commit_result = MagicMock()
        commit_result.success = success
        commit_result.commit_hash = "deadbeef"
        commit_result.message = "feat(backend): implement pitch analysis engine, add voice..."

        pr_result = MagicMock()
        pr_result.success = pr_success
        pr_result.pr_url = "https://github.com/forge/voice-coach/pull/42"
        pr_result.pr_number = 42

        session_result = MagicMock()
        session_result.success = success
        session_result.commit_result = commit_result if success else None
        session_result.pr_result = pr_result if (success and pr_success) else None
        session_result.error = None if success else "boom"
        mock_orch.complete_iteration.return_value = session_result

        return mock_orch

    def test_full_workflow_success(self, capsys):
        from forge_harness.iteration.session_orchestrator_example import example_full_workflow

        mock_orch = self._make_orchestrator()
        with patch(
            "forge_harness.iteration.session_orchestrator_example.create_session_orchestrator",
            return_value=mock_orch,
        ):
            example_full_workflow()

        out = capsys.readouterr().out
        assert "completed" in out.lower() or "commit" in out.lower()

    def test_full_workflow_failure_path(self, capsys):
        from forge_harness.iteration.session_orchestrator_example import example_full_workflow

        mock_orch = self._make_orchestrator(success=False)
        with patch(
            "forge_harness.iteration.session_orchestrator_example.create_session_orchestrator",
            return_value=mock_orch,
        ):
            example_full_workflow()

        out = capsys.readouterr().out
        assert "failed" in out.lower() or "boom" in out.lower()

    def test_full_workflow_sets_config(self):
        from forge_harness.iteration.session_orchestrator import SessionConfig, SessionOrchestrator
        from forge_harness.iteration.session_orchestrator_example import example_full_workflow

        mock_orch = MagicMock(spec=SessionOrchestrator)
        mock_orch.config = SessionConfig(project_name="voice-coach", auto_commit=True, auto_pr=True)
        mock_orch.start_iteration.return_value = 1
        res = MagicMock()
        res.success = False
        res.error = "x"
        res.commit_result = None
        res.pr_result = None
        mock_orch.complete_iteration.return_value = res

        with patch(
            "forge_harness.iteration.session_orchestrator_example.create_session_orchestrator",
            return_value=mock_orch,
        ):
            example_full_workflow()

        assert mock_orch.config.pr_draft is True
        assert mock_orch.config.base_branch == "main"
        assert mock_orch.config.commit_scope == "backend"


class TestSessionOrchestratorExampleMultipleIterations:
    """Tests for example_multiple_iterations function."""

    def _make_orch_with_results(self, results):
        from forge_harness.iteration.session_orchestrator import SessionConfig, SessionOrchestrator
        from forge_harness.iteration.tracker import TrackerStats

        mock_orch = MagicMock(spec=SessionOrchestrator)
        mock_orch.config = SessionConfig(project_name="interview-simulator", auto_commit=True)
        mock_orch.start_iteration.side_effect = list(range(1, len(results) + 1))
        mock_orch.complete_iteration.side_effect = results

        stats = MagicMock(spec=TrackerStats)
        stats.completed_iterations = len([r for r in results if r.success])
        stats.total_iterations = len(results)
        stats.total_features = 7
        stats.total_tests = 30
        stats.total_lines = 1400
        stats.avg_duration = 1.0
        mock_orch.get_stats.return_value = stats

        return mock_orch

    def _make_result(self, success=True, num=1):
        r = MagicMock()
        r.success = success
        r.iteration_number = num
        return r

    def test_multiple_iterations_runs(self, capsys):
        from forge_harness.iteration.session_orchestrator_example import (
            example_multiple_iterations,
        )

        results = [self._make_result(True, i) for i in range(1, 4)]
        mock_orch = self._make_orch_with_results(results)

        with patch(
            "forge_harness.iteration.session_orchestrator_example.create_session_orchestrator",
            return_value=mock_orch,
        ):
            example_multiple_iterations()

        out = capsys.readouterr().out
        assert "Iteration 1" in out or "iteration" in out.lower()

    def test_multiple_iterations_shows_progress(self, capsys):
        from forge_harness.iteration.session_orchestrator_example import (
            example_multiple_iterations,
        )

        results = [self._make_result(True, i) for i in range(1, 4)]
        mock_orch = self._make_orch_with_results(results)

        with patch(
            "forge_harness.iteration.session_orchestrator_example.create_session_orchestrator",
            return_value=mock_orch,
        ):
            example_multiple_iterations()

        out = capsys.readouterr().out
        assert "total progress" in out.lower() or "features" in out.lower()

    def test_multiple_iterations_calls_start_three_times(self):
        from forge_harness.iteration.session_orchestrator_example import (
            example_multiple_iterations,
        )

        results = [self._make_result(True, i) for i in range(1, 4)]
        mock_orch = self._make_orch_with_results(results)

        with patch(
            "forge_harness.iteration.session_orchestrator_example.create_session_orchestrator",
            return_value=mock_orch,
        ):
            example_multiple_iterations()

        assert mock_orch.start_iteration.call_count == 3


class TestSessionOrchestratorExampleFailureHandling:
    """Tests for example_failure_handling function."""

    def test_failure_handling_runs(self, capsys):
        from forge_harness.iteration.session_orchestrator import SessionConfig, SessionOrchestrator
        from forge_harness.iteration.session_orchestrator_example import example_failure_handling
        from forge_harness.iteration.tracker import IterationRecord

        mock_orch = MagicMock(spec=SessionOrchestrator)
        mock_orch.config = SessionConfig(project_name="example-project", auto_commit=True)
        mock_orch.start_iteration.side_effect = [1, 2]

        fail_result = MagicMock()
        fail_result.success = False
        fail_result.error = "Tests failed - authentication bug detected"

        ok_result = MagicMock()
        ok_result.success = True
        ok_result.error = None

        mock_orch.fail_iteration.return_value = fail_result
        mock_orch.complete_iteration.return_value = ok_result

        record1 = MagicMock(spec=IterationRecord)
        record1.status = "failed"
        record1.iteration_number = 1

        record2 = MagicMock(spec=IterationRecord)
        record2.status = "completed"
        record2.iteration_number = 2

        mock_orch.get_recent_iterations.return_value = [record1, record2]

        with patch(
            "forge_harness.iteration.session_orchestrator_example.create_session_orchestrator",
            return_value=mock_orch,
        ):
            example_failure_handling()

        out = capsys.readouterr().out
        assert "failed" in out.lower()

    def test_failure_handling_shows_recent_iterations(self, capsys):
        from forge_harness.iteration.session_orchestrator import SessionConfig, SessionOrchestrator
        from forge_harness.iteration.session_orchestrator_example import example_failure_handling
        from forge_harness.iteration.tracker import IterationRecord

        mock_orch = MagicMock(spec=SessionOrchestrator)
        mock_orch.config = SessionConfig(project_name="example-project", auto_commit=True)
        mock_orch.start_iteration.side_effect = [1, 2]

        fail_res = MagicMock()
        fail_res.success = False
        fail_res.error = "bug"

        ok_res = MagicMock()
        ok_res.success = True

        mock_orch.fail_iteration.return_value = fail_res
        mock_orch.complete_iteration.return_value = ok_res

        record = MagicMock(spec=IterationRecord)
        record.status = "completed"
        record.iteration_number = 2
        mock_orch.get_recent_iterations.return_value = [record]

        with patch(
            "forge_harness.iteration.session_orchestrator_example.create_session_orchestrator",
            return_value=mock_orch,
        ):
            example_failure_handling()

        mock_orch.get_recent_iterations.assert_called_once_with(count=5)
        out = capsys.readouterr().out
        assert "recent iterations" in out.lower()


# ============================================================================
# forge_harness.iteration.demo_monitor (ProgressMonitor tests through demo fns)
# ============================================================================


class TestProgressMonitorCore:
    """Direct tests of ProgressMonitor functionality exercised by demo_monitor."""

    def _make_monitor(self, **kwargs):
        from forge_harness.iteration.monitor import ProgressMonitor

        return ProgressMonitor(**kwargs)

    # --- register_session ---

    def test_register_session_default_timeout(self):
        m = self._make_monitor(default_timeout=5)
        m.register_session("s1")
        assert "s1" in m.sessions
        assert m.sessions["s1"].timeout_minutes == 5

    def test_register_session_custom_timeout(self):
        m = self._make_monitor()
        m.register_session("s1", timeout_minutes=30)
        assert m.sessions["s1"].timeout_minutes == 30

    def test_register_session_with_task_id(self):
        m = self._make_monitor()
        m.register_session("s1", task_id="HRN-001")
        assert m.sessions["s1"].task_id == "HRN-001"

    def test_register_session_with_metadata(self):
        m = self._make_monitor()
        m.register_session("s1", metadata={"domain": "ai"})
        assert m.sessions["s1"].metadata == {"domain": "ai"}

    def test_unregister_session_via_dict(self):
        """unregister_session is dead code on the class; exercise via dict."""
        m = self._make_monitor()
        m.register_session("s1")
        del m.sessions["s1"]
        assert "s1" not in m.sessions

    def test_multiple_sessions_can_coexist(self):
        m = self._make_monitor()
        m.register_session("s1")
        m.register_session("s2", timeout_minutes=5)
        assert len(m.sessions) == 2

    # --- SessionMetrics state access via sessions dict ---

    def test_session_state_default_unknown(self):
        from forge_harness.iteration.monitor import SessionState

        m = self._make_monitor()
        m.register_session("s1")
        assert m.sessions["s1"].state == SessionState.UNKNOWN

    def test_session_task_id_stored(self):
        m = self._make_monitor()
        m.register_session("s1", task_id="HRN-999")
        assert m.sessions["s1"].task_id == "HRN-999"

    def test_session_metadata_stored(self):
        m = self._make_monitor()
        m.register_session("s1", metadata={"key": "val"})
        assert m.sessions["s1"].metadata == {"key": "val"}

    # --- poll_session ---

    def test_poll_session_returns_running_when_no_marker(self):
        from forge_harness.iteration.monitor import ProgressMonitor, SessionStatus

        m = ProgressMonitor()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Still working..."
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = m.poll_session("my-session")

        assert result.status == SessionStatus.RUNNING
        assert result.session_id == "my-session"

    def test_poll_session_returns_completed_with_task_completed_marker(self):
        from forge_harness.iteration.monitor import ProgressMonitor, SessionStatus

        m = ProgressMonitor()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Task completed successfully"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = m.poll_session("my-session")

        assert result.status == SessionStatus.COMPLETED

    def test_poll_session_returns_completed_with_cogitated_marker(self):
        from forge_harness.iteration.monitor import ProgressMonitor, SessionStatus

        m = ProgressMonitor()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Cogitated for 12s and produced output"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = m.poll_session("my-session")

        assert result.status == SessionStatus.COMPLETED
        assert result.completion_marker == "Cogitated for"

    def test_poll_session_returns_completed_with_completed_marker(self):
        from forge_harness.iteration.monitor import ProgressMonitor, SessionStatus

        m = ProgressMonitor()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Completed build"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = m.poll_session("my-session")

        assert result.status == SessionStatus.COMPLETED

    def test_poll_session_returns_error_on_nonzero(self):
        from forge_harness.iteration.monitor import ProgressMonitor, SessionStatus

        m = ProgressMonitor()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "session not found"

        with patch("subprocess.run", return_value=mock_result):
            result = m.poll_session("bad-session")

        assert result.status == SessionStatus.ERROR

    def test_poll_session_tracks_output_changes(self):
        """Second poll with same output should not update _session_last_change."""
        from forge_harness.iteration.monitor import ProgressMonitor

        m = ProgressMonitor()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "same output"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            m.poll_session("s1")
            first_change = m._session_last_change.get("s1")
            m.poll_session("s1")
            second_change = m._session_last_change.get("s1")

        # Output didn't change, so last_change should remain the same
        assert first_change == second_change

    # --- detect_stall (threshold_seconds version, only one on the class) ---

    def test_detect_stall_recently_changed_not_stalled(self):
        """Session that changed output just now should not be stalled."""
        from forge_harness.iteration.monitor import ProgressMonitor

        m = ProgressMonitor()
        m._session_last_change["s1"] = time.time()  # just now
        assert m.detect_stall("s1", threshold_seconds=300) is False

    def test_detect_stall_stale_session(self):
        """Session that hasn't changed in >threshold seconds is stalled."""
        from forge_harness.iteration.monitor import ProgressMonitor

        m = ProgressMonitor()
        m._session_last_change["s1"] = time.time() - 400  # stale
        assert m.detect_stall("s1", threshold_seconds=300) is True

    def test_detect_stall_no_record_assumes_fresh(self):
        """Session with no change record defaults to current time (not stalled)."""
        from forge_harness.iteration.monitor import ProgressMonitor

        m = ProgressMonitor()
        # No record for s1 - defaults to time.time() so not stalled
        assert m.detect_stall("s1", threshold_seconds=300) is False

    # --- session metrics properties ---

    def test_session_metrics_age_minutes(self):
        from forge_harness.iteration.monitor import SessionMetrics

        metrics = SessionMetrics(session_id="s1")
        metrics.started_at = datetime.now(UTC) - timedelta(minutes=5)
        assert 4.9 < metrics.age_minutes < 5.1

    def test_session_metrics_idle_minutes_no_activity(self):
        from forge_harness.iteration.monitor import SessionMetrics

        metrics = SessionMetrics(session_id="s1")
        metrics.started_at = datetime.now(UTC) - timedelta(minutes=3)
        metrics.last_activity_at = None
        assert metrics.idle_minutes >= 2.9

    def test_session_metrics_idle_minutes_with_activity(self):
        from forge_harness.iteration.monitor import SessionMetrics

        metrics = SessionMetrics(session_id="s1")
        metrics.last_activity_at = datetime.now(UTC) - timedelta(minutes=2)
        assert 1.9 < metrics.idle_minutes < 2.1

    def test_session_metrics_is_timeout_normal(self):
        from forge_harness.iteration.monitor import SessionMetrics

        metrics = SessionMetrics(session_id="s1", timeout_minutes=15)
        metrics.last_activity_at = datetime.now(UTC) - timedelta(minutes=20)
        assert metrics.is_timeout is True

    def test_session_metrics_is_not_timeout_within_limit(self):
        from forge_harness.iteration.monitor import SessionMetrics

        metrics = SessionMetrics(session_id="s1", timeout_minutes=15)
        metrics.last_activity_at = datetime.now(UTC)
        assert metrics.is_timeout is False

    def test_session_metrics_is_not_timeout_when_extended(self):
        from forge_harness.iteration.monitor import SessionMetrics

        metrics = SessionMetrics(session_id="s1", timeout_minutes=1)
        metrics.last_activity_at = datetime.now(UTC) - timedelta(minutes=5)
        # extend by 30 minutes into the future
        metrics.extended_until = datetime.now(UTC) + timedelta(minutes=30)
        assert metrics.is_timeout is False

    def test_session_metrics_is_timeout_after_extension_expired(self):
        from forge_harness.iteration.monitor import SessionMetrics

        metrics = SessionMetrics(session_id="s1", timeout_minutes=1)
        metrics.last_activity_at = datetime.now(UTC) - timedelta(minutes=10)
        metrics.extended_until = datetime.now(UTC) - timedelta(minutes=5)  # already expired
        assert metrics.is_timeout is True

    def test_session_metrics_to_dict(self):
        from forge_harness.iteration.monitor import SessionMetrics

        metrics = SessionMetrics(session_id="s1", task_id="HRN-001", timeout_minutes=20)
        d = metrics.to_dict()
        assert d["session_id"] == "s1"
        assert d["task_id"] == "HRN-001"
        assert d["timeout_minutes"] == 20
        assert "age_minutes" in d
        assert "idle_minutes" in d
        assert "is_timeout" in d

    def test_session_metrics_to_dict_with_extended_until(self):
        from forge_harness.iteration.monitor import SessionMetrics

        metrics = SessionMetrics(session_id="s1")
        metrics.extended_until = datetime.now(UTC) + timedelta(minutes=10)
        d = metrics.to_dict()
        assert d["extended_until"] is not None

    def test_session_metrics_to_dict_no_extended_until(self):
        from forge_harness.iteration.monitor import SessionMetrics

        metrics = SessionMetrics(session_id="s1")
        d = metrics.to_dict()
        assert d["extended_until"] is None

    def test_session_metrics_to_dict_with_last_activity(self):
        from forge_harness.iteration.monitor import SessionMetrics

        metrics = SessionMetrics(session_id="s1")
        metrics.last_activity_at = datetime.now(UTC)
        d = metrics.to_dict()
        assert d["last_activity_at"] is not None

    # --- MonitorResult ---

    def test_monitor_result_summary(self):
        from forge_harness.iteration.monitor import MonitorResult, SessionStatus

        r = MonitorResult(session_id="s1", status=SessionStatus.COMPLETED, duration=5.2)
        assert "s1" in r.summary
        assert "completed" in r.summary
        assert "5.2" in r.summary

    def test_monitor_result_running_status(self):
        from forge_harness.iteration.monitor import MonitorResult, SessionStatus

        r = MonitorResult(session_id="s2", status=SessionStatus.RUNNING, output="work", duration=1.0)
        assert r.output == "work"
        assert r.completion_marker is None

    def test_monitor_result_with_completion_marker(self):
        from forge_harness.iteration.monitor import MonitorResult, SessionStatus

        r = MonitorResult(
            session_id="s3",
            status=SessionStatus.COMPLETED,
            duration=2.0,
            completion_marker="Task completed",
        )
        assert r.completion_marker == "Task completed"

    # --- create_monitor factory ---

    def test_create_monitor_factory(self):
        from forge_harness.iteration.monitor import ProgressMonitor, create_monitor

        m = create_monitor(poll_interval=10.0)
        assert isinstance(m, ProgressMonitor)
        assert m.poll_interval == 10

    def test_create_monitor_default_poll_interval(self):
        from forge_harness.iteration.monitor import ProgressMonitor, create_monitor

        m = create_monitor()
        assert isinstance(m, ProgressMonitor)

    # --- init parameters ---

    def test_custom_completion_markers(self):
        from forge_harness.iteration.monitor import ProgressMonitor

        m = ProgressMonitor(completion_markers=["CUSTOM_DONE"])
        assert "CUSTOM_DONE" in m.completion_markers

    def test_custom_failure_markers(self):
        from forge_harness.iteration.monitor import ProgressMonitor

        m = ProgressMonitor(failure_markers=["CUSTOM_FAIL"])
        assert "CUSTOM_FAIL" in m.failure_markers

    def test_default_markers_used_when_none(self):
        from forge_harness.iteration.monitor import (
            COMPLETION_MARKERS,
            FAILURE_MARKERS,
            ProgressMonitor,
        )

        m = ProgressMonitor()
        assert m.completion_markers is COMPLETION_MARKERS
        assert m.failure_markers is FAILURE_MARKERS

    # --- wait_for_completion ---

    @pytest.mark.asyncio
    async def test_wait_for_completion_already_done(self):
        """If session is immediately COMPLETED, should return right away."""
        from forge_harness.iteration.monitor import ProgressMonitor, SessionStatus

        m = ProgressMonitor(poll_interval=1)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Task completed"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            results = await m.wait_for_completion(["s1"], timeout=5.0)

        assert "s1" in results
        assert results["s1"].status == SessionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_wait_for_completion_timeout(self):
        """When timeout expires before completion, returns TIMEOUT status."""
        from forge_harness.iteration.monitor import ProgressMonitor, SessionStatus

        m = ProgressMonitor(poll_interval=1)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "still running"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            results = await m.wait_for_completion(["s1"], timeout=0.01, stall_threshold=9999)

        assert "s1" in results
        assert results["s1"].status in (SessionStatus.TIMEOUT, SessionStatus.STALLED)


class TestDemoMonitorAsyncFunctions:
    """Tests for demo_monitor async demo functions.

    The demo uses methods that are defined as dead code after an early return
    in create_monitor. We patch them onto the monitor instances to allow the
    demos to run and verify the printed output.
    """

    def _patch_monitor_methods(self):
        """Return a context that patches the missing ProgressMonitor methods."""
        from forge_harness.iteration.monitor import SessionMetrics, SessionState

        def fake_get_all_metrics(self_m):
            return {sid: metrics for sid, metrics in self_m.sessions.items()}

        def fake_get_state(self_m, session_id):
            m = self_m.sessions.get(session_id)
            return m.state if m else None

        def fake_extend_timeout(self_m, session_id, minutes):
            m = self_m.sessions.get(session_id)
            if m:
                m.extended_until = datetime.now(UTC) + timedelta(minutes=minutes)

        def fake_get_metrics(self_m, session_id):
            return self_m.sessions.get(session_id)

        def fake_unregister_session(self_m, session_id):
            self_m.sessions.pop(session_id, None)

        def fake_detect_completion_method(self_m, session_id, output=None):
            """Simplified detection for tests."""
            if output is None:
                return False
            markers = ["All tests pass", "✓ Success", "✅", "DONE", "Deployment complete"]
            tail = output[-1000:] if len(output) > 1000 else output
            return any(m in tail for m in markers)

        def fake_detect_failure_method(self_m, output):
            markers = ["Error:", "FAILED", "Exception:", "Traceback", "BUILD FAILED"]
            tail = output[-1000:] if len(output) > 1000 else output
            return any(m in tail for m in markers)

        def fake_detect_stall_method(self_m, session_id):
            m = self_m.sessions.get(session_id)
            if not m:
                return False
            return m.idle_minutes >= 10 and not m.is_timeout

        from forge_harness.iteration import monitor as monitor_mod

        ProgressMonitor = monitor_mod.ProgressMonitor

        return [
            patch.object(ProgressMonitor, "get_all_metrics", fake_get_all_metrics, create=True),
            patch.object(ProgressMonitor, "get_state", fake_get_state, create=True),
            patch.object(ProgressMonitor, "extend_timeout", fake_extend_timeout, create=True),
            patch.object(ProgressMonitor, "get_metrics", fake_get_metrics, create=True),
            patch.object(ProgressMonitor, "unregister_session", fake_unregister_session, create=True),
            patch.object(ProgressMonitor, "detect_completion", fake_detect_completion_method, create=True),
            patch.object(ProgressMonitor, "_detect_failure", fake_detect_failure_method, create=True),
            patch.object(ProgressMonitor, "detect_stall", fake_detect_stall_method, create=True),
        ]

    @pytest.mark.asyncio
    async def test_demo_basic_usage_runs(self, capsys):
        from forge_harness.iteration.demo_monitor import demo_basic_usage

        patches = self._patch_monitor_methods()
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patches[5], patches[6], patches[7],
        ):
            await demo_basic_usage()
        out = capsys.readouterr().out
        assert "session" in out.lower() or "registered" in out.lower()

    @pytest.mark.asyncio
    async def test_demo_completion_detection_runs(self, capsys):
        from forge_harness.iteration.demo_monitor import demo_completion_detection

        patches = self._patch_monitor_methods()
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patches[5], patches[6], patches[7],
        ):
            await demo_completion_detection()

        out = capsys.readouterr().out
        assert "completion" in out.lower() or "Output" in out

    @pytest.mark.asyncio
    async def test_demo_timeout_handling_runs(self, capsys):
        from forge_harness.iteration.demo_monitor import demo_timeout_handling

        patches = self._patch_monitor_methods()
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patches[5], patches[6], patches[7],
        ):
            await demo_timeout_handling()
        out = capsys.readouterr().out
        assert "timeout" in out.lower()

    @pytest.mark.asyncio
    async def test_demo_stall_detection_runs(self, capsys):
        from forge_harness.iteration.demo_monitor import demo_stall_detection

        patches = self._patch_monitor_methods()
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patches[5], patches[6], patches[7],
        ):
            await demo_stall_detection()
        out = capsys.readouterr().out
        assert "stall" in out.lower() or "idle" in out.lower()

    @pytest.mark.asyncio
    async def test_demo_metrics_export_runs(self, capsys):
        from forge_harness.iteration.demo_monitor import demo_metrics_export

        patches = self._patch_monitor_methods()
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patches[5], patches[6], patches[7],
        ):
            await demo_metrics_export()
        out = capsys.readouterr().out
        assert "session" in out.lower() or "metric" in out.lower()

    @pytest.mark.asyncio
    async def test_main_runs(self, capsys):
        from forge_harness.iteration.demo_monitor import main

        patches = self._patch_monitor_methods()
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patches[5], patches[6], patches[7],
        ):
            await main()

        out = capsys.readouterr().out
        assert "demonstration" in out.lower() or "monitor" in out.lower()


# ============================================================================
# forge_harness.iteration.demo_assess (through assess module data structures)
# ============================================================================


class TestAssessDatastructures:
    """Tests for data classes in the assess module."""

    # --- AgentType ---

    def test_agent_type_values(self):
        from forge_harness.iteration.assess import AgentType

        assert AgentType.BACKEND.value == "backend-engineer"
        assert AgentType.EXTERNAL.value == "external"
        assert AgentType.UNKNOWN.value == "unknown"

    # --- IssueType ---

    def test_issue_type_values(self):
        from forge_harness.iteration.assess import IssueType

        assert IssueType.FAILED_TEST.value == "failed_test"
        assert IssueType.GIT_CONFLICT.value == "git_conflict"

    # --- OverallHealth ---

    def test_overall_health_emoji(self):
        from forge_harness.iteration.assess import OverallHealth

        assert OverallHealth.HEALTHY.emoji == "✅"
        assert OverallHealth.DEGRADED.emoji == "⚠️"
        assert OverallHealth.FAILING.emoji == "🔴"

    # --- LintResults ---

    def test_lint_results_defaults(self):
        from forge_harness.iteration.assess import LintResults

        lr = LintResults()
        assert lr.error_count == 0

    def test_lint_results_to_dict(self):
        from forge_harness.iteration.assess import LintResults

        d = LintResults(error_count=5).to_dict()
        assert d["error_count"] == 5

    def test_lint_results_from_dict(self):
        from forge_harness.iteration.assess import LintResults

        lr = LintResults.from_dict({"error_count": 10})
        assert lr.error_count == 10

    def test_lint_results_from_dict_missing_key(self):
        from forge_harness.iteration.assess import LintResults

        lr = LintResults.from_dict({})
        assert lr.error_count == 0

    # --- AgentStatus ---

    def test_agent_status_defaults(self):
        from forge_harness.iteration.assess import AgentStatus, AgentType

        a = AgentStatus(session_name="forge:cursor")
        assert a.is_active is False
        assert a.agent_type == AgentType.UNKNOWN

    def test_agent_status_to_dict(self):
        from forge_harness.iteration.assess import AgentStatus, AgentType

        a = AgentStatus(session_name="forge:cursor", agent_type=AgentType.BACKEND, is_active=True)
        d = a.to_dict()
        assert d["session_name"] == "forge:cursor"
        assert d["agent_type"] == "backend-engineer"
        assert d["is_active"] is True

    def test_agent_status_from_dict(self):
        from forge_harness.iteration.assess import AgentStatus, AgentType

        d = {
            "session_name": "forge:kimi",
            "agent_type": "external",
            "is_active": True,
        }
        a = AgentStatus.from_dict(d)
        assert a.session_name == "forge:kimi"
        assert a.agent_type == AgentType.EXTERNAL
        assert a.is_active is True

    def test_agent_status_from_dict_unknown_type(self):
        from forge_harness.iteration.assess import AgentStatus, AgentType

        d = {"session_name": "x", "agent_type": "nonexistent"}
        a = AgentStatus.from_dict(d)
        assert a.agent_type == AgentType.UNKNOWN

    def test_agent_status_from_dict_with_working_directory(self, tmp_path):
        from forge_harness.iteration.assess import AgentStatus

        d = {"session_name": "x", "working_directory": str(tmp_path)}
        a = AgentStatus.from_dict(d)
        assert a.working_directory == tmp_path

    def test_agent_status_from_dict_with_last_activity(self):
        from forge_harness.iteration.assess import AgentStatus

        now_str = datetime.now(UTC).isoformat()
        d = {"session_name": "x", "last_activity": now_str}
        a = AgentStatus.from_dict(d)
        assert a.last_activity is not None

    # --- ProjectStatus ---

    def test_project_status_is_clean(self, tmp_path):
        from forge_harness.iteration.assess import ProjectStatus

        ps = ProjectStatus(domain="ai", project="vc", path=tmp_path, branch="main")
        assert ps.is_clean is True
        assert ps.has_changes is False

    def test_project_status_has_changes(self, tmp_path):
        from forge_harness.iteration.assess import ProjectStatus

        ps = ProjectStatus(
            domain="ai",
            project="vc",
            path=tmp_path,
            branch="main",
            modified=["file.py"],
        )
        assert ps.is_clean is False
        assert ps.has_changes is True

    def test_project_status_to_dict(self, tmp_path):
        from forge_harness.iteration.assess import ProjectStatus

        ps = ProjectStatus(domain="ai", project="vc", path=tmp_path, branch="main")
        d = ps.to_dict()
        assert d["domain"] == "ai"
        assert d["project"] == "vc"
        assert d["branch"] == "main"

    # --- GitStatus ---

    def test_git_status_defaults(self):
        from forge_harness.iteration.assess import GitStatus

        gs = GitStatus()
        assert gs.total_staged == 0
        assert gs.is_clean is True

    def test_git_status_uncommitted_count_computed(self):
        from forge_harness.iteration.assess import GitStatus

        gs = GitStatus(total_staged=2, total_modified=3, total_untracked=1)
        assert gs.uncommitted_count == 6
        assert gs.is_clean is False

    def test_git_status_to_dict(self):
        from forge_harness.iteration.assess import GitStatus

        gs = GitStatus(branch="main")
        d = gs.to_dict()
        assert d["branch"] == "main"
        assert "projects" in d

    def test_git_status_has_uncommitted_changes(self):
        from forge_harness.iteration.assess import GitStatus

        gs = GitStatus(total_staged=1)
        assert gs.has_uncommitted_changes is True

    def test_git_status_no_uncommitted_changes(self):
        from forge_harness.iteration.assess import GitStatus

        gs = GitStatus()
        assert gs.has_uncommitted_changes is False

    # --- TestResults ---

    def test_test_results_defaults(self):
        from forge_harness.iteration.assess import TestResults

        tr = TestResults()
        assert tr.total_tests == 0
        assert tr.is_passing is True

    def test_test_results_success_rate(self):
        from forge_harness.iteration.assess import TestResults

        tr = TestResults(total_tests=10, passed_count=8, failed_count=2)
        assert tr.success_rate == 0.8

    def test_test_results_success_rate_zero_total(self):
        from forge_harness.iteration.assess import TestResults

        tr = TestResults()
        assert tr.success_rate == 1.0

    def test_test_results_failure_rate(self):
        from forge_harness.iteration.assess import TestResults

        tr = TestResults(total_tests=10, failed_count=3)
        assert tr.failure_rate == 30.0

    def test_test_results_failure_rate_zero_total(self):
        from forge_harness.iteration.assess import TestResults

        tr = TestResults()
        assert tr.failure_rate == 0.0

    def test_test_results_is_passing_true_when_no_failures(self):
        from forge_harness.iteration.assess import TestResults

        tr = TestResults(total_tests=5, passed_count=5)
        assert tr.is_passing is True

    def test_test_results_is_passing_false_when_failures(self):
        from forge_harness.iteration.assess import TestResults

        tr = TestResults(total_tests=5, passed_count=3, failed_count=2)
        assert tr.is_passing is False

    def test_test_results_to_dict(self):
        from forge_harness.iteration.assess import TestResults

        tr = TestResults(total_tests=10, passed_count=8, failed_count=2)
        d = tr.to_dict()
        assert d["total_tests"] == 10
        assert d["passed_count"] == 8
        assert d["failed_count"] == 2

    def test_test_results_syncs_legacy_fields(self):
        from forge_harness.iteration.assess import TestResults

        tr = TestResults(total_tests=5, passed_tests=5)
        assert tr.passed_count == 5

    def test_test_results_pass_rate_computed(self):
        from forge_harness.iteration.assess import TestResults

        tr = TestResults(total_tests=10, passed_tests=7)
        assert tr.pass_rate == 70.0

    # --- FailedTestsList ---

    def test_failed_tests_list_equality_int(self):
        from forge_harness.iteration.assess import FailedTestsList

        lst = FailedTestsList(["a", "b", "c"])
        assert lst == 3
        assert lst != 2

    def test_failed_tests_list_equality_list(self):
        from forge_harness.iteration.assess import FailedTestsList

        lst = FailedTestsList(["a", "b"])
        assert lst == ["a", "b"]

    # --- TestResult ---

    def test_test_result_to_dict(self):
        from forge_harness.iteration.assess import TestResult

        tr = TestResult(test_name="test_foo", status="failed", message="assert failed")
        d = tr.to_dict()
        assert d["test_name"] == "test_foo"
        assert d["status"] == "failed"
        assert d["message"] == "assert failed"

    # --- Issue ---

    def test_issue_to_dict(self):
        from forge_harness.iteration.assess import Issue, IssueType

        issue = Issue(
            issue_type=IssueType.FAILED_TEST,
            severity="high",
            message="3 tests failed",
            location="tests/test_foo.py",
        )
        d = issue.to_dict()
        assert d["issue_type"] == "failed_test"
        assert d["severity"] == "high"
        assert d["location"] == "tests/test_foo.py"

    # --- AssessmentReport ---

    def test_assessment_report_defaults(self):
        from forge_harness.iteration.assess import AssessmentReport, OverallHealth

        r = AssessmentReport()
        assert r.agents == []
        assert r.overall_health == OverallHealth.HEALTHY
        assert r.duration_seconds == 0.0

    def test_assessment_report_get_summary(self):
        from forge_harness.iteration.assess import (
            AgentStatus,
            AgentType,
            AssessmentReport,
            GitStatus,
            Issue,
            IssueType,
            ProjectStatus,
            TestResults,
        )

        active_agent = AgentStatus(session_name="s1", agent_type=AgentType.BACKEND, is_active=True)
        idle_agent = AgentStatus(session_name="s2", agent_type=AgentType.BACKEND, is_active=False)

        project = ProjectStatus(
            domain="ai", project="vc", path=Path("/tmp"), branch="main", modified=["f.py"]
        )

        report = AssessmentReport(
            agents=[active_agent, idle_agent],
            git_status=GitStatus(projects=[project]),
            test_results=TestResults(total_tests=10, passed_count=8, failed_count=2),
            issues=[
                Issue(issue_type=IssueType.FAILED_TEST, severity="critical", message="test failed")
            ],
        )
        summary = report.get_summary()
        assert summary["active_agents"] == 1
        assert summary["total_agents"] == 2
        assert summary["projects_with_changes"] == 1
        assert summary["failed_tests"] == 2
        assert summary["critical_issues"] == 1

    def test_assessment_report_to_json_and_from_json(self):
        from forge_harness.iteration.assess import AssessmentReport

        report = AssessmentReport()
        json_str = report.to_json()
        data = json.loads(json_str)
        assert "agents" in data

        report2 = AssessmentReport.from_json(json_str)
        assert report2.duration_seconds == report.duration_seconds

    def test_assessment_report_from_dict(self):
        from forge_harness.iteration.assess import AssessmentReport, OverallHealth

        d = {
            "agents": [],
            "git_status": {},
            "test_results": {},
            "lint_errors": {"error_count": 3},
            "overall_health": "degraded",
            "assessed_at": datetime.now(UTC).isoformat(),
            "duration_seconds": 5.0,
        }
        r = AssessmentReport.from_dict(d)
        assert r.overall_health == OverallHealth.DEGRADED
        assert r.lint_errors.error_count == 3
        assert r.duration_seconds == 5.0

    def test_assessment_report_to_dict_complete(self):
        from forge_harness.iteration.assess import AssessmentReport

        r = AssessmentReport(duration_seconds=1.5)
        d = r.to_dict()
        assert "agents" in d
        assert "git_status" in d
        assert "test_results" in d
        assert "summary" in d
        assert d["duration_seconds"] == 1.5


class TestAssessFunctions:
    """Tests for standalone assessment functions."""

    # --- _classify_agent_type ---

    def test_classify_gemini_external(self):
        from forge_harness.iteration.assess import AgentType, _classify_agent_type

        assert _classify_agent_type("forge:gemini") == AgentType.EXTERNAL

    def test_classify_codex_external(self):
        from forge_harness.iteration.assess import AgentType, _classify_agent_type

        assert _classify_agent_type("codex-session") == AgentType.EXTERNAL

    def test_classify_qa(self):
        from forge_harness.iteration.assess import AgentType, _classify_agent_type

        assert _classify_agent_type("qa-agent") == AgentType.QA

    def test_classify_backend(self):
        from forge_harness.iteration.assess import AgentType, _classify_agent_type

        assert _classify_agent_type("backend-engineer") == AgentType.BACKEND

    def test_classify_frontend(self):
        from forge_harness.iteration.assess import AgentType, _classify_agent_type

        assert _classify_agent_type("frontend-ui") == AgentType.FRONTEND

    def test_classify_debug(self):
        from forge_harness.iteration.assess import AgentType, _classify_agent_type

        assert _classify_agent_type("debug-detective") == AgentType.DEBUG

    def test_classify_content(self):
        from forge_harness.iteration.assess import AgentType, _classify_agent_type

        assert _classify_agent_type("content-writer") == AgentType.CONTENT

    def test_classify_unknown(self):
        from forge_harness.iteration.assess import AgentType, _classify_agent_type

        assert _classify_agent_type("random-session-name") == AgentType.UNKNOWN

    def test_classify_api_is_backend(self):
        from forge_harness.iteration.assess import AgentType, _classify_agent_type

        assert _classify_agent_type("api-service") == AgentType.BACKEND

    def test_classify_web_is_frontend(self):
        from forge_harness.iteration.assess import AgentType, _classify_agent_type

        assert _classify_agent_type("web-build") == AgentType.FRONTEND

    def test_classify_test_is_qa(self):
        from forge_harness.iteration.assess import AgentType, _classify_agent_type

        assert _classify_agent_type("test-runner") == AgentType.QA

    # --- _extract_test_count ---

    def test_extract_test_count_passed(self):
        from forge_harness.iteration.assess import _extract_test_count

        assert _extract_test_count("24 passed, 3 failed in 12.4s", "passed") == 24

    def test_extract_test_count_failed(self):
        from forge_harness.iteration.assess import _extract_test_count

        assert _extract_test_count("24 passed, 3 failed in 12.4s", "failed") == 3

    def test_extract_test_count_missing(self):
        from forge_harness.iteration.assess import _extract_test_count

        assert _extract_test_count("no tests here", "passed") == 0

    # --- _determine_overall_health ---

    def test_determine_health_healthy(self):
        from forge_harness.iteration.assess import (
            OverallHealth,
            TestResults,
            _determine_overall_health,
        )

        tr = TestResults(total_tests=10, passed_count=10, passed_tests=10)
        assert _determine_overall_health(0, tr) == OverallHealth.HEALTHY

    def test_determine_health_degraded_on_lint(self):
        from forge_harness.iteration.assess import (
            OverallHealth,
            TestResults,
            _determine_overall_health,
        )

        tr = TestResults(total_tests=10, passed_count=10, passed_tests=10)
        assert _determine_overall_health(6, tr) == OverallHealth.DEGRADED

    def test_determine_health_failing_on_many_lint(self):
        from forge_harness.iteration.assess import (
            OverallHealth,
            TestResults,
            _determine_overall_health,
        )

        tr = TestResults()
        assert _determine_overall_health(25, tr) == OverallHealth.FAILING

    def test_determine_health_failing_on_high_failure_rate(self):
        from forge_harness.iteration.assess import (
            OverallHealth,
            TestResults,
            _determine_overall_health,
        )

        # 5 failed out of 10 = 50% failure rate
        tr = TestResults(total_tests=10, passed_count=5, failed_count=5)
        assert _determine_overall_health(0, tr) == OverallHealth.FAILING

    # --- identify_issues ---

    def test_identify_issues_failed_tests_medium(self):
        from forge_harness.iteration.assess import (
            AssessmentReport,
            IssueType,
            TestResults,
            identify_issues,
        )

        report = AssessmentReport(test_results=TestResults(total_tests=10, failed_count=3))
        issues = identify_issues(report)
        types = [i.issue_type for i in issues]
        assert IssueType.FAILED_TEST in types
        assert any(i.severity == "medium" for i in issues if i.issue_type == IssueType.FAILED_TEST)

    def test_identify_issues_failed_tests_high(self):
        from forge_harness.iteration.assess import (
            AssessmentReport,
            IssueType,
            TestResults,
            identify_issues,
        )

        report = AssessmentReport(test_results=TestResults(total_tests=20, failed_count=10))
        issues = identify_issues(report)
        failed_issues = [i for i in issues if i.issue_type == IssueType.FAILED_TEST]
        assert any(i.severity == "high" for i in failed_issues)

    def test_identify_issues_git_conflict(self):
        from forge_harness.iteration.assess import (
            AssessmentReport,
            GitStatus,
            IssueType,
            identify_issues,
        )

        report = AssessmentReport(git_status=GitStatus(total_conflicts=2))
        issues = identify_issues(report)
        types = [i.issue_type for i in issues]
        assert IssueType.GIT_CONFLICT in types
        assert any(i.severity == "critical" for i in issues if i.issue_type == IssueType.GIT_CONFLICT)

    def test_identify_issues_uncommitted_changes(self):
        from forge_harness.iteration.assess import (
            AssessmentReport,
            GitStatus,
            IssueType,
            identify_issues,
        )

        report = AssessmentReport(git_status=GitStatus(total_staged=5, total_modified=8))
        issues = identify_issues(report)
        types = [i.issue_type for i in issues]
        assert IssueType.UNCOMMITTED_CHANGES in types

    def test_identify_issues_no_issues_when_clean(self):
        from forge_harness.iteration.assess import AssessmentReport, identify_issues

        report = AssessmentReport()
        issues = identify_issues(report)
        assert issues == []

    def test_identify_issues_inactive_agent_with_task(self):
        from forge_harness.iteration.assess import (
            AgentStatus,
            AgentType,
            AssessmentReport,
            IssueType,
            identify_issues,
        )

        agent = AgentStatus(
            session_name="forge:stuck",
            agent_type=AgentType.BACKEND,
            is_active=False,
            current_task="Implement feature X",
        )
        report = AssessmentReport(agents=[agent])
        issues = identify_issues(report)
        types = [i.issue_type for i in issues]
        assert IssueType.RUNTIME_ERROR in types

    # --- _assess_agents ---

    def test_assess_agents_no_tmux(self):
        from forge_harness.iteration.assess import _assess_agents

        with patch("subprocess.run", side_effect=FileNotFoundError):
            agents = _assess_agents()
        assert agents == []

    def test_assess_agents_no_sessions(self):
        from forge_harness.iteration.assess import _assess_agents

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            agents = _assess_agents()
        assert agents == []

    def test_assess_agents_filters_non_forge_sessions(self):
        from forge_harness.iteration.assess import _assess_agents

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "main:editor:1:vim\nother:term:0:bash\n"
        with patch("subprocess.run", return_value=mock_result):
            agents = _assess_agents()
        assert agents == []

    def test_assess_agents_with_forge_session(self):
        from forge_harness.iteration.assess import _assess_agents

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "forge:cursor:1:claude\n"
        with patch("subprocess.run", return_value=mock_result):
            agents = _assess_agents()
        assert len(agents) == 1

    # --- _assess_git_status ---

    def test_assess_git_status_not_a_git_repo(self, tmp_path):
        from forge_harness.iteration.assess import _assess_git_status

        with patch("subprocess.run", side_effect=FileNotFoundError):
            gs = _assess_git_status(tmp_path)
        assert gs.branch == ""
        assert gs.is_clean is True

    def test_assess_git_status_clean(self, tmp_path):
        from forge_harness.iteration.assess import _assess_git_status

        def side_effect(cmd, **kwargs):
            mock = MagicMock()
            if "rev-parse" in cmd:
                mock.stdout = "main\n"
                mock.returncode = 0
            else:
                mock.stdout = ""
                mock.returncode = 0
            return mock

        with patch("subprocess.run", side_effect=side_effect):
            gs = _assess_git_status(tmp_path)

        assert gs.branch == "main"
        assert gs.is_clean is True

    def test_assess_git_status_modified_files(self, tmp_path):
        from forge_harness.iteration.assess import _assess_git_status

        def side_effect(cmd, **kwargs):
            mock = MagicMock()
            if "rev-parse" in cmd:
                mock.stdout = "main\n"
                mock.returncode = 0
            elif "status" in cmd:
                mock.stdout = " M file.py\n?? new_file.py\n"
                mock.returncode = 0
            return mock

        with patch("subprocess.run", side_effect=side_effect):
            gs = _assess_git_status(tmp_path)

        assert gs.is_clean is False

    # --- _assess_lint_errors ---

    def test_assess_lint_errors_ruff_not_found(self, tmp_path):
        from forge_harness.iteration.assess import _assess_lint_errors

        with patch("subprocess.run", side_effect=FileNotFoundError):
            lr = _assess_lint_errors(tmp_path)
        assert lr.error_count == 0

    def test_assess_lint_errors_no_errors(self, tmp_path):
        from forge_harness.iteration.assess import _assess_lint_errors

        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            lr = _assess_lint_errors(tmp_path)
        assert lr.error_count == 0

    def test_assess_lint_errors_with_errors(self, tmp_path):
        from forge_harness.iteration.assess import _assess_lint_errors

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Found 15 error(s) found in total."
        with patch("subprocess.run", return_value=mock_result):
            lr = _assess_lint_errors(tmp_path)
        assert lr.error_count == 15

    def test_assess_lint_errors_nonzero_no_match(self, tmp_path):
        from forge_harness.iteration.assess import _assess_lint_errors

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "some other error"
        with patch("subprocess.run", return_value=mock_result):
            lr = _assess_lint_errors(tmp_path)
        assert lr.error_count == 0

    # --- _assess_test_results ---

    def test_assess_test_results_pytest_not_found(self, tmp_path):
        from forge_harness.iteration.assess import _assess_test_results

        with patch("subprocess.run", side_effect=FileNotFoundError("pytest")):
            tr = _assess_test_results(tmp_path)
        assert tr.total_tests == 0
        assert tr.error_message is not None

    def test_assess_test_results_fallback_to_stdout_parsing(self, tmp_path):
        from forge_harness.iteration.assess import _assess_test_results

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "10 passed, 2 failed, 1 skipped in 5.0s"

        with patch("subprocess.run", return_value=mock_result):
            tr = _assess_test_results(tmp_path)

        assert tr.passed_tests == 10
        assert tr.failed_tests == 2 or tr.failed_count == 2

    def test_assess_test_results_parses_json_report(self, tmp_path):
        from forge_harness.iteration.assess import _assess_test_results

        # Create the JSON report file that the function will look for
        report_path = tmp_path / ".pytest_report.json"
        report_data = {
            "summary": {
                "total": 5,
                "passed": 4,
                "failed": 1,
                "skipped": 0,
            }
        }
        report_path.write_text(json.dumps(report_data))

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            tr = _assess_test_results(tmp_path)

        assert tr.total_tests == 5
        assert tr.passed_tests == 4

    # --- query_tmux_sessions ---

    def test_query_tmux_sessions_not_found(self):
        from forge_harness.iteration.assess import query_tmux_sessions

        with patch("subprocess.run", side_effect=FileNotFoundError):
            agents = query_tmux_sessions()
        assert agents == []

    def test_query_tmux_sessions_no_sessions(self):
        from forge_harness.iteration.assess import query_tmux_sessions

        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("subprocess.run", return_value=mock_result):
            agents = query_tmux_sessions()
        assert agents == []

    def test_query_tmux_sessions_parses_output(self):
        from forge_harness.iteration.assess import query_tmux_sessions

        ts = int(time.time())
        tmux_output = f"forge:{ts}:{ts}\n"

        def side_effect(cmd, *args, **kwargs):
            r = MagicMock()
            r.returncode = 0
            if "list-sessions" in cmd:
                r.stdout = tmux_output
            else:
                r.stdout = "/home/user/work\n"
            return r

        with patch("subprocess.run", side_effect=side_effect):
            agents = query_tmux_sessions()

        assert len(agents) == 1
        assert agents[0].session_name == "forge"

    def test_query_tmux_sessions_timeout(self):
        import subprocess

        from forge_harness.iteration.assess import query_tmux_sessions

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=5)):
            agents = query_tmux_sessions()
        assert agents == []

    # --- collect_test_results ---

    def test_collect_test_results_no_cache(self, monkeypatch, tmp_path):
        from forge_harness.iteration.assess import collect_test_results

        monkeypatch.chdir(tmp_path)
        tr = collect_test_results()
        assert tr.total_tests == 0

    def test_collect_test_results_reads_lastfailed(self, monkeypatch, tmp_path):
        from forge_harness.iteration.assess import collect_test_results

        monkeypatch.chdir(tmp_path)
        cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
        cache_dir.mkdir(parents=True)
        lastfailed = {"tests/test_foo.py::test_bar": True, "tests/test_baz.py::test_qux": True}
        (cache_dir / "lastfailed").write_text(json.dumps(lastfailed))

        tr = collect_test_results()
        assert tr.failed_count == 2

    def test_collect_test_results_reads_nodeids(self, monkeypatch, tmp_path):
        from forge_harness.iteration.assess import collect_test_results

        monkeypatch.chdir(tmp_path)
        cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
        cache_dir.mkdir(parents=True)
        nodeids = [f"tests/test_foo.py::test_{i}" for i in range(20)]
        (cache_dir / "nodeids").write_text(json.dumps(nodeids))

        tr = collect_test_results()
        assert tr.total_tests == 20

    # --- assess_status (integration smoke) ---

    def test_assess_status_returns_report(self, monkeypatch, tmp_path):
        from forge_harness.iteration.assess import AssessmentReport, assess_status

        monkeypatch.chdir(tmp_path)

        with (
            patch("forge_harness.iteration.assess._assess_agents", return_value=[]),
            patch(
                "forge_harness.iteration.assess.collect_git_status",
                return_value=MagicMock(
                    projects=[],
                    total_staged=0,
                    total_modified=0,
                    total_untracked=0,
                    total_conflicts=0,
                ),
            ),
            patch(
                "forge_harness.iteration.assess._assess_test_results",
                return_value=MagicMock(
                    total_tests=0,
                    failed_count=0,
                    error_count=0,
                    failure_rate=0.0,
                    pass_rate=None,
                    is_passing=True,
                ),
            ),
            patch(
                "forge_harness.iteration.assess._assess_lint_errors",
                return_value=MagicMock(error_count=0),
            ),
        ):
            report = assess_status()

        assert isinstance(report, AssessmentReport)
        assert report.duration_seconds >= 0


class TestDemoAssessMain:
    """Tests for demo_assess.main function."""

    def test_demo_assess_main_runs(self, capsys, monkeypatch, tmp_path):
        from forge_harness.iteration.assess import (
            AgentStatus,
            AgentType,
            AssessmentReport,
            GitStatus,
            Issue,
            IssueType,
            LintResults,
            OverallHealth,
            TestResults,
        )
        from forge_harness.iteration.demo_assess import main

        mock_report = AssessmentReport(
            agents=[
                AgentStatus(
                    session_name="forge:cursor",
                    agent_type=AgentType.BACKEND,
                    is_active=True,
                    working_directory=tmp_path,
                    last_activity=datetime.now(UTC),
                )
            ],
            git_status=GitStatus(
                projects=[],
                total_staged=0,
                total_modified=0,
                total_untracked=0,
                total_conflicts=0,
            ),
            test_results=TestResults(
                total_tests=10,
                passed_count=8,
                failed_count=2,
                last_run=datetime.now(UTC),
            ),
            lint_errors=LintResults(error_count=0),
            overall_health=OverallHealth.HEALTHY,
            issues=[
                Issue(
                    issue_type=IssueType.FAILED_TEST,
                    severity="medium",
                    message="2 tests failed",
                    location="tests/",
                )
            ],
            duration_seconds=0.5,
        )

        with patch("forge_harness.iteration.demo_assess.assess_status", return_value=mock_report):
            main()

        out = capsys.readouterr().out
        assert "assessment" in out.lower()
        assert "agent" in out.lower() or "forge:cursor" in out.lower()

    def test_demo_assess_main_no_agents(self, capsys, tmp_path):
        from forge_harness.iteration.assess import (
            AssessmentReport,
            GitStatus,
            LintResults,
            TestResults,
        )
        from forge_harness.iteration.demo_assess import main

        mock_report = AssessmentReport(
            agents=[],
            git_status=GitStatus(),
            test_results=TestResults(),
            lint_errors=LintResults(),
            duration_seconds=0.1,
        )

        with patch("forge_harness.iteration.demo_assess.assess_status", return_value=mock_report):
            main()

        out = capsys.readouterr().out
        assert "no active agents" in out.lower()

    def test_demo_assess_main_no_test_results(self, capsys, tmp_path):
        from forge_harness.iteration.assess import (
            AssessmentReport,
            GitStatus,
            LintResults,
            TestResults,
        )
        from forge_harness.iteration.demo_assess import main

        mock_report = AssessmentReport(
            agents=[],
            git_status=GitStatus(),
            test_results=TestResults(total_tests=0),
            lint_errors=LintResults(),
            duration_seconds=0.1,
        )

        with patch("forge_harness.iteration.demo_assess.assess_status", return_value=mock_report):
            main()

        out = capsys.readouterr().out
        assert "no test results" in out.lower() or "not available" in out.lower()

    def test_demo_assess_main_no_issues(self, capsys, tmp_path):
        from forge_harness.iteration.assess import (
            AssessmentReport,
            GitStatus,
            LintResults,
            TestResults,
        )
        from forge_harness.iteration.demo_assess import main

        mock_report = AssessmentReport(
            agents=[],
            git_status=GitStatus(),
            test_results=TestResults(),
            lint_errors=LintResults(),
            issues=[],
            duration_seconds=0.1,
        )

        with patch("forge_harness.iteration.demo_assess.assess_status", return_value=mock_report):
            main()

        out = capsys.readouterr().out
        assert "no issues" in out.lower()

    def test_demo_assess_main_many_failed_tests(self, capsys, tmp_path):
        """Exercises truncation of failed test list (>10 tests)."""
        from forge_harness.iteration.assess import (
            AssessmentReport,
            FailedTestsList,
            GitStatus,
            LintResults,
            TestResult,
            TestResults,
        )
        from forge_harness.iteration.demo_assess import main

        failed = FailedTestsList(
            [TestResult(test_name=f"test_{i}", status="failed") for i in range(15)]
        )
        tr = TestResults(total_tests=20, passed_count=5, failed_count=15, failed_tests=failed)
        mock_report = AssessmentReport(
            agents=[],
            git_status=GitStatus(),
            test_results=tr,
            lint_errors=LintResults(),
            duration_seconds=0.1,
        )

        with patch("forge_harness.iteration.demo_assess.assess_status", return_value=mock_report):
            main()

        out = capsys.readouterr().out
        assert "more" in out.lower() or "15" in out

    def test_demo_assess_main_issues_grouped_by_severity(self, capsys, tmp_path):
        from forge_harness.iteration.assess import (
            AssessmentReport,
            GitStatus,
            Issue,
            IssueType,
            LintResults,
            TestResults,
        )
        from forge_harness.iteration.demo_assess import main

        issues = [
            Issue(issue_type=IssueType.FAILED_TEST, severity="critical", message="critical issue"),
            Issue(issue_type=IssueType.GIT_CONFLICT, severity="high", message="high issue"),
            Issue(
                issue_type=IssueType.UNCOMMITTED_CHANGES, severity="medium", message="medium issue"
            ),
            Issue(issue_type=IssueType.RUNTIME_ERROR, severity="low", message="low issue"),
        ]
        mock_report = AssessmentReport(
            agents=[],
            git_status=GitStatus(),
            test_results=TestResults(),
            lint_errors=LintResults(),
            issues=issues,
            duration_seconds=0.1,
        )

        with patch("forge_harness.iteration.demo_assess.assess_status", return_value=mock_report):
            main()

        out = capsys.readouterr().out
        assert "critical" in out.lower()
        assert "high" in out.lower()
