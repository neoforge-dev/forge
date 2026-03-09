"""Tests for forge lead acks and forge lead pending-acks subcommands.

Coverage targets:
  - lead_acks: no inbox, empty results, pending/acked/nacked/expired
  - lead_acks: --status, --agent, --node, --limit filters
  - lead_acks: --json output envelope
  - lead_acks: human-readable table output
  - lead_pending_acks: delegates correctly to lead_acks --status pending
  - helper functions: _load_inbox_messages, _classify_ack_status
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli
from forge_harness.cli_v2.lead import _classify_ack_status, _load_inbox_messages

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _write_root(fs_root: Path) -> None:
    """Create a .forge-root marker so require_forge_root() succeeds."""
    (fs_root / ".forge-root").write_text("", encoding="utf-8")


def _acks_dir(fs_root: Path) -> Path:
    return fs_root / ".forge" / "xnode" / "acks"


def _inbox_dir(fs_root: Path) -> Path:
    return fs_root / ".forge" / "xnode" / "lead-inbox"


def _make_message(
    message_id: str = "msg_test_001",
    task_id: str = "IS-001",
    to_node: str = "sati",
    from_node: str = "nova",
    priority: str = "medium",
    requires_ack: bool = True,
    expires_offset_seconds: int = 1800,
    source_agent: str = "orchestrator",
    target_agent: str = "orchestrator",
) -> dict:
    """Build a minimal lead.send envelope that matches the schema produced by lead_send."""
    now = datetime.now(UTC)
    return {
        "schema_version": "1.0",
        "message_id": message_id,
        "correlation_id": f"task_{task_id}",
        "idempotency_key": f"lead-send-{message_id}",
        "sent_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=expires_offset_seconds)).isoformat(),
        "source": {
            "node": from_node,
            "lead_window": f"forge:{from_node}",
            "agent": source_agent,
        },
        "target": {
            "node": to_node,
            "lead_window": f"forge:{to_node}",
            "agent": target_agent,
        },
        "priority": priority,
        "type": "lead.send",
        "requires_ack": requires_ack,
        "payload": {
            "task_id": task_id,
            "intent": "implement",
            "summary": "test message",
            "write_scope": {"repo": "FORGE", "branch": "", "submodule": ""},
            "acceptance_criteria": [],
            "attachments": [],
        },
    }


def _write_inbox_message(fs_root: Path, node: str, message: dict) -> Path:
    """Append a message envelope to the node's inbox JSONL file."""
    inbox = _inbox_dir(fs_root) / f"{node}.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    with inbox.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(message) + "\n")
    return inbox


def _write_ack(fs_root: Path, message_id: str, ack_status: str = "ack", note: str = "") -> Path:
    """Write a durable ack file for a given message_id."""
    acks = _acks_dir(fs_root)
    acks.mkdir(parents=True, exist_ok=True)
    ack_file = acks / f"{message_id}.json"
    ack_file.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "message_id": message_id,
                "type": "lead.ack",
                "status": ack_status,
                "acknowledged_by_node": "sati",
                "acknowledged_at": datetime.now(UTC).isoformat(),
                "note": note,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return ack_file


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestLoadInboxMessages:
    def test_returns_empty_when_no_inbox_dir(self, tmp_path: Path) -> None:
        dirs = {
            "lead_inbox": tmp_path / "lead-inbox",
            "acks": tmp_path / "acks",
        }
        assert _load_inbox_messages(dirs) == []

    def test_loads_messages_from_multiple_nodes(self, tmp_path: Path) -> None:
        inbox_dir = tmp_path / "lead-inbox"
        inbox_dir.mkdir(parents=True)

        msg_a = _make_message(message_id="msg_a", to_node="sati")
        msg_b = _make_message(message_id="msg_b", to_node="nova")

        (inbox_dir / "sati.jsonl").write_text(json.dumps(msg_a) + "\n", encoding="utf-8")
        (inbox_dir / "nova.jsonl").write_text(json.dumps(msg_b) + "\n", encoding="utf-8")

        dirs = {"lead_inbox": inbox_dir, "acks": tmp_path / "acks"}
        messages = _load_inbox_messages(dirs)
        assert len(messages) == 2
        ids = {m["message_id"] for m in messages}
        assert ids == {"msg_a", "msg_b"}

    def test_skips_malformed_jsonl(self, tmp_path: Path) -> None:
        inbox_dir = tmp_path / "lead-inbox"
        inbox_dir.mkdir(parents=True)

        valid_msg = _make_message(message_id="msg_ok")
        (inbox_dir / "sati.jsonl").write_text(
            "not json\n" + json.dumps(valid_msg) + "\n",
            encoding="utf-8",
        )

        dirs = {"lead_inbox": inbox_dir, "acks": tmp_path / "acks"}
        # Malformed line raises JSONDecodeError inside _read_jsonl, which
        # propagates to _load_inbox_messages and is caught per-file.  The
        # entire file is skipped when the first bad line causes an error.
        messages = _load_inbox_messages(dirs)
        # One file was skipped due to bad line; result is 0 messages.
        assert len(messages) == 0


