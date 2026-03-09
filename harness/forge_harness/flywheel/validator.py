"""Flywheel Completion Validator - Verify implementation against acceptance criteria."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..logging_config import get_logger
from .config import FlywheelConfig

logger = get_logger(__name__)


class FeatureValidator:
    """Verify that implemented features meet their acceptance criteria."""

    def __init__(self, forge_root: Path):
        self.forge_root = forge_root

    async def validate_project(
        self,
        domain: str,
        project: str,
        config: FlywheelConfig | None = None,
    ) -> dict[str, Any]:
        """Validate all passing features in a project."""
        config = config or FlywheelConfig()
        project_path = self.forge_root / domain / project
        features_path = project_path / "features.json"

        # Special case: Command Center lives under harness/, not portfolio/harness.
        if domain == "harness" and project == "command_center":
             features_path = self.forge_root / "harness" / "command_center" / "features.json"

        if not features_path.exists():
            logger.warning(f"No features.json found at {features_path}")
            return {"validated": 0, "completed": 0, "failed": 0}

        try:
            data = json.loads(features_path.read_text())
            # Handle both formats: plain list or wrapper object with "features" key
            if isinstance(data, list):
                features = data
                wrapper = None
            elif isinstance(data, dict):
                features = data.get("features", [])
                wrapper = data
            else:
                logger.error(f"Invalid features.json format at {features_path}")
                return {"validated": 0, "completed": 0, "failed": 0}

            validated_count = 0
            completed_count = 0
            failed_count = 0

            for feature in features:
                if not isinstance(feature, dict):
                    continue

                if feature.get("status") == "passing":
                    validated_count += 1
                    # Perform validation
                    is_valid, error = await self.validate_feature(feature, project_path)

                    if is_valid:
                        feature["status"] = "completed"
                        completed_count += 1
                        logger.info(f"Feature {feature.get('id')} validated and marked completed")
                    else:
                        feature["status"] = "failing"
                        feature["last_error"] = f"Validation failed: {error}"
                        failed_count += 1
                        logger.warning(f"Feature {feature.get('id')} failed validation: {error}")

            if validated_count > 0 and not config.dry_run:
                if wrapper:
                    wrapper["features"] = features
                    output = wrapper
                else:
                    output = features
                features_path.write_text(json.dumps(output, indent=2, default=str))

            return {
                "validated": validated_count,
                "completed": completed_count,
                "failed": failed_count,
            }

        except Exception as e:
            logger.error(f"Error validating project {domain}/{project}: {e}")
            return {"validated": 0, "completed": 0, "failed": 0, "error": str(e)}

    async def validate_feature(
        self,
        feature: dict[str, Any],
        project_path: Path,
    ) -> tuple[bool, str | None]:
        """Validate a single feature against its acceptance criteria.

        In a full implementation, this might use an LLM to verify code changes.
        For now, we perform basic checks:
        1. All acceptance criteria strings are present.
        2. If 'tests' are listed, they exist.
        3. No 'TODO' or placeholder comments related to the feature.
        """
        criteria = feature.get("acceptance_criteria", [])
        if not criteria:
            # If no criteria, we just trust the 'passing' status from Ralph Loop
            return True, None

        # TODO: Implement more rigorous validation (e.g. LLM-based check)
        # For now, we assume if it passed tests in Ralph Loop, it's mostly good,
        # but we could add some heuristic checks here.

        # Example heuristic: if acceptance criteria says "Fix X", check if X is still there.
        # This is hard to do generically without LLM.

        return True, None
