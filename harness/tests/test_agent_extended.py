"""
Extended tests for FORGE agent — targeting uncovered lines.

Focused on:
- Circuit breaker (_should_skip_issue, _record_attempt)            lines 153-176
- _get_next_issue fallback path                                     line 321
- _build_coding_prompt retry context injection                      lines 405-421
- _run_coding_session (failure indicators, error path)             lines 485-541
- _run_preflight (logging, tracker calls)                          lines 563-580
- run() circuit breaker path                                       lines 666-700
- run() branch-creation fallback                                   lines 715-730
- run() retry-context clear on success                             line 780
- run() tracker.issue_failed on verification fail                  line 845
- run() max_iterations end_reason                                  lines 892-896
- run() stale-docs warning                                         lines 872-875
- FeatureOrchestrator._build_prompt                                lines 965-1014
- FeatureOrchestrator.implement_feature (success, failure, timeout) lines 1028-1075
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip entire module if claude_agent_sdk is not installed
pytest.importorskip("claude_agent_sdk", reason="claude_agent_sdk not installed")

from forge_harness.agent import AgentResult, FeatureOrchestrator, ForgeAgent
from forge_harness.llm_provider import MockProvider, ProviderOptions
from forge_harness.posthog_tracker import NoOpTracker, SessionMetrics

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_forge_root(tmp_path: Path) -> Path:
    """Create minimal FORGE root structure required by ForgeAgent."""
    project_dir = tmp_path / "test-domain" / "test-project"
    project_dir.mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("# FORGE Portfolio")
    (tmp_path / "test-domain" / "CLAUDE.md").write_text("# Test Domain")
    (project_dir / "CLAUDE.md").write_text("# Test Project")
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "00-portfolio-digest.md").write_text("# Portfolio\nSprint 6")
    (docs_dir / "progress.md").write_text("# Progress")
    return tmp_path


def _make_agent(tmp_path: Path, **kwargs) -> ForgeAgent:
    """Build a ForgeAgent with all external dependencies patched out."""
    forge_root = _make_forge_root(tmp_path)
    defaults = dict(
        domain="test-domain",
        project="test-project",
        forge_root=forge_root,
        github_repo="owner/repo",
        tracker=NoOpTracker(domain="test-domain", project="test-project", session_id="t"),
    )
    defaults.update(kwargs)
    with (
        patch("forge_harness.agent.GitHubClient"),
        patch("forge_harness.agent.LivingDocs"),
        patch("forge_harness.agent.ForgeDeployer"),
    ):
        agent = ForgeAgent(**defaults)
    return agent


# ---------------------------------------------------------------------------
# Circuit Breaker Tests
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Tests for _should_skip_issue and _record_attempt (lines 153-176)."""

    @pytest.fixture
    def agent(self, tmp_path):
        return _make_agent(tmp_path)

    def test_should_skip_returns_false_for_new_issue(self, agent):
        """Issue not yet attempted — never skip."""
        assert agent._should_skip_issue(99, "abc12345") is False

    def test_should_skip_returns_false_below_max_attempts(self, agent):
        """Issue with fewer attempts than limit — do not skip."""
        agent.issue_attempts[42] = (2, "sig123")  # attempt 2 of 3
        assert agent._should_skip_issue(42, "sig123") is False

    def test_should_skip_returns_true_at_max_attempts_same_sig(self, agent):
        """Issue at max attempts with same error sig — skip it."""
        agent.issue_attempts[42] = (3, "sig123")
        assert agent._should_skip_issue(42, "sig123") is True

    def test_should_skip_returns_false_different_sig(self, agent):
        """Max attempts reached but error signature changed — do not skip."""
        agent.issue_attempts[42] = (3, "oldsig1")
        assert agent._should_skip_issue(42, "newsig2") is False

    def test_record_attempt_first_attempt(self, agent):
        """First attempt for new issue records count=1."""
        count = agent._record_attempt(10, "sig_a")
        assert count == 1
        assert agent.issue_attempts[10] == (1, "sig_a")

    def test_record_attempt_increments_same_sig(self, agent):
        """Second attempt with same sig increments counter."""
        agent.issue_attempts[10] = (1, "sig_a")
        count = agent._record_attempt(10, "sig_a")
        assert count == 2
        assert agent.issue_attempts[10] == (2, "sig_a")

    def test_record_attempt_resets_different_sig(self, agent):
        """Different error sig resets counter to 1."""
        agent.issue_attempts[10] = (3, "old_sig")
        count = agent._record_attempt(10, "new_sig")
        assert count == 1
        assert agent.issue_attempts[10] == (1, "new_sig")

    def test_compute_error_signature_is_deterministic(self, agent):
        """Same errors always produce same signature."""
        errors = ["Error A", "Error B", "Error C"]
        sig1 = agent._compute_error_signature(errors)
        sig2 = agent._compute_error_signature(errors)
        assert sig1 == sig2
        assert len(sig1) == 8  # 8 hex chars

    def test_compute_error_signature_different_errors(self, agent):
        """Different errors produce different signatures."""
        sig1 = agent._compute_error_signature(["Error A"])
        sig2 = agent._compute_error_signature(["Error B"])
        assert sig1 != sig2


