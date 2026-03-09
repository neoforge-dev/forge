"""
FORGE Agent Session Orchestration
==================================

Core agent logic that orchestrates autonomous coding sessions.
Uses Claude Code SDK to run coding tasks with FORGE-specific context.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .branch_manager import BranchManager
from .deployment import ForgeDeployer
from .domain_config import get_domain_config, load_claude_md_hierarchy
from .github_client import GitHubClient
from .living_docs import LivingDocs
from .llm_provider import ClaudeCodeProvider, LLMProvider, ProviderOptions
from .memory_flush import ContextMonitor, MemoryFlushManager
from .posthog_tracker import HarnessTracker, SessionMetrics
from .preflight import PreflightChecker, PreflightResult
from .security import (
    bash_security_hook,
    create_forge_security_context,
)
from .verification import VerificationResult, Verifier


@dataclass
class AgentResult:
    """Result of an agent session."""

    success: bool
    session_id: str
    issues_completed: list[int]
    issues_failed: list[int]
    metrics: SessionMetrics
    deployment_result: dict[str, Any] | None
    errors: list[str] = field(default_factory=list)


class ForgeAgent:
    """
    FORGE autonomous coding agent.

    Orchestrates a complete coding session:
    1. Load context from living docs and CLAUDE.md hierarchy
    2. Query GitHub Issues for work
    3. Run coding tasks with Claude Code SDK
    4. Update issues and living docs
    5. Optionally deploy
    """

    def __init__(
        self,
        domain: str,
        project: str,
        forge_root: Path,
        github_repo: str,
        model: str = "claude-sonnet-4-20250514",
        max_iterations: int | None = None,
        deploy: bool = False,
        skip_quality_gates: bool = False,
        tracker: HarnessTracker | None = None,
        verbose: bool = False,
        provider: LLMProvider | None = None,
    ):
        """
        Initialize the agent.

        Args:
            domain: FORGE domain name
            project: Project name within domain
            forge_root: Path to FORGE repository root
            github_repo: GitHub repository (owner/repo format)
            model: Claude model to use
            max_iterations: Maximum iterations (None = unlimited)
            deploy: Whether to deploy after completing work
            skip_quality_gates: Skip quality gates for deployment
            tracker: PostHog tracker instance
            verbose: Enable verbose output
            provider: LLM Provider instance
        """
        self.domain = domain
        self.project = project
        self.forge_root = Path(forge_root)
        self.project_dir = self.forge_root / domain / project
        self.github_repo = github_repo
        self.model = model
        self.max_iterations = max_iterations
        self.deploy = deploy
        self.skip_quality_gates = skip_quality_gates
        self.tracker = tracker
        self.verbose = verbose
        self.provider = provider or ClaudeCodeProvider()

        # Initialize components
        self.domain_config = get_domain_config(domain)
        self.living_docs = LivingDocs(forge_root)
        self.github = GitHubClient(github_repo)
        self.deployer = ForgeDeployer(domain, project, self.project_dir)
        self.branch_manager = BranchManager(self.project_dir)

        # Initialize security context for bash command validation
        self.security_context = create_forge_security_context(
            domain=domain,
            project=project,
            forge_root=str(forge_root),
        )

        # Session state
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.metrics = SessionMetrics()
        self.issues_completed: list[int] = []
        self.issues_failed: list[int] = []
        self.current_branch: str | None = None

        # Error context for retry sessions (from KiddoRewards lessons)
        # Maps issue_number -> (error_details, fix_suggestions) from last failed verification
        self.retry_context: dict[int, tuple[list[str], list[str]]] = {}

        # Circuit breaker: prevent infinite retries on same error
        # Maps issue_number -> (attempt_count, last_error_signature)
        self.issue_attempts: dict[int, tuple[int, str]] = {}
        self.max_attempts_per_issue = 3  # Skip after 3 attempts with same error

        # Memory flush integration (MoltBot pattern)
        self._memory_monitor = ContextMonitor(threshold=0.7)
        self._memory_manager = MemoryFlushManager(forge_root=forge_root)
        self._estimated_token_usage = 0

    def _log(self, message: str) -> None:
        """Log a message if verbose mode is enabled."""
        if self.verbose:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def _compute_error_signature(self, errors: list[str]) -> str:
        """Compute a signature for error deduplication."""
        import hashlib

        # Sort and join first 5 errors to create stable signature
        error_text = "\n".join(sorted(errors[:5]))
        return hashlib.md5(error_text.encode()).hexdigest()[:8]

    def _should_skip_issue(self, issue_number: int, error_signature: str) -> bool:
        """
        Check if issue should be skipped due to circuit breaker.

        Returns True if:
        - Issue has been attempted max_attempts_per_issue times with same error
        """
        if issue_number not in self.issue_attempts:
            return False

        attempts, last_sig = self.issue_attempts[issue_number]
        if last_sig == error_signature and attempts >= self.max_attempts_per_issue:
            return True
        return False

    def _record_attempt(self, issue_number: int, error_signature: str) -> int:
        """Record an attempt for circuit breaker tracking. Returns new attempt count."""
        if issue_number not in self.issue_attempts:
            self.issue_attempts[issue_number] = (1, error_signature)
            return 1

        attempts, last_sig = self.issue_attempts[issue_number]
        if last_sig == error_signature:
            # Same error, increment counter
            new_attempts = attempts + 1
        else:
            # Different error, reset counter
            new_attempts = 1

        self.issue_attempts[issue_number] = (new_attempts, error_signature)
        return new_attempts

    def _create_security_hook(self):
        """
        Create a security hook function that captures the FORGE security context.

        Returns a callable that validates bash commands against the allowlist
        using the domain-specific security context.
        """
        context = self.security_context

        async def security_hook(
            input_data: dict[str, Any],
            tool_use_id: str | None = None,
            hook_context: Any = None,  # SDK HookContext (ignored, we use our own)
        ) -> dict[str, Any]:
            """Validate bash commands using FORGE security allowlist."""
            return await bash_security_hook(input_data, tool_use_id, context)

        return security_hook

    def _validate_human_gates(self, issue: dict[str, Any]) -> tuple[bool, str | None]:
        """
        Check if issue triggers human gates before starting work.

        Args:
            issue: GitHub issue dict with 'title', 'body', etc.

        Returns:
            Tuple of (can_proceed, gate_reason)
            - (True, None) if issue passes all gates
            - (False, reason) if issue triggers a gate
        """
        # If no gates configured, all issues pass
        if not self.domain_config.human_gates:
            return True, None

        # Gate categories and their trigger keywords
        gate_triggers = {
            "security": [
                "auth",
                "jwt",
                "password",
                "token",
                "encryption",
                "secret",
                "credential",
                "oauth",
                "session",
            ],
            "compliance": [
                "coppa",
                "hipaa",
                "gdpr",
                "age",
                "consent",
                "data protection",
                "privacy",
                "pii",
            ],
            "architecture": ["schema", "migration", "breaking", "api contract", "database"],
            "payments": ["stripe", "payment", "billing", "subscription", "checkout", "refund"],
        }

        # Combine title and body for keyword search
        issue_text = f"{issue.get('title', '')} {issue.get('body', '')}".lower()

        # Check each gate category
        for gate_type, keywords in gate_triggers.items():
            # Check if this gate type is configured for the domain
            gate_configured = any(
                gate_type.lower() in gate.lower() for gate in self.domain_config.human_gates
            )

            if gate_configured:
                for keyword in keywords:
                    if keyword in issue_text:
                        return False, f"Human gate triggered: {gate_type} (keyword: '{keyword}')"

        return True, None

    def _load_context(self) -> dict[str, Any]:
        """Load all context needed for the session."""
        self._log("Loading context...")

        # Load living docs
        docs_context = self.living_docs.consult(self.domain, self.project)

        # Load CLAUDE.md hierarchy
        claude_hierarchy = load_claude_md_hierarchy(self.forge_root, self.domain, self.project)

        # Build context summary
        context = {
            "domain": self.domain,
            "project": self.project,
            "domain_config": {
                "compliance": self.domain_config.compliance,
                "human_gates": self.domain_config.human_gates,
                "frontend_tier": self.domain_config.frontend_tier,
                "localization": self.domain_config.localization,
                "special_rules": self.domain_config.special_rules,
            },
            "current_sprint": docs_context.current_sprint,
            "blockers": docs_context.blockers,
            "priorities": docs_context.priorities,
            "recent_milestones": docs_context.recent_milestones,
            "tech_stack": claude_hierarchy["merged"].get("tech_stack", {}),
        }

        self._log(f"Context loaded. Sprint: {context['current_sprint']}")
        return context

    def _get_next_issue(self) -> dict[str, Any] | None:
        """Get the next issue to work on, prioritized correctly."""
        self._log("Querying GitHub Issues...")

        # Filter by both domain AND project for multi-project domains
        # This ensures agents don't pick up issues from sibling projects
        base_labels = [f"domain:{self.domain}", f"project:{self.project}"]

        # First check for in-progress issues
        in_progress = self.github.list_issues(
            labels=base_labels + ["status:in-progress"],
            limit=1,
        )
        if in_progress:
            self._log(f"Found in-progress issue: #{in_progress[0]['number']}")
            return in_progress[0]

        # Then by priority
        for priority in ["priority:critical", "priority:high", "priority:medium", "priority:low"]:
            issues = self.github.list_issues(
                labels=base_labels + [priority],
                limit=1,
            )
            if issues:
                self._log(f"Found {priority} issue: #{issues[0]['number']}")
                return issues[0]

        # Finally, any open issue for this project
        issues = self.github.list_issues(
            labels=base_labels,
            limit=1,
        )
        if issues:
            return issues[0]

        return None

    def _build_coding_prompt(self, context: dict[str, Any], issue: dict[str, Any]) -> str:
        """Build the coding prompt for LLM Provider."""
        # Read the template
        prompt_path = Path(__file__).parent.parent / "prompts" / "forge_coding.md"
        if prompt_path.exists():
            template = prompt_path.read_text()
        else:
            template = self._get_default_prompt()

        # Get full issue details
        issue_details = self.github.get_issue(issue["number"])

        # Build human gates string
        human_gates_str = (
            "\n".join(f"- {gate}" for gate in self.domain_config.human_gates)
            if self.domain_config.human_gates
            else "- None specific to this domain"
        )

        # Build compliance section
        compliance_items = []
        if self.domain_config.compliance:
            compliance_items.append(
                f"**Required Standards:** {', '.join(self.domain_config.compliance)}"
            )
        if self.domain_config.localization:
            compliance_items.append(f"**Localization:** {self.domain_config.localization}")
        for key, value in self.domain_config.special_rules.items():
            compliance_items.append(f"**{key}:** {value}")
        compliance_section = (
            "\n".join(compliance_items)
            if compliance_items
            else "No special compliance requirements."
        )

        # Build context strings from living docs
        priorities_str = (
            "\n".join(f"- {p}" for p in context.get("priorities", []))
            if context.get("priorities")
            else "- No specific priorities set"
        )

        recent_progress_str = (
            "\n".join(f"- {m}" for m in context.get("recent_milestones", []))
            if context.get("recent_milestones")
            else "- No recent milestones"
        )

        blockers_str = (
            "\n".join(f"- {b}" for b in context.get("blockers", []))
            if context.get("blockers")
            else "- No current blockers"
        )

        # Substitute placeholders
        prompt = template.format(
            domain=self.domain,
            project=self.project,
            session_id=self.session_id,
            frontend_tier=self.domain_config.frontend_tier,
            compliance_section=compliance_section,
            human_gates=human_gates_str,
            current_sprint=context.get("current_sprint", "Unknown"),
            priorities=priorities_str,
            recent_progress=recent_progress_str,
            blockers=blockers_str,
            issue_number=issue["number"],
            issue_title=issue.get("title", "Untitled"),
            date=datetime.now().strftime("%Y-%m-%d"),
        )

        # Add detailed issue context
        prompt += "\n\n## Current Issue Details\n\n"
        prompt += f"**Issue #{issue['number']}**: {issue.get('title', 'Untitled')}\n\n"
        if issue_details.get("body"):
            prompt += f"### Description\n\n{issue_details['body']}\n\n"

        # Add previous error context if this is a retry (from KiddoRewards lessons)
        issue_number = issue["number"]
        if issue_number in self.retry_context:
            error_details, fix_suggestions = self.retry_context[issue_number]
            prompt += "\n\n## ⚠️ PREVIOUS SESSION FAILED VERIFICATION\n\n"
            prompt += "The last attempt failed. **Fix these issues FIRST** before continuing:\n\n"

            if error_details:
                prompt += "### Error Details\n\n"
                for error in error_details[:10]:  # Limit to first 10 errors
                    prompt += f"- {error}\n"
                prompt += "\n"

            if fix_suggestions:
                prompt += "### Suggested Fixes\n\n"
                for suggestion in fix_suggestions:
                    prompt += f"- {suggestion}\n"
                prompt += "\n"

            prompt += "**Address these issues FIRST** before implementing new functionality.\n\n"

        # Add labels
        labels = [label["name"] for label in issue.get("labels", [])]
        if labels:
            prompt += f"**Labels**: {', '.join(labels)}\n\n"

        return prompt

    def _get_default_prompt(self) -> str:
        """Get a minimal default prompt if template not found."""
        return """## YOUR ROLE - FORGE MVP CODER

