"""
Pure unit tests for forge_harness.agent

Covers:
- AgentResult dataclass
- ForgeAgent.__init__ and attribute setup
- ForgeAgent._log (verbose/silent)
- ForgeAgent._compute_error_signature
- ForgeAgent._should_skip_issue (circuit breaker)
- ForgeAgent._record_attempt (circuit breaker)
- ForgeAgent._create_security_hook
- ForgeAgent._validate_human_gates
- ForgeAgent._load_context
- ForgeAgent._get_next_issue
- ForgeAgent._build_coding_prompt
- ForgeAgent._get_default_prompt
- ForgeAgent._run_coding_session
- ForgeAgent._run_preflight
- ForgeAgent._run_verification
- ForgeAgent.run (happy path, failure paths, circuit breaker, human gates, deploy)
- FeatureOrchestrator.__init__
- FeatureOrchestrator._build_prompt
- FeatureOrchestrator.implement_feature
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build consistent mocks for all heavy dependencies
# ---------------------------------------------------------------------------

FORGE_ROOT = Path("/fake/forge")
DOMAIN = "test-domain"
PROJECT = "test-project"
GITHUB_REPO = "org/test-repo"


def _make_domain_config(
    human_gates: list[str] | None = None,
    compliance: list[str] | None = None,
    frontend_tier: str = "React",
    localization: str | None = None,
    special_rules: dict | None = None,
):
    """Return a DomainConfig-like MagicMock."""
    cfg = MagicMock()
    cfg.human_gates = human_gates or []
    cfg.compliance = compliance or []
    cfg.frontend_tier = frontend_tier
    cfg.localization = localization
    cfg.special_rules = special_rules or {}
    return cfg


def _make_agent(
    domain: str = DOMAIN,
    project: str = PROJECT,
    forge_root: Path = FORGE_ROOT,
    github_repo: str = GITHUB_REPO,
    model: str = "claude-sonnet-4-20250514",
    max_iterations: int | None = None,
    deploy: bool = False,
    skip_quality_gates: bool = False,
    tracker=None,
    verbose: bool = False,
    provider=None,
    human_gates: list[str] | None = None,
):
    """
    Build a ForgeAgent with all heavy deps mocked at module level.
    Returns (agent, patch_dict) so callers can access sub-mocks.
    """
    # We patch at the module level so __init__ sees the mocks
    mock_domain_config = _make_domain_config(human_gates=human_gates)
    mock_living_docs = MagicMock()
    mock_github = MagicMock()
    mock_deployer = MagicMock()
    mock_branch_manager = MagicMock()
    mock_security_context = MagicMock()
    mock_context_monitor = MagicMock()
    mock_memory_manager = MagicMock()
    mock_metrics = MagicMock()
    mock_metrics.issues_attempted = 0
    mock_metrics.issues_completed = 0
    mock_metrics.issues_failed = 0

    with (
        patch("forge_harness.agent.get_domain_config", return_value=mock_domain_config),
        patch("forge_harness.agent.LivingDocs", return_value=mock_living_docs),
        patch("forge_harness.agent.GitHubClient", return_value=mock_github),
        patch("forge_harness.agent.ForgeDeployer", return_value=mock_deployer),
        patch("forge_harness.agent.BranchManager", return_value=mock_branch_manager),
        patch("forge_harness.agent.create_forge_security_context", return_value=mock_security_context),
        patch("forge_harness.agent.ContextMonitor", return_value=mock_context_monitor),
        patch("forge_harness.agent.MemoryFlushManager", return_value=mock_memory_manager),
        patch("forge_harness.agent.SessionMetrics", return_value=mock_metrics),
        patch("forge_harness.agent.load_claude_md_hierarchy"),
    ):
        from forge_harness.agent import ForgeAgent

        if provider is None:
            provider = MagicMock()
            provider.run_coding_session = AsyncMock(return_value=aiter([]))

        agent = ForgeAgent(
            domain=domain,
            project=project,
            forge_root=forge_root,
            github_repo=github_repo,
            model=model,
            max_iterations=max_iterations,
            deploy=deploy,
            skip_quality_gates=skip_quality_gates,
            tracker=tracker,
            verbose=verbose,
            provider=provider,
        )

    # Inject the mocks as attributes for easy access in tests
    agent._test_domain_config = mock_domain_config
    agent._test_living_docs = mock_living_docs
    agent._test_github = mock_github
    agent._test_deployer = mock_deployer
    agent._test_branch_manager = mock_branch_manager

    return agent


# An async generator helper used by coding session tests
async def aiter(items):
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# Tests: AgentResult dataclass
# ---------------------------------------------------------------------------


class TestAgentResult:
    def test_basic_construction(self):
        from forge_harness.agent import AgentResult
        from forge_harness.posthog_tracker import SessionMetrics

        metrics = SessionMetrics()
        result = AgentResult(
            success=True,
            session_id="20240101_120000",
            issues_completed=[1, 2],
            issues_failed=[],
            metrics=metrics,
            deployment_result=None,
        )
        assert result.success is True
        assert result.session_id == "20240101_120000"
        assert result.issues_completed == [1, 2]
        assert result.issues_failed == []
        assert result.deployment_result is None
        assert result.errors == []

    def test_errors_default_empty_list(self):
        from forge_harness.agent import AgentResult
        from forge_harness.posthog_tracker import SessionMetrics

        result = AgentResult(
            success=False,
            session_id="x",
            issues_completed=[],
            issues_failed=[3],
            metrics=SessionMetrics(),
            deployment_result=None,
        )
        assert result.errors == []

    def test_errors_populated(self):
        from forge_harness.agent import AgentResult
        from forge_harness.posthog_tracker import SessionMetrics

        result = AgentResult(
            success=False,
            session_id="x",
            issues_completed=[],
            issues_failed=[3],
            metrics=SessionMetrics(),
            deployment_result={"backend": {"success": False}},
            errors=["Something went wrong"],
        )
        assert result.errors == ["Something went wrong"]
        assert result.deployment_result is not None


# ---------------------------------------------------------------------------
# Tests: ForgeAgent initialisation
# ---------------------------------------------------------------------------


class TestForgeAgentInit:
    def test_attributes_set_correctly(self):
        agent = _make_agent(
            domain="my-domain",
            project="my-project",
            model="claude-opus-4",
            max_iterations=5,
            deploy=True,
            skip_quality_gates=True,
            verbose=True,
        )
        assert agent.domain == "my-domain"
        assert agent.project == "my-project"
        assert agent.forge_root == FORGE_ROOT
        assert agent.project_dir == FORGE_ROOT / "my-domain" / "my-project"
        assert agent.github_repo == GITHUB_REPO
        assert agent.model == "claude-opus-4"
        assert agent.max_iterations == 5
        assert agent.deploy is True
        assert agent.skip_quality_gates is True
        assert agent.verbose is True

    def test_default_provider_is_claude_code_provider(self):
        """When no provider is given, ClaudeCodeProvider is used."""
        with (
            patch("forge_harness.agent.get_domain_config"),
            patch("forge_harness.agent.LivingDocs"),
            patch("forge_harness.agent.GitHubClient"),
            patch("forge_harness.agent.ForgeDeployer"),
            patch("forge_harness.agent.BranchManager"),
            patch("forge_harness.agent.create_forge_security_context"),
            patch("forge_harness.agent.ContextMonitor"),
            patch("forge_harness.agent.MemoryFlushManager"),
            patch("forge_harness.agent.SessionMetrics"),
            patch("forge_harness.agent.ClaudeCodeProvider") as mock_provider_cls,
        ):
            from forge_harness.agent import ForgeAgent

            agent = ForgeAgent(
                domain=DOMAIN,
                project=PROJECT,
                forge_root=FORGE_ROOT,
                github_repo=GITHUB_REPO,
            )
            mock_provider_cls.assert_called_once()
            assert agent.provider is mock_provider_cls.return_value

    def test_circuit_breaker_initial_state(self):
        agent = _make_agent()
        assert agent.issue_attempts == {}
        assert agent.max_attempts_per_issue == 3
        assert agent.retry_context == {}

    def test_issues_lists_initially_empty(self):
        agent = _make_agent()
        assert agent.issues_completed == []
        assert agent.issues_failed == []

    def test_session_id_format(self):
        agent = _make_agent()
        # Should match YYYYMMDD_HHMMSS
        import re
        assert re.match(r"\d{8}_\d{6}", agent.session_id)


# ---------------------------------------------------------------------------
# Tests: _log
# ---------------------------------------------------------------------------


class TestForgeAgentLog:
    def test_no_output_when_not_verbose(self, capsys):
        agent = _make_agent(verbose=False)
        agent._log("should not appear")
        captured = capsys.readouterr()
        assert "should not appear" not in captured.out

    def test_output_when_verbose(self, capsys):
        agent = _make_agent(verbose=True)
        agent._log("hello world")
        captured = capsys.readouterr()
        assert "hello world" in captured.out

    def test_timestamp_in_output(self, capsys):
        agent = _make_agent(verbose=True)
        agent._log("timestamped")
        captured = capsys.readouterr()
        # Should have HH:MM:SS prefix
        import re
        assert re.search(r"\d{2}:\d{2}:\d{2}", captured.out)


# ---------------------------------------------------------------------------
# Tests: _compute_error_signature
# ---------------------------------------------------------------------------


class TestComputeErrorSignature:
    def test_returns_eight_char_hex(self):
        agent = _make_agent()
        sig = agent._compute_error_signature(["error one", "error two"])
        assert len(sig) == 8
        assert all(c in "0123456789abcdef" for c in sig)

    def test_same_errors_same_signature(self):
        agent = _make_agent()
        errors = ["ModuleNotFoundError: foo", "TypeError: bar"]
        sig1 = agent._compute_error_signature(errors)
        sig2 = agent._compute_error_signature(errors)
        assert sig1 == sig2

    def test_different_errors_different_signature(self):
        agent = _make_agent()
        sig1 = agent._compute_error_signature(["error A"])
        sig2 = agent._compute_error_signature(["error B"])
        assert sig1 != sig2

    def test_order_insensitive_because_sorted(self):
        agent = _make_agent()
        sig1 = agent._compute_error_signature(["b error", "a error"])
        sig2 = agent._compute_error_signature(["a error", "b error"])
        assert sig1 == sig2

    def test_empty_errors(self):
        agent = _make_agent()
        sig = agent._compute_error_signature([])
        assert len(sig) == 8

    def test_truncates_to_five_errors(self):
        agent = _make_agent()
        many = [f"error {i}" for i in range(20)]
        few = many[:5]
        sig_many = agent._compute_error_signature(many)
        sig_few = agent._compute_error_signature(few)
        # The signature is built from the first 5 sorted errors
        assert sig_many == sig_few


# ---------------------------------------------------------------------------
# Tests: _should_skip_issue
# ---------------------------------------------------------------------------


class TestShouldSkipIssue:
    def test_no_attempts_returns_false(self):
        agent = _make_agent()
        assert agent._should_skip_issue(42, "abc12345") is False

    def test_below_max_attempts_returns_false(self):
        agent = _make_agent()
        agent.issue_attempts[42] = (2, "abc12345")
        assert agent._should_skip_issue(42, "abc12345") is False

    def test_at_max_attempts_same_sig_returns_true(self):
        agent = _make_agent()
        sig = "abc12345"
        agent.issue_attempts[42] = (3, sig)
        assert agent._should_skip_issue(42, sig) is True

    def test_at_max_attempts_different_sig_returns_false(self):
        agent = _make_agent()
        agent.issue_attempts[42] = (3, "oldsig00")
        # Different error signature means a different root cause — do NOT skip
        assert agent._should_skip_issue(42, "newsig00") is False

    def test_exceeds_max_attempts_same_sig_returns_true(self):
        agent = _make_agent()
        sig = "deadbeef"
        agent.issue_attempts[99] = (10, sig)
        assert agent._should_skip_issue(99, sig) is True


# ---------------------------------------------------------------------------
# Tests: _record_attempt
# ---------------------------------------------------------------------------


class TestRecordAttempt:
    def test_first_attempt_records_one(self):
        agent = _make_agent()
        count = agent._record_attempt(1, "sig1")
        assert count == 1
        assert agent.issue_attempts[1] == (1, "sig1")

    def test_same_sig_increments(self):
        agent = _make_agent()
        agent._record_attempt(1, "sig1")
        count = agent._record_attempt(1, "sig1")
        assert count == 2
        assert agent.issue_attempts[1] == (2, "sig1")

    def test_different_sig_resets_to_one(self):
        agent = _make_agent()
        agent._record_attempt(1, "old_sig")
        agent._record_attempt(1, "old_sig")
        count = agent._record_attempt(1, "new_sig")
        assert count == 1
        assert agent.issue_attempts[1] == (1, "new_sig")

    def test_multiple_issues_tracked_independently(self):
        agent = _make_agent()
        agent._record_attempt(1, "sigA")
        agent._record_attempt(1, "sigA")
        agent._record_attempt(2, "sigB")
        assert agent.issue_attempts[1][0] == 2
        assert agent.issue_attempts[2][0] == 1


# ---------------------------------------------------------------------------
# Tests: _validate_human_gates
# ---------------------------------------------------------------------------


class TestValidateHumanGates:
    def test_no_gates_configured_passes_all(self):
        agent = _make_agent(human_gates=[])
        can_proceed, reason = agent._validate_human_gates(
            {"title": "Add JWT auth", "body": ""}
        )
        assert can_proceed is True
        assert reason is None

    def test_security_gate_triggered_by_keyword(self):
        agent = _make_agent(human_gates=["security"])
        can_proceed, reason = agent._validate_human_gates(
            {"title": "Implement JWT authentication", "body": ""}
        )
        assert can_proceed is False
        assert "security" in reason.lower()

    def test_compliance_gate_triggered_by_coppa(self):
        agent = _make_agent(human_gates=["compliance"])
        can_proceed, reason = agent._validate_human_gates(
            {"title": "Add COPPA consent flow", "body": ""}
        )
        assert can_proceed is False
        assert "compliance" in reason.lower()

    def test_payments_gate_triggered_by_stripe(self):
        agent = _make_agent(human_gates=["payments"])
        can_proceed, reason = agent._validate_human_gates(
            {"title": "Integrate Stripe checkout", "body": ""}
        )
        assert can_proceed is False

    def test_architecture_gate_triggered_by_migration(self):
        agent = _make_agent(human_gates=["architecture"])
        can_proceed, reason = agent._validate_human_gates(
            {"title": "Database migration for user table", "body": ""}
        )
        assert can_proceed is False

    def test_gate_not_configured_passes_even_with_keyword(self):
        """If gate type is not in domain_config.human_gates it should not trigger."""
        agent = _make_agent(human_gates=["compliance"])
        # "jwt" is a security keyword but security gate is NOT configured
        can_proceed, reason = agent._validate_human_gates(
            {"title": "Update JWT expiry", "body": ""}
        )
        assert can_proceed is True

    def test_keyword_in_body_triggers_gate(self):
        agent = _make_agent(human_gates=["security"])
        can_proceed, _ = agent._validate_human_gates(
            {"title": "Minor fix", "body": "Update the session handling logic"}
        )
        assert can_proceed is False

    def test_keyword_in_body_case_insensitive(self):
        agent = _make_agent(human_gates=["security"])
        can_proceed, _ = agent._validate_human_gates(
            {"title": "Update", "body": "OAUTH integration update"}
        )
        assert can_proceed is False

    def test_unrelated_issue_passes_gate(self):
        agent = _make_agent(human_gates=["security", "compliance"])
        can_proceed, reason = agent._validate_human_gates(
            {"title": "Fix typo in README", "body": "Just a small documentation fix."}
        )
        assert can_proceed is True
        assert reason is None


# ---------------------------------------------------------------------------
# Tests: _load_context
# ---------------------------------------------------------------------------


class TestLoadContext:
    def test_returns_dict_with_expected_keys(self):
        agent = _make_agent()
        docs_ctx = MagicMock()
        docs_ctx.current_sprint = "Sprint 10"
        docs_ctx.blockers = ["blocker 1"]
        docs_ctx.priorities = ["p1"]
        docs_ctx.recent_milestones = ["m1"]
        agent.living_docs.consult.return_value = docs_ctx

        with patch(
            "forge_harness.agent.load_claude_md_hierarchy",
            return_value={"merged": {"tech_stack": {"backend": "FastAPI"}}},
        ):
            context = agent._load_context()

        assert context["domain"] == DOMAIN
        assert context["project"] == PROJECT
        assert context["current_sprint"] == "Sprint 10"
        assert context["blockers"] == ["blocker 1"]
        assert context["priorities"] == ["p1"]
        assert context["recent_milestones"] == ["m1"]
        assert "domain_config" in context

    def test_domain_config_keys_present(self):
        agent = _make_agent()
        docs_ctx = MagicMock()
        docs_ctx.current_sprint = ""
        docs_ctx.blockers = []
        docs_ctx.priorities = []
        docs_ctx.recent_milestones = []
        agent.living_docs.consult.return_value = docs_ctx

        with patch(
            "forge_harness.agent.load_claude_md_hierarchy",
            return_value={"merged": {}},
        ):
            context = agent._load_context()

        dc = context["domain_config"]
        assert "compliance" in dc
        assert "human_gates" in dc
        assert "frontend_tier" in dc
        assert "localization" in dc
        assert "special_rules" in dc


# ---------------------------------------------------------------------------
# Tests: _get_next_issue
# ---------------------------------------------------------------------------


class TestGetNextIssue:
    def test_returns_in_progress_issue_first(self):
        agent = _make_agent()
        in_progress_issue = {"number": 5, "title": "WIP"}
        agent.github.list_issues.side_effect = [
            [in_progress_issue],  # in-progress query
        ]
        result = agent._get_next_issue()
        assert result["number"] == 5

    def test_returns_priority_critical_if_no_in_progress(self):
        agent = _make_agent()
        crit_issue = {"number": 10, "title": "Critical"}
        agent.github.list_issues.side_effect = [
            [],               # in-progress → empty
            [crit_issue],     # priority:critical
        ]
        result = agent._get_next_issue()
        assert result["number"] == 10

    def test_returns_none_when_no_issues(self):
        agent = _make_agent()
        agent.github.list_issues.return_value = []
        result = agent._get_next_issue()
        assert result is None

    def test_falls_back_to_any_open_issue(self):
        agent = _make_agent()
        any_issue = {"number": 99, "title": "Low priority"}
        # in-progress, critical, high, medium, low all empty → fallback
        agent.github.list_issues.side_effect = [
            [], [], [], [], [],  # 5 priority queries
            [any_issue],         # fallback: any open
        ]
        result = agent._get_next_issue()
        assert result["number"] == 99

    def test_priority_ordering_respects_high_before_medium(self):
        agent = _make_agent()
        high_issue = {"number": 7, "title": "High prio"}
        agent.github.list_issues.side_effect = [
            [],           # in-progress
            [],           # critical
            [high_issue], # high
        ]
        result = agent._get_next_issue()
        assert result["number"] == 7


# ---------------------------------------------------------------------------
# Tests: _get_default_prompt
# ---------------------------------------------------------------------------


class TestGetDefaultPrompt:
    def test_returns_string_with_placeholders(self):
        agent = _make_agent()
        prompt = agent._get_default_prompt()
        assert "{domain}" in prompt
        assert "{project}" in prompt
        assert "{issue_number}" in prompt
        assert "{compliance_section}" in prompt

    def test_return_type_is_str(self):
        agent = _make_agent()
        assert isinstance(agent._get_default_prompt(), str)


# ---------------------------------------------------------------------------
# Tests: _build_coding_prompt
# ---------------------------------------------------------------------------


class TestBuildCodingPrompt:
    def _make_issue(self, number=1, title="Test Issue", body="", labels=None):
        return {
            "number": number,
            "title": title,
            "body": body,
            "labels": labels or [],
        }

    def test_prompt_contains_issue_info(self):
        agent = _make_agent()
        agent.github.get_issue.return_value = {"body": "Issue body text"}

        context = {
            "current_sprint": "Sprint 1",
            "priorities": ["Finish auth"],
            "blockers": [],
            "recent_milestones": ["Released v1"],
        }

        with patch("pathlib.Path.exists", return_value=False):
            prompt = agent._build_coding_prompt(context, self._make_issue(number=42, title="My Feature"))

        assert "42" in prompt
        assert "My Feature" in prompt

    def test_prompt_includes_retry_context_when_present(self):
        agent = _make_agent()
        agent.github.get_issue.return_value = {"body": ""}
        agent.retry_context[5] = (["ImportError: no module"], ["Install missing deps"])

        context = {"current_sprint": "", "priorities": [], "blockers": [], "recent_milestones": []}

        with patch("pathlib.Path.exists", return_value=False):
            prompt = agent._build_coding_prompt(context, self._make_issue(number=5))

        assert "PREVIOUS SESSION FAILED" in prompt
        assert "ImportError" in prompt
        assert "Install missing deps" in prompt

    def test_prompt_uses_template_when_file_exists(self):
        agent = _make_agent()
        agent.github.get_issue.return_value = {"body": ""}
        agent.domain_config.human_gates = []
        agent.domain_config.compliance = []
        agent.domain_config.localization = None
        agent.domain_config.special_rules = {}
        agent.domain_config.frontend_tier = "React"

        context = {
            "current_sprint": "Sprint 1",
            "priorities": [],
            "blockers": [],
            "recent_milestones": [],
        }

        fake_template = (
            "Domain: {domain}\nProject: {project}\nIssue: {issue_number}\n"
            "Sprint: {current_sprint}\nTier: {frontend_tier}\n"
            "Compliance: {compliance_section}\nGates: {human_gates}\n"
            "Priorities: {priorities}\nProgress: {recent_progress}\n"
            "Blockers: {blockers}\nTitle: {issue_title}\nDate: {date}\n"
            "Session: {session_id}\n"
        )
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=fake_template),
        ):
            prompt = agent._build_coding_prompt(context, self._make_issue(number=3, title="T"))

        assert "test-domain" in prompt
        assert "test-project" in prompt

    def test_prompt_includes_labels(self):
        agent = _make_agent()
        agent.github.get_issue.return_value = {"body": ""}
        context = {"current_sprint": "", "priorities": [], "blockers": [], "recent_milestones": []}

        issue = self._make_issue(
            number=10,
            labels=[{"name": "priority:high"}, {"name": "domain:foo"}],
        )
        with patch("pathlib.Path.exists", return_value=False):
            prompt = agent._build_coding_prompt(context, issue)

        assert "priority:high" in prompt

    def test_prompt_includes_compliance_when_set(self):
        agent = _make_agent()
        agent.domain_config.compliance = ["COPPA", "GDPR"]
        agent.domain_config.localization = None
        agent.domain_config.special_rules = {}
        agent.github.get_issue.return_value = {"body": ""}
        context = {"current_sprint": "", "priorities": [], "blockers": [], "recent_milestones": []}

        with patch("pathlib.Path.exists", return_value=False):
            prompt = agent._build_coding_prompt(context, self._make_issue())

        assert "COPPA" in prompt

    def test_prompt_includes_localization_and_special_rules(self):
        agent = _make_agent()
        agent.domain_config.compliance = ["HIPAA"]
        agent.domain_config.localization = "en-US, fr-FR"
        agent.domain_config.special_rules = {"Age Verification": "Required for all users"}
        agent.domain_config.frontend_tier = "React"
        agent.github.get_issue.return_value = {"body": ""}
        context = {"current_sprint": "", "priorities": [], "blockers": [], "recent_milestones": []}

        with patch("pathlib.Path.exists", return_value=False):
            prompt = agent._build_coding_prompt(context, self._make_issue())

        assert "en-US, fr-FR" in prompt
        assert "Age Verification" in prompt

    def test_prompt_includes_checkpoint_section(self):
        agent = _make_agent()
        agent.github.get_issue.return_value = {"body": ""}
        context = {"current_sprint": "", "priorities": [], "blockers": [], "recent_milestones": []}
        issue = self._make_issue(number=1, body="Some text **Checkpoint:** All tests pass ---")

        with patch("pathlib.Path.exists", return_value=False):
            # Just verify build works without error even with checkpoint markers
            prompt = agent._build_coding_prompt(context, issue)

        assert "1" in prompt


# ---------------------------------------------------------------------------
# Tests: _run_coding_session
# ---------------------------------------------------------------------------


class TestRunCodingSession:
    @pytest.mark.asyncio
    async def test_success_when_no_failure_indicators(self):
        agent = _make_agent()
        msg = MagicMock()
        msg.content = "Implementation complete. All tests passed."

        async def fake_session(prompt, options):
            yield msg

        agent.provider.run_coding_session = fake_session

        success, summary = await agent._run_coding_session(
            "do stuff", {"number": 1}
        )
        assert success is True

    @pytest.mark.asyncio
    async def test_failure_when_explicit_indicator_in_last_section(self):
        agent = _make_agent()

        # Build a long enough response so the failure is in the last 1000 chars
        long_prefix = "x" * 2000
        failure_text = "i cannot complete this task"

        msg1 = MagicMock()
        msg1.content = long_prefix
        msg2 = MagicMock()
        msg2.content = failure_text

        async def fake_session(prompt, options):
            yield msg1
            yield msg2

        agent.provider.run_coding_session = fake_session
        success, _ = await agent._run_coding_session("do stuff", {"number": 1})
        assert success is False

    @pytest.mark.asyncio
    async def test_returns_truncated_summary(self):
        agent = _make_agent()
        msg = MagicMock()
        msg.content = "A" * 5000  # Large content

        async def fake_session(prompt, options):
            yield msg

        agent.provider.run_coding_session = fake_session
        _, summary = await agent._run_coding_session("do stuff", {"number": 1})
        # Should be truncated to last 2000 chars
        assert len(summary) <= 2001  # +1 for trailing \n

    @pytest.mark.asyncio
    async def test_exception_returns_false_with_error_message(self):
        agent = _make_agent()

        async def failing_session(prompt, options):
            raise RuntimeError("SDK exploded")
            yield  # make it a generator

        agent.provider.run_coding_session = failing_session
        success, error_msg = await agent._run_coding_session("do stuff", {"number": 1})
        assert success is False
        assert "SDK exploded" in error_msg

    @pytest.mark.asyncio
    async def test_messages_without_content_attr_are_skipped(self):
        agent = _make_agent()
        msg_no_content = MagicMock(spec=[])  # no 'content' attribute

        async def fake_session(prompt, options):
            yield msg_no_content

        agent.provider.run_coding_session = fake_session
        success, summary = await agent._run_coding_session("do stuff", {"number": 1})
        assert success is True
        assert summary == ""

    @pytest.mark.asyncio
    async def test_claude_code_provider_hooks_setup(self):
        """Hooks are added to options when provider is ClaudeCodeProvider."""
        from forge_harness.llm_provider import ClaudeCodeProvider

        # Create a real-ish provider mock that reports as ClaudeCodeProvider
        provider = MagicMock(spec=ClaudeCodeProvider)

        async def fake_session(prompt, options):
            yield MagicMock(content="done")

        provider.run_coding_session = fake_session

        agent = _make_agent(provider=provider)

        # Patch the HookMatcher import inside the method
        with patch.dict(
            "sys.modules",
            {"claude_agent_sdk": MagicMock(), "claude_agent_sdk.types": MagicMock()},
        ):
            success, _ = await agent._run_coding_session("do stuff", {"number": 1})

        assert success is True

    @pytest.mark.asyncio
    async def test_claude_code_provider_import_error_skips_hooks(self):
        """When claude_agent_sdk.types raises ImportError, hooks setup is skipped gracefully."""
        from forge_harness.llm_provider import ClaudeCodeProvider

        provider = MagicMock(spec=ClaudeCodeProvider)

        async def fake_session(prompt, options):
            yield MagicMock(content="done")

        provider.run_coding_session = fake_session
        agent = _make_agent(provider=provider)

        # Simulate ImportError inside the hook setup block
        import sys
        real_modules = sys.modules.copy()
        # Remove claude_agent_sdk so the import inside raises ImportError
        sys.modules.pop("claude_agent_sdk", None)
        sys.modules.pop("claude_agent_sdk.types", None)

        try:
            success, _ = await agent._run_coding_session("do stuff", {"number": 1})
        finally:
            # Restore module state
            for key in list(sys.modules.keys()):
                if key.startswith("claude_agent_sdk"):
                    sys.modules.pop(key, None)

        assert success is True


# ---------------------------------------------------------------------------
# Tests: _run_preflight
# ---------------------------------------------------------------------------


class TestRunPreflight:
    def test_calls_preflight_checker(self):
        agent = _make_agent()
        mock_result = MagicMock()
        mock_result.passed = True
        mock_result.fixes_applied = []
        mock_result.issues = []

        with patch("forge_harness.agent.PreflightChecker") as mock_cls:
            mock_cls.return_value.run.return_value = mock_result
            result = agent._run_preflight(auto_fix=True)

        mock_cls.assert_called_once_with(agent.project_dir)
        mock_cls.return_value.run.assert_called_once_with(auto_fix=True)
        assert result is mock_result

    def test_tracks_preflight_when_tracker_set(self):
        tracker = MagicMock()
        agent = _make_agent(tracker=tracker)
        mock_result = MagicMock()
        mock_result.passed = False
        mock_result.fixes_applied = ["fix 1"]
        mock_result.issues = ["issue 1"]

        with patch("forge_harness.agent.PreflightChecker") as mock_cls:
            mock_cls.return_value.run.return_value = mock_result
            agent._run_preflight()

        tracker.track_event.assert_called_once()
        call_args = tracker.track_event.call_args[0]
        assert call_args[0] == "preflight_completed"

    def test_no_tracking_when_no_fixes_or_issues(self):
        tracker = MagicMock()
        agent = _make_agent(tracker=tracker)
        mock_result = MagicMock()
        mock_result.passed = True
        mock_result.fixes_applied = []
        mock_result.issues = []

        with patch("forge_harness.agent.PreflightChecker") as mock_cls:
            mock_cls.return_value.run.return_value = mock_result
            agent._run_preflight()

        tracker.track_event.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _run_verification
# ---------------------------------------------------------------------------


class TestRunVerification:
    def test_calls_verifier(self):
        agent = _make_agent()
        mock_result = MagicMock()
        mock_result.passed = True
        mock_result.tests_passed = 10
        mock_result.tests_failed = 0
        mock_result.coverage_backend = 85.0
        mock_result.coverage_frontend = 70.0
        mock_result.lint_errors = 0

        with patch("forge_harness.agent.Verifier") as mock_cls:
            mock_cls.return_value.verify.return_value = mock_result
            result = agent._run_verification(checkpoint="all tests pass")

        mock_cls.assert_called_once_with(
            project_dir=agent.project_dir,
            domain_config=agent.domain_config,
            skip_quality_gates=agent.skip_quality_gates,
        )
        mock_cls.return_value.verify.assert_called_once_with(checkpoint="all tests pass")
        assert result is mock_result

    def test_default_checkpoint_is_empty_string(self):
        agent = _make_agent()
        mock_result = MagicMock()
        mock_result.passed = True
        mock_result.tests_passed = 0
        mock_result.tests_failed = 0
        mock_result.coverage_backend = 0.0
        mock_result.coverage_frontend = 0.0
        mock_result.lint_errors = 0

        with patch("forge_harness.agent.Verifier") as mock_cls:
            mock_cls.return_value.verify.return_value = mock_result
            agent._run_verification()

        mock_cls.return_value.verify.assert_called_once_with(checkpoint="")


# ---------------------------------------------------------------------------
# Tests: _create_security_hook
# ---------------------------------------------------------------------------


class TestCreateSecurityHook:
    def test_returns_callable(self):
        agent = _make_agent()
        hook = agent._create_security_hook()
        assert callable(hook)

    @pytest.mark.asyncio
    async def test_hook_calls_bash_security_hook(self):
        agent = _make_agent()
        hook = agent._create_security_hook()

        with patch("forge_harness.agent.bash_security_hook", new_callable=AsyncMock) as mock_bsh:
            mock_bsh.return_value = {"allowed": True}
            result = await hook({"command": "ls"}, tool_use_id="tid")

        mock_bsh.assert_called_once_with(
            {"command": "ls"}, "tid", agent.security_context
        )
        assert result == {"allowed": True}


# ---------------------------------------------------------------------------
# Tests: ForgeAgent.run — integration of the full session loop
# ---------------------------------------------------------------------------


def _make_preflight_result(passed=True, fixes=None, issues=None):
    r = MagicMock()
    r.passed = passed
    r.fixes_applied = fixes or []
    r.issues = issues or []
    return r


def _make_verification_result(
    passed=True,
    tests_passed=5,
    tests_failed=0,
    coverage_backend=80.0,
    coverage_frontend=75.0,
    lint_errors=0,
    error_message=None,
    error_details=None,
    fix_suggestions=None,
    summary="Verification: PASSED",
):
    r = MagicMock()
    r.passed = passed
    r.tests_passed = tests_passed
    r.tests_failed = tests_failed
    r.coverage_backend = coverage_backend
    r.coverage_frontend = coverage_frontend
    r.lint_errors = lint_errors
    r.error_message = error_message
    r.error_details = error_details or []
    r.fix_suggestions = fix_suggestions or []
    r.summary = summary
    return r


class TestForgeAgentRun:
    def _make_issue(self, number=1, title="Test", body="", labels=None):
        return {"number": number, "title": title, "body": body, "labels": labels or []}

    @pytest.mark.asyncio
    async def test_no_issues_returns_success(self):
        agent = _make_agent()
        agent.github = agent._test_github
        agent.github.list_issues.return_value = []
        agent.living_docs = agent._test_living_docs

        docs_ctx = MagicMock()
        docs_ctx.current_sprint = "Sprint 1"
        docs_ctx.blockers = []
        docs_ctx.priorities = []
        docs_ctx.recent_milestones = []
        agent.living_docs.consult.return_value = docs_ctx
        agent.living_docs.sync.return_value = {}

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            result = await agent.run()

        assert result.success is True
        assert result.issues_completed == []
        assert result.issues_failed == []

    @pytest.mark.asyncio
    async def test_tracker_session_started_called(self):
        tracker = MagicMock()
        agent = _make_agent(tracker=tracker)
        agent.github.list_issues.return_value = []
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            await agent.run()

        tracker.session_started.assert_called_once()

    @pytest.mark.asyncio
    async def test_successful_issue_closes_and_updates_docs(self):
        agent = _make_agent()
        issue = self._make_issue(number=7, title="Add login")
        agent.github.list_issues.side_effect = [[issue], []]  # first call returns issue, then empty
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="S1", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = False

        async def fake_session(prompt, options):
            yield MagicMock(content="Implementation done.")

        agent.provider.run_coding_session = fake_session

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.Verifier") as mock_verifier,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            mock_verifier.return_value.verify.return_value = _make_verification_result()
            result = await agent.run()

        assert 7 in result.issues_completed
        agent.github.close_issue.assert_called_once_with(7)
        agent.living_docs.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_verification_failure_records_retry_context(self):
        agent = _make_agent(max_iterations=1)
        issue = self._make_issue(number=3, title="Bugfix")
        agent.github.list_issues.return_value = [issue]
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = False

        async def fake_session(prompt, options):
            yield MagicMock(content="done")

        agent.provider.run_coding_session = fake_session

        ver_result = _make_verification_result(
            passed=False,
            error_message="Tests failed",
            error_details=["test_foo: AssertionError"],
            fix_suggestions=["Check assertion in test_foo"],
        )

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.Verifier") as mock_verifier,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            mock_verifier.return_value.verify.return_value = ver_result
            result = await agent.run()

        assert 3 in result.issues_failed
        assert 3 in agent.retry_context
        errors, suggestions = agent.retry_context[3]
        assert "test_foo: AssertionError" in errors

    @pytest.mark.asyncio
    async def test_human_gate_triggers_labels_and_skips(self):
        agent = _make_agent(human_gates=["security"])
        issue = self._make_issue(number=9, title="Add JWT auth", body="")
        agent.github.list_issues.side_effect = [[issue], []]
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            result = await agent.run()

        agent.github.update_issue.assert_called_with(9, add_labels=["needs-human-review"])
        # Issue was skipped, not completed or failed through normal path
        assert 9 not in result.issues_completed

    @pytest.mark.asyncio
    async def test_circuit_breaker_skips_issue_after_max_attempts(self):
        agent = _make_agent()
        issue = self._make_issue(number=11, title="Stuck issue")
        # Simulate already at max attempts
        agent.issue_attempts[11] = (3, "somesig")
        agent.github.list_issues.side_effect = [[issue], []]
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            result = await agent.run()

        # Should have been added to blocked
        agent.github.update_issue.assert_called_with(
            11,
            remove_labels=["status:in-progress"],
            add_labels=["status:blocked", "needs-human-review"],
        )
        assert 11 in result.issues_failed

    @pytest.mark.asyncio
    async def test_coding_session_failure_marks_issue_blocked(self):
        agent = _make_agent(max_iterations=1)
        issue = self._make_issue(number=5, title="Broken")
        agent.github.list_issues.return_value = [issue]
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = False

        async def failing_session(prompt, options):
            yield MagicMock(content="i cannot complete this task")

        agent.provider.run_coding_session = failing_session

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            result = await agent.run()

        assert 5 in result.issues_failed
        assert result.success is False

    @pytest.mark.asyncio
    async def test_max_iterations_respected(self):
        agent = _make_agent(max_iterations=2)
        issue = self._make_issue()
        # Always return same issue to keep loop going
        agent.github.list_issues.return_value = [issue]
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = False

        call_count = 0

        async def fake_session(prompt, options):
            nonlocal call_count
            call_count += 1
            yield MagicMock(content="done")

        agent.provider.run_coding_session = fake_session

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.Verifier") as mock_verifier,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            mock_verifier.return_value.verify.return_value = _make_verification_result()
            await agent.run()

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_deploy_called_when_flag_set_and_issues_completed(self):
        agent = _make_agent(deploy=True, max_iterations=1)
        issue = self._make_issue(number=2)
        # Provide enough side_effect entries for all list_issues calls:
        # _get_next_issue makes up to 6 calls: in-progress + 4 priorities + fallback
        agent.github.list_issues.side_effect = [
            [issue],  # in-progress — returns our issue on iteration 1
            [],       # in-progress on iteration 2 (after max_iterations stops loop)
        ]
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = False
        agent.deployer.full_deploy.return_value = {
            "backend": {"success": True},
            "frontend": {"success": True},
        }

        async def fake_session(prompt, options):
            yield MagicMock(content="done")

        agent.provider.run_coding_session = fake_session

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.Verifier") as mock_verifier,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            mock_verifier.return_value.verify.return_value = _make_verification_result()
            result = await agent.run()

        agent.deployer.full_deploy.assert_called_once_with(skip_quality_gates=False)
        assert result.deployment_result is not None

    @pytest.mark.asyncio
    async def test_deploy_not_called_when_no_issues_completed(self):
        agent = _make_agent(deploy=True)
        agent.github.list_issues.return_value = []
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            result = await agent.run()

        agent.deployer.full_deploy.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_in_run_returns_failure_result(self):
        agent = _make_agent()
        agent.living_docs.consult.side_effect = RuntimeError("Context load failed")

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            result = await agent.run()

        assert result.success is False
        assert any("Context load failed" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_tracker_error_occurred_on_exception(self):
        tracker = MagicMock()
        agent = _make_agent(tracker=tracker)
        agent.living_docs.consult.side_effect = ValueError("boom")

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            await agent.run()

        tracker.error_occurred.assert_called_once()

    @pytest.mark.asyncio
    async def test_feature_branch_created_on_protected_branch(self):
        agent = _make_agent()
        issue = self._make_issue(number=20, title="New feature")
        agent.github.list_issues.side_effect = [[issue], []]
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = True
        agent.branch_manager.create_feature_branch.return_value = "feat/issue-20-new-feature"

        async def fake_session(prompt, options):
            yield MagicMock(content="done")

        agent.provider.run_coding_session = fake_session

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.Verifier") as mock_verifier,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            mock_verifier.return_value.verify.return_value = _make_verification_result()
            await agent.run()

        agent.branch_manager.create_feature_branch.assert_called()

    @pytest.mark.asyncio
    async def test_branch_creation_fallback_to_local_on_remote_failure(self):
        agent = _make_agent()
        issue = self._make_issue(number=21, title="Feature")
        agent.github.list_issues.side_effect = [[issue], []]
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = True

        # First call (from_remote=True) raises, second (from_remote=False) succeeds
        agent.branch_manager.create_feature_branch.side_effect = [
            Exception("remote failed"),
            "feat/issue-21-feature",
        ]

        async def fake_session(prompt, options):
            yield MagicMock(content="done")

        agent.provider.run_coding_session = fake_session

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.Verifier") as mock_verifier,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            mock_verifier.return_value.verify.return_value = _make_verification_result()
            await agent.run()

        assert agent.branch_manager.create_feature_branch.call_count == 2

    @pytest.mark.asyncio
    async def test_living_docs_sync_called_at_end(self):
        agent = _make_agent()
        agent.github.list_issues.return_value = []
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            await agent.run()

        agent.living_docs.sync.assert_called_once_with(DOMAIN, PROJECT)

    @pytest.mark.asyncio
    async def test_retry_context_cleared_on_success(self):
        agent = _make_agent()
        issue = self._make_issue(number=4)
        # Pre-populate retry context to simulate a previous failure
        agent.retry_context[4] = (["old error"], ["old fix"])

        agent.github.list_issues.side_effect = [[issue], []]
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = False

        async def fake_session(prompt, options):
            yield MagicMock(content="done")

        agent.provider.run_coding_session = fake_session

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.Verifier") as mock_verifier,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            mock_verifier.return_value.verify.return_value = _make_verification_result()
            await agent.run()

        # Retry context should be cleared
        assert 4 not in agent.retry_context

    @pytest.mark.asyncio
    async def test_preflight_failure_logs_but_continues(self):
        """When preflight fails, errors are appended but run continues."""
        agent = _make_agent()
        agent.github.list_issues.return_value = []
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result(
                passed=False, issues=["missing pyproject.toml"]
            )
            result = await agent.run()

        # Errors include preflight issues but run proceeds and returns success=True
        # (no issues to work on, so success is True even with preflight errors logged)
        assert any("Pre-flight" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_verbose_failure_log_in_coding_session(self):
        """When verbose=True and session fails, summary is logged."""
        agent = _make_agent(verbose=True, max_iterations=1)
        issue = self._make_issue(number=33, title="Broken")
        agent.github.list_issues.return_value = [issue]
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = False

        async def failing_session(prompt, options):
            yield MagicMock(content="i cannot complete this task - some detailed reason")

        agent.provider.run_coding_session = failing_session

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            result = await agent.run()

        assert 33 in result.issues_failed

    @pytest.mark.asyncio
    async def test_tracker_human_gate_triggered_called(self):
        """Tracker.human_gate_triggered is called when gate fires."""
        tracker = MagicMock()
        agent = _make_agent(tracker=tracker, human_gates=["security"])
        issue = self._make_issue(number=50, title="Add JWT auth")
        agent.github.list_issues.side_effect = [[issue], []]
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            await agent.run()

        tracker.human_gate_triggered.assert_called_once()

    @pytest.mark.asyncio
    async def test_tracker_issue_started_with_priority_label(self):
        """Tracker.issue_started receives the correct priority from labels."""
        tracker = MagicMock()
        agent = _make_agent(tracker=tracker, max_iterations=1)
        issue = self._make_issue(
            number=60,
            title="High prio issue",
            labels=[{"name": "priority:high"}],
        )
        agent.github.list_issues.return_value = [issue]
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = False

        async def fake_session(prompt, options):
            yield MagicMock(content="done")

        agent.provider.run_coding_session = fake_session

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.Verifier") as mock_verifier,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            mock_verifier.return_value.verify.return_value = _make_verification_result()
            await agent.run()

        tracker.issue_started.assert_called_once_with(60, "High prio issue", "high")

    @pytest.mark.asyncio
    async def test_tracker_verification_completed_called_on_success(self):
        """Tracker.verification_completed is called after successful verification."""
        tracker = MagicMock()
        agent = _make_agent(tracker=tracker, max_iterations=1)
        issue = self._make_issue(number=70)
        agent.github.list_issues.return_value = [issue]
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = False

        async def fake_session(prompt, options):
            yield MagicMock(content="done")

        agent.provider.run_coding_session = fake_session

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.Verifier") as mock_verifier,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            mock_verifier.return_value.verify.return_value = _make_verification_result()
            await agent.run()

        tracker.verification_completed.assert_called_once()

    @pytest.mark.asyncio
    async def test_checkpoint_extracted_from_issue_body(self):
        """Checkpoint section is extracted from issue body and passed to verifier."""
        agent = _make_agent(max_iterations=1)
        body = "Some description\n**Checkpoint:** All 10 tests pass ---\nMore text"
        issue = self._make_issue(number=80, body=body)
        agent.github.list_issues.return_value = [issue]
        agent.github.get_issue.return_value = {"body": body}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = False

        async def fake_session(prompt, options):
            yield MagicMock(content="done")

        agent.provider.run_coding_session = fake_session

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.Verifier") as mock_verifier,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            mock_verifier.return_value.verify.return_value = _make_verification_result()
            await agent.run()

        # Verifier.verify should have been called with the extracted checkpoint text
        call_kwargs = mock_verifier.return_value.verify.call_args
        assert "All 10 tests pass" in call_kwargs.kwargs.get("checkpoint", "")

    @pytest.mark.asyncio
    async def test_tracker_issue_failed_called_on_verification_failure(self):
        """Tracker.issue_failed is called when verification fails."""
        tracker = MagicMock()
        agent = _make_agent(tracker=tracker, max_iterations=1)
        issue = self._make_issue(number=90)
        agent.github.list_issues.return_value = [issue]
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = False

        async def fake_session(prompt, options):
            yield MagicMock(content="done")

        agent.provider.run_coding_session = fake_session

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.Verifier") as mock_verifier,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            mock_verifier.return_value.verify.return_value = _make_verification_result(
                passed=False, error_message="Coverage too low"
            )
            await agent.run()

        tracker.issue_failed.assert_called_once()
        args = tracker.issue_failed.call_args[0]
        assert args[0] == 90
        assert args[2] == "verification_failed"

    @pytest.mark.asyncio
    async def test_tracker_issue_failed_on_implementation_error(self):
        """Tracker.issue_failed with reason 'implementation_error' when session fails."""
        tracker = MagicMock()
        agent = _make_agent(tracker=tracker, max_iterations=1)
        issue = self._make_issue(number=91)
        agent.github.list_issues.return_value = [issue]
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = False

        async def fail_session(prompt, options):
            yield MagicMock(content="task failed: stopping due to unresolvable error")

        agent.provider.run_coding_session = fail_session

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            await agent.run()

        tracker.issue_failed.assert_called_once()
        args = tracker.issue_failed.call_args[0]
        assert args[2] == "implementation_error"

    @pytest.mark.asyncio
    async def test_issue_not_added_twice_to_failed_on_retry(self):
        """If issue already in issues_failed, don't add it again on subsequent failure."""
        agent = _make_agent(max_iterations=2)
        issue = self._make_issue(number=6)
        agent.issues_failed.append(6)  # already failed once
        agent.github.list_issues.return_value = [issue]
        agent.github.get_issue.return_value = {"body": ""}
        agent.living_docs.consult.return_value = MagicMock(
            current_sprint="", blockers=[], priorities=[], recent_milestones=[]
        )
        agent.living_docs.sync.return_value = {}
        agent.branch_manager.is_on_protected_branch.return_value = False

        async def fake_session(prompt, options):
            yield MagicMock(content="done")

        agent.provider.run_coding_session = fake_session

        ver_result = _make_verification_result(passed=False, error_message="still failing")

        with (
            patch("forge_harness.agent.PreflightChecker") as mock_pf,
            patch("forge_harness.agent.Verifier") as mock_verifier,
            patch("forge_harness.agent.load_claude_md_hierarchy", return_value={"merged": {}}),
            patch("pathlib.Path.exists", return_value=False),
        ):
            mock_pf.return_value.run.return_value = _make_preflight_result()
            mock_verifier.return_value.verify.return_value = ver_result
            await agent.run()

        # Issue 6 should appear only once in issues_failed
        assert agent.issues_failed.count(6) == 1


