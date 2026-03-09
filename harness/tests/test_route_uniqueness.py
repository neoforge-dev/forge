"""CP-1003: Route uniqueness regression test.

Prevents accidental duplicate METHOD+PATH registrations in the FastAPI app.
Intentional legacy aliases (e.g., /api/legacy/*, /legacy/*) are explicitly
enumerated in LEGACY_ROUTE_ALLOWLIST so that:

  1. Duplicates within the allowlist are permitted (they alias canonical routes).
  2. Non-allowlisted routes must have globally unique METHOD+PATH pairs.
  3. Stale allowlist entries — paths that no longer exist — are detected.
  4. Total route count stays within a calibrated regression window.

See docs/reports/CP-1001_DUPLICATE_ROUTE_INVENTORY.md for the initial audit
and CP-1002 for the fixes that brought the app to its current clean state.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest

# ---------------------------------------------------------------------------
# Explicit allowlist of intentional legacy aliases
# ---------------------------------------------------------------------------
# Each entry is a (METHOD, PATH) pair that exists as a backward-compatibility
# alias for a canonical endpoint.  These routes are the only ones permitted to
# produce a duplicate METHOD+PATH pair with their canonical counterparts.
#
# Rules:
#   - Only add entries when you are intentionally registering a legacy alias.
#   - Never add entries here to silence a bug — fix the bug instead.
#   - When you remove a legacy route, remove it from this list too (the stale
#     allowlist test will enforce this).
#
# Current inventory (CP-1001 audit, 2026-02-20): 23 intentional aliases.
# All carry /api/legacy/* or /legacy/* prefix per the deprecation convention.

LEGACY_ROUTE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    [
        # Health aliases ---------------------------------------------------
        ("GET", "/legacy/health"),
        ("GET", "/legacy/health/full"),
        ("GET", "/legacy/health/metrics"),
        ("GET", "/legacy/health/{service_name}"),
        ("GET", "/api/legacy/health"),
        ("GET", "/api/legacy/metrics"),
        # Auth aliases -----------------------------------------------------
        ("GET", "/api/legacy/auth/status"),
        ("POST", "/api/legacy/auth/refresh"),
        # Config aliases ---------------------------------------------------
        ("GET", "/api/legacy/config/llm"),
        ("POST", "/api/legacy/config/llm"),
        # Agent lifecycle aliases ------------------------------------------
        ("POST", "/api/legacy/agents/register"),
        ("GET", "/api/legacy/agents"),
        ("GET", "/api/legacy/agents/{agent_id}"),
        ("POST", "/api/legacy/agents/{agent_id}/progress"),
        ("POST", "/api/legacy/agents/{agent_id}/heartbeat"),
        ("POST", "/api/legacy/agents/{agent_id}/complete"),
        ("POST", "/api/legacy/agents/{agent_id}/pause"),
        ("POST", "/api/legacy/agents/{agent_id}/resume"),
        ("POST", "/api/legacy/agents/{agent_id}/kill"),
        ("POST", "/api/legacy/agents/{agent_id}/message"),
        ("GET", "/api/legacy/agents/{agent_id}/context/export"),
        ("POST", "/api/legacy/agents/broadcast"),
        # Fleet aliases ----------------------------------------------------
        ("GET", "/api/legacy/agents/fleet/status"),
    ]
)

# ---------------------------------------------------------------------------
# Route count regression window
# ---------------------------------------------------------------------------
# Calibrated from the live app on 2026-02-20 (CP-1001 audit).
# Semantic routes = all METHOD+PATH pairs excluding HEAD and OPTIONS.
# Change these bounds only when you intentionally add or remove routes.

ROUTE_COUNT_MIN = 160  # Hard floor: too few routes means something is broken
ROUTE_COUNT_MAX = 220  # Soft ceiling: unexpected growth triggers investigation


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _collect_semantic_routes() -> list[tuple[str, str]]:
    """Instantiate create_app() and return all semantic (METHOD, PATH) pairs.

    "Semantic" means we exclude:
      - Internal FastAPI infrastructure routes (/docs, /redoc, /openapi.json)
      - HEAD and OPTIONS, which FastAPI generates automatically for every GET
        endpoint and are never user-defined duplicates.

    Returns a flat list — intentional duplicates appear multiple times.
    """
    from forge_harness.webhook_server_main import create_app

    app = create_app()

    routes: list[tuple[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is None or methods is None:
            continue
        if path in ("/docs", "/redoc", "/openapi.json"):
            continue
        for method in sorted(methods):
            if method in ("HEAD", "OPTIONS"):
                continue
            routes.append((method, path))

    return routes


def _normalize_path_params(path: str) -> str:
    """Replace all {param_name} segments with the token {param}.

    This lets us detect structurally equivalent routes that would shadow each
    other at request dispatch time regardless of parameter name differences.

    Example:
        /agents/{agent_id}/logs  ->  /agents/{param}/logs
        /agents/{id}/logs        ->  /agents/{param}/logs  (collision!)
    """
    return re.sub(r"\{[^}]+\}", "{param}", path)


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def all_routes() -> list[tuple[str, str]]:
    """All semantic (METHOD, PATH) pairs from the live app — module-scoped."""
    return _collect_semantic_routes()


@pytest.fixture(scope="module")
def allowlisted_paths(all_routes: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """Intersection of LEGACY_ROUTE_ALLOWLIST with routes actually in the app."""
    registered = set(all_routes)
    return LEGACY_ROUTE_ALLOWLIST & registered


# ---------------------------------------------------------------------------
# Test class: duplicate and collision detection
# ---------------------------------------------------------------------------


class TestRouteUniqueness:
    """No METHOD+PATH pair may appear more than once, except for explicit legacy aliases."""

    def test_app_registers_routes(self, all_routes: list[tuple[str, str]]) -> None:
        """Sanity check: app successfully registers a meaningful number of routes.

        A failure here usually means create_app() raised an exception that was
        silently swallowed, or all routers failed to import.
        """
        assert len(all_routes) >= ROUTE_COUNT_MIN, (
            f"App only registered {len(all_routes)} routes (minimum {ROUTE_COUNT_MIN}). "
            "A router probably failed to load."
        )

    def test_no_exact_duplicate_routes(self, all_routes: list[tuple[str, str]]) -> None:
        """No METHOD+PATH pair may appear more than once outside the allowlist.

        Core regression guard from CP-1002.  Legacy aliases in
        LEGACY_ROUTE_ALLOWLIST are excluded from this check because they are
        intentional duplicates of their canonical counterparts.
        """
        # Filter out allowlisted entries before counting
        non_allowlisted = [pair for pair in all_routes if pair not in LEGACY_ROUTE_ALLOWLIST]
        counts = Counter(non_allowlisted)
        duplicates = {pair: count for pair, count in counts.items() if count > 1}

        if duplicates:
            lines = [
                f"  {method} {path}  (registered {count}x)"
                for (method, path), count in sorted(duplicates.items())
            ]
            pytest.fail(
                "Duplicate route registrations found (not in allowlist):\n" + "\n".join(lines)
            )

    def test_no_normalized_path_collisions(self, all_routes: list[tuple[str, str]]) -> None:
        """No two canonical routes may shadow each other after path-param normalization.

        Catches cases like:
            GET /agents/{agent_id}/logs  and  GET /agents/{id}/logs

        Both would normalize to GET /agents/{param}/logs and produce a runtime
        shadowing bug where the first-registered handler wins silently.

        Legacy aliases are excluded because they are intentionally aliasing
        paths that differ in parameter names.
        """
        canonical: list[tuple[str, str]] = []
        for method, path in all_routes:
            if (method, path) in LEGACY_ROUTE_ALLOWLIST:
                continue
            canonical.append((method, _normalize_path_params(path)))

        counts = Counter(canonical)
        collisions = {pair: count for pair, count in counts.items() if count > 1}

        if collisions:
            lines = [
                f"  {method} {path}  (normalized collision, {count} routes)"
                for (method, path), count in sorted(collisions.items())
            ]
            pytest.fail(
                "Normalized path collisions found (routes that shadow each other):\n"
                + "\n".join(lines)
            )

    def test_content_stats_ordered_before_parameterized(
        self, all_routes: list[tuple[str, str]]
    ) -> None:
        """Regression guard for S-1: /api/content/stats must precede the wildcard.

        FastAPI evaluates routes in registration order.  If /api/content/stats
        is registered *after* /api/content/{domain}/{project}, the stats
        endpoint becomes unreachable (shadowed by the parameterized route).

        Fix: moved get_content_stats above the parameterized route in
        forge_harness/webhook_server/api/content.py.
        """
        get_routes = [path for method, path in all_routes if method == "GET"]
        stats_path = "/api/content/stats"
        param_path = "/api/content/{domain}/{project}"

        assert stats_path in get_routes, f"Expected GET {stats_path} to be registered"
        assert param_path in get_routes, f"Expected GET {param_path} to be registered"

        stats_idx = get_routes.index(stats_path)
        param_idx = get_routes.index(param_path)
        assert stats_idx < param_idx, (
            f"S-1 regression: GET {stats_path} (index {stats_idx}) must appear "
            f"before GET {param_path} (index {param_idx})"
        )

    def test_agent_message_registered_exactly_once(self, all_routes: list[tuple[str, str]]) -> None:
        """Regression guard for D-1: agents/{agent_id}/message must be owned by agents_router.

        The D-1 duplicate existed because both api/agents.py and api/messages.py
        registered POST /api/agents/{agent_id}/message.  The messages.py copy was
        removed; this test prevents it from coming back.
        """
        target = ("POST", "/api/agents/{agent_id}/message")
        count = all_routes.count(target)
        assert count == 1, (
            f"D-1 regression: POST /api/agents/{{agent_id}}/message registered "
            f"{count} times (expected exactly 1, via agents_router)"
        )


# ---------------------------------------------------------------------------
# Test class: stale allowlist detection
# ---------------------------------------------------------------------------


class TestAllowlistValidity:
    """Every entry in LEGACY_ROUTE_ALLOWLIST must exist in the running app.

    When a legacy alias is removed from the server, its allowlist entry must
    also be removed.  A stale entry is dead weight that confuses readers and
    masks real bugs.
    """

    def test_no_stale_allowlist_entries(self, all_routes: list[tuple[str, str]]) -> None:
        """Every allowlisted (METHOD, PATH) must be registered by the app.

        If this test fails, a legacy alias was removed from the server without
        updating LEGACY_ROUTE_ALLOWLIST.  Remove the stale entry from the
        allowlist to fix this test.
        """
        registered = set(all_routes)
        stale = LEGACY_ROUTE_ALLOWLIST - registered

        if stale:
            lines = [f"  {method} {path}" for method, path in sorted(stale)]
            pytest.fail(
                "Stale allowlist entries (routes no longer registered by the app):\n"
                + "\n".join(lines)
                + "\n\nRemove these entries from LEGACY_ROUTE_ALLOWLIST."
            )

    def test_allowlist_uses_legacy_prefix(self) -> None:
        """Every allowlisted route must use /api/legacy/* or /legacy/* prefix.

        This enforces the deprecation convention: legacy aliases MUST carry the
        legacy prefix so they are identifiable at a glance.  If you need to
        allowlist a non-legacy alias, that is a design smell — reconsider.
        """
        legacy_prefixes = ("/legacy/", "/api/legacy/")
        non_legacy = [
            (method, path)
            for method, path in sorted(LEGACY_ROUTE_ALLOWLIST)
            if not any(path.startswith(p) for p in legacy_prefixes)
        ]
        if non_legacy:
            lines = [f"  {method} {path}" for method, path in non_legacy]
            pytest.fail(
                "Allowlist entries missing legacy prefix (/legacy/* or /api/legacy/*):\n"
                + "\n".join(lines)
            )

    def test_allowlist_has_no_internal_duplicates(self) -> None:
        """LEGACY_ROUTE_ALLOWLIST itself must not contain duplicate entries.

        The allowlist is a frozenset so Python deduplicates automatically, but
        this test documents the expectation explicitly and will catch typos if
        the data structure type is ever changed.
        """
        entries = list(LEGACY_ROUTE_ALLOWLIST)
        counts = Counter(entries)
        dupes = {pair: count for pair, count in counts.items() if count > 1}
        assert not dupes, f"Duplicate entries in LEGACY_ROUTE_ALLOWLIST: {sorted(dupes)}"


# ---------------------------------------------------------------------------
# Test class: route count regression guard
# ---------------------------------------------------------------------------


class TestRouteCountRegression:
    """Total route count must stay within the calibrated window.

    These tests catch two classes of accidental change:
      - Floor breach: a router stopped loading (import error, conditional skip).
      - Ceiling breach: an unreviewed bulk addition of routes.

    Update ROUTE_COUNT_MIN / ROUTE_COUNT_MAX when you intentionally add or
    remove routes.  The window is deliberately wide enough to absorb normal
    growth but tight enough to flag a significant unplanned change.
    """

    def test_total_route_count_within_range(self, all_routes: list[tuple[str, str]]) -> None:
        """Semantic route count must be in [ROUTE_COUNT_MIN, ROUTE_COUNT_MAX].

        Current baseline (2026-02-20): 183 routes.
        Window calibrated at [160, 220] — update both constants when intentionally
        adding or removing a significant number of routes.
        """
        count = len(all_routes)
        assert ROUTE_COUNT_MIN <= count <= ROUTE_COUNT_MAX, (
            f"Route count {count} is outside the expected window "
            f"[{ROUTE_COUNT_MIN}, {ROUTE_COUNT_MAX}]. "
            "If you added/removed routes intentionally, update ROUTE_COUNT_MIN/MAX."
        )

    def test_legacy_alias_count_within_bounds(self, all_routes: list[tuple[str, str]]) -> None:
        """Legacy alias count must not silently grow beyond the inventory baseline.

        The CP-1001 audit found 23 intentional legacy aliases.  This test
        asserts the count stays at or below that ceiling — if it grows, a new
        alias was added without deliberate review.  If it shrinks, that is
        progress toward CP-4001 (legacy deprecation sprint).
        """
        legacy_prefixes = ("/legacy/", "/api/legacy/")
        legacy_count = sum(
            1 for _, path in all_routes if any(path.startswith(p) for p in legacy_prefixes)
        )
        # Upper bound: matches the size of LEGACY_ROUTE_ALLOWLIST exactly.
        # If this fails because count > allowlist size, a new legacy alias was
        # added without being allowlisted — add it to both places.
        allowlist_size = len(LEGACY_ROUTE_ALLOWLIST)
        assert legacy_count <= allowlist_size, (
            f"Legacy alias count ({legacy_count}) exceeds allowlist size "
            f"({allowlist_size}).  Add the new alias to LEGACY_ROUTE_ALLOWLIST "
            "or move it to a canonical router."
        )

    def test_non_legacy_route_count_floor(self, all_routes: list[tuple[str, str]]) -> None:
        """Canonical (non-legacy) routes must not drop below a safe floor.

        Protects against a scenario where many routers silently fail to register
        without causing an import error.
        """
        legacy_prefixes = ("/legacy/", "/api/legacy/")
        non_legacy_count = sum(
            1 for _, path in all_routes if not any(path.startswith(p) for p in legacy_prefixes)
        )
        # Current baseline: 160 canonical routes.  Floor is 140 to absorb
        # deliberate removals without triggering a false alarm.
        floor = 140
        assert non_legacy_count >= floor, (
            f"Only {non_legacy_count} canonical (non-legacy) routes registered "
            f"(floor: {floor}).  A router may have failed to load."
        )
