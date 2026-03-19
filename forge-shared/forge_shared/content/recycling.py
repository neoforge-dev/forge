"""
Content Recycling Engine - Phase 2 Specification.

Atomizes 1 blog post into 10+ derivative content pieces across platforms.

Example:
    from forge_shared.content.recycling import ContentRecycler, ContentAsset

    recycler = ContentRecycler(
        openai_client=client,  # Optional: for AI augmentation
        tone_presets=TONE_PRESETS,
    )

    # Atomize a blog post
    assets = recycler.atomize(
        source=BlogPost(
            title="How to Ace Your Software Engineering Interview",
            body_md="# The Complete Guide\\n\\nPreparation is key...",
            meta_description="Learn the essential strategies...",
            tags=["interview", "career", "software"],
        ),
        platforms=["linkedin", "twitter", "email", "newsletter"],
    )

    # Get all assets ready for publication
    for asset in assets:
        print(f"{asset.platform}: {asset.title}")
        print(f"Word count: {asset.word_count}")
        print(f"Quality score: {asset.quality_score}")
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel


class Platform(Enum):
    """Supported content platforms."""

    LINKEDIN = "linkedin"
    TWITTER = "twitter"
    THREADS = "threads"
    INSTAGRAM = "instagram"
    NEWSLETTER = "newsletter"
    EMAIL = "email"
    BLOG = "blog"
    MEDIUM = "medium"
    YOUTUBE = "youtube"
    TikTok = "tiktok"
    FACEBOOK = "facebook"
    SLACK = "slack"
    DISCORD = "discord"


class ContentType(Enum):
    """Types of derivative content."""

    POST = "post"  # Short-form social post
    THREAD = "thread"  # Multi-tweet thread
    CAROUSEL = "carousel"  # Image carousel (slide content)
    NEWSLETTER = "newsletter"  # Email newsletter
    EMAIL_SEQUENCE = "email_sequence"  # Multi-email nurture
    VIDEO_SCRIPT = "video_script"  # YouTube/video script
    QUICK_TIP = "quick_tip"  # Short actionable tip
    HOW_TO = "how_to"  # Step-by-step guide
    THOUGHT_LEADER = "thought_leader"  # Opinion/perspective piece
    CASE_STUDY = "case_study"  # Success story format
    FAQ = "faq"  # Q&A format
    CHEATSHEET = "cheatsheet"  # Reference material


@dataclass
class ContentAsset:
    """A single piece of derivative content."""

    platform: Platform
    content_type: ContentType
    title: str
    body: str
    word_count: int
    character_count: int
    hashtags: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    call_to_action: str | None = None
    quality_score: float = 0.0
    seo_score: float = 0.0
    tone: str = "professional"
    variants: list[str] = field(default_factory=list)  # A/B test variants
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AtomizationResult:
    """Result of atomizing source content."""

    source_id: str
    assets: list[ContentAsset]
    total_derivatives: int
    platforms_covered: list[Platform]
    quality_summary: dict[str, float]
    recommendations: list[str]


# Transformation rules for each platform
LINKEDIN_TRANSFORM = {
    "max_length": 3000,
    "ideal_length": 1500,
    "use_paragraphs": True,
    "use_emojis": "sparse",
    "use_hashtags": True,
    "hashtag_limit": 5,
    "call_to_action": "encouraged",
    "tone": "professional",
    "format": "hook_body_cta",
}

TWITTER_TRANSFORM = {
    "max_length": 280,
    "ideal_length": 200,
    "use_paragraphs": False,
    "use_emojis": "moderate",
    "use_hashtags": True,
    "hashtag_limit": 3,
    "call_to_action": "optional",
    "tone": "conversational",
    "format": "thread",
}

NEWSLETTER_TRANSFORM = {
    "max_length": None,  # No hard limit
    "ideal_length": 800,
    "use_paragraphs": True,
    "use_emojis": "minimal",
    "use_hashtags": False,
    "call_to_action": "required",
    "tone": "personal",
    "format": "personal_note",
}

INSTAGRAM_TRANSFORM = {
    "max_length": 2200,
    "ideal_length": 125,
    "use_paragraphs": False,
    "use_emojis": "frequent",
    "use_hashtags": True,
    "hashtag_limit": 30,
    "call_to_action": "required",
    "tone": "visual_friendly",
    "format": "caption",
}


class ContentRecycler:
    """
    Atomizes source content into multiple derivative pieces.

    Features:
    - Platform-specific transformation rules
    - Quality scoring for each derivative
    - Tone preservation across formats
    - SEO optimization per platform
    - Multi-variant generation for testing
    """

    def __init__(
        self,
        openai_client: Any | None = None,
        tone_presets: dict[str, dict[str, str]] | None = None,
        transformation_rules: dict[Platform, dict[str, Any]] | None = None,
    ):
        """
        Initialize the content recycler.

        Args:
            openai_client: OpenAI client for AI augmentation (optional)
            tone_presets: Predefined tone settings per platform
            transformation_rules: Custom platform transformation rules
        """
        self.client = openai_client
        self.tone_presets = tone_presets or {
            "professional": {
                "linkedin": LINKEDIN_TRANSFORM,
                "twitter": TWITTER_TRANSFORM,
                "newsletter": NEWSLETTER_TRANSFORM,
            },
            "casual": {
                "linkedin": {**LINKEDIN_TRANSFORM, "tone": "conversational"},
                "twitter": TWITTER_TRANSFORM,
            },
        }
        self.rules = transformation_rules or {
            Platform.LINKEDIN: LINKEDIN_TRANSFORM,
            Platform.TWITTER: TWITTER_TRANSFORM,
            Platform.NEWSLETTER: NEWSLETTER_TRANSFORM,
            Platform.INSTAGRAM: INSTAGRAM_TRANSFORM,
        }

    # ==========================================================================
    # Main Entry Point
    # ==========================================================================

    def atomize(
        self,
        source: "BlogPost | str",
        platforms: list[Platform] | None = None,
        content_types: list[ContentType] | None = None,
        tone: str = "professional",
        generate_variants: bool = False,
    ) -> AtomizationResult:
        """
        Convert source content into multiple derivative pieces.

        Args:
            source: Source blog post or markdown string
            platforms: Target platforms (defaults to all)
            content_types: Types of content to generate
            tone: Content tone (professional, casual, etc.)
            generate_variants: Generate A/B test variants

        Returns:
            AtomizationResult with all derivative assets
        """
        # Parse source if string
        if isinstance(source, str):
            blog_post = self._parse_markdown(source)
        else:
            blog_post = source

        # Default to all platforms
        if platforms is None:
            platforms = list(Platform)

        # Default content types per platform
        if content_types is None:
            content_types = self._default_content_types(platforms)

        assets = []
        recommendations = []

        # Generate each derivative
        for platform in platforms:
            platform_rules = self.rules.get(platform, {})
            for content_type in content_types:
                if asset := self._transform(
                    blog_post=blog_post,
                    platform=platform,
                    content_type=content_type,
                    tone=tone,
                    rules=platform_rules,
                ):
                    assets.append(asset)

        # Calculate quality summary
        quality_summary = self._calculate_quality_summary(assets)

        # Generate recommendations
        recommendations = self._generate_recommendations(assets, blog_post)

        return AtomizationResult(
            source_id=getattr(blog_post, "id", "unknown"),
            assets=assets,
            total_derivatives=len(assets),
            platforms_covered=list(set(a.platform for a in assets)),
            quality_summary=quality_summary,
            recommendations=recommendations,
        )

    # ==========================================================================
    # Transformation Methods
    # ==========================================================================

    def _transform(
        self,
        blog_post: "BlogPost",
        platform: Platform,
        content_type: ContentType,
        tone: str,
        rules: dict[str, Any],
    ) -> ContentAsset | None:
        """
        Transform source content for a specific platform/type.

        This is the core transformation method that applies
        platform-specific rules to create derivative content.
        """
        # Extract key points from source
        key_points = self._extract_key_points(blog_post)

        # Transform based on content type
        if content_type == ContentType.THREAD:
            return self._create_thread(blog_post, platform, rules)
        elif content_type == ContentType.POST:
            return self._create_post(blog_post, platform, rules)
        elif content_type == ContentType.NEWSLETTER:
            return self._create_newsletter(blog_post, platform, rules)
        elif content_type == ContentType.QUICK_TIP:
            return self._create_quick_tip(blog_post, platform, rules)
        elif content_type == ContentType.HOW_TO:
            return self._create_how_to(blog_post, platform, rules)
        elif content_type == ContentType.THOUGHT_LEADER:
            return self._create_thought_leader(blog_post, platform, rules)
        elif content_type == ContentType.VIDEO_SCRIPT:
            return self._create_video_script(blog_post, platform, rules)
        elif content_type == ContentType.CHEATSHEET:
            return self._create_cheatsheet(blog_post, platform, rules)
        elif content_type == ContentType.FAQ:
            return self._create_faq(blog_post, platform, rules)

        return None

    def _create_thread(
        self,
        blog_post: "BlogPost",
        platform: Platform,
        rules: dict[str, Any],
    ) -> ContentAsset:
        """Create a multi-tweet thread from source content."""
        # Extract 5-7 key points for thread
        key_points = self._extract_key_points(blog_post)[:7]

        # Format each tweet
        tweets = []
        for i, point in enumerate(key_points):
            tweet = f"{i + 1}/{len(key_points)} {point}"
            if i == len(key_points) - 1:
                tweet += " \\n\\n🔗 Read the full guide: [link]"
            tweets.append(tweet)

        body = " \\n\\n".join(tweets)

        return ContentAsset(
            platform=platform,
            content_type=ContentType.THREAD,
            title=f"Thread: {blog_post.title}",
            body=body,
            word_count=self._count_words(body),
            character_count=len(body),
            hashtags=self._extract_hashtags(blog_post.tags, limit=5),
            tone=rules.get("tone", "conversational"),
            quality_score=self._score_quality(body, rules),
        )

    def _create_post(
        self,
        blog_post: "BlogPost",
        platform: Platform,
        rules: dict[str, Any],
    ) -> ContentAsset:
        """Create a single social post."""
        # Hook + value + CTA format
        hook = self._create_hook(blog_post)
        value_prop = self._summarize_value(blog_post)

        body = f"{hook}\\n\\n{value_prop}"

        if rules.get("call_to_action") == "encouraged":
            body += "\\n\\n💭 What strategies have worked for you?"

        return ContentAsset(
            platform=platform,
            content_type=ContentType.POST,
            title=blog_post.title,
            body=body,
            word_count=self._count_words(body),
            character_count=len(body),
            hashtags=self._extract_hashtags(blog_post.tags, limit=rules.get("hashtag_limit", 5)),
            tone=rules.get("tone", "professional"),
            quality_score=self._score_quality(body, rules),
        )

    def _create_newsletter(
        self,
        blog_post: "BlogPost",
        platform: Platform,
        rules: dict[str, Any],
    ) -> ContentAsset:
        """Create a newsletter edition."""
        # Personal note + key takeaways + CTA
        hook = self._create_personal_hook(blog_post)
        takeaways = self._format_takeaways(blog_post)
        cta = "👉 Read the full article: [link]"

        body = f"{hook}\\n\\n{takeaways}\\n\\n{cta}"

        return ContentAsset(
            platform=platform,
            content_type=ContentType.NEWSLETTER,
            title=f"📬 {blog_post.title}",
            body=body,
            word_count=self._count_words(body),
            character_count=len(body),
            tone=rules.get("tone", "personal"),
            quality_score=self._score_quality(body, rules),
        )

    def _create_quick_tip(
        self,
        blog_post: "BlogPost",
        platform: Platform,
        rules: dict[str, Any],
    ) -> ContentAsset:
        """Create a single actionable tip."""
        tips = self._extract_tips(blog_post)

        if not tips:
            return None

        tip = tips[0]  # Take the best tip
        body = f"💡 {tip}"

        return ContentAsset(
            platform=platform,
            content_type=ContentType.QUICK_TIP,
            title=f"Quick Tip: {blog_post.title}",
            body=body,
            word_count=self._count_words(body),
            character_count=len(body),
            hashtags=self._extract_hashtags(blog_post.tags, limit=3),
            tone="actionable",
            quality_score=self._score_quality(body, rules),
        )

    def _create_how_to(
        self,
        blog_post: "BlogPost",
        platform: Platform,
        rules: dict[str, Any],
    ) -> ContentAsset:
        """Create a step-by-step how-to guide."""
        steps = self._extract_steps(blog_post)

        if not steps:
            return None

        body = f"🛠️ **How to {blog_post.title}**\\n\\n"
        for i, step in enumerate(steps, 1):
            body += f"{i}. {step}\\n"

        return ContentAsset(
            platform=platform,
            content_type=ContentType.HOW_TO,
            title=f"Guide: {blog_post.title}",
            body=body,
            word_count=self._count_words(body),
            character_count=len(body),
            tone="instructional",
            quality_score=self._score_quality(body, rules),
        )

    def _create_thought_leader(
        self,
        blog_post: "BlogPost",
        platform: Platform,
        rules: dict[str, Any],
    ) -> ContentAsset:
        """Create an opinion/perspective piece."""
        hook = f"Unpopular opinion about {blog_post.title}:"
        perspective = self._extract_perspective(blog_post)

        body = f"{hook}\\n\\n{perspective}"

        return ContentAsset(
            platform=platform,
            content_type=ContentType.THOUGHT_LEADER,
            title=f"Opinion: {blog_post.title}",
            body=body,
            word_count=self._count_words(body),
            character_count=len(body),
            tone="thought_leader",
            quality_score=self._score_quality(body, rules),
        )

    def _create_video_script(
        self,
        blog_post: "BlogPost",
        platform: Platform,
        rules: dict[str, Any],
    ) -> ContentAsset:
        """Create a YouTube/video script."""
        body = f"""🎬 **Video Script: {blog_post.title}**

