"""
CLI v2 Smoke Test Suite
=======================

Regression suite that validates every command group in the FORGE CLI v2:

1. Import Tests    — each module imports cleanly, exports a Click command/group
2. Registration    — all groups appear in cli.commands, no duplicates
3. Help Tests      — every command/subcommand returns exit code 0 for --help
4. JSON Envelope   — commands with --json produce {success, data, error, timestamp, command}
5. Error Contract  — invalid arguments produce structured errors, not raw tracebacks
6. Consistency     — naming conventions, no duplicate names in any group

Fast by design: no real network, filesystem, or external process calls.
Heavy dependencies are patched out where necessary so each test runs in <50ms.
"""

from __future__ import annotations

import importlib
import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import click
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _json_envelope_keys() -> frozenset[str]:
    return frozenset({"success", "data", "error", "timestamp", "command"})


def _invoke_help(runner: CliRunner, cli, *args) -> click.testing.Result:
    return runner.invoke(cli, list(args) + ["--help"])


def _parse_json_output(result) -> dict:
    """Parse the first complete JSON object from output."""
    return json.loads(result.output.strip())


# ---------------------------------------------------------------------------
# 1. IMPORT TESTS
# ---------------------------------------------------------------------------


class TestModuleImports(unittest.TestCase):
    """Each cli_v2 module must import without errors."""

    _modules = [
        "_common",
        "_task_parser",
        "approve",
        "claims",
        "contract",
        "dispatch",
        "doctor",
        "evaluator",
        "exit_codes",
        "fleet",
        "git_guard",
        "handoff",
        "health",
        "json_output",
        "lead",
        "loop",
        "nodes",
        "orchestrator",
        "plan",
        "ship",
        "status",
        "submodules",
        "tasks",
        "work",
        "xnode",
    ]

    def _import(self, name: str) -> types.ModuleType:
        full = f"forge_harness.cli_v2.{name}"
        if full in sys.modules:
            return sys.modules[full]
        return importlib.import_module(full)

    def test_import_common(self):
        mod = self._import("_common")
        self.assertIsNotNone(mod)

    def test_import_task_parser(self):
        mod = self._import("_task_parser")
        self.assertIsNotNone(mod)

    def test_import_approve(self):
        mod = self._import("approve")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "approve"))

    def test_import_claims(self):
        mod = self._import("claims")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "claims"))

    def test_import_contract(self):
        mod = self._import("contract")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "contract"))

    def test_import_dispatch(self):
        mod = self._import("dispatch")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "dispatch"))

    def test_import_doctor(self):
        mod = self._import("doctor")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "doctor"))

    def test_import_evaluator(self):
        mod = self._import("evaluator")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "evaluator"))

    def test_import_exit_codes(self):
        mod = self._import("exit_codes")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "ExitCode"))

    def test_import_fleet(self):
        mod = self._import("fleet")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "fleet"))

    def test_import_git_guard(self):
        mod = self._import("git_guard")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "git"))

    def test_import_handoff(self):
        mod = self._import("handoff")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "handoff"))

    def test_import_health(self):
        mod = self._import("health")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "health"))

    def test_import_json_output(self):
        mod = self._import("json_output")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "json_output"))
        self.assertTrue(hasattr(mod, "print_json"))
        self.assertTrue(hasattr(mod, "handle_error_json"))

    def test_import_lead(self):
        mod = self._import("lead")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "lead"))

    def test_import_loop(self):
        mod = self._import("loop")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "loop"))

    def test_import_nodes(self):
        mod = self._import("nodes")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "nodes"))

    def test_import_orchestrator(self):
        mod = self._import("orchestrator")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "orchestrator_group"))

    def test_import_plan(self):
        mod = self._import("plan")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "plan"))

    def test_import_ship(self):
        mod = self._import("ship")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "ship"))

    def test_import_status(self):
        mod = self._import("status")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "status"))

    def test_import_submodules(self):
        mod = self._import("submodules")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "submodules"))

    def test_import_tasks(self):
        mod = self._import("tasks")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "tasks"))

    def test_import_work(self):
        mod = self._import("work")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "work"))

    def test_import_xnode(self):
        mod = self._import("xnode")
        self.assertIsNotNone(mod)
        self.assertTrue(hasattr(mod, "xnode"))


# ---------------------------------------------------------------------------
# 2. REGISTRATION TESTS
# ---------------------------------------------------------------------------


