"""Email Sequence Service Tests - 300+ lines.

Comprehensive tests for sequence creation, scheduling, templating,
personalization, delivery tracking, and error handling.
"""

import pytest

from forge_shared.email.models import (
    EmailEvent,
    EmailSequence,
    EmailTemplate,
    SequenceStep,
    SequenceTrigger,
    Subscriber,
    SubscriberStatus,
)
from forge_shared.email.sender import EmailProvider, EmailSender
from forge_shared.email.sequence import SequenceExecutor, SequenceService, SequenceStorage
from forge_shared.email.tracking import EmailTracker

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def email_tracker():
    """Create email tracker fixture."""
    return EmailTracker()


@pytest.fixture
def email_sender():
    """Create email sender fixture."""
    return EmailSender(provider=EmailProvider.SMTP)


@pytest.fixture
async def sequence_executor(email_sender, email_tracker):
    """Create sequence executor fixture."""
    executor = SequenceExecutor(email_sender, email_tracker)
    return executor


class MockSequenceStorage(SequenceStorage):
    """Mock storage implementation."""

    def __init__(self):
        """Initialize mock storage."""
        self.sequences = {}

    async def get_sequence(self, sequence_id: str) -> EmailSequence | None:
        """Get sequence by ID."""
        return self.sequences.get(sequence_id)

    async def save_sequence(self, sequence: EmailSequence) -> None:
        """Save sequence."""
        self.sequences[sequence.id] = sequence

    async def list_sequences(self, domain: str | None = None):
        """List sequences."""
        sequences = list(self.sequences.values())
        if domain:
            sequences = [s for s in sequences if s.domain == domain]
        return sequences


@pytest.fixture
def mock_storage():
    """Create mock storage fixture."""
    return MockSequenceStorage()


@pytest.fixture
async def sequence_service(mock_storage, sequence_executor):
    """Create sequence service fixture."""
    service = SequenceService(mock_storage, sequence_executor)
    return service


@pytest.fixture
def sample_template():
    """Create sample email template."""
    return EmailTemplate(
        id="template_1",
        name="Welcome Email",
        subject="Welcome {{subscriber_name}}!",
        body_html="<h1>Welcome {{subscriber_name}}</h1><p>Thanks for signing up!</p>",
        body_text="Welcome {{subscriber_name}}! Thanks for signing up!",
        variables={"subscriber_name": "User"},
    )


@pytest.fixture
def sample_subscriber():
    """Create sample subscriber."""
    return Subscriber(
        id="sub_1",
        email="john@example.com",
        status=SubscriberStatus.ACTIVE,
        domain="example.com",
        attributes={"name": "John Doe", "company": "Acme Corp"},
    )


# ============================================================================
# Test Sequence Creation and Initialization
# ============================================================================


class TestSequenceCreation:
    """Test sequence creation and initialization."""

    @pytest.mark.asyncio
    async def test_create_sequence_basic(self, sequence_service):
        """Test basic sequence creation."""
        # Arrange
        name = "Welcome Sequence"
        trigger = SequenceTrigger.SIGNUP

        # Act
        sequence = await sequence_service.create_sequence(name, trigger)

        # Assert
        assert sequence is not None
        assert sequence.name == name
        assert sequence.trigger == trigger
        assert sequence.enabled is True
        assert len(sequence.steps) == 0

    @pytest.mark.asyncio
    async def test_create_sequence_with_domain(self, sequence_service):
        """Test sequence creation with domain filter."""
        # Arrange
        name = "Domain-specific Sequence"
        trigger = SequenceTrigger.PURCHASE
        domain = "saas.example.com"

        # Act
        sequence = await sequence_service.create_sequence(name, trigger, domain=domain)

        # Assert
        assert sequence.domain == domain
        assert sequence.trigger == trigger

    @pytest.mark.asyncio
    async def test_create_multiple_sequences(self, sequence_service):
        """Test creating multiple sequences."""
        # Arrange
        triggers = [
            SequenceTrigger.SIGNUP,
            SequenceTrigger.PURCHASE,
            SequenceTrigger.CART_ABANDONED,
        ]

        # Act
        sequences = []
        for i, trigger in enumerate(triggers):
            seq = await sequence_service.create_sequence(f"Sequence {i}", trigger)
            sequences.append(seq)

        # Assert
        assert len(sequences) == 3
        assert all(s.id for s in sequences)
        assert [s.trigger for s in sequences] == triggers