# ---------------------------------------------------------------------------
# Tests: FeatureOrchestrator
# ---------------------------------------------------------------------------


def _make_orchestrator(
    working_dir: Path = Path("/fake/project"),
    model: str = "claude-sonnet-4-20250514",
    max_iterations: int = 50,
    context: str | None = None,
    provider=None,
):
    with (
        patch("forge_harness.agent.ClaudeCodeProvider") as mock_provider_cls,
    ):
        from forge_harness.agent import FeatureOrchestrator

        if provider is None:
            # Use the class mock's return value automatically
            orch = FeatureOrchestrator(
                working_dir=working_dir,
                model=model,
                max_iterations=max_iterations,
                context=context,
            )
        else:
            orch = FeatureOrchestrator(
                working_dir=working_dir,
                model=model,
                max_iterations=max_iterations,
                context=context,
                provider=provider,
            )
    return orch


def _make_feature(
    id: str = "feat-001",
    name: str = "Feature Name",
    description: str = "Feature description",
    acceptance_criteria: list[str] | None = None,
    files_to_create: list[str] | None = None,
    files_to_modify: list[str] | None = None,
):
    feature = MagicMock()
    feature.id = id
    feature.name = name
    feature.description = description
    feature.acceptance_criteria = acceptance_criteria or ["Criterion 1", "Criterion 2"]
    if files_to_create is not None:
        feature.files_to_create = files_to_create
    else:
        del feature.files_to_create
    if files_to_modify is not None:
        feature.files_to_modify = files_to_modify
    else:
        del feature.files_to_modify
    return feature