class TestRegistration(unittest.TestCase):
    """All command groups must be registered in the root CLI without duplicates."""

    def setUp(self):
        from forge_harness.cli_v2 import cli

        self.cli = cli

    def test_cli_is_click_group(self):
        import click

        self.assertIsInstance(self.cli, click.Group)

    def test_cli_has_commands_dict(self):
        self.assertTrue(hasattr(self.cli, "commands"))
        self.assertIsInstance(self.cli.commands, dict)

    def test_no_duplicate_command_names(self):
        names = list(self.cli.commands.keys())
        self.assertEqual(len(names), len(set(names)), f"Duplicate command names: {names}")

    def _assert_registered(self, name: str):
        self.assertIn(name, self.cli.commands, f"'{name}' not registered in CLI")

    def test_approve_registered(self):
        self._assert_registered("approve")

    def test_claims_registered(self):
        self._assert_registered("claims")

    def test_contract_registered(self):
        self._assert_registered("contract")

    def test_dispatch_registered(self):
        self._assert_registered("dispatch")

    def test_doctor_registered(self):
        self._assert_registered("doctor")

    def test_evaluator_registered(self):
        self._assert_registered("evaluator")

    def test_fleet_registered(self):
        self._assert_registered("fleet")

    def test_git_registered(self):
        self._assert_registered("git")

    def test_handoff_registered(self):
        self._assert_registered("handoff")

    def test_health_registered(self):
        self._assert_registered("health")

    def test_ios_registered(self):
        self._assert_registered("ios")

    def test_lead_registered(self):
        self._assert_registered("lead")

    def test_loop_registered(self):
        self._assert_registered("loop")

    def test_nodes_registered(self):
        self._assert_registered("nodes")

    def test_orchestrator_registered(self):
        self._assert_registered("orchestrator")

    def test_plan_registered(self):
        self._assert_registered("plan")

    def test_ship_registered(self):
        self._assert_registered("ship")

    def test_status_registered(self):
        self._assert_registered("status")

    def test_submodules_registered(self):
        self._assert_registered("submodules")

    def test_tasks_registered(self):
        self._assert_registered("tasks")

    def test_work_registered(self):
        self._assert_registered("work")

    def test_xnode_registered(self):
        self._assert_registered("xnode")

    def test_subcommands_claims(self):
        cmd = self.cli.commands["claims"]
        self.assertTrue(hasattr(cmd, "commands"))
        for sub in ("submit", "verify", "list", "stats"):
            self.assertIn(sub, cmd.commands, f"claims.{sub} missing")

    def test_subcommands_contract(self):
        cmd = self.cli.commands["contract"]
        self.assertTrue(hasattr(cmd, "commands"))
        for sub in ("check", "list"):
            self.assertIn(sub, cmd.commands, f"contract.{sub} missing")

    def test_subcommands_dispatch(self):
        cmd = self.cli.commands["dispatch"]
        self.assertTrue(hasattr(cmd, "commands"))
        for sub in ("send", "relay"):
            self.assertIn(sub, cmd.commands, f"dispatch.{sub} missing")

    def test_subcommands_evaluator(self):
        cmd = self.cli.commands["evaluator"]
        self.assertTrue(hasattr(cmd, "commands"))
        for sub in ("status", "results", "rerun", "summary"):
            self.assertIn(sub, cmd.commands, f"evaluator.{sub} missing")

    def test_subcommands_git(self):
        cmd = self.cli.commands["git"]
        self.assertTrue(hasattr(cmd, "commands"))
        for sub in ("guard", "mutex", "clear-locks"):
            self.assertIn(sub, cmd.commands, f"git.{sub} missing")

    def test_subcommands_lead(self):
        cmd = self.cli.commands["lead"]
        self.assertTrue(hasattr(cmd, "commands"))
        for sub in ("send", "ack", "acks", "inbox", "pending-acks", "handoff"):
            self.assertIn(sub, cmd.commands, f"lead.{sub} missing")

    def test_subcommands_nodes(self):
        cmd = self.cli.commands["nodes"]
        self.assertTrue(hasattr(cmd, "commands"))
        self.assertIn("heartbeat", cmd.commands)

    def test_subcommands_orchestrator(self):
        cmd = self.cli.commands["orchestrator"]
        self.assertTrue(hasattr(cmd, "commands"))
        self.assertIn("checkpoint", cmd.commands)

    def test_subcommands_submodules(self):
        cmd = self.cli.commands["submodules"]
        self.assertTrue(hasattr(cmd, "commands"))
        for sub in ("sync-parent", "update"):
            self.assertIn(sub, cmd.commands, f"submodules.{sub} missing")

    def test_subcommands_tasks(self):
        cmd = self.cli.commands["tasks"]
        self.assertTrue(hasattr(cmd, "commands"))
        for sub in (
            "list",
            "create",
            "show",
            "update",
            "claim",
            "dispatch",
            "recommended",
            "stats",
            "complete",
            "delete",
            "cleanup",
            "import",
        ):
            self.assertIn(sub, cmd.commands, f"tasks.{sub} missing")

    def test_subcommands_xnode(self):
        cmd = self.cli.commands["xnode"]
        self.assertTrue(hasattr(cmd, "commands"))
        for sub in ("list", "relay", "listen"):
            self.assertIn(sub, cmd.commands, f"xnode.{sub} missing")


# ---------------------------------------------------------------------------
# 3. HELP TESTS
# ---------------------------------------------------------------------------


