"""Type checking quality gate using mypy."""

import asyncio
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class TypeCheckError:
    file: str
    line: int
    column: int
    code: str
    message: str
    severity: str = "error"


@dataclass
class TypeCheckResult:
    success: bool
    errors: list[TypeCheckError] = field(default_factory=list)
    file_count: int = 0
    duration: float = 0.0


@dataclass
class TypeCheckerConfig:
    strict: bool = False
    ignore_patterns: list[str] = field(default_factory=list)
    timeout: int = 300


class TypeChecker:
    def __init__(self, config: TypeCheckerConfig | None = None):
        self.config = config or TypeCheckerConfig()

    async def check_files(self, paths: list[Path]) -> TypeCheckResult:
        start = datetime.now()
        # Run mypy
        cmd = ["mypy"] + [str(p) for p in paths]
        if self.config.strict:
            cmd.append("--strict")
        try:
            result = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                ),
                timeout=self.config.timeout,
            )
            stdout, stderr = await result.communicate()
            # Parse output
            errors = self._parse_output(stdout.decode())
            return TypeCheckResult(
                success=result.returncode == 0,
                errors=errors,
                file_count=len(paths),
                duration=(datetime.now() - start).total_seconds(),
            )
        except TimeoutError:
            return TypeCheckResult(
                success=False, file_count=len(paths), duration=self.config.timeout
            )

    def _parse_output(self, output: str) -> list[TypeCheckError]:
        errors = []
        for line in output.strip().split("\n"):
            if ":" in line and not line.startswith("Found"):
                parts = line.split(":", 3)
                if len(parts) >= 4:
                    errors.append(
                        TypeCheckError(
                            file=parts[0],
                            line=int(parts[1]) if parts[1].isdigit() else 0,
                            column=int(parts[2]) if parts[2].isdigit() else 0,
                            code="",
                            message=parts[3].strip(),
                        )
                    )
        return errors

    async def check_staged(self) -> TypeCheckResult:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            capture_output=True,
            text=True,
        )
        py_files = [Path(f) for f in result.stdout.strip().split("\n") if f.endswith(".py")]
        if not py_files:
            return TypeCheckResult(success=True, file_count=0, duration=0.0)
        return await self.check_files(py_files)


def create_type_checker(config: TypeCheckerConfig | None = None) -> TypeChecker:
    return TypeChecker(config)
