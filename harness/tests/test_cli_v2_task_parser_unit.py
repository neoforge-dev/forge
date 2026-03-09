"""Comprehensive unit tests for forge_harness.cli_v2._task_parser.

Targets 90%+ line coverage by exercising every public and private function
with valid, edge-case, and malformed inputs.

Functions under test
--------------------
- _normalise_priority
- _normalise_status
- _parse_yaml_frontmatter
- _norm_key
- _extract_title_from_markdown
- _extract_body_description
- parse_heartbeat_task  (main entry point)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# ---------------------------------------------------------------------------
# Import helpers — import private symbols for white-box unit testing
# ---------------------------------------------------------------------------
from forge_harness.cli_v2._task_parser import (
    _BOLD_FIELD_RE,
    _PLAIN_FIELD_RE,
    _TASK_LINE_RE,
    _YAML_FRONTMATTER_RE,
    _extract_body_description,
    _extract_title_from_markdown,
    _norm_key,
    _normalise_priority,
    _normalise_status,
    _parse_yaml_frontmatter,
    parse_heartbeat_task,
)

# ===========================================================================
# _normalise_priority
# ===========================================================================


class TestNormalisePriority:
    """Unit tests for _normalise_priority."""

    # --- Mappings to "high" ---

    def test_critical_maps_to_high(self):
        assert _normalise_priority("critical") == "high"

    def test_blocking_maps_to_high(self):
        assert _normalise_priority("blocking") == "high"

    def test_high_maps_to_high(self):
        assert _normalise_priority("high") == "high"

    def test_p0_maps_to_high(self):
        assert _normalise_priority("p0") == "high"

    def test_p1_maps_to_high(self):
        assert _normalise_priority("p1") == "high"

    # --- Mappings to "medium" ---

    def test_medium_maps_to_medium(self):
        assert _normalise_priority("medium") == "medium"

    def test_p2_maps_to_medium(self):
        assert _normalise_priority("p2") == "medium"

    def test_normal_maps_to_medium(self):
        assert _normalise_priority("normal") == "medium"

    # --- Mappings to "low" ---

    def test_low_maps_to_low(self):
        assert _normalise_priority("low") == "low"

    def test_p3_maps_to_low(self):
        assert _normalise_priority("p3") == "low"

    # --- Case insensitivity ---

    def test_uppercase_critical_maps_to_high(self):
        assert _normalise_priority("CRITICAL") == "high"

    def test_mixed_case_blocking_maps_to_high(self):
        assert _normalise_priority("Blocking") == "high"

    def test_uppercase_high_maps_to_high(self):
        assert _normalise_priority("HIGH") == "high"

    def test_uppercase_p0_maps_to_high(self):
        assert _normalise_priority("P0") == "high"

    def test_uppercase_low_maps_to_low(self):
        assert _normalise_priority("LOW") == "low"

    # --- Leading/trailing whitespace ---

    def test_leading_spaces_stripped(self):
        assert _normalise_priority("  high") == "high"

    def test_trailing_spaces_stripped(self):
        assert _normalise_priority("low  ") == "low"

    def test_surrounding_whitespace_stripped(self):
        assert _normalise_priority("  medium  ") == "medium"

    # --- Keyword-in-token matching ---

    def test_phrase_containing_critical(self):
        """'critical fix' should match 'critical' keyword."""
        assert _normalise_priority("critical fix needed") == "high"

    def test_phrase_containing_p1(self):
        assert _normalise_priority("priority: p1 task") == "high"

    # --- Unknown values default to medium ---

    def test_unknown_value_returns_medium(self):
        assert _normalise_priority("somerandompriority") == "medium"

    def test_empty_string_returns_medium(self):
        assert _normalise_priority("") == "medium"

    def test_numeric_string_returns_medium(self):
        assert _normalise_priority("42") == "medium"


# ===========================================================================
# _normalise_status
# ===========================================================================


class TestNormaliseStatus:
    """Unit tests for _normalise_status."""

    def test_already_lowercase_unchanged(self):
        assert _normalise_status("pending") == "pending"

    def test_uppercase_lowercased(self):
        assert _normalise_status("PENDING") == "pending"

    def test_mixed_case_lowercased(self):
        assert _normalise_status("In_Progress") == "in_progress"

    def test_spaces_replaced_with_underscores(self):
        assert _normalise_status("in progress") == "in_progress"

    def test_done_status(self):
        assert _normalise_status("done") == "done"

    def test_in_review_status(self):
        assert _normalise_status("in review") == "in_review"

    def test_leading_trailing_whitespace_stripped(self):
        assert _normalise_status("  done  ") == "done"

    def test_multi_word_with_mixed_case_and_spaces(self):
        assert _normalise_status("In Progress") == "in_progress"

    def test_empty_string(self):
        assert _normalise_status("") == ""


# ===========================================================================
# _parse_yaml_frontmatter
# ===========================================================================


class TestParseYamlFrontmatter:
    """Unit tests for _parse_yaml_frontmatter."""

    def test_returns_none_when_no_frontmatter(self):
        text = "# Just a heading\n\nSome content."
        assert _parse_yaml_frontmatter(text) is None

    def test_returns_none_for_empty_string(self):
        assert _parse_yaml_frontmatter("") is None

    def test_basic_frontmatter_extracted(self):
        text = "---\ntitle: My Task\npriority: high\n---\n\nBody here."
        result = _parse_yaml_frontmatter(text)
        assert result is not None
        assert result["title"] == "My Task"
        assert result["priority"] == "high"

    def test_keys_lowercased(self):
        text = "---\nTitle: My Task\nPRIORITY: high\n---\n"
        result = _parse_yaml_frontmatter(text)
        assert "title" in result
        assert "priority" in result

    def test_keys_with_spaces_replaced_with_underscores(self):
        text = "---\nassigned to: kimi\n---\n"
        result = _parse_yaml_frontmatter(text)
        assert "assigned_to" in result
        assert result["assigned_to"] == "kimi"

    def test_values_stripped_of_whitespace(self):
        text = "---\ntitle:   Spaced Value   \n---\n"
        result = _parse_yaml_frontmatter(text)
        assert result["title"] == "Spaced Value"

    def test_empty_frontmatter_block_returns_empty_dict(self):
        text = "---\n\n---\n\nBody."
        result = _parse_yaml_frontmatter(text)
        assert result == {}

    def test_comment_lines_skipped(self):
        text = "---\n# this is a comment\ntitle: Task\n---\n"
        result = _parse_yaml_frontmatter(text)
        assert "title" in result
        assert len(result) == 1

    def test_lines_without_colon_skipped(self):
        text = "---\ntitle: Task\nno_colon_here\n---\n"
        result = _parse_yaml_frontmatter(text)
        assert "title" in result
        assert "no_colon_here" not in result

    def test_frontmatter_not_at_start_returns_none(self):
        """Frontmatter must be at the very start of the file."""
        text = "Some intro text\n---\ntitle: Task\n---\n"
        assert _parse_yaml_frontmatter(text) is None

    def test_multiple_colons_in_value_handled(self):
        """Value may contain colons — only first partition is used."""
        text = "---\nurl: http://example.com:8080/path\n---\n"
        result = _parse_yaml_frontmatter(text)
        assert result["url"] == "http://example.com:8080/path"

    def test_description_field_extracted(self):
        text = "---\ntitle: T\ndescription: Deploy to prod\n---\n"
        result = _parse_yaml_frontmatter(text)
        assert result["description"] == "Deploy to prod"

    def test_summary_field_extracted(self):
        text = "---\ntitle: T\nsummary: Quick summary line\n---\n"
        result = _parse_yaml_frontmatter(text)
        assert result["summary"] == "Quick summary line"

    def test_agent_field_extracted(self):
        text = "---\ntitle: T\nagent: gemini\n---\n"
        result = _parse_yaml_frontmatter(text)
        assert result["agent"] == "gemini"

    def test_status_field_extracted(self):
        text = "---\ntitle: T\nstatus: in_progress\n---\n"
        result = _parse_yaml_frontmatter(text)
        assert result["status"] == "in_progress"


# ===========================================================================
# _norm_key
# ===========================================================================


class TestNormKey:
    """Unit tests for _norm_key."""

    def test_lowercases_key(self):
        assert _norm_key("Priority") == "priority"

    def test_strips_trailing_colon(self):
        assert _norm_key("Status:") == "status"

    def test_strips_surrounding_whitespace(self):
        assert _norm_key("  Assignee  ") == "assignee"

    def test_strips_colon_and_whitespace_combined(self):
        assert _norm_key("  Priority:  ") == "priority"

    def test_already_normalised(self):
        assert _norm_key("priority") == "priority"

    def test_empty_string(self):
        assert _norm_key("") == ""

    def test_key_with_spaces_preserved_after_lower(self):
        assert _norm_key("Assigned To") == "assigned to"

    def test_multiple_trailing_colons_only_first_removed(self):
        # rstrip(":") removes ALL trailing colons
        assert _norm_key("key::") == "key"


# ===========================================================================
# _extract_title_from_markdown
# ===========================================================================


class TestExtractTitleFromMarkdown:
    """Unit tests for _extract_title_from_markdown."""

    def test_yaml_frontmatter_title_preferred(self, tmp_path: Path):
        text = "---\ntitle: FM Title\n---\n\n# Other Heading\n"
        fp = tmp_path / "task.md"
        assert _extract_title_from_markdown(text, fp) == "FM Title"

    def test_task_line_used_when_no_frontmatter_title(self, tmp_path: Path):
        text = "TASK: Deploy Code Atlas\n\n**Priority:** high\n"
        fp = tmp_path / "dispatch.md"
        assert _extract_title_from_markdown(text, fp) == "Deploy Code Atlas"

    def test_h1_heading_used_when_no_frontmatter_or_task_line(self, tmp_path: Path):
        text = "# Refactor Auth Module\n\nSome description.\n"
        fp = tmp_path / "task.md"
        assert _extract_title_from_markdown(text, fp) == "Refactor Auth Module"

    def test_h1_heading_strips_leading_hash_space(self, tmp_path: Path):
        text = "# Fix Login Bug\n"
        fp = tmp_path / "task.md"
        assert _extract_title_from_markdown(text, fp) == "Fix Login Bug"

    def test_h2_heading_not_treated_as_title(self, tmp_path: Path):
        """## headings should not be picked up as H1."""
        text = "## Section Header\n\nSome content.\n"
        fp = tmp_path / "fix-login-bug.md"
        title = _extract_title_from_markdown(text, fp)
        # Should fall back to filename-derived title
        assert "Fix" in title or "Login" in title or "Bug" in title

    def test_filename_fallback_with_dashes(self, tmp_path: Path):
        text = "Just some content with no heading.\n"
        fp = tmp_path / "deploy-voice-coach.md"
        title = _extract_title_from_markdown(text, fp)
        assert "Deploy" in title
        assert "Voice" in title
        assert "Coach" in title

    def test_filename_fallback_with_underscores(self, tmp_path: Path):
        text = "Some plain content.\n"
        fp = tmp_path / "fix_security_issue.md"
        title = _extract_title_from_markdown(text, fp)
        assert "Fix" in title
        assert "Security" in title
        assert "Issue" in title

    def test_task_line_strips_whitespace(self, tmp_path: Path):
        text = "TASK:   Deploy with spaces   \n"
        fp = tmp_path / "task.md"
        assert _extract_title_from_markdown(text, fp) == "Deploy with spaces"

    def test_frontmatter_title_empty_falls_through_to_task_line(self, tmp_path: Path):
        """Empty frontmatter title field should fall through to TASK: line."""
        text = "---\ntitle:\n---\n\nTASK: Fallthrough Title\n"
        fp = tmp_path / "task.md"
        # Empty frontmatter title should not be used (falsy check)
        title = _extract_title_from_markdown(text, fp)
        assert title == "Fallthrough Title"

    def test_h1_leading_whitespace_stripped(self, tmp_path: Path):
        text = "#   Spaced H1 Title   \n"
        fp = tmp_path / "task.md"
        assert _extract_title_from_markdown(text, fp) == "Spaced H1 Title"

    def test_yaml_frontmatter_title_takes_priority_over_task_line(self, tmp_path: Path):
        text = "---\ntitle: FM Wins\n---\n\nTASK: Task Line\n# H1 Title\n"
        fp = tmp_path / "task.md"
        assert _extract_title_from_markdown(text, fp) == "FM Wins"

    def test_task_line_takes_priority_over_h1(self, tmp_path: Path):
        text = "TASK: Task Line Title\n\n# H1 Title\n"
        fp = tmp_path / "task.md"
        assert _extract_title_from_markdown(text, fp) == "Task Line Title"


