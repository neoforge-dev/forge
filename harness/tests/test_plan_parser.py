"""Tests for PLAN.md parser functionality."""

from pathlib import Path

import pytest

from forge_harness.plan_parser import Epic, parse_plan_md


class TestEpic:
    """Tests for Epic dataclass."""

    def test_epic_creation(self):
        """Epic can be created with required fields."""
        epic = Epic(
            number="1.1",
            title="Wire Security Hooks",
            ice_score=9.0,
            priority="P0",
            effort_hours=4.0,
            checkpoint="Security hooks connected",
        )
        assert epic.number == "1.1"
        assert epic.title == "Wire Security Hooks"
        assert epic.ice_score == 9.0

    def test_github_title_property(self):
        """github_title property formats correctly."""
        epic = Epic(
            number="2.1",
            title="Parse PLAN.md",
            ice_score=8.0,
            priority="P1",
            effort_hours=4.0,
            checkpoint="Test",
        )
        assert epic.github_title == "[Epic 2.1] Parse PLAN.md"

    def test_epic_with_single_number(self):
        """Epic handles single digit numbers."""
        epic = Epic(
            number="1",
            title="Initial Setup",
            ice_score=10.0,
            priority="CRITICAL",
            effort_hours=2.0,
            checkpoint="Setup complete",
        )
        assert epic.github_title == "[Epic 1] Initial Setup"


class TestParsePlanMd:
    """Tests for parse_plan_md function."""

    @pytest.fixture
    def sample_plan_content(self):
        """Sample PLAN.md content for testing."""
        return """# FORGE Harness Implementation Plan

## Phase 1: Critical Fixes (Days 1-2)

### Epic 1.1: Wire Security Hooks to Agent

**ICE Score:** 10/10/9 = 9.0
**Effort:** 4h
**Priority:** P0 - BLOCKING

**Problem:** Security hooks are defined but not connected to agent.

**Checkpoint:**
- [ ] Security hook connected
- [ ] Tests pass

---

### Epic 1.2: Complete Coding Prompt Template

**ICE Score:** 10/10/7 = 9.0
**Effort:** 6h
**Priority:** P0 - BLOCKING

**Checkpoint:**
- [ ] Prompt template complete

---

## Phase 2: Enhanced Integration (Days 3-5)

### Epic 2.1: Parse PLAN.md for ICE-Scored Epics

**ICE Score:** 8/9/7 = 8.0
**Effort:** 4h
**Priority:** P1

**Checkpoint:**
- [ ] Parser works correctly
"""

    @pytest.fixture
    def plan_file(self, tmp_path, sample_plan_content):
        """Create a temporary PLAN.md file."""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(sample_plan_content)
        return plan_path

    def test_parses_epic_numbers(self, plan_file):
        """Parses epic numbers correctly."""
        epics = parse_plan_md(plan_file)
        numbers = [e.number for e in epics]
        assert "1.1" in numbers
        assert "1.2" in numbers
        assert "2.1" in numbers

    def test_parses_epic_titles(self, plan_file):
        """Parses epic titles correctly."""
        epics = parse_plan_md(plan_file)
        titles = [e.title for e in epics]
        assert any("Security Hooks" in t for t in titles)
        assert any("Prompt Template" in t for t in titles)
        assert any("PLAN.md" in t for t in titles)

    def test_parses_ice_scores(self, plan_file):
        """Parses ICE scores correctly."""
        epics = parse_plan_md(plan_file)
        # Find epic 1.1 (ICE: 10/10/9, average = 9.67, rounded to 9.7)
        epic_1_1 = next(e for e in epics if e.number == "1.1")
        assert epic_1_1.ice_score == 9.7  # (10+10+9)/3 = 9.67 rounded

        # Find epic 2.1 (ICE: 8/9/7, average = 8.0)
        epic_2_1 = next(e for e in epics if e.number == "2.1")
        assert epic_2_1.ice_score == 8.0  # (8+9+7)/3 = 8.0

    def test_parses_effort(self, plan_file):
        """Parses effort hours correctly."""
        epics = parse_plan_md(plan_file)
        epic_1_1 = next(e for e in epics if e.number == "1.1")
        assert epic_1_1.effort_hours == 4.0

        epic_1_2 = next(e for e in epics if e.number == "1.2")
        assert epic_1_2.effort_hours == 6.0

    def test_parses_priority(self, plan_file):
        """Parses priority correctly."""
        epics = parse_plan_md(plan_file)
        epic_1_1 = next(e for e in epics if e.number == "1.1")
        assert epic_1_1.priority == "P0"

        epic_2_1 = next(e for e in epics if e.number == "2.1")
        assert epic_2_1.priority == "P1"

    def test_parses_checkpoint(self, plan_file):
        """Parses checkpoint criteria."""
        epics = parse_plan_md(plan_file)
        epic_1_1 = next(e for e in epics if e.number == "1.1")
        assert "Security hook connected" in epic_1_1.checkpoint

    def test_returns_sorted_by_ice_score(self, plan_file):
        """Returns epics sorted by ICE score descending."""
        epics = parse_plan_md(plan_file)
        scores = [e.ice_score for e in epics]
        assert scores == sorted(scores, reverse=True)

    def test_handles_missing_ice_score(self, tmp_path):
        """Uses default ICE score when missing."""
        content = """### Epic 1.1: Test Epic

**Effort:** 4h
**Priority:** P1

**Checkpoint:**
- [ ] Done
"""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(content)

        epics = parse_plan_md(plan_path)
        assert len(epics) == 1
        assert epics[0].ice_score == 5.0  # Default

    def test_handles_missing_effort(self, tmp_path):
        """Uses default effort when missing."""
        content = """### Epic 1.1: Test Epic

**ICE Score:** 8/8/8 = 8.0
**Priority:** P1

**Checkpoint:**
- [ ] Done
"""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(content)

        epics = parse_plan_md(plan_path)
        assert len(epics) == 1
        assert epics[0].effort_hours == 4.0  # Default

    def test_handles_missing_priority(self, tmp_path):
        """Uses default priority when missing."""
        content = """### Epic 1.1: Test Epic

**ICE Score:** 8/8/8 = 8.0
**Effort:** 4h

**Checkpoint:**
- [ ] Done
"""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(content)

        epics = parse_plan_md(plan_path)
        assert len(epics) == 1
        assert epics[0].priority == "MEDIUM"  # Default

    def test_empty_file_returns_empty_list(self, tmp_path):
        """Empty file returns empty list."""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("")

        epics = parse_plan_md(plan_path)
        assert epics == []

    def test_no_epics_returns_empty_list(self, tmp_path):
        """File without epics returns empty list."""
        content = """# Project Plan

## Overview

This is just a regular document.
"""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(content)

        epics = parse_plan_md(plan_path)
        assert epics == []