# ============================================================================
# Test Sequence Steps and Scheduling
# ============================================================================


class TestSequenceSteps:
    """Test sequence steps and scheduling."""

    @pytest.mark.asyncio
    async def test_add_step_to_sequence(self, sequence_service):
        """Test adding a step to a sequence."""
        # Arrange
        sequence = await sequence_service.create_sequence("Test Sequence", SequenceTrigger.SIGNUP)
        template_id = "template_1"
        delay_hours = 24

        # Act
        step = await sequence_service.add_step_to_sequence(sequence.id, template_id, delay_hours)

        # Assert
        assert step is not None
        assert step.template_id == template_id
        assert step.delay_hours == delay_hours
        assert step.order == 0

    @pytest.mark.asyncio
    async def test_add_multiple_steps(self, sequence_service):
        """Test adding multiple steps to a sequence."""
        # Arrange
        sequence = await sequence_service.create_sequence(
            "Multi-step Sequence", SequenceTrigger.SIGNUP
        )

        # Act
        step1 = await sequence_service.add_step_to_sequence(sequence.id, "template_1", 0)
        step2 = await sequence_service.add_step_to_sequence(sequence.id, "template_2", 24)
        step3 = await sequence_service.add_step_to_sequence(sequence.id, "template_3", 72)

        # Assert
        assert step1.order == 0
        assert step2.order == 1
        assert step3.order == 2
        assert step1.delay_hours == 0
        assert step2.delay_hours == 24
        assert step3.delay_hours == 72

    @pytest.mark.asyncio
    async def test_step_with_conditions(self, sequence_service):
        """Test step with execution conditions."""
        # Arrange
        sequence = await sequence_service.create_sequence(
            "Conditional Sequence", SequenceTrigger.PURCHASE
        )
        template_id = "template_premium"
        delay_hours = 1

        # Act
        step = await sequence_service.add_step_to_sequence(sequence.id, template_id, delay_hours)
        step.conditions = {"purchase_amount": 100}

        # Assert
        assert step.conditions["purchase_amount"] == 100

    @pytest.mark.asyncio
    async def test_add_step_to_nonexistent_sequence(self, sequence_service):
        """Test adding step to non-existent sequence."""
        # Act
        step = await sequence_service.add_step_to_sequence("nonexistent_id", "template_1", 24)

        # Assert
        assert step is None


# ============================================================================
# Test Email Templating and Personalization
# ============================================================================


