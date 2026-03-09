"""
Tests for SimpleHistory - append-only pattern history tracker.

Tests cover:
1. Basic recording and retrieval
2. Success rate calculations
3. Recent pattern filtering
4. Decision threshold logic
5. Empty history defaults
6. Malformed line handling
"""

import json
from datetime import datetime

from forge_harness.simple_history import SimpleHistory


class TestRecordAndRetrieve:
    """Test basic recording and retrieval functionality."""

    def test_record_and_retrieve(self, tmp_path):
        """Test that records are written and can be retrieved."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        # Record a successful action
        history.record(
            domain="test-domain",
            project="test-project",
            action="deploy",
            success=True,
            context={"duration": 30},
        )

        # Verify record was written
        assert history_file.exists()
        assert history_file.stat().st_size > 0

        # Verify success rate is 1.0 (100%)
        success_rate = history.get_success_rate("test-domain", "deploy")
        assert success_rate == 1.0

    def test_record_multiple_actions(self, tmp_path):
        """Test recording multiple different actions."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        # Record different actions
        history.record("domain1", "proj1", "build", True)
        history.record("domain1", "proj1", "test", False)
        history.record("domain2", "proj2", "deploy", True)

        # Verify success rates are independent
        assert history.get_success_rate("domain1", "build") == 1.0
        assert history.get_success_rate("domain1", "test") == 0.0
        assert history.get_success_rate("domain2", "deploy") == 1.0

    def test_record_creates_directory(self, tmp_path):
        """Test that missing directories are created automatically."""
        nested_path = tmp_path / "nested" / "deep" / "history.jsonl"
        history = SimpleHistory(nested_path)

        # Directory should be created
        assert nested_path.parent.exists()
        assert nested_path.exists()


class TestSuccessRateCalculation:
    """Test success rate calculation with known data."""

    def test_success_rate_50_percent(self, tmp_path):
        """Test success rate with 2 successes and 2 failures."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        # Record 2 successes and 2 failures
        history.record("domain", "proj", "action", True)
        history.record("domain", "proj", "action", False)
        history.record("domain", "proj", "action", True)
        history.record("domain", "proj", "action", False)

        success_rate = history.get_success_rate("domain", "action")
        assert success_rate == 0.5

    def test_success_rate_all_failures(self, tmp_path):
        """Test success rate with all failures."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        history.record("domain", "proj", "action", False)
        history.record("domain", "proj", "action", False)
        history.record("domain", "proj", "action", False)

        success_rate = history.get_success_rate("domain", "action")
        assert success_rate == 0.0

    def test_success_rate_limit_parameter(self, tmp_path):
        """Test that limit parameter restricts records considered."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        # Record 10 successes, then 5 failures
        for _ in range(10):
            history.record("domain", "proj", "action", True)
        for _ in range(5):
            history.record("domain", "proj", "action", False)

        # With limit=5, should only see the 5 most recent (all failures)
        success_rate = history.get_success_rate("domain", "action", limit=5)
        assert success_rate == 0.0

        # With limit=15, should see all records (10 success, 5 fail = 0.666...)
        success_rate = history.get_success_rate("domain", "action", limit=15)
        assert abs(success_rate - (10 / 15)) < 0.01

    def test_success_rate_filters_by_domain_and_action(self, tmp_path):
        """Test that success rate only considers matching domain and action."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        # Record with different domains and actions
        history.record("domain1", "proj", "build", True)
        history.record("domain1", "proj", "build", True)
        history.record("domain1", "proj", "test", False)
        history.record("domain2", "proj", "build", False)

        # Only domain1 + build should count as 100%
        success_rate = history.get_success_rate("domain1", "build")
        assert success_rate == 1.0


class TestGetRecentPatterns:
    """Test retrieval of recent successful patterns."""

    def test_get_recent_patterns_returns_only_successful(self, tmp_path):
        """Test that get_recent_patterns returns only successful records."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        # Record mixed successes and failures
        history.record("domain", "proj", "action", False, {"error": "fail1"})
        history.record("domain", "proj", "action", True, {"duration": 10})
        history.record("domain", "proj", "action", False, {"error": "fail2"})
        history.record("domain", "proj", "action", True, {"duration": 15})

        patterns = history.get_recent_patterns("domain", "action", limit=5)

        # Should only have 2 successful records
        assert len(patterns) == 2
        assert all(p["success"] for p in patterns)

    def test_get_recent_patterns_respects_limit(self, tmp_path):
        """Test that get_recent_patterns respects the limit parameter."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        # Record 10 successful actions
        for i in range(10):
            history.record("domain", "proj", "action", True, {"index": i})

        # Request only 3
        patterns = history.get_recent_patterns("domain", "action", limit=3)
        assert len(patterns) == 3

    def test_get_recent_patterns_preserves_context(self, tmp_path):
        """Test that context data is preserved in patterns."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        context_data = {"duration": 42, "version": "1.2.3"}
        history.record("domain", "proj", "action", True, context_data)

        patterns = history.get_recent_patterns("domain", "action")

        assert len(patterns) == 1
        assert patterns[0]["context"] == context_data

    def test_get_recent_patterns_empty_when_no_successes(self, tmp_path):
        """Test that get_recent_patterns returns empty list when no successes."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        # Record only failures
        history.record("domain", "proj", "action", False)
        history.record("domain", "proj", "action", False)

        patterns = history.get_recent_patterns("domain", "action")
        assert patterns == []


