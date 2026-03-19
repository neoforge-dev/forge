"""Lead Management API Tests - 150+ lines.

Comprehensive tests for lead filtering, scoring, deduplication,
enrichment, and API endpoints.
"""

import pytest

from forge_shared.leads.deduplication import DeduplicationService
from forge_shared.leads.enrichment import LeadEnrichmentService
from forge_shared.leads.filtering import LeadFilter
from forge_shared.leads.models import Lead, LeadScore, LeadSource, LeadStatus
from forge_shared.leads.router import LeadRouter

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def lead_filter():
    """Create lead filter fixture."""
    return LeadFilter()


@pytest.fixture
def dedup_service():
    """Create deduplication service fixture."""
    return DeduplicationService()


@pytest.fixture
def enrichment_service():
    """Create enrichment service fixture."""
    return LeadEnrichmentService()


@pytest.fixture
def lead_router(lead_filter, dedup_service, enrichment_service):
    """Create lead router fixture."""
    return LeadRouter(lead_filter, dedup_service, enrichment_service)


@pytest.fixture
def sample_lead():
    """Create sample lead."""
    return Lead(
        id="lead_1",
        email="john@example.com",
        first_name="John",
        last_name="Doe",
        company="Acme Corp",
        domain="example.com",
        phone="555-1234",
        title="CTO",
        source=LeadSource.WEBSITE,
    )


@pytest.fixture
def hot_lead():
    """Create hot lead with high score."""
    lead = Lead(
        id="lead_hot",
        email="hotlead@example.com",
        first_name="Hot",
        last_name="Lead",
        company="TechCorp",
        source=LeadSource.FORM,
        status=LeadStatus.INTERESTED,
    )
    lead.score = LeadScore(
        engagement_score=85,
        fit_score=90,
        urgency_score=80,
        total_score=255,
    )
    return lead


# ============================================================================
# Test Lead Filtering by Domain
# ============================================================================


class TestLeadFilteringByDomain:
    """Test lead filtering by domain."""

    @pytest.mark.asyncio
    async def test_filter_leads_by_domain(self, lead_filter):
        """Test filtering leads by domain."""
        # Arrange
        lead1 = Lead(
            id="lead_1",
            email="user1@example.com",
            first_name="User",
            last_name="One",
            domain="example.com",
            source=LeadSource.WEBSITE,
        )
        lead2 = Lead(
            id="lead_2",
            email="user2@example.com",
            first_name="User",
            last_name="Two",
            domain="example.com",
            source=LeadSource.WEBSITE,
        )
        lead3 = Lead(
            id="lead_3",
            email="user3@other.com",
            first_name="User",
            last_name="Three",
            domain="other.com",
            source=LeadSource.FORM,
        )

        await lead_filter.add_lead(lead1)
        await lead_filter.add_lead(lead2)
        await lead_filter.add_lead(lead3)

        # Act
        example_leads = await lead_filter.filter_by_domain("example.com")
        other_leads = await lead_filter.filter_by_domain("other.com")

        # Assert
        assert len(example_leads) == 2
        assert len(other_leads) == 1
        assert example_leads[0].domain == "example.com"
        assert other_leads[0].domain == "other.com"

    @pytest.mark.asyncio
    async def test_filter_empty_domain(self, lead_filter, sample_lead):
        """Test filtering by non-existent domain."""
        # Arrange
        await lead_filter.add_lead(sample_lead)

        # Act
        results = await lead_filter.filter_by_domain("nonexistent.com")

        # Assert
        assert len(results) == 0


# ============================================================================
# Test Lead Scoring
# ============================================================================


