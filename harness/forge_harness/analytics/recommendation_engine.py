"""
Recommendation Engine for Next Task Recommendation Engine.

Provides high-level API for generating task recommendations.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..models import (
    AgentProfile,
    TaskAnalysis,
)


@dataclass
class CapabilityMatch:
    """Breakdown of capability matching for an agent."""

    agent_id: str
    score: float
    capability_score: float
    historical_score: float
    breakdown: dict[str, float]


@dataclass
class TaskRecommendation:
    """Recommendation result for a single task."""

    task: dict[str, Any]
    analysis: dict[str, Any]
    scoring: dict[str, Any]
    codebase_context: dict[str, Any] | None


@dataclass
class RecommendationResponse:
    """Full recommendation response."""

    recommendations: list[TaskRecommendation]
    meta: dict[str, Any]


class RecommendationEngine:
    """
    Engine for generating task recommendations.

    Combines task analysis, agent profiling, and scoring
    to produce ranked recommendations.
    """

    def __init__(self, time_decay: float = 0.1):
        """
        Initialize the recommendation engine.

        Args:
            time_decay: Weight for recency in historical scoring
        """
        self.time_decay = time_decay

    @staticmethod
    def _normalize_priority(priority: Any) -> int:
        """Convert priority to integer, handling various input types."""
        if priority is None:
            return 0
        if isinstance(priority, int):
            return priority
        if isinstance(priority, str):
            try:
                return int(priority)
            except ValueError:
                priority_map = {"critical": 4, "high": 3, "medium": 2, "low": 1}
                return priority_map.get(priority.lower(), 0)
        return 0

    def generate_recommendations(
        self,
        tasks: list[dict[str, Any]],
        agents: list[AgentProfile],
        limit: int = 10,
        agent_id_filter: str | None = None,
        include_blocked: bool = False,
        min_priority: int = 1,
        task_type_filter: str | None = None,
        domain_filter: str | None = None,
    ) -> RecommendationResponse:
        """
        Generate task recommendations.

        Args:
            tasks: List of task dictionaries
            agents: List of AgentProfiles
            limit: Maximum recommendations to return
            agent_id_filter: Optional agent ID to filter for
            include_blocked: Whether to include blocked tasks
            min_priority: Minimum priority threshold (1-10)
            task_type_filter: Optional task type to filter by
            domain_filter: Optional domain to filter by

        Returns:
            RecommendationResponse with ranked recommendations
        """
        from .scoring_engine import ScoringEngine
        from .task_classifier import TaskClassifier

        classifier = TaskClassifier()
        scoring_engine = ScoringEngine(time_decay=self.time_decay)

        # Filter and analyze tasks
        analyzed_tasks: list[tuple[TaskAnalysis, dict[str, Any]]] = []
        for task_dict in tasks:
            # Apply filters
            priority = self._normalize_priority(task_dict.get("priority", 1))
            if priority < min_priority:
                continue

            status = task_dict.get("status", "pending")
            if status == "completed":
                continue

            # Analyze task
            task_analysis = classifier.analyze_task(
                task_id=task_dict["id"],
                subject=task_dict.get("subject", ""),
                description=task_dict.get("description", ""),
                priority=priority,
                domain=task_dict.get("domain"),
                project=task_dict.get("project"),
                status=status,
            )

            # Apply additional filters
            if task_type_filter:
                if task_analysis.task_type.value != task_type_filter:
                    continue

            if domain_filter:
                if task_analysis.domain != domain_filter:
                    continue

            if task_analysis.blocked and not include_blocked:
                continue

            analyzed_tasks.append((task_analysis, task_dict))

        # Calculate scores for all task-agent pairs
        task_recommendations: list[TaskRecommendation] = []

        for task_analysis, task_dict in analyzed_tasks:
            # Calculate scores for all agents
            agent_scores = scoring_engine.calculate_agent_scores(task_analysis, agents)

            if not agent_scores:
                continue

            # Get top agents for this task
            top_agents = []
            for agent, score, breakdown in agent_scores[:3]:
                top_agents.append(
                    CapabilityMatch(
                        agent_id=agent.agent_id,
                        score=score,
                        capability_score=breakdown.capability_score,
                        historical_score=breakdown.historical_score,
                        breakdown={
                            "task_type_match": breakdown.task_type_match,
                            "skills_match": breakdown.skills_match,
                            "domain_preference": breakdown.domain_preference,
                        },
                    )
                )

            # Determine recommended agent (highest score)
            recommended_agent = agent_scores[0][0].agent_id if agent_scores else None
            recommendation_confidence = agent_scores[0][1] if agent_scores else 0.0

            # Build scoring response
            scoring = {
                "priority_score": task_analysis.priority_score,
                "capability_matches": [
                    {
                        "agent_id": match.agent_id,
                        "score": match.score,
                        "capability_score": match.capability_score,
                        "historical_score": match.historical_score,
                        "breakdown": match.breakdown,
                    }
                    for match in top_agents
                ],
                "recommended_agent": recommended_agent,
                "recommendation_confidence": recommendation_confidence,
            }

            # Build analysis response
            analysis = {
                "task_type": task_analysis.task_type.value,
                "complexity": task_analysis.complexity.name.lower(),
                "dependencies": task_analysis.dependencies,
                "blocked": task_analysis.blocked,
                "estimated_duration": str(task_analysis.estimated_duration)
                if task_analysis.estimated_duration
                else None,
                "required_skills": [s.value for s in task_analysis.required_skills],
            }

            # Build task response
            task = {
                "id": task_analysis.id,
                "subject": task_analysis.subject,
                "description": task_analysis.description,
                "priority": task_analysis.priority,
                "domain": task_analysis.domain,
                "project": task_analysis.project,
                "status": task_analysis.status,
            }

            # Build codebase context response
            codebase_context = None
            if task_analysis.codebase_context:
                codebase_context = task_analysis.codebase_context.to_dict()

            recommendation = TaskRecommendation(
                task=task,
                analysis=analysis,
                scoring=scoring,
                codebase_context=codebase_context,
            )

            task_recommendations.append(recommendation)

        # Sort by recommendation confidence
        task_recommendations.sort(
            key=lambda x: x.scoring.get("recommendation_confidence", 0), reverse=True
        )

        # Limit results
        limited_recommendations = task_recommendations[:limit]

        # Build response
        response = RecommendationResponse(
            recommendations=limited_recommendations,
            meta={
                "total_tasks": len(tasks),
                "recommended_count": len(limited_recommendations),
                "algorithm_version": "1.0",
                "generated_at": datetime.now().isoformat() + "Z",
            },
        )

        return response

    def to_dict(self, response: RecommendationResponse) -> dict[str, Any]:
        """
        Convert recommendation response to dictionary.

        Args:
            response: RecommendationResponse to convert

        Returns:
            Dictionary representation
        """
        return {
            "recommendations": [
                {
                    "task": rec.task,
                    "analysis": rec.analysis,
                    "scoring": rec.scoring,
                    "codebase_context": rec.codebase_context,
                }
                for rec in response.recommendations
            ],
            "meta": response.meta,
        }
