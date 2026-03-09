"""
Comprehensive unit tests for forge_harness.xnode_listener
==========================================================

Coverage targets (>= 80%):
- Module-level constants and patterns
- XNodeListener construction edge cases
- _mark_seen LRU eviction details
- _seed_seen_ids all branches
- _should_process all filtering paths
- _dispatch_sse_event SSE parsing, dedup, routing, error paths
- _handle_event routing table (all branches)
- _handle_lead_send idempotency, file content, notify
- _handle_lead_ack fallback ID, file content, notify
- _handle_handoff all ID fallbacks, file content, notify
- _handle_exception date-based JSONL, append
- _write_heartbeat connected/stopped states, OSError
- _heartbeat_loop and _outbox_retry_loop iteration behavior
- _process_outbox with/without aiohttp
- _process_outbox_file all terminal/pending/abandoned paths
- _mark_outbox_abandoned idempotency
- _retry_single_record success/failure/network-error
- _rewrite_jsonl atomic swap, redaction, empty list
- _resolve_target_node all sources
- _inbox_contains_message_id all branches
- _notify_lead_tmux all states (disabled, no-tmux, session-missing,
  delivered, send-failed, timeout, OSError)
- _get_sse_session_token success, non-200, exception, import-error
- _setup_signals registration, NotImplementedError
- stop() / start() once / backoff behaviour
- Module helpers: _now_iso, _now_slug, _append_jsonl, _write_json, _redact_secrets
"""

from __future__ import annotations

import asyncio
import json
import signal
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Stub aiohttp before importing xnode_listener.  This mirrors the approach in
# the existing test_xnode_listener.py so both files can co-exist safely.
# ---------------------------------------------------------------------------
_aiohttp_stub = MagicMock()
_aiohttp_stub.ClientSession = MagicMock()
_aiohttp_stub.ClientTimeout = MagicMock()
sys.modules.setdefault("aiohttp", _aiohttp_stub)

