"""Lead filtering service."""

import logging
from datetime import datetime, timedelta
from typing import Any

from .models import Lead, LeadSource, LeadStatus

logger = logging.getLogger(__name__)


class LeadFilter:
    """Advanced lead filtering service."""

    def __init__(self):
        """Initialize filter."""
        self._leads: list[Lead] = []

    async def add_lead(self, lead: Lead) -> None:
        """Add a lead to the filter database.

        Args:
            lead: Lead to add
        """
        self._leads.append(lead)
        logger.info(f"Added lead {lead.email}")

    async def filter_by_domain(self, domain: str) -> list[Lead]:
        """Filter leads by domain.

        Args:
            domain: Domain to filter by

        Returns:
            List of leads matching domain
        """
        return [l for l in self._leads if l.domain == domain]

    async def filter_by_status(self, status: LeadStatus) -> list[Lead]:
        """Filter leads by status.

        Args:
            status: Status to filter by

        Returns:
            List of leads with matching status
        """
        return [l for l in self._leads if l.status == status]

    async def filter_by_source(self, source: LeadSource) -> list[Lead]:
        """Filter leads by source.

        Args:
            source: Source to filter by

        Returns:
            List of leads from matching source
        """
        return [l for l in self._leads if l.source == source]

    async def filter_by_company(self, company: str) -> list[Lead]:
        """Filter leads by company.

        Args:
            company: Company name

        Returns:
            List of leads from company
        """
        return [l for l in self._leads if l.company and l.company.lower() == company.lower()]

    async def filter_qualified(self) -> list[Lead]:
        """Get all qualified leads.

        Returns:
            List of qualified leads
        """
        return [l for l in self._leads if l.is_qualified()]

    async def filter_hot_leads(self) -> list[Lead]:
        """Get all hot leads.

        Returns:
            List of hot leads
        """
        return [l for l in self._leads if l.is_hot()]

    async def filter_warm_leads(self) -> list[Lead]:
        """Get all warm leads.

        Returns:
            List of warm leads
        """
        return [l for l in self._leads if l.is_warm()]

    async def filter_cold_leads(self) -> list[Lead]:
        """Get all cold leads.

        Returns:
            List of cold leads
        """
        return [l for l in self._leads if l.is_cold()]

    async def filter_with_criteria(self, **criteria) -> list[Lead]:
        """Filter with multiple criteria.

        Args:
            **criteria: Filter criteria (domain, status, score_min, etc.)

        Returns:
            Filtered leads
        """
        results = self._leads

        if "domain" in criteria:
            results = [l for l in results if l.domain == criteria["domain"]]

        if "status" in criteria:
            status = criteria["status"]
            if isinstance(status, str):
                status = LeadStatus[status.upper()]
            results = [l for l in results if l.status == status]

        if "source" in criteria:
            source = criteria["source"]
            if isinstance(source, str):
                source = LeadSource[source.upper()]
            results = [l for l in results if l.source == source]

        if "score_min" in criteria:
            min_score = criteria["score_min"]
            results = [l for l in results if l.score and l.score.total_score >= min_score]

        if "score_max" in criteria:
            max_score = criteria["score_max"]
            results = [l for l in results if l.score and l.score.total_score <= max_score]

        if "company" in criteria:
            company = criteria["company"]
            results = [l for l in results if l.company and company.lower() in l.company.lower()]

        if "created_after" in criteria:
            created_after = criteria["created_after"]
            if isinstance(created_after, str):
                created_after = datetime.fromisoformat(created_after)
            results = [l for l in results if l.created_at >= created_after]

        if "contacted_in_days" in criteria:
            days = criteria["contacted_in_days"]
            since = datetime.utcnow() - timedelta(days=days)
            results = [l for l in results if l.last_contacted_at and l.last_contacted_at >= since]

        return results

    async def get_leads_by_attribute(self, attr_key: str, attr_value: Any) -> list[Lead]:
        """Get leads with specific attribute.

        Args:
            attr_key: Attribute key
            attr_value: Attribute value

        Returns:
            Matching leads
        """
        return [
            l
            for l in self._leads
            if attr_key in l.attributes and l.attributes[attr_key] == attr_value
        ]

    async def get_all_leads(self) -> list[Lead]:
        """Get all leads.

        Returns:
            All leads
        """
        return self._leads.copy()

    async def clear(self) -> None:
        """Clear all leads (for testing)."""
        self._leads.clear()
