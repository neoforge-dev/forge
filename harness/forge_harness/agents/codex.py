"""Codex CLI integration for FORGE harness."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodexResult:
    prompt: str
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    timed_out: bool
    success: bool
    error: str | None = None


class CodexExecutor:
    """Execute Codex CLI tasks via `codex exec` and capture results."""

    def __init__(self, timeout_seconds: int = 300) -> None:
        self.timeout_seconds = timeout_seconds

    def execute(
        self,
        prompt: str,
        cwd: str | Path | None = None,
        timeout_seconds: int | None = None,
        env: dict[str, str] | None = None,
        headless: bool = True,
    ) -> CodexResult:
        """Run `codex exec` with a prompt and return structured results.

        Args:
            prompt: Prompt to pass to Codex CLI
            cwd: Working directory
            timeout_seconds: Override timeout (default: 300 seconds)
            env: Optional environment overrides
            headless: If True, stdin is DEVNULL (non-interactive)
        """
        if not prompt:
            raise ValueError("prompt is required")

        working_dir = Path(cwd) if cwd else Path.cwd()
        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds

        command = ["codex", "exec", prompt]
        start = time.monotonic()

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        stdin = subprocess.DEVNULL if headless else None

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
            return CodexResult(
                prompt=prompt,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                exit_code=result.returncode,
                duration_seconds=duration,
                timed_out=False,
                success=success,
                error=None if success else "nonzero_exit",
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return CodexResult(
                prompt=prompt,
                stdout=stdout,
                stderr=stderr,
                exit_code=-1,
                duration_seconds=duration,
                timed_out=True,
                success=False,
                error="timeout",
            )
        except FileNotFoundError as exc:
            duration = time.monotonic() - start
            return CodexResult(
                prompt=prompt,
                stdout="",
                stderr=str(exc),
                exit_code=-1,
                duration_seconds=duration,
                timed_out=False,
                success=False,
                error="codex_not_found",
            )

    def is_available(self) -> bool:
        """Check if codex CLI is available in PATH."""
        try:
            result = subprocess.run(
                ["which", "codex"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False
