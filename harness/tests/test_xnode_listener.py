"""
Unit Tests for XNodeListener
=============================

Tests for forge_harness.xnode_listener module — the cross-node SSE listener
that connects to the Command Center and processes xnode events.

Coverage targets (>= 70%):
- XNodeListener construction and directory setup
- _mark_seen / LRU eviction
- _seed_seen_ids — reading existing inbox files
- _should_process — all filtering branches
- _dispatch_sse_event — parse, dedup, routing
- _handle_lead_send — idempotency, file write, tmux notify
- _handle_lead_ack — file write, tmux notify
- _handle_handoff — file write, tmux notify
- _handle_exception — file write
- _handle_event — routing dispatch table
- _resolve_target_node
- _inbox_contains_message_id
- _notify_lead_tmux — disabled / tmux unavailable / session missing / success / error
- _write_heartbeat — normal + OSError
- _process_outbox_file — delivered, abandoned (ttl + bad ts), pending
- _mark_outbox_abandoned — idempotent
- _retry_single_record — success, failure
- _rewrite_jsonl — atomic swap
- Module-level helpers: _now_iso, _now_slug, _append_jsonl, _write_json, _redact_secrets
- start() — once=True fast path, error/backoff/reset paths
- stop()
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub aiohttp before importing xnode_listener so the module can be imported
# in environments where aiohttp may not be available, and to prevent real
# network calls from test code.
# ---------------------------------------------------------------------------
_aiohttp_stub = MagicMock()
_aiohttp_stub.ClientSession = MagicMock()
_aiohttp_stub.ClientTimeout = MagicMock()
sys.modules.setdefault("aiohttp", _aiohttp_stub)

from forge_harness.xnode_listener import (  # noqa: E402
    HANDLED_EVENT_TYPES,
    XNodeListener,
    _append_jsonl,
    _now_iso,
    _now_slug,
    _redact_secrets,
    _write_json,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def forge_root(tmp_path: Path) -> Path:
    """Return a temporary FORGE root directory."""
    return tmp_path / "FORGE"


@pytest.fixture()
def listener(forge_root: Path) -> XNodeListener:
    """Return an XNodeListener with tmux notifications disabled."""
    return XNodeListener(
        command_center_url="http://cc.local:8080",
        token="test-token",
        forge_root=forge_root,
        hostname="testnode",
        notify_lead=False,
    )


# ---------------------------------------------------------------------------
# Construction & directory setup
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_strips_trailing_slash_from_url(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local:8080/",
            token="tok",
            forge_root=forge_root,
            hostname="n1",
        )
        assert lst.url == "http://cc.local:8080"

    def test_hostname_lowercased_and_stripped(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="  Nova  ",
        )
        assert lst.hostname == "nova"

    def test_default_lead_window_uses_hostname(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="prya",
        )
        assert lst.lead_window == "forge:prya"

    def test_custom_lead_window_accepted(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="prya",
            lead_window="forge:custom",
        )
        assert lst.lead_window == "forge:custom"

    def test_storage_directories_created(self, listener: XNodeListener) -> None:
        expected_keys = ["root", "lead_inbox", "handoffs", "acks", "exceptions", "realtime_outbox"]
        for key in expected_keys:
            assert listener._dirs[key].is_dir(), f"Directory '{key}' was not created"

    def test_running_is_false_on_init(self, listener: XNodeListener) -> None:
        assert listener._running is False

    def test_backoff_defaults(self, listener: XNodeListener) -> None:
        assert listener._backoff == 1.0
        assert listener._max_backoff == 30.0

    def test_seen_message_ids_starts_empty(self, listener: XNodeListener) -> None:
        assert isinstance(listener._seen_message_ids, dict)
        assert len(listener._seen_message_ids) == 0

    def test_channels_defaults_to_none(self, listener: XNodeListener) -> None:
        assert listener.channels is None

    def test_channels_stored_when_provided(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="n",
            channels=["lead.n.inbox"],
        )
        assert lst.channels == ["lead.n.inbox"]

    def test_uses_socket_hostname_when_not_specified(self, forge_root: Path) -> None:
        import socket

        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
        )
        assert lst.hostname == socket.gethostname().strip().lower()

    def test_notify_lead_true_by_default(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="n1",
        )
        assert lst.notify_lead is True

    def test_notify_lead_false_when_specified(self, listener: XNodeListener) -> None:
        assert listener.notify_lead is False

    def test_last_event_id_starts_empty(self, listener: XNodeListener) -> None:
        assert listener._last_event_id == ""

    def test_max_seen_ids_set(self, listener: XNodeListener) -> None:
        assert listener._MAX_SEEN_IDS == 10_000


# ---------------------------------------------------------------------------
# _mark_seen — bounded LRU
# ---------------------------------------------------------------------------


class TestMarkSeen:
    def test_adds_id(self, listener: XNodeListener) -> None:
        listener._mark_seen("msg-1")
        assert "msg-1" in listener._seen_message_ids

    def test_evicts_oldest_when_over_limit(self, listener: XNodeListener) -> None:
        listener._MAX_SEEN_IDS = 3
        for i in range(4):
            listener._mark_seen(f"msg-{i}")
        assert "msg-0" not in listener._seen_message_ids
        assert "msg-3" in listener._seen_message_ids
        assert len(listener._seen_message_ids) == 3

    def test_same_id_does_not_grow_set(self, listener: XNodeListener) -> None:
        listener._mark_seen("dup")
        listener._mark_seen("dup")
        assert len(listener._seen_message_ids) == 1

    def test_stays_within_limit_after_many_inserts(self, listener: XNodeListener) -> None:
        listener._MAX_SEEN_IDS = 5
        for i in range(20):
            listener._mark_seen(f"msg-{i}")
        assert len(listener._seen_message_ids) == 5

    def test_preserves_insertion_order(self, listener: XNodeListener) -> None:
        listener._MAX_SEEN_IDS = 5
        for i in range(5):
            listener._mark_seen(f"id-{i}")
        keys = list(listener._seen_message_ids.keys())
        assert keys == [f"id-{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# _seed_seen_ids
# ---------------------------------------------------------------------------


class TestSeedSeenIds:
    def test_reads_flat_message_id(self, listener: XNodeListener) -> None:
        inbox_dir = listener._dirs["lead_inbox"]
        record = {"message_id": "flat-id"}
        (inbox_dir / "testnode.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

        listener._seed_seen_ids()
        assert "flat-id" in listener._seen_message_ids

    def test_reads_envelope_wrapped_message_id(self, listener: XNodeListener) -> None:
        inbox_dir = listener._dirs["lead_inbox"]
        record = {"envelope": {"message_id": "env-id"}}
        (inbox_dir / "testnode.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

        listener._seed_seen_ids()
        assert "env-id" in listener._seen_message_ids

    def test_skips_blank_lines(self, listener: XNodeListener) -> None:
        inbox_dir = listener._dirs["lead_inbox"]
        (inbox_dir / "testnode.jsonl").write_text("\n  \n\n", encoding="utf-8")

        listener._seed_seen_ids()
        assert len(listener._seen_message_ids) == 0

    def test_skips_malformed_json(self, listener: XNodeListener) -> None:
        inbox_dir = listener._dirs["lead_inbox"]
        (inbox_dir / "testnode.jsonl").write_text("not-valid-json\n", encoding="utf-8")

        listener._seed_seen_ids()
        assert len(listener._seen_message_ids) == 0

    def test_handles_oserror_on_unreadable_file(self, listener: XNodeListener) -> None:
        inbox_dir = listener._dirs["lead_inbox"]
        inbox_file = inbox_dir / "locked.jsonl"
        inbox_file.write_text(json.dumps({"message_id": "blocked"}), encoding="utf-8")
        inbox_file.chmod(0o000)
        try:
            listener._seed_seen_ids()  # must not raise
        finally:
            inbox_file.chmod(0o644)

    def test_handles_multiple_files(self, listener: XNodeListener) -> None:
        inbox_dir = listener._dirs["lead_inbox"]
        for i in range(3):
            (inbox_dir / f"node{i}.jsonl").write_text(
                json.dumps({"message_id": f"id-{i}"}) + "\n", encoding="utf-8"
            )
        listener._seed_seen_ids()
        for i in range(3):
            assert f"id-{i}" in listener._seen_message_ids

    def test_skips_records_without_message_id(self, listener: XNodeListener) -> None:
        inbox_dir = listener._dirs["lead_inbox"]
        record = {"type": "lead.send", "payload": {}}
        (inbox_dir / "testnode.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

        listener._seed_seen_ids()
        assert len(listener._seen_message_ids) == 0


# ---------------------------------------------------------------------------
# _should_process — all filtering branches
# ---------------------------------------------------------------------------


class TestShouldProcess:
    def _make_payload(self, target_node: str = "", flat_node: str = "") -> dict:
        envelope: dict = {}
        if target_node:
            envelope = {"target": {"node": target_node}}
        data: dict = {"envelope": envelope}
        if flat_node:
            data["target_node"] = flat_node
        return {"data": data}

    def test_rejects_unknown_event_type(self, listener: XNodeListener) -> None:
        assert listener._should_process("unknown.event", "unknown.event", {}) is False

    def test_accepts_broadcast_with_no_target(self, listener: XNodeListener) -> None:
        assert listener._should_process("lead.send", "lead.send", self._make_payload()) is True

    def test_accepts_matching_envelope_target_node(self, listener: XNodeListener) -> None:
        result = listener._should_process(
            "lead.send", "lead.send", self._make_payload(target_node="testnode")
        )
        assert result is True

    def test_rejects_non_matching_envelope_target_node(self, listener: XNodeListener) -> None:
        result = listener._should_process(
            "lead.send", "lead.send", self._make_payload(target_node="othernode")
        )
        assert result is False

    def test_accepts_flat_target_node_match(self, listener: XNodeListener) -> None:
        result = listener._should_process(
            "lead.send", "lead.send", self._make_payload(flat_node="testnode")
        )
        assert result is True

    def test_rejects_flat_target_node_mismatch(self, listener: XNodeListener) -> None:
        result = listener._should_process(
            "lead.send", "lead.send", self._make_payload(flat_node="nova")
        )
        assert result is False

    def test_accepts_channel_filter_match(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            channels=["lead.testnode.inbox"],
        )
        result = lst._should_process("lead.send", "lead.testnode.inbox", self._make_payload())
        assert result is True

    def test_rejects_channel_filter_no_match(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            channels=["lead.testnode.inbox"],
        )
        result = lst._should_process("lead.send", "lead.other.inbox", self._make_payload())
        assert result is False

    def test_accepts_channel_routed_prefix_with_no_target(self, listener: XNodeListener) -> None:
        # SSE type starts with "lead." and there's no target → treat as broadcast
        result = listener._should_process("lead.send", "lead.testnode.inbox", self._make_payload())
        assert result is True

    def test_all_handled_event_types_accepted(self, listener: XNodeListener) -> None:
        for et in HANDLED_EVENT_TYPES:
            assert listener._should_process(et, et, self._make_payload()) is True

    def test_xnode_prefix_channel_routed_accepted(self, listener: XNodeListener) -> None:
        result = listener._should_process(
            "xnode.relay.exception", "xnode.testnode.relay", self._make_payload()
        )
        assert result is True

    def test_target_node_case_insensitive_comparison(self, listener: XNodeListener) -> None:
        result = listener._should_process(
            "lead.send", "lead.send", self._make_payload(target_node="TESTNODE")
        )
        assert result is True

    def test_channel_with_xnode_prefix_not_in_handled_types_accepted(
        self, listener: XNodeListener
    ) -> None:
        # "xnode.something" starts with "xnode." prefix — channel-routed, accept
        result = listener._should_process(
            "xnode.something", "xnode.something", self._make_payload()
        )
        assert result is True


# ---------------------------------------------------------------------------
# _resolve_target_node
# ---------------------------------------------------------------------------


class TestResolveTargetNode:
    def test_from_nested_target_dict(self, listener: XNodeListener) -> None:
        env = {"target": {"node": "Nova"}}
        assert listener._resolve_target_node(env) == "nova"

    def test_from_flat_target_node_key(self, listener: XNodeListener) -> None:
        env = {"target_node": "Prya"}
        assert listener._resolve_target_node(env) == "prya"

    def test_falls_back_to_hostname_on_empty_envelope(self, listener: XNodeListener) -> None:
        assert listener._resolve_target_node({}) == "testnode"

    def test_falls_back_when_node_is_empty_string(self, listener: XNodeListener) -> None:
        env = {"target": {"node": ""}}
        assert listener._resolve_target_node(env) == "testnode"

    def test_strips_and_lowercases_node_name(self, listener: XNodeListener) -> None:
        env = {"target": {"node": "  NOVA  "}}
        assert listener._resolve_target_node(env) == "nova"

    def test_target_dict_node_takes_priority_over_flat(self, listener: XNodeListener) -> None:
        env = {"target": {"node": "alpha"}, "target_node": "beta"}
        assert listener._resolve_target_node(env) == "alpha"


# ---------------------------------------------------------------------------
# _inbox_contains_message_id
# ---------------------------------------------------------------------------


class TestInboxContainsMessageId:
    def test_returns_false_when_file_missing(self, listener: XNodeListener, tmp_path: Path) -> None:
        result = listener._inbox_contains_message_id(tmp_path / "ghost.jsonl", "any-id")
        assert result is False

    def test_returns_true_for_flat_message_id(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        f = tmp_path / "inbox.jsonl"
        f.write_text(json.dumps({"message_id": "m-42"}) + "\n", encoding="utf-8")
        assert listener._inbox_contains_message_id(f, "m-42") is True

    def test_returns_true_for_envelope_message_id(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        f = tmp_path / "inbox.jsonl"
        f.write_text(json.dumps({"envelope": {"message_id": "env-99"}}) + "\n", encoding="utf-8")
        assert listener._inbox_contains_message_id(f, "env-99") is True

    def test_returns_false_for_different_id(self, listener: XNodeListener, tmp_path: Path) -> None:
        f = tmp_path / "inbox.jsonl"
        f.write_text(json.dumps({"message_id": "m-1"}) + "\n", encoding="utf-8")
        assert listener._inbox_contains_message_id(f, "m-999") is False

    def test_skips_malformed_lines_and_finds_valid(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        f = tmp_path / "inbox.jsonl"
        f.write_text("NOT JSON\n" + json.dumps({"message_id": "ok"}) + "\n", encoding="utf-8")
        assert listener._inbox_contains_message_id(f, "ok") is True

    def test_returns_false_on_oserror(self, listener: XNodeListener, tmp_path: Path) -> None:
        f = tmp_path / "locked.jsonl"
        f.write_text(json.dumps({"message_id": "x"}), encoding="utf-8")
        f.chmod(0o000)
        try:
            result = listener._inbox_contains_message_id(f, "x")
            assert result is False
        finally:
            f.chmod(0o644)

    def test_skips_blank_lines(self, listener: XNodeListener, tmp_path: Path) -> None:
        f = tmp_path / "inbox.jsonl"
        f.write_text("\n  \n" + json.dumps({"message_id": "present"}) + "\n", encoding="utf-8")
        assert listener._inbox_contains_message_id(f, "present") is True


# ---------------------------------------------------------------------------
# _handle_lead_send
# ---------------------------------------------------------------------------


class TestHandleLeadSend:
    @pytest.mark.asyncio
    async def test_stores_record_in_inbox_jsonl(self, listener: XNodeListener) -> None:
        envelope = {
            "message_id": "send-1",
            "target": {"node": "testnode"},
            "payload": {"task_id": "T-1", "summary": "hi"},
            "source": {"node": "prya"},
            "priority": "high",
        }
        await listener._handle_lead_send(envelope)

        out_file = listener._dirs["lead_inbox"] / "testnode.jsonl"
        assert out_file.exists()
        record = json.loads(out_file.read_text().strip())
        assert record["type"] == "lead.send"
        assert record["envelope"]["message_id"] == "send-1"

    @pytest.mark.asyncio
    async def test_marks_message_id_as_seen(self, listener: XNodeListener) -> None:
        envelope = {"message_id": "seen-1"}
        await listener._handle_lead_send(envelope)
        assert "seen-1" in listener._seen_message_ids

    @pytest.mark.asyncio
    async def test_skips_in_memory_duplicate(self, listener: XNodeListener) -> None:
        listener._mark_seen("dup-mem")
        await listener._handle_lead_send({"message_id": "dup-mem"})
        out_file = listener._dirs["lead_inbox"] / "testnode.jsonl"
        assert not out_file.exists()

    @pytest.mark.asyncio
    async def test_skips_durable_duplicate(self, listener: XNodeListener) -> None:
        msg_id = "durable-dup"
        out_file = listener._dirs["lead_inbox"] / "testnode.jsonl"
        out_file.write_text(json.dumps({"message_id": msg_id}) + "\n", encoding="utf-8")

        await listener._handle_lead_send({"message_id": msg_id})

        lines = out_file.read_text().strip().splitlines()
        assert len(lines) == 1  # No new record appended
        assert msg_id in listener._seen_message_ids

    @pytest.mark.asyncio
    async def test_notify_lead_called_when_enabled(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            notify_lead=True,
        )
        with patch.object(lst, "_notify_lead_tmux") as mock_notify:
            envelope = {
                "message_id": "notify-1",
                "payload": {"task_id": "T-01", "summary": "summary"},
                "source": {"node": "prya"},
                "priority": "low",
            }
            await lst._handle_lead_send(envelope)
        mock_notify.assert_called_once()
        assert mock_notify.call_args[0][0] == "lead.send"

    @pytest.mark.asyncio
    async def test_no_subprocess_when_notify_disabled(self, listener: XNodeListener) -> None:
        with patch("subprocess.run") as mock_subprocess:
            await listener._handle_lead_send({"message_id": "no-notify"})
        mock_subprocess.assert_not_called()

    @pytest.mark.asyncio
    async def test_appends_record_with_received_at(self, listener: XNodeListener) -> None:
        envelope = {"message_id": "ts-test"}
        await listener._handle_lead_send(envelope)
        out_file = listener._dirs["lead_inbox"] / "testnode.jsonl"
        record = json.loads(out_file.read_text().strip())
        assert "received_at" in record

    @pytest.mark.asyncio
    async def test_uses_hostname_as_fallback_target_node(self, listener: XNodeListener) -> None:
        """When envelope has no target, inbox file should use listener.hostname."""
        envelope = {"message_id": "fallback-target"}
        await listener._handle_lead_send(envelope)
        out_file = listener._dirs["lead_inbox"] / "testnode.jsonl"
        assert out_file.exists()


# ---------------------------------------------------------------------------
# _handle_lead_ack
# ---------------------------------------------------------------------------


class TestHandleLeadAck:
    @pytest.mark.asyncio
    async def test_stores_ack_json_file(self, listener: XNodeListener) -> None:
        envelope = {"message_id": "ack-1", "status": "ack", "acknowledged_by_node": "nova"}
        await listener._handle_lead_ack(envelope)

        out_file = listener._dirs["acks"] / "ack-1.json"
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["type"] == "lead.ack"
        assert data["message_id"] == "ack-1"

    @pytest.mark.asyncio
    async def test_falls_back_to_ack_message_id_field(self, listener: XNodeListener) -> None:
        envelope = {"ack_message_id": "fallback-ack"}
        await listener._handle_lead_ack(envelope)
        out_file = listener._dirs["acks"] / "fallback-ack.json"
        assert out_file.exists()

    @pytest.mark.asyncio
    async def test_ack_record_contains_received_at(self, listener: XNodeListener) -> None:
        envelope = {"message_id": "ack-ts"}
        await listener._handle_lead_ack(envelope)
        data = json.loads((listener._dirs["acks"] / "ack-ts.json").read_text())
        assert "received_at" in data

    @pytest.mark.asyncio
    async def test_notify_called_for_ack(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            notify_lead=True,
        )
        with patch.object(lst, "_notify_lead_tmux") as mock_notify:
            await lst._handle_lead_ack({"message_id": "ack-n", "status": "ack"})
        mock_notify.assert_called_once()
        assert mock_notify.call_args[0][0] == "lead.ack"

    @pytest.mark.asyncio
    async def test_ack_file_contains_full_envelope(self, listener: XNodeListener) -> None:
        envelope = {
            "message_id": "ack-full",
            "status": "ack",
            "acknowledged_by_node": "sati",
            "extra": "data",
        }
        await listener._handle_lead_ack(envelope)
        data = json.loads((listener._dirs["acks"] / "ack-full.json").read_text())
        assert data["envelope"]["acknowledged_by_node"] == "sati"


# ---------------------------------------------------------------------------
# _handle_handoff
# ---------------------------------------------------------------------------


class TestHandleHandoff:
    @pytest.mark.asyncio
    async def test_stores_handoff_json_file(self, listener: XNodeListener) -> None:
        envelope = {"handoff_id": "hnd-1", "source_node": "prya", "summary": "done", "risk": "low"}
        await listener._handle_handoff(envelope)

        out_file = listener._dirs["handoffs"] / "hnd-1.json"
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["type"] == "lead.handoff"
        assert data["handoff_id"] == "hnd-1"

    @pytest.mark.asyncio
    async def test_falls_back_to_message_id(self, listener: XNodeListener) -> None:
        envelope = {"message_id": "msg-hnd"}
        await listener._handle_handoff(envelope)
        out_file = listener._dirs["handoffs"] / "msg-hnd.json"
        assert out_file.exists()

    @pytest.mark.asyncio
    async def test_falls_back_to_timestamp_slug_when_no_ids(self, listener: XNodeListener) -> None:
        await listener._handle_handoff({})
        handoff_dir = listener._dirs["handoffs"]
        files = list(handoff_dir.glob("*.json"))
        assert len(files) == 1

    @pytest.mark.asyncio
    async def test_notify_called_for_handoff(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            notify_lead=True,
        )
        with patch.object(lst, "_notify_lead_tmux") as mock_notify:
            await lst._handle_handoff({"handoff_id": "hnd-n"})
        mock_notify.assert_called_once()
        assert mock_notify.call_args[0][0] == "lead.handoff"

    @pytest.mark.asyncio
    async def test_handoff_record_has_received_at(self, listener: XNodeListener) -> None:
        await listener._handle_handoff({"handoff_id": "hnd-ts"})
        data = json.loads((listener._dirs["handoffs"] / "hnd-ts.json").read_text())
        assert "received_at" in data


# ---------------------------------------------------------------------------
# _handle_exception
# ---------------------------------------------------------------------------


class TestHandleException:
    @pytest.mark.asyncio
    async def test_stores_exception_record_by_date(self, listener: XNodeListener) -> None:
        envelope = {"message_id": "exc-1", "error": "boom"}
        await listener._handle_exception(envelope)

        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        out_file = listener._dirs["exceptions"] / f"{date_str}.jsonl"
        assert out_file.exists()
        record = json.loads(out_file.read_text().strip())
        assert record["type"] == "xnode.relay.exception"
        assert record["envelope"]["message_id"] == "exc-1"

    @pytest.mark.asyncio
    async def test_appends_multiple_exceptions_same_day(self, listener: XNodeListener) -> None:
        for i in range(3):
            await listener._handle_exception({"message_id": f"exc-{i}"})

        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        lines = (
            (listener._dirs["exceptions"] / f"{date_str}.jsonl").read_text().strip().splitlines()
        )
        assert len(lines) == 3

    @pytest.mark.asyncio
    async def test_exception_record_has_received_at(self, listener: XNodeListener) -> None:
        await listener._handle_exception({"message_id": "exc-ts"})
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        record = json.loads(
            (listener._dirs["exceptions"] / f"{date_str}.jsonl").read_text().strip()
        )
        assert "received_at" in record


# ---------------------------------------------------------------------------
# _handle_event — routing dispatch table
# ---------------------------------------------------------------------------


class TestHandleEvent:
    @pytest.mark.asyncio
    async def test_routes_lead_send(self, listener: XNodeListener) -> None:
        with patch.object(listener, "_handle_lead_send", new_callable=AsyncMock) as mock:
            await listener._handle_event("lead.send", {"envelope": {}})
        mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_lead_ack(self, listener: XNodeListener) -> None:
        with patch.object(listener, "_handle_lead_ack", new_callable=AsyncMock) as mock:
            await listener._handle_event("lead.ack", {})
        mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_lead_handoff(self, listener: XNodeListener) -> None:
        with patch.object(listener, "_handle_handoff", new_callable=AsyncMock) as mock:
            await listener._handle_event("lead.handoff", {})
        mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_xnode_relay_exception(self, listener: XNodeListener) -> None:
        with patch.object(listener, "_handle_exception", new_callable=AsyncMock) as mock:
            await listener._handle_event("xnode.relay.exception", {})
        mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_logs_unknown_event_type_without_raising(self, listener: XNodeListener) -> None:
        await listener._handle_event("some.unknown.type", {})

    @pytest.mark.asyncio
    async def test_extracts_envelope_from_data(self, listener: XNodeListener) -> None:
        """_handle_event should pass the nested envelope dict to the handler."""
        inner_env = {"message_id": "inner-1"}
        with patch.object(listener, "_handle_lead_send", new_callable=AsyncMock) as mock:
            await listener._handle_event("lead.send", {"envelope": inner_env})
        mock.assert_called_once_with(inner_env)

    @pytest.mark.asyncio
    async def test_data_without_envelope_key_passes_data_as_envelope(
        self, listener: XNodeListener
    ) -> None:
        """When 'envelope' key is absent, data itself becomes the envelope."""
        data = {"message_id": "direct-1", "payload": {}}
        with patch.object(listener, "_handle_lead_send", new_callable=AsyncMock) as mock:
            await listener._handle_event("lead.send", data)
        mock.assert_called_once_with(data)


# ---------------------------------------------------------------------------
# _dispatch_sse_event
# ---------------------------------------------------------------------------


class TestDispatchSseEvent:
    @pytest.mark.asyncio
    async def test_ignores_invalid_json_data(self, listener: XNodeListener) -> None:
        await listener._dispatch_sse_event("id-1", "lead.send", "NOT JSON")

    @pytest.mark.asyncio
    async def test_ignores_empty_event_type(self, listener: XNodeListener) -> None:
        payload = json.dumps({"type": "", "data": {}})
        await listener._dispatch_sse_event("", "", payload)

    @pytest.mark.asyncio
    async def test_routes_to_handle_event(self, listener: XNodeListener) -> None:
        payload = json.dumps(
            {
                "type": "lead.send",
                "data": {
                    "message_id": "d-1",
                    "envelope": {"message_id": "d-1", "target": {"node": "testnode"}},
                },
            }
        )
        with patch.object(listener, "_handle_event", new_callable=AsyncMock) as mock:
            await listener._dispatch_sse_event("id-1", "lead.send", payload)
        mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_dedup_skips_seen_message_id(self, listener: XNodeListener) -> None:
        listener._mark_seen("dup-sse")
        payload = json.dumps({"type": "lead.send", "data": {"message_id": "dup-sse"}})
        with patch.object(listener, "_handle_event", new_callable=AsyncMock) as mock:
            await listener._dispatch_sse_event("id-1", "lead.send", payload)
        mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_filters_out_other_node_events(self, listener: XNodeListener) -> None:
        payload = json.dumps(
            {
                "type": "lead.send",
                "data": {"envelope": {"target": {"node": "othernode"}}},
            }
        )
        with patch.object(listener, "_handle_event", new_callable=AsyncMock) as mock:
            await listener._dispatch_sse_event("id-1", "lead.send", payload)
        mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_handler_exception_is_caught(self, listener: XNodeListener) -> None:
        payload = json.dumps({"type": "lead.send", "data": {}})
        with patch.object(
            listener, "_handle_event", new_callable=AsyncMock, side_effect=RuntimeError("boom")
        ):
            await listener._dispatch_sse_event("id-1", "lead.send", payload)

    @pytest.mark.asyncio
    async def test_resolves_event_type_from_data_event_type_field(
        self, listener: XNodeListener
    ) -> None:
        """data.event_type overrides the SSE event field for canonical routing."""
        payload = json.dumps(
            {
                "type": "lead.testnode.inbox",
                "data": {
                    "event_type": "lead.send",
                    "envelope": {"target": {"node": "testnode"}},
                },
            }
        )
        with patch.object(listener, "_handle_event", new_callable=AsyncMock) as mock:
            await listener._dispatch_sse_event("id-1", "lead.testnode.inbox", payload)
        mock.assert_called_once()
        call_event_type = mock.call_args[0][0]
        assert call_event_type == "lead.send"

    @pytest.mark.asyncio
    async def test_dedup_skips_envelope_message_id(self, listener: XNodeListener) -> None:
        listener._mark_seen("env-dup")
        payload = json.dumps(
            {
                "type": "lead.send",
                "data": {"envelope": {"message_id": "env-dup"}},
            }
        )
        with patch.object(listener, "_handle_event", new_callable=AsyncMock) as mock:
            await listener._dispatch_sse_event("id-1", "lead.send", payload)
        mock.assert_not_called()


# ---------------------------------------------------------------------------
# _write_heartbeat
# ---------------------------------------------------------------------------


class TestWriteHeartbeat:
    @pytest.mark.asyncio
    async def test_creates_heartbeat_file(self, listener: XNodeListener) -> None:
        listener._running = True
        await listener._write_heartbeat()

        hb_file = listener.forge_root / ".forge" / "ops" / "heartbeat" / "testnode.state.json"
        assert hb_file.exists()
        data = json.loads(hb_file.read_text())
        assert data["hostname"] == "testnode"
        assert data["status"] == "connected"
        assert data["listener"] == "xnode"

    @pytest.mark.asyncio
    async def test_status_stopped_when_not_running(self, listener: XNodeListener) -> None:
        listener._running = False
        await listener._write_heartbeat()

        hb_file = listener.forge_root / ".forge" / "ops" / "heartbeat" / "testnode.state.json"
        data = json.loads(hb_file.read_text())
        assert data["status"] == "stopped"

    @pytest.mark.asyncio
    async def test_last_heartbeat_is_iso_timestamp(self, listener: XNodeListener) -> None:
        listener._running = True
        await listener._write_heartbeat()

        hb_file = listener.forge_root / ".forge" / "ops" / "heartbeat" / "testnode.state.json"
        data = json.loads(hb_file.read_text())
        parsed = datetime.fromisoformat(data["last_heartbeat"])
        assert parsed.tzinfo is not None

    @pytest.mark.asyncio
    async def test_logs_warning_on_os_error(self, listener: XNodeListener) -> None:
        listener._running = True
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            await listener._write_heartbeat()  # must not raise


# ---------------------------------------------------------------------------
# _notify_lead_tmux
# ---------------------------------------------------------------------------


class TestNotifyLeadTmux:
    def test_skips_when_notify_lead_is_false(self, listener: XNodeListener) -> None:
        with patch("subprocess.run") as mock_run:
            listener._notify_lead_tmux("lead.send", "T-1", "summary", "prya", "high")
        mock_run.assert_not_called()

    def test_skips_when_tmux_unavailable(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            notify_lead=True,
        )
        lst._has_tmux = False
        with patch("subprocess.run") as mock_run:
            lst._notify_lead_tmux("lead.send")
        mock_run.assert_not_called()

    def test_skips_when_session_missing(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            notify_lead=True,
        )
        lst._has_tmux = True
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            lst._notify_lead_tmux("lead.send", "T-1")
        assert mock_run.call_count == 1

    def test_sends_notification_successfully(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            notify_lead=True,
        )
        lst._has_tmux = True

        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.return_value = MagicMock(returncode=0)
            lst._notify_lead_tmux("lead.send", "T-1", "summary", "prya", "high")

        # has-session + C-u clear + send-keys literal + Enter = 4 calls
        assert mock_run.call_count >= 3

    def test_handles_timeout_expired(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            notify_lead=True,
        )
        lst._has_tmux = True
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 5)):
            lst._notify_lead_tmux("lead.send")  # must not raise

    def test_handles_file_not_found(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            notify_lead=True,
        )
        lst._has_tmux = True
        with patch("subprocess.run", side_effect=FileNotFoundError("not found")):
            lst._notify_lead_tmux("lead.send")  # must not raise

    def test_message_contains_priority_tag(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            notify_lead=True,
        )
        lst._has_tmux = True

        sent_messages: list[str] = []

        def capture(args, **kwargs):
            if "send-keys" in args and "-l" in args:
                sent_messages.append(args[-1])
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=capture), patch("time.sleep"):
            lst._notify_lead_tmux("lead.send", "T-1", "details", "prya", "critical")

        assert any("[XNODE CRITICAL]" in m for m in sent_messages)

    def test_message_includes_source_node(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            notify_lead=True,
        )
        lst._has_tmux = True

        sent_messages: list[str] = []

        def capture(args, **kwargs):
            if "send-keys" in args and "-l" in args:
                sent_messages.append(args[-1])
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=capture), patch("time.sleep"):
            lst._notify_lead_tmux("lead.send", source_node="my-source")

        assert any("my-source" in m for m in sent_messages)

    def test_handles_oserror(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            notify_lead=True,
        )
        lst._has_tmux = True
        with patch("subprocess.run", side_effect=OSError("pipe broken")):
            lst._notify_lead_tmux("lead.send")  # must not raise

    def test_send_failed_rc_does_not_raise(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.local",
            token="tok",
            forge_root=forge_root,
            hostname="testnode",
            notify_lead=True,
        )
        lst._has_tmux = True

        responses = [
            MagicMock(returncode=0),  # has-session succeeds
            MagicMock(returncode=0),  # C-u
            MagicMock(returncode=1),  # send-keys -l fails
            MagicMock(returncode=0),  # Enter
        ]

        with patch("subprocess.run", side_effect=responses), patch("time.sleep"):
            lst._notify_lead_tmux("lead.send", "T-1")  # must not raise


# ---------------------------------------------------------------------------
# _process_outbox_file
# ---------------------------------------------------------------------------


class TestProcessOutboxFile:
    def _make_record(
        self,
        *,
        hours_ago: float = 0,
        delivered: bool = False,
        abandoned: bool = False,
        bad_ts: bool = False,
    ) -> dict:
        recorded_at = (
            "not-a-date" if bad_ts else (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
        )
        record: dict = {
            "event_type": "lead.send",
            "envelope": {"message_id": "m-1"},
            "recorded_at": recorded_at,
        }
        if delivered:
            record["delivered_at"] = _now_iso()
        if abandoned:
            record["abandoned_at"] = _now_iso()
        return record

    @pytest.mark.asyncio
    async def test_skips_already_delivered_records(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "out.jsonl"
        path.write_text(json.dumps(self._make_record(delivered=True)) + "\n", encoding="utf-8")

        with patch.object(listener, "_retry_single_record", new_callable=AsyncMock) as mock_retry:
            await listener._process_outbox_file(AsyncMock(), path)
        mock_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_already_abandoned_records(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "out.jsonl"
        path.write_text(json.dumps(self._make_record(abandoned=True)) + "\n", encoding="utf-8")

        with patch.object(listener, "_retry_single_record", new_callable=AsyncMock) as mock_retry:
            await listener._process_outbox_file(AsyncMock(), path)
        mock_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_abandons_expired_records(self, listener: XNodeListener, tmp_path: Path) -> None:
        path = tmp_path / "out.jsonl"
        path.write_text(json.dumps(self._make_record(hours_ago=25)) + "\n", encoding="utf-8")

        await listener._process_outbox_file(AsyncMock(), path)

        updated = json.loads(path.read_text().strip())
        assert "abandoned_at" in updated
        assert updated["abandon_reason"] == "expired_ttl"

    @pytest.mark.asyncio
    async def test_abandons_records_with_invalid_timestamp(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "out.jsonl"
        path.write_text(json.dumps(self._make_record(bad_ts=True)) + "\n", encoding="utf-8")

        await listener._process_outbox_file(AsyncMock(), path)

        updated = json.loads(path.read_text().strip())
        assert "abandoned_at" in updated
        assert updated["abandon_reason"] == "invalid_recorded_at"

    @pytest.mark.asyncio
    async def test_marks_delivered_on_retry_success(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "out.jsonl"
        path.write_text(json.dumps(self._make_record()) + "\n", encoding="utf-8")

        with patch.object(
            listener, "_retry_single_record", new_callable=AsyncMock, return_value=True
        ):
            await listener._process_outbox_file(AsyncMock(), path)

        updated = json.loads(path.read_text().strip())
        assert "delivered_at" in updated

    @pytest.mark.asyncio
    async def test_leaves_record_pending_on_retry_failure(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "out.jsonl"
        path.write_text(json.dumps(self._make_record()) + "\n", encoding="utf-8")
        original_mtime = path.stat().st_mtime

        with patch.object(
            listener, "_retry_single_record", new_callable=AsyncMock, return_value=False
        ):
            await listener._process_outbox_file(AsyncMock(), path)

        # File must NOT be rewritten when no terminal state changes occur
        assert path.stat().st_mtime == original_mtime

    @pytest.mark.asyncio
    async def test_handles_missing_file_gracefully(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "ghost.jsonl"
        await listener._process_outbox_file(AsyncMock(), path)  # must not raise

    @pytest.mark.asyncio
    async def test_skips_empty_file(self, listener: XNodeListener, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")

        with patch.object(listener, "_retry_single_record", new_callable=AsyncMock) as mock_retry:
            await listener._process_outbox_file(AsyncMock(), path)
        mock_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_malformed_json_lines(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text("not-json\n", encoding="utf-8")

        with patch.object(listener, "_retry_single_record", new_callable=AsyncMock) as mock_retry:
            await listener._process_outbox_file(AsyncMock(), path)
        mock_retry.assert_not_called()


# ---------------------------------------------------------------------------
# _mark_outbox_abandoned
# ---------------------------------------------------------------------------


class TestMarkOutboxAbandoned:
    def test_sets_abandoned_fields(self, listener: XNodeListener) -> None:
        record: dict = {}
        listener._mark_outbox_abandoned(record, abandoned_at="2026-01-01T00:00:00Z", reason="ttl")
        assert record["abandoned_at"] == "2026-01-01T00:00:00Z"
        assert record["abandon_reason"] == "ttl"

    def test_is_idempotent_does_not_overwrite(self, listener: XNodeListener) -> None:
        record: dict = {"abandoned_at": "original-ts"}
        listener._mark_outbox_abandoned(record, abandoned_at="new-ts", reason="ttl")
        assert record["abandoned_at"] == "original-ts"

    def test_sets_different_abandon_reasons(self, listener: XNodeListener) -> None:
        r1: dict = {}
        listener._mark_outbox_abandoned(r1, abandoned_at="ts", reason="expired_ttl")
        assert r1["abandon_reason"] == "expired_ttl"

        r2: dict = {}
        listener._mark_outbox_abandoned(r2, abandoned_at="ts", reason="invalid_recorded_at")
        assert r2["abandon_reason"] == "invalid_recorded_at"


# ---------------------------------------------------------------------------
# _retry_single_record
# ---------------------------------------------------------------------------


class TestRetrySingleRecord:
    @pytest.mark.asyncio
    async def test_returns_true_on_2xx_response(self, listener: XNodeListener) -> None:
        record = {"event_type": "lead.send", "envelope": {"message_id": "r-1"}}
        mock_resp = AsyncMock()
        mock_resp.status = 202
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        result = await listener._retry_single_record(mock_session, record)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_5xx_response(self, listener: XNodeListener) -> None:
        record = {"event_type": "lead.send", "envelope": {}}
        mock_resp = AsyncMock()
        mock_resp.status = 503
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        result = await listener._retry_single_record(mock_session, record)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_network_error(self, listener: XNodeListener) -> None:
        record = {"event_type": "lead.send", "envelope": {}}
        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=OSError("connection refused"))

        result = await listener._retry_single_record(mock_session, record)
        assert result is False

    @pytest.mark.asyncio
    async def test_includes_bearer_token_in_headers(self, listener: XNodeListener) -> None:
        record = {"event_type": "lead.send", "envelope": {}}
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        await listener._retry_single_record(mock_session, record)

        call_kwargs = mock_session.post.call_args[1]
        assert "Authorization" in call_kwargs.get("headers", {})
        assert "Bearer test-token" in call_kwargs["headers"]["Authorization"]

    @pytest.mark.asyncio
    async def test_posts_to_xnode_events_endpoint(self, listener: XNodeListener) -> None:
        record = {"event_type": "lead.ack", "envelope": {}}
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        await listener._retry_single_record(mock_session, record)

        call_url = mock_session.post.call_args[0][0]
        assert "/api/xnode/events" in call_url

    @pytest.mark.asyncio
    async def test_returns_true_on_200(self, listener: XNodeListener) -> None:
        record = {"event_type": "test", "envelope": {}}
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        result = await listener._retry_single_record(mock_session, record)
        assert result is True


# ---------------------------------------------------------------------------
# _rewrite_jsonl
# ---------------------------------------------------------------------------


class TestRewriteJsonl:
    def test_overwrites_file_with_new_records(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "data.jsonl"
        path.write_text(json.dumps({"old": True}) + "\n", encoding="utf-8")

        listener._rewrite_jsonl(path, [{"new": 1}, {"new": 2}])

        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"new": 1}

    def test_no_temp_file_remains(self, listener: XNodeListener, tmp_path: Path) -> None:
        path = tmp_path / "data.jsonl"
        path.write_text("", encoding="utf-8")
        listener._rewrite_jsonl(path, [{"x": 1}])

        tmp_file = path.with_suffix(".tmp")
        assert not tmp_file.exists()

    def test_redacts_secrets_in_written_records(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "data.jsonl"
        path.write_text("", encoding="utf-8")
        records = [{"token_env": "FORGE_WEBHOOK_TOKEN=supersecret"}]
        listener._rewrite_jsonl(path, records)

        content = path.read_text()
        assert "supersecret" not in content
        assert "[REDACTED]" in content

    def test_handles_empty_records_list(self, listener: XNodeListener, tmp_path: Path) -> None:
        path = tmp_path / "data.jsonl"
        path.write_text(json.dumps({"old": True}) + "\n", encoding="utf-8")
        listener._rewrite_jsonl(path, [])

        assert path.read_text().strip() == ""

    def test_creates_new_file_if_not_exists(self, listener: XNodeListener, tmp_path: Path) -> None:
        path = tmp_path / "new.jsonl"
        listener._rewrite_jsonl(path, [{"a": 1}])
        assert path.exists()
        assert json.loads(path.read_text().strip()) == {"a": 1}


# ---------------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------------


class TestStop:
    def test_sets_running_to_false(self, listener: XNodeListener) -> None:
        listener._running = True
        listener.stop()
        assert listener._running is False

    def test_stop_when_already_stopped_is_safe(self, listener: XNodeListener) -> None:
        listener._running = False
        listener.stop()
        assert listener._running is False


# ---------------------------------------------------------------------------
# start() — once=True fast path, reconnect/backoff
# ---------------------------------------------------------------------------


class TestStart:
    @pytest.mark.asyncio
    async def test_start_once_exits_after_one_connection(self, listener: XNodeListener) -> None:
        connect_calls = 0

        async def fake_connect():
            nonlocal connect_calls
            connect_calls += 1

        with (
            patch.object(listener, "_connect_and_listen", side_effect=fake_connect),
            patch.object(listener, "_setup_signals"),
            patch.object(listener, "_seed_seen_ids"),
            patch.object(listener, "_heartbeat_loop", new_callable=AsyncMock),
            patch.object(listener, "_outbox_retry_loop", new_callable=AsyncMock),
        ):
            await listener.start(once=True)

        assert connect_calls == 1

    @pytest.mark.asyncio
    async def test_start_exits_on_cancelled_error(self, listener: XNodeListener) -> None:
        async def raise_cancel():
            raise asyncio.CancelledError()

        with (
            patch.object(listener, "_connect_and_listen", side_effect=raise_cancel),
            patch.object(listener, "_setup_signals"),
            patch.object(listener, "_seed_seen_ids"),
            patch.object(listener, "_heartbeat_loop", new_callable=AsyncMock),
            patch.object(listener, "_outbox_retry_loop", new_callable=AsyncMock),
        ):
            await listener.start(once=True)

    @pytest.mark.asyncio
    async def test_start_reconnects_on_connection_error(self, listener: XNodeListener) -> None:
        call_count = 0

        async def flaky_connect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("refused")
            listener._running = False

        with (
            patch.object(listener, "_connect_and_listen", side_effect=flaky_connect),
            patch.object(listener, "_setup_signals"),
            patch.object(listener, "_seed_seen_ids"),
            patch.object(listener, "_heartbeat_loop", new_callable=AsyncMock),
            patch.object(listener, "_outbox_retry_loop", new_callable=AsyncMock),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await listener.start(once=False)

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_start_increases_backoff_on_error(self, listener: XNodeListener) -> None:
        call_count = 0

        async def error_connect():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("refused")
            listener._running = False

        sleep_durations: list[float] = []

        async def capture_sleep(duration: float) -> None:
            sleep_durations.append(duration)

        with (
            patch.object(listener, "_connect_and_listen", side_effect=error_connect),
            patch.object(listener, "_setup_signals"),
            patch.object(listener, "_seed_seen_ids"),
            patch.object(listener, "_heartbeat_loop", new_callable=AsyncMock),
            patch.object(listener, "_outbox_retry_loop", new_callable=AsyncMock),
            patch("asyncio.sleep", side_effect=capture_sleep),
        ):
            await listener.start(once=False)

        assert len(sleep_durations) >= 2
        assert sleep_durations[1] > sleep_durations[0]

    @pytest.mark.asyncio
    async def test_start_resets_backoff_after_successful_connect(
        self, listener: XNodeListener
    ) -> None:
        call_count = 0

        async def connect_then_stop():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("refused")
            listener._running = False

        listener._backoff = 16.0  # Elevated from prior failures

        with (
            patch.object(listener, "_connect_and_listen", side_effect=connect_then_stop),
            patch.object(listener, "_setup_signals"),
            patch.object(listener, "_seed_seen_ids"),
            patch.object(listener, "_heartbeat_loop", new_callable=AsyncMock),
            patch.object(listener, "_outbox_retry_loop", new_callable=AsyncMock),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await listener.start(once=False)

        assert listener._backoff == 1.0

    @pytest.mark.asyncio
    async def test_start_caps_backoff_at_max(self, listener: XNodeListener) -> None:
        call_count = 0

        async def always_fail():
            nonlocal call_count
            call_count += 1
            if call_count >= 10:
                listener._running = False
            raise ConnectionError("refused")

        with (
            patch.object(listener, "_connect_and_listen", side_effect=always_fail),
            patch.object(listener, "_setup_signals"),
            patch.object(listener, "_seed_seen_ids"),
            patch.object(listener, "_heartbeat_loop", new_callable=AsyncMock),
            patch.object(listener, "_outbox_retry_loop", new_callable=AsyncMock),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await listener.start(once=False)

        assert listener._backoff <= listener._max_backoff


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestNowIso:
    def test_returns_iso_formatted_utc_string(self) -> None:
        ts = _now_iso()
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None

    def test_successive_calls_are_non_decreasing(self) -> None:
        t1 = _now_iso()
        t2 = _now_iso()
        assert t2 >= t1


class TestNowSlug:
    def test_has_correct_length(self) -> None:
        assert len(_now_slug()) == 15

    def test_has_underscore_separator_at_position_8(self) -> None:
        assert _now_slug()[8] == "_"

    def test_digits_only_except_separator(self) -> None:
        slug = _now_slug()
        digits_only = slug.replace("_", "")
        assert digits_only.isdigit()


class TestAppendJsonl:
    def test_creates_file_on_first_call(self, tmp_path: Path) -> None:
        path = tmp_path / "test.jsonl"
        _append_jsonl(path, {"key": "value"})
        assert path.exists()
        assert json.loads(path.read_text().strip()) == {"key": "value"}

    def test_appends_multiple_records(self, tmp_path: Path) -> None:
        path = tmp_path / "test.jsonl"
        _append_jsonl(path, {"n": 1})
        _append_jsonl(path, {"n": 2})
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1]) == {"n": 2}

    def test_redacts_secrets_in_payload(self, tmp_path: Path) -> None:
        path = tmp_path / "test.jsonl"
        _append_jsonl(path, {"env": "FORGE_WEBHOOK_TOKEN=secret"})
        content = path.read_text()
        assert "secret" not in content

    def test_each_record_on_separate_line(self, tmp_path: Path) -> None:
        path = tmp_path / "test.jsonl"
        for i in range(5):
            _append_jsonl(path, {"n": i})
        lines = [l for l in path.read_text().splitlines() if l.strip()]
        assert len(lines) == 5


class TestWriteJson:
    def test_creates_json_file(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        _write_json(path, {"hello": "world"})
        assert json.loads(path.read_text()) == {"hello": "world"}

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        _write_json(path, {"v": 1})
        _write_json(path, {"v": 2})
        assert json.loads(path.read_text())["v"] == 2

    def test_is_indented_json(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        _write_json(path, {"a": 1})
        content = path.read_text()
        assert "\n" in content


class TestRedactSecrets:
    def test_redacts_assignment_pattern(self) -> None:
        value = "export FORGE_WEBHOOK_TOKEN=supersecret"
        result = _redact_secrets(value)
        assert "supersecret" not in result
        assert "[REDACTED]" in result

    def test_redacts_json_pattern(self) -> None:
        value = '"FORGE_WEBHOOK_TOKEN": "mysecrettoken"'
        result = _redact_secrets(value)
        assert "mysecrettoken" not in result

    def test_passes_through_safe_strings(self) -> None:
        value = "hello world"
        assert _redact_secrets(value) == "hello world"

    def test_recurses_into_dicts(self) -> None:
        data = {"nested": {"info": "FORGE_WEBHOOK_TOKEN=abc123"}}
        result = _redact_secrets(data)
        assert "abc123" not in str(result)
        assert isinstance(result, dict)

    def test_recurses_into_lists(self) -> None:
        data = ["FORGE_WEBHOOK_TOKEN=abc", "safe"]
        result = _redact_secrets(data)
        assert "abc" not in str(result)
        assert "safe" in result

    def test_passes_through_integers(self) -> None:
        assert _redact_secrets(42) == 42

    def test_passes_through_none(self) -> None:
        assert _redact_secrets(None) is None

    def test_passes_through_booleans(self) -> None:
        assert _redact_secrets(True) is True

    def test_case_insensitive_match(self) -> None:
        value = "forge_webhook_token=lowercase"
        result = _redact_secrets(value)
        assert "lowercase" not in result

    def test_colon_assignment_also_redacted(self) -> None:
        value = "FORGE_WEBHOOK_TOKEN: mytoken"
        result = _redact_secrets(value)
        assert "mytoken" not in result


# ---------------------------------------------------------------------------
# _get_sse_session_token
# ---------------------------------------------------------------------------


class TestGetSseSessionToken:
    @pytest.mark.asyncio
    async def test_returns_session_token_on_200(self, listener: XNodeListener) -> None:
        """Returns the session_token from a successful 200 response."""
        # Patch at the forge_harness.xnode_listener module level so the lazy
        # 'import aiohttp' inside the method picks up our stub.
        import forge_harness.xnode_listener as xnl

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"data": {"session_token": "sess-abc"}})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_post_cm = MagicMock()
        mock_post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_post_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_cm)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        aiohttp_stub = MagicMock()
        aiohttp_stub.ClientTimeout = MagicMock(return_value=MagicMock())
        aiohttp_stub.ClientSession = MagicMock(return_value=mock_session)

        with patch.dict("sys.modules", {"aiohttp": aiohttp_stub}):
            result = await listener._get_sse_session_token()

        assert result == "sess-abc"

    @pytest.mark.asyncio
    async def test_returns_none_when_status_not_200(self, listener: XNodeListener) -> None:
        """Returns None when the token exchange endpoint returns non-200."""
        mock_resp = AsyncMock()
        mock_resp.status = 401
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_post_cm = MagicMock()
        mock_post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_post_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_cm)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        aiohttp_stub = MagicMock()
        aiohttp_stub.ClientTimeout = MagicMock(return_value=MagicMock())
        aiohttp_stub.ClientSession = MagicMock(return_value=mock_session)

        with patch.dict("sys.modules", {"aiohttp": aiohttp_stub}):
            result = await listener._get_sse_session_token()

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_exception(self, listener: XNodeListener) -> None:
        """Returns None when the session context manager raises an unexpected exception."""
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(side_effect=OSError("connection refused"))
        mock_session.__aexit__ = AsyncMock(return_value=False)

        aiohttp_stub = MagicMock()
        aiohttp_stub.ClientTimeout = MagicMock(return_value=MagicMock())
        aiohttp_stub.ClientSession = MagicMock(return_value=mock_session)

        with patch.dict("sys.modules", {"aiohttp": aiohttp_stub}):
            result = await listener._get_sse_session_token()

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_session_token_missing_from_response(
        self, listener: XNodeListener
    ) -> None:
        """Returns None when 200 but the JSON body has no session_token."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"data": {}})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_post_cm = MagicMock()
        mock_post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_post_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_cm)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        aiohttp_stub = MagicMock()
        aiohttp_stub.ClientTimeout = MagicMock(return_value=MagicMock())
        aiohttp_stub.ClientSession = MagicMock(return_value=mock_session)

        with patch.dict("sys.modules", {"aiohttp": aiohttp_stub}):
            result = await listener._get_sse_session_token()

        assert result is None


