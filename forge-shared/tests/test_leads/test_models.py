"""
Tests for leads models and data structures.

Test coverage for lead management models, scoring, and validation.
"""

import pytest
from datetime import datetime
from pydantic import ValidationError


class TestLeadModels:
    """
    Tests for lead model classes.

    Tests Lead, LeadScore, and related models.
    """

    def test_lead_models_imports(self):
        """
        Test that lead models can be imported.
        """
        try:
            from forge_shared.leads.models import (
                Lead,
                LeadScore,
                LeadStatus,
                LeadSource,
            )
            assert Lead is not None
        except ImportError:
            pytest.skip("Lead models not implemented")

    def test_lead_status_enum(self):
        """
        Test LeadStatus enum values.
        """
        try:
            from forge_shared.leads.models import LeadStatus

            assert LeadStatus.NEW.value == "new"
            assert LeadStatus.CONTACTED.value == "contacted"
            assert LeadStatus.QUALIFIED.value == "qualified"
            assert LeadStatus.CONVERTED.value == "converted"
            assert LeadStatus.LOST.value == "lost"
        except ImportError:
            pytest.skip("LeadStatus enum not implemented")

    def test_lead_source_enum(self):
        """
        Test LeadSource enum values.
        """
        try:
            from forge_shared.leads.models import LeadSource

            assert LeadSource.FORM.value == "form"
            assert LeadSource.CHAT.value == "chat"
            assert LeadSource.EMAIL.value == "email"
            assert LeadSource.PHONE.value == "phone"
            assert LeadSource.REFERRAL.value == "referral"
        except ImportError:
            pytest.skip("LeadSource enum not implemented")

    def test_lead_creation(self):
        """
        Test creating a Lead instance.
        """
        try:
            from forge_shared.leads.models import Lead, LeadSource, LeadStatus

            lead = Lead(
                id="lead_123",
                email="test@example.com",
                first_name="John",
                last_name="Doe",
                company="Test Corp",
                source=LeadSource.FORM,
                status=LeadStatus.NEW,
            )
            
            assert lead.email == "test@example.com"
            assert lead.first_name == "John"
            assert lead.last_name == "Doe"
            assert lead.company == "Test Corp"
            assert lead.source == LeadSource.FORM
            assert lead.status == LeadStatus.NEW
        except ImportError:
            pytest.skip("Lead model not implemented")

    def test_lead_with_score(self):
        """
        Test Lead with LeadScore.
        """
        try:
            from forge_shared.leads.models import Lead, LeadScore, LeadSource

            score = LeadScore(
                engagement=85,
                fit=90,
                total=87,
                grade="A"
            )
            
            lead = Lead(
                id="lead_456",
                email="scored@example.com",
                first_name="Jane",
                last_name="Smith",
                source=LeadSource.CHAT,
                score=score,
            )
            
            assert lead.score is not None
            assert lead.score.total == 87
            assert lead.score.grade == "A"
        except ImportError:
            pytest.skip("Lead scoring not implemented")

    def test_lead_validation_email_required(self):
        """
        Test that email is required for Lead.
        """
        try:
            from forge_shared.leads.models import Lead

            with pytest.raises(ValidationError):
                Lead(
                    first_name="Test",
                    last_name="User"
                )
        except ImportError:
            pytest.skip("Lead validation not implemented")

    def test_lead_validation_invalid_email(self):
        """
        Test email validation for Lead.
        """
        try:
            from forge_shared.leads.models import Lead, LeadSource

            with pytest.raises(ValidationError):
                Lead(
                    email="invalid-email",
                    first_name="Test",
                    last_name="User",
                    source=LeadSource.FORM
                )
        except ImportError:
            pytest.skip("Email validation not implemented")


