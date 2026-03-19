"""Email sequence and messaging module for Marketing API."""

from .models import (
    EmailEvent,
    EmailSequence,
    EmailTemplate,
    SequenceStep,
    SubscriberStatus,
)
from .sender import EmailProvider, EmailSender
from .sequence import SequenceExecutor, SequenceService
from .tracking import EmailTracker

__all__ = [
    "EmailTemplate",
    "EmailSequence",
    "SequenceStep",
    "EmailEvent",
    "SubscriberStatus",
    "SequenceService",
    "SequenceExecutor",
    "EmailSender",
    "EmailProvider",
    "EmailTracker",
]
