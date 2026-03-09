"""Tests for meta_learning/bridges/tech_diligence.py - Tech Diligence Bridge.

Tests cover:
- CacheEntry class
- TechDiligenceBridge initialization
- HTTP client management
- Cache operations
- get_signals method
- _report_to_signals conversion
- submit_analysis method
- get_job_status method
- get_report method
- analyze_and_wait method
- health_check method
- Cache management methods
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from forge_harness.circuit_breaker import CircuitOpenError
from forge_harness.meta_learning.bridges.tech_diligence import (
    CacheEntry,
    TechDiligenceBridge,
)
from forge_harness.meta_learning.schemas import (
    AnalysisJobStatus,
    DiligenceFinding,
    DiligenceReport,
    DiligenceSignals,
    FindingSeverity,
)

# =============================================================================
# CacheEntry Tests
# =============================================================================


class TestCacheEntry:
    """Tests for CacheEntry class."""

    def test_creation(self) -> None:
        """Should create CacheEntry with data and TTL."""
        entry = CacheEntry(data={"key": "value"}, ttl_seconds=3600)

        assert entry.data == {"key": "value"}
        assert entry.expires_at > datetime.now(UTC).timestamp()

    def test_is_expired_false(self) -> None:
        """Should return False for fresh entry."""
        entry = CacheEntry(data={"key": "value"}, ttl_seconds=3600)

        assert entry.is_expired() is False

    def test_is_expired_true(self) -> None:
        """Should return True for expired entry."""
        entry = CacheEntry(data={"key": "value"}, ttl_seconds=-1)

        assert entry.is_expired() is True


# =============================================================================
# TechDiligenceBridge Initialization Tests
# =============================================================================


class TestTechDiligenceBridgeInit:
    """Tests for TechDiligenceBridge initialization."""

    def test_init_defaults(self) -> None:
        """Should initialize with defaults."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        assert bridge.base_url == "http://localhost:8080"
        assert bridge.api_key is None
        assert bridge.timeout_seconds == 60
        assert bridge.poll_interval_seconds == 5
        assert bridge.cache_ttl_seconds == 3600
        assert bridge.enabled is True
        assert bridge._cache == {}
        assert bridge._client is None

    def test_init_custom(self) -> None:
        """Should initialize with custom values."""
        bridge = TechDiligenceBridge(
            base_url="http://api.example.com/",
            api_key="test-key",
            timeout_seconds=120,
            poll_interval_seconds=10,
            cache_ttl_seconds=7200,
            enabled=False,
        )

        assert bridge.base_url == "http://api.example.com"
        assert bridge.api_key == "test-key"
        assert bridge.timeout_seconds == 120
        assert bridge.poll_interval_seconds == 10
        assert bridge.cache_ttl_seconds == 7200
        assert bridge.enabled is False

    def test_init_strips_trailing_slash(self) -> None:
        """Should strip trailing slash from base_url."""
        bridge = TechDiligenceBridge(base_url="http://api.example.com/")

        assert bridge.base_url == "http://api.example.com"

    def test_from_config(self) -> None:
        """Should create from config."""
        from forge_harness.meta_learning.config import TechDiligenceConfig

        config = TechDiligenceConfig(
            base_url="http://config.example.com",
            api_key="config-key",
            timeout_seconds=90,
            poll_interval_seconds=3,
            cache_ttl_seconds=1800,
            enabled=True,
        )

        bridge = TechDiligenceBridge.from_config(config)

        assert bridge.base_url == "http://config.example.com"
        assert bridge.api_key == "config-key"
        assert bridge.timeout_seconds == 90


# =============================================================================
# HTTP Client Tests
# =============================================================================


