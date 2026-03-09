"""Tests for handoff_generator.py"""

import json
import os
from datetime import UTC, datetime

import pytest

from forge_harness.handoff_generator import HandoffGenerator, create_handoff_generator


@pytest.fixture
def temp_forge_root(tmp_path):
    """Create temporary FORGE root directory structure."""
    forge_root = tmp_path / "FORGE"
    forge_root.mkdir()

    # Create necessary directories
    (forge_root / ".forge/fleet").mkdir()
    (forge_root / ".forge/handoffs").mkdir()

    return forge_root


@pytest.fixture
def sample_fleet_state(temp_forge_root):
    """Create sample fleet state file."""
    state = {
        "version": "2.0",
        "last_updated": "2026-01-27T16:08:20Z",
        "agents": {
            "forge:tech": {
                "session": "forge",
                "window": "tech",
                "agent_type": "claude",
                "context_percent": "92",
                "current_task": "Implement multi-agent provider support",
                "working_directory": "/Users/bogdan/work/FORGE",
                "needs_handoff": True,
                "saved_at": "2026-01-27T15:00:59Z",
            },
            "forge:game": {
                "session": "forge",
                "window": "game",
                "agent_type": "claude",
                "context_percent": "35",
                "current_task": "Frontend design UI/UX",
                "working_directory": "/Users/bogdan/work/FORGE",
                "needs_handoff": False,
                "saved_at": "2026-01-27T15:00:59Z",
            },
            "forge:codex": {
                "session": "forge",
                "window": "codex",
                "agent_type": "claude",
                "context_percent": "95",
                "current_task": "Continue FC-006 Command Palette implementation",
                "working_directory": "/Users/bogdan/work/FORGE/harness/command_center",
                "needs_handoff": True,
                "saved_at": "2026-01-27T15:00:59Z",
            },
            "forge:qa": {
                "session": "forge",
                "window": "qa",
                "agent_type": "claude",
                "context_percent": "unknown",
                "current_task": "Implementation",
                "working_directory": "/Users/bogdan/work/FORGE",
                "needs_handoff": False,
                "saved_at": "2026-01-27T15:00:59Z",
            },
        },
        "fleet_config": {
            "heartbeat_interval_seconds": 30,
            "stale_threshold_seconds": 120,
            "context_handoff_threshold": 50,
            "max_restart_attempts": 3,
            "autonomous_mode": True,
        },
    }

    state_file = temp_forge_root / ".forge/fleet" / "state.json"
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)

    return state


