"""Unit tests for forge_harness.task_queue module.

Covers TaskPriority, TaskStatus, Task (to_dict / from_dict), TaskQueue CRUD,
claim_next filtering rules, release, complete, get_stats, and
create_task_queue factory.  All I/O is performed through tmp_path so no
real filesystem state leaks between tests.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.task_queue import (
    Task,
    TaskPriority,
    TaskQueue,
    TaskStatus,
    create_task_queue,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_task(
    *,
    task_id: str | None = None,
    title: str = "Test Task",
    description: str = "A test task",
    priority: TaskPriority = TaskPriority.MEDIUM,
    status: TaskStatus = TaskStatus.PENDING,
    domain: str | None = None,
    required_role: str | None = None,
    required_skills: list[str] | None = None,
    depends_on: list[str] | None = None,
    order: int = 0,
) -> Task:
    return Task(
        id=task_id or str(uuid.uuid4())[:8],
        title=title,
        description=description,
        priority=priority,
        status=status,
        domain=domain,
        required_role=required_role,
        required_skills=required_skills or [],
        depends_on=depends_on or [],
        order=order,
    )


# ---------------------------------------------------------------------------
# TaskPriority & TaskStatus enum tests
# ---------------------------------------------------------------------------


class TestTaskPriorityEnum:
    def test_values_are_strings(self):
        assert TaskPriority.CRITICAL == "critical"
        assert TaskPriority.HIGH == "high"
        assert TaskPriority.MEDIUM == "medium"
        assert TaskPriority.LOW == "low"

    def test_str_enum_usable_as_string(self):
        p = TaskPriority.HIGH
        assert isinstance(p, str)
        assert p.upper() == "HIGH"

    def test_all_priorities_exist(self):
        names = {e.name for e in TaskPriority}
        assert names == {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


class TestTaskStatusEnum:
    def test_values_are_strings(self):
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.ASSIGNED == "assigned"
        assert TaskStatus.IN_PROGRESS == "in_progress"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.CANCELLED == "cancelled"
        assert TaskStatus.BLOCKED == "blocked"

    def test_all_statuses_exist(self):
        names = {e.name for e in TaskStatus}
        assert names == {
            "PENDING",
            "ASSIGNED",
            "IN_PROGRESS",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
            "BLOCKED",
        }


# ---------------------------------------------------------------------------
# Task dataclass tests
# ---------------------------------------------------------------------------


class TestTaskDataclass:
    def test_default_status_and_priority(self):
        t = Task(id="t1", title="X", description="Y")
        assert t.status == TaskStatus.PENDING
        assert t.priority == TaskPriority.MEDIUM

    def test_default_factory_fields_are_independent(self):
        t1 = Task(id="a", title="A", description="")
        t2 = Task(id="b", title="B", description="")
        t1.required_skills.append("python")
        assert "python" not in t2.required_skills

    def test_created_at_is_set_automatically(self):
        t = Task(id="x", title="X", description="")
        assert t.created_at is not None
        assert "T" in t.created_at  # ISO format contains 'T'

    def test_to_dict_round_trip(self):
        t = make_task(task_id="rt1", title="Round-trip", required_skills=["go"])
        d = t.to_dict()
        assert isinstance(d, dict)
        assert d["id"] == "rt1"
        assert d["title"] == "Round-trip"
        assert d["required_skills"] == ["go"]
        # Enums serialised as their string values
        assert d["priority"] == "medium"
        assert d["status"] == "pending"

    def test_to_dict_contains_all_expected_keys(self):
        t = make_task()
        d = t.to_dict()
        expected_keys = {
            "id", "title", "description", "priority", "status",
            "domain", "project", "required_role", "required_skills",
            "assigned_agent", "assigned_at", "context", "files",
            "depends_on", "order", "lease", "created_at",
            "created_by", "completed_at", "result",
        }
        assert expected_keys.issubset(d.keys())


class TestTaskFromDict:
    def test_basic_round_trip(self):
        original = make_task(task_id="fd1", title="FromDict", required_role="dev")
        restored = Task.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.title == original.title
        assert restored.required_role == original.required_role

    def test_enum_strings_are_converted(self):
        data = {
            "id": "e1",
            "title": "E",
            "description": "",
            "priority": "critical",
            "status": "completed",
        }
        t = Task.from_dict(data)
        assert t.priority is TaskPriority.CRITICAL
        assert t.status is TaskStatus.COMPLETED

    def test_legacy_assigned_to_migration(self):
        data = {
            "id": "lg1",
            "title": "Legacy",
            "description": "",
            "assigned_to": "agent-007",
        }
        t = Task.from_dict(data)
        assert t.assigned_agent == "agent-007"
        # old key must be gone (handled internally via copy)

    def test_legacy_created_migration(self):
        data = {
            "id": "lg2",
            "title": "Legacy2",
            "description": "",
            "created": "2025-01-01T00:00:00+00:00",
        }
        t = Task.from_dict(data)
        assert t.created_at == "2025-01-01T00:00:00+00:00"

    def test_legacy_created_not_overwritten_when_created_at_present(self):
        data = {
            "id": "lg3",
            "title": "Legacy3",
            "description": "",
            "created": "2020-01-01T00:00:00+00:00",
            "created_at": "2025-06-01T00:00:00+00:00",
        }
        t = Task.from_dict(data)
        assert t.created_at == "2025-06-01T00:00:00+00:00"

    def test_unknown_fields_are_stripped(self):
        data = {
            "id": "unk1",
            "title": "Unknown Fields",
            "description": "",
            "some_future_field": "value",
            "another_extra": 42,
        }
        t = Task.from_dict(data)
        assert t.id == "unk1"
        assert not hasattr(t, "some_future_field")

    def test_missing_title_defaults_to_id(self):
        data = {"id": "notitle", "description": "no title here"}
        t = Task.from_dict(data)
        assert t.title == "notitle"

    def test_missing_description_defaults_to_empty(self):
        data = {"id": "nodesc", "title": "No Desc"}
        t = Task.from_dict(data)
        assert t.description == ""

    def test_original_dict_not_mutated(self):
        data = {
            "id": "orig",
            "title": "Original",
            "description": "",
            "assigned_to": "agent-x",
        }
        original_copy = data.copy()
        Task.from_dict(data)
        assert data == original_copy


# ---------------------------------------------------------------------------
# TaskQueue tests
# ---------------------------------------------------------------------------


@pytest.fixture
def queue(tmp_path) -> TaskQueue:
    """Return a fresh TaskQueue backed by a temp directory."""
    return TaskQueue(tmp_path / ".forge/tasks")


class TestTaskQueueInit:
    def test_creates_directory(self, tmp_path):
        q_dir = tmp_path / "nested" / "queue"
        assert not q_dir.exists()
        TaskQueue(q_dir)
        assert q_dir.is_dir()

    def test_queue_dir_stored(self, tmp_path):
        q = TaskQueue(tmp_path / "q")
        assert isinstance(q.queue_dir, Path)


class TestTaskQueueAdd:
    @pytest.mark.asyncio
    async def test_add_returns_task(self, queue):
        t = make_task(task_id="add1")
        result = await queue.add(t)
        assert result.id == "add1"

    @pytest.mark.asyncio
    async def test_add_writes_json_file(self, queue):
        t = make_task(task_id="add2")
        await queue.add(t)
        path = queue.queue_dir / "add2.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["id"] == "add2"

    @pytest.mark.asyncio
    async def test_add_auto_generates_id_when_empty(self, queue):
        t = Task(id="", title="No ID", description="")
        result = await queue.add(t)
        assert result.id  # generated
        assert len(result.id) == 8  # uuid4 first 8 chars

    @pytest.mark.asyncio
    async def test_add_preserves_existing_id(self, queue):
        t = make_task(task_id="keep-me")
        result = await queue.add(t)
        assert result.id == "keep-me"


class TestTaskQueueGet:
    @pytest.mark.asyncio
    async def test_get_existing_task(self, queue):
        t = make_task(task_id="get1")
        await queue.add(t)
        fetched = await queue.get("get1")
        assert fetched is not None
        assert fetched.id == "get1"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, queue):
        result = await queue.get("does-not-exist")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_deserialises_enums(self, queue):
        t = make_task(task_id="g2", priority=TaskPriority.CRITICAL)
        await queue.add(t)
        fetched = await queue.get("g2")
        assert fetched.priority is TaskPriority.CRITICAL


class TestTaskQueueUpdate:
    @pytest.mark.asyncio
    async def test_update_persists_changes(self, queue):
        t = make_task(task_id="upd1")
        await queue.add(t)
        t.status = TaskStatus.IN_PROGRESS
        t.title = "Updated Title"
        await queue.update(t)

        fetched = await queue.get("upd1")
        assert fetched.status is TaskStatus.IN_PROGRESS
        assert fetched.title == "Updated Title"

    @pytest.mark.asyncio
    async def test_update_returns_task(self, queue):
        t = make_task(task_id="upd2")
        await queue.add(t)
        returned = await queue.update(t)
        assert returned is t


class TestTaskQueueDelete:
    @pytest.mark.asyncio
    async def test_delete_existing_task(self, queue):
        t = make_task(task_id="del1")
        await queue.add(t)
        success = await queue.delete("del1")
        assert success is True
        assert not (queue.queue_dir / "del1.json").exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self, queue):
        result = await queue.delete("ghost")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_makes_task_unreachable(self, queue):
        t = make_task(task_id="del2")
        await queue.add(t)
        await queue.delete("del2")
        assert await queue.get("del2") is None


class TestTaskQueueListAll:
    @pytest.mark.asyncio
    async def test_list_all_returns_all_tasks(self, queue):
        for i in range(3):
            await queue.add(make_task(task_id=f"la{i}"))
        tasks = await queue.list_all()
        assert len(tasks) == 3

    @pytest.mark.asyncio
    async def test_filter_by_status(self, queue):
        t1 = make_task(task_id="s1", status=TaskStatus.PENDING)
        t2 = make_task(task_id="s2", status=TaskStatus.COMPLETED)
        await queue.add(t1)
        await queue.add(t2)

        pending = await queue.list_all(status=TaskStatus.PENDING)
        assert all(t.status == TaskStatus.PENDING for t in pending)
        ids = {t.id for t in pending}
        assert "s1" in ids
        assert "s2" not in ids

    @pytest.mark.asyncio
    async def test_filter_by_priority(self, queue):
        await queue.add(make_task(task_id="p1", priority=TaskPriority.HIGH))
        await queue.add(make_task(task_id="p2", priority=TaskPriority.LOW))

        high = await queue.list_all(priority=TaskPriority.HIGH)
        assert all(t.priority == TaskPriority.HIGH for t in high)
        assert any(t.id == "p1" for t in high)

    @pytest.mark.asyncio
    async def test_filter_by_domain(self, queue):
        await queue.add(make_task(task_id="d1", domain="alpha"))
        await queue.add(make_task(task_id="d2", domain="beta"))

        alpha = await queue.list_all(domain="alpha")
        assert all(t.domain == "alpha" for t in alpha)

    @pytest.mark.asyncio
    async def test_filter_by_role(self, queue):
        await queue.add(make_task(task_id="r1", required_role="frontend"))
        await queue.add(make_task(task_id="r2", required_role="backend"))

        fe = await queue.list_all(role="frontend")
        assert all(t.required_role == "frontend" for t in fe)

    @pytest.mark.asyncio
    async def test_sort_order_priority_then_created_at(self, queue):
        # CRITICAL should come before LOW when order is equal
        low = make_task(task_id="lo", priority=TaskPriority.LOW, order=0)
        crit = make_task(task_id="cr", priority=TaskPriority.CRITICAL, order=0)
        await queue.add(low)
        await queue.add(crit)

        tasks = await queue.list_all()
        ids = [t.id for t in tasks]
        assert ids.index("cr") < ids.index("lo")

    @pytest.mark.asyncio
    async def test_sort_order_field_takes_precedence(self, queue):
        # explicit order=1 should sort after order=0 regardless of priority
        high_order1 = make_task(task_id="ho1", priority=TaskPriority.CRITICAL, order=1)
        low_order0 = make_task(task_id="lo0", priority=TaskPriority.LOW, order=0)
        await queue.add(high_order1)
        await queue.add(low_order0)

        tasks = await queue.list_all()
        ids = [t.id for t in tasks]
        assert ids.index("lo0") < ids.index("ho1")

    @pytest.mark.asyncio
    async def test_empty_queue_returns_empty_list(self, queue):
        assert await queue.list_all() == []


class TestTaskQueueClaimNext:
    @pytest.mark.asyncio
    async def test_claim_returns_highest_priority_pending_task(self, queue):
        low = make_task(task_id="cl1", priority=TaskPriority.LOW)
        high = make_task(task_id="ch1", priority=TaskPriority.HIGH)
        await queue.add(low)
        await queue.add(high)

        claimed = await queue.claim_next(agent_id="agt", agent_role="any-role")
        assert claimed is not None
        assert claimed.id == "ch1"

    @pytest.mark.asyncio
    async def test_claim_sets_assigned_fields(self, queue):
        t = make_task(task_id="ca1")
        await queue.add(t)

        claimed = await queue.claim_next(agent_id="agt-42", agent_role="dev")
        assert claimed is not None
        assert claimed.status == TaskStatus.ASSIGNED
        assert claimed.assigned_agent == "agt-42"
        assert claimed.assigned_at is not None

    @pytest.mark.asyncio
    async def test_claim_persists_status(self, queue):
        t = make_task(task_id="cp1")
        await queue.add(t)
        await queue.claim_next(agent_id="agt", agent_role="dev")

        fetched = await queue.get("cp1")
        assert fetched.status == TaskStatus.ASSIGNED

    @pytest.mark.asyncio
    async def test_claim_skips_non_pending_tasks(self, queue):
        t = make_task(task_id="np1", status=TaskStatus.COMPLETED)
        await queue.add(t)

        result = await queue.claim_next(agent_id="agt", agent_role="dev")
        assert result is None

    @pytest.mark.asyncio
    async def test_claim_respects_required_role(self, queue):
        t = make_task(task_id="rr1", required_role="designer")
        await queue.add(t)

        # Wrong role — should not claim
        result = await queue.claim_next(agent_id="dev-agt", agent_role="developer")
        assert result is None

    @pytest.mark.asyncio
    async def test_claim_matches_correct_role(self, queue):
        t = make_task(task_id="rr2", required_role="designer")
        await queue.add(t)

        result = await queue.claim_next(agent_id="des-agt", agent_role="designer")
        assert result is not None
        assert result.id == "rr2"

    @pytest.mark.asyncio
    async def test_claim_with_no_required_role_accepts_any_role(self, queue):
        t = make_task(task_id="nr1")  # required_role=None
        await queue.add(t)

        result = await queue.claim_next(agent_id="agt", agent_role="anything")
        assert result is not None

    @pytest.mark.asyncio
    async def test_claim_respects_domain_filter(self, queue):
        t_alpha = make_task(task_id="da1", domain="alpha")
        t_beta = make_task(task_id="db1", domain="beta")
        await queue.add(t_alpha)
        await queue.add(t_beta)

        result = await queue.claim_next(
            agent_id="agt", agent_role="dev", domain="alpha"
        )
        assert result is not None
        assert result.id == "da1"

    @pytest.mark.asyncio
    async def test_claim_domain_none_does_not_filter(self, queue):
        """When agent passes domain=None, domain filter is disabled."""
        t = make_task(task_id="dn1", domain="some-domain")
        await queue.add(t)

        result = await queue.claim_next(agent_id="agt", agent_role="dev", domain=None)
        assert result is not None

    @pytest.mark.asyncio
    async def test_claim_respects_required_skills(self, queue):
        t = make_task(task_id="sk1", required_skills=["python", "django"])
        await queue.add(t)

        # Missing django
        result = await queue.claim_next(
            agent_id="agt", agent_role="dev", agent_skills=["python"]
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_claim_succeeds_with_all_required_skills(self, queue):
        t = make_task(task_id="sk2", required_skills=["python", "django"])
        await queue.add(t)

        result = await queue.claim_next(
            agent_id="agt", agent_role="dev", agent_skills=["python", "django", "sql"]
        )
        assert result is not None
        assert result.id == "sk2"

    @pytest.mark.asyncio
    async def test_claim_waits_for_dependency_to_complete(self, queue):
        dep = make_task(task_id="dep1", status=TaskStatus.PENDING)
        t = make_task(task_id="dep-child", depends_on=["dep1"])
        await queue.add(dep)
        await queue.add(t)

        result = await queue.claim_next(agent_id="agt", agent_role="dev")
        # Should claim dep1 first (child is blocked)
        assert result is not None
        assert result.id == "dep1"

    @pytest.mark.asyncio
    async def test_claim_allows_task_when_dependency_completed(self, queue):
        dep = make_task(task_id="dep2", status=TaskStatus.COMPLETED)
        t = make_task(task_id="child2", depends_on=["dep2"])
        await queue.add(dep)
        await queue.add(t)

        result = await queue.claim_next(agent_id="agt", agent_role="dev")
        assert result is not None
        assert result.id == "child2"

    @pytest.mark.asyncio
    async def test_claim_missing_dependency_does_not_block(self, queue):
        """If dependency task doesn't exist, treat as unblocked."""
        t = make_task(task_id="ghost-dep", depends_on=["nonexistent-id"])
        await queue.add(t)

        # The dep task doesn't exist so dep_task is None → deps_met stays True
        result = await queue.claim_next(agent_id="agt", agent_role="dev")
        assert result is not None
        assert result.id == "ghost-dep"

    @pytest.mark.asyncio
    async def test_claim_returns_none_when_empty(self, queue):
        result = await queue.claim_next(agent_id="agt", agent_role="dev")
        assert result is None

    @pytest.mark.asyncio
    async def test_claim_agent_skills_defaults_to_empty(self, queue):
        """agent_skills=None should default to [] without error."""
        t = make_task(task_id="ns1")
        await queue.add(t)
        result = await queue.claim_next(agent_id="agt", agent_role="dev", agent_skills=None)
        assert result is not None