class TestEmailTemplating:
    """Test email templating and personalization."""

    def test_template_render_basic(self, sample_template):
        """Test basic template rendering."""
        # Arrange
        variables = {"subscriber_name": "Alice"}

        # Act
        subject, body_html, body_text = sample_template.render(variables)

        # Assert
        assert "Alice" in subject
        assert "Alice" in body_html
        assert "Alice" in body_text
        assert "Welcome Alice!" in subject

    def test_template_render_multiple_variables(self):
        """Test rendering with multiple variables."""
        # Arrange
        template = EmailTemplate(
            id="template_2",
            name="Purchase Receipt",
            subject="Order #{{order_id}} - {{total_amount}}",
            body_html="<p>Order: {{order_id}}</p><p>Total: {{total_amount}}</p>",
        )
        variables = {"order_id": "ORD-12345", "total_amount": "$99.99"}

        # Act
        subject, body_html, body_text = template.render(variables)

        # Assert
        assert "ORD-12345" in subject
        assert "$99.99" in subject
        assert "ORD-12345" in body_html
        assert "$99.99" in body_html

    def test_template_render_preserves_unmapped_variables(self):
        """Test that unmapped variables are preserved."""
        # Arrange
        template = EmailTemplate(
            id="template_3",
            name="Template",
            subject="Hello {{name}} and {{other_var}}",
            body_html="Content",
        )
        variables = {"name": "John"}

        # Act
        subject, _, _ = template.render(variables)

        # Assert
        assert "John" in subject
        assert "{{other_var}}" in subject

    def test_template_validation_requires_name(self):
        """Test template validation requires name."""
        # Act & Assert
        with pytest.raises(ValueError):
            EmailTemplate(
                id="template_bad",
                name="",
                subject="Subject",
                body_html="Body",
            )

    def test_template_validation_requires_subject(self):
        """Test template validation requires subject."""
        # Act & Assert
        with pytest.raises(ValueError):
            EmailTemplate(
                id="template_bad",
                name="Name",
                subject="",
                body_html="Body",
            )

    def test_template_validation_requires_body_html(self):
        """Test template validation requires body_html."""
        # Act & Assert
        with pytest.raises(ValueError):
            EmailTemplate(
                id="template_bad",
                name="Name",
                subject="Subject",
                body_html="",
            )


# ============================================================================
# Test Subscriber Management
# ============================================================================


class TestSubscriberManagement:
    """Test subscriber management."""

    @pytest.mark.asyncio
    async def test_add_subscriber(self, sequence_service, sample_subscriber):
        """Test adding a subscriber."""
        # Act
        await sequence_service.add_subscriber(sample_subscriber)

        # Assert
        retrieved = await sequence_service.get_subscriber(sample_subscriber.id)
        assert retrieved is not None
        assert retrieved.email == sample_subscriber.email

    @pytest.mark.asyncio
    async def test_subscriber_is_subscribed(self, sample_subscriber):
        """Test subscriber is_subscribed method."""
        # Arrange
        subscriber = sample_subscriber

        # Act & Assert
        assert subscriber.is_subscribed() is True

    @pytest.mark.asyncio
    async def test_subscriber_can_receive_email(self, sample_subscriber):
        """Test subscriber can_receive_email method."""
        # Arrange
        subscriber = sample_subscriber

        # Act & Assert
        assert subscriber.can_receive_email() is True

    @pytest.mark.asyncio
    async def test_unsubscribe_subscriber(self, sequence_service, sample_subscriber):
        """Test unsubscribing a subscriber."""
        # Arrange
        await sequence_service.add_subscriber(sample_subscriber)

        # Act
        result = await sequence_service.unsubscribe(sample_subscriber.id)

        # Assert
        assert result is True
        subscriber = await sequence_service.get_subscriber(sample_subscriber.id)
        assert subscriber.status == SubscriberStatus.UNSUBSCRIBED
        assert subscriber.is_subscribed() is False

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent_subscriber(self, sequence_service):
        """Test unsubscribing non-existent subscriber."""
        # Act
        result = await sequence_service.unsubscribe("nonexistent_id")

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_bounced_subscriber_cannot_receive_email(self):
        """Test bounced subscriber cannot receive emails."""
        # Arrange
        subscriber = Subscriber(
            id="sub_bounced",
            email="bounced@example.com",
            status=SubscriberStatus.BOUNCED,
        )

        # Act & Assert
        assert subscriber.can_receive_email() is False


# ============================================================================
# Test Delivery Tracking
# ============================================================================


