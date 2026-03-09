"""
Tests for forge_harness/metrics_common.py - Shared Metrics Utilities
"""

from datetime import UTC, datetime, timedelta

import pytest

from forge_harness.metrics_common import (
    PRIORITY_ORDER,
    ApprovalSummary,
    filter_approvals_by_time,
    format_age,
    format_duration,
    sort_approvals_by_priority,
)


class TestPriorityOrder:
    """Tests for PRIORITY_ORDER constant."""

    def test_priority_order_values(self):
        """Test that PRIORITY_ORDER has correct values."""
        assert PRIORITY_ORDER["critical"] == 0
        assert PRIORITY_ORDER["high"] == 1
        assert PRIORITY_ORDER["normal"] == 2
        assert PRIORITY_ORDER["low"] == 3

    def test_priority_order_is_consistent(self):
        """Test that PRIORITY_ORDER reflects correct ordering."""
        priorities = ["critical", "high", "normal", "low"]
        sorted_by_order = sorted(priorities, key=lambda p: PRIORITY_ORDER.get(p, 999))
        assert sorted_by_order == priorities


class TestSortApprovalsByPriority:
    """Tests for sort_approvals_by_priority() function."""

    def test_sorts_by_priority_then_age(self):
        """Test that approvals are sorted by priority first, then age."""
        now = datetime.now(UTC)
        approvals = [
            ApprovalSummary(
                id="low-old",
                type="test",
                title="Low priority old",
                domain="test",
                priority="low",
                tier="PHONE",
                risk_score=0.2,
                created_at=now - timedelta(hours=5),
            ),
            ApprovalSummary(
                id="critical-new",
                type="test",
                title="Critical priority new",
                domain="test",
                priority="critical",
                tier="WATCH",
                risk_score=0.9,
                created_at=now - timedelta(hours=1),
            ),
            ApprovalSummary(
                id="high-old",
                type="test",
                title="High priority old",
                domain="test",
                priority="high",
                tier="PHONE",
                risk_score=0.7,
                created_at=now - timedelta(hours=3),
            ),
            ApprovalSummary(
                id="critical-old",
                type="test",
                title="Critical priority old",
                domain="test",
                priority="critical",
                tier="DESKTOP",
                risk_score=0.9,
                created_at=now - timedelta(hours=10),
            ),
        ]

        sorted_approvals = sort_approvals_by_priority(approvals)

        # Should be: critical (old first), high, low
        assert sorted_approvals[0].id == "critical-old"
        assert sorted_approvals[1].id == "critical-new"
        assert sorted_approvals[2].id == "high-old"
        assert sorted_approvals[3].id == "low-old"

    def test_handles_unknown_priority(self):
        """Test that unknown priorities are treated as normal."""
        now = datetime.now(UTC)
        approvals = [
            ApprovalSummary(
                id="unknown",
                type="test",
                title="Unknown priority",
                domain="test",
                priority="urgent",  # Not in PRIORITY_ORDER
                tier="PHONE",
                risk_score=0.5,
                created_at=now - timedelta(hours=2),
            ),
            ApprovalSummary(
                id="high",
                type="test",
                title="High priority",
                domain="test",
                priority="high",
                tier="PHONE",
                risk_score=0.7,
                created_at=now - timedelta(hours=3),
            ),
        ]

        sorted_approvals = sort_approvals_by_priority(approvals)

        # High should come first, unknown treated as normal (2)
        assert sorted_approvals[0].id == "high"
        assert sorted_approvals[1].id == "unknown"

    def test_empty_list_returns_empty(self):
        """Test that empty list returns empty."""
        assert sort_approvals_by_priority([]) == []


class TestFilterApprovalsByTime:
    """Tests for filter_approvals_by_time() function."""

    def test_filter_all_returns_all(self):
        """Test that 'all' filter returns all approvals."""
        now = datetime.now(UTC)
        approvals = [
            ApprovalSummary(
                id="old",
                type="test",
                title="Old",
                domain="test",
                priority="normal",
                tier="PHONE",
                risk_score=0.5,
                created_at=now - timedelta(days=10),
            ),
            ApprovalSummary(
                id="new",
                type="test",
                title="New",
                domain="test",
                priority="normal",
                tier="PHONE",
                risk_score=0.5,
                created_at=now - timedelta(minutes=5),
            ),
        ]

        filtered = filter_approvals_by_time(approvals, "all")
        assert len(filtered) == 2

    def test_filter_1h_excludes_old(self):
        """Test that '1h' filter excludes approvals older than 1 hour."""
        now = datetime.now(UTC)
        approvals = [
            ApprovalSummary(
                id="old",
                type="test",
                title="Old",
                domain="test",
                priority="normal",
                tier="PHONE",
                risk_score=0.5,
                created_at=now - timedelta(hours=2),
            ),
            ApprovalSummary(
                id="new",
                type="test",
                title="New",
                domain="test",
                priority="normal",
                tier="PHONE",
                risk_score=0.5,
                created_at=now - timedelta(minutes=30),
            ),
        ]

        filtered = filter_approvals_by_time(approvals, "1h", now=now)
        assert len(filtered) == 1
        assert filtered[0].id == "new"

    def test_filter_24h_excludes_old(self):
        """Test that '24h' filter excludes approvals older than 24 hours."""
        now = datetime.now(UTC)
        approvals = [
            ApprovalSummary(
                id="old",
                type="test",
                title="Old",
                domain="test",
                priority="normal",
                tier="PHONE",
                risk_score=0.5,
                created_at=now - timedelta(days=2),
            ),
            ApprovalSummary(
                id="recent",
                type="test",
                title="Recent",
                domain="test",
                priority="normal",
                tier="PHONE",
                risk_score=0.5,
                created_at=now - timedelta(hours=12),
            ),
        ]

        filtered = filter_approvals_by_time(approvals, "24h", now=now)
        assert len(filtered) == 1
        assert filtered[0].id == "recent"

    def test_empty_list_returns_empty(self):
        """Test that empty list returns empty."""
        assert filter_approvals_by_time([], "1h") == []

    def test_none_created_at_filtered_out(self):
        """Test that approvals with None created_at are filtered out."""
        now = datetime.now(UTC)
        approvals = [
            ApprovalSummary(
                id="no-timestamp",
                type="test",
                title="No timestamp",
                domain="test",
                priority="normal",
                tier="PHONE",
                risk_score=0.5,
                created_at=None,  # type: ignore
            ),
            ApprovalSummary(
                id="with-timestamp",
                type="test",
                title="With timestamp",
                domain="test",
                priority="normal",
                tier="PHONE",
                risk_score=0.5,
                created_at=now - timedelta(minutes=5),
            ),
        ]

        filtered = filter_approvals_by_time(approvals, "1h", now=now)
        assert len(filtered) == 1
        assert filtered[0].id == "with-timestamp"


class TestReExportedFunctions:
    """Tests for re-exported formatting functions."""

    def test_format_age_is_available(self):
        """Test that format_age is re-exported."""
        dt = datetime.now(UTC) - timedelta(minutes=5)
        result = format_age(dt)
        assert "m ago" in result

    def test_format_duration_is_available(self):
        """Test that format_duration is re-exported."""
        result = format_duration(90)
        assert "m" in result and "s" in result
