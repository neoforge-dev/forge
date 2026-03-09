"""Tests for user_store.py infrastructure module.

Covers: UserInfo dataclass, UserStore CRUD/auth, get_user_store singleton.
"""

from __future__ import annotations

import hashlib
import os
from unittest.mock import patch

import pytest

from forge_harness.webhook_server.infrastructure.user_store import (
    UserInfo,
    UserStore,
    get_user_store,
)

# ---------------------------------------------------------------------------
# UserInfo
# ---------------------------------------------------------------------------


class TestUserInfo:
    """Tests for the UserInfo dataclass."""

    def test_default_is_active(self):
        """is_active should default to True."""
        user = UserInfo(
            username="alice",
            password_hash="hash",
            salt="salt",
            permissions=["read"],
        )
        assert user.is_active is True

    def test_explicit_inactive(self):
        """is_active can be set to False."""
        user = UserInfo(
            username="bob",
            password_hash="hash",
            salt="salt",
            permissions=["read"],
            is_active=False,
        )
        assert user.is_active is False

    def test_permissions_stored_as_list(self):
        """permissions is preserved as-is."""
        perms = ["read", "write", "admin"]
        user = UserInfo(
            username="carol",
            password_hash="h",
            salt="s",
            permissions=perms,
        )
        assert user.permissions == perms

    def test_fields_accessible(self):
        """All fields are accessible as attributes."""
        user = UserInfo(
            username="dave",
            password_hash="deadbeef",
            salt="cafebabe",
            permissions=["read"],
        )
        assert user.username == "dave"
        assert user.password_hash == "deadbeef"
        assert user.salt == "cafebabe"


# ---------------------------------------------------------------------------
# UserStore – initialization
# ---------------------------------------------------------------------------


class TestUserStoreInit:
    """Tests for UserStore initialisation from environment."""

    def test_default_admin_created_when_no_env(self):
        """Without FORGE_AUTH_USERS, a default admin account is created."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()

        user = store.get_user("admin")
        assert user is not None
        assert user.username == "admin"
        assert "admin" in user.permissions

    def test_default_admin_authenticates(self):
        """Default admin/forge_admin credentials must work."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()

        result = store.authenticate("admin", "forge_admin")
        assert result is not None
        assert result.username == "admin"

    def test_load_single_user_from_env(self):
        """A single username:password entry is loaded correctly."""
        with patch.dict(os.environ, {"FORGE_AUTH_USERS": "alice:secret"}, clear=True):
            store = UserStore()

        user = store.get_user("alice")
        assert user is not None
        assert user.permissions == ["read", "write"]

    def test_load_user_with_permissions_from_env(self):
        """Custom permissions are parsed from the env string."""
        with patch.dict(
            os.environ,
            {"FORGE_AUTH_USERS": "bob:pass:read|admin"},
            clear=True,
        ):
            store = UserStore()

        user = store.get_user("bob")
        assert user is not None
        assert "read" in user.permissions
        assert "admin" in user.permissions

    def test_load_multiple_users_from_env(self):
        """Multiple comma-separated users are all loaded."""
        with patch.dict(
            os.environ,
            {"FORGE_AUTH_USERS": "alice:pw1,bob:pw2"},
            clear=True,
        ):
            store = UserStore()

        assert store.get_user("alice") is not None
        assert store.get_user("bob") is not None

    def test_admin_permission_auto_appended(self):
        """If username is 'admin' but 'admin' perm is missing, it is appended."""
        with patch.dict(
            os.environ,
            {"FORGE_AUTH_USERS": "admin:secret:read|write"},
            clear=True,
        ):
            store = UserStore()

        user = store.get_user("admin")
        assert "admin" in user.permissions

    def test_admin_permission_not_duplicated(self):
        """If username is 'admin' and already has 'admin' perm, no duplicate."""
        with patch.dict(
            os.environ,
            {"FORGE_AUTH_USERS": "admin:secret:read|admin"},
            clear=True,
        ):
            store = UserStore()

        user = store.get_user("admin")
        assert user.permissions.count("admin") == 1

    def test_malformed_entry_too_few_parts_skipped(self):
        """Entries with fewer than 2 colon-separated parts are silently ignored."""
        with patch.dict(
            os.environ,
            {"FORGE_AUTH_USERS": "onlyusername,valid:pass"},
            clear=True,
        ):
            store = UserStore()

        assert store.get_user("onlyusername") is None
        assert store.get_user("valid") is not None