class TestClassifyAckStatus:
    def test_pending_when_no_ack_file(self, tmp_path: Path) -> None:
        acks_dir = tmp_path / "acks"
        acks_dir.mkdir()
        msg = _make_message(message_id="msg_pending")
        now = datetime.now(UTC)
        assert _classify_ack_status(msg, acks_dir, now) == "pending"

    def test_acked_when_ack_file_with_status_ack(self, tmp_path: Path) -> None:
        acks_dir = tmp_path / "acks"
        acks_dir.mkdir()
        msg = _make_message(message_id="msg_acked")
        # Write the ack file directly into acks_dir (not via _write_ack which
        # uses the full .forge/xnode/acks path).
        ack_file = acks_dir / "msg_acked.json"
        ack_file.write_text(
            json.dumps({"status": "ack", "acknowledged_by_node": "sati", "note": ""}),
            encoding="utf-8",
        )
        now = datetime.now(UTC)
        assert _classify_ack_status(msg, acks_dir, now) == "acked"

    def test_nacked_when_ack_file_with_status_nack(self, tmp_path: Path) -> None:
        acks_dir = tmp_path / "acks"
        acks_dir.mkdir()
        msg = _make_message(message_id="msg_nacked")
        ack_file = acks_dir / "msg_nacked.json"
        ack_file.write_text(
            json.dumps({"status": "nack", "acknowledged_by_node": "sati", "note": "rejected"}),
            encoding="utf-8",
        )
        now = datetime.now(UTC)
        assert _classify_ack_status(msg, acks_dir, now) == "nacked"

    def test_expired_when_past_expiry_and_no_ack(self, tmp_path: Path) -> None:
        acks_dir = tmp_path / "acks"
        acks_dir.mkdir()
        past = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        msg = _make_message(message_id="msg_expired")
        msg["expires_at"] = past
        now = datetime.now(UTC)
        assert _classify_ack_status(msg, acks_dir, now) == "expired"

    def test_acked_overrides_expired(self, tmp_path: Path) -> None:
        """If an ack file exists for an expired message, status is still 'acked'."""
        acks_dir = tmp_path / "acks"
        acks_dir.mkdir()
        past = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        msg = _make_message(message_id="msg_acked_expired")
        msg["expires_at"] = past
        ack_file = acks_dir / "msg_acked_expired.json"
        ack_file.write_text(
            json.dumps({"status": "ack", "acknowledged_by_node": "sati", "note": ""}),
            encoding="utf-8",
        )
        now = datetime.now(UTC)
        assert _classify_ack_status(msg, acks_dir, now) == "acked"

    def test_pending_when_bad_expires_at(self, tmp_path: Path) -> None:
        acks_dir = tmp_path / "acks"
        acks_dir.mkdir()
        msg = _make_message(message_id="msg_bad_exp")
        msg["expires_at"] = "not-a-date"
        now = datetime.now(UTC)
        assert _classify_ack_status(msg, acks_dir, now) == "pending"


