"""Pre-commit hook entry point for quality gates."""

import argparse
import sys
from pathlib import Path

from .test_runner import TestRunner, TestRunnerConfig


def main() -> None:
    """Run tests as pre-commit hook."""
    parser = argparse.ArgumentParser(description="Run tests for pre-commit")
    parser.add_argument(
        "path",
        type=Path,
        default=Path("."),
        nargs="?",
        help="Path to test (default: current directory)",
    )
    parser.add_argument(
        "--coverage-threshold",
        type=float,
        default=None,
        help="Minimum coverage percentage required",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first failure",
    )
    parser.add_argument(
        "--pattern",
        default="test_*.py",
        help="Test file pattern (default: test_*.py)",
    )
    parser.add_argument(
        "--markers",
        type=str,
        default=None,
        help="Pytest markers to select",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        dest="ignore_patterns",
        default=[],
        help="Patterns to ignore (can be specified multiple times)",
    )

    args = parser.parse_args()

    config = TestRunnerConfig(
        pattern=args.pattern,
        timeout=args.timeout,
        coverage_threshold=args.coverage_threshold,
        fail_fast=args.fail_fast,
        markers=args.markers,
        ignore_patterns=args.ignore_patterns,
    )

    runner = TestRunner(config)
    exit_code = runner.run_pre_commit(args.path)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
