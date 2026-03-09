"""Regression tests for targeted duplicate-route cleanup decisions."""

from collections import Counter

from forge_harness.webhook_server_main import create_app

# Intentional METHOD+path aliases can be placed here if needed in the future.
_METHOD_PATH_ALLOWLIST: set[tuple[str, str]] = set()


def _method_path_counts() -> Counter[tuple[str, str]]:
    """Return METHOD+path route counts for the runtime app."""
    app = create_app()
    counts: Counter[tuple[str, str]] = Counter()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            counts[(method, route.path)] += 1
    return counts


def test_targeted_collisions_removed() -> None:
    """Ensure CP-1002 slice endpoints are single-owned."""
    counts = _method_path_counts()

    assert counts[("GET", "/api/version")] == 1
    assert counts[("GET", "/api/nodes")] == 1
    assert counts[("GET", "/api/nodes/health")] == 1
    assert counts[("GET", "/api/nodes/recommend")] == 1
    assert counts[("GET", "/api/nodes/{node_id}")] == 1
    assert counts[("POST", "/api/nodes/{node_id}/dispatch")] == 1
    assert counts[("POST", "/api/nodes/sync")] == 1
    assert counts[("POST", "/api/webhooks/slack/approvals")] == 1


def test_no_unallowlisted_method_collisions() -> None:
    """Block accidental duplicate route registrations in create_app()."""
    counts = _method_path_counts()
    collisions = {
        method_path
        for method_path, count in counts.items()
        if count > 1 and method_path not in _METHOD_PATH_ALLOWLIST
    }
    assert collisions == set(), f"Unexpected method-path collisions: {sorted(collisions)}"