class TestFeatureOrchestratorInit:
    def test_attributes_set(self):
        provider = MagicMock()
        from forge_harness.agent import FeatureOrchestrator

        orch = FeatureOrchestrator(
            working_dir=Path("/foo"),
            model="test-model",
            max_iterations=10,
            context="some context",
            provider=provider,
        )
        assert orch.working_dir == Path("/foo")
        assert orch.model == "test-model"
        assert orch.max_iterations == 10
        assert orch.context == "some context"
        assert orch.provider is provider

    def test_default_context_is_empty_string(self):
        provider = MagicMock()
        from forge_harness.agent import FeatureOrchestrator

        orch = FeatureOrchestrator(working_dir=Path("/foo"), provider=provider)
        assert orch.context == ""

    def test_default_provider_is_claude_code(self):
        with patch("forge_harness.agent.ClaudeCodeProvider") as mock_cls:
            from forge_harness.agent import FeatureOrchestrator

            orch = FeatureOrchestrator(working_dir=Path("/foo"))
            mock_cls.assert_called_once()
            assert orch.provider is mock_cls.return_value


class TestFeatureOrchestratorBuildPrompt:
    def test_prompt_contains_feature_info(self):
        provider = MagicMock()
        from forge_harness.agent import FeatureOrchestrator

        orch = FeatureOrchestrator(working_dir=Path("/foo"), provider=provider)
        feature = _make_feature(
            id="F-001",
            name="User Auth",
            description="Implement user authentication",
            acceptance_criteria=["Login works", "Logout works"],
        )
        prompt = orch._build_prompt(feature)

        assert "F-001" in prompt
        assert "User Auth" in prompt
        assert "Implement user authentication" in prompt
        assert "Login works" in prompt
        assert "Logout works" in prompt

    def test_prompt_includes_files_to_create_when_present(self):
        provider = MagicMock()
        from forge_harness.agent import FeatureOrchestrator

        orch = FeatureOrchestrator(working_dir=Path("/foo"), provider=provider)
        feature = _make_feature(files_to_create=["app/auth.py", "app/models.py"])
        prompt = orch._build_prompt(feature)

        assert "app/auth.py" in prompt
        assert "app/models.py" in prompt
        assert "Files to Create" in prompt

    def test_prompt_includes_files_to_modify_when_present(self):
        provider = MagicMock()
        from forge_harness.agent import FeatureOrchestrator

        orch = FeatureOrchestrator(working_dir=Path("/foo"), provider=provider)
        feature = _make_feature(files_to_modify=["app/main.py"])
        prompt = orch._build_prompt(feature)

        assert "app/main.py" in prompt
        assert "Files to Modify" in prompt

    def test_prompt_includes_context(self):
        provider = MagicMock()
        from forge_harness.agent import FeatureOrchestrator

        orch = FeatureOrchestrator(
            working_dir=Path("/foo"), context="Custom context here", provider=provider
        )
        feature = _make_feature()
        prompt = orch._build_prompt(feature)

        assert "Custom context here" in prompt

    def test_prompt_without_files_to_create_omits_section(self):
        provider = MagicMock()
        from forge_harness.agent import FeatureOrchestrator

        orch = FeatureOrchestrator(working_dir=Path("/foo"), provider=provider)
        # feature with no files_to_create attribute
        feature = MagicMock(spec=["id", "name", "description", "acceptance_criteria"])
        feature.id = "F"
        feature.name = "N"
        feature.description = "D"
        feature.acceptance_criteria = ["C1"]
        prompt = orch._build_prompt(feature)

        assert "Files to Create" not in prompt


