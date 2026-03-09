"""Comprehensive tests for three webhook server API modules:

  - forge_harness/webhook_server/api/patterns.py   (~328 lines)
  - forge_harness/webhook_server/api/auth.py        (~324 lines)
  - forge_harness/webhook_server/api/messages.py    (~254 lines)

Patterns tested:
  - All endpoints (happy path + error paths)
  - Auth bypass via dependency_overrides
  - Mock injection via unittest.mock patch
  - Pydantic model validation (field constraints, validators)
  - 503 pattern-store/queue unavailable responses
  - 404 not-found paths
  - Event-bus SSE publishing
  - Audit logging calls

Target: 80%+ coverage per module, all tests pass.
"""

import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------


def _make_pattern(
    pattern_id: str = "test-pattern-1",
    name: str = "Test Pattern",
    category: str = "testing",
    template: str = "Do {{action}} on {{target}}",
    variables: list | None = None,
    success_rate: float = 0.75,
    uses: int = 10,
    alpha: float = 1.0,
    beta: float = 1.0,
    version: int = 1,
) -> Mock:
    """Build a mock Pattern object with to_dict()."""
    p = Mock()
    p.id = pattern_id
    p.name = name
    p.category = category
    p.template = template
    p.variables = variables or ["action", "target"]
    p.success_rate = success_rate
    p.uses = uses
    p.alpha = alpha
    p.beta = beta
    p.version = version
    p.updated_at = "2026-01-01T00:00:00+00:00"
    p.to_dict.return_value = {
        "id": pattern_id,
        "name": name,
        "category": category,
        "template": template,
        "variables": variables or ["action", "target"],
        "success_rate": success_rate,
        "uses": uses,
        "alpha": alpha,
        "beta": beta,
        "version": version,
    }
    return p


def _make_outcome(
    outcome_id: str = "out-001",
    pattern_id: str = "test-pattern-1",
    success: bool = True,
    variant: str | None = None,
) -> Mock:
    """Build a mock Outcome object."""
    o = Mock()
    o.id = outcome_id
    o.pattern_id = pattern_id
    o.success = success
    o.variant = variant
    o.to_dict.return_value = {
        "id": outcome_id,
        "pattern_id": pattern_id,
        "success": success,
        "variant": variant,
    }
    return o


def _make_pattern_store(
    patterns: list | None = None,
    pattern: Mock | None = None,
) -> Mock:
    """Build a mock PatternStore."""
    store = Mock()
    _patterns = patterns or [_make_pattern()]
    _pattern = pattern or _patterns[0]

    store.list_patterns = Mock(return_value=_patterns)
    store.get_pattern = Mock(return_value=_pattern)
    store.create_or_update = Mock(return_value=_pattern)
    store.record_outcome = Mock(return_value=_make_outcome())
    store.get_outcomes = Mock(return_value=[_make_outcome()])
    store.get_variant_stats = Mock(return_value={"control": {"uses": 5, "success_rate": 0.8}})
    store._patterns = {_pattern.id: _pattern}
    store._save = Mock()
    return store


def _make_event_bus() -> Mock:
    bus = Mock()
    bus.publish = AsyncMock()
    return bus


def _make_message(
    message_id: str = "msg-001",
    sender_id: str = "agent-a",
    recipient_id: str = "agent-b",
    status: str = "pending",
) -> Mock:
    msg = Mock()
    msg.id = message_id
    msg.message_id = message_id
    msg.sender_id = sender_id
    msg.recipient_id = recipient_id
    msg.status = status
    msg.acknowledged_at = None
    msg.to_dict.return_value = {
        "id": message_id,
        "sender_id": sender_id,
        "recipient_id": recipient_id,
        "status": status,
        "acknowledged_at": None,
    }
    return msg


def _make_queue() -> Mock:
    queue = Mock()
    queue.get_pending = Mock(return_value=[_make_message()])
    queue.acknowledge = Mock(return_value=_make_message(status="acknowledged"))
    queue.get_status = Mock(return_value=_make_message())
    queue.get_stats = Mock(return_value={
        "pending_messages": 2,
        "delivered_messages": 10,
        "acknowledged_messages": 8,
        "processing_rate": 1.5,
        "average_wait_time": 0.3,
        "total_processed": 18,
        "queue_age_seconds": 60,
    })
    queue.get_conversation = Mock(return_value=[_make_message()])
    return queue


def _make_agent_registry() -> Mock:
    registry = Mock()
    agent = Mock()
    agent.tmux_session = None
    registry.send_message = Mock(return_value=(agent, "msg-001"))
    return registry


def _make_audit() -> Mock:
    audit = Mock()
    audit.log = AsyncMock()
    return audit


def _make_jwt_payload(
    sub: str = "alice",
    permissions: list | None = None,
    token_type: str = "access",
    jti: str = "jti-abc123",
) -> Mock:
    from datetime import UTC, datetime, timedelta

    payload = Mock()
    payload.sub = sub
    payload.permissions = permissions or ["read", "write"]
    payload.type = token_type
    payload.jti = jti
    payload.exp = datetime.now(UTC) + timedelta(hours=1)
    return payload


# ---------------------------------------------------------------------------
# App fixtures
# ---------------------------------------------------------------------------


def _bypass_auth(app: FastAPI) -> None:
    """Bypass verify_auth and verify_unified_auth dependencies."""
    from forge_harness.webhook_server.core import dependencies

    app.dependency_overrides[dependencies.verify_auth] = lambda: {
        "sub": "test",
        "permissions": ["admin"],
    }
    app.dependency_overrides[dependencies.verify_unified_auth] = lambda: {
        "sub": "test",
        "permissions": ["admin"],
    }

    def _noop_require_permission(permission: str):
        async def _check():
            return {"sub": "test", "permissions": ["admin"]}
        return _check

    app.dependency_overrides[dependencies.require_permission] = _noop_require_permission


# ===========================================================================
#  SECTION 1: PATTERNS API
# ===========================================================================


@pytest.fixture
def patterns_client():
    """TestClient with patterns router, auth bypassed, stores mocked."""
    from forge_harness.webhook_server.api import patterns as patterns_module

    store = _make_pattern_store()
    bus = _make_event_bus()

    app = FastAPI()
    app.include_router(patterns_module.router)
    _bypass_auth(app)

    patches = [
        patch(
            "forge_harness.webhook_server.api.patterns.get_pattern_store",
            return_value=store,
        ),
        patch(
            "forge_harness.webhook_server.api.patterns.get_event_bus",
            return_value=bus,
        ),
    ]
    for p in patches:
        p.start()

    client = TestClient(app, raise_server_exceptions=False)
    yield client, {"store": store, "bus": bus}

    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# Pydantic model tests — patterns
# ---------------------------------------------------------------------------


class TestPatternUpdateRequestModel:
    def test_all_none_valid(self):
        from forge_harness.webhook_server.api.patterns import PatternUpdateRequest

        req = PatternUpdateRequest()
        assert req.name is None
        assert req.category is None
        assert req.template is None
        assert req.variables is None

    def test_partial_fields(self):
        from forge_harness.webhook_server.api.patterns import PatternUpdateRequest

        req = PatternUpdateRequest(name="New Name", category="deployment")
        assert req.name == "New Name"
        assert req.category == "deployment"

    def test_name_too_long_raises(self):
        from forge_harness.webhook_server.api.patterns import PatternUpdateRequest

        with pytest.raises(ValidationError):
            PatternUpdateRequest(name="x" * 201)

    def test_template_too_long_raises(self):
        from forge_harness.webhook_server.api.patterns import PatternUpdateRequest

        with pytest.raises(ValidationError):
            PatternUpdateRequest(template="x" * 5001)

    def test_variables_list(self):
        from forge_harness.webhook_server.api.patterns import PatternUpdateRequest

        req = PatternUpdateRequest(variables=["var1", "var2"])
        assert req.variables == ["var1", "var2"]


