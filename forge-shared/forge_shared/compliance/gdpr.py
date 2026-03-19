"""GDPR compliance service."""

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from .models import ConsentRecord, ConsentType, DataExportFormat, DataExportRequest, DeletionRequest

logger = logging.getLogger(__name__)


class GDPRService:
    """GDPR compliance service."""

    def __init__(self):
        """Initialize GDPR service."""
        self._consent_records: list[ConsentRecord] = []
        self._export_requests: list[DataExportRequest] = []
        self._deletion_requests: list[DeletionRequest] = []
        self._user_data: dict[str, dict[str, Any]] = {}

    async def record_consent(
        self,
        email: str,
        consent_type: ConsentType,
        given: bool,
        source: str = "website",
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> ConsentRecord:
        """Record user consent.

        Args:
            email: User email
            consent_type: Type of consent
            given: Whether consent was given
            source: Source of consent (website, email, etc)
            ip_address: User IP address
            user_agent: User agent string

        Returns:
            ConsentRecord
        """
        record_id = f"consent_{email}_{consent_type.value}_{int(datetime.utcnow().timestamp())}"

        record = ConsentRecord(
            id=record_id,
            email=email,
            consent_type=consent_type,
            given=given,
            source=source,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=datetime.utcnow() + timedelta(days=365),
        )

        self._consent_records.append(record)
        logger.info(f"Recorded {consent_type.value} consent for {email}: {given}")
        return record

    async def get_consent(self, email: str, consent_type: ConsentType) -> ConsentRecord | None:
        """Get latest consent record for user.

        Args:
            email: User email
            consent_type: Type of consent

        Returns:
            ConsentRecord or None
        """
        matching = [
            r for r in self._consent_records if r.email == email and r.consent_type == consent_type
        ]
        if matching:
            return max(matching, key=lambda r: r.timestamp)
        return None

    async def has_consent(self, email: str, consent_type: ConsentType) -> bool:
        """Check if user has given consent.

        Args:
            email: User email
            consent_type: Type of consent

        Returns:
            True if consent was given and not expired
        """
        record = await self.get_consent(email, consent_type)
        if not record:
            return False

        if not record.given:
            return False

        if record.expires_at and record.expires_at < datetime.utcnow():
            return False

        return True

    async def withdraw_consent(self, email: str, consent_type: ConsentType) -> bool:
        """Withdraw user consent.

        Args:
            email: User email
            consent_type: Type of consent

        Returns:
            True if successful
        """
        record = await self.get_consent(email, consent_type)
        if not record:
            logger.warning(f"No consent record found for {email} ({consent_type.value})")
            return False

        record.given = False
        logger.info(f"Withdrew {consent_type.value} consent for {email}")
        return True

    async def request_data_export(
        self,
        email: str,
        format: DataExportFormat = DataExportFormat.JSON,
    ) -> DataExportRequest:
        """Request data export.

        Args:
            email: User email
            format: Export format

        Returns:
            DataExportRequest
        """
        request_id = f"export_{email}_{int(datetime.utcnow().timestamp())}"

        request = DataExportRequest(
            id=request_id,
            email=email,
            format=format,
            status="pending",
            expires_at=datetime.utcnow() + timedelta(days=30),
        )

        self._export_requests.append(request)
        logger.info(f"Created data export request for {email}")
        return request

    async def get_export_request(self, request_id: str) -> DataExportRequest | None:
        """Get export request by ID.

        Args:
            request_id: Request ID

        Returns:
            DataExportRequest or None
        """
        for request in self._export_requests:
            if request.id == request_id:
                return request
        return None

    async def complete_export(
        self,
        request_id: str,
        data: dict[str, Any],
        download_url: str,
        size_mb: float,
    ) -> bool:
        """Complete data export.

        Args:
            request_id: Request ID
            data: Exported data
            download_url: URL to download exported data
            size_mb: Size of exported data in MB

        Returns:
            True if successful
        """
        request = await self.get_export_request(request_id)
        if not request:
            logger.error(f"Export request not found: {request_id}")
            return False

        request.status = "completed"
        request.completed_at = datetime.utcnow()
        request.download_url = download_url
        request.data_size_mb = size_mb

        logger.info(f"Completed export request {request_id} for {request.email}")
        return True

    async def request_deletion(
        self,
        email: str,
        reason: str | None = None,
    ) -> DeletionRequest:
        """Request data deletion (right to be forgotten).

        Args:
            email: User email
            reason: Reason for deletion

        Returns:
            DeletionRequest
        """
        request_id = f"deletion_{email}_{int(datetime.utcnow().timestamp())}"

        request = DeletionRequest(
            id=request_id,
            email=email,
            reason=reason,
            status="pending",
        )

        self._deletion_requests.append(request)
        logger.info(f"Created deletion request for {email}")
        return request

    async def get_deletion_request(self, request_id: str) -> DeletionRequest | None:
        """Get deletion request by ID.

        Args:
            request_id: Request ID

        Returns:
            DeletionRequest or None
        """
        for request in self._deletion_requests:
            if request.id == request_id:
                return request
        return None

    async def verify_deletion(self, request_id: str, verification_code: str) -> bool:
        """Verify deletion request with verification code.

        Args:
            request_id: Request ID
            verification_code: Verification code from email

        Returns:
            True if verified
        """
        request = await self.get_deletion_request(request_id)
        if not request:
            logger.error(f"Deletion request not found: {request_id}")
            return False

        if request.verification_code != verification_code:
            logger.warning(f"Invalid verification code for {request_id}")
            return False

        request.verified_at = datetime.utcnow()
        logger.info(f"Verified deletion request {request_id}")
        return True

    async def complete_deletion(
        self,
        request_id: str,
    ) -> bool:
        """Complete data deletion.

        Args:
            request_id: Request ID

        Returns:
            True if successful
        """
        request = await self.get_deletion_request(request_id)
        if not request:
            logger.error(f"Deletion request not found: {request_id}")
            return False

        if not request.is_verified():
            logger.error(f"Deletion request not verified: {request_id}")
            return False

        # Delete all user data
        if request.email in self._user_data:
            del self._user_data[request.email]

        # Remove consent records
        self._consent_records = [r for r in self._consent_records if r.email != request.email]

        request.status = "completed"
        request.completed_at = datetime.utcnow()

        logger.info(f"Completed deletion for {request.email}")
        return True

    async def store_user_data(self, email: str, data: dict[str, Any]) -> None:
        """Store user data for export/deletion.

        Args:
            email: User email
            data: User data
        """
        self._user_data[email] = data
        logger.info(f"Stored data for {email}")

    async def get_user_data(self, email: str) -> dict[str, Any] | None:
        """Get stored user data.

        Args:
            email: User email

        Returns:
            User data or None
        """
        return self._user_data.get(email)

    async def export_user_data(
        self,
        email: str,
        format: DataExportFormat = DataExportFormat.JSON,
    ) -> str | None:
        """Export user data in specified format.

        Args:
            email: User email
            format: Export format

        Returns:
            Exported data as string
        """
        data = await self.get_user_data(email)
        if not data:
            logger.warning(f"No data found for {email}")
            return None

        if format == DataExportFormat.JSON:
            return json.dumps(data, indent=2, default=str)
        elif format == DataExportFormat.CSV:
            # Simple CSV export
            lines = []
            for key, value in data.items():
                lines.append(f"{key},{value}")
            return "\n".join(lines)
        else:  # XML
            return self._dict_to_xml(data)

    def _dict_to_xml(self, data: dict[str, Any], root: str = "user") -> str:
        """Convert dict to XML.

        Args:
            data: Data to convert
            root: Root element name

        Returns:
            XML string
        """
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        lines.append(f"<{root}>")
        for key, value in data.items():
            lines.append(f"  <{key}>{value}</{key}>")
        lines.append(f"</{root}>")
        return "\n".join(lines)

    async def get_all_consent_records(self, email: str | None = None) -> list[ConsentRecord]:
        """Get all consent records.

        Args:
            email: Optional email filter

        Returns:
            List of consent records
        """
        if email:
            return [r for r in self._consent_records if r.email == email]
        return self._consent_records.copy()

    async def clear(self) -> None:
        """Clear all data (for testing)."""
        self._consent_records.clear()
        self._export_requests.clear()
        self._deletion_requests.clear()
        self._user_data.clear()
        logger.info("Cleared all GDPR data")
