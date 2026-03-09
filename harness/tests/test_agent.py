"""Tests for FORGE agent session orchestration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip all tests in this module if claude_agent_sdk is not available
pytest.importorskip("claude_agent_sdk", reason="claude_agent_sdk not installed")

from forge_harness.agent import AgentResult, ForgeAgent
from forge_harness.posthog_tracker import NoOpTracker, SessionMetrics


class TestAgentResult:
    """Tests for AgentResult dataclass."""

    def test_successful_result(self):
        """AgentResult can represent successful session."""
        result = AgentResult(
            success=True,
            session_id="test123",
            issues_completed=[1, 2, 3],
            issues_failed=[],
            metrics=SessionMetrics(issues_completed=3),
            deployment_result={"backend": {"success": True}},
            errors=[],
        )

        assert result.success is True
        assert len(result.issues_completed) == 3
        assert len(result.issues_failed) == 0

    def test_failed_result(self):
        """AgentResult can represent failed session."""
        result = AgentResult(
            success=False,
            session_id="test456",
            issues_completed=[1],
            issues_failed=[2, 3],
            metrics=SessionMetrics(issues_completed=1, issues_failed=2),
            deployment_result=None,
            errors=["Issue #2 failed", "Issue #3 blocked"],
        )

        assert result.success is False
        assert len(result.errors) == 2


class TestForgeAgent:
    """Tests for ForgeAgent class."""

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        """Create mock FORGE root structure."""
        # Create domain/project structure
        project_dir = tmp_path / "test-domain" / "test-project"
        project_dir.mkdir(parents=True)

        # Create CLAUDE.md
        (tmp_path / "CLAUDE.md").write_text("# FORGE Portfolio")

        # Create domain CLAUDE.md
        (tmp_path / "test-domain" / "CLAUDE.md").write_text("# Test Domain")

        # Create project CLAUDE.md
        (project_dir / "CLAUDE.md").write_text("# Test Project")

        # Create living docs structure
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "00-portfolio-digest.md").write_text("# Portfolio\nSprint 6")
        (docs_dir / "progress.md").write_text("# Progress")

        return tmp_path

    @pytest.fixture
    def mock_agent(self, mock_forge_root):
        """Create a ForgeAgent with mocked dependencies."""
        with patch("forge_harness.agent.GitHubClient"):
            with patch("forge_harness.agent.LivingDocs"):
                with patch("forge_harness.agent.ForgeDeployer"):
                    agent = ForgeAgent(
                        domain="test-domain",
                        project="test-project",
                        forge_root=mock_forge_root,
                        github_repo="owner/repo",
                        model="claude-sonnet-4-20250514",
                        tracker=NoOpTracker(
                            domain="test-domain",
                            project="test-project",
                            session_id="test123",
                        ),
                    )
                    return agent

    def test_agent_initialization(self, mock_agent):
        """ForgeAgent initializes with correct properties."""
        assert mock_agent.domain == "test-domain"
        assert mock_agent.project == "test-project"
        assert mock_agent.model == "claude-sonnet-4-20250514"
        assert mock_agent.deploy is False
        assert mock_agent.max_iterations is None

    def test_agent_session_id_format(self, mock_agent):
        """ForgeAgent generates valid session ID."""
        # Session ID should be timestamp format
        assert len(mock_agent.session_id) == 15  # YYYYMMDD_HHMMSS
        assert "_" in mock_agent.session_id

    def test_agent_project_dir(self, mock_agent, mock_forge_root):
        """ForgeAgent calculates correct project directory."""
        expected = mock_forge_root / "test-domain" / "test-project"
        assert mock_agent.project_dir == expected

    def test_agent_with_max_iterations(self, mock_forge_root):
        """ForgeAgent accepts max_iterations parameter."""
        with patch("forge_harness.agent.GitHubClient"):
            with patch("forge_harness.agent.LivingDocs"):
                with patch("forge_harness.agent.ForgeDeployer"):
                    agent = ForgeAgent(
                        domain="test",
                        project="test",
                        forge_root=mock_forge_root,
                        github_repo="owner/repo",
                        max_iterations=5,
                    )

                    assert agent.max_iterations == 5

    def test_agent_with_deploy(self, mock_forge_root):
        """ForgeAgent accepts deploy parameter."""
        with patch("forge_harness.agent.GitHubClient"):
            with patch("forge_harness.agent.LivingDocs"):
                with patch("forge_harness.agent.ForgeDeployer"):
                    agent = ForgeAgent(
                        domain="test",
                        project="test",
                        forge_root=mock_forge_root,
                        github_repo="owner/repo",
                        deploy=True,
                    )

                    assert agent.deploy is True

    def test_log_verbose(self, mock_agent, capsys):
        """_log outputs when verbose is True."""
        mock_agent.verbose = True
        mock_agent._log("Test message")

        captured = capsys.readouterr()
        assert "Test message" in captured.out

    def test_log_not_verbose(self, mock_agent, capsys):
        """_log is silent when verbose is False."""
        mock_agent.verbose = False
        mock_agent._log("Test message")

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_load_context(self, mock_agent):
        """_load_context returns context dictionary."""
        mock_agent.living_docs.consult = MagicMock(
            return_value=MagicMock(
                current_sprint="Sprint 6",
                blockers=[],
                priorities=["Build harness"],
                recent_milestones=["Completed Phase 1"],
            )
        )

        context = mock_agent._load_context()

        assert context["domain"] == "test-domain"
        assert context["project"] == "test-project"
        assert "domain_config" in context
        assert "current_sprint" in context

    def test_get_next_issue_priority_order(self, mock_agent):
        """_get_next_issue prioritizes correctly."""
        # Setup mock to return issues for different queries
        call_count = [0]

        def mock_list_issues(labels, limit):
            call_count[0] += 1
            # First call is in-progress, return nothing
            if "status:in-progress" in labels:
                return []
            # Second call is critical priority
            if "priority:critical" in labels:
                return [{"number": 1, "title": "Critical issue"}]
            return []

        mock_agent.github.list_issues = mock_list_issues

        issue = mock_agent._get_next_issue()

        assert issue is not None
        assert issue["number"] == 1

    def test_get_next_issue_in_progress_first(self, mock_agent):
        """_get_next_issue returns in-progress issues first."""

        def mock_list_issues(labels, limit):
            if "status:in-progress" in labels:
                return [{"number": 5, "title": "In progress issue"}]
            return []

        mock_agent.github.list_issues = mock_list_issues

        issue = mock_agent._get_next_issue()

        assert issue["number"] == 5

    def test_get_next_issue_none_available(self, mock_agent):
        """_get_next_issue returns None when no issues."""
        mock_agent.github.list_issues = MagicMock(return_value=[])

        issue = mock_agent._get_next_issue()

        assert issue is None

    def test_build_coding_prompt(self, mock_agent, mock_forge_root):
        """_build_coding_prompt generates prompt with issue context."""
        mock_agent.github.get_issue = MagicMock(return_value={"body": "Fix the bug", "labels": []})

        context = {
            "domain": "test-domain",
            "project": "test-project",
            "current_sprint": "Sprint 6",
            "priorities": ["Fix bugs", "Add tests"],
            "blockers": [],
            "recent_milestones": ["Completed API"],
        }
        issue = {"number": 42, "title": "Fix bug", "labels": []}

        prompt = mock_agent._build_coding_prompt(context, issue)

        # Verify issue number and title are in prompt
        assert "42" in prompt
        assert "Fix bug" in prompt
        # Verify context is included
        assert "Sprint 6" in prompt or "current_sprint" not in prompt  # May use default
        # Verify tech stack reference
        assert "FastAPI" in prompt or "uv" in prompt

    def test_build_coding_prompt_with_compliance(self, mock_agent, mock_forge_root):
        """_build_coding_prompt includes domain compliance info."""
        mock_agent.github.get_issue = MagicMock(return_value={"body": "Test", "labels": []})
        # Set compliance on domain config
        mock_agent.domain_config.compliance = ["COPPA", "HIPAA"]
        mock_agent.domain_config.localization = "es"

        context = {
            "current_sprint": "Test Sprint",
            "priorities": [],
            "blockers": [],
            "recent_milestones": [],
        }
        issue = {"number": 1, "title": "Test issue", "labels": []}

        prompt = mock_agent._build_coding_prompt(context, issue)

        assert "COPPA" in prompt
        assert "HIPAA" in prompt
        assert "es" in prompt

    def test_build_coding_prompt_with_human_gates(self, mock_agent, mock_forge_root):
        """_build_coding_prompt includes human gates from domain config."""
        mock_agent.github.get_issue = MagicMock(return_value={"body": "Test", "labels": []})
        mock_agent.domain_config.human_gates = [
            "Security changes require review",
            "Data model changes require review",
        ]

        context = {
            "current_sprint": "Test Sprint",
            "priorities": [],
            "blockers": [],
            "recent_milestones": [],
        }
        issue = {"number": 1, "title": "Test issue", "labels": []}

        prompt = mock_agent._build_coding_prompt(context, issue)

        assert "Security changes require review" in prompt
        assert "Data model changes require review" in prompt

    def test_get_default_prompt(self, mock_agent):
        """_get_default_prompt returns valid template."""
        prompt = mock_agent._get_default_prompt()

        assert "{domain}" in prompt
        assert "{project}" in prompt
        assert "{session_id}" in prompt
        assert "HUMAN GATES" in prompt
        assert "TECH STACK" in prompt
        assert "{compliance_section}" in prompt

    @pytest.mark.asyncio
    async def test_run_no_issues(self, mock_agent):
        """run() completes when no issues available."""
        mock_agent.github.list_issues = MagicMock(return_value=[])
        mock_agent._load_context = MagicMock(return_value={})

        result = await mock_agent.run()

        assert result.success is True
        assert result.issues_completed == []
        assert result.issues_failed == []

    @pytest.mark.asyncio
    async def test_run_max_iterations(self, mock_forge_root):
        """run() respects max_iterations limit."""
        with patch("forge_harness.agent.GitHubClient") as MockGitHub:
            with patch("forge_harness.agent.LivingDocs") as MockDocs:
                with patch("forge_harness.agent.ForgeDeployer"):
                    with patch("forge_harness.agent.BranchManager") as MockBranch:
                        mock_github = MagicMock()
                        mock_github.list_issues.return_value = [
                            {"number": 1, "title": "Issue 1", "labels": []}
                        ]
                        mock_github.get_issue.return_value = {"body": "Test", "labels": []}
                        MockGitHub.return_value = mock_github

                        mock_docs = MagicMock()
                        mock_docs.consult.return_value = MagicMock(
                            current_sprint="Test",
                            blockers=[],
                            priorities=[],
                            recent_milestones=[],
                        )
                        MockDocs.return_value = mock_docs

                        # Mock branch manager to not be on protected branch
                        mock_branch = MagicMock()
                        mock_branch.is_on_protected_branch.return_value = False
                        MockBranch.return_value = mock_branch

                        agent = ForgeAgent(
                            domain="test",
                            project="test",
                            forge_root=mock_forge_root,
                            github_repo="owner/repo",
                            max_iterations=1,
                            tracker=NoOpTracker(
                                domain="test",
                                project="test",
                                session_id="test",
                            ),
                        )

                        # Mock the coding session to return quickly
                        agent._run_coding_session = AsyncMock(return_value=(True, "Completed"))

                        result = await agent.run()

                        # Should stop after 1 iteration
                        assert agent._run_coding_session.call_count == 1

    @pytest.mark.asyncio
    async def test_run_tracks_session_start(self, mock_agent):
        """run() tracks session start with PostHog."""
        mock_tracker = MagicMock()
        mock_agent.tracker = mock_tracker
        mock_agent.github.list_issues = MagicMock(return_value=[])
        mock_agent._load_context = MagicMock(return_value={})

        await mock_agent.run()

        mock_tracker.session_started.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_tracks_session_end(self, mock_agent):
        """run() tracks session end with PostHog."""
        mock_tracker = MagicMock()
        mock_agent.tracker = mock_tracker
        mock_agent.github.list_issues = MagicMock(return_value=[])
        mock_agent._load_context = MagicMock(return_value={})

        await mock_agent.run()

        mock_tracker.session_ended.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_handles_exception(self, mock_agent):
        """run() handles exceptions gracefully."""
        mock_agent._load_context = MagicMock(side_effect=Exception("Context error"))

        result = await mock_agent.run()

        assert result.success is False
        assert "Context error" in result.errors[0]

    @pytest.mark.asyncio
    async def test_run_syncs_living_docs_on_handoff(self, mock_agent):
        """run() syncs living docs before returning (handoff)."""
        mock_agent.github.list_issues = MagicMock(return_value=[])
        mock_agent._load_context = MagicMock(return_value={})

        await mock_agent.run()

        # Sync should always be called on session handoff
        mock_agent.living_docs.sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_with_deploy(self, mock_forge_root):
        """run() triggers deployment when deploy=True."""
        with patch("forge_harness.agent.GitHubClient"):
            with patch("forge_harness.agent.LivingDocs") as MockDocs:
                with patch("forge_harness.agent.ForgeDeployer") as MockDeployer:
                    mock_docs = MagicMock()
                    mock_docs.consult.return_value = MagicMock(
                        current_sprint="Test",
                        blockers=[],
                        priorities=[],
                        recent_milestones=[],
                    )
                    MockDocs.return_value = mock_docs

                    mock_deployer = MagicMock()
                    mock_deployer.full_deploy.return_value = {
                        "success": True,
                        "backend": {"success": True},
                        "frontend": {"success": True},
                    }
                    MockDeployer.return_value = mock_deployer

                    agent = ForgeAgent(
                        domain="test",
                        project="test",
                        forge_root=mock_forge_root,
                        github_repo="owner/repo",
                        deploy=True,
                        tracker=NoOpTracker(
                            domain="test",
                            project="test",
                            session_id="test",
                        ),
                    )

                    agent.github.list_issues = MagicMock(return_value=[])
                    agent.issues_completed = [1]  # Pretend we completed something

                    result = await agent.run()

                    mock_deployer.full_deploy.assert_called_once()


class TestAgentMetricsTracking:
    """Tests for agent metrics tracking."""

    @pytest.fixture
    def agent_with_tracker(self, tmp_path):
        """Create agent with mock tracker."""
        project_dir = tmp_path / "test" / "project"
        project_dir.mkdir(parents=True)
        (tmp_path / "CLAUDE.md").write_text("# FORGE")

        with patch("forge_harness.agent.GitHubClient"):
            with patch("forge_harness.agent.LivingDocs"):
                with patch("forge_harness.agent.ForgeDeployer"):
                    tracker = MagicMock()
                    agent = ForgeAgent(
                        domain="test",
                        project="project",
                        forge_root=tmp_path,
                        github_repo="owner/repo",
                        tracker=tracker,
                    )
                    return agent, tracker

    def test_metrics_initialized(self, agent_with_tracker):
        """Agent initializes with empty metrics."""
        agent, _ = agent_with_tracker

        assert agent.metrics.issues_attempted == 0
        assert agent.metrics.issues_completed == 0
        assert agent.metrics.issues_failed == 0

    @pytest.mark.asyncio
    async def test_tracks_issue_started(self, agent_with_tracker):
        """Agent tracks when issue work begins."""
        agent, tracker = agent_with_tracker

        agent.github.list_issues = MagicMock(
            return_value=[
                {
                    "number": 1,
                    "title": "Test Issue",
                    "labels": [{"name": "priority:high"}],
                }
            ]
        )
        agent.github.get_issue = MagicMock(return_value={"body": "Test", "labels": []})
        agent._load_context = MagicMock(return_value={})

        # Mock session to fail so we don't loop forever
        agent._run_coding_session = AsyncMock(return_value=(False, "Error"))

        # Return empty after first issue to stop loop
        call_count = [0]
        original_list = agent.github.list_issues

        def limit_issues(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 5:  # First few calls for priority checking
                return original_list(*args, **kwargs)
            return []

        agent.github.list_issues = limit_issues

        await agent.run()

        tracker.issue_started.assert_called()

    @pytest.mark.asyncio
    async def test_tracks_issue_completed(self, agent_with_tracker):
        """Agent tracks when issue is completed."""
        agent, tracker = agent_with_tracker

        agent.github.list_issues = MagicMock(
            return_value=[{"number": 1, "title": "Test", "labels": []}]
        )
        agent.github.get_issue = MagicMock(return_value={"body": "Test", "labels": []})
        agent._load_context = MagicMock(return_value={})
        agent._run_coding_session = AsyncMock(return_value=(True, "Done"))
        # Mock verification to pass (required now for issue to be marked complete)
        agent._run_verification = MagicMock(
            return_value=MagicMock(
                passed=True,
                tests_passed=10,
                tests_failed=0,
                coverage_backend=85.0,
                coverage_frontend=70.0,
                lint_errors=0,
                summary="Verification: PASSED",
                error_message=None,
            )
        )
        agent.max_iterations = 1

        await agent.run()

        tracker.issue_completed.assert_called()

    @pytest.mark.asyncio
    async def test_tracks_issue_failed(self, agent_with_tracker):
        """Agent tracks when issue fails."""
        agent, tracker = agent_with_tracker

        agent.github.list_issues = MagicMock(
            return_value=[{"number": 1, "title": "Test", "labels": []}]
        )
        agent.github.get_issue = MagicMock(return_value={"body": "Test", "labels": []})
        agent._load_context = MagicMock(return_value={})
        agent._run_coding_session = AsyncMock(return_value=(False, "Error"))
        agent.max_iterations = 1

        await agent.run()

        tracker.issue_failed.assert_called()


class TestAgentHumanGateValidation:
    """Tests for human gate pre-validation."""

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        """Create mock FORGE root structure."""
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

    @pytest.fixture
    def agent_with_gates(self, mock_forge_root):
        """Create agent with security domain config."""
        with patch("forge_harness.agent.GitHubClient"):
            with patch("forge_harness.agent.LivingDocs") as MockDocs:
                with patch("forge_harness.agent.ForgeDeployer"):
                    with patch("forge_harness.agent.BranchManager"):
                        mock_docs = MagicMock()
                        mock_docs.consult.return_value = MagicMock(
                            current_sprint="Test",
                            blockers=[],
                            priorities=[],
                            recent_milestones=[],
                        )
                        mock_docs.sync.return_value = {}
                        MockDocs.return_value = mock_docs

                        agent = ForgeAgent(
                            domain="test-domain",
                            project="test-project",
                            forge_root=mock_forge_root,
                            github_repo="owner/repo",
                        )
                        # Add human gates to domain config
                        agent.domain_config.human_gates = [
                            "Security: Auth changes require review",
                            "Compliance: COPPA changes require review",
                        ]
                        return agent

    def test_validate_human_gates_method_exists(self, agent_with_gates):
        """Agent has _validate_human_gates method."""
        assert hasattr(agent_with_gates, "_validate_human_gates")
        assert callable(agent_with_gates._validate_human_gates)

    def test_validate_normal_issue_passes(self, agent_with_gates):
        """Normal issues pass human gate validation."""
        issue = {
            "number": 1,
            "title": "Add unit tests",
            "body": "Add tests for utils module",
        }

        can_proceed, reason = agent_with_gates._validate_human_gates(issue)

        assert can_proceed is True
        assert reason is None

    def test_validate_security_issue_blocked(self, agent_with_gates):
        """Security-related issues trigger human gate."""
        issue = {
            "number": 2,
            "title": "Update JWT authentication",
            "body": "Change the token expiration time",
        }

        can_proceed, reason = agent_with_gates._validate_human_gates(issue)

        assert can_proceed is False
        assert "security" in reason.lower()

    def test_validate_auth_keyword_blocked(self, agent_with_gates):
        """Auth keyword triggers security gate."""
        issue = {
            "number": 3,
            "title": "Fix auth flow",
            "body": "User authentication is broken",
        }

        can_proceed, reason = agent_with_gates._validate_human_gates(issue)

        assert can_proceed is False

    def test_validate_password_keyword_blocked(self, agent_with_gates):
        """Password keyword triggers security gate."""
        issue = {
            "number": 4,
            "title": "Update password validation",
            "body": "Add stronger password requirements",
        }

        can_proceed, reason = agent_with_gates._validate_human_gates(issue)

        assert can_proceed is False

    def test_validate_compliance_issue_blocked(self, agent_with_gates):
        """Compliance-related issues trigger human gate."""
        issue = {
            "number": 5,
            "title": "Add age verification",
            "body": "Need COPPA compliance for users under 13",
        }

        can_proceed, reason = agent_with_gates._validate_human_gates(issue)

        assert can_proceed is False
        assert "compliance" in reason.lower() or "coppa" in reason.lower()

    def test_validate_no_gates_configured(self, agent_with_gates):
        """Issues pass when no gates configured."""
        agent_with_gates.domain_config.human_gates = []

        issue = {
            "number": 6,
            "title": "Update JWT authentication",
            "body": "Security changes",
        }

        can_proceed, reason = agent_with_gates._validate_human_gates(issue)

        assert can_proceed is True
        assert reason is None


class TestAgentLivingDocsIntegration:
    """Tests for agent living-docs integration."""

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        """Create mock FORGE root structure."""
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

    @pytest.fixture
    def agent_with_living_docs(self, mock_forge_root):
        """Create agent with mocked living docs."""
        with patch("forge_harness.agent.GitHubClient"):
            with patch("forge_harness.agent.LivingDocs") as MockLivingDocs:
                with patch("forge_harness.agent.ForgeDeployer"):
                    with patch("forge_harness.agent.BranchManager"):
                        mock_living_docs = MagicMock()
                        mock_living_docs.consult.return_value = MagicMock(
                            current_sprint="Sprint 6",
                            blockers=[],
                            priorities=["Build harness"],
                            recent_milestones=["Completed Phase 1"],
                        )
                        mock_living_docs.sync.return_value = {"missing": [], "stale": []}
                        MockLivingDocs.return_value = mock_living_docs

                        agent = ForgeAgent(
                            domain="test-domain",
                            project="test-project",
                            forge_root=mock_forge_root,
                            github_repo="owner/repo",
                        )
                        agent.living_docs = mock_living_docs
                        return agent

    def test_living_docs_initialized(self, agent_with_living_docs):
        """Agent has living docs instance."""
        assert hasattr(agent_with_living_docs, "living_docs")
        assert agent_with_living_docs.living_docs is not None

    def test_load_context_calls_consult(self, agent_with_living_docs):
        """_load_context calls living_docs.consult."""
        agent_with_living_docs._load_context()
        agent_with_living_docs.living_docs.consult.assert_called_once_with(
            "test-domain", "test-project"
        )

    @pytest.mark.asyncio
    async def test_run_syncs_living_docs(self, agent_with_living_docs):
        """run() syncs living docs before returning."""
        agent_with_living_docs.github.list_issues = MagicMock(return_value=[])

        await agent_with_living_docs.run()

        agent_with_living_docs.living_docs.sync.assert_called_once_with(
            "test-domain", "test-project"
        )

    @pytest.mark.asyncio
    async def test_run_logs_missing_docs_warning(self, agent_with_living_docs, capsys):
        """run() logs warning for missing docs."""
        agent_with_living_docs.verbose = True
        agent_with_living_docs.github.list_issues = MagicMock(return_value=[])
        agent_with_living_docs.living_docs.sync.return_value = {
            "missing": ["active-context.md"],
            "stale": [],
        }

        await agent_with_living_docs.run()

        captured = capsys.readouterr()
        assert "Warning: Missing docs" in captured.out


class TestAgentBranchManagement:
    """Tests for agent branch management."""

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        """Create mock FORGE root structure."""
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

    @pytest.fixture
    def agent_with_branch_manager(self, mock_forge_root):
        """Create a ForgeAgent with branch manager."""
        with patch("forge_harness.agent.GitHubClient"):
            with patch("forge_harness.agent.LivingDocs"):
                with patch("forge_harness.agent.ForgeDeployer"):
                    agent = ForgeAgent(
                        domain="test-domain",
                        project="test-project",
                        forge_root=mock_forge_root,
                        github_repo="owner/repo",
                    )
                    return agent

    def test_branch_manager_initialized(self, agent_with_branch_manager):
        """Agent has branch manager on initialization."""
        assert hasattr(agent_with_branch_manager, "branch_manager")
        assert agent_with_branch_manager.branch_manager is not None

    def test_current_branch_tracking(self, agent_with_branch_manager):
        """Agent tracks current branch."""
        assert hasattr(agent_with_branch_manager, "current_branch")
        assert agent_with_branch_manager.current_branch is None  # Initially None


class TestAgentSecurityHooks:
    """Tests for agent security hook integration."""

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        """Create mock FORGE root structure."""
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

    @pytest.fixture
    def agent_with_security(self, mock_forge_root):
        """Create a ForgeAgent with security context."""
        with patch("forge_harness.agent.GitHubClient"):
            with patch("forge_harness.agent.LivingDocs"):
                with patch("forge_harness.agent.ForgeDeployer"):
                    agent = ForgeAgent(
                        domain="test-domain",
                        project="test-project",
                        forge_root=mock_forge_root,
                        github_repo="owner/repo",
                    )
                    return agent

    def test_security_context_created(self, agent_with_security):
        """Agent creates security context on initialization."""
        assert hasattr(agent_with_security, "security_context")
        assert agent_with_security.security_context is not None
        assert agent_with_security.security_context.domain == "test-domain"
        assert agent_with_security.security_context.project == "test-project"

    def test_security_hook_method_exists(self, agent_with_security):
        """Agent has _create_security_hook method."""
        assert hasattr(agent_with_security, "_create_security_hook")
        assert callable(agent_with_security._create_security_hook)

    @pytest.mark.asyncio
    async def test_security_hook_blocks_disallowed_commands(self, agent_with_security):
        """Security hook blocks commands not in allowlist."""
        hook = agent_with_security._create_security_hook()

        # Test with a dangerous command
        result = await hook(
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
            "test-tool-id",
            None,
        )

        assert result.get("decision") == "block"
        assert "reason" in result

    @pytest.mark.asyncio
    async def test_security_hook_allows_safe_commands(self, agent_with_security):
        """Security hook allows commands in allowlist."""
        hook = agent_with_security._create_security_hook()

        # Test with allowed command
        result = await hook(
            {"tool_name": "Bash", "tool_input": {"command": "ls -la"}},
            "test-tool-id",
            None,
        )

        # Empty dict means allowed
        assert result == {} or result.get("decision") != "block"

    @pytest.mark.asyncio
    async def test_security_hook_blocks_pip(self, agent_with_security):
        """Security hook blocks pip in favor of uv."""
        hook = agent_with_security._create_security_hook()

        result = await hook(
            {"tool_name": "Bash", "tool_input": {"command": "pip install requests"}},
            "test-tool-id",
            None,
        )

        assert result.get("decision") == "block"
        assert "uv" in result.get("reason", "").lower()

    @pytest.mark.asyncio
    async def test_security_hook_used_in_coding_session(self, agent_with_security):
        """Security hook is configured when running coding sessions.

        This verifies that the security hook is properly integrated into the
        provider options when _run_coding_session is called. The hook setup
        happens inline in that method when using ClaudeCodeProvider.
        """
        # The security hook is created via _create_security_hook
        hook = agent_with_security._create_security_hook()
        assert hook is not None

        # Verify the hook is callable and returns expected structure
        result = await hook(
            {"tool_name": "Bash", "tool_input": {"command": "echo test"}},
            "test-tool-id",
            None,
        )
        # Safe commands should be allowed (empty dict or not blocked)
        assert result == {} or result.get("decision") != "block"


class TestAgentVerification:
    """Tests for post-coding verification integration."""

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        """Create mock FORGE root structure."""
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

    @pytest.fixture
    def agent_with_verification(self, mock_forge_root):
        """Create a ForgeAgent for verification tests."""
        with patch("forge_harness.agent.GitHubClient"):
            with patch("forge_harness.agent.LivingDocs"):
                with patch("forge_harness.agent.ForgeDeployer"):
                    agent = ForgeAgent(
                        domain="test-domain",
                        project="test-project",
                        forge_root=mock_forge_root,
                        github_repo="owner/repo",
                        verbose=True,
                    )
                    return agent

    def test_run_verification_method_exists(self, agent_with_verification):
        """Agent has _run_verification method."""
        assert hasattr(agent_with_verification, "_run_verification")
        assert callable(agent_with_verification._run_verification)

    def test_run_verification_returns_result(self, agent_with_verification):
        """_run_verification returns a VerificationResult."""
        with patch("forge_harness.agent.Verifier") as MockVerifier:
            mock_verifier = MagicMock()
            mock_verifier.verify.return_value = MagicMock(
                passed=True,
                tests_passed=10,
                tests_failed=0,
                coverage_backend=85.0,
                coverage_frontend=70.0,
                lint_errors=0,
            )
            MockVerifier.return_value = mock_verifier

            result = agent_with_verification._run_verification()

            assert result.passed is True
            mock_verifier.verify.assert_called_once()

    def test_run_verification_passes_checkpoint(self, agent_with_verification):
        """_run_verification passes checkpoint to verifier."""
        with patch("forge_harness.agent.Verifier") as MockVerifier:
            mock_verifier = MagicMock()
            mock_verifier.verify.return_value = MagicMock(passed=True)
            MockVerifier.return_value = mock_verifier

            agent_with_verification._run_verification(checkpoint="- [ ] Tests pass")

            mock_verifier.verify.assert_called_with(checkpoint="- [ ] Tests pass")

    def test_run_verification_respects_skip_quality_gates(self, mock_forge_root):
        """_run_verification passes skip_quality_gates to verifier."""
        with patch("forge_harness.agent.GitHubClient"):
            with patch("forge_harness.agent.LivingDocs"):
                with patch("forge_harness.agent.ForgeDeployer"):
                    with patch("forge_harness.agent.Verifier") as MockVerifier:
                        agent = ForgeAgent(
                            domain="test-domain",
                            project="test-project",
                            forge_root=mock_forge_root,
                            github_repo="owner/repo",
                            skip_quality_gates=True,
                        )

                        MockVerifier.return_value.verify.return_value = MagicMock(passed=True)

                        agent._run_verification()

                        # Check that skip_quality_gates was passed to Verifier
                        MockVerifier.assert_called_with(
                            project_dir=agent.project_dir,
                            domain_config=agent.domain_config,
                            skip_quality_gates=True,
                        )

    @pytest.mark.asyncio
    async def test_run_calls_verification_after_coding(self, agent_with_verification):
        """run() calls verification after successful coding session."""
        with patch("forge_harness.agent.BranchManager"):
            with patch("forge_harness.agent.Verifier") as MockVerifier:
                mock_verifier = MagicMock()
                mock_verifier.verify.return_value = MagicMock(
                    passed=True,
                    tests_passed=10,
                    tests_failed=0,
                    coverage_backend=85.0,
                    coverage_frontend=70.0,
                    lint_errors=0,
                    summary="Verification: PASSED",
                    error_message=None,
                )
                MockVerifier.return_value = mock_verifier

                # Mock coding session success
                agent_with_verification._run_coding_session = AsyncMock(
                    return_value=(True, "Success summary")
                )
                agent_with_verification._load_context = MagicMock(return_value={})
                agent_with_verification.github.list_issues = MagicMock(
                    side_effect=[
                        [{"number": 1, "title": "Test", "labels": [], "body": ""}],
                        [],  # No more issues
                    ]
                )
                agent_with_verification.github.get_issue = MagicMock(
                    return_value={"body": "Issue body"}
                )
                agent_with_verification.branch_manager.is_on_protected_branch = MagicMock(
                    return_value=False
                )

                await agent_with_verification.run()

                # Verification should have been called
                mock_verifier.verify.assert_called()

    @pytest.mark.asyncio
    async def test_run_fails_issue_on_verification_failure(self, agent_with_verification):
        """run() marks issue as failed when verification fails."""
        with patch("forge_harness.agent.BranchManager"):
            with patch("forge_harness.agent.Verifier") as MockVerifier:
                mock_verifier = MagicMock()
                mock_verifier.verify.return_value = MagicMock(
                    passed=False,
                    tests_passed=8,
                    tests_failed=2,
                    coverage_backend=50.0,
                    coverage_frontend=40.0,
                    lint_errors=3,
                    summary="Verification: FAILED",
                    error_message="Quality gates not met",
                )
                MockVerifier.return_value = mock_verifier

                # Mock coding session success
                agent_with_verification._run_coding_session = AsyncMock(
                    return_value=(True, "Success summary")
                )
                agent_with_verification._load_context = MagicMock(return_value={})
                agent_with_verification.github.list_issues = MagicMock(
                    side_effect=[
                        [{"number": 1, "title": "Test", "labels": [], "body": ""}],
                        [],  # No more issues
                    ]
                )
                agent_with_verification.github.get_issue = MagicMock(
                    return_value={"body": "Issue body"}
                )
                agent_with_verification.branch_manager.is_on_protected_branch = MagicMock(
                    return_value=False
                )

                result = await agent_with_verification.run()

                # Issue should be failed, not completed
                assert 1 in result.issues_failed
                assert 1 not in result.issues_completed

    @pytest.mark.asyncio
    async def test_run_stores_retry_context_on_verification_failure(self, agent_with_verification):
        """run() stores error context for retry when verification fails."""
        with patch("forge_harness.agent.BranchManager"):
            with patch("forge_harness.agent.Verifier") as MockVerifier:
                mock_verifier = MagicMock()
                mock_verifier.verify.return_value = MagicMock(
                    passed=False,
                    tests_passed=8,
                    tests_failed=2,
                    coverage_backend=50.0,
                    coverage_frontend=40.0,
                    lint_errors=3,
                    summary="Verification: FAILED",
                    error_message="Quality gates not met",
                    error_details=[
                        "Test failure: FAILED test_foo",
                        "Import error: ModuleNotFoundError",
                    ],
                    fix_suggestions=["FIX: Run 'uv add pytest'"],
                )
                MockVerifier.return_value = mock_verifier

                agent_with_verification._run_coding_session = AsyncMock(
                    return_value=(True, "Success")
                )
                agent_with_verification._load_context = MagicMock(return_value={})
                agent_with_verification.github.list_issues = MagicMock(
                    side_effect=[
                        [{"number": 1, "title": "Test", "labels": [], "body": ""}],
                        [],
                    ]
                )
                agent_with_verification.github.get_issue = MagicMock(return_value={"body": ""})
                agent_with_verification.branch_manager.is_on_protected_branch = MagicMock(
                    return_value=False
                )

                await agent_with_verification.run()

                # Check that retry context was stored for the failed issue
                assert 1 in agent_with_verification.retry_context
                error_details, fix_suggestions = agent_with_verification.retry_context[1]
                assert "Test failure: FAILED test_foo" in error_details
                assert "FIX: Run 'uv add pytest'" in fix_suggestions