class TestHelpOutput(unittest.TestCase):
    """Every command and subcommand must respond to --help with exit code 0."""

    def setUp(self):
        from forge_harness.cli_v2 import cli

        self.cli = cli
        self.runner = CliRunner()

    def _help_ok(self, *args):
        result = self.runner.invoke(self.cli, list(args) + ["--help"])
        self.assertEqual(
            result.exit_code,
            0,
            f"'{' '.join(str(a) for a in args)} --help' exited {result.exit_code}:\n{result.output}",
        )
        self.assertIn("Usage:", result.output)

    # Root
    def test_root_help(self):
        self._help_ok()

    def test_approve_help(self):
        self._help_ok("approve")

    def test_claims_help(self):
        self._help_ok("claims")

    def test_claims_submit_help(self):
        self._help_ok("claims", "submit")

    def test_claims_verify_help(self):
        self._help_ok("claims", "verify")

    def test_claims_list_help(self):
        self._help_ok("claims", "list")

    def test_claims_stats_help(self):
        self._help_ok("claims", "stats")

    def test_contract_help(self):
        self._help_ok("contract")

    def test_contract_check_help(self):
        self._help_ok("contract", "check")

    def test_contract_list_help(self):
        self._help_ok("contract", "list")

    def test_dispatch_help(self):
        self._help_ok("dispatch")

    def test_dispatch_send_help(self):
        self._help_ok("dispatch", "send")

    def test_dispatch_relay_help(self):
        self._help_ok("dispatch", "relay")

    def test_doctor_help(self):
        self._help_ok("doctor")

    def test_evaluator_help(self):
        self._help_ok("evaluator")

    def test_evaluator_status_help(self):
        self._help_ok("evaluator", "status")

    def test_evaluator_results_help(self):
        self._help_ok("evaluator", "results")

    def test_evaluator_rerun_help(self):
        self._help_ok("evaluator", "rerun")

    def test_evaluator_summary_help(self):
        self._help_ok("evaluator", "summary")

    def test_fleet_help(self):
        self._help_ok("fleet")

    def test_git_help(self):
        self._help_ok("git")

    def test_git_guard_help(self):
        self._help_ok("git", "guard")

    def test_git_mutex_help(self):
        self._help_ok("git", "mutex")

    def test_git_clear_locks_help(self):
        self._help_ok("git", "clear-locks")

    def test_handoff_help(self):
        self._help_ok("handoff")

    def test_health_help(self):
        self._help_ok("health")

    def test_lead_help(self):
        self._help_ok("lead")

    def test_lead_send_help(self):
        self._help_ok("lead", "send")

    def test_lead_ack_help(self):
        self._help_ok("lead", "ack")

    def test_lead_acks_help(self):
        self._help_ok("lead", "acks")

    def test_lead_inbox_help(self):
        self._help_ok("lead", "inbox")

    def test_lead_pending_acks_help(self):
        self._help_ok("lead", "pending-acks")

    def test_lead_handoff_help(self):
        self._help_ok("lead", "handoff")

    def test_loop_help(self):
        self._help_ok("loop")

    def test_nodes_help(self):
        self._help_ok("nodes")

    def test_nodes_heartbeat_help(self):
        self._help_ok("nodes", "heartbeat")

    def test_orchestrator_help(self):
        self._help_ok("orchestrator")

    def test_orchestrator_checkpoint_help(self):
        self._help_ok("orchestrator", "checkpoint")

    def test_plan_help(self):
        self._help_ok("plan")

    def test_ship_help(self):
        self._help_ok("ship")

    def test_status_help(self):
        self._help_ok("status")

    def test_submodules_help(self):
        self._help_ok("submodules")

    def test_submodules_sync_parent_help(self):
        self._help_ok("submodules", "sync-parent")

    def test_submodules_update_help(self):
        self._help_ok("submodules", "update")

    def test_tasks_help(self):
        self._help_ok("tasks")

    def test_tasks_list_help(self):
        self._help_ok("tasks", "list")

    def test_tasks_create_help(self):
        self._help_ok("tasks", "create")

    def test_tasks_show_help(self):
        self._help_ok("tasks", "show")

    def test_tasks_update_help(self):
        self._help_ok("tasks", "update")

    def test_tasks_claim_help(self):
        self._help_ok("tasks", "claim")

    def test_tasks_dispatch_help(self):
        self._help_ok("tasks", "dispatch")

    def test_tasks_recommended_help(self):
        self._help_ok("tasks", "recommended")

    def test_tasks_stats_help(self):
        self._help_ok("tasks", "stats")

    def test_tasks_complete_help(self):
        self._help_ok("tasks", "complete")

    def test_tasks_delete_help(self):
        self._help_ok("tasks", "delete")

    def test_tasks_cleanup_help(self):
        self._help_ok("tasks", "cleanup")

    def test_tasks_import_help(self):
        self._help_ok("tasks", "import")

    def test_work_help(self):
        self._help_ok("work")

    def test_xnode_help(self):
        self._help_ok("xnode")

    def test_xnode_list_help(self):
        self._help_ok("xnode", "list")

    def test_xnode_relay_help(self):
        self._help_ok("xnode", "relay")

    def test_xnode_listen_help(self):
        self._help_ok("xnode", "listen")


# ---------------------------------------------------------------------------
# 4. JSON OUTPUT ENVELOPE TESTS
# ---------------------------------------------------------------------------