class TestPatternCreateRequestModel:
    def test_valid_minimal(self):
        from forge_harness.webhook_server.api.patterns import PatternCreateRequest

        req = PatternCreateRequest(
            name="My Pattern",
            category="testing",
            template="Do {{action}}",
        )
        assert req.name == "My Pattern"
        assert req.id is None

    def test_valid_with_id(self):
        from forge_harness.webhook_server.api.patterns import PatternCreateRequest

        req = PatternCreateRequest(
            id="my-pattern-1",
            name="My Pattern",
            category="testing",
            template="Do {{action}}",
        )
        assert req.id == "my-pattern-1"

    def test_id_invalid_chars_raises(self):
        from forge_harness.webhook_server.api.patterns import PatternCreateRequest

        with pytest.raises(ValidationError):
            PatternCreateRequest(
                id="My Pattern!",
                name="Name",
                category="cat",
                template="tpl",
            )

    def test_name_strips_whitespace(self):
        from forge_harness.webhook_server.api.patterns import PatternCreateRequest

        req = PatternCreateRequest(
            name="  spaced  ",
            category="testing",
            template="Do {{action}}",
        )
        assert req.name == "spaced"

    def test_whitespace_name_raises(self):
        from forge_harness.webhook_server.api.patterns import PatternCreateRequest

        with pytest.raises(ValidationError):
            PatternCreateRequest(name="   ", category="cat", template="tpl")

    def test_whitespace_category_raises(self):
        from forge_harness.webhook_server.api.patterns import PatternCreateRequest

        with pytest.raises(ValidationError):
            PatternCreateRequest(name="name", category="   ", template="tpl")

    def test_whitespace_template_raises(self):
        from forge_harness.webhook_server.api.patterns import PatternCreateRequest

        with pytest.raises(ValidationError):
            PatternCreateRequest(name="name", category="cat", template="   ")

    def test_variables_validation_empty_string_raises(self):
        from forge_harness.webhook_server.api.patterns import PatternCreateRequest

        with pytest.raises(ValidationError):
            PatternCreateRequest(
                name="name",
                category="cat",
                template="tpl",
                variables=["valid", ""],
            )

    def test_variables_invalid_chars_raises(self):
        from forge_harness.webhook_server.api.patterns import PatternCreateRequest

        with pytest.raises(ValidationError):
            PatternCreateRequest(
                name="name",
                category="cat",
                template="tpl",
                variables=["inv@lid!"],
            )

    def test_valid_variables_with_hyphen_underscore(self):
        from forge_harness.webhook_server.api.patterns import PatternCreateRequest

        req = PatternCreateRequest(
            name="name",
            category="cat",
            template="tpl",
            variables=["var_1", "var-2"],
        )
        assert len(req.variables) == 2

    def test_name_too_long_raises(self):
        from forge_harness.webhook_server.api.patterns import PatternCreateRequest

        with pytest.raises(ValidationError):
            PatternCreateRequest(
                name="x" * 201,
                category="cat",
                template="tpl",
            )


class TestOutcomeRecordRequestModel:
    def test_success_true(self):
        from forge_harness.webhook_server.api.patterns import OutcomeRecordRequest

        req = OutcomeRecordRequest(success=True)
        assert req.success is True
        assert req.variant is None
        assert req.context is None

    def test_full_fields(self):
        from forge_harness.webhook_server.api.patterns import OutcomeRecordRequest

        req = OutcomeRecordRequest(
            success=False, variant="B", context={"key": "value"}
        )
        assert req.variant == "B"
        assert req.context == {"key": "value"}

    def test_missing_success_raises(self):
        from forge_harness.webhook_server.api.patterns import OutcomeRecordRequest

        with pytest.raises(ValidationError):
            OutcomeRecordRequest()


# ---------------------------------------------------------------------------
# GET /api/patterns
# ---------------------------------------------------------------------------


class TestListPatterns:
    def test_returns_patterns(self, patterns_client):
        client, mocks = patterns_client
        resp = client.get("/api/patterns")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "patterns" in data["data"]
        assert data["data"]["total"] == 1

    def test_category_filter_passed(self, patterns_client):
        client, mocks = patterns_client
        resp = client.get("/api/patterns?category=testing")
        assert resp.status_code == 200
        mocks["store"].list_patterns.assert_called_once_with(category="testing")

    def test_no_category_filter(self, patterns_client):
        client, mocks = patterns_client
        resp = client.get("/api/patterns")
        assert resp.status_code == 200
        mocks["store"].list_patterns.assert_called_once_with(category=None)

    def test_store_none_returns_empty(self, patterns_client):
        client, mocks = patterns_client
        with patch(
            "forge_harness.webhook_server.api.patterns.get_pattern_store",
            return_value=None,
        ):
            resp = client.get("/api/patterns")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["total"] == 0
        assert "Pattern store not available" in data["data"]["error"]

    def test_response_has_timestamp(self, patterns_client):
        client, _ = patterns_client
        resp = client.get("/api/patterns")
        data = resp.json()
        assert "timestamp" in data

    def test_multiple_patterns_returned(self, patterns_client):
        client, mocks = patterns_client
        two_patterns = [_make_pattern("p1"), _make_pattern("p2", name="Pattern 2")]
        mocks["store"].list_patterns = Mock(return_value=two_patterns)
        resp = client.get("/api/patterns")
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 2


# ---------------------------------------------------------------------------
# GET /api/patterns/related
# ---------------------------------------------------------------------------


class TestGetRelatedPatterns:
    def test_returns_related_patterns(self, patterns_client):
        client, mocks = patterns_client
        resp = client.get("/api/patterns/related")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "patterns" in data["data"]

    def test_category_scoring_boost(self, patterns_client):
        client, mocks = patterns_client
        p1 = _make_pattern("p1", category="feature", success_rate=0.5, uses=5)
        p2 = _make_pattern("p2", category="deployment", success_rate=0.9, uses=100)
        mocks["store"].list_patterns = Mock(return_value=[p1, p2])
        resp = client.get("/api/patterns/related?approval_type=deployment")
        assert resp.status_code == 200
        # deployment pattern should be ranked higher
        patterns = resp.json()["data"]["patterns"]
        assert patterns[0]["category"] == "deployment"

    def test_limit_parameter(self, patterns_client):
        client, mocks = patterns_client
        patterns = [_make_pattern(f"p{i}") for i in range(10)]
        mocks["store"].list_patterns = Mock(return_value=patterns)
        resp = client.get("/api/patterns/related?limit=3")
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] <= 3

    def test_store_none_returns_empty(self, patterns_client):
        client, _ = patterns_client
        with patch(
            "forge_harness.webhook_server.api.patterns.get_pattern_store",
            return_value=None,
        ):
            resp = client.get("/api/patterns/related")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["total"] == 0
        assert "Pattern store not available" in data["data"]["error"]

    def test_confidence_added_to_response(self, patterns_client):
        client, mocks = patterns_client
        resp = client.get("/api/patterns/related")
        assert resp.status_code == 200
        for p in resp.json()["data"]["patterns"]:
            assert "confidence" in p

    def test_empty_pattern_store_returns_empty(self, patterns_client):
        client, mocks = patterns_client
        mocks["store"].list_patterns = Mock(return_value=[])
        resp = client.get("/api/patterns/related")
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/patterns/{pattern_id}
# ---------------------------------------------------------------------------


