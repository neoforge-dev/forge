"""OpenCode CLI integration for FORGE harness."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class TestResults:
    """Parsed test results from pytest output."""

    passed: int
    failed: int
    skipped: int
    total: int
    failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OpenCodeResult:
    """Result from OpenCode CLI execution."""

    prompt: str
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    timed_out: bool
    success: bool
    files_modified: list[str] = field(default_factory=list)
    test_results: TestResults | None = None
    error: str | None = None


class OpenCodeExecutor:
    """Execute OpenCode CLI tasks via `opencode run` and capture results."""

    def __init__(self, timeout_seconds: int = 300) -> None:
        """Initialize OpenCode executor.

        Args:
            timeout_seconds: Default timeout for command execution
        """
        self.timeout_seconds = timeout_seconds

    def execute(
        self,
        prompt: str,
        cwd: str | Path | None = None,
        timeout_seconds: int | None = None,
        env: dict[str, str] | None = None,
        headless: bool = True,
        model: str | None = None,
        session_id: str | None = None,
        agent: str | None = None,
    ) -> OpenCodeResult:
        """Run `opencode run` with a prompt and return structured results.

        Args:
            prompt: Prompt to pass to OpenCode CLI
            cwd: Working directory
            timeout_seconds: Override timeout (default: 300 seconds)
            env: Optional environment overrides
            headless: If True, stdin is DEVNULL (non-interactive)
            model: Model name (e.g., 'anthropic/claude-sonnet-4')
            session_id: Continue specific session
            agent: Named agent to use

        Returns:
            OpenCodeResult with execution details and parsed output

        Raises:
            ValueError: If prompt is empty
        """
        if not prompt:
            raise ValueError("prompt is required")

        working_dir = Path(cwd) if cwd else Path.cwd()
        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds

        command = ["opencode"]

        # Model selection
        if model:
            command.extend(["-m", model])

        # Session management
        if session_id:
            command.extend(["-s", session_id])

        # Agent mode
        if agent:
            command.extend(["--agent", agent])

        # Non-interactive run subcommand
        command.extend(["run", prompt])

        start = time.monotonic()

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        stdin = subprocess.DEVNULL if headless else None

        logger.info(
            "Executing OpenCode command",
            extra={
                "command": " ".join(command),
                "cwd": str(working_dir),
                "timeout": timeout,
            },
        )

        try:
            result = subprocess.run(
                command,
                cwd=str(working_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=merged_env,
                stdin=stdin,
            )
            duration = time.monotonic() - start
            success = result.returncode == 0

            stdout = result.stdout or ""
            stderr = result.stderr or ""

            # Parse output for files modified and test results
            files_modified = self._parse_files_modified(stdout)
            test_results = self._parse_test_results(stdout)

            logger.info(
                "OpenCode execution completed",
                extra={
                    "success": success,
                    "duration": duration,
                    "exit_code": result.returncode,
                    "files_modified_count": len(files_modified),
                    "has_test_results": test_results is not None,
                },
            )

            return OpenCodeResult(
                prompt=prompt,
                stdout=stdout,
                stderr=stderr,
                exit_code=result.returncode,
                duration_seconds=duration,
                timed_out=False,
                success=success,
                files_modified=files_modified,
                test_results=test_results,
                error=None if success else "nonzero_exit",
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")

            logger.warning(
                "OpenCode execution timed out",
                extra={"duration": duration, "timeout": timeout},
            )

            return OpenCodeResult(
                prompt=prompt,
                stdout=stdout,
                stderr=stderr,
                exit_code=-1,
                duration_seconds=duration,
                timed_out=True,
                success=False,
                files_modified=self._parse_files_modified(stdout),
                test_results=self._parse_test_results(stdout),
                error="timeout",
            )
        except FileNotFoundError as exc:
            duration = time.monotonic() - start

            logger.error(
                "OpenCode CLI not found",
                extra={"error": str(exc), "duration": duration},
            )

            return OpenCodeResult(
                prompt=prompt,
                stdout="",
                stderr=str(exc),
                exit_code=-1,
                duration_seconds=duration,
                timed_out=False,
                success=False,
                files_modified=[],
                test_results=None,
                error="opencode_not_found",
            )

    async def stream_execute(
        self,
        prompt: str,
        cwd: str | Path | None = None,
        timeout_seconds: int | None = None,
        env: dict[str, str] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream output from `opencode run` for long-running tasks.

        Args:
            prompt: Prompt to pass to OpenCode CLI
            cwd: Working directory
            timeout_seconds: Override timeout (default: 300 seconds)
            env: Optional environment overrides
            model: Model name (e.g., 'anthropic/claude-sonnet-4')

        Yields:
            Output chunks as they arrive

        Raises:
            ValueError: If prompt is empty
            asyncio.TimeoutError: If execution times out
        """
        if not prompt:
            raise ValueError("prompt is required")

        working_dir = Path(cwd) if cwd else Path.cwd()
        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds

        command = ["opencode"]

        # Model selection
        if model:
            command.extend(["-m", model])

        # Non-interactive run subcommand
        command.extend(["run", prompt])

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        logger.info(
            "Starting streaming OpenCode execution",
            extra={
                "command": " ".join(command),
                "cwd": str(working_dir),
                "timeout": timeout,
            },
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(working_dir),
                env=merged_env,
            )

            assert process.stdout is not None

            # Stream output with timeout
            start_time = asyncio.get_event_loop().time()
            try:
                while True:
                    # Check timeout before each read
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed >= timeout:
                        logger.warning("Streaming OpenCode execution timed out")
                        process.kill()
                        await process.wait()
                        raise TimeoutError()

                    # Read with a timeout per line
                    try:
                        line = await asyncio.wait_for(
                            process.stdout.readline(), timeout=timeout - elapsed
                        )
                        if not line:
                            break
                        yield line.decode("utf-8", errors="replace")
                    except TimeoutError:
                        logger.warning("Streaming OpenCode execution timed out")
                        process.kill()
                        await process.wait()
                        raise
            finally:
                # Ensure process is cleaned up
                if process.returncode is None:
                    await process.wait()

        except FileNotFoundError:
            logger.error("OpenCode CLI not found in PATH")
            raise

    def is_available(self) -> bool:
        """Check if opencode CLI is available in PATH.

        Returns:
            True if opencode is available, False otherwise
        """
        try:
            result = subprocess.run(
                ["which", "opencode"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            available = result.returncode == 0
            logger.debug(
                "OpenCode availability check",
                extra={"available": available},
            )
            return available
        except Exception as exc:
            logger.debug(
                "OpenCode availability check failed",
                extra={"error": str(exc)},
            )
            return False

    def _parse_files_modified(self, output: str) -> list[str]:
        """Extract modified files from opencode output.

        Looks for patterns like:
        - "Modified: path/to/file.py"
        - "Created: path/to/file.py"
        - "✓ path/to/file.py"

        Args:
            output: Command output to parse

        Returns:
            List of file paths that were modified
        """
        files: list[str] = []
        patterns = [
            r"^Modified:\s+(.+)$",
            r"^Created:\s+(.+)$",
            r"^✓\s+(.+)$",
        ]

        for line in output.splitlines():
            line = line.strip()
            for pattern in patterns:
                match = re.match(pattern, line)
                if match:
                    file_path = match.group(1).strip()
                    if file_path and file_path not in files:
                        files.append(file_path)
                    break

        logger.debug(
            "Parsed modified files",
            extra={"count": len(files), "files": files},
        )

        return files

    def _parse_test_results(self, output: str) -> TestResults | None:
        """Extract test results from opencode output.

        Looks for pytest output patterns like:
        - "=== 5 passed, 2 failed in 1.23s ==="
        - "FAILED tests/test_foo.py::test_bar"

        Args:
            output: Command output to parse

        Returns:
            TestResults if test output found, None otherwise
        """
        # Look for pytest summary line
        summary_pattern = r"===?\s*(\d+)\s+passed(?:,\s*(\d+)\s+failed)?(?:,\s*(\d+)\s+skipped)?\s+in\s+[\d.]+s\s*===?"
        summary_match = re.search(summary_pattern, output)

        if not summary_match:
            return None

        passed = int(summary_match.group(1))
        failed = int(summary_match.group(2) or 0)
        skipped = int(summary_match.group(3) or 0)
        total = passed + failed + skipped

        # Extract failure details
        failures: list[str] = []
        failure_pattern = r"^FAILED\s+(.+)$"
        for line in output.splitlines():
            match = re.match(failure_pattern, line.strip())
            if match:
                failures.append(match.group(1).strip())

        result = TestResults(
            passed=passed,
            failed=failed,
            skipped=skipped,
            total=total,
            failures=failures,
        )

        logger.debug(
            "Parsed test results",
            extra={
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "total": total,
            },
        )

        return result