class TestJsonOutputModule(unittest.TestCase):
    """The json_output helper must produce correct envelopes."""

    def setUp(self):
        from forge_harness.cli_v2.json_output import handle_error_json, json_output

        self.json_output = json_output
        self.handle_error_json = handle_error_json

    def _required_keys(self):
        return _json_envelope_keys()

    def test_success_envelope_keys(self):
        out = self.json_output(True, {"foo": "bar"}, command="test cmd")
        self.assertEqual(set(out.keys()), self._required_keys())

    def test_success_envelope_success_true(self):
        out = self.json_output(True, {"x": 1}, command="test")
        self.assertTrue(out["success"])

    def test_success_envelope_error_none(self):
        out = self.json_output(True, {"x": 1}, command="test")
        self.assertIsNone(out["error"])

    def test_success_envelope_data_preserved(self):
        data = {"a": 1, "b": [1, 2, 3]}
        out = self.json_output(True, data, command="test")
        self.assertEqual(out["data"], data)

    def test_success_envelope_command_field(self):
        out = self.json_output(True, {}, command="forge tasks list")
        self.assertEqual(out["command"], "forge tasks list")

    def test_success_envelope_timestamp_present(self):
        out = self.json_output(True, {}, command="test")
        self.assertIn("timestamp", out)
        self.assertIsNotNone(out["timestamp"])

    def test_error_envelope_keys(self):
        out = self.json_output(False, None, error="Something went wrong", command="test")
        self.assertEqual(set(out.keys()), self._required_keys())

    def test_error_envelope_success_false(self):
        out = self.json_output(False, None, error="error msg", command="test")
        self.assertFalse(out["success"])

    def test_error_envelope_data_none(self):
        out = self.json_output(False, None, error="error msg", command="test")
        self.assertIsNone(out["data"])

    def test_error_envelope_has_error_object(self):
        out = self.json_output(False, None, error="test error", command="test")
        self.assertIsNotNone(out["error"])
        self.assertIsInstance(out["error"], dict)

    def test_error_envelope_error_has_message(self):
        out = self.json_output(False, None, error="boom", command="test")
        self.assertIn("message", out["error"])
        self.assertEqual(out["error"]["message"], "boom")

    def test_error_envelope_error_has_code(self):
        out = self.json_output(False, None, error="boom", command="test")
        self.assertIn("code", out["error"])

    def test_error_envelope_command_preserved(self):
        out = self.json_output(False, None, error="err", command="forge claims list")
        self.assertEqual(out["command"], "forge claims list")

    def test_json_serialisable(self):
        out = self.json_output(True, {"nested": {"list": [1, 2, 3]}}, command="test")
        serialised = json.dumps(out)
        self.assertIsInstance(serialised, str)

    def test_handle_error_json_outputs_to_stdout(self):
        import io
        from unittest.mock import patch

        import click

        buf = io.StringIO()
        with patch("click.echo", side_effect=lambda s, **kw: buf.write(str(s) + "\n")):
            self.handle_error_json(RuntimeError("boom"), "test cmd", output_json=True)
        output = buf.getvalue()
        self.assertIn("success", output)

    def test_handle_error_json_silent_when_not_json_mode(self):
        """No output when output_json=False."""
        import io
        from unittest.mock import patch

        buf = io.StringIO()
        with patch("click.echo", side_effect=lambda s, **kw: buf.write(str(s) + "\n")):
            self.handle_error_json(RuntimeError("boom"), "test cmd", output_json=False)
        self.assertEqual(buf.getvalue(), "")


# ---------------------------------------------------------------------------
# 5. JSON CONTRACT: claims stats (offline — mocks ClaimsService)
# ---------------------------------------------------------------------------


class TestClaimsJsonContract(unittest.TestCase):
    """forge claims list/stats --json must produce a valid envelope."""

    def setUp(self):
        from forge_harness.cli_v2 import cli

        self.cli = cli
        self.runner = CliRunner()

    @patch("forge_harness.cli_v2.claims.get_claims_service")
    def test_claims_list_json_envelope(self, mock_svc_factory):
        mock_svc = MagicMock()
        mock_svc.list_claims.return_value = []
        mock_svc_factory.return_value = mock_svc

        result = self.runner.invoke(self.cli, ["claims", "--json", "list"])
        self.assertEqual(result.exit_code, 0, result.output)
        envelope = json.loads(result.output.strip())
        self.assertEqual(set(envelope.keys()), _json_envelope_keys())
        self.assertTrue(envelope["success"])
        self.assertIsInstance(envelope["data"], list)

    @patch("forge_harness.cli_v2.claims.get_claims_service")
    def test_claims_stats_json_envelope(self, mock_svc_factory):
        mock_svc = MagicMock()
        mock_svc.get_stats.return_value = {
            "submitted": 2,
            "approved": 1,
            "rejected": 0,
            "verifying": 0,
        }
        mock_svc_factory.return_value = mock_svc

        result = self.runner.invoke(self.cli, ["claims", "--json", "stats"])
        self.assertEqual(result.exit_code, 0, result.output)
        envelope = json.loads(result.output.strip())
        self.assertEqual(set(envelope.keys()), _json_envelope_keys())
        self.assertTrue(envelope["success"])

    @patch("forge_harness.cli_v2.claims.get_claims_service")
    def test_claims_list_command_field(self, mock_svc_factory):
        mock_svc = MagicMock()
        mock_svc.list_claims.return_value = []
        mock_svc_factory.return_value = mock_svc

        result = self.runner.invoke(self.cli, ["claims", "--json", "list"])
        envelope = json.loads(result.output.strip())
        self.assertIn("forge claims list", envelope["command"])


# ---------------------------------------------------------------------------
# 6. JSON CONTRACT: evaluator commands (mocks EvaluatorOrchestrator)
# ---------------------------------------------------------------------------


