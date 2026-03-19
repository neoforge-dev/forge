"""
Marketing Module.

Provides content sequence automation, templates, and multi-channel distribution.

Submodules:
    - content_sequence: Content sequence engine and scheduling
"""

from forge_shared.marketing.content_sequence import (
    Channel,
    ContentSequence,
    DistributionScheduler,
    MultiChannelDistributor,
    SequenceSchedule,
    SequenceStep,
    SequenceTemplate,
    create_sequence,
    format_content,
    schedule_sequence,
)

__all__ = [
    "Channel",
    "ContentSequence",
    "DistributionScheduler",
    "MultiChannelDistributor",
    "SequenceSchedule",
    "SequenceStep",
    "SequenceTemplate",
    "create_sequence",
    "format_content",
    "schedule_sequence",
]