class TestShouldProceedThreshold:
    """Test the decision logic threshold."""

    def test_should_proceed_above_threshold(self, tmp_path):
        """Test should_proceed returns True when above threshold."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        # 70% success rate (7 successes, 3 failures)
        for _ in range(7):
            history.record("domain", "proj", "action", True)
        for _ in range(3):
            history.record("domain", "proj", "action", False)

        should_proceed, rate = history.should_proceed("domain", "action", threshold=0.6)

        assert should_proceed is True
        assert abs(rate - 0.7) < 0.01

    def test_should_proceed_below_threshold(self, tmp_path):
        """Test should_proceed returns False when below threshold."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        # 30% success rate (3 successes, 7 failures)
        for _ in range(3):
            history.record("domain", "proj", "action", True)
        for _ in range(7):
            history.record("domain", "proj", "action", False)

        should_proceed, rate = history.should_proceed("domain", "action", threshold=0.6)

        assert should_proceed is False
        assert abs(rate - 0.3) < 0.01

    def test_should_proceed_at_threshold(self, tmp_path):
        """Test should_proceed when rate exactly equals threshold."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        # 60% success rate (6 successes, 4 failures)
        for _ in range(6):
            history.record("domain", "proj", "action", True)
        for _ in range(4):
            history.record("domain", "proj", "action", False)

        should_proceed, rate = history.should_proceed("domain", "action", threshold=0.6)

        assert should_proceed is True  # >= threshold
        assert abs(rate - 0.6) < 0.01

    def test_should_proceed_returns_tuple(self, tmp_path):
        """Test that should_proceed returns (bool, float) tuple."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        result = history.should_proceed("domain", "action")

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], float)


class TestEmptyHistoryReturnsNeutral:
    """Test neutral defaults when no history exists."""

    def test_empty_history_success_rate_neutral(self, tmp_path):
        """Test that success_rate returns 0.5 when no history exists."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        # No records recorded
        success_rate = history.get_success_rate("domain", "action")

        assert success_rate == 0.5

    def test_empty_history_should_proceed_neutral(self, tmp_path):
        """Test that should_proceed uses 0.5 when no history exists."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        should_proceed, rate = history.should_proceed("domain", "action", threshold=0.6)

        # 0.5 < 0.6, so should not proceed
        assert should_proceed is False
        assert rate == 0.5

    def test_empty_history_get_recent_patterns_empty(self, tmp_path):
        """Test that get_recent_patterns returns empty list when no history."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        patterns = history.get_recent_patterns("domain", "action")

        assert patterns == []

    def test_no_matching_records_neutral(self, tmp_path):
        """Test neutral default when records exist but don't match."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        # Record for different domain
        history.record("other-domain", "proj", "action", True)

        # Query for non-existent domain
        success_rate = history.get_success_rate("domain", "action")

        assert success_rate == 0.5


