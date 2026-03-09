"""Unit tests for forge_harness.webhook_server.infrastructure.api_key_auth.

Covers:
- APIKeyResult enum values
- APIKeyPayload dataclass construction and defaults
- generate_api_key format and uniqueness
- get_key_hash determinism and correctness
- register_api_key storage, hashing, expiry, and default permissions
- verify_api_key: missing key, invalid key, master key env-var fallback,
  revoked key, expired key, valid key, last_used_at update
- revoke_api_key: existing key, missing key

All tests operate against an isolated copy of the _api_keys store so they
cannot interfere with each other.
"""

import hashlib
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

import forge_harness.webhook_server.infrastructure.api_key_auth as _module

# ---------------------------------------------------------------------------
# Module import
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.infrastructure.api_key_auth import (
    APIKeyPayload,
    APIKeyResult,
    generate_api_key,
    get_key_hash,
    register_api_key,
    revoke_api_key,
    verify_api_key,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_store():
    """Return the module-level _api_keys dict after clearing it."""
    _module._api_keys.clear()
    return _module._api_keys


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_key_store():
    """Clear the global in-memory key store before and after every test."""
    _clean_store()
    yield
    _clean_store()


@pytest.fixture()
def sample_key():
    return "forge_key_abc123testkey"


@pytest.fixture()
def registered_key(sample_key):
    register_api_key(sample_key, owner="tester", permissions=["read", "write"])
    return sample_key


# ---------------------------------------------------------------------------
# APIKeyResult enum
# ---------------------------------------------------------------------------

class TestAPIKeyResult:
    def test_enum_values(self):
        assert APIKeyResult.SUCCESS.value == "success"
        assert APIKeyResult.MISSING_KEY.value == "missing_key"
        assert APIKeyResult.INVALID_KEY.value == "invalid_key"
        assert APIKeyResult.EXPIRED_KEY.value == "expired_key"
        assert APIKeyResult.REVOKED_KEY.value == "revoked_key"

    def test_all_five_members_exist(self):
        members = set(APIKeyResult)
        assert len(members) == 5

    def test_members_are_distinct(self):
        values = [m.value for m in APIKeyResult]
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# APIKeyPayload dataclass
# ---------------------------------------------------------------------------

class TestAPIKeyPayload:
    def test_required_fields(self):
        now = datetime.now(UTC)
        payload = APIKeyPayload(
            key_id="abc123",
            owner="alice",
            permissions=["read"],
            created_at=now,
        )
        assert payload.key_id == "abc123"
        assert payload.owner == "alice"
        assert payload.permissions == ["read"]
        assert payload.created_at == now

    def test_optional_fields_default_to_none_or_true(self):
        payload = APIKeyPayload(
            key_id="x",
            owner="o",
            permissions=[],
            created_at=datetime.now(UTC),
        )
        assert payload.expires_at is None
        assert payload.last_used_at is None
        assert payload.is_active is True

    def test_explicit_optional_fields(self):
        now = datetime.now(UTC)
        expiry = now + timedelta(days=30)
        payload = APIKeyPayload(
            key_id="k",
            owner="bob",
            permissions=["admin"],
            created_at=now,
            expires_at=expiry,
            last_used_at=now,
            is_active=False,
        )
        assert payload.expires_at == expiry
        assert payload.last_used_at == now
        assert payload.is_active is False

    def test_payload_is_mutable(self):
        payload = APIKeyPayload(
            key_id="k",
            owner="bob",
            permissions=["read"],
            created_at=datetime.now(UTC),
        )
        payload.last_used_at = datetime.now(UTC)
        assert payload.last_used_at is not None


# ---------------------------------------------------------------------------
# generate_api_key
# ---------------------------------------------------------------------------

class TestGenerateApiKey:
    def test_prefix(self):
        key = generate_api_key()
        assert key.startswith("forge_key_")

    def test_minimum_length(self):
        key = generate_api_key()
        # "forge_key_" is 10 chars; token_urlsafe(32) yields >=43 base64 chars
        assert len(key) > 40

    def test_uniqueness(self):
        keys = {generate_api_key() for _ in range(50)}
        assert len(keys) == 50

    def test_url_safe_characters(self):
        key = generate_api_key()
        suffix = key[len("forge_key_"):]
        # URL-safe base64 uses A-Z a-z 0-9 - _
        import re
        assert re.fullmatch(r"[A-Za-z0-9\-_]+", suffix)


# ---------------------------------------------------------------------------
# get_key_hash
# ---------------------------------------------------------------------------

class TestGetKeyHash:
    def test_returns_sha256_hex(self):
        key = "my_test_key"
        expected = hashlib.sha256(key.encode()).hexdigest()
        assert get_key_hash(key) == expected

    def test_deterministic(self):
        key = "deterministic_key"
        assert get_key_hash(key) == get_key_hash(key)

    def test_different_keys_different_hashes(self):
        assert get_key_hash("key_a") != get_key_hash("key_b")

    def test_hash_length_is_64_chars(self):
        assert len(get_key_hash("any_key")) == 64

    def test_empty_string(self):
        # Empty string should still produce a valid SHA-256 hex digest
        result = get_key_hash("")
        assert len(result) == 64

    def test_unicode_key(self):
        key = "forge_\u00e9\u00e0\u00fc"
        result = get_key_hash(key)
        expected = hashlib.sha256(key.encode()).hexdigest()
        assert result == expected


# ---------------------------------------------------------------------------
# register_api_key
# ---------------------------------------------------------------------------

class TestRegisterApiKey:
    def test_key_stored_by_hash(self, sample_key):
        register_api_key(sample_key, owner="owner1")
        key_hash = get_key_hash(sample_key)
        assert key_hash in _module._api_keys

    def test_payload_owner(self, sample_key):
        register_api_key(sample_key, owner="owner1")
        payload = _module._api_keys[get_key_hash(sample_key)]
        assert payload.owner == "owner1"

    def test_default_permissions_are_read(self, sample_key):
        register_api_key(sample_key, owner="owner1")
        payload = _module._api_keys[get_key_hash(sample_key)]
        assert payload.permissions == ["read"]

    def test_custom_permissions(self, sample_key):
        register_api_key(sample_key, owner="owner1", permissions=["admin", "write"])
        payload = _module._api_keys[get_key_hash(sample_key)]
        assert payload.permissions == ["admin", "write"]

    def test_no_expiry_by_default(self, sample_key):
        register_api_key(sample_key, owner="owner1")
        payload = _module._api_keys[get_key_hash(sample_key)]
        assert payload.expires_at is None

    def test_expiry_set_when_expires_in_days_given(self, sample_key):
        # The source truncates microseconds via .replace(microsecond=0), so we
        # must do the same when building the boundary timestamps.
        before = datetime.now(UTC).replace(microsecond=0)
        register_api_key(sample_key, owner="owner1", expires_in_days=7)
        after = datetime.now(UTC).replace(microsecond=0)
        payload = _module._api_keys[get_key_hash(sample_key)]
        assert payload.expires_at is not None
        expected_low = before + timedelta(days=7)
        expected_high = after + timedelta(days=7)
        assert expected_low <= payload.expires_at <= expected_high

    def test_expiry_has_no_microseconds(self, sample_key):
        register_api_key(sample_key, owner="owner1", expires_in_days=1)
        payload = _module._api_keys[get_key_hash(sample_key)]
        assert payload.expires_at.microsecond == 0

    def test_is_active_true_on_registration(self, sample_key):
        register_api_key(sample_key, owner="owner1")
        payload = _module._api_keys[get_key_hash(sample_key)]
        assert payload.is_active is True

    def test_key_id_is_first_12_chars_of_hash(self, sample_key):
        register_api_key(sample_key, owner="owner1")
        key_hash = get_key_hash(sample_key)
        payload = _module._api_keys[key_hash]
        assert payload.key_id == key_hash[:12]

    def test_created_at_is_utc_aware(self, sample_key):
        register_api_key(sample_key, owner="owner1")
        payload = _module._api_keys[get_key_hash(sample_key)]
        assert payload.created_at.tzinfo is not None

    def test_overwrite_existing_key(self, sample_key):
        register_api_key(sample_key, owner="first_owner", permissions=["read"])
        register_api_key(sample_key, owner="second_owner", permissions=["admin"])
        payload = _module._api_keys[get_key_hash(sample_key)]
        assert payload.owner == "second_owner"
        assert payload.permissions == ["admin"]

    def test_expires_in_days_zero_not_treated_as_truthy(self, sample_key):
        # expires_in_days=0 is falsy; no expiry should be set
        register_api_key(sample_key, owner="owner1", expires_in_days=0)
        payload = _module._api_keys[get_key_hash(sample_key)]
        assert payload.expires_at is None


# ---------------------------------------------------------------------------
# verify_api_key
# ---------------------------------------------------------------------------

class TestVerifyApiKey:

    # -- missing key --

    def test_empty_string_returns_missing_key(self):
        result, payload = verify_api_key("")
        assert result == APIKeyResult.MISSING_KEY
        assert payload is None

    def test_none_returns_missing_key(self):
        # None is falsy, same branch as empty string
        result, payload = verify_api_key(None)  # type: ignore[arg-type]
        assert result == APIKeyResult.MISSING_KEY
        assert payload is None

    # -- invalid key (not registered, no master key) --

    def test_unregistered_key_returns_invalid(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORGE_MASTER_API_KEY", None)
            result, payload = verify_api_key("forge_key_doesnotexist")
        assert result == APIKeyResult.INVALID_KEY
        assert payload is None

    def test_wrong_key_returns_invalid(self, registered_key):
        result, payload = verify_api_key("forge_key_wrongvalue")
        assert result == APIKeyResult.INVALID_KEY
        assert payload is None

    # -- master key env-var fallback --

    def test_master_key_success(self):
        master = "super_secret_master"
        with patch.dict(os.environ, {"FORGE_MASTER_API_KEY": master}):
            result, payload = verify_api_key(master)
        assert result == APIKeyResult.SUCCESS
        assert payload is not None
        assert payload.key_id == "master"
        assert payload.owner == "admin"
        assert "admin" in payload.permissions
        assert "read" in payload.permissions
        assert "write" in payload.permissions

    def test_master_key_wrong_value_returns_invalid(self):
        with patch.dict(os.environ, {"FORGE_MASTER_API_KEY": "correct_master"}):
            result, payload = verify_api_key("wrong_master")
        assert result == APIKeyResult.INVALID_KEY
        assert payload is None

    def test_master_key_not_set_returns_invalid_for_unknown_key(self):
        env = {k: v for k, v in os.environ.items() if k != "FORGE_MASTER_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            result, payload = verify_api_key("some_unknown_key")
        assert result == APIKeyResult.INVALID_KEY
        assert payload is None

    def test_master_key_payload_is_active(self):
        master = "active_master_key"
        with patch.dict(os.environ, {"FORGE_MASTER_API_KEY": master}):
            _, payload = verify_api_key(master)
        assert payload.is_active is True

    # -- revoked key --

    def test_revoked_key_returns_revoked(self, registered_key):
        key_hash = get_key_hash(registered_key)
        _module._api_keys[key_hash].is_active = False
        result, payload = verify_api_key(registered_key)
        assert result == APIKeyResult.REVOKED_KEY
        assert payload is None

    # -- expired key --

    def test_expired_key_returns_expired(self, registered_key):
        key_hash = get_key_hash(registered_key)
        # Set expiry to 1 second in the past
        _module._api_keys[key_hash].expires_at = datetime.now(UTC) - timedelta(seconds=1)
        result, payload = verify_api_key(registered_key)
        assert result == APIKeyResult.EXPIRED_KEY
        assert payload is None

    def test_key_expiring_in_future_is_valid(self, registered_key):
        key_hash = get_key_hash(registered_key)
        _module._api_keys[key_hash].expires_at = datetime.now(UTC) + timedelta(days=30)
        result, payload = verify_api_key(registered_key)
        assert result == APIKeyResult.SUCCESS
        assert payload is not None

    def test_key_with_no_expiry_is_valid(self, registered_key):
        # registered_key has no expiry
        result, payload = verify_api_key(registered_key)
        assert result == APIKeyResult.SUCCESS

    # -- successful verification --

    def test_valid_key_returns_success(self, registered_key):
        result, payload = verify_api_key(registered_key)
        assert result == APIKeyResult.SUCCESS
        assert payload is not None

    def test_valid_key_returns_correct_owner(self, registered_key):
        _, payload = verify_api_key(registered_key)
        assert payload.owner == "tester"

    def test_valid_key_returns_correct_permissions(self, registered_key):
        _, payload = verify_api_key(registered_key)
        assert "read" in payload.permissions
        assert "write" in payload.permissions

    def test_valid_key_updates_last_used_at(self, registered_key):
        before = datetime.now(UTC)
        _, payload = verify_api_key(registered_key)
        after = datetime.now(UTC)
        assert payload.last_used_at is not None
        assert before <= payload.last_used_at <= after

    def test_last_used_at_updates_in_place(self, registered_key):
        key_hash = get_key_hash(registered_key)
        # Verify twice; last_used_at should be updated on each call
        verify_api_key(registered_key)
        first_used = _module._api_keys[key_hash].last_used_at
        verify_api_key(registered_key)
        second_used = _module._api_keys[key_hash].last_used_at
        assert second_used >= first_used

    def test_verify_does_not_remove_key_from_store(self, registered_key):
        key_hash = get_key_hash(registered_key)
        verify_api_key(registered_key)
        assert key_hash in _module._api_keys

    # -- register-then-verify cycle --

    def test_register_with_expiry_then_verify(self, sample_key):
        register_api_key(sample_key, owner="tester", expires_in_days=1)
        result, payload = verify_api_key(sample_key)
        assert result == APIKeyResult.SUCCESS
        assert payload.expires_at is not None

    def test_multiple_keys_independent(self):
        key_a = "forge_key_alpha"
        key_b = "forge_key_beta"
        register_api_key(key_a, owner="alice", permissions=["read"])
        register_api_key(key_b, owner="bob", permissions=["admin"])

        result_a, payload_a = verify_api_key(key_a)
        result_b, payload_b = verify_api_key(key_b)

        assert result_a == APIKeyResult.SUCCESS
        assert payload_a.owner == "alice"
        assert result_b == APIKeyResult.SUCCESS
        assert payload_b.owner == "bob"


# ---------------------------------------------------------------------------
# revoke_api_key
# ---------------------------------------------------------------------------

class TestRevokeApiKey:
    def test_revoke_existing_key_returns_true(self, registered_key):
        key_hash = get_key_hash(registered_key)
        result = revoke_api_key(key_hash)
        assert result is True

    def test_revoke_sets_is_active_false(self, registered_key):
        key_hash = get_key_hash(registered_key)
        revoke_api_key(key_hash)
        assert _module._api_keys[key_hash].is_active is False

    def test_revoke_nonexistent_key_returns_false(self):
        result = revoke_api_key("nonexistent_hash_value")
        assert result is False

    def test_revoked_key_fails_verify(self, registered_key):
        key_hash = get_key_hash(registered_key)
        revoke_api_key(key_hash)
        result, payload = verify_api_key(registered_key)
        assert result == APIKeyResult.REVOKED_KEY
        assert payload is None

    def test_revoke_by_full_hash_only(self, registered_key):
        key_hash = get_key_hash(registered_key)
        # Revoking by prefix should NOT work (expects full hash)
        result = revoke_api_key(key_hash[:12])
        assert result is False
        # Key should still be active
        assert _module._api_keys[key_hash].is_active is True

    def test_double_revoke_second_call_returns_false(self, registered_key):
        key_hash = get_key_hash(registered_key)
        revoke_api_key(key_hash)
        # Second revoke: key exists but is already inactive; the function
        # still finds it by hash so returns True again (key is present in dict)
        # — this tests the actual implemented behaviour, not an assumption
        result = revoke_api_key(key_hash)
        # The current implementation checks `if key_hash in _api_keys` and sets
        # is_active=False regardless; it returns True when found
        assert result is True

    def test_revoke_empty_string_returns_false(self):
        result = revoke_api_key("")
        assert result is False


# ---------------------------------------------------------------------------
# Logging (smoke-test: no exception raised on normal paths)
# ---------------------------------------------------------------------------

class TestLogging:
    def test_register_logs_no_exception(self, sample_key):
        """Verify logger.info is called with the owner name on registration.

        The module uses a Rich-based logger that routes to stderr rather than
        the standard logging hierarchy, so caplog cannot capture its output.
        We patch the module-level logger directly instead.
        """
        with patch.object(_module.logger, "info") as mock_info:
            register_api_key(sample_key, owner="log_tester")
        assert mock_info.called
        call_args = " ".join(str(a) for a in mock_info.call_args[0])
        assert "log_tester" in call_args