# ---------------------------------------------------------------------------
# _connect_and_listen — SSE state machine
# ---------------------------------------------------------------------------


class TestConnectAndListen:
    """Tests for the SSE state machine in _connect_and_listen.

    The code under test uses a three-level async context manager chain:
        async with aiohttp.ClientSession(...) as session:
            async with session.get(...) as resp:
                async for raw_line in resp.content: ...

    The ``_build_aiohttp_stub`` helper wires all three levels correctly.
    """

    def _make_sse_content(self, lines: list[bytes]) -> object:
        """Return an async iterable that yields the given byte lines."""

        class AsyncByteLines:
            def __init__(self, data: list[bytes]) -> None:
                self._data = data

            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                for line in self._data:
                    yield line

        return AsyncByteLines(lines)

    def _build_aiohttp_stub(
        self,
        resp_status: int = 200,
        sse_lines: list[bytes] | None = None,
    ) -> tuple[MagicMock, MagicMock]:
        """Return (aiohttp_stub, mock_session) with the full async CM chain.

        aiohttp_stub.ClientSession(timeout) → session_cm
        async with session_cm as session:
            session.get(url, params, headers) → resp_cm
            async with resp_cm as resp:
                resp.status, resp.content
        """
        # Bottom level: the HTTP response object
        mock_resp = MagicMock()
        mock_resp.status = resp_status
        mock_resp.content = self._make_sse_content(sse_lines or [])

        # Middle context manager wrapping the response
        mock_resp_cm = MagicMock()
        mock_resp_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp_cm.__aexit__ = AsyncMock(return_value=False)

        # The session object with .get()
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp_cm)

        # Context manager wrapping the session
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        # aiohttp module stub
        aiohttp_stub = MagicMock()
        aiohttp_stub.ClientTimeout = MagicMock(return_value=MagicMock())
        aiohttp_stub.ClientSession = MagicMock(return_value=mock_session_cm)

        return aiohttp_stub, mock_session

    @pytest.mark.asyncio
    async def test_raises_connection_error_on_non_200(self, listener: XNodeListener) -> None:
        """Non-200 HTTP responses must raise ConnectionError."""
        aiohttp_stub, _ = self._build_aiohttp_stub(resp_status=503)

        with patch.dict("sys.modules", {"aiohttp": aiohttp_stub}):
            with patch.object(
                listener, "_get_sse_session_token", new_callable=AsyncMock, return_value=None
            ):
                with pytest.raises(ConnectionError, match="SSE connection rejected"):
                    await listener._connect_and_listen()

    @pytest.mark.asyncio
    async def test_dispatches_event_on_blank_line(self, listener: XNodeListener) -> None:
        """A complete SSE block (data + blank line) must call _dispatch_sse_event."""
        sse_bytes = [
            b"event: lead.send\r\n",
            b'data: {"type":"lead.send","data":{}}\r\n',
            b"\r\n",
        ]
        aiohttp_stub, _ = self._build_aiohttp_stub(sse_lines=sse_bytes)
        listener._running = True

        with patch.dict("sys.modules", {"aiohttp": aiohttp_stub}):
            with (
                patch.object(
                    listener, "_get_sse_session_token", new_callable=AsyncMock, return_value=None
                ),
                patch.object(
                    listener, "_dispatch_sse_event", new_callable=AsyncMock
                ) as mock_dispatch,
            ):
                await listener._connect_and_listen()

        mock_dispatch.assert_called_once()
        call_args = mock_dispatch.call_args[0]
        assert call_args[1] == "lead.send"

    @pytest.mark.asyncio
    async def test_skips_comment_lines(self, listener: XNodeListener) -> None:
        """Lines starting with ':' (keep-alive comments) must not affect state."""
        aiohttp_stub, _ = self._build_aiohttp_stub(sse_lines=[b": keep-alive\r\n"])
        listener._running = True

        with patch.dict("sys.modules", {"aiohttp": aiohttp_stub}):
            with (
                patch.object(
                    listener, "_get_sse_session_token", new_callable=AsyncMock, return_value=None
                ),
                patch.object(
                    listener, "_dispatch_sse_event", new_callable=AsyncMock
                ) as mock_dispatch,
            ):
                await listener._connect_and_listen()

        mock_dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_stops_reading_when_running_is_false(self, listener: XNodeListener) -> None:
        """If _running becomes False, the listener exits the event loop."""
        call_count = 0
        _listener_ref = listener

        class StopAfterFirstLine:
            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                nonlocal call_count
                for _ in range(10):
                    _listener_ref._running = False
                    call_count += 1
                    yield b"data: test\r\n"

        # Build the stub manually for custom content
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.content = StopAfterFirstLine()
        mock_resp_cm = MagicMock()
        mock_resp_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp_cm)
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)
        aiohttp_stub = MagicMock()
        aiohttp_stub.ClientTimeout = MagicMock(return_value=MagicMock())
        aiohttp_stub.ClientSession = MagicMock(return_value=mock_session_cm)

        listener._running = True
        with patch.dict("sys.modules", {"aiohttp": aiohttp_stub}):
            with patch.object(
                listener, "_get_sse_session_token", new_callable=AsyncMock, return_value=None
            ):
                await listener._connect_and_listen()

        # Should only process 1 iteration because _running is set to False immediately
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_updates_last_event_id_from_sse_id_field(self, listener: XNodeListener) -> None:
        """SSE 'id:' field updates _last_event_id after event dispatch."""
        sse_bytes = [
            b"id: evt-42\r\n",
            b"event: lead.send\r\n",
            b'data: {"type":"lead.send","data":{}}\r\n',
            b"\r\n",
        ]
        aiohttp_stub, _ = self._build_aiohttp_stub(sse_lines=sse_bytes)
        listener._running = True

        with patch.dict("sys.modules", {"aiohttp": aiohttp_stub}):
            with (
                patch.object(
                    listener, "_get_sse_session_token", new_callable=AsyncMock, return_value=None
                ),
                patch.object(listener, "_dispatch_sse_event", new_callable=AsyncMock),
            ):
                await listener._connect_and_listen()

        assert listener._last_event_id == "evt-42"

    @pytest.mark.asyncio
    async def test_ignores_id_field_with_null_byte(self, listener: XNodeListener) -> None:
        """SSE spec: id fields containing null must be ignored."""
        sse_bytes = [
            b"id: bad\x00id\r\n",
            b'data: {"type":"lead.send","data":{}}\r\n',
            b"\r\n",
        ]
        aiohttp_stub, _ = self._build_aiohttp_stub(sse_lines=sse_bytes)
        listener._running = True

        with patch.dict("sys.modules", {"aiohttp": aiohttp_stub}):
            with (
                patch.object(
                    listener, "_get_sse_session_token", new_callable=AsyncMock, return_value=None
                ),
                patch.object(listener, "_dispatch_sse_event", new_callable=AsyncMock),
            ):
                await listener._connect_and_listen()

        # null-byte id must not update last_event_id
        assert listener._last_event_id == ""

    @pytest.mark.asyncio
    async def test_sends_last_event_id_header_on_reconnect(self, listener: XNodeListener) -> None:
        """When _last_event_id is set, it must appear in request headers."""
        listener._last_event_id = "prev-123"
        aiohttp_stub, mock_session = self._build_aiohttp_stub()

        with patch.dict("sys.modules", {"aiohttp": aiohttp_stub}):
            with patch.object(
                listener, "_get_sse_session_token", new_callable=AsyncMock, return_value=None
            ):
                await listener._connect_and_listen()

        call_kwargs = mock_session.get.call_args[1]
        assert call_kwargs.get("headers", {}).get("Last-Event-ID") == "prev-123"
        assert call_kwargs.get("params", {}).get("last_event_id") == "prev-123"

    @pytest.mark.asyncio
    async def test_handles_field_with_no_colon(self, listener: XNodeListener) -> None:
        """A line without ':' is treated as a field name with empty value (SSE spec)."""
        sse_bytes = [
            b"data\r\n",  # field="data", value="" per SSE spec
            b"\r\n",
        ]
        aiohttp_stub, _ = self._build_aiohttp_stub(sse_lines=sse_bytes)
        listener._running = True

        with patch.dict("sys.modules", {"aiohttp": aiohttp_stub}):
            with (
                patch.object(
                    listener, "_get_sse_session_token", new_callable=AsyncMock, return_value=None
                ),
                patch.object(
                    listener, "_dispatch_sse_event", new_callable=AsyncMock
                ) as mock_dispatch,
            ):
                await listener._connect_and_listen()

        # Empty data line dispatched with empty string content
        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args[0][2] == ""


