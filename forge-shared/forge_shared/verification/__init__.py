"""Verification module for FORGE claims and evidence tracking.

Provides claim verification and evidence tracking for the FORGE ecosystem.

Example:
    ```python
    from forge_shared.verification import VerificationClaim

    claim = VerificationClaim(
        id="claim-123",
        type="test_result",
        evidence={"test_output": "passed"}
    )
    ```
"""

from .models import (
    VerificationClaim,
    VerificationEvidence,
    VerificationType,
    VerificationStatus,
)

__all__ = [
    "VerificationClaim",
    "VerificationEvidence",
    "VerificationType",
    "VerificationStatus",
]
