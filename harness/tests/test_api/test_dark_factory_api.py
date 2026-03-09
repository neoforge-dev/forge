"""Tests for Dark Factory API endpoints (lanes, audit, canary)."""

from __future__ import annotations

import json

import pytest

from forge_harness.dark_factory.audit_sampler import reset_audit_sampler
from forge_harness.dark_factory.canary_control import reset_canary_control
from forge_harness.dark_factory.lane_policy_enforcer import reset_lane_policy_enforcer
from forge_harness.webhook_server.api.audit import (
    ManualAuditSampleRequest,
    UpdateAuditConfigRequest,
    get_audit_config,
    get_audit_queue,
    trigger_audit_sample,
    update_audit_config,
)
from forge_harness.webhook_server.api.canary import (
    CanaryActionRequest,
    CreateCanaryRolloutRequest,
    create_canary_rollout,
    get_canary_rollout,
    list_canary_rollouts,
    pause_canary_rollout,
    promote_canary_rollout,
    resume_canary_rollout,
    rollback_canary_rollout,
)
from forge_harness.webhook_server.api.lanes import (
    UpdateLanePolicyRequest,
    get_lane_policy,
    list_lane_policies,
    update_lane_policy,
)


@pytest.fixture(autouse=True)
def _reset_df_singletons() -> None:
    """Reset DF singleton state between tests."""
    reset_lane_policy_enforcer()
    reset_audit_sampler()
    reset_canary_control()
    yield
    reset_lane_policy_enforcer()
    reset_audit_sampler()
    reset_canary_control()


def _payload(response) -> dict:
    return json.loads(response.body.decode())


@pytest.mark.asyncio
async def test_lanes_policy_roundtrip_update() -> None:
    initial = await list_lane_policies()
    initial_data = _payload(initial)
    assert initial.status_code == 200
    assert initial_data["success"] is True
    assert initial_data["error"] is None
    assert initial_data["data"]["count"] > 0

    update_body = UpdateLanePolicyRequest(
        can_auto_complete=False,
        requires_human_review=True,
        operator="test",
        reason="unit-test",
    )
    updated = await update_lane_policy("docs", update_body)
    updated_data = _payload(updated)

    assert updated.status_code == 200
    assert updated_data["success"] is True
    assert updated_data["error"] is None
    assert updated_data["data"]["policy"]["lane"] == "docs"
    assert updated_data["data"]["policy"]["can_auto_complete"] is False
    assert updated_data["data"]["policy"]["requires_human_review"] is True

    docs_policy = await get_lane_policy("docs")
    docs_data = _payload(docs_policy)
    assert docs_policy.status_code == 200
    assert docs_data["data"]["can_auto_complete"] is False
    assert docs_data["data"]["requires_human_review"] is True


@pytest.mark.asyncio
async def test_audit_config_and_queue() -> None:
    cfg_before = await get_audit_config()
    before_data = _payload(cfg_before)
    assert cfg_before.status_code == 200
    assert before_data["success"] is True
    assert before_data["error"] is None
    assert before_data["data"]["sample_rate"] > 0

    cfg_update = await update_audit_config(
        UpdateAuditConfigRequest(sample_rate=0.2, operator="test", reason="unit-test")
    )
    update_data = _payload(cfg_update)
    assert cfg_update.status_code == 200
    assert update_data["success"] is True
    assert update_data["error"] is None
    assert update_data["data"]["sample_rate"] == pytest.approx(0.2)

    queue = await get_audit_queue(status="all", limit=20)
    queue_data = _payload(queue)
    assert queue.status_code == 200
    assert queue_data["success"] is True
    assert queue_data["error"] is None
    assert queue_data["data"]["status"] == "all"
    assert isinstance(queue_data["data"]["audits"], list)


@pytest.mark.asyncio
async def test_canary_rollout_create_pause_resume_promote_rollback() -> None:
    created = await create_canary_rollout(
        CreateCanaryRolloutRequest(
            node_id="prya",
            lane="docs",
            stage="canary",
            operator="test",
            reason="unit-test",
        )
    )
    created_data = _payload(created)
    assert created.status_code == 200
    assert created_data["success"] is True
    assert created_data["error"] is None
    rollout_id = created_data["data"]["rollout"]["rollout_id"]
    assert rollout_id == "prya:docs"

    listed = await list_canary_rollouts(node_id=None, lane=None, limit=10)
    listed_data = _payload(listed)
    assert listed.status_code == 200
    assert listed_data["success"] is True
    assert listed_data["error"] is None
    assert listed_data["data"]["count"] >= 1

    paused = await pause_canary_rollout(rollout_id, CanaryActionRequest(operator="test"))
    paused_data = _payload(paused)
    assert paused.status_code == 200
    assert paused_data["data"]["rollout"]["paused"] is True
    assert paused_data["data"]["rollout"]["effective_stage"] == "disabled"

    resumed = await resume_canary_rollout(rollout_id, CanaryActionRequest(operator="test"))
    resumed_data = _payload(resumed)
    assert resumed.status_code == 200
    assert resumed_data["data"]["rollout"]["paused"] is False
    assert resumed_data["data"]["rollout"]["effective_stage"] == "canary"

    promoted = await promote_canary_rollout(rollout_id, CanaryActionRequest(operator="test"))
    promoted_data = _payload(promoted)
    assert promoted.status_code == 200
    assert promoted_data["data"]["rollout"]["effective_stage"] == "enabled"

    rolled_back = await rollback_canary_rollout(rollout_id, CanaryActionRequest(operator="test"))
    rollback_data = _payload(rolled_back)
    assert rolled_back.status_code == 200
    assert rollback_data["data"]["rollout"]["effective_stage"] == "canary"

    status = await get_canary_rollout(rollout_id)
    status_data = _payload(status)
    assert status.status_code == 200
    assert status_data["success"] is True
    assert status_data["error"] is None
    assert status_data["data"]["rollout"]["rollout_id"] == rollout_id


@pytest.mark.asyncio
async def test_manual_audit_sample() -> None:
    # Set rate to 100% to ensure selection in test
    await update_audit_config(
        UpdateAuditConfigRequest(sample_rate=1.0, operator="test", reason="force-select")
    )

    sample = await trigger_audit_sample(
        ManualAuditSampleRequest(task_id="T-TEST-123", lane="test-lane")
    )
    sample_data = _payload(sample)
    assert sample.status_code == 200
    assert sample_data["success"] is True
    assert sample_data["data"]["task_id"] == "T-TEST-123"
    assert sample_data["data"]["selected"] is True

    # Verify it's in the queue
    queue = await get_audit_queue(status="pending", limit=50)
    queue_data = _payload(queue)
    assert any(a["task_id"] == "T-TEST-123" for a in queue_data["data"]["audits"])
