"""Flywheel - Compounding Autonomous Development Loops.

This module is a wrapper around the forge_harness.flywheel package for backwards compatibility.
"""

from __future__ import annotations

from .flywheel import (
    create_flywheel_loop,
    generate_portfolio_features,
    run_flywheel,
)

# Re-exports for convenience
create_fully_wired_loop = create_flywheel_loop
scan_portfolio = generate_portfolio_features
run_flywheel_sync = run_flywheel # This was a separate function in original, but maybe just use the async one or provide a sync wrapper