class TestTaskQueueRelease:
    @pytest.mark.asyncio
    async def test_release_resets_status_to_pending(self, queue):
        t = make_task(task_id="rel1", status=TaskStatus.ASSIGNED)
        t.assigned_agent = "agt-99"
        t.assigned_at = "2025-01-01T00:00:00+00:00"
        await queue.add(t)

        released = await queue.release("rel1")
        assert released is not None
        assert released.status == TaskStatus.PENDING
        assert released.assigned_agent is None
        assert released.assigned_at is None

    @pytest.mark.asyncio
    async def test_release_persists_changes(self, queue):
        t = make_task(task_id="rel2", status=TaskStatus.ASSIGNED)
        t.assigned_agent = "agt-77"
        await queue.add(t)
        await queue.release("rel2")

        fetched = await queue.get("rel2")
        assert fetched.status == TaskStatus.PENDING
        assert fetched.assigned_agent is None

    @pytest.mark.asyncio
    async def test_release_nonexistent_task_returns_none(self, queue):
        result = await queue.release("no-such-task")
        assert result is None


class TestTaskQueueComplete:
    @pytest.mark.asyncio
    async def test_complete_success(self, queue):
        t = make_task(task_id="co1")
        await queue.add(t)

        result = await queue.complete("co1", result={"output": "ok"}, success=True)
        assert result is not None
        assert result.status == TaskStatus.COMPLETED
        assert result.result == {"output": "ok"}
        assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_complete_failure(self, queue):
        t = make_task(task_id="co2")
        await queue.add(t)

        result = await queue.complete("co2", result={"error": "boom"}, success=False)
        assert result.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_complete_default_success_is_true(self, queue):
        t = make_task(task_id="co3")
        await queue.add(t)

        result = await queue.complete("co3")
        assert result.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_complete_result_none_by_default(self, queue):
        t = make_task(task_id="co4")
        await queue.add(t)

        result = await queue.complete("co4")
        assert result.result is None

    @pytest.mark.asyncio
    async def test_complete_persists_to_disk(self, queue):
        t = make_task(task_id="co5")
        await queue.add(t)
        await queue.complete("co5", result={"x": 1})

        fetched = await queue.get("co5")
        assert fetched.status == TaskStatus.COMPLETED
        assert fetched.result == {"x": 1}

    @pytest.mark.asyncio
    async def test_complete_nonexistent_task_returns_none(self, queue):
        result = await queue.complete("ghost-task")
        assert result is None


