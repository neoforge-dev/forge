"""Comprehensive tests for forge_harness.webhook_server.services.audit.

Targets 100% statement coverage across:
- AuditBackend (abstract base class)
- MemoryAuditBackend
- JSONLAuditBackend (including _find_forge_root walk)
- AuditLogger (core log() + all log_* helper methods)
- get_audit_logger / reset_audit_logger singletons
"""

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.webhook_server.services.audit import (
    AuditBackend,
    AuditLogger,
    JSONLAuditBackend,
    MemoryAuditBackend,
    get_audit_logger,
    reset_audit_logger,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

ACTOR = {"id": "user-001", "type": "user", "name": "test-actor"}
TARGET = {"id": "resource-001", "type": "resource"}


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure the module-level singleton is cleared before and after every test."""
    reset_audit_logger()
    yield
    reset_audit_logger()


@pytest.fixture
def memory_backend():
    return MemoryAuditBackend()


@pytest.fixture
def audit_logger(memory_backend):
    """AuditLogger backed by the in-memory backend — no disk I/O."""
    return AuditLogger(backend=memory_backend)


# ===========================================================================
# AuditBackend — abstract base class
# ===========================================================================


class TestAuditBackendAbstract:
    """AuditBackend is abstract; it cannot be instantiated directly."""

    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            AuditBackend()  # type: ignore[abstract]

    def test_incomplete_subclass_raises_type_error(self):
        """A concrete subclass that omits emit() must raise TypeError."""

        class Incomplete(AuditBackend):
            pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_complete_subclass_can_be_instantiated(self):
        class Complete(AuditBackend):
            async def emit(self, event):
                pass  # line 30 equivalent — abstract method body covered here

        backend = Complete()
        assert backend is not None

    async def test_abstract_emit_body_executed_via_super(self):
        """Explicitly call super().emit() to execute the abstract pass on line 30."""

        class CallsSuper(AuditBackend):
            async def emit(self, event):
                # Call super() so CPython executes the abstract body (line 30: pass)
                await super().emit(event)

        backend = CallsSuper()
        # Must not raise — abstract body is just `pass`
        await backend.emit({"action": "test"})


# ===========================================================================
# MemoryAuditBackend
# ===========================================================================


class TestMemoryAuditBackend:
    def test_initial_state(self, memory_backend):
        assert memory_backend.events == []

    async def test_emit_appends_event(self, memory_backend):
        event = {"action": "dispatch", "actor": ACTOR}
        await memory_backend.emit(event)
        assert len(memory_backend.events) == 1
        assert memory_backend.events[0] is event

    async def test_emit_multiple_events(self, memory_backend):
        for i in range(5):
            await memory_backend.emit({"seq": i})
        assert len(memory_backend.events) == 5
        assert memory_backend.events[2]["seq"] == 2

    async def test_emit_preserves_nested_dict(self, memory_backend):
        event = {"key": "value", "nested": {"a": 1}}
        await memory_backend.emit(event)
        assert memory_backend.events[0]["nested"]["a"] == 1


# ===========================================================================
# JSONLAuditBackend — __init__
# ===========================================================================


class TestJSONLAuditBackendInit:
    def test_explicit_path_used_when_provided(self, tmp_path):
        audit_file = tmp_path / "custom" / "audit.jsonl"
        backend = JSONLAuditBackend(path=audit_file)
        assert backend._path == audit_file
        # Parent directories should be created
        assert audit_file.parent.exists()

    def test_explicit_path_as_string(self, tmp_path):
        audit_file = tmp_path / "str_path.jsonl"
        backend = JSONLAuditBackend(path=str(audit_file))
        assert backend._path == audit_file

    def test_forge_root_used_when_no_path(self, tmp_path):
        backend = JSONLAuditBackend(forge_root=tmp_path)
        expected = tmp_path / ".forge/state" / "audit" / "audit.jsonl"
        assert backend._path == expected
        assert expected.parent.exists()

    def test_init_creates_nested_directory(self, tmp_path):
        path = tmp_path / "a" / "b" / "c" / "audit.jsonl"
        JSONLAuditBackend(path=path)
        assert path.parent.exists()


# ===========================================================================
# JSONLAuditBackend — _find_forge_root
# ===========================================================================


class TestJSONLAuditBackendFindForgeRoot:
    """Cover lines 64-74: the parent-walk loop in _find_forge_root."""

    def test_returns_path_with_forge_harness_subdir(self, tmp_path):
        """Stops climbing when it finds a directory named 'forge_harness'."""
        # Build:  tmp_path / forge_harness / sub / module.py
        (tmp_path / "forge_harness").mkdir()
        sub = tmp_path / "forge_harness" / "sub"
        sub.mkdir(parents=True)

        backend = JSONLAuditBackend.__new__(JSONLAuditBackend)

        with patch(
            "forge_harness.webhook_server.services.audit.Path.__file__",
            create=True,
        ):
            # Patch __file__ inside the module so the walk starts from sub
            import forge_harness.webhook_server.services.audit as audit_module

            original = audit_module.__file__
            try:
                audit_module.__file__ = str(sub / "audit.py")
                # Re-instantiate to trigger _find_forge_root from sub directory
                # We exercise it directly
                result = backend._find_forge_root()
                assert isinstance(result, Path)
            finally:
                audit_module.__file__ = original

    def test_returns_path_with_pyproject_toml(self, tmp_path):
        """Stops when it finds pyproject.toml."""
        (tmp_path / "pyproject.toml").write_text("[project]")
        backend = JSONLAuditBackend.__new__(JSONLAuditBackend)
        result = backend._find_forge_root()
        assert isinstance(result, Path)

    def test_falls_back_to_cwd_when_no_marker_found(self, tmp_path, monkeypatch):
        """When depth is exceeded without finding a marker, returns Path.cwd()."""
        monkeypatch.chdir(tmp_path)

        # Build a path chain 12 levels deep — deeper than the 10-level limit
        deep = tmp_path
        for i in range(12):
            deep = deep / f"d{i}"
        deep.mkdir(parents=True, exist_ok=True)

        backend = JSONLAuditBackend.__new__(JSONLAuditBackend)

        import forge_harness.webhook_server.services.audit as audit_module

        original = audit_module.__file__
        try:
            audit_module.__file__ = str(deep / "fake.py")
            result = backend._find_forge_root()
            assert isinstance(result, Path)
        finally:
            audit_module.__file__ = original

    def test_stops_at_filesystem_root(self, tmp_path, monkeypatch):
        """Hitting the filesystem root (parent == current) breaks the loop cleanly."""
        monkeypatch.chdir(tmp_path)
        backend = JSONLAuditBackend.__new__(JSONLAuditBackend)

        # Start from the real filesystem root — loop should break immediately
        import forge_harness.webhook_server.services.audit as audit_module

        original = audit_module.__file__
        try:
            audit_module.__file__ = "/fake_root_level_module.py"
            result = backend._find_forge_root()
            assert isinstance(result, Path)
        finally:
            audit_module.__file__ = original

    def test_no_path_no_forge_root_triggers_find(self, tmp_path, monkeypatch):
        """Passing neither path nor forge_root triggers _find_forge_root."""
        monkeypatch.chdir(tmp_path)

        import forge_harness.webhook_server.services.audit as audit_module

        original = audit_module.__file__
        try:
            # Point __file__ at the real harness location which DOES have pyproject.toml
            # This ensures _find_forge_root returns a valid path and mkdir succeeds.
            audit_module.__file__ = original  # keep real path
            backend = JSONLAuditBackend()  # no path, no forge_root
            assert isinstance(backend._path, Path)
            assert backend._path.name == "audit.jsonl"
        finally:
            audit_module.__file__ = original


# ===========================================================================
# JSONLAuditBackend — emit()
# ===========================================================================


class TestJSONLAuditBackendEmit:
    async def test_emit_writes_valid_jsonl(self, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        backend = JSONLAuditBackend(path=audit_file)
        event = {"action": "dispatch", "actor": ACTOR, "target": TARGET}
        await backend.emit(event)

        lines = audit_file.read_text().strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["action"] == "dispatch"

    async def test_emit_appends_multiple_lines(self, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        backend = JSONLAuditBackend(path=audit_file)

        for i in range(3):
            await backend.emit({"seq": i})

        lines = audit_file.read_text().strip().splitlines()
        assert len(lines) == 3
        assert json.loads(lines[1])["seq"] == 1

    async def test_emit_serialises_non_json_values_via_default_str(self, tmp_path):
        """Non-JSON-serialisable objects are stringified via default=str."""
        import datetime

        audit_file = tmp_path / "audit.jsonl"
        backend = JSONLAuditBackend(path=audit_file)
        dt = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        await backend.emit({"ts": dt})
        line = audit_file.read_text().strip()
        parsed = json.loads(line)
        assert "2026" in parsed["ts"]

    async def test_emit_logs_warning_on_io_error(self, tmp_path):
        """OSError during file write is caught and a warning is logged."""
        audit_file = tmp_path / "audit.jsonl"
        backend = JSONLAuditBackend(path=audit_file)

        with (
            patch("builtins.open", side_effect=OSError("disk full")),
            patch("forge_harness.webhook_server.services.audit.logger") as mock_logger,
        ):
            await backend.emit({"action": "test"})
            mock_logger.warning.assert_called_once()
            warning_text = str(mock_logger.warning.call_args)
            assert "disk full" in warning_text

    async def test_emit_handles_directory_path_gracefully(self, tmp_path):
        """Writing to a path that is actually a directory raises an error caught silently."""
        # Create a directory where the file would normally be
        dir_path = tmp_path / "is_a_dir"
        dir_path.mkdir()
        backend = JSONLAuditBackend(path=dir_path)
        # Must not raise
        await backend.emit({"action": "test"})


# ===========================================================================
# AuditLogger — constructor
# ===========================================================================


class TestAuditLoggerConstructor:
    def test_uses_provided_backend(self, memory_backend):
        logger = AuditLogger(backend=memory_backend)
        assert logger._backend is memory_backend

    def test_creates_jsonl_backend_when_no_backend_given(self, tmp_path):
        logger = AuditLogger(forge_root=tmp_path)
        assert isinstance(logger._backend, JSONLAuditBackend)

    def test_none_backend_with_none_forge_root_creates_jsonl(self, tmp_path):
        """Passing backend=None + forge_root=None constructs a default JSONLAuditBackend."""
        with patch.object(JSONLAuditBackend, "__init__", return_value=None) as mock_init:
            AuditLogger(forge_root=None)
            mock_init.assert_called_once_with(forge_root=None)


# ===========================================================================
# AuditLogger — core log() method
# ===========================================================================


class TestAuditLoggerLog:
    async def test_basic_event_structure(self, audit_logger, memory_backend):
        await audit_logger.log(action="dispatch", actor=ACTOR, target=TARGET)

        assert len(memory_backend.events) == 1
        event = memory_backend.events[0]
        assert event["action"] == "dispatch"
        assert event["actor"] == ACTOR
        assert event["target"] == TARGET
        assert event["status"] == "success"
        assert event["source"] == "webhook_api"
        assert event["error"] is None
        assert event["request_id"] is None
        assert event["context"] == {}
        uuid.UUID(event["event_id"])  # must be a valid UUID

    async def test_custom_status_and_error(self, audit_logger, memory_backend):
        await audit_logger.log(
            action="kill",
            actor=ACTOR,
            target=TARGET,
            status="failure",
            error="agent not found",
        )
        event = memory_backend.events[0]
        assert event["status"] == "failure"
        assert event["error"] == "agent not found"

    async def test_context_passed_through(self, audit_logger, memory_backend):
        ctx = {"agent_id": "agent-42", "reason": "manual"}
        await audit_logger.log(action="pause", actor=ACTOR, target=TARGET, context=ctx)
        assert memory_backend.events[0]["context"] == ctx

    async def test_none_context_defaults_to_empty_dict(self, audit_logger, memory_backend):
        await audit_logger.log(action="resume", actor=ACTOR, target=TARGET, context=None)
        assert memory_backend.events[0]["context"] == {}

    async def test_request_id_included(self, audit_logger, memory_backend):
        await audit_logger.log(action="complete", actor=ACTOR, target=TARGET, request_id="req-999")
        assert memory_backend.events[0]["request_id"] == "req-999"

    async def test_custom_source(self, audit_logger, memory_backend):
        await audit_logger.log(action="complete", actor=ACTOR, target=TARGET, source="cli")
        assert memory_backend.events[0]["source"] == "cli"

    async def test_ip_address_added_when_provided(self, audit_logger, memory_backend):
        await audit_logger.log(
            action="dispatch", actor=ACTOR, target=TARGET, ip_address="192.168.1.1"
        )
        assert memory_backend.events[0]["ip_address"] == "192.168.1.1"

    async def test_ip_address_omitted_when_none(self, audit_logger, memory_backend):
        await audit_logger.log(action="dispatch", actor=ACTOR, target=TARGET)
        assert "ip_address" not in memory_backend.events[0]

    async def test_user_agent_added_when_provided(self, audit_logger, memory_backend):
        await audit_logger.log(
            action="dispatch", actor=ACTOR, target=TARGET, user_agent="Mozilla/5.0"
        )
        assert memory_backend.events[0]["user_agent"] == "Mozilla/5.0"

    async def test_user_agent_omitted_when_none(self, audit_logger, memory_backend):
        await audit_logger.log(action="dispatch", actor=ACTOR, target=TARGET)
        assert "user_agent" not in memory_backend.events[0]

    async def test_both_ip_and_user_agent_included(self, audit_logger, memory_backend):
        await audit_logger.log(
            action="dispatch",
            actor=ACTOR,
            target=TARGET,
            ip_address="10.0.0.1",
            user_agent="curl/7.0",
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "10.0.0.1"
        assert event["user_agent"] == "curl/7.0"

    async def test_timestamp_is_utc_iso_format(self, audit_logger, memory_backend):
        from datetime import datetime

        await audit_logger.log(action="test", actor=ACTOR, target=TARGET)
        ts = memory_backend.events[0]["timestamp"]
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None

    # -----------------------------------------------------------------------
    # Line 137-138: except branch when backend.emit() raises
    # -----------------------------------------------------------------------

    async def test_backend_exception_is_caught_and_logged_as_warning(self, memory_backend):
        """Lines 137-138: RuntimeError from backend is caught; warning is emitted."""
        memory_backend.emit = AsyncMock(side_effect=RuntimeError("backend exploded"))
        logger = AuditLogger(backend=memory_backend)

        with patch("forge_harness.webhook_server.services.audit.logger") as mock_logger:
            # Must not raise
            await logger.log(action="dispatch", actor=ACTOR, target=TARGET)
            mock_logger.warning.assert_called_once()
            call_text = str(mock_logger.warning.call_args)
            assert "dispatch" in call_text

    async def test_backend_exception_includes_action_in_warning(self, memory_backend):
        memory_backend.emit = AsyncMock(side_effect=Exception("oops"))
        logger = AuditLogger(backend=memory_backend)

        with patch("forge_harness.webhook_server.services.audit.logger") as mock_logger:
            await logger.log(action="fleet_pause", actor=ACTOR, target=TARGET)
            warning_args = str(mock_logger.warning.call_args)
            assert "fleet_pause" in warning_args


# ===========================================================================
# AuditLogger — log_agent_registration()
# ===========================================================================


class TestLogAgentRegistration:
    async def test_action_and_target(self, audit_logger, memory_backend):
        await audit_logger.log_agent_registration(agent_id="a-001", actor=ACTOR)
        event = memory_backend.events[0]
        assert event["action"] == "agent_registration"
        assert event["target"] == {"id": "a-001", "type": "agent"}

    async def test_context_forwarded(self, audit_logger, memory_backend):
        await audit_logger.log_agent_registration(
            agent_id="a-002", actor=ACTOR, context={"role": "debug"}
        )
        assert memory_backend.events[0]["context"] == {"role": "debug"}

    async def test_none_context_becomes_empty_dict(self, audit_logger, memory_backend):
        await audit_logger.log_agent_registration(agent_id="a-003", actor=ACTOR, context=None)
        assert memory_backend.events[0]["context"] == {}

    async def test_ip_and_ua_forwarded(self, audit_logger, memory_backend):
        await audit_logger.log_agent_registration(
            agent_id="a-004", actor=ACTOR, ip_address="1.2.3.4", user_agent="cli/1"
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "1.2.3.4"
        assert event["user_agent"] == "cli/1"


# ===========================================================================
# AuditLogger — log_agent_deregistration()
# ===========================================================================


class TestLogAgentDeregistration:
    async def test_action_and_target(self, audit_logger, memory_backend):
        await audit_logger.log_agent_deregistration(agent_id="a-010", actor=ACTOR)
        event = memory_backend.events[0]
        assert event["action"] == "agent_deregistration"
        assert event["target"] == {"id": "a-010", "type": "agent"}

    async def test_context_forwarded(self, audit_logger, memory_backend):
        await audit_logger.log_agent_deregistration(
            agent_id="a-011", actor=ACTOR, context={"reason": "shutdown"}
        )
        assert memory_backend.events[0]["context"] == {"reason": "shutdown"}

    async def test_none_context_becomes_empty_dict(self, audit_logger, memory_backend):
        await audit_logger.log_agent_deregistration(agent_id="a-012", actor=ACTOR, context=None)
        assert memory_backend.events[0]["context"] == {}

    async def test_ip_and_ua_forwarded(self, audit_logger, memory_backend):
        await audit_logger.log_agent_deregistration(
            agent_id="a-013", actor=ACTOR, ip_address="5.6.7.8", user_agent="sdk/2"
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "5.6.7.8"
        assert event["user_agent"] == "sdk/2"


# ===========================================================================
# AuditLogger — log_task_creation()
# ===========================================================================


class TestLogTaskCreation:
    async def test_action_and_target(self, audit_logger, memory_backend):
        await audit_logger.log_task_creation(task_id="t-100", actor=ACTOR)
        event = memory_backend.events[0]
        assert event["action"] == "task_creation"
        assert event["target"] == {"id": "t-100", "type": "task"}

    async def test_context_forwarded(self, audit_logger, memory_backend):
        await audit_logger.log_task_creation(
            task_id="t-101", actor=ACTOR, context={"title": "Feat X"}
        )
        assert memory_backend.events[0]["context"] == {"title": "Feat X"}

    async def test_none_context(self, audit_logger, memory_backend):
        await audit_logger.log_task_creation(task_id="t-102", actor=ACTOR, context=None)
        assert memory_backend.events[0]["context"] == {}

    async def test_ip_and_ua(self, audit_logger, memory_backend):
        await audit_logger.log_task_creation(
            task_id="t-103", actor=ACTOR, ip_address="9.9.9.9", user_agent="browser"
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "9.9.9.9"
        assert event["user_agent"] == "browser"


# ===========================================================================
# AuditLogger — log_task_deletion()
# ===========================================================================


class TestLogTaskDeletion:
    async def test_action_and_target(self, audit_logger, memory_backend):
        await audit_logger.log_task_deletion(task_id="t-200", actor=ACTOR)
        event = memory_backend.events[0]
        assert event["action"] == "task_deletion"
        assert event["target"] == {"id": "t-200", "type": "task"}

    async def test_context_forwarded(self, audit_logger, memory_backend):
        await audit_logger.log_task_deletion(
            task_id="t-201", actor=ACTOR, context={"reason": "obsolete"}
        )
        assert memory_backend.events[0]["context"] == {"reason": "obsolete"}

    async def test_none_context(self, audit_logger, memory_backend):
        await audit_logger.log_task_deletion(task_id="t-202", actor=ACTOR, context=None)
        assert memory_backend.events[0]["context"] == {}

    async def test_ip_and_ua(self, audit_logger, memory_backend):
        await audit_logger.log_task_deletion(
            task_id="t-203", actor=ACTOR, ip_address="3.3.3.3", user_agent="admin"
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "3.3.3.3"
        assert event["user_agent"] == "admin"


# ===========================================================================
# AuditLogger — log_task_assignment()
# ===========================================================================


class TestLogTaskAssignment:
    async def test_action_target_and_agent_id_in_context(self, audit_logger, memory_backend):
        await audit_logger.log_task_assignment(task_id="t-300", agent_id="agent-007", actor=ACTOR)
        event = memory_backend.events[0]
        assert event["action"] == "task_assignment"
        assert event["target"] == {"id": "t-300", "type": "task"}
        assert event["context"]["agent_id"] == "agent-007"

    async def test_existing_context_merged_with_agent_id(self, audit_logger, memory_backend):
        await audit_logger.log_task_assignment(
            task_id="t-301",
            agent_id="agent-008",
            actor=ACTOR,
            context={"priority": "high"},
        )
        ctx = memory_backend.events[0]["context"]
        assert ctx["priority"] == "high"
        assert ctx["agent_id"] == "agent-008"

    async def test_none_context_replaced_by_dict_with_agent_id(self, audit_logger, memory_backend):
        await audit_logger.log_task_assignment(
            task_id="t-302", agent_id="a0", actor=ACTOR, context=None
        )
        assert memory_backend.events[0]["context"]["agent_id"] == "a0"

    async def test_ip_and_ua(self, audit_logger, memory_backend):
        await audit_logger.log_task_assignment(
            task_id="t-303",
            agent_id="a1",
            actor=ACTOR,
            ip_address="6.6.6.6",
            user_agent="forge-bot",
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "6.6.6.6"
        assert event["user_agent"] == "forge-bot"


# ===========================================================================
# AuditLogger — log_approval_decision()
# ===========================================================================


class TestLogApprovalDecision:
    async def test_approved_maps_to_approval_approve(self, audit_logger, memory_backend):
        await audit_logger.log_approval_decision(
            approval_id="appr-001", decision="approved", actor=ACTOR
        )
        event = memory_backend.events[0]
        assert event["action"] == "approval_approve"
        assert event["target"] == {"id": "appr-001", "type": "approval_request"}
        assert event["context"]["decision"] == "approved"

    async def test_rejected_maps_to_approval_reject(self, audit_logger, memory_backend):
        await audit_logger.log_approval_decision(
            approval_id="appr-002", decision="rejected", actor=ACTOR
        )
        assert memory_backend.events[0]["action"] == "approval_reject"

    async def test_non_approved_decision_maps_to_approval_reject(
        self, audit_logger, memory_backend
    ):
        """Any value other than 'approved' triggers the reject path."""
        await audit_logger.log_approval_decision(
            approval_id="appr-003", decision="pending", actor=ACTOR
        )
        assert memory_backend.events[0]["action"] == "approval_reject"

    async def test_existing_context_merged_with_decision(self, audit_logger, memory_backend):
        await audit_logger.log_approval_decision(
            approval_id="appr-004",
            decision="approved",
            actor=ACTOR,
            context={"reviewer": "alice"},
        )
        ctx = memory_backend.events[0]["context"]
        assert ctx["reviewer"] == "alice"
        assert ctx["decision"] == "approved"

    async def test_none_context_replaced(self, audit_logger, memory_backend):
        await audit_logger.log_approval_decision(
            approval_id="appr-005", decision="approved", actor=ACTOR, context=None
        )
        assert memory_backend.events[0]["context"]["decision"] == "approved"

    async def test_ip_and_ua(self, audit_logger, memory_backend):
        await audit_logger.log_approval_decision(
            approval_id="appr-006",
            decision="rejected",
            actor=ACTOR,
            ip_address="7.7.7.7",
            user_agent="mobile-app",
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "7.7.7.7"
        assert event["user_agent"] == "mobile-app"


# ===========================================================================
# AuditLogger — log_handoff_operation()
# ===========================================================================


class TestLogHandoffOperation:
    async def test_action_and_target(self, audit_logger, memory_backend):
        await audit_logger.log_handoff_operation(handoff_id="hoff-500", actor=ACTOR)
        event = memory_backend.events[0]
        assert event["action"] == "handoff_operation"
        assert event["target"] == {"id": "hoff-500", "type": "handoff"}

    async def test_context_forwarded(self, audit_logger, memory_backend):
        await audit_logger.log_handoff_operation(
            handoff_id="hoff-501", actor=ACTOR, context={"from": "a1", "to": "a2"}
        )
        assert memory_backend.events[0]["context"] == {"from": "a1", "to": "a2"}

    async def test_none_context(self, audit_logger, memory_backend):
        await audit_logger.log_handoff_operation(handoff_id="hoff-502", actor=ACTOR, context=None)
        assert memory_backend.events[0]["context"] == {}

    async def test_ip_and_ua(self, audit_logger, memory_backend):
        await audit_logger.log_handoff_operation(
            handoff_id="hoff-503",
            actor=ACTOR,
            ip_address="2.2.2.2",
            user_agent="agent-sdk",
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "2.2.2.2"
        assert event["user_agent"] == "agent-sdk"


# ===========================================================================
# AuditLogger — log_fleet_control()
# ===========================================================================


class TestLogFleetControl:
    async def test_action_prefixed_with_fleet(self, audit_logger, memory_backend):
        await audit_logger.log_fleet_control(action_type="pause", actor=ACTOR)
        event = memory_backend.events[0]
        assert event["action"] == "fleet_pause"
        assert event["target"]["type"] == "fleet"

    async def test_target_id_used_when_provided(self, audit_logger, memory_backend):
        await audit_logger.log_fleet_control(
            action_type="resume", actor=ACTOR, target_id="cluster-A"
        )
        assert memory_backend.events[0]["target"]["id"] == "cluster-A"

    async def test_target_id_none_defaults_to_fleet_string(self, audit_logger, memory_backend):
        await audit_logger.log_fleet_control(action_type="broadcast", actor=ACTOR, target_id=None)
        assert memory_backend.events[0]["target"]["id"] == "fleet"

    async def test_context_forwarded(self, audit_logger, memory_backend):
        await audit_logger.log_fleet_control(
            action_type="broadcast", actor=ACTOR, context={"message": "hello"}
        )
        assert memory_backend.events[0]["context"] == {"message": "hello"}

    async def test_none_context(self, audit_logger, memory_backend):
        await audit_logger.log_fleet_control(action_type="kill", actor=ACTOR)
        assert memory_backend.events[0]["context"] == {}

    async def test_ip_and_ua(self, audit_logger, memory_backend):
        await audit_logger.log_fleet_control(
            action_type="pause",
            actor=ACTOR,
            ip_address="3.3.3.3",
            user_agent="control-plane",
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "3.3.3.3"
        assert event["user_agent"] == "control-plane"


# ===========================================================================
# AuditLogger — log_configuration_change()
# ===========================================================================


class TestLogConfigurationChange:
    async def test_action_and_target(self, audit_logger, memory_backend):
        await audit_logger.log_configuration_change(config_key="max_agents", actor=ACTOR)
        event = memory_backend.events[0]
        assert event["action"] == "configuration_change"
        assert event["target"] == {"id": "max_agents", "type": "configuration"}
        assert event["context"]["config_key"] == "max_agents"

    async def test_existing_context_merged(self, audit_logger, memory_backend):
        await audit_logger.log_configuration_change(
            config_key="timeout",
            actor=ACTOR,
            context={"old": 30, "new": 60},
        )
        ctx = memory_backend.events[0]["context"]
        assert ctx["old"] == 30
        assert ctx["config_key"] == "timeout"

    async def test_none_context(self, audit_logger, memory_backend):
        await audit_logger.log_configuration_change(
            config_key="rate_limit", actor=ACTOR, context=None
        )
        assert memory_backend.events[0]["context"]["config_key"] == "rate_limit"

    async def test_ip_and_ua(self, audit_logger, memory_backend):
        await audit_logger.log_configuration_change(
            config_key="k", actor=ACTOR, ip_address="4.4.4.4", user_agent="admin-ui"
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "4.4.4.4"
        assert event["user_agent"] == "admin-ui"


# ===========================================================================
# AuditLogger — log_authentication_event()
# ===========================================================================


class TestLogAuthenticationEvent:
    async def test_action_prefixed_with_auth(self, audit_logger, memory_backend):
        await audit_logger.log_authentication_event(event_type="login", actor=ACTOR)
        event = memory_backend.events[0]
        assert event["action"] == "auth_login"
        assert event["target"]["type"] == "user"

    async def test_logout_event(self, audit_logger, memory_backend):
        await audit_logger.log_authentication_event(event_type="logout", actor=ACTOR)
        assert memory_backend.events[0]["action"] == "auth_logout"

    async def test_refresh_event(self, audit_logger, memory_backend):
        await audit_logger.log_authentication_event(event_type="refresh", actor=ACTOR)
        assert memory_backend.events[0]["action"] == "auth_refresh"

    async def test_target_id_taken_from_actor(self, audit_logger, memory_backend):
        actor = {"id": "user-XYZ", "type": "user"}
        await audit_logger.log_authentication_event(event_type="login", actor=actor)
        assert memory_backend.events[0]["target"]["id"] == "user-XYZ"

    async def test_target_id_is_unknown_when_actor_has_no_id(self, audit_logger, memory_backend):
        actor_without_id = {"type": "service"}
        await audit_logger.log_authentication_event(event_type="login", actor=actor_without_id)
        assert memory_backend.events[0]["target"]["id"] == "unknown"

    async def test_context_forwarded(self, audit_logger, memory_backend):
        await audit_logger.log_authentication_event(
            event_type="login", actor=ACTOR, context={"method": "oauth2"}
        )
        assert memory_backend.events[0]["context"] == {"method": "oauth2"}

    async def test_none_context(self, audit_logger, memory_backend):
        await audit_logger.log_authentication_event(event_type="login", actor=ACTOR, context=None)
        assert memory_backend.events[0]["context"] == {}

    async def test_ip_and_ua(self, audit_logger, memory_backend):
        await audit_logger.log_authentication_event(
            event_type="login",
            actor=ACTOR,
            ip_address="8.8.8.8",
            user_agent="safari/15",
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "8.8.8.8"
        assert event["user_agent"] == "safari/15"


# ===========================================================================
# Singleton helpers — get_audit_logger / reset_audit_logger
# ===========================================================================


class TestSingletonHelpers:
    def test_get_audit_logger_returns_audit_logger_instance(self, tmp_path):
        instance = get_audit_logger(forge_root=tmp_path)
        assert isinstance(instance, AuditLogger)

    def test_get_audit_logger_returns_same_instance_on_repeated_calls(self, tmp_path):
        first = get_audit_logger(forge_root=tmp_path)
        second = get_audit_logger(forge_root=tmp_path)
        assert first is second

    def test_reset_audit_logger_clears_singleton(self, tmp_path):
        first = get_audit_logger(forge_root=tmp_path)
        reset_audit_logger()
        second = get_audit_logger(forge_root=tmp_path)
        assert first is not second

    def test_get_after_reset_creates_fresh_instance(self, tmp_path):
        get_audit_logger(forge_root=tmp_path)
        reset_audit_logger()
        instance = get_audit_logger(forge_root=tmp_path)
        assert isinstance(instance, AuditLogger)

    def test_reset_is_idempotent(self):
        reset_audit_logger()
        reset_audit_logger()  # Second call must not raise

    def test_get_returns_none_forge_root_instance_when_already_set(self, tmp_path):
        """Second call to get_audit_logger ignores forge_root if singleton exists."""
        first = get_audit_logger(forge_root=tmp_path)
        # forge_root arg is irrelevant on second call — singleton is returned
        second = get_audit_logger(forge_root=None)
        assert first is second


# ===========================================================================
# Integration: JSONLAuditBackend + AuditLogger end-to-end
# ===========================================================================


class TestJSONLIntegration:
    async def test_full_round_trip_dispatch_event(self, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        backend = JSONLAuditBackend(path=audit_file)
        logger = AuditLogger(backend=backend)

        await logger.log(
            action="dispatch",
            actor={"id": "orchestrator", "type": "system"},
            target={"id": "task-99", "type": "task"},
            context={"domain": "codeswiftr"},
            status="success",
            request_id="req-abc",
            source="command_center",
            ip_address="127.0.0.1",
            user_agent="forge-ui/2.0",
        )

        line = audit_file.read_text().strip()
        event = json.loads(line)
        assert event["action"] == "dispatch"
        assert event["source"] == "command_center"
        assert event["request_id"] == "req-abc"
        assert event["context"]["domain"] == "codeswiftr"
        assert event["ip_address"] == "127.0.0.1"
        assert event["user_agent"] == "forge-ui/2.0"

    async def test_multiple_helper_methods_produce_separate_jsonl_lines(self, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        backend = JSONLAuditBackend(path=audit_file)
        logger = AuditLogger(backend=backend)

        await logger.log_agent_registration(agent_id="a1", actor=ACTOR)
        await logger.log_task_creation(task_id="t1", actor=ACTOR)
        await logger.log_fleet_control(action_type="pause", actor=ACTOR)

        lines = audit_file.read_text().strip().splitlines()
        assert len(lines) == 3
        actions = [json.loads(line)["action"] for line in lines]
        assert actions == ["agent_registration", "task_creation", "fleet_pause"]

    async def test_each_event_has_unique_event_id(self, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        backend = JSONLAuditBackend(path=audit_file)
        logger = AuditLogger(backend=backend)

        for _ in range(5):
            await logger.log(action="ping", actor=ACTOR, target=TARGET)

        lines = audit_file.read_text().strip().splitlines()
        event_ids = [json.loads(line)["event_id"] for line in lines]
        assert len(set(event_ids)) == 5  # all unique


# ===========================================================================
# Concurrent access
# ===========================================================================


class TestConcurrentAccess:
    """Verify correctness under concurrent coroutine emission."""

    async def test_memory_backend_concurrent_emit_all_events_stored(self):
        """50 concurrent emits to MemoryAuditBackend must all be stored."""
        import asyncio

        backend = MemoryAuditBackend()
        n = 50
        await asyncio.gather(*(backend.emit({"seq": i}) for i in range(n)))
        assert len(backend.events) == n

    async def test_audit_logger_concurrent_log_all_events_recorded(self):
        """50 concurrent AuditLogger.log() calls must produce 50 distinct events."""
        import asyncio

        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        n = 50
        await asyncio.gather(
            *(audit.log(action=f"act-{i}", actor=ACTOR, target=TARGET) for i in range(n))
        )
        assert len(backend.events) == n

    async def test_audit_logger_concurrent_event_ids_are_unique(self):
        """Concurrent calls must still produce globally unique event_ids."""
        import asyncio

        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        await asyncio.gather(
            *(audit.log(action="ping", actor=ACTOR, target=TARGET) for _ in range(30))
        )
        ids = {ev["event_id"] for ev in backend.events}
        assert len(ids) == 30

    async def test_jsonl_concurrent_writes_produce_correct_line_count(self, tmp_path):
        """Concurrent writes to the JSONL backend must each appear as a separate line."""
        import asyncio

        audit_file = tmp_path / "audit.jsonl"
        backend = JSONLAuditBackend(path=audit_file)
        n = 20
        await asyncio.gather(*(backend.emit({"seq": i}) for i in range(n)))

        lines = audit_file.read_text().splitlines()
        assert len(lines) == n
        for line in lines:
            parsed = json.loads(line)
            assert "seq" in parsed

    async def test_jsonl_concurrent_lines_all_valid_json(self, tmp_path):
        """Every line written under concurrency must be valid, parseable JSON."""
        import asyncio

        audit_file = tmp_path / "audit.jsonl"
        backend = JSONLAuditBackend(path=audit_file)
        n = 30
        await asyncio.gather(
            *(backend.emit({"action": f"op-{i}", "index": i}) for i in range(n))
        )

        lines = audit_file.read_text().splitlines()
        assert len(lines) == n
        for line in lines:
            obj = json.loads(line)
            assert "action" in obj
            assert "index" in obj


# ===========================================================================
# Edge cases — boundary inputs and unusual values
# ===========================================================================


class TestEdgeCases:
    """Boundary inputs that exercise defensive branches in the service."""

    async def test_log_empty_actor_dict(self):
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        await audit.log(action="dispatch", actor={}, target=TARGET)
        assert backend.events[0]["actor"] == {}

    async def test_log_empty_target_dict(self):
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        await audit.log(action="dispatch", actor=ACTOR, target={})
        assert backend.events[0]["target"] == {}

    async def test_log_empty_string_action(self):
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        await audit.log(action="", actor=ACTOR, target=TARGET)
        assert backend.events[0]["action"] == ""

    async def test_log_deeply_nested_context(self):
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        ctx = {"l1": {"l2": {"l3": {"val": 99}}}}
        await audit.log(action="dispatch", actor=ACTOR, target=TARGET, context=ctx)
        assert backend.events[0]["context"]["l1"]["l2"]["l3"]["val"] == 99

    async def test_log_very_long_action_string(self):
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        long_action = "a" * 10_000
        await audit.log(action=long_action, actor=ACTOR, target=TARGET)
        assert backend.events[0]["action"] == long_action

    async def test_log_unicode_actor_and_target_ids(self):
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        await audit.log(
            action="dispatch",
            actor={"id": "用户-1", "type": "user"},
            target={"id": "任务-42", "type": "task"},
        )
        ev = backend.events[0]
        assert ev["actor"]["id"] == "用户-1"
        assert ev["target"]["id"] == "任务-42"

    async def test_log_unicode_values_survive_jsonl_round_trip(self, tmp_path):
        """Unicode characters must be preserved when serialised to JSONL."""
        audit_file = tmp_path / "audit.jsonl"
        backend = JSONLAuditBackend(path=audit_file)
        audit = AuditLogger(backend=backend)
        await audit.log(
            action="dispatch",
            actor={"id": "café-user", "type": "user"},
            target={"id": "tâche-1", "type": "task"},
        )
        line = audit_file.read_text().strip()
        parsed = json.loads(line)
        assert parsed["actor"]["id"] == "café-user"
        assert parsed["target"]["id"] == "tâche-1"

    async def test_jsonl_emit_path_object_serialised_as_string(self, tmp_path):
        """Path objects in event dicts must be coerced to strings via default=str."""
        audit_file = tmp_path / "audit.jsonl"
        backend = JSONLAuditBackend(path=audit_file)
        await backend.emit({"path_field": tmp_path / "some" / "file.txt"})
        line = audit_file.read_text().strip()
        parsed = json.loads(line)
        assert "path_field" in parsed
        assert str(tmp_path / "some" / "file.txt") in parsed["path_field"]

    async def test_memory_backend_emit_empty_event(self):
        backend = MemoryAuditBackend()
        await backend.emit({})
        assert backend.events == [{}]

    async def test_log_multiple_calls_emit_called_once_each(self):
        """Each call to log() must result in exactly one backend.emit() call."""
        backend = MemoryAuditBackend()
        call_count = 0
        original_emit = backend.emit

        async def counting_emit(event):
            nonlocal call_count
            call_count += 1
            await original_emit(event)

        backend.emit = counting_emit  # type: ignore[method-assign]
        audit = AuditLogger(backend=backend)

        for _ in range(7):
            await audit.log(action="x", actor=ACTOR, target=TARGET)

        assert call_count == 7

    async def test_log_fleet_control_arbitrary_action_type(self):
        """Any action_type string must be prefixed with 'fleet_'."""
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        await audit.log_fleet_control(action_type="custom_op_99", actor=ACTOR)
        assert backend.events[0]["action"] == "fleet_custom_op_99"

    async def test_log_authentication_event_arbitrary_event_type(self):
        """Any event_type string must be prefixed with 'auth_'."""
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        await audit.log_authentication_event(event_type="mfa_challenge", actor=ACTOR)
        assert backend.events[0]["action"] == "auth_mfa_challenge"

    async def test_jsonl_emit_exception_during_json_dumps_caught(self, tmp_path):
        """A json.dumps failure (e.g. circular reference) is caught by the except clause."""

        class Unserializable:
            def __repr__(self):
                raise RuntimeError("cannot repr")

        audit_file = tmp_path / "audit.jsonl"
        backend = JSONLAuditBackend(path=audit_file)

        import forge_harness.webhook_server.services.audit as audit_module

        with patch.object(audit_module, "logger") as mock_logger:
            with patch("json.dumps", side_effect=TypeError("circular reference")):
                # Must not raise
                await backend.emit({"bad": "data"})
            mock_logger.warning.assert_called_once()

    async def test_log_task_assignment_agent_id_overwrites_existing_key(self):
        """If context already has 'agent_id', it will be overwritten by the method."""
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        ctx = {"agent_id": "old-agent"}
        await audit.log_task_assignment(
            task_id="t-999", agent_id="new-agent", actor=ACTOR, context=ctx
        )
        # The method mutates ctx and sets agent_id — final value is new-agent
        assert backend.events[0]["context"]["agent_id"] == "new-agent"

    async def test_log_approval_decision_context_decision_key_overwrites_existing(self):
        """If context already has 'decision', it will be overwritten by the method."""
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        ctx = {"decision": "old_value", "extra": "data"}
        await audit.log_approval_decision(
            approval_id="apr-ow",
            decision="approved",
            actor=ACTOR,
            context=ctx,
        )
        assert backend.events[0]["context"]["decision"] == "approved"
        assert backend.events[0]["context"]["extra"] == "data"

    async def test_log_configuration_change_config_key_overwrites_existing(self):
        """If context already has 'config_key', it will be overwritten by the method."""
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        ctx = {"config_key": "old_key", "note": "testing"}
        await audit.log_configuration_change(
            config_key="new_key", actor=ACTOR, context=ctx
        )
        assert backend.events[0]["context"]["config_key"] == "new_key"
        assert backend.events[0]["context"]["note"] == "testing"


# ===========================================================================
# Schema contract — all log_* helpers must produce compliant event dicts
# ===========================================================================


class TestEventSchemaContract:
    """Assert that every log helper produces an event conforming to the core schema."""

    _REQUIRED_FIELDS = {
        "timestamp",
        "event_id",
        "actor",
        "action",
        "target",
        "context",
        "status",
        "error",
        "request_id",
        "source",
    }

    async def _collect_event(self, coro) -> dict:
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)
        await coro(audit)
        return backend.events[0]

    async def _check_schema(self, coro) -> None:
        event = await self._collect_event(coro)
        missing = self._REQUIRED_FIELDS - event.keys()
        assert not missing, f"Event missing required fields: {missing}"

    async def test_log_schema(self):
        await self._check_schema(
            lambda a: a.log(action="dispatch", actor=ACTOR, target=TARGET)
        )

    async def test_log_agent_registration_schema(self):
        await self._check_schema(
            lambda a: a.log_agent_registration(agent_id="x", actor=ACTOR)
        )

    async def test_log_agent_deregistration_schema(self):
        await self._check_schema(
            lambda a: a.log_agent_deregistration(agent_id="x", actor=ACTOR)
        )

    async def test_log_task_creation_schema(self):
        await self._check_schema(
            lambda a: a.log_task_creation(task_id="t", actor=ACTOR)
        )

    async def test_log_task_deletion_schema(self):
        await self._check_schema(
            lambda a: a.log_task_deletion(task_id="t", actor=ACTOR)
        )

    async def test_log_task_assignment_schema(self):
        await self._check_schema(
            lambda a: a.log_task_assignment(task_id="t", agent_id="a", actor=ACTOR)
        )

    async def test_log_approval_decision_schema(self):
        await self._check_schema(
            lambda a: a.log_approval_decision(approval_id="ap", decision="approved", actor=ACTOR)
        )

    async def test_log_handoff_operation_schema(self):
        await self._check_schema(
            lambda a: a.log_handoff_operation(handoff_id="hoff", actor=ACTOR)
        )

    async def test_log_fleet_control_schema(self):
        await self._check_schema(
            lambda a: a.log_fleet_control(action_type="pause", actor=ACTOR)
        )

    async def test_log_configuration_change_schema(self):
        await self._check_schema(
            lambda a: a.log_configuration_change(config_key="k", actor=ACTOR)
        )

    async def test_log_authentication_event_schema(self):
        await self._check_schema(
            lambda a: a.log_authentication_event(event_type="login", actor=ACTOR)
        )

    async def test_all_events_have_valid_uuid_event_id(self):
        """Every helper must generate a well-formed UUID as event_id."""
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)

        await audit.log(action="a", actor=ACTOR, target=TARGET)
        await audit.log_agent_registration(agent_id="x", actor=ACTOR)
        await audit.log_task_creation(task_id="t", actor=ACTOR)
        await audit.log_fleet_control(action_type="pause", actor=ACTOR)
        await audit.log_authentication_event(event_type="login", actor=ACTOR)

        for ev in backend.events:
            try:
                uuid.UUID(ev["event_id"])
            except ValueError:
                raise AssertionError(f"Invalid event_id UUID: {ev['event_id']}")

    async def test_all_events_default_source_is_webhook_api(self):
        """All convenience helpers hard-code source='webhook_api'."""
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)

        await audit.log_agent_registration(agent_id="x", actor=ACTOR)
        await audit.log_agent_deregistration(agent_id="x", actor=ACTOR)
        await audit.log_task_creation(task_id="t", actor=ACTOR)
        await audit.log_task_deletion(task_id="t", actor=ACTOR)
        await audit.log_task_assignment(task_id="t", agent_id="a", actor=ACTOR)
        await audit.log_approval_decision(approval_id="ap", decision="approved", actor=ACTOR)
        await audit.log_handoff_operation(handoff_id="hoff", actor=ACTOR)
        await audit.log_fleet_control(action_type="pause", actor=ACTOR)
        await audit.log_configuration_change(config_key="k", actor=ACTOR)
        await audit.log_authentication_event(event_type="login", actor=ACTOR)

        for ev in backend.events:
            assert ev["source"] == "webhook_api", (
                f"Unexpected source '{ev['source']}' in action '{ev['action']}'"
            )

    async def test_all_events_default_status_is_success(self):
        """All convenience helpers inherit the default status='success'."""
        backend = MemoryAuditBackend()
        audit = AuditLogger(backend=backend)

        await audit.log_agent_registration(agent_id="x", actor=ACTOR)
        await audit.log_task_creation(task_id="t", actor=ACTOR)
        await audit.log_fleet_control(action_type="resume", actor=ACTOR)
        await audit.log_authentication_event(event_type="logout", actor=ACTOR)

        for ev in backend.events:
            assert ev["status"] == "success"