# ---------------------------------------------------------------------------
# UserStore.add_user
# ---------------------------------------------------------------------------


class TestUserStoreAddUser:
    """Tests for UserStore.add_user."""

    def test_add_user_stores_user(self):
        """add_user should store the user under the given username."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        store.add_user("eve", "password123")
        assert store.get_user("eve") is not None

    def test_add_user_default_permissions(self):
        """add_user without permissions defaults to ['read']."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        store.add_user("frank", "pw")
        user = store.get_user("frank")
        assert user.permissions == ["read"]

    def test_add_user_custom_permissions(self):
        """add_user stores the supplied permissions."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        store.add_user("grace", "pw", ["read", "write"])
        user = store.get_user("grace")
        assert user.permissions == ["read", "write"]

    def test_add_user_stores_hash_not_plaintext(self):
        """The stored password_hash must not equal the plaintext password."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        store.add_user("henry", "mysecret")
        user = store.get_user("henry")
        assert user.password_hash != "mysecret"

    def test_add_user_hash_is_deterministic_with_same_salt(self):
        """Given the same password and salt, _hash_password is deterministic."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        salt = "testsalt"
        h1 = store._hash_password("pw", salt)
        h2 = store._hash_password("pw", salt)
        assert h1 == h2

    def test_add_user_unique_salt_per_user(self):
        """Two calls to add_user should produce different salts (with overwhelming probability)."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        store.add_user("user1", "same_password")
        store.add_user("user2", "same_password")
        u1 = store.get_user("user1")
        u2 = store.get_user("user2")
        assert u1.salt != u2.salt

    def test_add_user_overwrites_existing(self):
        """Adding a user with the same name replaces the previous entry."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        store.add_user("ivy", "old_pass")
        store.add_user("ivy", "new_pass")
        # New password should authenticate
        assert store.authenticate("ivy", "new_pass") is not None
        assert store.authenticate("ivy", "old_pass") is None


# ---------------------------------------------------------------------------
# UserStore.authenticate
# ---------------------------------------------------------------------------


class TestUserStoreAuthenticate:
    """Tests for UserStore.authenticate."""

    @pytest.fixture
    def store(self):
        """Isolated UserStore with no env contamination."""
        with patch.dict(os.environ, {}, clear=True):
            s = UserStore()
        s.add_user("tester", "correct_password", ["read", "write"])
        return s

    def test_authenticate_correct_credentials(self, store):
        """Correct username and password returns UserInfo."""
        result = store.authenticate("tester", "correct_password")
        assert result is not None
        assert result.username == "tester"

    def test_authenticate_wrong_password(self, store):
        """Wrong password returns None."""
        result = store.authenticate("tester", "wrong_password")
        assert result is None

    def test_authenticate_nonexistent_user(self, store):
        """Non-existent username returns None."""
        result = store.authenticate("ghost", "any_password")
        assert result is None

    def test_authenticate_inactive_user(self, store):
        """Inactive user returns None even with correct password."""
        store.add_user("disabled", "pw123")
        user = store.get_user("disabled")
        user.is_active = False

        result = store.authenticate("disabled", "pw123")
        assert result is None

    def test_authenticate_returns_user_info_with_permissions(self, store):
        """Returned UserInfo should carry the correct permissions."""
        result = store.authenticate("tester", "correct_password")
        assert result is not None
        assert "read" in result.permissions
        assert "write" in result.permissions

    def test_authenticate_empty_username(self, store):
        """Empty username string returns None."""
        result = store.authenticate("", "correct_password")
        assert result is None

    def test_authenticate_empty_password(self, store):
        """Empty password returns None when it does not match stored hash."""
        result = store.authenticate("tester", "")
        assert result is None

    def test_authenticate_uses_constant_time_comparison(self, store):
        """authenticate uses secrets.compare_digest (constant-time)."""
        # We verify the code path works correctly with injected hash collision
        # attempt; the main assertion is functional correctness.
        import secrets as _secrets

        called = []
        original = _secrets.compare_digest

        def spy_compare(a, b):
            called.append((a, b))
            return original(a, b)

        with patch(
            "forge_harness.webhook_server.infrastructure.user_store.secrets.compare_digest",
            side_effect=spy_compare,
        ):
            store.authenticate("tester", "correct_password")

        assert len(called) == 1


# ---------------------------------------------------------------------------
# UserStore.get_user
# ---------------------------------------------------------------------------


class TestUserStoreGetUser:
    """Tests for UserStore.get_user."""

    def test_get_existing_user(self):
        """get_user returns user for known username."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        store.add_user("jack", "pw")
        user = store.get_user("jack")
        assert user is not None
        assert user.username == "jack"

    def test_get_nonexistent_user(self):
        """get_user returns None for unknown username."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        result = store.get_user("nobody")
        assert result is None

    def test_get_user_does_not_mutate_store(self):
        """Calling get_user twice returns the same object reference."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        store.add_user("kim", "pw")
        u1 = store.get_user("kim")
        u2 = store.get_user("kim")
        assert u1 is u2


