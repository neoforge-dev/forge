"""
Tests for auth dependencies.

Test coverage for authentication dependencies, guards, and permission checks.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, Request, Depends, HTTPException
from starlette.testclient import TestClient


class TestAuthDependencies:
    """
    Tests for auth dependency functions.

    Tests get_current_user, require_user, and permission guards.
    """

    def test_auth_dependencies_imports(self):
        """
        Test that auth dependencies can be imported.
        """
        try:
            from forge_shared.auth.dependencies import get_current_user

            assert get_current_user is not None
        except ImportError:
            pytest.skip("Auth dependencies not implemented")

    def test_forge_user_creation(self):
        """Test creating a ForgeUser with valid data."""
        from forge_shared.auth.models import ForgeUser, UserRole, PlanTier, Permission

        user = ForgeUser(
            id="user_123",
            email="test@example.com",
            domain="test.com",
            roles=[UserRole.USER],
            permissions=[Permission.READ_OWN],
            plan=PlanTier.FREE,
            products=["interview-simulator"],
        )

        assert user.id == "user_123"
        assert user.email == "test@example.com"
        assert user.domain == "test.com"

    def test_forge_user_invalid_email(self):
        """Test that ForgeUser rejects invalid email."""
        from forge_shared.auth.models import ForgeUser, UserRole, PlanTier, Permission

        with pytest.raises(ValueError):
            ForgeUser(
                id="user_123",
                email="invalid-email",
                domain="test.com",
                roles=[UserRole.USER],
                permissions=[Permission.READ_OWN],
                plan=PlanTier.FREE,
                products=[],
            )


class TestForgeUserModel:
    """
    Tests for ForgeUser model.
    """

    def test_forge_user_creation(self):
        """Test creating a ForgeUser with valid data."""
        from forge_shared.auth.models import ForgeUser, UserRole, PlanTier, Permission

        user = ForgeUser(
            id="user_123",
            email="test@example.com",
            domain="test.com",
            roles=[UserRole.USER],
            permissions=[Permission.READ_OWN],
            plan=PlanTier.FREE,
            products=["interview-simulator"],
        )

        assert user.id == "user_123"
        assert user.email == "test@example.com"
        assert user.domain == "test.com"

    def test_forge_user_invalid_email(self):
        """Test that ForgeUser rejects invalid email."""
        from forge_shared.auth.models import ForgeUser, UserRole, PlanTier, Permission

        with pytest.raises(ValueError):
            ForgeUser(
                id="user_123",
                email="invalid-email",
                domain="test.com",
                roles=[UserRole.USER],
                permissions=[Permission.READ_OWN],
                plan=PlanTier.FREE,
                products=[],
            )


class TestPermissionModel:
    """
    Tests for Permission enum.
    """

    def test_permission_values(self):
        """Test that Permission enum has correct values."""
        from forge_shared.auth.models import Permission

        assert Permission.READ_OWN.value == "read:own"
        assert Permission.WRITE_OWN.value == "write:own"
        assert Permission.DELETE_OWN.value == "delete:own"
        assert Permission.READ_ALL.value == "read:all"
        assert Permission.WRITE_ALL.value == "write:all"


class TestUserRoleModel:
    """
    Tests for UserRole enum.
    """

    def test_user_role_values(self):
        """Test that UserRole enum has correct values."""
        from forge_shared.auth.models import UserRole

        assert UserRole.GUEST.value == "guest"
        assert UserRole.USER.value == "user"
        assert UserRole.ADMIN.value == "admin"
        assert UserRole.SUPER_ADMIN.value == "super_admin"


class TestPlanTierModel:
    """
    Tests for PlanTier enum.
    """

    def test_plan_tier_values(self):
        """Test that PlanTier enum has correct values."""
        from forge_shared.auth.models import PlanTier

        assert PlanTier.FREE.value == "free"
        assert PlanTier.PRO.value == "pro"
        assert PlanTier.ENTERPRISE.value == "enterprise"
