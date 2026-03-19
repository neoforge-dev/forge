"""
Content Sequence Engine - Automated multi-channel content distribution.

Provides sequence templates, scheduling, and distribution for marketing campaigns.

Example:
    from forge_shared.marketing.content_sequence import (
        ContentSequence,
        SequenceTemplate,
        Channel,
        DistributionScheduler,
    )

    # Create a nurture sequence
    sequence = ContentSequence(
        name="Lead Nurture",
        template=SequenceTemplate.WELCOME,
        channels=[Channel.EMAIL, Channel.LINKEDIN, Channel.TWITTER],
    )

    # Schedule distribution
    scheduler = DistributionScheduler(timezone="America/New_York")
    schedule = scheduler.create_schedule(sequence, start_date=datetime.now())
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel


class Channel(Enum):
    """Marketing channels for content distribution."""

    EMAIL = "email"
    LINKEDIN = "linkedin"
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    BLOG = "blog"
    NEWSLETTER = "newsletter"
    SLACK = "slack"
    DISCORD = "discord"


class SequenceTemplate(Enum):
    """Predefined sequence templates."""

    WELCOME = "welcome"
    LEAD_NURTURE = "lead_nurture"
    PRODUCT_LAUNCH = "product_launch"
    WEBINAR_PROMOTION = "webinar_promotion"
    CONTENT_UPGRADE = "content_upgrade"
    REENGAGEMENT = "reengagement"
    ONBOARDING = "onboarding"
    CUSTOM = "custom"


class SequenceStep(BaseModel):
    """A single step in a content sequence."""

    step_number: int
    day_offset: int  # Days from sequence start
    channel: Channel
    content_type: str  # email, post, tweet, etc.
    subject: str | None = None
    body_template: str | None = None
    delay_hours: int = 0  # Within-day delay
    triggered: bool = False  # For behavioral triggers


class ContentSequence(BaseModel):
    """A complete content sequence across multiple channels."""

    name: str
    template: SequenceTemplate | None = None
    channels: list[Channel]
    steps: list[SequenceStep] = field(default_factory=list)
    start_date: datetime | None = None
    end_date: datetime | None = None
    timezone: str = "UTC"
    status: str = "draft"  # draft, scheduled, active, paused, completed


class SequenceSchedule(BaseModel):
    """Scheduled execution plan for a sequence."""

    sequence_name: str
    scheduled_items: list[dict[str, Any]] = field(default_factory=list)
    total_steps: int = 0
    duration_days: int = 0


@dataclass
class DistributionScheduler:
    """
    Schedules content distribution across channels.

    Features:
    - Timezone-aware scheduling
    - Optimal send times per channel
    - Sequence template generation
    - Multi-channel coordination
    """

    timezone: str = "UTC"

    # Optimal send times by channel (hour of day in local time)
    OPTIMAL_TIMES = {
        Channel.EMAIL: 9,  # 9 AM
        Channel.LINKEDIN: 8,  # 8 AM
        Channel.TWITTER: 12,  # 12 PM
        Channel.FACEBOOK: 13,  # 1 PM
        Channel.INSTAGRAM: 11,  # 11 AM
        Channel.BLOG: 10,  # 10 AM
        Channel.NEWSLETTER: 7,  # 7 AM
        Channel.SLACK: 9,  # 9 AM
        Channel.DISCORD: 12,  # 12 PM
    }

    # Days to avoid sending
    WEEKEND_DAYS = {5, 6}  # Saturday, Sunday

    def __init__(self, timezone: str = "UTC"):
        """Initialize scheduler with timezone."""
        self.timezone = timezone

    def create_schedule(
        self,
        sequence: ContentSequence,
        start_date: datetime | None = None,
    ) -> SequenceSchedule:
        """
        Create a schedule from a sequence.

        Args:
            sequence: The content sequence to schedule
            start_date: When to start (defaults to now)

        Returns:
            SequenceSchedule with all scheduled items
        """
        if start_date is None:
            start_date = datetime.now()

        if sequence.start_date:
            start_date = max(start_date, sequence.start_date)

        scheduled_items = []
        last_date = start_date

        for step in sorted(sequence.steps, key=lambda s: s.day_offset):
            # Calculate scheduled datetime
            scheduled_datetime = self._calculate_scheduled_time(
                start_date, step.day_offset, step.delay_hours, step.channel
            )

            # Skip weekends if configured
            scheduled_datetime = self._adjust_for_weekends(scheduled_datetime)

            scheduled_items.append(
                {
                    "step_number": step.step_number,
                    "scheduled_at": scheduled_datetime.isoformat(),
                    "channel": step.channel.value,
                    "content_type": step.content_type,
                    "subject": step.subject,
                    "delay_days": step.day_offset,
                    "optimal_time": self.OPTIMAL_TIMES.get(step.channel, 9),
                }
            )

            last_date = scheduled_datetime

        duration_days = (last_date - start_date).days if scheduled_items else 0

        return SequenceSchedule(
            sequence_name=sequence.name,
            scheduled_items=scheduled_items,
            total_steps=len(scheduled_items),
            duration_days=duration_days,
        )

    def _calculate_scheduled_time(
        self,
        start_date: datetime,
        day_offset: int,
        delay_hours: int,
        channel: Channel,
    ) -> datetime:
        """Calculate the scheduled datetime for a step."""
        # Base date from start
        scheduled = start_date + timedelta(days=day_offset)

        # Set to optimal time for channel
        optimal_hour = self.OPTIMAL_TIMES.get(channel, 9)
        scheduled = scheduled.replace(
            hour=optimal_hour,
            minute=0,
            second=0,
            microsecond=0,
        )

        # Add within-day delay
        if delay_hours > 0:
            scheduled = scheduled + timedelta(hours=delay_hours)

        return scheduled

    def _adjust_for_weekends(self, dt: datetime) -> datetime:
        """Adjust datetime if it falls on a weekend."""
        while dt.weekday() in self.WEEKEND_DAYS:
            # Move to Monday
            dt = dt + timedelta(days=1)
            # Reset to 9 AM
            dt = dt.replace(hour=9, minute=0, second=0, microsecond=0)
        return dt

    def generate_template(
        self,
        template: SequenceTemplate,
        name: str | None = None,
    ) -> ContentSequence:
        """
        Generate a sequence from a template.

        Args:
            template: The template to generate from
            name: Optional custom name

        Returns:
            ContentSequence populated with template steps
        """
        if name is None:
            name = f"{template.value.title()} Sequence"

        channels, steps = self._get_template_steps(template)

        return ContentSequence(
            name=name,
            template=template,
            channels=channels,
            steps=steps,
            status="draft",
        )

    def _get_template_steps(
        self,
        template: SequenceTemplate,
    ) -> tuple[list[Channel], list[SequenceStep]]:
        """Get channels and steps for a template."""
        templates = {
            SequenceTemplate.WELCOME: (
                [Channel.EMAIL, Channel.SLACK],
                [
                    SequenceStep(
                        step_number=1,
                        day_offset=0,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Welcome!",
                        body_template="welcome_email",
                    ),
                    SequenceStep(
                        step_number=2,
                        day_offset=1,
                        channel=Channel.SLACK,
                        content_type="message",
                        subject=None,
                        body_template="welcome_slack",
                    ),
                ],
            ),
            SequenceTemplate.LEAD_NURTURE: (
                [Channel.EMAIL, Channel.LINKEDIN, Channel.TWITTER],
                [
                    SequenceStep(
                        step_number=1,
                        day_offset=0,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Thanks for signing up",
                        body_template="lead_nurture_1",
                    ),
                    SequenceStep(
                        step_number=2,
                        day_offset=2,
                        channel=Channel.LINKEDIN,
                        content_type="post",
                        subject="Quick tip",
                        body_template="linkedin_tip",
                    ),
                    SequenceStep(
                        step_number=3,
                        day_offset=4,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Here's something useful",
                        body_template="lead_nurture_2",
                    ),
                    SequenceStep(
                        step_number=4,
                        day_offset=7,
                        channel=Channel.TWITTER,
                        content_type="tweet",
                        subject=None,
                        body_template="twitter_thread",
                    ),
                    SequenceStep(
                        step_number=5,
                        day_offset=10,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Ready to learn more?",
                        body_template="lead_nurture_3",
                    ),
                ],
            ),
            SequenceTemplate.PRODUCT_LAUNCH: (
                [Channel.EMAIL, Channel.LINKEDIN, Channel.TWITTER, Channel.FACEBOOK],
                [
                    SequenceStep(
                        step_number=1,
                        day_offset=-3,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Something big is coming",
                        body_template="launch_teaser",
                    ),
                    SequenceStep(
                        step_number=2,
                        day_offset=-2,
                        channel=Channel.LINKEDIN,
                        content_type="post",
                        subject="Big news",
                        body_template="launch_linkedin",
                    ),
                    SequenceStep(
                        step_number=3,
                        day_offset=-1,
                        channel=Channel.TWITTER,
                        content_type="thread",
                        subject=None,
                        body_template="launch_twitter",
                    ),
                    SequenceStep(
                        step_number=4,
                        day_offset=0,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="It's here!",
                        body_template="launch_announce",
                    ),
                    SequenceStep(
                        step_number=5,
                        day_offset=1,
                        channel=Channel.FACEBOOK,
                        content_type="post",
                        subject="Launch post",
                        body_template="launch_facebook",
                    ),
                    SequenceStep(
                        step_number=6,
                        day_offset=3,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="How's it going?",
                        body_template="launch_followup",
                    ),
                ],
            ),
            SequenceTemplate.WEBINAR_PROMOTION: (
                [Channel.EMAIL, Channel.LINKEDIN, Channel.TWITTER, Channel.NEWSLETTER],
                [
                    SequenceStep(
                        step_number=1,
                        day_offset=-7,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Save the date",
                        body_template="webinar_save_date",
                    ),
                    SequenceStep(
                        step_number=2,
                        day_offset=-5,
                        channel=Channel.LINKEDIN,
                        content_type="post",
                        subject="Webinar announcement",
                        body_template="webinar_linkedin",
                    ),
                    SequenceStep(
                        step_number=3,
                        day_offset=-3,
                        channel=Channel.TWITTER,
                        content_type="tweet",
                        subject=None,
                        body_template="webinar_twitter",
                    ),
                    SequenceStep(
                        step_number=4,
                        day_offset=-2,
                        channel=Channel.NEWSLETTER,
                        content_type="newsletter",
                        subject="Webinar this week",
                        body_template="webinar_newsletter",
                    ),
                    SequenceStep(
                        step_number=5,
                        day_offset=-1,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Tomorrow!",
                        body_template="webinar_reminder",
                    ),
                    SequenceStep(
                        step_number=6,
                        day_offset=0,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Starting now",
                        body_template="webinar_now",
                    ),
                ],
            ),
            SequenceTemplate.CONTENT_UPGRADE: (
                [Channel.EMAIL, Channel.TWITTER],
                [
                    SequenceStep(
                        step_number=1,
                        day_offset=0,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Your free resource",
                        body_template="content_upgrade_1",
                    ),
                    SequenceStep(
                        step_number=2,
                        day_offset=3,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Did you find it useful?",
                        body_template="content_upgrade_2",
                    ),
                    SequenceStep(
                        step_number=3,
                        day_offset=7,
                        channel=Channel.TWITTER,
                        content_type="tweet",
                        subject=None,
                        body_template="content_upgrade_twitter",
                    ),
                    SequenceStep(
                        step_number=4,
                        day_offset=10,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Bonus inside",
                        body_template="content_upgrade_3",
                    ),
                ],
            ),
            SequenceTemplate.REENGAGEMENT: (
                [Channel.EMAIL],
                [
                    SequenceStep(
                        step_number=1,
                        day_offset=0,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="We miss you",
                        body_template="reengage_1",
                    ),
                    SequenceStep(
                        step_number=2,
                        day_offset=7,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Here's what's new",
                        body_template="reengage_2",
                    ),
                    SequenceStep(
                        step_number=3,
                        day_offset=14,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="One last try",
                        body_template="reengage_3",
                    ),
                ],
            ),
            SequenceTemplate.ONBOARDING: (
                [Channel.EMAIL, Channel.SLACK],
                [
                    SequenceStep(
                        step_number=1,
                        day_offset=0,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Welcome aboard",
                        body_template="onboard_1",
                    ),
                    SequenceStep(
                        step_number=2,
                        day_offset=1,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Getting started",
                        body_template="onboard_2",
                    ),
                    SequenceStep(
                        step_number=3,
                        day_offset=3,
                        channel=Channel.SLACK,
                        content_type="message",
                        subject=None,
                        body_template="onboard_slack",
                    ),
                    SequenceStep(
                        step_number=4,
                        day_offset=5,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="Pro tips",
                        body_template="onboard_3",
                    ),
                    SequenceStep(
                        step_number=5,
                        day_offset=10,
                        channel=Channel.EMAIL,
                        content_type="email",
                        subject="You're all set!",
                        body_template="onboard_4",
                    ),
                ],
            ),
        }

        return templates.get(template, ([], []))

    def get_template_info(self, template: SequenceTemplate) -> dict[str, Any]:
        """Get information about a template."""
        channels, steps = self._get_template_steps(template)

        return {
            "template": template.value,
            "channels": [c.value for c in channels],
            "step_count": len(steps),
            "duration_days": max((s.day_offset for s in steps), default=0) + 1,
            "optimal_times": {c.value: self.OPTIMAL_TIMES.get(c, 9) for c in channels},
        }


@dataclass
class MultiChannelDistributor:
    """
    Distributes content across multiple channels.

    Features:
    - Channel-specific formatting
    - Character limit enforcement
    - Hashtag management
    - Platform optimization
    """

    def format_for_channel(
        self,
        content: str,
        channel: Channel,
        variables: dict[str, str] | None = None,
    ) -> str:
        """
        Format content for a specific channel.

        Args:
            content: Template content with {{variables}}
            channel: Target channel
            variables: Variable substitutions

        Returns:
            Formatted content for channel
        """
        if variables:
            for key, value in variables.items():
                content = content.replace(f"{{{{{key}}}}}", value)
                content = content.replace(f"{{{{.{key}}}}}", value)

        # Channel-specific formatting
        if channel == Channel.TWITTER:
            content = self._format_twitter(content)
        elif channel == Channel.LINKEDIN:
            content = self._format_linkedin(content)
        elif channel == Channel.INSTAGRAM:
            content = self._format_instagram(content)
        elif channel == Channel.EMAIL:
            content = self._format_email(content)

        return content

    def _format_twitter(self, content: str) -> str:
        """Format content for Twitter (280 char limit)."""
        # Truncate if needed
        if len(content) > 280:
            content = content[:277] + "..."
        return content

    def _format_linkedin(self, content: str) -> str:
        """Format content for LinkedIn."""
        # Add spacing for readability
        if "\n" not in content and len(content) > 200:
            paragraphs = content.split(". ")
            content = "\n\n".join(paragraphs)
        return content

    def _format_instagram(self, content: str) -> str:
        """Format content for Instagram."""
        # Ensure line breaks
        if "\n" not in content and len(content) > 125:
            sentences = content.split(". ")
            content = ".\n\n".join(sentences)
        # Add spacing for hashtags
        if "#" in content:
            parts = content.rsplit("#", 1)
            if len(parts) > 1:
                content = parts[0] + "\n\n#" + parts[1]
        return content

    def _format_email(self, content: str) -> str:
        """Format content for email."""
        # Ensure proper spacing
        if not content.startswith("Hi") and not content.startswith("Hey"):
            if "\n\n" not in content:
                paragraphs = content.split(". ")
                content = "\n\n".join(paragraphs)
        return content

    def extract_hashtags(self, content: str) -> list[str]:
        """Extract hashtags from content."""
        import re

        tags = re.findall(r"#(\w+)", content)
        return [f"#{tag}" for tag in tags]

    def generate_hashtags(
        self,
        topic: str,
        platform: Channel,
        count: int = 5,
    ) -> list[str]:
        """Generate relevant hashtags for a topic."""
        base_tags = {
            Channel.TWITTER: ["#Tech", "#Innovation", "#Growth"],
            Channel.LINKEDIN: ["#Technology", "#Business", "#Leadership"],
            Channel.INSTAGRAM: ["#TechLife", "#Innovation", "#DailyInspo"],
        }

        tags = base_tags.get(platform, ["#Tech"])
        topic_tag = f"#{topic.replace(' ', '')}"

        if topic_tag not in tags:
            tags = [topic_tag] + tags

        return tags[:count]


# =============================================================================
# Convenience Functions
# =============================================================================


def create_sequence(
    name: str,
    template: SequenceTemplate,
    timezone: str = "UTC",
) -> ContentSequence:
    """
    Create a content sequence from a template.

    Args:
        name: Name for the sequence
        template: Template to use
        timezone: Scheduling timezone

    Returns:
        ContentSequence ready to be configured and scheduled
    """
    scheduler = DistributionScheduler(timezone=timezone)
    return scheduler.generate_template(template, name)


def schedule_sequence(
    sequence: ContentSequence,
    start_date: datetime | None = None,
) -> SequenceSchedule:
    """
    Create a schedule from a sequence.

    Args:
        sequence: The sequence to schedule
        start_date: When to start

    Returns:
        SequenceSchedule with all scheduled items
    """
    scheduler = DistributionScheduler()
    return scheduler.create_schedule(sequence, start_date)


def format_content(
    content: str,
    channel: Channel,
    variables: dict[str, str] | None = None,
) -> str:
    """
    Format content for a specific channel.

    Args:
        content: Template content
        channel: Target channel
        variables: Variable substitutions

    Returns:
        Formatted content
    """
    distributor = MultiChannelDistributor()
    return distributor.format_for_channel(content, channel, variables)


__all__ = [
    "Channel",
    "ContentSequence",
    "DistributionScheduler",
    "MultiChannelDistributor",
    "SequenceSchedule",
    "SequenceStep",
    "SequenceTemplate",
    "create_sequence",
    "schedule_sequence",
    "format_content",
]