# ===========================================================================
# _extract_body_description
# ===========================================================================


class TestExtractBodyDescription:
    """Unit tests for _extract_body_description."""

    def test_returns_body_content_without_frontmatter(self):
        text = "---\ntitle: T\n---\n\nThis is the body paragraph."
        from forge_harness.cli_v2._task_parser import _parse_yaml_frontmatter
        fm = _parse_yaml_frontmatter(text)
        result = _extract_body_description(text, fm)
        assert "This is the body paragraph." in result

    def test_heading_lines_stripped_from_body(self):
        text = "# My Title\n\n## Section\n\nActual description content."
        result = _extract_body_description(text, None)
        assert "My Title" not in result
        assert "Section" not in result
        assert "Actual description content." in result

    def test_triple_dash_lines_stripped_from_body(self):
        text = "---\nSome divider line.\n---\nContent here."
        result = _extract_body_description(text, None)
        assert "Some divider line." in result
        assert "Content here." in result
        # The --- lines themselves are stripped
        assert "---" not in result

    def test_empty_input_returns_empty_string(self):
        result = _extract_body_description("", None)
        assert result == ""

    def test_only_headings_returns_empty_string(self):
        text = "# Title\n## Subtitle\n### Nested\n"
        result = _extract_body_description(text, None)
        assert result == ""

    def test_description_truncated_at_500_chars(self):
        long_text = "A" * 600
        result = _extract_body_description(long_text, None)
        assert len(result) <= 500

    def test_exactly_500_chars_not_truncated(self):
        text = "B" * 500
        result = _extract_body_description(text, None)
        assert len(result) == 500

    def test_frontmatter_stripped_before_body_extraction(self):
        text = "---\ntitle: T\npriority: high\n---\n\nThis is the real body."
        fm = _parse_yaml_frontmatter(text)
        result = _extract_body_description(text, fm)
        assert "This is the real body." in result
        # Frontmatter keys should not appear as body content
        assert "priority" not in result

    def test_multiline_body_preserved(self):
        text = "First paragraph line.\nSecond line of paragraph."
        result = _extract_body_description(text, None)
        assert "First paragraph line." in result
        assert "Second line of paragraph." in result

    def test_blank_lines_preserved_in_output(self):
        """Blank lines should be kept — they join as empty lines."""
        text = "Para one.\n\nPara two."
        result = _extract_body_description(text, None)
        assert "Para one." in result
        assert "Para two." in result

    def test_no_frontmatter_uses_full_text(self):
        text = "No frontmatter here.\nJust plain content."
        result = _extract_body_description(text, None)
        assert "No frontmatter here." in result

    def test_frontmatter_only_no_body_returns_empty(self):
        """When frontmatter exists but body is empty, return empty string."""
        text = "---\ntitle: T\n---\n"
        fm = _parse_yaml_frontmatter(text)
        result = _extract_body_description(text, fm)
        assert result == ""