# ---------------------------------------------------------------------------
# _heartbeat_loop — iteration logic
# ---------------------------------------------------------------------------


class TestHeartbeatLoop:
    @pytest.mark.asyncio
    async def test_calls_write_heartbeat_while_running(self, listener: XNodeListener) -> None:
        """_heartbeat_loop must call _write_heartbeat on each iteration."""
        call_count = 0

        async def fake_write_heartbeat():
            nonlocal call_count
            call_count += 1
            # Stop after first call to avoid infinite loop
            listener._running = False

        listener._running = True
        with patch.object(listener, "_write_heartbeat", side_effect=fake_write_heartbeat):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await listener._heartbeat_loop()

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_does_not_call_write_heartbeat_when_not_running(
        self, listener: XNodeListener
    ) -> None:
        """_heartbeat_loop must not call _write_heartbeat if _running is False from start."""
        listener._running = False
        with patch.object(listener, "_write_heartbeat", new_callable=AsyncMock) as mock_hb:
            await listener._heartbeat_loop()
        mock_hb.assert_not_called()


# ---------------------------------------------------------------------------
# _outbox_retry_loop — iteration logic
# ---------------------------------------------------------------------------


class TestOutboxRetryLoop:
    @pytest.mark.asyncio
    async def test_calls_process_outbox_while_running(self, listener: XNodeListener) -> None:
        """_outbox_retry_loop must call _process_outbox on each iteration."""
        call_count = 0

        async def fake_process_outbox():
            nonlocal call_count
            call_count += 1
            listener._running = False

        listener._running = True
        with patch.object(listener, "_process_outbox", side_effect=fake_process_outbox):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await listener._outbox_retry_loop()

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_does_not_call_process_outbox_when_not_running(
        self, listener: XNodeListener
    ) -> None:
        """_outbox_retry_loop must not call _process_outbox if _running is False from start."""
        listener._running = False
        with patch.object(listener, "_process_outbox", new_callable=AsyncMock) as mock_proc:
            await listener._outbox_retry_loop()
        mock_proc.assert_not_called()


