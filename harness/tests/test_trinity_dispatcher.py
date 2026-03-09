from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from click.testing import CliRunner

from forge_harness.cli_v2 import cli
from forge_harness.trinity import (
    choose_route,
    dispatch_route,
    load_routing_bundle,
    normalize_envelope,
    watch_result_file,
)


def _write_routing_files(root: Path) -> None:
    routing_dir = root / ".forge" / "routing"
    routing_dir.mkdir(parents=True, exist_ok=True)
    routing_dir.joinpath("node-registry.yaml").write_text(
        """
nodes:
  prya:
    role: orchestrator
    allowed_workloads: [dispatch, monitoring, docs, small-edits, api-calls]
    forbidden_workloads: [go-test, coverage-waves, heavy-builds]
  sati:
    role: worker
    allowed_workloads: [go-test, coverage-waves, heavy-builds, complex-reasoning]
    forbidden_workloads: []
  nova:
    role: worker
    allowed_workloads: [go-test, coverage-waves, heavy-builds, ios-builds, research]
    forbidden_workloads: []
  vega:
    role: auxiliary
    allowed_workloads: [docs, small-edits, api-calls]
    forbidden_workloads: [heavy-builds, go-test]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    routing_dir.joinpath("agent-registry.yaml").write_text(
        """
agents:
  minimax:
    home_node: prya
    best_at: [docs, runbooks, small-edits, summaries]
    status: OK
  pi:
    home_node: prya
    best_at: [analysis, recommendations]
    status: OK
  kimi:
    home_node: sati
    best_at: [complex-edits, multi-file-refactors, research, coverage-waves, go-test]
    status: OK
  gemini:
    home_node: nova
    best_at: [research, audits, code-review]
    status: OK
""".strip()
        + "\n",
        encoding="utf-8",
    )
    routing_dir.joinpath("task-envelope-v1.yaml").write_text("schema: {}\n", encoding="utf-8")


def test_route_light_docs_prefers_prya_minimax(tmp_path: Path) -> None:
    _write_routing_files(tmp_path)
    bundle = load_routing_bundle(tmp_path)
    envelope = normalize_envelope(
        {"objective": "Update the runbook docs for Trinity routing.", "weight": "light"},
        source_name="test.yaml",
    )

    with patch("forge_harness.trinity.current_node", return_value="prya"):
        route = choose_route(envelope, bundle)

    assert route.node == "prya"
    assert route.agent == "minimax"
    assert route.dispatch_method == "local_dispatch"


def test_route_heavy_prefers_sati_kimi(tmp_path: Path) -> None:
    _write_routing_files(tmp_path)
    bundle = load_routing_bundle(tmp_path)
    envelope = normalize_envelope(
        {
            "objective": "Run coverage wave and go test validation for the daemon.",
            "weight": "heavy",
            "preferred_executor": "kimi",
        },
        source_name="heavy.yaml",
    )

    with patch("forge_harness.trinity.current_node", return_value="prya"):
        route = choose_route(envelope, bundle)

    assert route.node == "sati"
    assert route.agent == "kimi"
    assert route.dispatch_method == "cross_node_lead"


def test_dispatch_route_uses_local_dispatch_command(tmp_path: Path) -> None:
    _write_routing_files(tmp_path)
    bundle = load_routing_bundle(tmp_path)
    envelope = normalize_envelope(
        {"objective": "Update Trinity docs.", "weight": "light"},
        source_name="local.yaml",
    )
    with patch("forge_harness.trinity.current_node", return_value="prya"):
        route = choose_route(envelope, bundle)
        with patch(
            "forge_harness.trinity.subprocess.run",
            return_value=CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ) as run_mock:
            result = dispatch_route(tmp_path, envelope, route, perform_dispatch=True)

    assert result["dispatched"] is True
    run_mock.assert_called_once()
    assert run_mock.call_args.args[0][:4] == ["forge", "dispatch", "send", "forge:minimax"]
    assert (tmp_path / ".forge" / "dispatches" / f"trinity-{envelope.task_id}.md").exists()


def test_dispatch_route_uses_cross_node_lead_for_remote(tmp_path: Path) -> None:
    _write_routing_files(tmp_path)
    bundle = load_routing_bundle(tmp_path)
    envelope = normalize_envelope(
        {
            "objective": "Run coverage wave and go test validation for the daemon.",
            "weight": "heavy",
            "preferred_executor": "kimi",
        },
        source_name="remote.yaml",
    )
    with patch("forge_harness.trinity.current_node", return_value="prya"):
        route = choose_route(envelope, bundle)
        with patch(
            "forge_harness.trinity.subprocess.run",
            return_value=CompletedProcess(args=[], returncode=0, stdout="sent", stderr=""),
        ) as run_mock:
            result = dispatch_route(tmp_path, envelope, route, perform_dispatch=True)

    assert result["dispatched"] is True
    assert run_mock.call_args.args[0][:3] == ["forge", "lead", "send"]
    assert "--to-node" in run_mock.call_args.args[0]
    assert "sati" in run_mock.call_args.args[0]


def test_watch_result_file_pushes_payload(tmp_path: Path) -> None:
    result_dir = tmp_path / ".forge" / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": "TRINITY-TEST",
        "status": "ok",
        "agent": "kimi",
        "node": "sati",
        "summary": "done",
        "result_path": ".forge/results/TRINITY-TEST.json",
        "artifacts": ["README.md"],
    }
    result_dir.joinpath("TRINITY-TEST.json").write_text(json.dumps(payload), encoding="utf-8")

    with patch(
        "forge_harness.trinity.ingest_completion_payload",
        return_value=({"ok": True}, None),
    ) as ingest_mock:
        result = watch_result_file(tmp_path, task_id="TRINITY-TEST", push_grace_seconds=0.1)

    ingest_mock.assert_called_once()
    assert result["payload"]["task_id"] == "TRINITY-TEST"
    assert result["push_error"] is None


def test_cli_trinity_route_invokes_command(tmp_path: Path) -> None:
    _write_routing_files(tmp_path)
    envelope_path = tmp_path / "sample-envelope.yaml"
    envelope_path.write_text(
        "objective: Update the runbook docs for Trinity routing.\nweight: light\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    with patch(
        "forge_harness.trinity.subprocess.run",
        return_value=CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
    ):
        result = runner.invoke(
            cli,
            ["trinity", "route", str(envelope_path)],
            env={"FORGE_ROOT": str(tmp_path), "FORGE_NODE_ID": "prya"},
        )

    assert result.exit_code == 0, result.output
    assert "Computed route:" in result.output
    assert "agent=minimax" in result.output