# ===========================================================================
# parse_heartbeat_task — YAML frontmatter format
# ===========================================================================


class TestParseHeartbeatTaskYamlFrontmatter:
    """Integration-level tests for parse_heartbeat_task with YAML input."""

    def _make_yaml(self, tmp_path: Path, name: str = "task.md", **fields) -> Path:
        defaults = {
            "title": "Default Task Title",
            "priority": "medium",
            "status": "pending",
            "assignee": "gemini",
        }
        defaults.update(fields)
        lines = ["---"]
        for k, v in defaults.items():
            lines.append(f"{k}: {v}")
        lines += ["---", "", "Body content here."]
        p = tmp_path / name
        p.write_text("\n".join(lines))
        return p

    def test_returns_dict_with_all_required_keys(self, tmp_path: Path):
        f = self._make_yaml(tmp_path)
        meta = parse_heartbeat_task(f)
        for key in ("title", "priority", "status", "assignee", "description", "source_file"):
            assert key in meta, f"Missing key: {key}"

    def test_source_file_is_absolute_path_string(self, tmp_path: Path):
        f = self._make_yaml(tmp_path)
        meta = parse_heartbeat_task(f)
        assert meta["source_file"] == str(f)

    def test_title_extracted_from_frontmatter(self, tmp_path: Path):
        f = self._make_yaml(tmp_path, title="Implement OAuth2 Flow")
        assert parse_heartbeat_task(f)["title"] == "Implement OAuth2 Flow"

    def test_priority_high_from_frontmatter(self, tmp_path: Path):
        f = self._make_yaml(tmp_path, priority="high")
        assert parse_heartbeat_task(f)["priority"] == "high"

    def test_priority_critical_normalised_to_high(self, tmp_path: Path):
        f = self._make_yaml(tmp_path, priority="critical")
        assert parse_heartbeat_task(f)["priority"] == "high"

    def test_priority_blocking_normalised_to_high(self, tmp_path: Path):
        f = self._make_yaml(tmp_path, priority="blocking")
        assert parse_heartbeat_task(f)["priority"] == "high"

    def test_priority_p0_normalised_to_high(self, tmp_path: Path):
        f = self._make_yaml(tmp_path, priority="p0")
        assert parse_heartbeat_task(f)["priority"] == "high"

    def test_priority_p1_normalised_to_high(self, tmp_path: Path):
        f = self._make_yaml(tmp_path, priority="p1")
        assert parse_heartbeat_task(f)["priority"] == "high"

    def test_priority_p2_normalised_to_medium(self, tmp_path: Path):
        f = self._make_yaml(tmp_path, priority="p2")
        assert parse_heartbeat_task(f)["priority"] == "medium"

    def test_priority_p3_normalised_to_low(self, tmp_path: Path):
        f = self._make_yaml(tmp_path, priority="p3")
        assert parse_heartbeat_task(f)["priority"] == "low"

    def test_priority_low_from_frontmatter(self, tmp_path: Path):
        f = self._make_yaml(tmp_path, priority="low")
        assert parse_heartbeat_task(f)["priority"] == "low"

    def test_status_from_frontmatter(self, tmp_path: Path):
        f = self._make_yaml(tmp_path, status="in_progress")
        assert parse_heartbeat_task(f)["status"] == "in_progress"

    def test_status_with_spaces_normalised(self, tmp_path: Path):
        f = self._make_yaml(tmp_path, status="in progress")
        assert parse_heartbeat_task(f)["status"] == "in_progress"

    def test_assignee_from_frontmatter(self, tmp_path: Path):
        f = self._make_yaml(tmp_path, assignee="kimi")
        assert parse_heartbeat_task(f)["assignee"] == "kimi"

    def test_description_from_frontmatter(self, tmp_path: Path):
        f = self._make_yaml(tmp_path, description="Deploy to Railway prod")
        assert parse_heartbeat_task(f)["description"] == "Deploy to Railway prod"

    def test_summary_used_as_description_when_no_description(self, tmp_path: Path):
        # Need a body line after the closing --- so the frontmatter regex anchor holds
        content = "---\ntitle: T\nsummary: Summary line text\n---\n\nBody.\n"
        f = tmp_path / "task.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["description"] == "Summary line text"

    def test_missing_priority_defaults_to_medium(self, tmp_path: Path):
        content = "---\ntitle: Minimal Task\n---\n\nBody."
        f = tmp_path / "task.md"
        f.write_text(content)
        assert parse_heartbeat_task(f)["priority"] == "medium"

    def test_missing_status_defaults_to_pending(self, tmp_path: Path):
        content = "---\ntitle: Minimal Task\n---\n\nBody."
        f = tmp_path / "task.md"
        f.write_text(content)
        assert parse_heartbeat_task(f)["status"] == "pending"

    def test_missing_assignee_defaults_to_empty_string(self, tmp_path: Path):
        content = "---\ntitle: Minimal Task\n---\n\nBody."
        f = tmp_path / "task.md"
        f.write_text(content)
        assert parse_heartbeat_task(f)["assignee"] == ""

    def test_assigned_to_field_used_as_assignee(self, tmp_path: Path):
        content = "---\ntitle: T\nassigned to: opencode\n---\n"
        f = tmp_path / "task.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["assignee"] == "opencode"

    def test_agent_field_used_as_assignee_fallback(self, tmp_path: Path):
        content = "---\ntitle: T\nagent: cursor\n---\n"
        f = tmp_path / "task.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["assignee"] == "cursor"

    def test_body_used_as_description_when_no_fm_description(self, tmp_path: Path):
        content = "---\ntitle: T\n---\n\nThis is a body paragraph for description."
        f = tmp_path / "task.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert "body paragraph" in meta["description"]


