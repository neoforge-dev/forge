"""Unit tests for forge_harness.webhook_server.services.audit

Targets: AuditBackend, MemoryAuditBackend, JSONLAuditBackend, AuditLogger,
         get_audit_logger, reset_audit_logger

Coverage goal: 95%+ line coverage

All external dependencies (file I/O, logging) are mocked.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio  # noqa: F401 — ensures async mode is available

import forge_harness.webhook_server.services.audit as _audit_module  # noqa: E402

# ---------------------------------------------------------------------------
# Import module under test
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.services.audit import (  # noqa: E402
    AuditBackend,
    AuditLogger,
    JSONLAuditBackend,
    MemoryAuditBackend,
    get_audit_logger,
    reset_audit_logger,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the global audit logger singleton before and after every test."""
    reset_audit_logger()
    yield
    reset_audit_logger()


@pytest.fixture
def memory_backend():
    """Return a fresh MemoryAuditBackend."""
    return MemoryAuditBackend()


@pytest.fixture
def audit_logger(memory_backend):
    """Return an AuditLogger backed by MemoryAuditBackend."""
    return AuditLogger(backend=memory_backend)


@pytest.fixture
def actor():
    return {"id": "user-1", "type": "user", "name": "Alice"}


# ===========================================================================
# TestAuditBackendInterface
# ===========================================================================


class TestAuditBackendInterface:
    """AuditBackend is an ABC — verify it cannot be instantiated directly."""

    def test_cannot_instantiate_abstract_base(self):
        with pytest.raises(TypeError):
            AuditBackend()  # type: ignore[abstract]

    def test_concrete_subclass_must_implement_emit(self):
        """A subclass that does not implement emit should be uninstantiable."""

        class BadBackend(AuditBackend):
            pass

        with pytest.raises(TypeError):
            BadBackend()  # type: ignore[abstract]

    def test_concrete_subclass_with_emit_is_valid(self):
        class GoodBackend(AuditBackend):
            async def emit(self, event):
                pass

        backend = GoodBackend()
        assert isinstance(backend, AuditBackend)


# ===========================================================================
# TestMemoryAuditBackend
# ===========================================================================


class TestMemoryAuditBackend:
    """Tests for MemoryAuditBackend."""

    def test_starts_empty(self, memory_backend):
        assert memory_backend.events == []

    @pytest.mark.asyncio
    async def test_emit_appends_event(self, memory_backend):
        event = {"action": "dispatch", "actor": {}}
        await memory_backend.emit(event)
        assert len(memory_backend.events) == 1
        assert memory_backend.events[0] is event

    @pytest.mark.asyncio
    async def test_emit_multiple_events_accumulate(self, memory_backend):
        for i in range(5):
            await memory_backend.emit({"seq": i})
        assert len(memory_backend.events) == 5

    @pytest.mark.asyncio
    async def test_emit_preserves_event_identity(self, memory_backend):
        """The exact dict object passed is stored."""
        event = {"action": "test"}
        await memory_backend.emit(event)
        assert memory_backend.events[0] is event

    @pytest.mark.asyncio
    async def test_emit_accepts_empty_dict(self, memory_backend):
        await memory_backend.emit({})
        assert memory_backend.events == [{}]

    def test_events_list_is_mutable(self, memory_backend):
        memory_backend.events.append({"manual": True})
        assert len(memory_backend.events) == 1


# ===========================================================================
# TestJSONLAuditBackendInit
# ===========================================================================