You are continuing autonomous development on a FORGE portfolio MVP.

### CRITICAL CONTEXT

**Domain:** {domain}
**Project:** {project}
**Issue:** #{issue_number} - {issue_title}
**Session ID:** {session_id}
**Date:** {date}

### TECH STACK (MANDATORY)

| Layer | Standard |
|-------|----------|
| Backend | FastAPI + SQLModel + PostgreSQL |
| Frontend | {frontend_tier} + Vite + TailwindCSS v4 |
| Package Manager | Astral uv (Python) / npm (JS) |
| Testing | pytest (backend) / vitest (frontend) |

### DOMAIN COMPLIANCE

{compliance_section}

### HUMAN GATES (STOP AND ESCALATE)

If you encounter any of these, stop and report:
{human_gates}

### IMPLEMENTATION PROTOCOL

1. Read relevant existing code before changes
2. Make one logical change at a time
3. Add tests for new functionality
4. Run tests: `uv run pytest tests/ -x --tb=short`

### BEGIN IMPLEMENTATION

Start by reading the issue's target files and related tests.
"""

    async def _run_coding_session(
        self,
        prompt: str,
        issue: dict[str, Any],
    ) -> tuple[bool, str]:
        """
        Run a coding session with LLM Provider.

        Returns:
            Tuple of (success, summary)
        """
        self._log(f"Starting coding session for issue #{issue['number']}...")

        # Initialize provider options
        provider_options = ProviderOptions(
            model=self.model,
            cwd=str(self.project_dir),
            allowed_tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
        )

        # For ClaudeCodeProvider, we need to pass security hooks
        if isinstance(self.provider, ClaudeCodeProvider):
            try:
                from claude_agent_sdk.types import HookMatcher

                provider_options.hooks = {
                    "PreToolUse": [
                        HookMatcher(
                            matcher="Bash",
                            hooks=[self._create_security_hook()],
                        ),
                    ],
                }
            except ImportError:
                self._log("Warning: claude-agent-sdk not installed, skipping security hooks.")

        try:
            # Run the session using query function
            summary = ""

            async for message in self.provider.run_coding_session(
                prompt=prompt, options=provider_options
            ):
                # Extract content from message
                if hasattr(message, "content"):
                    content = str(message.content)
                    summary += content + "\n"

            # Check final summary for explicit failure indicators
            # Only check the last portion to avoid false positives from project analysis
            final_section = summary[-1000:].lower()
            failure_indicators = [
                "i cannot complete this task",
                "task failed:",
                "unable to proceed",
                "human review required",
                "stopping due to",
            ]
            success = not any(indicator in final_section for indicator in failure_indicators)

            self._log(f"Session completed. Success: {success}")
            if not success and self.verbose:
                self._log(f"Session summary (last 500 chars): {summary[-500:]}")
            return success, summary[-2000:]  # Truncate long summaries

        except Exception as e:
            self._log(f"Session error: {e}")
            return False, str(e)

    def _run_preflight(self, auto_fix: bool = True) -> PreflightResult:
        """
        Run pre-flight checks before starting work.

        Validates project structure, dependencies, and fixes common issues.
        Based on lessons learned from KiddoRewards session (2025-12-30).

        Args:
            auto_fix: Whether to auto-fix common issues

        Returns:
            PreflightResult with check outcome
        """
        self._log("Running pre-flight checks...")

        checker = PreflightChecker(self.project_dir)
        result = checker.run(auto_fix=auto_fix)

        self._log(f"Pre-flight: {'PASSED' if result.passed else 'FAILED'}")
        if result.fixes_applied:
            for fix in result.fixes_applied:
                self._log(f"  + Auto-fixed: {fix}")
        if result.issues:
            for issue in result.issues:
                self._log(f"  - Issue: {issue}")

        # Track pre-flight metrics
        if self.tracker and (result.fixes_applied or result.issues):
            self.tracker.track_event(
                "preflight_completed",
                {
                    "passed": result.passed,
                    "fixes_count": len(result.fixes_applied),
                    "issues_count": len(result.issues),
                    "fixes": result.fixes_applied[:5],  # First 5 fixes
                    "issues": result.issues[:5],  # First 5 issues
                },
            )

        return result

    def _run_verification(self, checkpoint: str = "") -> VerificationResult:
        """
        Run post-coding verification suite.

        Args:
            checkpoint: Optional checkpoint criteria from the issue

        Returns:
            VerificationResult with test/coverage/lint status
        """
        self._log("Running post-coding verification...")

        verifier = Verifier(
            project_dir=self.project_dir,
            domain_config=self.domain_config,
            skip_quality_gates=self.skip_quality_gates,
        )

        result = verifier.verify(checkpoint=checkpoint)

        self._log(f"Verification: {'PASSED' if result.passed else 'FAILED'}")
        self._log(f"  Tests: {result.tests_passed} passed, {result.tests_failed} failed")
        self._log(
            f"  Coverage: Backend {result.coverage_backend}%, Frontend {result.coverage_frontend}%"
        )
        self._log(f"  Lint errors: {result.lint_errors}")

        return result

    async def run(self) -> AgentResult:
        """
        Run the complete agent session.

        Returns:
            AgentResult with session outcome
        """
        errors: list[str] = []
        deployment_result = None

        # Track session start
        if self.tracker:
            self.tracker.session_started(
                model=self.model,
                is_first_run=not (self.project_dir / ".forge/project.json").exists(),
                max_iterations=self.max_iterations,
            )

        try:
            # Run pre-flight checks before any work (KiddoRewards lesson)
            preflight = self._run_preflight(auto_fix=True)
            if not preflight.passed:
                self._log(f"Pre-flight failed with {len(preflight.issues)} issues")
                for issue in preflight.issues:
                    errors.append(f"Pre-flight: {issue}")

                # Don't abort - continue with issues logged so Claude can fix
                self._log("Continuing despite pre-flight issues...")

            # Load context
            context = self._load_context()

            iteration = 0
            while True:
                # Check iteration limit
                if self.max_iterations and iteration >= self.max_iterations:
                    self._log(f"Reached max iterations ({self.max_iterations})")
                    break

                # Get next issue
                issue = self._get_next_issue()
                if not issue:
                    self._log("No more issues to work on")
                    break

                issue_number = issue["number"]
                issue_title = issue["title"]

                self._log(f"Evaluating issue #{issue_number}: {issue_title}")

                # Validate human gates BEFORE starting any work
                can_proceed, gate_reason = self._validate_human_gates(issue)
                if not can_proceed:
                    self._log(f"[HUMAN GATE] {gate_reason}")

                    # Label issue for human review
                    self.github.update_issue(
                        issue_number,
                        add_labels=["needs-human-review"],
                    )

                    # Track gate trigger
                    if self.tracker:
                        gate_type = (
                            gate_reason.split(":")[0].split()[-1] if gate_reason else "unknown"
                        )
                        self.tracker.human_gate_triggered(gate_type, issue_number)

                    # Skip to next issue
                    iteration += 1
                    continue

                # Circuit breaker: check if we should skip this issue
                if issue_number in self.issue_attempts:
                    attempts, last_sig = self.issue_attempts[issue_number]
                    if attempts >= self.max_attempts_per_issue:
                        self._log(
                            f"[CIRCUIT BREAKER] Skipping issue #{issue_number} after {attempts} failed attempts"
                        )
                        # Mark as blocked and remove in-progress
                        self.github.update_issue(
                            issue_number,
                            remove_labels=["status:in-progress"],
                            add_labels=["status:blocked", "needs-human-review"],
                        )
                        self.issues_failed.append(issue_number)
                        iteration += 1
                        continue

                self._log(f"Working on issue #{issue_number}: {issue_title}")
                self.metrics.issues_attempted += 1

                # Track issue start
                if self.tracker:
                    priority = "medium"
                    for label in issue.get("labels", []):
                        if label["name"].startswith("priority:"):
                            priority = label["name"].split(":")[1]
                    self.tracker.issue_started(issue_number, issue_title, priority)

                # Create feature branch if on protected branch
                if self.branch_manager.is_on_protected_branch():
                    self._log(f"Creating feature branch for issue #{issue_number}")
                    try:
                        self.current_branch = self.branch_manager.create_feature_branch(
                            issue_number=issue_number,
                            issue_title=issue_title,
                            from_remote=True,
                        )
                        self._log(f"Created branch: {self.current_branch}")
                    except Exception as e:
                        self._log(f"Failed to create branch from remote, trying local: {e}")
                        self.current_branch = self.branch_manager.create_feature_branch(
                            issue_number=issue_number,
                            issue_title=issue_title,
                            from_remote=False,
                        )
                        self._log(f"Created local branch: {self.current_branch}")

                # Mark as in-progress
                self.github.update_issue(
                    issue_number,
                    add_labels=["status:in-progress"],
                )

                # Build prompt and run session
                start_time = datetime.now()
                prompt = self._build_coding_prompt(context, issue)
                success, summary = await self._run_coding_session(prompt, issue)

                duration = int((datetime.now() - start_time).total_seconds() / 60)

                if success:
                    self._log("Coding session completed, running verification...")

                    # Extract checkpoint from issue body if available
                    issue_body = issue.get("body", "")
                    checkpoint = ""
                    if "**Checkpoint:**" in issue_body or "## Checkpoint" in issue_body:
                        # Extract checkpoint section
                        for marker in ["**Checkpoint:**", "## Checkpoint"]:
                            if marker in issue_body:
                                checkpoint = issue_body.split(marker)[1].split("---")[0].strip()
                                break

                    # Run verification
                    verification = self._run_verification(checkpoint=checkpoint)

                    # Track verification metrics
                    if self.tracker:
                        self.tracker.verification_completed(
                            issue_number=issue_number,
                            passed=verification.passed,
                            tests_passed=verification.tests_passed,
                            tests_failed=verification.tests_failed,
                            coverage_backend=verification.coverage_backend,
                            coverage_frontend=verification.coverage_frontend,
                            lint_errors=verification.lint_errors,
                        )

                    if verification.passed:
                        self._log(f"Issue #{issue_number} completed successfully")
                        self.issues_completed.append(issue_number)
                        self.metrics.issues_completed += 1

                        # Clear retry context if exists (issue now passed)
                        if issue_number in self.retry_context:
                            del self.retry_context[issue_number]

                        # Close issue
                        self.github.close_issue(issue_number)
                        self.github.update_issue(
                            issue_number,
                            remove_labels=["status:in-progress"],
                        )

                        # Update living docs immediately after each completed issue
                        self._log(f"Updating living docs for issue #{issue_number}...")
                        self.living_docs.update(
                            domain=self.domain,
                            project=self.project,
                            milestone=f"Completed #{issue_number}: {issue_title}",
                            details=f"Session {self.session_id} - Duration: {duration}min - {verification.summary.split(chr(10))[0]}",
                        )

                        # Track completion
                        if self.tracker:
                            self.tracker.issue_completed(issue_number, issue_title, duration)
                    else:
                        # Verification failed - store error context for retry (KiddoRewards lesson)
                        self._log(f"Issue #{issue_number} failed verification")

                        # Circuit breaker: record this attempt
                        error_sig = self._compute_error_signature(
                            verification.error_details or [verification.error_message or "unknown"]
                        )
                        attempt_count = self._record_attempt(issue_number, error_sig)
                        self._log(
                            f"  Attempt {attempt_count}/{self.max_attempts_per_issue} (error sig: {error_sig})"
                        )

                        # Store error context for next retry session
                        self.retry_context[issue_number] = (
                            verification.error_details,
                            verification.fix_suggestions,
                        )

                        if verification.error_details:
                            self._log(
                                f"  Storing {len(verification.error_details)} errors for retry context"
                            )
                            for err in verification.error_details[:3]:  # Log first 3
                                self._log(f"    - {err}")

                        if verification.fix_suggestions:
                            self._log(
                                f"  Storing {len(verification.fix_suggestions)} fix suggestions"
                            )

                        # Only add to failed list once (not on every retry)
                        if issue_number not in self.issues_failed:
                            self.issues_failed.append(issue_number)
                            self.metrics.issues_failed += 1
                        errors.append(
                            f"Issue #{issue_number}: Verification failed - {verification.error_message}"
                        )

                        # Keep status:in-progress so issue gets picked up for retry
                        # No update_issue call needed - just leave the label as-is

                        # Track as failure
                        if self.tracker:
                            self.tracker.issue_failed(
                                issue_number, issue_title, "verification_failed", duration
                            )
                else:
                    self._log(f"Issue #{issue_number} failed")
                    self.issues_failed.append(issue_number)
                    self.metrics.issues_failed += 1
                    errors.append(f"Issue #{issue_number}: {summary[:200]}")

                    # Remove in-progress, add blocked
                    self.github.update_issue(
                        issue_number,
                        remove_labels=["status:in-progress"],
                        add_labels=["status:blocked"],
                    )

                    # Track failure
                    if self.tracker:
                        self.tracker.issue_failed(
                            issue_number, issue_title, "implementation_error", duration
                        )

                iteration += 1

            # Sync living-docs pyramid before handoff
            self._log("Syncing living-docs pyramid...")
            sync_result = self.living_docs.sync(self.domain, self.project)
            if sync_result.get("missing"):
                self._log(f"Warning: Missing docs: {sync_result['missing']}")
            if sync_result.get("stale"):
                self._log(f"Warning: Stale docs: {sync_result['stale']}")

            # Deploy if requested
            if self.deploy and self.issues_completed:
                self._log("Running deployment...")
                deployment_result = self.deployer.full_deploy(
                    skip_quality_gates=self.skip_quality_gates
                )

                if self.tracker:
                    if deployment_result.get("backend", {}).get("success"):
                        self.tracker.deployment_triggered("railway", True)
                    if deployment_result.get("frontend", {}).get("success"):
                        self.tracker.deployment_triggered("cloudflare", True)

            # Track session end
            if self.tracker:
                end_reason = "completed"
                if self.max_iterations and iteration >= self.max_iterations:
                    end_reason = "max_iterations"
                elif errors:
                    end_reason = "errors"

                self.tracker.session_ended(self.metrics, end_reason)

            return AgentResult(
                success=len(errors) == 0,
                session_id=self.session_id,
                issues_completed=self.issues_completed,
                issues_failed=self.issues_failed,
                metrics=self.metrics,
                deployment_result=deployment_result,
                errors=errors,
            )

        except Exception as e:
            self._log(f"Session failed: {e}")
            errors.append(str(e))

            if self.tracker:
                self.tracker.error_occurred("session_error", str(e))
                self.tracker.session_ended(self.metrics, "error")

            return AgentResult(
                success=False,
                session_id=self.session_id,
                issues_completed=self.issues_completed,
                issues_failed=self.issues_failed,
                metrics=self.metrics,
                deployment_result=None,
                errors=errors,
            )


class FeatureOrchestrator:
    """
    Orchestrator for implementing features from features.json using Claude Code SDK.

    Bridges the Ralph Loop with Claude Code SDK to enable autonomous feature
    implementation. Designed to work with the RalphLoopHarness.

    Usage with Ralph Loop:
        orchestrator = FeatureOrchestrator(
            working_dir=Path("/path/to/project"),
            model="claude-sonnet-4-20250514",
        )
        loop = create_ralph_loop(
            features_path="features.json",
            orchestrator=orchestrator,
        )
        await loop.run()
    """

    def __init__(
        self,
        working_dir: Path,
        model: str = "claude-sonnet-4-20250514",
        max_iterations: int = 50,
        context: str | None = None,
        provider: LLMProvider | None = None,
    ):
        """Initialize the feature orchestrator.

        Args:
            working_dir: Working directory for the project
            model: Claude model to use
            max_iterations: Max iterations per feature implementation
            context: Optional context to include in prompts (e.g., CLAUDE.md contents)
            provider: Optional LLM provider (defaults to ClaudeCodeProvider)
        """
        self.working_dir = working_dir
        self.model = model
        self.max_iterations = max_iterations
        self.context = context or ""
        self.provider = provider or ClaudeCodeProvider()

    def _build_prompt(self, feature: Any) -> str:
        """Build a prompt for implementing a feature.

        Args:
            feature: FeatureSpec from Ralph Loop

        Returns:
            Prompt string for Claude Code SDK
        """
        # Build acceptance criteria list
        criteria = "\n".join(f"- {c}" for c in feature.acceptance_criteria)

        # Build files to create list (if available)
        files_to_create = ""
        if hasattr(feature, "files_to_create") and feature.files_to_create:
            files = "\n".join(f"- {f}" for f in feature.files_to_create)
            files_to_create = f"\n\n## Files to Create\n{files}"

        # Build files to modify list (if available)
        files_to_modify = ""
        if hasattr(feature, "files_to_modify") and feature.files_to_modify:
            files = "\n".join(f"- {f}" for f in feature.files_to_modify)
            files_to_modify = f"\n\n## Files to Modify\n{files}"

        prompt = f"""# Implement Feature: {feature.name}