# ---------------------------------------------------------------------------
# _process_outbox — integration with aiohttp session
# ---------------------------------------------------------------------------


class TestProcessOutbox:
    def _make_outbox_aiohttp(self) -> MagicMock:
        """Return an aiohttp stub suitable for _process_outbox."""
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        aiohttp_mod = MagicMock()
        aiohttp_mod.ClientTimeout = MagicMock(return_value=MagicMock())
        aiohttp_mod.ClientSession = MagicMock(return_value=mock_session)
        return aiohttp_mod

    @pytest.mark.asyncio
    async def test_returns_early_when_no_jsonl_files(self, listener: XNodeListener) -> None:
        """_process_outbox must return without creating a session when outbox is empty."""
        aiohttp_mod = self._make_outbox_aiohttp()

        with patch("sys.modules", dict(sys.modules, aiohttp=aiohttp_mod)):
            with patch.object(
                listener, "_process_outbox_file", new_callable=AsyncMock
            ) as mock_proc_file:
                await listener._process_outbox()

        mock_proc_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_processes_each_jsonl_file_in_outbox(self, listener: XNodeListener) -> None:
        """_process_outbox must call _process_outbox_file for each .jsonl file."""
        outbox_dir = listener._dirs["realtime_outbox"]
        for i in range(3):
            (outbox_dir / f"file{i}.jsonl").write_text(
                '{"event_type":"lead.send","envelope":{},"recorded_at":"2026-01-01T00:00:00+00:00"}\n',
                encoding="utf-8",
            )

        aiohttp_mod = self._make_outbox_aiohttp()

        with patch("sys.modules", dict(sys.modules, aiohttp=aiohttp_mod)):
            with patch.object(
                listener, "_process_outbox_file", new_callable=AsyncMock
            ) as mock_proc_file:
                await listener._process_outbox()

        assert mock_proc_file.call_count == 3