class TestParsePlanMdRealFile:
    """Tests using the real PLAN.md file."""

    @pytest.fixture
    def real_plan_path(self):
        """Path to the real PLAN.md file."""
        plan_path = Path(__file__).parent.parent / "PLAN.md"
        if not plan_path.exists():
            pytest.skip("Real PLAN.md not found")
        return plan_path

    def test_parses_real_plan_md(self, real_plan_path):
        """Parses the real PLAN.md file without error.

        Note: The current PLAN.md uses a table-based status format rather than
        epic format, so it may return an empty list. This test verifies parsing
        completes without error.
        """
        epics = parse_plan_md(real_plan_path)
        # Parser should complete without error; empty list is valid for non-epic format
        assert isinstance(epics, list), "Should return a list"

    def test_all_epics_have_numbers(self, real_plan_path):
        """All parsed epics have valid numbers."""
        epics = parse_plan_md(real_plan_path)
        if not epics:
            pytest.skip("No epics found in current PLAN.md format")
        for epic in epics:
            assert epic.number, f"Epic '{epic.title}' missing number"
            # Should match pattern like "1.1", "2.3", etc.
            assert "." in epic.number or epic.number.isdigit()

    def test_all_epics_have_titles(self, real_plan_path):
        """All parsed epics have titles."""
        epics = parse_plan_md(real_plan_path)
        if not epics:
            pytest.skip("No epics found in current PLAN.md format")
        for epic in epics:
            assert epic.title, f"Epic {epic.number} missing title"
            assert len(epic.title) > 0


