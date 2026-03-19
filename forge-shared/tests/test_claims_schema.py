"""TDD tests for ClaimEnvelope schema.

Tests follow RED-GREEN-TDD approach:
1. Tests fail initially (RED) - feature not implemented
2. Implementation makes tests pass (GREEN)

Reference: docs/research/ARCHITECTURE_STRATEGY_TDD_IMPLEMENTATION_PLAN.md
Task: P1-4 - Claims Submit Schema TDD
"""

import pytest
from pydantic import ValidationError

# Import from the correct module path (verification.claims submodule)
from forge_shared.verification.claims import ClaimEnvelope, RiskLevel


class TestClaimEnvelopeSchema:
    """TDD tests for ClaimEnvelope schema validation."""

    def test_claim_envelope_requires_all_fields(self):
        """Test that ClaimEnvelope requires all mandatory fields."""
        # Missing required fields should fail validation
        with pytest.raises(ValidationError):
            ClaimEnvelope(
                agent_id="test-agent",
                branch="main",
                commit_sha="abc1234",
            )

        # All fields present should validate
        envelope = ClaimEnvelope(
            task_id="TASK_001",
            agent_id="test-agent",
            branch="main",
            commit_sha="abc1234",
            changed_files=["file1.py"],
            checks_run=[{"command": "pytest"}],
            artifacts=["test_report.txt"],
            risk_level=RiskLevel.LOW,
            evidence_digest="a" * 64,
        )
        assert envelope.task_id == "TASK_001"
        assert envelope.agent_id == "test-agent"
        assert envelope.branch == "main"

    def test_claim_envelope_field_validation(self):
        """Test individual field validation rules."""
        # Test short commit_sha fails (less than 7 chars)
        with pytest.raises(ValidationError):
            ClaimEnvelope(
                task_id="TASK_001",
                agent_id="test-agent",
                branch="main",
                commit_sha="abc",  # Only 3 chars
                changed_files=[],
                checks_run=[],
                artifacts=[],
                risk_level=RiskLevel.LOW,
                evidence_digest="a" * 64,
            )

        # Test empty branch fails
        with pytest.raises(ValidationError):
            ClaimEnvelope(
                task_id="TASK_001",
                agent_id="test-agent",
                branch="",
                commit_sha="abc1234",
                changed_files=[],
                checks_run=[],
                artifacts=[],
                risk_level=RiskLevel.LOW,
                evidence_digest="a" * 64,
            )

    def test_risk_level_enum_validation(self):
        """Test that risk_level accepts valid enum values."""
        # Valid risk levels
        for risk in RiskLevel:
            envelope = ClaimEnvelope(
                task_id="TASK_001",
                agent_id="test-agent",
                branch="main",
                commit_sha="abc1234",
                changed_files=[],
                checks_run=[],
                artifacts=[],
                risk_level=risk,
                evidence_digest="a" * 64,
            )
            assert envelope.risk_level == risk

        # Invalid risk level should fail - Pydantic V2 coerces strings to enums
        # So we test with a completely invalid type instead
        with pytest.raises(ValidationError):
            ClaimEnvelope(
                task_id="TASK_001",
                agent_id="test-agent",
                branch="main",
                commit_sha="abc1234",
                changed_files=[],
                checks_run=[],
                artifacts=[],
                risk_level=123,  # Invalid type
                evidence_digest="a" * 64,
            )

    def test_changed_files_is_list_of_strings(self):
        """Test that changed_files accepts list of strings."""
        envelope = ClaimEnvelope(
            task_id="TASK_001",
            agent_id="test-agent",
            branch="main",
            commit_sha="abc1234",
            changed_files=["file1.py", "file2.py"],
            checks_run=[],
            artifacts=[],
            risk_level=RiskLevel.LOW,
            evidence_digest="a" * 64,
        )
        assert isinstance(envelope.changed_files, list)
        assert all(isinstance(f, str) for f in envelope.changed_files)

    def test_checks_run_is_dict_of_command_results(self):
        """Test that checks_run is dict with command results."""
        envelope = ClaimEnvelope(
            task_id="TASK_001",
            agent_id="test-agent",
            branch="main",
            commit_sha="abc1234",
            changed_files=[],
            checks_run=[
                {"command": "pytest", "exit_code": 0},
                {"command": "ruff", "exit_code": 1},
            ],
            artifacts=[],
            risk_level=RiskLevel.LOW,
            evidence_digest="a" * 64,
        )
        assert isinstance(envelope.checks_run, list)
        assert all(isinstance(item, dict) for item in envelope.checks_run)

    def test_artifacts_is_list_of_strings(self):
        """Test that artifacts accepts list of strings."""
        envelope = ClaimEnvelope(
            task_id="TASK_001",
            agent_id="test-agent",
            branch="main",
            commit_sha="abc1234",
            changed_files=[],
            checks_run=[],
            artifacts=["test_report.txt", "screenshot.png"],
            risk_level=RiskLevel.LOW,
            evidence_digest="a" * 64,
        )
        assert isinstance(envelope.artifacts, list)
        assert all(isinstance(a, str) for a in envelope.artifacts)

    def test_evidence_digest_is_sha256_string(self):
        """Test that evidence_digest is SHA256 hash string."""
        # Use a valid 64-character hex string
        valid_sha256 = "a3f5e7a8c942d2a6f4a6b4469ef5c38f9a628e27b8c4e1d5f0a3b7c9e2f8d1a0"
        envelope = ClaimEnvelope(
            task_id="TASK_001",
            agent_id="test-agent",
            branch="main",
            commit_sha="abc1234",
            changed_files=[],
            checks_run=[],
            artifacts=[],
            risk_level=RiskLevel.LOW,
            evidence_digest=valid_sha256,
        )
        assert len(envelope.evidence_digest) == 64  # SHA256 = 64 hex chars
        assert all(c in "0123456789abcdef" for c in envelope.evidence_digest)

    def test_full_valid_envelope(self):
        """Test completely valid ClaimEnvelope."""
        envelope = ClaimEnvelope(
            task_id="TASK_001",
            agent_id="test-agent",
            branch="main",
            commit_sha="abc123def456",
            changed_files=["file1.py", "tests/test_claims_schema.py"],
            checks_run=[
                {"command": "pytest", "exit_code": 0},
                {"command": "ruff", "exit_code": 0},
            ],
            artifacts=["test_report.txt", "logs/"],
            risk_level=RiskLevel.MEDIUM,
            evidence_digest="a" * 64,
        )
        assert envelope.task_id == "TASK_001"
        assert envelope.agent_id == "test-agent"
        assert envelope.branch == "main"
        assert envelope.commit_sha == "abc123def456"
