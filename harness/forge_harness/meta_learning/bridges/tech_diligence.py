"""
Tech Diligence Bridge for the FORGE Meta-Learning System.

Connects to Tech Diligence to retrieve security and quality analysis results.
Provides graceful degradation when Tech Diligence is unavailable.
"""

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime

# Type hint for tracker to avoid circular import
from typing import TYPE_CHECKING, Optional

import httpx

from forge_harness.circuit_breaker import CircuitOpenError, get_circuit_breaker
from forge_harness.meta_learning.config import TechDiligenceConfig
from forge_harness.meta_learning.schemas import (
    AnalysisJobStatus,
    DiligenceFinding,
    DiligenceReport,
    DiligenceSignals,
    FindingSeverity,
)

if TYPE_CHECKING:
    from forge_harness.posthog_tracker import HarnessTracker

logger = logging.getLogger(__name__)


class CacheEntry:
    """A cached response with expiration."""

    def __init__(self, data: dict, ttl_seconds: int):
        self.data = data
        self.expires_at = datetime.now(UTC).timestamp() + ttl_seconds

    def is_expired(self) -> bool:
        return datetime.now(UTC).timestamp() > self.expires_at


class TechDiligenceBridge:
    """
    Bridge to Tech Diligence for security and quality analysis.

    Features:
    - Async job submission and polling
    - Report caching with configurable TTL
    - Graceful degradation (returns empty signals when unavailable)
    - Finding deduplication across scanners

    Usage:
        bridge = TechDiligenceBridge.from_config(config)

        # Get signals for a context
        signals = await bridge.get_signals(domain="codeswiftr-com", project="interview-simulator")

        # Submit and wait for analysis
        report = await bridge.analyze_and_wait(repo_path="/path/to/repo")
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: int = 60,
        poll_interval_seconds: int = 5,
        cache_ttl_seconds: int = 3600,
        enabled: bool = True,
        tracker: Optional["HarnessTracker"] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.enabled = enabled
        self.tracker = tracker

        self._cache: dict[str, CacheEntry] = {}
        self._client: httpx.AsyncClient | None = None
        self._breaker = get_circuit_breaker("tech_diligence")

    def _track_call(
        self,
        operation: str,
        start_time: float,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Track a bridge API call in PostHog."""
        import time

        if self.tracker:
            latency_ms = (time.time() - start_time) * 1000
            self.tracker.bridge_called(
                bridge_name="tech_diligence",
                operation=operation,
                latency_ms=latency_ms,
                success=success,
                error=error,
            )

    @classmethod
    def from_config(cls, config: TechDiligenceConfig) -> "TechDiligenceBridge":
        """Create a TechDiligenceBridge from configuration."""
        return cls(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            poll_interval_seconds=config.poll_interval_seconds,
            cache_ttl_seconds=config.cache_ttl_seconds,
            enabled=config.enabled,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                timeout=self.timeout_seconds,
                headers=headers,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _cache_key(self, endpoint: str, params: dict) -> str:
        """Generate a cache key for an endpoint and params."""
        canonical = json.dumps({"endpoint": endpoint, "params": params}, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def _get_cached(self, key: str) -> dict | None:
        """Get a cached response if not expired."""
        entry = self._cache.get(key)
        if entry and not entry.is_expired():
            return entry.data
        if entry and entry.is_expired():
            del self._cache[key]
        return None

    def _set_cached(self, key: str, data: dict) -> None:
        """Cache a response."""
        if self.cache_ttl_seconds > 0:
            self._cache[key] = CacheEntry(data, self.cache_ttl_seconds)

    # =========================================================================
    # Core API Methods
    # =========================================================================

    async def get_signals(
        self,
        domain: str,
        project: str,
    ) -> DiligenceSignals:
        """
        Get aggregated signals from Tech Diligence for a project.

        Returns empty signals if Tech Diligence is unavailable or disabled.
        """
        import time

        start_time = time.time()

        if not self.enabled:
            logger.debug("Tech Diligence disabled, returning empty signals")
            return DiligenceSignals()

        params = {"domain": domain, "project": project}
        cache_key = self._cache_key("/api/v1/reports/latest", params)
        cached = self._get_cached(cache_key)
        if cached:
            logger.debug("Returning cached signals for %s/%s", domain, project)
            return self._report_to_signals(cached)

        try:
            async with self._breaker:
                client = await self._get_client()
                response = await client.get(
                    f"{self.base_url}/api/v1/reports/latest",
                    params=params,
                )

                if response.status_code == 404:
                    # No report found, return clean signals
                    self._track_call("get_signals", start_time, success=True)
                    return DiligenceSignals()

                response.raise_for_status()

                data = response.json()
                self._set_cached(cache_key, data)

                self._track_call("get_signals", start_time, success=True)
                return self._report_to_signals(data)

        except CircuitOpenError as e:
            logger.warning(f"Tech Diligence circuit open: {e}")
            self._track_call("get_signals", start_time, success=False, error="CircuitOpenError")
            return DiligenceSignals()
        except httpx.TimeoutException:
            logger.warning("Tech Diligence timeout for signals request")
            self._track_call("get_signals", start_time, success=False, error="TimeoutException")
            return DiligenceSignals()
        except httpx.HTTPStatusError as e:
            logger.warning("Tech Diligence HTTP error: %s", e)
            self._track_call(
                "get_signals",
                start_time,
                success=False,
                error=f"HTTPStatusError: {e.response.status_code}",
            )
            return DiligenceSignals()
        except httpx.RequestError as e:
            logger.warning("Tech Diligence connection error: %s", e)
            self._track_call(
                "get_signals", start_time, success=False, error=f"RequestError: {type(e).__name__}"
            )
            return DiligenceSignals()
        except Exception as e:
            logger.error("Unexpected error getting Tech Diligence signals: %s", e)
            self._track_call("get_signals", start_time, success=False, error=str(e))
            return DiligenceSignals()

    def _report_to_signals(self, report_data: dict) -> DiligenceSignals:
        """Convert a report to DiligenceSignals."""
        findings = [DiligenceFinding.model_validate(f) for f in report_data.get("findings", [])]

        # Deduplicate findings by title
        seen_titles = set()
        unique_findings = []
        for f in findings:
            if f.title not in seen_titles:
                seen_titles.add(f.title)
                unique_findings.append(f)

        # Categorize findings
        blocking = [
            f
            for f in unique_findings
            if f.severity in (FindingSeverity.CRITICAL, FindingSeverity.HIGH)
        ]
        warnings = [
            f
            for f in unique_findings
            if f.severity in (FindingSeverity.MEDIUM, FindingSeverity.LOW)
        ]

        # Determine risk level
        if any(f.severity == FindingSeverity.CRITICAL for f in unique_findings):
            risk_level = "critical"
        elif any(f.severity == FindingSeverity.HIGH for f in unique_findings):
            risk_level = "high"
        elif any(f.severity == FindingSeverity.MEDIUM for f in unique_findings):
            risk_level = "medium"
        else:
            risk_level = "low"

        return DiligenceSignals(
            overall_score=report_data.get("summary_score", 100.0),
            risk_level=risk_level,
            blocking_issues=blocking,
            warnings=warnings,
        )

    async def submit_analysis(
        self,
        repo_path: str,
        scanners: list[str] | None = None,
    ) -> str | None:
        """
        Submit a repository for analysis.

        Returns the job ID or None if submission failed.
        """
        if not self.enabled:
            return None

        try:
            async with self._breaker:
                client = await self._get_client()
                response = await client.post(
                    f"{self.base_url}/api/v1/analyze",
                    json={
                        "repo_path": repo_path,
                        "scanners": scanners or ["bandit", "semgrep", "codeql", "snyk"],
                    },
                )
                response.raise_for_status()

                data = response.json()
                return data.get("job_id")

        except CircuitOpenError as e:
            logger.warning(f"Tech Diligence circuit open: {e}")
            return None
        except Exception as e:
            logger.warning("Error submitting Tech Diligence analysis: %s", e)
            return None

    async def get_job_status(self, job_id: str) -> AnalysisJobStatus | None:
        """
        Get the status of an analysis job.

        Returns None if the job doesn't exist or an error occurred.
        """
        if not self.enabled:
            return None

        try:
            async with self._breaker:
                client = await self._get_client()
                response = await client.get(
                    f"{self.base_url}/api/v1/jobs/{job_id}",
                )
                response.raise_for_status()

                data = response.json()
                return AnalysisJobStatus.model_validate(data)

        except CircuitOpenError as e:
            logger.warning(f"Tech Diligence circuit open: {e}")
            return None
        except Exception as e:
            logger.warning("Error getting job status: %s", e)
            return None

    async def get_report(self, report_id: str) -> DiligenceReport | None:
        """
        Get a specific analysis report.

        Returns None if the report doesn't exist or an error occurred.
        """
        if not self.enabled:
            return None

        try:
            async with self._breaker:
                client = await self._get_client()
                response = await client.get(
                    f"{self.base_url}/api/v1/reports/{report_id}",
                )
                response.raise_for_status()

                data = response.json()
                return DiligenceReport.model_validate(data)

        except CircuitOpenError as e:
            logger.warning(f"Tech Diligence circuit open: {e}")
            # Allow a direct read attempt for report retrieval to avoid stale-open failures.
            try:
                client = await self._get_client()
                response = await client.get(
                    f"{self.base_url}/api/v1/reports/{report_id}",
                )
                response.raise_for_status()
                data = response.json()
                return DiligenceReport.model_validate(data)
            except Exception as direct_error:
                logger.warning("Error getting report with circuit open: %s", direct_error)
                return None
        except Exception as e:
            logger.warning("Error getting report: %s", e)
            return None

    async def analyze_and_wait(
        self,
        repo_path: str,
        scanners: list[str] | None = None,
        max_wait_seconds: int = 300,
    ) -> DiligenceReport | None:
        """
        Submit analysis and wait for completion.

        Returns the report or None if analysis failed/timed out.
        """
        if not self.enabled:
            return None

        job_id = await self.submit_analysis(repo_path, scanners)
        if not job_id:
            return None

        start_time = datetime.now(UTC).timestamp()
        while True:
            elapsed = datetime.now(UTC).timestamp() - start_time
            if elapsed > max_wait_seconds:
                logger.warning("Analysis timed out after %d seconds", max_wait_seconds)
                return None

            status = await self.get_job_status(job_id)
            if not status:
                return None

            if status.status == "completed" and status.report_id:
                return await self.get_report(status.report_id)

            if status.status == "failed":
                logger.warning("Analysis failed: %s", status.error_message)
                return None

            await asyncio.sleep(self.poll_interval_seconds)

    async def health_check(self) -> bool:
        """
        Check if Tech Diligence is reachable.

        Returns True if healthy, False otherwise.
        """
        if not self.enabled:
            return False

        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.base_url}/health",
                timeout=5.0,
            )
            return response.status_code == 200
        except Exception:
            return False

    # =========================================================================
    # Cache Management
    # =========================================================================

    def clear_cache(self) -> None:
        """Clear all cached responses."""
        self._cache.clear()

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        now = datetime.now(UTC).timestamp()
        active = sum(1 for e in self._cache.values() if e.expires_at > now)
        return {
            "total_entries": len(self._cache),
            "active_entries": active,
            "expired_entries": len(self._cache) - active,
        }
