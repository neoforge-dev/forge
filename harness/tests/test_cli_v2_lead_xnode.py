"""Tests for forge lead and forge xnode CLI stubs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from forge_harness.cli_v2 import cli


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestLeadCommands:
    def test_lead_send_creates_inbox_message(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".forge-root").write_text("", encoding="utf-8")
            result = runner.invoke(
                cli,
                [
                    "lead",
                    "send",
                    "--to-node",
                    "sati",
                    "--task-id",
                    "IS-219",
                    "--summary",
                    "Implement telemetry endpoint hardening",
                ],
            )
            assert result.exit_code == 0

            inbox = Path(".forge/xnode/lead-inbox/sati.jsonl")
            assert inbox.exists()
            rows = _read_jsonl(inbox)
            assert len(rows) == 1
            assert rows[0]["type"] == "lead.send"
            assert rows[0]["payload"]["task_id"] == "IS-219"

    def test_lead_send_realtime_without_durable_writes_realtime_outbox(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".forge-root").write_text("", encoding="utf-8")
            result = runner.invoke(
                cli,
                [
                    "lead",
                    "send",
                    "--to-node",
                    "sati",
                    "--task-id",
                    "IS-220",
                    "--summary",
                    "Realtime only test",
                    "--realtime",
                    "--no-durable",
                ],
            )
            assert result.exit_code == 0

            assert not Path(".forge/xnode/lead-inbox/sati.jsonl").exists()
            rt_files = list(Path(".forge/xnode/realtime-outbox").glob("*.jsonl"))
            assert len(rt_files) == 1
            rows = _read_jsonl(rt_files[0])
            assert len(rows) == 1
            assert rows[0]["event_type"] == "lead.send"

    def test_lead_send_keeps_durable_write_on_realtime_delivery(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".forge-root").write_text("", encoding="utf-8")
            with patch(
                "forge_harness.cli_v2.lead.xnode_realtime_emit",
                return_value={
                    "recorded": True,
                    "outbox": ".forge/xnode/realtime-outbox/test.jsonl",
                    "delivered": True,
                    "transport": "http-post",
                    "error": None,
                },
            ):
                result = runner.invoke(
                    cli,
                    [
                        "lead",
                        "send",
                        "--to-node",
                        "sati",
                        "--task-id",
                        "IS-221",
                        "--summary",
                        "Durable + realtime must both persist",
                        "--json",
                    ],
                )
            assert result.exit_code == 0
            payload = json.loads(result.output)
            assert payload["data"]["durable_requested"] is True
            assert payload["data"]["durable_written"] is True
            assert payload["data"]["realtime"]["delivered"] is True

            inbox = Path(".forge/xnode/lead-inbox/sati.jsonl")
            assert inbox.exists()
            rows = _read_jsonl(inbox)
            assert len(rows) == 1
            assert rows[0]["payload"]["task_id"] == "IS-221"

    def test_lead_send_uses_message_unique_idempotency_key(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".forge-root").write_text("", encoding="utf-8")
            first = runner.invoke(
                cli,
                [
                    "lead",
                    "send",
                    "--to-node",
                    "sati",
                    "--task-id",
                    "IS-300",
                    "--summary",
                    "first send",
                ],
            )
            second = runner.invoke(
                cli,
                [
                    "lead",
                    "send",
                    "--to-node",
                    "sati",
                    "--task-id",
                    "IS-300",
                    "--summary",
                    "second send",
                ],
            )
            assert first.exit_code == 0
            assert second.exit_code == 0

            rows = _read_jsonl(Path(".forge/xnode/lead-inbox/sati.jsonl"))
            assert len(rows) == 2
            first_key = rows[0]["idempotency_key"]
            second_key = rows[1]["idempotency_key"]
            assert first_key != second_key
            assert first_key.startswith("lead-send-msg_")
            assert second_key.startswith("lead-send-msg_")

    def test_lead_send_respects_custom_idempotency_key(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".forge-root").write_text("", encoding="utf-8")
            result = runner.invoke(
                cli,
                [
                    "lead",
                    "send",
                    "--to-node",
                    "sati",
                    "--task-id",
                    "IS-301",
                    "--summary",
                    "custom idem key",
                    "--idempotency-key",
                    "semantic-is-301",
                    "--json",
                ],
            )
            assert result.exit_code == 0
            payload = json.loads(result.output)
            assert payload["data"]["idempotency_key"] == "semantic-is-301"

            rows = _read_jsonl(Path(".forge/xnode/lead-inbox/sati.jsonl"))
            assert rows[0]["idempotency_key"] == "semantic-is-301"

    def test_lead_handoff_create_list_accept(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".forge-root").write_text("", encoding="utf-8")

            create_result = runner.invoke(
                cli,
                [
                    "lead",
                    "handoff",
                    "create",
                    "--to-node",
                    "nova",
                    "--from-task",
                    "IS-100",
                    "--to-task",
                    "IS-101",
                    "--summary",
                    "Continue iOS release prep",
                ],
            )
            assert create_result.exit_code == 0

            handoff_files = list(Path(".forge/xnode/handoffs").glob("*.json"))
            assert len(handoff_files) == 1
            handoff_id = json.loads(handoff_files[0].read_text(encoding="utf-8"))["handoff_id"]

            list_result = runner.invoke(
                cli,
                ["lead", "handoff", "list", "--status", "pending"],
            )
            assert list_result.exit_code == 0
            assert handoff_id in list_result.output

            accept_result = runner.invoke(
                cli,
                ["lead", "handoff", "accept", "--handoff-id", handoff_id],
            )
            assert accept_result.exit_code == 0

            payload = json.loads(handoff_files[0].read_text(encoding="utf-8"))
            assert payload["status"] == "accepted"

    def test_lead_inbox_json_lists_messages(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".forge-root").write_text("", encoding="utf-8")
            send_result = runner.invoke(
                cli,
                [
                    "lead",
                    "send",
                    "--to-node",
                    "sati",
                    "--task-id",
                    "IS-300",
                    "--summary",
                    "Check queue depth",
                ],
            )
            assert send_result.exit_code == 0

            inbox_result = runner.invoke(
                cli,
                ["lead", "inbox", "--node", "sati", "--json"],
            )
            assert inbox_result.exit_code == 0
            payload = json.loads(inbox_result.output)
            assert payload["success"] is True
            assert payload["data"]["count"] == 1
            assert payload["data"]["messages"][0]["payload"]["task_id"] == "IS-300"

    def test_lead_ack_writes_durable_record(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".forge-root").write_text("", encoding="utf-8")
            ack_result = runner.invoke(
                cli,
                [
                    "lead",
                    "ack",
                    "--message-id",
                    "msg_20260220_000001_test",
                    "--status",
                    "ack",
                    "--note",
                    "Accepted by receiving lead",
                ],
            )
            assert ack_result.exit_code == 0

            ack_file = Path(".forge/xnode/acks/msg_20260220_000001_test.json")
            assert ack_file.exists()
            ack_payload = json.loads(ack_file.read_text(encoding="utf-8"))
            assert ack_payload["type"] == "lead.ack"
            assert ack_payload["status"] == "ack"


class TestXnodeRelay:
    def test_relay_requires_exception_flag(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".forge-root").write_text("", encoding="utf-8")
            result = runner.invoke(
                cli,
                [
                    "xnode",
                    "relay",
                    "--from-agent",
                    "forge:sati:backend",
                    "--to-agent",
                    "forge:nova:ios",
                    "--task-id",
                    "IS-219",
                    "--reason",
                    "urgent_unblock",
                    "--message",
                    "Need API contract clarification",
                ],
            )
            assert result.exit_code != 0

    def test_relay_writes_exception_record(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".forge-root").write_text("", encoding="utf-8")
            result = runner.invoke(
                cli,
                [
                    "xnode",
                    "relay",
                    "--from-agent",
                    "forge:sati:backend",
                    "--to-agent",
                    "forge:nova:ios",
                    "--task-id",
                    "IS-219",
                    "--reason",
                    "urgent_unblock",
                    "--message",
                    "Need API contract clarification",
                    "--exception",
                ],
            )
            assert result.exit_code == 0

            out_dir = Path(".forge/xnode/exceptions")
            files = list(out_dir.glob("*.jsonl"))
            assert len(files) == 1
            rows = _read_jsonl(files[0])
            assert len(rows) == 1
            assert rows[0]["type"] == "xnode.relay.exception"
            assert rows[0]["task_id"] == "IS-219"

    def test_relay_realtime_without_durable_writes_realtime_outbox(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".forge-root").write_text("", encoding="utf-8")
            result = runner.invoke(
                cli,
                [
                    "xnode",
                    "relay",
                    "--from-agent",
                    "forge:sati:backend",
                    "--to-agent",
                    "forge:nova:ios",
                    "--task-id",
                    "IS-230",
                    "--reason",
                    "urgent_unblock",
                    "--message",
                    "Need API contract clarification",
                    "--exception",
                    "--realtime",
                    "--no-durable",
                ],
            )
            assert result.exit_code == 0

            out_dir = Path(".forge/xnode/exceptions")
            assert not list(out_dir.glob("*.jsonl"))
            rt_files = list(Path(".forge/xnode/realtime-outbox").glob("*.jsonl"))
            assert len(rt_files) == 1
            rows = _read_jsonl(rt_files[0])
            assert len(rows) == 1
            assert rows[0]["event_type"] == "xnode.relay.exception"
