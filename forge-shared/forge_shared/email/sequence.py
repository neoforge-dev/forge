"""Email sequence service and execution logic."""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from .models import (
    EmailEvent,
    EmailSequence,
    SequenceStep,
    SequenceTrigger,
    Subscriber,
)

logger = logging.getLogger(__name__)


class SequenceStorage(ABC):
    """Abstract base for sequence storage."""

    @abstractmethod
    async def get_sequence(self, sequence_id: str) -> EmailSequence | None:
        """Get sequence by ID."""
        pass

    @abstractmethod
    async def save_sequence(self, sequence: EmailSequence) -> None:
        """Save sequence."""
        pass

    @abstractmethod
    async def list_sequences(self, domain: str | None = None) -> list[EmailSequence]:
        """List all sequences."""
        pass


class SequenceExecutor:
    """Executes email sequence steps."""

    def __init__(self, sender: "EmailSender", tracker: "EmailTracker"):
        """Initialize executor.

        Args:
            sender: Email sender service
            tracker: Email event tracker
        """
        self.sender = sender
        self.tracker = tracker

    async def execute_step(
        self,
        step: SequenceStep,
        subscriber: Subscriber,
        template: "EmailTemplate",
        context: dict[str, Any],
    ) -> bool:
        """Execute a single sequence step.

        Args:
            step: Step to execute
            subscriber: Target subscriber
            template: Email template
            context: Execution context

        Returns:
            True if successful
        """
        if not step.enabled:
            logger.info(f"Step {step.id} is disabled, skipping")
            return False

        if not step.should_execute(context):
            logger.info(f"Step {step.id} conditions not met for {subscriber.email}")
            return False

        if not subscriber.can_receive_email():
            logger.warning(f"Subscriber {subscriber.email} cannot receive emails")
            return False

        try:
            # Render template with subscriber data
            subject, body_html, body_text = template.render(
                {
                    **context,
                    "subscriber_email": subscriber.email,
                    "subscriber_name": subscriber.attributes.get("name", ""),
                }
            )

            # Send email
            message_id = await self.sender.send(
                to_email=subscriber.email,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
            )

            # Track event
            event = EmailEvent(
                id=f"event_{step.sequence_id}_{step.id}_{subscriber.id}",
                subscriber_email=subscriber.email,
                sequence_id=step.sequence_id,
                step_id=step.id,
                event_type="sent",
                metadata={"message_id": message_id, "template_id": template.id},
                domain=subscriber.domain,
            )
            await self.tracker.track_event(event)

            logger.info(f"Successfully sent step {step.id} to {subscriber.email}")
            return True

        except Exception as e:
            logger.error(f"Failed to execute step {step.id} for {subscriber.email}: {str(e)}")
            return False

    async def schedule_step(
        self,
        step: SequenceStep,
        subscriber: Subscriber,
        template: "EmailTemplate",
        context: dict[str, Any],
    ) -> None:
        """Schedule a step for future execution.

        Args:
            step: Step to schedule
            subscriber: Target subscriber
            template: Email template
            context: Execution context
        """
        delay = timedelta(hours=step.delay_hours)
        scheduled_time = datetime.utcnow() + delay

        logger.info(f"Scheduled step {step.id} for {subscriber.email} at {scheduled_time}")