class TestLeadScoring:
    """Test lead scoring functionality."""

    @pytest.mark.asyncio
    async def test_calculate_engagement_score(self, enrichment_service):
        """Test engagement score calculation."""
        # Arrange
        lead = Lead(
            id="lead_engagement",
            email="user@example.com",
            first_name="Test",
            last_name="User",
            source=LeadSource.WEBSITE,
            attributes={
                "website_visits": 5,
                "email_opens": 3,
                "email_clicks": 2,
            },
        )

        # Act
        score = await enrichment_service.calculate_lead_score(lead)

        # Assert
        assert score.engagement_score > 0
        assert score.engagement_score <= 100

    @pytest.mark.asyncio
    async def test_calculate_fit_score(self, enrichment_service):
        """Test fit score calculation."""
        # Arrange
        lead = Lead(
            id="lead_fit",
            email="user@example.com",
            first_name="Test",
            last_name="User",
            source=LeadSource.WEBSITE,
            attributes={
                "company_size": "200-1000",
                "industry": "saas",
                "budget": 100000,
            },
        )

        # Act
        score = await enrichment_service.calculate_lead_score(lead)

        # Assert
        assert score.fit_score > 0
        assert score.fit_score <= 100

    @pytest.mark.asyncio
    async def test_hot_lead_detection(self, hot_lead):
        """Test hot lead detection."""
        # Act & Assert
        assert hot_lead.is_hot() is True
        assert hot_lead.is_warm() is False
        assert hot_lead.is_cold() is False

    @pytest.mark.asyncio
    async def test_warm_lead_detection(self):
        """Test warm lead detection."""
        # Arrange
        lead = Lead(
            id="lead_warm",
            email="warm@example.com",
            first_name="Warm",
            last_name="Lead",
            source=LeadSource.FORM,
        )
        lead.score = LeadScore(
            engagement_score=40,
            fit_score=40,
            urgency_score=40,
            total_score=120,
        )

        # Act & Assert
        assert lead.is_warm() is True
        assert lead.is_hot() is False
        assert lead.is_cold() is False

    @pytest.mark.asyncio
    async def test_cold_lead_detection(self):
        """Test cold lead detection."""
        # Arrange
        lead = Lead(
            id="lead_cold",
            email="cold@example.com",
            first_name="Cold",
            last_name="Lead",
            source=LeadSource.WEBSITE,
        )

        # Act & Assert
        assert lead.is_cold() is True
        assert lead.is_hot() is False
        assert lead.is_warm() is False


# ============================================================================
# Test Lead Deduplication
# ============================================================================


class TestLeadDeduplication:
    """Test lead deduplication."""

    @pytest.mark.asyncio
    async def test_detect_duplicate_email(self, dedup_service):
        """Test duplicate detection by email."""
        # Arrange
        lead1 = Lead(
            id="lead_1",
            email="duplicate@example.com",
            first_name="John",
            last_name="Doe",
            source=LeadSource.WEBSITE,
        )
        lead2 = Lead(
            id="lead_2",
            email="duplicate@example.com",
            first_name="Jane",
            last_name="Smith",
            source=LeadSource.FORM,
        )

        # Act
        is_new1, dup1 = await dedup_service.add_lead(lead1)
        is_new2, dup2 = await dedup_service.add_lead(lead2)

        # Assert
        assert is_new1 is True
        assert dup1 is None
        assert is_new2 is False
        assert dup2 is not None
        assert dup2.id == "lead_1"

    @pytest.mark.asyncio
    async def test_detect_duplicate_phone(self, dedup_service):
        """Test duplicate detection by phone."""
        # Arrange
        lead1 = Lead(
            id="lead_1",
            email="user1@example.com",
            first_name="User",
            last_name="One",
            phone="555-1234",
            source=LeadSource.WEBSITE,
        )
        lead2 = Lead(
            id="lead_2",
            email="user2@example.com",
            first_name="User",
            last_name="Two",
            phone="555-1234",
            source=LeadSource.FORM,
        )

        # Act
        is_new1, _ = await dedup_service.add_lead(lead1)
        is_new2, dup2 = await dedup_service.add_lead(lead2)

        # Assert
        assert is_new1 is True
        assert is_new2 is False
        assert dup2.id == "lead_1"

    @pytest.mark.asyncio
    async def test_find_duplicates_by_email(self, dedup_service):
        """Test finding duplicates by email."""
        # Arrange
        email = "test@example.com"
        lead = Lead(
            id="lead_1",
            email=email,
            first_name="Test",
            last_name="User",
            source=LeadSource.WEBSITE,
        )
        await dedup_service.add_lead(lead)

        # Act
        duplicates = await dedup_service.find_duplicates_by_email(email)

        # Assert
        assert len(duplicates) == 1
        assert duplicates[0].email == email

    @pytest.mark.asyncio
    async def test_merge_leads(self, dedup_service):
        """Test merging two leads."""
        # Arrange
        lead1 = Lead(
            id="lead_primary",
            email="primary@example.com",
            first_name="Primary",
            last_name="Lead",
            company="Company A",
            source=LeadSource.WEBSITE,
            attributes={"website_visits": 5},
        )
        lead2 = Lead(
            id="lead_secondary",
            email="secondary@example.com",
            first_name="Secondary",
            last_name="Lead",
            phone="555-1234",
            source=LeadSource.FORM,
            attributes={"email_opens": 3},
        )

        await dedup_service.add_lead(lead1)
        await dedup_service.add_lead(lead2)

        # Act
        merged = await dedup_service.merge_leads("lead_primary", "lead_secondary")

        # Assert
        assert merged is not None
        assert merged.id == "lead_primary"
        assert merged.phone == "555-1234"
        assert "website_visits" in merged.attributes
        assert "email_opens" in merged.attributes


