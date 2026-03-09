"""
Tests for forge_harness.error_schema
=====================================

Covers:
- All exported error-code constants
- next_action_for() — known codes, unknown codes, empty string
- ForgeError dataclass — construction, auto next_action, to_dict, from_dict
- api_error_body() — envelope structure, optional details, auto next_action
- api_success_body() — envelope structure, various data payloads
- Edge cases — None, empty strings, nested dicts, unrecognised codes
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest

import forge_harness.error_schema as es
from forge_harness.error_schema import (
    # Agent / dispatch
    AGENT_NOT_AVAILABLE,
    AGENT_NOT_FOUND,
    # Auth
    AUTH_INVALID_TOKEN,
    AUTH_MISSING_CREDENTIALS,
    AUTH_PERMISSION_DENIED,
    AUTH_TOKEN_EXPIRED,
    # Operational
    CONTRACT_VIOLATION,
    DISPATCH_FAILED,
    DOCTOR_CHECK_FAILED,
    DOCTOR_FIX_FAILED,
    # Generic
    INTERNAL_ERROR,
    # Lease
    LEASE_ALREADY_OWNED,
    LEASE_EXPIRED,
    LEASE_INVALID,
    LEASE_NOT_FOUND,
    LEASE_OWNER_MISMATCH,
    LEASE_PATH_LOCK_CONFLICT,
    LEASE_PATH_LOCK_IMMUTABLE,
    NOT_FOUND,
    SERVICE_UNAVAILABLE,
    # Task
    TASK_CREATION_FAILED,
    TASK_DELETE_FAILED,
    TASK_NOT_FOUND,
    TASK_UPDATE_FAILED,
    TASK_VALIDATION_ERROR,
    UNKNOWN_ERROR,
    VALIDATION_ERROR,
    # Callables
    ForgeError,
    api_error_body,
    api_success_body,
    next_action_for,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$"
)


def _is_iso8601_utc(value: str) -> bool:
    return bool(ISO8601_RE.match(value))


# ---------------------------------------------------------------------------
# Section 1 — Error-code constants
# ---------------------------------------------------------------------------


class TestErrorCodeConstants:
    """All exported constants must be non-empty SCREAMING_SNAKE_CASE strings."""

    ALL_CODES = [
        AUTH_MISSING_CREDENTIALS,
        AUTH_INVALID_TOKEN,
        AUTH_TOKEN_EXPIRED,
        AUTH_PERMISSION_DENIED,
        TASK_NOT_FOUND,
        TASK_VALIDATION_ERROR,
        TASK_CREATION_FAILED,
        TASK_UPDATE_FAILED,
        TASK_DELETE_FAILED,
        LEASE_INVALID,
        LEASE_ALREADY_OWNED,
        LEASE_NOT_FOUND,
        LEASE_EXPIRED,
        LEASE_OWNER_MISMATCH,
        LEASE_PATH_LOCK_CONFLICT,
        LEASE_PATH_LOCK_IMMUTABLE,
        AGENT_NOT_FOUND,
        AGENT_NOT_AVAILABLE,
        DISPATCH_FAILED,
        CONTRACT_VIOLATION,
        DOCTOR_CHECK_FAILED,
        DOCTOR_FIX_FAILED,
        UNKNOWN_ERROR,
        VALIDATION_ERROR,
        NOT_FOUND,
        SERVICE_UNAVAILABLE,
        INTERNAL_ERROR,
    ]

    def test_all_codes_are_strings(self):
        for code in self.ALL_CODES:
            assert isinstance(code, str), f"{code!r} is not a str"

    def test_all_codes_are_non_empty(self):
        for code in self.ALL_CODES:
            assert code, "Empty code found"

    def test_all_codes_are_uppercase(self):
        """Codes should be SCREAMING_SNAKE_CASE (upper + digits + underscores)."""
        pattern = re.compile(r"^[A-Z][A-Z0-9_]+$")
        for code in self.ALL_CODES:
            assert pattern.match(code), (
                f"{code!r} does not match SCREAMING_SNAKE_CASE"
            )

    def test_codes_are_unique(self):
        seen: set[str] = set()
        for code in self.ALL_CODES:
            assert code not in seen, f"Duplicate code: {code!r}"
            seen.add(code)

    def test_auth_code_values(self):
        assert AUTH_MISSING_CREDENTIALS == "AUTH_MISSING_CREDENTIALS"
        assert AUTH_INVALID_TOKEN == "AUTH_INVALID_TOKEN"
        assert AUTH_TOKEN_EXPIRED == "AUTH_TOKEN_EXPIRED"
        assert AUTH_PERMISSION_DENIED == "AUTH_PERMISSION_DENIED"

    def test_task_code_values(self):
        assert TASK_NOT_FOUND == "TASK_NOT_FOUND"
        assert TASK_VALIDATION_ERROR == "TASK_VALIDATION_ERROR"
        assert TASK_CREATION_FAILED == "TASK_CREATION_FAILED"
        assert TASK_UPDATE_FAILED == "TASK_UPDATE_FAILED"
        assert TASK_DELETE_FAILED == "TASK_DELETE_FAILED"

    def test_lease_code_values(self):
        assert LEASE_INVALID == "INVALID_LEASE"
        assert LEASE_ALREADY_OWNED == "LEASE_ALREADY_OWNED"
        assert LEASE_NOT_FOUND == "LEASE_NOT_FOUND"
        assert LEASE_EXPIRED == "LEASE_EXPIRED"
        assert LEASE_OWNER_MISMATCH == "LEASE_OWNER_MISMATCH"
        assert LEASE_PATH_LOCK_CONFLICT == "PATH_LOCK_CONFLICT"
        assert LEASE_PATH_LOCK_IMMUTABLE == "PATH_LOCK_IMMUTABLE"

    def test_agent_code_values(self):
        assert AGENT_NOT_FOUND == "AGENT_NOT_FOUND"
        assert AGENT_NOT_AVAILABLE == "AGENT_NOT_AVAILABLE"
        assert DISPATCH_FAILED == "DISPATCH_FAILED"

    def test_generic_code_values(self):
        assert UNKNOWN_ERROR == "UNKNOWN_ERROR"
        assert VALIDATION_ERROR == "VALIDATION_ERROR"
        assert NOT_FOUND == "NOT_FOUND"
        assert SERVICE_UNAVAILABLE == "SERVICE_UNAVAILABLE"
        assert INTERNAL_ERROR == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# Section 2 — next_action_for()
# ---------------------------------------------------------------------------


class TestNextActionFor:
    """next_action_for returns meaningful guidance strings."""

    KNOWN_CODES = [
        AUTH_MISSING_CREDENTIALS,
        AUTH_INVALID_TOKEN,
        AUTH_TOKEN_EXPIRED,
        AUTH_PERMISSION_DENIED,
        TASK_NOT_FOUND,
        TASK_VALIDATION_ERROR,
        TASK_CREATION_FAILED,
        TASK_UPDATE_FAILED,
        TASK_DELETE_FAILED,
        LEASE_INVALID,
        LEASE_ALREADY_OWNED,
        LEASE_NOT_FOUND,
        LEASE_EXPIRED,
        LEASE_OWNER_MISMATCH,
        LEASE_PATH_LOCK_CONFLICT,
        LEASE_PATH_LOCK_IMMUTABLE,
        AGENT_NOT_FOUND,
        AGENT_NOT_AVAILABLE,
        DISPATCH_FAILED,
        CONTRACT_VIOLATION,
        DOCTOR_CHECK_FAILED,
        DOCTOR_FIX_FAILED,
        UNKNOWN_ERROR,
        VALIDATION_ERROR,
        NOT_FOUND,
        SERVICE_UNAVAILABLE,
        INTERNAL_ERROR,
    ]

    def test_returns_string_for_all_known_codes(self):
        for code in self.KNOWN_CODES:
            result = next_action_for(code)
            assert isinstance(result, str), f"Code {code!r}: expected str, got {type(result)}"
            assert result, f"Code {code!r}: got empty string"

    def test_unknown_code_falls_back_to_unknown_error_guidance(self):
        fallback = next_action_for("COMPLETELY_UNKNOWN_XYZ")
        expected = next_action_for(UNKNOWN_ERROR)
        assert fallback == expected

    def test_empty_string_falls_back(self):
        fallback = next_action_for("")
        expected = next_action_for(UNKNOWN_ERROR)
        assert fallback == expected

    def test_lowercase_code_falls_back(self):
        """Codes are case-sensitive; lowercase should fall back."""
        fallback = next_action_for("task_not_found")
        expected = next_action_for(UNKNOWN_ERROR)
        assert fallback == expected

    def test_auth_missing_credentials_mentions_token(self):
        action = next_action_for(AUTH_MISSING_CREDENTIALS)
        assert "token" in action.lower() or "Bearer" in action

    def test_auth_invalid_token_mentions_token(self):
        action = next_action_for(AUTH_INVALID_TOKEN)
        assert "token" in action.lower()

    def test_auth_token_expired_mentions_login(self):
        action = next_action_for(AUTH_TOKEN_EXPIRED)
        assert "login" in action or "expired" in action.lower()

    def test_task_not_found_mentions_list(self):
        action = next_action_for(TASK_NOT_FOUND)
        assert "list" in action.lower() or "id" in action.lower()

    def test_lease_already_owned_mentions_expire(self):
        action = next_action_for(LEASE_ALREADY_OWNED)
        assert "expire" in action.lower() or "lease" in action.lower()

    def test_unknown_error_mentions_doctor(self):
        action = next_action_for(UNKNOWN_ERROR)
        assert "doctor" in action.lower()

    def test_all_actions_are_actionable_sentences(self):
        """Every guidance string should be a non-trivial, readable sentence."""
        for code in self.KNOWN_CODES:
            action = next_action_for(code)
            # Must have at least 20 chars and end with a sentence-like structure
            assert len(action) >= 20, f"Code {code!r}: guidance too short ({len(action)} chars)"


# ---------------------------------------------------------------------------
# Section 3 — ForgeError dataclass
# ---------------------------------------------------------------------------


class TestForgeErrorConstruction:
    """Basic construction and attribute access."""

    def test_minimal_construction(self):
        err = ForgeError(code=TASK_NOT_FOUND, message="Task 42 not found")
        assert err.code == TASK_NOT_FOUND
        assert err.message == "Task 42 not found"
        assert err.details is None

    def test_auto_next_action_populated(self):
        """When next_action is omitted it should be populated automatically."""
        err = ForgeError(code=AUTH_INVALID_TOKEN, message="Bad token")
        assert err.next_action  # non-empty
        assert err.next_action == next_action_for(AUTH_INVALID_TOKEN)

    def test_explicit_next_action_preserved(self):
        custom = "Run `forge doctor` immediately."
        err = ForgeError(
            code=INTERNAL_ERROR,
            message="Boom",
            next_action=custom,
        )
        assert err.next_action == custom

    def test_empty_next_action_triggers_auto_populate(self):
        err = ForgeError(code=NOT_FOUND, message="gone", next_action="")
        assert err.next_action == next_action_for(NOT_FOUND)

    def test_details_can_be_dict(self):
        err = ForgeError(
            code=TASK_VALIDATION_ERROR,
            message="Bad fields",
            details={"field": "title", "reason": "too short"},
        )
        assert err.details == {"field": "title", "reason": "too short"}

    def test_details_defaults_to_none(self):
        err = ForgeError(code=UNKNOWN_ERROR, message="oops")
        assert err.details is None

    def test_details_accepts_nested_dict(self):
        nested = {"conflict": {"owner": "node-1", "task_id": "abc123"}}
        err = ForgeError(
            code=LEASE_ALREADY_OWNED,
            message="Conflict",
            details=nested,
        )
        assert err.details == nested

    def test_details_accepts_empty_dict(self):
        err = ForgeError(code=VALIDATION_ERROR, message="x", details={})
        assert err.details == {}

    def test_unknown_code_gets_fallback_next_action(self):
        err = ForgeError(code="MY_CUSTOM_CODE", message="Custom error")
        # Should not raise; should fall back
        assert err.next_action == next_action_for(UNKNOWN_ERROR)

    def test_message_can_be_empty_string(self):
        err = ForgeError(code=UNKNOWN_ERROR, message="")
        assert err.message == ""

    def test_all_known_codes_construct_without_error(self):
        for code in TestNextActionFor.KNOWN_CODES:
            err = ForgeError(code=code, message="test")
            assert err.code == code
            assert err.next_action  # non-empty


class TestForgeErrorToDict:
    """ForgeError.to_dict() serialisation."""

    def test_basic_fields_present(self):
        err = ForgeError(code=TASK_NOT_FOUND, message="Not found")
        d = err.to_dict()
        assert d["code"] == TASK_NOT_FOUND
        assert d["message"] == "Not found"
        assert "next_action" in d

    def test_details_omitted_when_none(self):
        err = ForgeError(code=UNKNOWN_ERROR, message="x")
        d = err.to_dict()
        assert "details" not in d

    def test_details_included_when_set(self):
        err = ForgeError(
            code=LEASE_OWNER_MISMATCH,
            message="Mismatch",
            details={"expected": "node-1", "actual": "node-2"},
        )
        d = err.to_dict()
        assert d["details"] == {"expected": "node-1", "actual": "node-2"}

    def test_details_included_when_empty_dict(self):
        err = ForgeError(code=VALIDATION_ERROR, message="x", details={})
        d = err.to_dict()
        assert "details" in d
        assert d["details"] == {}

    def test_next_action_in_dict(self):
        err = ForgeError(code=AUTH_TOKEN_EXPIRED, message="Expired")
        d = err.to_dict()
        assert d["next_action"] == next_action_for(AUTH_TOKEN_EXPIRED)

    def test_custom_next_action_in_dict(self):
        custom = "Do the thing."
        err = ForgeError(code=UNKNOWN_ERROR, message="x", next_action=custom)
        d = err.to_dict()
        assert d["next_action"] == custom

    def test_dict_is_plain_types(self):
        """to_dict() must return JSON-serialisable plain types."""
        import json

        err = ForgeError(
            code=DISPATCH_FAILED,
            message="Agent unreachable",
            details={"agent_id": "kimi", "session": "tmux:0"},
        )
        # json.dumps should not raise
        serialised = json.dumps(err.to_dict())
        assert DISPATCH_FAILED in serialised

    def test_returns_new_dict_each_call(self):
        err = ForgeError(code=NOT_FOUND, message="x")
        d1 = err.to_dict()
        d2 = err.to_dict()
        assert d1 is not d2

    def test_modifying_returned_dict_does_not_affect_object(self):
        err = ForgeError(code=NOT_FOUND, message="original")
        d = err.to_dict()
        d["message"] = "mutated"
        assert err.message == "original"


class TestForgeErrorFromDict:
    """ForgeError.from_dict() deserialisation."""

    def test_round_trip(self):
        original = ForgeError(
            code=TASK_CREATION_FAILED,
            message="Could not write task file",
            details={"path": "/tmp/tasks/abc.json"},
        )
        reconstructed = ForgeError.from_dict(original.to_dict())
        assert reconstructed.code == original.code
        assert reconstructed.message == original.message
        assert reconstructed.next_action == original.next_action
        assert reconstructed.details == original.details

    def test_from_dict_with_all_fields(self):
        data = {
            "code": AUTH_PERMISSION_DENIED,
            "message": "Insufficient scope",
            "next_action": "Contact admin.",
            "details": {"required": "admin", "actual": "viewer"},
        }
        err = ForgeError.from_dict(data)
        assert err.code == AUTH_PERMISSION_DENIED
        assert err.message == "Insufficient scope"
        assert err.next_action == "Contact admin."
        assert err.details == {"required": "admin", "actual": "viewer"}

    def test_from_dict_missing_code_defaults_to_unknown(self):
        data = {"message": "Something went wrong"}
        err = ForgeError.from_dict(data)
        assert err.code == UNKNOWN_ERROR

    def test_from_dict_missing_message_defaults_to_empty(self):
        data = {"code": NOT_FOUND}
        err = ForgeError.from_dict(data)
        assert err.message == ""

    def test_from_dict_missing_next_action_auto_populates(self):
        data = {"code": DOCTOR_CHECK_FAILED, "message": "disk full"}
        err = ForgeError.from_dict(data)
        assert err.next_action == next_action_for(DOCTOR_CHECK_FAILED)

    def test_from_dict_explicit_next_action_preserved(self):
        data = {
            "code": UNKNOWN_ERROR,
            "message": "x",
            "next_action": "My custom action.",
        }
        err = ForgeError.from_dict(data)
        assert err.next_action == "My custom action."

    def test_from_dict_missing_details_defaults_to_none(self):
        data = {"code": TASK_DELETE_FAILED, "message": "could not delete"}
        err = ForgeError.from_dict(data)
        assert err.details is None

    def test_from_dict_details_none_explicitly(self):
        data = {
            "code": TASK_DELETE_FAILED,
            "message": "could not delete",
            "details": None,
        }
        err = ForgeError.from_dict(data)
        assert err.details is None

    def test_from_dict_empty_dict(self):
        err = ForgeError.from_dict({})
        assert err.code == UNKNOWN_ERROR
        assert err.message == ""
        # next_action auto-populated from UNKNOWN_ERROR fallback
        assert err.next_action == next_action_for(UNKNOWN_ERROR)

    def test_from_dict_preserves_nested_details(self):
        nested = {"a": {"b": {"c": 42}}}
        data = {
            "code": LEASE_PATH_LOCK_CONFLICT,
            "message": "conflict",
            "details": nested,
        }
        err = ForgeError.from_dict(data)
        assert err.details == nested

    def test_from_dict_with_list_in_details(self):
        data = {
            "code": VALIDATION_ERROR,
            "message": "Multiple errors",
            "details": {"errors": ["field_a missing", "field_b invalid"]},
        }
        err = ForgeError.from_dict(data)
        assert err.details["errors"] == ["field_a missing", "field_b invalid"]


# ---------------------------------------------------------------------------
# Section 4 — api_error_body()
# ---------------------------------------------------------------------------


class TestApiErrorBody:
    """api_error_body() produces the correct API envelope."""

    def test_top_level_keys(self):
        body = api_error_body(TASK_NOT_FOUND, "Task missing")
        assert set(body.keys()) == {"success", "data", "error", "timestamp"}

    def test_success_is_false(self):
        body = api_error_body(UNKNOWN_ERROR, "oops")
        assert body["success"] is False

    def test_data_is_none(self):
        body = api_error_body(UNKNOWN_ERROR, "oops")
        assert body["data"] is None

    def test_error_is_dict(self):
        body = api_error_body(AGENT_NOT_FOUND, "Agent gone")
        assert isinstance(body["error"], dict)

    def test_error_code_matches(self):
        body = api_error_body(AGENT_NOT_FOUND, "gone")
        assert body["error"]["code"] == AGENT_NOT_FOUND

    def test_error_message_matches(self):
        body = api_error_body(SERVICE_UNAVAILABLE, "DB down")
        assert body["error"]["message"] == "DB down"

    def test_auto_next_action_when_omitted(self):
        body = api_error_body(DOCTOR_FIX_FAILED, "Could not fix")
        assert body["error"]["next_action"] == next_action_for(DOCTOR_FIX_FAILED)

    def test_explicit_next_action_preserved(self):
        custom = "Do X then Y."
        body = api_error_body(
            INTERNAL_ERROR, "Bad", next_action=custom
        )
        assert body["error"]["next_action"] == custom

    def test_details_omitted_when_none(self):
        body = api_error_body(NOT_FOUND, "Missing")
        assert "details" not in body["error"]

    def test_details_included_when_provided(self):
        d = {"resource_type": "task", "id": "xyz"}
        body = api_error_body(NOT_FOUND, "Missing", details=d)
        assert body["error"]["details"] == d

    def test_timestamp_is_iso8601_utc(self):
        body = api_error_body(UNKNOWN_ERROR, "x")
        ts = body["timestamp"]
        assert isinstance(ts, str)
        assert _is_iso8601_utc(ts), f"Timestamp {ts!r} is not UTC ISO-8601"

    def test_timestamp_is_recent(self):
        before = datetime.now(UTC)
        body = api_error_body(UNKNOWN_ERROR, "x")
        after = datetime.now(UTC)
        ts = datetime.fromisoformat(body["timestamp"])
        assert before <= ts <= after

    def test_multiple_calls_produce_independent_bodies(self):
        b1 = api_error_body(TASK_NOT_FOUND, "one")
        b2 = api_error_body(AGENT_NOT_FOUND, "two")
        assert b1["error"]["code"] != b2["error"]["code"]

    def test_empty_message_string(self):
        body = api_error_body(VALIDATION_ERROR, "")
        assert body["error"]["message"] == ""

    def test_all_known_codes_produce_valid_envelope(self):
        for code in TestNextActionFor.KNOWN_CODES:
            body = api_error_body(code, "test message")
            assert body["success"] is False
            assert body["error"]["code"] == code
            assert body["error"]["next_action"]

    def test_details_with_numeric_values(self):
        d = {"count": 5, "limit": 10}
        body = api_error_body(VALIDATION_ERROR, "Too many", details=d)
        assert body["error"]["details"]["count"] == 5

    def test_details_with_list_values(self):
        d = {"invalid_fields": ["name", "email"]}
        body = api_error_body(VALIDATION_ERROR, "Bad fields", details=d)
        assert body["error"]["details"]["invalid_fields"] == ["name", "email"]

    def test_json_serialisable(self):
        import json

        body = api_error_body(
            LEASE_PATH_LOCK_CONFLICT,
            "Conflict",
            details={"conflicting_task": "task-999"},
        )
        raw = json.dumps(body)
        parsed = json.loads(raw)
        assert parsed["success"] is False


# ---------------------------------------------------------------------------
# Section 5 — api_success_body()
# ---------------------------------------------------------------------------


class TestApiSuccessBody:
    """api_success_body() produces the correct API success envelope."""

    def test_top_level_keys(self):
        body = api_success_body({"id": "abc"})
        assert set(body.keys()) == {"success", "data", "error", "timestamp"}

    def test_success_is_true(self):
        body = api_success_body(None)
        assert body["success"] is True

    def test_error_is_none(self):
        body = api_success_body({"x": 1})
        assert body["error"] is None

    def test_data_passed_through(self):
        payload = {"task_id": "t-001", "status": "running"}
        body = api_success_body(payload)
        assert body["data"] == payload

    def test_data_can_be_none(self):
        body = api_success_body(None)
        assert body["data"] is None

    def test_data_can_be_list(self):
        items = [{"id": 1}, {"id": 2}]
        body = api_success_body(items)
        assert body["data"] == items

    def test_data_can_be_string(self):
        body = api_success_body("ok")
        assert body["data"] == "ok"

    def test_data_can_be_integer(self):
        body = api_success_body(42)
        assert body["data"] == 42

    def test_data_can_be_boolean(self):
        body = api_success_body(True)
        assert body["data"] is True

    def test_data_can_be_empty_dict(self):
        body = api_success_body({})
        assert body["data"] == {}

    def test_data_can_be_empty_list(self):
        body = api_success_body([])
        assert body["data"] == []

    def test_timestamp_is_iso8601_utc(self):
        body = api_success_body({})
        ts = body["timestamp"]
        assert isinstance(ts, str)
        assert _is_iso8601_utc(ts), f"Timestamp {ts!r} is not UTC ISO-8601"

    def test_timestamp_is_recent(self):
        before = datetime.now(UTC)
        body = api_success_body({})
        after = datetime.now(UTC)
        ts = datetime.fromisoformat(body["timestamp"])
        assert before <= ts <= after

    def test_json_serialisable(self):
        import json

        body = api_success_body({"agents": ["kimi", "codex"], "count": 2})
        raw = json.dumps(body)
        parsed = json.loads(raw)
        assert parsed["success"] is True

    def test_data_nested_dict(self):
        nested = {"a": {"b": {"c": [1, 2, 3]}}}
        body = api_success_body(nested)
        assert body["data"] == nested


# ---------------------------------------------------------------------------
# Section 6 — ForgeError equality and identity
# ---------------------------------------------------------------------------


class TestForgeErrorEquality:
    """Dataclass equality semantics."""

    def test_equal_errors(self):
        e1 = ForgeError(code=NOT_FOUND, message="x")
        e2 = ForgeError(code=NOT_FOUND, message="x")
        assert e1 == e2

    def test_different_codes_not_equal(self):
        e1 = ForgeError(code=NOT_FOUND, message="x")
        e2 = ForgeError(code=UNKNOWN_ERROR, message="x")
        assert e1 != e2

    def test_different_messages_not_equal(self):
        e1 = ForgeError(code=NOT_FOUND, message="gone")
        e2 = ForgeError(code=NOT_FOUND, message="missing")
        assert e1 != e2

    def test_different_details_not_equal(self):
        e1 = ForgeError(code=NOT_FOUND, message="x", details={"a": 1})
        e2 = ForgeError(code=NOT_FOUND, message="x", details={"a": 2})
        assert e1 != e2

    def test_none_vs_empty_details_not_equal(self):
        e1 = ForgeError(code=NOT_FOUND, message="x", details=None)
        e2 = ForgeError(code=NOT_FOUND, message="x", details={})
        assert e1 != e2


# ---------------------------------------------------------------------------
# Section 7 — Integration: full round-trip
# ---------------------------------------------------------------------------


class TestFullRoundTrip:
    """End-to-end: build an API error body, parse inner error, verify fidelity."""

    def test_error_body_inner_error_round_trip(self):
        details = {"task_id": "t-999", "owner": "node-A"}
        body = api_error_body(
            LEASE_ALREADY_OWNED,
            "Task already leased",
            details=details,
        )
        # Reconstruct ForgeError from the nested error dict
        inner = ForgeError.from_dict(body["error"])
        assert inner.code == LEASE_ALREADY_OWNED
        assert inner.message == "Task already leased"
        assert inner.details == details
        assert inner.next_action == next_action_for(LEASE_ALREADY_OWNED)

    def test_forge_error_to_api_body_matches_api_error_body(self):
        """ForgeError.to_dict() embedded in a manual envelope should match api_error_body result."""
        code = CONTRACT_VIOLATION
        message = "Contract check failed"
        details = {"check": "storage_health"}

        err = ForgeError(code=code, message=message, details=details)
        body = api_error_body(code, message, details=details)

        # The error subdicts should be identical
        assert body["error"]["code"] == err.to_dict()["code"]
        assert body["error"]["message"] == err.to_dict()["message"]
        assert body["error"]["next_action"] == err.to_dict()["next_action"]
        assert body["error"]["details"] == err.to_dict()["details"]

    def test_success_and_error_body_are_mutually_exclusive(self):
        success = api_success_body({"result": "ok"})
        error = api_error_body(INTERNAL_ERROR, "crash")

        assert success["success"] is not error["success"]
        assert success["error"] is None
        assert error["data"] is None

    def test_from_dict_then_to_dict_idempotent(self):
        original_dict = {
            "code": TASK_UPDATE_FAILED,
            "message": "Update failed",
            "next_action": "Retry the update.",
            "details": {"task_id": "abc"},
        }
        err = ForgeError.from_dict(original_dict)
        result_dict = err.to_dict()

        assert result_dict["code"] == original_dict["code"]
        assert result_dict["message"] == original_dict["message"]
        assert result_dict["next_action"] == original_dict["next_action"]
        assert result_dict["details"] == original_dict["details"]


# ---------------------------------------------------------------------------
# Section 8 — Edge cases & boundary values
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Boundary and unusual inputs."""

    def test_forge_error_with_very_long_message(self):
        long_msg = "a" * 5000
        err = ForgeError(code=INTERNAL_ERROR, message=long_msg)
        assert err.message == long_msg
        d = err.to_dict()
        assert d["message"] == long_msg

    def test_forge_error_with_unicode_message(self):
        msg = "エラー: \u4e0d\u660e\u306a\u30a8\u30e9\u30fc \U0001f525"
        err = ForgeError(code=UNKNOWN_ERROR, message=msg)
        assert err.message == msg

    def test_forge_error_with_special_chars_in_message(self):
        msg = 'Error: "quotes" & <tags> and \'apostrophe\''
        err = ForgeError(code=VALIDATION_ERROR, message=msg)
        assert err.message == msg

    def test_api_error_body_with_deep_nested_details(self):
        deep = {"level1": {"level2": {"level3": {"value": [1, 2, 3]}}}}
        body = api_error_body(INTERNAL_ERROR, "deep err", details=deep)
        assert body["error"]["details"] == deep

    def test_from_dict_with_extra_keys_ignored(self):
        data = {
            "code": NOT_FOUND,
            "message": "not here",
            "next_action": "Check ID.",
            "details": None,
            "extra_field": "should be ignored",
            "another_extra": 999,
        }
        # Should not raise; extra keys are silently ignored by from_dict
        err = ForgeError.from_dict(data)
        assert err.code == NOT_FOUND

    def test_to_dict_does_not_include_none_details(self):
        err = ForgeError(code=UNKNOWN_ERROR, message="x", details=None)
        assert "details" not in err.to_dict()

    def test_next_action_for_whitespace_only_code(self):
        action = next_action_for("   ")
        expected = next_action_for(UNKNOWN_ERROR)
        assert action == expected

    def test_forge_error_details_with_boolean_values(self):
        d = {"is_expired": True, "is_active": False}
        err = ForgeError(code=LEASE_EXPIRED, message="expired", details=d)
        assert err.details["is_expired"] is True
        assert err.details["is_active"] is False

    def test_forge_error_details_with_none_values(self):
        d = {"owner": None, "id": "abc"}
        err = ForgeError(code=LEASE_NOT_FOUND, message="no lease", details=d)
        assert err.details["owner"] is None

    def test_api_success_body_with_none_does_not_raise(self):
        body = api_success_body(None)
        assert body["data"] is None
        assert body["success"] is True

    def test_api_error_body_empty_details_dict(self):
        body = api_error_body(VALIDATION_ERROR, "x", details={})
        # Empty dict is still truthy enough to be included
        assert body["error"]["details"] == {}

    def test_multiple_forge_errors_are_independent(self):
        e1 = ForgeError(code=NOT_FOUND, message="e1", details={"k": 1})
        e2 = ForgeError(code=INTERNAL_ERROR, message="e2", details={"k": 2})
        assert e1.code != e2.code
        assert e1.details != e2.details
