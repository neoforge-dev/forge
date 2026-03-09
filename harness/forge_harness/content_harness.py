"""
Content Harness for FORGE MVPs
==============================

Generates marketing content (blog posts, social media, email sequences)
from living-docs context for MVP launches.

V2 Architecture:
- State machine for content lifecycle (pending → generating → draft → review → approved)
- Human-in-the-loop feedback integration
- Storage abstraction (files, Notion)
- Agent loop with stop conditions
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from .living_docs import LivingDocs
from .posthog_tracker import HarnessTracker, NoOpTracker

# Conditional import for Notion storage (optional dependency)
try:
    from .notion_storage import (
        NotionConfig,
        NotionConnectionMode,
        NotionContentStorage,
    )

    NOTION_AVAILABLE = True
except ImportError:
    NOTION_AVAILABLE = False

logger = logging.getLogger(__name__)

ContentType = Literal[
    "blog_posts",
    "social_media",
    "email_sequences",
    "landing_copy",
    "case_studies",
]


class ContentStage(Enum):
    """
    Lifecycle stages for content generation.

    State transitions:
        PENDING → GENERATING → DRAFT → REVIEW → APPROVED → PUBLISHED
                      ↑           ↓
                      └── REVISION ←┘
                            ↓
                         ERROR (terminal from any state)
    """

    PENDING = "pending"  # Brief exists, generation not started
    GENERATING = "generating"  # Claude actively generating content
    DRAFT = "draft"  # Draft complete, ready for review
    REVIEW = "review"  # Under human review
    REVISION = "revision"  # Incorporating human feedback
    APPROVED = "approved"  # Human approved, ready to publish
    PUBLISHED = "published"  # Content is live
    ERROR = "error"  # Generation failed

    def is_terminal(self) -> bool:
        """Check if this is a terminal state."""
        return self in (ContentStage.APPROVED, ContentStage.PUBLISHED, ContentStage.ERROR)

    def can_transition_to(self, target: ContentStage) -> bool:
        """Check if transition to target state is valid."""
        valid_transitions = {
            ContentStage.PENDING: {ContentStage.GENERATING, ContentStage.ERROR},
            ContentStage.GENERATING: {ContentStage.DRAFT, ContentStage.ERROR},
            ContentStage.DRAFT: {ContentStage.REVIEW, ContentStage.ERROR},
            ContentStage.REVIEW: {ContentStage.APPROVED, ContentStage.REVISION, ContentStage.ERROR},
            ContentStage.REVISION: {ContentStage.GENERATING, ContentStage.ERROR},
            ContentStage.APPROVED: {ContentStage.PUBLISHED},
            ContentStage.PUBLISHED: set(),  # Terminal
            ContentStage.ERROR: set(),  # Terminal
        }
        return target in valid_transitions.get(self, set())


@dataclass
class ContentBrief:
    """
    Input specification for content generation.

    Represents a content request before generation begins.
    Can be loaded from Notion, files, or created programmatically.
    """

    id: str
    title: str
    content_type: ContentType
    requirements: str
    audience: str | None = None
    length: str | None = None  # "short", "medium", "long"
    keywords: list[str] = field(default_factory=list)
    tone: str | None = None  # "professional", "casual", "technical"
    extra_context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_spec(cls, brief_id: str, spec: ContentSpec, title: str = "") -> ContentBrief:
        """Create a brief from a ContentSpec."""
        length = None
        if spec.word_range:
            if spec.word_range[1] <= 500:
                length = "short"
            elif spec.word_range[1] <= 1200:
                length = "medium"
            else:
                length = "long"

        return cls(
            id=brief_id,
            title=title or f"{spec.content_type} content",
            content_type=spec.content_type,
            requirements=spec.focus,
            length=length,
            extra_context={
                "count": spec.count,
                "platforms": spec.platforms,
                "sections": spec.sections,
            },
        )


@dataclass
class ContentItem:
    """
    State container for content generation.

    Tracks the full lifecycle of a content piece from brief to publication.
    """

    brief_id: str
    content_type: ContentType
    stage: ContentStage = ContentStage.PENDING
    generated_content: str | None = None
    human_feedback: str | None = None
    revision_round: int = 0
    errors: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def transition_to(self, new_stage: ContentStage) -> bool:
        """
        Attempt to transition to a new stage.

        Returns:
            True if transition succeeded, False if invalid
        """
        if self.stage.can_transition_to(new_stage):
            self.stage = new_stage
            self.updated_at = datetime.now()
            return True
        return False

    def add_error(self, error: str) -> None:
        """Record an error."""
        self.errors.append(error)
        self.updated_at = datetime.now()

    def add_feedback(self, feedback: str) -> None:
        """Record human feedback and increment revision round."""
        self.human_feedback = feedback
        self.revision_round += 1
        self.updated_at = datetime.now()

    def is_complete(self) -> bool:
        """Check if content generation is complete (success or failure)."""
        return self.stage.is_terminal()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for storage/logging."""
        return {
            "brief_id": self.brief_id,
            "content_type": self.content_type,
            "stage": self.stage.value,
            "generated_content": self.generated_content,
            "human_feedback": self.human_feedback,
            "revision_round": self.revision_round,
            "errors": self.errors,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class GenerationConfig:
    """
    Configuration for content generation loop.

    Controls iteration limits, timeouts, and behavior.
    """

    max_iterations: int = 5  # Max revision rounds
    poll_timeout: float = 60.0  # Seconds to wait for feedback
    poll_interval: float = 5.0  # Seconds between feedback checks
    error_threshold: int = 3  # Errors before giving up
    auto_approve: bool = False  # Skip feedback loop (for batch jobs)
    model: str = "claude-sonnet-4-20250514"  # Model for generation
    dry_run: bool = False  # If True, don't actually generate


# =============================================================================
# V2: Storage Abstraction
# =============================================================================


@runtime_checkable
class ContentStorage(Protocol):
    """
    Protocol for content storage backends.

    Implementations can store content in files, Notion, databases, etc.
    """

    async def save_item(self, item: ContentItem) -> str:
        """
        Save a content item.

        Args:
            item: ContentItem to save

        Returns:
            Storage identifier (path, page ID, etc.)
        """
        ...

    async def load_item(self, brief_id: str) -> ContentItem | None:
        """
        Load a content item by brief ID.

        Args:
            brief_id: Brief identifier

        Returns:
            ContentItem if found, None otherwise
        """
        ...

    async def save_brief(self, brief: ContentBrief) -> str:
        """
        Save a content brief.

        Args:
            brief: ContentBrief to save

        Returns:
            Storage identifier
        """
        ...

    async def load_brief(self, brief_id: str) -> ContentBrief | None:
        """
        Load a content brief by ID.

        Args:
            brief_id: Brief identifier

        Returns:
            ContentBrief if found, None otherwise
        """
        ...

    async def get_feedback(self, brief_id: str) -> str | None:
        """
        Get human feedback for a brief.

        Args:
            brief_id: Brief identifier

        Returns:
            Feedback string if available, None otherwise
        """
        ...

    async def clear_feedback(self, brief_id: str) -> None:
        """
        Clear feedback after processing.

        Args:
            brief_id: Brief identifier
        """
        ...


class FileContentStorage:
    """
    File-based content storage implementation.

    Stores briefs, items, and content as JSON/markdown files.
    """

    def __init__(self, base_dir: Path):
        """
        Initialize file storage.

        Args:
            base_dir: Base directory for storage
        """
        self.base_dir = Path(base_dir)
        self.briefs_dir = self.base_dir / "briefs"
        self.items_dir = self.base_dir / "items"
        self.feedback_dir = self.base_dir / "feedback"
        self.content_dir = self.base_dir / "content"

        # Ensure directories exist
        for d in [self.briefs_dir, self.items_dir, self.feedback_dir, self.content_dir]:
            d.mkdir(parents=True, exist_ok=True)

    async def save_item(self, item: ContentItem) -> str:
        """Save a content item to JSON file."""
        file_path = self.items_dir / f"{item.brief_id}.json"
        data = item.to_dict()
        file_path.write_text(json.dumps(data, indent=2, default=str))

        # Also save generated content as markdown if available
        if item.generated_content:
            content_path = self.content_dir / f"{item.brief_id}.md"
            content_path.write_text(item.generated_content)

        return str(file_path)

    async def load_item(self, brief_id: str) -> ContentItem | None:
        """Load a content item from JSON file."""
        file_path = self.items_dir / f"{brief_id}.json"
        if not file_path.exists():
            return None

        try:
            data = json.loads(file_path.read_text())
            return ContentItem(
                brief_id=data["brief_id"],
                content_type=data["content_type"],
                stage=ContentStage(data["stage"]),
                generated_content=data.get("generated_content"),
                human_feedback=data.get("human_feedback"),
                revision_round=data.get("revision_round", 0),
                errors=data.get("errors", []),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to load item {brief_id}: {e}")
            return None

    async def save_brief(self, brief: ContentBrief) -> str:
        """Save a content brief to JSON file."""
        file_path = self.briefs_dir / f"{brief.id}.json"
        data = {
            "id": brief.id,
            "title": brief.title,
            "content_type": brief.content_type,
            "requirements": brief.requirements,
            "audience": brief.audience,
            "length": brief.length,
            "keywords": brief.keywords,
            "tone": brief.tone,
            "extra_context": brief.extra_context,
        }
        file_path.write_text(json.dumps(data, indent=2))
        return str(file_path)

    async def load_brief(self, brief_id: str) -> ContentBrief | None:
        """Load a content brief from JSON file."""
        file_path = self.briefs_dir / f"{brief_id}.json"
        if not file_path.exists():
            return None

        try:
            data = json.loads(file_path.read_text())
            return ContentBrief(
                id=data["id"],
                title=data["title"],
                content_type=data["content_type"],
                requirements=data["requirements"],
                audience=data.get("audience"),
                length=data.get("length"),
                keywords=data.get("keywords", []),
                tone=data.get("tone"),
                extra_context=data.get("extra_context", {}),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to load brief {brief_id}: {e}")
            return None

    async def get_feedback(self, brief_id: str) -> str | None:
        """Get feedback from feedback file."""
        file_path = self.feedback_dir / f"{brief_id}.txt"
        if not file_path.exists():
            return None
        return file_path.read_text().strip() or None

    async def clear_feedback(self, brief_id: str) -> None:
        """Delete feedback file after processing."""
        file_path = self.feedback_dir / f"{brief_id}.txt"
        if file_path.exists():
            file_path.unlink()


@dataclass
class ContentSpec:
    """Specification for content generation."""

    content_type: ContentType
    count: int = 1
    word_range: tuple[int, int] | None = None
    platforms: list[str] | None = None
    sections: list[str] | None = None
    focus: str = ""


@dataclass
class ContentResult:
    """Result of content generation."""

    content_type: ContentType
    pieces_generated: int
    output_dir: Path
    word_count: int
    files_created: list[str]
    # V2 additions
    stage: ContentStage = ContentStage.PENDING
    items: list[ContentItem] = field(default_factory=list)
    revision_rounds: int = 0


# Standard content specifications
CONTENT_SPECS: dict[ContentType, ContentSpec] = {
    "blog_posts": ContentSpec(
        content_type="blog_posts",
        count=10,
        word_range=(800, 1500),
        focus="problem-solution narratives with SEO keywords",
    ),
    "social_media": ContentSpec(
        content_type="social_media",
        count=20,
        platforms=["twitter", "linkedin"],
        focus="hooks, engagement, CTAs",
    ),
    "email_sequences": ContentSpec(
        content_type="email_sequences",
        count=5,
        sections=["welcome", "value_1", "value_2", "social_proof", "cta"],
        focus="nurture sequence with progressive CTAs",
    ),
    "landing_copy": ContentSpec(
        content_type="landing_copy",
        count=1,
        sections=["hero", "problem", "solution", "features", "testimonials", "cta"],
        focus="conversion-optimized copy",
    ),
    "case_studies": ContentSpec(
        content_type="case_studies",
        count=3,
        sections=["challenge", "solution", "results", "testimonial"],
        focus="quantified outcomes",
    ),
}


# =============================================================================
# Content Priority Tiers
# =============================================================================


class ContentTier(Enum):
    """
    Content priority tiers for FORGE portfolio.

    Tier 1: Primary revenue focus (60% allocation)
    Tier 2: Growth candidates (25% allocation)
    Tier 3: Maintenance only (15% allocation)
    """

    TIER_1 = 1  # 60% - Full content library
    TIER_2 = 2  # 25% - Targeted content
    TIER_3 = 3  # 15% - Minimal content


@dataclass
class TierAllocation:
    """Content allocation for a tier."""

    tier: ContentTier
    percentage: int  # Percentage of total effort
    blog_posts: int
    social_media: int
    email_sequences: int
    landing_copy: int
    case_studies: int
    description: str


# Tier allocations define content counts per tier
TIER_ALLOCATIONS: dict[ContentTier, TierAllocation] = {
    ContentTier.TIER_1: TierAllocation(
        tier=ContentTier.TIER_1,
        percentage=60,
        blog_posts=10,
        social_media=20,
        email_sequences=5,
        landing_copy=1,
        case_studies=3,
        description="Full content library (50+ pieces)",
    ),
    ContentTier.TIER_2: TierAllocation(
        tier=ContentTier.TIER_2,
        percentage=25,
        blog_posts=5,
        social_media=10,
        email_sequences=3,
        landing_copy=1,
        case_studies=1,
        description="Targeted content (20-30 pieces)",
    ),
    ContentTier.TIER_3: TierAllocation(
        tier=ContentTier.TIER_3,
        percentage=15,
        blog_posts=2,
        social_media=5,
        email_sequences=1,
        landing_copy=1,
        case_studies=0,
        description="Maintenance only (1 lead magnet)",
    ),
}


def _build_domain_tiers() -> dict[str, ContentTier]:
    """
    Build domain tiers from DomainRegistry.

    Falls back to hardcoded defaults if registry unavailable.
    """
    try:
        from .domain_registry import DomainRegistry

        DomainRegistry._ensure_loaded()
        tiers = {}
        for entry in DomainRegistry.all():
            if entry.content:
                tier_num = entry.content.tier
                if tier_num == 1:
                    tiers[entry.key] = ContentTier.TIER_1
                elif tier_num == 2:
                    tiers[entry.key] = ContentTier.TIER_2
                else:
                    tiers[entry.key] = ContentTier.TIER_3
        return tiers
    except Exception:
        # Fallback to hardcoded if registry fails
        return {
            "codeswiftr-com": ContentTier.TIER_1,
            "leanvibe-dev": ContentTier.TIER_2,
            "neoforge-dev": ContentTier.TIER_2,
        }


# Domain to tier mapping (loaded from DomainRegistry)
DOMAIN_TIERS: dict[str, ContentTier] = _build_domain_tiers()


def get_domain_tier(domain: str) -> ContentTier:
    """
    Get the content tier for a domain.

    Args:
        domain: FORGE domain name

    Returns:
        ContentTier for the domain (defaults to TIER_3)
    """
    # Always try registry first for fresh data
    try:
        from .domain_registry import DomainRegistry

        DomainRegistry._ensure_loaded()
        entry = DomainRegistry.get_or_none(domain)
        if entry and entry.content:
            tier_num = entry.content.tier
            if tier_num == 1:
                return ContentTier.TIER_1
            elif tier_num == 2:
                return ContentTier.TIER_2
            else:
                return ContentTier.TIER_3
    except Exception:
        pass

    # Fallback to DOMAIN_TIERS
    return DOMAIN_TIERS.get(domain, ContentTier.TIER_3)


def get_tier_allocation(tier: ContentTier) -> TierAllocation:
    """
    Get content allocation for a tier.

    Args:
        tier: ContentTier

    Returns:
        TierAllocation with content counts
    """
    return TIER_ALLOCATIONS[tier]


def get_content_allocation(domain: str) -> TierAllocation:
    """
    Get content allocation for a domain based on its tier.

    Args:
        domain: FORGE domain name

    Returns:
        TierAllocation with adjusted content counts

    Example:
        allocation = get_content_allocation("codeswiftr-com")
        print(f"Blog posts: {allocation.blog_posts}")  # 10
    """
    tier = get_domain_tier(domain)
    return get_tier_allocation(tier)


def get_tier_adjusted_spec(content_type: ContentType, domain: str) -> ContentSpec:
    """
    Get a content spec adjusted for domain's tier allocation.

    Args:
        content_type: Type of content
        domain: FORGE domain name

    Returns:
        ContentSpec with count adjusted for tier
    """
    base_spec = CONTENT_SPECS[content_type]
    allocation = get_content_allocation(domain)

    # Map content type to allocation field
    count_map = {
        "blog_posts": allocation.blog_posts,
        "social_media": allocation.social_media,
        "email_sequences": allocation.email_sequences,
        "landing_copy": allocation.landing_copy,
        "case_studies": allocation.case_studies,
    }

    adjusted_count = count_map.get(content_type, base_spec.count)

    return ContentSpec(
        content_type=base_spec.content_type,
        count=adjusted_count,
        word_range=base_spec.word_range,
        platforms=base_spec.platforms,
        sections=base_spec.sections,
        focus=base_spec.focus,
    )


def get_all_priorities() -> list[dict]:
    """
    Get content priorities for all domains.

    Returns:
        List of priority dictionaries with domain, tier, and allocation info
    """
    priorities = []

    # Group domains by tier
    for tier in ContentTier:
        allocation = get_tier_allocation(tier)
        domains = [d for d, t in DOMAIN_TIERS.items() if t == tier]

        # Add explicit domains
        for domain in domains:
            priorities.append(
                {
                    "domain": domain,
                    "tier": tier.value,
                    "tier_name": f"Tier {tier.value}",
                    "percentage": allocation.percentage,
                    "description": allocation.description,
                    "blog_posts": allocation.blog_posts,
                    "social_media": allocation.social_media,
                    "email_sequences": allocation.email_sequences,
                    "total_pieces": (
                        allocation.blog_posts
                        + allocation.social_media
                        + allocation.email_sequences
                        + allocation.landing_copy
                        + allocation.case_studies
                    ),
                }
            )

    # Sort by tier (Tier 1 first)
    priorities.sort(key=lambda x: x["tier"])
    return priorities


@dataclass
class CalendarEntry:
    """A single content calendar entry."""

    date: str  # ISO date string
    day_of_week: str
    domain: str
    content_type: ContentType
    title_suggestion: str
    tier: int


def generate_content_calendar(
    weeks: int = 4,
    start_date: datetime | None = None,
) -> list[CalendarEntry]:
    """
    Generate a content calendar based on tier allocations.

    Creates a weekly schedule distributing content across tiers:
    - Tier 1: 3 pieces/week (Mon, Wed, Fri)
    - Tier 2: 2 pieces/week (Tue, Thu)
    - Tier 3: 1 piece/week (Wed - alternating domains)

    Args:
        weeks: Number of weeks to generate
        start_date: Starting date (defaults to next Monday)

    Returns:
        List of CalendarEntry objects
    """
    from datetime import timedelta

    if start_date is None:
        # Start from next Monday
        today = datetime.now()
        days_until_monday = (7 - today.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        start_date = today + timedelta(days=days_until_monday)
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

    calendar: list[CalendarEntry] = []
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    # Get domains by tier
    tier_1_domains = [d for d, t in DOMAIN_TIERS.items() if t == ContentTier.TIER_1]
    tier_2_domains = [d for d, t in DOMAIN_TIERS.items() if t == ContentTier.TIER_2]

    # Content type rotation for each tier
    tier_1_types: list[ContentType] = ["blog_posts", "social_media", "blog_posts"]
    tier_2_types: list[ContentType] = ["blog_posts", "social_media"]

    tier_2_idx = 0

    for week in range(weeks):
        week_start = start_date + timedelta(weeks=week)

        # Monday: Tier 1 blog post
        if tier_1_domains:
            domain = tier_1_domains[0]
            calendar.append(
                CalendarEntry(
                    date=(week_start).isoformat()[:10],
                    day_of_week=day_names[0],
                    domain=domain,
                    content_type=tier_1_types[0],
                    title_suggestion=f"[{domain}] Blog: Core topic #{week * 3 + 1}",
                    tier=1,
                )
            )

        # Tuesday: Tier 2 content
        if tier_2_domains:
            domain = tier_2_domains[tier_2_idx % len(tier_2_domains)]
            calendar.append(
                CalendarEntry(
                    date=(week_start + timedelta(days=1)).isoformat()[:10],
                    day_of_week=day_names[1],
                    domain=domain,
                    content_type=tier_2_types[0],
                    title_suggestion=f"[{domain}] Blog: Focus topic #{week + 1}",
                    tier=2,
                )
            )

        # Wednesday: Tier 1 social + Tier 3 (alternating weeks)
        if tier_1_domains:
            domain = tier_1_domains[0]
            calendar.append(
                CalendarEntry(
                    date=(week_start + timedelta(days=2)).isoformat()[:10],
                    day_of_week=day_names[2],
                    domain=domain,
                    content_type=tier_1_types[1],
                    title_suggestion=f"[{domain}] Social: Engagement post #{week * 3 + 2}",
                    tier=1,
                )
            )

        # Thursday: Tier 2 social
        if tier_2_domains:
            domain = tier_2_domains[(tier_2_idx + 1) % len(tier_2_domains)]
            calendar.append(
                CalendarEntry(
                    date=(week_start + timedelta(days=3)).isoformat()[:10],
                    day_of_week=day_names[3],
                    domain=domain,
                    content_type=tier_2_types[1],
                    title_suggestion=f"[{domain}] Social: Tips post #{week + 1}",
                    tier=2,
                )
            )
            tier_2_idx += 1

        # Friday: Tier 1 blog post
        if tier_1_domains:
            domain = tier_1_domains[0]
            calendar.append(
                CalendarEntry(
                    date=(week_start + timedelta(days=4)).isoformat()[:10],
                    day_of_week=day_names[4],
                    domain=domain,
                    content_type=tier_1_types[2],
                    title_suggestion=f"[{domain}] Blog: Weekly roundup #{week + 1}",
                    tier=1,
                )
            )

    return calendar


def format_calendar_markdown(calendar: list[CalendarEntry]) -> str:
    """
    Format calendar entries as markdown.

    Args:
        calendar: List of CalendarEntry objects

    Returns:
        Markdown-formatted calendar string
    """
    if not calendar:
        return "No calendar entries."

    lines = ["# Content Calendar", ""]

    # Group by week
    current_week = None
    for entry in calendar:
        week_num = datetime.fromisoformat(entry.date).isocalendar()[1]

        if week_num != current_week:
            current_week = week_num
            lines.append(f"## Week {week_num}")
            lines.append("")

        tier_badge = f"[Tier {entry.tier}]"
        lines.append(
            f"- **{entry.day_of_week} ({entry.date})**: {tier_badge} {entry.title_suggestion}"
        )

    return "\n".join(lines)


class ContentHarness:
    """
    Generate marketing content from living-docs context.

    Usage:
        harness = ContentHarness(
            domain="codeswiftr-com",
            project="interview-simulator",
            forge_root=Path("/Users/dev/FORGE")
        )

        results = await harness.generate_content_library(
            content_types=["blog_posts", "social_media"]
        )
    """

    def __init__(
        self,
        domain: str,
        project: str,
        forge_root: Path,
    ):
        """
        Initialize the content harness.

        Args:
            domain: FORGE domain name
            project: Project name within domain
            forge_root: Path to FORGE repository root
        """
        self.domain = domain
        self.project = project
        self.forge_root = Path(forge_root)
        self.project_dir = self.forge_root / domain / project
        self.living_docs = LivingDocs(forge_root)

    def get_project_context(self) -> dict[str, Any]:
        """
        Load project context from living-docs.

        Returns:
            Dict with value_proposition, target_audience, features, differentiators
        """
        docs_context = self.living_docs.consult(self.domain, self.project)

        return {
            "domain": self.domain,
            "project": self.project,
            "current_sprint": docs_context.current_sprint,
            "priorities": docs_context.priorities,
            "recent_milestones": docs_context.recent_milestones,
            "value_proposition": self._extract_value_proposition(docs_context),
            "target_audience": self._extract_target_audience(docs_context),
            "features": self._extract_features(docs_context),
            "differentiators": self._extract_differentiators(docs_context),
        }

    def _extract_value_proposition(self, docs_context: Any) -> str:
        """Extract value proposition from context."""
        # Check active_context for value prop
        if hasattr(docs_context, "active_context") and docs_context.active_context:
            ctx = docs_context.active_context
            # Handle both string and object types
            ctx_str = str(ctx) if not isinstance(ctx, str) else ctx
            if "value" in ctx_str.lower() or "benefit" in ctx_str.lower():
                return ctx_str

        # Fall back to priorities
        if docs_context.priorities:
            return docs_context.priorities[0]

        return f"Solve problems in {self.project.replace('-', ' ')}"

    def _extract_target_audience(self, docs_context: Any) -> str:
        """Extract target audience from context."""
        # Check for audience in active_context
        if hasattr(docs_context, "active_context") and docs_context.active_context:
            ctx = docs_context.active_context
            ctx_str = str(ctx) if not isinstance(ctx, str) else ctx
            if "user" in ctx_str.lower():
                return ctx_str

        # Default based on project name
        project_words = self.project.replace("-", " ").title()
        return f"Professionals looking for {project_words} solutions"

    def _extract_features(self, docs_context: Any) -> list[str]:
        """Extract features from context."""
        features = []

        # Extract from priorities
        for priority in (docs_context.priorities or [])[:5]:
            features.append(priority)

        # Extract from recent milestones
        for milestone in (docs_context.recent_milestones or [])[:3]:
            if "feature" in milestone.lower() or "add" in milestone.lower():
                features.append(milestone)

        return features[:5] if features else ["Core functionality", "User-friendly interface"]

    def _extract_differentiators(self, docs_context: Any) -> list[str]:
        """Extract differentiators from context."""
        # Default differentiators based on FORGE tech stack
        return [
            "Built with modern FastAPI + React stack",
            "Enterprise-grade security",
            "Clean, intuitive interface",
        ]

    def build_content_prompt(
        self,
        content_type: ContentType,
        spec: ContentSpec,
        project_context: dict[str, Any],
    ) -> str:
        """
        Build comprehensive content generation prompt.

        Args:
            content_type: Type of content to generate
            spec: Content specification
            project_context: Project context from living-docs

        Returns:
            Prompt string for Claude
        """
        spec_details = []
        if spec.count:
            spec_details.append(f"Count: {spec.count} pieces")
        if spec.word_range:
            spec_details.append(f"Word range: {spec.word_range[0]}-{spec.word_range[1]} words each")
        if spec.platforms:
            spec_details.append(f"Platforms: {', '.join(spec.platforms)}")
        if spec.sections:
            spec_details.append(f"Sections: {', '.join(spec.sections)}")
        spec_details.append(f"Focus: {spec.focus}")

        features_str = "\n".join(f"- {f}" for f in project_context.get("features", []))
        differentiators_str = "\n".join(
            f"- {d}" for d in project_context.get("differentiators", [])
        )

        return f"""## CONTENT GENERATION TASK

**Content Type:** {content_type}
**Project:** {self.project}
**Domain:** {self.domain}

### PROJECT CONTEXT

**Value Proposition:**
{project_context.get("value_proposition", "TBD")}

**Target Audience:**
{project_context.get("target_audience", "TBD")}

**Key Features:**
{features_str}

**Differentiators:**
{differentiators_str}

### CONTENT SPECIFICATIONS

{chr(10).join(spec_details)}

### REQUIREMENTS

1. Use problem-aware language (focus on pain points)
2. Include specific, quantified benefits where possible
3. Maintain brand voice: professional but approachable
4. Optimize for the target platform/channel
5. Include clear calls-to-action
6. Follow SEO best practices for blog content
7. Keep social media posts concise and engaging

### OUTPUT FORMAT

Generate content as markdown with clear section headers.
Each piece should be clearly separated with "---" dividers.
"""

    def get_content_spec(self, content_type: ContentType) -> ContentSpec:
        """
        Get the content specification for a type.

        Args:
            content_type: Type of content

        Returns:
            ContentSpec for the type
        """
        return CONTENT_SPECS.get(content_type, ContentSpec(content_type=content_type))

    # =========================================================================
    # V2: State Machine Methods
    # =========================================================================

    def create_content_item(
        self,
        brief: ContentBrief,
    ) -> ContentItem:
        """
        Create a new ContentItem from a brief.

        Args:
            brief: The content brief to generate from

        Returns:
            New ContentItem in PENDING state
        """
        return ContentItem(
            brief_id=brief.id,
            content_type=brief.content_type,
            stage=ContentStage.PENDING,
            metadata={
                "title": brief.title,
                "audience": brief.audience,
                "tone": brief.tone,
            },
        )

    def create_brief(
        self,
        brief_id: str,
        content_type: ContentType,
        title: str = "",
        requirements: str = "",
        **kwargs: Any,
    ) -> ContentBrief:
        """
        Create a ContentBrief with project context.

        Args:
            brief_id: Unique identifier for this brief
            content_type: Type of content to generate
            title: Brief title
            requirements: Specific requirements
            **kwargs: Additional brief fields (audience, tone, keywords, etc.)

        Returns:
            ContentBrief with project context
        """
        spec = self.get_content_spec(content_type)
        context = self.get_project_context()

        return ContentBrief(
            id=brief_id,
            title=title or f"{content_type} for {self.project}",
            content_type=content_type,
            requirements=requirements or spec.focus,
            audience=kwargs.get("audience", context.get("target_audience")),
            length=kwargs.get("length"),
            keywords=kwargs.get("keywords", []),
            tone=kwargs.get("tone", "professional"),
            extra_context={
                "spec": {
                    "count": spec.count,
                    "word_range": spec.word_range,
                    "platforms": spec.platforms,
                    "sections": spec.sections,
                },
                "project_context": context,
            },
        )

    def should_stop(
        self,
        item: ContentItem,
        config: GenerationConfig,
        iteration: int = 0,
    ) -> bool:
        """
        Check if the generation loop should stop.

        Args:
            item: Current content item state
            config: Generation configuration
            iteration: Current iteration number

        Returns:
            True if loop should stop
        """
        # Terminal states always stop
        if item.is_complete():
            return True

        # Max iterations reached
        if iteration >= config.max_iterations:
            return True

        # Error threshold exceeded
        if len(item.errors) >= config.error_threshold:
            return True

        # Auto-approve mode (no feedback loop)
        if config.auto_approve and item.stage == ContentStage.DRAFT:
            return True

        return False

    def get_stop_reason(
        self,
        item: ContentItem,
        config: GenerationConfig,
        iteration: int = 0,
    ) -> str:
        """
        Get the reason why generation stopped.

        Args:
            item: Current content item state
            config: Generation configuration
            iteration: Current iteration number

        Returns:
            Human-readable stop reason
        """
        if item.stage == ContentStage.APPROVED:
            return "Content approved by human"
        if item.stage == ContentStage.PUBLISHED:
            return "Content published"
        if item.stage == ContentStage.ERROR:
            return f"Error: {item.errors[-1] if item.errors else 'Unknown error'}"
        if iteration >= config.max_iterations:
            return f"Max iterations ({config.max_iterations}) reached"
        if len(item.errors) >= config.error_threshold:
            return f"Error threshold ({config.error_threshold}) exceeded"
        if config.auto_approve and item.stage == ContentStage.DRAFT:
            return "Auto-approved (no feedback loop)"
        return "Unknown"

    def build_prompt_with_feedback(
        self,
        brief: ContentBrief,
        item: ContentItem,
        project_context: dict[str, Any] | None = None,
    ) -> str:
        """
        Build content generation prompt including any feedback.

        Args:
            brief: The content brief
            item: Current content item state (may have feedback)
            project_context: Optional pre-loaded project context

        Returns:
            Complete prompt string for Claude
        """
        if project_context is None:
            project_context = self.get_project_context()

        spec = self.get_content_spec(brief.content_type)
        base_prompt = self.build_content_prompt(brief.content_type, spec, project_context)

        # Add brief-specific details
        brief_section = f"""
### BRIEF DETAILS

**Title:** {brief.title}
**Requirements:** {brief.requirements}
"""
        if brief.audience:
            brief_section += f"**Target Audience:** {brief.audience}\n"
        if brief.tone:
            brief_section += f"**Tone:** {brief.tone}\n"
        if brief.keywords:
            brief_section += f"**Keywords:** {', '.join(brief.keywords)}\n"
        if brief.length:
            brief_section += f"**Length:** {brief.length}\n"

        prompt = base_prompt + brief_section

        # Add feedback if this is a revision
        if item.human_feedback and item.revision_round > 0:
            prompt += f"""
### REVISION REQUIRED (Round {item.revision_round})

**Human Feedback:**
{item.human_feedback}

**Instructions:**
- Carefully incorporate ALL feedback points
- Preserve what was working well
- Make targeted changes based on feedback
- Do not make unnecessary changes beyond the feedback
"""

        # Add previous content for reference in revisions
        if item.generated_content and item.revision_round > 0:
            prompt += f"""
### PREVIOUS DRAFT (for reference)

```
{item.generated_content[:2000]}{"..." if len(item.generated_content) > 2000 else ""}
```
"""

        return prompt

    def is_approval(self, feedback: str | None) -> bool:
        """
        Check if feedback indicates approval.

        Args:
            feedback: Human feedback string

        Returns:
            True if feedback indicates approval
        """
        if not feedback:
            return False

        feedback_lower = feedback.lower().strip()
        approval_phrases = [
            "approved",
            "lgtm",
            "looks good",
            "ship it",
            "publish",
            "accept",
            "good to go",
            "approve",
        ]
        return any(phrase in feedback_lower for phrase in approval_phrases)

    def prepare_output_directory(
        self,
        content_type: ContentType,
        output_dir: Path | None = None,
    ) -> Path:
        """
        Prepare output directory for content.

        Args:
            content_type: Type of content
            output_dir: Base output directory (defaults to project/content)

        Returns:
            Path to the content type directory
        """
        if output_dir is None:
            output_dir = self.project_dir / "content"

        type_dir = output_dir / content_type
        type_dir.mkdir(parents=True, exist_ok=True)

        return type_dir

    def save_content(
        self,
        output_dir: Path,
        content_type: ContentType,
        content: str,
    ) -> list[str]:
        """
        Save generated content to files.

        Args:
            output_dir: Directory to save to
            content_type: Type of content
            content: Generated content string

        Returns:
            List of created file paths
        """
        files_created = []

        # Split content by dividers
        pieces = [p.strip() for p in content.split("---") if p.strip()]

        if not pieces:
            # Single file if no dividers
            file_path = output_dir / f"{content_type}.md"
            file_path.write_text(content)
            files_created.append(str(file_path))
        else:
            # Multiple files
            for i, piece in enumerate(pieces, 1):
                file_path = output_dir / f"{content_type}_{i:02d}.md"
                file_path.write_text(piece)
                files_created.append(str(file_path))

        return files_created

    # =========================================================================
    # V2: Agent Loop Methods
    # =========================================================================

    async def generate_with_feedback_loop(
        self,
        brief: ContentBrief,
        storage: ContentStorage,
        config: GenerationConfig | None = None,
        generator: Any | None = None,
        tracker: HarnessTracker | None = None,
    ) -> ContentItem:
        """
        Main agent loop for content generation with human feedback.

        This implements the full Notion Content Machine pattern:
        1. Generate initial draft
        2. Save draft and wait for feedback
        3. Process feedback (approve or revise)
        4. Loop until stop condition met

        Args:
            brief: Content brief to generate from
            storage: Storage backend for persistence
            config: Generation configuration (defaults to standard config)
            generator: Optional content generator (for testing/dry-run)
            tracker: Optional PostHog tracker for analytics

        Returns:
            Final ContentItem (approved, published, or error)
        """
        if config is None:
            config = GenerationConfig()

        # Use no-op tracker if none provided
        if tracker is None:
            tracker = NoOpTracker()

        # Initialize content item
        item = self.create_content_item(brief)
        iteration = 0
        project_context = self.get_project_context()
        start_time = datetime.now()

        logger.info(f"Starting generation loop for brief '{brief.id}': {brief.title}")

        # Track generation started
        tracker.content_generation_started(
            brief_id=brief.id,
            content_type=brief.content_type,
            title=brief.title,
            auto_approve=config.auto_approve,
        )

        # Save initial state
        await storage.save_brief(brief)
        await storage.save_item(item)

        while not self.should_stop(item, config, iteration):
            try:
                # Transition to GENERATING
                if item.stage == ContentStage.PENDING or item.stage == ContentStage.REVISION:
                    item.transition_to(ContentStage.GENERATING)
                    await storage.save_item(item)
                    logger.debug(f"Iteration {iteration}: Generating content...")

                # Generate content
                prompt = self.build_prompt_with_feedback(brief, item, project_context)
                content = await self._generate_content(
                    prompt=prompt,
                    config=config,
                    generator=generator,
                )

                # Update item with generated content
                item.generated_content = content
                item.transition_to(ContentStage.DRAFT)
                await storage.save_item(item)
                logger.info(f"Draft generated (iteration {iteration}, {len(content)} chars)")

                # Auto-approve mode: skip feedback loop
                if config.auto_approve:
                    item.transition_to(ContentStage.REVIEW)
                    item.transition_to(ContentStage.APPROVED)
                    await storage.save_item(item)
                    logger.info("Auto-approved (config.auto_approve=True)")
                    tracker.content_approved(
                        brief_id=brief.id,
                        content_type=brief.content_type,
                        approval_method="auto_approve",
                        revision_rounds=item.revision_round,
                    )
                    break

                # Transition to REVIEW and wait for feedback
                item.transition_to(ContentStage.REVIEW)
                await storage.save_item(item)

                # Poll for human feedback
                feedback = await self._poll_for_feedback(
                    brief_id=brief.id,
                    storage=storage,
                    timeout=config.poll_timeout,
                    interval=config.poll_interval,
                )

                if feedback:
                    await storage.clear_feedback(brief.id)

                    # Check if it's an approval
                    if self.is_approval(feedback):
                        item.transition_to(ContentStage.APPROVED)
                        await storage.save_item(item)
                        logger.info(f"Content approved: {feedback}")
                        tracker.content_approved(
                            brief_id=brief.id,
                            content_type=brief.content_type,
                            approval_method="human",
                            revision_rounds=item.revision_round,
                        )
                        break
                    else:
                        # It's a revision request
                        item.add_feedback(feedback)
                        item.transition_to(ContentStage.REVISION)
                        await storage.save_item(item)
                        logger.info(
                            f"Revision requested (round {item.revision_round}): {feedback[:100]}..."
                        )
                        tracker.content_revision_requested(
                            brief_id=brief.id,
                            content_type=brief.content_type,
                            revision_round=item.revision_round,
                            feedback_preview=feedback,
                        )
                else:
                    # No feedback received within timeout
                    logger.warning(f"No feedback received within {config.poll_timeout}s timeout")
                    # Stay in REVIEW state for manual intervention
                    break

                iteration += 1

            except Exception as e:
                error_msg = f"Generation error: {str(e)}"
                logger.error(error_msg)
                item.add_error(error_msg)

                if len(item.errors) >= config.error_threshold:
                    item.transition_to(ContentStage.ERROR)
                    await storage.save_item(item)
                    break

                # Retry after error
                iteration += 1

        # Log final state
        stop_reason = self.get_stop_reason(item, config, iteration)
        logger.info(f"Generation complete: {stop_reason}")
        logger.info(f"Final state: {item.stage.value}, revisions: {item.revision_round}")

        # Track generation completed
        duration_seconds = (datetime.now() - start_time).total_seconds()
        word_count = len(item.generated_content.split()) if item.generated_content else 0
        tracker.content_generation_completed(
            brief_id=brief.id,
            content_type=brief.content_type,
            final_stage=item.stage.value,
            revision_rounds=item.revision_round,
            duration_seconds=duration_seconds,
            word_count=word_count,
            error_count=len(item.errors),
        )

        return item

    async def _poll_for_feedback(
        self,
        brief_id: str,
        storage: ContentStorage,
        timeout: float,
        interval: float,
    ) -> str | None:
        """
        Poll storage for human feedback.

        Args:
            brief_id: Brief identifier
            storage: Storage backend
            timeout: Max seconds to wait
            interval: Seconds between polls

        Returns:
            Feedback string if received, None if timeout
        """
        elapsed = 0.0
        while elapsed < timeout:
            feedback = await storage.get_feedback(brief_id)
            if feedback:
                return feedback

            await asyncio.sleep(interval)
            elapsed += interval
            logger.debug(f"Polling for feedback ({elapsed:.1f}s / {timeout:.1f}s)...")

        return None

    async def _generate_content(
        self,
        prompt: str,
        config: GenerationConfig,
        generator: Any | None = None,
    ) -> str:
        """
        Generate content using Claude or provided generator.

        Args:
            prompt: Generation prompt
            config: Generation configuration
            generator: Optional generator function (for testing)

        Returns:
            Generated content string
        """
        # Dry run mode: return placeholder
        if config.dry_run:
            return f"[DRY RUN] Content for prompt:\n\n{prompt[:500]}..."

        # Custom generator (for testing or alternative backends)
        if generator is not None:
            if asyncio.iscoroutinefunction(generator):
                return await generator(prompt, config)
            return generator(prompt, config)

        # Default: Use Anthropic SDK
        try:
            import os

            import anthropic

            # Check for Z.AI compatible endpoint first, then Anthropic
            z_ai_token = os.environ.get("Z_AI_AUTH_TOKEN", "")
            z_ai_base_url = os.environ.get("Z_AI_BASE_URL", "")
            z_ai_model = os.environ.get("Z_AI_DEFAULT_MODEL", "")

            if z_ai_token and z_ai_base_url:
                # Use Z.AI Anthropic-compatible endpoint
                client = anthropic.AsyncAnthropic(
                    api_key=z_ai_token,
                    base_url=z_ai_base_url,
                )
                # Override model if Z.AI model specified
                if z_ai_model:
                    config = GenerationConfig(
                        model=z_ai_model,
                        auto_approve=config.auto_approve,
                        max_iterations=config.max_iterations,
                        dry_run=config.dry_run,
                    )
            else:
                # Use standard Anthropic API
                client = anthropic.AsyncAnthropic()

            response = await client.messages.create(
                model=config.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )

            # Extract text content
            content_blocks = [block.text for block in response.content if hasattr(block, "text")]
            return "\n\n".join(content_blocks)

        except ImportError:
            raise RuntimeError("anthropic package not installed. Install with: uv add anthropic")
        except Exception as e:
            raise RuntimeError(f"Claude API error: {e}")

    async def generate_content_library(
        self,
        content_types: list[ContentType] | None = None,
        output_dir: Path | None = None,
    ) -> list[ContentResult]:
        """
        Generate full content library for MVP.

        Note: This method generates prompts but does not call Claude directly.
        The prompts can be used with the FORGE agent or Claude Code SDK.

        Args:
            content_types: Types to generate (defaults to all)
            output_dir: Base output directory

        Returns:
            List of ContentResult for each type
        """
        if content_types is None:
            content_types = list(CONTENT_SPECS.keys())

        if output_dir is None:
            output_dir = self.project_dir / "content"

        # Load project context
        context = self.get_project_context()

        results = []
        for content_type in content_types:
            spec = self.get_content_spec(content_type)
            type_dir = self.prepare_output_directory(content_type, output_dir)

            # Build prompt (actual generation would happen externally)
            prompt = self.build_content_prompt(content_type, spec, context)

            # Save the prompt for now (actual content generation is external)
            prompt_file = type_dir / "_generation_prompt.md"
            prompt_file.write_text(prompt)

            results.append(
                ContentResult(
                    content_type=content_type,
                    pieces_generated=0,  # Would be updated after actual generation
                    output_dir=type_dir,
                    word_count=0,
                    files_created=[str(prompt_file)],
                )
            )

        return results


def create_content_harness(
    domain: str,
    project: str,
    forge_root: str | Path,
) -> ContentHarness:
    """
    Factory function to create a content harness.

    Args:
        domain: FORGE domain name
        project: Project name
        forge_root: Path to FORGE repository root

    Returns:
        ContentHarness instance
    """
    return ContentHarness(
        domain=domain,
        project=project,
        forge_root=Path(forge_root),
    )


# =============================================================================
# Storage Factory Functions
# =============================================================================


def create_file_storage(base_dir: str | Path) -> FileContentStorage:
    """
    Create file-based content storage.

    Args:
        base_dir: Base directory for storage

    Returns:
        FileContentStorage instance
    """
    return FileContentStorage(Path(base_dir))


def create_notion_storage(
    briefs_database_id: str,
    content_database_id: str,
    api_token: str | None = None,
    use_mcp: bool = True,
) -> NotionContentStorage:
    """
    Create Notion-based content storage.

    Args:
        briefs_database_id: Notion database ID for briefs
        content_database_id: Notion database ID for content
        api_token: Notion API token (required for direct mode)
        use_mcp: Use MCP server (True) or direct API (False)

    Returns:
        NotionContentStorage instance

    Raises:
        ImportError: If Notion storage module not available
        ValueError: If configuration is invalid
    """
    if not NOTION_AVAILABLE:
        raise ImportError(
            "notion_storage has been archived. "
            "See harness/archive/README.md for restoration instructions."
        )

    config = NotionConfig(
        briefs_database_id=briefs_database_id,
        content_database_id=content_database_id,
        api_token=api_token,
        connection_mode=NotionConnectionMode.MCP if use_mcp else NotionConnectionMode.DIRECT,
    )

    return NotionContentStorage(config)


def create_storage(
    storage_type: Literal["file", "notion"] = "file",
    base_dir: str | Path | None = None,
    briefs_database_id: str | None = None,
    content_database_id: str | None = None,
    api_token: str | None = None,
    use_mcp: bool = True,
) -> ContentStorage:
    """
    Create content storage based on type.

    This is the main factory function for creating storage backends.

    Args:
        storage_type: "file" or "notion"
        base_dir: Base directory for file storage
        briefs_database_id: Notion database ID for briefs
        content_database_id: Notion database ID for content
        api_token: Notion API token
        use_mcp: Use MCP server for Notion

    Returns:
        ContentStorage implementation

    Examples:
        # File storage
        storage = create_storage("file", base_dir="./content")

        # Notion storage with MCP
        storage = create_storage(
            "notion",
            briefs_database_id="abc123",
            content_database_id="def456",
        )

        # Notion storage with direct API
        storage = create_storage(
            "notion",
            briefs_database_id="abc123",
            content_database_id="def456",
            api_token="secret_xxx",
            use_mcp=False,
        )
    """
    if storage_type == "file":
        if base_dir is None:
            raise ValueError("base_dir is required for file storage")
        return create_file_storage(base_dir)

    elif storage_type == "notion":
        if briefs_database_id is None or content_database_id is None:
            raise ValueError(
                "briefs_database_id and content_database_id are required for Notion storage"
            )
        return create_notion_storage(
            briefs_database_id=briefs_database_id,
            content_database_id=content_database_id,
            api_token=api_token,
            use_mcp=use_mcp,
        )

    else:
        raise ValueError(f"Unknown storage type: {storage_type}")
