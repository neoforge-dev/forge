"""
Pipeline Data Management
========================

Handles reading and parsing pipeline checkpoint data from disk.
Provides structured access to orchestration and Ralph loop checkpoints.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineExecution:
    """Represents a single pipeline execution from checkpoint data.

    Attributes:
        id: Unique identifier (filename stem)
        name: Pipeline name
        type: Pipeline type (orchestration, ralph_loop)
        status: Current status (pending, running, success, failed, paused)
        current_step: Current step index (for orchestration)
        total_steps: Total number of steps
        started_at: ISO timestamp when started
        completed_at: ISO timestamp when completed (if applicable)
        updated_at: ISO timestamp of last update
        domain: Domain name (if available)
        project: Project name (if available)
        error: Error message (if failed)
        checkpoint_path: Path to checkpoint file
        metadata: Additional metadata from checkpoint
    """

    id: str
    name: str
    type: str
    status: str
    current_step: int | None
    total_steps: int
    started_at: str | None
    completed_at: str | None
    updated_at: str
    domain: str | None
    project: str | None
    error: str | None
    checkpoint_path: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "updated_at": self.updated_at,
            "domain": self.domain,
            "project": self.project,
            "error": self.error,
            "checkpoint_path": self.checkpoint_path,
            "metadata": self.metadata,
        }


class PipelineDataReader:
    """Reads and parses pipeline checkpoint data from disk.

    Scans checkpoint directories for orchestration and Ralph loop data,
    parses JSON files, and provides structured pipeline execution data.
    """

    def __init__(self, forge_root: Path):
        """Initialize reader.

        Args:
            forge_root: Root directory containing checkpoint folders
        """
        self.forge_root = forge_root
        self.orchestration_dir = forge_root / ".forge/orchestration_checkpoints"
        self.ralph_dir = forge_root / ".forge/ralph_checkpoints"

    def get_recent_pipelines(
        self,
        limit: int = 10,
        status: str | None = None,
        type_filter: str | None = None,
    ) -> list[PipelineExecution]:
        """Get recent pipeline executions.

        Args:
            limit: Maximum number of pipelines to return
            status: Filter by status (pending, running, success, failed, paused)
            type_filter: Filter by type (orchestration, ralph_loop)

        Returns:
            List of pipeline executions, sorted by most recent first
        """
        pipelines: list[PipelineExecution] = []

        # Read orchestration checkpoints
        if self.orchestration_dir.exists() and (
            type_filter is None or type_filter == "orchestration"
        ):
            pipelines.extend(self._read_orchestration_checkpoints())

        # Read Ralph loop checkpoints
        if self.ralph_dir.exists() and (type_filter is None or type_filter == "ralph_loop"):
            pipelines.extend(self._read_ralph_checkpoints())

        # Sort by updated_at (most recent first)
        pipelines.sort(key=lambda p: p.updated_at, reverse=True)

        # Filter by status if requested
        if status:
            pipelines = [p for p in pipelines if p.status == status]

        # Apply limit
        return pipelines[:limit]

    def get_pipeline_stats(self) -> dict[str, Any]:
        """Get statistics about pipeline executions.

        Returns:
            Dictionary with counts by status and type
        """
        pipelines = self.get_recent_pipelines(limit=1000)  # Get all recent

        stats = {
            "total": len(pipelines),
            "by_status": {
                "pending": 0,
                "running": 0,
                "success": 0,
                "failed": 0,
                "paused": 0,
            },
            "by_type": {
                "orchestration": 0,
                "ralph_loop": 0,
            },
        }

        for pipeline in pipelines:
            stats["by_status"][pipeline.status] = stats["by_status"].get(pipeline.status, 0) + 1
            stats["by_type"][pipeline.type] = stats["by_type"].get(pipeline.type, 0) + 1

        return stats

    def _read_orchestration_checkpoints(self) -> list[PipelineExecution]:
        """Read orchestration checkpoint files.

        Returns:
            List of pipeline executions from orchestration checkpoints
        """
        executions: list[PipelineExecution] = []

        try:
            checkpoint_files = sorted(
                self.orchestration_dir.glob("*.json"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )

            for checkpoint_file in checkpoint_files:
                try:
                    with open(checkpoint_file) as f:
                        data = json.load(f)

                    # Parse orchestration checkpoint format
                    execution = self._parse_orchestration_checkpoint(checkpoint_file, data)
                    if execution:
                        executions.append(execution)

                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON in checkpoint {checkpoint_file}: {e}")
                except Exception as e:
                    logger.warning(f"Failed to read checkpoint {checkpoint_file}: {e}")

        except Exception as e:
            logger.error(f"Failed to scan orchestration checkpoints: {e}")

        return executions

    def _read_ralph_checkpoints(self) -> list[PipelineExecution]:
        """Read Ralph loop checkpoint files.

        Returns:
            List of pipeline executions from Ralph checkpoints
        """
        executions: list[PipelineExecution] = []

        try:
            checkpoint_files = sorted(
                self.ralph_dir.glob("*.json"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )

            for checkpoint_file in checkpoint_files:
                try:
                    with open(checkpoint_file) as f:
                        data = json.load(f)

                    # Parse Ralph checkpoint format
                    execution = self._parse_ralph_checkpoint(checkpoint_file, data)
                    if execution:
                        executions.append(execution)

                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON in checkpoint {checkpoint_file}: {e}")
                except Exception as e:
                    logger.warning(f"Failed to read checkpoint {checkpoint_file}: {e}")

        except Exception as e:
            logger.error(f"Failed to scan Ralph checkpoints: {e}")

        return executions

    def _parse_orchestration_checkpoint(
        self,
        checkpoint_file: Path,
        data: dict[str, Any],
    ) -> PipelineExecution | None:
        """Parse orchestration checkpoint data.

        Args:
            checkpoint_file: Path to checkpoint file
            data: Parsed JSON data

        Returns:
            PipelineExecution or None if parsing failed
        """
        try:
            # Determine status from checkpoint data
            status = "pending"
            completed_steps = data.get("completed_steps", [])
            current_step_index = data.get("current_step_index", 0)
            total_steps = len(data.get("pipeline_steps", []))

            # Check if there's an error or completion indicator
            # Orchestration checkpoints may have different completion indicators
            if len(completed_steps) == total_steps and current_step_index >= total_steps:
                status = "success"
            elif current_step_index > 0:
                status = "running"

            # Get timestamps
            created_at = data.get("created_at")
            file_mtime = datetime.fromtimestamp(checkpoint_file.stat().st_mtime).isoformat()

            # Extract context information
            context = data.get("context", {})
            domain = context.get("domain")
            project = context.get("project")

            return PipelineExecution(
                id=checkpoint_file.stem,
                name=data.get("pipeline_name", checkpoint_file.stem),
                type="orchestration",
                status=status,
                current_step=current_step_index,
                total_steps=total_steps,
                started_at=created_at,
                completed_at=None,  # Orchestration checkpoints don't track completion separately
                updated_at=file_mtime,
                domain=domain,
                project=project,
                error=None,
                checkpoint_path=str(checkpoint_file),
                metadata={
                    "completed_steps": completed_steps,
                    "step_outputs": data.get("step_outputs", {}),
                },
            )

        except Exception as e:
            logger.warning(f"Failed to parse orchestration checkpoint {checkpoint_file}: {e}")
            return None

    def _parse_ralph_checkpoint(
        self,
        checkpoint_file: Path,
        data: dict[str, Any],
    ) -> PipelineExecution | None:
        """Parse Ralph loop checkpoint data.

        Args:
            checkpoint_file: Path to checkpoint file
            data: Parsed JSON data

        Returns:
            PipelineExecution or None if parsing failed
        """
        try:
            # Ralph checkpoints track iterations and feature stats
            iteration = data.get("iteration", 0)
            timestamp = data.get("timestamp")
            stats = data.get("stats", {})

            # Determine status from feature stats
            status = "running"
            passing = stats.get("passing", 0)
            failing = stats.get("failing", 0)
            blocked = stats.get("blocked", 0)
            pending = stats.get("pending", 0)
            in_progress = stats.get("in_progress", 0)

            passing + failing + blocked + pending + in_progress

            if failing > 0 or blocked > 0:
                status = "failed"
            elif pending == 0 and in_progress == 0:
                status = "success"
            elif iteration > 0:
                status = "running"
            else:
                status = "pending"

            # Get file modification time for updated_at
            file_mtime = datetime.fromtimestamp(checkpoint_file.stat().st_mtime).isoformat()

            # Extract features path to get domain/project if available
            features_path = data.get("features_path", "")
            domain = None
            project = None
            if features_path:
                # Try to extract domain/project from path
                parts = Path(features_path).parts
                if len(parts) >= 2:
                    domain = parts[-2] if "-" in parts[-2] else None

            return PipelineExecution(
                id=checkpoint_file.stem,
                name=f"Ralph Loop (iteration {iteration})",
                type="ralph_loop",
                status=status,
                current_step=iteration,
                total_steps=data.get("config", {}).get("max_iterations", 100),
                started_at=timestamp,
                completed_at=None,
                updated_at=file_mtime,
                domain=domain,
                project=project,
                error=None,
                checkpoint_path=str(checkpoint_file),
                metadata={
                    "stats": stats,
                    "features_path": features_path,
                    "config": data.get("config", {}),
                },
            )

        except Exception as e:
            logger.warning(f"Failed to parse Ralph checkpoint {checkpoint_file}: {e}")
            return None


def create_pipeline_reader(forge_root: Path | None = None) -> PipelineDataReader:
    """Create pipeline data reader.

    Args:
        forge_root: Root directory containing checkpoint folders.
                   Defaults to current working directory.

    Returns:
        PipelineDataReader instance
    """
    if forge_root is None:
        forge_root = Path.cwd()
    return PipelineDataReader(forge_root)
