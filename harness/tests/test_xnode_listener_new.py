"""Additional tests for XNodeListener - _seed_seen_ids and helper methods.

Tests cover:
- _seed_seen_ids: Pre-populating seen message IDs from existing inbox files
- _ensure_dirs: Directory creation and structure
- _resolve_target_node: Target node resolution from envelopes
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.xnode_listener import XNodeListener, _append_jsonl, _write_json


@pytest.fixture
def listener(tmp_path: Path) -> XNodeListener:
    """Create a listener rooted at a temp directory with hostname 'prya'."""
    return XNodeListener(
        command_center_url="https://example.com",
        token="test-token",
        forge_root=tmp_path,
        hostname="prya",
    )


# ---------------------------------------------------------------------------
# TestSeedSeenIds
# ---------------------------------------------------------------------------


class TestSeedSeenIds:
    """Tests for _seed_seen_ids method."""

    def test_seed_seen_ids_empty_inbox(self, listener: XNodeListener, tmp_path: Path) -> None:
        """_seed_seen_ids with empty inbox should result in empty set."""
        # Inbox is empty by default (just created by fixture)
        listener._seed_seen_ids()

        assert len(listener._seen_message_ids) == 0

    def test_seed_seen_ids_single_message(self, listener: XNodeListener, tmp_path: Path) -> None:
        """Should extract message_id from a single inbox record."""
        inbox_file = tmp_path / ".forge" / "xnode" / "lead-inbox" / "prya.jsonl"
        record = {
            "envelope": {"message_id": "msg_001", "type": "lead.send"},
            "timestamp": "2026-02-20T00:00:00Z"
        }
        _append_jsonl(inbox_file, record)

        listener._seed_seen_ids()

        assert "msg_001" in listener._seen_message_ids
        assert len(listener._seen_message_ids) == 1

    def test_seed_seen_ids_multiple_messages(self, listener: XNodeListener, tmp_path: Path) -> None:
        """Should extract multiple message_ids from inbox."""
        inbox_file = tmp_path / ".forge" / "xnode" / "lead-inbox" / "prya.jsonl"

        for i in range(5):
            record = {
                "envelope": {"message_id": f"msg_{i:03d}", "type": "lead.send"},
                "timestamp": "2026-02-20T00:00:00Z"
            }
            _append_jsonl(inbox_file, record)

        listener._seed_seen_ids()

        assert len(listener._seen_message_ids) == 5
        for i in range(5):
            assert f"msg_{i:03d}" in listener._seen_message_ids

    def test_seed_seen_ids_multiple_inbox_files(self, listener: XNodeListener, tmp_path: Path) -> None:
        """Should process all .jsonl files in inbox directory."""
        inbox_dir = tmp_path / ".forge" / "xnode" / "lead-inbox"

        # Create multiple inbox files
        for filename in ["prya.jsonl", "other.jsonl", "backup.jsonl"]:
            inbox_file = inbox_dir / filename
            record = {
                "envelope": {"message_id": f"from_{filename}", "type": "lead.send"},
                "timestamp": "2026-02-20T00:00:00Z"
            }
            _append_jsonl(inbox_file, record)

        listener._seed_seen_ids()

        assert "from_prya.jsonl" in listener._seen_message_ids
        assert "from_other.jsonl" in listener._seen_message_ids
        assert "from_backup.jsonl" in listener._seen_message_ids
        assert len(listener._seen_message_ids) == 3

    def test_seed_seen_ids_skips_missing_envelope(self, listener: XNodeListener, tmp_path: Path) -> None:
        """Should skip records without envelope field."""
        inbox_file = tmp_path / ".forge" / "xnode" / "lead-inbox" / "prya.jsonl"

        # Record without envelope
        record_no_envelope = {"timestamp": "2026-02-20T00:00:00Z"}
        _append_jsonl(inbox_file, record_no_envelope)

        # Record with envelope but no message_id
        record_no_id = {"envelope": {"type": "lead.send"}}
        _append_jsonl(inbox_file, record_no_id)

        # Valid record
        record_valid = {"envelope": {"message_id": "valid_msg", "type": "lead.send"}}
        _append_jsonl(inbox_file, record_valid)

        listener._seed_seen_ids()

        assert len(listener._seen_message_ids) == 1
        assert "valid_msg" in listener._seen_message_ids

    def test_seed_seen_ids_skips_invalid_json(self, listener: XNodeListener, tmp_path: Path) -> None:
        """Should skip lines that are not valid JSON."""
        inbox_file = tmp_path / ".forge" / "xnode" / "lead-inbox" / "prya.jsonl"
        inbox_file.write_text("not valid json\n")

        # Should not raise an exception
        listener._seed_seen_ids()

        assert len(listener._seen_message_ids) == 0

    def test_seed_seen_ids_skips_empty_lines(self, listener: XNodeListener, tmp_path: Path) -> None:
        """Should skip empty lines in inbox files."""
        inbox_file = tmp_path / ".forge" / "xnode" / "lead-inbox" / "prya.jsonl"

        # Write file with empty lines
        with open(inbox_file, "w") as f:
            f.write('\n')
            f.write('{"envelope": {"message_id": "msg_after_empty"}}\n')
            f.write('\n')
            f.write('   \n')

        listener._seed_seen_ids()

        assert "msg_after_empty" in listener._seen_message_ids
        assert len(listener._seen_message_ids) == 1

    def test_seed_seen_ids_handles_file_read_error(self, listener: XNodeListener, tmp_path: Path) -> None:
        """Should handle OSError when reading inbox files gracefully."""
        inbox_dir = tmp_path / ".forge" / "xnode" / "lead-inbox"
        inbox_file = inbox_dir / "corrupt.jsonl"
        inbox_file.write_text('{"envelope": {"message_id": "test"}}')

        # Make file unreadable
        inbox_file.chmod(0o000)

        try:
            # Should not raise an exception
            listener._seed_seen_ids()
        finally:
            # Restore permissions for cleanup
            inbox_file.chmod(0o644)

        # No crash means success
        assert True

    def test_seed_seen_ids_deduplicates_messages(self, listener: XNodeListener, tmp_path: Path) -> None:
        """Should deduplicate message IDs across multiple files."""
        inbox_dir = tmp_path / ".forge" / "xnode" / "lead-inbox"

        # Same message ID in multiple files
        for filename in ["file1.jsonl", "file2.jsonl"]:
            inbox_file = inbox_dir / filename
            record = {
                "envelope": {"message_id": "duplicate_msg", "type": "lead.send"}
            }
            _append_jsonl(inbox_file, record)

        listener._seed_seen_ids()

        # Should only have one entry
        assert len(listener._seen_message_ids) == 1
        assert "duplicate_msg" in listener._seen_message_ids


# ---------------------------------------------------------------------------
# TestEnsureDirs
# ---------------------------------------------------------------------------


class TestEnsureDirs:
    """Tests for _ensure_dirs method."""

    def test_ensure_dirs_creates_all_directories(self, tmp_path: Path) -> None:
        """Should create all required xnode directories."""
        listener = XNodeListener(
            command_center_url="https://example.com",
            token="test-token",
            forge_root=tmp_path,
            hostname="test",
        )

        dirs = listener._dirs

        assert dirs["root"].exists()
        assert dirs["lead_inbox"].exists()
        assert dirs["handoffs"].exists()
        assert dirs["acks"].exists()
        assert dirs["exceptions"].exists()
        assert dirs["realtime_outbox"].exists()

    def test_ensure_dirs_returns_correct_paths(self, tmp_path: Path) -> None:
        """Should return correct paths for all directories."""
        listener = XNodeListener(
            command_center_url="https://example.com",
            token="test-token",
            forge_root=tmp_path,
            hostname="test",
        )

        dirs = listener._dirs
        expected_root = tmp_path / ".forge" / "xnode"

        assert dirs["root"] == expected_root
        assert dirs["lead_inbox"] == expected_root / "lead-inbox"
        assert dirs["handoffs"] == expected_root / "handoffs"
        assert dirs["acks"] == expected_root / "acks"
        assert dirs["exceptions"] == expected_root / "exceptions"
        assert dirs["realtime_outbox"] == expected_root / "realtime-outbox"

    def test_ensure_dirs_idempotent(self, tmp_path: Path) -> None:
        """Calling _ensure_dirs multiple times should not fail."""
        listener = XNodeListener(
            command_center_url="https://example.com",
            token="test-token",
            forge_root=tmp_path,
            hostname="test",
        )

        # First call in __init__ already created dirs
        # Second call should be safe
        dirs2 = listener._ensure_dirs()

        assert dirs2["root"].exists()
        assert len(list(dirs2["root"].iterdir())) > 0


# ---------------------------------------------------------------------------
# TestResolveTargetNode
# ---------------------------------------------------------------------------


class TestResolveTargetNode:
    """Tests for _resolve_target_node method."""

    def test_resolve_target_node_from_envelope_target(self, listener: XNodeListener) -> None:
        """Should extract node from envelope.target.node."""
        envelope = {"target": {"node": "sati"}}

        result = listener._resolve_target_node(envelope)

        assert result == "sati"

    def test_resolve_target_node_from_flat_target_node(self, listener: XNodeListener) -> None:
        """Should extract node from envelope.target_node (flat)."""
        envelope = {"target_node": "kira"}

        result = listener._resolve_target_node(envelope)

        assert result == "kira"

    def test_resolve_target_node_prefers_nested_over_flat(self, listener: XNodeListener) -> None:
        """Should prefer nested target.node over flat target_node."""
        envelope = {
            "target": {"node": "nested_node"},
            "target_node": "flat_node"
        }

        result = listener._resolve_target_node(envelope)

        assert result == "nested_node"

    def test_resolve_target_node_defaults_to_local_hostname(self, listener: XNodeListener) -> None:
        """Should default to listener's hostname when no target specified."""
        envelope = {"message_id": "broadcast_msg"}

        result = listener._resolve_target_node(envelope)

        assert result == "prya"

    def test_resolve_target_node_empty_envelope(self, listener: XNodeListener) -> None:
        """Should handle empty envelope gracefully."""
        envelope = {}

        result = listener._resolve_target_node(envelope)

        assert result == "prya"  # Defaults to listener hostname