# ---------------------------------------------------------------------------
# _get_next_issue fallback tests
# ---------------------------------------------------------------------------


class TestGetNextIssueFallback:
    """Tests for _get_next_issue fallback paths (line 321)."""

    @pytest.fixture
    def agent(self, tmp_path):
        return _make_agent(tmp_path)

    def test_fallback_to_any_open_issue(self, agent):
        """When no priority labels match, returns any open issue for the project."""
        def mock_list(labels, limit):
            # Return empty for in-progress and all priority queries
            if "status:in-progress" in labels:
                return []
            for p in ["priority:critical", "priority:high", "priority:medium", "priority:low"]:
                if p in labels:
                    return []
            # Fallback call (base labels only) — return something
            return [{"number": 7, "title": "Fallback issue"}]

        agent.github.list_issues = mock_list
        issue = agent._get_next_issue()
        assert issue is not None
        assert issue["number"] == 7

    def test_returns_none_when_no_issues_at_all(self, agent):
        """None returned when even the fallback call finds nothing."""
        agent.github.list_issues = MagicMock(return_value=[])
        assert agent._get_next_issue() is None


# ---------------------------------------------------------------------------
# _build_coding_prompt with retry context
# ---------------------------------------------------------------------------


class TestBuildCodingPromptRetryContext:
    """Tests for retry context injection in _build_coding_prompt (lines 405-421)."""

    @pytest.fixture
    def agent(self, tmp_path):
        a = _make_agent(tmp_path)
        a.github.get_issue = MagicMock(return_value={"body": "Do the thing", "labels": []})
        return a

    def _base_context(self):
        return {
            "current_sprint": "Sprint X",
            "priorities": [],
            "blockers": [],
            "recent_milestones": [],
        }

    def test_no_retry_context_no_failure_banner(self, agent):
        """Without retry context, no PREVIOUS SESSION FAILED block in prompt."""
        issue = {"number": 1, "title": "New issue", "labels": []}
        prompt = agent._build_coding_prompt(self._base_context(), issue)
        assert "PREVIOUS SESSION FAILED" not in prompt

    def test_retry_context_injects_error_details(self, agent):
        """With retry context, error details appear in prompt."""
        agent.retry_context[5] = (
            ["ImportError: cannot import 'Foo'", "AssertionError: 1 != 2"],
            ["Run: uv add foo-lib"],
        )
        issue = {"number": 5, "title": "Retry issue", "labels": []}
        prompt = agent._build_coding_prompt(self._base_context(), issue)

        assert "PREVIOUS SESSION FAILED" in prompt
        assert "ImportError: cannot import 'Foo'" in prompt
        assert "AssertionError: 1 != 2" in prompt

    def test_retry_context_injects_fix_suggestions(self, agent):
        """With retry context, fix suggestions appear in prompt."""
        agent.retry_context[5] = (
            ["Some error"],
            ["Fix suggestion A", "Fix suggestion B"],
        )
        issue = {"number": 5, "title": "Retry issue", "labels": []}
        prompt = agent._build_coding_prompt(self._base_context(), issue)

        assert "Fix suggestion A" in prompt
        assert "Fix suggestion B" in prompt

    def test_retry_context_limits_to_ten_errors(self, agent):
        """At most 10 errors are injected even with more in context."""
        errors = [f"Error {i}" for i in range(20)]
        agent.retry_context[3] = (errors, [])
        issue = {"number": 3, "title": "Many errors", "labels": []}
        prompt = agent._build_coding_prompt(self._base_context(), issue)

        # Errors 0-9 must appear; errors 10-19 should not
        assert "Error 9" in prompt
        assert "Error 10" not in prompt

    def test_build_prompt_no_compliance_items(self, agent):
        """When domain has no compliance/localization/special_rules, no-requirement text used."""
        agent.domain_config.compliance = []
        agent.domain_config.localization = None
        agent.domain_config.special_rules = {}
        issue = {"number": 1, "title": "Plain issue", "labels": []}
        prompt = agent._build_coding_prompt(self._base_context(), issue)
        assert "No special compliance requirements." in prompt


