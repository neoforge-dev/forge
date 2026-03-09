"""
FORGE Meta-Learning System.

This module provides autonomous feedback loops that compound development velocity by:
1. Learning from past Claude sessions (Code Atlas integration)
2. Identifying quality issues (Tech Diligence integration)
3. Making intelligent decisions (Decision Engine)
4. Recording outcomes to improve future decisions (Learning Store)

Usage:
    from forge_harness.meta_learning import DecisionEngine, LearningStore
    from forge_harness.meta_learning.schemas import DecisionContext, DecisionRecommendation
"""

from forge_harness.meta_learning.bridges.code_atlas import CodeAtlasBridge
from forge_harness.meta_learning.bridges.tech_diligence import TechDiligenceBridge
from forge_harness.meta_learning.claude_md_updater import (
    ClaudeMdUpdater,
    LearnedPattern,
    PatternsSection,
    auto_update_claude_md,
)
from forge_harness.meta_learning.config import MetaLearningConfig
from forge_harness.meta_learning.decision_engine import DecisionEngine
from forge_harness.meta_learning.feedback_loops import (
    DebtFeature,
    FeedbackConfig,
    FeedbackLoopManager,
    SessionSummary,
    generate_debt_features,
    human_gate_optimizer,
    post_session_hook,
)
from forge_harness.meta_learning.learning_store import (
    LearningStore,
    ThresholdAdjustment,
    ThresholdConfig,
)
from forge_harness.meta_learning.schemas import (
    AnalysisJobStatus,
    # Code Atlas schemas
    AtlasEntity,
    AtlasPattern,
    AtlasRAGResponse,
    AtlasSignals,
    ConfidenceLevel,
    # Decision Engine schemas
    DecisionAction,
    DecisionContext,
    DecisionOutcome,
    DecisionRecommendation,
    DecisionRecord,
    DiligenceFinding,
    DiligenceReport,
    DiligenceSignals,
    FindingCategory,
    # Tech Diligence schemas
    FindingSeverity,
    # Learning Store schemas
    PatternRecord,
    ScoreCard,
    Warning,
)

__all__ = [
    # Code Atlas
    "AtlasEntity",
    "AtlasPattern",
    "AtlasSignals",
    "AtlasRAGResponse",
    # Tech Diligence
    "FindingSeverity",
    "FindingCategory",
    "DiligenceFinding",
    "DiligenceReport",
    "DiligenceSignals",
    "AnalysisJobStatus",
    # Decision Engine
    "DecisionAction",
    "ConfidenceLevel",
    "DecisionContext",
    "ScoreCard",
    "DecisionRecommendation",
    "DecisionOutcome",
    "Warning",
    # Learning Store
    "PatternRecord",
    "DecisionRecord",
    # Config
    "MetaLearningConfig",
    # Learning Store (loop-003)
    "LearningStore",
    "ThresholdConfig",
    "ThresholdAdjustment",
    # Bridges
    "CodeAtlasBridge",
    "TechDiligenceBridge",
    # Decision Engine
    "DecisionEngine",
    # Feedback Loops
    "FeedbackConfig",
    "FeedbackLoopManager",
    "SessionSummary",
    "DebtFeature",
    "post_session_hook",
    "generate_debt_features",
    "human_gate_optimizer",
    # CLAUDE.md Updater
    "ClaudeMdUpdater",
    "LearnedPattern",
    "PatternsSection",
    "auto_update_claude_md",
]
