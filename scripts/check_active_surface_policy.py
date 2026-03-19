#!/usr/bin/env python3
"""Fail when active/public-facing docs teach deprecated operator paths."""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ADR_ENFORCED = [
#   {"adr": "ADR-040", "bans": ["cli_v2", "forge-harness as CLI invocation"]},
#   {"adr": "ADR-032", "bans": ["bogdan-velscu/FORGE", "openclaw/FORGE"]},
# ]

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files to scan unconditionally (skip with warning if missing).
ACTIVE_SURFACE_DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "CONTRIBUTING.md",
    REPO_ROOT / "docs" / "GETTING_STARTED.md",
    REPO_ROOT / "docs" / "ACTIVE_SURFACES.md",
    REPO_ROOT / "docs" / "README.md",
    REPO_ROOT / "docs" / "runbooks" / "CANONICAL_WORKFLOW.md",
    REPO_ROOT / "docs" / "runbooks" / "LEGACY_TOOLING_POLICY.md",
]

# Patterns that must not appear in active/public-facing docs.
BANNED_PATTERNS = [
    (re.compile(r"forge-harness\s+\w"), "deprecated forge-harness CLI invocation (ADR-040)"),
    (re.compile(r"\bcli_v2\b"), "deprecated cli_v2 reference (ADR-040)"),
    (re.compile(r"tmux send-keys.*dispatch", re.IGNORECASE), "raw tmux dispatch (use forge dispatch send)"),
    (re.compile(r"\bforge-sprint\b"), "deleted script forge-sprint"),
    (re.compile(r"\bforge-fleet\b"), "deleted script forge-fleet"),
    (re.compile(r"\bforge-start\b"), "deleted script forge-start"),
    (re.compile(r"\bforge trinity\b"), "forge trinity does not exist in Go CLI"),
    (re.compile(r"bogdan-velscu/FORGE"), "old GitHub slug (ADR-032)"),
    (re.compile(r"openclaw/FORGE"), "old GitHub org (ADR-032)"),
    (re.compile(r"/home/bogdan/"), "absolute private home path"),
    (re.compile(r"/home/openclaw/"), "absolute private home path"),
    (re.compile(r"\$HOME/work/FORGE"), "hardcoded FORGE_ROOT assumption"),
    (re.compile(r"http://prya:8081"), "direct private URL (use DefaultControlPlaneURL constant)"),
    (re.compile(r"\bcmd/forge-v3\b"), "daemon renamed to forged (use cmd/forged/)"),
]


def scan_text(text: str) -> list[str]:
    """Return a list of human-readable failure strings for banned patterns found in *text*."""
    failures: list[str] = []
    for pattern, label in BANNED_PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            failures.append(f"L{line}: banned pattern — {label}")
    return failures


def _collect_paths() -> tuple[list[Path], list[str]]:
    """Return (paths_to_scan, warnings) where warnings covers missing files."""
    paths: list[Path] = []
    warnings: list[str] = []

    for path in ACTIVE_SURFACE_DOCS:
        if not path.exists():
            warnings.append(f"WARNING: missing file (skipped): {path.relative_to(REPO_ROOT)}")
        else:
            paths.append(path)

    # .github/workflows/*.yml — glob, skip if directory absent
    workflow_dir = REPO_ROOT / ".github" / "workflows"
    if workflow_dir.is_dir():
        paths.extend(sorted(workflow_dir.glob("*.yml")))
    else:
        warnings.append("WARNING: .github/workflows/ not found (skipped)")

    return paths, warnings


def main() -> int:
    paths, warnings = _collect_paths()

    for warning in warnings:
        print(warning, file=sys.stderr)

    failures: list[str] = []
    for path in paths:
        issues = scan_text(path.read_text(encoding="utf-8"))
        for issue in issues:
            failures.append(f"{path.relative_to(REPO_ROOT)}:{issue}")

    if failures:
        print("Active surface policy check FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        print(f"\nSummary: {len(failures)} violation(s) across {len(paths)} file(s) scanned.")
        return 1

    print(f"Active surface policy check passed. ({len(paths)} file(s) scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