class TestHandoffGenerator:
    """Test HandoffGenerator class."""

    def test_init_creates_directories(self, temp_forge_root):
        """Test that initialization creates necessary directories."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        assert generator.handoffs_dir.exists()
        assert generator.fleet_dir.exists()
        assert generator.context_threshold == 50

    def test_init_with_custom_threshold(self, temp_forge_root):
        """Test initialization with custom context threshold."""
        generator = HandoffGenerator(forge_root=temp_forge_root, context_threshold=70)

        assert generator.context_threshold == 70

    def test_load_fleet_state_success(self, temp_forge_root, sample_fleet_state):
        """Test loading fleet state successfully."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        state = generator.load_fleet_state()

        assert state["version"] == "2.0"
        assert "agents" in state
        assert "forge:tech" in state["agents"]

    def test_load_fleet_state_not_found(self, temp_forge_root):
        """Test loading fleet state when file doesn't exist."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        with pytest.raises(FileNotFoundError) as exc_info:
            generator.load_fleet_state()

        assert "state.json" in str(exc_info.value)

    def test_needs_handoff_explicit_flag(self, temp_forge_root):
        """Test needs_handoff with explicit flag."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        agent_data = {"context_percent": "30", "needs_handoff": True}

        assert generator.needs_handoff(agent_data) is True

    def test_needs_handoff_context_above_threshold(self, temp_forge_root):
        """Test needs_handoff with context above threshold."""
        generator = HandoffGenerator(forge_root=temp_forge_root, context_threshold=50)

        agent_data = {"context_percent": "75", "needs_handoff": False}

        assert generator.needs_handoff(agent_data) is True

    def test_needs_handoff_context_below_threshold(self, temp_forge_root):
        """Test needs_handoff with context below threshold."""
        generator = HandoffGenerator(forge_root=temp_forge_root, context_threshold=50)

        agent_data = {"context_percent": "30", "needs_handoff": False}

        assert generator.needs_handoff(agent_data) is False

    def test_needs_handoff_unknown_context(self, temp_forge_root):
        """Test needs_handoff with unknown context."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        agent_data = {"context_percent": "unknown", "needs_handoff": False}

        assert generator.needs_handoff(agent_data) is False

    def test_generate_handoff_prompt_content(self, temp_forge_root):
        """Test handoff prompt content generation."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        agent_id = "forge:tech"
        agent_data = {
            "session": "forge",
            "window": "tech",
            "agent_type": "claude",
            "context_percent": "92",
            "current_task": "Implement multi-agent provider support",
            "working_directory": "/Users/bogdan/work/FORGE",
            "saved_at": "2026-01-27T15:00:59Z",
        }

        content = generator.generate_handoff_prompt(agent_id, agent_data)

        # Check key sections are present
        assert "# Handoff: forge:tech" in content
        assert "Context Used:** 92%" in content
        assert "Agent Type:** claude" in content
        assert "Implement multi-agent provider support" in content
        assert "Working Directory:** /Users/bogdan/work/FORGE" in content
        assert "## Context Recovery Instructions" in content
        assert "## Resume Command" in content

    def test_generate_handoff_creates_file(self, temp_forge_root, sample_fleet_state):
        """Test that generate_handoff creates markdown file."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        agent_id = "forge:tech"
        agent_data = sample_fleet_state["agents"][agent_id]

        handoff_path = generator.generate_handoff(agent_id, agent_data)

        assert handoff_path is not None
        assert handoff_path.exists()
        assert handoff_path.suffix == ".md"
        assert "forge_tech" in handoff_path.name

    def test_generate_handoff_skips_low_context(self, temp_forge_root, sample_fleet_state):
        """Test that generate_handoff skips agents below threshold."""
        generator = HandoffGenerator(forge_root=temp_forge_root, context_threshold=50)

        agent_id = "forge:game"
        agent_data = sample_fleet_state["agents"][agent_id]

        handoff_path = generator.generate_handoff(agent_id, agent_data)

        assert handoff_path is None

    def test_generate_all_handoffs(self, temp_forge_root, sample_fleet_state):
        """Test generating handoffs for all agents."""
        generator = HandoffGenerator(forge_root=temp_forge_root, context_threshold=50)

        results = generator.generate_all_handoffs()

        # Should generate for tech (92%) and codex (95%), skip game (35%) and qa (unknown)
        assert len(results) == 4
        assert results["forge:tech"] is not None
        assert results["forge:codex"] is not None
        assert results["forge:game"] is None
        assert results["forge:qa"] is None

    def test_list_handoffs_empty(self, temp_forge_root):
        """Test listing handoffs when none exist."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        handoffs = generator.list_handoffs()

        assert len(handoffs) == 0

    def test_list_handoffs_sorted(self, temp_forge_root):
        """Test that handoffs are sorted by modification time."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        # Create some handoff files
        handoff1 = generator.handoffs_dir / "forge_tech_20260127-120000.md"
        handoff2 = generator.handoffs_dir / "forge_codex_20260127-130000.md"

        handoff1.write_text("# Handoff 1")
        handoff2.write_text("# Handoff 2")

        # Touch handoff1 to make it newer
        import time

        time.sleep(0.1)
        handoff1.touch()

        handoffs = generator.list_handoffs()

        assert len(handoffs) == 2
        assert handoffs[0] == handoff1  # Newer file first

    def test_get_handoff_for_agent_found(self, temp_forge_root):
        """Test getting handoff for specific agent."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        # Create handoff file
        handoff_path = generator.handoffs_dir / "forge_tech_20260127-120000.md"
        handoff_path.write_text("# Handoff")

        result = generator.get_handoff_for_agent("forge", "tech")

        assert result == handoff_path

    def test_get_handoff_for_agent_not_found(self, temp_forge_root):
        """Test getting handoff for agent when none exists."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        result = generator.get_handoff_for_agent("forge", "nonexistent")

        assert result is None

    def test_get_handoff_for_agent_returns_newest(self, temp_forge_root):
        """Test that get_handoff_for_agent returns newest file."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        # Create multiple handoffs for same agent
        older = generator.handoffs_dir / "forge_tech_20260127-120000.md"
        newer = generator.handoffs_dir / "forge_tech_20260127-130000.md"

        older.write_text("# Old")
        newer.write_text("# New")

        # Touch newer to ensure it's actually newer
        import time

        time.sleep(0.1)
        newer.touch()

        result = generator.get_handoff_for_agent("forge", "tech")

        assert result == newer

    def test_archive_old_handoffs(self, temp_forge_root):
        """Test archiving old handoff files."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        # Create an old handoff
        old_handoff = generator.handoffs_dir / "forge_tech_old.md"
        old_handoff.write_text("# Old")

        # Set modification time to 10 days ago
        old_time = datetime.now(UTC).timestamp() - (10 * 86400)
        os.utime(old_handoff, (old_time, old_time))

        # Archive handoffs older than 7 days
        archived = generator.archive_old_handoffs(days=7)

        assert len(archived) == 1
        assert not old_handoff.exists()
        assert (generator.handoffs_dir / "archive" / "forge_tech_old.md").exists()

    def test_archive_old_handoffs_keeps_recent(self, temp_forge_root):
        """Test that recent handoffs are not archived."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        # Create a recent handoff
        recent = generator.handoffs_dir / "forge_tech_recent.md"
        recent.write_text("# Recent")

        archived = generator.archive_old_handoffs(days=7)

        assert len(archived) == 0
        assert recent.exists()

    def test_update_fleet_state_with_handoffs(self, temp_forge_root, sample_fleet_state):
        """Test updating fleet state with handoff paths."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        # Create handoff files
        handoff1 = generator.handoffs_dir / "forge_tech_20260127.md"
        handoff2 = generator.handoffs_dir / "forge_codex_20260127.md"
        handoff1.write_text("# Handoff 1")
        handoff2.write_text("# Handoff 2")

        handoff_results = {"forge:tech": handoff1, "forge:codex": handoff2, "forge:game": None}

        generator.update_fleet_state_with_handoffs(handoff_results)

        # Reload state and check
        state = generator.load_fleet_state()

        assert "handoff" in state["agents"]["forge:tech"]
        assert "prompt_path" in state["agents"]["forge:tech"]["handoff"]
        assert "generated_at" in state["agents"]["forge:tech"]["handoff"]

        assert "handoff" in state["agents"]["forge:codex"]
        assert "handoff" not in state["agents"]["forge:game"]


class TestFactoryFunction:
    """Test create_handoff_generator factory function."""

    def test_create_handoff_generator_default(self, temp_forge_root):
        """Test factory function with default parameters."""
        generator = create_handoff_generator(forge_root=temp_forge_root)

        assert isinstance(generator, HandoffGenerator)
        assert generator.forge_root == temp_forge_root
        assert generator.context_threshold == 50

    def test_create_handoff_generator_custom_threshold(self, temp_forge_root):
        """Test factory function with custom threshold."""
        generator = create_handoff_generator(forge_root=temp_forge_root, context_threshold=70)

        assert generator.context_threshold == 70


class TestHandoffFileFormat:
    """Test handoff file format and content."""

    def test_handoff_filename_format(self, temp_forge_root, sample_fleet_state):
        """Test that handoff filename follows expected format."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        agent_id = "forge:tech"
        agent_data = sample_fleet_state["agents"][agent_id]

        handoff_path = generator.generate_handoff(agent_id, agent_data)

        # Filename should be: {session}_{window}_{timestamp}.md
        assert handoff_path.name.startswith("forge_tech_")
        assert handoff_path.suffix == ".md"

    def test_handoff_content_structure(self, temp_forge_root, sample_fleet_state):
        """Test that handoff content has required sections."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        agent_id = "forge:tech"
        agent_data = sample_fleet_state["agents"][agent_id]

        handoff_path = generator.generate_handoff(agent_id, agent_data)

        with open(handoff_path) as f:
            content = f.read()

        # Check required sections
        required_sections = [
            "# Handoff:",
            "## Current Task",
            "## Context Recovery Instructions",
            "## Resume Command",
            "## Technical Details",
            "## Next Steps",
            "## Troubleshooting",
        ]

        for section in required_sections:
            assert section in content, f"Missing section: {section}"

    def test_handoff_includes_agent_details(self, temp_forge_root, sample_fleet_state):
        """Test that handoff includes all agent details."""
        generator = HandoffGenerator(forge_root=temp_forge_root)

        agent_id = "forge:codex"
        agent_data = sample_fleet_state["agents"][agent_id]

        handoff_path = generator.generate_handoff(agent_id, agent_data)

        with open(handoff_path) as f:
            content = f.read()

        # Check agent details are included
        assert "forge:codex" in content
        assert "claude" in content
        assert "95%" in content
        assert "Continue FC-006 Command Palette" in content
        assert "/harness/command_center" in content