class TestParsePlanYaml:
    """Tests for YAML plan parsing."""

    def test_parses_yaml_epics(self, tmp_path):
        """Parses epics from YAML file."""
        content = """
epics:
  - number: "1.1"
    title: "First Epic"
    ice_score: 9.0
    priority: "P0"
    effort_hours: 4.0
    checkpoint: "Test passes"
  - number: "1.2"
    title: "Second Epic"
    ice_score: 7.5
    priority: "P1"
    effort_hours: 6.0
    checkpoint: "Feature complete"
"""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text(content)

        from forge_harness.plan_parser import parse_plan_yaml

        epics = parse_plan_yaml(plan_path)
        assert len(epics) == 2
        # Should be sorted by ICE score
        assert epics[0].number == "1.1"
        assert epics[0].ice_score == 9.0
        assert epics[1].number == "1.2"
        assert epics[1].ice_score == 7.5

    def test_parses_yaml_with_ice_tuple(self, tmp_path):
        """Parses ICE score as tuple [impact, confidence, ease]."""
        content = """
epics:
  - number: "1.1"
    title: "Test Epic"
    ice_score: [10, 9, 8]
    priority: "HIGH"
    effort_hours: 4.0
    checkpoint: "Done"
"""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text(content)

        from forge_harness.plan_parser import parse_plan_yaml

        epics = parse_plan_yaml(plan_path)
        assert len(epics) == 1
        # (10 + 9 + 8) / 3 = 9.0
        assert epics[0].ice_score == 9.0

    def test_yaml_empty_file(self, tmp_path):
        """Empty YAML returns empty list."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("")

        from forge_harness.plan_parser import parse_plan_yaml

        epics = parse_plan_yaml(plan_path)
        assert epics == []

    def test_yaml_no_epics_key(self, tmp_path):
        """YAML without epics key returns empty list."""
        content = """
metadata:
  version: "1.0"
"""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text(content)

        from forge_harness.plan_parser import parse_plan_yaml

        epics = parse_plan_yaml(plan_path)
        assert epics == []


class TestParsePlanJson:
    """Tests for JSON plan parsing."""

    def test_parses_json_epics(self, tmp_path):
        """Parses epics from JSON file."""
        content = """
{
  "epics": [
    {
      "number": "1.1",
      "title": "First Epic",
      "ice_score": 9.0,
      "priority": "P0",
      "effort_hours": 4.0,
      "checkpoint": "Test passes"
    },
    {
      "number": "1.2",
      "title": "Second Epic",
      "ice_score": 7.5,
      "priority": "P1",
      "effort_hours": 6.0,
      "checkpoint": "Feature complete"
    }
  ]
}
"""
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(content)

        from forge_harness.plan_parser import parse_plan_json

        epics = parse_plan_json(plan_path)
        assert len(epics) == 2
        assert epics[0].number == "1.1"
        assert epics[0].ice_score == 9.0

    def test_json_empty_file(self, tmp_path):
        """Empty JSON object returns empty list."""
        plan_path = tmp_path / "plan.json"
        plan_path.write_text("{}")

        from forge_harness.plan_parser import parse_plan_json

        epics = parse_plan_json(plan_path)
        assert epics == []


class TestParsePlanAutoDetect:
    """Tests for auto-detecting plan format."""

    def test_auto_detects_yaml(self, tmp_path):
        """Auto-detects .yaml files."""
        content = """
epics:
  - number: "1.1"
    title: "YAML Epic"
    ice_score: 8.0
"""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text(content)

        from forge_harness.plan_parser import parse_plan

        epics = parse_plan(plan_path)
        assert len(epics) == 1
        assert epics[0].title == "YAML Epic"

    def test_auto_detects_yml(self, tmp_path):
        """Auto-detects .yml files."""
        content = """
epics:
  - number: "1.1"
    title: "YML Epic"
    ice_score: 8.0
"""
        plan_path = tmp_path / "plan.yml"
        plan_path.write_text(content)

        from forge_harness.plan_parser import parse_plan

        epics = parse_plan(plan_path)
        assert len(epics) == 1
        assert epics[0].title == "YML Epic"

    def test_auto_detects_json(self, tmp_path):
        """Auto-detects .json files."""
        content = """{"epics": [{"number": "1.1", "title": "JSON Epic", "ice_score": 8.0}]}"""
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(content)

        from forge_harness.plan_parser import parse_plan

        epics = parse_plan(plan_path)
        assert len(epics) == 1
        assert epics[0].title == "JSON Epic"

    def test_defaults_to_markdown(self, tmp_path):
        """Defaults to markdown for .md files."""
        content = """### Epic 1.1: Markdown Epic

**ICE Score:** 8/8/8 = 8.0
**Effort:** 4h
**Priority:** P1

