"""Lead API endpoints router."""

import logging

from .deduplication import DeduplicationService
from .enrichment import LeadEnrichmentService
from .filtering import LeadFilter
from .models import Lead, LeadSource, LeadStatus

logger = logging.getLogger(__name__)


class LeadRouter:
    """API router for lead endpoints."""

    def __init__(
        self,
        lead_filter: LeadFilter,
        dedup_service: DeduplicationService,
        enrichment_service: LeadEnrichmentService,
    ):
        """Initialize router.

        Args:
            lead_filter: Lead filtering service
            dedup_service: Deduplication service
            enrichment_service: Enrichment service
        """
        self.lead_filter = lead_filter
        self.dedup_service = dedup_service
        self.enrichment_service = enrichment_service

    async def create_lead(
        self,
        email: str,
        first_name: str,
        last_name: str,
        source: str,
        company: str | None = None,
        domain: str | None = None,
        phone: str | None = None,
        title: str | None = None,
    ) -> dict:
        """Create a new lead.

        Args:
            email: Lead email
            first_name: First name
            last_name: Last name
            source: Lead source
            company: Company name
            domain: Domain filter
            phone: Phone number
            title: Job title

        Returns:
            Created lead or error
        """
        try:
            lead_source = LeadSource[source.upper()]
            lead = Lead(
                id=f"lead_{hash(email)}",
                email=email,
                first_name=first_name,
                last_name=last_name,
                source=lead_source,
                company=company,
                domain=domain,
                phone=phone,
                title=title,
            )

            is_new, duplicate = await self.dedup_service.add_lead(lead)

            if not is_new:
                logger.warning(f"Duplicate lead: {email}")
                return {
                    "success": False,
                    "error": "Duplicate lead",
                    "duplicate_id": duplicate.id,
                }

            await self.lead_filter.add_lead(lead)

            logger.info(f"Created lead: {email}")
            return {
                "success": True,
                "lead_id": lead.id,
                "email": lead.email,
            }

        except KeyError:
            logger.error(f"Invalid source: {source}")
            return {"success": False, "error": f"Invalid source: {source}"}
        except Exception as e:
            logger.error(f"Error creating lead: {str(e)}")
            return {"success": False, "error": str(e)}

    async def get_leads_by_domain(self, domain: str) -> list[Lead]:
        """Get leads by domain.

        Args:
            domain: Domain to filter by

        Returns:
            List of leads
        """
        return await self.lead_filter.filter_by_domain(domain)

    async def get_leads_by_status(self, status: str) -> list[Lead]:
        """Get leads by status.

        Args:
            status: Status to filter by

        Returns:
            List of leads
        """
        lead_status = LeadStatus[status.upper()]
        return await self.lead_filter.filter_by_status(lead_status)

    async def get_qualified_leads(self) -> list[Lead]:
        """Get all qualified leads.

        Returns:
            List of qualified leads
        """
        return await self.lead_filter.filter_qualified()

    async def get_hot_leads(self) -> list[Lead]:
        """Get all hot leads.

        Returns:
            List of hot leads
        """
        return await self.lead_filter.filter_hot_leads()

    async def score_lead(self, lead_id: str, attributes: dict | None = None) -> dict:
        """Score a lead.

        Args:
            lead_id: Lead ID
            attributes: Lead attributes

        Returns:
            Score result
        """
        # Get lead from dedup service
        leads = await self.dedup_service.get_all_leads()
        lead = next((l for l in leads if l.id == lead_id), None)

        if not lead:
            logger.error(f"Lead not found: {lead_id}")
            return {"success": False, "error": "Lead not found"}

        # Enrich and score
        if attributes:
            lead = await self.enrichment_service.enrich_with_attributes(lead, attributes)

        score = await self.enrichment_service.calculate_lead_score(lead)
        lead.score = score

        return {
            "success": True,
            "lead_id": lead_id,
            "score": {
                "engagement": score.engagement_score,
                "fit": score.fit_score,
                "urgency": score.urgency_score,
                "total": score.total_score,
            },
        }

    async def search_leads(self, **criteria) -> list[Lead]:
        """Search leads with criteria.

        Args:
            **criteria: Search criteria

        Returns:
            Matching leads
        """
        return await self.lead_filter.filter_with_criteria(**criteria)
