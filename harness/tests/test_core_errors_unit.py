"""
Unit tests for forge_harness.webhook_server.core.errors
=========================================================

Covers:
- forge_http_error: raises HTTPException with canonical error body
- forge_json_error: returns JSONResponse with canonical error body
- forge_auth_error: returns JSONResponse with auth-specific headers

Edge cases tested:
- Empty strings for code/message/next_action
- None details vs. provided details dict
- status_code boundaries (401 vs 403 for WWW-Authenticate header)
- Auto-populated next_action from catalogue (empty string triggers auto-fill)
- Unknown error codes (fallback next_action)
- Nested / complex details dicts
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.core.errors import (
    forge_auth_error,
    forge_http_error,
    forge_json_error,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_json_response(response: JSONResponse) -> dict:
    """Decode a JSONResponse body to a Python dict."""
    raw = response.body
    return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)


# ===========================================================================
# forge_http_error
# ===========================================================================


class TestForgeHttpError:
    """forge_http_error must always raise HTTPException with structured detail."""

    def test_raises_http_exception(self):
        with pytest.raises(HTTPException):
            forge_http_error(400, "TASK_NOT_FOUND", "Task not found")

    def test_status_code_propagated(self):
        with pytest.raises(HTTPException) as exc_info:
            forge_http_error(404, "TASK_NOT_FOUND", "Not here")
        assert exc_info.value.status_code == 404

    def test_detail_contains_code(self):
        with pytest.raises(HTTPException) as exc_info:
            forge_http_error(400, "TASK_NOT_FOUND", "Missing task")
        detail = exc_info.value.detail
        assert detail["code"] == "TASK_NOT_FOUND"

    def test_detail_contains_message(self):
        with pytest.raises(HTTPException) as exc_info:
            forge_http_error(400, "TASK_NOT_FOUND", "My custom message")
        assert exc_info.value.detail["message"] == "My custom message"

    def test_detail_contains_next_action_auto_filled(self):
        """When next_action is omitted, the catalogue should auto-fill it."""
        with pytest.raises(HTTPException) as exc_info:
            forge_http_error(400, "TASK_NOT_FOUND", "Gone")
        # Auto-filled — must be a non-empty string
        assert exc_info.value.detail["next_action"]

    def test_detail_next_action_explicit(self):
        with pytest.raises(HTTPException) as exc_info:
            forge_http_error(400, "TASK_NOT_FOUND", "Gone", next_action="Do something")
        assert exc_info.value.detail["next_action"] == "Do something"

    def test_detail_without_details_key(self):
        """details=None means the 'details' key should be absent from the error dict."""
        with pytest.raises(HTTPException) as exc_info:
            forge_http_error(400, "TASK_NOT_FOUND", "Gone")
        assert "details" not in exc_info.value.detail

    def test_detail_with_details_dict(self):
        ctx = {"task_id": "abc-123", "owner": "agent-x"}
        with pytest.raises(HTTPException) as exc_info:
            forge_http_error(409, "LEASE_ALREADY_OWNED", "Conflict", details=ctx)
        assert exc_info.value.detail["details"] == ctx

    def test_500_status_code(self):
        with pytest.raises(HTTPException) as exc_info:
            forge_http_error(500, "INTERNAL_ERROR", "Boom")
        assert exc_info.value.status_code == 500

    def test_empty_message(self):
        """Empty message string must not cause an error."""
        with pytest.raises(HTTPException) as exc_info:
            forge_http_error(400, "VALIDATION_ERROR", "")
        assert exc_info.value.detail["message"] == ""

    def test_unknown_code_gets_fallback_next_action(self):
        """Codes not in the catalogue fall back to UNKNOWN_ERROR guidance."""
        with pytest.raises(HTTPException) as exc_info:
            forge_http_error(400, "COMPLETELY_MADE_UP_CODE", "Oops")
        detail = exc_info.value.detail
        assert detail["next_action"]  # Fallback string must be non-empty

    def test_empty_code(self):
        """Empty code string should still produce a structured detail."""
        with pytest.raises(HTTPException) as exc_info:
            forge_http_error(400, "", "Some error")
        detail = exc_info.value.detail
        assert "code" in detail
        assert detail["code"] == ""

    def test_details_with_nested_dict(self):
        nested = {"fields": {"name": "required", "email": "invalid"}}
        with pytest.raises(HTTPException) as exc_info:
            forge_http_error(422, "VALIDATION_ERROR", "Invalid payload", details=nested)
        assert exc_info.value.detail["details"] == nested

    def test_all_common_status_codes(self):
        """Verify common HTTP status codes are accepted without errors."""
        for code in (400, 401, 403, 404, 409, 422, 500, 503):
            with pytest.raises(HTTPException) as exc_info:
                forge_http_error(code, "UNKNOWN_ERROR", "error")
            assert exc_info.value.status_code == code


# ===========================================================================
# forge_json_error
# ===========================================================================


class TestForgeJsonError:
    """forge_json_error must return a JSONResponse, never raise."""

    def test_returns_json_response(self):
        result = forge_json_error(400, "TASK_NOT_FOUND", "Not found")
        assert isinstance(result, JSONResponse)

    def test_does_not_raise(self):
        # Must complete without raising any exception
        result = forge_json_error(500, "INTERNAL_ERROR", "Oops")
        assert result is not None

    def test_status_code_propagated(self):
        result = forge_json_error(404, "TASK_NOT_FOUND", "Missing")
        assert result.status_code == 404

    def test_body_success_is_false(self):
        result = forge_json_error(400, "TASK_NOT_FOUND", "Gone")
        body = _decode_json_response(result)
        assert body["success"] is False

    def test_body_data_is_none(self):
        result = forge_json_error(400, "TASK_NOT_FOUND", "Gone")
        body = _decode_json_response(result)
        assert body["data"] is None

    def test_body_error_code(self):
        result = forge_json_error(400, "TASK_NOT_FOUND", "Gone")
        body = _decode_json_response(result)
        assert body["error"]["code"] == "TASK_NOT_FOUND"

    def test_body_error_message(self):
        result = forge_json_error(400, "TASK_NOT_FOUND", "Custom message")
        body = _decode_json_response(result)
        assert body["error"]["message"] == "Custom message"

    def test_body_error_next_action_auto_filled(self):
        result = forge_json_error(400, "TASK_NOT_FOUND", "Gone")
        body = _decode_json_response(result)
        assert body["error"]["next_action"]

    def test_body_error_explicit_next_action(self):
        result = forge_json_error(400, "TASK_NOT_FOUND", "Gone", next_action="Check docs")
        body = _decode_json_response(result)
        assert body["error"]["next_action"] == "Check docs"

    def test_body_no_details_key_when_none(self):
        result = forge_json_error(400, "TASK_NOT_FOUND", "Gone")
        body = _decode_json_response(result)
        assert "details" not in body["error"]

    def test_body_details_present_when_provided(self):
        ctx = {"conflict_owner": "agent-42"}
        result = forge_json_error(409, "LEASE_ALREADY_OWNED", "Conflict", details=ctx)
        body = _decode_json_response(result)
        assert body["error"]["details"] == ctx

    def test_body_has_timestamp(self):
        result = forge_json_error(400, "TASK_NOT_FOUND", "Gone")
        body = _decode_json_response(result)
        assert "timestamp" in body
        assert body["timestamp"]  # Non-empty ISO string

    def test_empty_message(self):
        result = forge_json_error(400, "VALIDATION_ERROR", "")
        body = _decode_json_response(result)
        assert body["error"]["message"] == ""

    def test_empty_code_unknown_fallback(self):
        result = forge_json_error(400, "", "An error")
        body = _decode_json_response(result)
        assert body["error"]["code"] == ""

    def test_unknown_code_fallback_next_action(self):
        result = forge_json_error(400, "INVENTED_CODE_XYZ", "Unknown thing")
        body = _decode_json_response(result)
        assert body["error"]["next_action"]

    def test_details_with_list_value(self):
        ctx = {"failed_ids": ["t1", "t2", "t3"]}
        result = forge_json_error(400, "VALIDATION_ERROR", "Multiple failures", details=ctx)
        body = _decode_json_response(result)
        assert body["error"]["details"]["failed_ids"] == ["t1", "t2", "t3"]

    def test_503_service_unavailable(self):
        result = forge_json_error(503, "SERVICE_UNAVAILABLE", "Down for maintenance")
        assert result.status_code == 503
        body = _decode_json_response(result)
        assert body["error"]["code"] == "SERVICE_UNAVAILABLE"


# ===========================================================================
# forge_auth_error
# ===========================================================================


class TestForgeAuthError:
    """forge_auth_error must return JSONResponse with correct auth headers."""

    # --- Default parameter tests ---

    def test_returns_json_response(self):
        result = forge_auth_error()
        assert isinstance(result, JSONResponse)

    def test_default_status_code_is_401(self):
        result = forge_auth_error()
        assert result.status_code == 401

    def test_default_code(self):
        result = forge_auth_error()
        body = _decode_json_response(result)
        assert body["error"]["code"] == "AUTH_MISSING_CREDENTIALS"

    def test_default_message(self):
        result = forge_auth_error()
        body = _decode_json_response(result)
        assert body["error"]["message"] == "Authentication required"

    # --- WWW-Authenticate header tests ---

    def test_401_includes_www_authenticate_header(self):
        result = forge_auth_error(status_code=401)
        assert result.headers.get("www-authenticate") == "Bearer"

    def test_403_excludes_www_authenticate_header(self):
        result = forge_auth_error(status_code=403, code="AUTH_PERMISSION_DENIED", message="Forbidden")
        # Header must NOT be present for 403
        assert "www-authenticate" not in {k.lower(): v for k, v in result.headers.items()}

    def test_custom_401_code_and_message(self):
        result = forge_auth_error(
            status_code=401,
            code="AUTH_INVALID_TOKEN",
            message="Token is invalid",
        )
        assert result.status_code == 401
        body = _decode_json_response(result)
        assert body["error"]["code"] == "AUTH_INVALID_TOKEN"
        assert body["error"]["message"] == "Token is invalid"

    def test_403_with_permission_denied(self):
        result = forge_auth_error(
            status_code=403,
            code="AUTH_PERMISSION_DENIED",
            message="Insufficient permissions",
        )
        assert result.status_code == 403
        body = _decode_json_response(result)
        assert body["error"]["code"] == "AUTH_PERMISSION_DENIED"

    # --- next_action ---

    def test_next_action_auto_filled_for_401(self):
        result = forge_auth_error()
        body = _decode_json_response(result)
        assert body["error"]["next_action"]

    def test_explicit_next_action_overrides_catalogue(self):
        result = forge_auth_error(next_action="Go check the docs")
        body = _decode_json_response(result)
        assert body["error"]["next_action"] == "Go check the docs"

    # --- details ---

    def test_no_details_key_when_none(self):
        result = forge_auth_error()
        body = _decode_json_response(result)
        assert "details" not in body["error"]

    def test_details_included_when_provided(self):
        ctx = {"token_prefix": "Bearer eyJ..."}
        result = forge_auth_error(details=ctx)
        body = _decode_json_response(result)
        assert body["error"]["details"] == ctx

    # --- body envelope ---

    def test_body_success_is_false(self):
        result = forge_auth_error()
        body = _decode_json_response(result)
        assert body["success"] is False

    def test_body_data_is_none(self):
        result = forge_auth_error()
        body = _decode_json_response(result)
        assert body["data"] is None

    def test_body_has_timestamp(self):
        result = forge_auth_error()
        body = _decode_json_response(result)
        assert "timestamp" in body

    # --- edge cases ---

    def test_empty_message_accepted(self):
        result = forge_auth_error(message="")
        body = _decode_json_response(result)
        assert body["error"]["message"] == ""

    def test_token_expired_code(self):
        result = forge_auth_error(
            status_code=401,
            code="AUTH_TOKEN_EXPIRED",
            message="Token has expired",
        )
        assert result.status_code == 401
        assert result.headers.get("www-authenticate") == "Bearer"
        body = _decode_json_response(result)
        assert body["error"]["code"] == "AUTH_TOKEN_EXPIRED"

    def test_does_not_raise(self):
        # Must complete without raising
        result = forge_auth_error(status_code=401, code="AUTH_MISSING_CREDENTIALS", message="Missing")
        assert result is not None


# ===========================================================================
# Integration — api_error_body called by all three helpers
# ===========================================================================


class TestApiErrorBodyIntegration:
    """Verify the api_error_body wrapper is called correctly by each helper."""

    def test_forge_http_error_calls_api_error_body(self):
        from forge_harness import error_schema

        with patch.object(error_schema, "api_error_body", wraps=error_schema.api_error_body) as mock_fn:
            # Re-import after patch
            from forge_harness.webhook_server.core import errors as err_mod

            with patch.object(err_mod, "api_error_body", wraps=error_schema.api_error_body) as m:
                with pytest.raises(HTTPException):
                    err_mod.forge_http_error(400, "TASK_NOT_FOUND", "Gone", details={"x": 1})
                m.assert_called_once_with(
                    code="TASK_NOT_FOUND",
                    message="Gone",
                    next_action="",
                    details={"x": 1},
                )

    def test_forge_json_error_calls_api_error_body(self):
        from forge_harness import error_schema
        from forge_harness.webhook_server.core import errors as err_mod

        with patch.object(err_mod, "api_error_body", wraps=error_schema.api_error_body) as m:
            err_mod.forge_json_error(404, "NOT_FOUND", "Missing", next_action="Check ID")
            m.assert_called_once_with(
                code="NOT_FOUND",
                message="Missing",
                next_action="Check ID",
                details=None,
            )

    def test_forge_auth_error_calls_api_error_body(self):
        from forge_harness import error_schema
        from forge_harness.webhook_server.core import errors as err_mod

        with patch.object(err_mod, "api_error_body", wraps=error_schema.api_error_body) as m:
            err_mod.forge_auth_error(
                status_code=403,
                code="AUTH_PERMISSION_DENIED",
                message="Nope",
                next_action="Ask admin",
                details={"role": "read-only"},
            )
            m.assert_called_once_with(
                code="AUTH_PERMISSION_DENIED",
                message="Nope",
                next_action="Ask admin",
                details={"role": "read-only"},
            )