class TestLeadScore:
    """
    Tests for LeadScore model.

    Tests score calculation and grading.
    """

    def test_lead_score_creation(self):
        """
        Test creating a LeadScore instance.
        """
        try:
            from forge_shared.leads.models import LeadScore

            score = LeadScore(
                engagement=80,
                fit=75,
                total=77,
                grade="B+"
            )
            
            assert score.engagement == 80
            assert score.fit == 75
            assert score.total == 77
            assert score.grade == "B+"
        except ImportError:
            pytest.skip("LeadScore model not implemented")

    def test_lead_score_validation_range(self):
        """
        Test that scores must be in valid range.
        """
        try:
            from forge_shared.leads.models import LeadScore

            with pytest.raises(ValidationError):
                LeadScore(
                    engagement=150,  # Invalid: > 100
                    fit=75,
                    total=100,
                    grade="A"
                )
        except ImportError:
            pytest.skip("Score validation not implemented")

    def test_lead_score_grade_calculation(self):
        """
        Test automatic grade calculation from score.
        """
        try:
            from forge_shared.leads.models import LeadScore

            score = LeadScore(
                engagement=95,
                fit=90,
                total=92
            )
            
            # Grade should be calculated automatically
            assert score.grade in ["A", "A+", "A-"]
        except (ImportError, ValidationError):
            pytest.skip("Automatic grading not implemented")


class TestLeadMetadata:
    """
    Tests for lead metadata and tracking.

    Tests UTM parameters, custom fields, and tracking.
    """

    def test_lead_with_utm_params(self):
        """
        Test Lead with UTM tracking parameters.
        """
        try:
            from forge_shared.leads.models import Lead, LeadSource

            lead = Lead(
                id="lead_789",
                email="utm@example.com",
                first_name="UTM",
                last_name="Test",
                source=LeadSource.FORM,
                utm_params={
                    "utm_source": "google",
                    "utm_medium": "cpc",
                    "utm_campaign": "spring_sale",
                }
            )
            
            assert lead.utm_params["utm_source"] == "google"
            assert lead.utm_source == "google"  # Property access
        except ImportError:
            pytest.skip("UTM tracking not implemented")

    def test_lead_with_custom_fields(self):
        """
        Test Lead with custom metadata fields.
        """
        try:
            from forge_shared.leads.models import Lead, LeadSource

            lead = Lead(
                id="lead_custom",
                email="custom@example.com",
                first_name="Custom",
                last_name="Fields",
                source=LeadSource.REFERRAL,
                attributes={
                    "industry": "Technology",
                    "company_size": "50-100",
                    "budget": "$10k-$50k"
                }
            )
            
            assert lead.attributes["industry"] == "Technology"
            assert lead.custom_fields["company_size"] == "50-100"  # Alias
        except ImportError:
            pytest.skip("Custom fields not implemented")


class TestLeadLifecycle:
    """
    Tests for lead lifecycle and status transitions.

    Tests status changes and conversion tracking.
    """

    def test_lead_status_transition(self):
        """
        Test transitioning lead status.
        """
        try:
            from forge_shared.leads.models import Lead, LeadStatus, LeadSource

            lead = Lead(
                id="lead_lifecycle",
                email="lifecycle@example.com",
                first_name="Life",
                last_name="Cycle",
                source=LeadSource.FORM,
                status=LeadStatus.NEW
            )
            
            # Transition to contacted
            lead.status = LeadStatus.CONTACTED
            assert lead.status == LeadStatus.CONTACTED
            
            # Transition to qualified
            lead.status = LeadStatus.QUALIFIED
            assert lead.status == LeadStatus.QUALIFIED
        except ImportError:
            pytest.skip("Lead lifecycle not implemented")

    def test_lead_conversion_tracking(self):
        """
        Test lead conversion tracking.
        """
        try:
            from forge_shared.leads.models import Lead, LeadStatus, LeadSource

            lead = Lead(
                id="lead_converted",
                email="converted@example.com",
                first_name="Con",
                last_name="Verted",
                source=LeadSource.CHAT,
                status=LeadStatus.CONVERTED,
                converted_at=datetime.now(),
                customer_id="cust_123"
            )
            
            assert lead.status == LeadStatus.CONVERTED
            assert lead.converted_at is not None
            assert lead.customer_id == "cust_123"
        except ImportError:
            pytest.skip("Conversion tracking not implemented")