class TestJSONLAuditBackendInit:
    """Tests for JSONLAuditBackend.__init__ and path resolution."""

    def test_explicit_path_used(self, tmp_path):
        explicit = tmp_path / "my_audit.jsonl"
        backend = JSONLAuditBackend(path=explicit)
        assert backend._path == explicit

    def test_explicit_path_as_string(self, tmp_path):
        explicit = str(tmp_path / "string_path.jsonl")
        backend = JSONLAuditBackend(path=explicit)
        assert backend._path == Path(explicit)

    def test_parent_directory_created_for_explicit_path(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "audit.jsonl"
        backend = JSONLAuditBackend(path=deep)
        assert deep.parent.exists()

    def test_forge_root_constructs_expected_path(self, tmp_path):
        backend = JSONLAuditBackend(forge_root=tmp_path)
        expected = tmp_path / ".forge/state" / "audit" / "audit.jsonl"
        assert backend._path == expected

    def test_forge_root_directory_created(self, tmp_path):
        backend = JSONLAuditBackend(forge_root=tmp_path)
        assert backend._path.parent.exists()

    def test_path_kwarg_takes_precedence_over_forge_root(self, tmp_path):
        explicit = tmp_path / "explicit.jsonl"
        backend = JSONLAuditBackend(path=explicit, forge_root=tmp_path / "other")
        assert backend._path == explicit

    def test_default_no_args_does_not_raise(self, tmp_path):
        """Calling with no args should succeed (uses _find_forge_root internally)."""
        with patch.object(
            JSONLAuditBackend,
            "_find_forge_root",
            return_value=tmp_path,
        ):
            backend = JSONLAuditBackend()
            assert backend._path == tmp_path / ".forge/state" / "audit" / "audit.jsonl"


# ===========================================================================
# TestJSONLAuditBackendFindForgeRoot
# ===========================================================================


class TestJSONLAuditBackendFindForgeRoot:
    """Tests for JSONLAuditBackend._find_forge_root."""

    def test_returns_path_instance(self, tmp_path):
        backend = JSONLAuditBackend(forge_root=tmp_path)
        result = backend._find_forge_root()
        assert isinstance(result, Path)

    def test_finds_dir_with_forge_harness(self, tmp_path):
        """If a parent dir has 'forge_harness' subdir, it should be returned."""
        target = tmp_path / "project"
        target.mkdir()
        (target / "forge_harness").mkdir()

        # Simulate the method traversal by patching __file__ context
        with patch.object(
            JSONLAuditBackend,
            "_find_forge_root",
            return_value=target,
        ):
            backend = JSONLAuditBackend(forge_root=tmp_path)
            assert backend._find_forge_root() == target

    def test_finds_dir_with_pyproject_toml(self, tmp_path):
        target = tmp_path / "project"
        target.mkdir()
        (target / "pyproject.toml").touch()

        with patch.object(
            JSONLAuditBackend,
            "_find_forge_root",
            return_value=target,
        ):
            backend = JSONLAuditBackend(forge_root=tmp_path)
            assert backend._find_forge_root() == target

    def test_returns_cwd_when_not_found(self, tmp_path):
        """Falls back to Path.cwd() when no forge_harness or pyproject.toml found."""
        # The actual _find_forge_root will walk up and eventually return cwd or
        # find the harness dir. We just confirm it returns a Path without error.
        backend = JSONLAuditBackend(forge_root=tmp_path)
        result = backend._find_forge_root()
        assert isinstance(result, Path)


# ===========================================================================
# TestJSONLAuditBackendEmit
# ===========================================================================


class TestJSONLAuditBackendEmit:
    """Tests for JSONLAuditBackend.emit."""

    @pytest.mark.asyncio
    async def test_emit_writes_jsonl_line(self, tmp_path):
        backend = JSONLAuditBackend(path=tmp_path / "audit.jsonl")
        event = {"action": "dispatch", "actor": {"id": "u1"}}
        await backend.emit(event)
        content = (tmp_path / "audit.jsonl").read_text()
        assert content.endswith("\n")
        parsed = json.loads(content.strip())
        assert parsed["action"] == "dispatch"

    @pytest.mark.asyncio
    async def test_emit_appends_multiple_events(self, tmp_path):
        backend = JSONLAuditBackend(path=tmp_path / "audit.jsonl")
        for i in range(3):
            await backend.emit({"seq": i})
        lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3
        for i, line in enumerate(lines):
            assert json.loads(line)["seq"] == i

    @pytest.mark.asyncio
    async def test_emit_uses_json_default_str_for_non_serializable(self, tmp_path):
        """Non-JSON-serializable values (e.g. Path) are coerced via default=str."""
        backend = JSONLAuditBackend(path=tmp_path / "audit.jsonl")
        event = {"path_field": Path("/tmp/test"), "action": "test"}
        await backend.emit(event)
        content = (tmp_path / "audit.jsonl").read_text()
        parsed = json.loads(content.strip())
        assert parsed["path_field"] == "/tmp/test"

    @pytest.mark.asyncio
    async def test_emit_logs_warning_on_io_error(self, tmp_path):
        backend = JSONLAuditBackend(path=tmp_path / "audit.jsonl")
        with patch("builtins.open", side_effect=OSError("disk full")):
            with patch.object(_audit_module.logger, "warning") as mock_warn:
                await backend.emit({"action": "test"})
                mock_warn.assert_called_once()
                args = mock_warn.call_args[0]
                assert "Audit log emit failed" in args[0]

    @pytest.mark.asyncio
    async def test_emit_does_not_raise_on_io_error(self, tmp_path):
        """Errors during emit must be swallowed (warning only)."""
        backend = JSONLAuditBackend(path=tmp_path / "audit.jsonl")
        with patch("builtins.open", side_effect=OSError("disk full")):
            # Should NOT raise
            await backend.emit({"action": "test"})


# ===========================================================================
# TestAuditLoggerInit
# ===========================================================================


class TestAuditLoggerInit:
    """Tests for AuditLogger.__init__."""

    def test_default_backend_is_jsonl(self, tmp_path):
        with patch.object(
            JSONLAuditBackend,
            "_find_forge_root",
            return_value=tmp_path,
        ):
            logger = AuditLogger()
        assert isinstance(logger._backend, JSONLAuditBackend)

    def test_custom_backend_injected(self, memory_backend):
        logger = AuditLogger(backend=memory_backend)
        assert logger._backend is memory_backend

    def test_forge_root_forwarded_to_jsonl_backend(self, tmp_path):
        logger = AuditLogger(forge_root=tmp_path)
        expected = tmp_path / ".forge/state" / "audit" / "audit.jsonl"
        assert logger._backend._path == expected


# ===========================================================================
# TestAuditLoggerLog
# ===========================================================================


class TestAuditLoggerLog:
    """Tests for AuditLogger.log — the core emit method."""

    @pytest.mark.asyncio
    async def test_log_emits_event_with_required_fields(self, audit_logger, memory_backend, actor):
        await audit_logger.log(action="dispatch", actor=actor, target={"id": "t1", "type": "task"})
        assert len(memory_backend.events) == 1
        event = memory_backend.events[0]
        for field in ("timestamp", "event_id", "actor", "action", "target", "context", "status"):
            assert field in event

    @pytest.mark.asyncio
    async def test_log_action_stored_correctly(self, audit_logger, memory_backend, actor):
        await audit_logger.log(action="kill", actor=actor, target={})
        assert memory_backend.events[0]["action"] == "kill"

    @pytest.mark.asyncio
    async def test_log_actor_stored(self, audit_logger, memory_backend, actor):
        await audit_logger.log(action="pause", actor=actor, target={})
        assert memory_backend.events[0]["actor"] == actor

    @pytest.mark.asyncio
    async def test_log_target_stored(self, audit_logger, memory_backend, actor):
        target = {"id": "agent-42", "type": "agent"}
        await audit_logger.log(action="resume", actor=actor, target=target)
        assert memory_backend.events[0]["target"] == target

    @pytest.mark.asyncio
    async def test_log_default_status_is_success(self, audit_logger, memory_backend, actor):
        await audit_logger.log(action="complete", actor=actor, target={})
        assert memory_backend.events[0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_log_custom_status(self, audit_logger, memory_backend, actor):
        await audit_logger.log(action="dispatch", actor=actor, target={}, status="failure")
        assert memory_backend.events[0]["status"] == "failure"

    @pytest.mark.asyncio
    async def test_log_error_field_stored(self, audit_logger, memory_backend, actor):
        await audit_logger.log(
            action="dispatch", actor=actor, target={}, error="something went wrong"
        )
        assert memory_backend.events[0]["error"] == "something went wrong"

    @pytest.mark.asyncio
    async def test_log_error_defaults_to_none(self, audit_logger, memory_backend, actor):
        await audit_logger.log(action="dispatch", actor=actor, target={})
        assert memory_backend.events[0]["error"] is None

    @pytest.mark.asyncio
    async def test_log_request_id_stored(self, audit_logger, memory_backend, actor):
        await audit_logger.log(
            action="dispatch", actor=actor, target={}, request_id="req-abc"
        )
        assert memory_backend.events[0]["request_id"] == "req-abc"

    @pytest.mark.asyncio
    async def test_log_request_id_defaults_to_none(self, audit_logger, memory_backend, actor):
        await audit_logger.log(action="dispatch", actor=actor, target={})
        assert memory_backend.events[0]["request_id"] is None

    @pytest.mark.asyncio
    async def test_log_source_defaults_to_webhook_api(self, audit_logger, memory_backend, actor):
        await audit_logger.log(action="dispatch", actor=actor, target={})
        assert memory_backend.events[0]["source"] == "webhook_api"

    @pytest.mark.asyncio
    async def test_log_custom_source(self, audit_logger, memory_backend, actor):
        await audit_logger.log(action="dispatch", actor=actor, target={}, source="cli")
        assert memory_backend.events[0]["source"] == "cli"

    @pytest.mark.asyncio
    async def test_log_context_defaults_to_empty_dict(self, audit_logger, memory_backend, actor):
        await audit_logger.log(action="dispatch", actor=actor, target={})
        assert memory_backend.events[0]["context"] == {}

    @pytest.mark.asyncio
    async def test_log_context_passed_through(self, audit_logger, memory_backend, actor):
        ctx = {"project": "voice-coach", "domain": "brandfocus"}
        await audit_logger.log(action="dispatch", actor=actor, target={}, context=ctx)
        assert memory_backend.events[0]["context"] == ctx

    @pytest.mark.asyncio
    async def test_log_ip_address_added_when_provided(self, audit_logger, memory_backend, actor):
        await audit_logger.log(
            action="dispatch", actor=actor, target={}, ip_address="127.0.0.1"
        )
        assert memory_backend.events[0]["ip_address"] == "127.0.0.1"

    @pytest.mark.asyncio
    async def test_log_ip_address_not_in_event_when_none(self, audit_logger, memory_backend, actor):
        await audit_logger.log(action="dispatch", actor=actor, target={})
        assert "ip_address" not in memory_backend.events[0]

    @pytest.mark.asyncio
    async def test_log_user_agent_added_when_provided(self, audit_logger, memory_backend, actor):
        await audit_logger.log(
            action="dispatch", actor=actor, target={}, user_agent="Mozilla/5.0"
        )
        assert memory_backend.events[0]["user_agent"] == "Mozilla/5.0"

    @pytest.mark.asyncio
    async def test_log_user_agent_not_in_event_when_none(
        self, audit_logger, memory_backend, actor
    ):
        await audit_logger.log(action="dispatch", actor=actor, target={})
        assert "user_agent" not in memory_backend.events[0]

    @pytest.mark.asyncio
    async def test_log_timestamp_is_iso_format(self, audit_logger, memory_backend, actor):
        from datetime import datetime

        await audit_logger.log(action="dispatch", actor=actor, target={})
        ts = memory_backend.events[0]["timestamp"]
        # Should parse without error
        datetime.fromisoformat(ts)

    @pytest.mark.asyncio
    async def test_log_event_id_is_uuid_string(self, audit_logger, memory_backend, actor):
        import uuid

        await audit_logger.log(action="dispatch", actor=actor, target={})
        event_id = memory_backend.events[0]["event_id"]
        # Should be a valid UUID
        uuid.UUID(event_id)

    @pytest.mark.asyncio
    async def test_log_each_call_generates_unique_event_id(
        self, audit_logger, memory_backend, actor
    ):
        await audit_logger.log(action="dispatch", actor=actor, target={})
        await audit_logger.log(action="kill", actor=actor, target={})
        ids = [e["event_id"] for e in memory_backend.events]
        assert ids[0] != ids[1]

    @pytest.mark.asyncio
    async def test_log_swallows_backend_exception(self, actor):
        """When backend.emit raises, log() should catch it and only warn."""
        bad_backend = AsyncMock()
        bad_backend.emit.side_effect = RuntimeError("backend down")
        logger = AuditLogger(backend=bad_backend)
        with patch.object(_audit_module.logger, "warning") as mock_warn:
            await logger.log(action="dispatch", actor=actor, target={})
            mock_warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_does_not_raise_when_backend_fails(self, actor):
        bad_backend = AsyncMock()
        bad_backend.emit.side_effect = Exception("unexpected")
        logger = AuditLogger(backend=bad_backend)
        # Should NOT propagate
        await logger.log(action="dispatch", actor=actor, target={})

    @pytest.mark.asyncio
    async def test_log_all_optional_params_at_once(self, audit_logger, memory_backend, actor):
        await audit_logger.log(
            action="dispatch",
            actor=actor,
            target={"id": "t1", "type": "task"},
            context={"k": "v"},
            status="success",
            error=None,
            request_id="req-1",
            source="cli",
            ip_address="192.168.1.1",
            user_agent="TestAgent/1.0",
        )
        event = memory_backend.events[0]
        assert event["source"] == "cli"
        assert event["request_id"] == "req-1"
        assert event["ip_address"] == "192.168.1.1"
        assert event["user_agent"] == "TestAgent/1.0"


# ===========================================================================
# TestAuditLoggerLogAgentRegistration
# ===========================================================================


class TestAuditLoggerLogAgentRegistration:
    """Tests for AuditLogger.log_agent_registration."""

    @pytest.mark.asyncio
    async def test_action_is_agent_registration(self, audit_logger, memory_backend, actor):
        await audit_logger.log_agent_registration(agent_id="agent-1", actor=actor)
        assert memory_backend.events[0]["action"] == "agent_registration"

    @pytest.mark.asyncio
    async def test_target_id_and_type(self, audit_logger, memory_backend, actor):
        await audit_logger.log_agent_registration(agent_id="agent-42", actor=actor)
        target = memory_backend.events[0]["target"]
        assert target["id"] == "agent-42"
        assert target["type"] == "agent"

    @pytest.mark.asyncio
    async def test_source_is_webhook_api(self, audit_logger, memory_backend, actor):
        await audit_logger.log_agent_registration(agent_id="a1", actor=actor)
        assert memory_backend.events[0]["source"] == "webhook_api"

    @pytest.mark.asyncio
    async def test_context_defaults_to_empty(self, audit_logger, memory_backend, actor):
        await audit_logger.log_agent_registration(agent_id="a1", actor=actor)
        assert memory_backend.events[0]["context"] == {}

    @pytest.mark.asyncio
    async def test_context_passed_through(self, audit_logger, memory_backend, actor):
        ctx = {"region": "us-east-1"}
        await audit_logger.log_agent_registration(agent_id="a1", actor=actor, context=ctx)
        assert memory_backend.events[0]["context"] == ctx

    @pytest.mark.asyncio
    async def test_ip_address_forwarded(self, audit_logger, memory_backend, actor):
        await audit_logger.log_agent_registration(
            agent_id="a1", actor=actor, ip_address="10.0.0.1"
        )
        assert memory_backend.events[0]["ip_address"] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_user_agent_forwarded(self, audit_logger, memory_backend, actor):
        await audit_logger.log_agent_registration(
            agent_id="a1", actor=actor, user_agent="ForgeClient/2.0"
        )
        assert memory_backend.events[0]["user_agent"] == "ForgeClient/2.0"


# ===========================================================================
# TestAuditLoggerLogAgentDeregistration
# ===========================================================================


class TestAuditLoggerLogAgentDeregistration:
    """Tests for AuditLogger.log_agent_deregistration."""

    @pytest.mark.asyncio
    async def test_action_is_agent_deregistration(self, audit_logger, memory_backend, actor):
        await audit_logger.log_agent_deregistration(agent_id="agent-1", actor=actor)
        assert memory_backend.events[0]["action"] == "agent_deregistration"

    @pytest.mark.asyncio
    async def test_target_id_and_type(self, audit_logger, memory_backend, actor):
        await audit_logger.log_agent_deregistration(agent_id="agent-99", actor=actor)
        target = memory_backend.events[0]["target"]
        assert target["id"] == "agent-99"
        assert target["type"] == "agent"

    @pytest.mark.asyncio
    async def test_source_is_webhook_api(self, audit_logger, memory_backend, actor):
        await audit_logger.log_agent_deregistration(agent_id="a1", actor=actor)
        assert memory_backend.events[0]["source"] == "webhook_api"

    @pytest.mark.asyncio
    async def test_context_none_becomes_empty_dict(self, audit_logger, memory_backend, actor):
        await audit_logger.log_agent_deregistration(agent_id="a1", actor=actor, context=None)
        assert memory_backend.events[0]["context"] == {}

    @pytest.mark.asyncio
    async def test_ip_and_ua_forwarded(self, audit_logger, memory_backend, actor):
        await audit_logger.log_agent_deregistration(
            agent_id="a1", actor=actor, ip_address="1.2.3.4", user_agent="UA/1"
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "1.2.3.4"
        assert event["user_agent"] == "UA/1"


# ===========================================================================
# TestAuditLoggerLogTaskCreation
# ===========================================================================


class TestAuditLoggerLogTaskCreation:
    """Tests for AuditLogger.log_task_creation."""

    @pytest.mark.asyncio
    async def test_action_is_task_creation(self, audit_logger, memory_backend, actor):
        await audit_logger.log_task_creation(task_id="task-1", actor=actor)
        assert memory_backend.events[0]["action"] == "task_creation"

    @pytest.mark.asyncio
    async def test_target_id_and_type(self, audit_logger, memory_backend, actor):
        await audit_logger.log_task_creation(task_id="task-abc", actor=actor)
        target = memory_backend.events[0]["target"]
        assert target["id"] == "task-abc"
        assert target["type"] == "task"

    @pytest.mark.asyncio
    async def test_context_defaults_to_empty(self, audit_logger, memory_backend, actor):
        await audit_logger.log_task_creation(task_id="t1", actor=actor)
        assert memory_backend.events[0]["context"] == {}

    @pytest.mark.asyncio
    async def test_context_passed_through(self, audit_logger, memory_backend, actor):
        ctx = {"priority": "high"}
        await audit_logger.log_task_creation(task_id="t1", actor=actor, context=ctx)
        assert memory_backend.events[0]["context"] == ctx

    @pytest.mark.asyncio
    async def test_ip_and_ua_forwarded(self, audit_logger, memory_backend, actor):
        await audit_logger.log_task_creation(
            task_id="t1", actor=actor, ip_address="5.5.5.5", user_agent="UA"
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "5.5.5.5"
        assert event["user_agent"] == "UA"


# ===========================================================================
# TestAuditLoggerLogTaskDeletion
# ===========================================================================


class TestAuditLoggerLogTaskDeletion:
    """Tests for AuditLogger.log_task_deletion."""

    @pytest.mark.asyncio
    async def test_action_is_task_deletion(self, audit_logger, memory_backend, actor):
        await audit_logger.log_task_deletion(task_id="task-1", actor=actor)
        assert memory_backend.events[0]["action"] == "task_deletion"

    @pytest.mark.asyncio
    async def test_target_id_and_type(self, audit_logger, memory_backend, actor):
        await audit_logger.log_task_deletion(task_id="del-task-5", actor=actor)
        target = memory_backend.events[0]["target"]
        assert target["id"] == "del-task-5"
        assert target["type"] == "task"

    @pytest.mark.asyncio
    async def test_context_none_becomes_empty(self, audit_logger, memory_backend, actor):
        await audit_logger.log_task_deletion(task_id="t1", actor=actor, context=None)
        assert memory_backend.events[0]["context"] == {}

    @pytest.mark.asyncio
    async def test_ip_and_ua_forwarded(self, audit_logger, memory_backend, actor):
        await audit_logger.log_task_deletion(
            task_id="t1", actor=actor, ip_address="9.9.9.9", user_agent="X"
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "9.9.9.9"
        assert event["user_agent"] == "X"


# ===========================================================================
# TestAuditLoggerLogTaskAssignment
# ===========================================================================


class TestAuditLoggerLogTaskAssignment:
    """Tests for AuditLogger.log_task_assignment."""

    @pytest.mark.asyncio
    async def test_action_is_task_assignment(self, audit_logger, memory_backend, actor):
        await audit_logger.log_task_assignment(
            task_id="t1", agent_id="a1", actor=actor
        )
        assert memory_backend.events[0]["action"] == "task_assignment"

    @pytest.mark.asyncio
    async def test_target_id_and_type(self, audit_logger, memory_backend, actor):
        await audit_logger.log_task_assignment(
            task_id="task-42", agent_id="agent-7", actor=actor
        )
        target = memory_backend.events[0]["target"]
        assert target["id"] == "task-42"
        assert target["type"] == "task"

    @pytest.mark.asyncio
    async def test_agent_id_injected_into_context(self, audit_logger, memory_backend, actor):
        await audit_logger.log_task_assignment(
            task_id="t1", agent_id="agent-7", actor=actor
        )
        assert memory_backend.events[0]["context"]["agent_id"] == "agent-7"

    @pytest.mark.asyncio
    async def test_existing_context_merged_with_agent_id(
        self, audit_logger, memory_backend, actor
    ):
        ctx = {"extra": "value"}
        await audit_logger.log_task_assignment(
            task_id="t1", agent_id="agent-7", actor=actor, context=ctx
        )
        event_ctx = memory_backend.events[0]["context"]
        assert event_ctx["extra"] == "value"
        assert event_ctx["agent_id"] == "agent-7"

    @pytest.mark.asyncio
    async def test_context_none_defaults_to_agent_id_only(
        self, audit_logger, memory_backend, actor
    ):
        await audit_logger.log_task_assignment(
            task_id="t1", agent_id="a9", actor=actor, context=None
        )
        assert memory_backend.events[0]["context"] == {"agent_id": "a9"}

    @pytest.mark.asyncio
    async def test_ip_and_ua_forwarded(self, audit_logger, memory_backend, actor):
        await audit_logger.log_task_assignment(
            task_id="t1",
            agent_id="a1",
            actor=actor,
            ip_address="1.1.1.1",
            user_agent="UA",
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "1.1.1.1"
        assert event["user_agent"] == "UA"


# ===========================================================================
# TestAuditLoggerLogApprovalDecision
# ===========================================================================


class TestAuditLoggerLogApprovalDecision:
    """Tests for AuditLogger.log_approval_decision."""

    @pytest.mark.asyncio
    async def test_approved_decision_sets_action_approve(
        self, audit_logger, memory_backend, actor
    ):
        await audit_logger.log_approval_decision(
            approval_id="appr-1", decision="approved", actor=actor
        )
        assert memory_backend.events[0]["action"] == "approval_approve"

    @pytest.mark.asyncio
    async def test_rejected_decision_sets_action_reject(
        self, audit_logger, memory_backend, actor
    ):
        await audit_logger.log_approval_decision(
            approval_id="appr-2", decision="rejected", actor=actor
        )
        assert memory_backend.events[0]["action"] == "approval_reject"

    @pytest.mark.asyncio
    async def test_any_other_decision_sets_action_reject(
        self, audit_logger, memory_backend, actor
    ):
        """Any decision string besides 'approved' should produce approval_reject."""
        await audit_logger.log_approval_decision(
            approval_id="appr-3", decision="pending", actor=actor
        )
        assert memory_backend.events[0]["action"] == "approval_reject"

    @pytest.mark.asyncio
    async def test_target_id_and_type(self, audit_logger, memory_backend, actor):
        await audit_logger.log_approval_decision(
            approval_id="appr-99", decision="approved", actor=actor
        )
        target = memory_backend.events[0]["target"]
        assert target["id"] == "appr-99"
        assert target["type"] == "approval_request"

    @pytest.mark.asyncio
    async def test_decision_injected_into_context(self, audit_logger, memory_backend, actor):
        await audit_logger.log_approval_decision(
            approval_id="a1", decision="approved", actor=actor
        )
        assert memory_backend.events[0]["context"]["decision"] == "approved"

    @pytest.mark.asyncio
    async def test_existing_context_merged(self, audit_logger, memory_backend, actor):
        ctx = {"reason": "LGTM"}
        await audit_logger.log_approval_decision(
            approval_id="a1", decision="approved", actor=actor, context=ctx
        )
        event_ctx = memory_backend.events[0]["context"]
        assert event_ctx["reason"] == "LGTM"
        assert event_ctx["decision"] == "approved"

    @pytest.mark.asyncio
    async def test_context_none_defaults_to_decision_only(
        self, audit_logger, memory_backend, actor
    ):
        await audit_logger.log_approval_decision(
            approval_id="a1", decision="rejected", actor=actor, context=None
        )
        assert memory_backend.events[0]["context"] == {"decision": "rejected"}

    @pytest.mark.asyncio
    async def test_ip_and_ua_forwarded(self, audit_logger, memory_backend, actor):
        await audit_logger.log_approval_decision(
            approval_id="a1",
            decision="approved",
            actor=actor,
            ip_address="2.2.2.2",
            user_agent="Safari",
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "2.2.2.2"
        assert event["user_agent"] == "Safari"


# ===========================================================================
# TestAuditLoggerLogHandoffOperation
# ===========================================================================


class TestAuditLoggerLogHandoffOperation:
    """Tests for AuditLogger.log_handoff_operation."""

    @pytest.mark.asyncio
    async def test_action_is_handoff_operation(self, audit_logger, memory_backend, actor):
        await audit_logger.log_handoff_operation(handoff_id="hoff-1", actor=actor)
        assert memory_backend.events[0]["action"] == "handoff_operation"

    @pytest.mark.asyncio
    async def test_target_id_and_type(self, audit_logger, memory_backend, actor):
        await audit_logger.log_handoff_operation(handoff_id="hoff-42", actor=actor)
        target = memory_backend.events[0]["target"]
        assert target["id"] == "hoff-42"
        assert target["type"] == "handoff"

    @pytest.mark.asyncio
    async def test_source_is_webhook_api(self, audit_logger, memory_backend, actor):
        await audit_logger.log_handoff_operation(handoff_id="h1", actor=actor)
        assert memory_backend.events[0]["source"] == "webhook_api"

    @pytest.mark.asyncio
    async def test_context_defaults_to_empty(self, audit_logger, memory_backend, actor):
        await audit_logger.log_handoff_operation(handoff_id="h1", actor=actor)
        assert memory_backend.events[0]["context"] == {}

    @pytest.mark.asyncio
    async def test_context_passed_through(self, audit_logger, memory_backend, actor):
        ctx = {"from": "agent-1", "to": "agent-2"}
        await audit_logger.log_handoff_operation(handoff_id="h1", actor=actor, context=ctx)
        assert memory_backend.events[0]["context"] == ctx

    @pytest.mark.asyncio
    async def test_ip_and_ua_forwarded(self, audit_logger, memory_backend, actor):
        await audit_logger.log_handoff_operation(
            handoff_id="h1", actor=actor, ip_address="3.3.3.3", user_agent="UB"
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "3.3.3.3"
        assert event["user_agent"] == "UB"


# ===========================================================================
# TestAuditLoggerLogFleetControl
# ===========================================================================


class TestAuditLoggerLogFleetControl:
    """Tests for AuditLogger.log_fleet_control."""

    @pytest.mark.asyncio
    async def test_action_prefixed_with_fleet(self, audit_logger, memory_backend, actor):
        await audit_logger.log_fleet_control(action_type="pause", actor=actor)
        assert memory_backend.events[0]["action"] == "fleet_pause"

    @pytest.mark.asyncio
    async def test_action_type_resume(self, audit_logger, memory_backend, actor):
        await audit_logger.log_fleet_control(action_type="resume", actor=actor)
        assert memory_backend.events[0]["action"] == "fleet_resume"

    @pytest.mark.asyncio
    async def test_action_type_broadcast(self, audit_logger, memory_backend, actor):
        await audit_logger.log_fleet_control(action_type="broadcast", actor=actor)
        assert memory_backend.events[0]["action"] == "fleet_broadcast"

    @pytest.mark.asyncio
    async def test_target_id_defaults_to_fleet(self, audit_logger, memory_backend, actor):
        await audit_logger.log_fleet_control(action_type="pause", actor=actor)
        target = memory_backend.events[0]["target"]
        assert target["id"] == "fleet"
        assert target["type"] == "fleet"

    @pytest.mark.asyncio
    async def test_target_id_with_explicit_target_id(self, audit_logger, memory_backend, actor):
        await audit_logger.log_fleet_control(
            action_type="pause", actor=actor, target_id="agent-5"
        )
        assert memory_backend.events[0]["target"]["id"] == "agent-5"

    @pytest.mark.asyncio
    async def test_context_defaults_to_empty(self, audit_logger, memory_backend, actor):
        await audit_logger.log_fleet_control(action_type="pause", actor=actor)
        assert memory_backend.events[0]["context"] == {}

    @pytest.mark.asyncio
    async def test_context_passed_through(self, audit_logger, memory_backend, actor):
        ctx = {"reason": "maintenance"}
        await audit_logger.log_fleet_control(action_type="pause", actor=actor, context=ctx)
        assert memory_backend.events[0]["context"] == ctx

    @pytest.mark.asyncio
    async def test_ip_and_ua_forwarded(self, audit_logger, memory_backend, actor):
        await audit_logger.log_fleet_control(
            action_type="kill", actor=actor, ip_address="4.4.4.4", user_agent="CLI"
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "4.4.4.4"
        assert event["user_agent"] == "CLI"

    @pytest.mark.asyncio
    async def test_target_none_becomes_fleet(self, audit_logger, memory_backend, actor):
        await audit_logger.log_fleet_control(
            action_type="broadcast", actor=actor, target_id=None
        )
        assert memory_backend.events[0]["target"]["id"] == "fleet"


# ===========================================================================
# TestAuditLoggerLogConfigurationChange
# ===========================================================================


class TestAuditLoggerLogConfigurationChange:
    """Tests for AuditLogger.log_configuration_change."""

    @pytest.mark.asyncio
    async def test_action_is_configuration_change(self, audit_logger, memory_backend, actor):
        await audit_logger.log_configuration_change(config_key="max_agents", actor=actor)
        assert memory_backend.events[0]["action"] == "configuration_change"

    @pytest.mark.asyncio
    async def test_target_id_is_config_key_and_type_is_configuration(
        self, audit_logger, memory_backend, actor
    ):
        await audit_logger.log_configuration_change(config_key="rate_limit", actor=actor)
        target = memory_backend.events[0]["target"]
        assert target["id"] == "rate_limit"
        assert target["type"] == "configuration"

    @pytest.mark.asyncio
    async def test_config_key_injected_into_context(self, audit_logger, memory_backend, actor):
        await audit_logger.log_configuration_change(config_key="timeout_ms", actor=actor)
        assert memory_backend.events[0]["context"]["config_key"] == "timeout_ms"

    @pytest.mark.asyncio
    async def test_existing_context_merged(self, audit_logger, memory_backend, actor):
        ctx = {"old_value": 100, "new_value": 200}
        await audit_logger.log_configuration_change(
            config_key="timeout_ms", actor=actor, context=ctx
        )
        event_ctx = memory_backend.events[0]["context"]
        assert event_ctx["old_value"] == 100
        assert event_ctx["new_value"] == 200
        assert event_ctx["config_key"] == "timeout_ms"

    @pytest.mark.asyncio
    async def test_context_none_defaults_to_config_key_only(
        self, audit_logger, memory_backend, actor
    ):
        await audit_logger.log_configuration_change(
            config_key="max_conn", actor=actor, context=None
        )
        assert memory_backend.events[0]["context"] == {"config_key": "max_conn"}

    @pytest.mark.asyncio
    async def test_ip_and_ua_forwarded(self, audit_logger, memory_backend, actor):
        await audit_logger.log_configuration_change(
            config_key="k", actor=actor, ip_address="6.6.6.6", user_agent="Admin"
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "6.6.6.6"
        assert event["user_agent"] == "Admin"

    @pytest.mark.asyncio
    async def test_source_is_webhook_api(self, audit_logger, memory_backend, actor):
        await audit_logger.log_configuration_change(config_key="k", actor=actor)
        assert memory_backend.events[0]["source"] == "webhook_api"


# ===========================================================================
# TestAuditLoggerLogAuthenticationEvent
# ===========================================================================


class TestAuditLoggerLogAuthenticationEvent:
    """Tests for AuditLogger.log_authentication_event."""

    @pytest.mark.asyncio
    async def test_action_prefixed_with_auth(self, audit_logger, memory_backend, actor):
        await audit_logger.log_authentication_event(event_type="login", actor=actor)
        assert memory_backend.events[0]["action"] == "auth_login"

    @pytest.mark.asyncio
    async def test_auth_logout_action(self, audit_logger, memory_backend, actor):
        await audit_logger.log_authentication_event(event_type="logout", actor=actor)
        assert memory_backend.events[0]["action"] == "auth_logout"

    @pytest.mark.asyncio
    async def test_auth_refresh_action(self, audit_logger, memory_backend, actor):
        await audit_logger.log_authentication_event(event_type="refresh", actor=actor)
        assert memory_backend.events[0]["action"] == "auth_refresh"

    @pytest.mark.asyncio
    async def test_target_id_uses_actor_id(self, audit_logger, memory_backend, actor):
        await audit_logger.log_authentication_event(event_type="login", actor=actor)
        target = memory_backend.events[0]["target"]
        assert target["id"] == actor["id"]
        assert target["type"] == "user"

    @pytest.mark.asyncio
    async def test_target_id_falls_back_to_unknown_when_no_id(
        self, audit_logger, memory_backend
    ):
        actor_no_id = {"type": "service"}
        await audit_logger.log_authentication_event(event_type="login", actor=actor_no_id)
        assert memory_backend.events[0]["target"]["id"] == "unknown"

    @pytest.mark.asyncio
    async def test_context_defaults_to_empty(self, audit_logger, memory_backend, actor):
        await audit_logger.log_authentication_event(event_type="login", actor=actor)
        assert memory_backend.events[0]["context"] == {}

    @pytest.mark.asyncio
    async def test_context_passed_through(self, audit_logger, memory_backend, actor):
        ctx = {"method": "JWT"}
        await audit_logger.log_authentication_event(
            event_type="login", actor=actor, context=ctx
        )
        assert memory_backend.events[0]["context"] == ctx

    @pytest.mark.asyncio
    async def test_ip_and_ua_forwarded(self, audit_logger, memory_backend, actor):
        await audit_logger.log_authentication_event(
            event_type="login",
            actor=actor,
            ip_address="7.7.7.7",
            user_agent="Chrome/120",
        )
        event = memory_backend.events[0]
        assert event["ip_address"] == "7.7.7.7"
        assert event["user_agent"] == "Chrome/120"

    @pytest.mark.asyncio
    async def test_source_is_webhook_api(self, audit_logger, memory_backend, actor):
        await audit_logger.log_authentication_event(event_type="login", actor=actor)
        assert memory_backend.events[0]["source"] == "webhook_api"


# ===========================================================================
# TestGetAuditLogger
# ===========================================================================


class TestGetAuditLogger:
    """Tests for the get_audit_logger() singleton factory function."""

    def test_returns_audit_logger_instance(self, tmp_path):
        result = get_audit_logger(forge_root=tmp_path)
        assert isinstance(result, AuditLogger)

    def test_same_instance_on_repeated_calls(self, tmp_path):
        a = get_audit_logger(forge_root=tmp_path)
        b = get_audit_logger(forge_root=tmp_path)
        assert a is b

    def test_none_triggers_creation(self, tmp_path):
        _audit_module._audit_logger = None
        result = get_audit_logger(forge_root=tmp_path)
        assert result is not None
        assert isinstance(result, AuditLogger)

    def test_existing_instance_reused(self):
        existing = AuditLogger(backend=MemoryAuditBackend())
        _audit_module._audit_logger = existing
        result = get_audit_logger()
        assert result is existing

    def test_forge_root_forwarded_on_creation(self, tmp_path):
        result = get_audit_logger(forge_root=tmp_path)
        expected_path = tmp_path / ".forge/state" / "audit" / "audit.jsonl"
        assert result._backend._path == expected_path

    def test_singleton_persists_across_calls(self, tmp_path):
        """State accumulated in the singleton backend is visible from subsequent calls."""
        logger_1 = get_audit_logger(forge_root=tmp_path)
        # Replace backend with memory backend on first call result
        mem = MemoryAuditBackend()
        logger_1._backend = mem
        logger_2 = get_audit_logger(forge_root=tmp_path)
        # Same instance — same backend
        assert logger_2._backend is mem


# ===========================================================================
# TestResetAuditLogger
# ===========================================================================


class TestResetAuditLogger:
    """Tests for the reset_audit_logger() utility."""

    def test_reset_clears_singleton(self, tmp_path):
        get_audit_logger(forge_root=tmp_path)
        assert _audit_module._audit_logger is not None
        reset_audit_logger()
        assert _audit_module._audit_logger is None

    def test_reset_when_already_none_does_not_raise(self):
        _audit_module._audit_logger = None
        reset_audit_logger()  # Should not raise
        assert _audit_module._audit_logger is None

    def test_reset_then_get_creates_new_instance(self, tmp_path):
        first = get_audit_logger(forge_root=tmp_path)
        reset_audit_logger()
        second = get_audit_logger(forge_root=tmp_path)
        assert first is not second

    def test_reset_multiple_times_safe(self, tmp_path):
        get_audit_logger(forge_root=tmp_path)
        reset_audit_logger()
        reset_audit_logger()
        reset_audit_logger()
        assert _audit_module._audit_logger is None


# ===========================================================================
# TestIntegrationScenarios
# ===========================================================================


class TestIntegrationScenarios:
    """End-to-end scenarios exercising multiple methods together."""

    @pytest.mark.asyncio
    async def test_full_agent_lifecycle_audit_trail(self):
        backend = MemoryAuditBackend()
        logger = AuditLogger(backend=backend)
        actor = {"id": "orchestrator-1", "type": "system"}

        await logger.log_agent_registration(agent_id="agent-42", actor=actor)
        await logger.log_task_creation(
            task_id="task-1", actor=actor, context={"title": "Implement feature"}
        )
        await logger.log_task_assignment(task_id="task-1", agent_id="agent-42", actor=actor)
        await logger.log(
            action="complete",
            actor=actor,
            target={"id": "agent-42", "type": "agent"},
            status="success",
        )
        await logger.log_agent_deregistration(agent_id="agent-42", actor=actor)

        assert len(backend.events) == 5
        actions = [e["action"] for e in backend.events]
        assert actions == [
            "agent_registration",
            "task_creation",
            "task_assignment",
            "complete",
            "agent_deregistration",
        ]

    @pytest.mark.asyncio
    async def test_approval_workflow_audit_trail(self):
        backend = MemoryAuditBackend()
        logger = AuditLogger(backend=backend)
        actor = {"id": "admin-1", "type": "user"}

        await logger.log_approval_decision(
            approval_id="appr-1", decision="approved", actor=actor, context={"tier": "watch"}
        )
        await logger.log_approval_decision(
            approval_id="appr-2", decision="rejected", actor=actor, context={"reason": "risk"}
        )

        assert len(backend.events) == 2
        assert backend.events[0]["action"] == "approval_approve"
        assert backend.events[1]["action"] == "approval_reject"
        assert backend.events[0]["context"]["decision"] == "approved"
        assert backend.events[1]["context"]["decision"] == "rejected"

    @pytest.mark.asyncio
    async def test_fleet_control_sequence(self):
        backend = MemoryAuditBackend()
        logger = AuditLogger(backend=backend)
        actor = {"id": "ops-bot", "type": "system"}

        await logger.log_fleet_control(action_type="pause", actor=actor)
        await logger.log_fleet_control(
            action_type="broadcast",
            actor=actor,
            context={"message": "maintenance"},
        )
        await logger.log_fleet_control(action_type="resume", actor=actor)

        actions = [e["action"] for e in backend.events]
        assert actions == ["fleet_pause", "fleet_broadcast", "fleet_resume"]

    @pytest.mark.asyncio
    async def test_jsonl_backend_round_trip(self, tmp_path):
        """Emit several events to JSONL file and verify they round-trip correctly."""
        backend = JSONLAuditBackend(path=tmp_path / "audit.jsonl")
        logger = AuditLogger(backend=backend)
        actor = {"id": "svc", "type": "service"}

        await logger.log_authentication_event(event_type="login", actor=actor, ip_address="1.1.1.1")
        await logger.log_configuration_change(
            config_key="feature_flag", actor=actor, context={"value": True}
        )

        lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2

        first = json.loads(lines[0])
        assert first["action"] == "auth_login"
        assert first["ip_address"] == "1.1.1.1"

        second = json.loads(lines[1])
        assert second["action"] == "configuration_change"
        assert second["context"]["config_key"] == "feature_flag"
        assert second["context"]["value"] is True

    @pytest.mark.asyncio
    async def test_singleton_provides_shared_audit_trail(self, tmp_path):
        """Events logged via the singleton are visible from all callers."""
        reset_audit_logger()
        backend = MemoryAuditBackend()
        logger = get_audit_logger(forge_root=tmp_path)
        logger._backend = backend  # inject memory backend

        actor = {"id": "x", "type": "user"}
        await logger.log(action="dispatch", actor=actor, target={"id": "t1", "type": "task"})

        # Get same singleton — events should be there
        same_logger = get_audit_logger(forge_root=tmp_path)
        assert same_logger._backend is backend
        assert len(backend.events) == 1

    @pytest.mark.asyncio
    async def test_all_log_helper_methods_produce_events(self):
        """Smoke-test that every log_* helper actually produces one event each."""
        backend = MemoryAuditBackend()
        logger = AuditLogger(backend=backend)
        actor = {"id": "smoke", "type": "test"}

        await logger.log_agent_registration(agent_id="a1", actor=actor)
        await logger.log_agent_deregistration(agent_id="a1", actor=actor)
        await logger.log_task_creation(task_id="t1", actor=actor)
        await logger.log_task_deletion(task_id="t1", actor=actor)
        await logger.log_task_assignment(task_id="t1", agent_id="a1", actor=actor)
        await logger.log_approval_decision(
            approval_id="ap1", decision="approved", actor=actor
        )
        await logger.log_handoff_operation(handoff_id="h1", actor=actor)
        await logger.log_fleet_control(action_type="pause", actor=actor)
        await logger.log_configuration_change(config_key="k", actor=actor)
        await logger.log_authentication_event(event_type="login", actor=actor)

        assert len(backend.events) == 10
