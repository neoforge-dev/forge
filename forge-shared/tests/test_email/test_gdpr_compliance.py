"""GDPR Compliance Tests - 200+ lines.

Comprehensive tests for data export, deletion, consent tracking,
cookie compliance, and privacy controls.
"""

from datetime import datetime

import pytest

from forge_shared.compliance.gdpr import GDPRService
from forge_shared.compliance.models import (
    ConsentRecord,
    ConsentType,
    DataExportFormat,
    DeletionRequest,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def gdpr_service():
    """Create GDPR service fixture."""
    return GDPRService()


# ============================================================================
# Test Consent Management
# ============================================================================


class TestConsentManagement:
    """Test consent tracking and management."""

    @pytest.mark.asyncio
    async def test_record_marketing_consent(self, gdpr_service):
        """Test recording marketing consent."""
        # Arrange
        email = "user@example.com"

        # Act
        record = await gdpr_service.record_consent(
            email=email,
            consent_type=ConsentType.MARKETING,
            given=True,
            source="website",
        )

        # Assert
        assert record is not None
        assert record.email == email
        assert record.consent_type == ConsentType.MARKETING
        assert record.given is True
        assert record.source == "website"

    @pytest.mark.asyncio
    async def test_record_analytics_consent(self, gdpr_service):
        """Test recording analytics consent."""
        # Arrange
        email = "user@example.com"

        # Act
        record = await gdpr_service.record_consent(
            email=email,
            consent_type=ConsentType.ANALYTICS,
            given=True,
            source="email",
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
        )

        # Assert
        assert record.consent_type == ConsentType.ANALYTICS
        assert record.ip_address == "192.168.1.1"
        assert record.user_agent == "Mozilla/5.0"

    @pytest.mark.asyncio
    async def test_record_cookie_consent(self, gdpr_service):
        """Test recording cookie consent."""
        # Arrange
        email = "user@example.com"

        # Act
        record = await gdpr_service.record_consent(
            email=email,
            consent_type=ConsentType.COOKIES,
            given=True,
        )

        # Assert
        assert record.consent_type == ConsentType.COOKIES
        assert record.expires_at is not None

    @pytest.mark.asyncio
    async def test_get_latest_consent(self, gdpr_service):
        """Test getting latest consent record."""
        # Arrange
        email = "user@example.com"
        await gdpr_service.record_consent(
            email=email,
            consent_type=ConsentType.MARKETING,
            given=False,
        )
        await gdpr_service.record_consent(
            email=email,
            consent_type=ConsentType.MARKETING,
            given=True,
        )

        # Act
        record = await gdpr_service.get_consent(email, ConsentType.MARKETING)

        # Assert
        assert record is not None
        assert record.given is True

    @pytest.mark.asyncio
    async def test_has_valid_consent(self, gdpr_service):
        """Test checking if user has valid consent."""
        # Arrange
        email = "user@example.com"

        # Act - No consent initially
        has_consent_1 = await gdpr_service.has_consent(email, ConsentType.MARKETING)

        # Record consent
        await gdpr_service.record_consent(
            email=email,
            consent_type=ConsentType.MARKETING,
            given=True,
        )

        # Act - Has consent
        has_consent_2 = await gdpr_service.has_consent(email, ConsentType.MARKETING)

        # Assert
        assert has_consent_1 is False
        assert has_consent_2 is True

    @pytest.mark.asyncio
    async def test_has_withdrawn_consent(self, gdpr_service):
        """Test checking withdrawn consent."""
        # Arrange
        email = "user@example.com"
        await gdpr_service.record_consent(
            email=email,
            consent_type=ConsentType.MARKETING,
            given=True,
        )

        # Act
        assert await gdpr_service.has_consent(email, ConsentType.MARKETING) is True
        await gdpr_service.withdraw_consent(email, ConsentType.MARKETING)
        has_consent = await gdpr_service.has_consent(email, ConsentType.MARKETING)

        # Assert
        assert has_consent is False

    @pytest.mark.asyncio
    async def test_withdraw_nonexistent_consent(self, gdpr_service):
        """Test withdrawing non-existent consent."""
        # Act
        result = await gdpr_service.withdraw_consent(
            "nonexistent@example.com", ConsentType.MARKETING
        )

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_get_all_consent_records(self, gdpr_service):
        """Test getting all consent records."""
        # Arrange
        email1 = "user1@example.com"
        email2 = "user2@example.com"

        await gdpr_service.record_consent(email1, ConsentType.MARKETING, True)
        await gdpr_service.record_consent(email1, ConsentType.ANALYTICS, True)
        await gdpr_service.record_consent(email2, ConsentType.COOKIES, False)

        # Act
        all_records = await gdpr_service.get_all_consent_records()
        user1_records = await gdpr_service.get_all_consent_records(email=email1)
        user2_records = await gdpr_service.get_all_consent_records(email=email2)

        # Assert
        assert len(all_records) == 3
        assert len(user1_records) == 2
        assert len(user2_records) == 1


# ============================================================================
# Test Data Export
# ============================================================================


class TestDataExport:
    """Test data export functionality."""

    @pytest.mark.asyncio
    async def test_request_data_export(self, gdpr_service):
        """Test requesting data export."""
        # Arrange
        email = "user@example.com"

        # Act
        request = await gdpr_service.request_data_export(
            email=email,
            format=DataExportFormat.JSON,
        )

        # Assert
        assert request is not None
        assert request.email == email
        assert request.format == DataExportFormat.JSON
        assert request.status == "pending"

    @pytest.mark.asyncio
    async def test_get_export_request(self, gdpr_service):
        """Test retrieving export request."""
        # Arrange
        email = "user@example.com"
        request = await gdpr_service.request_data_export(email=email)

        # Act
        retrieved = await gdpr_service.get_export_request(request.id)

        # Assert
        assert retrieved is not None
        assert retrieved.id == request.id
        assert retrieved.email == email

    @pytest.mark.asyncio
    async def test_complete_export_request(self, gdpr_service):
        """Test completing export request."""
        # Arrange
        email = "user@example.com"
        request = await gdpr_service.request_data_export(email=email)
        data = {"name": "John Doe", "email": email}
        download_url = "https://s3.example.com/exports/user_123.json"

        # Act
        result = await gdpr_service.complete_export(request.id, data, download_url, 0.5)

        # Assert
        assert result is True
        completed = await gdpr_service.get_export_request(request.id)
        assert completed.status == "completed"
        assert completed.download_url == download_url
        assert completed.data_size_mb == 0.5

    @pytest.mark.asyncio
    async def test_export_formats(self, gdpr_service):
        """Test export in different formats."""
        # Arrange
        email = "user@example.com"
        data = {"name": "John Doe", "email": email, "age": 30}
        await gdpr_service.store_user_data(email, data)

        # Act - JSON
        json_export = await gdpr_service.export_user_data(email, DataExportFormat.JSON)

        # Act - CSV
        csv_export = await gdpr_service.export_user_data(email, DataExportFormat.CSV)

        # Act - XML
        xml_export = await gdpr_service.export_user_data(email, DataExportFormat.XML)

        # Assert
        assert json_export is not None
        assert "John Doe" in json_export
        assert csv_export is not None
        assert "name,John Doe" in csv_export
        assert xml_export is not None
        assert "<name>John Doe</name>" in xml_export


# ============================================================================
# Test Data Deletion (Right to be Forgotten)
# ============================================================================


class TestDataDeletion:
    """Test data deletion functionality."""

    @pytest.mark.asyncio
    async def test_request_data_deletion(self, gdpr_service):
        """Test requesting data deletion."""
        # Arrange
        email = "user@example.com"
        reason = "User requested deletion"

        # Act
        request = await gdpr_service.request_deletion(email=email, reason=reason)

        # Assert
        assert request is not None
        assert request.email == email
        assert request.reason == reason
        assert request.status == "pending"
        assert request.is_verified() is False

    @pytest.mark.asyncio
    async def test_get_deletion_request(self, gdpr_service):
        """Test retrieving deletion request."""
        # Arrange
        email = "user@example.com"
        request = await gdpr_service.request_deletion(email=email)

        # Act
        retrieved = await gdpr_service.get_deletion_request(request.id)

        # Assert
        assert retrieved is not None
        assert retrieved.id == request.id
        assert retrieved.email == email

    @pytest.mark.asyncio
    async def test_verify_deletion_request(self, gdpr_service):
        """Test verifying deletion request."""
        # Arrange
        email = "user@example.com"
        request = await gdpr_service.request_deletion(email=email)
        request.verification_code = "VERIFY_12345"

        # Act
        result = await gdpr_service.verify_deletion(request.id, "VERIFY_12345")

        # Assert
        assert result is True
        verified_request = await gdpr_service.get_deletion_request(request.id)
        assert verified_request.is_verified() is True

    @pytest.mark.asyncio
    async def test_verify_with_wrong_code(self, gdpr_service):
        """Test verification with wrong code."""
        # Arrange
        email = "user@example.com"
        request = await gdpr_service.request_deletion(email=email)
        request.verification_code = "CORRECT_CODE"

        # Act
        result = await gdpr_service.verify_deletion(request.id, "WRONG_CODE")

        # Assert
        assert result is False
        verified_request = await gdpr_service.get_deletion_request(request.id)
        assert verified_request.is_verified() is False

    @pytest.mark.asyncio
    async def test_complete_deletion(self, gdpr_service):
        """Test completing data deletion."""
        # Arrange
        email = "user@example.com"
        data = {"name": "John Doe", "email": email}
        await gdpr_service.store_user_data(email, data)
        await gdpr_service.record_consent(email, ConsentType.MARKETING, True)

        request = await gdpr_service.request_deletion(email=email)
        request.verification_code = "VERIFY_CODE"
        await gdpr_service.verify_deletion(request.id, "VERIFY_CODE")

        # Act
        result = await gdpr_service.complete_deletion(request.id)

        # Assert
        assert result is True
        deleted_request = await gdpr_service.get_deletion_request(request.id)
        assert deleted_request.is_completed() is True
        assert deleted_request.completed_at is not None

        # Verify data is deleted
        deleted_data = await gdpr_service.get_user_data(email)
        assert deleted_data is None

    @pytest.mark.asyncio
    async def test_cannot_delete_unverified(self, gdpr_service):
        """Test cannot delete unverified requests."""
        # Arrange
        email = "user@example.com"
        request = await gdpr_service.request_deletion(email=email)

        # Act
        result = await gdpr_service.complete_deletion(request.id)

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_deletion_removes_consent_records(self, gdpr_service):
        """Test deletion removes all consent records."""
        # Arrange
        email = "user@example.com"
        await gdpr_service.record_consent(email, ConsentType.MARKETING, True)
        await gdpr_service.record_consent(email, ConsentType.ANALYTICS, True)

        request = await gdpr_service.request_deletion(email=email)
        request.verification_code = "CODE"
        await gdpr_service.verify_deletion(request.id, "CODE")

        # Act
        await gdpr_service.complete_deletion(request.id)

        # Assert
        records = await gdpr_service.get_all_consent_records(email=email)
        assert len(records) == 0


# ============================================================================
# Test User Data Management
# ============================================================================


class TestUserDataManagement:
    """Test user data storage and retrieval."""

    @pytest.mark.asyncio
    async def test_store_user_data(self, gdpr_service):
        """Test storing user data."""
        # Arrange
        email = "user@example.com"
        data = {
            "name": "John Doe",
            "email": email,
            "company": "Acme Corp",
            "phone": "555-1234",
        }

        # Act
        await gdpr_service.store_user_data(email, data)

        # Assert
        retrieved = await gdpr_service.get_user_data(email)
        assert retrieved is not None
        assert retrieved["name"] == "John Doe"
        assert retrieved["company"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_get_nonexistent_user_data(self, gdpr_service):
        """Test getting non-existent user data."""
        # Act
        data = await gdpr_service.get_user_data("nonexistent@example.com")

        # Assert
        assert data is None

    @pytest.mark.asyncio
    async def test_export_json_format(self, gdpr_service):
        """Test exporting data in JSON format."""
        # Arrange
        email = "user@example.com"
        data = {
            "name": "John Doe",
            "email": email,
            "created_at": "2024-01-01",
        }
        await gdpr_service.store_user_data(email, data)

        # Act
        exported = await gdpr_service.export_user_data(email, DataExportFormat.JSON)

        # Assert
        assert exported is not None
        assert "John Doe" in exported
        assert '"name": "John Doe"' in exported

    @pytest.mark.asyncio
    async def test_export_csv_format(self, gdpr_service):
        """Test exporting data in CSV format."""
        # Arrange
        email = "user@example.com"
        data = {"name": "John Doe", "email": email}
        await gdpr_service.store_user_data(email, data)

        # Act
        exported = await gdpr_service.export_user_data(email, DataExportFormat.CSV)

        # Assert
        assert exported is not None
        lines = exported.split("\n")
        assert any("John Doe" in line for line in lines)

    @pytest.mark.asyncio
    async def test_export_xml_format(self, gdpr_service):
        """Test exporting data in XML format."""
        # Arrange
        email = "user@example.com"
        data = {"name": "John Doe", "email": email}
        await gdpr_service.store_user_data(email, data)

        # Act
        exported = await gdpr_service.export_user_data(email, DataExportFormat.XML)

        # Assert
        assert exported is not None
        assert "<?xml" in exported
        assert "<name>" in exported
        assert "John Doe" in exported


# ============================================================================
# Test Compliance Models
# ============================================================================


class TestComplianceModels:
    """Test compliance model validation."""

    def test_consent_record_validation(self):
        """Test consent record validation."""
        # Act & Assert - empty source
        with pytest.raises(ValueError):
            ConsentRecord(
                id="consent_1",
                email="user@example.com",
                consent_type=ConsentType.MARKETING,
                given=True,
                source="",
            )

    def test_deletion_request_status_validation(self):
        """Test deletion request status validation."""
        # Act & Assert - invalid status
        with pytest.raises(ValueError):
            DeletionRequest(
                id="del_1",
                email="user@example.com",
                status="invalid_status",
            )

    def test_deletion_request_is_verified(self):
        """Test deletion request is_verified method."""
        # Arrange
        request = DeletionRequest(
            id="del_1",
            email="user@example.com",
        )

        # Act & Assert
        assert request.is_verified() is False
        request.verified_at = datetime.utcnow()
        assert request.is_verified() is True

    def test_deletion_request_is_completed(self):
        """Test deletion request is_completed method."""
        # Arrange
        request = DeletionRequest(
            id="del_1",
            email="user@example.com",
            status="pending",
        )

        # Act & Assert
        assert request.is_completed() is False
        request.status = "completed"
        assert request.is_completed() is True


# ============================================================================
# Integration Tests
# ============================================================================


class TestGDPRIntegration:
    """Integration tests for GDPR compliance."""

    @pytest.mark.asyncio
    async def test_complete_gdpr_workflow(self, gdpr_service):
        """Test complete GDPR workflow."""
        # Arrange
        email = "user@example.com"
        user_data = {
            "name": "John Doe",
            "email": email,
            "company": "Acme Corp",
            "phone": "555-1234",
        }

        # Store user data
        await gdpr_service.store_user_data(email, user_data)

        # Record consents
        await gdpr_service.record_consent(email, ConsentType.MARKETING, True)
        await gdpr_service.record_consent(email, ConsentType.ANALYTICS, True)
        await gdpr_service.record_consent(email, ConsentType.COOKIES, True)

        # Verify consents are recorded
        assert await gdpr_service.has_consent(email, ConsentType.MARKETING) is True

        # Request data export
        export_request = await gdpr_service.request_data_export(email)
        assert export_request.status == "pending"

        # Complete export
        exported_data = await gdpr_service.export_user_data(email)
        await gdpr_service.complete_export(
            export_request.id, user_data, "https://s3.example.com/export.json", 1.0
        )

        # Verify export is complete
        completed_export = await gdpr_service.get_export_request(export_request.id)
        assert completed_export.status == "completed"

        # Request deletion
        deletion_request = await gdpr_service.request_deletion(email)
        deletion_request.verification_code = "VERIFY_123"

        # Verify deletion
        await gdpr_service.verify_deletion(deletion_request.id, "VERIFY_123")

        # Complete deletion
        await gdpr_service.complete_deletion(deletion_request.id)

        # Verify everything is deleted
        final_data = await gdpr_service.get_user_data(email)
        final_consents = await gdpr_service.get_all_consent_records(email=email)

        assert final_data is None
        assert len(final_consents) == 0