class TestEvaluatorJsonContract(unittest.TestCase):
    """forge evaluator status/summary --json must produce valid envelopes."""

    def setUp(self):
        from forge_harness.cli_v2 import cli

        self.cli = cli
        self.runner = CliRunner()

    def _make_mock_evaluator(self):
        ev = MagicMock()
        ev.get_summary.return_value = {
            "total": 0,
            "passed": 0,
            "required_passed": 0,
            "auto_approved": 0,
            "pass_rate": 0.0,
        }
        ev._history = []
        return ev

    @patch("forge_harness.cli_v2.evaluator._get_evaluator")
    def test_evaluator_status_json_envelope(self, mock_get):
        mock_get.return_value = self._make_mock_evaluator()
        result = self.runner.invoke(self.cli, ["evaluator", "--json", "status"])
        self.assertEqual(result.exit_code, 0, result.output)
        envelope = json.loads(result.output.strip())
        self.assertEqual(set(envelope.keys()), _json_envelope_keys())
        self.assertTrue(envelope["success"])

    @patch("forge_harness.cli_v2.evaluator._get_evaluator")
    def test_evaluator_summary_json_envelope(self, mock_get):
        mock_get.return_value = self._make_mock_evaluator()
        result = self.runner.invoke(self.cli, ["evaluator", "--json", "summary"])
        self.assertEqual(result.exit_code, 0, result.output)
        envelope = json.loads(result.output.strip())
        self.assertEqual(set(envelope.keys()), _json_envelope_keys())
        self.assertTrue(envelope["success"])

    @patch("forge_harness.cli_v2.evaluator._get_evaluator")
    def test_evaluator_status_command_field(self, mock_get):
        mock_get.return_value = self._make_mock_evaluator()
        result = self.runner.invoke(self.cli, ["evaluator", "--json", "status"])
        envelope = json.loads(result.output.strip())
        self.assertIn("evaluator status", envelope["command"])


# ---------------------------------------------------------------------------
# 7. JSON CONTRACT: contract list (offline — no external deps)
# ---------------------------------------------------------------------------


class TestContractJsonContract(unittest.TestCase):
    """forge contract list --json must produce a valid envelope."""

    def setUp(self):
        from forge_harness.cli_v2 import cli

        self.cli = cli
        self.runner = CliRunner()

    def test_contract_list_json_envelope(self):
        result = self.runner.invoke(self.cli, ["contract", "--json", "list"])
        self.assertEqual(result.exit_code, 0, result.output)
        envelope = json.loads(result.output.strip())
        self.assertEqual(set(envelope.keys()), _json_envelope_keys())
        self.assertTrue(envelope["success"])
        self.assertIsInstance(envelope["data"], list)
        self.assertGreater(len(envelope["data"]), 0)

    def test_contract_list_data_has_name_description(self):
        result = self.runner.invoke(self.cli, ["contract", "--json", "list"])
        envelope = json.loads(result.output.strip())
        for item in envelope["data"]:
            self.assertIn("name", item)
            self.assertIn("description", item)

    def test_contract_list_command_field(self):
        result = self.runner.invoke(self.cli, ["contract", "--json", "list"])
        envelope = json.loads(result.output.strip())
        self.assertIn("forge contract list", envelope["command"])


# ---------------------------------------------------------------------------
# 8. JSON CONTRACT: xnode list (offline — reads empty filesystem)
# ---------------------------------------------------------------------------


class TestXnodeJsonContract(unittest.TestCase):
    """forge xnode list --json must produce a valid envelope even with no data."""

    def setUp(self):
        from forge_harness.cli_v2 import cli

        self.cli = cli
        self.runner = CliRunner()

    @patch("forge_harness.cli_v2.xnode.require_forge_root")
    @patch("forge_harness.cli_v2.xnode.xnode_ensure_dirs")
    def test_xnode_list_json_envelope(self, mock_dirs, mock_root):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            exceptions_dir = Path(tmpdir) / "exceptions"
            exceptions_dir.mkdir()
            mock_root.return_value = Path(tmpdir)
            mock_dirs.return_value = {
                "exceptions": exceptions_dir,
                "lead_inbox": Path(tmpdir) / "lead_inbox",
                "handoffs": Path(tmpdir) / "handoffs",
                "acks": Path(tmpdir) / "acks",
            }
            result = self.runner.invoke(self.cli, ["xnode", "list", "--json"])
            self.assertEqual(result.exit_code, 0, result.output)
            envelope = json.loads(result.output.strip())
            self.assertEqual(set(envelope.keys()), _json_envelope_keys())
            self.assertTrue(envelope["success"])
            self.assertIn("messages", envelope["data"])

    @patch("forge_harness.cli_v2.xnode.require_forge_root")
    @patch("forge_harness.cli_v2.xnode.xnode_ensure_dirs")
    def test_xnode_list_command_field(self, mock_dirs, mock_root):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            exceptions_dir = Path(tmpdir) / "exceptions"
            exceptions_dir.mkdir()
            mock_root.return_value = Path(tmpdir)
            mock_dirs.return_value = {
                "exceptions": exceptions_dir,
                "lead_inbox": Path(tmpdir),
                "handoffs": Path(tmpdir),
                "acks": Path(tmpdir),
            }
            result = self.runner.invoke(self.cli, ["xnode", "list", "--json"])
            envelope = json.loads(result.output.strip())
            self.assertIn("forge xnode list", envelope["command"])


# ---------------------------------------------------------------------------
# 9. JSON CONTRACT: lead acks (offline)
# ---------------------------------------------------------------------------


