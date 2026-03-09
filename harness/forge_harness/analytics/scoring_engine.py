"""
Scoring Engine for Next Task Recommendation Engine.

Implements multi-factor scoring algorithm for task-agent matching.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..models import AgentProfile, TaskAnalysis


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of scoring components."""

    priority_score: float
    capability_score: float
    historical_score: float
    task_type_match: float
    skills_match: float
    domain_preference: float
    final_score: float
    blocked_penalty: bool
    complexity_penalty: bool
    availability_penalty: bool
    recency_boost: float


def calculate_task_score(
    task: TaskAnalysis,
    agent: AgentProfile,
    time_decay: float = 0.1,
) -> tuple[float, ScoreBreakdown]:
    """
    Calculate score for task-agent pairing.

    Args:
        task: TaskAnalysis with task details
        agent: AgentProfile with agent capabilities
        time_decay: Weight for recency (default: 0.1)

    Returns:
        Tuple of (score, breakdown) where score is in range [0, 1]
    """
    # Factor 1: Priority Score (Weight: 0.4)
    priority_score = task.priority_score

    # Factor 2: Capability Match (Weight: 0.4)
    capability_result = calculate_capability_match(task, agent)
    capability_score = capability_result["score"]

    # Factor 3: Historical Performance (Weight: 0.2)
    historical_score = calculate_historical_score(task, agent)

    # Weighted Combination
    final_score = (
        0.4 * priority_score +
        0.4 * capability_score +
        0.2 * historical_score
    )

    # Apply Penalties
    blocked_penalty = False
    complexity_penalty = False
    availability_penalty = False

    if task.blocked:
        final_score *= 0.1  # Heavy penalty for blocked tasks
        blocked_penalty = True

    if task.complexity.value > agent.max_complexity.value:
        final_score *= 0.3  # Penalty for exceeding agent capability
        complexity_penalty = True

    if agent.status != "idle":
        final_score *= 0.5  # Penalty for busy agents
        availability_penalty = True

    # Time Decay (boost recent successful completions)
    recency_boost = 0.0
    if task.task_type in agent.task_type_history:
        history = agent.task_type_history[task.task_type]
        if history.last_completed:
            days_since_last = (datetime.now() - history.last_completed).days
            recency_boost_val = math.exp(-time_decay * days_since_last)
            final_score *= (1 + 0.1 * recency_boost_val)
            recency_boost = recency_boost_val

    # Clamp to [0, 1]
    final_score = max(0, min(1, final_score))

    breakdown = ScoreBreakdown(
        priority_score=priority_score,
        capability_score=capability_score,
        historical_score=historical_score,
        task_type_match=capability_result["task_type_match"],
        skills_match=capability_result["skills_match"],
        domain_preference=capability_result["domain_preference"],
        final_score=final_score,
        blocked_penalty=blocked_penalty,
        complexity_penalty=complexity_penalty,
        availability_penalty=availability_penalty,
        recency_boost=recency_boost,
    )

    return final_score, breakdown


def calculate_capability_match(
    task: TaskAnalysis,
    agent: AgentProfile,
) -> dict[str, Any]:
    """
    Calculate how well agent capabilities match task requirements.

    Args:
        task: TaskAnalysis with task details
        agent: AgentProfile with agent capabilities

    Returns:
        Dictionary with score and component breakdowns
    """
    # Direct Task Type Match
    task_type_score = agent.capabilities.get(task.task_type, 0.0)

    # Skills Match
    if task.required_skills:
        skill_scores = [
            agent.skills.get(skill, 0.0)
            for skill in task.required_skills
        ]
        skills_score = sum(skill_scores) / len(skill_scores)
    else:
        skills_score = 0.5  # Neutral if no skills specified

    # Domain Preference
    domain_score = 0.5  # Base score
    if task.domain and task.domain in agent.preferred_domains:
        domain_score = 1.0

    # Task Type Avoidance
    adjusted_task_type_score = task_type_score
    if task.task_type in agent.avoids:
        adjusted_task_type_score *= 0.2  # Heavy penalty for avoided types

    # Combine (70% task type, 20% skills, 10% domain)
    capability_score = (
        0.7 * adjusted_task_type_score +
        0.2 * skills_score +
        0.1 * domain_score
    )

    return {
        "score": capability_score,
        "task_type_match": adjusted_task_type_score,
        "skills_match": skills_score,
        "domain_preference": domain_score,
    }


