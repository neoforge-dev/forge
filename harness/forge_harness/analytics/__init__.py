"""
Analytics Module for Next Task Recommendation Engine.

Provides scoring algorithms, task classification, and complexity estimation.
"""

from .recommendation_engine import RecommendationEngine
from .scoring_engine import (
    ScoringEngine,
    calculate_capability_match,
    calculate_historical_score,
    calculate_task_score,
)
from .task_classifier import (
    TaskClassifier,
    detect_task_type,
    estimate_complexity,
    extract_required_skills,
)

__all__ = [
    "ScoringEngine",
    "calculate_task_score",
    "calculate_capability_match",
    "calculate_historical_score",
    "TaskClassifier",
    "detect_task_type",
    "estimate_complexity",
    "extract_required_skills",
    "RecommendationEngine",
]