class TestDeliveryTracking:
    """Test email delivery tracking."""

    @pytest.mark.asyncio
    async def test_track_sent_event(self, email_tracker):
        """Test tracking sent event."""
        # Arrange
        event = EmailEvent(
            id="event_1",
            subscriber_email="john@example.com",
            sequence_id="seq_1",
            step_id="step_1",
            event_type="sent",
        )

        # Act
        await email_tracker.track_event(event)

        # Assert
        events = await email_tracker.get_events(subscriber_email="john@example.com")
        assert len(events) == 1
        assert events[0].event_type == "sent"

    @pytest.mark.asyncio
    async def test_track_multiple_event_types(self, email_tracker):
        """Test tracking multiple event types."""
        # Arrange
        email = "user@example.com"
        sequence = "seq_123"
        event_types = ["sent", "delivered", "opened", "clicked"]

        # Act
        for i, event_type in enumerate(event_types):
            event = EmailEvent(
                id=f"event_{i}",
                subscriber_email=email,
                sequence_id=sequence,
                step_id="step_1",
                event_type=event_type,
            )
            await email_tracker.track_event(event)

        # Assert
        all_events = await email_tracker.get_events(subscriber_email=email, sequence_id=sequence)
        assert len(all_events) == 4
        assert [e.event_type for e in all_events] == event_types

    @pytest.mark.asyncio
    async def test_get_sequence_analytics(self, email_tracker):
        """Test getting sequence analytics."""
        # Arrange
        sequence_id = "seq_analytics"
        events_data = [
            ("sent", 100),
            ("delivered", 95),
            ("opened", 60),
            ("clicked", 15),
            ("bounced", 5),
        ]

        # Act
        for event_type, count in events_data:
            for i in range(count):
                event = EmailEvent(
                    id=f"event_{event_type}_{i}",
                    subscriber_email=f"user{i}@example.com",
                    sequence_id=sequence_id,
                    step_id="step_1",
                    event_type=event_type,
                )
                await email_tracker.track_event(event)

        analytics = await email_tracker.get_sequence_analytics(sequence_id)

        # Assert
        assert analytics["sent"] == 100
        assert analytics["delivered"] == 95
        assert analytics["opened"] == 60
        assert analytics["clicked"] == 15
        assert analytics["bounced"] == 5
        assert abs(analytics["open_rate"] - 0.60) < 0.01
        assert abs(analytics["click_rate"] - 0.15) < 0.01

    @pytest.mark.asyncio
    async def test_mark_unsubscribed_event(self, email_tracker):
        """Test tracking unsubscribe event."""
        # Arrange
        email = "unsubscriber@example.com"
        sequence_id = "seq_1"

        # Act
        await email_tracker.mark_unsubscribed(email, sequence_id)

        # Assert
        events = await email_tracker.get_events(subscriber_email=email, event_type="unsubscribed")
        assert len(events) == 1
        assert events[0].subscriber_email == email


# ============================================================================
# Test Error Handling and Edge Cases
# ============================================================================


