"""Self-improvement module for FORGE Harness.

This module enables the harness to analyze and improve its own codebase,
creating a closed-loop self-improvement cycle.

The key insight: the harness can use the same tools (Tech Diligence, Code Atlas)
on its own code that it uses to analyze other projects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .meta_learning.bridges.tech_diligence import DiligenceReport, TechDiligenceBridge
from .meta_learning.feedback_loops import DebtFeature

logger = logging.getLogger(__name__)

# Default Tech Diligence URL (can be overridden by env or config)
DEFAULT_TECH_DILIGENCE_URL = os.getenv("TECH_DILIGENCE_URL", "http://localhost:8000")


@dataclass
class SelfAnalysisResult:
    """Result of self-analysis on the harness codebase."""

    timestamp: datetime
    report: DiligenceReport | None
    features_generated: list[DebtFeature]
    features_added: int
    features_skipped: int  # Already exist or duplicate
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "report_summary": {
                "total_issues": self.report.total_issues if self.report else 0,
                "critical": self.report.critical_count if self.report else 0,
                "high": self.report.high_count if self.report else 0,
                "medium": self.report.medium_count if self.report else 0,
            }
            if self.report
            else None,
            "features_generated": len(self.features_generated),
            "features_added": self.features_added,
            "features_skipped": self.features_skipped,
            "errors": self.errors,
        }


class HarnessSelfImprover:
    """Manages self-analysis and self-improvement of the harness codebase."""

    def __init__(
        self,
        harness_root: Path | None = None,
        features_path: Path | None = None,
        tech_diligence_url: str | None = None,
    ):
        """Initialize the self-improver.

        Args:
            harness_root: Path to harness root. Defaults to this package's parent.
            features_path: Path to features.json. Defaults to harness_root/features.json.
            tech_diligence_url: URL for Tech Diligence service. Uses DEFAULT_TECH_DILIGENCE_URL if None.
        """
        self.harness_root = harness_root or Path(__file__).parent.parent
        self.features_path = features_path or self.harness_root / "features.json"
        self.tech_diligence = TechDiligenceBridge(
            base_url=tech_diligence_url or DEFAULT_TECH_DILIGENCE_URL
        )
        self._analysis_history: list[SelfAnalysisResult] = []

    async def analyze(
        self,
        scanners: list[str] | None = None,
        max_features: int = 10,
        priority_threshold: str = "medium",
    ) -> SelfAnalysisResult:
        """Analyze the harness codebase for technical debt.

        Args:
            scanners: Specific scanners to run (None = all).
            max_features: Maximum features to generate.
            priority_threshold: Minimum priority to include (critical, high, medium, low).

        Returns:
            SelfAnalysisResult with analysis findings and generated features.
        """
        logger.info("Starting self-analysis of harness codebase at %s", self.harness_root)
        errors: list[str] = []
        features: list[DebtFeature] = []

        # Run Tech Diligence on harness codebase
        report = await self.tech_diligence.analyze_and_wait(
            repo_path=str(self.harness_root),
            scanners=scanners,
            max_wait_seconds=300,
        )

        if report is None:
            errors.append("Tech Diligence analysis failed or timed out")
            logger.warning("Tech Diligence analysis returned no report")
        else:
            logger.info(
                "Tech Diligence found %d issues (critical=%d, high=%d)",
                report.total_issues,
                report.critical_count,
                report.high_count,
            )
            # Convert findings to features
            features = self._convert_findings_to_features(report, max_features, priority_threshold)

        # Add features to backlog
        added, skipped = await self._add_features_to_backlog(features)

        result = SelfAnalysisResult(
            timestamp=datetime.now(UTC),
            report=report,
            features_generated=features,
            features_added=added,
            features_skipped=skipped,
            errors=errors,
        )
        self._analysis_history.append(result)

        logger.info(
            "Self-analysis complete: %d features generated, %d added, %d skipped",
            len(features),
            added,
            skipped,
        )
        return result

    def _convert_findings_to_features(
        self,
        report: DiligenceReport,
        max_features: int,
        priority_threshold: str,
    ) -> list[DebtFeature]:
        """Convert Tech Diligence findings to DebtFeature objects."""
        priority_order = ["critical", "high", "medium", "low"]
        threshold_idx = priority_order.index(priority_threshold)
        allowed_priorities = set(priority_order[: threshold_idx + 1])

        features: list[DebtFeature] = []

        for finding in report.findings:
            if finding.severity.lower() not in allowed_priorities:
                continue

            # Map severity to feature priority
            priority_map = {
                "critical": "critical",
                "high": "high",
                "medium": "medium",
                "low": "low",
            }

            # Build category from scanner or default
            category = getattr(finding, "scanner", None) or "self-analysis"

            # DebtFeature fields: id, name, description, priority, finding_id, category, estimated_effort
            feature = DebtFeature(
                id=f"self-debt-{finding.rule_id or 'unknown'}-{len(features)}",
                name=f"[Harness] {getattr(finding, 'title', None) or finding.message[:80]}",
                description=self._build_feature_description(finding),
                priority=priority_map.get(finding.severity.lower(), "medium"),
                finding_id=finding.rule_id or "unknown",
                category=category,
                estimated_effort=2.0,  # Default 2 hours for debt items
            )
            features.append(feature)

            if len(features) >= max_features:
                break

        return features

    def _build_feature_description(self, finding: Any) -> str:
        """Build a descriptive feature description from a finding."""
        parts = [finding.message]

        if finding.file_path:
            location = f"Location: {finding.file_path}"
            if finding.line_number:
                location += f":{finding.line_number}"
            parts.append(location)

        if finding.recommendation:
            parts.append(f"Recommendation: {finding.recommendation}")

        return "\n\n".join(parts)

    async def _add_features_to_backlog(
        self,
        features: list[DebtFeature],
    ) -> tuple[int, int]:
        """Add generated features to the harness features.json.

        Returns:
            Tuple of (added_count, skipped_count).
        """
        if not features:
            return 0, 0

        # Load existing features
        existing_features: list[dict[str, Any]] = []
        if self.features_path.exists():
            try:
                existing_features = json.loads(self.features_path.read_text())
            except json.JSONDecodeError:
                logger.warning("Could not parse existing features.json, starting fresh")

        # Track existing IDs and names for dedup
        existing_ids = {f.get("id") for f in existing_features}
        existing_names = {f.get("name") for f in existing_features}

        added = 0
        skipped = 0

        for feature in features:
            feature_dict = feature.to_dict()

            # Skip if ID or name already exists
            if feature_dict["id"] in existing_ids:
                skipped += 1
                continue
            if feature_dict["name"] in existing_names:
                skipped += 1
                continue

            existing_features.append(feature_dict)
            existing_ids.add(feature_dict["id"])
            existing_names.add(feature_dict["name"])
            added += 1

        # Write updated features
        self.features_path.write_text(json.dumps(existing_features, indent=2, default=str))

        return added, skipped

    def get_analysis_history(self) -> list[dict[str, Any]]:
        """Get history of self-analysis runs."""
        return [r.to_dict() for r in self._analysis_history]


async def run_self_analysis(
    harness_root: Path | None = None,
    features_path: Path | None = None,
    scanners: list[str] | None = None,
    max_features: int = 10,
    priority_threshold: str = "medium",
    dry_run: bool = False,
) -> SelfAnalysisResult:
    """Run self-analysis on the harness codebase.

    This is the main entry point for the self-improvement loop.

    Args:
        harness_root: Path to harness root directory.
        features_path: Path to features.json to update.
        scanners: Specific Tech Diligence scanners to run.
        max_features: Maximum number of features to generate.
        priority_threshold: Minimum priority level to include.
        dry_run: If True, don't write features to file.

    Returns:
        SelfAnalysisResult with analysis results.
    """
    improver = HarnessSelfImprover(
        harness_root=harness_root,
        features_path=features_path if not dry_run else Path("/dev/null"),
    )

    return await improver.analyze(
        scanners=scanners,
        max_features=max_features,
        priority_threshold=priority_threshold,
    )


def self_analyze_sync(
    harness_root: Path | None = None,
    features_path: Path | None = None,
    scanners: list[str] | None = None,
    max_features: int = 10,
    priority_threshold: str = "medium",
    dry_run: bool = False,
) -> SelfAnalysisResult:
    """Synchronous wrapper for run_self_analysis."""
    return asyncio.run(
        run_self_analysis(
            harness_root=harness_root,
            features_path=features_path,
            scanners=scanners,
            max_features=max_features,
            priority_threshold=priority_threshold,
            dry_run=dry_run,
        )
    )
