"""Quality gate reporter for formatting results."""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class OutputFormat(str, Enum):
    """Output format for reports."""

    TEXT = "text"
    JSON = "json"
    MARKDOWN = "markdown"
    GITHUB = "github"  # GitHub Actions annotations


@dataclass
class ReportConfig:
    """Configuration for report generation."""

    format: OutputFormat = OutputFormat.TEXT
    verbose: bool = False
    include_details: bool = True
    colorize: bool = True


class QualityGateReporter:
    """Generates formatted reports from quality gate results."""

    def __init__(self, config: ReportConfig | None = None):
        self.config = config or ReportConfig()

    def format_result(self, result: Any) -> str:
        """Format a quality gate orchestrator result.

        Args:
            result: OrchestratorResult from running gates

        Returns:
            Formatted string based on configured format
        """
        if self.config.format == OutputFormat.TEXT:
            return self._format_text(result)
        elif self.config.format == OutputFormat.JSON:
            return self._format_json(result)
        elif self.config.format == OutputFormat.MARKDOWN:
            return self._format_markdown(result)
        elif self.config.format == OutputFormat.GITHUB:
            return self._format_github(result)
        return str(result)

    def _format_text(self, result: Any) -> str:
        """Format as plain text."""
        lines = []

        # Header
        status = "PASSED" if result.overall_success else "FAILED"
        lines.append(f"Quality Gate Results: {status}")
        lines.append("=" * 40)

        # Individual gates
        for gate in result.gate_results:
            icon = "[+]" if gate.status.value == "passed" else "[-]"
            lines.append(f"{icon} {gate.gate_name}: {gate.status.value} ({gate.duration:.2f}s)")
            if gate.error and self.config.verbose:
                lines.append(f"    Error: {gate.error}")

        # Summary
        lines.append("-" * 40)
        lines.append(f"Total: {result.passed_gates}/{len(result.gate_results)} passed")
        lines.append(f"Duration: {result.total_duration:.2f}s")

        return "\n".join(lines)

    def _format_json(self, result: Any) -> str:
        """Format as JSON."""
        data = {
            "success": result.overall_success,
            "gates": [
                {
                    "name": g.gate_name,
                    "status": g.status.value,
                    "duration": g.duration,
                    "error": g.error,
                }
                for g in result.gate_results
            ],
            "summary": {
                "passed": result.passed_gates,
                "failed": result.failed_gates,
                "total": len(result.gate_results),
                "duration": result.total_duration,
            },
            "timestamp": result.started_at.isoformat() if hasattr(result, "started_at") else None,
        }
        return json.dumps(data, indent=2)

    def _format_markdown(self, result: Any) -> str:
        """Format as Markdown."""
        lines = []

        # Header
        status = ":white_check_mark:" if result.overall_success else ":x:"
        lines.append(f"# Quality Gate Report {status}")
        lines.append("")

        # Table
        lines.append("| Gate | Status | Duration |")
        lines.append("|------|--------|----------|")
        for gate in result.gate_results:
            icon = ":white_check_mark:" if gate.status.value == "passed" else ":x:"
            lines.append(
                f"| {gate.gate_name} | {icon} {gate.status.value} | {gate.duration:.2f}s |"
            )

        # Summary
        lines.append("")
        lines.append(f"**Summary:** {result.passed_gates}/{len(result.gate_results)} gates passed")
        lines.append(f"**Total Duration:** {result.total_duration:.2f}s")

        return "\n".join(lines)

    def _format_github(self, result: Any) -> str:
        """Format as GitHub Actions annotations."""
        lines = []

        for gate in result.gate_results:
            if gate.status.value == "failed":
                msg = f"Quality gate '{gate.gate_name}' failed"
                if gate.error:
                    msg += f": {gate.error}"
                lines.append(f"::error::{msg}")
            else:
                lines.append(f"::notice::Quality gate '{gate.gate_name}' passed")

        # Summary
        if result.overall_success:
            lines.append("::notice::All quality gates passed!")
        else:
            lines.append(f"::error::{result.failed_gates} quality gate(s) failed")

        return "\n".join(lines)

    def print_result(self, result: Any) -> None:
        """Print formatted result to stdout."""
        output = self.format_result(result)
        print(output)

    def write_result(self, result: Any, path: str) -> None:
        """Write formatted result to file."""
        output = self.format_result(result)
        with open(path, "w") as f:
            f.write(output)


def create_reporter(config: ReportConfig | None = None) -> QualityGateReporter:
    """Factory function to create a QualityGateReporter."""
    return QualityGateReporter(config)
