"""Tests for AtlasPrioritizer integration."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from forge_harness.meta_learning.bridges.atlas_prioritizer import AtlasPrioritizer


@pytest.fixture
def mock_atlas_bridge():
    """Mock Code Atlas bridge."""
    bridge = AsyncMock()
    bridge.enabled = True
    bridge.health_check = AsyncMock(return_value=True)
    bridge.search_similar_problems = AsyncMock(return_value=[])
    bridge.get_signals = AsyncMock(return_value=MagicMock(related_patterns=[]))
    return bridge


@pytest.fixture
def prioritizer(mock_atlas_bridge):
    """AtlasPrioritizer instance."""
    return AtlasPrioritizer(mock_atlas_bridge)


@pytest.mark.asyncio
async def test_is_available_when_healthy(prioritizer, mock_atlas_bridge):
    """Test is_available returns True when Atlas is healthy."""
    assert await prioritizer.is_available() is True
    mock_atlas_bridge.health_check.assert_called_once()


@pytest.mark.asyncio
async def test_is_available_when_unhealthy(mock_atlas_bridge):
    """Test is_available returns False when Atlas is unhealthy."""
    mock_atlas_bridge.health_check = AsyncMock(return_value=False)
    prioritizer = AtlasPrioritizer(mock_atlas_bridge)

    assert await prioritizer.is_available() is False


@pytest.mark.asyncio
async def test_is_available_when_disabled(mock_atlas_bridge):
    """Test is_available returns False when Atlas is disabled."""
    mock_atlas_bridge.enabled = False
    prioritizer = AtlasPrioritizer(mock_atlas_bridge)

    assert await prioritizer.is_available() is False


@pytest.mark.asyncio
async def test_filter_by_historical_success_unavailable(prioritizer, mock_atlas_bridge):
    """Test filter gracefully handles unavailable Atlas."""
    mock_atlas_bridge.health_check = AsyncMock(return_value=False)

    findings = [
        {"category": "sql-injection", "title": "SQL Injection", "description": "Unsafe query"},
    ]

    result = await prioritizer.filter_by_historical_success(findings)
    assert result == findings  # Returns unchanged


@pytest.mark.asyncio
async def test_filter_by_historical_success_with_solutions(prioritizer, mock_atlas_bridge):
    """Test filter adds past solutions to findings."""
    mock_atlas_bridge.search_similar_problems = AsyncMock(return_value=[
        {
            "entity_name": "SQL Injection in user query",
            "combined_score": 0.8,
            "entity_id": "prob-123",
            "properties": {"solution": "Use parameterized queries"}
        }
    ])

    findings = [
        {
            "category": "sql-injection",
            "title": "SQL Injection",
            "description": "Unsafe query",
        },
    ]

    result = await prioritizer.filter_by_historical_success(findings)

    assert len(result) == 1
    assert "metadata" in result[0]
    assert "past_solutions" in result[0]["metadata"]
    assert result[0]["metadata"]["solution_count"] == 1
    assert "Past Solutions" in result[0]["description"]


@pytest.mark.asyncio
async def test_filter_marks_novel_problems(prioritizer, mock_atlas_bridge):
    """Test filter marks findings with no past solutions as novel."""
    mock_atlas_bridge.search_similar_problems = AsyncMock(return_value=[])

    findings = [
        {
            "category": "new-vuln",
            "title": "Novel Vulnerability",
            "description": "Never seen before",
        },
    ]

    result = await prioritizer.filter_by_historical_success(findings)

    assert len(result) == 1
    assert "metadata" in result[0]
    assert result[0]["metadata"]["novel_problem"] is True


@pytest.mark.asyncio
async def test_get_recently_changed_files(prioritizer, mock_atlas_bridge):
    """Test getting recently changed files."""
    # Mock signals with file patterns
    pattern = MagicMock()
    pattern.entities = ["src/api.py", "tests/test_api.py", "SomeClass"]
    pattern.session_count = 3

    signals = MagicMock()
    signals.related_patterns = [pattern]

    mock_atlas_bridge.get_signals = AsyncMock(return_value=signals)

    result = await prioritizer.get_recently_changed_files(
        domain="test-domain",
        project="test-project",
        days_back=7
    )

    assert len(result) == 2  # Only file paths, not SomeClass
    assert "src/api.py" in result
    assert "tests/test_api.py" in result


@pytest.mark.asyncio
async def test_get_file_hotspots(prioritizer, mock_atlas_bridge):
    """Test getting file hotspots."""
    # Mock signals with file patterns
    pattern1 = MagicMock()
    pattern1.entities = ["src/auth.py", "src/api.py"]
    pattern1.session_count = 10

    pattern2 = MagicMock()
    pattern2.entities = ["src/auth.py", "tests/test.py"]
    pattern2.session_count = 5

    signals = MagicMock()
    signals.related_patterns = [pattern1, pattern2]

    mock_atlas_bridge.get_signals = AsyncMock(return_value=signals)

    result = await prioritizer.get_file_hotspots(
        domain="test-domain",
        project="test-project",
        min_mentions=3
    )

    assert "src/auth.py" in result
    assert result["src/auth.py"] == 15  # 10 + 5
    assert "src/api.py" in result
    assert result["src/api.py"] == 10


@pytest.mark.asyncio
async def test_boost_hotspot_priorities(prioritizer, mock_atlas_bridge):
    """Test boosting priorities for hotspot files."""
    # Mock hotspots
    prioritizer._hotspot_cache = {
        "src/auth.py": 20,
        "src/api.py": 3,  # Below threshold
    }

    findings = [
        {
            "file_path": "src/auth.py",
            "severity": "medium",
            "title": "Issue in hotspot",
        },
        {
            "file_path": "src/api.py",
            "severity": "medium",
            "title": "Issue not in hotspot",
        },
        {
            "file_path": "src/utils.py",
            "severity": "medium",
            "title": "Issue in non-hotspot",
        },
    ]

    result = await prioritizer.boost_hotspot_priorities(
        findings,
        domain="test-domain",
        project="test-project",
        boost_threshold=5
    )

    # First finding should be boosted (high mentions)
    assert result[0]["severity"] == "high"
    assert result[0]["metadata"]["priority_boosted"] is True
    assert result[0]["metadata"]["hotspot_mentions"] == 20

    # Second and third should remain medium
    assert result[1]["severity"] == "medium"
    assert result[2]["severity"] == "medium"


@pytest.mark.asyncio
async def test_clear_cache(prioritizer):
    """Test cache clearing."""
    prioritizer._hotspot_cache = {"file.py": 10}
    prioritizer.clear_cache()
    assert prioritizer._hotspot_cache is None