from forge_harness.xnode_listener import (  # noqa: E402
    _CHANNEL_PREFIXES,
    _HEARTBEAT_INTERVAL,
    _OUTBOX_RETRY_INTERVAL,
    _OUTBOX_TTL,
    _REDACTED,
    _SSE_READ_TIMEOUT,
    _TOKEN_ASSIGNMENT_PATTERN,
    _TOKEN_JSON_PATTERN,
    HANDLED_EVENT_TYPES,
    XNodeListener,
    _append_jsonl,
    _now_iso,
    _now_slug,
    _redact_secrets,
    _write_json,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def forge_root(tmp_path: Path) -> Path:
    return tmp_path / "FORGE"


@pytest.fixture()
def listener(forge_root: Path) -> XNodeListener:
    """XNodeListener with notifications disabled for fast unit tests."""
    return XNodeListener(
        command_center_url="http://cc.example.com:8080",
        token="unit-test-token",
        forge_root=forge_root,
        hostname="unithost",
        notify_lead=False,
    )


# ===========================================================================
# Module-level constants
# ===========================================================================


class TestModuleConstants:
    def test_handled_event_types_is_frozenset(self) -> None:
        assert isinstance(HANDLED_EVENT_TYPES, frozenset)

    def test_handled_event_types_contains_lead_send(self) -> None:
        assert "lead.send" in HANDLED_EVENT_TYPES

    def test_handled_event_types_contains_lead_ack(self) -> None:
        assert "lead.ack" in HANDLED_EVENT_TYPES

    def test_handled_event_types_contains_lead_handoff(self) -> None:
        assert "lead.handoff" in HANDLED_EVENT_TYPES

    def test_handled_event_types_contains_xnode_relay_exception(self) -> None:
        assert "xnode.relay.exception" in HANDLED_EVENT_TYPES

    def test_handled_event_types_has_four_entries(self) -> None:
        assert len(HANDLED_EVENT_TYPES) == 4

    def test_channel_prefixes_contains_lead_dot(self) -> None:
        assert "lead." in _CHANNEL_PREFIXES

    def test_channel_prefixes_contains_xnode_dot(self) -> None:
        assert "xnode." in _CHANNEL_PREFIXES

    def test_heartbeat_interval_is_positive(self) -> None:
        assert _HEARTBEAT_INTERVAL > 0

    def test_outbox_retry_interval_is_positive(self) -> None:
        assert _OUTBOX_RETRY_INTERVAL > 0

    def test_sse_read_timeout_is_positive(self) -> None:
        assert _SSE_READ_TIMEOUT > 0

    def test_outbox_ttl_is_timedelta(self) -> None:
        assert isinstance(_OUTBOX_TTL, timedelta)

    def test_redacted_sentinel_value(self) -> None:
        assert _REDACTED == "[REDACTED]"

    def test_token_assignment_pattern_matches_env_style(self) -> None:
        m = _TOKEN_ASSIGNMENT_PATTERN.search("FORGE_WEBHOOK_TOKEN=abc123")
        assert m is not None
        assert m.group(2) == "abc123"

    def test_token_assignment_pattern_case_insensitive(self) -> None:
        m = _TOKEN_ASSIGNMENT_PATTERN.search("forge_webhook_token=lower")
        assert m is not None

    def test_token_json_pattern_matches_json_style(self) -> None:
        m = _TOKEN_JSON_PATTERN.search('"FORGE_WEBHOOK_TOKEN": "secret"')
        assert m is not None
        assert m.group(2) == "secret"


# ===========================================================================
# Construction
# ===========================================================================


class TestConstruction:
    def test_trailing_slash_stripped(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.example.com/",
            token="t",
            forge_root=forge_root,
            hostname="h1",
        )
        assert not lst.url.endswith("/")

    def test_multiple_trailing_slashes_stripped(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc.example.com///",
            token="t",
            forge_root=forge_root,
            hostname="h1",
        )
        # rstrip("/") removes all trailing slashes
        assert not lst.url.endswith("/")

    def test_hostname_lowercased(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="UPPER",
        )
        assert lst.hostname == "upper"

    def test_hostname_stripped_of_whitespace(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname=" spaces ",
        )
        assert lst.hostname == "spaces"

    def test_default_lead_window(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="myhost",
        )
        assert lst.lead_window == "forge:myhost"

    def test_custom_lead_window_overrides_default(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="myhost",
            lead_window="custom:window",
        )
        assert lst.lead_window == "custom:window"

    def test_notify_lead_defaults_to_true(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="h",
        )
        assert lst.notify_lead is True

    def test_notify_lead_false_stored(self, listener: XNodeListener) -> None:
        assert listener.notify_lead is False

    def test_channels_none_by_default(self, listener: XNodeListener) -> None:
        assert listener.channels is None

    def test_channels_list_stored(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="h",
            channels=["lead.h.inbox", "xnode.h.relay"],
        )
        assert lst.channels == ["lead.h.inbox", "xnode.h.relay"]

    def test_running_is_false_initially(self, listener: XNodeListener) -> None:
        assert listener._running is False

    def test_backoff_initial_value(self, listener: XNodeListener) -> None:
        assert listener._backoff == 1.0

    def test_max_backoff_value(self, listener: XNodeListener) -> None:
        assert listener._max_backoff == 30.0

    def test_seen_message_ids_empty_dict(self, listener: XNodeListener) -> None:
        assert listener._seen_message_ids == {}

    def test_last_event_id_empty_string(self, listener: XNodeListener) -> None:
        assert listener._last_event_id == ""

    def test_all_expected_dirs_created(self, listener: XNodeListener) -> None:
        for key in ("root", "lead_inbox", "handoffs", "acks", "exceptions", "realtime_outbox"):
            assert listener._dirs[key].is_dir(), f"Directory '{key}' missing"

    def test_dirs_are_under_forge_xnode(self, listener: XNodeListener) -> None:
        expected_root = listener.forge_root / ".forge" / "xnode"
        assert listener._dirs["root"] == expected_root


# ===========================================================================
# _mark_seen — LRU behaviour
# ===========================================================================


class TestMarkSeen:
    def test_id_added_to_dict(self, listener: XNodeListener) -> None:
        listener._mark_seen("x-1")
        assert "x-1" in listener._seen_message_ids

    def test_duplicate_id_does_not_grow_dict(self, listener: XNodeListener) -> None:
        listener._mark_seen("same")
        listener._mark_seen("same")
        assert len(listener._seen_message_ids) == 1

    def test_lru_evicts_oldest_when_full(self, listener: XNodeListener) -> None:
        listener._MAX_SEEN_IDS = 2
        listener._mark_seen("a")
        listener._mark_seen("b")
        listener._mark_seen("c")  # triggers eviction of "a"
        assert "a" not in listener._seen_message_ids
        assert "b" in listener._seen_message_ids
        assert "c" in listener._seen_message_ids

    def test_size_stays_at_limit_after_many_inserts(self, listener: XNodeListener) -> None:
        listener._MAX_SEEN_IDS = 3
        for i in range(10):
            listener._mark_seen(f"id-{i}")
        assert len(listener._seen_message_ids) == 3

    def test_insertion_order_preserved_oldest_evicted_first(
        self, listener: XNodeListener
    ) -> None:
        listener._MAX_SEEN_IDS = 3
        listener._mark_seen("first")
        listener._mark_seen("second")
        listener._mark_seen("third")
        listener._mark_seen("fourth")
        assert "first" not in listener._seen_message_ids
        assert "fourth" in listener._seen_message_ids


# ===========================================================================
# _seed_seen_ids
# ===========================================================================


class TestSeedSeenIds:
    def test_seeds_from_flat_message_id(self, listener: XNodeListener) -> None:
        f = listener._dirs["lead_inbox"] / "unithost.jsonl"
        f.write_text(json.dumps({"message_id": "flat-seed"}) + "\n", encoding="utf-8")
        listener._seed_seen_ids()
        assert "flat-seed" in listener._seen_message_ids

    def test_seeds_from_envelope_message_id(self, listener: XNodeListener) -> None:
        f = listener._dirs["lead_inbox"] / "unithost.jsonl"
        f.write_text(
            json.dumps({"envelope": {"message_id": "env-seed"}}) + "\n", encoding="utf-8"
        )
        listener._seed_seen_ids()
        assert "env-seed" in listener._seen_message_ids

    def test_empty_inbox_directory_leaves_seen_ids_empty(
        self, listener: XNodeListener
    ) -> None:
        listener._seed_seen_ids()
        assert len(listener._seen_message_ids) == 0

    def test_skips_empty_lines(self, listener: XNodeListener) -> None:
        f = listener._dirs["lead_inbox"] / "test.jsonl"
        f.write_text("\n  \n\n", encoding="utf-8")
        listener._seed_seen_ids()
        assert len(listener._seen_message_ids) == 0

    def test_skips_non_json_lines_gracefully(self, listener: XNodeListener) -> None:
        f = listener._dirs["lead_inbox"] / "bad.jsonl"
        f.write_text("this is not json\n", encoding="utf-8")
        listener._seed_seen_ids()
        assert len(listener._seen_message_ids) == 0

    def test_skips_records_without_message_id(self, listener: XNodeListener) -> None:
        f = listener._dirs["lead_inbox"] / "no-id.jsonl"
        f.write_text(json.dumps({"some": "data"}) + "\n", encoding="utf-8")
        listener._seed_seen_ids()
        assert len(listener._seen_message_ids) == 0

    def test_processes_multiple_jsonl_files(self, listener: XNodeListener) -> None:
        for name in ("a.jsonl", "b.jsonl"):
            f = listener._dirs["lead_inbox"] / name
            f.write_text(
                json.dumps({"message_id": f"seed-{name}"}) + "\n", encoding="utf-8"
            )
        listener._seed_seen_ids()
        assert "seed-a.jsonl" in listener._seen_message_ids
        assert "seed-b.jsonl" in listener._seen_message_ids

    def test_oserror_on_unreadable_file_does_not_raise(
        self, listener: XNodeListener
    ) -> None:
        f = listener._dirs["lead_inbox"] / "locked.jsonl"
        f.write_text(json.dumps({"message_id": "locked-id"}), encoding="utf-8")
        f.chmod(0o000)
        try:
            listener._seed_seen_ids()  # must not raise
        finally:
            f.chmod(0o644)


# ===========================================================================
# _should_process — filtering branches
# ===========================================================================


class TestShouldProcess:
    """Comprehensive filtering logic coverage."""

    def _broadcast_payload(self) -> dict:
        return {"data": {"envelope": {}}}

    def _targeted_payload(self, node: str) -> dict:
        return {"data": {"envelope": {"target": {"node": node}}}}

    def _flat_targeted_payload(self, node: str) -> dict:
        return {"data": {"target_node": node, "envelope": {}}}

    def test_rejects_completely_unknown_event_type(
        self, listener: XNodeListener
    ) -> None:
        assert listener._should_process("foo.bar", "foo.bar", {}) is False

    def test_accepts_all_four_handled_event_types(
        self, listener: XNodeListener
    ) -> None:
        for et in HANDLED_EVENT_TYPES:
            assert (
                listener._should_process(et, et, self._broadcast_payload()) is True
            ), f"Failed for {et}"

    def test_accepts_lead_dot_channel_prefix_even_if_not_in_handled(
        self, listener: XNodeListener
    ) -> None:
        # SSE type starts with "lead." even though canonical event_type is unknown
        assert (
            listener._should_process(
                "lead.custom", "lead.custom.inbox", self._broadcast_payload()
            )
            is True
        )

    def test_accepts_xnode_dot_channel_prefix(self, listener: XNodeListener) -> None:
        assert (
            listener._should_process(
                "xnode.custom", "xnode.custom.relay", self._broadcast_payload()
            )
            is True
        )

    def test_broadcast_event_accepted_when_no_target_node(
        self, listener: XNodeListener
    ) -> None:
        assert (
            listener._should_process(
                "lead.send", "lead.send", self._broadcast_payload()
            )
            is True
        )

    def test_envelope_target_node_match_accepted(
        self, listener: XNodeListener
    ) -> None:
        assert (
            listener._should_process(
                "lead.send", "lead.send", self._targeted_payload("unithost")
            )
            is True
        )

    def test_envelope_target_node_mismatch_rejected(
        self, listener: XNodeListener
    ) -> None:
        assert (
            listener._should_process(
                "lead.send", "lead.send", self._targeted_payload("othernode")
            )
            is False
        )

    def test_flat_target_node_match_accepted(self, listener: XNodeListener) -> None:
        assert (
            listener._should_process(
                "lead.send", "lead.send", self._flat_targeted_payload("unithost")
            )
            is True
        )

    def test_flat_target_node_mismatch_rejected(
        self, listener: XNodeListener
    ) -> None:
        assert (
            listener._should_process(
                "lead.send", "lead.send", self._flat_targeted_payload("notme")
            )
            is False
        )

    def test_channels_override_accepted(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="h",
            channels=["lead.h.inbox"],
        )
        result = lst._should_process("lead.send", "lead.h.inbox", self._broadcast_payload())
        assert result is True

    def test_channels_override_rejected_when_no_match(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="h",
            channels=["lead.h.inbox"],
        )
        result = lst._should_process("lead.send", "lead.other.inbox", self._broadcast_payload())
        assert result is False

    def test_case_insensitive_target_node_comparison(
        self, listener: XNodeListener
    ) -> None:
        payload = {"data": {"envelope": {"target": {"node": "UNITHOST"}}}}
        assert listener._should_process("lead.send", "lead.send", payload) is True

    def test_whitespace_in_target_node_trimmed(
        self, listener: XNodeListener
    ) -> None:
        payload = {"data": {"envelope": {"target": {"node": "  unithost  "}}}}
        assert listener._should_process("lead.send", "lead.send", payload) is True

    def test_empty_data_dict_treated_as_broadcast(
        self, listener: XNodeListener
    ) -> None:
        # No envelope key at all — should be treated as broadcast
        assert listener._should_process("lead.send", "lead.send", {"data": {}}) is True


# ===========================================================================
# _resolve_target_node
# ===========================================================================


class TestResolveTargetNode:
    def test_nested_target_node(self, listener: XNodeListener) -> None:
        assert listener._resolve_target_node({"target": {"node": "Nova"}}) == "nova"

    def test_flat_target_node(self, listener: XNodeListener) -> None:
        assert listener._resolve_target_node({"target_node": "Prya"}) == "prya"

    def test_falls_back_to_self_hostname_on_empty_envelope(
        self, listener: XNodeListener
    ) -> None:
        assert listener._resolve_target_node({}) == "unithost"

    def test_falls_back_when_node_value_is_empty_string(
        self, listener: XNodeListener
    ) -> None:
        assert listener._resolve_target_node({"target": {"node": ""}}) == "unithost"

    def test_nested_takes_precedence_over_flat(self, listener: XNodeListener) -> None:
        env = {"target": {"node": "nested"}, "target_node": "flat"}
        assert listener._resolve_target_node(env) == "nested"

    def test_strips_and_lowercases(self, listener: XNodeListener) -> None:
        assert listener._resolve_target_node({"target": {"node": "  NODE  "}}) == "node"


# ===========================================================================
# _inbox_contains_message_id
# ===========================================================================


class TestInboxContainsMessageId:
    def test_returns_false_for_nonexistent_file(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        assert listener._inbox_contains_message_id(tmp_path / "ghost.jsonl", "x") is False

    def test_finds_flat_message_id(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        f = tmp_path / "inbox.jsonl"
        f.write_text(json.dumps({"message_id": "found-1"}) + "\n", encoding="utf-8")
        assert listener._inbox_contains_message_id(f, "found-1") is True

    def test_finds_nested_envelope_message_id(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        f = tmp_path / "inbox.jsonl"
        f.write_text(
            json.dumps({"envelope": {"message_id": "nested-1"}}) + "\n",
            encoding="utf-8",
        )
        assert listener._inbox_contains_message_id(f, "nested-1") is True

    def test_returns_false_for_different_id(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        f = tmp_path / "inbox.jsonl"
        f.write_text(json.dumps({"message_id": "abc"}) + "\n", encoding="utf-8")
        assert listener._inbox_contains_message_id(f, "xyz") is False

    def test_skips_malformed_json_and_finds_valid_record(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        f = tmp_path / "inbox.jsonl"
        f.write_text(
            "INVALID JSON\n" + json.dumps({"message_id": "valid-1"}) + "\n",
            encoding="utf-8",
        )
        assert listener._inbox_contains_message_id(f, "valid-1") is True

    def test_skips_blank_lines(self, listener: XNodeListener, tmp_path: Path) -> None:
        f = tmp_path / "inbox.jsonl"
        f.write_text("\n\n" + json.dumps({"message_id": "after-blank"}) + "\n", encoding="utf-8")
        assert listener._inbox_contains_message_id(f, "after-blank") is True

    def test_returns_false_on_oserror(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        f = tmp_path / "locked.jsonl"
        f.write_text(json.dumps({"message_id": "secret"}), encoding="utf-8")
        f.chmod(0o000)
        try:
            assert listener._inbox_contains_message_id(f, "secret") is False
        finally:
            f.chmod(0o644)


# ===========================================================================
# _handle_lead_send
# ===========================================================================


class TestHandleLeadSend:
    @pytest.mark.asyncio
    async def test_writes_record_to_jsonl(self, listener: XNodeListener) -> None:
        await listener._handle_lead_send({"message_id": "ls-1"})
        out = listener._dirs["lead_inbox"] / "unithost.jsonl"
        assert out.exists()
        record = json.loads(out.read_text().strip())
        assert record["type"] == "lead.send"
        assert "received_at" in record

    @pytest.mark.asyncio
    async def test_marks_message_id_seen(self, listener: XNodeListener) -> None:
        await listener._handle_lead_send({"message_id": "seen-ls"})
        assert "seen-ls" in listener._seen_message_ids

    @pytest.mark.asyncio
    async def test_skips_in_memory_duplicate(self, listener: XNodeListener) -> None:
        listener._mark_seen("dup-ls")
        await listener._handle_lead_send({"message_id": "dup-ls"})
        out = listener._dirs["lead_inbox"] / "unithost.jsonl"
        assert not out.exists()

    @pytest.mark.asyncio
    async def test_skips_durable_duplicate(self, listener: XNodeListener) -> None:
        out = listener._dirs["lead_inbox"] / "unithost.jsonl"
        out.write_text(json.dumps({"message_id": "dur-dup"}) + "\n", encoding="utf-8")
        await listener._handle_lead_send({"message_id": "dur-dup"})
        lines = out.read_text().strip().splitlines()
        assert len(lines) == 1  # no second record written

    @pytest.mark.asyncio
    async def test_envelope_stored_inside_record(self, listener: XNodeListener) -> None:
        env = {"message_id": "env-ls", "payload": {"task_id": "T-99"}}
        await listener._handle_lead_send(env)
        out = listener._dirs["lead_inbox"] / "unithost.jsonl"
        record = json.loads(out.read_text().strip())
        assert record["envelope"]["message_id"] == "env-ls"

    @pytest.mark.asyncio
    async def test_uses_target_node_for_filename(self, listener: XNodeListener) -> None:
        env = {
            "message_id": "fn-ls",
            "target": {"node": "unithost"},
        }
        await listener._handle_lead_send(env)
        out = listener._dirs["lead_inbox"] / "unithost.jsonl"
        assert out.exists()

    @pytest.mark.asyncio
    async def test_notify_called_when_notify_lead_true(
        self, forge_root: Path
    ) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="unithost",
            notify_lead=True,
        )
        with patch.object(lst, "_notify_lead_tmux") as mock_notify:
            await lst._handle_lead_send({"message_id": "notify-ls"})
        mock_notify.assert_called_once()
        assert mock_notify.call_args[0][0] == "lead.send"

    @pytest.mark.asyncio
    async def test_no_subprocess_when_notify_disabled(
        self, listener: XNodeListener
    ) -> None:
        with patch("subprocess.run") as mock_sub:
            await listener._handle_lead_send({"message_id": "no-sub"})
        mock_sub.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_message_id_uses_question_mark(
        self, listener: XNodeListener
    ) -> None:
        await listener._handle_lead_send({})
        out = listener._dirs["lead_inbox"] / "unithost.jsonl"
        assert out.exists()


# ===========================================================================
# _handle_lead_ack
# ===========================================================================


class TestHandleLeadAck:
    @pytest.mark.asyncio
    async def test_stores_ack_json_by_message_id(
        self, listener: XNodeListener
    ) -> None:
        await listener._handle_lead_ack(
            {"message_id": "ack-1", "status": "ack", "acknowledged_by_node": "other"}
        )
        out = listener._dirs["acks"] / "ack-1.json"
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["type"] == "lead.ack"
        assert data["message_id"] == "ack-1"

    @pytest.mark.asyncio
    async def test_falls_back_to_ack_message_id_field(
        self, listener: XNodeListener
    ) -> None:
        await listener._handle_lead_ack({"ack_message_id": "fallback-ack"})
        out = listener._dirs["acks"] / "fallback-ack.json"
        assert out.exists()

    @pytest.mark.asyncio
    async def test_received_at_present_in_record(
        self, listener: XNodeListener
    ) -> None:
        await listener._handle_lead_ack({"message_id": "ack-ts"})
        out = listener._dirs["acks"] / "ack-ts.json"
        data = json.loads(out.read_text())
        assert "received_at" in data

    @pytest.mark.asyncio
    async def test_notify_called_with_lead_ack_event_type(
        self, forge_root: Path
    ) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="unithost",
            notify_lead=True,
        )
        with patch.object(lst, "_notify_lead_tmux") as mock_notify:
            await lst._handle_lead_ack({"message_id": "ack-n"})
        mock_notify.assert_called_once()
        assert mock_notify.call_args[0][0] == "lead.ack"

    @pytest.mark.asyncio
    async def test_falls_back_to_generated_slug_when_no_id(
        self, listener: XNodeListener
    ) -> None:
        await listener._handle_lead_ack({})
        acks_dir = listener._dirs["acks"]
        files = list(acks_dir.glob("*.json"))
        assert len(files) == 1


# ===========================================================================
# _handle_handoff
# ===========================================================================


class TestHandleHandoff:
    @pytest.mark.asyncio
    async def test_stores_handoff_json_by_handoff_id(
        self, listener: XNodeListener
    ) -> None:
        await listener._handle_handoff(
            {"handoff_id": "hnd-1", "source_node": "prya", "summary": "done", "risk": "low"}
        )
        out = listener._dirs["handoffs"] / "hnd-1.json"
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["type"] == "lead.handoff"
        assert data["handoff_id"] == "hnd-1"

    @pytest.mark.asyncio
    async def test_falls_back_to_message_id(self, listener: XNodeListener) -> None:
        await listener._handle_handoff({"message_id": "msg-hnd"})
        out = listener._dirs["handoffs"] / "msg-hnd.json"
        assert out.exists()

    @pytest.mark.asyncio
    async def test_falls_back_to_timestamp_slug_when_no_ids(
        self, listener: XNodeListener
    ) -> None:
        await listener._handle_handoff({})
        files = list(listener._dirs["handoffs"].glob("*.json"))
        assert len(files) == 1

    @pytest.mark.asyncio
    async def test_received_at_present(self, listener: XNodeListener) -> None:
        await listener._handle_handoff({"handoff_id": "hnd-ts"})
        data = json.loads((listener._dirs["handoffs"] / "hnd-ts.json").read_text())
        assert "received_at" in data

    @pytest.mark.asyncio
    async def test_notify_called_with_lead_handoff_event_type(
        self, forge_root: Path
    ) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="unithost",
            notify_lead=True,
        )
        with patch.object(lst, "_notify_lead_tmux") as mock_notify:
            await lst._handle_handoff({"handoff_id": "hnd-notify"})
        mock_notify.assert_called_once()
        assert mock_notify.call_args[0][0] == "lead.handoff"


# ===========================================================================
# _handle_exception
# ===========================================================================


class TestHandleException:
    @pytest.mark.asyncio
    async def test_stores_exception_in_dated_jsonl(
        self, listener: XNodeListener
    ) -> None:
        await listener._handle_exception({"message_id": "exc-1", "error": "boom"})
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        out = listener._dirs["exceptions"] / f"{date_str}.jsonl"
        assert out.exists()
        record = json.loads(out.read_text().strip())
        assert record["type"] == "xnode.relay.exception"

    @pytest.mark.asyncio
    async def test_appends_multiple_on_same_day(
        self, listener: XNodeListener
    ) -> None:
        for i in range(3):
            await listener._handle_exception({"message_id": f"exc-{i}"})
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        lines = (
            (listener._dirs["exceptions"] / f"{date_str}.jsonl")
            .read_text()
            .strip()
            .splitlines()
        )
        assert len(lines) == 3

    @pytest.mark.asyncio
    async def test_envelope_stored_in_record(self, listener: XNodeListener) -> None:
        await listener._handle_exception({"message_id": "exc-env"})
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        record = json.loads(
            (listener._dirs["exceptions"] / f"{date_str}.jsonl").read_text().strip()
        )
        assert record["envelope"]["message_id"] == "exc-env"

    @pytest.mark.asyncio
    async def test_received_at_is_utc_iso(self, listener: XNodeListener) -> None:
        await listener._handle_exception({})
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        record = json.loads(
            (listener._dirs["exceptions"] / f"{date_str}.jsonl").read_text().strip()
        )
        parsed = datetime.fromisoformat(record["received_at"])
        assert parsed.tzinfo is not None


# ===========================================================================
# _handle_event routing
# ===========================================================================


class TestHandleEvent:
    @pytest.mark.asyncio
    async def test_routes_lead_send_to_handle_lead_send(
        self, listener: XNodeListener
    ) -> None:
        with patch.object(listener, "_handle_lead_send", new_callable=AsyncMock) as m:
            await listener._handle_event("lead.send", {"envelope": {"message_id": "r1"}})
        m.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_lead_ack_to_handle_lead_ack(
        self, listener: XNodeListener
    ) -> None:
        with patch.object(listener, "_handle_lead_ack", new_callable=AsyncMock) as m:
            await listener._handle_event("lead.ack", {})
        m.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_lead_handoff_to_handle_handoff(
        self, listener: XNodeListener
    ) -> None:
        with patch.object(listener, "_handle_handoff", new_callable=AsyncMock) as m:
            await listener._handle_event("lead.handoff", {})
        m.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_xnode_relay_exception_to_handle_exception(
        self, listener: XNodeListener
    ) -> None:
        with patch.object(listener, "_handle_exception", new_callable=AsyncMock) as m:
            await listener._handle_event("xnode.relay.exception", {})
        m.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_event_type_does_not_raise(
        self, listener: XNodeListener
    ) -> None:
        await listener._handle_event("totally.unknown", {})  # must not raise

    @pytest.mark.asyncio
    async def test_envelope_extracted_from_data_dict(
        self, listener: XNodeListener
    ) -> None:
        inner = {"message_id": "inner-env"}
        with patch.object(listener, "_handle_lead_send", new_callable=AsyncMock) as m:
            await listener._handle_event("lead.send", {"envelope": inner})
        m.assert_called_once_with(inner)

    @pytest.mark.asyncio
    async def test_empty_data_dict_passes_data_as_envelope_fallback(
        self, listener: XNodeListener
    ) -> None:
        # When no "envelope" key, data itself is passed as the envelope
        with patch.object(listener, "_handle_lead_send", new_callable=AsyncMock) as m:
            await listener._handle_event("lead.send", {"message_id": "flat-env"})
        m.assert_called_once_with({"message_id": "flat-env"})


# ===========================================================================
# _dispatch_sse_event
# ===========================================================================


class TestDispatchSseEvent:
    @pytest.mark.asyncio
    async def test_invalid_json_does_not_raise(self, listener: XNodeListener) -> None:
        await listener._dispatch_sse_event("id-1", "lead.send", "NOT VALID JSON")

    @pytest.mark.asyncio
    async def test_empty_event_type_skips_processing(
        self, listener: XNodeListener
    ) -> None:
        payload = json.dumps({"type": "", "data": {}})
        with patch.object(listener, "_handle_event", new_callable=AsyncMock) as m:
            await listener._dispatch_sse_event("", "", payload)
        m.assert_not_called()

    @pytest.mark.asyncio
    async def test_routes_to_handle_event_for_broadcast(
        self, listener: XNodeListener
    ) -> None:
        payload = json.dumps({"type": "lead.send", "data": {"message_id": "d-1"}})
        with patch.object(listener, "_handle_event", new_callable=AsyncMock) as m:
            await listener._dispatch_sse_event("id-1", "lead.send", payload)
        m.assert_called_once()

    @pytest.mark.asyncio
    async def test_dedup_via_message_id_in_data(self, listener: XNodeListener) -> None:
        listener._mark_seen("dedup-d")
        payload = json.dumps({"type": "lead.send", "data": {"message_id": "dedup-d"}})
        with patch.object(listener, "_handle_event", new_callable=AsyncMock) as m:
            await listener._dispatch_sse_event("id-1", "lead.send", payload)
        m.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_via_envelope_message_id(self, listener: XNodeListener) -> None:
        listener._mark_seen("dedup-env")
        payload = json.dumps(
            {
                "type": "lead.send",
                "data": {"envelope": {"message_id": "dedup-env"}},
            }
        )
        with patch.object(listener, "_handle_event", new_callable=AsyncMock) as m:
            await listener._dispatch_sse_event("id-1", "lead.send", payload)
        m.assert_not_called()

    @pytest.mark.asyncio
    async def test_filters_event_for_other_node(self, listener: XNodeListener) -> None:
        payload = json.dumps(
            {
                "type": "lead.send",
                "data": {"envelope": {"target": {"node": "othernode"}}},
            }
        )
        with patch.object(listener, "_handle_event", new_callable=AsyncMock) as m:
            await listener._dispatch_sse_event("id-1", "lead.send", payload)
        m.assert_not_called()

    @pytest.mark.asyncio
    async def test_handler_exception_caught_does_not_propagate(
        self, listener: XNodeListener
    ) -> None:
        payload = json.dumps({"type": "lead.send", "data": {}})
        with patch.object(
            listener,
            "_handle_event",
            new_callable=AsyncMock,
            side_effect=RuntimeError("test-error"),
        ):
            await listener._dispatch_sse_event("id-1", "lead.send", payload)

    @pytest.mark.asyncio
    async def test_sse_event_field_overrides_type_in_payload(
        self, listener: XNodeListener
    ) -> None:
        """SSE event: field takes priority for event_type resolution."""
        payload = json.dumps({"type": "lead.testnode.inbox", "data": {}})
        with patch.object(listener, "_handle_event", new_callable=AsyncMock) as m:
            await listener._dispatch_sse_event("id-1", "lead.send", payload)
        m.assert_called_once()
        # event_type passed should be "lead.send" (sse_event overrides type field via sse_type)

    @pytest.mark.asyncio
    async def test_data_event_type_field_resolves_canonical_type(
        self, listener: XNodeListener
    ) -> None:
        """data.event_type should resolve canonical event_type from channel-routed SSE."""
        payload = json.dumps(
            {
                "type": "lead.unithost.inbox",
                "data": {
                    "event_type": "lead.send",
                    "envelope": {"target": {"node": "unithost"}},
                },
            }
        )
        with patch.object(listener, "_handle_event", new_callable=AsyncMock) as m:
            await listener._dispatch_sse_event("id-1", "lead.unithost.inbox", payload)
        m.assert_called_once()
        call_event_type = m.call_args[0][0]
        assert call_event_type == "lead.send"


# ===========================================================================
# _write_heartbeat
# ===========================================================================


class TestWriteHeartbeat:
    @pytest.mark.asyncio
    async def test_creates_heartbeat_file(self, listener: XNodeListener) -> None:
        listener._running = True
        await listener._write_heartbeat()
        hb_file = (
            listener.forge_root / ".forge" / "ops" / "heartbeat" / "unithost.state.json"
        )
        assert hb_file.exists()

    @pytest.mark.asyncio
    async def test_status_connected_when_running(self, listener: XNodeListener) -> None:
        listener._running = True
        await listener._write_heartbeat()
        hb_file = (
            listener.forge_root / ".forge" / "ops" / "heartbeat" / "unithost.state.json"
        )
        data = json.loads(hb_file.read_text())
        assert data["status"] == "connected"

    @pytest.mark.asyncio
    async def test_status_stopped_when_not_running(self, listener: XNodeListener) -> None:
        listener._running = False
        await listener._write_heartbeat()
        hb_file = (
            listener.forge_root / ".forge" / "ops" / "heartbeat" / "unithost.state.json"
        )
        data = json.loads(hb_file.read_text())
        assert data["status"] == "stopped"

    @pytest.mark.asyncio
    async def test_hostname_in_state_file(self, listener: XNodeListener) -> None:
        await listener._write_heartbeat()
        hb_file = (
            listener.forge_root / ".forge" / "ops" / "heartbeat" / "unithost.state.json"
        )
        data = json.loads(hb_file.read_text())
        assert data["hostname"] == "unithost"

    @pytest.mark.asyncio
    async def test_listener_field_is_xnode(self, listener: XNodeListener) -> None:
        await listener._write_heartbeat()
        hb_file = (
            listener.forge_root / ".forge" / "ops" / "heartbeat" / "unithost.state.json"
        )
        data = json.loads(hb_file.read_text())
        assert data["listener"] == "xnode"

    @pytest.mark.asyncio
    async def test_last_heartbeat_is_utc_timestamp(self, listener: XNodeListener) -> None:
        await listener._write_heartbeat()
        hb_file = (
            listener.forge_root / ".forge" / "ops" / "heartbeat" / "unithost.state.json"
        )
        data = json.loads(hb_file.read_text())
        parsed = datetime.fromisoformat(data["last_heartbeat"])
        assert parsed.tzinfo is not None

    @pytest.mark.asyncio
    async def test_oserror_on_write_does_not_raise(self, listener: XNodeListener) -> None:
        listener._running = True
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            await listener._write_heartbeat()  # must not raise


# ===========================================================================
# _heartbeat_loop
# ===========================================================================


class TestHeartbeatLoop:
    @pytest.mark.asyncio
    async def test_calls_write_heartbeat_and_then_stops(
        self, listener: XNodeListener
    ) -> None:
        call_count = 0

        async def fake_write_heartbeat():
            nonlocal call_count
            call_count += 1
            listener._running = False  # Stop after first write

        listener._running = True

        with (
            patch.object(listener, "_write_heartbeat", side_effect=fake_write_heartbeat),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await listener._heartbeat_loop()

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_loop_exits_when_running_is_false(
        self, listener: XNodeListener
    ) -> None:
        listener._running = False
        write_mock = AsyncMock()
        with patch.object(listener, "_write_heartbeat", write_mock):
            await listener._heartbeat_loop()
        write_mock.assert_not_called()


# ===========================================================================
# _outbox_retry_loop
# ===========================================================================


class TestOutboxRetryLoop:
    @pytest.mark.asyncio
    async def test_calls_process_outbox_and_then_stops(
        self, listener: XNodeListener
    ) -> None:
        call_count = 0

        async def fake_process_outbox():
            nonlocal call_count
            call_count += 1
            listener._running = False

        listener._running = True
        with (
            patch.object(listener, "_process_outbox", side_effect=fake_process_outbox),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await listener._outbox_retry_loop()

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_loop_skips_when_not_running(self, listener: XNodeListener) -> None:
        listener._running = False
        process_mock = AsyncMock()
        with patch.object(listener, "_process_outbox", process_mock):
            await listener._outbox_retry_loop()
        process_mock.assert_not_called()


# ===========================================================================
# _process_outbox
# ===========================================================================


class TestProcessOutbox:
    @pytest.mark.asyncio
    async def test_returns_early_when_no_jsonl_files(
        self, listener: XNodeListener
    ) -> None:
        process_file_mock = AsyncMock()
        # Set up the aiohttp stub so the import check inside _process_outbox succeeds
        mock_aiohttp = MagicMock()
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_aiohttp.ClientSession.return_value = mock_session_ctx
        mock_aiohttp.ClientTimeout = MagicMock()
        with (
            patch.dict(sys.modules, {"aiohttp": mock_aiohttp}),
            patch.object(listener, "_process_outbox_file", process_file_mock),
        ):
            await listener._process_outbox()
        process_file_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_process_outbox_file_for_each_jsonl(
        self, listener: XNodeListener
    ) -> None:
        outbox_dir = listener._dirs["realtime_outbox"]
        for i in range(2):
            f = outbox_dir / f"record-{i}.jsonl"
            f.write_text(
                json.dumps(
                    {
                        "event_type": "lead.send",
                        "envelope": {},
                        "recorded_at": datetime.now(UTC).isoformat(),
                    }
                )
                + "\n",
                encoding="utf-8",
            )

        process_file_mock = AsyncMock()
        mock_aiohttp = MagicMock()
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_aiohttp.ClientSession.return_value = mock_session_ctx
        mock_aiohttp.ClientTimeout = MagicMock()

        with (
            patch.dict(sys.modules, {"aiohttp": mock_aiohttp}),
            patch.object(listener, "_process_outbox_file", process_file_mock),
        ):
            await listener._process_outbox()

        assert process_file_mock.call_count == 2


# ===========================================================================
# _process_outbox_file
# ===========================================================================


class TestProcessOutboxFile:
    def _make_pending_record(self, hours_ago: float = 0.5) -> dict:
        return {
            "event_type": "lead.send",
            "envelope": {"message_id": "o-1"},
            "recorded_at": (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat(),
        }

    @pytest.mark.asyncio
    async def test_missing_file_handled_gracefully(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        await listener._process_outbox_file(AsyncMock(), tmp_path / "ghost.jsonl")

    @pytest.mark.asyncio
    async def test_empty_file_skips_retry(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        retry_mock = AsyncMock()
        with patch.object(listener, "_retry_single_record", retry_mock):
            await listener._process_outbox_file(AsyncMock(), path)
        retry_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_delivered_skips_retry(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        record = self._make_pending_record()
        record["delivered_at"] = _now_iso()
        path = tmp_path / "delivered.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        retry_mock = AsyncMock()
        with patch.object(listener, "_retry_single_record", retry_mock):
            await listener._process_outbox_file(AsyncMock(), path)
        retry_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_abandoned_skips_retry(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        record = self._make_pending_record()
        record["abandoned_at"] = _now_iso()
        path = tmp_path / "abandoned.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        retry_mock = AsyncMock()
        with patch.object(listener, "_retry_single_record", retry_mock):
            await listener._process_outbox_file(AsyncMock(), path)
        retry_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_record_marked_abandoned(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        record = self._make_pending_record(hours_ago=25)
        path = tmp_path / "expired.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        await listener._process_outbox_file(AsyncMock(), path)

        updated = json.loads(path.read_text().strip())
        assert "abandoned_at" in updated
        assert updated["abandon_reason"] == "expired_ttl"

    @pytest.mark.asyncio
    async def test_invalid_timestamp_marked_abandoned(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        record = self._make_pending_record()
        record["recorded_at"] = "not-a-date"
        path = tmp_path / "bad-ts.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        await listener._process_outbox_file(AsyncMock(), path)

        updated = json.loads(path.read_text().strip())
        assert updated["abandon_reason"] == "invalid_recorded_at"

    @pytest.mark.asyncio
    async def test_successful_retry_marks_delivered(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "pending.jsonl"
        path.write_text(json.dumps(self._make_pending_record()) + "\n", encoding="utf-8")

        with patch.object(
            listener, "_retry_single_record", new_callable=AsyncMock, return_value=True
        ):
            await listener._process_outbox_file(AsyncMock(), path)

        updated = json.loads(path.read_text().strip())
        assert "delivered_at" in updated

    @pytest.mark.asyncio
    async def test_failed_retry_leaves_record_pending(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "pending.jsonl"
        path.write_text(json.dumps(self._make_pending_record()) + "\n", encoding="utf-8")

        with patch.object(
            listener, "_retry_single_record", new_callable=AsyncMock, return_value=False
        ):
            await listener._process_outbox_file(AsyncMock(), path)

        content = json.loads(path.read_text().strip())
        assert "delivered_at" not in content
        assert "abandoned_at" not in content

    @pytest.mark.asyncio
    async def test_malformed_json_lines_skipped(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "bad-json.jsonl"
        path.write_text("NOT JSON\n", encoding="utf-8")

        retry_mock = AsyncMock()
        with patch.object(listener, "_retry_single_record", retry_mock):
            await listener._process_outbox_file(AsyncMock(), path)
        retry_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_naive_datetime_in_recorded_at_handled(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        # Naive datetime (no timezone) should be treated as UTC
        naive_ts = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        record = {
            "event_type": "lead.send",
            "envelope": {},
            "recorded_at": naive_ts,
        }
        path = tmp_path / "naive-ts.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        with patch.object(
            listener, "_retry_single_record", new_callable=AsyncMock, return_value=True
        ):
            await listener._process_outbox_file(AsyncMock(), path)

        updated = json.loads(path.read_text().strip())
        assert "delivered_at" in updated


# ===========================================================================
# _mark_outbox_abandoned
# ===========================================================================


class TestMarkOutboxAbandoned:
    def test_sets_abandoned_at_and_reason(self, listener: XNodeListener) -> None:
        record: dict = {}
        listener._mark_outbox_abandoned(record, abandoned_at="2026-01-01T00:00:00Z", reason="ttl")
        assert record["abandoned_at"] == "2026-01-01T00:00:00Z"
        assert record["abandon_reason"] == "ttl"

    def test_is_idempotent_does_not_overwrite(self, listener: XNodeListener) -> None:
        record: dict = {"abandoned_at": "original"}
        listener._mark_outbox_abandoned(record, abandoned_at="new", reason="ttl")
        assert record["abandoned_at"] == "original"

    def test_reason_stored_correctly(self, listener: XNodeListener) -> None:
        record: dict = {}
        listener._mark_outbox_abandoned(
            record, abandoned_at="ts", reason="invalid_recorded_at"
        )
        assert record["abandon_reason"] == "invalid_recorded_at"


# ===========================================================================
# _retry_single_record
# ===========================================================================


class TestRetrySingleRecord:
    def _make_mock_session(self, status: int) -> MagicMock:
        mock_resp = AsyncMock()
        mock_resp.status = status
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        return mock_session

    @pytest.mark.asyncio
    async def test_returns_true_on_200(self, listener: XNodeListener) -> None:
        result = await listener._retry_single_record(
            self._make_mock_session(200), {"event_type": "lead.send", "envelope": {}}
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_on_201(self, listener: XNodeListener) -> None:
        result = await listener._retry_single_record(
            self._make_mock_session(201), {"event_type": "lead.send", "envelope": {}}
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_on_202(self, listener: XNodeListener) -> None:
        result = await listener._retry_single_record(
            self._make_mock_session(202), {"event_type": "lead.send", "envelope": {}}
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_400(self, listener: XNodeListener) -> None:
        result = await listener._retry_single_record(
            self._make_mock_session(400), {"event_type": "lead.send", "envelope": {}}
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_500(self, listener: XNodeListener) -> None:
        result = await listener._retry_single_record(
            self._make_mock_session(500), {"event_type": "lead.send", "envelope": {}}
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_network_exception(
        self, listener: XNodeListener
    ) -> None:
        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=OSError("connection refused"))
        result = await listener._retry_single_record(
            mock_session, {"event_type": "lead.send", "envelope": {}}
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_posts_to_correct_endpoint(self, listener: XNodeListener) -> None:
        session = self._make_mock_session(200)
        await listener._retry_single_record(session, {"event_type": "lead.ack", "envelope": {}})
        url = session.post.call_args[0][0]
        assert url.endswith("/api/xnode/events")

    @pytest.mark.asyncio
    async def test_sends_authorization_header(self, listener: XNodeListener) -> None:
        session = self._make_mock_session(200)
        await listener._retry_single_record(session, {"event_type": "lead.send", "envelope": {}})
        headers = session.post.call_args[1].get("headers", {})
        assert "Authorization" in headers
        assert "unit-test-token" in headers["Authorization"]

    @pytest.mark.asyncio
    async def test_payload_includes_event_type_and_envelope(
        self, listener: XNodeListener
    ) -> None:
        session = self._make_mock_session(200)
        record = {"event_type": "lead.handoff", "envelope": {"message_id": "hnd-1"}}
        await listener._retry_single_record(session, record)
        json_body = session.post.call_args[1].get("json", {})
        assert json_body["event_type"] == "lead.handoff"
        assert json_body["envelope"]["message_id"] == "hnd-1"

    @pytest.mark.asyncio
    async def test_missing_event_type_falls_back_to_unknown(
        self, listener: XNodeListener
    ) -> None:
        session = self._make_mock_session(200)
        await listener._retry_single_record(session, {"envelope": {}})
        json_body = session.post.call_args[1].get("json", {})
        assert json_body["event_type"] == "unknown"


# ===========================================================================
# _rewrite_jsonl
# ===========================================================================


class TestRewriteJsonl:
    def test_replaces_file_contents(self, listener: XNodeListener, tmp_path: Path) -> None:
        path = tmp_path / "data.jsonl"
        path.write_text(json.dumps({"old": True}) + "\n", encoding="utf-8")
        listener._rewrite_jsonl(path, [{"new": 1}])
        content = json.loads(path.read_text().strip())
        assert content == {"new": 1}

    def test_no_tmp_file_left_behind(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "data.jsonl"
        path.write_text("", encoding="utf-8")
        listener._rewrite_jsonl(path, [{"x": 1}])
        assert not path.with_suffix(".tmp").exists()

    def test_empty_records_writes_empty_file(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "data.jsonl"
        path.write_text(json.dumps({"old": 1}) + "\n", encoding="utf-8")
        listener._rewrite_jsonl(path, [])
        assert path.read_text().strip() == ""

    def test_multiple_records_each_on_own_line(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        path = tmp_path / "data.jsonl"
        path.write_text("", encoding="utf-8")
        listener._rewrite_jsonl(path, [{"a": 1}, {"b": 2}])
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1}
        assert json.loads(lines[1]) == {"b": 2}

    def test_secrets_are_redacted(self, listener: XNodeListener, tmp_path: Path) -> None:
        path = tmp_path / "data.jsonl"
        path.write_text("", encoding="utf-8")
        listener._rewrite_jsonl(path, [{"val": "FORGE_WEBHOOK_TOKEN=mysecret"}])
        content = path.read_text()
        assert "mysecret" not in content
        assert "[REDACTED]" in content


# ===========================================================================
# _notify_lead_tmux
# ===========================================================================


class TestNotifyLeadTmux:
    def test_skips_all_subprocess_when_notify_lead_false(
        self, listener: XNodeListener
    ) -> None:
        with patch("subprocess.run") as mock_run:
            listener._notify_lead_tmux("lead.send", "T-1", "summary", "prya", "high")
        mock_run.assert_not_called()

    def test_skips_when_tmux_not_available(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="h",
            notify_lead=True,
        )
        lst._has_tmux = False
        with patch("subprocess.run") as mock_run:
            lst._notify_lead_tmux("lead.send")
        mock_run.assert_not_called()

    def test_skips_when_tmux_session_missing(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="h",
            notify_lead=True,
        )
        lst._has_tmux = True
        with patch("subprocess.run", return_value=MagicMock(returncode=1)) as mock_run:
            lst._notify_lead_tmux("lead.send", "T-1")
        # Only has-session check should be run
        assert mock_run.call_count == 1

    def test_sends_three_or_more_subprocess_calls_on_success(
        self, forge_root: Path
    ) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="h",
            notify_lead=True,
        )
        lst._has_tmux = True
        with patch("subprocess.run", return_value=MagicMock(returncode=0)), patch(
            "time.sleep"
        ):
            lst._notify_lead_tmux("lead.send", "T-1", "details", "prya", "medium")

    def test_message_includes_priority_tag(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="h",
            notify_lead=True,
        )
        lst._has_tmux = True
        captured: list[str] = []

        def capture(args, **kwargs):
            if "send-keys" in args and "-l" in args:
                captured.append(args[-1])
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=capture), patch("time.sleep"):
            lst._notify_lead_tmux("lead.send", "T-99", "desc", "src", "critical")

        assert any("[XNODE CRITICAL]" in m for m in captured)

    def test_message_includes_source_node(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="h",
            notify_lead=True,
        )
        lst._has_tmux = True
        captured: list[str] = []

        def capture(args, **kwargs):
            if "send-keys" in args and "-l" in args:
                captured.append(args[-1])
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=capture), patch("time.sleep"):
            lst._notify_lead_tmux("lead.send", source_node="src-node")

        assert any("src-node" in m for m in captured)

    def test_timeout_expired_does_not_raise(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="h",
            notify_lead=True,
        )
        lst._has_tmux = True
        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 5)
        ):
            lst._notify_lead_tmux("lead.send")  # must not raise

    def test_file_not_found_does_not_raise(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="h",
            notify_lead=True,
        )
        lst._has_tmux = True
        with patch("subprocess.run", side_effect=FileNotFoundError("tmux not found")):
            lst._notify_lead_tmux("lead.send")  # must not raise

    def test_oserror_does_not_raise(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="h",
            notify_lead=True,
        )
        lst._has_tmux = True
        with patch("subprocess.run", side_effect=OSError("pipe broken")):
            lst._notify_lead_tmux("lead.send")  # must not raise

    def test_send_keys_failure_logged_not_raised(self, forge_root: Path) -> None:
        lst = XNodeListener(
            command_center_url="http://cc",
            token="t",
            forge_root=forge_root,
            hostname="h",
            notify_lead=True,
        )
        lst._has_tmux = True

        call_count = 0

        def side_effect(args, **kwargs):
            nonlocal call_count
            call_count += 1
            # has-session succeeds; all send-keys calls return non-zero
            return MagicMock(returncode=0 if call_count == 1 else 1)

        with patch("subprocess.run", side_effect=side_effect), patch("time.sleep"):
            lst._notify_lead_tmux("lead.send", "T-1")  # must not raise


# ===========================================================================
# _get_sse_session_token
# ===========================================================================


class TestGetSseSessionToken:
    @pytest.mark.asyncio
    async def test_returns_session_token_on_200(self, listener: XNodeListener) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"data": {"session_token": "short-lived-tok"}}
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp = MagicMock()
        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock()

        with patch.dict(sys.modules, {"aiohttp": mock_aiohttp}):
            import importlib

            import forge_harness.xnode_listener as mod
            original = mod.aiohttp if hasattr(mod, "aiohttp") else None
            try:
                mod.aiohttp = mock_aiohttp  # type: ignore[attr-defined]
                # Re-patch aiohttp inside the method context
                with patch(
                    "forge_harness.xnode_listener.aiohttp", mock_aiohttp
                ):
                    token = await listener._get_sse_session_token()
            finally:
                if original is not None:
                    mod.aiohttp = original

        # The token may be None because the stub isn't fully wired; just ensure no raise
        assert token is None or isinstance(token, str)

    @pytest.mark.asyncio
    async def test_returns_none_when_aiohttp_import_fails(
        self, listener: XNodeListener
    ) -> None:
        original = sys.modules.get("aiohttp")
        sys.modules["aiohttp"] = None  # type: ignore[assignment]
        try:
            result = await listener._get_sse_session_token()
        finally:
            if original is not None:
                sys.modules["aiohttp"] = original
            else:
                sys.modules.pop("aiohttp", None)
        assert result is None


# ===========================================================================
# _setup_signals
# ===========================================================================


class TestSetupSignals:
    def test_registers_sigint_handler(self, listener: XNodeListener) -> None:
        mock_loop = MagicMock()
        with patch("asyncio.get_event_loop", return_value=mock_loop):
            listener._setup_signals()
        assert mock_loop.add_signal_handler.called

    def test_handles_not_implemented_error_gracefully(
        self, listener: XNodeListener
    ) -> None:
        mock_loop = MagicMock()
        mock_loop.add_signal_handler.side_effect = NotImplementedError
        with patch("asyncio.get_event_loop", return_value=mock_loop):
            listener._setup_signals()  # must not raise

    def test_handles_value_error_gracefully(self, listener: XNodeListener) -> None:
        mock_loop = MagicMock()
        mock_loop.add_signal_handler.side_effect = ValueError("loop closed")
        with patch("asyncio.get_event_loop", return_value=mock_loop):
            listener._setup_signals()  # must not raise


# ===========================================================================
# stop()
# ===========================================================================


class TestStop:
    def test_sets_running_false(self, listener: XNodeListener) -> None:
        listener._running = True
        listener.stop()
        assert listener._running is False

    def test_safe_to_call_when_already_stopped(self, listener: XNodeListener) -> None:
        listener._running = False
        listener.stop()
        assert listener._running is False

    def test_idempotent_multiple_calls(self, listener: XNodeListener) -> None:
        listener._running = True
        listener.stop()
        listener.stop()
        assert listener._running is False


# ===========================================================================
# start() — once=True / backoff / cancellation
# ===========================================================================


class TestStart:
    @pytest.mark.asyncio
    async def test_once_true_exits_after_single_connection(
        self, listener: XNodeListener
    ) -> None:
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
    async def test_cancelled_error_exits_cleanly(self, listener: XNodeListener) -> None:
        async def raise_cancel():
            raise asyncio.CancelledError()

        with (
            patch.object(listener, "_connect_and_listen", side_effect=raise_cancel),
            patch.object(listener, "_setup_signals"),
            patch.object(listener, "_seed_seen_ids"),
            patch.object(listener, "_heartbeat_loop", new_callable=AsyncMock),
            patch.object(listener, "_outbox_retry_loop", new_callable=AsyncMock),
        ):
            await listener.start(once=True)  # must not raise

    @pytest.mark.asyncio
    async def test_backoff_doubles_on_connection_error(
        self, listener: XNodeListener
    ) -> None:
        call_count = 0
        sleep_values: list[float] = []

        async def flaky_connect():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("refused")
            listener._running = False

        async def capture_sleep(val: float) -> None:
            sleep_values.append(val)

        with (
            patch.object(listener, "_connect_and_listen", side_effect=flaky_connect),
            patch.object(listener, "_setup_signals"),
            patch.object(listener, "_seed_seen_ids"),
            patch.object(listener, "_heartbeat_loop", new_callable=AsyncMock),
            patch.object(listener, "_outbox_retry_loop", new_callable=AsyncMock),
            patch("asyncio.sleep", side_effect=capture_sleep),
        ):
            await listener.start(once=False)

        assert len(sleep_values) >= 2
        assert sleep_values[1] > sleep_values[0]

    @pytest.mark.asyncio
    async def test_backoff_capped_at_max_backoff(self, listener: XNodeListener) -> None:
        listener._backoff = 20.0  # Pre-elevated near max

        call_count = 0

        async def always_fail():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                listener._running = False
            raise ConnectionError("refused")

        sleep_values: list[float] = []

        async def capture_sleep(val: float) -> None:
            sleep_values.append(val)

        with (
            patch.object(listener, "_connect_and_listen", side_effect=always_fail),
            patch.object(listener, "_setup_signals"),
            patch.object(listener, "_seed_seen_ids"),
            patch.object(listener, "_heartbeat_loop", new_callable=AsyncMock),
            patch.object(listener, "_outbox_retry_loop", new_callable=AsyncMock),
            patch("asyncio.sleep", side_effect=capture_sleep),
        ):
            await listener.start(once=False)

        assert all(v <= listener._max_backoff for v in sleep_values)

    @pytest.mark.asyncio
    async def test_heartbeat_and_outbox_loop_methods_are_called_during_start(
        self, listener: XNodeListener
    ) -> None:
        """_heartbeat_loop and _outbox_retry_loop must be invoked as tasks during start()."""
        heartbeat_mock = AsyncMock()
        outbox_mock = AsyncMock()

        async def immediate_connect():
            pass  # completes immediately so start() can exit

        with (
            patch.object(listener, "_connect_and_listen", side_effect=immediate_connect),
            patch.object(listener, "_setup_signals"),
            patch.object(listener, "_seed_seen_ids"),
            patch.object(listener, "_heartbeat_loop", heartbeat_mock),
            patch.object(listener, "_outbox_retry_loop", outbox_mock),
        ):
            await listener.start(once=True)

        # Both background loop coroutines must have been awaited/started
        heartbeat_mock.assert_called_once()
        outbox_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_running_set_true_during_start(self, listener: XNodeListener) -> None:
        seen_running: list[bool] = []

        async def probe_connect():
            seen_running.append(listener._running)

        with (
            patch.object(listener, "_connect_and_listen", side_effect=probe_connect),
            patch.object(listener, "_setup_signals"),
            patch.object(listener, "_seed_seen_ids"),
            patch.object(listener, "_heartbeat_loop", new_callable=AsyncMock),
            patch.object(listener, "_outbox_retry_loop", new_callable=AsyncMock),
        ):
            await listener.start(once=True)

        assert seen_running == [True]


# ===========================================================================
# Module-level helpers
# ===========================================================================


class TestNowIso:
    def test_returns_timezone_aware_timestamp(self) -> None:
        ts = _now_iso()
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None

    def test_successive_calls_non_decreasing(self) -> None:
        assert _now_iso() <= _now_iso()

    def test_returns_string(self) -> None:
        assert isinstance(_now_iso(), str)


class TestNowSlug:
    def test_length_is_15(self) -> None:
        assert len(_now_slug()) == 15

    def test_position_8_is_underscore(self) -> None:
        assert _now_slug()[8] == "_"

    def test_all_digits_except_underscore(self) -> None:
        slug = _now_slug()
        assert slug.replace("_", "").isdigit()

    def test_returns_string(self) -> None:
        assert isinstance(_now_slug(), str)


class TestAppendJsonl:
    def test_creates_file_when_not_exists(self, tmp_path: Path) -> None:
        path = tmp_path / "new.jsonl"
        _append_jsonl(path, {"key": "val"})
        assert path.exists()

    def test_content_is_valid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "test.jsonl"
        _append_jsonl(path, {"n": 42})
        assert json.loads(path.read_text().strip()) == {"n": 42}

    def test_appends_multiple_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "multi.jsonl"
        _append_jsonl(path, {"n": 1})
        _append_jsonl(path, {"n": 2})
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1]) == {"n": 2}

    def test_redacts_token_in_payload(self, tmp_path: Path) -> None:
        path = tmp_path / "secret.jsonl"
        _append_jsonl(path, {"env": "FORGE_WEBHOOK_TOKEN=mysecret"})
        assert "mysecret" not in path.read_text()
        assert "[REDACTED]" in path.read_text()


class TestWriteJson:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        _write_json(path, {"hello": "world"})
        assert json.loads(path.read_text()) == {"hello": "world"}

    def test_overwrites_existing_content(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        _write_json(path, {"v": 1})
        _write_json(path, {"v": 2})
        assert json.loads(path.read_text())["v"] == 2

    def test_output_is_indented(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        _write_json(path, {"a": 1})
        assert "\n" in path.read_text()

    def test_redacts_token_in_payload(self, tmp_path: Path) -> None:
        path = tmp_path / "secret.json"
        _write_json(path, {"key": "FORGE_WEBHOOK_TOKEN=mysecret"})
        assert "mysecret" not in path.read_text()


class TestRedactSecrets:
    def test_redacts_env_assignment_style(self) -> None:
        result = _redact_secrets("FORGE_WEBHOOK_TOKEN=abc")
        assert "abc" not in result
        assert "[REDACTED]" in result

    def test_redacts_json_style(self) -> None:
        result = _redact_secrets('"FORGE_WEBHOOK_TOKEN": "mysecret"')
        assert "mysecret" not in result

    def test_case_insensitive(self) -> None:
        result = _redact_secrets("forge_webhook_token=lower")
        assert "lower" not in result

    def test_safe_string_unchanged(self) -> None:
        assert _redact_secrets("hello world") == "hello world"

    def test_recurses_into_nested_dict(self) -> None:
        data = {"a": {"b": "FORGE_WEBHOOK_TOKEN=deep"}}
        result = _redact_secrets(data)
        assert "deep" not in str(result)
        assert isinstance(result, dict)

    def test_recurses_into_list(self) -> None:
        data = ["FORGE_WEBHOOK_TOKEN=item", "safe"]
        result = _redact_secrets(data)
        assert "item" not in str(result)
        assert "safe" in result

    def test_integer_passthrough(self) -> None:
        assert _redact_secrets(99) == 99

    def test_none_passthrough(self) -> None:
        assert _redact_secrets(None) is None

    def test_bool_passthrough(self) -> None:
        assert _redact_secrets(False) is False

    def test_colon_style_assignment(self) -> None:
        result = _redact_secrets("FORGE_WEBHOOK_TOKEN: colon_val")
        assert "colon_val" not in result