# ---------------------------------------------------------------------------
# _run_coding_session tests
# ---------------------------------------------------------------------------


class TestRunCodingSession:
    """Tests for _run_coding_session with MockProvider (lines 485-541)."""

    @pytest.fixture
    def agent(self, tmp_path):
        provider = MockProvider()
        return _make_agent(tmp_path, provider=provider)

    @pytest.mark.asyncio
    async def test_returns_success_for_normal_output(self, agent):
        """Normal provider output yields success=True."""
        issue = {"number": 1, "title": "Test issue"}
        success, summary = await agent._run_coding_session("Some prompt", issue)
        assert success is True
        assert isinstance(summary, str)

    @pytest.mark.asyncio
    async def test_failure_indicator_i_cannot_complete(self, agent):
        """'i cannot complete this task' indicator causes success=False."""
        async def _mock_session(prompt, options):
            yield type("Msg", (), {"content": "i cannot complete this task"})()

        agent.provider.run_coding_session = _mock_session
        issue = {"number": 2, "title": "Failing issue"}
        success, _ = await agent._run_coding_session("prompt", issue)
        assert success is False

    @pytest.mark.asyncio
    async def test_failure_indicator_task_failed(self, agent):
        """'task failed:' indicator causes success=False."""
        async def _mock_session(prompt, options):
            yield type("Msg", (), {"content": "task failed: something broke"})()

        agent.provider.run_coding_session = _mock_session
        issue = {"number": 3, "title": "Another issue"}
        success, _ = await agent._run_coding_session("prompt", issue)
        assert success is False

    @pytest.mark.asyncio
    async def test_failure_indicator_human_review_required(self, agent):
        """'human review required' indicator causes success=False."""
        async def _mock_session(prompt, options):
            yield type("Msg", (), {"content": "human review required"})()

        agent.provider.run_coding_session = _mock_session
        issue = {"number": 4, "title": "Gate triggered"}
        success, _ = await agent._run_coding_session("prompt", issue)
        assert success is False

    @pytest.mark.asyncio
    async def test_failure_indicator_unable_to_proceed(self, agent):
        """'unable to proceed' indicator causes success=False."""
        async def _mock_session(prompt, options):
            yield type("Msg", (), {"content": "unable to proceed with this implementation"})()

        agent.provider.run_coding_session = _mock_session
        issue = {"number": 5, "title": "Blocked issue"}
        success, _ = await agent._run_coding_session("prompt", issue)
        assert success is False

    @pytest.mark.asyncio
    async def test_exception_in_session_returns_failure(self, agent):
        """Provider exception causes (False, error_string) return."""
        async def _bad_session(prompt, options):
            raise RuntimeError("provider exploded")
            # Need at least one yield to be a generator
            yield  # pragma: no cover

        agent.provider.run_coding_session = _bad_session
        issue = {"number": 6, "title": "Exploding issue"}
        success, summary = await agent._run_coding_session("prompt", issue)
        assert success is False
        assert "provider exploded" in summary

    @pytest.mark.asyncio
    async def test_summary_truncated_to_2000_chars(self, agent):
        """Summary is truncated to 2000 characters."""
        big = "X" * 5000

        async def _mock_session(prompt, options):
            yield type("Msg", (), {"content": big})()

        agent.provider.run_coding_session = _mock_session
        issue = {"number": 7, "title": "Big output"}
        success, summary = await agent._run_coding_session("prompt", issue)
        assert len(summary) <= 2000


# ---------------------------------------------------------------------------
# _run_preflight tests
# ---------------------------------------------------------------------------