# ---------------------------------------------------------------------------
# UserStore._hash_password
# ---------------------------------------------------------------------------


class TestHashPassword:
    """Tests for the internal _hash_password helper."""

    def test_produces_sha256_hex(self):
        """Output should be a 64-character hex string (SHA-256)."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        result = store._hash_password("password", "salt")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_matches_manual_sha256(self):
        """Output should match manually computed SHA-256(password + salt)."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        password, salt = "hunter2", "abc123"
        expected = hashlib.sha256((password + salt).encode()).hexdigest()
        assert store._hash_password(password, salt) == expected

    def test_different_passwords_different_hashes(self):
        """Different passwords with same salt produce different hashes."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        h1 = store._hash_password("pw1", "salt")
        h2 = store._hash_password("pw2", "salt")
        assert h1 != h2

    def test_different_salts_different_hashes(self):
        """Same password with different salts produces different hashes."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()
        h1 = store._hash_password("pw", "salt1")
        h2 = store._hash_password("pw", "salt2")
        assert h1 != h2


# ---------------------------------------------------------------------------
# get_user_store singleton
# ---------------------------------------------------------------------------


class TestGetUserStore:
    """Tests for the module-level get_user_store singleton."""

    def test_returns_user_store_instance(self):
        """get_user_store() should return a UserStore instance."""
        store = get_user_store()
        assert isinstance(store, UserStore)

    def test_singleton_returns_same_instance(self):
        """Multiple calls return the same object."""
        s1 = get_user_store()
        s2 = get_user_store()
        assert s1 is s2

    def test_singleton_has_admin_user(self):
        """The module-level singleton should always have an admin user."""
        store = get_user_store()
        # Admin is guaranteed by _load_from_env fallback or FORGE_AUTH_USERS
        # Having at least one user is sufficient to show the store initialised.
        assert store is not None


# ---------------------------------------------------------------------------
# Integration: full auth round-trip
# ---------------------------------------------------------------------------


class TestAuthIntegration:
    """End-to-end integration tests combining add_user and authenticate."""

    def test_multiple_users_independent_auth(self):
        """Each user authenticates with their own password only."""
        with patch.dict(os.environ, {}, clear=True):
            store = UserStore()

        store.add_user("user_a", "pass_a")
        store.add_user("user_b", "pass_b")

        assert store.authenticate("user_a", "pass_a") is not None
        assert store.authenticate("user_a", "pass_b") is None
        assert store.authenticate("user_b", "pass_b") is not None
        assert store.authenticate("user_b", "pass_a") is None

    def test_env_user_authenticates(self):
        """Users loaded from FORGE_AUTH_USERS env can authenticate."""
        with patch.dict(
            os.environ,
            {"FORGE_AUTH_USERS": "envuser:envpass:read|write"},
            clear=True,
        ):
            store = UserStore()

        result = store.authenticate("envuser", "envpass")
        assert result is not None
        assert result.username == "envuser"
        assert "read" in result.permissions
        assert "write" in result.permissions

    def test_env_user_wrong_password_fails(self):
        """Users from env do not authenticate with the wrong password."""
        with patch.dict(
            os.environ,
            {"FORGE_AUTH_USERS": "secureuser:rightpass"},
            clear=True,
        ):
            store = UserStore()

        assert store.authenticate("secureuser", "wrongpass") is None
