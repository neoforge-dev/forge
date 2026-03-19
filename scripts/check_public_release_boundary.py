#!/usr/bin/env python3
"""Fail when public-facing docs still contain private/internal identifiers."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

PUBLIC_DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "CONTRIBUTING.md",
    REPO_ROOT / "docs" / "README.md",
    REPO_ROOT / "docs" / "GETTING_STARTED.md",
    REPO_ROOT / "docs" / "ACTIVE_SURFACES.md",
    REPO_ROOT / "docs" / "OPEN_SOURCE_SPLIT_PLAN.md",
    REPO_ROOT / "docs" / "PUBLIC_RELEASE_CHECKLIST.md",
    REPO_ROOT / "docs" / "portfolio" / "OPERATING_LOOP_V1.md",
    REPO_ROOT / "examples" / "portfolio" / "sample-portfolio-state.yaml",
]

PRIVATE_MARKERS = [
    (re.compile(r"/Users/"), "local absolute filesystem path"),
    (re.compile(r"\bprya\b"), "internal node hostname"),
    (re.compile(r"\bsati\b"), "internal node hostname"),
    (re.compile(r"\bnova\b"), "internal node hostname"),
    (re.compile(r"\bgaea\b"), "internal node hostname"),
    (re.compile(r"\bvega\b"), "internal node hostname"),
    (re.compile(r"\bcodeswiftr-com\b"), "private portfolio domain"),
    (re.compile(r"\bbrandfocus-ai\b"), "private portfolio domain"),
    (re.compile(r"\bthebrightharbor-com\b"), "private portfolio domain"),
    (re.compile(r"\bleanvibe-ai\b"), "private portfolio domain"),
    (re.compile(r"\binterview-simulator\b"), "private product identifier"),
    (re.compile(r"\bvoice-coach\b"), "private product identifier"),
    (re.compile(r"\bstudy-flow\b"), "private product identifier"),
    (re.compile(r"\bcode-atlas\b"), "private product identifier"),
    (re.compile(r"\bmvp-validator\b"), "private product identifier"),
    (re.compile(r"bogdan-velscu/FORGE"), "old GitHub slug"),
    (re.compile(r"\bcli_v2\b"), "deprecated cli_v2 (ADR-040)"),
    (re.compile(r"\.forge/routing"), "runtime state path (use config/routing/)"),
    (re.compile(r"docs/portfolio/portfolio-state\.yaml"), "wrong config path (use config/portfolio/)"),
]


def scan_text(text: str) -> list[str]:
    failures: list[str] = []
    for pattern, label in PRIVATE_MARKERS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            failures.append(f"L{line}: remove {label}")
    return failures


def main() -> int:
    failures: list[str] = []
    for path in PUBLIC_DOCS:
        if not path.exists():
            failures.append(f"Missing public doc: {path.relative_to(REPO_ROOT)}")
            continue
        issues = scan_text(path.read_text(encoding="utf-8"))
        for issue in issues:
            failures.append(f"{path.relative_to(REPO_ROOT)}:{issue}")

    if failures:
        print("Public release boundary check failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("Public release boundary check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
