"""Mock-based integration tests for FORGE Harness.

These tests verify harness workflows WITHOUT requiring claude_code_sdk installed.
Uses module-level mocking to simulate SDK presence.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Mock claude_code_sdk at module level BEFORE importing agent
@pytest.fixture(autouse=True)
def mock_claude_sdk():
    """Mock claude_code_sdk module for all tests in this file."""
    # Create mock module structure
    mock_sdk = MagicMock()
    mock_sdk.ClaudeCodeOptions = MagicMock
    mock_sdk.query = AsyncMock(return_value=iter([]))  # Empty async iterator

    mock_types = MagicMock()
    mock_types.HookMatcher = MagicMock

    # Inject mocks into sys.modules
    sys.modules["claude_code_sdk"] = mock_sdk
    sys.modules["claude_code_sdk.types"] = mock_types

    yield mock_sdk

    # Clean up
    if "claude_code_sdk" in sys.modules:
        del sys.modules["claude_code_sdk"]
    if "claude_code_sdk.types" in sys.modules:
        del sys.modules["claude_code_sdk.types"]


class TestWorkflowIntegrationMocked:
    """Integration tests for workflow harness with mocked dependencies."""

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        """Create a minimal FORGE project structure."""
        # Root CLAUDE.md
        (tmp_path / "CLAUDE.md").write_text("# FORGE Portfolio\nTech stack: FastAPI")

        # Docs directory
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "00-portfolio-digest.md").write_text("# Portfolio\nActive projects: 1")
        (docs / "progress.md").write_text("# Progress")

        # Domain directory
        domain = tmp_path / "mock-domain"
        domain.mkdir()
        (domain / "CLAUDE.md").write_text("# Mock Domain\nCompliance: None")

        # Project directory
        project = domain / "mock-project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("# Mock Project")

        # Backend
        backend = project / "backend"
        backend.mkdir()
        (backend / "app").mkdir()
        (backend / "tests").mkdir()
        (backend / "pyproject.toml").write_text(
            '[project]\nname = "mock-project"\nversion = "0.1.0"'
        )

        # Frontend
        frontend = project / "frontend"
        frontend.mkdir()
        (frontend / "src").mkdir()
        (frontend / "package.json").write_text(
            '{"name": "mock-project", "scripts": {"test": "vitest"}}'
        )

        # Project docs
        project_docs = project / "docs"
        project_docs.mkdir()
        (project_docs / "progress.md").write_text("# Progress")
        (project_docs / "PLAN.md").write_text("# Plan\n\n## Phase 1\nTask list")

        return tmp_path

    def test_living_docs_flow(self, mock_forge_root):
        """Test living docs can be loaded and updated without SDK."""
        from forge_harness.living_docs import LivingDocs

        docs = LivingDocs(mock_forge_root)

        # Consult should work
        context = docs.consult("mock-domain", "mock-project")
        assert context is not None

        # Update should work
        docs.update(
            domain="mock-domain",
            project="mock-project",
            milestone="Test milestone",
            details="Test details",
        )

        # Verify progress.md was updated
        progress_path = mock_forge_root / "mock-domain" / "mock-project" / "docs" / "progress.md"
        assert "Test milestone" in progress_path.read_text()

    def test_preflight_checker_flow(self, mock_forge_root):
        """Test preflight checker works without SDK."""
        from forge_harness.preflight import PreflightChecker

        project_dir = mock_forge_root / "mock-domain" / "mock-project"
        checker = PreflightChecker(project_dir)

        result = checker.run(auto_fix=False)

        # Should return a result even if some checks fail
        assert result is not None
        assert hasattr(result, "passed")
        assert hasattr(result, "issues")
        assert hasattr(result, "fixes_applied")

    def test_verification_flow(self, mock_forge_root):
        """Test verification works without SDK."""
        from forge_harness.domain_config import DomainConfig
        from forge_harness.verification import VerificationResult, Verifier

        project_dir = mock_forge_root / "mock-domain" / "mock-project"

        # Create a minimal domain config
        domain_config = DomainConfig(
            name="mock-domain",
            compliance=[],
            human_gates=[],
            frontend_tier="React",
            special_rules={},
        )

        # Verifier should initialize without errors
        verifier = Verifier(
            project_dir=project_dir,
            domain_config=domain_config,
            skip_quality_gates=True,  # Skip for this test
        )

        # Should be able to call verify (may fail due to no tests, but shouldn't crash)
        result = verifier.verify(checkpoint="")
        assert isinstance(result, VerificationResult)

    def test_branch_manager_detection(self, mock_forge_root):
        """Test branch manager can detect git status without SDK."""
        import subprocess

        from forge_harness.branch_manager import BranchManager

        project_dir = mock_forge_root / "mock-domain" / "mock-project"

        # Initialize git repo for this test
        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=project_dir,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=project_dir,
            capture_output=True,
        )

        manager = BranchManager(project_dir)

        # Should detect current branch without errors
        # (will be empty or 'master' depending on git version)
        result = manager.is_on_protected_branch()
        assert isinstance(result, bool)


class TestAgentWithMockedSDK:
    """Test ForgeAgent with fully mocked SDK."""

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        """Create FORGE structure for agent tests."""
        (tmp_path / "CLAUDE.md").write_text("# FORGE Portfolio")

        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "00-portfolio-digest.md").write_text("# Portfolio")
        (docs / "progress.md").write_text("# Progress")

        domain = tmp_path / "test-domain"
        domain.mkdir()
        (domain / "CLAUDE.md").write_text("# Test Domain")

        project = domain / "test-project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("# Test Project")
        (project / "backend").mkdir()
        (project / "docs").mkdir()
        (project / "docs" / "progress.md").write_text("# Progress")

        return tmp_path

    @pytest.fixture
    def mock_github_client(self):
        """Create mock GitHub client."""
        client = MagicMock()
        client.list_issues = MagicMock(
            side_effect=[
                [{"number": 1, "title": "Test Issue", "labels": [], "body": "Test body"}],
                [],  # No more issues
            ]
        )
        client.get_issue = MagicMock(return_value={"body": "Test body", "labels": []})
        client.update_issue = MagicMock()
        client.close_issue = MagicMock()
        client.add_comment = MagicMock()
        return client

    @pytest.fixture
    def mock_living_docs(self):
        """Create mock living docs."""
        docs = MagicMock()
        mock_context = MagicMock()
        mock_context.current_sprint = "Sprint 1"
        mock_context.priorities = ["Test priority"]
        mock_context.blockers = []
        mock_context.recent_milestones = []
        mock_context.recent_progress = ""
        mock_context.active_context = ""
        mock_context.decision_log = []
        docs.consult = MagicMock(return_value=mock_context)
        docs.update = MagicMock()
        docs.sync = MagicMock(return_value={"status": "synced"})
        return docs

    @pytest.fixture
    def mock_domain_config(self):
        """Create mock domain config."""
        config = MagicMock()
        config.human_gates = []
        config.compliance = []
        config.compliance_requirements = []
        config.min_backend_coverage = 70.0
        config.min_frontend_coverage = 60.0
        config.frontend_tier = "React"
        config.localization = None
        config.special_rules = {}
        return config

    @pytest.mark.asyncio
    async def test_agent_initialization_with_mocked_sdk(
        self, mock_forge_root, mock_github_client, mock_living_docs, mock_domain_config
    ):
        """Test that ForgeAgent can be initialized with mocked SDK."""
        with patch("forge_harness.agent.GitHubClient", return_value=mock_github_client):
            with patch("forge_harness.agent.LivingDocs", return_value=mock_living_docs):
                with patch("forge_harness.agent.ForgeDeployer"):
                    with patch(
                        "forge_harness.agent.get_domain_config", return_value=mock_domain_config
                    ):
                        from forge_harness.agent import ForgeAgent
                        from forge_harness.posthog_tracker import NoOpTracker

                        agent = ForgeAgent(
                            domain="test-domain",
                            project="test-project",
                            forge_root=mock_forge_root,
                            github_repo="test/repo",
                            max_iterations=1,
                            tracker=NoOpTracker(
                                domain="test-domain",
                                project="test-project",
                                session_id="test",
                            ),
                        )

                        assert agent.domain == "test-domain"
                        assert agent.project == "test-project"

    @pytest.mark.asyncio
    async def test_agent_run_with_mocked_session(
        self, mock_forge_root, mock_github_client, mock_living_docs, mock_domain_config
    ):
        """Test agent run() with fully mocked coding session."""
        with patch("forge_harness.agent.GitHubClient", return_value=mock_github_client):
            with patch("forge_harness.agent.LivingDocs", return_value=mock_living_docs):
                with patch("forge_harness.agent.ForgeDeployer"):
                    with patch("forge_harness.agent.BranchManager") as MockBranch:
                        with patch(
                            "forge_harness.agent.get_domain_config", return_value=mock_domain_config
                        ):
                            MockBranch.return_value.is_on_protected_branch.return_value = False

                            from forge_harness.agent import AgentResult, ForgeAgent
                            from forge_harness.posthog_tracker import NoOpTracker
                            from forge_harness.verification import VerificationResult

                            agent = ForgeAgent(
                                domain="test-domain",
                                project="test-project",
                                forge_root=mock_forge_root,
                                github_repo="test/repo",
                                max_iterations=1,
                                tracker=NoOpTracker(
                                    domain="test-domain",
                                    project="test-project",
                                    session_id="test",
                                ),
                            )

                            # Mock internal methods completely
                            agent._run_coding_session = AsyncMock(return_value=(True, "Success"))
                            agent._run_verification = MagicMock(
                                return_value=VerificationResult(
                                    passed=True,
                                    tests_passed=10,
                                    tests_failed=0,
                                    coverage_backend=85.0,
                                    coverage_frontend=70.0,
                                    lint_errors=0,
                                    checkpoint_results={},
                                    error_message=None,
                                )
                            )
                            agent._run_preflight = MagicMock(
                                return_value=MagicMock(passed=True, issues=[], fixes_applied=[])
                            )

                            result = await agent.run()

                            assert isinstance(result, AgentResult)
                            assert 1 in result.issues_completed
                            mock_github_client.close_issue.assert_called_with(1)


class TestHumanGateValidation:
    """Test human gate validation without SDK."""

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        """Create FORGE structure with gates configured."""
        (tmp_path / "CLAUDE.md").write_text("# FORGE")

        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "00-portfolio-digest.md").write_text("# Portfolio")
        (docs / "progress.md").write_text("# Progress")

        domain = tmp_path / "secure-domain"
        domain.mkdir()
        (domain / "CLAUDE.md").write_text("""# Secure Domain