class SequenceService:
    """Service for managing email sequences."""

    def __init__(
        self,
        storage: SequenceStorage,
        executor: SequenceExecutor,
    ):
        """Initialize service.

        Args:
            storage: Sequence storage backend
            executor: Sequence executor
        """
        self.storage = storage
        self.executor = executor
        self._subscribers: dict[str, Subscriber] = {}
        self._sequences: dict[str, EmailSequence] = {}

    async def create_sequence(
        self,
        name: str,
        trigger: SequenceTrigger,
        domain: str | None = None,
    ) -> EmailSequence:
        """Create a new sequence.

        Args:
            name: Sequence name
            trigger: Trigger type
            domain: Optional domain filter

        Returns:
            Created sequence
        """
        sequence_id = f"seq_{int(datetime.utcnow().timestamp())}"
        sequence = EmailSequence(
            id=sequence_id,
            name=name,
            trigger=trigger,
            domain=domain,
        )
        self._sequences[sequence_id] = sequence
        await self.storage.save_sequence(sequence)
        logger.info(f"Created sequence {sequence_id}: {name}")
        return sequence

    async def add_step_to_sequence(
        self,
        sequence_id: str,
        template_id: str,
        delay_hours: int,
    ) -> SequenceStep | None:
        """Add a step to a sequence.

        Args:
            sequence_id: Target sequence
            template_id: Email template
            delay_hours: Delay in hours

        Returns:
            Created step or None if sequence not found
        """
        sequence = self._sequences.get(sequence_id)
        if not sequence:
            sequence = await self.storage.get_sequence(sequence_id)
            if not sequence:
                logger.error(f"Sequence {sequence_id} not found")
                return None
            self._sequences[sequence_id] = sequence

        step = sequence.add_step(template_id, delay_hours)
        await self.storage.save_sequence(sequence)
        logger.info(f"Added step {step.id} to sequence {sequence_id}")
        return step

    async def execute_sequence_for_subscriber(
        self,
        sequence_id: str,
        subscriber: Subscriber,
        context: dict[str, Any],
        template_getter: Callable[[str], "EmailTemplate"],
    ) -> int:
        """Execute all steps in a sequence for a subscriber.

        Args:
            sequence_id: Sequence to execute
            subscriber: Target subscriber
            context: Execution context
            template_getter: Function to get templates by ID

        Returns:
            Number of steps executed
        """
        sequence = self._sequences.get(sequence_id)
        if not sequence:
            sequence = await self.storage.get_sequence(sequence_id)
            if not sequence:
                logger.error(f"Sequence {sequence_id} not found")
                return 0
            self._sequences[sequence_id] = sequence

        if not sequence.enabled:
            logger.warning(f"Sequence {sequence_id} is disabled")
            return 0

        executed_count = 0
        for step in sequence.steps:
            if not step.enabled:
                continue

            template = template_getter(step.template_id)
            if not template:
                logger.warning(f"Template {step.template_id} not found for step {step.id}")
                continue

            success = await self.executor.execute_step(step, subscriber, template, context)
            if success:
                executed_count += 1

        logger.info(f"Executed {executed_count}/{len(sequence.steps)} steps for {subscriber.email}")
        return executed_count

    async def add_subscriber(self, subscriber: Subscriber) -> None:
        """Add a subscriber.

        Args:
            subscriber: Subscriber to add
        """
        self._subscribers[subscriber.id] = subscriber
        logger.info(f"Added subscriber {subscriber.email}")

    async def get_subscriber(self, subscriber_id: str) -> Subscriber | None:
        """Get subscriber by ID.

        Args:
            subscriber_id: Subscriber ID

        Returns:
            Subscriber or None if not found
        """
        return self._subscribers.get(subscriber_id)

    async def unsubscribe(self, subscriber_id: str) -> bool:
        """Unsubscribe a subscriber.

        Args:
            subscriber_id: Subscriber ID

        Returns:
            True if successful
        """
        subscriber = self._subscribers.get(subscriber_id)
        if not subscriber:
            logger.warning(f"Subscriber {subscriber_id} not found")
            return False

        subscriber.unsubscribe()
        logger.info(f"Unsubscribed {subscriber.email}")
        return True

    async def list_sequences(self, domain: str | None = None) -> list[EmailSequence]:
        """List sequences.

        Args:
            domain: Optional domain filter

        Returns:
            List of sequences
        """
        return await self.storage.list_sequences(domain=domain)

    async def get_sequence_status(self, sequence_id: str) -> dict[str, Any]:
        """Get sequence status and metrics.

        Args:
            sequence_id: Sequence ID

        Returns:
            Status dictionary
        """
        sequence = self._sequences.get(sequence_id)
        if not sequence:
            sequence = await self.storage.get_sequence(sequence_id)

        if not sequence:
            return {"error": f"Sequence {sequence_id} not found"}

        return {
            "id": sequence.id,
            "name": sequence.name,
            "enabled": sequence.enabled,
            "steps": len(sequence.steps),
            "trigger": sequence.trigger.value,
            "created_at": sequence.created_at.isoformat(),
        }