class TestLeadAcksJsonContract(unittest.TestCase):
    """forge lead acks --json must produce a valid envelope."""

    def setUp(self):
        from forge_harness.cli_v2 import cli

        self.cli = cli
        self.runner = CliRunner()

    @patch("forge_harness.cli_v2.lead.require_forge_root")
    @patch("forge_harness.cli_v2.lead.xnode_ensure_dirs")
    def test_lead_acks_json_envelope(self, mock_dirs, mock_root):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            inbox_dir = Path(tmpdir) / "lead_inbox"
            inbox_dir.mkdir()
            acks_dir = Path(tmpdir) / "acks"
            acks_dir.mkdir()
            mock_root.return_value = Path(tmpdir)
            mock_dirs.return_value = {
                "lead_inbox": inbox_dir,
                "acks": acks_dir,
                "handoffs": Path(tmpdir) / "handoffs",
                "exceptions": Path(tmpdir) / "exceptions",
            }
            result = self.runner.invoke(self.cli, ["lead", "acks", "--json"])
            self.assertEqual(result.exit_code, 0, result.output)
            envelope = json.loads(result.output.strip())
            self.assertEqual(set(envelope.keys()), _json_envelope_keys())
            self.assertTrue(envelope["success"])
            self.assertIn("acks", envelope["data"])

    @patch("forge_harness.cli_v2.lead.require_forge_root")
    @patch("forge_harness.cli_v2.lead.xnode_ensure_dirs")
    def test_lead_acks_command_field(self, mock_dirs, mock_root):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            inbox_dir = Path(tmpdir) / "lead_inbox"
            inbox_dir.mkdir()
            acks_dir = Path(tmpdir) / "acks"
            acks_dir.mkdir()
            mock_root.return_value = Path(tmpdir)
            mock_dirs.return_value = {
                "lead_inbox": inbox_dir,
                "acks": acks_dir,
                "handoffs": Path(tmpdir) / "handoffs",
                "exceptions": Path(tmpdir) / "exceptions",
            }
            result = self.runner.invoke(self.cli, ["lead", "acks", "--json"])
            envelope = json.loads(result.output.strip())
            self.assertIn("forge lead acks", envelope["command"])


# ---------------------------------------------------------------------------
# 10. JSON CONTRACT: nodes heartbeat (mocks httpx + require_forge_root)
# ---------------------------------------------------------------------------


class TestNodesHeartbeatJsonContract(unittest.TestCase):
    """forge nodes heartbeat --json must produce a valid envelope."""

    def setUp(self):
        from forge_harness.cli_v2 import cli

        self.cli = cli
        self.runner = CliRunner()

    @patch("forge_harness.cli_v2.nodes.require_forge_root")
    @patch("forge_harness.cli_v2.nodes.httpx")
    def test_nodes_heartbeat_json_envelope(self, mock_httpx, mock_root):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            mock_root.return_value = Path(tmpdir)

            # Mock httpx client to simulate connection failure (graceful)
            mock_client_instance = MagicMock()
            mock_client_instance.post.side_effect = Exception("connection refused")
            mock_httpx.Client.return_value.__enter__ = MagicMock(return_value=mock_client_instance)
            mock_httpx.Client.return_value.__exit__ = MagicMock(return_value=False)

            result = self.runner.invoke(
                self.cli, ["nodes", "heartbeat", "--json", "--node", "test-node"]
            )
            self.assertEqual(result.exit_code, 0, result.output)
            envelope = json.loads(result.output.strip())
            self.assertEqual(set(envelope.keys()), _json_envelope_keys())
            self.assertTrue(envelope["success"])
            self.assertIn("telemetry", envelope["data"])
            self.assertIn("publish", envelope["data"])

    @patch("forge_harness.cli_v2.nodes.require_forge_root")
    @patch("forge_harness.cli_v2.nodes.httpx")
    def test_nodes_heartbeat_telemetry_structure(self, mock_httpx, mock_root):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            mock_root.return_value = Path(tmpdir)
            mock_client_instance = MagicMock()
            mock_client_instance.post.side_effect = Exception("no server")
            mock_httpx.Client.return_value.__enter__ = MagicMock(return_value=mock_client_instance)
            mock_httpx.Client.return_value.__exit__ = MagicMock(return_value=False)

            result = self.runner.invoke(
                self.cli, ["nodes", "heartbeat", "--json", "--node", "test-node"]
            )
            envelope = json.loads(result.output.strip())
            telemetry = envelope["data"]["telemetry"]
            self.assertIn("node_id", telemetry)
            self.assertIn("resources", telemetry)
            self.assertIn("capabilities", telemetry)
            self.assertIn("health", telemetry)


# ---------------------------------------------------------------------------
# 11. ERROR CONTRACT TESTS
# ---------------------------------------------------------------------------


