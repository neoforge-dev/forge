"""CP-0003 / CP-0004 — CLI & API regression tests.

CP-0003: Verify that every expected top-level command is registered on the
         Click CLI group and that command groups expose their expected
         subcommands.  Also ensures that a raw Typer command injected
         *without* going through the Click adapter is detected and rejected.

CP-0004: Verify that every modular FastAPI router exported from
         ``forge_harness.webhook_server.api`` has at least one route, that
         all route paths start with ``/``, and that importing the modules
         raises no NameError.
"""

from __future__ import annotations

import click
import pytest

# ---------------------------------------------------------------------------
# CP-0003: CLI command registration
# ---------------------------------------------------------------------------

# Commands added via ``cli.add_command(...)`` in cli_v2/__init__.py.
# Format: name -> None (leaf command) | set[str] (expected subcommands).
EXPECTED_COMMANDS: dict[str, set[str] | None] = {
    "work": None,
    "status": None,
    "approve": None,
    "bootstrap": None,
    "claims": {"list", "stats", "submit", "verify"},
    "content": {"list", "stats"},
    "contract": {"check", "list"},
    "decisions": {"list", "show"},
    "dispatch": {"send", "relay"},
    "doctor": None,
    "evaluator": {"rerun", "results", "status", "summary"},
    "fleet": None,
    "orchestrator": {"checkpoint"},
    "git": {"guard", "mutex", "clear-locks"},
    "handoff": None,
    "health": None,
    "loop": None,
    "lead": {"send", "inbox", "ack", "acks", "handoff", "pending-acks"},
    "nodes": {"heartbeat", "list", "recommend"},
    "patterns": {"list", "show", "stats"},
    "plan": None,
    "quality": {"check", "fix", "report"},
    "recommend": {"contention", "explain", "log", "task"},
    "relay": {"start", "status", "stop"},
    "sessions": {"list", "show"},
    "ship": None,
    "submodules": {"update", "sync-parent"},
    "tasks": {
        "list",
        "create",
        "show",
        "claim",
        "dispatch",
        "recommended",
        "stats",
        "update",
        "complete",
        "delete",
        "cleanup",
        "import",
    },
    "xnode": {"relay", "listen", "list"},
    # ios is always registered (either real or stub)
    "ios": None,
    # Additional commands
    "auth": None,
    "balancer": None,
    "config": None,
    "daily-notes": None,
    "dashboard": None,
    "decompose": None,
    "df": None,
    "events": None,
    "features": None,
    "heartbeat": None,
    "intake": None,
    "memories": None,
    "portfolio": None,
    "slo": None,
    "version": None,
    "webhooks": None,
}


def _get_cli() -> click.Group:
    """Import the CLI group fresh for each test."""
    from forge_harness.cli_v2 import cli  # type: ignore[import]

    return cli  # type: ignore[return-value]


class TestCLICommandRegistration:
    """CP-0003: CLI command registration contract."""

    def test_all_expected_top_level_commands_registered(self) -> None:
        """Every expected top-level command name must appear in cli.commands."""
        cli = _get_cli()
        registered = set(cli.commands.keys())
        missing = set(EXPECTED_COMMANDS.keys()) - registered
        assert not missing, (
            f"Top-level commands missing from CLI: {sorted(missing)}\n"
            f"Registered commands: {sorted(registered)}"
        )

    def test_no_unexpected_top_level_commands(self) -> None:
        """No unrecognised top-level commands should appear (prevents silent injection)."""
        cli = _get_cli()
        registered = set(cli.commands.keys())
        unexpected = registered - set(EXPECTED_COMMANDS.keys())
        assert not unexpected, (
            f"Unexpected top-level commands found: {sorted(unexpected)}\n"
            "Update EXPECTED_COMMANDS if this addition is intentional."
        )

    @pytest.mark.parametrize(
        "group_name,expected_subs",
        [(name, subs) for name, subs in EXPECTED_COMMANDS.items() if subs is not None],
    )
    def test_group_has_expected_subcommands(self, group_name: str, expected_subs: set[str]) -> None:
        """Each command group exposes the subcommands it was designed to have."""
        cli = _get_cli()
        cmd = cli.commands.get(group_name)
        assert cmd is not None, f"Command '{group_name}' not found in CLI"
        assert isinstance(cmd, click.Group), (
            f"'{group_name}' should be a click.Group, got {type(cmd).__name__}"
        )
        registered_subs = set(cmd.commands.keys())
        missing_subs = expected_subs - registered_subs
        assert not missing_subs, (
            f"Group '{group_name}' is missing subcommands: {sorted(missing_subs)}\n"
            f"Registered subcommands: {sorted(registered_subs)}"
        )

    def test_raw_typer_command_not_injected_without_adapter(self) -> None:
        """A raw Typer app injected directly (not wrapped via get_command) must be
        detected.  The ios command is the only Typer-backed command and it MUST
        be wrapped as a Click BaseCommand before registration.

        This test fails if someone calls ``cli.add_command(typer_app, ...)``
        instead of ``cli.add_command(get_command(typer_app), ...)``.
        """
        import typer  # type: ignore[import]

        cli = _get_cli()
        for name, cmd in cli.commands.items():
            # A raw Typer TyperGroup / TyperCommand is NOT a click.BaseCommand
            # subclass *from Click's own registry* — it smuggles itself in via
            # duck-typing. Detect it by checking for the Typer-specific attribute
            # ``registered_groups`` which only exists on Typer internals.
            assert not isinstance(cmd, typer.Typer), (
                f"Command '{name}' is a raw typer.Typer instance. "
                "Wrap it with typer.main.get_command() before adding to the CLI."
            )

    def test_ios_command_is_click_command(self) -> None:
        """The ios command (real or stub) is always a proper click.Command or click.Group.

        A raw typer.Typer instance must not be passed directly; it must be
        wrapped via typer.main.get_command() so Click recognises it natively.
        """
        cli = _get_cli()
        ios_cmd = cli.commands.get("ios")
        assert ios_cmd is not None, "ios command not registered"
        # click.Group is a subclass of click.MultiCommand which is a subclass of
        # click.Command in Click >= 8.  Both are acceptable here — we only reject
        # raw Typer objects which have neither type in their MRO.
        assert isinstance(ios_cmd, (click.Command, click.Group)), (
            f"ios command must be a click.Command or click.Group, got {type(ios_cmd).__name__}. "
            "Ensure the Typer app is wrapped via typer.main.get_command()."
        )


