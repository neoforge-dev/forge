"""Email sequence and messaging models."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, EmailStr, Field, validator


class SubscriberStatus(str, Enum):
    """Subscriber status enum."""

    ACTIVE = "active"
    UNSUBSCRIBED = "unsubscribed"
    BOUNCED = "bounced"
    INACTIVE = "inactive"
    PENDING_CONFIRMATION = "pending_confirmation"


class SequenceTrigger(str, Enum):
    """Sequence trigger types."""

    SIGNUP = "signup"
    PURCHASE = "purchase"
    CART_ABANDONED = "cart_abandoned"
    INACTIVITY = "inactivity"
    BIRTHDAY = "birthday"
    MANUAL = "manual"


class EmailTemplate(BaseModel):
    """Email template model."""

    id: str
    name: str
    subject: str
    body_html: str
    body_text: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @validator("name")
    def name_not_empty(cls, v):
        """Validate template name is not empty."""
        if not v or not v.strip():
            raise ValueError("Template name cannot be empty")
        return v

    @validator("subject")
    def subject_not_empty(cls, v):
        """Validate subject is not empty."""
        if not v or not v.strip():
            raise ValueError("Subject cannot be empty")
        return v

    @validator("body_html")
    def body_html_not_empty(cls, v):
        """Validate HTML body is not empty."""
        if not v or not v.strip():
            raise ValueError("HTML body cannot be empty")
        return v

    def render(self, variables: dict[str, Any]) -> tuple:
        """Render template with variables.

        Args:
            variables: Template variables to substitute

        Returns:
            Tuple of (subject, body_html, body_text)
        """
        subject = self.subject
        body_html = self.body_html
        body_text = self.body_text or ""

        for key, value in variables.items():
            placeholder = f"{{{{{key}}}}}"
            subject = subject.replace(placeholder, str(value))
            body_html = body_html.replace(placeholder, str(value))
            body_text = body_text.replace(placeholder, str(value))

        return subject, body_html, body_text


class SequenceStep(BaseModel):
    """Single step in an email sequence."""

    id: str
    sequence_id: str
    template_id: str
    delay_hours: int = Field(ge=0)
    order: int = Field(ge=0)
    enabled: bool = True
    conditions: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @validator("delay_hours")
    def valid_delay(cls, v):
        """Validate delay hours is reasonable."""
        if v < 0 or v > 8760:  # 1 year max
            raise ValueError("Delay hours must be between 0 and 8760 (1 year)")
        return v

    def should_execute(self, context: dict[str, Any]) -> bool:
        """Check if step should execute based on conditions.

        Args:
            context: Execution context with subscriber/purchase data

        Returns:
            True if conditions are met
        """
        if not self.conditions:
            return True

        for key, expected_value in self.conditions.items():
            context_value = context.get(key)
            if context_value != expected_value:
                return False

        return True


class EmailSequence(BaseModel):
    """Email sequence model."""

    id: str
    name: str
    trigger: SequenceTrigger
    domain: str | None = None
    description: str | None = None
    steps: list[SequenceStep] = Field(default_factory=list)
    enabled: bool = True
    max_recipients: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @validator("name")
    def name_not_empty(cls, v):
        """Validate sequence name."""
        if not v or not v.strip():
            raise ValueError("Sequence name cannot be empty")
        return v

    @validator("steps")
    def steps_ordered(cls, v):
        """Validate steps are properly ordered."""
        if v:
            sorted_steps = sorted(v, key=lambda s: s.order)
            for i, step in enumerate(sorted_steps):
                if step.order != i:
                    raise ValueError("Steps must have sequential order values")
        return v

    def add_step(
        self, template_id: str, delay_hours: int, order: int | None = None
    ) -> SequenceStep:
        """Add a step to the sequence.

        Args:
            template_id: Template to use
            delay_hours: Delay before sending
            order: Order in sequence (auto-incremented if not provided)

        Returns:
            Created SequenceStep
        """
        if order is None:
            order = len(self.steps)

        step = SequenceStep(
            id=f"step_{self.id}_{order}",
            sequence_id=self.id,
            template_id=template_id,
            delay_hours=delay_hours,
            order=order,
        )
        self.steps.append(step)
        return step


class EmailEvent(BaseModel):
    """Email delivery and interaction event."""

    id: str
    subscriber_email: EmailStr
    sequence_id: str
    step_id: str
    event_type: str  # sent, delivered, opened, clicked, bounced, complained
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
    domain: str | None = None

    @validator("event_type")
    def valid_event_type(cls, v):
        """Validate event type."""
        valid_types = {
            "sent",
            "delivered",
            "opened",
            "clicked",
            "bounced",
            "complained",
            "unsubscribed",
        }
        if v not in valid_types:
            raise ValueError(f"Event type must be one of {valid_types}")
        return v


class Subscriber(BaseModel):
    """Email subscriber model."""

    id: str
    email: EmailStr
    status: SubscriberStatus = SubscriberStatus.ACTIVE
    domain: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    consent_timestamp: datetime | None = None
    unsubscribe_timestamp: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def is_subscribed(self) -> bool:
        """Check if subscriber is subscribed."""
        return self.status == SubscriberStatus.ACTIVE

    def unsubscribe(self) -> None:
        """Mark subscriber as unsubscribed."""
        self.status = SubscriberStatus.UNSUBSCRIBED
        self.unsubscribe_timestamp = datetime.utcnow()

    def can_receive_email(self) -> bool:
        """Check if subscriber can receive emails."""
        return self.status in {SubscriberStatus.ACTIVE, SubscriberStatus.PENDING_CONFIRMATION}