# ===========================================================================
# parse_heartbeat_task — simple / human-authored format
# ===========================================================================


class TestParseHeartbeatTaskSimpleFormat:
    """Tests for parse_heartbeat_task with simple header (no frontmatter) input."""

    def _make_simple(
        self,
        tmp_path: Path,
        name: str = "task.md",
        title: str = "# Fix the Bug",
        extra: str = "",
    ) -> Path:
        content = f"{title}\n\n{extra}\n"
        p = tmp_path / name
        p.write_text(content)
        return p

    def test_h1_title_extracted(self, tmp_path: Path):
        f = self._make_simple(tmp_path, title="# Refactor Module")
        assert parse_heartbeat_task(f)["title"] == "Refactor Module"

    def test_bold_priority_high(self, tmp_path: Path):
        f = self._make_simple(tmp_path, extra="**Priority:** high")
        assert parse_heartbeat_task(f)["priority"] == "high"

    def test_bold_priority_low(self, tmp_path: Path):
        f = self._make_simple(tmp_path, extra="**Priority:** low")
        assert parse_heartbeat_task(f)["priority"] == "low"

    def test_bold_priority_critical_maps_to_high(self, tmp_path: Path):
        f = self._make_simple(tmp_path, extra="**Priority:** CRITICAL")
        assert parse_heartbeat_task(f)["priority"] == "high"

    def test_bold_priority_blocking_maps_to_high(self, tmp_path: Path):
        f = self._make_simple(tmp_path, extra="**Priority:** blocking")
        assert parse_heartbeat_task(f)["priority"] == "high"

    def test_bold_status_extracted(self, tmp_path: Path):
        f = self._make_simple(tmp_path, extra="**Status:** done")
        assert parse_heartbeat_task(f)["status"] == "done"

    def test_bold_assignee_extracted(self, tmp_path: Path):
        f = self._make_simple(tmp_path, extra="**Assigned to:** cursor")
        assert parse_heartbeat_task(f)["assignee"] == "cursor"

    def test_bold_agent_extracted_as_assignee(self, tmp_path: Path):
        f = self._make_simple(tmp_path, extra="**Agent:** gemini")
        assert parse_heartbeat_task(f)["assignee"] == "gemini"

    def test_bold_assignee_with_colon_variant(self, tmp_path: Path):
        f = self._make_simple(tmp_path, extra="**Assignee:** kimi")
        assert parse_heartbeat_task(f)["assignee"] == "kimi"

    def test_task_colon_line_as_title(self, tmp_path: Path):
        content = "TASK: Deploy Code Atlas\n\n**Priority:** high\n"
        f = tmp_path / "dispatch-xyz.md"
        f.write_text(content)
        assert parse_heartbeat_task(f)["title"] == "Deploy Code Atlas"

    def test_plain_priority_line(self, tmp_path: Path):
        content = "# Some Task\n\nPriority: high\n\nDescription."
        f = tmp_path / "task.md"
        f.write_text(content)
        assert parse_heartbeat_task(f)["priority"] == "high"

    def test_plain_status_not_captured_via_bold_falls_to_default(self, tmp_path: Path):
        """Without a bold **Status:** marker, status defaults to pending."""
        content = "# Some Task\n\nJust a description without status field.\n"
        f = tmp_path / "task.md"
        f.write_text(content)
        assert parse_heartbeat_task(f)["status"] == "pending"

    def test_plain_assignee_line(self, tmp_path: Path):
        content = "# Task\n\nAssigned to: opencode\n"
        f = tmp_path / "task.md"
        f.write_text(content)
        assert parse_heartbeat_task(f)["assignee"] == "opencode"

    def test_plain_agent_line(self, tmp_path: Path):
        content = "# Task\n\nAgent: kimi\n"
        f = tmp_path / "task.md"
        f.write_text(content)
        assert parse_heartbeat_task(f)["assignee"] == "kimi"

    def test_filename_fallback_title_dashes(self, tmp_path: Path):
        content = "Just content, no heading here at all.\n"
        f = tmp_path / "deploy-voice-coach.md"
        f.write_text(content)
        title = parse_heartbeat_task(f)["title"]
        assert "Deploy" in title

    def test_filename_fallback_title_underscores(self, tmp_path: Path):
        content = "Just content, no heading.\n"
        f = tmp_path / "fix_security_issue.md"
        f.write_text(content)
        title = parse_heartbeat_task(f)["title"]
        assert "Fix" in title

    def test_description_from_body(self, tmp_path: Path):
        content = "# Title\n\nThis is the description body paragraph."
        f = tmp_path / "task.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert "description body paragraph" in meta["description"]

    def test_body_description_excludes_headings(self, tmp_path: Path):
        content = "# Title\n\n## Section\n\nActual content paragraph."
        f = tmp_path / "task.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert "Section" not in meta["description"]
        assert "Actual content paragraph." in meta["description"]

    def test_unknown_priority_defaults_to_medium(self, tmp_path: Path):
        f = self._make_simple(tmp_path, extra="**Priority:** someweirdvalue")
        assert parse_heartbeat_task(f)["priority"] == "medium"