## Feature ID
{feature.id}

## Description
{feature.description}

## Acceptance Criteria
{criteria}{files_to_create}{files_to_modify}

## Instructions
1. Implement all acceptance criteria
2. Write clean, well-documented code
3. Follow existing code patterns in the project
4. Ensure all acceptance criteria are met before completing

{self.context}
"""
        return prompt

    async def implement_feature(
        self, feature: Any, timeout_seconds: int = 600
    ) -> tuple[bool, str | None]:
        """Implement a single feature using LLM Provider.

        Args:
            feature: FeatureSpec from Ralph Loop
            timeout_seconds: Timeout for feature implementation (default 10 min)

        Returns:
            Tuple of (success, error_message)
        """
        import asyncio

        prompt = self._build_prompt(feature)

        try:
            # Create options for LLM Provider
            options = ProviderOptions(
                model=self.model,
                cwd=str(self.working_dir),
                system_prompt=f"""You are implementing feature {feature.id} for the project.
Focus on completing the acceptance criteria efficiently.
Do not add unnecessary features or refactoring beyond the scope.
{self.context}""",
                allowed_tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
            )

            # Run Coding Session via Provider
            async def run_query() -> str:
                summary = ""
                async for message in self.provider.run_coding_session(
                    prompt=prompt, options=options
                ):
                    # Extract content from message
                    if hasattr(message, "content"):
                        content = str(message.content)
                        summary += content + "\n"
                return summary

            summary = await asyncio.wait_for(run_query(), timeout=timeout_seconds)

            # Check final summary for failure indicators
            final_section = summary[-1000:].lower() if summary else ""
            failure_indicators = [
                "i cannot complete this task",
                "unable to implement",
                "failed to create",
                "error occurred",
            ]
            for indicator in failure_indicators:
                if indicator in final_section:
                    return False, f"Orchestrator reported: {indicator}"

            return True, None

        except TimeoutError:
            return False, f"Feature implementation timed out after {timeout_seconds}s"
        except Exception as e:
            return False, str(e)
