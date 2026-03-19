"""COPPA compliance utilities for children's data protection.

COPPA (Children's Online Privacy Protection Act) compliance module
for handling child data with proper age verification, parental consent,
and data minimization.

The Zero-Data Architecture approach: collect no data = no COPPA burden.
This module provides compliance utilities for apps that do collect child data.
"""

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class AgeGroup(str, Enum):
    """Age groups for COPPA compliance."""

    CHILD_UNDER_13 = "under_13"
    CHILD_13_PLUS = "13_plus"
    TEEN = "teen"  # 13-16
    ADULT = "adult"


class ConsentStatus(str, Enum):
    """Parental consent status."""

    PENDING = "pending"
    VERIFIED = "verified"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


class DataRetentionPolicy(str, Enum):
    """Data retention policies for child data."""

    IMMEDIATE_DELETE = "immediate"  # Delete after processing
    SHORT_TERM = "short_term"  # 24 hours
    MEDIUM_TERM = "medium_term"  # 7 days
    LONG_TERM = "long_term"  # 30 days (requires explicit consent)


class ChildProfile(BaseModel):
    """Model for child profile (minimized data)."""

    child_id: str = Field(..., description="Internal UUID, not PII")
    parent_account_id: str = Field(..., description="Parent account reference")
    age: int = Field(..., ge=0, le=18, description="Child's age")
    grade_level: int | None = Field(None, ge=0, le=12, description="School grade level")
    display_name: str | None = Field(
        None, description="Child's chosen display name (not real name)"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    consent_status: ConsentStatus = Field(default=ConsentStatus.PENDING)
    consent_timestamp: datetime | None = Field(None)
    consent_method: str | None = Field(None, description="How consent was obtained")
    preferences: dict[str, Any] = Field(default_factory=dict)


class ParentalConsent(BaseModel):
    """Parental consent record for COPPA compliance."""

    id: str = Field(..., description="Unique consent ID")
    parent_account_id: str = Field(..., description="Parent account")
    child_id: str = Field(..., description="Child this consent applies to")
    consent_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    consent_method: str = Field(
        ...,
        description="Consent method: email, credit_card, mail, phone, or digital_signature",
    )
    consent_proof: str | None = Field(
        None, description="Reference to consent proof (email, transaction ID)"
    )
    terms_version: str = Field(..., description="Version of terms accepted")
    expires_at: datetime = Field(
        ..., description="Consent expiration (COPPA recommends annual re-consent)"
    )
    status: ConsentStatus = Field(default=ConsentStatus.VERIFIED)
    ip_address: str | None = Field(None, description="IP address of consent provider")
    user_agent: str | None = Field(None)


class COPPAComplianceService:
    """COPPA compliance service for child data handling.

    This service implements COPPA requirements including:
    - Age verification
    - Parental consent management
    - Data retention limits
    - Data minimization
    - Consent expiration and renewal
    """

    # COPPA-required retention limits
    AUDIO_RETENTION_HOURS = 24
    TRANSCRIPT_RETENTION_HOURS = 24
    SESSION_DATA_RETENTION_DAYS = 30
    METRICS_RETENTION_DAYS = 365  # Aggregated metrics can be kept longer

    # Minimum ages for different consent methods (COPPA safe harbor)
    CONSENT_METHOD_AGES = {
        "email": 13,  # Email verification for 13+
        "credit_card": 16,  # Credit card verification for 16+
        "mail": 13,  # Mail consent for 13+
        "phone": 13,  # Phone consent for 13+
        "digital_signature": 13,  # Digital signature for 13+
        "parent_email": 0,  # Parent email consent for any age
    }

    def __init__(self):
        """Initialize COPPA compliance service."""
        self._consents: dict[str, ParentalConsent] = {}
        self._profiles: dict[str, ChildProfile] = {}

    def verify_age(
        self, birth_date: datetime | None = None, age: int | None = None
    ) -> tuple[bool, AgeGroup]:
        """Verify user age for COPPA compliance.

        Args:
            birth_date: User's birth date
            age: User's age (alternative to birth_date)

        Returns:
            Tuple of (is_verified, age_group)
        """
        if age is None and birth_date is None:
            return False, AgeGroup.CHILD_UNDER_13

        if age is None:
            today = datetime.now(UTC).date()
            age = (
                today.year
                - birth_date.year
                - ((today.month, today.day) < (birth_date.month, birth_date.day))
            )

        if age < 13:
            return True, AgeGroup.CHILD_UNDER_13
        elif age < 16:
            return True, AgeGroup.TEEN
        else:
            return True, AgeGroup.ADULT

    def is_minor(self, age: int | None = None, birth_date: datetime | None = None) -> bool:
        """Check if user is a minor (under 13).

        Args:
            age: User's age
            birth_date: User's birth date

        Returns:
            True if user is under 13
        """
        is_verified, age_group = self.verify_age(age=age, birth_date=birth_date)
        return age_group == AgeGroup.CHILD_UNDER_13

    def validate_parental_consent(
        self,
        consent: ParentalConsent,
        current_terms_version: str,
    ) -> bool:
        """Validate parental consent is current and valid.

        Args:
            consent: Parental consent record
            current_terms_version: Current version of terms

        Returns:
            True if consent is valid
        """
        # Check consent status
        if consent.status != ConsentStatus.VERIFIED:
            return False

        # Check terms version
        if consent.terms_version != current_terms_version:
            return False

        # Check expiration
        if datetime.now(UTC) > consent.expires_at:
            return False

        return True

    def should_delete_audio(self, upload_timestamp: datetime) -> bool:
        """Check if audio should be deleted based on retention policy.

        COPPA requires deletion of audio/voice recordings when no longer needed.

        Args:
            upload_timestamp: When audio was uploaded

        Returns:
            True if audio should be deleted
        """
        if upload_timestamp.tzinfo is None:
            upload_timestamp = upload_timestamp.replace(tzinfo=UTC)

        retention_period = timedelta(hours=self.AUDIO_RETENTION_HOURS)
        deletion_time = upload_timestamp + retention_period
        return datetime.now(UTC) >= deletion_time

    def should_delete_transcript(self, creation_timestamp: datetime) -> bool:
        """Check if transcript should be deleted based on retention policy.

        Args:
            creation_timestamp: When transcript was created

        Returns:
            True if transcript should be deleted
        """
        if creation_timestamp.tzinfo is None:
            creation_timestamp = creation_timestamp.replace(tzinfo=UTC)

        retention_period = timedelta(hours=self.TRANSCRIPT_RETENTION_HOURS)
        deletion_time = creation_timestamp + retention_period
        return datetime.now(UTC) >= deletion_time

    def should_delete_session_data(self, session_timestamp: datetime) -> bool:
        """Check if session data should be deleted based on retention policy.

        Args:
            session_timestamp: When session occurred

        Returns:
            True if session data should be deleted
        """
        if session_timestamp.tzinfo is None:
            session_timestamp = session_timestamp.replace(tzinfo=UTC)

        retention_period = timedelta(days=self.SESSION_DATA_RETENTION_DAYS)
        deletion_time = session_timestamp + retention_period
        return datetime.now(UTC) >= deletion_time

    def sanitize_child_data(
        self,
        data: dict[str, Any],
        retain_metrics: bool = True,
    ) -> dict[str, Any]:
        """Sanitize child data to remove PII, keeping only aggregated metrics.

        This implements data minimization principle required by COPPA.

        Args:
            data: Full session data
            retain_metrics: Whether to retain aggregated metrics

        Returns:
            Sanitized data with PII removed
        """
        # Fields that can always be retained (non-PII)
        safe_fields = {
            "child_id",
            "session_id",
            "session_timestamp",
        }

        # Aggregated metrics that can be retained
        metric_fields = {
            "wpm",  # Words per minute
            "accuracy_percent",
            "expression_score",
            "duration_seconds",
            "completion_percent",
            "grade_level_estimate",
            "practice_minutes",
            "sessions_completed",
            "streak_days",
        }

        # Fields that must always be removed (PII)
        pii_fields = {
            "name",
            "real_name",
            "email",
            "phone",
            "address",
            "ip_address",
            "audio_url",
            "audio_recording",
            "voice_recording",
            "transcript",
            "raw_audio",
            "video_recording",
            "photo",
            "social_media",
            "ssn",
            "birth_date",
            "full_birth_date",
        }

        sanitized = {}

        for key, value in data.items():
            key_lower = key.lower()

            # Always remove PII
            if any(pii in key_lower for pii in pii_fields):
                continue

            # Keep safe fields
            if key in safe_fields:
                sanitized[key] = value
                continue

            # Keep metrics if requested
            if retain_metrics and key in metric_fields:
                sanitized[key] = value
                continue

            # For any other field, check if it's a dict we should recurse into
            if isinstance(value, dict):
                sanitized_sub = self.sanitize_child_data(value, retain_metrics)
                if sanitized_sub:
                    sanitized[key] = sanitized_sub

        return sanitized

    def get_retention_policy(self, age: int, consent_status: ConsentStatus) -> DataRetentionPolicy:
        """Determine appropriate data retention policy based on age and consent.

        Args:
            age: Child's age
            consent_status: Status of parental consent

        Returns:
            Appropriate retention policy
        """
        if consent_status != ConsentStatus.VERIFIED:
            return DataRetentionPolicy.IMMEDIATE_DELETE

        if age < 10:
            # Younger children: stricter retention
            return DataRetentionPolicy.SHORT_TERM
        elif age < 13:
            # Older children: medium retention with consent
            return DataRetentionPolicy.MEDIUM_TERM
        else:
            # Teens (13+): standard retention
            return DataRetentionPolicy.LONG_TERM

    def calculate_consent_expiry(self, consent_timestamp: datetime) -> datetime:
        """Calculate consent expiration date.

        COPPA recommends annual re-consent.

        Args:
            consent_timestamp: When consent was given

        Returns:
            Expiration datetime
        """
        return consent_timestamp + timedelta(days=365)

    def is_consent_required(self, age: int | None = None) -> bool:
        """Determine if parental consent is required.

        Args:
            age: User's age

        Returns:
            True if parental consent is required
        """
        if age is None:
            return True  # Assume consent needed if age unknown
        return age < 13

    def get_age_appropriate_config(self, age: int, config_type: str) -> dict[str, Any]:
        """Get age-appropriate configuration for UI/content.

        Args:
            age: Child's age
            config_type: Type of config (ui, content, feedback)

        Returns:
            Age-appropriate configuration
        """
        if age <= 5:
            return self._get_preschool_config(config_type)
        elif age <= 8:
            return self._get_early_elementary_config(config_type)
        elif age <= 10:
            return self._get_late_elementary_config(config_type)
        elif age <= 12:
            return self._get_preteen_config(config_type)
        else:
            return self._get_teen_config(config_type)

    def _get_preschool_config(self, config_type: str) -> dict[str, Any]:
        """Configuration for ages 3-5."""
        if config_type == "ui":
            return {
                "touch_target_min_size": 75,  # points
                "text_min_size": 24,  # points
                "complexity": "minimal",
                "animation": "frequent",
                "emoji_usage": "frequent",
                "button_style": "large_rounded",
                "colors": "bright_primary",
            }
        elif config_type == "content":
            return {
                "vocabulary_level": "simple",
                "sentence_length_max": 8,
                "illustration_style": "cartoon",
                "interactive_elements": "many",
            }
        elif config_type == "feedback":
            return {
                "encouragement_frequency": "very_high",
                "celebration": "elaborate",
                "error_handling": "gentle_redirect",
                "progress_visibility": "immediate",
            }
        return {}

    def _get_early_elementary_config(self, config_type: str) -> dict[str, Any]:
        """Configuration for ages 6-8."""
        if config_type == "ui":
            return {
                "touch_target_min_size": 56,  # points
                "text_min_size": 18,  # points
                "complexity": "low",
                "animation": "moderate",
                "emoji_usage": "frequent",
                "button_style": "rounded",
                "colors": "bright_primary",
            }
        elif config_type == "content":
            return {
                "vocabulary_level": "simple",
                "sentence_length_max": 12,
                "illustration_style": "illustrated",
                "interactive_elements": "standard",
            }
        elif config_type == "feedback":
            return {
                "encouragement_frequency": "high",
                "celebration": "enthusiastic",
                "error_handling": "supportive",
                "progress_visibility": "frequent",
            }
        return {}

    def _get_late_elementary_config(self, config_type: str) -> dict[str, Any]:
        """Configuration for ages 9-10."""
        if config_type == "ui":
            return {
                "touch_target_min_size": 48,  # points
                "text_min_size": 16,  # points
                "complexity": "medium",
                "animation": "minimal",
                "emoji_usage": "occasional",
                "button_style": "rounded",
                "colors": "modern_primary",
            }
        elif config_type == "content":
            return {
                "vocabulary_level": "moderate",
                "sentence_length_max": 16,
                "illustration_style": "modern_illustrated",
                "interactive_elements": "focused",
            }
        elif config_type == "feedback":
            return {
                "encouragement_frequency": "balanced",
                "celebration": "positive",
                "error_handling": "informative",
                "progress_visibility": "regular",
            }
        return {}

    def _get_preteen_config(self, config_type: str) -> dict[str, Any]:
        """Configuration for ages 11-12."""
        if config_type == "ui":
            return {
                "touch_target_min_size": 44,  # points
                "text_min_size": 14,  # points
                "complexity": "medium_high",
                "animation": "minimal",
                "emoji_usage": "subtle",
                "button_style": "standard",
                "colors": "sophisticated",
            }
        elif config_type == "content":
            return {
                "vocabulary_level": "advanced",
                "sentence_length_max": 20,
                "illustration_style": "sophisticated",
                "interactive_elements": "focused",
            }
        elif config_type == "feedback":
            return {
                "encouragement_frequency": "moderate",
                "celebration": "subtle",
                "error_handling": "analytical",
                "progress_visibility": "on_demand",
            }
        return {}

    def _get_teen_config(self, config_type: str) -> dict[str, Any]:
        """Configuration for ages 13+."""
        if config_type == "ui":
            return {
                "touch_target_min_size": 44,  # points
                "text_min_size": 14,  # points
                "complexity": "standard",
                "animation": "minimal",
                "emoji_usage": "user_preference",
                "button_style": "standard",
                "colors": "standard",
            }
        elif config_type == "content":
            return {
                "vocabulary_level": "advanced",
                "sentence_length_max": "standard",
                "illustration_style": "minimal",
                "interactive_elements": "standard",
            }
        elif config_type == "feedback":
            return {
                "encouragement_frequency": "low",
                "celebration": "subtle",
                "error_handling": "direct",
                "progress_visibility": "on_demand",
            }
        return {}

    # CRUD operations for consent management
    async def record_consent(self, consent: ParentalConsent) -> None:
        """Record parental consent.

        Args:
            consent: Parental consent record
        """
        self._consents[consent.id] = consent
        # Also update child's consent status
        if consent.child_id in self._profiles:
            profile = self._profiles[consent.child_id]
            profile.consent_status = consent.status
            profile.consent_timestamp = consent.consent_timestamp
            profile.consent_method = consent.consent_method

    async def get_consent(self, consent_id: str) -> ParentalConsent | None:
        """Get consent record by ID.

        Args:
            consent_id: Consent ID

        Returns:
            Consent record or None
        """
        return self._consents.get(consent_id)

    async def get_child_consents(self, child_id: str) -> list[ParentalConsent]:
        """Get all consents for a child.

        Args:
            child_id: Child ID

        Returns:
            List of consent records
        """
        return [c for c in self._consents.values() if c.child_id == child_id]

    async def withdraw_consent(self, consent_id: str) -> bool:
        """Withdraw parental consent.

        Args:
            consent_id: Consent ID

        Returns:
            True if successful
        """
        if consent_id not in self._consents:
            return False

        consent = self._consents[consent_id]
        consent.status = ConsentStatus.WITHDRAWN

        # Update child's profile
        if consent.child_id in self._profiles:
            self._profiles[consent.child_id].consent_status = ConsentStatus.WITHDRAWN

        return True

    async def create_child_profile(
        self, parent_account_id: str, age: int, **kwargs
    ) -> ChildProfile:
        """Create a new child profile.

        Args:
            parent_account_id: Parent account reference
            age: Child's age
            **kwargs: Additional profile fields

        Returns:
            Created profile
        """
        import uuid

        profile = ChildProfile(
            child_id=str(uuid.uuid4()),
            parent_account_id=parent_account_id,
            age=age,
            **kwargs,
        )
        self._profiles[profile.child_id] = profile
        return profile

    async def get_child_profile(self, child_id: str) -> ChildProfile | None:
        """Get child profile by ID.

        Args:
            child_id: Child ID

        Returns:
            Profile or None
        """
        return self._profiles.get(child_id)

    async def get_children_by_parent(self, parent_account_id: str) -> list[ChildProfile]:
        """Get all children for a parent account.

        Args:
            parent_account_id: Parent account ID

        Returns:
            List of child profiles
        """
        return [p for p in self._profiles.values() if p.parent_account_id == parent_account_id]


# Singleton instance
coppa_service = COPPAComplianceService()
