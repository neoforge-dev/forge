"""
PLAN.md Parser for FORGE Agent
==============================

Parses PLAN.md files to extract ICE-scored epics for autonomous development.
When no GitHub Issues exist, the agent can work from PLAN.md directly.

Supports multiple formats:
- Markdown (default): Traditional PLAN.md with ### Epic headers
- YAML: plan.yaml files with structured epic data
- JSON: plan.json files with structured epic data
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Epic:
    """
    Represents an epic from a PLAN.md file.

    Epics are scored using the ICE framework:
    - Impact: How much will this improve things? (1-10)
    - Confidence: How sure are we about the estimate? (1-10)
    - Ease: How easy is this to implement? (1-10)
    """

    number: str  # "1.1", "2.3", etc.
    title: str
    ice_score: float  # Calculated from Impact/Confidence/Ease
    priority: str  # "CRITICAL", "HIGH", "MEDIUM", "LOW", "P0", "P1", etc.
    effort_hours: float
    checkpoint: str  # Validation criteria

    @property
    def github_title(self) -> str:
        """Format as GitHub issue title."""
        return f"[Epic {self.number}] {self.title}"

    @property
    def is_blocking(self) -> bool:
        """Check if this is a blocking priority."""
        return self.priority in {"CRITICAL", "P0", "P0 - BLOCKING"}


def parse_plan_md(plan_path: Path) -> list[Epic]:
    """
    Parse a PLAN.md file and extract ICE-scored epics.

    Args:
        plan_path: Path to the PLAN.md file

    Returns:
        List of Epic objects, sorted by ICE score (descending)
    """
    content = plan_path.read_text()
    epics = []

    # Pattern: ### Epic N.M: Title (handles both ## and ### prefixes)
    epic_pattern = r"#{2,3}\s+Epic\s+(\d+\.?\d*):\s*(.+?)(?=\n)"
    # Patterns for extracting metadata - use simpler patterns that are more robust
    ice_pattern = r"ICE Score[:\*]*\s*(\d+)/(\d+)/(\d+)"
    effort_pattern = r"Effort[:\*]*\s*(\d+(?:\.\d+)?)\s*h"
    priority_pattern = r"Priority[:\*]*\s*(CRITICAL|HIGH|MEDIUM|LOW|P\d)"
    checkpoint_pattern = r"\*\*Checkpoint[:\*]*\s*\*\*\s*\n((?:[-\[].*\n?)+)"

    # Find all epic sections
    for match in re.finditer(epic_pattern, content, re.IGNORECASE):
        epic_num = match.group(1)
        epic_title = match.group(2).strip()

        # Find the epic section content (until next epic or end of file)
        start = match.end()
        next_epic = re.search(r"#{2,3}\s+Epic\s+\d", content[start:])
        if next_epic:
            section = content[start : start + next_epic.start()]
        else:
            section = content[start:]

        # Extract ICE score
        ice_match = re.search(ice_pattern, section)
        if ice_match:
            impact, confidence, ease = map(float, ice_match.groups())
            # ICE score is the average or the stated result
            # Format shows: 10/10/9 = 9.0, so we use the actual calculation
            ice_score = (impact + confidence + ease) / 3
        else:
            ice_score = 5.0  # Default

        # Extract effort
        effort_match = re.search(effort_pattern, section)
        effort_hours = float(effort_match.group(1)) if effort_match else 4.0

        # Extract priority
        priority_match = re.search(priority_pattern, section)
        priority = priority_match.group(1) if priority_match else "MEDIUM"

        # Extract checkpoint
        checkpoint_match = re.search(checkpoint_pattern, section, re.MULTILINE)
        if checkpoint_match:
            checkpoint = checkpoint_match.group(1).strip()
        else:
            # Try simpler pattern
            simple_checkpoint = re.search(
                r"\*\*Checkpoint\*\*:?\s*(.+?)(?=\n\n|\n---|\n##|\Z)",
                section,
                re.DOTALL,
            )
            checkpoint = simple_checkpoint.group(1).strip() if simple_checkpoint else ""

        epics.append(
            Epic(
                number=epic_num,
                title=epic_title,
                ice_score=round(ice_score, 1),
                priority=priority,
                effort_hours=effort_hours,
                checkpoint=checkpoint,
            )
        )

    # Sort by ICE score (descending)
    return sorted(epics, key=lambda e: e.ice_score, reverse=True)


def get_next_epic(plan_path: Path, completed_numbers: set[str] | None = None) -> Epic | None:
    """
    Get the next epic to work on from PLAN.md.

    Args:
        plan_path: Path to the PLAN.md file
        completed_numbers: Set of epic numbers already completed

    Returns:
        The highest priority uncompleted Epic, or None if all done
    """
    completed = completed_numbers or set()
    epics = parse_plan_md(plan_path)

    for epic in epics:
        if epic.number not in completed:
            return epic

    return None


def parse_plan_yaml(plan_path: Path) -> list[Epic]:
    """
    Parse a YAML plan file and extract epics.

    Expected YAML format:
    ```yaml
    epics:
      - number: "1.1"
        title: "Wire Security Hooks"
        ice_score: 9.0
        priority: "P0"
        effort_hours: 4.0
        checkpoint: "Security hooks connected"
    ```

    Args:
        plan_path: Path to the plan.yaml file

    Returns:
        List of Epic objects, sorted by ICE score (descending)
    """
    content = plan_path.read_text()
    data = yaml.safe_load(content)

    if not data or "epics" not in data:
        return []

    epics = []
    for epic_data in data.get("epics", []):
        epic = _epic_from_dict(epic_data)
        if epic:
            epics.append(epic)

    return sorted(epics, key=lambda e: e.ice_score, reverse=True)


def parse_plan_json(plan_path: Path) -> list[Epic]:
    """
    Parse a JSON plan file and extract epics.

    Expected JSON format:
    ```json
    {
      "epics": [
        {
          "number": "1.1",
          "title": "Wire Security Hooks",
          "ice_score": 9.0,
          "priority": "P0",
          "effort_hours": 4.0,
          "checkpoint": "Security hooks connected"
        }
      ]
    }
    ```

    Args:
        plan_path: Path to the plan.json file

    Returns:
        List of Epic objects, sorted by ICE score (descending)
    """
    content = plan_path.read_text()
    data = json.loads(content)

    if not data or "epics" not in data:
        return []

    epics = []
    for epic_data in data.get("epics", []):
        epic = _epic_from_dict(epic_data)
        if epic:
            epics.append(epic)

    return sorted(epics, key=lambda e: e.ice_score, reverse=True)


def _epic_from_dict(data: dict[str, Any]) -> Epic | None:
    """
    Create an Epic from a dictionary.

    Args:
        data: Dictionary with epic fields

    Returns:
        Epic object or None if required fields are missing
    """
    if not data.get("number") or not data.get("title"):
        return None

    # Support ICE as tuple/list (impact, confidence, ease) or as score
    ice_score = data.get("ice_score", 5.0)
    if isinstance(ice_score, (list, tuple)) and len(ice_score) == 3:
        ice_score = sum(ice_score) / 3

    return Epic(
        number=str(data["number"]),
        title=str(data["title"]),
        ice_score=round(float(ice_score), 1),
        priority=str(data.get("priority", "MEDIUM")),
        effort_hours=float(data.get("effort_hours", 4.0)),
        checkpoint=str(data.get("checkpoint", "")),
    )


def parse_plan(plan_path: Path) -> list[Epic]:
    """
    Auto-detect plan format and parse appropriately.

    Supports:
    - .yaml/.yml files: YAML format
    - .json files: JSON format
    - .md files (default): Markdown format

    Args:
        plan_path: Path to the plan file

    Returns:
        List of Epic objects, sorted by ICE score (descending)
    """
    suffix = plan_path.suffix.lower()

    if suffix in (".yaml", ".yml"):
        return parse_plan_yaml(plan_path)
    elif suffix == ".json":
        return parse_plan_json(plan_path)
    else:
        return parse_plan_md(plan_path)


def validate_plan(plan_path: Path) -> dict[str, list[str]]:
    """
    Validate a plan file and return errors/warnings.

    Args:
        plan_path: Path to the plan file

    Returns:
        Dict with "errors" and "warnings" lists
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not plan_path.exists():
        errors.append(f"Plan file not found: {plan_path}")
        return {"errors": errors, "warnings": warnings}

    try:
        epics = parse_plan(plan_path)
    except json.JSONDecodeError as e:
        errors.append(f"Invalid JSON format: {e}")
        return {"errors": errors, "warnings": warnings}
    except yaml.YAMLError as e:
        errors.append(f"Invalid YAML format: {e}")
        return {"errors": errors, "warnings": warnings}
    except Exception as e:
        errors.append(f"Failed to parse plan: {e}")
        return {"errors": errors, "warnings": warnings}

    if not epics:
        warnings.append("No epics found in plan file")
        return {"errors": errors, "warnings": warnings}

    # Validate each epic
    seen_numbers = set()
    for epic in epics:
        # Check for duplicate numbers
        if epic.number in seen_numbers:
            errors.append(f"Duplicate epic number: {epic.number}")
        seen_numbers.add(epic.number)

        # Check ICE score range
        if not 0 <= epic.ice_score <= 10:
            warnings.append(f"Epic {epic.number}: ICE score {epic.ice_score} outside 0-10 range")

        # Check for empty checkpoint
        if not epic.checkpoint:
            warnings.append(f"Epic {epic.number}: Missing checkpoint criteria")

        # Check priority format
        valid_priorities = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "P0", "P1", "P2", "P3"}
        if epic.priority not in valid_priorities:
            warnings.append(f"Epic {epic.number}: Unknown priority '{epic.priority}'")

    return {"errors": errors, "warnings": warnings}


def list_epics(plan_path: Path) -> list[dict[str, Any]]:
    """
    List all epics from a plan file in a dictionary format.

    Useful for CLI display or JSON output.

    Args:
        plan_path: Path to the plan file

    Returns:
        List of epic dictionaries with display-friendly keys
    """
    epics = parse_plan(plan_path)

    return [
        {
            "number": e.number,
            "title": e.title,
            "priority": e.priority,
            "ice_score": e.ice_score,
            "effort": f"{e.effort_hours}h",
            "criteria_count": len(e.checkpoint.split("\n")) if e.checkpoint else 0,
            "is_blocking": e.is_blocking,
        }
        for e in epics
    ]