class TestHTTPClient:
    """Tests for HTTP client management."""

    @pytest.mark.asyncio
    async def test_get_client_creates_new(self) -> None:
        """Should create new client when none exists."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        mock_client = AsyncMock()
        mock_client.is_closed = False

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = await bridge._get_client()

            assert client is mock_client

    @pytest.mark.asyncio
    async def test_get_client_with_api_key(self) -> None:
        """Should set Authorization header when API key provided."""
        bridge = TechDiligenceBridge(
            base_url="http://localhost:8080",
            api_key="test-key",
        )

        mock_client = AsyncMock()
        mock_client.is_closed = False

        with patch("httpx.AsyncClient") as mock_class:
            mock_class.return_value = mock_client
            await bridge._get_client()

            call_kwargs = mock_class.call_args[1]
            assert call_kwargs["headers"]["Authorization"] == "Bearer test-key"

    @pytest.mark.asyncio
    async def test_close_client(self) -> None:
        """Should close HTTP client."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        mock_client = AsyncMock()
        mock_client.is_closed = False
        bridge._client = mock_client

        await bridge.close()

        mock_client.aclose.assert_called_once()
        assert bridge._client is None

    @pytest.mark.asyncio
    async def test_close_already_closed(self) -> None:
        """Should handle already closed client."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        mock_client = AsyncMock()
        mock_client.is_closed = True
        bridge._client = mock_client

        await bridge.close()

        mock_client.aclose.assert_not_called()


# =============================================================================
# Cache Operations Tests
# =============================================================================


class TestCacheOperations:
    """Tests for cache operations."""

    def test_cache_key_generation(self) -> None:
        """Should generate consistent cache key."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        key1 = bridge._cache_key("/api/test", {"a": 1, "b": 2})
        key2 = bridge._cache_key("/api/test", {"b": 2, "a": 1})

        # Keys should be same regardless of param order
        assert key1 == key2
        assert len(key1) == 16

    def test_get_cached_hit(self) -> None:
        """Should return cached data when not expired."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")
        bridge._cache["key"] = CacheEntry(data={"test": "data"}, ttl_seconds=3600)

        result = bridge._get_cached("key")

        assert result == {"test": "data"}

    def test_get_cached_miss(self) -> None:
        """Should return None for missing key."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        result = bridge._get_cached("missing")

        assert result is None

    def test_get_cached_expired(self) -> None:
        """Should return None and remove expired entry."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")
        bridge._cache["key"] = CacheEntry(data={"test": "data"}, ttl_seconds=-1)

        result = bridge._get_cached("key")

        assert result is None
        assert "key" not in bridge._cache

    def test_set_cached(self) -> None:
        """Should cache data."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        bridge._set_cached("key", {"test": "data"})

        assert "key" in bridge._cache
        assert bridge._cache["key"].data == {"test": "data"}

    def test_set_cached_zero_ttl(self) -> None:
        """Should not cache when TTL is 0."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080", cache_ttl_seconds=0)

        bridge._set_cached("key", {"test": "data"})

        assert "key" not in bridge._cache

    def test_clear_cache(self) -> None:
        """Should clear all cached entries."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")
        bridge._cache["key1"] = CacheEntry(data={}, ttl_seconds=3600)
        bridge._cache["key2"] = CacheEntry(data={}, ttl_seconds=3600)

        bridge.clear_cache()

        assert bridge._cache == {}

    def test_get_cache_stats(self) -> None:
        """Should return cache statistics."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")
        bridge._cache["active"] = CacheEntry(data={}, ttl_seconds=3600)
        bridge._cache["expired"] = CacheEntry(data={}, ttl_seconds=-1)

        stats = bridge.get_cache_stats()

        assert stats["total_entries"] == 2
        assert stats["active_entries"] == 1
        assert stats["expired_entries"] == 1


# =============================================================================
# get_signals Tests
# =============================================================================


class TestGetSignals:
    """Tests for get_signals method."""

    @pytest.mark.asyncio
    async def test_get_signals_disabled(self) -> None:
        """Should return empty signals when disabled."""
        bridge = TechDiligenceBridge(
            base_url="http://localhost:8080",
            enabled=False,
        )

        signals = await bridge.get_signals("domain", "project")

        assert isinstance(signals, DiligenceSignals)
        assert signals.overall_score == 100.0
        assert signals.risk_level == "low"

    @pytest.mark.asyncio
    async def test_get_signals_cached(self) -> None:
        """Should return cached signals."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        cached_data = {
            "findings": [],
            "summary_score": 95.0,
        }
        cache_key = bridge._cache_key("/api/v1/reports/latest", {"domain": "test", "project": "proj"})
        bridge._set_cached(cache_key, cached_data)

        signals = await bridge.get_signals("test", "proj")

        assert signals.overall_score == 95.0

    @pytest.mark.asyncio
    async def test_get_signals_success(self) -> None:
        """Should fetch and return signals."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "findings": [
                {
                    "finding_id": "find-001",
                    "title": "High severity issue",
                    "severity": "high",
                    "description": "Test",
                    "category": "security",
                },
            ],
            "summary_score": 85.0,
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(bridge, "_get_client", return_value=mock_client), \
             patch.object(bridge._breaker, "__aenter__", AsyncMock(return_value=None)), \
             patch.object(bridge._breaker, "__aexit__", AsyncMock(return_value=None)):
            signals = await bridge.get_signals("domain", "project")

            assert signals.overall_score == 85.0
            assert signals.risk_level == "high"
            assert len(signals.blocking_issues) == 1

    @pytest.mark.asyncio
    async def test_get_signals_404(self) -> None:
        """Should return empty signals on 404."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(bridge, "_get_client", return_value=mock_client), \
             patch.object(bridge._breaker, "__aenter__", AsyncMock(return_value=None)), \
             patch.object(bridge._breaker, "__aexit__", AsyncMock(return_value=None)):
            signals = await bridge.get_signals("domain", "project")

            assert isinstance(signals, DiligenceSignals)
            assert signals.overall_score == 100.0

    @pytest.mark.asyncio
    async def test_get_signals_circuit_open(self) -> None:
        """Should return empty signals on circuit open."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        async def raise_circuit_open(*args, **kwargs):
            raise CircuitOpenError("Circuit is open")

        with patch.object(bridge._breaker, "__aenter__", side_effect=raise_circuit_open):
            signals = await bridge.get_signals("domain", "project")

            assert isinstance(signals, DiligenceSignals)

    @pytest.mark.asyncio
    async def test_get_signals_timeout(self) -> None:
        """Should return empty signals on timeout."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        async def raise_timeout(*args, **kwargs):
            raise httpx.TimeoutException("Timeout")

        with patch.object(bridge._breaker, "__aenter__", side_effect=raise_timeout):
            signals = await bridge.get_signals("domain", "project")

            assert isinstance(signals, DiligenceSignals)

    @pytest.mark.asyncio
    async def test_get_signals_http_error(self) -> None:
        """Should return empty signals on HTTP error."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_error = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=mock_response,
        )

        async def raise_http_error(*args, **kwargs):
            raise mock_error

        with patch.object(bridge._breaker, "__aenter__", side_effect=raise_http_error):
            signals = await bridge.get_signals("domain", "project")

            assert isinstance(signals, DiligenceSignals)


# =============================================================================
# _report_to_signals Tests
# =============================================================================


class TestReportToSignals:
    """Tests for _report_to_signals method."""

    def test_empty_report(self) -> None:
        """Should convert empty report to signals."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        signals = bridge._report_to_signals({"findings": []})

        assert signals.overall_score == 100.0
        assert signals.risk_level == "low"
        assert signals.blocking_issues == []
        assert signals.warnings == []

    def test_critical_finding(self) -> None:
        """Should categorize critical finding as blocking."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        report = {
            "findings": [
                {"finding_id": "find-001", "title": "Critical bug", "severity": "critical", "description": "Test", "category": "security"},
            ],
            "summary_score": 50.0,
        }

        signals = bridge._report_to_signals(report)

        assert signals.risk_level == "critical"
        assert len(signals.blocking_issues) == 1
        assert signals.blocking_issues[0].severity == FindingSeverity.CRITICAL

    def test_deduplication(self) -> None:
        """Should deduplicate findings by title."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        report = {
            "findings": [
                {"finding_id": "find-001", "title": "Same issue", "severity": "high", "description": "Test 1", "category": "security"},
                {"finding_id": "find-002", "title": "Same issue", "severity": "high", "description": "Test 2", "category": "security"},
            ],
            "summary_score": 80.0,
        }

        signals = bridge._report_to_signals(report)

        assert len(signals.blocking_issues) == 1

    def test_risk_levels(self) -> None:
        """Should determine correct risk level."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        # High severity
        signals = bridge._report_to_signals({
            "findings": [{"finding_id": "f1", "title": "High", "severity": "high", "description": "Test", "category": "security"}],
        })
        assert signals.risk_level == "high"

        # Medium severity
        signals = bridge._report_to_signals({
            "findings": [{"finding_id": "f2", "title": "Medium", "severity": "medium", "description": "Test", "category": "security"}],
        })
        assert signals.risk_level == "medium"

        # Low severity
        signals = bridge._report_to_signals({
            "findings": [{"finding_id": "f3", "title": "Low", "severity": "low", "description": "Test", "category": "security"}],
        })
        assert signals.risk_level == "low"


# =============================================================================
# submit_analysis Tests
# =============================================================================


class TestSubmitAnalysis:
    """Tests for submit_analysis method."""

    @pytest.mark.asyncio
    async def test_submit_disabled(self) -> None:
        """Should return None when disabled."""
        bridge = TechDiligenceBridge(
            base_url="http://localhost:8080",
            enabled=False,
        )

        result = await bridge.submit_analysis("/path/to/repo")

        assert result is None

    @pytest.mark.asyncio
    async def test_submit_success(self) -> None:
        """Should submit and return job ID."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        mock_response = MagicMock()
        mock_response.json.return_value = {"job_id": "job-123"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(bridge, "_get_client", return_value=mock_client), \
             patch.object(bridge._breaker, "__aenter__", AsyncMock(return_value=None)), \
             patch.object(bridge._breaker, "__aexit__", AsyncMock(return_value=None)):
            result = await bridge.submit_analysis("/path/to/repo")

            assert result == "job-123"

    @pytest.mark.asyncio
    async def test_submit_with_scanners(self) -> None:
        """Should submit with custom scanners."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        mock_response = MagicMock()
        mock_response.json.return_value = {"job_id": "job-123"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(bridge, "_get_client", return_value=mock_client), \
             patch.object(bridge._breaker, "__aenter__", AsyncMock(return_value=None)), \
             patch.object(bridge._breaker, "__aexit__", AsyncMock(return_value=None)):
            result = await bridge.submit_analysis("/path/to/repo", scanners=["custom-scanner"])

            call_args = mock_client.post.call_args
            assert call_args[1]["json"]["scanners"] == ["custom-scanner"]

    @pytest.mark.asyncio
    async def test_submit_circuit_open(self) -> None:
        """Should return None on circuit open."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        async def raise_circuit_open(*args, **kwargs):
            raise CircuitOpenError("Circuit is open")

        with patch.object(bridge._breaker, "__aenter__", side_effect=raise_circuit_open):
            result = await bridge.submit_analysis("/path/to/repo")

            assert result is None


# =============================================================================
# get_job_status Tests
# =============================================================================


class TestGetJobStatus:
    """Tests for get_job_status method."""

    @pytest.mark.asyncio
    async def test_get_status_disabled(self) -> None:
        """Should return None when disabled."""
        bridge = TechDiligenceBridge(
            base_url="http://localhost:8080",
            enabled=False,
        )

        result = await bridge.get_job_status("job-123")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_status_success(self) -> None:
        """Should return job status."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "job_id": "job-123",
            "status": "completed",
            "report_id": "report-456",
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(bridge, "_get_client", return_value=mock_client), \
             patch.object(bridge._breaker, "__aenter__", AsyncMock(return_value=None)), \
             patch.object(bridge._breaker, "__aexit__", AsyncMock(return_value=None)):
            result = await bridge.get_job_status("job-123")

            assert isinstance(result, AnalysisJobStatus)
            assert result.status == "completed"
            assert result.report_id == "report-456"


# =============================================================================
# get_report Tests
# =============================================================================


class TestGetReport:
    """Tests for get_report method."""

    @pytest.mark.asyncio
    async def test_get_report_disabled(self) -> None:
        """Should return None when disabled."""
        bridge = TechDiligenceBridge(
            base_url="http://localhost:8080",
            enabled=False,
        )

        result = await bridge.get_report("report-123")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_report_success(self) -> None:
        """Should return report."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "report_id": "report-123",
            "findings": [],
            "summary_score": 95.0,
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(bridge, "_get_client", return_value=mock_client), \
             patch.object(bridge._breaker, "__aenter__", AsyncMock(return_value=None)), \
             patch.object(bridge._breaker, "__aexit__", AsyncMock(return_value=None)):
            result = await bridge.get_report("report-123")

            assert isinstance(result, DiligenceReport)
            assert result.report_id == "report-123"

    @pytest.mark.asyncio
    async def test_get_report_circuit_open_with_fallback(self) -> None:
        """Should try direct request when circuit is open."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        async def raise_circuit_open(*args, **kwargs):
            raise CircuitOpenError("Circuit is open")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "report_id": "report-123",
            "findings": [],
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(bridge._breaker, "__aenter__", side_effect=raise_circuit_open), \
             patch.object(bridge, "_get_client", return_value=mock_client):
            result = await bridge.get_report("report-123")

            assert isinstance(result, DiligenceReport)


# =============================================================================
# analyze_and_wait Tests
# =============================================================================


class TestAnalyzeAndWait:
    """Tests for analyze_and_wait method."""

    @pytest.mark.asyncio
    async def test_analyze_disabled(self) -> None:
        """Should return None when disabled."""
        bridge = TechDiligenceBridge(
            base_url="http://localhost:8080",
            enabled=False,
        )

        result = await bridge.analyze_and_wait("/path/to/repo")

        assert result is None

    @pytest.mark.asyncio
    async def test_analyze_submit_fails(self) -> None:
        """Should return None when submit fails."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        with patch.object(bridge, "submit_analysis", return_value=None):
            result = await bridge.analyze_and_wait("/path/to/repo")

            assert result is None

    @pytest.mark.asyncio
    async def test_analyze_success(self) -> None:
        """Should wait and return report."""
        bridge = TechDiligenceBridge(
            base_url="http://localhost:8080",
            poll_interval_seconds=0.01,
        )

        mock_report = DiligenceReport(
            report_id="report-123",
            findings=[],
            summary_score=95.0,
        )

        with patch.object(bridge, "submit_analysis", return_value="job-123"), \
             patch.object(bridge, "get_job_status") as mock_status, \
             patch.object(bridge, "get_report", return_value=mock_report):

            # First call: pending, Second call: completed
            mock_status.side_effect = [
                AnalysisJobStatus(job_id="job-123", status="pending"),
                AnalysisJobStatus(job_id="job-123", status="completed", report_id="report-123"),
            ]

            result = await bridge.analyze_and_wait("/path/to/repo")

            assert result is mock_report

    @pytest.mark.asyncio
    async def test_analyze_failed(self) -> None:
        """Should return None when analysis fails."""
        bridge = TechDiligenceBridge(
            base_url="http://localhost:8080",
            poll_interval_seconds=0.01,
        )

        with patch.object(bridge, "submit_analysis", return_value="job-123"), \
             patch.object(bridge, "get_job_status") as mock_status:

            mock_status.return_value = AnalysisJobStatus(
                job_id="job-123",
                status="failed",
                error_message="Analysis error",
            )

            result = await bridge.analyze_and_wait("/path/to/repo")

            assert result is None

    @pytest.mark.asyncio
    async def test_analyze_timeout(self) -> None:
        """Should return None when timeout exceeded."""
        bridge = TechDiligenceBridge(
            base_url="http://localhost:8080",
            poll_interval_seconds=0.01,
        )

        with patch.object(bridge, "submit_analysis", return_value="job-123"), \
             patch.object(bridge, "get_job_status") as mock_status:

            # Always return pending
            mock_status.return_value = AnalysisJobStatus(job_id="job-123", status="pending")

            result = await bridge.analyze_and_wait("/path/to/repo", max_wait_seconds=0.05)

            assert result is None


# =============================================================================
# health_check Tests
# =============================================================================


class TestHealthCheck:
    """Tests for health_check method."""

    @pytest.mark.asyncio
    async def test_health_disabled(self) -> None:
        """Should return False when disabled."""
        bridge = TechDiligenceBridge(
            base_url="http://localhost:8080",
            enabled=False,
        )

        result = await bridge.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_healthy(self) -> None:
        """Should return True when healthy."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(bridge, "_get_client", return_value=mock_client):
            result = await bridge.health_check()

            assert result is True

    @pytest.mark.asyncio
    async def test_health_unhealthy(self) -> None:
        """Should return False when unhealthy."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(bridge, "_get_client", return_value=mock_client):
            result = await bridge.health_check()

            assert result is False

    @pytest.mark.asyncio
    async def test_health_exception(self) -> None:
        """Should return False on exception."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        with patch.object(bridge, "_get_client", side_effect=Exception("Connection failed")):
            result = await bridge.health_check()

            assert result is False


# =============================================================================
# _track_call Tests
# =============================================================================


class TestTrackCall:
    """Tests for _track_call method."""

    def test_track_call_with_tracker(self) -> None:
        """Should track call when tracker present."""
        mock_tracker = MagicMock()
        bridge = TechDiligenceBridge(
            base_url="http://localhost:8080",
            tracker=mock_tracker,
        )

        import time
        start_time = time.time()

        bridge._track_call("test_op", start_time, success=True, error=None)

        mock_tracker.bridge_called.assert_called_once()
        call_args = mock_tracker.bridge_called.call_args[1]
        assert call_args["bridge_name"] == "tech_diligence"
        assert call_args["operation"] == "test_op"
        assert call_args["success"] is True

    def test_track_call_without_tracker(self) -> None:
        """Should not fail when no tracker."""
        bridge = TechDiligenceBridge(base_url="http://localhost:8080")

        import time
        bridge._track_call("test_op", time.time(), success=True)  # Should not raise
