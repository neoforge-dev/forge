"""Comprehensive tests for forge_harness.shared_contracts (CP-3005).

Covers:
- TaskContract, AgentContract, NodeContract Pydantic models
- LeaseContract and all sub-models
- StatusSnapshot and TaskStatsContract
- normalize_task(), normalize_agent(), normalize_node() helpers
- Cross-surface parity: all fields expected by the API are present on contracts

All tests are synchronous (Pydantic is sync). Run with:
    uv run pytest tests/test_shared_contracts.py -v
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from forge_harness.shared_contracts import (
    AgentContract,
    LeaseContract,
    NodeAgentSummary,
    NodeAlertContract,
    NodeCapabilitiesContract,
    NodeContract,
    NodeSpecsContract,
    StatusSnapshot,
    TaskContract,
    TaskStatsContract,
    normalize_agent,
    normalize_node,
    normalize_task,
)
from forge_harness.task_queue import TaskPriority, TaskStatus

# ===========================================================================
# Fixtures — reusable raw dicts
# ===========================================================================


@pytest.fixture()
def minimal_task_raw() -> dict:
    return {"id": "task-001", "subject": "Do something important"}


@pytest.fixture()
def full_lease_dict() -> dict:
    return {
        "owner_node": "nova",
        "owner_agent": "forge:codex",
        "lease_expires_at": "2026-02-21T12:00:00Z",
        "path_lock": "forge-terminal#FT-123",
    }


@pytest.fixture()
def full_task_raw(full_lease_dict) -> dict:
    return {
        "id": "task-999",
        "subject": "Build authentication module",
        "description": "Implement JWT auth",
        "priority": "high",
        "status": "in_progress",
        "required_role": "backend-engineer",
        "claimed_by": "forge:kimi",
        "domain": "codeswiftr-com",
        "project": "interview-simulator",
        "order": 5,
        "lease": full_lease_dict,
        "depends_on": ["task-998"],
        "created_at": "2026-02-20T10:00:00Z",
        "updated_at": "2026-02-20T11:00:00Z",
        "completed_at": None,
    }


@pytest.fixture()
def full_agent_raw() -> dict:
    return {
        "session_id": "abc12345",
        "agent_role": "builder",
        "agent_name": "Builder Agent",
        "domain": "codeswiftr-com",
        "project": "interview-simulator",
        "current_task": "Implementing user authentication",
        "focus_tags": ["auth", "security"],
        "status": "active",
        "progress_pct": 45.5,
        "tasks_completed": 3,
        "tasks_remaining": 4,
        "started_at": "2026-01-18T10:00:00Z",
        "last_activity": "2026-01-18T12:30:00Z",
        "token_usage": 15420,
        "api_calls": 47,
        "pending_approval_id": None,
        "parent_id": None,
        "children": [],
        "source": "tmux",
    }


@pytest.fixture()
def full_node_raw() -> dict:
    return {
        "node_id": "forge:nova",
        "name": "nova",
        "status": "online",
        "agent_count": 2,
        "cpu_load": 34.5,
        "ram_usage": 62.1,
        "last_heartbeat": "2026-02-21T08:00:00Z",
        "latency_ms": 12,
        "agents": [
            {
                "id": "agent-1",
                "session_id": "abc12345",
                "agent_id": "forge:agent-1",
                "name": "Builder",
                "role": "builder",
                "status": "active",
                "project": "interview-simulator",
                "task": "auth module",
                "progress": 50,
                "context_percentage": 40,
            }
        ],
        "load_average": 1.2,
        "disk_usage": 55.0,
        "alerts": [
            {"level": "warning", "message": "High RAM usage", "timestamp": "2026-02-21T07:55:00Z"}
        ],
        "capabilities": {
            "claude": True,
            "codex": False,
            "gemini": True,
            "kimi": True,
            "opencode": False,
            "ollama": False,
            "docker": True,
            "ios_simulator": False,
            "gpu": False,
        },
        "specs": {"cpu": "Apple M4 Pro", "ram_gb": 64, "os": "macOS 15.3", "cores": 14},
        "is_fresh": True,
        "age_seconds": 5.3,
        "received_at": "2026-02-21T08:00:05Z",
    }


# ===========================================================================
# TaskContract tests
# ===========================================================================


class TestNormalizeTask:
    """Tests for normalize_task() helper and TaskContract model."""

    def test_normalize_task_minimal(self, minimal_task_raw):
        """Normalize with just id and subject — all other fields use safe defaults."""
        task = normalize_task(minimal_task_raw)

        assert isinstance(task, TaskContract)
        assert task.id == "task-001"
        assert task.subject == "Do something important"
        assert task.description == ""
        assert task.priority == TaskPriority.MEDIUM.value
        assert task.status == TaskStatus.PENDING.value
        assert task.required_role is None
        assert task.claimed_by is None
        assert task.domain is None
        assert task.project is None
        assert task.order == 0
        assert task.lease is None
        assert task.depends_on == []
        assert task.completed_at is None

    def test_normalize_task_with_lease(self, full_task_raw, full_lease_dict):
        """Normalize with a full lease dict — LeaseContract is populated correctly."""
        task = normalize_task(full_task_raw)

        assert task.lease is not None
        assert isinstance(task.lease, LeaseContract)
        assert task.lease.owner_node == full_lease_dict["owner_node"]
        assert task.lease.owner_agent == full_lease_dict["owner_agent"]
        assert task.lease.lease_expires_at == full_lease_dict["lease_expires_at"]
        assert task.lease.path_lock == full_lease_dict["path_lock"]

    def test_normalize_task_title_fallback(self):
        """When 'subject' is absent, 'title' is used as the subject."""
        raw = {"id": "task-t1", "title": "From title field"}
        task = normalize_task(raw)
        assert task.subject == "From title field"

    def test_normalize_task_title_fallback_subject_takes_precedence(self):
        """When both 'subject' and 'title' are present, 'subject' wins."""
        raw = {"id": "task-t2", "subject": "Subject field", "title": "Title field"}
        task = normalize_task(raw)
        assert task.subject == "Subject field"

    def test_normalize_task_claimed_by_fallback(self):
        """'assigned_agent' maps to claimed_by when claimed_by is absent."""
        raw = {"id": "task-ca", "subject": "Claimed task", "assigned_agent": "forge:gemini"}
        task = normalize_task(raw)
        assert task.claimed_by == "forge:gemini"

    def test_normalize_task_claimed_by_takes_precedence_over_assigned_agent(self):
        """'claimed_by' wins over 'assigned_agent' when both are present."""
        raw = {
            "id": "task-cb",
            "subject": "Task",
            "claimed_by": "forge:kimi",
            "assigned_agent": "forge:gemini",
        }
        task = normalize_task(raw)
        assert task.claimed_by == "forge:kimi"

    def test_normalize_task_priority_enum_values(self):
        """All TaskPriority enum values are accepted and preserved."""
        for priority in TaskPriority:
            raw = {"id": f"task-{priority.value}", "subject": "Test", "priority": priority.value}
            task = normalize_task(raw)
            assert task.priority == priority.value, (
                f"Priority '{priority.value}' was not preserved"
            )

    def test_normalize_task_status_enum_values(self):
        """All TaskStatus enum values are accepted and preserved."""
        for status in TaskStatus:
            raw = {"id": f"task-{status.value}", "subject": "Test", "status": status.value}
            task = normalize_task(raw)
            assert task.status == status.value, (
                f"Status '{status.value}' was not preserved"
            )

    def test_normalize_task_empty_lease_dict_becomes_none(self):
        """An empty dict for 'lease' is treated as no lease (None)."""
        raw = {"id": "task-el", "subject": "No lease", "lease": {}}
        task = normalize_task(raw)
        assert task.lease is None

    def test_normalize_task_none_lease_becomes_none(self):
        """An explicit None for 'lease' stays None."""
        raw = {"id": "task-nl", "subject": "Explicit None", "lease": None}
        task = normalize_task(raw)
        assert task.lease is None

    def test_normalize_task_missing_fields_have_defaults(self):
        """A raw dict with only id returns a valid TaskContract with all safe defaults."""
        raw = {"id": "bare-task"}
        task = normalize_task(raw)

        assert task.id == "bare-task"
        assert task.subject == ""
        assert task.description == ""
        assert task.order == 0
        assert task.depends_on == []
        assert task.lease is None
        assert task.claimed_by is None
        assert task.required_role is None

    def test_normalize_task_updated_at_falls_back_to_created_at(self):
        """When 'updated_at' is absent, it falls back to 'created_at'."""
        raw = {"id": "task-ub", "subject": "Fallback", "created_at": "2026-01-01T00:00:00Z"}
        task = normalize_task(raw)
        assert task.updated_at == "2026-01-01T00:00:00Z"
        assert task.created_at == "2026-01-01T00:00:00Z"

    def test_normalize_task_depends_on_list_preserved(self):
        """'depends_on' list is preserved verbatim."""
        raw = {"id": "task-dep", "subject": "Dependent", "depends_on": ["a", "b", "c"]}
        task = normalize_task(raw)
        assert task.depends_on == ["a", "b", "c"]

    def test_normalize_task_order_coerced_to_int(self):
        """'order' field is coerced to int even when provided as a string."""
        raw = {"id": "task-ord", "subject": "Ordered", "order": "7"}
        task = normalize_task(raw)
        assert task.order == 7
        assert isinstance(task.order, int)

    def test_normalize_task_full_round_trip(self, full_task_raw):
        """Normalize a complete raw dict — all fields should be faithfully mapped."""
        task = normalize_task(full_task_raw)

        assert task.id == "task-999"
        assert task.subject == "Build authentication module"
        assert task.description == "Implement JWT auth"
        assert task.priority == "high"
        assert task.status == "in_progress"
        assert task.required_role == "backend-engineer"
        assert task.claimed_by == "forge:kimi"
        assert task.domain == "codeswiftr-com"
        assert task.project == "interview-simulator"
        assert task.order == 5
        assert task.depends_on == ["task-998"]


# ===========================================================================
# AgentContract tests
# ===========================================================================


class TestNormalizeAgent:
    """Tests for normalize_agent() helper and AgentContract model."""

    def test_normalize_agent_cli_style_fields(self):
        """CLI-style 'role'/'name'/'id' map to canonical 'agent_role'/'agent_name'/'session_id'."""
        raw = {
            "id": "ses-abc",
            "role": "backend-engineer",
            "name": "My Engineer",
            "status": "active",
        }
        agent = normalize_agent(raw)

        assert agent.session_id == "ses-abc"
        assert agent.agent_role == "backend-engineer"
        assert agent.agent_name == "My Engineer"

    def test_normalize_agent_api_style_fields(self, full_agent_raw):
        """API-style 'agent_role'/'agent_name'/'session_id' pass through without transformation."""
        agent = normalize_agent(full_agent_raw)

        assert agent.session_id == "abc12345"
        assert agent.agent_role == "builder"
        assert agent.agent_name == "Builder Agent"

    def test_normalize_agent_status_error_maps_to_failed(self):
        """'error' status is canonicalized to 'failed'."""
        agent = normalize_agent({"role": "qa", "status": "error"})
        assert agent.status == "failed"

    def test_normalize_agent_status_unknown_maps_to_idle(self):
        """'unknown' status is canonicalized to 'idle'."""
        agent = normalize_agent({"role": "qa", "status": "unknown"})
        assert agent.status == "idle"

    def test_normalize_agent_status_active_preserved(self):
        """'active' status passes through unchanged."""
        agent = normalize_agent({"role": "qa", "status": "active"})
        assert agent.status == "active"

    def test_normalize_agent_status_normalization_all_mappings(self):
        """All status mappings in _AGENT_STATUS_CANONICAL are correct."""
        expected = {
            "active": "active",
            "waiting": "waiting",
            "idle": "idle",
            "completed": "completed",
            "paused": "paused",
            "failed": "failed",
            "error": "failed",
            "unknown": "idle",
        }
        for raw_status, canonical in expected.items():
            agent = normalize_agent({"role": "qa", "status": raw_status})
            assert agent.status == canonical, (
                f"Status '{raw_status}' should map to '{canonical}', got '{agent.status}'"
            )

    def test_normalize_agent_status_unrecognized_defaults_to_idle(self):
        """An unrecognized status string defaults to 'idle'."""
        agent = normalize_agent({"role": "qa", "status": "mystery-status"})
        assert agent.status == "idle"

    def test_normalize_agent_session_id_from_session_id(self):
        """session_id is used directly when present."""
        raw = {"session_id": "ses-explicit", "role": "qa"}
        agent = normalize_agent(raw)
        assert agent.session_id == "ses-explicit"

    def test_normalize_agent_session_id_fallback_to_id(self):
        """Falls back to 'id' when 'session_id' is absent."""
        raw = {"id": "id-fallback", "role": "qa"}
        agent = normalize_agent(raw)
        assert agent.session_id == "id-fallback"

    def test_normalize_agent_session_id_fallback_to_tmux_session(self):
        """Falls back to 'tmux_session' when both 'session_id' and 'id' are absent."""
        raw = {"tmux_session": "tmux-win:0", "role": "qa"}
        agent = normalize_agent(raw)
        assert agent.session_id == "tmux-win:0"

    def test_normalize_agent_session_id_generated_from_role(self):
        """When no id fields are present, generates session_id from agent_role."""
        raw = {"agent_role": "reviewer"}
        agent = normalize_agent(raw)
        assert agent.session_id == "agent-reviewer"

    def test_normalize_agent_session_id_generated_from_role_cli_style(self):
        """When no id fields are present, generates session_id from role (CLI style)."""
        raw = {"role": "deployer"}
        agent = normalize_agent(raw)
        assert agent.session_id == "agent-deployer"

    def test_normalize_agent_session_id_generated_fallback_to_unknown(self):
        """When no id or role fields are present, generates session_id as 'agent-unknown'."""
        agent = normalize_agent({})
        assert agent.session_id == "agent-unknown"

    def test_normalize_agent_progress_field_mapping_from_int(self):
        """CLI-style integer 'progress' maps to float 'progress_pct'."""
        raw = {"role": "qa", "progress": 75}
        agent = normalize_agent(raw)
        assert agent.progress_pct == 75.0
        assert isinstance(agent.progress_pct, float)

    def test_normalize_agent_progress_pct_takes_precedence(self):
        """When both 'progress_pct' and 'progress' are present, 'progress_pct' wins."""
        raw = {"role": "qa", "progress_pct": 60.0, "progress": 30}
        agent = normalize_agent(raw)
        assert agent.progress_pct == 60.0

    def test_normalize_agent_progress_pct_float_preserved(self):
        """Float 'progress_pct' from API style is preserved as float."""
        raw = {"session_id": "s1", "agent_role": "builder", "progress_pct": 45.5}
        agent = normalize_agent(raw)
        assert agent.progress_pct == 45.5

    def test_normalize_agent_agent_name_fallback_to_role(self):
        """When neither 'agent_name' nor 'name' is present, role is used as name."""
        raw = {"role": "tester"}
        agent = normalize_agent(raw)
        assert agent.agent_name == "tester"

    def test_normalize_agent_current_task_from_task_field(self):
        """CLI-style 'task' field maps to 'current_task'."""
        raw = {"role": "qa", "task": "Running lint"}
        agent = normalize_agent(raw)
        assert agent.current_task == "Running lint"

    def test_normalize_agent_tasks_completed_coerced_to_int(self):
        """'tasks_completed' is coerced to int."""
        raw = {"role": "qa", "tasks_completed": "5"}
        agent = normalize_agent(raw)
        assert agent.tasks_completed == 5
        assert isinstance(agent.tasks_completed, int)

    def test_normalize_agent_token_usage_dict_is_summed(self):
        """Dashboard-style token_usage dict should be normalized to a numeric total."""
        raw = {"role": "qa", "token_usage": {"input": 120, "output": "80", "ignored": "x"}}
        agent = normalize_agent(raw)
        assert agent.token_usage == 200

    def test_normalize_agent_handles_nullable_dashboard_fields(self):
        """Dashboard AgentState-style nullable fields should normalize without validation errors."""
        raw = {
            "id": "agent-1",
            "role": "qa",
            "project": None,
            "task": None,
            "last_activity": datetime(2026, 2, 21, 12, 0, tzinfo=UTC),
            "focus_tags": None,
            "children": None,
            "token_usage": {"input": 10, "output": 20},
        }
        agent = normalize_agent(raw)

        assert agent.project == ""
        assert agent.current_task == ""
        assert agent.last_activity == "2026-02-21T12:00:00+00:00"
        assert agent.focus_tags == []
        assert agent.children == []
        assert agent.token_usage == 30

    def test_normalize_agent_full_round_trip(self, full_agent_raw):
        """All fields from a complete API-style agent dict are faithfully mapped."""
        agent = normalize_agent(full_agent_raw)

        assert agent.session_id == "abc12345"
        assert agent.agent_role == "builder"
        assert agent.agent_name == "Builder Agent"
        assert agent.domain == "codeswiftr-com"
        assert agent.project == "interview-simulator"
        assert agent.current_task == "Implementing user authentication"
        assert agent.focus_tags == ["auth", "security"]
        assert agent.status == "active"
        assert agent.progress_pct == 45.5
        assert agent.tasks_completed == 3
        assert agent.tasks_remaining == 4
        assert agent.token_usage == 15420
        assert agent.api_calls == 47
        assert agent.pending_approval_id is None
        assert agent.source == "tmux"


# ===========================================================================
# NodeContract tests
# ===========================================================================


class TestNormalizeNode:
    """Tests for normalize_node() helper and NodeContract model."""

    def test_normalize_node_minimal(self):
        """Normalize with just node_id and name — all other fields use safe defaults."""
        raw = {"node_id": "forge:nova", "name": "nova"}
        node = normalize_node(raw)

        assert isinstance(node, NodeContract)
        assert node.node_id == "forge:nova"
        assert node.name == "nova"
        assert node.status == "offline"
        assert node.agent_count == 0
        assert node.cpu_load == 0.0
        assert node.ram_usage == 0.0
        assert node.latency_ms == 0
        assert node.agents == []
        assert node.alerts == []
        assert node.is_fresh is False
        assert node.age_seconds == 0.0
        assert node.received_at is None

    def test_normalize_node_with_agents(self, full_node_raw):
        """Agents list within a node is normalized into NodeAgentSummary instances."""
        node = normalize_node(full_node_raw)

        assert len(node.agents) == 1
        agent = node.agents[0]
        assert isinstance(agent, NodeAgentSummary)
        assert agent.id == "agent-1"
        assert agent.session_id == "abc12345"
        assert agent.agent_id == "forge:agent-1"
        assert agent.name == "Builder"
        assert agent.role == "builder"
        assert agent.status == "active"
        assert agent.project == "interview-simulator"
        assert agent.task == "auth module"
        assert agent.progress == 50
        assert agent.context_percentage == 40

    def test_normalize_node_agents_non_dict_entries_skipped(self):
        """Non-dict entries in the 'agents' list are silently skipped."""
        raw = {
            "node_id": "n1",
            "name": "n1",
            "agents": [
                {"id": "a1", "name": "Good"},
                "invalid-string",
                None,
                42,
            ],
        }
        node = normalize_node(raw)
        assert len(node.agents) == 1
        assert node.agents[0].id == "a1"

    def test_normalize_node_with_capabilities(self, full_node_raw):
        """Capabilities dict is mapped to boolean NodeCapabilitiesContract fields."""
        node = normalize_node(full_node_raw)

        assert isinstance(node.capabilities, NodeCapabilitiesContract)
        assert node.capabilities.claude is True
        assert node.capabilities.codex is False
        assert node.capabilities.gemini is True
        assert node.capabilities.kimi is True
        assert node.capabilities.docker is True
        assert node.capabilities.ios_simulator is False
        assert node.capabilities.gpu is False

    def test_normalize_node_capabilities_unknown_keys_ignored(self):
        """Unknown keys in capabilities dict are silently ignored."""
        raw = {
            "node_id": "n1",
            "name": "n1",
            "capabilities": {"claude": True, "unknown_tool": True, "future_tool": False},
        }
        node = normalize_node(raw)
        assert node.capabilities.claude is True

    def test_normalize_node_capabilities_non_dict_becomes_defaults(self):
        """A non-dict 'capabilities' value results in all-False NodeCapabilitiesContract."""
        raw = {"node_id": "n1", "name": "n1", "capabilities": "bad-value"}
        node = normalize_node(raw)
        assert isinstance(node.capabilities, NodeCapabilitiesContract)
        assert node.capabilities.claude is False

    def test_normalize_node_with_specs(self, full_node_raw):
        """Specs dict is mapped to NodeSpecsContract with correct types."""
        node = normalize_node(full_node_raw)

        assert isinstance(node.specs, NodeSpecsContract)
        assert node.specs.cpu == "Apple M4 Pro"
        assert node.specs.ram_gb == 64
        assert isinstance(node.specs.ram_gb, int)
        assert node.specs.os == "macOS 15.3"
        assert node.specs.cores == 14

    def test_normalize_node_specs_non_dict_becomes_defaults(self):
        """A non-dict 'specs' value results in default NodeSpecsContract."""
        raw = {"node_id": "n1", "name": "n1", "specs": None}
        node = normalize_node(raw)
        assert isinstance(node.specs, NodeSpecsContract)
        assert node.specs.cpu == ""
        assert node.specs.ram_gb == 0

    def test_normalize_node_with_alerts(self, full_node_raw):
        """Alerts list is mapped to NodeAlertContract instances."""
        node = normalize_node(full_node_raw)

        assert len(node.alerts) == 1
        alert = node.alerts[0]
        assert isinstance(alert, NodeAlertContract)
        assert alert.level == "warning"
        assert alert.message == "High RAM usage"
        assert alert.timestamp == "2026-02-21T07:55:00Z"

    def test_normalize_node_alerts_non_dict_entries_skipped(self):
        """Non-dict entries in the 'alerts' list are silently skipped."""
        raw = {
            "node_id": "n1",
            "name": "n1",
            "alerts": [
                {"level": "info", "message": "OK"},
                "not-a-dict",
                None,
            ],
        }
        node = normalize_node(raw)
        assert len(node.alerts) == 1
        assert node.alerts[0].level == "info"

    def test_normalize_node_freshness_preserved(self, full_node_raw):
        """is_fresh and age_seconds are preserved from the raw dict."""
        node = normalize_node(full_node_raw)
        assert node.is_fresh is True
        assert node.age_seconds == 5.3

    def test_normalize_node_is_fresh_defaults_to_false(self):
        """is_fresh defaults to False when absent."""
        node = normalize_node({"node_id": "n1", "name": "n1"})
        assert node.is_fresh is False

    def test_normalize_node_full_round_trip(self, full_node_raw):
        """All scalar fields from a complete raw node dict are faithfully mapped."""
        node = normalize_node(full_node_raw)

        assert node.node_id == "forge:nova"
        assert node.name == "nova"
        assert node.status == "online"
        assert node.agent_count == 2
        assert node.cpu_load == 34.5
        assert node.ram_usage == 62.1
        assert node.last_heartbeat == "2026-02-21T08:00:00Z"
        assert node.latency_ms == 12
        assert node.load_average == 1.2
        assert node.disk_usage == 55.0
        assert node.received_at == "2026-02-21T08:00:05Z"


# ===========================================================================
# StatusSnapshot tests
# ===========================================================================


class TestStatusSnapshot:
    """Tests for the StatusSnapshot aggregate model."""

    def test_status_snapshot_empty(self):
        """An empty StatusSnapshot has correct list defaults and a timestamp."""
        snap = StatusSnapshot()

        assert snap.tasks == []
        assert snap.agents == []
        assert snap.nodes == []
        assert isinstance(snap.task_stats, TaskStatsContract)
        assert snap.task_stats.total == 0
        # timestamp is auto-generated as ISO 8601 UTC string
        assert snap.timestamp != ""
        datetime.fromisoformat(snap.timestamp)  # must be valid ISO 8601

    def test_status_snapshot_full(
        self, full_task_raw, full_agent_raw, full_node_raw
    ):
        """StatusSnapshot correctly holds populated tasks, agents, and nodes."""
        task = normalize_task(full_task_raw)
        agent = normalize_agent(full_agent_raw)
        node = normalize_node(full_node_raw)
        stats = TaskStatsContract(pending=1, in_progress=1, total=2)

        snap = StatusSnapshot(
            tasks=[task],
            task_stats=stats,
            agents=[agent],
            nodes=[node],
        )

        assert len(snap.tasks) == 1
        assert snap.tasks[0].id == "task-999"

        assert len(snap.agents) == 1
        assert snap.agents[0].session_id == "abc12345"

        assert len(snap.nodes) == 1
        assert snap.nodes[0].node_id == "forge:nova"

        assert snap.task_stats.pending == 1
        assert snap.task_stats.in_progress == 1
        assert snap.task_stats.total == 2

    def test_status_snapshot_serializable(self, full_task_raw, full_agent_raw, full_node_raw):
        """model_dump() produces a dict that is fully JSON-serializable."""
        snap = StatusSnapshot(
            tasks=[normalize_task(full_task_raw)],
            agents=[normalize_agent(full_agent_raw)],
            nodes=[normalize_node(full_node_raw)],
        )

        dumped = snap.model_dump()

        # Must not raise — all values must be JSON-serializable primitives
        serialized = json.dumps(dumped)
        assert isinstance(serialized, str)
        assert len(serialized) > 0

        # Round-trip: re-parsing should yield valid data
        parsed = json.loads(serialized)
        assert len(parsed["tasks"]) == 1
        assert len(parsed["agents"]) == 1
        assert len(parsed["nodes"]) == 1

    def test_status_snapshot_timestamp_is_utc(self):
        """Auto-generated snapshot timestamp is a valid UTC ISO 8601 string."""
        snap = StatusSnapshot()
        # Should parse without error and represent a recent time
        ts = datetime.fromisoformat(snap.timestamp)
        # Confirm it is timezone-aware (has UTC offset)
        assert ts.tzinfo is not None


# ===========================================================================
# TaskStatsContract tests
# ===========================================================================


class TestTaskStatsContract:
    """Tests for the TaskStatsContract aggregate."""

    def test_task_stats_all_defaults_are_zero(self):
        """All TaskStatsContract fields default to 0."""
        stats = TaskStatsContract()
        assert stats.pending == 0
        assert stats.assigned == 0
        assert stats.in_progress == 0
        assert stats.completed == 0
        assert stats.failed == 0
        assert stats.blocked == 0
        assert stats.total == 0

    def test_task_stats_can_be_set(self):
        """TaskStatsContract accepts all integer values."""
        stats = TaskStatsContract(
            pending=3, assigned=1, in_progress=2, completed=10, failed=1, blocked=0, total=17
        )
        assert stats.total == 17
        assert stats.failed == 1


# ===========================================================================
# Cross-surface parity tests
# ===========================================================================


class TestCrossSurfaceParity:
    """Verify that all API contract fields defined in API_CONTRACTS.md
    are present on the canonical Pydantic contract models.

    These tests guard against contract drift where the API spec is updated
    but the shared contract models are not, or vice versa.
    """

    # Fields defined in command_center/docs/API_CONTRACTS.md for GET /api/agents
    API_AGENT_REQUIRED_FIELDS = {
        "session_id",
        "agent_role",
        "agent_name",
        "domain",
        "project",
        "current_task",
        "focus_tags",
        "status",
        "progress_pct",
        "tasks_completed",
        "tasks_remaining",
        "started_at",
        "last_activity",
        "token_usage",
        "api_calls",
        "pending_approval_id",
    }

    # Fields defined in command_center/docs/API_CONTRACTS.md for GET /api/tasks
    # (inferred from TaskContract shape and normalizer usage)
    API_TASK_REQUIRED_FIELDS = {
        "id",
        "subject",
        "description",
        "priority",
        "status",
        "required_role",
        "claimed_by",
        "domain",
        "project",
        "order",
        "lease",
        "depends_on",
        "created_at",
        "updated_at",
        "completed_at",
    }

    # Fields defined for nodes (from NodeContract shape)
    API_NODE_REQUIRED_FIELDS = {
        "node_id",
        "name",
        "status",
        "agent_count",
        "cpu_load",
        "ram_usage",
        "last_heartbeat",
        "latency_ms",
        "agents",
        "load_average",
        "disk_usage",
        "alerts",
        "capabilities",
        "specs",
        "is_fresh",
        "age_seconds",
        "received_at",
    }

    def test_task_contract_has_all_api_fields(self):
        """TaskContract model fields are a superset of the API-required task fields."""
        model_fields = set(TaskContract.model_fields.keys())
        missing = self.API_TASK_REQUIRED_FIELDS - model_fields
        assert missing == set(), (
            f"TaskContract is missing API-required fields: {missing}. "
            "Update TaskContract or API_CONTRACTS.md to keep them in sync."
        )

    def test_agent_contract_has_all_api_fields(self):
        """AgentContract model fields are a superset of the API-required agent fields."""
        model_fields = set(AgentContract.model_fields.keys())
        missing = self.API_AGENT_REQUIRED_FIELDS - model_fields
        assert missing == set(), (
            f"AgentContract is missing API-required fields: {missing}. "
            "Update AgentContract or API_CONTRACTS.md to keep them in sync."
        )

    def test_node_contract_has_all_api_fields(self):
        """NodeContract model fields are a superset of the API-required node fields."""
        model_fields = set(NodeContract.model_fields.keys())
        missing = self.API_NODE_REQUIRED_FIELDS - model_fields
        assert missing == set(), (
            f"NodeContract is missing API-required fields: {missing}. "
            "Update NodeContract or API_CONTRACTS.md to keep them in sync."
        )

    def test_normalize_task_output_satisfies_api_contract(self):
        """normalize_task() output dict contains every API-required task field."""
        raw = {"id": "t1", "subject": "Parity test"}
        task = normalize_task(raw)
        dumped = task.model_dump()

        for field in self.API_TASK_REQUIRED_FIELDS:
            assert field in dumped, (
                f"normalize_task() output missing API field '{field}'"
            )

    def test_normalize_agent_output_satisfies_api_contract(self):
        """normalize_agent() output dict contains every API-required agent field."""
        raw = {
            "session_id": "ses-parity",
            "agent_role": "builder",
            "agent_name": "Parity Agent",
        }
        agent = normalize_agent(raw)
        dumped = agent.model_dump()

        for field in self.API_AGENT_REQUIRED_FIELDS:
            assert field in dumped, (
                f"normalize_agent() output missing API field '{field}'"
            )

    def test_normalize_node_output_satisfies_api_contract(self):
        """normalize_node() output dict contains every API-required node field."""
        raw = {"node_id": "forge:nova", "name": "nova"}
        node = normalize_node(raw)
        dumped = node.model_dump()

        for field in self.API_NODE_REQUIRED_FIELDS:
            assert field in dumped, (
                f"normalize_node() output missing API field '{field}'"
            )

    def test_status_snapshot_contains_all_surface_collections(self):
        """StatusSnapshot exposes tasks, agents, and nodes — all three surfaces."""
        snap_fields = set(StatusSnapshot.model_fields.keys())
        assert "tasks" in snap_fields
        assert "agents" in snap_fields
        assert "nodes" in snap_fields
        assert "task_stats" in snap_fields
        assert "timestamp" in snap_fields


# ===========================================================================
# LeaseContract standalone tests
# ===========================================================================


class TestLeaseContract:
    """Tests for the LeaseContract sub-model in isolation."""

    def test_lease_contract_required_fields(self):
        """LeaseContract requires all four fields — no defaults exist."""
        lease = LeaseContract(
            owner_node="nova",
            owner_agent="forge:codex",
            lease_expires_at="2026-02-21T12:00:00Z",
            path_lock="forge-terminal#FT-123",
        )

        assert lease.owner_node == "nova"
        assert lease.owner_agent == "forge:codex"
        assert lease.lease_expires_at == "2026-02-21T12:00:00Z"
        assert lease.path_lock == "forge-terminal#FT-123"

    def test_lease_contract_model_dump_is_json_serializable(self):
        """LeaseContract.model_dump() produces a JSON-serializable dict."""
        lease = LeaseContract(
            owner_node="nova",
            owner_agent="forge:codex",
            lease_expires_at="2026-02-21T12:00:00Z",
            path_lock="forge-terminal#FT-123",
        )
        dumped = lease.model_dump(mode="json")
        serialized = json.dumps(dumped)
        parsed = json.loads(serialized)

        assert parsed["owner_node"] == "nova"
        assert parsed["owner_agent"] == "forge:codex"

    def test_lease_contract_embedded_in_task_contract(self):
        """LeaseContract nested inside TaskContract survives model_dump round-trip."""
        task = TaskContract(
            id="t-lease",
            subject="Leased task",
            lease=LeaseContract(
                owner_node="nova",
                owner_agent="forge:gemini",
                lease_expires_at="2026-02-22T00:00:00Z",
                path_lock="repo#BRANCH",
            ),
        )
        dumped = task.model_dump()
        assert dumped["lease"]["owner_node"] == "nova"
        assert dumped["lease"]["path_lock"] == "repo#BRANCH"


# ===========================================================================
# NodeCapabilitiesContract and NodeSpecsContract standalone tests
# ===========================================================================


class TestNodeSubModels:
    """Tests for NodeCapabilitiesContract and NodeSpecsContract in isolation."""

    def test_node_capabilities_all_default_to_false(self):
        """All NodeCapabilitiesContract capabilities default to False."""
        caps = NodeCapabilitiesContract()
        for field_name in NodeCapabilitiesContract.model_fields:
            assert getattr(caps, field_name) is False, (
                f"Capability '{field_name}' should default to False"
            )

    def test_node_capabilities_all_fields_settable(self):
        """Every capability field can be set to True independently."""
        for field_name in NodeCapabilitiesContract.model_fields:
            caps = NodeCapabilitiesContract(**{field_name: True})
            assert getattr(caps, field_name) is True

    def test_node_specs_all_default_to_empty(self):
        """NodeSpecsContract fields default to empty string or 0."""
        specs = NodeSpecsContract()
        assert specs.cpu == ""
        assert specs.ram_gb == 0
        assert specs.os == ""
        assert specs.cores == 0

    def test_node_alert_defaults(self):
        """NodeAlertContract defaults to info level with empty strings."""
        alert = NodeAlertContract()
        assert alert.level == "info"
        assert alert.message == ""
        assert alert.timestamp == ""

    def test_node_agent_summary_defaults(self):
        """NodeAgentSummary defaults to idle status and empty strings."""
        summary = NodeAgentSummary()
        assert summary.id == ""
        assert summary.session_id == ""
        assert summary.status == "idle"
        assert summary.progress == 0
        assert summary.context_percentage == 0