# ---------------------------------------------------------------------------
# TestInitialization
# ---------------------------------------------------------------------------


class TestInitialization:
    """Tests for XNodeListener initialization."""

    def test_init_creates_seen_ids_set(self, tmp_path: Path) -> None:
        """Should initialize _seen_message_ids as empty dict (LRU-bounded)."""
        listener = XNodeListener(
            command_center_url="https://example.com",
            token="test-token",
            forge_root=tmp_path,
            hostname="test",
        )

        assert isinstance(listener._seen_message_ids, dict)
        assert len(listener._seen_message_ids) == 0

    def test_init_normalizes_hostname(self, tmp_path: Path) -> None:
        """Should normalize hostname to lowercase."""
        listener = XNodeListener(
            command_center_url="https://example.com",
            token="test-token",
            forge_root=tmp_path,
            hostname="HOSTNAME",
        )

        assert listener.hostname == "hostname"

    def test_init_strips_whitespace_from_hostname(self, tmp_path: Path) -> None:
        """Should strip whitespace from hostname."""
        listener = XNodeListener(
            command_center_url="https://example.com",
            token="test-token",
            forge_root=tmp_path,
            hostname="  test-host  ",
        )

        assert listener.hostname == "test-host"

    def test_init_sets_default_lead_window(self, tmp_path: Path) -> None:
        """Should set default lead_window based on hostname."""
        listener = XNodeListener(
            command_center_url="https://example.com",
            token="test-token",
            forge_root=tmp_path,
            hostname="nova",
        )

        assert listener.lead_window == "forge:nova"

    def test_init_allows_custom_lead_window(self, tmp_path: Path) -> None:
        """Should accept custom lead_window."""
        listener = XNodeListener(
            command_center_url="https://example.com",
            token="test-token",
            forge_root=tmp_path,
            hostname="nova",
            lead_window="custom:window",
        )

        assert listener.lead_window == "custom:window"

    def test_init_strips_trailing_slash_from_url(self, tmp_path: Path) -> None:
        """Should strip trailing slash from command_center_url."""
        listener = XNodeListener(
            command_center_url="https://example.com/",
            token="test-token",
            forge_root=tmp_path,
            hostname="test",
        )

        assert listener.url == "https://example.com"

    def test_init_detects_tmux_availability(self, tmp_path: Path) -> None:
        """Should detect if tmux is available."""
        listener = XNodeListener(
            command_center_url="https://example.com",
            token="test-token",
            forge_root=tmp_path,
            hostname="test",
        )

        # Just verify the attribute exists
        assert hasattr(listener, "_has_tmux")
        assert isinstance(listener._has_tmux, bool)

    def test_init_sets_channels(self, tmp_path: Path) -> None:
        """Should store channels if provided."""
        listener = XNodeListener(
            command_center_url="https://example.com",
            token="test-token",
            forge_root=tmp_path,
            hostname="test",
            channels=["lead.test.inbox", "lead.other.inbox"],
        )

        assert listener.channels == ["lead.test.inbox", "lead.other.inbox"]


