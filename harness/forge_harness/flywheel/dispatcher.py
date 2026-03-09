"""Flywheel Dispatcher - Dispatch work to agents and run loops."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..logging_config import get_logger
from .config import FlywheelConfig, FlywheelResult

logger = get_logger(__name__)


class FlywheelDispatcher:
    """Dispatches features to Ralph loops for implementation."""

    def __init__(self, forge_root: Path, config: FlywheelConfig | None = None):
        self.forge_root = forge_root
        self.config = config or FlywheelConfig()

    async def run_project_loop(
        self,
        domain: str,
        project: str,
        orchestrator: Any | None = None,
    ) -> FlywheelResult:
        """Run the flywheel loop for a project."""
        return await run_flywheel(
            forge_root=self.forge_root,
            domain=domain,
            project=project,
            config=self.config,
            orchestrator=orchestrator,
        )


def create_flywheel_loop(
    domain: str,
    project: str,
    features_path: Path | str | None = None,
    config: FlywheelConfig | None = None,
    orchestrator: Any | None = None,
) -> Any:
    """Create a fully-wired Ralph loop with all meta-learning enabled."""
    from ..harness_registry import create_harness_registry
    from ..ralph_loop import create_ralph_loop_from_registry

    config = config or FlywheelConfig()

    # Create registry
    registry = create_harness_registry(domain=domain, project=project)

    # Features path
    if features_path is None:
        forge_root = Path(os.getenv("FORGE_ROOT", Path.cwd()))
        project_path = forge_root / domain / project
        if project_path.exists():
            features_path = project_path / "features.json"
        else:
            features_path = Path("features.json")

    features_path = Path(features_path)

    # Create loop
    loop = create_ralph_loop_from_registry(
        features_path=features_path,
        registry=registry,
        max_iterations=config.max_iterations,
        dry_run=config.dry_run,
        orchestrator=orchestrator,
        domain=domain,
        project=project,
        test_command=config.test_command,
        working_dir=config.working_dir,
    )

    return loop


async def run_flywheel(
    forge_root: Path,
    domain: str,
    project: str,
    config: FlywheelConfig | None = None,
    orchestrator: Any | None = None,
    create_orchestrator: bool = True,
) -> FlywheelResult:
    """Run the complete autonomous development flywheel."""
    # Import from package so tests can patch forge_harness.flywheel.scan_project_for_debt
    from . import scan_project_for_debt

    config = config or FlywheelConfig()

    # Special case: Command Center lives under harness/, not portfolio/harness.
    # For domain="harness" and project="command_center", default the working_dir
    # to <forge_root>/harness so tests and callers do not need to set it.
    if config.working_dir is None and domain == "harness" and project == "command_center":
        config.working_dir = (forge_root / "harness").resolve()
    result = FlywheelResult(started_at=datetime.now(UTC))

    # Orchestrator
    if orchestrator is None and create_orchestrator and not config.dry_run:
        try:
            from ..agent import FeatureOrchestrator
            project_path = forge_root / domain / project
            working_dir = project_path.resolve() if project_path.exists() else forge_root.resolve()
            orchestrator = FeatureOrchestrator(
                working_dir=working_dir,
                model="claude-sonnet-4-20250514",
                max_iterations=50,
            )
        except Exception as e:
            logger.warning(f"Failed to create orchestrator: {e}")

    try:
        # Phase 1: Scan
        project_path = forge_root / domain / project
        features_path = project_path / "features.json"

        # Load existing features
        existing_features = []
        features_wrapper = None  # Track if using wrapper format
        if features_path.exists():
            try:
                data = json.loads(features_path.read_text())
                # Handle both formats: plain list or wrapper object with "features" key
                if isinstance(data, list):
                    existing_features = data
                elif isinstance(data, dict):
                    if "features" in data:
                        existing_features = data.get("features", [])
                        features_wrapper = data  # Preserve wrapper metadata
                    else:
                        # Dict without features key - treat as empty
                        logger.warning(
                            f"features.json has dict format without 'features' key: {features_path}"
                        )
                        existing_features = []
            except json.JSONDecodeError:
                pass

        # Ensure existing_features is a list of dicts
        if not isinstance(existing_features, list):
            existing_features = []

        existing_ids = {f.get("id") for f in existing_features if isinstance(f, dict)}

        # New features
        new_features = await scan_project_for_debt(
            domain=domain,
            project=project,
            project_path=project_path,
            max_features=config.max_features_per_project,
            priority_threshold=config.priority_threshold,
        )

        added = 0
        for f in new_features:
            if f["id"] not in existing_ids:
                existing_features.append(f)
                existing_ids.add(f["id"])
                added += 1

        result.features_generated = added
        result.projects_scanned = 1

        # Write updated features
        if not config.dry_run and added > 0:
            # Preserve wrapper format if it was used
            if features_wrapper is not None:
                features_wrapper["features"] = existing_features
                output_data = features_wrapper
            else:
                output_data = existing_features
            features_path.write_text(json.dumps(output_data, indent=2, default=str))
            logger.info(f"Added {added} new features to {features_path}")

        # Phase 2: Run Ralph Loop
        loop = create_flywheel_loop(
            domain=domain,
            project=project,
            features_path=features_path,
            config=config,
            orchestrator=orchestrator,
        )

        loop_result = await loop.run()
        result.features_implemented = loop_result.features_completed
        result.features_blocked = loop_result.features_blocked
        result.sessions_indexed = 1

        # Phase 3: Validation
        from .validator import FeatureValidator
        validator = FeatureValidator(forge_root=forge_root)
        val_result = await validator.validate_project(
            domain=domain,
            project=project,
            config=config,
        )
        logger.info(
            f"Validation complete for {domain}/{project}: "
            f"{val_result['completed']} completed, {val_result['failed']} failed"
        )

    except Exception as e:
        result.errors.append(str(e))
        logger.error(f"Flywheel error: {e}")

    result.ended_at = datetime.now(UTC)
    return result