class TestHandlesMalformedLines:
    """Test graceful handling of malformed data."""

    def test_skips_invalid_json(self, tmp_path):
        """Test that invalid JSON lines are skipped gracefully."""
        history_file = tmp_path / "history.jsonl"

        # Write some valid and invalid records
        with open(history_file, "w") as f:
            f.write(json.dumps({"domain": "d", "action": "a", "success": True}) + "\n")
            f.write("{ invalid json }\n")
            f.write(json.dumps({"domain": "d", "action": "a", "success": False}) + "\n")

        history = SimpleHistory(history_file)
        success_rate = history.get_success_rate("d", "a")

        # Should only count the 2 valid records (1 success, 1 failure = 0.5)
        assert success_rate == 0.5

    def test_skips_missing_required_keys(self, tmp_path):
        """Test that records missing required keys are skipped."""
        history_file = tmp_path / "history.jsonl"

        with open(history_file, "w") as f:
            # Missing 'domain' key
            f.write(json.dumps({"action": "a", "success": True}) + "\n")
            # Valid record
            f.write(
                json.dumps({"domain": "d", "action": "a", "success": True, "context": {}}) + "\n"
            )
            # Missing 'action' key
            f.write(json.dumps({"domain": "d", "success": False}) + "\n")

        history = SimpleHistory(history_file)
        success_rate = history.get_success_rate("d", "a")

        # Should only count the 1 valid record
        assert success_rate == 1.0

    def test_empty_lines_ignored(self, tmp_path):
        """Test that empty lines are ignored."""
        history_file = tmp_path / "history.jsonl"

        with open(history_file, "w") as f:
            f.write(json.dumps({"domain": "d", "action": "a", "success": True}) + "\n")
            f.write("\n")  # Empty line
            f.write("   \n")  # Whitespace only
            f.write(json.dumps({"domain": "d", "action": "a", "success": False}) + "\n")

        history = SimpleHistory(history_file)
        success_rate = history.get_success_rate("d", "a")

        assert success_rate == 0.5

    def test_mixed_valid_and_invalid_records(self, tmp_path):
        """Test robustness with mix of valid, invalid, and empty records."""
        history_file = tmp_path / "history.jsonl"

        with open(history_file, "w") as f:
            # Valid
            f.write(
                json.dumps({"domain": "test", "action": "build", "success": True, "context": {}})
                + "\n"
            )
            # Invalid JSON
            f.write("not json at all\n")
            # Empty
            f.write("\n")
            # Missing keys
            f.write(json.dumps({"incomplete": "record"}) + "\n")
            # Valid
            f.write(
                json.dumps({"domain": "test", "action": "build", "success": True, "context": {}})
                + "\n"
            )
            # Valid
            f.write(
                json.dumps({"domain": "test", "action": "build", "success": False, "context": {}})
                + "\n"
            )

        history = SimpleHistory(history_file)
        success_rate = history.get_success_rate("test", "build")

        # 2 successes, 1 failure from 3 valid records
        assert abs(success_rate - (2 / 3)) < 0.01


class TestHistoryPersistence:
    """Test that history persists across instances."""

    def test_records_persist_across_instances(self, tmp_path):
        """Test that records written by one instance are read by another."""
        history_file = tmp_path / "history.jsonl"

        # Write records with first instance
        history1 = SimpleHistory(history_file)
        history1.record("domain", "proj", "action", True)
        history1.record("domain", "proj", "action", False)

        # Read with second instance
        history2 = SimpleHistory(history_file)
        success_rate = history2.get_success_rate("domain", "action")

        assert success_rate == 0.5


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_limit_parameter(self, tmp_path):
        """Test with limit=0."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        history.record("domain", "proj", "action", True)

        # With limit=0, the condition len(matching) >= 0 is true on first record
        # so it returns 1 record (the implementation appends then checks limit)
        success_rate = history.get_success_rate("domain", "action", limit=0)

        assert success_rate == 1.0

    def test_very_large_limit(self, tmp_path):
        """Test with very large limit parameter."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        history.record("domain", "proj", "action", True)

        success_rate = history.get_success_rate("domain", "action", limit=999999)
        assert success_rate == 1.0

    def test_threshold_zero(self, tmp_path):
        """Test should_proceed with threshold=0."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        history.record("domain", "proj", "action", False)

        should_proceed, _ = history.should_proceed("domain", "action", threshold=0.0)
        assert should_proceed is True  # 0.0 >= 0.0 is True

    def test_threshold_one(self, tmp_path):
        """Test should_proceed with threshold=1.0."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        history.record("domain", "proj", "action", True)

        should_proceed, _ = history.should_proceed("domain", "action", threshold=1.0)
        assert should_proceed is True  # 1.0 >= 1.0 is True

    def test_special_characters_in_fields(self, tmp_path):
        """Test that special characters in domain/action/context are handled."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        special_domain = "domain-with-dashes_and_underscores"
        special_action = "action:with:colons/and/slashes"
        special_context = {"error": "Exception: Something went wrong!", "unicode": "ñ é ü 中文"}

        history.record(special_domain, "proj", special_action, True, special_context)

        patterns = history.get_recent_patterns(special_domain, special_action)
        assert len(patterns) == 1
        assert patterns[0]["context"] == special_context

    def test_timestamp_format(self, tmp_path):
        """Test that timestamps are ISO format UTC."""
        history_file = tmp_path / "history.jsonl"
        history = SimpleHistory(history_file)

        history.record("domain", "proj", "action", True)

        with open(history_file) as f:
            line = f.readline()
            record = json.loads(line)

        # Parse timestamp to verify it's valid ISO format
        timestamp = record["timestamp"]
        parsed = datetime.fromisoformat(timestamp)

        # Should be timezone-aware UTC
        assert parsed.tzinfo is not None
