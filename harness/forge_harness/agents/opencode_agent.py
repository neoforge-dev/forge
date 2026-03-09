"""
OpenCode CLI Agent Wrapper
==========================

Wraps the OpenCode CLI tool for programmatic execution.

Usage:
    from forge_harness.agents import OpenCodeAgent, OpenCodeConfig

    config = OpenCodeConfig(model="cerebras/qwen-3-32b", timeout=600)
    agent = OpenCodeAgent(config)

    result = await agent.execute(
        prompt="Fix the authentication bug",
        working_dir=Path("/path/to/project"),
    )

    if result.success:
        print(f"Output: {result.output}")
    else:
        print(f"Error: {result.error}")
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from ..logging_config import get_logger

logger = get_logger(__name__)


class AgentStatus(str, Enum):
    """Status of agent execution."""

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    def __str__(self) -> str:
        return self.value


@dataclass
class OpenCodeConfig:
    """Configuration for OpenCode agent.

    Attributes:
        model: Model to use (default: cerebras/qwen-3-32b)
        timeout: Execution timeout in seconds (default: 600)
        continue_session: Whether to continue the last session
        session_id: Specific session ID to resume
        agent_name: Named agent to use
        env_vars: Additional environment variables
    """

    model: str = "cerebras/qwen-3-32b"
    timeout: int = 600
    continue_session: bool = False
    session_id: str | None = None
    agent_name: str | None = None
    env_vars: dict[str, str] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Result of agent execution.

    Attributes:
        status: Execution status
        output: Captured stdout output
        error: Captured stderr output or error message
        exit_code: Process exit code
        duration: Execution time in seconds
        started_at: When execution started
        completed_at: When execution completed
        command: The command that was executed
        working_dir: Directory where execution occurred
    """

    status: AgentStatus
    output: str = ""
    error: str = ""
    exit_code: int = -1
    duration: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    command: list[str] = field(default_factory=list)
    working_dir: str | None = None

    @property
    def success(self) -> bool:
        """True if execution succeeded."""
        return self.status == AgentStatus.SUCCESS

    @property
    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [f"Status: {self.status.value}"]
        lines.append(f"Exit Code: {self.exit_code}")
        lines.append(f"Duration: {self.duration:.1f}s")
        if self.output:
            preview = self.output[-500:] if len(self.output) > 500 else self.output
            lines.append(f"Output: {preview}")
        if self.error:
            lines.append(f"Error: {self.error}")
        return "\n".join(lines)