class TestTaskQueueGetStats:
    @pytest.mark.asyncio
    async def test_stats_total(self, queue):
        for i in range(4):
            await queue.add(make_task(task_id=f"st{i}"))
        stats = await queue.get_stats()
        assert stats["total"] == 4

    @pytest.mark.asyncio
    async def test_stats_by_status(self, queue):
        t1 = make_task(task_id="ss1", status=TaskStatus.PENDING)
        t2 = make_task(task_id="ss2", status=TaskStatus.COMPLETED)
        t3 = make_task(task_id="ss3", status=TaskStatus.FAILED)
        for t in [t1, t2, t3]:
            await queue.add(t)

        stats = await queue.get_stats()
        assert stats["by_status"]["pending"] >= 1
        assert stats["by_status"]["completed"] >= 1
        assert stats["by_status"]["failed"] >= 1

    @pytest.mark.asyncio
    async def test_stats_by_priority(self, queue):
        await queue.add(make_task(task_id="sp1", priority=TaskPriority.CRITICAL))
        await queue.add(make_task(task_id="sp2", priority=TaskPriority.LOW))

        stats = await queue.get_stats()
        assert stats["by_priority"]["critical"] >= 1
        assert stats["by_priority"]["low"] >= 1

    @pytest.mark.asyncio
    async def test_stats_by_domain_with_unassigned(self, queue):
        await queue.add(make_task(task_id="sd1", domain="alpha"))
        await queue.add(make_task(task_id="sd2", domain=None))  # no domain

        stats = await queue.get_stats()
        assert stats["by_domain"]["alpha"] >= 1
        assert stats["by_domain"]["unassigned"] >= 1

    @pytest.mark.asyncio
    async def test_stats_empty_queue(self, queue):
        stats = await queue.get_stats()
        assert stats["total"] == 0
        assert stats["by_status"] == {}
        assert stats["by_priority"] == {}
        assert stats["by_domain"] == {}

    @pytest.mark.asyncio
    async def test_stats_structure_keys(self, queue):
        stats = await queue.get_stats()
        assert set(stats.keys()) == {"total", "by_status", "by_priority", "by_domain"}