class TestRunPreflight:
    """Tests for _run_preflight logging and tracker integration (lines 563-580)."""

    @pytest.fixture
    def agent(self, tmp_path):
        a = _make_agent(tmp_path, verbose=True)
        a.tracker = MagicMock()
        return a

    def test_preflight_logs_pass(self, agent, capsys):
        """Passed preflight emits PASSED log when verbose."""
        with patch("forge_harness.agent.PreflightChecker") as MockChecker:
            MockChecker.return_value.run.return_value = MagicMock(
                passed=True, fixes_applied=[], issues=[]
            )
            agent._run_preflight()
        out = capsys.readouterr().out
        assert "PASSED" in out

    def test_preflight_logs_fail(self, agent, capsys):
        """Failed preflight emits FAILED log when verbose."""
        with patch("forge_harness.agent.PreflightChecker") as MockChecker:
            MockChecker.return_value.run.return_value = MagicMock(
                passed=False, fixes_applied=[], issues=["Missing pyproject.toml"]
            )
            agent._run_preflight()
        out = capsys.readouterr().out
        assert "FAILED" in out

    def test_preflight_logs_auto_fixes(self, agent, capsys):
        """Auto-fixes are logged when verbose."""
        with patch("forge_harness.agent.PreflightChecker") as MockChecker:
            MockChecker.return_value.run.return_value = MagicMock(
                passed=True,
                fixes_applied=["Created __init__.py"],
                issues=[],
            )
            agent._run_preflight()
        out = capsys.readouterr().out
        assert "Auto-fixed" in out

    def test_preflight_logs_issues_when_verbose(self, agent, capsys):
        """Issues are logged when verbose."""
        with patch("forge_harness.agent.PreflightChecker") as MockChecker:
            MockChecker.return_value.run.return_value = MagicMock(
                passed=False,
                fixes_applied=[],
                issues=["Missing tests dir"],
            )
            agent._run_preflight()
        out = capsys.readouterr().out
        assert "Issue" in out

    def test_preflight_calls_tracker_when_fixes_applied(self, agent):
        """Tracker event fired when fixes are applied."""
        with patch("forge_harness.agent.PreflightChecker") as MockChecker:
            MockChecker.return_value.run.return_value = MagicMock(
                passed=True,
                fixes_applied=["Created pyproject.toml"],
                issues=[],
            )
            agent._run_preflight()
        agent.tracker.track_event.assert_called_once()
        call_args = agent.tracker.track_event.call_args
        assert call_args[0][0] == "preflight_completed"

    def test_preflight_calls_tracker_when_issues_present(self, agent):
        """Tracker event fired when issues are detected."""
        with patch("forge_harness.agent.PreflightChecker") as MockChecker:
            MockChecker.return_value.run.return_value = MagicMock(
                passed=False,
                fixes_applied=[],
                issues=["Missing tests dir"],
            )
            agent._run_preflight()
        agent.tracker.track_event.assert_called_once()

    def test_preflight_no_tracker_event_when_clean(self, agent):
        """Tracker NOT called when no fixes and no issues."""
        with patch("forge_harness.agent.PreflightChecker") as MockChecker:
            MockChecker.return_value.run.return_value = MagicMock(
                passed=True, fixes_applied=[], issues=[]
            )
            agent._run_preflight()
        agent.tracker.track_event.assert_not_called()


# ---------------------------------------------------------------------------
# run() — circuit breaker path
# ---------------------------------------------------------------------------


class TestRunCircuitBreaker:
    """Tests for the circuit-breaker branch inside run() (lines 686-700)."""

    @pytest.fixture
    def full_agent(self, tmp_path):
        """Agent with all integrations fully patched."""
        forge_root = _make_forge_root(tmp_path)
        with (
            patch("forge_harness.agent.GitHubClient") as MockGH,
            patch("forge_harness.agent.LivingDocs") as MockDocs,
            patch("forge_harness.agent.ForgeDeployer"),
            patch("forge_harness.agent.BranchManager") as MockBranch,
        ):
            mock_gh = MagicMock()
            mock_gh.list_issues.side_effect = [
                [{"number": 5, "title": "Stuck issue", "labels": []}],
                [],
            ]
            MockGH.return_value = mock_gh

            mock_docs = MagicMock()
            mock_docs.consult.return_value = MagicMock(
                current_sprint="Sprint T", blockers=[], priorities=[], recent_milestones=[]
            )
            mock_docs.sync.return_value = {}
            MockDocs.return_value = mock_docs

            mock_branch = MagicMock()
            mock_branch.is_on_protected_branch.return_value = False
            MockBranch.return_value = mock_branch

            agent = ForgeAgent(
                domain="test-domain",
                project="test-project",
                forge_root=forge_root,
                github_repo="owner/repo",
                tracker=NoOpTracker(domain="d", project="p", session_id="s"),
            )
            # Pre-seed circuit breaker for issue 5 — already at max attempts
            agent.issue_attempts[5] = (3, "some_sig")
            return agent

    @pytest.mark.asyncio
    async def test_circuit_breaker_skips_issue_and_adds_to_failed(self, full_agent):
        """Issue at max attempts is skipped and added to issues_failed."""
        result = await full_agent.run()
        assert 5 in result.issues_failed

    @pytest.mark.asyncio
    async def test_circuit_breaker_labels_issue_blocked(self, full_agent):
        """Issue at max attempts gets status:blocked label."""
        await full_agent.run()
        # At least one update_issue call should add status:blocked
        calls = [str(c) for c in full_agent.github.update_issue.call_args_list]
        assert any("status:blocked" in c for c in calls)