def calculate_historical_score(
    task: TaskAnalysis,
    agent: AgentProfile,
) -> float:
    """
    Calculate score based on agent's historical performance.

    Args:
        task: TaskAnalysis with task details
        agent: AgentProfile with agent capabilities

    Returns:
        Score in range [0, 1]
    """
    # Overall Success Rate (40% weight)
    success_rate = agent.success_rate

    # Task-Type Specific Performance (40% weight)
    if task.task_type in agent.task_type_history:
        history = agent.task_type_history[task.task_type]
        type_success_rate = history.success_rate
        type_quality = history.avg_quality_score
        type_performance = (type_success_rate + type_quality) / 2
    else:
        # Cold start penalty for new task types
        type_performance = 0.3

    # Recent Performance Trend (20% weight)
    # Calculate trend from last 5 completed tasks
    recent_scores = get_recent_performance_scores(agent.agent_id, n=5)
    if recent_scores:
        trend = sum(recent_scores) / len(recent_scores)
    else:
        trend = 0.5  # Neutral for no history

    # Combine
    historical_score = (
        0.4 * success_rate +
        0.4 * type_performance +
        0.2 * trend
    )

    return historical_score


def get_recent_performance_scores(
    agent_id: str,
    n: int = 5,
) -> list[float]:
    """
    Get recent performance scores for an agent.

    This is a placeholder that would query the historical store.
    In a real implementation, this would retrieve actual performance data.

    Args:
        agent_id: The agent ID to query
        n: Number of recent scores to retrieve

    Returns:
        List of recent performance scores
    """
    # NOTE: Actual retrieval from historical store is planned for Phase 5 (ML Enhancement)
    # per architectural roadmap. For now, return empty list (neutral score).
    return []


class ScoringEngine:
    """
    Engine for calculating task-agent scores.

    Provides batch scoring and caching capabilities.
    """

    def __init__(self, time_decay: float = 0.1):
        """
        Initialize the scoring engine.

        Args:
            time_decay: Weight for recency in historical scoring
        """
        self.time_decay = time_decay
        self._cache: dict[str, float] = {}

    def calculate_agent_scores(
        self,
        task: TaskAnalysis,
        agents: list[AgentProfile],
    ) -> list[tuple[AgentProfile, float, ScoreBreakdown]]:
        """
        Calculate scores for all agents for a given task.

        Args:
            task: TaskAnalysis to score
            agents: List of AgentProfiles to score against

        Returns:
            List of (agent, score, breakdown) tuples, sorted by score descending
        """
        results = []
        for agent in agents:
            score, breakdown = calculate_task_score(task, agent, self.time_decay)
            results.append((agent, score, breakdown))

        # Sort by score descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def calculate_task_scores_for_agent(
        self,
        tasks: list[TaskAnalysis],
        agent: AgentProfile,
    ) -> list[tuple[TaskAnalysis, float, ScoreBreakdown]]:
        """
        Calculate scores for all tasks for a given agent.

        Args:
            tasks: List of TaskAnalysis to score
            agent: AgentProfile to score against

        Returns:
            List of (task, score, breakdown) tuples, sorted by score descending
        """
        results = []
        for task in tasks:
            score, breakdown = calculate_task_score(task, agent, self.time_decay)
            results.append((task, score, breakdown))

        # Sort by score descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def clear_cache(self) -> None:
        """Clear the scoring cache."""
        self._cache.clear()