class TestFeatureOrchestratorImplementFeature:
    @pytest.mark.asyncio
    async def test_success_when_no_failure_indicators(self):
        provider = MagicMock()
        from forge_harness.agent import FeatureOrchestrator

        orch = FeatureOrchestrator(working_dir=Path("/foo"), provider=provider)

        async def fake_session(prompt, options):
            yield MagicMock(content="Feature implemented successfully!")

        provider.run_coding_session = fake_session
        feature = _make_feature()

        success, error = await orch.implement_feature(feature)
        assert success is True
        assert error is None

    @pytest.mark.asyncio
    async def test_failure_when_failure_indicator_present(self):
        provider = MagicMock()
        from forge_harness.agent import FeatureOrchestrator

        orch = FeatureOrchestrator(working_dir=Path("/foo"), provider=provider)

        async def fake_session(prompt, options):
            yield MagicMock(content="unable to implement the requested feature")

        provider.run_coding_session = fake_session
        feature = _make_feature()

        success, error = await orch.implement_feature(feature)
        assert success is False
        assert error is not None

    @pytest.mark.asyncio
    async def test_timeout_returns_failure(self):
        provider = MagicMock()
        from forge_harness.agent import FeatureOrchestrator

        orch = FeatureOrchestrator(working_dir=Path("/foo"), provider=provider)

        async def slow_session(prompt, options):
            import asyncio
            await asyncio.sleep(999)
            yield MagicMock(content="done")

        provider.run_coding_session = slow_session
        feature = _make_feature()

        success, error = await orch.implement_feature(feature, timeout_seconds=0)
        assert success is False
        assert "timed out" in error.lower()

    @pytest.mark.asyncio
    async def test_exception_returns_failure(self):
        provider = MagicMock()
        from forge_harness.agent import FeatureOrchestrator

        orch = FeatureOrchestrator(working_dir=Path("/foo"), provider=provider)

        async def broken_session(prompt, options):
            raise ValueError("Provider exploded")
            yield  # make it a generator

        provider.run_coding_session = broken_session
        feature = _make_feature()

        success, error = await orch.implement_feature(feature)
        assert success is False
        assert "Provider exploded" in error

    @pytest.mark.asyncio
    async def test_options_include_correct_model_and_cwd(self):
        """Verify that ProviderOptions is built with model and cwd from orchestrator."""
        provider = MagicMock()
        from forge_harness.agent import FeatureOrchestrator, ProviderOptions

        orch = FeatureOrchestrator(
            working_dir=Path("/my/project"),
            model="test-model-42",
            provider=provider,
        )

        captured_options = []

        async def capturing_session(prompt, options):
            captured_options.append(options)
            yield MagicMock(content="done")

        provider.run_coding_session = capturing_session
        feature = _make_feature()

        await orch.implement_feature(feature)

        assert len(captured_options) == 1
        opts = captured_options[0]
        assert opts.model == "test-model-42"
        assert opts.cwd == "/my/project"

    @pytest.mark.asyncio
    async def test_empty_summary_does_not_crash(self):
        provider = MagicMock()
        from forge_harness.agent import FeatureOrchestrator

        orch = FeatureOrchestrator(working_dir=Path("/foo"), provider=provider)

        async def empty_session(prompt, options):
            # yields nothing
            return
            yield  # make it a generator

        provider.run_coding_session = empty_session
        feature = _make_feature()

        success, error = await orch.implement_feature(feature)
        assert success is True
        assert error is None