# ---------------------------------------------------------------------------
# CP-0004: Frontend endpoint extraction — modular router contract
# ---------------------------------------------------------------------------

# All routers exported from forge_harness.webhook_server.api.__all__
EXPECTED_ROUTER_NAMES = [
    "agents_router",
    "approvals_router",
    "auth_router",
    "config_router",
    "content_router",
    "dashboard_router",
    "decisions_router",
    "handoffs_router",
    "health_router",
    "memories_router",
    "messages_router",
    "nodes_router",
    "patterns_router",
    "quality_router",
    "ralph_router",
    "sessions_router",
    "tasks_router",
    "version_router",
    "webhooks_router",
    "xnode_router",
]


def _import_api_package() -> object:
    """Import the api package; raise ImportError with context on failure."""
    try:
        import forge_harness.webhook_server.api as api_pkg  # type: ignore[import]
    except Exception as exc:
        pytest.fail(f"Failed to import forge_harness.webhook_server.api: {exc}")
    return api_pkg


class TestFrontendEndpointExtraction:
    """CP-0004: Modular router contract for frontend endpoint extraction."""

    def test_api_package_imports_without_errors(self) -> None:
        """Importing the api package must not raise NameError or any other error."""
        _import_api_package()

    def test_all_expected_routers_present_in_package(self) -> None:
        """Every router listed in __all__ must be accessible as a package attribute."""
        api_pkg = _import_api_package()
        missing = [name for name in EXPECTED_ROUTER_NAMES if not hasattr(api_pkg, name)]
        assert not missing, f"Routers missing from api package: {missing}"

    @pytest.mark.parametrize("router_name", EXPECTED_ROUTER_NAMES)
    def test_router_has_at_least_one_route(self, router_name: str) -> None:
        """Each router must define at least one route (non-empty routes list)."""
        api_pkg = _import_api_package()
        router = getattr(api_pkg, router_name)
        assert len(router.routes) > 0, (
            f"Router '{router_name}' has no routes defined. "
            "Add at least one @router.<method> handler."
        )

    @pytest.mark.parametrize("router_name", EXPECTED_ROUTER_NAMES)
    def test_all_route_paths_start_with_slash(self, router_name: str) -> None:
        """All route paths in a router must start with '/' (absolute paths)."""
        api_pkg = _import_api_package()
        router = getattr(api_pkg, router_name)
        bad_paths = [
            route.path
            for route in router.routes
            if hasattr(route, "path") and not route.path.startswith("/")
        ]
        assert not bad_paths, (
            f"Router '{router_name}' has routes with relative paths: {bad_paths}\n"
            "All route paths must start with '/'."
        )

    def test_no_router_raises_name_error_on_import(self) -> None:
        """Importing individual router modules must not raise NameError.

        This is a belt-and-suspenders check: it imports each module file
        directly so that any forward-reference or missing symbol is caught
        before the api package __init__ can mask it.
        """
        import importlib

        module_names = [
            "forge_harness.webhook_server.api.agents",
            "forge_harness.webhook_server.api.approvals",
            "forge_harness.webhook_server.api.auth",
            "forge_harness.webhook_server.api.config",
            "forge_harness.webhook_server.api.content",
            "forge_harness.webhook_server.api.dashboard",
            "forge_harness.webhook_server.api.decisions",
            "forge_harness.webhook_server.api.handoffs",
            "forge_harness.webhook_server.api.health",
            "forge_harness.webhook_server.api.memories",
            "forge_harness.webhook_server.api.messages",
            "forge_harness.webhook_server.api.nodes",
            "forge_harness.webhook_server.api.patterns",
            "forge_harness.webhook_server.api.quality",
            "forge_harness.webhook_server.api.ralph",
            "forge_harness.webhook_server.api.sessions",
            "forge_harness.webhook_server.api.tasks",
            "forge_harness.webhook_server.api.version",
            "forge_harness.webhook_server.api.webhooks",
            "forge_harness.webhook_server.api.xnode",
        ]
        failed: list[tuple[str, str]] = []
        for mod_name in module_names:
            try:
                importlib.import_module(mod_name)
            except NameError as exc:
                failed.append((mod_name, str(exc)))
            except Exception:
                # Non-NameError import errors (e.g. missing optional deps) are
                # acceptable here — CP-0004 only targets NameError.
                pass
        assert not failed, "NameError raised when importing router modules:\n" + "\n".join(
            f"  {mod}: {err}" for mod, err in failed
        )


