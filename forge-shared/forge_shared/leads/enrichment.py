"""Lead enrichment service."""

import logging
from typing import Any

from .models import Lead, LeadScore

logger = logging.getLogger(__name__)


class LeadEnrichmentService:
    """Service for enriching lead data."""

    def __init__(self):
        """Initialize enrichment service."""
        pass

    async def calculate_lead_score(self, lead: Lead) -> LeadScore:
        """Calculate lead score based on attributes.

        Args:
            lead: Lead to score

        Returns:
            LeadScore object
        """
        engagement_score = self._calculate_engagement(lead)
        fit_score = self._calculate_fit(lead)
        urgency_score = self._calculate_urgency(lead)
        total_score = engagement_score + fit_score + urgency_score

        return LeadScore(
            engagement_score=engagement_score,
            fit_score=fit_score,
            urgency_score=urgency_score,
            total_score=total_score,
        )

    def _calculate_engagement(self, lead: Lead) -> int:
        """Calculate engagement score (0-100).

        Args:
            lead: Lead to score

        Returns:
            Engagement score
        """
        score = 0

        # Website visits
        if "website_visits" in lead.attributes:
            visits = lead.attributes["website_visits"]
            score += min(visits * 10, 50)

        # Email opens
        if "email_opens" in lead.attributes:
            opens = lead.attributes["email_opens"]
            score += min(opens * 5, 25)

        # Email clicks
        if "email_clicks" in lead.attributes:
            clicks = lead.attributes["email_clicks"]
            score += min(clicks * 10, 25)

        return min(score, 100)

    def _calculate_fit(self, lead: Lead) -> int:
        """Calculate fit score (0-100).

        Args:
            lead: Lead to score

        Returns:
            Fit score
        """
        score = 0

        # Company size match
        if "company_size" in lead.attributes:
            size = lead.attributes["company_size"]
            if size in ["50-200", "200-1000", "1000-5000"]:
                score += 40

        # Industry match
        if "industry" in lead.attributes:
            industry = lead.attributes["industry"]
            if industry in ["saas", "fintech", "healthtech"]:
                score += 30

        # Budget fit
        if "budget" in lead.attributes:
            budget = lead.attributes["budget"]
            if budget and budget >= 50000:
                score += 30

        return min(score, 100)

    def _calculate_urgency(self, lead: Lead) -> int:
        """Calculate urgency score (0-100).

        Args:
            lead: Lead to score

        Returns:
            Urgency score
        """
        score = 0

        # Request for demo
        if "requested_demo" in lead.attributes and lead.attributes["requested_demo"]:
            score += 40

        # Number of interactions
        if "interaction_count" in lead.attributes:
            count = lead.attributes["interaction_count"]
            score += min(count * 5, 40)

        # Recently active
        if "recently_active" in lead.attributes and lead.attributes["recently_active"]:
            score += 20

        return min(score, 100)

    async def enrich_with_attributes(self, lead: Lead, attributes: dict[str, Any]) -> Lead:
        """Add attributes to a lead.

        Args:
            lead: Lead to enrich
            attributes: Attributes to add

        Returns:
            Enriched lead
        """
        for key, value in attributes.items():
            if key not in lead.attributes:
                lead.attributes[key] = value

        logger.info(f"Enriched lead {lead.email} with {len(attributes)} attributes")
        return lead

    async def enrich_with_utm(self, lead: Lead, utm_params: dict[str, str]) -> Lead:
        """Add UTM parameters to a lead.

        Args:
            lead: Lead to enrich
            utm_params: UTM parameters

        Returns:
            Enriched lead
        """
        for key, value in utm_params.items():
            lead.utm_params[key] = value

        logger.info(f"Enriched lead {lead.email} with UTM params")
        return lead

    async def score_and_enrich(
        self,
        lead: Lead,
        attributes: dict[str, Any] | None = None,
        utm_params: dict[str, str] | None = None,
    ) -> Lead:
        """Score and enrich a lead in one operation.

        Args:
            lead: Lead to process
            attributes: Optional attributes to add
            utm_params: Optional UTM parameters

        Returns:
            Processed lead
        """
        # Add attributes
        if attributes:
            lead = await self.enrich_with_attributes(lead, attributes)

        # Add UTM params
        if utm_params:
            lead = await self.enrich_with_utm(lead, utm_params)

        # Calculate score
        lead.score = await self.calculate_lead_score(lead)

        logger.info(f"Scored lead {lead.email}: {lead.score.total_score} points")
        return lead
