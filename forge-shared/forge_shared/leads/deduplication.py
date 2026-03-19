"""Lead deduplication service."""

import logging

from .models import Lead

logger = logging.getLogger(__name__)


class DeduplicationService:
    """Handles lead deduplication."""

    def __init__(self):
        """Initialize deduplication service."""
        self._leads: list[Lead] = []

    async def add_lead(self, lead: Lead) -> tuple[bool, Lead | None]:
        """Add a lead, checking for duplicates.

        Args:
            lead: Lead to add

        Returns:
            Tuple of (is_new, duplicate_lead or None)
        """
        duplicate = await self.find_duplicate(lead)
        if duplicate:
            logger.warning(f"Duplicate lead found: {lead.email} (duplicate of {duplicate.id})")
            return False, duplicate

        self._leads.append(lead)
        logger.info(f"Added new lead: {lead.email}")
        return True, None

    async def find_duplicate(self, lead: Lead) -> Lead | None:
        """Find duplicate lead.

        Args:
            lead: Lead to check

        Returns:
            Duplicate lead if found
        """
        # Check exact email match (highest priority)
        for existing in self._leads:
            if existing.email.lower() == lead.email.lower():
                return existing

        # Check phone number if provided
        if lead.phone:
            for existing in self._leads:
                if existing.phone and existing.phone == lead.phone:
                    return existing

        # Check full name + company combination
        if lead.company:
            full_name = lead.get_full_name().lower()
            company = lead.company.lower()
            for existing in self._leads:
                if (
                    existing.get_full_name().lower() == full_name
                    and existing.company
                    and existing.company.lower() == company
                ):
                    return existing

        return None

    async def find_duplicates_by_email(self, email: str) -> list[Lead]:
        """Find all leads with specific email.

        Args:
            email: Email address

        Returns:
            List of leads with that email
        """
        email_lower = email.lower()
        return [l for l in self._leads if l.email.lower() == email_lower]

    async def find_duplicates_by_phone(self, phone: str) -> list[Lead]:
        """Find all leads with specific phone.

        Args:
            phone: Phone number

        Returns:
            List of leads with that phone
        """
        return [l for l in self._leads if l.phone == phone]

    async def merge_leads(self, primary_id: str, secondary_id: str) -> Lead | None:
        """Merge two leads.

        Args:
            primary_id: Primary lead ID
            secondary_id: Secondary lead ID (to merge into primary)

        Returns:
            Merged lead or None if not found
        """
        primary = None
        secondary = None

        for lead in self._leads:
            if lead.id == primary_id:
                primary = lead
            elif lead.id == secondary_id:
                secondary = lead

        if not primary or not secondary:
            logger.error(f"Could not find leads to merge: {primary_id}, {secondary_id}")
            return None

        # Merge data (prefer primary, fill gaps from secondary)
        if not primary.phone and secondary.phone:
            primary.phone = secondary.phone

        if not primary.title and secondary.title:
            primary.title = secondary.title

        if not primary.company and secondary.company:
            primary.company = secondary.company

        # Merge attributes
        for key, value in secondary.attributes.items():
            if key not in primary.attributes:
                primary.attributes[key] = value

        # Merge UTM params
        for key, value in secondary.utm_params.items():
            if key not in primary.utm_params:
                primary.utm_params[key] = value

        # Remove secondary from list
        self._leads = [l for l in self._leads if l.id != secondary_id]

        logger.info(f"Merged lead {secondary_id} into {primary_id}")
        return primary

    async def get_all_leads(self) -> list[Lead]:
        """Get all leads.

        Returns:
            List of all leads
        """
        return self._leads.copy()

    async def clear(self) -> None:
        """Clear all leads (for testing)."""
        self._leads.clear()