class TestErrorHandling:
    """Test error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_execute_disabled_step(
        self, sequence_executor, sample_subscriber, sample_template
    ):
        """Test executing disabled step."""
        # Arrange
        step = SequenceStep(
            id="step_disabled",
            sequence_id="seq_1",
            template_id="template_1",
            delay_hours=0,
            order=0,
            enabled=False,
        )

        # Act
        result = await sequence_executor.execute_step(step, sample_subscriber, sample_template, {})

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_execute_step_with_unsubscribed_subscriber(
        self, sequence_executor, sample_template
    ):
        """Test executing step with unsubscribed subscriber."""
        # Arrange
        subscriber = Subscriber(
            id="sub_unsub",
            email="unsub@example.com",
            status=SubscriberStatus.UNSUBSCRIBED,
        )
        step = SequenceStep(
            id="step_1",
            sequence_id="seq_1",
            template_id="template_1",
            delay_hours=0,
            order=0,
        )

        # Act
        result = await sequence_executor.execute_step(step, subscriber, sample_template, {})

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_step_condition_not_met(
        self, sequence_executor, sample_subscriber, sample_template
    ):
        """Test step with unmet conditions."""
        # Arrange
        step = SequenceStep(
            id="step_conditional",
            sequence_id="seq_1",
            template_id="template_1",
            delay_hours=0,
            order=0,
            conditions={"user_type": "premium"},
        )
        context = {"user_type": "free"}

        # Act
        result = await sequence_executor.execute_step(
            step, sample_subscriber, sample_template, context
        )

        # Assert
        assert result is False

    def test_sequence_step_delay_validation(self):
        """Test sequence step delay validation."""
        # Act & Assert - negative delay
        with pytest.raises(ValueError):
            SequenceStep(
                id="step_bad",
                sequence_id="seq_1",
                template_id="template_1",
                delay_hours=-1,
                order=0,
            )

        # Act & Assert - too large delay (> 1 year)
        with pytest.raises(ValueError):
            SequenceStep(
                id="step_bad",
                sequence_id="seq_1",
                template_id="template_1",
                delay_hours=8761,
                order=0,
            )

    def test_email_event_validation(self):
        """Test email event validation."""
        # Act & Assert - invalid event type
        with pytest.raises(ValueError):
            EmailEvent(
                id="event_bad",
                subscriber_email="user@example.com",
                sequence_id="seq_1",
                step_id="step_1",
                event_type="invalid_type",
            )

    @pytest.mark.asyncio
    async def test_sequence_service_list_sequences(self, sequence_service):
        """Test listing sequences."""
        # Arrange - create a sequence and verify it can be retrieved
        seq1 = await sequence_service.create_sequence(
            "List Test Seq", SequenceTrigger.SIGNUP, domain="test.com"
        )

        # Act
        all_sequences = await sequence_service.list_sequences()
        test_sequences = await sequence_service.list_sequences(domain="test.com")

        # Assert - at least one sequence should be found
        assert len(all_sequences) >= 1, f"Expected at least 1 sequence, got {len(all_sequences)}"
        assert len(test_sequences) >= 1, (
            f"Expected at least 1 test domain sequence, got {len(test_sequences)}"
        )

        # Verify the created sequence is in the results
        seq_names = [s.name for s in test_sequences]
        assert "List Test Seq" in seq_names

    @pytest.mark.asyncio
    async def test_sequence_status(self, sequence_service):
        """Test getting sequence status."""
        # Arrange
        sequence = await sequence_service.create_sequence("Status Test", SequenceTrigger.SIGNUP)
        await sequence_service.add_step_to_sequence(sequence.id, "template_1", 24)
        await sequence_service.add_step_to_sequence(sequence.id, "template_2", 48)

        # Act
        status = await sequence_service.get_sequence_status(sequence.id)

        # Assert
        assert status["name"] == "Status Test"
        assert status["enabled"] is True
        assert status["steps"] == 2
        assert status["trigger"] == "signup"


# ============================================================================
# Test Subscriber Attributes and Personalization
# ============================================================================


class TestPersonalization:
    """Test personalization with subscriber attributes."""

    def test_subscriber_attributes(self):
        """Test subscriber attributes."""
        # Arrange
        subscriber = Subscriber(
            id="sub_attr",
            email="user@example.com",
            attributes={
                "first_name": "John",
                "last_name": "Doe",
                "company": "Acme",
                "plan": "premium",
            },
        )

        # Act & Assert
        assert subscriber.attributes["first_name"] == "John"
        assert subscriber.attributes["company"] == "Acme"
        assert subscriber.attributes["plan"] == "premium"

    def test_template_with_complex_variables(self):
        """Test template rendering with complex personalization."""
        # Arrange
        template = EmailTemplate(
            id="template_complex",
            name="Personalized",
            subject="Hi {{first_name}}, exclusive {{plan}} offer!",
            body_html="<p>Dear {{first_name}} {{last_name}},</p><p>As a {{plan}} member at {{company}}, you get...</p>",
        )
        variables = {
            "first_name": "Jane",
            "last_name": "Smith",
            "company": "TechCorp",
            "plan": "Enterprise",
        }

        # Act
        subject, body_html, _ = template.render(variables)

        # Assert
        assert "Hi Jane" in subject
        assert "Enterprise" in subject
        assert "Dear Jane Smith" in body_html
        assert "TechCorp" in body_html


# ============================================================================
# Test Email Sending Integration
# ============================================================================


class TestEmailSending:
    """Test email sending integration."""

    @pytest.mark.asyncio
    async def test_email_sender_basic(self, email_sender):
        """Test basic email sending."""
        # Act
        message_id = await email_sender.send(
            to_email="user@example.com",
            subject="Test Email",
            body_html="<p>Test content</p>",
        )

        # Assert
        assert message_id is not None
        assert isinstance(message_id, str)
        assert len(message_id) > 0

    @pytest.mark.asyncio
    async def test_email_sender_validation(self, email_sender):
        """Test email sender validation."""
        # Act & Assert - missing to_email
        with pytest.raises(ValueError):
            await email_sender.send(
                to_email="",
                subject="Test",
                body_html="<p>Test</p>",
            )

        # Act & Assert - missing subject
        with pytest.raises(ValueError):
            await email_sender.send(
                to_email="user@example.com",
                subject="",
                body_html="<p>Test</p>",
            )

        # Act & Assert - missing body_html
        with pytest.raises(ValueError):
            await email_sender.send(
                to_email="user@example.com",
                subject="Test",
                body_html="",
            )

    @pytest.mark.asyncio
    async def test_email_sender_with_different_providers(self):
        """Test email sender with different providers."""
        # Arrange
        providers = [
            EmailProvider.SMTP,
            EmailProvider.SENDGRID,
            EmailProvider.MAILGUN,
            EmailProvider.AWS_SES,
        ]

        # Act & Assert
        for provider in providers:
            sender = EmailSender(provider=provider)
            message_id = await sender.send(
                to_email="test@example.com",
                subject="Test",
                body_html="<p>Test</p>",
            )
            assert message_id is not None


# ============================================================================
# Integration Tests
# ============================================================================


class TestSequenceIntegration:
    """Integration tests for complete sequence workflow."""

    @pytest.mark.asyncio
    async def test_complete_sequence_workflow(
        self, sequence_service, email_tracker, sample_subscriber
    ):
        """Test complete sequence creation and execution workflow."""
        # Arrange
        sequence = await sequence_service.create_sequence(
            "Welcome Series", SequenceTrigger.SIGNUP, domain="example.com"
        )

        step1 = await sequence_service.add_step_to_sequence(sequence.id, "template_welcome", 0)
        step2 = await sequence_service.add_step_to_sequence(sequence.id, "template_onboarding", 24)

        await sequence_service.add_subscriber(sample_subscriber)

        # Act - Track some events
        event1 = EmailEvent(
            id="event_1",
            subscriber_email=sample_subscriber.email,
            sequence_id=sequence.id,
            step_id=step1.id,
            event_type="sent",
        )
        event2 = EmailEvent(
            id="event_2",
            subscriber_email=sample_subscriber.email,
            sequence_id=sequence.id,
            step_id=step1.id,
            event_type="delivered",
        )
        event3 = EmailEvent(
            id="event_3",
            subscriber_email=sample_subscriber.email,
            sequence_id=sequence.id,
            step_id=step1.id,
            event_type="opened",
        )

        await email_tracker.track_event(event1)
        await email_tracker.track_event(event2)
        await email_tracker.track_event(event3)

        # Assert
        status = await sequence_service.get_sequence_status(sequence.id)
        assert status["steps"] == 2

        analytics = await email_tracker.get_sequence_analytics(sequence.id)
        assert analytics["sent"] == 1
        assert analytics["delivered"] == 1
        assert analytics["opened"] == 1
        assert analytics["open_rate"] == 1.0

        subscriber_events = await email_tracker.get_subscriber_events(
            sample_subscriber.email, sequence.id
        )
        assert len(subscriber_events) == 3