# ---------------------------------------------------------------------------
# run() — branch creation fallback
# ---------------------------------------------------------------------------


class TestRunBranchCreation:
    """Tests for branch creation with remote fallback (lines 715-730)."""

    def _setup_agent(self, tmp_path, branch_side_effect=None):
        forge_root = _make_forge_root(tmp_path)
        with (
            patch("forge_harness.agent.GitHubClient") as MockGH,
            patch("forge_harness.agent.LivingDocs") as MockDocs,
            patch("forge_harness.agent.ForgeDeployer"),
            patch("forge_harness.agent.BranchManager") as MockBranch,
        ):
            mock_gh = MagicMock()
            mock_gh.list_issues.side_effect = [
                [{"number": 9, "title": "Branch test", "labels": []}],
                [],
            ]
            mock_gh.get_issue.return_value = {"body": ""}
            MockGH.return_value = mock_gh

            mock_docs = MagicMock()
            mock_docs.consult.return_value = MagicMock(
                current_sprint="S", blockers=[], priorities=[], recent_milestones=[]
            )
            mock_docs.sync.return_value = {}
            MockDocs.return_value = mock_docs

            mock_branch = MagicMock()
            mock_branch.is_on_protected_branch.return_value = True
            if branch_side_effect:
                mock_branch.create_feature_branch.side_effect = branch_side_effect
            else:
                mock_branch.create_feature_branch.return_value = "feature/9-branch-test"
            MockBranch.return_value = mock_branch

            agent = ForgeAgent(
                domain="test-domain",
                project="test-project",
                forge_root=forge_root,
                github_repo="owner/repo",
                tracker=NoOpTracker(domain="d", project="p", session_id="s"),
            )
            agent._run_coding_session = AsyncMock(return_value=(False, "err"))
            return agent

    @pytest.mark.asyncio
    async def test_creates_feature_branch_on_protected(self, tmp_path):
        """On protected branch, create_feature_branch is called."""
        agent = self._setup_agent(tmp_path)
        await agent.run()
        agent.branch_manager.create_feature_branch.assert_called()

    @pytest.mark.asyncio
    async def test_fallback_to_local_branch_on_remote_failure(self, tmp_path):
        """Remote branch creation failure falls back to local (from_remote=False)."""
        # First call (from_remote=True) raises; second (from_remote=False) succeeds
        agent = self._setup_agent(
            tmp_path,
            branch_side_effect=[
                Exception("remote failed"),
                "feature/9-local",
            ],
        )
        await agent.run()
        assert agent.branch_manager.create_feature_branch.call_count == 2
        # Second call should have from_remote=False
        second_call_kwargs = agent.branch_manager.create_feature_branch.call_args_list[1].kwargs
        assert second_call_kwargs.get("from_remote") is False


# ---------------------------------------------------------------------------
# run() — retry context cleared on success
# ---------------------------------------------------------------------------


class TestRunRetryContextCleared:
    """Tests retry context cleared when issue passes verification (line 780)."""

    @pytest.fixture
    def agent(self, tmp_path):
        forge_root = _make_forge_root(tmp_path)
        with (
            patch("forge_harness.agent.GitHubClient") as MockGH,
            patch("forge_harness.agent.LivingDocs") as MockDocs,
            patch("forge_harness.agent.ForgeDeployer"),
            patch("forge_harness.agent.BranchManager") as MockBranch,
        ):
            mock_gh = MagicMock()
            mock_gh.list_issues.side_effect = [
                [{"number": 11, "title": "Retry clear", "labels": []}],
                [],
            ]
            mock_gh.get_issue.return_value = {"body": ""}
            MockGH.return_value = mock_gh

            mock_docs = MagicMock()
            mock_docs.consult.return_value = MagicMock(
                current_sprint="S", blockers=[], priorities=[], recent_milestones=[]
            )
            mock_docs.sync.return_value = {}
            MockDocs.return_value = mock_docs

            mock_branch = MagicMock()
            mock_branch.is_on_protected_branch.return_value = False
            MockBranch.return_value = mock_branch

            agent = ForgeAgent(
                domain="test-domain",
                project="test-project",
                forge_root=forge_root,
                github_repo="owner/repo",
                tracker=NoOpTracker(domain="d", project="p", session_id="s"),
            )
            # Seed retry context for issue 11
            agent.retry_context[11] = (["Prior error"], ["Prior fix"])
            return agent

    @pytest.mark.asyncio
    async def test_retry_context_cleared_after_successful_verification(self, agent):
        """Retry context removed from dict when issue passes verification."""
        agent._run_coding_session = AsyncMock(return_value=(True, "Done"))
        with patch("forge_harness.agent.Verifier") as MockVerifier:
            MockVerifier.return_value.verify.return_value = MagicMock(
                passed=True,
                tests_passed=5,
                tests_failed=0,
                coverage_backend=80.0,
                coverage_frontend=70.0,
                lint_errors=0,
                summary="PASSED",
                error_message=None,
            )
            await agent.run()

        assert 11 not in agent.retry_context


