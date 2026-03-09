"""Flywheel Progress Tracker - Track feature implementation progress."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..logging_config import get_logger

logger = get_logger(__name__)


class ProgressTracker:
    """Track and report on flywheel progress."""

    def __init__(self, forge_root: Path):
        self.forge_root = forge_root

    def get_project_stats(self, domain: str, project: str) -> dict[str, int]:
        """Get feature stats for a project."""
        features_path = self.forge_root / domain / project / "features.json"
        if not features_path.exists():
            return {"pending": 0, "completed": 0, "in_progress": 0, "blocked": 0, "total": 0}

        try:
            data = json.loads(features_path.read_text())
            features = data if isinstance(data, list) else data.get("features", [])

            stats = {
                "pending": 0,
                "completed": 0,
                "in_progress": 0,
                "blocked": 0,
                "total": len(features),
            }

            for f in features:
                if not isinstance(f, dict):
                    continue
                status = f.get("status", "pending")
                if status in stats:
                    stats[status] += 1
                else:
                    stats["pending"] += 1

            return stats
        except Exception as e:
            logger.warning(f"Failed to read features for {domain}/{project}: {e}")
            return {"pending": 0, "completed": 0, "in_progress": 0, "blocked": 0, "total": 0}

    def get_portfolio_stats(self) -> dict[str, Any]:
        """Get feature stats for the entire portfolio."""
        portfolio_stats = {
            "projects": 0,
            "pending": 0,
            "completed": 0,
            "in_progress": 0,
            "blocked": 0,
            "total_features": 0,
        }

        # Iterate through domains and projects
        for domain_path in self.forge_root.iterdir():
            if not domain_path.is_dir() or domain_path.name.startswith((".", "_")):
                continue
            if domain_path.name in ["docs", "scripts", "templates", "node_modules", ".git"]:
                continue

            for project_path in domain_path.iterdir():
                if not project_path.is_dir() or project_path.name.startswith("."):
                    continue

                stats = self.get_project_stats(domain_path.name, project_path.name)
                if stats["total"] > 0:
                    portfolio_stats["projects"] += 1
                    portfolio_stats["pending"] += stats["pending"]
                    portfolio_stats["completed"] += stats["completed"]
                    portfolio_stats["in_progress"] += stats["in_progress"]
                    portfolio_stats["blocked"] += stats["blocked"]
                    portfolio_stats["total_features"] += stats["total"]

        return portfolio_stats