## Human Gates
- Security: Auth changes
- Compliance: GDPR
""")

        project = domain / "secure-project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("# Secure Project")
        (project / "backend").mkdir()
        (project / "docs").mkdir()
        (project / "docs" / "progress.md").write_text("# Progress")

        return tmp_path

    @pytest.fixture
    def mock_github_client(self):
        """Create mock GitHub client with security issue."""
        client = MagicMock()
        client.list_issues = MagicMock(
            return_value=[
                {
                    "number": 1,
                    "title": "Implement JWT authentication",
                    "labels": [],
                    "body": "Add JWT token handling",
                }
            ]
        )
        client.get_issue = MagicMock(return_value={"body": "Add JWT token handling", "labels": []})
        client.update_issue = MagicMock()
        return client

    @pytest.mark.asyncio
    async def test_human_gate_triggers_on_security_keywords(
        self, mock_forge_root, mock_github_client
    ):
        """Test that security keywords trigger human gate."""
        mock_living_docs = MagicMock()
        mock_context = MagicMock()
        mock_context.current_sprint = "Sprint 1"
        mock_context.priorities = []
        mock_context.blockers = []
        mock_context.recent_milestones = []
        mock_living_docs.consult = MagicMock(return_value=mock_context)
        mock_living_docs.update = MagicMock()
        mock_living_docs.sync = MagicMock(return_value={})

        mock_domain_config = MagicMock()
        mock_domain_config.human_gates = ["Security"]
        mock_domain_config.compliance = []
        mock_domain_config.compliance_requirements = []
        mock_domain_config.min_backend_coverage = 70.0
        mock_domain_config.min_frontend_coverage = 60.0
        mock_domain_config.frontend_tier = "React"
        mock_domain_config.localization = None
        mock_domain_config.special_rules = {}

        with patch("forge_harness.agent.GitHubClient", return_value=mock_github_client):
            with patch("forge_harness.agent.LivingDocs", return_value=mock_living_docs):
                with patch("forge_harness.agent.ForgeDeployer"):
                    with patch("forge_harness.agent.BranchManager") as MockBranch:
                        with patch(
                            "forge_harness.agent.get_domain_config", return_value=mock_domain_config
                        ):
                            MockBranch.return_value.is_on_protected_branch.return_value = False

                            from forge_harness.agent import ForgeAgent
                            from forge_harness.posthog_tracker import NoOpTracker

                            agent = ForgeAgent(
                                domain="secure-domain",
                                project="secure-project",
                                forge_root=mock_forge_root,
                                github_repo="test/repo",
                                max_iterations=1,
                                tracker=NoOpTracker(
                                    domain="secure-domain",
                                    project="secure-project",
                                    session_id="test",
                                ),
                            )

                            result = await agent.run()

                            # Issue should NOT be completed (blocked by gate)
                            assert 1 not in result.issues_completed

                            # Issue should have been labeled
                            mock_github_client.update_issue.assert_called()
                            call_args = mock_github_client.update_issue.call_args
                            assert "needs-human-review" in call_args.kwargs.get("add_labels", [])


class TestMetricsTrackingMocked:
    """Test metrics tracking without SDK."""

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        """Create minimal FORGE structure."""
        (tmp_path / "CLAUDE.md").write_text("# FORGE")

        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "00-portfolio-digest.md").write_text("# Portfolio")
        (docs / "progress.md").write_text("# Progress")

        domain = tmp_path / "metrics-domain"
        domain.mkdir()
        (domain / "CLAUDE.md").write_text("# Metrics Domain")

        project = domain / "metrics-project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("# Metrics Project")
        (project / "backend").mkdir()
        (project / "docs").mkdir()
        (project / "docs" / "progress.md").write_text("# Progress")

        return tmp_path

    @pytest.mark.asyncio
    async def test_tracker_methods_called(self, mock_forge_root):
        """Test that tracker methods are called during session."""
        tracker = MagicMock()

        mock_github = MagicMock()
        mock_github.list_issues = MagicMock(
            side_effect=[
                [{"number": 1, "title": "Test", "labels": [], "body": ""}],
                [],
            ]
        )
        mock_github.get_issue = MagicMock(return_value={"body": "", "labels": []})
        mock_github.update_issue = MagicMock()
        mock_github.close_issue = MagicMock()

        mock_living_docs = MagicMock()
        mock_context = MagicMock()
        mock_context.current_sprint = "Sprint 1"
        mock_context.priorities = []
        mock_context.blockers = []
        mock_context.recent_milestones = []
        mock_living_docs.consult = MagicMock(return_value=mock_context)
        mock_living_docs.update = MagicMock()
        mock_living_docs.sync = MagicMock(return_value={})

        mock_domain_config = MagicMock()
        mock_domain_config.human_gates = []
        mock_domain_config.compliance = []
        mock_domain_config.min_backend_coverage = 70.0
        mock_domain_config.min_frontend_coverage = 60.0
        mock_domain_config.frontend_tier = "React"
        mock_domain_config.localization = None
        mock_domain_config.special_rules = {}

        with patch("forge_harness.agent.GitHubClient", return_value=mock_github):
            with patch("forge_harness.agent.LivingDocs", return_value=mock_living_docs):
                with patch("forge_harness.agent.ForgeDeployer"):
                    with patch("forge_harness.agent.BranchManager") as MockBranch:
                        with patch(
                            "forge_harness.agent.get_domain_config", return_value=mock_domain_config
                        ):
                            MockBranch.return_value.is_on_protected_branch.return_value = False

                            from forge_harness.agent import ForgeAgent
                            from forge_harness.verification import VerificationResult

                            agent = ForgeAgent(
                                domain="metrics-domain",
                                project="metrics-project",
                                forge_root=mock_forge_root,
                                github_repo="test/repo",
                                max_iterations=1,
                                tracker=tracker,
                            )

                            agent._run_coding_session = AsyncMock(return_value=(True, "Success"))
                            agent._run_verification = MagicMock(
                                return_value=VerificationResult(
                                    passed=True,
                                    tests_passed=10,
                                    tests_failed=0,
                                    coverage_backend=85.0,
                                    coverage_frontend=70.0,
                                    lint_errors=0,
                                    checkpoint_results={},
                                    error_message=None,
                                )
                            )
                            agent._run_preflight = MagicMock(
                                return_value=MagicMock(passed=True, issues=[], fixes_applied=[])
                            )

                            await agent.run()

                            # Verify tracker methods were called
                            tracker.session_started.assert_called_once()
                            tracker.issue_started.assert_called_once()
                            tracker.verification_completed.assert_called_once()
                            tracker.issue_completed.assert_called_once()
                            tracker.session_ended.assert_called_once()


class TestCircuitBreakerMocked:
    """Test circuit breaker functionality without SDK."""

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        """Create minimal FORGE structure."""
        (tmp_path / "CLAUDE.md").write_text("# FORGE")

        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "00-portfolio-digest.md").write_text("# Portfolio")
        (docs / "progress.md").write_text("# Progress")

        domain = tmp_path / "cb-domain"
        domain.mkdir()
        (domain / "CLAUDE.md").write_text("# CB Domain")

        project = domain / "cb-project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("# CB Project")
        (project / "backend").mkdir()
        (project / "docs").mkdir()
        (project / "docs" / "progress.md").write_text("# Progress")

        return tmp_path

    @pytest.mark.asyncio
    async def test_circuit_breaker_skips_after_max_attempts(self, mock_forge_root):
        """Test that circuit breaker skips issues after max attempts with same error."""
        mock_github = MagicMock()
        # Return same issue multiple times
        mock_github.list_issues = MagicMock(
            side_effect=[
                [{"number": 1, "title": "Failing Issue", "labels": [], "body": ""}],
                [{"number": 1, "title": "Failing Issue", "labels": [], "body": ""}],
                [{"number": 1, "title": "Failing Issue", "labels": [], "body": ""}],
                [{"number": 1, "title": "Failing Issue", "labels": [], "body": ""}],
                [],
            ]
        )
        mock_github.get_issue = MagicMock(return_value={"body": "", "labels": []})
        mock_github.update_issue = MagicMock()
        mock_github.close_issue = MagicMock()

        mock_living_docs = MagicMock()
        mock_context = MagicMock()
        mock_context.current_sprint = "Sprint 1"
        mock_context.priorities = []
        mock_context.blockers = []
        mock_context.recent_milestones = []
        mock_living_docs.consult = MagicMock(return_value=mock_context)
        mock_living_docs.update = MagicMock()
        mock_living_docs.sync = MagicMock(return_value={})

        mock_domain_config = MagicMock()
        mock_domain_config.human_gates = []
        mock_domain_config.compliance = []
        mock_domain_config.min_backend_coverage = 70.0
        mock_domain_config.min_frontend_coverage = 60.0
        mock_domain_config.frontend_tier = "React"
        mock_domain_config.localization = None
        mock_domain_config.special_rules = {}

        with patch("forge_harness.agent.GitHubClient", return_value=mock_github):
            with patch("forge_harness.agent.LivingDocs", return_value=mock_living_docs):
                with patch("forge_harness.agent.ForgeDeployer"):
                    with patch("forge_harness.agent.BranchManager") as MockBranch:
                        with patch(
                            "forge_harness.agent.get_domain_config", return_value=mock_domain_config
                        ):
                            MockBranch.return_value.is_on_protected_branch.return_value = False

                            from forge_harness.agent import ForgeAgent
                            from forge_harness.posthog_tracker import NoOpTracker
                            from forge_harness.verification import VerificationResult

                            agent = ForgeAgent(
                                domain="cb-domain",
                                project="cb-project",
                                forge_root=mock_forge_root,
                                github_repo="test/repo",
                                max_iterations=10,  # Allow many iterations
                                tracker=NoOpTracker(
                                    domain="cb-domain",
                                    project="cb-project",
                                    session_id="test",
                                ),
                            )

                            # Set max attempts low for test
                            agent.max_attempts_per_issue = 2

                            # Always fail with same error
                            agent._run_coding_session = AsyncMock(
                                return_value=(True, "Coding done")
                            )
                            agent._run_verification = MagicMock(
                                return_value=VerificationResult(
                                    passed=False,
                                    tests_passed=5,
                                    tests_failed=5,
                                    coverage_backend=50.0,
                                    coverage_frontend=40.0,
                                    lint_errors=0,
                                    checkpoint_results={},
                                    error_message="Same error",
                                    error_details=["Error 1", "Error 2"],
                                    fix_suggestions=["Fix 1"],
                                )
                            )
                            agent._run_preflight = MagicMock(
                                return_value=MagicMock(passed=True, issues=[], fixes_applied=[])
                            )

                            result = await agent.run()

                            # Issue should be in failed list
                            assert 1 in result.issues_failed

                            # Should have been labeled as blocked
                            update_calls = mock_github.update_issue.call_args_list
                            blocked_labels = [
                                call
                                for call in update_calls
                                if "status:blocked" in call.kwargs.get("add_labels", [])
                            ]
                            assert len(blocked_labels) > 0


class TestErrorSignatureComputation:
    """Test error signature computation for circuit breaker."""

    def test_compute_error_signature_deterministic(self, mock_forge_root=None, tmp_path=None):
        """Test that error signature is deterministic."""
        # Need to create minimal structure for agent init
        if tmp_path is None:
            import tempfile

            tmp_path = Path(tempfile.mkdtemp())

        (tmp_path / "CLAUDE.md").write_text("# FORGE")
        docs = tmp_path / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "00-portfolio-digest.md").write_text("# Portfolio")
        (docs / "progress.md").write_text("# Progress")

        domain = tmp_path / "test-domain"
        domain.mkdir(exist_ok=True)
        (domain / "CLAUDE.md").write_text("# Test")

        project = domain / "test-project"
        project.mkdir(exist_ok=True)
        (project / "CLAUDE.md").write_text("# Test")
        (project / "backend").mkdir(exist_ok=True)
        (project / "docs").mkdir(exist_ok=True)
        (project / "docs" / "progress.md").write_text("# Progress")

        mock_domain_config = MagicMock()
        mock_domain_config.human_gates = []
        mock_domain_config.compliance = []
        mock_domain_config.frontend_tier = "React"
        mock_domain_config.localization = None
        mock_domain_config.special_rules = {}

        with patch("forge_harness.agent.GitHubClient"):
            with patch("forge_harness.agent.LivingDocs"):
                with patch("forge_harness.agent.ForgeDeployer"):
                    with patch(
                        "forge_harness.agent.get_domain_config", return_value=mock_domain_config
                    ):
                        from forge_harness.agent import ForgeAgent

                        agent = ForgeAgent(
                            domain="test-domain",
                            project="test-project",
                            forge_root=tmp_path,
                            github_repo="test/repo",
                        )

                        errors = ["Error A", "Error B", "Error C"]
                        sig1 = agent._compute_error_signature(errors)
                        sig2 = agent._compute_error_signature(errors)

                        assert sig1 == sig2
                        assert len(sig1) == 8  # MD5 truncated to 8 chars

    def test_different_errors_different_signatures(self, tmp_path):
        """Test that different errors produce different signatures."""
        (tmp_path / "CLAUDE.md").write_text("# FORGE")
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "00-portfolio-digest.md").write_text("# Portfolio")
        (docs / "progress.md").write_text("# Progress")

        domain = tmp_path / "test-domain"
        domain.mkdir()
        (domain / "CLAUDE.md").write_text("# Test")

        project = domain / "test-project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("# Test")
        (project / "backend").mkdir()
        (project / "docs").mkdir()
        (project / "docs" / "progress.md").write_text("# Progress")

        mock_domain_config = MagicMock()
        mock_domain_config.human_gates = []
        mock_domain_config.compliance = []
        mock_domain_config.frontend_tier = "React"
        mock_domain_config.localization = None
        mock_domain_config.special_rules = {}

        with patch("forge_harness.agent.GitHubClient"):
            with patch("forge_harness.agent.LivingDocs"):
                with patch("forge_harness.agent.ForgeDeployer"):
                    with patch(
                        "forge_harness.agent.get_domain_config", return_value=mock_domain_config
                    ):
                        from forge_harness.agent import ForgeAgent

                        agent = ForgeAgent(
                            domain="test-domain",
                            project="test-project",
                            forge_root=tmp_path,
                            github_repo="test/repo",
                        )

                        sig1 = agent._compute_error_signature(["Error A"])
                        sig2 = agent._compute_error_signature(["Error B"])

                        assert sig1 != sig2
