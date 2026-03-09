"""
Pydantic schemas for the FORGE Meta-Learning System.

All models use Pydantic v2 syntax with field validators.
"""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field, computed_field

# =============================================================================
# Code Atlas Schemas
# =============================================================================


class AtlasEntity(BaseModel):
    """An entity extracted from Claude sessions (file, function, class, etc.)."""

    id: str = Field(..., description="Unique entity identifier")
    name: str = Field(..., description="Entity name")
    entity_type: str = Field(..., description="Type: file, function, class, method, etc.")
    description: str | None = Field(None, description="AI-generated description")
    mention_count: int = Field(0, ge=0, description="Number of session mentions")


class AtlasPattern(BaseModel):
    """A recurring pattern identified across sessions."""

    pattern_id: str = Field(..., description="Unique pattern identifier")
    pattern_type: str = Field(..., description="Type: implementation, bug_fix, refactor, etc.")
    entities: list[str] = Field(default_factory=list, description="Related entity IDs")
    session_count: int = Field(0, ge=0, description="Sessions where pattern appeared")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Confidence score")


class AtlasSignals(BaseModel):
    """Aggregated signals from Code Atlas for decision making."""

    related_patterns: list[AtlasPattern] = Field(
        default_factory=list, description="Patterns matching current context"
    )
    similar_decisions: list[str] = Field(
        default_factory=list, description="IDs of similar past decisions"
    )
    recurring_problems: list[str] = Field(
        default_factory=list, description="Known issues in related code"
    )


class AtlasRAGResponse(BaseModel):
    """Response from a RAG query to Code Atlas."""

    answer: str = Field(..., description="Generated answer")
    sources: list[str] = Field(default_factory=list, description="Source session IDs")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Answer confidence")
    execution_time_ms: int = Field(0, ge=0, description="Query execution time in ms")


# =============================================================================
# Tech Diligence Schemas
# =============================================================================


class FindingSeverity(str, Enum):
    """Severity levels for diligence findings."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingCategory(str, Enum):
    """Categories for diligence findings."""

    SECURITY = "security"
    ARCHITECTURE = "architecture"
    DEPENDENCY = "dependency"
    CODE_QUALITY = "code_quality"


class DiligenceFinding(BaseModel):
    """A single finding from Tech Diligence analysis."""

    finding_id: str = Field(..., description="Unique finding identifier")
    title: str = Field(..., description="Finding title")
    severity: FindingSeverity = Field(..., description="Severity level")
    category: FindingCategory = Field(..., description="Finding category")
    file_path: str | None = Field(None, description="Affected file path")
    line_number: int | None = Field(None, ge=1, description="Affected line number")
    fix_suggestion: str | None = Field(None, description="Suggested fix")
    scanner: str = Field("unknown", description="Scanner that found this issue")


class DiligenceReport(BaseModel):
    """Complete report from Tech Diligence analysis."""

    report_id: str = Field(..., description="Unique report identifier")
    findings: list[DiligenceFinding] = Field(default_factory=list)
    summary_score: float = Field(100.0, ge=0.0, le=100.0, description="Overall score 0-100")
    analysis_time_seconds: float = Field(0.0, ge=0.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DiligenceSignals(BaseModel):
    """Aggregated signals from Tech Diligence for decision making."""

    overall_score: float = Field(100.0, ge=0.0, le=100.0)
    risk_level: str = Field("low", description="low, medium, high, critical")
    blocking_issues: list[DiligenceFinding] = Field(
        default_factory=list, description="Critical/high findings that block"
    )
    warnings: list[DiligenceFinding] = Field(
        default_factory=list, description="Medium/low findings as warnings"
    )


class AnalysisJobStatus(BaseModel):
    """Status of an async Tech Diligence analysis job."""

    job_id: str = Field(..., description="Unique job identifier")
    status: str = Field("pending", description="pending, running, completed, failed")
    progress_percent: int = Field(0, ge=0, le=100)
    report_id: str | None = Field(None, description="Report ID when completed")
    error_message: str | None = Field(None, description="Error message if failed")


# =============================================================================
# Decision Engine Schemas
# =============================================================================


class DecisionAction(str, Enum):
    """Possible actions from the decision engine."""

    PROCEED = "proceed"
    PROCEED_WITH_CAUTION = "proceed_with_caution"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    BLOCK = "block"
    DEFER = "defer"


class DecisionTier(str, Enum):
    """Decision tier for human review - determines notification format.

    WATCH: Binary decisions (approve/reject) with minimal context.
           Suitable for Apple Watch notifications. Low-risk, routine decisions.
           Example: test retry, dependency update, lint fix

    PHONE: Decisions needing brief context but can be made on mobile.
           Suitable for iPhone/Android with summary view.
           Example: feature completion, content publish, minor config change

    DESKTOP: Complex decisions requiring full context review.
             Requires desktop/laptop with full dashboard access.
             Example: security change, breaking API change, deployment to prod
    """

    WATCH = "watch"  # Binary approve/reject, minimal context
    PHONE = "phone"  # Needs summary context, mobile-friendly
    DESKTOP = "desktop"  # Full review required, complex decision


class ConfidenceLevel(str, Enum):
    """Confidence levels for decisions."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class DecisionContext(BaseModel):
    """Context for making a decision."""

    domain: str = Field(..., description="Domain name (e.g., 'codeswiftr-com')")
    project: str = Field(..., description="Project name (e.g., 'interview-simulator')")
    feature_id: str | None = Field(None, description="Feature ID if applicable")
    file_paths: list[str] = Field(default_factory=list, description="Files being modified")
    tags: list[str] = Field(default_factory=list, description="Contextual tags")
    description: str | None = Field(None, description="Human-readable description")