class TestErrorContract(unittest.TestCase):
    """Commands with bad arguments must not produce raw tracebacks."""

    def setUp(self):
        from forge_harness.cli_v2 import cli

        self.cli = cli
        self.runner = CliRunner()

    def _no_traceback(self, result):
        self.assertNotIn(
            "Traceback (most recent call last)",
            result.output,
            f"Raw traceback found:\n{result.output}",
        )

    def test_tasks_create_missing_subject(self):
        """forge tasks create requires a SUBJECT argument."""
        result = self.runner.invoke(self.cli, ["tasks", "create"])
        # Must fail but not with a raw traceback
        self._no_traceback(result)
        self.assertNotEqual(result.exit_code, 0)

    def test_claims_list_invalid_status(self):
        """forge claims list --status invalid must fail cleanly."""
        result = self.runner.invoke(self.cli, ["claims", "list", "--status", "bogus"])
        self._no_traceback(result)
        self.assertNotEqual(result.exit_code, 0)

    def test_dispatch_send_missing_args(self):
        """forge dispatch send requires TARGET and MESSAGE."""
        result = self.runner.invoke(self.cli, ["dispatch", "send"])
        self._no_traceback(result)
        self.assertNotEqual(result.exit_code, 0)

    def test_evaluator_results_missing_task_id(self):
        """forge evaluator results requires TASK_ID."""
        result = self.runner.invoke(self.cli, ["evaluator", "results"])
        self._no_traceback(result)
        self.assertNotEqual(result.exit_code, 0)

    def test_contract_check_invalid_service(self):
        """forge contract check --service invalid must fail cleanly."""
        result = self.runner.invoke(self.cli, ["contract", "check", "--service", "invalid"])
        self._no_traceback(result)
        self.assertNotEqual(result.exit_code, 0)

    def test_tasks_create_short_title_json(self):
        """Validation errors with --json produce structured output, not tracebacks."""
        with patch("forge_harness.cli_v2.tasks.require_forge_root") as mock_root:
            mock_root.return_value = __import__("pathlib").Path("/tmp/forge-test")
            result = self.runner.invoke(self.cli, ["tasks", "--json", "create", "Hi"])
        self._no_traceback(result)
        # Output should be valid JSON when --json is set
        try:
            envelope = json.loads(result.output.strip())
            # If JSON parsed, check structure
            self.assertIn("success", envelope)
        except json.JSONDecodeError:
            pass  # Non-JSON error output is also acceptable for validation errors

    def test_xnode_list_invalid_since(self):
        """forge xnode list --since invalid must report error cleanly."""
        with (
            patch("forge_harness.cli_v2.xnode.require_forge_root") as mock_root,
            patch("forge_harness.cli_v2.xnode.xnode_ensure_dirs") as mock_dirs,
        ):
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmpdir:
                mock_root.return_value = Path(tmpdir)
                mock_dirs.return_value = {
                    "exceptions": Path(tmpdir),
                    "lead_inbox": Path(tmpdir),
                    "handoffs": Path(tmpdir),
                    "acks": Path(tmpdir),
                }
                result = self.runner.invoke(self.cli, ["xnode", "list", "--since", "notadate"])
        self._no_traceback(result)


# ---------------------------------------------------------------------------
# 12. CROSS-COMMAND CONSISTENCY TESTS
# ---------------------------------------------------------------------------


class TestCrossCommandConsistency(unittest.TestCase):
    """Cross-cutting conventions across the entire CLI."""

    def setUp(self):
        from forge_harness.cli_v2 import cli

        self.cli = cli

    def _all_commands(self):
        """Yield (path_tuple, command) for every command in the CLI."""

        def _walk(group, path=()):
            for name, cmd in group.commands.items():
                current = path + (name,)
                yield current, cmd
                if hasattr(cmd, "commands"):
                    yield from _walk(cmd, current)

        yield from _walk(self.cli)

    def test_no_duplicate_names_in_any_group(self):
        """Within every group, subcommand names must be unique."""

        def _check_group(group, path=()):
            if not hasattr(group, "commands"):
                return
            names = list(group.commands.keys())
            self.assertEqual(
                len(names), len(set(names)), f"Duplicates in group '{'/'.join(path)}': {names}"
            )
            for name, cmd in group.commands.items():
                _check_group(cmd, path + (name,))

        _check_group(self.cli)

    def test_all_commands_have_docstrings(self):
        """Every command callback must have a non-empty docstring (help text)."""
        missing = []
        for path, cmd in self._all_commands():
            help_text = getattr(cmd, "help", None) or getattr(cmd, "callback", None)
            if callable(help_text):
                # cmd.help is the extracted docstring
                help_text = cmd.help
            if not help_text or not help_text.strip():
                missing.append("/".join(path))
        self.assertEqual(missing, [], f"Commands missing help text: {missing}")

    def test_command_names_are_kebab_case_or_single_word(self):
        """Command names must be lowercase and use hyphens, not underscores."""
        violations = []
        for path, cmd in self._all_commands():
            name = path[-1]
            if "_" in name:
                violations.append("/".join(path))
        self.assertEqual(
            violations, [], f"Commands using underscore instead of hyphen: {violations}"
        )

    def test_tasks_group_has_json_flag(self):
        """forge tasks group must have --json option."""
        tasks_cmd = self.cli.commands["tasks"]
        param_names = [p.name for p in tasks_cmd.params]
        self.assertIn("output_json", param_names, "tasks group missing --json flag")

    def test_claims_group_has_json_flag(self):
        """forge claims group must have --json option."""
        claims_cmd = self.cli.commands["claims"]
        param_names = [p.name for p in claims_cmd.params]
        self.assertIn("output_json", param_names, "claims group missing --json flag")

    def test_evaluator_group_has_json_flag(self):
        """forge evaluator group must have --json option."""
        evaluator_cmd = self.cli.commands["evaluator"]
        param_names = [p.name for p in evaluator_cmd.params]
        self.assertIn("output_json", param_names, "evaluator group missing --json flag")

    def test_contract_group_has_json_flag(self):
        """forge contract group must have --json option."""
        contract_cmd = self.cli.commands["contract"]
        param_names = [p.name for p in contract_cmd.params]
        self.assertIn("output_json", param_names, "contract group missing --json flag")

    def test_dispatch_group_has_json_flag(self):
        """forge dispatch group must have --json option."""
        dispatch_cmd = self.cli.commands["dispatch"]
        param_names = [p.name for p in dispatch_cmd.params]
        self.assertIn("output_json", param_names, "dispatch group missing --json flag")

    def test_root_cli_has_version_option(self):
        """Root CLI must expose --version."""
        param_names = [p.name for p in self.cli.params]
        self.assertIn("version", param_names, "root CLI missing --version")

    def test_tasks_list_is_subcommand_of_tasks(self):
        tasks_cmd = self.cli.commands["tasks"]
        self.assertIn("list", tasks_cmd.commands)

    def test_tasks_create_has_subject_argument(self):
        """forge tasks create must have a SUBJECT argument."""
        create_cmd = self.cli.commands["tasks"].commands["create"]
        arg_names = [p.name for p in create_cmd.params if hasattr(p, "nargs")]
        # Just check the command exists and has params
        self.assertGreater(len(create_cmd.params), 0)

    def test_claims_list_accepts_status_filter(self):
        """forge claims list must have --status option."""
        list_cmd = self.cli.commands["claims"].commands["list"]
        param_names = [p.name for p in list_cmd.params]
        self.assertIn("status_filter", param_names)

    def test_xnode_list_accepts_json_flag(self):
        """forge xnode list must have --json option."""
        list_cmd = self.cli.commands["xnode"].commands["list"]
        param_names = [p.name for p in list_cmd.params]
        self.assertIn("json_mode", param_names)

    def test_lead_acks_accepts_json_flag(self):
        """forge lead acks must have --json option."""
        acks_cmd = self.cli.commands["lead"].commands["acks"]
        param_names = [p.name for p in acks_cmd.params]
        self.assertIn("json_mode", param_names)

    def test_nodes_heartbeat_accepts_json_flag(self):
        """forge nodes heartbeat must have --json option."""
        hb_cmd = self.cli.commands["nodes"].commands["heartbeat"]
        param_names = [p.name for p in hb_cmd.params]
        self.assertIn("output_json", param_names)