# ---------------------------------------------------------------------------
# run() — tracker.issue_failed for verification failure
# ---------------------------------------------------------------------------


class TestRunTrackerVerificationFailed:
    """Tests tracker.issue_failed called when verification fails (line 845)."""

    @pytest.fixture
    def agent(self, tmp_path):
        forge_root = _make_forge_root(tmp_path)
        with (
            patch("forge_harness.agent.GitHubClient") as MockGH,
            patch("forge_harness.agent.LivingDocs") as MockDocs,
            patch("forge_harness.agent.ForgeDeployer"),
            patch("forge_harness.agent.BranchManager") as MockBranch,
        ):
            mock_gh = MagicMock()
            mock_gh.list_issues.side_effect = [
                [{"number": 20, "title": "VF issue", "labels": []}],
                [],
            ]
            mock_gh.get_issue.return_value = {"body": ""}
            MockGH.return_value = mock_gh

            mock_docs = MagicMock()
            mock_docs.consult.return_value = MagicMock(
                current_sprint="S", blockers=[], priorities=[], recent_milestones=[]
            )
            mock_docs.sync.return_value = {}
            MockDocs.return_value = mock_docs

            mock_branch = MagicMock()
            mock_branch.is_on_protected_branch.return_value = False
            MockBranch.return_value = mock_branch

            tracker = MagicMock()
            agent = ForgeAgent(
                domain="test-domain",
                project="test-project",
                forge_root=forge_root,
                github_repo="owner/repo",
                tracker=tracker,
            )
            return agent

    @pytest.mark.asyncio
    async def test_tracker_issue_failed_called_on_verification_failure(self, agent):
        """tracker.issue_failed fired when coding OK but verification fails."""
        agent._run_coding_session = AsyncMock(return_value=(True, "Done"))
        with patch("forge_harness.agent.Verifier") as MockVerifier:
            MockVerifier.return_value.verify.return_value = MagicMock(
                passed=False,
                tests_passed=0,
                tests_failed=3,
                coverage_backend=30.0,
                coverage_frontend=20.0,
                lint_errors=5,
                summary="FAILED",
                error_message="Tests failed",
                error_details=["test_foo FAILED"],
                fix_suggestions=["Fix foo"],
            )
            result = await agent.run()
        agent.tracker.issue_failed.assert_called()
        call_args = agent.tracker.issue_failed.call_args
        assert call_args[0][2] == "verification_failed"


# ---------------------------------------------------------------------------
# run() — end reason logic
# ---------------------------------------------------------------------------


class TestRunEndReason:
    """Tests for session end reason passed to tracker (lines 892-896)."""

    def _setup_full(self, tmp_path, max_iter=None):
        forge_root = _make_forge_root(tmp_path)
        with (
            patch("forge_harness.agent.GitHubClient") as MockGH,
            patch("forge_harness.agent.LivingDocs") as MockDocs,
            patch("forge_harness.agent.ForgeDeployer"),
            patch("forge_harness.agent.BranchManager") as MockBranch,
        ):
            mock_gh = MagicMock()
            mock_gh.list_issues.return_value = []
            MockGH.return_value = mock_gh

            mock_docs = MagicMock()
            mock_docs.consult.return_value = MagicMock(
                current_sprint="S", blockers=[], priorities=[], recent_milestones=[]
            )
            mock_docs.sync.return_value = {}
            MockDocs.return_value = mock_docs

            mock_branch = MagicMock()
            mock_branch.is_on_protected_branch.return_value = False
            MockBranch.return_value = mock_branch

            tracker = MagicMock()
            agent = ForgeAgent(
                domain="test-domain",
                project="test-project",
                forge_root=forge_root,
                github_repo="owner/repo",
                tracker=tracker,
                max_iterations=max_iter,
            )
        return agent

    @pytest.mark.asyncio
    async def test_end_reason_completed_when_no_issues(self, tmp_path):
        """end_reason='completed' when loop ends normally."""
        agent = self._setup_full(tmp_path)
        await agent.run()
        call_args = agent.tracker.session_ended.call_args
        assert call_args[0][1] == "completed"

    @pytest.mark.asyncio
    async def test_end_reason_errors_when_errors_list_non_empty(self, tmp_path):
        """end_reason='errors' when errors list is non-empty."""
        agent = self._setup_full(tmp_path)
        agent._load_context = MagicMock(side_effect=ValueError("boom"))
        await agent.run()
        # Session ended with error reason
        call_args = agent.tracker.session_ended.call_args
        assert call_args[0][1] == "error"

    @pytest.mark.asyncio
    async def test_stale_docs_warning_logged(self, tmp_path):
        """Stale docs in sync result triggers warning log."""
        agent = self._setup_full(tmp_path)
        agent.verbose = True
        agent.living_docs.sync.return_value = {"missing": [], "stale": ["active-context.md"]}
        import io
        import sys

        captured = io.StringIO()
        sys.stdout = captured
        try:
            await agent.run()
        finally:
            sys.stdout = sys.__stdout__

        assert "Stale docs" in captured.getvalue()