# ===========================================================================
# parse_heartbeat_task — error cases
# ===========================================================================


class TestParseHeartbeatTaskErrors:
    """Error handling and edge cases for parse_heartbeat_task."""

    def test_empty_file_raises_value_error(self, tmp_path: Path):
        f = tmp_path / "empty.md"
        f.write_text("")
        with pytest.raises(ValueError, match="empty"):
            parse_heartbeat_task(f)

    def test_whitespace_only_raises_value_error(self, tmp_path: Path):
        f = tmp_path / "blank.md"
        f.write_text("   \n\n   \t  \n")
        with pytest.raises(ValueError, match="empty"):
            parse_heartbeat_task(f)

    def test_nonexistent_file_raises_value_error(self, tmp_path: Path):
        f = tmp_path / "does-not-exist.md"
        with pytest.raises(ValueError, match="Cannot read"):
            parse_heartbeat_task(f)

    def test_oserror_wrapped_in_value_error(self, tmp_path: Path):
        """OSError from read_text is re-raised as ValueError."""
        f = tmp_path / "task.md"
        f.write_text("some content")
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            with pytest.raises(ValueError, match="Cannot read"):
                parse_heartbeat_task(f)

    def test_malformed_yaml_falls_back_gracefully(self, tmp_path: Path):
        """Broken YAML frontmatter should not raise — falls back to body."""
        content = "---\ntitle: [broken yaml list\n---\n\n# Actual Title\n\n**Priority:** medium\n"
        f = tmp_path / "broken.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["title"]  # Some title is extracted

    def test_file_with_only_h1_heading_works(self, tmp_path: Path):
        f = tmp_path / "heading-only.md"
        f.write_text("# Just a Heading\n")
        meta = parse_heartbeat_task(f)
        assert meta["title"] == "Just a Heading"
        assert meta["priority"] == "medium"
        assert meta["status"] == "pending"
        assert meta["assignee"] == ""

    def test_very_long_body_description_truncated(self, tmp_path: Path):
        f = tmp_path / "long.md"
        long_body = "word " * 200  # > 500 chars
        f.write_text(f"# Title\n\n{long_body}\n")
        meta = parse_heartbeat_task(f)
        assert len(meta["description"]) <= 500

    def test_file_with_multiple_bold_priority_uses_first(self, tmp_path: Path):
        content = "# Task\n\n**Priority:** high\n**Priority:** low\n"
        f = tmp_path / "task.md"
        f.write_text(content)
        # First matching bold field wins
        assert parse_heartbeat_task(f)["priority"] == "high"

    def test_file_with_multiple_bold_status_uses_first(self, tmp_path: Path):
        content = "# Task\n\n**Status:** done\n**Status:** pending\n"
        f = tmp_path / "task.md"
        f.write_text(content)
        assert parse_heartbeat_task(f)["status"] == "done"

    def test_file_with_multiple_bold_assignee_uses_first(self, tmp_path: Path):
        content = "# Task\n\n**Assigned to:** alice\n**Assigned to:** bob\n"
        f = tmp_path / "task.md"
        f.write_text(content)
        assert parse_heartbeat_task(f)["assignee"] == "alice"


