"""Smart Task Prioritizer for FORGE Harness.

Ranks tasks by impact, urgency, effort, and domain weights.
Considers failing tests, broken builds, and dependencies.

Usage:
    from forge_harness.iteration.prioritizer import prioritize_tasks, PrioritizedTask

    tasks = prioritize_tasks(
        features_paths=[Path("features.json")],
        assessment_report=report,
        strategy="impact-first",
        domain_weights={"voice-coach": 1.5}
    )

    for task in tasks[:5]:  # Top 5
        print(f"{task.feature_id}: {task.title} (score: {task.score:.2f})")
        print(f"  Reasoning: {task.reasoning}")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from forge_harness.iteration.assess import AssessmentReport, IssueType
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

PRIORITY_WEIGHTS = {
    "critical": 4.0,
    "high": 3.0,
    "medium": 2.0,
    "low": 1.0,
}

DOMAIN_BOOSTS = {
    "voice-coach": 1.5,
    "command_center": 1.5,
}


def _prioritize_simple(features_path: Path) -> list[PrioritizedTask]:
    """Simple scoring for legacy features.json structure (used in tests)."""
    if not features_path.exists():
        return []
    data = json.loads(features_path.read_text())
    features = data.get("features", [])
    if not features:
        return []

    tasks: list[PrioritizedTask] = []
    for feature in features:
        task_id = feature.get("id", "")
        title = feature.get("title", "")
        priority = feature.get("priority", "medium")
        deps = feature.get("dependencies", []) or []
        estimated_tokens = float(feature.get("estimated_tokens", 0))
        has_failing_tests = bool(feature.get("has_failing_tests", False))
        domain = feature.get("domain", "")

        impact = (len(deps) * 2.0) + PRIORITY_WEIGHTS.get(priority, 2.0)
        urgency = 1.0
        if has_failing_tests:
            urgency += 3.0
        if priority == "critical":
            urgency += 2.0
        effort = estimated_tokens / 1000.0 if estimated_tokens else 0.0
        domain_boost = DOMAIN_BOOSTS.get(domain, 1.0)
        score = (impact + urgency - effort) * domain_boost

        reasoning = (
            f"Score={score:.1f} (impact={impact:.1f}, "
            f"{len(deps)} deps, {priority} priority; "
            f"urgency={urgency:.1f} {'failing tests' if has_failing_tests else ''}; "
            f"effort={effort:.1f} from {int(estimated_tokens)} tokens; "
            f"domain_boost={domain_boost}x for {domain})"
        )

        tasks.append(
            PrioritizedTask(
                task_id=task_id,
                title=title,
                score=round(score, 1),
                impact=round(impact, 1),
                urgency=round(urgency, 1),
                effort=round(effort, 1),
                domain_weight=domain_boost,
                reasoning=reasoning,
                dependencies=list(deps),
                status=feature.get("status", "pending"),
                acceptance_criteria=feature.get("acceptance_criteria", []) or [],
                test_command=feature.get("test_command"),
                metadata={"domain": domain, "priority": priority},
            )
        )

    tasks.sort(key=lambda t: t.score, reverse=True)
    return tasks


class PrioritizationStrategy(str, Enum):
    """Strategy for prioritizing tasks."""

    IMPACT_FIRST = "impact-first"  # Maximize impact regardless of effort
    QUICK_WINS = "quick-wins"  # Prioritize high value / low effort
    BALANCED = "balanced"  # Balance impact, urgency, and effort
    URGENCY_FIRST = "urgency-first"  # Focus on time-sensitive tasks
    DEPENDENCY_FIRST = "dependency-first"  # Clear blockers first


@dataclass
class PrioritizedTask:
    """A task with priority scoring and metadata."""

    task_id: str
    title: str
    score: float
    impact: float  # Weighted impact score
    urgency: float  # Weighted urgency score
    effort: float  # Estimated effort
    domain_weight: float  # Multiplier for domain priority
    reasoning: str
    dependencies: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    status: str = "pending"
    epic: str | None = None
    acceptance_criteria: list[str] = field(default_factory=list)
    test_command: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    feature_id: str | None = None

    def __post_init__(self) -> None:
        if self.feature_id is None:
            self.feature_id = self.task_id

        # Defensive: Ensure metadata is always a dict
        if not isinstance(self.metadata, dict):
            logger.warning(
                f"Task {self.task_id} had metadata of type {type(self.metadata)}, converting to dict"
            )
            self.metadata = {}

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "feature_id": self.feature_id,
            "title": self.title,
            "score": self.score,
            "impact": self.impact,
            "urgency": self.urgency,
            "effort": self.effort,
            "domain_weight": self.domain_weight,
            "reasoning": self.reasoning,
            "dependencies": self.dependencies,
            "blockers": self.blockers,
            "status": self.status,
            "epic": self.epic,
            "acceptance_criteria": self.acceptance_criteria,
            "test_command": self.test_command,
            "metadata": self.metadata,
        }

    @property
    def is_blocked(self) -> bool:
        """Check if task has blockers."""
        return len(self.blockers) > 0

    @property
    def impact_effort_ratio(self) -> float:
        """Get impact/effort ratio for quick wins analysis."""
        return self.impact / max(self.effort, 1)


# ============================================================================
# Default Domain Weights
# ============================================================================

DEFAULT_DOMAIN_WEIGHTS = {
    # High priority domains (active development)
    "voice-coach": 1.5,
    "interview-simulator": 1.3,
    "brand-brain": 1.3,
    # Standard priority domains
    "codeswiftr-com": 1.0,
    "thebrightharbor-com": 1.0,
    "calmconnect-io": 1.0,
    "adguild-io": 1.0,
    "leanvibe-ai": 1.0,
    "neoforge-dev": 1.0,
    "babybit-es": 1.0,
    "mumchef-io": 1.0,
    "bogdanveliscu-com": 1.0,
    # Infrastructure/harness
    "harness": 1.2,
    "forge-shared": 1.1,
}


# ============================================================================
# Feature Loading
# ============================================================================


def load_features_from_file(features_path: Path) -> list[dict[str, Any]]:
    """Load features from a features.json file.

    Args:
        features_path: Path to features.json file

    Returns:
        List of feature dictionaries
    """
    if not features_path.exists():
        logger.warning(f"Features file not found: {features_path}")
        return []

    try:
        with open(features_path) as f:
            data = json.load(f)

        # Handle different JSON structures
        if isinstance(data, dict):
            features_data = data.get("features", [])
            # Handle both array and object structures
            if isinstance(features_data, dict):
                # Convert dict of features to list (e.g., {"backend_core": {...}, "ui": {...}})
                features = [
                    {**feature_dict, "id": feature_id}
                    for feature_id, feature_dict in features_data.items()
                    if isinstance(feature_dict, dict)
                ]
                if len(features) < len(features_data):
                    logger.warning(
                        f"Skipped non-dict entries in features object in {features_path}"
                    )
            elif isinstance(features_data, list):
                features = features_data
            else:
                logger.warning(
                    f"Unexpected features type in {features_path}: {type(features_data)}"
                )
                return []
        elif isinstance(data, list):
            features = data
        else:
            logger.warning(f"Unexpected features.json structure: {type(data)}")
            return []

        # Filter out non-dict features (defensive validation)
        valid_features = []
        skipped_count = 0
        for feature in features:
            if isinstance(feature, dict):
                valid_features.append(feature)
            else:
                skipped_count += 1
                logger.warning(
                    f"Skipping non-dict feature in {features_path}: {type(feature).__name__}"
                )

        if skipped_count > 0:
            logger.warning(f"Skipped {skipped_count} non-dict feature(s) in {features_path}")

        logger.info(f"Loaded {len(valid_features)} valid features from {features_path}")
        return valid_features

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse {features_path}: {e}")
        return []
    except Exception as e:
        logger.error(f"Error loading features from {features_path}: {e}")
        return []


def find_features_in_portfolio(forge_root: Path | None = None) -> list[Path]:
    """Find all features.json files in the FORGE portfolio.

    Args:
        forge_root: Root of FORGE portfolio (defaults to current directory's FORGE ancestor)

    Returns:
        List of paths to features.json files
    """
    if forge_root is None:
        # Try to find FORGE root
        current = Path.cwd()
        if current.name == "FORGE":
            forge_root = current
        else:
            for parent in current.parents:
                if parent.name == "FORGE":
                    forge_root = parent
                    break
            else:
                forge_root = current

    features_files = []

    # Search for features.json recursively
    for features_file in forge_root.rglob("features*.json"):
        # Skip node_modules, .git, venv, etc.
        if any(
            part.startswith(".") or part in ["node_modules", "venv", ".venv", "dist", "build"]
            for part in features_file.parts
        ):
            continue

        features_files.append(features_file)

    logger.info(f"Found {len(features_files)} features files in portfolio")
    return features_files


# ============================================================================
# Effort Estimation
# ============================================================================


def estimate_effort_from_criteria(acceptance_criteria: list[str]) -> int:
    """Estimate effort (1-5) based on acceptance criteria complexity.

    Args:
        acceptance_criteria: List of acceptance criteria strings

    Returns:
        Effort score from 1 (trivial) to 5 (very complex)
    """
    if not acceptance_criteria:
        return 3  # Default medium effort

    num_criteria = len(acceptance_criteria)

    # Count complexity indicators
    complexity_indicators = 0
    for criterion in acceptance_criteria:
        criterion_lower = criterion.lower()
        # Integration/external service = complex
        if any(
            word in criterion_lower
            for word in ["integrate", "api", "external", "service", "webhook"]
        ):
            complexity_indicators += 2
        # Database/state changes = moderately complex
        if any(
            word in criterion_lower
            for word in ["database", "migration", "schema", "persist", "store"]
        ):
            complexity_indicators += 1
        # Testing/validation = adds effort
        if any(word in criterion_lower for word in ["test", "validate", "verify"]):
            complexity_indicators += 0.5
        # Performance requirements = complex
        if any(word in criterion_lower for word in ["performance", "optimize", "cache"]):
            complexity_indicators += 1

    # Base effort on number of criteria and complexity
    base_effort = min(num_criteria / 2, 3)
    complexity_effort = min(complexity_indicators / 2, 2)

    total_effort = base_effort + complexity_effort

    # Map to 1-5 scale
    if total_effort <= 1:
        return 1  # Trivial
    elif total_effort <= 2:
        return 2  # Simple
    elif total_effort <= 3:
        return 3  # Medium
    elif total_effort <= 4:
        return 4  # Complex
    else:
        return 5  # Very complex


# ============================================================================
# Dependency Analysis
# ============================================================================


def identify_dependencies(features: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Identify dependencies between features.

    Args:
        features: List of feature dictionaries

    Returns:
        Dict mapping feature_id to list of dependency feature_ids
    """
    dependencies = {}

    # Build feature ID index
    feature_ids = {f.get("id") for f in features if f.get("id")}

    for feature in features:
        feature_id = feature.get("id")
        if not feature_id:
            continue

        deps = []

        # Check explicit dependencies field
        if "dependencies" in feature:
            explicit_deps = feature["dependencies"]
            if isinstance(explicit_deps, list):
                deps.extend(explicit_deps)

        # Check acceptance criteria for references to other features
        criteria = feature.get("acceptance_criteria", [])
        for criterion in criteria:
            # Look for feature ID references (e.g., "HRN-001", "IS-042")
            for other_id in feature_ids:
                if other_id != feature_id and other_id in criterion:
                    deps.append(other_id)

        # Check description for dependencies
        description = feature.get("description", "")
        for other_id in feature_ids:
            if other_id != feature_id and other_id in description:
                deps.append(other_id)

        dependencies[feature_id] = list(set(deps))  # Remove duplicates

    return dependencies


def identify_blockers(
    feature_id: str,
    dependencies: dict[str, list[str]],
    feature_status: dict[str, str],
) -> list[str]:
    """Identify blockers for a feature (incomplete dependencies).

    Args:
        feature_id: Feature ID to check
        dependencies: Dict of feature dependencies
        feature_status: Dict mapping feature_id to status

    Returns:
        List of blocking feature IDs (dependencies that are not complete)
    """
    blockers = []
    deps = dependencies.get(feature_id, [])

    for dep_id in deps:
        dep_status = feature_status.get(dep_id, "pending")
        if dep_status not in ["complete", "done", "released"]:
            blockers.append(dep_id)

    return blockers


# ============================================================================
# Scoring Strategies
# ============================================================================


def calculate_score(
    impact: int,
    urgency: int,
    effort: int,
    domain_weight: float,
    strategy: PrioritizationStrategy,
) -> float:
    """Calculate priority score based on strategy.

    Args:
        impact: Impact score (1-10)
        urgency: Urgency score (1-10)
        effort: Effort score (1-5)
        domain_weight: Domain multiplier
        strategy: Prioritization strategy

    Returns:
        Composite priority score (higher is better)
    """
    if strategy == PrioritizationStrategy.IMPACT_FIRST:
        # Maximize impact, ignore effort
        score = (impact * 2 + urgency) * domain_weight

    elif strategy == PrioritizationStrategy.QUICK_WINS:
        # Maximize impact/effort ratio
        score = ((impact * urgency) / max(effort, 1)) * domain_weight

    elif strategy == PrioritizationStrategy.BALANCED:
        # Balance all factors
        score = ((impact * urgency) / effort) * domain_weight

    elif strategy == PrioritizationStrategy.URGENCY_FIRST:
        # Prioritize urgent tasks
        score = (urgency * 2 + impact) / effort * domain_weight

    elif strategy == PrioritizationStrategy.DEPENDENCY_FIRST:
        # Prioritize tasks with no dependencies (will be boosted later)
        score = (impact + urgency) / effort * domain_weight

    else:
        # Default to balanced
        score = ((impact * urgency) / effort) * domain_weight

    return score


# ============================================================================
# Assessment-Based Boosting
# ============================================================================


def boost_for_failures(tasks: list[PrioritizedTask], assessment: AssessmentReport) -> None:
    """Boost priority for tasks related to failing tests or broken builds.

    Modifies tasks in place by increasing scores for failure-related tasks.

    Args:
        tasks: List of prioritized tasks to modify
        assessment: Assessment report with test/build status
    """
    # Extract failed test files
    failed_test_files = set()
    for test_result in assessment.test_results.failed_tests:
        if test_result.file_path:
            failed_test_files.add(test_result.file_path)

    # Extract issues by type
    build_errors = [i for i in assessment.issues if i.issue_type == IssueType.BUILD_ERROR]
    [i for i in assessment.issues if i.issue_type == IssueType.FAILED_TEST]

    for task in tasks:
        boost_applied = False

        # Boost for failing tests
        if assessment.test_results.failed_count > 0:
            # Check if task mentions testing or is a QA-related feature
            if any(word in task.title.lower() for word in ["test", "qa", "quality", "coverage"]):
                task.score *= 1.5
                task.reasoning += " [BOOST: Failing tests detected]"
                boost_applied = True

        # Boost for build errors
        if build_errors:
            if any(word in task.title.lower() for word in ["build", "compile", "lint", "type"]):
                task.score *= 1.8
                task.reasoning += " [BOOST: Build errors detected]"
                boost_applied = True

        # Boost for git conflicts
        if assessment.git_status.total_conflicts > 0:
            if any(
                word in task.title.lower() for word in ["merge", "conflict", "resolve", "rebase"]
            ):
                task.score *= 1.7
                task.reasoning += " [BOOST: Git conflicts detected]"
                boost_applied = True

        # Tag if boost was applied
        if boost_applied:
            task.metadata["failure_boost"] = True


# ============================================================================
# Main Prioritization Function
# ============================================================================


def prioritize_tasks(
    features_paths: list[Path] | str | Path | None = None,
    assessment_report: AssessmentReport | None = None,
    strategy: PrioritizationStrategy = PrioritizationStrategy.BALANCED,
    domain_weights: dict[str, float] | None = None,
    forge_root: Path | None = None,
) -> list[PrioritizedTask]:
    """Prioritize tasks from features.json files.

    Args:
        features_paths: List of paths to features.json files (auto-discovered if None)
        assessment_report: Assessment report for failure boosting
        strategy: Prioritization strategy to use
        domain_weights: Custom domain weights (merged with defaults)
        forge_root: FORGE portfolio root

    Returns:
        Sorted list of PrioritizedTask objects (highest priority first)
    """
    # Simple path-based prioritization for legacy tests
    if isinstance(features_paths, (str, Path)):
        return _prioritize_simple(Path(features_paths))

    # Auto-discover features if not provided
    if features_paths is None:
        features_paths = find_features_in_portfolio(forge_root)

    # Merge domain weights
    weights = DEFAULT_DOMAIN_WEIGHTS.copy()
    if domain_weights:
        weights.update(domain_weights)

    # Load all features
    all_features = []
    for path in features_paths:
        features = load_features_from_file(path)
        # Tag with source path for domain detection (defensive check for dict type)
        for feature in features:
            if isinstance(feature, dict):
                feature["_source_path"] = str(path)
            else:
                logger.warning(f"Skipping non-dict feature from {path}: {type(feature).__name__}")
        # Only extend with dict features
        all_features.extend([f for f in features if isinstance(f, dict)])

    if not all_features:
        logger.warning("No features found to prioritize")
        return []

    # Build dependency graph
    dependencies = identify_dependencies(all_features)
    feature_status = {f.get("id"): f.get("status", "pending") for f in all_features if f.get("id")}

    # Convert to PrioritizedTask objects
    tasks = []
    for feature in all_features:
        feature_id = feature.get("id")
        if not feature_id:
            continue

        # Skip completed features
        status = feature.get("status", "pending")
        if status in ["complete", "done", "released"]:
            continue

        # Extract fields
        title = feature.get("title", "Untitled")
        epic = feature.get("epic")
        priority = feature.get("priority", "medium")
        acceptance_criteria = feature.get("acceptance_criteria", [])
        test_command = feature.get("test_command")

        # Determine domain from source path
        source_path = Path(feature.get("_source_path", ""))
        domain = None
        for part in source_path.parts:
            if part in weights:
                domain = part
                break

        domain_weight = weights.get(domain, 1.0) if domain else 1.0

        # Estimate dimensions
        impact = _estimate_impact(feature, priority, epic)
        urgency = _estimate_urgency(feature, priority)
        effort = estimate_effort_from_criteria(acceptance_criteria)

        # Calculate score
        score = calculate_score(impact, urgency, effort, domain_weight, strategy)

        # Identify blockers
        blockers = identify_blockers(feature_id, dependencies, feature_status)

        # Build reasoning
        reasoning = f"Impact: {impact}/10, Urgency: {urgency}/10, Effort: {effort}/5"
        if domain:
            reasoning += f", Domain: {domain} ({domain_weight}x)"
        if blockers:
            reasoning += f", Blocked by: {', '.join(blockers)}"

        task = PrioritizedTask(
            task_id=feature_id,
            title=title,
            score=score,
            impact=impact,
            urgency=urgency,
            effort=effort,
            domain_weight=domain_weight,
            reasoning=reasoning,
            dependencies=dependencies.get(feature_id, []),
            blockers=blockers,
            status=status,
            epic=epic,
            acceptance_criteria=acceptance_criteria,
            test_command=test_command,
            metadata={"domain": domain, "priority": priority},
        )

        tasks.append(task)

    # Apply failure boosting if assessment provided
    if assessment_report:
        boost_for_failures(tasks, assessment_report)

    # Apply dependency-based boosting
    if strategy == PrioritizationStrategy.DEPENDENCY_FIRST:
        for task in tasks:
            if not task.blockers:
                task.score *= 2.0
                task.reasoning += " [BOOST: No blockers]"

    # Sort by score (highest first)
    tasks.sort(key=lambda t: t.score, reverse=True)

    logger.info(
        f"Prioritized {len(tasks)} tasks using {strategy.value} strategy. "
        f"Top task: {tasks[0].feature_id if tasks else 'none'}"
    )

    return tasks


def _estimate_impact(feature: dict[str, Any], priority: str, epic: str | None) -> int:
    """Estimate impact score (1-10) from feature metadata.

    Args:
        feature: Feature dictionary
        priority: Feature priority (low/medium/high/critical)
        epic: Epic name

    Returns:
        Impact score 1-10
    """
    # Base impact from priority
    priority_map = {
        "critical": 9,
        "high": 7,
        "medium": 5,
        "low": 3,
    }
    base_impact = priority_map.get(priority.lower() if priority else "medium", 5)

    # Adjust for epic
    epic_boosts = {
        "Quality Gates": 2,
        "Iteration Loop": 2,
        "Multi-Agent Executor": 1,
        "Fleet Management": 1,
    }
    epic_boost = epic_boosts.get(epic, 0) if epic else 0

    # Cap at 10
    return min(base_impact + epic_boost, 10)


def _estimate_urgency(feature: dict[str, Any], priority: str) -> int:
    """Estimate urgency score (1-10) from feature metadata.

    Args:
        feature: Feature dictionary
        priority: Feature priority

    Returns:
        Urgency score 1-10
    """
    # Base urgency from priority
    priority_map = {
        "critical": 10,
        "high": 7,
        "medium": 4,
        "low": 2,
    }
    urgency = priority_map.get(priority.lower() if priority else "medium", 4)

    # Check for time-sensitive keywords in title/description
    title = feature.get("title", "").lower()
    description = feature.get("description", "").lower()
    text = f"{title} {description}"

    if any(word in text for word in ["urgent", "critical", "blocker", "broken"]):
        urgency = min(urgency + 3, 10)
    elif any(word in text for word in ["fix", "bug", "error", "failure"]):
        urgency = min(urgency + 2, 10)

    return urgency