# ---------------------------------------------------------------------------
# FeatureOrchestrator._build_prompt tests
# ---------------------------------------------------------------------------


class TestFeatureOrchestratorBuildPrompt:
    """Tests for FeatureOrchestrator._build_prompt (lines 965-1014)."""

    def _make_feature(self, **kwargs):
        defaults = dict(
            id="F-001",
            name="Add login endpoint",
            description="Implement POST /auth/login returning JWT",
            acceptance_criteria=["Returns 200 on valid creds", "Returns 401 on invalid creds"],
        )
        defaults.update(kwargs)
        return type("Feature", (), defaults)()

    @pytest.fixture
    def orchestrator(self, tmp_path):
        return FeatureOrchestrator(
            working_dir=tmp_path,
            model="mock-model",
            context="# Project Context",
            provider=MockProvider(),
        )

    def test_prompt_contains_feature_name(self, orchestrator):
        """Feature name appears in the prompt."""
        feature = self._make_feature()
        prompt = orchestrator._build_prompt(feature)
        assert "Add login endpoint" in prompt

    def test_prompt_contains_feature_id(self, orchestrator):
        """Feature ID appears in the prompt."""
        feature = self._make_feature()
        prompt = orchestrator._build_prompt(feature)
        assert "F-001" in prompt

    def test_prompt_contains_description(self, orchestrator):
        """Feature description appears in the prompt."""
        feature = self._make_feature()
        prompt = orchestrator._build_prompt(feature)
        assert "POST /auth/login" in prompt

    def test_prompt_contains_acceptance_criteria(self, orchestrator):
        """All acceptance criteria appear as list items."""
        feature = self._make_feature()
        prompt = orchestrator._build_prompt(feature)
        assert "Returns 200 on valid creds" in prompt
        assert "Returns 401 on invalid creds" in prompt

    def test_prompt_includes_context(self, orchestrator):
        """Context string is appended to the prompt."""
        feature = self._make_feature()
        prompt = orchestrator._build_prompt(feature)
        assert "# Project Context" in prompt

    def test_prompt_files_to_create_included(self, orchestrator):
        """Files to create section appears when attribute is set."""
        feature = self._make_feature(files_to_create=["app/auth.py", "tests/test_auth.py"])
        prompt = orchestrator._build_prompt(feature)
        assert "app/auth.py" in prompt
        assert "tests/test_auth.py" in prompt
        assert "Files to Create" in prompt

    def test_prompt_files_to_modify_included(self, orchestrator):
        """Files to modify section appears when attribute is set."""
        feature = self._make_feature(files_to_modify=["app/main.py"])
        prompt = orchestrator._build_prompt(feature)
        assert "app/main.py" in prompt
        assert "Files to Modify" in prompt

    def test_prompt_no_files_sections_when_absent(self, orchestrator):
        """Files sections omitted when attributes not set."""
        feature = self._make_feature()  # no files_to_create / files_to_modify
        prompt = orchestrator._build_prompt(feature)
        assert "Files to Create" not in prompt
        assert "Files to Modify" not in prompt

    def test_prompt_no_files_sections_when_empty(self, orchestrator):
        """Files sections omitted when attributes are empty lists."""
        feature = self._make_feature(files_to_create=[], files_to_modify=[])
        prompt = orchestrator._build_prompt(feature)
        assert "Files to Create" not in prompt
        assert "Files to Modify" not in prompt