# ============================================================================
# Test Lead Enrichment
# ============================================================================


class TestLeadEnrichment:
    """Test lead enrichment."""

    @pytest.mark.asyncio
    async def test_enrich_with_attributes(self, enrichment_service):
        """Test enriching lead with attributes."""
        # Arrange
        lead = Lead(
            id="lead_1",
            email="user@example.com",
            first_name="User",
            last_name="Name",
            source=LeadSource.WEBSITE,
        )
        attributes = {
            "company_size": "100-500",
            "budget": 50000,
            "industry": "saas",
        }

        # Act
        enriched = await enrichment_service.enrich_with_attributes(lead, attributes)

        # Assert
        assert enriched.attributes["company_size"] == "100-500"
        assert enriched.attributes["budget"] == 50000
        assert enriched.attributes["industry"] == "saas"

    @pytest.mark.asyncio
    async def test_enrich_with_utm(self, enrichment_service):
        """Test enriching lead with UTM parameters."""
        # Arrange
        lead = Lead(
            id="lead_1",
            email="user@example.com",
            first_name="User",
            last_name="Name",
            source=LeadSource.WEBSITE,
        )
        utm_params = {
            "utm_source": "google",
            "utm_medium": "cpc",
            "utm_campaign": "summer_sale",
        }

        # Act
        enriched = await enrichment_service.enrich_with_utm(lead, utm_params)

        # Assert
        assert enriched.utm_params["utm_source"] == "google"
        assert enriched.utm_params["utm_medium"] == "cpc"
        assert enriched.utm_params["utm_campaign"] == "summer_sale"

    @pytest.mark.asyncio
    async def test_score_and_enrich(self, enrichment_service):
        """Test scoring and enriching lead."""
        # Arrange
        lead = Lead(
            id="lead_1",
            email="user@example.com",
            first_name="User",
            last_name="Name",
            source=LeadSource.WEBSITE,
        )
        attributes = {
            "website_visits": 10,
            "company_size": "1000-5000",
        }
        utm_params = {"utm_source": "google"}

        # Act
        enriched = await enrichment_service.score_and_enrich(lead, attributes, utm_params)

        # Assert
        assert enriched.score is not None
        assert enriched.score.total_score > 0
        assert enriched.attributes["website_visits"] == 10
        assert enriched.utm_params["utm_source"] == "google"


# ============================================================================
# Test Lead API Endpoints
# ============================================================================


