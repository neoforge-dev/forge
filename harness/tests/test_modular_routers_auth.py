"""R5a: Modular routers must not use Depends(lambda: None) on control endpoints.

Phase 1.4 Router Auth: Control endpoints (tasks, agents, patterns, handoffs,
decisions, approvals, ralph, memories) require real auth, not placeholder.

TDD: This test fails until opencode wires verify_auth/security into these routers.
"""

from pathlib import Path

import pytest

HARNESS_ROOT = Path(__file__).resolve().parent.parent
API_DIR = HARNESS_ROOT / "forge_harness" / "webhook_server" / "api"

# Control modules that must use real auth (Phase 1.4)
CONTROL_MODULES = [
    "agents",
    "tasks",
    "patterns",
    "handoffs",
    "decisions",
    "approvals",
    "ralph",
    "memories",
]


def _has_placeholder_auth(content: str) -> bool:
    """Check if file uses Depends(lambda: None) placeholder."""
    # Matches: Depends(lambda: None), Depends(lambda: None ), _=Depends(lambda: None)
    return "Depends(lambda: None)" in content or "Depends(lambda: None )" in content


def _count_placeholder_occurrences(content: str) -> int:
    """Count occurrences of Depends(lambda: None) in content."""
    return content.count("Depends(lambda: None)")


class TestModularRoutersAuth:
    """Assert no Depends(lambda: None) on control endpoint routers."""

    @pytest.mark.parametrize("module", CONTROL_MODULES)
    def test_control_module_has_no_placeholder_auth(self, module: str):
        """Control module must not use Depends(lambda: None) — wire real auth instead."""
        path = API_DIR / f"{module}.py"
        if not path.exists():
            pytest.skip(f"Module {module}.py not found")
        content = path.read_text()
        count = _count_placeholder_occurrences(content)
        assert count == 0, (
            f"{module}.py has {count} Depends(lambda: None) placeholder(s). "
            "Phase 1.4: Wire verify_auth or security into control endpoints."
        )

    def test_control_modules_summary(self):
        """Report which control modules still have placeholder auth (for debugging)."""
        violations = []
        for module in CONTROL_MODULES:
            path = API_DIR / f"{module}.py"
            if path.exists():
                content = path.read_text()
                if _has_placeholder_auth(content):
                    violations.append(f"{module}.py")
        # This test fails if any violations; the parametrized test above gives per-module detail
        assert len(violations) == 0, (
            f"Control modules with Depends(lambda: None): {violations}"
        )