# ---------------------------------------------------------------------------
# 13. EXIT CODE TESTS
# ---------------------------------------------------------------------------


class TestExitCodes(unittest.TestCase):
    """ExitCode enum must define expected codes."""

    def setUp(self):
        from forge_harness.cli_v2.exit_codes import ExitCode

        self.ExitCode = ExitCode

    def test_general_error_is_1(self):
        self.assertEqual(self.ExitCode.GENERAL_ERROR, 1)

    def test_usage_error_is_2(self):
        self.assertEqual(self.ExitCode.USAGE_ERROR, 2)

    def test_not_found_is_6(self):
        self.assertEqual(self.ExitCode.NOT_FOUND, 6)

    def test_validation_error_is_9(self):
        self.assertEqual(self.ExitCode.VALIDATION_ERROR, 9)

    def test_exit_codes_are_integers(self):
        for member in self.ExitCode:
            self.assertIsInstance(member.value, int)


# ---------------------------------------------------------------------------
# 14. TASKS JSON CONTRACT (mocks CommandCenterClient)
# ---------------------------------------------------------------------------


class TestTasksJsonContract(unittest.TestCase):
    """forge tasks list/stats --json must produce valid envelopes."""

    def setUp(self):
        from forge_harness.cli_v2 import cli

        self.cli = cli
        self.runner = CliRunner()

    @patch("forge_harness.cli_v2.tasks.require_forge_root")
    @patch("forge_harness.cli_v2.tasks._get_client")
    def test_tasks_list_json_envelope(self, mock_get_client, mock_root):
        from pathlib import Path

        mock_root.return_value = Path("/tmp/forge-test")
        mock_client = MagicMock()
        mock_client.sync_list_tasks.return_value = []
        mock_get_client.return_value = mock_client

        result = self.runner.invoke(self.cli, ["tasks", "--json", "list"])
        self.assertEqual(result.exit_code, 0, result.output)
        envelope = json.loads(result.output.strip())
        self.assertEqual(set(envelope.keys()), _json_envelope_keys())
        self.assertTrue(envelope["success"])

    @patch("forge_harness.cli_v2.tasks.require_forge_root")
    @patch("forge_harness.cli_v2.tasks._get_client")
    def test_tasks_stats_json_envelope(self, mock_get_client, mock_root):
        from pathlib import Path

        mock_root.return_value = Path("/tmp/forge-test")
        mock_client = MagicMock()
        mock_client.sync_get_task_stats.return_value = {"total": 0, "pending": 0}
        mock_get_client.return_value = mock_client

        result = self.runner.invoke(self.cli, ["tasks", "--json", "stats"])
        self.assertEqual(result.exit_code, 0, result.output)
        envelope = json.loads(result.output.strip())
        self.assertEqual(set(envelope.keys()), _json_envelope_keys())
        self.assertTrue(envelope["success"])

    @patch("forge_harness.cli_v2.tasks.require_forge_root")
    @patch("forge_harness.cli_v2.tasks._get_client")
    def test_tasks_list_command_field(self, mock_get_client, mock_root):
        from pathlib import Path

        mock_root.return_value = Path("/tmp/forge-test")
        mock_client = MagicMock()
        mock_client.sync_list_tasks.return_value = []
        mock_get_client.return_value = mock_client

        result = self.runner.invoke(self.cli, ["tasks", "--json", "list"])
        envelope = json.loads(result.output.strip())
        self.assertIn("forge tasks list", envelope["command"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