# ---------------------------------------------------------------------------
# CP-1003: Route uniqueness (no accidental duplicate registrations)
# ---------------------------------------------------------------------------

# Routes that are intentionally duplicated (legacy aliases, etc.)
# Format: (method, path) tuples that are allowed to appear more than once.
ALLOWED_DUPLICATE_ROUTES: set[tuple[str, str]] = {
    # Intentional duplicates (legacy endpoints, alternative routes, etc.)
    ("POST", "/api/auth/validate"),
    ("GET", "/api/legacy/auth/status"),
    ("POST", "/api/auth/sse-session"),
    ("POST", "/api/legacy/auth/refresh"),
    ("GET", "/legacy/health/full"),
    ("GET", "/legacy/health/metrics"),
    ("GET", "/legacy/health/{service_name}"),
    ("GET", "/api/sync/status"),
    ("GET", "/api/state/snapshot"),
    ("POST", "/api/state/sync"),
    ("GET", "/api/state/stats"),
    ("POST", "/api/agents/fleet/pause"),
    ("POST", "/api/agents/fleet/resume"),
    ("POST", "/api/agents/{agent_id}/handoff"),
    ("POST", "/api/prime/register"),
    ("POST", "/api/prime/complete"),
    ("GET", "/api/prime/assignments"),
    ("POST", "/api/completions"),
    ("POST", "/api/orchestrator/events"),
    ("GET", "/api/legacy/config/llm"),
    ("POST", "/api/legacy/config/llm"),
    ("GET", "/api/version"),
}


class TestRouteUniqueness:
    """CP-1003 — Ensure no accidental duplicate route registrations."""

    @pytest.fixture(scope="class")
    def app_routes(self) -> list[tuple[str, str]]:
        """Collect all (method, path) pairs from the FastAPI app."""
        from forge_harness.webhook_server_main import create_app

        app = create_app()
        assert app is not None, "create_app() returned None — FastAPI not installed?"

        routes: list[tuple[str, str]] = []
        for route in app.routes:
            if hasattr(route, "methods") and hasattr(route, "path"):
                for method in route.methods:
                    routes.append((method.upper(), route.path))
        return routes

    def test_no_duplicate_routes(self, app_routes: list[tuple[str, str]]) -> None:
        """No (method, path) pair should be registered more than once."""
        from collections import Counter

        counts = Counter(app_routes)
        duplicates = {
            k: v for k, v in counts.items() if v > 1 and k not in ALLOWED_DUPLICATE_ROUTES
        }
        assert not duplicates, "Duplicate routes found (not in allowlist):\n" + "\n".join(
            f"  {m} {p} (×{c})" for (m, p), c in duplicates.items()
        )

    def test_all_routes_have_path_prefix(self, app_routes: list[tuple[str, str]]) -> None:
        """All route paths must start with /."""
        bad = [(m, p) for m, p in app_routes if not p.startswith("/")]
        assert not bad, "Routes with invalid path prefix:\n" + "\n".join(
            f"  {m} {p}" for m, p in bad
        )

    def test_minimum_route_count(self, app_routes: list[tuple[str, str]]) -> None:
        """Sanity check: app should have at least 50 routes (currently ~178)."""
        assert len(app_routes) >= 50, (
            f"Only {len(app_routes)} routes found — expected at least 50. "
            "Did a router fail to register?"
        )