# ---------------------------------------------------------------------------
# TaskQueue._task_path
# ---------------------------------------------------------------------------


class TestTaskQueueTaskPath:
    def test_returns_path_with_json_suffix(self, tmp_path):
        q = TaskQueue(tmp_path)
        p = q._task_path("abc123")
        assert p == tmp_path / "abc123.json"
        assert str(p).endswith(".json")


# ---------------------------------------------------------------------------
# create_task_queue factory
# ---------------------------------------------------------------------------


class TestCreateTaskQueue:
    def test_returns_task_queue_instance(self, tmp_path):
        q = create_task_queue(forge_root=tmp_path)
        assert isinstance(q, TaskQueue)

    def test_queue_dir_under_forge_tasks(self, tmp_path):
        q = create_task_queue(forge_root=tmp_path)
        assert q.queue_dir == tmp_path / ".forge/tasks"

    def test_uses_forge_root_env_when_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
        q = create_task_queue(forge_root=None)
        assert q.queue_dir == tmp_path / ".forge/tasks"

    def test_uses_dot_as_default_when_no_env(self, monkeypatch):
        monkeypatch.delenv("FORGE_ROOT", raising=False)
        q = create_task_queue(forge_root=None)
        assert q.queue_dir == Path(".") / ".forge/tasks"


# ---------------------------------------------------------------------------
# Concurrency / locking (smoke tests using real asyncio)
# ---------------------------------------------------------------------------


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_adds_do_not_corrupt_files(self, queue):
        import asyncio

        tasks = [make_task(task_id=f"cc{i}") for i in range(10)]
        await asyncio.gather(*[queue.add(t) for t in tasks])

        all_tasks = await queue.list_all()
        assert len(all_tasks) == 10

    @pytest.mark.asyncio
    async def test_concurrent_updates_last_write_wins(self, queue):
        import asyncio

        t = make_task(task_id="lw1")
        await queue.add(t)

        async def update_title(title: str):
            fresh = await queue.get("lw1")
            fresh.title = title
            await queue.update(fresh)

        await asyncio.gather(
            update_title("Title A"),
            update_title("Title B"),
        )
        # Just ensure no exception and file is still valid JSON
        fetched = await queue.get("lw1")
        assert fetched is not None
        assert fetched.title in {"Title A", "Title B"}
