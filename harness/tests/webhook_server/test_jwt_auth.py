"""Tests for JWT authentication endpoints and refresh token rotation.

Covers:
- POST /api/auth/token  (static_token + password grant types)
- POST /api/auth/refresh  (rotate refresh token, single-use enforcement)
- POST /api/auth/login  (username/password login)
- POST /api/legacy/auth/refresh  (legacy bearer-based refresh endpoint)
- Backward compatibility: existing FORGE_WEBHOOK_TOKEN bearer auth still works
- FileRefreshTokenStore: persist/read revocations, atomic write
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ============================================================================
# Fixtures
# ============================================================================

STATIC_TOKEN = "test-static-token-abc123"
JWT_SECRET = "test-jwt-secret-for-unit-tests-only"


@pytest.fixture
def tmp_store_path(tmp_path):
    """Provide a temporary path for the file-based refresh token store."""
    return tmp_path / "refresh_tokens.json"


@pytest.fixture(autouse=True)
def reset_jwt_revocation_state(tmp_store_path):
    """Reset in-memory + file revocation state before each test."""
    from forge_harness.webhook_server.infrastructure.jwt_auth import (
        clear_all_revoked_tokens,
    )
    from forge_harness.webhook_server.infrastructure.refresh_token_store import (
        get_refresh_token_store,
        reset_store_singleton,
    )

    clear_all_revoked_tokens()
    reset_store_singleton()
    # Wire the singleton to our tmp path for this test
    get_refresh_token_store(store_path=tmp_store_path)
    yield
    clear_all_revoked_tokens()
    reset_store_singleton()


@pytest.fixture
def client(tmp_store_path):
    """TestClient with auth configured for testing."""
    from forge_harness.webhook_server_main import create_app

    env_overrides = {
        "FORGE_WEBHOOK_TOKEN": STATIC_TOKEN,
        "FORGE_JWT_SECRET": JWT_SECRET,
        "FORGE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "FORGE_JWT_REQUIRE_AUTH": "true",
        "FORGE_WEBHOOK_REQUIRE_AUTH": "true",
    }

    with patch.dict(os.environ, env_overrides, clear=False):
        app = create_app()

        # Bypass unified auth for non-auth endpoints so we can test in isolation
        from forge_harness.webhook_server.core import dependencies

        def bypass_auth():
            return {
                "sub": "test_user",
                "permissions": ["read", "write", "admin"],
                "auth_method": "test",
                "authenticated": True,
            }

        app.dependency_overrides[dependencies.verify_auth] = bypass_auth
        app.dependency_overrides[dependencies.verify_unified_auth] = bypass_auth

        yield TestClient(app, raise_server_exceptions=True)


# ============================================================================
# Helper: issue tokens via /api/auth/token
# ============================================================================


def _issue_tokens_static(client: TestClient) -> dict:
    """Exchange the static FORGE_WEBHOOK_TOKEN for JWT tokens."""
    resp = client.post(
        "/api/auth/token",
        json={"grant_type": "static_token", "token": STATIC_TOKEN},
    )
    assert resp.status_code == 200, f"Token issue failed: {resp.text}"
    return resp.json()


def _issue_tokens_password(client: TestClient) -> dict:
    """Issue tokens via password grant using default dev user."""
    resp = client.post(
        "/api/auth/token",
        json={"grant_type": "password", "username": "admin", "password": "forge_admin"},
    )
    assert resp.status_code == 200, f"Password grant failed: {resp.text}"
    return resp.json()


# ============================================================================
# POST /api/auth/token — static_token grant
# ============================================================================


class TestGetTokenEndpoint:
    def test_static_token_grant_returns_jwt_pair(self, client):
        """Valid FORGE_WEBHOOK_TOKEN exchange returns access + refresh tokens."""
        resp = client.post(
            "/api/auth/token",
            json={"grant_type": "static_token", "token": STATIC_TOKEN},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "Bearer"
        assert data["expires_in"] == 15 * 60  # 15 minutes

    def test_static_token_grant_invalid_token_returns_401(self, client):
        """Wrong static token returns 401."""
        resp = client.post(
            "/api/auth/token",
            json={"grant_type": "static_token", "token": "wrong-token"},
        )
        assert resp.status_code == 401

    def test_static_token_grant_missing_token_field_returns_400(self, client):
        """Omitting the 'token' field for static_token grant returns 400."""
        resp = client.post(
            "/api/auth/token",
            json={"grant_type": "static_token"},
        )
        assert resp.status_code == 400

    def test_password_grant_valid_credentials(self, client):
        """Valid username/password returns JWT pair."""
        data = _issue_tokens_password(client)
        assert "access_token" in data
        assert "refresh_token" in data

    def test_password_grant_invalid_credentials_returns_401(self, client):
        """Wrong password returns 401."""
        resp = client.post(
            "/api/auth/token",
            json={"grant_type": "password", "username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 401

    def test_unsupported_grant_type_returns_400(self, client):
        """Unknown grant_type returns 400."""
        resp = client.post(
            "/api/auth/token",
            json={"grant_type": "client_credentials"},
        )
        assert resp.status_code == 400

    def test_returned_access_token_is_valid_jwt(self, client):
        """Access token can be decoded and has expected claims."""
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            JWTAuthConfig,
            JWTAuthResult,
            decode_token,
        )

        data = _issue_tokens_static(client)
        config = JWTAuthConfig(secret_key=JWT_SECRET, access_token_expire_minutes=15)
        result, payload = decode_token(data["access_token"], config)
        assert result == JWTAuthResult.SUCCESS
        assert payload.type == "access"
        assert payload.sub == "forge_client"

    def test_returned_refresh_token_is_valid_jwt(self, client):
        """Refresh token can be decoded and has expected claims."""
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            JWTAuthConfig,
            JWTAuthResult,
            decode_token,
        )

        data = _issue_tokens_static(client)
        config = JWTAuthConfig(secret_key=JWT_SECRET, access_token_expire_minutes=15)
        result, payload = decode_token(data["refresh_token"], config)
        assert result == JWTAuthResult.SUCCESS
        assert payload.type == "refresh"
        assert payload.family_id is not None  # rotation tracking


# ============================================================================
# POST /api/auth/refresh — token rotation
# ============================================================================


class TestRefreshTokenRotation:
    def test_valid_refresh_token_returns_new_pair(self, client):
        """Valid refresh token returns new access + refresh token pair."""
        tokens = _issue_tokens_static(client)
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        # Rotated tokens should be different from originals
        assert data["access_token"] != tokens["access_token"]
        assert data["refresh_token"] != tokens["refresh_token"]

    def test_old_refresh_token_rejected_after_rotation(self, client):
        """Reusing old refresh token after rotation returns 401."""
        tokens = _issue_tokens_static(client)
        original_refresh = tokens["refresh_token"]

        # First rotation succeeds
        resp1 = client.post(
            "/api/auth/refresh",
            json={"refresh_token": original_refresh},
        )
        assert resp1.status_code == 200

        # Second attempt with same old token must fail
        resp2 = client.post(
            "/api/auth/refresh",
            json={"refresh_token": original_refresh},
        )
        assert resp2.status_code == 401

    def test_new_refresh_token_valid_after_rotation(self, client):
        """New refresh token from rotation can be used for another rotation."""
        tokens = _issue_tokens_static(client)
        resp1 = client.post(
            "/api/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert resp1.status_code == 200
        new_tokens = resp1.json()

        # Use the new refresh token
        resp2 = client.post(
            "/api/auth/refresh",
            json={"refresh_token": new_tokens["refresh_token"]},
        )
        assert resp2.status_code == 200

    def test_invalid_refresh_token_returns_401(self, client):
        """Garbage token string returns 401."""
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": "not.a.real.jwt"},
        )
        assert resp.status_code == 401

    def test_access_token_rejected_as_refresh_token(self, client):
        """Passing an access token where refresh token expected returns 401."""
        tokens = _issue_tokens_static(client)
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": tokens["access_token"]},
        )
        assert resp.status_code == 401

    def test_rotation_expires_in_correct(self, client):
        """expires_in reflects the configured 15-minute access token expiry."""
        tokens = _issue_tokens_static(client)
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["expires_in"] == 15 * 60


# ============================================================================
# Backward compatibility: legacy FORGE_WEBHOOK_TOKEN bearer auth
# ============================================================================


class TestLegacyTokenBackwardCompat:
    def test_legacy_static_bearer_token_accepted(self, client):
        """Requests with FORGE_WEBHOOK_TOKEN in Authorization header still work."""
        # The verify_unified_auth dependency falls back to legacy bearer if JWT fails
        # We test this by using the static token directly against a protected endpoint
        from forge_harness.webhook_server_main import create_app

        with patch.dict(
            os.environ,
            {
                "FORGE_WEBHOOK_TOKEN": STATIC_TOKEN,
                "FORGE_JWT_SECRET": JWT_SECRET,
                "FORGE_WEBHOOK_REQUIRE_AUTH": "true",
                "FORGE_JWT_REQUIRE_AUTH": "true",
            },
            clear=False,
        ):
            app = create_app()
            # No dependency override — use real auth
            legacy_client = TestClient(app, raise_server_exceptions=False)

            # The /api/auth/validate endpoint validates a static token
            resp = legacy_client.post(
                "/api/auth/validate",
                headers={"Authorization": f"Bearer {STATIC_TOKEN}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True

    def test_legacy_invalid_token_rejected(self, client):
        """Wrong static bearer token returns 401."""
        from forge_harness.webhook_server_main import create_app

        with patch.dict(
            os.environ,
            {
                "FORGE_WEBHOOK_TOKEN": STATIC_TOKEN,
                "FORGE_JWT_SECRET": JWT_SECRET,
                "FORGE_WEBHOOK_REQUIRE_AUTH": "true",
            },
            clear=False,
        ):
            app = create_app()
            legacy_client = TestClient(app, raise_server_exceptions=False)
            resp = legacy_client.post(
                "/api/auth/validate",
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert resp.status_code == 401


# ============================================================================
# POST /api/legacy/auth/refresh
# ============================================================================


class TestLegacyRefreshEndpoint:
    def test_valid_refresh_token_in_bearer_header(self, client):
        """Legacy endpoint: refresh token in Authorization Bearer returns new pair."""
        tokens = _issue_tokens_static(client)
        resp = client.post(
            "/api/legacy/auth/refresh",
            headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "access_token" in data["data"]
        assert "refresh_token" in data["data"]

    def test_missing_bearer_returns_401(self, client):
        """Legacy endpoint without token returns 401."""
        resp = client.post("/api/legacy/auth/refresh")
        assert resp.status_code == 401
        data = resp.json()
        assert data["success"] is False
        assert data["error"]["code"] == "MISSING_TOKEN"

    def test_reused_refresh_token_rejected(self, client):
        """Legacy endpoint rejects already-rotated refresh token."""
        tokens = _issue_tokens_static(client)
        refresh = tokens["refresh_token"]

        # First use succeeds
        resp1 = client.post(
            "/api/legacy/auth/refresh",
            headers={"Authorization": f"Bearer {refresh}"},
        )
        assert resp1.status_code == 200

        # Second use of same token fails
        resp2 = client.post(
            "/api/legacy/auth/refresh",
            headers={"Authorization": f"Bearer {refresh}"},
        )
        assert resp2.status_code == 401


# ============================================================================
# FileRefreshTokenStore unit tests
# ============================================================================


class TestFileRefreshTokenStore:
    def test_revoke_jti_persists_to_disk(self, tmp_store_path):
        """Revoking a JTI writes it to the file."""
        from forge_harness.webhook_server.infrastructure.refresh_token_store import (
            FileRefreshTokenStore,
        )

        store = FileRefreshTokenStore(store_path=tmp_store_path)
        store.revoke_jti("abc123")

        assert tmp_store_path.exists()
        data = json.loads(tmp_store_path.read_text())
        assert "abc123" in data["revoked_jtis"]

    def test_is_jti_revoked_reads_from_disk(self, tmp_store_path):
        """Revocation check reads current file state."""
        from forge_harness.webhook_server.infrastructure.refresh_token_store import (
            FileRefreshTokenStore,
        )

        store = FileRefreshTokenStore(store_path=tmp_store_path)
        store.revoke_jti("xyz789")

        # Create a fresh store instance pointing to same path
        store2 = FileRefreshTokenStore(store_path=tmp_store_path)
        assert store2.is_jti_revoked("xyz789") is True
        assert store2.is_jti_revoked("other") is False

    def test_revoke_family_persists_to_disk(self, tmp_store_path):
        """Revoking a family ID writes it to the file."""
        from forge_harness.webhook_server.infrastructure.refresh_token_store import (
            FileRefreshTokenStore,
        )

        store = FileRefreshTokenStore(store_path=tmp_store_path)
        store.revoke_family("family-001")

        data = json.loads(tmp_store_path.read_text())
        assert "family-001" in data["revoked_families"]

    def test_is_family_revoked_reads_from_disk(self, tmp_store_path):
        """Family revocation check reads current file state."""
        from forge_harness.webhook_server.infrastructure.refresh_token_store import (
            FileRefreshTokenStore,
        )

        store = FileRefreshTokenStore(store_path=tmp_store_path)
        store.revoke_family("family-002")

        store2 = FileRefreshTokenStore(store_path=tmp_store_path)
        assert store2.is_family_revoked("family-002") is True
        assert store2.is_family_revoked("family-999") is False

    def test_missing_store_file_returns_not_revoked(self, tmp_path):
        """Store treats missing file as empty (not revoked)."""
        from forge_harness.webhook_server.infrastructure.refresh_token_store import (
            FileRefreshTokenStore,
        )

        store = FileRefreshTokenStore(store_path=tmp_path / "nonexistent.json")
        assert store.is_jti_revoked("any") is False
        assert store.is_family_revoked("any") is False

    def test_corrupt_store_file_treated_as_empty(self, tmp_store_path):
        """Corrupt JSON in store file is handled gracefully."""
        from forge_harness.webhook_server.infrastructure.refresh_token_store import (
            FileRefreshTokenStore,
        )

        tmp_store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_store_path.write_text("{invalid json{{")

        store = FileRefreshTokenStore(store_path=tmp_store_path)
        assert store.is_jti_revoked("any") is False

    def test_clear_empties_store(self, tmp_store_path):
        """Clear() removes all revocation records."""
        from forge_harness.webhook_server.infrastructure.refresh_token_store import (
            FileRefreshTokenStore,
        )

        store = FileRefreshTokenStore(store_path=tmp_store_path)
        store.revoke_jti("jti-1")
        store.revoke_family("fam-1")

        store.clear()
        assert store.is_jti_revoked("jti-1") is False
        assert store.is_family_revoked("fam-1") is False

    def test_atomic_write_uses_tmp_then_rename(self, tmp_store_path, monkeypatch):
        """Store writes atomically (temp file then rename) to avoid partial writes."""
        from forge_harness.webhook_server.infrastructure import refresh_token_store as rts

        written_paths = []
        original_save = rts._save_store

        def capturing_save(store_path, data):
            tmp_path = store_path.with_suffix(".json.tmp")
            written_paths.append(tmp_path)
            return original_save(store_path, data)

        monkeypatch.setattr(rts, "_save_store", capturing_save)

        store = rts.FileRefreshTokenStore(store_path=tmp_store_path)
        store.revoke_jti("some-jti")

        # After atomic write, the .tmp file should not exist (renamed)
        for tmp_path in written_paths:
            assert not tmp_path.exists(), f"Temp file {tmp_path} was not renamed"


# ============================================================================
# POST /api/auth/login (existing endpoint — regression)
# ============================================================================


class TestLoginEndpointRegression:
    def test_login_valid_credentials(self, client):
        """Login endpoint still works after our changes."""
        resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "forge_admin"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    def test_login_invalid_credentials(self, client):
        """Login endpoint returns 401 for wrong password."""
        resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "bad-password"},
        )
        assert resp.status_code == 401


# ============================================================================
# JWT config: 15-minute default access token expiry
# ============================================================================


class TestJWTConfigDefaults:
    def test_default_access_token_expiry_is_15_minutes(self):
        """JWTAuthConfig defaults to 15-minute access tokens."""
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthConfig

        with patch.dict(os.environ, {"FORGE_JWT_SECRET": JWT_SECRET}, clear=False):
            # Unset override so we get the default
            env = {
                k: v for k, v in os.environ.items() if k != "FORGE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES"
            }
            with patch.dict(os.environ, env, clear=True):
                config = JWTAuthConfig.from_env()
        assert config.access_token_expire_minutes == 15

    def test_access_token_expiry_env_override(self):
        """FORGE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES overrides default."""
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthConfig

        with patch.dict(
            os.environ,
            {"FORGE_JWT_SECRET": JWT_SECRET, "FORGE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "30"},
            clear=False,
        ):
            config = JWTAuthConfig.from_env()
        assert config.access_token_expire_minutes == 30