# ---------------------------------------------------------------------------
# _setup_signals
# ---------------------------------------------------------------------------


class TestSetupSignals:
    @pytest.mark.asyncio
    async def test_registers_signal_handlers_on_supported_platform(
        self, listener: XNodeListener
    ) -> None:
        """_setup_signals must register SIGINT and SIGTERM handlers."""
        import signal as sig_mod

        loop = asyncio.get_event_loop()
        registered_signals: list[sig_mod.Signals] = []

        def fake_add_signal_handler(sig, handler, *args):
            registered_signals.append(sig)

        with patch.object(loop, "add_signal_handler", side_effect=fake_add_signal_handler):
            listener._setup_signals()

        assert sig_mod.SIGINT in registered_signals
        assert sig_mod.SIGTERM in registered_signals

    @pytest.mark.asyncio
    async def test_handles_not_implemented_error_gracefully(self, listener: XNodeListener) -> None:
        """_setup_signals must not raise when the platform rejects signal handlers."""
        loop = asyncio.get_event_loop()

        with patch.object(loop, "add_signal_handler", side_effect=NotImplementedError):
            listener._setup_signals()  # must not raise

    @pytest.mark.asyncio
    async def test_signal_handler_calls_stop(self, listener: XNodeListener) -> None:
        """The registered signal handler must call listener.stop()."""
        import signal as sig_mod

        loop = asyncio.get_event_loop()
        captured_handler = None
        captured_arg = None

        def fake_add(sig, handler, *args):
            nonlocal captured_handler, captured_arg
            if sig == sig_mod.SIGINT:
                captured_handler = handler
                captured_arg = args[0] if args else None

        listener._running = True
        with patch.object(loop, "add_signal_handler", side_effect=fake_add):
            listener._setup_signals()

        assert captured_handler is not None
        captured_handler(captured_arg)
        assert listener._running is False
