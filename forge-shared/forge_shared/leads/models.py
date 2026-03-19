"""Lead management models."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


class LeadStatus(str, Enum):
    """Lead status enum."""

    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    INTERESTED = "interested"
    NEGOTIATING = "negotiating"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"
    UNQUALIFIED = "unqualified"
    CONVERTED = "converted"
    LOST = "lost"


class LeadSource(str, Enum):
    """Lead source enum."""

    WEBSITE = "website"
    FORM = "form"
    EMAIL = "email"
    SOCIAL = "social"
    REFERRAL = "referral"
    API = "api"
    IMPORT = "import"
    ORGANIC = "organic"
    CHAT = "chat"
    PAID = "paid"
    WEBINAR = "webinar"
    PHONE = "phone"


class LeadScore(BaseModel):
    """Lead scoring model.

    Accepts both short field names (engagement, fit, urgency, total) and long
    legacy names (engagement_score, fit_score, urgency_score, total_score).
    Both naming conventions remain accessible as attributes.
    """

    model_config = ConfigDict(populate_by_name=True)

    engagement: int = Field(
        ge=0,
        le=100,
        validation_alias=AliasChoices("engagement", "engagement_score"),
    )
    fit: int = Field(
        ge=0,
        le=100,
        validation_alias=AliasChoices("fit", "fit_score"),
    )
    urgency: int = Field(
        default=0,
        ge=0,
        le=100,
        validation_alias=AliasChoices("urgency", "urgency_score"),
    )
    total: int = Field(
        ge=0,
        le=300,
        validation_alias=AliasChoices("total", "total_score"),
    )
    grade: str = ""
    calculated_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def compute_grade(self) -> "LeadScore":
        """Auto-calculate grade from total if grade is not explicitly set."""
        if not self.grade:
            t = self.total
            if t >= 90:
                self.grade = "A"
            elif t >= 80:
                self.grade = "B+"
            elif t >= 70:
                self.grade = "B"
            elif t >= 50:
                self.grade = "C"
            else:
                self.grade = "D"
        return self

    # Long-name property aliases for backward compatibility with filtering.py
    # and test_lead_filtering.py which access .engagement_score, etc.
    @property
    def engagement_score(self) -> int:
        """Backward-compatible alias for engagement."""
        return self.engagement

    @property
    def fit_score(self) -> int:
        """Backward-compatible alias for fit."""
        return self.fit

    @property
    def urgency_score(self) -> int:
        """Backward-compatible alias for urgency."""
        return self.urgency

    @property
    def total_score(self) -> int:
        """Backward-compatible alias for total."""
        return self.total


class Lead(BaseModel):
    """Lead model."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    email: EmailStr
    first_name: str
    last_name: str
    company: str | None = None
    domain: str | None = None
    status: LeadStatus = LeadStatus.NEW
    source: LeadSource
    phone: str | None = None
    title: str | None = None
    score: LeadScore | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    utm_params: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_contacted_at: datetime | None = None
    converted_at: datetime | None = None
    customer_id: str | None = None

    @property
    def custom_fields(self) -> dict[str, Any]:
        """Alias for attributes for backward compatibility."""
        return self.attributes

    @property
    def utm_source(self) -> str | None:
        """Get utm_source from utm_params."""
        return self.utm_params.get("utm_source")

    @property
    def utm_medium(self) -> str | None:
        """Get utm_medium from utm_params."""
        return self.utm_params.get("utm_medium")

    @property
    def utm_campaign(self) -> str | None:
        """Get utm_campaign from utm_params."""
        return self.utm_params.get("utm_campaign")

    @field_validator("first_name", "last_name")
    @classmethod
    def names_not_empty(cls, v: str) -> str:
        """Validate names are not empty."""
        if not v or not v.strip():
            raise ValueError("Name cannot be empty")
        return v.strip()

    def get_full_name(self) -> str:
        """Get full name."""
        return f"{self.first_name} {self.last_name}"

    def is_qualified(self) -> bool:
        """Check if lead is qualified."""
        return self.status in {
            LeadStatus.QUALIFIED,
            LeadStatus.INTERESTED,
            LeadStatus.NEGOTIATING,
            LeadStatus.CLOSED_WON,
            LeadStatus.CONVERTED,
        }

    def is_hot(self) -> bool:
        """Check if lead is hot (high score)."""
        if not self.score:
            return False
        return self.score.total >= 200

    def is_warm(self) -> bool:
        """Check if lead is warm (medium score)."""
        if not self.score:
            return False
        return 100 <= self.score.total < 200

    def is_cold(self) -> bool:
        """Check if lead is cold (low score)."""
        if not self.score:
            return True
        return self.score.total < 100
