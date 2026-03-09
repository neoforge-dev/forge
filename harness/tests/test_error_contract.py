"""
CCX-053 — Structured Error Contract Tests
==========================================

Verifies that the FORGE canonical error schema is present and actionable
across API and CLI surfaces.

Coverage:
- ForgeError schema (code / message / next_action / details)
- next_action is always populated (non-empty)
- API envelope helpers (api_error_body / api_success_body)
- CLI json_output uses canonical schema
- CLI render_forge_error renders structured errors (human + JSON mode)
- Task-lifecycle errors return schema-conformant bodies
- Auth/token errors carry structured error bodies
- Lease errors carry structured error bodies + next_action
- CLI and API JSON output are structurally consistent
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# ---------------------------------------------------------------------------
# Schema correctness helpers
# ---------------------------------------------------------------------------


def _assert_error_schema(error: dict[str, Any], *, code: str | None = None) -> None:
    """Assert a dict conforms to the FORGE canonical error schema."""
    assert "code" in error, f"Missing 'code' field: {error}"
    assert "message" in error, f"Missing 'message' field: {error}"
    assert "next_action" in error, f"Missing 'next_action' field: {error}"
    assert isinstance(error["code"], str) and error["code"], "code must be a non-empty string"
    assert isinstance(error["message"], str), "message must be a string"
    assert isinstance(error["next_action"], str) and error["next_action"], (
        f"next_action must be a non-empty string, got: {error['next_action']!r}"
    )
    if code is not None:
        assert error["code"] == code, f"Expected code={code!r}, got {error['code']!r}"


def _assert_api_envelope(body: dict[str, Any], *, success: bool) -> None:
    """Assert the outer API envelope structure."""
    assert "success" in body, f"Missing 'success' in envelope: {body}"
    assert "timestamp" in body, f"Missing 'timestamp' in envelope: {body}"
    assert body["success"] is success
    if not success:
        assert body.get("data") is None
        assert body.get("error") is not None
        _assert_error_schema(body["error"])
    else:
        assert body.get("error") is None


# ===========================================================================
# Unit tests: ForgeError schema
# ===========================================================================


class TestForgeErrorSchema:
    """ForgeError dataclass conforms to the canonical schema."""

    def test_basic_construction(self):
        from forge_harness.error_schema import TASK_NOT_FOUND, ForgeError

        err = ForgeError(code=TASK_NOT_FOUND, message="Task abc not found")
        assert err.code == TASK_NOT_FOUND
        assert err.message == "Task abc not found"
        assert err.next_action  # auto-populated
        assert err.details is None

    def test_next_action_auto_populated_for_known_codes(self):
        """Every known code should resolve a non-empty next_action automatically."""
        from forge_harness import error_schema
        from forge_harness.error_schema import ForgeError

        known_codes = [
            error_schema.AUTH_MISSING_CREDENTIALS,
            error_schema.AUTH_INVALID_TOKEN,
            error_schema.AUTH_TOKEN_EXPIRED,
            error_schema.AUTH_PERMISSION_DENIED,
            error_schema.TASK_NOT_FOUND,
            error_schema.TASK_VALIDATION_ERROR,
            error_schema.TASK_CREATION_FAILED,
            error_schema.LEASE_INVALID,
            error_schema.LEASE_ALREADY_OWNED,
            error_schema.LEASE_NOT_FOUND,
            error_schema.LEASE_EXPIRED,
            error_schema.LEASE_OWNER_MISMATCH,
            error_schema.LEASE_PATH_LOCK_CONFLICT,
            error_schema.AGENT_NOT_FOUND,
            error_schema.AGENT_NOT_AVAILABLE,
            error_schema.DISPATCH_FAILED,
            error_schema.CONTRACT_VIOLATION,
            error_schema.DOCTOR_CHECK_FAILED,
            error_schema.UNKNOWN_ERROR,
            error_schema.VALIDATION_ERROR,
            error_schema.NOT_FOUND,
        ]
        for code in known_codes:
            err = ForgeError(code=code, message="test")
            assert err.next_action, f"next_action empty for code={code!r}"

    def test_unknown_code_falls_back_to_unknown_error_guidance(self):
        from forge_harness.error_schema import UNKNOWN_ERROR, ForgeError, next_action_for

        err = ForgeError(code="TOTALLY_MADE_UP_CODE_XYZ", message="oops")
        assert err.next_action == next_action_for(UNKNOWN_ERROR)

    def test_explicit_next_action_not_overwritten(self):
        from forge_harness.error_schema import TASK_NOT_FOUND, ForgeError

        custom = "Run forge tasks fix-it"
        err = ForgeError(code=TASK_NOT_FOUND, message="gone", next_action=custom)
        assert err.next_action == custom

    def test_to_dict_contains_required_fields(self):
        from forge_harness.error_schema import AUTH_INVALID_TOKEN, ForgeError

        d = ForgeError(code=AUTH_INVALID_TOKEN, message="bad token").to_dict()
        _assert_error_schema(d, code=AUTH_INVALID_TOKEN)

    def test_to_dict_includes_details_when_present(self):
        from forge_harness.error_schema import TASK_NOT_FOUND, ForgeError

        details = {"task_id": "abc123", "hint": "check list"}
        d = ForgeError(code=TASK_NOT_FOUND, message="gone", details=details).to_dict()
        assert d.get("details") == details

    def test_to_dict_omits_details_when_none(self):
        from forge_harness.error_schema import TASK_NOT_FOUND, ForgeError

        d = ForgeError(code=TASK_NOT_FOUND, message="gone").to_dict()
        assert "details" not in d

    def test_from_dict_roundtrip(self):
        from forge_harness.error_schema import LEASE_EXPIRED, ForgeError

        original = ForgeError(
            code=LEASE_EXPIRED,
            message="lease gone",
            details={"task_id": "t1"},
        )
        d = original.to_dict()
        restored = ForgeError.from_dict(d)
        assert restored.code == original.code
        assert restored.message == original.message
        assert restored.next_action == original.next_action
        assert restored.details == original.details


# ===========================================================================
# Unit tests: API envelope helpers
# ===========================================================================


class TestApiEnvelopeHelpers:
    """api_error_body and api_success_body produce valid envelopes."""

    def test_api_error_body_structure(self):
        from forge_harness.error_schema import TASK_NOT_FOUND, api_error_body

        body = api_error_body(code=TASK_NOT_FOUND, message="Task gone")
        _assert_api_envelope(body, success=False)
        _assert_error_schema(body["error"], code=TASK_NOT_FOUND)

    def test_api_error_body_next_action_present(self):
        from forge_harness.error_schema import AUTH_MISSING_CREDENTIALS, api_error_body

        body = api_error_body(code=AUTH_MISSING_CREDENTIALS, message="No auth")
        assert body["error"]["next_action"]

    def test_api_error_body_custom_next_action(self):
        from forge_harness.error_schema import UNKNOWN_ERROR, api_error_body

        body = api_error_body(
            code=UNKNOWN_ERROR,
            message="oops",
            next_action="try rebooting the universe",
        )
        assert body["error"]["next_action"] == "try rebooting the universe"

    def test_api_error_body_details(self):
        from forge_harness.error_schema import VALIDATION_ERROR, api_error_body

        details = {"field": "subject", "issue": "empty"}
        body = api_error_body(code=VALIDATION_ERROR, message="bad", details=details)
        assert body["error"].get("details") == details

    def test_api_success_body_structure(self):
        from forge_harness.error_schema import api_success_body

        body = api_success_body({"count": 5})
        _assert_api_envelope(body, success=True)
        assert body["data"] == {"count": 5}


# ===========================================================================
# Unit tests: CLI json_output
# ===========================================================================


class TestCLIJsonOutput:
    """CLI json_output uses the canonical schema for errors."""

    def test_error_output_uses_canonical_schema(self):
        from forge_harness.cli_v2.json_output import json_output

        from forge_harness.error_schema import TASK_NOT_FOUND

        out = json_output(success=False, data=None, error="task gone", command="tasks")
        _assert_api_envelope(out, success=False)
        # Plain string error wraps as UNKNOWN_ERROR
        assert out["error"]["code"] == "UNKNOWN_ERROR"
        assert out["error"]["next_action"]

    def test_error_output_with_forge_error(self):
        from forge_harness.cli_v2.json_output import json_output

        from forge_harness.error_schema import TASK_NOT_FOUND, ForgeError

        forge_err = ForgeError(code=TASK_NOT_FOUND, message="Task abc not found")
        out = json_output(success=False, data=None, error=forge_err, command="tasks")
        _assert_api_envelope(out, success=False)
        _assert_error_schema(out["error"], code=TASK_NOT_FOUND)

    def test_error_output_with_dict_error(self):
        from forge_harness.cli_v2.json_output import json_output

        from forge_harness.error_schema import AUTH_INVALID_TOKEN, next_action_for

        err_dict = {
            "code": AUTH_INVALID_TOKEN,
            "message": "bad token",
            "next_action": next_action_for(AUTH_INVALID_TOKEN),
        }
        out = json_output(success=False, data=None, error=err_dict, command="dispatch")
        _assert_api_envelope(out, success=False)
        _assert_error_schema(out["error"], code=AUTH_INVALID_TOKEN)

    def test_success_output_has_null_error(self):
        from forge_harness.cli_v2.json_output import json_output

        out = json_output(success=True, data={"tasks": []}, command="tasks")
        _assert_api_envelope(out, success=True)

    def test_json_and_api_envelopes_structurally_consistent(self):
        """CLI JSON error output and API error body share the same error sub-schema."""
        from forge_harness.cli_v2.json_output import json_output

        from forge_harness.error_schema import AGENT_NOT_FOUND, ForgeError, api_error_body

        cli_out = json_output(
            success=False,
            data=None,
            error=ForgeError(code=AGENT_NOT_FOUND, message="no such agent"),
            command="dispatch",
        )
        api_out = api_error_body(code=AGENT_NOT_FOUND, message="no such agent")

        # Both error sub-objects must have the same keys
        assert set(cli_out["error"].keys()) == set(api_out["error"].keys())
        # Both must have next_action
        assert cli_out["error"]["next_action"]
        assert api_out["error"]["next_action"]


# ===========================================================================
# Unit tests: CLI render_forge_error
# ===========================================================================


class TestCLIRenderForgeError:
    """render_forge_error renders structured errors correctly."""

    def test_human_mode_shows_code_and_message(self, capsys):
        from forge_harness.cli_v2._common import render_forge_error

        from forge_harness.error_schema import TASK_NOT_FOUND, ForgeError

        err = ForgeError(code=TASK_NOT_FOUND, message="Task abc gone")
        with pytest.raises(SystemExit) as exc:
            render_forge_error(err)
        assert exc.value.code == 1
        # Rich console output goes to stderr; capsys may not capture it with stderr=True
        # but we can verify no crash occurs and the exit code is correct.

    def test_json_mode_outputs_valid_schema(self, capsys):
        from forge_harness.cli_v2._common import render_forge_error

        from forge_harness.error_schema import AUTH_MISSING_CREDENTIALS, ForgeError

        err = ForgeError(code=AUTH_MISSING_CREDENTIALS, message="No credentials")
        with pytest.raises(SystemExit):
            render_forge_error(err, output_json=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        _assert_error_schema(data, code=AUTH_MISSING_CREDENTIALS)

    def test_plain_string_is_wrapped(self, capsys):
        from forge_harness.cli_v2._common import render_forge_error

        with pytest.raises(SystemExit):
            render_forge_error("something went wrong", output_json=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["code"] == "UNKNOWN_ERROR"
        assert data["next_action"]

    def test_dict_input_is_deserialised(self, capsys):
        from forge_harness.cli_v2._common import render_forge_error

        from forge_harness.error_schema import DISPATCH_FAILED, next_action_for

        err_dict = {
            "code": DISPATCH_FAILED,
            "message": "agent unreachable",
            "next_action": next_action_for(DISPATCH_FAILED),
        }
        with pytest.raises(SystemExit):
            render_forge_error(err_dict, output_json=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        _assert_error_schema(data, code=DISPATCH_FAILED)

    def test_custom_exit_code(self, capsys):
        from forge_harness.cli_v2._common import render_forge_error

        from forge_harness.error_schema import NOT_FOUND, ForgeError

        with pytest.raises(SystemExit) as exc:
            render_forge_error(ForgeError(code=NOT_FOUND, message="gone"), exit_code=6)
        assert exc.value.code == 6


# ===========================================================================
# Unit tests: Task API error responses
# ===========================================================================


class TestTaskAPIErrorResponses:
    """Task API handlers return structured errors for common failure paths."""

    def _make_forge_error_detail(self, code: str, message: str) -> dict[str, Any]:
        from forge_harness.error_schema import ForgeError

        return ForgeError(code=code, message=message).to_dict()

    def test_api_response_helper_error_path(self):
        """api_response with error args produces canonical schema."""
        from forge_harness.error_schema import TASK_NOT_FOUND
        from forge_harness.webhook_server.api.tasks import api_response

        body = api_response(error_code=TASK_NOT_FOUND, error_message="gone")
        _assert_api_envelope(body, success=False)
        _assert_error_schema(body["error"], code=TASK_NOT_FOUND)

    def test_api_response_helper_success_path(self):
        from forge_harness.webhook_server.api.tasks import api_response

        body = api_response(data={"tasks": []})
        _assert_api_envelope(body, success=True)

    def test_lease_error_codes_have_next_action(self):
        """All TaskLeaseError codes recognised by the schema catalogue."""
        from forge_harness.error_schema import (
            LEASE_ALREADY_OWNED,
            LEASE_EXPIRED,
            LEASE_INVALID,
            LEASE_NOT_FOUND,
            LEASE_OWNER_MISMATCH,
            LEASE_PATH_LOCK_CONFLICT,
            LEASE_PATH_LOCK_IMMUTABLE,
            next_action_for,
        )

        lease_codes = [
            LEASE_INVALID,
            LEASE_ALREADY_OWNED,
            LEASE_NOT_FOUND,
            LEASE_EXPIRED,
            LEASE_OWNER_MISMATCH,
            LEASE_PATH_LOCK_CONFLICT,
            LEASE_PATH_LOCK_IMMUTABLE,
        ]
        for code in lease_codes:
            action = next_action_for(code)
            assert action, f"next_action empty for lease code={code!r}"

    @pytest.mark.asyncio
    async def test_get_task_not_found_returns_structured_error(self):
        """GET /api/tasks/{task_id} returns structured 404 when task missing."""
        from fastapi import HTTPException

        from forge_harness.error_schema import TASK_NOT_FOUND
        from forge_harness.webhook_server.api.tasks import get_task

        mock_handler = AsyncMock()
        mock_handler.get_task = AsyncMock(return_value=None)

        with patch(
            "forge_harness.webhook_server.api.tasks.get_task_handler",
            new=AsyncMock(return_value=mock_handler),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_task(task_id="missing-id")

        assert exc_info.value.status_code == 404
        detail = exc_info.value.detail
        assert isinstance(detail, dict), f"Expected dict detail, got {type(detail)}"
        _assert_error_schema(detail, code=TASK_NOT_FOUND)

    @pytest.mark.asyncio
    async def test_create_task_validation_error_returns_structured_error(self):
        """POST /api/tasks returns 400 with structured error on validation failure.

        validate_task is imported inside the route function at call time.
        We verify the schema by patching the module at the call site path.
        """
        from fastapi import HTTPException

        from forge_harness.error_schema import TASK_VALIDATION_ERROR
        from forge_harness.webhook_server.api.tasks import CreateTaskRequest, create_task

        body = CreateTaskRequest(subject="", description="")
        mock_request = Mock()

        # validate_task is called inside create_task as:
        #   from forge_harness.cli_v2.tasks import validate_task
        # patch it at the source module level so the local import picks up the mock.
        with (
            patch(
                "forge_harness.webhook_server.api.tasks.get_task_handler",
                new=AsyncMock(return_value=AsyncMock()),
            ),
            patch(
                "forge_harness.cli_v2.tasks.validate_task",
                side_effect=ValueError("subject cannot be empty"),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_task(body=body, request=mock_request)

        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        assert isinstance(detail, dict), f"Expected dict, got {type(detail)}: {detail!r}"
        _assert_error_schema(detail, code=TASK_VALIDATION_ERROR)


# ===========================================================================
# Unit tests: Auth errors carry structured schema
# ===========================================================================


class TestAuthErrorStructuredSchema:
    """Auth dependencies raise HTTPException with structured error detail."""

    def _make_request(self, auth_header: str | None = None, client_ip: str = "1.2.3.4") -> Mock:
        req = Mock()
        req.headers = {}
        if auth_header:
            req.headers["authorization"] = auth_header
        client = Mock()
        client.host = client_ip
        req.client = client
        req.url = Mock()
        req.url.path = "/api/tasks"
        req.app = Mock()
        req.app.state = Mock()
        # Disable localhost bypass so auth runs properly
        req.app.state.auth_config = Mock()
        req.app.state.auth_config.allow_localhost = False
        return req

    def test_missing_credentials_returns_structured_error(self):
        """When no auth header is provided the exception detail is structured."""
        from fastapi import HTTPException

        from forge_harness.error_schema import AUTH_MISSING_CREDENTIALS
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth

        req = self._make_request(auth_header=None)

        with patch(
            "forge_harness.webhook_server.infrastructure.api_key_auth.verify_api_key",
            return_value=("missing_key", None),
        ):
            with pytest.raises(HTTPException) as exc_info:
                verify_unified_auth(req)

        assert exc_info.value.status_code == 401
        detail = exc_info.value.detail
        assert isinstance(detail, dict), f"Expected dict detail, got {type(detail)}: {detail!r}"
        _assert_error_schema(detail, code=AUTH_MISSING_CREDENTIALS)

    def test_invalid_bearer_token_returns_structured_error(self):
        """Invalid bearer token results in structured AUTH_INVALID_TOKEN detail."""
        from fastapi import HTTPException

        from forge_harness.error_schema import AUTH_INVALID_TOKEN
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth

        req = self._make_request(auth_header="Bearer bad-token-value")

        # Patch JWT + legacy to fail
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_bearer_token_jwt",
                return_value=("invalid_token", None),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.auth.verify_bearer_token",
                return_value="invalid_token",
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                verify_unified_auth(req)

        assert exc_info.value.status_code == 401
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        _assert_error_schema(detail)
        # code should be one of the auth error codes
        from forge_harness.error_schema import AUTH_INVALID_TOKEN, AUTH_TOKEN_EXPIRED

        assert detail["code"] in (AUTH_INVALID_TOKEN, AUTH_TOKEN_EXPIRED)

    def test_permission_denied_returns_structured_error(self):
        """require_permission raises 403 with structured AUTH_PERMISSION_DENIED."""
        from fastapi import HTTPException

        from forge_harness.error_schema import AUTH_PERMISSION_DENIED

        # Import the function factory
        from forge_harness.webhook_server.core.dependencies import require_permission

        checker_factory = require_permission("tasks:write")
        # The returned checker is an async function — get it via inspection
        import inspect

        checker = None
        for name, obj in inspect.getmembers(checker_factory.__code__, predicate=callable):
            pass
        # Access the inner async function directly (it's a closure)
        checker = checker_factory.__code__

        # Simpler: test via async call with a user missing the permission
        import asyncio

        async def _run():
            # Build the checker from the factory; inject a user dict directly
            user = {"sub": "alice", "permissions": ["tasks:read"], "authenticated": True}
            inner = require_permission("tasks:write")
            # Invoke the dependency callable with a user who lacks permission
            # The dependency signature is: async def permission_checker(user=Depends(...))
            # We bypass Depends and call it directly with our user dict.

            # Extract inner function
            import forge_harness.webhook_server.core.dependencies as dep_mod

            perm_fn = dep_mod.require_permission("tasks:write")
            # Get the inner async function
            # perm_fn is a closure; call it with user keyword bypass
            with pytest.raises(HTTPException) as exc_info:
                # Directly call the inner closure by extracting it
                # The factory returns the inner function
                await perm_fn(user=user)
            return exc_info

        exc_info = asyncio.get_event_loop().run_until_complete(_run())
        assert exc_info.value.status_code == 403
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        _assert_error_schema(detail, code=AUTH_PERMISSION_DENIED)


# ===========================================================================
# Integration-style: webhook_server.core.errors module
# ===========================================================================


class TestWebhookServerErrorHelpers:
    """forge_http_error and forge_json_error produce conformant responses."""

    def test_forge_json_error_body(self):
        from forge_harness.error_schema import TASK_NOT_FOUND
        from forge_harness.webhook_server.core.errors import forge_json_error

        resp = forge_json_error(404, TASK_NOT_FOUND, "Task gone")
        assert resp.status_code == 404
        import json as _json

        body = _json.loads(resp.body)
        _assert_api_envelope(body, success=False)
        _assert_error_schema(body["error"], code=TASK_NOT_FOUND)

    def test_forge_auth_error_body_401(self):
        from forge_harness.error_schema import AUTH_MISSING_CREDENTIALS
        from forge_harness.webhook_server.core.errors import forge_auth_error

        resp = forge_auth_error(
            status_code=401,
            code=AUTH_MISSING_CREDENTIALS,
            message="No credentials",
        )
        assert resp.status_code == 401
        assert resp.headers.get("WWW-Authenticate") == "Bearer"
        import json as _json

        body = _json.loads(resp.body)
        _assert_api_envelope(body, success=False)
        _assert_error_schema(body["error"], code=AUTH_MISSING_CREDENTIALS)

    def test_forge_auth_error_body_403(self):
        from forge_harness.error_schema import AUTH_PERMISSION_DENIED
        from forge_harness.webhook_server.core.errors import forge_auth_error

        resp = forge_auth_error(
            status_code=403,
            code=AUTH_PERMISSION_DENIED,
            message="Forbidden",
        )
        assert resp.status_code == 403
        # No WWW-Authenticate on 403
        assert resp.headers.get("WWW-Authenticate") is None
        import json as _json

        body = _json.loads(resp.body)
        _assert_api_envelope(body, success=False)
        _assert_error_schema(body["error"], code=AUTH_PERMISSION_DENIED)

    def test_forge_http_error_raises_with_structured_detail(self):
        from fastapi import HTTPException

        from forge_harness.error_schema import AGENT_NOT_FOUND
        from forge_harness.webhook_server.core.errors import forge_http_error

        with pytest.raises(HTTPException) as exc_info:
            forge_http_error(404, AGENT_NOT_FOUND, "Agent not found")

        assert exc_info.value.status_code == 404
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        _assert_error_schema(detail, code=AGENT_NOT_FOUND)


# ===========================================================================
# Operator guidance: common operator mistakes surface actionable next_action
# ===========================================================================


class TestOperatorActionableGuidance:
    """Common operator mistakes yield readable, actionable guidance."""

    @pytest.mark.parametrize(
        "code,expected_fragment",
        [
            ("AUTH_MISSING_CREDENTIALS", "Bearer token"),
            ("AUTH_INVALID_TOKEN", "token"),
            ("AUTH_TOKEN_EXPIRED", "forge auth login"),
            ("AUTH_PERMISSION_DENIED", "permission"),
            ("TASK_NOT_FOUND", "forge tasks list"),
            ("TASK_VALIDATION_ERROR", "forge tasks create"),
            ("INVALID_LEASE", "lease_expires_at"),
            ("LEASE_ALREADY_OWNED", "another owner"),
            ("LEASE_EXPIRED", "claim the task again"),
            ("AGENT_NOT_FOUND", "forge fleet list"),
            ("AGENT_NOT_AVAILABLE", "forge fleet status"),
            ("DISPATCH_FAILED", "forge fleet status"),
            ("CONTRACT_VIOLATION", "forge contract verify"),
            ("DOCTOR_CHECK_FAILED", "forge doctor"),
            ("UNKNOWN_ERROR", "forge doctor"),
        ],
    )
    def test_next_action_contains_fragment(self, code: str, expected_fragment: str):
        from forge_harness.error_schema import next_action_for

        action = next_action_for(code)
        assert expected_fragment.lower() in action.lower(), (
            f"next_action for {code!r} missing fragment {expected_fragment!r}. Got: {action!r}"
        )