**Checkpoint:**
- [ ] Done
"""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(content)

        from forge_harness.plan_parser import parse_plan

        epics = parse_plan(plan_path)
        assert len(epics) == 1
        assert epics[0].title == "Markdown Epic"


class TestValidatePlan:
    """Tests for plan validation."""

    def test_validates_missing_file(self, tmp_path):
        """Reports error for missing file."""
        plan_path = tmp_path / "nonexistent.yaml"

        from forge_harness.plan_parser import validate_plan

        result = validate_plan(plan_path)
        assert len(result["errors"]) > 0
        assert "not found" in result["errors"][0]

    def test_validates_empty_plan(self, tmp_path):
        """Warns about empty plan."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("epics: []")

        from forge_harness.plan_parser import validate_plan

        result = validate_plan(plan_path)
        assert len(result["warnings"]) > 0
        assert "No epics found" in result["warnings"][0]

    def test_validates_duplicate_numbers(self, tmp_path):
        """Reports error for duplicate epic numbers."""
        content = """
epics:
  - number: "1.1"
    title: "First"
    ice_score: 8.0
  - number: "1.1"
    title: "Duplicate"
    ice_score: 7.0
"""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text(content)

        from forge_harness.plan_parser import validate_plan

        result = validate_plan(plan_path)
        assert len(result["errors"]) > 0
        assert "Duplicate" in result["errors"][0]

    def test_validates_ice_score_range(self, tmp_path):
        """Warns about ICE score outside 0-10 range."""
        content = """
epics:
  - number: "1.1"
    title: "High Score"
    ice_score: 15.0
"""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text(content)

        from forge_harness.plan_parser import validate_plan

        result = validate_plan(plan_path)
        assert len(result["warnings"]) > 0
        assert "outside" in result["warnings"][0]

    def test_validates_missing_checkpoint(self, tmp_path):
        """Warns about missing checkpoint."""
        content = """
epics:
  - number: "1.1"
    title: "No Checkpoint"
    ice_score: 8.0
"""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text(content)

        from forge_harness.plan_parser import validate_plan

        result = validate_plan(plan_path)
        assert len(result["warnings"]) > 0
        assert "checkpoint" in result["warnings"][0].lower()


class TestListEpics:
    """Tests for list_epics function."""

    def test_lists_epics_as_dicts(self, tmp_path):
        """list_epics returns dictionary format."""
        content = """
epics:
  - number: "1.1"
    title: "Test Epic"
    ice_score: 9.0
    priority: "P0"
    effort_hours: 4.0
    checkpoint: "Done\\nVerified"
"""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text(content)

        from forge_harness.plan_parser import list_epics

        result = list_epics(plan_path)
        assert len(result) == 1
        assert result[0]["number"] == "1.1"
        assert result[0]["title"] == "Test Epic"
        assert result[0]["priority"] == "P0"
        assert result[0]["ice_score"] == 9.0
        assert result[0]["effort"] == "4.0h"
        assert result[0]["criteria_count"] == 2  # Two lines in checkpoint
        assert result[0]["is_blocking"] is True  # P0 is blocking


class TestEpicIsBlocking:
    """Tests for Epic.is_blocking property."""

    def test_critical_is_blocking(self):
        """CRITICAL priority is blocking."""
        epic = Epic(
            number="1.1",
            title="Test",
            ice_score=10.0,
            priority="CRITICAL",
            effort_hours=4.0,
            checkpoint="",
        )
        assert epic.is_blocking is True

    def test_p0_is_blocking(self):
        """P0 priority is blocking."""
        epic = Epic(
            number="1.1",
            title="Test",
            ice_score=10.0,
            priority="P0",
            effort_hours=4.0,
            checkpoint="",
        )
        assert epic.is_blocking is True

    def test_p0_blocking_suffix_is_blocking(self):
        """P0 - BLOCKING priority is blocking."""
        epic = Epic(
            number="1.1",
            title="Test",
            ice_score=10.0,
            priority="P0 - BLOCKING",
            effort_hours=4.0,
            checkpoint="",
        )
        assert epic.is_blocking is True

    def test_p1_is_not_blocking(self):
        """P1 priority is not blocking."""
        epic = Epic(
            number="1.1",
            title="Test",
            ice_score=10.0,
            priority="P1",
            effort_hours=4.0,
            checkpoint="",
        )
        assert epic.is_blocking is False

    def test_high_is_not_blocking(self):
        """HIGH priority is not blocking."""
        epic = Epic(
            number="1.1",
            title="Test",
            ice_score=10.0,
            priority="HIGH",
            effort_hours=4.0,
            checkpoint="",
        )
        assert epic.is_blocking is False
