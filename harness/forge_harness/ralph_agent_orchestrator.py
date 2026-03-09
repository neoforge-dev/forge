"""
Ralph Loop Agent Orchestrator
=============================

Integrates Ralph Loop with Claude for autonomous feature implementation.

This orchestrator:
1. Takes a FeatureSpec from the Ralph Loop
2. Generates a prompt with context
3. Calls Claude Code to implement the feature
4. Returns success/failure based on implementation

Usage:
    from forge_harness.ralph_agent_orchestrator import RalphAgentOrchestrator

    orchestrator = RalphAgentOrchestrator(
        project_path=Path("/path/to/project"),
        claude_model="sonnet",
    )

    loop = RalphLoopHarness(
        feature_store=store,
        config=config,
        orchestrator=orchestrator,
    )
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .logging_config import get_logger
from .ralph_loop import FeatureSpec
from .token_budget import create_tracker_from_env

logger = get_logger(__name__)


@dataclass
class AgentConfig:
    """Configuration for the agent orchestrator."""

    project_path: Path
    """Path to the project being worked on."""

    claude_model: str = "sonnet"
    """Claude model to use (sonnet, opus, haiku)."""

    max_tokens: int = 16000
    """Maximum tokens for response."""

    timeout_seconds: int = 600
    """Timeout for agent execution."""

    context_files: list[str] | None = None
    """Additional files to include in context."""

    test_files: list[Path] | None = None
    """Test files to include in prompt for context."""


class RalphAgentOrchestrator:
    """Orchestrator that uses Claude Code to implement features."""

    def __init__(self, config: AgentConfig):
        """Initialize the orchestrator.

        Args:
            config: Agent configuration
        """
        self.config = config
        self._implementation_count = 0
        self.token_tracker = create_tracker_from_env()

    def _extract_test_content(self, test_names: list[str]) -> str:
        """Extract relevant test methods from test files.

        Args:
            test_names: Names of tests to extract

        Returns:
            Test content as string
        """
        if not self.config.test_files:
            return ""

        test_content_parts = []
        for test_file in self.config.test_files:
            if not test_file.exists():
                continue

            content = test_file.read_text()
            lines = content.split("\n")

            # Find test methods that match the test names
            for test_name in test_names:
                # Look for class or method matching the test name
                in_relevant_section = False
                section_lines = []
                indent_level = 0

                for i, line in enumerate(lines):
                    # Check if this line starts a relevant test class
                    if "class Test" in line and any(
                        t in line.lower() for t in test_name.lower().split("_")
                    ):
                        in_relevant_section = True
                        section_lines = [line]
                        indent_level = len(line) - len(line.lstrip())
                        continue

                    # Check if this line starts the specific test method
                    if f"def {test_name}" in line:
                        in_relevant_section = True
                        # Include class definition if we're in a class
                        section_lines = [line]
                        indent_level = len(line) - len(line.lstrip())
                        continue

                    if in_relevant_section:
                        current_indent = (
                            len(line) - len(line.lstrip()) if line.strip() else indent_level + 1
                        )
                        if (
                            line.strip()
                            and current_indent <= indent_level
                            and not line.strip().startswith("#")
                        ):
                            # End of section
                            break
                        section_lines.append(line)

                if section_lines:
                    test_content_parts.append(f"# From {test_file.name}:")
                    test_content_parts.append("\n".join(section_lines))
                    test_content_parts.append("")

        return "\n".join(test_content_parts)

    def _build_prompt(self, feature: FeatureSpec) -> str:
        """Build implementation prompt for a feature.

        Args:
            feature: Feature to implement

        Returns:
            Prompt string for Claude
        """
        prompt_parts = [
            f"# Implement Feature: {feature.name}",
            "",
            f"**Feature ID:** {feature.id}",
            f"**Priority:** {feature.priority}",
            "",
            "## Description",
            feature.description,
            "",
            "## Acceptance Criteria",
        ]

        for criterion in feature.acceptance_criteria:
            prompt_parts.append(f"- {criterion}")

        prompt_parts.extend(
            [
                "",
                "## Tests to Pass",
                "The following tests must pass after implementation:",
            ]
        )

        for test in feature.tests:
            prompt_parts.append(f"- `{test}`")

        # Add actual test content so Claude knows exact import paths
        test_content = self._extract_test_content(feature.tests)
        if test_content:
            prompt_parts.extend(
                [
                    "",
                    "## Test Code (IMPORTANT: Follow these exact import paths)",
                    "",
                    "```python",
                    test_content,
                    "```",
                    "",
                    "**CRITICAL:** The tests above show the EXACT import paths you must use.",
                    "Create files and functions at those exact locations.",
                ]
            )

        prompt_parts.extend(
            [
                "",
                "## Instructions",
                "",
                "1. Read the test code above to understand EXACTLY what imports/paths are expected",
                "2. Create the modules/functions at the EXACT paths the tests expect",
                "3. Follow existing code patterns in the project",
                "4. Ensure all acceptance criteria are met",
                "5. Do NOT commit changes - just implement",
                "",
                "Focus on making the specified tests pass by creating code at the exact import paths shown.",
            ]
        )

        return "\n".join(prompt_parts)

    async def implement_feature(self, feature: FeatureSpec) -> tuple[bool, str | None]:
        """Implement a feature using Claude Code.

        Args:
            feature: Feature to implement

        Returns:
            Tuple of (success, error_message)
        """
        self._implementation_count += 1

        # Check budget before proceeding
        agent_id = "claude" # Base identifier for budget tracking
        if self.token_tracker.should_handoff(agent_id):
            status = self.token_tracker.get_status(agent_id)
            logger.warning(f"Token budget threshold reached: {status['recommendation']}")

        prompt = self._build_prompt(feature)

        logger.info(f"Implementing {feature.id} via Claude Code")
        logger.debug(f"Prompt:\n{prompt}")

        try:
            # Estimate tokens for the prompt
            estimated_tokens = len(prompt) // 4
            logger.debug(f"Estimated prompt tokens: {estimated_tokens:,}")

            # Call Claude Code CLI
            result = await self._call_claude(prompt)

            if result["success"]:
                logger.info(f"Successfully implemented {feature.id}")

                # Record usage - estimating 50K tokens per successful marathon output if not reported
                # Kimi hit rate limits after 5 outputs with ~40K each
                usage = result.get("usage", {})
                tokens_used = usage.get("total_tokens", 50000)
                status = self.token_tracker.record_output(agent_id, tokens_used)

                if status["should_handoff"]:
                    logger.warning(f"Post-output budget check: {status['recommendation']}")

                return True, None
            else:
                error = result.get("error", "Unknown error")
                logger.warning(f"Failed to implement {feature.id}: {error}")
                return False, error

        except Exception as e:
            logger.error(f"Agent error for {feature.id}: {e}")
            return False, str(e)

    async def _call_claude(self, prompt: str) -> dict[str, Any]:
        """Call Claude Code CLI with the prompt.

        Args:
            prompt: Implementation prompt

        Returns:
            Result dict with success and optional error
        """
        # Build command - use -p for prompt mode (allows file writes)
        cmd = [
            "claude",
            "-p",
            prompt,  # Pass prompt directly
            "--model",
            self.config.claude_model,
            "--max-turns",
            "10",  # Limit iterations
            "--allowedTools",
            "Edit,Write,Read,Glob,Grep,Bash",  # Allow file operations
        ]

        # Add project path as working directory context
        cwd = str(self.config.project_path)

        try:
            # Run claude with prompt passed via -p flag
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config.timeout_seconds,
            )

            output = stdout.decode("utf-8")
            errors = stderr.decode("utf-8")

            if proc.returncode == 0:
                return {"success": True, "output": output}
            else:
                return {
                    "success": False,
                    "error": errors or output or f"Exit code {proc.returncode}",
                }

        except TimeoutError:
            return {"success": False, "error": f"Timed out after {self.config.timeout_seconds}s"}
        except FileNotFoundError:
            return {
                "success": False,
                "error": "Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


class MockAgentOrchestrator:
    """Mock orchestrator for testing without calling Claude."""

    def __init__(self, success_rate: float = 1.0):
        """Initialize mock orchestrator.

        Args:
            success_rate: Probability of success (0.0 to 1.0)
        """
        self.success_rate = success_rate
        self._implementation_count = 0

    async def implement_feature(self, feature: FeatureSpec) -> tuple[bool, str | None]:
        """Mock implementation.

        Args:
            feature: Feature to implement

        Returns:
            Tuple of (success, error_message)
        """
        import random

        self._implementation_count += 1
        logger.info(f"[Mock] Implementing {feature.id}")

        # Simulate some work
        await asyncio.sleep(0.1)

        if random.random() < self.success_rate:
            return True, None
        else:
            return False, "Mock implementation failed (simulated failure)"


def create_agent_orchestrator(
    project_path: Path | str,
    model: str = "sonnet",
    timeout: int = 600,
) -> RalphAgentOrchestrator:
    """Create an agent orchestrator for Ralph Loop.

    Args:
        project_path: Path to project
        model: Claude model to use
        timeout: Timeout in seconds

    Returns:
        Configured orchestrator
    """
    config = AgentConfig(
        project_path=Path(project_path),
        claude_model=model,
        timeout_seconds=timeout,
    )
    return RalphAgentOrchestrator(config)