class ScoreCard(BaseModel):
    """Scoring breakdown for a decision."""

    risk_score: float = Field(0.0, ge=0.0, le=1.0, description="Risk level 0-1")
    confidence_score: float = Field(0.0, ge=0.0, le=1.0, description="Confidence 0-1")
    complexity_score: float = Field(0.0, ge=0.0, le=1.0, description="Complexity 0-1")
    precedent_score: float = Field(0.0, ge=0.0, le=1.0, description="Similar precedent 0-1")

    @computed_field
    @property
    def aggregate_score(self) -> float:
        """Weighted aggregate score."""
        weights = {
            "risk": 0.4,
            "confidence": 0.3,
            "complexity": 0.2,
            "precedent": 0.1,
        }
        # Lower risk is better, so invert it
        return (
            (1.0 - self.risk_score) * weights["risk"]
            + self.confidence_score * weights["confidence"]
            + (1.0 - self.complexity_score) * weights["complexity"]
            + self.precedent_score * weights["precedent"]
        )


class Warning(BaseModel):
    """A warning to accompany a decision."""

    code: str = Field(..., description="Warning code for categorization")
    message: str = Field(..., description="Human-readable warning message")
    severity: str = Field("medium", description="low, medium, high")


class DecisionRecommendation(BaseModel):
    """A recommendation from the decision engine."""

    action: DecisionAction = Field(..., description="Recommended action")
    confidence: ConfidenceLevel = Field(..., description="Confidence in recommendation")
    scores: ScoreCard = Field(..., description="Detailed scoring breakdown")
    reasoning: str = Field(..., description="Explanation for the recommendation")
    warnings: list[Warning] = Field(default_factory=list)
    suggested_human_reviewers: list[str] = Field(
        default_factory=list, description="Suggested reviewers if human review needed"
    )


class DecisionOutcome(BaseModel):
    """Recorded outcome of a decision for learning."""

    decision_id: str = Field(..., description="ID of the original decision")
    success: bool = Field(..., description="Whether the action was successful")
    actual_action: DecisionAction = Field(..., description="Action that was taken")
    outcome_description: str | None = Field(None, description="What happened")
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# =============================================================================
# Learning Store Schemas
# =============================================================================


class PatternRecord(BaseModel):
    """A recorded pattern for learning."""

    pattern_id: str = Field(..., description="Unique pattern identifier")
    context_signature: str = Field(..., description="Hash of context characteristics")
    pattern_type: str = Field(..., description="Type of pattern")
    total_applications: int = Field(0, ge=0, description="Times this pattern was applied")
    successful_applications: int = Field(0, ge=0, description="Successful applications")
    last_applied: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field
    @property
    def effectiveness(self) -> float:
        """Calculate effectiveness ratio."""
        if self.total_applications == 0:
            return 0.0
        return self.successful_applications / self.total_applications

    @computed_field
    @property
    def confidence(self) -> float:
        """Calculate confidence based on sample size (50 samples = full confidence)."""
        return min(1.0, self.total_applications / 50)


class DecisionRecord(BaseModel):
    """A recorded decision for learning."""

    decision_id: str = Field(..., description="Unique decision identifier")
    context_signature: str = Field(..., description="Hash of context characteristics")
    domain: str = Field(..., description="Domain where decision was made")
    project: str = Field(..., description="Project where decision was made")
    recommended_action: DecisionAction = Field(..., description="What was recommended")
    actual_action: DecisionAction | None = Field(None, description="What action was taken")
    outcome_success: bool | None = Field(None, description="Whether it succeeded")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    outcome_recorded_at: datetime | None = Field(None)