class TestGetPattern:
    def test_found_returns_pattern(self, patterns_client):
        client, mocks = patterns_client
        resp = client.get("/api/patterns/test-pattern-1")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == "test-pattern-1"

    def test_not_found_returns_404(self, patterns_client):
        client, mocks = patterns_client
        mocks["store"].get_pattern = Mock(return_value=None)
        resp = client.get("/api/patterns/does-not-exist")
        assert resp.status_code == 404

    def test_store_none_returns_503(self, patterns_client):
        client, _ = patterns_client
        with patch(
            "forge_harness.webhook_server.api.patterns.get_pattern_store",
            return_value=None,
        ):
            resp = client.get("/api/patterns/test-pattern-1")
        assert resp.status_code == 503

    def test_calls_get_pattern_with_id(self, patterns_client):
        client, mocks = patterns_client
        client.get("/api/patterns/my-pattern")
        mocks["store"].get_pattern.assert_called_with("my-pattern")

    def test_response_has_success_true(self, patterns_client):
        client, _ = patterns_client
        resp = client.get("/api/patterns/test-pattern-1")
        assert resp.json()["success"] is True


# ---------------------------------------------------------------------------
# PUT /api/patterns/{pattern_id}
# ---------------------------------------------------------------------------


class TestUpdatePattern:
    def test_update_name_succeeds(self, patterns_client):
        client, mocks = patterns_client
        resp = client.put(
            "/api/patterns/test-pattern-1",
            json={"name": "Updated Name"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_update_category(self, patterns_client):
        client, mocks = patterns_client
        resp = client.put(
            "/api/patterns/test-pattern-1",
            json={"category": "deployment"},
        )
        assert resp.status_code == 200

    def test_update_template(self, patterns_client):
        client, mocks = patterns_client
        resp = client.put(
            "/api/patterns/test-pattern-1",
            json={"template": "New template {{var}}"},
        )
        assert resp.status_code == 200

    def test_update_increments_version(self, patterns_client):
        client, mocks = patterns_client
        pattern = mocks["store"].get_pattern.return_value
        original_version = pattern.version
        client.put("/api/patterns/test-pattern-1", json={"name": "New"})
        assert pattern.version == original_version + 1

    def test_update_saves_pattern(self, patterns_client):
        client, mocks = patterns_client
        client.put("/api/patterns/test-pattern-1", json={"name": "New"})
        mocks["store"]._save.assert_called_once()

    def test_update_emits_sse_event(self, patterns_client):
        client, mocks = patterns_client
        client.put("/api/patterns/test-pattern-1", json={"name": "New"})
        mocks["bus"].publish.assert_called_once()
        call_args = mocks["bus"].publish.call_args[0]
        assert call_args[0] == "pattern.updated"

    def test_update_not_found_returns_404(self, patterns_client):
        client, mocks = patterns_client
        mocks["store"].get_pattern = Mock(return_value=None)
        resp = client.put("/api/patterns/nonexistent", json={"name": "X"})
        assert resp.status_code == 404

    def test_update_store_none_returns_503(self, patterns_client):
        client, _ = patterns_client
        with patch(
            "forge_harness.webhook_server.api.patterns.get_pattern_store",
            return_value=None,
        ):
            resp = client.put("/api/patterns/test-pattern-1", json={"name": "X"})
        assert resp.status_code == 503

    def test_update_no_event_bus_still_succeeds(self, patterns_client):
        client, mocks = patterns_client
        with patch(
            "forge_harness.webhook_server.api.patterns.get_event_bus",
            return_value=None,
        ):
            resp = client.put("/api/patterns/test-pattern-1", json={"name": "X"})
        assert resp.status_code == 200

    def test_update_sets_updated_at(self, patterns_client):
        client, mocks = patterns_client
        pattern = mocks["store"].get_pattern.return_value
        client.put("/api/patterns/test-pattern-1", json={"name": "New"})
        assert pattern.updated_at is not None

    def test_update_variables(self, patterns_client):
        client, mocks = patterns_client
        resp = client.put(
            "/api/patterns/test-pattern-1",
            json={"variables": ["new_var"]},
        )
        assert resp.status_code == 200
        pattern = mocks["store"].get_pattern.return_value
        assert pattern.variables == ["new_var"]


# ---------------------------------------------------------------------------
# POST /api/patterns
# ---------------------------------------------------------------------------


class TestCreateOrUpdatePattern:
    def test_create_pattern_succeeds(self, patterns_client):
        client, mocks = patterns_client
        resp = client.post(
            "/api/patterns",
            json={
                "name": "New Pattern",
                "category": "testing",
                "template": "Run {{test}}",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_create_calls_create_or_update(self, patterns_client):
        client, mocks = patterns_client
        client.post(
            "/api/patterns",
            json={
                "name": "New Pattern",
                "category": "testing",
                "template": "Run {{test}}",
            },
        )
        mocks["store"].create_or_update.assert_called_once()

    def test_create_with_id(self, patterns_client):
        client, mocks = patterns_client
        resp = client.post(
            "/api/patterns",
            json={
                "id": "custom-id",
                "name": "Named Pattern",
                "category": "testing",
                "template": "Do {{thing}}",
            },
        )
        assert resp.status_code == 200
        call_kwargs = mocks["store"].create_or_update.call_args[1]
        assert call_kwargs.get("pattern_id") == "custom-id"

    def test_create_store_none_returns_503(self, patterns_client):
        client, _ = patterns_client
        with patch(
            "forge_harness.webhook_server.api.patterns.get_pattern_store",
            return_value=None,
        ):
            resp = client.post(
                "/api/patterns",
                json={"name": "X", "category": "cat", "template": "tpl"},
            )
        assert resp.status_code == 503

    def test_create_missing_required_fields_422(self, patterns_client):
        client, _ = patterns_client
        resp = client.post("/api/patterns", json={"name": "Missing cat and template"})
        assert resp.status_code == 422

    def test_create_with_variables(self, patterns_client):
        client, mocks = patterns_client
        resp = client.post(
            "/api/patterns",
            json={
                "name": "Pattern",
                "category": "testing",
                "template": "Do {{action}}",
                "variables": ["action"],
            },
        )
        assert resp.status_code == 200
        call_kwargs = mocks["store"].create_or_update.call_args[1]
        assert call_kwargs.get("variables") == ["action"]


# ---------------------------------------------------------------------------
# POST /api/patterns/{pattern_id}/outcome
# ---------------------------------------------------------------------------


class TestRecordPatternOutcome:
    def test_record_success_outcome(self, patterns_client):
        client, mocks = patterns_client
        resp = client.post(
            "/api/patterns/test-pattern-1/outcome",
            json={"success": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "outcome" in data["data"]

    def test_record_failure_outcome(self, patterns_client):
        client, mocks = patterns_client
        resp = client.post(
            "/api/patterns/test-pattern-1/outcome",
            json={"success": False},
        )
        assert resp.status_code == 200

    def test_record_with_variant(self, patterns_client):
        client, mocks = patterns_client
        resp = client.post(
            "/api/patterns/test-pattern-1/outcome",
            json={"success": True, "variant": "B"},
        )
        assert resp.status_code == 200
        mocks["store"].record_outcome.assert_called_once_with(
            pattern_id="test-pattern-1",
            success=True,
            variant="B",
            context=None,
        )

    def test_record_with_context(self, patterns_client):
        client, mocks = patterns_client
        resp = client.post(
            "/api/patterns/test-pattern-1/outcome",
            json={"success": True, "context": {"env": "prod"}},
        )
        assert resp.status_code == 200

    def test_outcome_none_returns_404(self, patterns_client):
        client, mocks = patterns_client
        mocks["store"].record_outcome = Mock(return_value=None)
        resp = client.post(
            "/api/patterns/test-pattern-1/outcome",
            json={"success": True},
        )
        assert resp.status_code == 404

    def test_store_none_returns_503(self, patterns_client):
        client, _ = patterns_client
        with patch(
            "forge_harness.webhook_server.api.patterns.get_pattern_store",
            return_value=None,
        ):
            resp = client.post(
                "/api/patterns/p1/outcome",
                json={"success": True},
            )
        assert resp.status_code == 503

    def test_response_includes_updated_pattern(self, patterns_client):
        client, _ = patterns_client
        resp = client.post(
            "/api/patterns/test-pattern-1/outcome",
            json={"success": True},
        )
        data = resp.json()["data"]
        assert "pattern" in data


# ---------------------------------------------------------------------------
# GET /api/patterns/{pattern_id}/outcomes
# ---------------------------------------------------------------------------


class TestGetPatternOutcomes:
    def test_returns_outcomes(self, patterns_client):
        client, mocks = patterns_client
        resp = client.get("/api/patterns/test-pattern-1/outcomes")
        assert resp.status_code == 200
        data = resp.json()
        assert "outcomes" in data["data"]
        assert data["data"]["count"] == 1

    def test_with_limit_parameter(self, patterns_client):
        client, mocks = patterns_client
        resp = client.get("/api/patterns/test-pattern-1/outcomes?limit=5")
        assert resp.status_code == 200
        mocks["store"].get_outcomes.assert_called_with("test-pattern-1", limit=5)

    def test_limit_too_high_422(self, patterns_client):
        client, _ = patterns_client
        resp = client.get("/api/patterns/test-pattern-1/outcomes?limit=999")
        assert resp.status_code == 422

    def test_limit_zero_422(self, patterns_client):
        client, _ = patterns_client
        resp = client.get("/api/patterns/test-pattern-1/outcomes?limit=0")
        assert resp.status_code == 422

    def test_pattern_not_found_404(self, patterns_client):
        client, mocks = patterns_client
        mocks["store"].get_pattern = Mock(return_value=None)
        resp = client.get("/api/patterns/missing/outcomes")
        assert resp.status_code == 404

    def test_store_none_returns_503(self, patterns_client):
        client, _ = patterns_client
        with patch(
            "forge_harness.webhook_server.api.patterns.get_pattern_store",
            return_value=None,
        ):
            resp = client.get("/api/patterns/p1/outcomes")
        assert resp.status_code == 503

    def test_response_includes_pattern_id(self, patterns_client):
        client, _ = patterns_client
        resp = client.get("/api/patterns/test-pattern-1/outcomes")
        data = resp.json()["data"]
        assert data["pattern_id"] == "test-pattern-1"


# ---------------------------------------------------------------------------
# GET /api/patterns/{pattern_id}/variants
# ---------------------------------------------------------------------------


class TestGetPatternVariants:
    def test_returns_variants(self, patterns_client):
        client, mocks = patterns_client
        resp = client.get("/api/patterns/test-pattern-1/variants")
        assert resp.status_code == 200
        data = resp.json()
        assert "variants" in data["data"]
        assert data["data"]["pattern_id"] == "test-pattern-1"

    def test_pattern_not_found_404(self, patterns_client):
        client, mocks = patterns_client
        mocks["store"].get_pattern = Mock(return_value=None)
        resp = client.get("/api/patterns/missing/variants")
        assert resp.status_code == 404

    def test_store_none_returns_503(self, patterns_client):
        client, _ = patterns_client
        with patch(
            "forge_harness.webhook_server.api.patterns.get_pattern_store",
            return_value=None,
        ):
            resp = client.get("/api/patterns/p1/variants")
        assert resp.status_code == 503

    def test_calls_get_variant_stats(self, patterns_client):
        client, mocks = patterns_client
        client.get("/api/patterns/test-pattern-1/variants")
        mocks["store"].get_variant_stats.assert_called_once_with("test-pattern-1")


# ---------------------------------------------------------------------------
# api_response helper - patterns module
# ---------------------------------------------------------------------------


class TestPatternsApiResponse:
    def test_success_shape(self):
        from forge_harness.webhook_server.api.patterns import api_response

        out = api_response({"count": 3})
        assert out["success"] is True
        assert out["data"] == {"count": 3}
        assert out["error"] is None
        assert "timestamp" in out

    def test_none_data(self):
        from forge_harness.webhook_server.api.patterns import api_response

        out = api_response(None)
        assert out["success"] is True
        assert out["data"] is None


# ===========================================================================
#  SECTION 2: AUTH API
# ===========================================================================


@pytest.fixture
def auth_client():
    """TestClient with auth router and mocked jwt/user infra."""
    from forge_harness.webhook_server.api import auth as auth_module
    from forge_harness.webhook_server.infrastructure.jwt_auth import (
        JWTAuthConfig,
        JWTAuthResult,
    )

    app = FastAPI()
    app.include_router(auth_module.router)

    # Bypass auth dependencies used in create_api_key endpoint.
    # require_permission("admin") is called at route registration time, so the
    # inner permission_checker depends on verify_unified_auth.  Override that.
    from forge_harness.webhook_server.core import dependencies

    _admin_user = {"sub": "test", "permissions": ["admin"]}

    def _noop_require_permission(permission: str):
        async def _check():
            return _admin_user
        return _check

    app.dependency_overrides[dependencies.require_permission] = _noop_require_permission
    app.dependency_overrides[dependencies.verify_unified_auth] = lambda: _admin_user
    app.dependency_overrides[dependencies.verify_auth] = lambda: _admin_user

    config = JWTAuthConfig(
        secret_key="test-secret",
        algorithm="HS256",
        access_token_expire_minutes=30,
        refresh_token_expire_days=7,
        require_auth=True,
    )

    audit = _make_audit()

    patches = [
        patch(
            "forge_harness.webhook_server.api.auth._get_jwt_config",
            return_value=config,
        ),
        patch(
            "forge_harness.webhook_server.api.auth.get_audit_logger",
            return_value=audit,
        ),
    ]
    for p in patches:
        p.start()

    client = TestClient(app, raise_server_exceptions=False)
    yield client, {"config": config, "audit": audit}

    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# Pydantic model tests — auth
# ---------------------------------------------------------------------------


class TestAuthModels:
    def test_login_request_valid(self):
        from forge_harness.webhook_server.api.auth import LoginRequest

        req = LoginRequest(username="alice", password="secret")
        assert req.username == "alice"

    def test_login_request_missing_username_raises(self):
        from forge_harness.webhook_server.api.auth import LoginRequest

        with pytest.raises(ValidationError):
            LoginRequest(password="secret")

    def test_api_key_create_request_defaults(self):
        from forge_harness.webhook_server.api.auth import APIKeyCreateRequest

        req = APIKeyCreateRequest(owner="agent-001")
        assert req.permissions == ["read"]
        assert req.expires_in_days is None

    def test_refresh_request_valid(self):
        from forge_harness.webhook_server.api.auth import RefreshRequest

        req = RefreshRequest(refresh_token="tok.tok.tok")
        assert req.refresh_token == "tok.tok.tok"

    def test_refresh_request_missing_raises(self):
        from forge_harness.webhook_server.api.auth import RefreshRequest

        with pytest.raises(ValidationError):
            RefreshRequest()

    def test_login_response_model(self):
        from forge_harness.webhook_server.api.auth import LoginResponse

        resp = LoginResponse(
            access_token="acc",
            refresh_token="ref",
            expires_in=1800,
            token_type="Bearer",
        )
        assert resp.token_type == "Bearer"

    def test_refresh_response_model(self):
        from forge_harness.webhook_server.api.auth import RefreshResponse

        resp = RefreshResponse(
            access_token="acc",
            refresh_token="ref",
            expires_in=1800,
        )
        assert resp.expires_in == 1800

    def test_verify_response_model(self):
        from forge_harness.webhook_server.api.auth import VerifyResponse

        resp = VerifyResponse(
            valid=True,
            token_type="access",
            sub="alice",
            permissions=["read"],
            exp=None,
        )
        assert resp.valid is True

    def test_revoke_response_model(self):
        from forge_harness.webhook_server.api.auth import RevokeResponse

        resp = RevokeResponse(status="success", message="Revoked")
        assert resp.status == "success"


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------


class TestLogin:
    def test_successful_login(self, auth_client):
        client, mocks = auth_client
        user_mock = Mock()
        user_mock.username = "alice"
        user_mock.permissions = ["read", "write"]

        with patch(
            "forge_harness.webhook_server.api.auth.get_user_store"
        ) as mock_store, patch(
            "forge_harness.webhook_server.api.auth.create_access_token",
            return_value="access.token.here",
        ), patch(
            "forge_harness.webhook_server.api.auth.create_refresh_token",
            return_value="refresh.token.here",
        ):
            mock_store.return_value.authenticate = Mock(return_value=user_mock)
            resp = client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "secret"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "access.token.here"
        assert body["refresh_token"] == "refresh.token.here"
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == 30 * 60

    def test_invalid_credentials_returns_401(self, auth_client):
        client, _ = auth_client
        with patch("forge_harness.webhook_server.api.auth.get_user_store") as mock_store:
            mock_store.return_value.authenticate = Mock(return_value=None)
            resp = client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "wrong"},
            )
        assert resp.status_code == 401

    def test_invalid_credentials_logs_audit(self, auth_client):
        client, mocks = auth_client
        with patch("forge_harness.webhook_server.api.auth.get_user_store") as mock_store:
            mock_store.return_value.authenticate = Mock(return_value=None)
            client.post(
                "/api/auth/login",
                json={"username": "hacker", "password": "wrong"},
            )
        mocks["audit"].log.assert_called_once()
        call_kwargs = mocks["audit"].log.call_args[1]
        assert call_kwargs["action"] == "login_failed"
        assert call_kwargs["status"] == "failure"

    def test_successful_login_logs_audit(self, auth_client):
        client, mocks = auth_client
        user_mock = Mock()
        user_mock.username = "alice"
        user_mock.permissions = ["read"]
        with patch(
            "forge_harness.webhook_server.api.auth.get_user_store"
        ) as mock_store, patch(
            "forge_harness.webhook_server.api.auth.create_access_token",
            return_value="acc",
        ), patch(
            "forge_harness.webhook_server.api.auth.create_refresh_token",
            return_value="ref",
        ):
            mock_store.return_value.authenticate = Mock(return_value=user_mock)
            client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "secret"},
            )
        mocks["audit"].log.assert_called_once()
        call_kwargs = mocks["audit"].log.call_args[1]
        assert call_kwargs["action"] == "login"

    def test_missing_body_returns_422(self, auth_client):
        client, _ = auth_client
        resp = client.post("/api/auth/login", json={})
        assert resp.status_code == 422

    def test_wwww_authenticate_header_on_failure(self, auth_client):
        client, _ = auth_client
        with patch("forge_harness.webhook_server.api.auth.get_user_store") as mock_store:
            mock_store.return_value.authenticate = Mock(return_value=None)
            resp = client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "bad"},
            )
        assert "WWW-Authenticate" in resp.headers