# ===========================================================================
# parse_heartbeat_task — dispatch file conventions
# ===========================================================================


class TestParseHeartbeatTaskDispatchFormat:
    """Tests for dispatch-file-style markdown with TASK: prefix."""

    def test_task_line_title(self, tmp_path: Path):
        content = "TASK: Fix Rate Limiter Bug\n\n**Priority:** high\n**Status:** pending\n"
        f = tmp_path / "dispatch-fix.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["title"] == "Fix Rate Limiter Bug"
        assert meta["priority"] == "high"

    def test_task_line_with_agent_assignment(self, tmp_path: Path):
        content = (
            "TASK: Deploy Interview Simulator\n\n"
            "**Agent:** opencode\n"
            "**Priority:** p1\n"
        )
        f = tmp_path / "dispatch-is.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["title"] == "Deploy Interview Simulator"
        assert meta["assignee"] == "opencode"
        assert meta["priority"] == "high"

    def test_task_line_multiline_content(self, tmp_path: Path):
        content = (
            "TASK: Comprehensive Coverage Expansion\n\n"
            "This is a detailed description.\n"
            "It spans multiple lines.\n"
        )
        f = tmp_path / "dispatch-coverage.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["title"] == "Comprehensive Coverage Expansion"
        assert "detailed description" in meta["description"]


