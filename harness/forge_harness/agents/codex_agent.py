"""Codex CLI Agent Integration.

Provides Python wrapper for the Codex CLI tool.

Codex is a command-line AI coding assistant from OpenAI.
This module provides a Python interface for programmatic execution.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge_harness.logging_config import get_logger


@dataclass
class AgentResult:
    """Result from agent execution.

    Attributes:
        success: Whether execution succeeded
        output: Agent output/response
        duration_seconds: Execution time
        error: Error message if failed
        exit_code: Process exit code
        files_modified: List of files changed
        tokens_used: Estimated tokens (if available)
        model: Model used
    """

    success: bool
    output: str
    duration_seconds: float
    error: str | None = None
    exit_code: int = 0
    files_modified: list[str] = field(default_factory=list)
    tokens_used: int = 0
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "success": self.success,
            "output": self.output,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "exit_code": self.exit_code,
            "files_modified": self.files_modified,
            "tokens_used": self.tokens_used,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentResult:
        """Deserialize from dictionary."""
        return cls(
            success=data["success"],
            output=data["output"],
            duration_seconds=data["duration_seconds"],
            error=data.get("error"),
            exit_code=data.get("exit_code", 0),
            files_modified=data.get("files_modified", []),
            tokens_used=data.get("tokens_used", 0),
            model=data.get("model", ""),
        )


@dataclass
class CodexConfig:
    """Configuration for Codex agent.

    Attributes:
        timeout: Max execution time in seconds (default 300)
        model: Model to use (default "gpt-4o")
        auto_approve: Auto-approve actions (default True)
        quiet: Suppress verbose output (default False)
        json_output: Request JSON output (default False)
    """

    timeout: float = 300.0
    model: str = "gpt-4o"
    auto_approve: bool = True
    quiet: bool = False
    json_output: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "timeout": self.timeout,
            "model": self.model,
            "auto_approve": self.auto_approve,
            "quiet": self.quiet,
            "json_output": self.json_output,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodexConfig:
        """Deserialize from dictionary."""
        return cls(
            timeout=data.get("timeout", 300.0),
            model=data.get("model", "gpt-4o"),
            auto_approve=data.get("auto_approve", True),
            quiet=data.get("quiet", False),
            json_output=data.get("json_output", False),
        )


class CodexAgent:
    """Wrapper for the Codex CLI tool.

    Codex is a command-line AI coding assistant. This class provides
    a Python interface for programmatic execution.

    CLI Reference (codex exec):
        codex exec "prompt"                    # Execute prompt
        codex exec -m gpt-4o "prompt"          # With model
        codex exec -y "prompt"                 # Auto-approve
        codex exec --quiet "prompt"            # Quiet mode
        codex exec --json "prompt"             # JSON output

    Usage:
        agent = CodexAgent()
        result = await agent.execute("Implement feature X", working_dir=Path("."))

        # With custom config
        config = CodexConfig(timeout=600, model="gpt-4-turbo")
        agent = CodexAgent(config=config)
        result = await agent.execute("Add tests for module Y")
    """

    EXECUTABLE = "codex"

    def __init__(
        self,
        config: CodexConfig | None = None,
        logger: logging.Logger | None = None,
    ):
        """Initialize Codex agent.

        Args:
            config: Agent configuration (uses defaults if None)
            logger: Logger instance (creates new if None)
        """
        self.config = config or CodexConfig()
        self.logger = logger or get_logger(__name__)

    def build_command(
        self,
        prompt: str,
        working_dir: Path | None = None,
        **kwargs,
    ) -> list[str]:
        """Build codex exec command.

        Args:
            prompt: Task prompt
            working_dir: Working directory (not used in command, but for cwd)
            **kwargs: Override config options

        Returns:
            Command list for subprocess
        """
        cmd = [self.EXECUTABLE, "exec"]

        # Model selection
        model = kwargs.get("model", self.config.model)
        if model:
            cmd.extend(["-m", model])

        # Auto-approve
        if kwargs.get("auto_approve", self.config.auto_approve):
            cmd.append("-y")

        # Quiet mode
        if kwargs.get("quiet", self.config.quiet):
            cmd.append("--quiet")

        # JSON output
        if kwargs.get("json_output", self.config.json_output):
            cmd.append("--json")

        # Prompt (positional)
        cmd.append(prompt)

        return cmd

    async def execute(
        self,
        prompt: str,
        working_dir: Path | None = None,
        **kwargs,
    ) -> AgentResult:
        """Execute a prompt using Codex.

        Args:
            prompt: Task prompt/instructions
            working_dir: Working directory for execution
            **kwargs: Override config options

        Returns:
            AgentResult with execution outcome
        """
        start_time = time.time()

        cmd = self.build_command(prompt, working_dir, **kwargs)
        timeout = kwargs.get("timeout", self.config.timeout)

        self.logger.info(f"Executing codex: {' '.join(cmd[:3])}...")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(working_dir) if working_dir else None,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                duration = time.time() - start_time
                self.logger.error(f"Codex execution timed out after {timeout}s")
                return AgentResult(
                    success=False,
                    output="",
                    duration_seconds=duration,
                    error=f"Execution timed out after {timeout}s",
                    exit_code=-1,
                )

            duration = time.time() - start_time
            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")

            output, files_modified = self._parse_output(stdout_text, stderr_text)

            success = process.returncode == 0
            if success:
                self.logger.info(
                    f"Codex execution completed in {duration:.2f}s",
                    extra={"files_modified": len(files_modified)},
                )
            else:
                self.logger.error(
                    f"Codex execution failed with exit code {process.returncode}",
                    extra={"duration": duration},
                )

            return AgentResult(
                success=success,
                output=output,
                duration_seconds=duration,
                error=stderr_text if process.returncode != 0 else None,
                exit_code=process.returncode or 0,
                files_modified=files_modified,
                model=kwargs.get("model", self.config.model),
            )

        except FileNotFoundError:
            duration = time.time() - start_time
            self.logger.error("Codex executable not found")
            return AgentResult(
                success=False,
                output="",
                duration_seconds=duration,
                error="codex executable not found. Install with: npm install -g @openai/codex",
                exit_code=-1,
            )
        except Exception as e:
            duration = time.time() - start_time
            self.logger.error(f"Unexpected error during codex execution: {e}")
            return AgentResult(
                success=False,
                output="",
                duration_seconds=duration,
                error=f"Unexpected error: {str(e)}",
                exit_code=-1,
            )

    def execute_sync(
        self,
        prompt: str,
        working_dir: Path | None = None,
        **kwargs,
    ) -> AgentResult:
        """Synchronous execution wrapper.

        Convenience method for non-async contexts.

        Args:
            prompt: Task prompt/instructions
            working_dir: Working directory for execution
            **kwargs: Override config options

        Returns:
            AgentResult with execution outcome
        """
        return asyncio.run(self.execute(prompt, working_dir, **kwargs))

    def _parse_output(self, stdout: str, stderr: str) -> tuple[str, list[str]]:
        """Parse codex output for content and modified files.

        Args:
            stdout: Standard output from codex
            stderr: Standard error from codex

        Returns:
            Tuple of (output_content, files_modified)
        """
        # Combine stdout and stderr for full output
        full_output = stdout
        if stderr and not stderr.strip().startswith("Error"):
            full_output = f"{stdout}\n{stderr}" if stdout else stderr

        # Look for file modification patterns
        files_modified = []

        # Pattern 1: "Modified: path/to/file.py"
        modified_pattern = re.compile(r"Modified:\s+(.+?)(?:\n|$)", re.MULTILINE)
        files_modified.extend(modified_pattern.findall(full_output))

        # Pattern 2: "Created: path/to/file.py"
        created_pattern = re.compile(r"Created:\s+(.+?)(?:\n|$)", re.MULTILINE)
        files_modified.extend(created_pattern.findall(full_output))

        # Pattern 3: "Edited path/to/file.py"
        edited_pattern = re.compile(r"Edited\s+(.+?)(?:\n|$)", re.MULTILINE)
        files_modified.extend(edited_pattern.findall(full_output))

        # Pattern 4: "Writing to path/to/file.py"
        writing_pattern = re.compile(r"Writing to\s+(.+?)(?:\n|$)", re.MULTILINE)
        files_modified.extend(writing_pattern.findall(full_output))

        # Clean up file paths
        files_modified = [f.strip() for f in files_modified]
        files_modified = list(dict.fromkeys(files_modified))  # Remove duplicates, preserve order

        return full_output.strip(), files_modified

    @staticmethod
    def is_available() -> bool:
        """Check if codex CLI is installed and available.

        Returns:
            True if codex is installed, False otherwise
        """
        try:
            result = subprocess.run(
                ["codex", "--version"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


def create_codex_agent(
    timeout: float = 300.0,
    model: str = "gpt-4o",
    auto_approve: bool = True,
    **kwargs,
) -> CodexAgent:
    """Create a configured CodexAgent instance.

    Factory function for convenience.

    Args:
        timeout: Max execution time in seconds
        model: Model to use
        auto_approve: Auto-approve actions
        **kwargs: Additional config options

    Returns:
        Configured CodexAgent instance
    """
    config = CodexConfig(
        timeout=timeout,
        model=model,
        auto_approve=auto_approve,
        quiet=kwargs.get("quiet", False),
        json_output=kwargs.get("json_output", False),
    )
    return CodexAgent(config=config)
