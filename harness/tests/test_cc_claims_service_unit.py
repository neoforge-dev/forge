"""Comprehensive unit tests for ClaimsService.

Tests every public method, edge case, and error path.
All external I/O (file system) is mocked via tmp_path or patching.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from forge_harness.webhook_server.models.claim import (
    CheckResult,
    ClaimStatus,
    ClaimTransitionError,
    CompletionClaim,
    EvidenceManifest,
)
from forge_harness.webhook_server.services.claims_service import (
    _DEFAULT_CLAIMS_PATH,
    ClaimsService,
    get_claims_service,
    reset_claims_service,
)

# ---------------------------------------------------------------------------
# Shared helpers / factories
# ---------------------------------------------------------------------------

def make_evidence(
    task_id: str = "IS-001",
    agent_id: str = "forge:nova",
    commit_sha: str = "abc1234",
    changed_files: list[str] | None = None,
    all_pass: bool = True,
) -> EvidenceManifest:
    """Build an EvidenceManifest with all checks passing or failing."""
    if changed_files is None:
        changed_files = ["src/main.py"]
    checks = {
        "tests": CheckResult(passed=all_pass),
        "lint": CheckResult(passed=all_pass),
        "typecheck": CheckResult(passed=all_pass),
    }
    return EvidenceManifest(
        task_id=task_id,
        agent_id=agent_id,
        commit_sha=commit_sha,
        changed_files=changed_files,
        checks=checks,
    )


def make_service(tmp_path: Path) -> ClaimsService:
    """Return a fresh ClaimsService backed by a temp file."""
    return ClaimsService(storage_path=tmp_path / "claims.jsonl")


# ===========================================================================
# class TestClaimsServiceInit
# ===========================================================================


class TestClaimsServiceInit:
    """Tests for ClaimsService.__init__."""

    def test_default_path_attribute(self, tmp_path):
        svc = make_service(tmp_path)
        assert svc._path == tmp_path / "claims.jsonl"

    def test_initial_state_not_loaded(self, tmp_path):
        svc = make_service(tmp_path)
        assert svc._loaded is False
        assert svc._claims == {}

    def test_custom_path_stored(self, tmp_path):
        custom = tmp_path / "sub" / "custom.jsonl"
        svc = ClaimsService(storage_path=custom)
        assert svc._path == custom

    def test_lock_is_threading_lock(self, tmp_path):
        svc = make_service(tmp_path)
        assert hasattr(svc._lock, "acquire")


# ===========================================================================
# class TestEnsureLoaded
# ===========================================================================


class TestEnsureLoaded:
    """Tests for the lazy-load behaviour via _ensure_loaded / _load."""

    def test_load_called_once_on_missing_file(self, tmp_path):
        svc = make_service(tmp_path)
        svc._ensure_loaded()
        assert svc._loaded is True
        assert svc._claims == {}

    def test_load_not_called_again_after_first(self, tmp_path):
        svc = make_service(tmp_path)
        svc._ensure_loaded()
        # Mark dirty to detect re-execution
        svc._claims["sentinel"] = MagicMock()
        svc._ensure_loaded()  # Should be a no-op
        assert "sentinel" in svc._claims

    def test_missing_file_treated_as_empty(self, tmp_path):
        path = tmp_path / "nonexistent.jsonl"
        svc = ClaimsService(storage_path=path)
        svc._load()
        assert svc._loaded is True
        assert svc._claims == {}


# ===========================================================================
# class TestLoad
# ===========================================================================


class TestLoad:
    """Tests for _load — reading JSONL from disk."""

    def test_load_single_valid_claim(self, tmp_path):
        evidence = make_evidence()
        claim = CompletionClaim(
            task_id="IS-001",
            agent_id="forge:nova",
            status=ClaimStatus.SUBMITTED,
            evidence=evidence,
            submitted_at=datetime.now(UTC),
        )
        path = tmp_path / "claims.jsonl"
        path.write_text(claim.model_dump_json() + "\n", encoding="utf-8")

        svc = ClaimsService(storage_path=path)
        svc._load()

        assert claim.claim_id in svc._claims
        loaded = svc._claims[claim.claim_id]
        assert loaded.task_id == "IS-001"
        assert loaded.agent_id == "forge:nova"

    def test_load_multiple_claims(self, tmp_path):
        claims = []
        lines = []
        for i in range(3):
            evidence = make_evidence(task_id=f"IS-00{i}")
            c = CompletionClaim(
                task_id=f"IS-00{i}",
                agent_id="forge:nova",
                status=ClaimStatus.SUBMITTED,
                evidence=evidence,
                submitted_at=datetime.now(UTC),
            )
            claims.append(c)
            lines.append(c.model_dump_json())

        path = tmp_path / "claims.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        svc = ClaimsService(storage_path=path)
        svc._load()

        assert len(svc._claims) == 3
        for c in claims:
            assert c.claim_id in svc._claims

    def test_load_skips_blank_lines(self, tmp_path):
        evidence = make_evidence()
        claim = CompletionClaim(
            task_id="IS-001",
            agent_id="forge:nova",
            status=ClaimStatus.SUBMITTED,
            evidence=evidence,
            submitted_at=datetime.now(UTC),
        )
        path = tmp_path / "claims.jsonl"
        path.write_text(
            "\n" + claim.model_dump_json() + "\n\n",
            encoding="utf-8",
        )

        svc = ClaimsService(storage_path=path)
        svc._load()

        assert len(svc._claims) == 1

    def test_load_skips_malformed_json_lines(self, tmp_path):
        evidence = make_evidence()
        good = CompletionClaim(
            task_id="IS-001",
            agent_id="forge:nova",
            status=ClaimStatus.SUBMITTED,
            evidence=evidence,
            submitted_at=datetime.now(UTC),
        )
        path = tmp_path / "claims.jsonl"
        path.write_text(
            "NOT VALID JSON\n" + good.model_dump_json() + "\n",
            encoding="utf-8",
        )

        svc = ClaimsService(storage_path=path)
        svc._load()

        # The good claim should be loaded; malformed line is skipped
        assert len(svc._claims) == 1
        assert good.claim_id in svc._claims

    def test_load_skips_valid_json_but_invalid_claim(self, tmp_path):
        evidence = make_evidence()
        good = CompletionClaim(
            task_id="IS-002",
            agent_id="forge:nova",
            status=ClaimStatus.SUBMITTED,
            evidence=evidence,
            submitted_at=datetime.now(UTC),
        )
        path = tmp_path / "claims.jsonl"
        # Valid JSON but missing required fields for CompletionClaim
        path.write_text(
            '{"not_a_claim": true}\n' + good.model_dump_json() + "\n",
            encoding="utf-8",
        )

        svc = ClaimsService(storage_path=path)
        svc._load()

        assert len(svc._claims) == 1
        assert good.claim_id in svc._claims

    def test_load_sets_loaded_flag(self, tmp_path):
        path = tmp_path / "claims.jsonl"
        path.touch()
        svc = ClaimsService(storage_path=path)
        svc._load()
        assert svc._loaded is True


# ===========================================================================
# class TestRewrite
# ===========================================================================


class TestRewrite:
    """Tests for _rewrite — persisting in-memory cache to disk."""

    def test_rewrite_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "a" / "b" / "claims.jsonl"
        svc = ClaimsService(storage_path=nested)
        evidence = make_evidence()
        claim = CompletionClaim(
            task_id="IS-001",
            agent_id="forge:nova",
            status=ClaimStatus.SUBMITTED,
            evidence=evidence,
            submitted_at=datetime.now(UTC),
        )
        svc._claims[claim.claim_id] = claim
        svc._rewrite()

        assert nested.exists()

    def test_rewrite_content_is_valid_jsonl(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence()
        claim = CompletionClaim(
            task_id="IS-001",
            agent_id="forge:nova",
            status=ClaimStatus.SUBMITTED,
            evidence=evidence,
            submitted_at=datetime.now(UTC),
        )
        svc._claims[claim.claim_id] = claim
        svc._rewrite()

        lines = (tmp_path / "claims.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["claim_id"] == claim.claim_id

    def test_rewrite_overwrites_previous_content(self, tmp_path):
        svc = make_service(tmp_path)
        for i in range(3):
            ev = make_evidence(task_id=f"IS-00{i}")
            c = CompletionClaim(
                task_id=f"IS-00{i}",
                agent_id="forge:nova",
                status=ClaimStatus.SUBMITTED,
                evidence=ev,
                submitted_at=datetime.now(UTC),
            )
            svc._claims[c.claim_id] = c

        svc._rewrite()
        lines = (tmp_path / "claims.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3

        # Remove one and rewrite — file should shrink
        key = next(iter(svc._claims))
        del svc._claims[key]
        svc._rewrite()

        lines = (tmp_path / "claims.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2

    def test_rewrite_empty_cache_produces_empty_file(self, tmp_path):
        svc = make_service(tmp_path)
        svc._rewrite()
        assert (tmp_path / "claims.jsonl").read_text(encoding="utf-8") == ""


# ===========================================================================
# class TestPersistClaim
# ===========================================================================


class TestPersistClaim:
    """Tests for _persist_claim — updates cache and calls _rewrite."""

    def test_persist_claim_adds_to_cache(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence()
        claim = CompletionClaim(
            task_id="IS-001",
            agent_id="forge:nova",
            status=ClaimStatus.SUBMITTED,
            evidence=evidence,
            submitted_at=datetime.now(UTC),
        )
        svc._persist_claim(claim)
        assert claim.claim_id in svc._claims

    def test_persist_claim_writes_to_disk(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence()
        claim = CompletionClaim(
            task_id="IS-001",
            agent_id="forge:nova",
            status=ClaimStatus.SUBMITTED,
            evidence=evidence,
            submitted_at=datetime.now(UTC),
        )
        svc._persist_claim(claim)
        path = tmp_path / "claims.jsonl"
        assert path.exists()

    def test_persist_claim_overwrites_existing_entry(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence()
        claim = CompletionClaim(
            task_id="IS-001",
            agent_id="forge:nova",
            status=ClaimStatus.SUBMITTED,
            evidence=evidence,
            submitted_at=datetime.now(UTC),
        )
        svc._persist_claim(claim)

        updated = claim.model_copy(update={"status": ClaimStatus.VERIFYING})
        svc._persist_claim(updated)

        assert svc._claims[claim.claim_id].status == ClaimStatus.VERIFYING
        lines = (tmp_path / "claims.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1  # One claim, not two


# ===========================================================================
# class TestSubmit
# ===========================================================================


class TestSubmit:
    """Tests for ClaimsService.submit."""

    def test_submit_returns_completion_claim(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence()
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        assert isinstance(claim, CompletionClaim)

    def test_submit_status_is_submitted(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence()
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        assert claim.status == ClaimStatus.SUBMITTED

    def test_submit_task_id_preserved(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence(task_id="IS-999")
        claim = svc.submit(task_id="IS-999", agent_id="forge:nova", evidence=evidence)
        assert claim.task_id == "IS-999"

    def test_submit_agent_id_preserved(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence(agent_id="forge:kimi")
        claim = svc.submit(task_id="IS-001", agent_id="forge:kimi", evidence=evidence)
        assert claim.agent_id == "forge:kimi"

    def test_submit_evidence_attached(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence(commit_sha="deadbeef")
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        assert claim.evidence.commit_sha == "deadbeef"

    def test_submit_submitted_at_is_utc(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence()
        before = datetime.now(UTC)
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        after = datetime.now(UTC)
        assert before <= claim.submitted_at <= after
        assert claim.submitted_at.tzinfo is not None

    def test_submit_claim_persisted(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence()
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        assert claim.claim_id in svc._claims

    def test_submit_multiple_claims_distinct_ids(self, tmp_path):
        svc = make_service(tmp_path)
        ids = set()
        for i in range(5):
            ev = make_evidence(task_id=f"IS-{i:03d}")
            c = svc.submit(task_id=f"IS-{i:03d}", agent_id="forge:nova", evidence=ev)
            ids.add(c.claim_id)
        assert len(ids) == 5

    def test_submit_triggers_lazy_load(self, tmp_path):
        svc = make_service(tmp_path)
        assert svc._loaded is False
        evidence = make_evidence()
        svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        assert svc._loaded is True

    def test_submit_writes_to_disk(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence()
        svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        path = tmp_path / "claims.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1


# ===========================================================================
# class TestVerify
# ===========================================================================


class TestVerify:
    """Tests for ClaimsService.verify."""

    def test_verify_all_checks_pass_returns_approved(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence(all_pass=True)
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        result = svc.verify(claim.claim_id)
        assert result.status == ClaimStatus.APPROVED

    def test_verify_approved_has_no_rejection_reason(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence(all_pass=True)
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        result = svc.verify(claim.claim_id)
        assert result.rejection_reason is None

    def test_verify_approved_sets_verified_at(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence(all_pass=True)
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        before = datetime.now(UTC)
        result = svc.verify(claim.claim_id)
        after = datetime.now(UTC)
        assert result.verified_at is not None
        assert before <= result.verified_at <= after

    def test_verify_failed_checks_returns_rejected(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence(all_pass=False)
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        result = svc.verify(claim.claim_id)
        assert result.status == ClaimStatus.REJECTED

    def test_verify_rejected_populates_rejection_reason(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence(all_pass=False)
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        result = svc.verify(claim.claim_id)
        assert result.rejection_reason is not None
        assert "Failed checks:" in result.rejection_reason

    def test_verify_rejection_reason_contains_failed_check_names(self, tmp_path):
        svc = make_service(tmp_path)
        # Only lint fails
        evidence = EvidenceManifest(
            task_id="IS-001",
            agent_id="forge:nova",
            commit_sha="abc1234",
            changed_files=["src/main.py"],
            checks={
                "tests": CheckResult(passed=True),
                "lint": CheckResult(passed=False),
                "typecheck": CheckResult(passed=True),
            },
        )
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        result = svc.verify(claim.claim_id)
        assert result.status == ClaimStatus.REJECTED
        assert "lint" in result.rejection_reason

    def test_verify_rejection_reason_sorted(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = EvidenceManifest(
            task_id="IS-001",
            agent_id="forge:nova",
            commit_sha="abc1234",
            changed_files=["src/main.py"],
            checks={
                "tests": CheckResult(passed=False),
                "lint": CheckResult(passed=False),
                "typecheck": CheckResult(passed=False),
            },
        )
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        result = svc.verify(claim.claim_id)
        # Reason should list check names in sorted order
        checks_part = result.rejection_reason.replace("Failed checks: ", "")
        names = [n.strip() for n in checks_part.split(",")]
        assert names == sorted(names)

    def test_verify_unknown_claim_id_raises_key_error(self, tmp_path):
        svc = make_service(tmp_path)
        with pytest.raises(KeyError, match="nonexistent-id"):
            svc.verify("nonexistent-id")

    def test_verify_already_approved_raises_transition_error(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence(all_pass=True)
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        svc.verify(claim.claim_id)  # Transitions to approved

        # Trying again should fail because approved is terminal
        with pytest.raises(ClaimTransitionError):
            svc.verify(claim.claim_id)

    def test_verify_already_rejected_raises_transition_error(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence(all_pass=False)
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        svc.verify(claim.claim_id)  # Transitions to rejected

        with pytest.raises(ClaimTransitionError):
            svc.verify(claim.claim_id)

    def test_verify_persists_final_state(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = make_evidence(all_pass=True)
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        svc.verify(claim.claim_id)

        # Reload from disk
        svc2 = ClaimsService(storage_path=tmp_path / "claims.jsonl")
        svc2._load()
        loaded = svc2._claims[claim.claim_id]
        assert loaded.status == ClaimStatus.APPROVED

    def test_verify_claim_with_no_checks_approves(self, tmp_path):
        """Empty checks dict — no failed checks, should approve."""
        svc = make_service(tmp_path)
        evidence = EvidenceManifest(
            task_id="IS-001",
            agent_id="forge:nova",
            commit_sha="abc1234",
            changed_files=[],
            checks={},
        )
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        result = svc.verify(claim.claim_id)
        # empty checks means no failed checks -> approved
        assert result.status == ClaimStatus.APPROVED

    def test_verify_partial_failure_rejects(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = EvidenceManifest(
            task_id="IS-001",
            agent_id="forge:nova",
            commit_sha="abc",
            changed_files=[],
            checks={
                "tests": CheckResult(passed=True),
                "lint": CheckResult(passed=False),
            },
        )
        claim = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        result = svc.verify(claim.claim_id)
        assert result.status == ClaimStatus.REJECTED
        assert "lint" in result.rejection_reason


# ===========================================================================
# class TestListClaims
# ===========================================================================


class TestListClaims:
    """Tests for ClaimsService.list_claims."""

    def test_list_empty_store(self, tmp_path):
        svc = make_service(tmp_path)
        result = svc.list_claims()
        assert result == []

    def test_list_returns_all_claims_no_filter(self, tmp_path):
        svc = make_service(tmp_path)
        for i in range(3):
            ev = make_evidence(task_id=f"IS-{i:03d}")
            svc.submit(task_id=f"IS-{i:03d}", agent_id="forge:nova", evidence=ev)
        result = svc.list_claims()
        assert len(result) == 3

    def test_list_filter_by_submitted_status(self, tmp_path):
        svc = make_service(tmp_path)
        ev = make_evidence(all_pass=True)
        c1 = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)
        ev2 = make_evidence(task_id="IS-002", all_pass=True)
        svc.submit(task_id="IS-002", agent_id="forge:nova", evidence=ev2)
        svc.verify(c1.claim_id)  # IS-001 becomes approved

        submitted = svc.list_claims(status=ClaimStatus.SUBMITTED)
        assert len(submitted) == 1
        assert submitted[0].task_id == "IS-002"

    def test_list_filter_by_approved_status(self, tmp_path):
        svc = make_service(tmp_path)
        ev = make_evidence(all_pass=True)
        c1 = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)
        ev2 = make_evidence(task_id="IS-002", all_pass=False)
        svc.submit(task_id="IS-002", agent_id="forge:nova", evidence=ev2)
        svc.verify(c1.claim_id)

        approved = svc.list_claims(status=ClaimStatus.APPROVED)
        assert len(approved) == 1
        assert approved[0].task_id == "IS-001"

    def test_list_filter_by_rejected_status(self, tmp_path):
        svc = make_service(tmp_path)
        ev = make_evidence(all_pass=False)
        c1 = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)
        svc.verify(c1.claim_id)

        rejected = svc.list_claims(status=ClaimStatus.REJECTED)
        assert len(rejected) == 1
        assert rejected[0].task_id == "IS-001"

    def test_list_filter_returns_empty_for_no_match(self, tmp_path):
        svc = make_service(tmp_path)
        ev = make_evidence()
        svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)

        verifying = svc.list_claims(status=ClaimStatus.VERIFYING)
        assert verifying == []

    def test_list_sorted_by_submitted_at_ascending(self, tmp_path):
        svc = make_service(tmp_path)
        # Manually inject claims with known submitted_at to control ordering
        claims_in_order = []
        for i in range(3):
            ev = make_evidence(task_id=f"IS-{i:03d}")
            c = CompletionClaim(
                task_id=f"IS-{i:03d}",
                agent_id="forge:nova",
                status=ClaimStatus.SUBMITTED,
                evidence=ev,
                submitted_at=datetime(2026, 1, i + 1, tzinfo=UTC),
            )
            svc._claims[c.claim_id] = c
            claims_in_order.append(c)

        svc._loaded = True
        result = svc.list_claims()
        for idx in range(len(result) - 1):
            assert result[idx].submitted_at <= result[idx + 1].submitted_at

    def test_list_all_claims_is_copy_not_reference(self, tmp_path):
        svc = make_service(tmp_path)
        ev = make_evidence()
        svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)

        result1 = svc.list_claims()
        result2 = svc.list_claims()
        # Should return equal lists but not the same object
        assert result1 is not result2


# ===========================================================================
# class TestGetClaim
# ===========================================================================


class TestGetClaim:
    """Tests for ClaimsService.get_claim."""

    def test_get_existing_claim(self, tmp_path):
        svc = make_service(tmp_path)
        ev = make_evidence()
        c = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)
        found = svc.get_claim(c.claim_id)
        assert found is not None
        assert found.claim_id == c.claim_id

    def test_get_nonexistent_claim_returns_none(self, tmp_path):
        svc = make_service(tmp_path)
        result = svc.get_claim("does-not-exist")
        assert result is None

    def test_get_claim_triggers_lazy_load(self, tmp_path):
        svc = make_service(tmp_path)
        assert svc._loaded is False
        svc.get_claim("anything")
        assert svc._loaded is True

    def test_get_claim_after_verify(self, tmp_path):
        svc = make_service(tmp_path)
        ev = make_evidence(all_pass=True)
        c = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)
        svc.verify(c.claim_id)
        found = svc.get_claim(c.claim_id)
        assert found.status == ClaimStatus.APPROVED


# ===========================================================================
# class TestGetStats
# ===========================================================================


class TestGetStats:
    """Tests for ClaimsService.get_stats."""

    def test_stats_empty_store_all_zeros(self, tmp_path):
        svc = make_service(tmp_path)
        stats = svc.get_stats()
        for status in ClaimStatus:
            assert stats[status.value] == 0

    def test_stats_contains_all_status_keys(self, tmp_path):
        svc = make_service(tmp_path)
        stats = svc.get_stats()
        expected_keys = {s.value for s in ClaimStatus}
        assert expected_keys == set(stats.keys())

    def test_stats_submitted_count(self, tmp_path):
        svc = make_service(tmp_path)
        for i in range(3):
            ev = make_evidence(task_id=f"IS-{i:03d}")
            svc.submit(task_id=f"IS-{i:03d}", agent_id="forge:nova", evidence=ev)

        stats = svc.get_stats()
        assert stats["submitted"] == 3

    def test_stats_approved_count(self, tmp_path):
        svc = make_service(tmp_path)
        for i in range(2):
            ev = make_evidence(task_id=f"IS-{i:03d}", all_pass=True)
            c = svc.submit(task_id=f"IS-{i:03d}", agent_id="forge:nova", evidence=ev)
            svc.verify(c.claim_id)

        stats = svc.get_stats()
        assert stats["approved"] == 2
        assert stats["submitted"] == 0

    def test_stats_rejected_count(self, tmp_path):
        svc = make_service(tmp_path)
        ev = make_evidence(all_pass=False)
        c = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)
        svc.verify(c.claim_id)

        stats = svc.get_stats()
        assert stats["rejected"] == 1
        assert stats["submitted"] == 0

    def test_stats_mixed_statuses(self, tmp_path):
        svc = make_service(tmp_path)

        # 1 submitted (not verified)
        ev1 = make_evidence(task_id="IS-001")
        svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev1)

        # 1 approved
        ev2 = make_evidence(task_id="IS-002", all_pass=True)
        c2 = svc.submit(task_id="IS-002", agent_id="forge:nova", evidence=ev2)
        svc.verify(c2.claim_id)

        # 1 rejected
        ev3 = make_evidence(task_id="IS-003", all_pass=False)
        c3 = svc.submit(task_id="IS-003", agent_id="forge:nova", evidence=ev3)
        svc.verify(c3.claim_id)

        stats = svc.get_stats()
        assert stats["submitted"] == 1
        assert stats["approved"] == 1
        assert stats["rejected"] == 1
        assert stats["verifying"] == 0

    def test_stats_triggers_lazy_load(self, tmp_path):
        svc = make_service(tmp_path)
        assert svc._loaded is False
        svc.get_stats()
        assert svc._loaded is True


# ===========================================================================
# class TestGetClaimsService (singleton factory)
# ===========================================================================


class TestGetClaimsService:
    """Tests for get_claims_service singleton factory."""

    def setup_method(self):
        reset_claims_service()

    def teardown_method(self):
        reset_claims_service()

    def test_returns_claims_service_instance(self, tmp_path):
        svc = get_claims_service(storage_path=tmp_path / "claims.jsonl")
        assert isinstance(svc, ClaimsService)

    def test_singleton_same_object_on_second_call(self, tmp_path):
        path = tmp_path / "claims.jsonl"
        svc1 = get_claims_service(storage_path=path)
        svc2 = get_claims_service(storage_path=path)
        assert svc1 is svc2

    def test_second_call_ignores_different_path(self, tmp_path):
        path1 = tmp_path / "first.jsonl"
        path2 = tmp_path / "second.jsonl"
        svc1 = get_claims_service(storage_path=path1)
        svc2 = get_claims_service(storage_path=path2)
        assert svc1 is svc2
        assert svc1._path == path1  # path from first call is kept

    def test_default_path_used_when_no_argument(self):
        svc = get_claims_service()
        assert svc._path == _DEFAULT_CLAIMS_PATH

    def test_custom_path_honoured_on_first_call(self, tmp_path):
        custom = tmp_path / "custom.jsonl"
        svc = get_claims_service(storage_path=custom)
        assert svc._path == custom


# ===========================================================================
# class TestResetClaimsService (singleton reset)
# ===========================================================================


class TestResetClaimsService:
    """Tests for reset_claims_service."""

    def setup_method(self):
        reset_claims_service()

    def teardown_method(self):
        reset_claims_service()

    def test_reset_allows_new_singleton(self, tmp_path):
        path1 = tmp_path / "first.jsonl"
        svc1 = get_claims_service(storage_path=path1)

        reset_claims_service()

        path2 = tmp_path / "second.jsonl"
        svc2 = get_claims_service(storage_path=path2)

        assert svc1 is not svc2
        assert svc2._path == path2

    def test_reset_idempotent_when_no_singleton(self):
        # Should not raise even when already None
        reset_claims_service()
        reset_claims_service()  # No error

    def test_reset_then_get_returns_fresh_instance(self, tmp_path):
        path = tmp_path / "claims.jsonl"
        svc1 = get_claims_service(storage_path=path)
        ev = make_evidence()
        svc1.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)

        reset_claims_service()

        # Fresh singleton reads from the same file
        svc2 = get_claims_service(storage_path=path)
        assert svc2 is not svc1
        # Data should persist on disk, so it loads the previous claim
        result = svc2.list_claims()
        assert len(result) == 1


# ===========================================================================
# class TestThreadSafety
# ===========================================================================


class TestThreadSafety:
    """Tests for concurrent access to ClaimsService."""

    def test_concurrent_submits_all_persisted(self, tmp_path):
        svc = make_service(tmp_path)
        errors: list[Exception] = []
        claim_ids: list[str] = []
        lock = threading.Lock()

        def submit_claim(i: int) -> None:
            try:
                ev = make_evidence(task_id=f"IS-{i:04d}")
                c = svc.submit(
                    task_id=f"IS-{i:04d}", agent_id="forge:nova", evidence=ev
                )
                with lock:
                    claim_ids.append(c.claim_id)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=submit_claim, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert len(claim_ids) == 20
        assert len(set(claim_ids)) == 20  # All IDs unique
        assert len(svc.list_claims()) == 20

    def test_concurrent_reads_do_not_raise(self, tmp_path):
        svc = make_service(tmp_path)
        # Pre-populate
        for i in range(5):
            ev = make_evidence(task_id=f"IS-{i:03d}")
            svc.submit(task_id=f"IS-{i:03d}", agent_id="forge:nova", evidence=ev)

        errors: list[Exception] = []

        def read_claims() -> None:
            try:
                svc.list_claims()
                svc.get_stats()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=read_claims) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ===========================================================================
# class TestRoundTripPersistence
# ===========================================================================


class TestRoundTripPersistence:
    """Tests that data survives a complete write-then-read round trip."""

    def test_round_trip_approved_claim(self, tmp_path):
        path = tmp_path / "claims.jsonl"
        svc = ClaimsService(storage_path=path)
        ev = make_evidence(all_pass=True)
        c = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)
        svc.verify(c.claim_id)

        svc2 = ClaimsService(storage_path=path)
        svc2._load()
        loaded = svc2._claims[c.claim_id]
        assert loaded.status == ClaimStatus.APPROVED
        assert loaded.verified_at is not None
        assert loaded.rejection_reason is None

    def test_round_trip_rejected_claim(self, tmp_path):
        path = tmp_path / "claims.jsonl"
        svc = ClaimsService(storage_path=path)
        ev = make_evidence(all_pass=False)
        c = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)
        svc.verify(c.claim_id)

        svc2 = ClaimsService(storage_path=path)
        svc2._load()
        loaded = svc2._claims[c.claim_id]
        assert loaded.status == ClaimStatus.REJECTED
        assert loaded.rejection_reason is not None

    def test_round_trip_preserves_all_evidence_fields(self, tmp_path):
        path = tmp_path / "claims.jsonl"
        svc = ClaimsService(storage_path=path)
        ev = EvidenceManifest(
            task_id="IS-007",
            agent_id="forge:gemini",
            commit_sha="cafebabe",
            changed_files=["a.py", "b.py"],
            checks={
                "tests": CheckResult(passed=True),
                "lint": CheckResult(passed=True),
            },
        )
        c = svc.submit(task_id="IS-007", agent_id="forge:gemini", evidence=ev)

        svc2 = ClaimsService(storage_path=path)
        svc2._load()
        loaded = svc2._claims[c.claim_id]
        assert loaded.evidence.commit_sha == "cafebabe"
        assert loaded.evidence.changed_files == ["a.py", "b.py"]
        assert loaded.evidence.checks["tests"].passed is True


# ===========================================================================
# class TestEdgeCases
# ===========================================================================


class TestEdgeCases:
    """Additional edge-case and boundary tests."""

    def test_verify_error_message_includes_claim_id(self, tmp_path):
        svc = make_service(tmp_path)
        fake_id = "fake-claim-id-xyz"
        with pytest.raises(KeyError) as exc_info:
            svc.verify(fake_id)
        assert fake_id in str(exc_info.value)

    def test_list_claims_none_status_returns_all(self, tmp_path):
        svc = make_service(tmp_path)
        ev = make_evidence(all_pass=True)
        c = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)
        svc.verify(c.claim_id)
        ev2 = make_evidence(task_id="IS-002")
        svc.submit(task_id="IS-002", agent_id="forge:nova", evidence=ev2)

        all_claims = svc.list_claims(status=None)
        assert len(all_claims) == 2

    def test_submit_does_not_set_verified_at(self, tmp_path):
        svc = make_service(tmp_path)
        ev = make_evidence()
        c = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)
        assert c.verified_at is None

    def test_submit_does_not_set_rejection_reason(self, tmp_path):
        svc = make_service(tmp_path)
        ev = make_evidence()
        c = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=ev)
        assert c.rejection_reason is None

    def test_multiple_failed_checks_all_in_reason(self, tmp_path):
        svc = make_service(tmp_path)
        evidence = EvidenceManifest(
            task_id="IS-001",
            agent_id="forge:nova",
            commit_sha="abc",
            changed_files=[],
            checks={
                "tests": CheckResult(passed=False),
                "lint": CheckResult(passed=False),
                "typecheck": CheckResult(passed=False),
            },
        )
        c = svc.submit(task_id="IS-001", agent_id="forge:nova", evidence=evidence)
        result = svc.verify(c.claim_id)
        assert "tests" in result.rejection_reason
        assert "lint" in result.rejection_reason
        assert "typecheck" in result.rejection_reason

    def test_get_stats_returns_int_values(self, tmp_path):
        svc = make_service(tmp_path)
        stats = svc.get_stats()
        for v in stats.values():
            assert isinstance(v, int)

    def test_claims_service_can_handle_large_number_of_claims(self, tmp_path):
        svc = make_service(tmp_path)
        n = 100
        for i in range(n):
            ev = make_evidence(task_id=f"IS-{i:04d}")
            svc.submit(task_id=f"IS-{i:04d}", agent_id="forge:nova", evidence=ev)

        result = svc.list_claims()
        assert len(result) == n

    def test_load_handles_extra_whitespace_in_lines(self, tmp_path):
        ev = make_evidence()
        claim = CompletionClaim(
            task_id="IS-001",
            agent_id="forge:nova",
            status=ClaimStatus.SUBMITTED,
            evidence=ev,
            submitted_at=datetime.now(UTC),
        )
        path = tmp_path / "claims.jsonl"
        # Add surrounding whitespace to the line
        path.write_text("   " + claim.model_dump_json() + "   \n", encoding="utf-8")

        svc = ClaimsService(storage_path=path)
        svc._load()
        assert claim.claim_id in svc._claims
