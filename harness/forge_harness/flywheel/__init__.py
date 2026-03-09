"""Flywheel - Compounding Autonomous Development Loops.

This package provides:
1. Debt scanning - Detect tech debt across portfolio
2. Feature generation - Convert debt to features.json entries
3. Auto-dispatch - Dispatch work to fleet agents
4. Progress tracking - Track implementation progress
5. Completion validation - Verify acceptance criteria

Usage:
    from forge_harness.flywheel import (
        FlywheelConfig,
        FlywheelResult,
        create_flywheel_loop,
        run_flywheel,
        scan_project_for_debt,
    )
"""

from __future__ import annotations

import asyncio

from .config import FlywheelConfig, FlywheelResult
from .dispatcher import FlywheelDispatcher, create_flywheel_loop, run_flywheel
from .generator import FeatureGenerator, quality_report_to_features
from .scanner import (
    DebtScanner,
    scan_portfolio_for_debt,
    scan_project_for_debt,
    scan_project_local,
)
from .scanner import (
    generate_portfolio_features as _scanner_generate_portfolio_features,
)
from .tracker import ProgressTracker
from .validator import FeatureValidator

create_fully_wired_loop = create_flywheel_loop


async def generate_portfolio_features(
    forge_root: Path,
    output_path: Path | None = None,
    priority_threshold: str = "medium",
    max_features_per_project: int = 10,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    include_harness: bool = True,
) -> list[dict[str, Any]]:
    """Wrapper around scanner.generate_portfolio_features that routes scanning
    through forge_harness.flywheel.scan_project_for_debt.

    Tests patch forge_harness.flywheel.scan_project_for_debt, so this function
    must call the patched symbol rather than the scanner module's local
    implementation.
    """

    return await _scanner_generate_portfolio_features(
        forge_root=forge_root,
        output_path=output_path,
        priority_threshold=priority_threshold,
        max_features_per_project=max_features_per_project,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        include_harness=include_harness,
        scan_fn=scan_project_for_debt,
    )


scan_portfolio = generate_portfolio_features


def run_flywheel_sync(*args, **kwargs) -> FlywheelResult:
    """Synchronous wrapper for run_flywheel."""
    return asyncio.run(run_flywheel(*args, **kwargs))


__all__ = [
    # Core classes
    "FlywheelConfig",
    "FlywheelResult",
    "DebtScanner",
    "FeatureGenerator",
    "FlywheelDispatcher",
    "ProgressTracker",
    "FeatureValidator",
    # Functions
    "create_flywheel_loop",
    "create_fully_wired_loop",
    "run_flywheel",
    "run_flywheel_sync",
    "scan_project_for_debt",
    "scan_project_local",
    "scan_portfolio_for_debt",
    "generate_portfolio_features",
    "scan_portfolio",
    "quality_report_to_features",
]