# ---------------------------------------------------------------------------
# Integration tests via CLI runner
# ---------------------------------------------------------------------------


class TestLeadAcksCommand:
    """Tests for `forge lead acks`."""

    def test_empty_inbox_returns_info_message(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            _write_root(Path("."))
            result = runner.invoke(cli, ["lead", "acks"])
            assert result.exit_code == 0
            assert "No acknowledgement records found" in result.output

    def test_empty_inbox_json_mode(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            _write_root(Path("."))
            result = runner.invoke(cli, ["lead", "acks", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["success"] is True
            assert data["data"]["count"] == 0
            assert data["data"]["acks"] == []

    def test_pending_message_appears_in_acks(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            msg = _make_message(message_id="msg_20260222_pending")
            _write_inbox_message(root, "sati", msg)

            result = runner.invoke(cli, ["lead", "acks", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 1
            entry = data["data"]["acks"][0]
            assert entry["message_id"] == "msg_20260222_pending"
            assert entry["ack_status"] == "pending"
            assert entry["task_id"] == "IS-001"

    def test_acked_message_status(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            msg = _make_message(message_id="msg_acked_001")
            _write_inbox_message(root, "sati", msg)
            _write_ack(root, "msg_acked_001", "ack", note="All good")

            result = runner.invoke(cli, ["lead", "acks", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            entry = data["data"]["acks"][0]
            assert entry["ack_status"] == "acked"
            assert entry["ack_note"] == "All good"
            assert entry["acknowledged_by_node"] == "sati"

    def test_nacked_message_status(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            msg = _make_message(message_id="msg_nacked_001")
            _write_inbox_message(root, "sati", msg)
            _write_ack(root, "msg_nacked_001", "nack", note="Rejected: wrong scope")

            result = runner.invoke(cli, ["lead", "acks", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            entry = data["data"]["acks"][0]
            assert entry["ack_status"] == "nacked"
            assert "Rejected" in entry["ack_note"]

    def test_expired_message_status(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            msg = _make_message(message_id="msg_expired_001", expires_offset_seconds=-120)
            _write_inbox_message(root, "sati", msg)

            result = runner.invoke(cli, ["lead", "acks", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            entry = data["data"]["acks"][0]
            assert entry["ack_status"] == "expired"

    # ------------------------------------------------------------------
    # Filter: --status
    # ------------------------------------------------------------------

    def test_status_filter_pending_excludes_acked(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            pending_msg = _make_message(message_id="msg_pend")
            acked_msg = _make_message(message_id="msg_ackd")
            _write_inbox_message(root, "sati", pending_msg)
            _write_inbox_message(root, "sati", acked_msg)
            _write_ack(root, "msg_ackd", "ack")

            result = runner.invoke(cli, ["lead", "acks", "--status", "pending", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 1
            assert data["data"]["acks"][0]["message_id"] == "msg_pend"

    def test_status_filter_acked_excludes_pending(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            _write_inbox_message(root, "sati", _make_message(message_id="msg_pend2"))
            acked_msg = _make_message(message_id="msg_ackd2")
            _write_inbox_message(root, "sati", acked_msg)
            _write_ack(root, "msg_ackd2", "ack")

            result = runner.invoke(cli, ["lead", "acks", "--status", "acked", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 1
            assert data["data"]["acks"][0]["message_id"] == "msg_ackd2"

    def test_status_filter_nacked(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            msg = _make_message(message_id="msg_nackd3")
            _write_inbox_message(root, "sati", msg)
            _write_ack(root, "msg_nackd3", "nack")

            result = runner.invoke(cli, ["lead", "acks", "--status", "nacked", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 1
            assert data["data"]["acks"][0]["ack_status"] == "nacked"

    def test_status_filter_expired(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            expired = _make_message(message_id="msg_expd4", expires_offset_seconds=-300)
            pending = _make_message(message_id="msg_pend4")
            _write_inbox_message(root, "sati", expired)
            _write_inbox_message(root, "sati", pending)

            result = runner.invoke(cli, ["lead", "acks", "--status", "expired", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 1
            assert data["data"]["acks"][0]["message_id"] == "msg_expd4"

    # ------------------------------------------------------------------
    # Filter: --agent
    # ------------------------------------------------------------------

    def test_agent_filter_matches_source_agent(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            msg_orch = _make_message(
                message_id="msg_orch", source_agent="orchestrator", target_agent="worker"
            )
            msg_worker = _make_message(
                message_id="msg_wrk", source_agent="worker", target_agent="worker"
            )
            _write_inbox_message(root, "sati", msg_orch)
            _write_inbox_message(root, "sati", msg_worker)

            result = runner.invoke(cli, ["lead", "acks", "--agent", "orchestrator", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 1
            assert data["data"]["acks"][0]["message_id"] == "msg_orch"

    def test_agent_filter_matches_target_agent(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            msg = _make_message(
                message_id="msg_tgt_agent",
                source_agent="orchestrator",
                target_agent="ios-agent",
            )
            _write_inbox_message(root, "sati", msg)

            result = runner.invoke(cli, ["lead", "acks", "--agent", "ios-agent", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 1

    def test_agent_filter_no_match_returns_empty(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            _write_inbox_message(root, "sati", _make_message(source_agent="orchestrator"))

            result = runner.invoke(cli, ["lead", "acks", "--agent", "nonexistent-agent", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 0

    # ------------------------------------------------------------------
    # Filter: --node
    # ------------------------------------------------------------------

    def test_node_filter_matches_target_node(self) -> None:
        """Node filter matches any message involving the node (source OR target).

        msg_to_sati: nova -> sati  (sati is target)
        msg_to_nova: sati -> nova  (sati is source)
        Both match --node sati.  A message with no sati involvement is excluded.
        """
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            msg_sati = _make_message(message_id="msg_to_sati", to_node="sati", from_node="nova")
            msg_nova = _make_message(message_id="msg_to_nova", to_node="nova", from_node="sati")
            msg_prya = _make_message(message_id="msg_to_prya", to_node="prya", from_node="nova")
            _write_inbox_message(root, "sati", msg_sati)
            _write_inbox_message(root, "nova", msg_nova)
            _write_inbox_message(root, "prya", msg_prya)

            result = runner.invoke(cli, ["lead", "acks", "--node", "sati", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            # Both msg_to_sati (target=sati) and msg_to_nova (source=sati) match.
            # msg_to_prya (nova->prya) should be excluded.
            ids = {e["message_id"] for e in data["data"]["acks"]}
            assert "msg_to_prya" not in ids
            assert "msg_to_sati" in ids
            assert "msg_to_nova" in ids
            assert data["data"]["count"] == 2

    def test_node_filter_matches_source_node(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            # Message sent FROM nova TO sati
            msg = _make_message(message_id="msg_from_nova", to_node="sati", from_node="nova")
            _write_inbox_message(root, "sati", msg)

            # Filter by source node nova — should match.
            result = runner.invoke(cli, ["lead", "acks", "--node", "nova", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 1

    # ------------------------------------------------------------------
    # Filter: --limit
    # ------------------------------------------------------------------

    def test_limit_caps_results(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            for i in range(10):
                _write_inbox_message(root, "sati", _make_message(message_id=f"msg_lim_{i:03d}"))

            result = runner.invoke(cli, ["lead", "acks", "--limit", "3", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 3

    def test_limit_defaults_to_20(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            for i in range(25):
                _write_inbox_message(root, "sati", _make_message(message_id=f"msg_def_{i:03d}"))

            result = runner.invoke(cli, ["lead", "acks", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 20

    # ------------------------------------------------------------------
    # Human-readable output
    # ------------------------------------------------------------------

    def test_human_output_contains_message_id(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            _write_inbox_message(root, "sati", _make_message(message_id="msg_human_001"))

            result = runner.invoke(cli, ["lead", "acks"])
            assert result.exit_code == 0
            assert "msg_human_001" in result.output

    def test_human_output_shows_status_tag(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            _write_inbox_message(root, "sati", _make_message(message_id="msg_human_002"))

            result = runner.invoke(cli, ["lead", "acks"])
            assert result.exit_code == 0
            assert "[pending]" in result.output

    def test_human_output_shows_acked_tag(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            msg = _make_message(message_id="msg_human_ack")
            _write_inbox_message(root, "sati", msg)
            _write_ack(root, "msg_human_ack", "ack")

            result = runner.invoke(cli, ["lead", "acks"])
            assert result.exit_code == 0
            assert "[acked]" in result.output

    def test_human_output_shows_nacked_tag(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            msg = _make_message(message_id="msg_human_nack")
            _write_inbox_message(root, "sati", msg)
            _write_ack(root, "msg_human_nack", "nack")

            result = runner.invoke(cli, ["lead", "acks"])
            assert result.exit_code == 0
            assert "[nacked]" in result.output

    def test_human_output_shows_expired_tag(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            msg = _make_message(message_id="msg_human_exp", expires_offset_seconds=-300)
            _write_inbox_message(root, "sati", msg)

            result = runner.invoke(cli, ["lead", "acks"])
            assert result.exit_code == 0
            assert "[expired]" in result.output

    # ------------------------------------------------------------------
    # JSON envelope structure
    # ------------------------------------------------------------------

    def test_json_envelope_has_required_fields(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            _write_inbox_message(root, "sati", _make_message(message_id="msg_env_001"))

            result = runner.invoke(cli, ["lead", "acks", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "success" in data
            assert "data" in data
            assert "timestamp" in data
            assert data["command"] == "forge lead acks"
            assert data["data"]["status_filter"] is None
            assert data["data"]["agent_filter"] is None
            assert data["data"]["node_filter"] is None

    def test_json_entry_has_all_expected_keys(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            msg = _make_message(message_id="msg_keys_001")
            _write_inbox_message(root, "sati", msg)
            _write_ack(root, "msg_keys_001", "ack", note="ok")

            result = runner.invoke(cli, ["lead", "acks", "--json"])
            assert result.exit_code == 0
            entry = json.loads(result.output)["data"]["acks"][0]
            expected_keys = {
                "message_id",
                "correlation_id",
                "sent_at",
                "expires_at",
                "priority",
                "source_node",
                "target_node",
                "source_agent",
                "target_agent",
                "requires_ack",
                "task_id",
                "ack_status",
                "ack_note",
                "acknowledged_at",
                "acknowledged_by_node",
            }
            assert expected_keys.issubset(entry.keys())

    def test_json_filters_reflected_in_envelope(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            result = runner.invoke(
                cli,
                [
                    "lead",
                    "acks",
                    "--status",
                    "pending",
                    "--agent",
                    "orchestrator",
                    "--node",
                    "sati",
                    "--json",
                ],
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["status_filter"] == "pending"
            assert data["data"]["agent_filter"] == "orchestrator"
            assert data["data"]["node_filter"] == "sati"

    # ------------------------------------------------------------------
    # End-to-end: send + ack + acks
    # ------------------------------------------------------------------

    def test_end_to_end_send_then_acks_shows_pending(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            _write_root(Path("."))
            send_result = runner.invoke(
                cli,
                [
                    "lead",
                    "send",
                    "--to-node",
                    "sati",
                    "--task-id",
                    "IS-e2e-001",
                    "--summary",
                    "E2E test message",
                    "--no-realtime",
                ],
            )
            assert send_result.exit_code == 0

            acks_result = runner.invoke(cli, ["lead", "acks", "--json"])
            assert acks_result.exit_code == 0
            data = json.loads(acks_result.output)
            assert data["data"]["count"] == 1
            assert data["data"]["acks"][0]["ack_status"] == "pending"
            assert data["data"]["acks"][0]["task_id"] == "IS-e2e-001"

    def test_end_to_end_send_ack_then_acks_shows_acked(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            _write_root(Path("."))
            send_result = runner.invoke(
                cli,
                [
                    "lead",
                    "send",
                    "--to-node",
                    "sati",
                    "--task-id",
                    "IS-e2e-002",
                    "--summary",
                    "E2E ack flow",
                    "--no-realtime",
                    "--json",
                ],
            )
            assert send_result.exit_code == 0
            send_data = json.loads(send_result.output)
            message_id = send_data["data"]["message_id"]

            ack_result = runner.invoke(
                cli,
                [
                    "lead",
                    "ack",
                    "--message-id",
                    message_id,
                    "--status",
                    "ack",
                    "--note",
                    "Accepted",
                    "--no-realtime",
                ],
            )
            assert ack_result.exit_code == 0

            acks_result = runner.invoke(cli, ["lead", "acks", "--status", "acked", "--json"])
            assert acks_result.exit_code == 0
            data = json.loads(acks_result.output)
            assert data["data"]["count"] == 1
            assert data["data"]["acks"][0]["message_id"] == message_id
            assert data["data"]["acks"][0]["ack_note"] == "Accepted"


# ---------------------------------------------------------------------------
# Tests for `forge lead pending-acks`
# ---------------------------------------------------------------------------


class TestLeadPendingAcksCommand:
    """Tests for `forge lead pending-acks` alias."""

    def test_pending_acks_shows_only_pending(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            pending = _make_message(message_id="msg_pa_pend")
            acked = _make_message(message_id="msg_pa_ackd")
            _write_inbox_message(root, "sati", pending)
            _write_inbox_message(root, "sati", acked)
            _write_ack(root, "msg_pa_ackd", "ack")

            result = runner.invoke(cli, ["lead", "pending-acks", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 1
            assert data["data"]["acks"][0]["message_id"] == "msg_pa_pend"
            assert data["data"]["acks"][0]["ack_status"] == "pending"

    def test_pending_acks_empty_inbox(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            _write_root(Path("."))
            result = runner.invoke(cli, ["lead", "pending-acks"])
            assert result.exit_code == 0
            assert "No acknowledgement records found" in result.output

    def test_pending_acks_respects_node_filter(self) -> None:
        """Node filter only excludes messages with no involvement from the specified node."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            # nova -> sati: sati is the target
            msg_sati = _make_message(message_id="msg_pa_sati", to_node="sati", from_node="nova")
            # nova -> prya: sati has no involvement
            msg_prya = _make_message(message_id="msg_pa_prya", to_node="prya", from_node="nova")
            _write_inbox_message(root, "sati", msg_sati)
            _write_inbox_message(root, "prya", msg_prya)

            result = runner.invoke(cli, ["lead", "pending-acks", "--node", "sati", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 1
            assert data["data"]["acks"][0]["message_id"] == "msg_pa_sati"

    def test_pending_acks_respects_agent_filter(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            msg = _make_message(
                message_id="msg_pa_agent",
                source_agent="orchestrator",
                target_agent="backend",
            )
            _write_inbox_message(root, "sati", msg)

            result = runner.invoke(cli, ["lead", "pending-acks", "--agent", "backend", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 1

    def test_pending_acks_respects_limit(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            for i in range(5):
                _write_inbox_message(root, "sati", _make_message(message_id=f"msg_pa_lim_{i}"))

            result = runner.invoke(cli, ["lead", "pending-acks", "--limit", "2", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["data"]["count"] == 2

    def test_pending_acks_human_output_shows_pending_tag(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path(".")
            _write_root(root)
            _write_inbox_message(root, "sati", _make_message(message_id="msg_pa_human"))

            result = runner.invoke(cli, ["lead", "pending-acks"])
            assert result.exit_code == 0
            assert "[pending]" in result.output
            assert "msg_pa_human" in result.output