# ===========================================================================
# Regex pattern sanity checks
# ===========================================================================


class TestRegexPatterns:
    """Sanity-check the module-level compiled regex patterns."""

    def test_yaml_frontmatter_re_matches_valid_block(self):
        text = "---\ntitle: T\n---\n\nBody."
        assert _YAML_FRONTMATTER_RE.match(text) is not None

    def test_yaml_frontmatter_re_no_match_without_opening(self):
        text = "title: T\n---\n\nBody."
        assert _YAML_FRONTMATTER_RE.match(text) is None

    def test_bold_field_re_matches_priority_line(self):
        line = "**Priority:** high"
        m = _BOLD_FIELD_RE.search(line)
        assert m is not None
        assert m.group("key") == "Priority:"
        assert m.group("value") == "high"

    def test_bold_field_re_matches_status_line(self):
        line = "**Status:** in_progress"
        m = _BOLD_FIELD_RE.search(line)
        assert m is not None
        assert m.group("key") == "Status:"

    def test_bold_field_re_matches_assigned_to_line(self):
        line = "**Assigned to:** kimi"
        m = _BOLD_FIELD_RE.search(line)
        assert m is not None
        assert m.group("key") == "Assigned to:"
        assert m.group("value") == "kimi"

    def test_bold_field_re_no_match_plain_text(self):
        line = "Priority: high"
        m = _BOLD_FIELD_RE.search(line)
        assert m is None

    def test_task_line_re_matches(self):
        line = "TASK: Deploy to Production"
        m = _TASK_LINE_RE.search(line)
        assert m is not None
        assert m.group(1) == "Deploy to Production"

    def test_task_line_re_no_match_lowercase_task(self):
        line = "task: Deploy to Production"
        m = _TASK_LINE_RE.search(line)
        assert m is None

    def test_plain_field_re_matches_priority(self):
        line = "Priority: high"
        m = _PLAIN_FIELD_RE.search(line)
        assert m is not None
        assert "priority" in m.group("key").lower()

    def test_plain_field_re_matches_agent(self):
        line = "Agent: gemini"
        m = _PLAIN_FIELD_RE.search(line)
        assert m is not None

    def test_plain_field_re_case_insensitive(self):
        line = "PRIORITY: high"
        m = _PLAIN_FIELD_RE.search(line)
        assert m is not None

    def test_plain_field_re_matches_assigned_to_with_space(self):
        line = "Assigned to: opencode"
        m = _PLAIN_FIELD_RE.search(line)
        assert m is not None

    def test_plain_field_re_matches_assignee(self):
        line = "Assignee: kimi"
        m = _PLAIN_FIELD_RE.search(line)
        assert m is not None

    def test_plain_field_re_matches_status(self):
        line = "Status: done"
        m = _PLAIN_FIELD_RE.search(line)
        assert m is not None


