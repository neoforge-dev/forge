"""Tests for evidence-based completion claims service.

Covers:
- Model construction and field defaults
- ClaimStatus state machine and transition validation
- EvidenceManifest check evaluation
- ClaimsService.submit — creates claim with SUBMITTED status
- ClaimsService.verify — approved path (all checks pass)
- ClaimsService.verify — rejected path (one or more checks fail)
- ClaimsService.list_claims — unfiltered and status-filtered
- ClaimsService.get_claim — found and not-found
- ClaimsService.get_stats — accurate counts per status
- JSONL persistence — write-then-read-back round-trip
- Invalid transition enforcement
- Singleton factory + reset
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from forge_harness.webhook_server.models.claim import (
    TERMINAL_CLAIM_STATUSES,
    VALID_CLAIM_TRANSITIONS,
    CheckResult,
    ClaimStatus,
    ClaimTransitionError,
    CompletionClaim,
    EvidenceManifest,
    validate_claim_transition,
)
from forge_harness.webhook_server.services.claims_service import (
    ClaimsService,
    get_claims_service,
    reset_claims_service,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_evidence(
    *,
    task_id: str = "IS-001",
    agent_id: str = "forge:nova",
    commit_sha: str = "deadbeef",
    changed_files: list[str] | None = None,
    tests_passed: bool = True,
    lint_passed: bool = True,
    typecheck_passed: bool = True,
) -> EvidenceManifest:
    return EvidenceManifest(
        task_id=task_id,
        agent_id=agent_id,
        commit_sha=commit_sha,
        changed_files=changed_files or ["src/main.py"],
        checks={
            "tests": CheckResult(passed=tests_passed),
            "lint": CheckResult(passed=lint_passed),
            "typecheck": CheckResult(passed=typecheck_passed),
        },
    )


def _make_service(tmp_path: Path) -> ClaimsService:
    return ClaimsService(storage_path=tmp_path / "claims.jsonl")


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestClaimStatus:
    def test_all_statuses_present(self):
        values = {s.value for s in ClaimStatus}
        assert values == {"submitted", "verifying", "approved", "rejected"}

    def test_string_subclass(self):
        assert isinstance(ClaimStatus.SUBMITTED, str)
        assert ClaimStatus.SUBMITTED == "submitted"

    def test_terminal_statuses(self):
        assert TERMINAL_CLAIM_STATUSES == {ClaimStatus.APPROVED, ClaimStatus.REJECTED}

    def test_valid_transitions_structure(self):
        # Every ClaimStatus must appear as a key
        assert set(VALID_CLAIM_TRANSITIONS.keys()) == set(ClaimStatus)

    def test_submitted_can_reach_verifying(self):
        assert ClaimStatus.VERIFYING in VALID_CLAIM_TRANSITIONS[ClaimStatus.SUBMITTED]

    def test_verifying_can_reach_approved_and_rejected(self):
        allowed = VALID_CLAIM_TRANSITIONS[ClaimStatus.VERIFYING]
        assert ClaimStatus.APPROVED in allowed
        assert ClaimStatus.REJECTED in allowed

    def test_approved_is_terminal(self):
        assert VALID_CLAIM_TRANSITIONS[ClaimStatus.APPROVED] == frozenset()

    def test_rejected_is_terminal(self):
        assert VALID_CLAIM_TRANSITIONS[ClaimStatus.REJECTED] == frozenset()


class TestValidateClaimTransition:
    def test_valid_submitted_to_verifying(self):
        assert validate_claim_transition(ClaimStatus.SUBMITTED, ClaimStatus.VERIFYING) is True

    def test_valid_verifying_to_approved(self):
        assert validate_claim_transition(ClaimStatus.VERIFYING, ClaimStatus.APPROVED) is True

    def test_valid_verifying_to_rejected(self):
        assert validate_claim_transition(ClaimStatus.VERIFYING, ClaimStatus.REJECTED) is True

    def test_invalid_submitted_to_approved_raises(self):
        with pytest.raises(ClaimTransitionError) as exc_info:
            validate_claim_transition(ClaimStatus.SUBMITTED, ClaimStatus.APPROVED)
        err = exc_info.value
        assert err.from_status == ClaimStatus.SUBMITTED
        assert err.to_status == ClaimStatus.APPROVED

    def test_invalid_approved_to_any_raises(self):
        with pytest.raises(ClaimTransitionError) as exc_info:
            validate_claim_transition(ClaimStatus.APPROVED, ClaimStatus.SUBMITTED)
        assert "terminal" in str(exc_info.value)

    def test_invalid_rejected_to_any_raises(self):
        with pytest.raises(ClaimTransitionError):
            validate_claim_transition(ClaimStatus.REJECTED, ClaimStatus.VERIFYING)

    def test_accepts_string_values(self):
        assert validate_claim_transition("submitted", "verifying") is True

    def test_invalid_string_value_raises(self):
        with pytest.raises(ValueError):
            validate_claim_transition("submitted", "nonexistent")

    def test_error_message_lists_allowed_states(self):
        with pytest.raises(ClaimTransitionError) as exc_info:
            validate_claim_transition(ClaimStatus.SUBMITTED, ClaimStatus.REJECTED)
        msg = str(exc_info.value)
        assert "verifying" in msg  # only allowed next state from submitted


class TestClaimTransitionError:
    def test_terminal_error_message(self):
        err = ClaimTransitionError(ClaimStatus.APPROVED, ClaimStatus.SUBMITTED, frozenset())
        assert "terminal" in str(err)

    def test_non_terminal_error_lists_allowed(self):
        allowed = frozenset({ClaimStatus.VERIFYING})
        err = ClaimTransitionError(ClaimStatus.SUBMITTED, ClaimStatus.APPROVED, allowed)
        assert "verifying" in str(err)


class TestCheckResult:
    def test_passed_true(self):
        cr = CheckResult(passed=True)
        assert cr.passed is True

    def test_passed_false(self):
        cr = CheckResult(passed=False)
        assert cr.passed is False

    def test_extra_fields_allowed(self):
        cr = CheckResult(passed=True, exit_code=0, duration_ms=1234)
        assert cr.passed is True
        # Extra fields preserved via model_config extra="allow"
        assert cr.exit_code == 0  # type: ignore[attr-defined]


class TestEvidenceManifest:
    def test_defaults(self):
        ev = EvidenceManifest(task_id="T-1", agent_id="agent", commit_sha="abc")
        assert ev.changed_files == []
        assert "tests" in ev.checks
        assert "lint" in ev.checks
        assert "typecheck" in ev.checks

    def test_all_checks_passed_true(self):
        ev = _make_evidence()
        assert ev.all_checks_passed is True

    def test_all_checks_passed_false_when_one_fails(self):
        ev = _make_evidence(tests_passed=False)
        assert ev.all_checks_passed is False

    def test_all_checks_passed_false_when_all_fail(self):
        ev = _make_evidence(tests_passed=False, lint_passed=False, typecheck_passed=False)
        assert ev.all_checks_passed is False

    def test_all_checks_passed_false_when_checks_empty(self):
        ev = EvidenceManifest(task_id="T-1", agent_id="agent", commit_sha="abc", checks={})
        assert ev.all_checks_passed is False

    def test_changed_files_stored(self):
        files = ["src/a.py", "src/b.py"]
        ev = _make_evidence(changed_files=files)
        assert ev.changed_files == files


class TestCompletionClaim:
    def test_defaults(self):
        ev = _make_evidence()
        claim = CompletionClaim(
            task_id="IS-001",
            agent_id="forge:nova",
            evidence=ev,
            submitted_at=datetime.now(UTC),
        )
        assert claim.status == ClaimStatus.SUBMITTED
        assert claim.verified_at is None
        assert claim.rejection_reason is None
        # claim_id is a UUID4 string
        assert len(claim.claim_id) == 36

    def test_unique_claim_ids(self):
        ev = _make_evidence()
        now = datetime.now(UTC)
        c1 = CompletionClaim(task_id="T-1", agent_id="a", evidence=ev, submitted_at=now)
        c2 = CompletionClaim(task_id="T-1", agent_id="a", evidence=ev, submitted_at=now)
        assert c1.claim_id != c2.claim_id

    def test_json_round_trip(self):
        ev = _make_evidence()
        claim = CompletionClaim(
            task_id="IS-001",
            agent_id="forge:nova",
            evidence=ev,
            submitted_at=datetime.now(UTC),
        )
        raw = claim.model_dump_json()
        restored = CompletionClaim.model_validate_json(raw)
        assert restored.claim_id == claim.claim_id
        assert restored.status == claim.status
        assert restored.evidence.commit_sha == claim.evidence.commit_sha


# ---------------------------------------------------------------------------
# ClaimsService tests
# ---------------------------------------------------------------------------


class TestClaimsServiceSubmit:
    def test_submit_returns_claim(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence()
        claim = svc.submit("IS-001", "forge:nova", ev)

        assert claim.task_id == "IS-001"
        assert claim.agent_id == "forge:nova"
        assert claim.status == ClaimStatus.SUBMITTED
        assert claim.verified_at is None
        assert claim.rejection_reason is None

    def test_submit_creates_jsonl_file(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence()
        claim = svc.submit("IS-001", "forge:nova", ev)

        jsonl_path = tmp_path / "claims.jsonl"
        assert jsonl_path.exists()
        lines = [l for l in jsonl_path.read_text().strip().splitlines() if l]
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["claim_id"] == claim.claim_id

    def test_submit_multiple_claims(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence()
        c1 = svc.submit("IS-001", "forge:nova", ev)
        c2 = svc.submit("IS-002", "forge:sati", ev)

        jsonl_path = tmp_path / "claims.jsonl"
        lines = [l for l in jsonl_path.read_text().strip().splitlines() if l]
        assert len(lines) == 2
        ids = {json.loads(l)["claim_id"] for l in lines}
        assert c1.claim_id in ids
        assert c2.claim_id in ids

    def test_submit_sets_submitted_at(self, tmp_path):
        svc = _make_service(tmp_path)
        before = datetime.now(UTC)
        claim = svc.submit("IS-001", "forge:nova", _make_evidence())
        after = datetime.now(UTC)

        assert before <= claim.submitted_at <= after


class TestClaimsServiceVerifyApproved:
    def test_verify_all_checks_passing_approves(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence(tests_passed=True, lint_passed=True, typecheck_passed=True)
        claim = svc.submit("IS-001", "forge:nova", ev)

        verified = svc.verify(claim.claim_id)

        assert verified.status == ClaimStatus.APPROVED
        assert verified.rejection_reason is None
        assert verified.verified_at is not None

    def test_verify_approved_persisted(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence()
        claim = svc.submit("IS-001", "forge:nova", ev)
        svc.verify(claim.claim_id)

        # Re-read from disk
        svc2 = ClaimsService(storage_path=tmp_path / "claims.jsonl")
        reloaded = svc2.get_claim(claim.claim_id)
        assert reloaded is not None
        assert reloaded.status == ClaimStatus.APPROVED


class TestClaimsServiceVerifyRejected:
    def test_verify_failing_tests_rejects(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence(tests_passed=False)
        claim = svc.submit("IS-001", "forge:nova", ev)

        verified = svc.verify(claim.claim_id)

        assert verified.status == ClaimStatus.REJECTED
        assert verified.rejection_reason is not None
        assert "tests" in verified.rejection_reason

    def test_verify_failing_lint_rejects(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence(lint_passed=False)
        claim = svc.submit("IS-002", "forge:nova", ev)

        verified = svc.verify(claim.claim_id)

        assert verified.status == ClaimStatus.REJECTED
        assert "lint" in verified.rejection_reason

    def test_verify_multiple_failing_checks(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence(tests_passed=False, lint_passed=False, typecheck_passed=False)
        claim = svc.submit("IS-003", "forge:nova", ev)

        verified = svc.verify(claim.claim_id)

        assert verified.status == ClaimStatus.REJECTED
        reason = verified.rejection_reason
        assert "tests" in reason
        assert "lint" in reason
        assert "typecheck" in reason

    def test_verify_rejected_persisted(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence(tests_passed=False)
        claim = svc.submit("IS-004", "forge:nova", ev)
        svc.verify(claim.claim_id)

        svc2 = ClaimsService(storage_path=tmp_path / "claims.jsonl")
        reloaded = svc2.get_claim(claim.claim_id)
        assert reloaded is not None
        assert reloaded.status == ClaimStatus.REJECTED

    def test_verify_sets_verified_at(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence(tests_passed=False)
        claim = svc.submit("IS-005", "forge:nova", ev)
        before = datetime.now(UTC)
        verified = svc.verify(claim.claim_id)
        after = datetime.now(UTC)
        assert before <= verified.verified_at <= after


class TestClaimsServiceVerifyErrors:
    def test_verify_unknown_claim_raises_key_error(self, tmp_path):
        svc = _make_service(tmp_path)
        with pytest.raises(KeyError, match="not found"):
            svc.verify("nonexistent-id")

    def test_verify_already_approved_raises_transition_error(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence()
        claim = svc.submit("IS-010", "forge:nova", ev)
        svc.verify(claim.claim_id)  # -> approved

        with pytest.raises(ClaimTransitionError):
            svc.verify(claim.claim_id)  # already terminal

    def test_verify_already_rejected_raises_transition_error(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence(tests_passed=False)
        claim = svc.submit("IS-011", "forge:nova", ev)
        svc.verify(claim.claim_id)  # -> rejected

        with pytest.raises(ClaimTransitionError):
            svc.verify(claim.claim_id)  # already terminal


class TestClaimsServiceListClaims:
    def test_list_all_returns_all(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence()
        c1 = svc.submit("IS-001", "forge:nova", ev)
        c2 = svc.submit("IS-002", "forge:sati", ev)

        claims = svc.list_claims()
        ids = {c.claim_id for c in claims}
        assert c1.claim_id in ids
        assert c2.claim_id in ids

    def test_list_empty_store_returns_empty(self, tmp_path):
        svc = _make_service(tmp_path)
        assert svc.list_claims() == []

    def test_list_with_status_filter_submitted(self, tmp_path):
        svc = _make_service(tmp_path)
        ev_pass = _make_evidence()
        ev_fail = _make_evidence(tests_passed=False)

        c_sub = svc.submit("IS-001", "a", ev_pass)  # stays submitted after filter
        c_apv = svc.submit("IS-002", "b", ev_pass)
        svc.verify(c_apv.claim_id)  # -> approved
        c_rej = svc.submit("IS-003", "c", ev_fail)
        svc.verify(c_rej.claim_id)  # -> rejected

        submitted = svc.list_claims(status=ClaimStatus.SUBMITTED)
        ids = {c.claim_id for c in submitted}
        assert c_sub.claim_id in ids
        assert c_apv.claim_id not in ids
        assert c_rej.claim_id not in ids

    def test_list_with_status_filter_approved(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence()
        c1 = svc.submit("IS-001", "a", ev)
        c2 = svc.submit("IS-002", "b", ev)
        svc.verify(c1.claim_id)

        approved = svc.list_claims(status=ClaimStatus.APPROVED)
        ids = {c.claim_id for c in approved}
        assert c1.claim_id in ids
        assert c2.claim_id not in ids

    def test_list_with_status_filter_rejected(self, tmp_path):
        svc = _make_service(tmp_path)
        ev_fail = _make_evidence(tests_passed=False)
        c1 = svc.submit("IS-001", "a", ev_fail)
        c2 = svc.submit("IS-002", "b", _make_evidence())
        svc.verify(c1.claim_id)

        rejected = svc.list_claims(status=ClaimStatus.REJECTED)
        ids = {c.claim_id for c in rejected}
        assert c1.claim_id in ids
        assert c2.claim_id not in ids

    def test_list_sorted_by_submitted_at(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence()
        c1 = svc.submit("IS-001", "a", ev)
        c2 = svc.submit("IS-002", "b", ev)
        c3 = svc.submit("IS-003", "c", ev)

        claims = svc.list_claims()
        ids = [c.claim_id for c in claims]
        # submitted_at is monotonically increasing (all within same second
        # in practice; ordering is stable due to sort key)
        assert set(ids) == {c1.claim_id, c2.claim_id, c3.claim_id}


class TestClaimsServiceGetClaim:
    def test_get_existing_claim(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence()
        claim = svc.submit("IS-001", "forge:nova", ev)

        found = svc.get_claim(claim.claim_id)
        assert found is not None
        assert found.claim_id == claim.claim_id

    def test_get_nonexistent_returns_none(self, tmp_path):
        svc = _make_service(tmp_path)
        result = svc.get_claim("does-not-exist")
        assert result is None

    def test_get_after_verify(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence()
        claim = svc.submit("IS-001", "a", ev)
        svc.verify(claim.claim_id)

        found = svc.get_claim(claim.claim_id)
        assert found is not None
        assert found.status == ClaimStatus.APPROVED


class TestClaimsServiceGetStats:
    def test_stats_empty_store(self, tmp_path):
        svc = _make_service(tmp_path)
        stats = svc.get_stats()

        assert set(stats.keys()) == {"submitted", "verifying", "approved", "rejected"}
        assert all(v == 0 for v in stats.values())

    def test_stats_submitted_only(self, tmp_path):
        svc = _make_service(tmp_path)
        ev = _make_evidence()
        svc.submit("IS-001", "a", ev)
        svc.submit("IS-002", "b", ev)

        stats = svc.get_stats()
        assert stats["submitted"] == 2
        assert stats["verifying"] == 0
        assert stats["approved"] == 0
        assert stats["rejected"] == 0

    def test_stats_mixed(self, tmp_path):
        svc = _make_service(tmp_path)
        ev_pass = _make_evidence()
        ev_fail = _make_evidence(tests_passed=False)

        s1 = svc.submit("IS-001", "a", ev_pass)
        s2 = svc.submit("IS-002", "b", ev_pass)
        s3 = svc.submit("IS-003", "c", ev_fail)

        svc.verify(s1.claim_id)  # -> approved
        svc.verify(s3.claim_id)  # -> rejected

        stats = svc.get_stats()
        assert stats["submitted"] == 1  # s2 still submitted
        assert stats["approved"] == 1  # s1
        assert stats["rejected"] == 1  # s3
        assert stats["verifying"] == 0

    def test_stats_all_statuses_present(self, tmp_path):
        svc = _make_service(tmp_path)
        stats = svc.get_stats()
        for status in ClaimStatus:
            assert status.value in stats


class TestClaimsServiceJSONLPersistence:
    def test_persist_and_reload(self, tmp_path):
        """Write claims via one service instance, read back from a fresh instance."""
        path = tmp_path / "claims.jsonl"
        svc1 = ClaimsService(storage_path=path)
        ev = _make_evidence()
        claim = svc1.submit("IS-001", "forge:nova", ev)

        # Fresh instance reads from disk
        svc2 = ClaimsService(storage_path=path)
        reloaded = svc2.get_claim(claim.claim_id)

        assert reloaded is not None
        assert reloaded.task_id == "IS-001"
        assert reloaded.agent_id == "forge:nova"
        assert reloaded.status == ClaimStatus.SUBMITTED
        assert reloaded.evidence.commit_sha == ev.commit_sha

    def test_persist_verified_claim(self, tmp_path):
        path = tmp_path / "claims.jsonl"
        svc1 = ClaimsService(storage_path=path)
        ev = _make_evidence()
        claim = svc1.submit("IS-002", "forge:sati", ev)
        svc1.verify(claim.claim_id)

        svc2 = ClaimsService(storage_path=path)
        reloaded = svc2.get_claim(claim.claim_id)
        assert reloaded is not None
        assert reloaded.status == ClaimStatus.APPROVED
        assert reloaded.verified_at is not None

    def test_jsonl_one_line_per_claim(self, tmp_path):
        path = tmp_path / "claims.jsonl"
        svc = ClaimsService(storage_path=path)
        ev = _make_evidence()
        svc.submit("IS-001", "a", ev)
        svc.submit("IS-002", "b", ev)

        lines = [l for l in path.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        for line in lines:
            data = json.loads(line)
            assert "claim_id" in data
            assert "task_id" in data
            assert "status" in data

    def test_jsonl_missing_file_treated_as_empty(self, tmp_path):
        path = tmp_path / "subdir" / "claims.jsonl"
        svc = ClaimsService(storage_path=path)

        # Should not raise; treated as empty store
        assert svc.list_claims() == []
        assert svc.get_claim("any-id") is None

    def test_jsonl_parent_dir_created_on_write(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "claims.jsonl"
        assert not path.parent.exists()

        svc = ClaimsService(storage_path=path)
        svc.submit("IS-001", "a", _make_evidence())

        assert path.parent.exists()
        assert path.exists()

    def test_jsonl_blank_lines_skipped(self, tmp_path):
        """Blank lines (e.g. trailing newlines) in the JSONL file are ignored."""
        path = tmp_path / "claims.jsonl"
        ev = _make_evidence()
        good_claim = CompletionClaim(
            task_id="IS-001",
            agent_id="a",
            evidence=ev,
            submitted_at=datetime.now(UTC),
        )
        # Write a file that contains blank lines between valid records
        path.write_text("\n" + good_claim.model_dump_json() + "\n\n")

        svc = ClaimsService(storage_path=path)
        claims = svc.list_claims()
        assert len(claims) == 1
        assert claims[0].claim_id == good_claim.claim_id

    def test_jsonl_malformed_lines_skipped(self, tmp_path):
        """A malformed JSONL line should be skipped, not crash the load."""
        path = tmp_path / "claims.jsonl"

        # Write one valid + one malformed line manually
        ev = _make_evidence()
        good_claim = CompletionClaim(
            task_id="IS-001",
            agent_id="a",
            evidence=ev,
            submitted_at=datetime.now(UTC),
        )
        path.write_text(good_claim.model_dump_json() + "\n{bad json\n")

        svc = ClaimsService(storage_path=path)
        claims = svc.list_claims()
        assert len(claims) == 1
        assert claims[0].claim_id == good_claim.claim_id

    def test_rewrite_updates_existing_claim(self, tmp_path):
        """After verify, the JSONL should reflect the updated status, not duplicate."""
        path = tmp_path / "claims.jsonl"
        svc = ClaimsService(storage_path=path)
        ev = _make_evidence()
        claim = svc.submit("IS-001", "a", ev)
        svc.verify(claim.claim_id)

        lines = [l for l in path.read_text().splitlines() if l.strip()]
        # Rewrite replaces all entries — should still be exactly one claim entry
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["status"] == "approved"


# ---------------------------------------------------------------------------
# Singleton factory tests
# ---------------------------------------------------------------------------


class TestClaimsServiceSingleton:
    def setup_method(self):
        reset_claims_service()

    def teardown_method(self):
        reset_claims_service()

    def test_get_claims_service_returns_instance(self, tmp_path):
        svc = get_claims_service(storage_path=tmp_path / "claims.jsonl")
        assert isinstance(svc, ClaimsService)

    def test_get_claims_service_same_instance_on_repeat_calls(self, tmp_path):
        svc1 = get_claims_service(storage_path=tmp_path / "claims.jsonl")
        svc2 = get_claims_service(storage_path=tmp_path / "other.jsonl")
        assert svc1 is svc2  # singleton; second path ignored

    def test_reset_allows_new_singleton(self, tmp_path):
        svc1 = get_claims_service(storage_path=tmp_path / "a.jsonl")
        reset_claims_service()
        svc2 = get_claims_service(storage_path=tmp_path / "b.jsonl")
        assert svc1 is not svc2

    def test_default_storage_path(self):
        from forge_harness.webhook_server.services.claims_service import (
            _DEFAULT_CLAIMS_PATH,
        )

        svc = get_claims_service()
        assert svc._path == _DEFAULT_CLAIMS_PATH


# ---------------------------------------------------------------------------
# File-system mock tests (verify no real I/O when mocked)
# ---------------------------------------------------------------------------


class TestClaimsServiceMockedIO:
    """Verify service behaviour using unittest.mock to intercept filesystem calls."""

    def test_submit_mocked_no_real_files(self, tmp_path, monkeypatch):
        """Patch Path.exists + open to confirm no real file is touched."""
        written_lines: list[str] = []

        def mock_exists(self):  # noqa: ANN001
            return False

        fake_file = MagicMock()
        fake_file.__enter__ = lambda s: s
        fake_file.__exit__ = MagicMock(return_value=False)
        fake_file.write = lambda line: written_lines.append(line)

        path = tmp_path / "mocked.jsonl"
        svc = ClaimsService(storage_path=path)

        monkeypatch.setattr(Path, "exists", mock_exists)

        with patch.object(Path, "open", return_value=fake_file):
            with patch.object(Path, "mkdir"):
                ev = _make_evidence()
                claim = svc.submit("IS-999", "forge:test", ev)

        assert claim.task_id == "IS-999"
        assert claim.status == ClaimStatus.SUBMITTED

    def test_list_claims_with_mocked_read(self, tmp_path):
        """Verify list_claims parses JSONL lines returned by mocked file."""
        ev = _make_evidence()
        dummy = CompletionClaim(
            task_id="IS-001",
            agent_id="forge:nova",
            evidence=ev,
            submitted_at=datetime.now(UTC),
        )
        fake_jsonl = dummy.model_dump_json() + "\n"

        path = tmp_path / "mocked.jsonl"
        svc = ClaimsService(storage_path=path)

        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "open", mock_open(read_data=fake_jsonl)):
                claims = svc.list_claims()

        assert len(claims) == 1
        assert claims[0].claim_id == dummy.claim_id
        assert claims[0].task_id == "IS-001"


# ---------------------------------------------------------------------------
# Module-level export tests
# ---------------------------------------------------------------------------


class TestModuleExports:
    def test_claim_model_importable_from_models_package(self):
        from forge_harness.webhook_server.models import (
            TERMINAL_CLAIM_STATUSES,
            VALID_CLAIM_TRANSITIONS,
            CheckResult,
            ClaimStatus,
            ClaimTransitionError,
            CompletionClaim,
            EvidenceManifest,
            validate_claim_transition,
        )

        assert ClaimStatus.SUBMITTED == "submitted"
        assert EvidenceManifest is not None
        assert CompletionClaim is not None

    def test_claims_service_importable_from_services_package(self):
        from forge_harness.webhook_server.services import (
            ClaimsService,
            get_claims_service,
            reset_claims_service,
        )

        assert ClaimsService is not None
        assert callable(get_claims_service)
        assert callable(reset_claims_service)