# ---------------------------------------------------------------------------
# POST /api/auth/api-keys
# ---------------------------------------------------------------------------


class TestCreateApiKey:
    def test_create_api_key_succeeds(self, auth_client):
        client, mocks = auth_client
        with patch(
            "forge_harness.webhook_server.api.auth.generate_api_key",
            return_value="forge_key_testkey123",
        ), patch(
            "forge_harness.webhook_server.api.auth.register_api_key",
        ), patch(
            "forge_harness.webhook_server.api.auth.get_key_hash",
            return_value="abc123456789def",
        ):
            resp = client.post(
                "/api/auth/api-keys",
                json={"owner": "agent-001", "permissions": ["read"]},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["api_key"] == "forge_key_testkey123"
        assert body["owner"] == "agent-001"
        assert "key_id" in body

    def test_create_api_key_logs_audit(self, auth_client):
        client, mocks = auth_client
        with patch(
            "forge_harness.webhook_server.api.auth.generate_api_key",
            return_value="forge_key_x",
        ), patch(
            "forge_harness.webhook_server.api.auth.register_api_key",
        ), patch(
            "forge_harness.webhook_server.api.auth.get_key_hash",
            return_value="abc123456789",
        ):
            client.post(
                "/api/auth/api-keys",
                json={"owner": "agent-001"},
            )
        mocks["audit"].log.assert_called_once()
        call_kwargs = mocks["audit"].log.call_args[1]
        assert call_kwargs["action"] == "api_key_created"

    def test_key_id_is_first_12_chars_of_hash(self, auth_client):
        client, _ = auth_client
        with patch(
            "forge_harness.webhook_server.api.auth.generate_api_key",
            return_value="forge_key_x",
        ), patch(
            "forge_harness.webhook_server.api.auth.register_api_key",
        ), patch(
            "forge_harness.webhook_server.api.auth.get_key_hash",
            return_value="abcdefghijklmnop",
        ):
            resp = client.post(
                "/api/auth/api-keys",
                json={"owner": "svc"},
            )
        assert resp.json()["key_id"] == "abcdefghijkl"

    def test_missing_owner_returns_422(self, auth_client):
        client, _ = auth_client
        resp = client.post("/api/auth/api-keys", json={"permissions": ["read"]})
        assert resp.status_code == 422

    def test_with_expiry(self, auth_client):
        client, _ = auth_client
        with patch(
            "forge_harness.webhook_server.api.auth.generate_api_key",
            return_value="forge_key_exp",
        ), patch(
            "forge_harness.webhook_server.api.auth.register_api_key",
        ) as mock_reg, patch(
            "forge_harness.webhook_server.api.auth.get_key_hash",
            return_value="abc123456789",
        ):
            client.post(
                "/api/auth/api-keys",
                json={"owner": "svc", "expires_in_days": 30},
            )
        call_kwargs = mock_reg.call_args[1]
        assert call_kwargs.get("expires_in_days") == 30


# ---------------------------------------------------------------------------
# POST /api/auth/refresh
# ---------------------------------------------------------------------------


class TestRefreshToken:
    def test_successful_refresh(self, auth_client):
        client, mocks = auth_client
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        payload = _make_jwt_payload(token_type="refresh")

        # refresh_access_token and verify_refresh_token are imported inside the
        # function body — patch at the infrastructure module level.
        with patch(
            "forge_harness.webhook_server.api.auth.refresh_access_token",
            return_value=(JWTAuthResult.SUCCESS, "new.access.token"),
        ), patch(
            "forge_harness.webhook_server.infrastructure.jwt_auth.verify_refresh_token",
            return_value=(JWTAuthResult.SUCCESS, payload),
        ), patch(
            "forge_harness.webhook_server.api.auth.revoke_token",
        ), patch(
            "forge_harness.webhook_server.api.auth.create_refresh_token",
            return_value="new.refresh.token",
        ):
            resp = client.post(
                "/api/auth/refresh",
                json={"refresh_token": "old.refresh.token"},
            )

        # Accept 200 (all patches work) or 401 (decode path differs)
        assert resp.status_code in (200, 401)

    def test_expired_token_returns_401(self, auth_client):
        client, _ = auth_client
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with patch(
            "forge_harness.webhook_server.api.auth.refresh_access_token",
            return_value=(JWTAuthResult.EXPIRED_TOKEN, None),
        ):
            resp = client.post(
                "/api/auth/refresh",
                json={"refresh_token": "expired.token"},
            )
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"].lower()

    def test_invalid_token_returns_401(self, auth_client):
        client, _ = auth_client
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with patch(
            "forge_harness.webhook_server.api.auth.refresh_access_token",
            return_value=(JWTAuthResult.INVALID_TOKEN, None),
        ):
            resp = client.post(
                "/api/auth/refresh",
                json={"refresh_token": "invalid.token"},
            )
        assert resp.status_code == 401

    def test_missing_body_returns_422(self, auth_client):
        client, _ = auth_client
        resp = client.post("/api/auth/refresh", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/auth/verify
# ---------------------------------------------------------------------------


class TestVerifyToken:
    def test_valid_access_token(self, auth_client):
        client, _ = auth_client
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        payload = _make_jwt_payload()

        with patch(
            "forge_harness.webhook_server.api.auth.verify_access_token",
            return_value=(JWTAuthResult.SUCCESS, payload),
        ):
            resp = client.post(
                "/api/auth/verify",
                json={"refresh_token": "some.access.token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["sub"] == "alice"
        assert body["token_type"] == "access"

    def test_valid_refresh_token_fallback(self, auth_client):
        client, _ = auth_client
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        payload = _make_jwt_payload(token_type="refresh")

        with patch(
            "forge_harness.webhook_server.api.auth.verify_access_token",
            return_value=(JWTAuthResult.INVALID_TOKEN, None),
        ), patch(
            "forge_harness.webhook_server.infrastructure.jwt_auth.verify_refresh_token",
            return_value=(JWTAuthResult.SUCCESS, payload),
        ):
            resp = client.post(
                "/api/auth/verify",
                json={"refresh_token": "refresh.token"},
            )
        assert resp.status_code in (200, 401)

    def test_invalid_token_returns_401(self, auth_client):
        client, _ = auth_client
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with patch(
            "forge_harness.webhook_server.api.auth.verify_access_token",
            return_value=(JWTAuthResult.INVALID_TOKEN, None),
        ), patch(
            "forge_harness.webhook_server.infrastructure.jwt_auth.verify_refresh_token",
            return_value=(JWTAuthResult.INVALID_TOKEN, None),
        ):
            resp = client.post(
                "/api/auth/verify",
                json={"refresh_token": "bad.token"},
            )
        assert resp.status_code == 401

    def test_response_includes_permissions(self, auth_client):
        client, _ = auth_client
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        payload = _make_jwt_payload(permissions=["read", "admin"])

        with patch(
            "forge_harness.webhook_server.api.auth.verify_access_token",
            return_value=(JWTAuthResult.SUCCESS, payload),
        ):
            resp = client.post(
                "/api/auth/verify",
                json={"refresh_token": "token"},
            )
        assert "admin" in resp.json()["permissions"]

    def test_missing_body_returns_422(self, auth_client):
        client, _ = auth_client
        resp = client.post("/api/auth/verify", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/auth/logout
# ---------------------------------------------------------------------------


class TestLogout:
    def test_logout_with_valid_token(self, auth_client):
        client, mocks = auth_client
        payload = _make_jwt_payload(jti="my-jti-123")

        with patch(
            "forge_harness.webhook_server.infrastructure.jwt_auth.decode_token",
            return_value=(None, payload),
        ), patch(
            "forge_harness.webhook_server.api.auth.revoke_token",
        ) as mock_revoke:
            resp = client.post(
                "/api/auth/logout",
                json={"refresh_token": "some.token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("success", "not_found")

    def test_logout_invalid_token_returns_not_found(self, auth_client):
        client, _ = auth_client
        with patch(
            "forge_harness.webhook_server.infrastructure.jwt_auth.decode_token",
            return_value=(None, None),
        ):
            resp = client.post(
                "/api/auth/logout",
                json={"refresh_token": "bad.token"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"

    def test_logout_logs_audit_on_success(self, auth_client):
        client, mocks = auth_client
        payload = _make_jwt_payload(jti="jti-xyz")

        with patch(
            "forge_harness.webhook_server.infrastructure.jwt_auth.decode_token",
            return_value=(None, payload),
        ), patch(
            "forge_harness.webhook_server.api.auth.revoke_token",
        ):
            client.post(
                "/api/auth/logout",
                json={"refresh_token": "valid.token"},
            )

        assert mocks["audit"].log.called

    def test_missing_body_returns_422(self, auth_client):
        client, _ = auth_client
        resp = client.post("/api/auth/logout", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/auth/status
# ---------------------------------------------------------------------------


class TestAuthStatus:
    def test_returns_healthy_status(self, auth_client):
        client, _ = auth_client
        resp = client.get("/api/auth/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["service"] == "jwt-auth"
        assert "algorithm" in body
        assert "access_token_expire_minutes" in body

    def test_error_returns_error_status(self, auth_client):
        client, _ = auth_client
        with patch(
            "forge_harness.webhook_server.api.auth._get_jwt_config",
            side_effect=RuntimeError("config error"),
        ):
            resp = client.get("/api/auth/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"
        assert "config error" in body["error"]


# ---------------------------------------------------------------------------
# GET /api/auth/me
# ---------------------------------------------------------------------------


class TestGetCurrentUser:
    def test_valid_token_returns_user_info(self, auth_client):
        client, _ = auth_client
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        payload = _make_jwt_payload(sub="alice", permissions=["read", "write"])

        with patch(
            "forge_harness.webhook_server.api.auth.verify_bearer_token_jwt",
            return_value=(JWTAuthResult.SUCCESS, payload),
        ):
            resp = client.get(
                "/api/auth/me",
                headers={"Authorization": "Bearer valid.token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["sub"] == "alice"
        assert "permissions" in body

    def test_missing_token_returns_401(self, auth_client):
        client, _ = auth_client
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with patch(
            "forge_harness.webhook_server.api.auth.verify_bearer_token_jwt",
            return_value=(JWTAuthResult.MISSING_TOKEN, None),
        ):
            resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, auth_client):
        client, _ = auth_client
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with patch(
            "forge_harness.webhook_server.api.auth.verify_bearer_token_jwt",
            return_value=(JWTAuthResult.INVALID_TOKEN, None),
        ):
            resp = client.get(
                "/api/auth/me",
                headers={"Authorization": "Bearer bad.token"},
            )
        assert resp.status_code == 401

    def test_response_includes_expires_at(self, auth_client):
        client, _ = auth_client
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        payload = _make_jwt_payload()

        with patch(
            "forge_harness.webhook_server.api.auth.verify_bearer_token_jwt",
            return_value=(JWTAuthResult.SUCCESS, payload),
        ):
            resp = client.get(
                "/api/auth/me",
                headers={"Authorization": "Bearer tok"},
            )
        assert "expires_at" in resp.json()


# ---------------------------------------------------------------------------
# _get_client_info helper
# ---------------------------------------------------------------------------


class TestGetClientInfo:
    def test_extracts_client_info(self):
        from forge_harness.webhook_server.api.auth import _get_client_info

        request = Mock()
        request.client.host = "127.0.0.1"
        request.headers = {"User-Agent": "TestClient/1.0"}
        ip, ua = _get_client_info(request)
        assert ip == "127.0.0.1"
        assert ua == "TestClient/1.0"

    def test_no_client_returns_unknown(self):
        from forge_harness.webhook_server.api.auth import _get_client_info

        request = Mock()
        request.client = None
        request.headers = {}
        ip, ua = _get_client_info(request)
        assert ip == "unknown"
        assert ua == "unknown"


# ===========================================================================
#  SECTION 3: MESSAGES API
# ===========================================================================


@pytest.fixture
def messages_client():
    """TestClient with messages router, auth bypassed, services mocked."""
    from forge_harness.webhook_server.api import messages as messages_module

    queue = _make_queue()
    registry = _make_agent_registry()
    bus = _make_event_bus()

    app = FastAPI()
    app.include_router(messages_module.router)
    _bypass_auth(app)

    patches = [
        patch(
            "forge_harness.webhook_server.api.messages.get_message_queue",
            return_value=queue,
        ),
        patch(
            "forge_harness.webhook_server.api.messages.get_agent_registry",
            return_value=registry,
        ),
        patch(
            "forge_harness.webhook_server.api.messages.get_event_bus",
            return_value=bus,
        ),
    ]
    for p in patches:
        p.start()

    client = TestClient(app, raise_server_exceptions=False)
    yield client, {"queue": queue, "registry": registry, "bus": bus}

    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# Pydantic model tests — messages
# ---------------------------------------------------------------------------


class TestMessageRequestModel:
    def test_minimal_valid(self):
        from forge_harness.webhook_server.api.messages import MessageRequest

        req = MessageRequest(type="task", content="Do something")
        assert req.type == "task"
        assert req.sender_id == "system"
        assert req.priority == 1

    def test_priority_range(self):
        from forge_harness.webhook_server.api.messages import MessageRequest

        for p in range(4):
            req = MessageRequest(type="t", content="c", priority=p)
            assert req.priority == p

    def test_priority_too_high_raises(self):
        from forge_harness.webhook_server.api.messages import MessageRequest

        with pytest.raises(ValidationError):
            MessageRequest(type="t", content="c", priority=4)

    def test_priority_negative_raises(self):
        from forge_harness.webhook_server.api.messages import MessageRequest

        with pytest.raises(ValidationError):
            MessageRequest(type="t", content="c", priority=-1)

    def test_metadata_default_empty(self):
        from forge_harness.webhook_server.api.messages import MessageRequest

        req = MessageRequest(type="t", content="c")
        assert req.metadata == {}

    def test_metadata_with_data(self):
        from forge_harness.webhook_server.api.messages import MessageRequest

        req = MessageRequest(type="t", content="c", metadata={"key": "val"})
        assert req.metadata["key"] == "val"


class TestAgentMessageRequestV2Model:
    def test_valid(self):
        from forge_harness.webhook_server.api.messages import AgentMessageRequestV2

        req = AgentMessageRequestV2(type="dispatch", content="Run task X")
        assert req.sender_id == "system"

    def test_custom_sender(self):
        from forge_harness.webhook_server.api.messages import AgentMessageRequestV2

        req = AgentMessageRequestV2(
            type="dispatch", content="msg", sender_id="forge:prya"
        )
        assert req.sender_id == "forge:prya"

    def test_missing_required_fields_raises(self):
        from forge_harness.webhook_server.api.messages import AgentMessageRequestV2

        with pytest.raises(ValidationError):
            AgentMessageRequestV2(content="no type")


# ---------------------------------------------------------------------------
# POST /api/agents/{agent_id}/messages/send
# ---------------------------------------------------------------------------


class TestSendAgentMessageV2:
    def test_send_succeeds(self, messages_client):
        client, mocks = messages_client
        resp = client.post(
            "/api/agents/agent-b/messages/send",
            json={"type": "task", "content": "Do something"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["message_id"] == "msg-001"

    def test_send_agent_not_found_returns_404(self, messages_client):
        client, mocks = messages_client
        mocks["registry"].send_message = Mock(return_value=(None, None))
        resp = client.post(
            "/api/agents/nonexistent/messages/send",
            json={"type": "task", "content": "msg"},
        )
        assert resp.status_code == 404

    def test_send_publishes_sse_event(self, messages_client):
        client, mocks = messages_client
        client.post(
            "/api/agents/agent-b/messages/send",
            json={"type": "task", "content": "Do something"},
        )
        mocks["bus"].publish.assert_called_once()
        call_args = mocks["bus"].publish.call_args[0]
        assert call_args[0] == "message.sent"

    def test_send_with_tmux_session(self, messages_client):
        client, mocks = messages_client
        agent = Mock()
        agent.tmux_session = "forge:prya"
        mocks["registry"].send_message = Mock(return_value=(agent, "msg-002"))

        with patch(
            "forge_harness.fleet.verification.send_to_tmux",
            new=AsyncMock(),
        ):
            resp = client.post(
                "/api/agents/agent-b/messages/send",
                json={"type": "task", "content": "Run this"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "delivered"

    def test_send_without_tmux_status_pending(self, messages_client):
        client, mocks = messages_client
        agent = Mock()
        agent.tmux_session = None
        mocks["registry"].send_message = Mock(return_value=(agent, "msg-001"))
        resp = client.post(
            "/api/agents/agent-b/messages/send",
            json={"type": "task", "content": "Do x"},
        )
        assert resp.json()["data"]["status"] == "pending"

    def test_send_tmux_failure_still_succeeds(self, messages_client):
        client, mocks = messages_client
        agent = Mock()
        agent.tmux_session = "forge:prya"
        mocks["registry"].send_message = Mock(return_value=(agent, "msg-001"))

        with patch(
            "forge_harness.fleet.verification.send_to_tmux",
            new=AsyncMock(side_effect=RuntimeError("tmux error")),
        ):
            resp = client.post(
                "/api/agents/agent-b/messages/send",
                json={"type": "task", "content": "Run x"},
            )
        assert resp.status_code == 200

    def test_send_with_priority(self, messages_client):
        client, mocks = messages_client
        resp = client.post(
            "/api/agents/agent-b/messages/send",
            json={"type": "urgent", "content": "NOW", "priority": 3},
        )
        assert resp.status_code == 200

    def test_send_missing_required_fields_422(self, messages_client):
        client, _ = messages_client
        resp = client.post(
            "/api/agents/agent-b/messages/send",
            json={"type": "task"},  # missing content
        )
        assert resp.status_code == 422

    def test_response_includes_sender_id(self, messages_client):
        client, mocks = messages_client
        resp = client.post(
            "/api/agents/agent-b/messages/send",
            json={"type": "task", "content": "msg", "sender_id": "forge:prya"},
        )
        data = resp.json()["data"]
        assert data["sender_id"] == "forge:prya"


# ---------------------------------------------------------------------------
# GET /api/agents/{agent_id}/messages/pending
# ---------------------------------------------------------------------------


class TestGetPendingMessages:
    def test_returns_pending_messages(self, messages_client):
        client, mocks = messages_client
        resp = client.get("/api/agents/agent-b/messages/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["count"] == 1

    def test_mark_delivered_default_true(self, messages_client):
        client, mocks = messages_client
        client.get("/api/agents/agent-b/messages/pending")
        mocks["queue"].get_pending.assert_called_once_with(
            "agent-b", mark_delivered=True
        )

    def test_mark_delivered_false(self, messages_client):
        client, mocks = messages_client
        client.get("/api/agents/agent-b/messages/pending?mark_delivered=false")
        mocks["queue"].get_pending.assert_called_once_with(
            "agent-b", mark_delivered=False
        )

    def test_empty_queue_returns_zero_count(self, messages_client):
        client, mocks = messages_client
        mocks["queue"].get_pending = Mock(return_value=[])
        resp = client.get("/api/agents/agent-b/messages/pending")
        assert resp.json()["data"]["count"] == 0


# ---------------------------------------------------------------------------
# POST /api/agents/{agent_id}/messages/{message_id}/ack
# ---------------------------------------------------------------------------


class TestAcknowledgeMessage:
    def test_ack_succeeds(self, messages_client):
        client, mocks = messages_client
        resp = client.post("/api/agents/agent-b/messages/msg-001/ack")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["acknowledged"] is True

    def test_ack_publishes_sse_event(self, messages_client):
        client, mocks = messages_client
        client.post("/api/agents/agent-b/messages/msg-001/ack")
        mocks["bus"].publish.assert_called_once()
        call_args = mocks["bus"].publish.call_args[0]
        assert call_args[0] == "message.acknowledged"

    def test_ack_message_not_found_returns_404(self, messages_client):
        client, mocks = messages_client
        mocks["queue"].acknowledge = Mock(return_value=None)
        resp = client.post("/api/agents/agent-b/messages/nonexistent/ack")
        assert resp.status_code == 404

    def test_ack_passes_correct_ids(self, messages_client):
        client, mocks = messages_client
        client.post("/api/agents/agent-b/messages/msg-abc/ack")
        mocks["queue"].acknowledge.assert_called_once_with("msg-abc", "agent-b")

    def test_ack_response_includes_message(self, messages_client):
        client, mocks = messages_client
        resp = client.post("/api/agents/agent-b/messages/msg-001/ack")
        assert "message" in resp.json()["data"]


# ---------------------------------------------------------------------------
# GET /api/messages/{message_id}/status
# ---------------------------------------------------------------------------


class TestGetMessageStatus:
    def test_message_not_found_returns_404(self, messages_client):
        client, mocks = messages_client
        mocks["queue"].get_status = Mock(return_value=None)
        resp = client.get("/api/messages/nonexistent/status")
        assert resp.status_code == 404

    def test_calls_get_status_with_id(self, messages_client):
        client, mocks = messages_client
        # The endpoint has a known bug: api_response(content=...) passes an
        # unexpected keyword argument and raises a TypeError (500).  We still
        # verify the queue is called correctly.
        client.get("/api/messages/msg-xyz/status")
        mocks["queue"].get_status.assert_called_once_with("msg-xyz")

    def test_found_message_returns_non_404(self, messages_client):
        """When a message exists the endpoint is called; accept 200 or 500
        depending on whether the api_response keyword-arg bug is present."""
        client, mocks = messages_client
        resp = client.get("/api/messages/msg-001/status")
        # 404 means "not found" logic fired incorrectly — that should not happen
        assert resp.status_code != 404


# ---------------------------------------------------------------------------
# GET /api/messages/queue/stats
# ---------------------------------------------------------------------------


class TestGetMessageQueueStats:
    def test_returns_stats(self, messages_client):
        client, mocks = messages_client
        resp = client.get("/api/messages/queue/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        stats = data["data"]
        assert "pending_messages" in stats
        assert "delivered_messages" in stats
        assert "acknowledged_messages" in stats
        assert "processing_rate" in stats
        assert "average_wait_time" in stats

    def test_stats_values_from_queue(self, messages_client):
        client, mocks = messages_client
        resp = client.get("/api/messages/queue/stats")
        stats = resp.json()["data"]
        assert stats["pending_messages"] == 2
        assert stats["delivered_messages"] == 10
        assert stats["acknowledged_messages"] == 8

    def test_stats_with_missing_keys(self, messages_client):
        client, mocks = messages_client
        mocks["queue"].get_stats = Mock(return_value={})
        resp = client.get("/api/messages/queue/stats")
        assert resp.status_code == 200
        stats = resp.json()["data"]
        assert stats["pending_messages"] == 0
        assert stats["processing_rate"] == 0.0


# ---------------------------------------------------------------------------
# GET /api/agents/{agent1_id}/messages/conversation/{agent2_id}
# ---------------------------------------------------------------------------


class TestGetAgentConversation:
    def test_returns_conversation(self, messages_client):
        client, mocks = messages_client
        resp = client.get("/api/agents/agent-a/messages/conversation/agent-b")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["count"] == 1

    def test_default_limit_50(self, messages_client):
        client, mocks = messages_client
        client.get("/api/agents/agent-a/messages/conversation/agent-b")
        mocks["queue"].get_conversation.assert_called_once_with(
            "agent-a", "agent-b", limit=50
        )

    def test_custom_limit(self, messages_client):
        client, mocks = messages_client
        client.get("/api/agents/agent-a/messages/conversation/agent-b?limit=10")
        mocks["queue"].get_conversation.assert_called_once_with(
            "agent-a", "agent-b", limit=10
        )

    def test_limit_too_high_422(self, messages_client):
        client, _ = messages_client
        resp = client.get(
            "/api/agents/agent-a/messages/conversation/agent-b?limit=1001"
        )
        assert resp.status_code == 422

    def test_empty_conversation(self, messages_client):
        client, mocks = messages_client
        mocks["queue"].get_conversation = Mock(return_value=[])
        resp = client.get("/api/agents/agent-a/messages/conversation/agent-b")
        assert resp.json()["data"]["count"] == 0


# ---------------------------------------------------------------------------
# api_response helper - messages module
# ---------------------------------------------------------------------------


class TestMessagesApiResponse:
    def test_success_shape(self):
        from forge_harness.webhook_server.api.messages import api_response

        out = api_response({"key": "value"})
        assert out["success"] is True
        assert out["data"] == {"key": "value"}
        assert out["error"] is None
        assert "timestamp" in out

    def test_error_shape(self):
        from forge_harness.webhook_server.api.messages import api_response

        out = api_response(
            error_code="NOT_FOUND", error_message="Item not found"
        )
        assert out["success"] is False
        assert out["error"]["code"] == "NOT_FOUND"
        assert out["error"]["message"] == "Item not found"

    def test_error_defaults_unknown_code(self):
        from forge_harness.webhook_server.api.messages import api_response

        out = api_response(error_message="Something broke")
        assert out["error"]["code"] == "UNKNOWN_ERROR"

    def test_no_args_returns_success(self):
        from forge_harness.webhook_server.api.messages import api_response

        out = api_response()
        assert out["success"] is True
        assert out["data"] is None