# ===========================================================================
# Integration: mixed / complex documents
# ===========================================================================


class TestParseHeartbeatTaskMixed:
    """Complex real-world-style document parsing."""

    def test_full_yaml_frontmatter_document(self, tmp_path: Path):
        content = """\
---
title: Implement Voice Coach WebSocket API
priority: p1
status: in_progress
assignee: opencode
description: Build the real-time voice streaming endpoint.
---

## Background

The voice coach needs a WebSocket connection for real-time audio.

## Acceptance Criteria

- Connect to audio stream
- Transcribe in real-time
"""
        f = tmp_path / "vc-websocket.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["title"] == "Implement Voice Coach WebSocket API"
        assert meta["priority"] == "high"
        assert meta["status"] == "in_progress"
        assert meta["assignee"] == "opencode"
        assert meta["description"] == "Build the real-time voice streaming endpoint."
        assert meta["source_file"] == str(f)

    def test_full_simple_document(self, tmp_path: Path):
        content = """\
# Fix Security Vulnerability in Auth Module

**Priority:** critical
**Status:** pending
**Assigned to:** kimi

A critical security vulnerability was found in the JWT verification step.
The fix involves stricter token validation and expiry checks.
"""
        f = tmp_path / "fix-security.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["title"] == "Fix Security Vulnerability in Auth Module"
        assert meta["priority"] == "high"
        assert meta["status"] == "pending"
        assert meta["assignee"] == "kimi"
        assert "security vulnerability" in meta["description"].lower()

    def test_dispatch_file_with_all_fields(self, tmp_path: Path):
        content = """\
TASK: Run harness coverage expansion

**Priority:** p2
**Status:** pending
**Agent:** gemini

Expand test coverage for the harness modules.
Focus on _task_parser, token_budget, and approval_queue.
"""
        f = tmp_path / "dispatch-coverage.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["title"] == "Run harness coverage expansion"
        assert meta["priority"] == "medium"
        assert meta["assignee"] == "gemini"

    def test_yaml_frontmatter_with_no_body_description_uses_fm(self, tmp_path: Path):
        # Must have a trailing newline + content after closing --- for strip() not to eat the anchor
        content = """\
---
title: Empty Body Task
priority: low
description: Frontmatter description only.
---

Some minimal body.
"""
        f = tmp_path / "fm-only.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["description"] == "Frontmatter description only."
        assert meta["priority"] == "low"

    def test_frontmatter_assignee_takes_priority_over_bold(self, tmp_path: Path):
        """When both frontmatter assignee and bold marker exist, FM wins."""
        content = """\
---
title: T
assignee: fm-agent
---

**Assigned to:** bold-agent
"""
        f = tmp_path / "task.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["assignee"] == "fm-agent"

    def test_frontmatter_priority_takes_priority_over_bold(self, tmp_path: Path):
        """When both frontmatter priority and bold marker exist, FM wins."""
        content = """\
---
title: T
priority: low
---

**Priority:** critical
"""
        f = tmp_path / "task.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["priority"] == "low"

    def test_leading_whitespace_in_file_is_stripped(self, tmp_path: Path):
        """Files with leading newlines are stripped before parsing."""
        content = "\n\n\n# Title After Whitespace\n\n**Priority:** high\n"
        f = tmp_path / "whitespace.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["title"] == "Title After Whitespace"
        assert meta["priority"] == "high"