# ---------------------------------------------------------------------------
# TestSeenMessageIdsUsage
# ---------------------------------------------------------------------------


class TestSeenMessageIdsUsage:
    """Tests for how _seen_message_ids is used to prevent replays."""

    @pytest.mark.asyncio
    async def test_duplicate_message_id_prevents_processing(
        self, listener: XNodeListener, tmp_path: Path
    ) -> None:
        """Message with already-seen ID should not be processed again."""
        # Pre-seed a message ID using the correct dict API
        listener._seen_message_ids["duplicate_msg_001"] = None

        # The dedup check in _handle_lead_send uses _seen_message_ids
        # so calling it with this ID should skip storage
        envelope = {
            "message_id": "duplicate_msg_001",
            "target": {"node": "prya"},
            "payload": {"task": "test"},
        }

        await listener._handle_lead_send(envelope)

        # No file should be written since message_id was already seen
        out_file = listener._dirs["lead_inbox"] / "prya.jsonl"
        assert not out_file.exists()

    def test_seed_seen_ids_populates_before_start(self, listener: XNodeListener, tmp_path: Path) -> None:
        """_seed_seen_ids should be called during start() to populate seen IDs."""
        inbox_file = tmp_path / ".forge" / "xnode" / "lead-inbox" / "prya.jsonl"
        record = {"envelope": {"message_id": "pre_existing_msg"}}
        _append_jsonl(inbox_file, record)

        # Mock _connect_and_listen to stop immediately
        async def mock_connect() -> None:
            listener._running = False

        listener._connect_and_listen = mock_connect  # type: ignore[method-assign]

        # Run start which should call _seed_seen_ids
        import asyncio
        asyncio.run(listener.start(once=True))

        # The seen IDs should be populated
        assert "pre_existing_msg" in listener._seen_message_ids
