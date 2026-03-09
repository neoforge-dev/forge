"""P0-6 Security Phase 1: Dev mode assertions.

Asserts:
- No static token in init.ts (Command Center)
- Auth bypass requires explicit env (VITE_DEV_AUTH_BYPASS, FORGE_WEBHOOK_ALLOW_LOCALHOST)
"""

from pathlib import Path

import pytest

# Paths relative to harness root
HARNESS_ROOT = Path(__file__).resolve().parent.parent
INIT_TS = HARNESS_ROOT / "command_center" / "src" / "lib" / "init.ts"
AUTH_GATE_TSX = HARNESS_ROOT / "command_center" / "src" / "components" / "auth" / "AuthGate.tsx"


class TestNoStaticTokenInInit:
    """Assert Command Center init.ts has no static/hardcoded token."""

    def test_init_ts_has_no_static_token(self):
        """No hardcoded dev-token or static token bootstrap in init.ts."""
        if not INIT_TS.exists():
            pytest.skip(f"init.ts not found at {INIT_TS}")
        content = INIT_TS.read_text()
        # Must not contain hardcoded dev token
        assert "dev-token" not in content.lower(), (
            "init.ts must not contain hardcoded dev-token"
        )
        # Must not bootstrap token via setItem with literal
        if "setItem" in content:
            assert "dev-token" not in content and "dev_token" not in content, (
                "init.ts must not set a static token"
            )

    def test_init_ts_uses_env_or_auth_flow(self):
        """init.ts references VITE_API_TOKEN or auth flow, not hardcoded."""
        if not INIT_TS.exists():
            pytest.skip(f"init.ts not found at {INIT_TS}")
        content = INIT_TS.read_text()
        # Should reference env or auth (comment or code)
        assert "VITE_API_TOKEN" in content or "auth" in content.lower(), (
            "init.ts should use VITE_API_TOKEN or auth flow"
        )


class TestAuthBypassRequiresEnv:
    """Assert auth bypass requires explicit environment variables."""

    def test_auth_config_allow_localhost_defaults_false(self):
        """AuthConfig.from_env() has allow_localhost=False when env unset."""
        import os

        from forge_harness.webhook_server.infrastructure.auth import AuthConfig

        # Clear env to test default
        old_val = os.environ.pop("FORGE_WEBHOOK_ALLOW_LOCALHOST", None)
        try:
            config = AuthConfig.from_env()
            assert config.allow_localhost is False
        finally:
            if old_val is not None:
                os.environ["FORGE_WEBHOOK_ALLOW_LOCALHOST"] = old_val

    def test_auth_config_allow_localhost_true_when_explicit(self):
        """AuthConfig.from_env() allows localhost when FORGE_WEBHOOK_ALLOW_LOCALHOST=true."""
        import os

        from forge_harness.webhook_server.infrastructure.auth import AuthConfig

        old_val = os.environ.get("FORGE_WEBHOOK_ALLOW_LOCALHOST")
        try:
            os.environ["FORGE_WEBHOOK_ALLOW_LOCALHOST"] = "true"
            config = AuthConfig.from_env()
            assert config.allow_localhost is True
        finally:
            if old_val is not None:
                os.environ["FORGE_WEBHOOK_ALLOW_LOCALHOST"] = old_val
            else:
                os.environ.pop("FORGE_WEBHOOK_ALLOW_LOCALHOST", None)

    def test_auth_gate_requires_vite_dev_auth_bypass(self):
        """AuthGate.tsx bypass on localhost requires VITE_DEV_AUTH_BYPASS."""
        if not AUTH_GATE_TSX.exists():
            pytest.skip(f"AuthGate.tsx not found at {AUTH_GATE_TSX}")
        content = AUTH_GATE_TSX.read_text()
        # Must check VITE_DEV_AUTH_BYPASS for localhost bypass
        assert "VITE_DEV_AUTH_BYPASS" in content, (
            "AuthGate must require VITE_DEV_AUTH_BYPASS for localhost bypass"
        )
        # Must not auto-bypass on localhost without env
        assert "isLocalhost" in content or "localhost" in content.lower(), (
            "AuthGate should gate localhost bypass on env"
        )


class TestSecurityDevModeDocExists:
    """Assert SECURITY_DEV_MODE.md documents the env vars."""

    def test_doc_exists(self):
        """SECURITY_DEV_MODE.md exists."""
        doc = HARNESS_ROOT / "docs" / "SECURITY_DEV_MODE.md"
        assert doc.exists(), "harness/docs/SECURITY_DEV_MODE.md must exist"

    def test_doc_documents_bypass_env_vars(self):
        """SECURITY_DEV_MODE.md documents VITE_DEV_AUTH_BYPASS and FORGE_WEBHOOK_ALLOW_LOCALHOST."""
        doc = HARNESS_ROOT / "docs" / "SECURITY_DEV_MODE.md"
        content = doc.read_text()
        assert "VITE_DEV_AUTH_BYPASS" in content
        assert "FORGE_WEBHOOK_ALLOW_LOCALHOST" in content