class OpenCodeAgent:
    """Wrapper for OpenCode CLI tool.

    Provides async execution of OpenCode prompts with proper
    subprocess handling, timeout management, and result capture.

    Example:
        agent = OpenCodeAgent()
        result = await agent.execute("Fix the bug", Path("/project"))
        print(result.summary)
    """

    EXECUTABLE = "opencode"

    def __init__(self, config: OpenCodeConfig | None = None):
        """Initialize OpenCode agent.

        Args:
            config: Agent configuration. Uses defaults if not provided.
        """
        self.config = config or OpenCodeConfig()
        self._process: asyncio.subprocess.Process | None = None

    def _build_command(self, prompt: str) -> list[str]:
        """Build the OpenCode command.

        Args:
            prompt: Task prompt

        Returns:
            Command list for subprocess
        """
        cmd = [self.EXECUTABLE]

        # Model selection
        if self.config.model:
            cmd.extend(["-m", self.config.model])

        # Session management
        if self.config.session_id:
            cmd.extend(["-s", self.config.session_id])
        elif self.config.continue_session:
            cmd.append("-c")

        # Agent mode
        if self.config.agent_name:
            cmd.extend(["--agent", self.config.agent_name])

        # Run subcommand with prompt
        cmd.append("run")
        cmd.append(prompt)

        return cmd

    async def execute(
        self,
        prompt: str,
        working_dir: Path,
        *,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> AgentResult:
        """Execute OpenCode with the given prompt.

        Args:
            prompt: Task prompt to execute
            working_dir: Working directory for execution
            timeout: Override default timeout (seconds)
            env: Additional environment variables

        Returns:
            AgentResult with execution outcome
        """
        started_at = datetime.now()
        start_time = time.time()
        timeout_seconds = timeout or self.config.timeout

        # Build command
        cmd = self._build_command(prompt)

        # Merge environment variables
        import os

        process_env = os.environ.copy()
        process_env.update(self.config.env_vars)
        if env:
            process_env.update(env)

        logger.info(
            "Executing OpenCode",
            extra={
                "command": " ".join(cmd),
                "working_dir": str(working_dir),
                "timeout": timeout_seconds,
            },
        )

        try:
            # Create subprocess
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                env=process_env,
            )

            # Wait for completion with timeout
            try:
                stdout, stderr = await asyncio.wait_for(
                    self._process.communicate(),
                    timeout=timeout_seconds,
                )

                duration = time.time() - start_time
                completed_at = datetime.now()
                exit_code = self._process.returncode or 0

                # Determine status
                if exit_code == 0:
                    status = AgentStatus.SUCCESS
                else:
                    status = AgentStatus.FAILED

                logger.info(
                    "OpenCode completed",
                    extra={
                        "status": status.value,
                        "exit_code": exit_code,
                        "duration": duration,
                    },
                )

                return AgentResult(
                    status=status,
                    output=stdout.decode("utf-8", errors="replace"),
                    error=stderr.decode("utf-8", errors="replace"),
                    exit_code=exit_code,
                    duration=duration,
                    started_at=started_at,
                    completed_at=completed_at,
                    command=cmd,
                    working_dir=str(working_dir),
                )

            except TimeoutError:
                # Kill the process
                self._process.kill()
                await self._process.wait()

                duration = time.time() - start_time
                completed_at = datetime.now()

                logger.warning(
                    f"OpenCode timed out after {timeout_seconds}s",
                    extra={"duration": duration},
                )

                return AgentResult(
                    status=AgentStatus.TIMEOUT,
                    output="",
                    error=f"Execution timed out after {timeout_seconds} seconds",
                    exit_code=-1,
                    duration=duration,
                    started_at=started_at,
                    completed_at=completed_at,
                    command=cmd,
                    working_dir=str(working_dir),
                )

        except FileNotFoundError:
            duration = time.time() - start_time

            logger.error("OpenCode executable not found")

            return AgentResult(
                status=AgentStatus.FAILED,
                output="",
                error="OpenCode executable not found. Is 'opencode' installed and in PATH?",
                exit_code=-1,
                duration=duration,
                started_at=started_at,
                completed_at=datetime.now(),
                command=cmd,
                working_dir=str(working_dir),
            )

        except Exception as e:
            duration = time.time() - start_time

            logger.exception(f"OpenCode execution failed: {e}")

            return AgentResult(
                status=AgentStatus.FAILED,
                output="",
                error=str(e),
                exit_code=-1,
                duration=duration,
                started_at=started_at,
                completed_at=datetime.now(),
                command=cmd,
                working_dir=str(working_dir),
            )
        finally:
            self._process = None

    async def cancel(self) -> bool:
        """Cancel the running execution.

        Returns:
            True if cancellation was successful
        """
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
                logger.info("OpenCode execution cancelled")
                return True
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
                logger.warning("OpenCode execution force killed")
                return True
            except Exception as e:
                logger.error(f"Failed to cancel OpenCode: {e}")
                return False
        return False

    def execute_sync(
        self,
        prompt: str,
        working_dir: Path,
        *,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> AgentResult:
        """Synchronous wrapper for execute().

        Args:
            prompt: Task prompt to execute
            working_dir: Working directory for execution
            timeout: Override default timeout (seconds)
            env: Additional environment variables

        Returns:
            AgentResult with execution outcome
        """
        return asyncio.run(self.execute(prompt, working_dir, timeout=timeout, env=env))


# Convenience function
def create_opencode_agent(
    model: str = "cerebras/qwen-3-32b",
    timeout: int = 600,
) -> OpenCodeAgent:
    """Create an OpenCode agent with common settings.

    Args:
        model: Model to use
        timeout: Execution timeout in seconds

    Returns:
        Configured OpenCodeAgent
    """
    config = OpenCodeConfig(model=model, timeout=timeout)
    return OpenCodeAgent(config)