# ---------------------------------------------------------------------------
# FeatureOrchestrator.implement_feature tests
# ---------------------------------------------------------------------------


class TestFeatureOrchestratorImplementFeature:
    """Tests for FeatureOrchestrator.implement_feature (lines 1028-1075)."""

    def _make_feature(self):
        return type(
            "Feature",
            (),
            {
                "id": "F-002",
                "name": "Register endpoint",
                "description": "POST /auth/register",
                "acceptance_criteria": ["Stores user in DB", "Returns 201"],
            },
        )()

    @pytest.fixture
    def orchestrator(self, tmp_path):
        return FeatureOrchestrator(
            working_dir=tmp_path,
            model="mock-model",
            provider=MockProvider(),
        )

    @pytest.mark.asyncio
    async def test_implement_feature_success(self, orchestrator):
        """MockProvider yields clean output — success=True, error=None."""
        feature = self._make_feature()
        success, error = await orchestrator.implement_feature(feature)
        assert success is True
        assert error is None

    @pytest.mark.asyncio
    async def test_implement_feature_failure_indicator(self, orchestrator, tmp_path):
        """Failure indicator in output returns success=False with message."""
        async def _fail_session(prompt, options):
            yield type("Msg", (), {"content": "i cannot complete this task"})()

        orchestrator.provider.run_coding_session = _fail_session
        feature = self._make_feature()
        success, error = await orchestrator.implement_feature(feature)
        assert success is False
        assert error is not None
        assert "i cannot complete this task" in error

    @pytest.mark.asyncio
    async def test_implement_feature_timeout(self, orchestrator, tmp_path):
        """asyncio.TimeoutError returns (False, timeout message)."""
        async def _slow_session(prompt, options):
            await asyncio.sleep(999)
            yield type("Msg", (), {"content": "done"})()

        orchestrator.provider.run_coding_session = _slow_session
        feature = self._make_feature()
        success, error = await orchestrator.implement_feature(feature, timeout_seconds=0)
        # With timeout=0 the wait_for should fire immediately
        assert success is False
        assert error is not None

    @pytest.mark.asyncio
    async def test_implement_feature_exception(self, orchestrator, tmp_path):
        """Generic exception returns (False, exception string)."""
        async def _boom_session(prompt, options):
            raise ValueError("unexpected crash")
            yield  # make it a generator  # pragma: no cover

        orchestrator.provider.run_coding_session = _boom_session
        feature = self._make_feature()
        success, error = await orchestrator.implement_feature(feature)
        assert success is False
        assert "unexpected crash" in error

    @pytest.mark.asyncio
    async def test_implement_feature_all_indicators_checked(self, orchestrator):
        """All four failure indicators trigger failure."""
        indicators = [
            "i cannot complete this task",
            "unable to implement",
            "failed to create",
            "error occurred",
        ]
        feature = self._make_feature()
        for indicator in indicators:
            async def _make_session(ind=indicator):
                async def session(prompt, options):
                    yield type("Msg", (), {"content": ind})()
                return session

            orchestrator.provider.run_coding_session = await _make_session()
            success, error = await orchestrator.implement_feature(feature)
            assert success is False, f"Indicator '{indicator}' should cause failure"

    @pytest.mark.asyncio
    async def test_implement_feature_no_content_attribute_ok(self, orchestrator):
        """Messages without 'content' attribute are silently ignored."""
        async def _session(prompt, options):
            yield type("Msg", (), {})()  # No 'content' attribute
            yield type("Msg", (), {"content": "Implementation complete."})()

        orchestrator.provider.run_coding_session = _session
        feature = self._make_feature()
        success, error = await orchestrator.implement_feature(feature)
        assert success is True
        assert error is None

    def test_feature_orchestrator_defaults(self, tmp_path):
        """FeatureOrchestrator sets correct defaults when no provider given."""
        # ClaudeCodeProvider import may fail in test env — use MockProvider
        orch = FeatureOrchestrator(
            working_dir=tmp_path,
            model="claude-sonnet-4-20250514",
            max_iterations=25,
            provider=MockProvider(),
        )
        assert orch.model == "claude-sonnet-4-20250514"
        assert orch.max_iterations == 25
        assert orch.context == ""

    def test_feature_orchestrator_with_context(self, tmp_path):
        """FeatureOrchestrator stores context correctly."""
        orch = FeatureOrchestrator(
            working_dir=tmp_path,
            context="## Extra context",
            provider=MockProvider(),
        )
        assert orch.context == "## Extra context"
