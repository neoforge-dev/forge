"""Flywheel Feature Generator - Convert debt findings to features.json entries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..logging_config import get_logger

logger = get_logger(__name__)


class FeatureGenerator:
    """Convert tech debt findings into actionable features."""

    def generate_from_findings(
        self,
        domain: str,
        project: str,
        findings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Generate feature dicts from raw findings."""
        features = []
        for finding in findings:
            feature = {
                "id": f"debt-{domain}-{project}-{finding.get('category', 'unknown')}-{len(features)}",
                "name": f"[{project}] {finding.get('title', 'Untitled issue')}",
                "description": finding.get("description", ""),
                "status": "pending",
                "priority": finding.get("severity", "medium"),
                "acceptance_criteria": [
                    f"Fix {finding.get('category', 'issue')} in {finding.get('file_path') or 'codebase'}",
                    "All tests pass",
                ],
                "metadata": {
                    "source": "flywheel_generator",
                    "domain": domain,
                    "project": project,
                    "generated_at": datetime.now(UTC).isoformat(),
                },
            }
            features.append(feature)
        return features


def quality_report_to_features(
    report: Any,  # ProjectQualityReport
    max_features: int = 10,
    priority_threshold: str = "medium",
) -> list[dict[str, Any]]:
    """Convert a ProjectQualityReport to feature dicts."""
    from ..quality_loop import SeverityLevel

    # Priority filtering
    priority_order = ["critical", "high", "medium", "low"]
    threshold_idx = priority_order.index(priority_threshold)
    allowed_priorities = set(priority_order[: threshold_idx + 1])

    # Map severity to priority
    severity_to_priority = {
        SeverityLevel.CRITICAL: "critical",
        SeverityLevel.HIGH: "high",
        SeverityLevel.MEDIUM: "medium",
        SeverityLevel.LOW: "low",
    }

    features: list[dict[str, Any]] = []

    # Convert security findings to features
    for finding in report.security_findings:
        priority = severity_to_priority.get(finding.severity, "medium")
        if priority not in allowed_priorities:
            continue

        feature_id = f"quality-{report.domain}-{report.project_name}-{finding.rule_id}-{len(features)}"

        # Build description
        description = f"**Security Issue:** {finding.message}"
        if finding.file_path:
            location = f"\n\n**Location:** `{finding.file_path}`"
            if finding.line_number:
                location += f":{finding.line_number}"
            description += location
        description += f"\n\n**Tool:** {finding.tool}\n**Confidence:** {finding.confidence}"

        feature = {
            "id": feature_id,
            "name": f"[{report.project_name}] {finding.message[:60]}",
            "description": description,
            "status": "pending",
            "priority": priority,
            "acceptance_criteria": [
                f"Fix {finding.rule_id} in {finding.file_path or 'codebase'}",
                "Security scan passes",
                "All tests pass",
            ],
            "depends_on": [],
            "tests": [],
            "estimated_tokens": 4000,
            "attempts": 0,
            "metadata": {
                "source": "quality_loop",
                "domain": report.domain,
                "project": report.project_name,
                "finding_type": "security",
                "rule_id": finding.rule_id,
                "tool": finding.tool,
                "generated_at": datetime.now(UTC).isoformat(),
            },
        }
        features.append(feature)

        if len(features) >= max_features:
            break

    # Add high-level debt issue if quality score is low
    if report.quality_score < 70 and len(features) < max_features:
        feature = {
            "id": f"quality-{report.domain}-{report.project_name}-debt-score",
            "name": f"[{report.project_name}] Improve quality score ({report.quality_score:.0f}%)",
            "description": f"**Quality Score:** {report.quality_score:.0f}%\n\n"
            f"**Issues:**\n" + "\n".join(f"- {i}" for i in report.issues[:5]) + "\n\n"
            "**Recommendations:**\n" + "\n".join(f"- {r}" for r in report.recommendations[:5]),
            "status": "pending",
            "priority": "high" if report.quality_score < 50 else "medium",
            "acceptance_criteria": [
                f"Quality score above {max(report.quality_score + 10, 70)}%",
                "Address identified issues",
                "All tests pass",
            ],
            "depends_on": [],
            "tests": [],
            "estimated_tokens": 6000,
            "attempts": 0,
            "metadata": {
                "source": "quality_loop",
                "domain": report.domain,
                "project": report.project_name,
                "finding_type": "debt",
                "quality_score": report.quality_score,
                "generated_at": datetime.now(UTC).isoformat(),
            },
        }
        features.append(feature)

    return features


def _build_debt_description(finding: Any) -> str:
    """Build feature description from a Tech Diligence finding."""
    parts = [finding.message]

    if finding.file_path:
        location = f"**Location:** `{finding.file_path}`"
        if finding.line_number:
            location += f":{finding.line_number}"
        parts.append(location)

    if finding.recommendation:
        parts.append(f"**Recommendation:** {finding.recommendation}")

    return "\n\n".join(parts)