class TestLeadRouter:
    """Test lead router API endpoints."""

    @pytest.mark.asyncio
    async def test_create_lead_endpoint(self, lead_router):
        """Test creating lead via API endpoint."""
        # Act
        result = await lead_router.create_lead(
            email="newlead@example.com",
            first_name="New",
            last_name="Lead",
            source="website",
            company="TechCorp",
        )

        # Assert
        assert result["success"] is True
        assert result["lead_id"] is not None
        assert result["email"] == "newlead@example.com"

    @pytest.mark.asyncio
    async def test_create_duplicate_lead_endpoint(self, lead_router):
        """Test creating duplicate lead via API endpoint."""
        # Arrange
        await lead_router.create_lead(
            email="duplicate@example.com",
            first_name="First",
            last_name="Lead",
            source="website",
        )

        # Act
        result = await lead_router.create_lead(
            email="duplicate@example.com",
            first_name="Second",
            last_name="Lead",
            source="form",
        )

        # Assert
        assert result["success"] is False
        assert "Duplicate" in result["error"]

    @pytest.mark.asyncio
    async def test_get_leads_by_domain(self, lead_router):
        """Test getting leads by domain."""
        # Arrange
        await lead_router.create_lead(
            email="user1@example.com",
            first_name="User",
            last_name="One",
            source="website",
            domain="example.com",
        )
        await lead_router.create_lead(
            email="user2@example.com",
            first_name="User",
            last_name="Two",
            source="website",
            domain="example.com",
        )

        # Act
        leads = await lead_router.get_leads_by_domain("example.com")

        # Assert
        assert len(leads) == 2

    @pytest.mark.asyncio
    async def test_multi_domain_filtering_isolation(self, lead_router):
        """Test that domain filtering isolates leads across multiple domains (E15-MA-2)."""
        # Arrange: leads from 3 different domains (portfolio multi-domain scenario)
        await lead_router.create_lead(
            email="user@codeswiftr.com",
            first_name="Code",
            last_name="User",
            source="website",
            domain="codeswiftr.com",
        )
        await lead_router.create_lead(
            email="cto@leanvibe.dev",
            first_name="CTO",
            last_name="Lead",
            source="form",
            domain="leanvibe.dev",
        )
        await lead_router.create_lead(
            email="dev@neoforge.dev",
            first_name="Dev",
            last_name="Lead",
            source="website",
            domain="neoforge.dev",
        )

        # Act: filter each domain
        codeswiftr = await lead_router.get_leads_by_domain("codeswiftr.com")
        leanvibe = await lead_router.get_leads_by_domain("leanvibe.dev")
        neoforge = await lead_router.get_leads_by_domain("neoforge.dev")
        other = await lead_router.get_leads_by_domain("other.com")

        # Assert: each domain returns only its leads
        assert len(codeswiftr) == 1
        assert codeswiftr[0].domain == "codeswiftr.com"
        assert len(leanvibe) == 1
        assert leanvibe[0].domain == "leanvibe.dev"
        assert len(neoforge) == 1
        assert neoforge[0].domain == "neoforge.dev"
        assert len(other) == 0

    @pytest.mark.asyncio
    async def test_get_qualified_leads(self, lead_router, dedup_service, lead_filter):
        """Test getting qualified leads."""
        # Arrange
        lead = Lead(
            id="lead_qualified",
            email="qualified@example.com",
            first_name="Qualified",
            last_name="Lead",
            status=LeadStatus.INTERESTED,
            source=LeadSource.FORM,
        )
        await dedup_service.add_lead(lead)
        await lead_filter.add_lead(lead)

        # Act
        qualified = await lead_router.get_qualified_leads()

        # Assert
        assert len(qualified) > 0
        assert any(l.status == LeadStatus.INTERESTED for l in qualified)

    @pytest.mark.asyncio
    async def test_get_hot_leads(self, lead_router, dedup_service, lead_filter, hot_lead):
        """Test getting hot leads."""
        # Arrange
        await dedup_service.add_lead(hot_lead)
        await lead_filter.add_lead(hot_lead)

        # Act
        hot_leads = await lead_router.get_hot_leads()

        # Assert
        assert len(hot_leads) > 0
        assert hot_leads[0].is_hot() is True

    @pytest.mark.asyncio
    async def test_score_lead_endpoint(self, lead_router):
        """Test scoring lead via endpoint."""
        # Arrange
        create_result = await lead_router.create_lead(
            email="toscore@example.com",
            first_name="To",
            last_name="Score",
            source="website",
        )
        lead_id = create_result["lead_id"]

        # Act
        score_result = await lead_router.score_lead(
            lead_id,
            attributes={
                "website_visits": 5,
                "company_size": "100-500",
            },
        )

        # Assert
        assert score_result["success"] is True
        assert "score" in score_result
        assert score_result["score"]["total"] > 0

    @pytest.mark.asyncio
    async def test_search_leads(self, lead_router, dedup_service, lead_filter):
        """Test searching leads with criteria."""
        # Arrange
        lead = Lead(
            id="lead_search",
            email="search@example.com",
            first_name="Search",
            last_name="Lead",
            company="SearchCorp",
            domain="search.com",
            status=LeadStatus.INTERESTED,
            source=LeadSource.FORM,
        )
        lead.score = LeadScore(
            engagement_score=60,
            fit_score=70,
            urgency_score=50,
            total_score=180,
        )
        await dedup_service.add_lead(lead)
        await lead_filter.add_lead(lead)

        # Act
        results = await lead_router.search_leads(
            domain="search.com",
            score_min=100,
            status="interested",
        )

        # Assert
        assert len(results) > 0
        assert results[0].domain == "search.com"


# ============================================================================
# Test Lead Model Validation
# ============================================================================


class TestLeadValidation:
    """Test lead model validation."""

    def test_lead_requires_names(self):
        """Test lead requires first and last names."""
        # Act & Assert
        with pytest.raises(ValueError):
            Lead(
                id="bad_lead",
                email="user@example.com",
                first_name="",
                last_name="Last",
                source=LeadSource.WEBSITE,
            )

    def test_lead_full_name(self, sample_lead):
        """Test getting full name."""
        # Act
        full_name = sample_lead.get_full_name()

        # Assert
        assert full_name == "John Doe"

    def test_lead_qualification_status(self):
        """Test lead qualification status methods."""
        # Arrange
        unqualified = Lead(
            id="unq",
            email="unq@example.com",
            first_name="Un",
            last_name="Qualified",
            status=LeadStatus.NEW,
            source=LeadSource.WEBSITE,
        )
        qualified = Lead(
            id="q",
            email="q@example.com",
            first_name="Qualified",
            last_name="Lead",
            status=LeadStatus.INTERESTED,
            source=LeadSource.WEBSITE,
        )

        # Act & Assert
        assert unqualified.is_qualified() is False
        assert qualified.is_qualified() is True