**INTRO (0:00-0:30)**
- Hook: "{self._create_hook(blog_post)}"
- Preview: "In this video, we'll cover..."

**MAIN CONTENT (0:30-4:00)**
- Point 1: Key insight from the article
- Point 2: Actionable step
- Point 3: Common mistake to avoid

**OUTRO (4:00-5:00)**
- Recap key points
- CTA: "Subscribe for more tips"
"""

        return ContentAsset(
            platform=platform,
            content_type=ContentType.VIDEO_SCRIPT,
            title=f"Script: {blog_post.title}",
            body=body,
            word_count=self._count_words(body),
            character_count=len(body),
            tone="video_host",
            quality_score=self._score_quality(body, rules),
        )

    def _create_cheatsheet(
        self,
        blog_post: "BlogPost",
        platform: Platform,
        rules: dict[str, Any],
    ) -> ContentAsset:
        """Create a reference cheatsheet."""
        key_points = self._extract_key_points(blog_post)
        tips = self._extract_tips(blog_post)

        body = f"📋 **{blog_post.title} - Cheatsheet**\\n\\n"
        body += "**KEY TAKEAWAYS**\\n"
        for point in key_points[:5]:
            body += f"• {point}\\n"

        if tips:
            body += "\\n**QUICK TIPS**\\n"
            for tip in tips[:3]:
                body += f"• {tip}\\n"

        return ContentAsset(
            platform=platform,
            content_type=ContentType.CHEATSHEET,
            title=f"Cheatsheet: {blog_post.title}",
            body=body,
            word_count=self._count_words(body),
            character_count=len(body),
            tone="reference",
            quality_score=self._score_quality(body, rules),
        )

    def _create_faq(
        self,
        blog_post: "BlogPost",
        platform: Platform,
        rules: dict[str, Any],
    ) -> ContentAsset:
        """Create FAQ format content."""
        qa_pairs = self._extract_qa(blog_post)

        if not qa_pairs:
            return None

        body = f"❓ **{blog_post.title}: FAQ**\\n\\n"
        for question, answer in qa_pairs:
            body += f"**Q: {question}**\\nA: {answer}\\n\\n"

        return ContentAsset(
            platform=platform,
            content_type=ContentType.FAQ,
            title=f"FAQ: {blog_post.title}",
            body=body,
            word_count=self._count_words(body),
            character_count=len(body),
            tone="informative",
            quality_score=self._score_quality(body, rules),
        )

    # ==========================================================================
    # Helper Methods
    # ==========================================================================

    def _parse_markdown(self, markdown_text: str) -> "BlogPost":
        """Parse markdown into a BlogPost-like object."""
        # Extract title from first H1
        title = ""
        if markdown_text.startswith("#"):
            lines = markdown_text.split("\n", 1)
            title = lines[0].lstrip("# ").strip()

        return BlogPost(
            title=title,
            body_md=markdown_text,
            meta_description="",
            tags=[],
        )

    def _extract_key_points(self, blog_post: "BlogPost") -> list[str]:
        """Extract key points from blog post content."""
        # Simple extraction - split by H2 headers
        points = []
        lines = blog_post.body_md.split("\n")

        for line in lines:
            line = line.strip()
            if line.startswith("##") or line.startswith("- ") or line.startswith("* "):
                point = line.lstrip("#*- ").strip()
                if len(point) > 20:
                    points.append(point)

        return points or ["Key insight from the article"]

    def _extract_tips(self, blog_post: "BlogPost") -> list[str]:
        """Extract actionable tips from content."""
        tips = []
        lines = blog_post.body_md.split("\n")

        for line in lines:
            line = line.strip().lower()
            if any(word in line for word in ["tip:", "pro tip", "quick tip", "instead,", "try:"]):
                tip = line.lstrip("- *").lstrip("Tip: ").lstrip("PRO TIP: ")
                if tip:
                    tips.append(tip)

        return tips

    def _extract_steps(self, blog_post: "BlogPost") -> list[str]:
        """Extract step-by-step instructions."""
        steps = []
        lines = blog_post.body_md.split("\n")

        for line in lines:
            line = line.strip()
            if line.startswith("1.") or line.startswith("- [ ]") or "step" in line.lower():
                step = line.lstrip("1. ").lstrip("- [ ]").lstrip("Step: ")
                if step:
                    steps.append(step)

        return steps

    def _extract_qa(self, blog_post: "BlogPost") -> list[tuple[str, str]]:
        """Extract Q&A pairs from content."""
        qa_pairs = []
        lines = blog_post.body_md.split("\n")

        for i, line in enumerate(lines):
            if line.startswith("## Q:") or line.lower().startswith("question:"):
                answer = ""
                for j in range(i + 1, min(i + 5, len(lines))):
                    next_line = lines[j].strip()
                    if next_line.startswith("## ") or not next_line:
                        break
                    answer += next_line + " "

                qa_pairs.append((line.split(":", 1)[1].strip(), answer.strip()))

        return qa_pairs

    def _extract_perspective(self, blog_post: "BlogPost") -> str:
        """Extract opinion/perspective angle."""
        return f"Most people approach {blog_post.title} the wrong way. Here's why..."

    def _create_hook(self, blog_post: "BlogPost") -> str:
        """Create an engaging hook for social posts."""
        return f"🚀 {blog_post.title} - Here's what you need to know:"

    def _create_personal_hook(self, blog_post: "BlogPost") -> str:
        """Create a personal tone hook for newsletters."""
        return f"Hey there! 👋\\n\\nI just explored {blog_post.title} and thought you'd find this valuable."

    def _summarize_value(self, blog_post: "BlogPost") -> str:
        """Summarize the value proposition."""
        return "Discover the key strategies that actually work."

    def _format_takeaways(self, blog_post: "BlogPost") -> str:
        """Format key takeaways for newsletter."""
        points = self._extract_key_points(blog_post)
        takeaways = "**Here's what you'll learn:**\\n\\n"
        for point in points[:3]:
            takeaways += f"• {point}\\n"
        return takeaways

    def _extract_hashtags(
        self,
        tags: list[str],
        limit: int = 5,
        prefix: str = "",
    ) -> list[str]:
        """Generate hashtags from tags."""
        hashtags = []
        for tag in tags[:limit]:
            hashtag = f"#{prefix}{tag.replace(' ', '').replace('-', '').lower()}"
            hashtags.append(hashtag)
        return hashtags

    def _count_words(self, text: str) -> int:
        """Count words in text."""
        return len(text.split())

    def _score_quality(self, body: str, rules: dict[str, Any]) -> float:
        """Calculate quality score for content."""
        score = 1.0

        # Length scoring
        ideal = rules.get("ideal_length", 200)
        actual = self._count_words(body)
        if actual > 0:
            length_ratio = min(actual, ideal) / ideal
            score = 0.5 + (0.5 * length_ratio)

        # Hook presence
        if any(word in body.lower() for word in ["🚀", "💡", "🔗", "👉", "💭"]):
            score += 0.1

        # CTA presence
        if any(cta in body for cta in ["[link]", "Subscribe", "Read more", "Learn more"]):
            score += 0.1

        return min(1.0, score)

    def _calculate_quality_summary(self, assets: list[ContentAsset]) -> dict[str, float]:
        """Calculate quality metrics across all assets."""
        if not assets:
            return {}

        return {
            "avg_quality": sum(a.quality_score for a in assets) / len(assets),
            "min_quality": min(a.quality_score for a in assets),
            "max_quality": max(a.quality_score for a in assets),
        }

    def _generate_recommendations(
        self,
        assets: list[ContentAsset],
        blog_post: "BlogPost",
    ) -> list[str]:
        """Generate improvement recommendations."""
        recommendations = []

        # Check variety
        platforms = set(a.platform for a in assets)
        if len(platforms) < 3:
            recommendations.append("Consider adding more platforms for wider reach")

        # Check engagement hooks
        hooks_present = sum(1 for a in assets if any(h in a.body for h in ["?", "💡", "🚀", "👋"]))
        if hooks_present < len(assets) * 0.5:
            recommendations.append("Add more engaging hooks to your posts")

        # Check hashtags
        no_hashtags = sum(1 for a in assets if not a.hashtags)
        if no_hashtags > 0:
            recommendations.append("Add hashtags to improve discoverability")

        return recommendations

    def _default_content_types(
        self,
        platforms: list[Platform],
    ) -> list[ContentType]:
        """Get default content types per platform."""
        defaults = {
            Platform.TWITTER: [ContentType.THREAD, ContentType.QUICK_TIP],
            Platform.LINKEDIN: [ContentType.POST, ContentType.THOUGHT_LEADER],
            Platform.NEWSLETTER: [ContentType.NEWSLETTER],
            Platform.INSTAGRAM: [ContentType.POST, ContentType.CAROUSEL],
            Platform.YOUTUBE: [ContentType.VIDEO_SCRIPT],
            Platform.BLOG: [ContentType.HOW_TO, ContentType.CHEATSHEET],
        }

        types = []
        for platform in platforms:
            types.extend(defaults.get(platform, [ContentType.POST]))

        return list(set(types))


# ==========================================================================
# Supporting Models
# ==========================================================================


class BlogPost(BaseModel):
    """Source blog post model."""

    id: str | None = None
    title: str
    body_md: str
    meta_description: str = ""
    tags: list[str] = field(default_factory=list)
    author: str = ""
    published_at: str | None = None
    reading_time_minutes: int = 5


# =============================================================================
# Convenience Functions
# =============================================================================


def atomize_blog_post(
    source: str | BlogPost,
    platforms: list[Platform] | None = None,
) -> AtomizationResult:
    """
    Convenience function to atomize a blog post.

    Args:
        source: Markdown string or BlogPost object
        platforms: Target platforms (defaults to [linkedin, twitter, newsletter])

    Returns:
        AtomizationResult with all derivative content
    """
    if platforms is None:
        platforms = [Platform.LINKEDIN, Platform.TWITTER, Platform.NEWSLETTER]

    recycler = ContentRecycler()
    return recycler.atomize(source, platforms)


__all__ = [
    "AtomizationResult",
    "BlogPost",
    "ContentAsset",
    "ContentRecycler",
    "ContentType",
    "Platform",
    "atomize_blog_post",
]
