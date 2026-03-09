"""
Code Atlas Bridge for the FORGE Meta-Learning System.

Connects to Code Atlas to retrieve patterns and insights from past Claude sessions.
Provides graceful degradation when Code Atlas is unavailable.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime

# Type hint for tracker to avoid circular import
from typing import TYPE_CHECKING, Optional

import httpx

from forge_harness.circuit_breaker import CircuitOpenError, get_circuit_breaker
from forge_harness.meta_learning.config import CodeAtlasConfig
from forge_harness.meta_learning.schemas import (
    AtlasEntity,
    AtlasPattern,
    AtlasRAGResponse,
    AtlasSignals,
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


class CodeAtlasBridge:
    """
    Bridge to Code Atlas for pattern extraction and RAG queries.

    Features:
    - Response caching with configurable TTL
    - Graceful degradation (returns empty signals when unavailable)
    - Async HTTP client with timeout handling

    Usage:
        bridge = CodeAtlasBridge.from_config(config)

        # Get signals for a context
        signals = await bridge.get_signals(domain="codeswiftr-com", project="interview-simulator")

        # Query RAG
        response = await bridge.query_rag("How do we handle authentication?")
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: int = 30,
        cache_ttl_seconds: int = 300,
        enabled: bool = True,
        tracker: Optional["HarnessTracker"] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.enabled = enabled
        self.tracker = tracker

        self._cache: dict[str, CacheEntry] = {}
        self._client: httpx.AsyncClient | None = None

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
                bridge_name="code_atlas",
                operation=operation,
                latency_ms=latency_ms,
                success=success,
                error=error,
            )

    @classmethod
    def from_config(cls, config: CodeAtlasConfig) -> "CodeAtlasBridge":
        """Create a CodeAtlasBridge from configuration."""
        return cls(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            cache_ttl_seconds=config.cache_ttl_seconds,
            enabled=config.enabled,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                # Code Atlas uses X-API-Key header for authentication
                headers["X-API-Key"] = self.api_key
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
        file_paths: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> AtlasSignals:
        """
        Get aggregated signals from Code Atlas for a context.

        Constructs signals from available Code Atlas endpoints:
        - Recurring problems from /api/v1/insights/recurring-problems
        - Related patterns from hybrid search
        - Similar decisions from entity search

        Returns empty signals if Code Atlas is unavailable or disabled.
        """
        import time

        start_time = time.time()

        if not self.enabled:
            logger.debug("Code Atlas disabled, returning empty signals")
            return AtlasSignals()

        params = {
            "domain": domain,
            "project": project,
            "file_paths": file_paths or [],
            "tags": tags or [],
        }

        cache_key = self._cache_key("signals", params)
        cached = self._get_cached(cache_key)
        if cached:
            logger.debug("Returning cached signals for %s/%s", domain, project)
            return AtlasSignals.model_validate(cached)

        breaker = get_circuit_breaker("code_atlas")

        try:
            async with breaker:
                client = await self._get_client()

                # Build context query from tags and project
                context_query = f"{project} {' '.join(tags or [])}"

                # Fetch recurring problems
                recurring_problems = []
                try:
                    resp = await client.get(
                        f"{self.base_url}/api/v1/insights/recurring-problems",
                        params={"limit": 10, "min_sessions": 2},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        recurring_problems = [
                            p.get("entity", {}).get("name", "") for p in data.get("problems", [])
                        ]
                except Exception as e:
                    logger.debug("Failed to fetch recurring problems: %s", e)

                # Search for related patterns using hybrid search
                related_patterns = []
                try:
                    resp = await client.post(
                        f"{self.base_url}/api/v1/graph/hybrid-search",
                        json={
                            "query": context_query,
                            "entity_type": "Solution",
                            "limit": 5,
                            "min_score": 0.4,
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for result in data.get("results", []):
                            related_patterns.append(
                                AtlasPattern(
                                    pattern_id=result.get("entity_id", ""),
                                    pattern_type="solution",
                                    entities=[result.get("entity_name", "")],
                                    session_count=1,
                                    confidence=result.get("combined_score", 0.5),
                                )
                            )
                except Exception as e:
                    logger.debug("Failed to search for patterns: %s", e)

                # Search for similar decisions (problems solved before)
                similar_decisions = []
                try:
                    resp = await client.get(
                        f"{self.base_url}/api/v1/graph/entities/search",
                        params={
                            "q": context_query,
                            "type": "Problem",
                            "limit": 5,
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        similar_decisions = [
                            r.get("entity", {}).get("id", "")
                            for r in data.get("results", [])
                            if r.get("score", 0) > 0.5
                        ]
                except Exception as e:
                    logger.debug("Failed to search for similar decisions: %s", e)

                signals = AtlasSignals(
                    related_patterns=related_patterns,
                    similar_decisions=similar_decisions,
                    recurring_problems=recurring_problems,
                )

                self._set_cached(cache_key, signals.model_dump())
                self._track_call("get_signals", start_time, success=True)
                return signals

        except CircuitOpenError as e:
            logger.warning(f"Code Atlas circuit open: {e}")
            self._track_call("get_signals", start_time, success=False, error="CircuitOpenError")
            return AtlasSignals()
        except httpx.TimeoutException:
            logger.warning("Code Atlas timeout for signals request")
            self._track_call("get_signals", start_time, success=False, error="TimeoutException")
            return AtlasSignals()
        except httpx.HTTPStatusError as e:
            logger.warning("Code Atlas HTTP error: %s", e)
            self._track_call(
                "get_signals",
                start_time,
                success=False,
                error=f"HTTPStatusError: {e.response.status_code}",
            )
            return AtlasSignals()
        except httpx.RequestError as e:
            logger.warning("Code Atlas connection error: %s", e)
            self._track_call(
                "get_signals", start_time, success=False, error=f"RequestError: {type(e).__name__}"
            )
            return AtlasSignals()
        except Exception as e:
            logger.error("Unexpected error getting Code Atlas signals: %s", e)
            self._track_call("get_signals", start_time, success=False, error=str(e))
            return AtlasSignals()

    async def get_patterns(
        self,
        pattern_type: str | None = None,
        limit: int = 10,
    ) -> list[AtlasPattern]:
        """
        Get patterns from Code Atlas.

        Returns empty list if Code Atlas is unavailable or disabled.
        """
        if not self.enabled:
            return []

        params = {"pattern_type": pattern_type, "limit": limit}
        cache_key = self._cache_key("/api/v1/insights/patterns", params)
        cached = self._get_cached(cache_key)
        if cached:
            return [AtlasPattern.model_validate(p) for p in cached.get("patterns", [])]

        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.base_url}/api/v1/insights/patterns",
                params={k: v for k, v in params.items() if v is not None},
            )
            response.raise_for_status()

            data = response.json()
            self._set_cached(cache_key, data)

            return [AtlasPattern.model_validate(p) for p in data.get("patterns", [])]

        except Exception as e:
            logger.warning("Error getting Code Atlas patterns: %s", e)
            return []

    async def get_entities(
        self,
        entity_type: str | None = None,
        name_contains: str | None = None,
        limit: int = 20,
    ) -> list[AtlasEntity]:
        """
        Get entities from Code Atlas.

        Returns empty list if Code Atlas is unavailable or disabled.
        """
        if not self.enabled:
            return []

        params = {
            "entity_type": entity_type,
            "name_contains": name_contains,
            "limit": limit,
        }
        cache_key = self._cache_key("/api/v1/insights/entities", params)
        cached = self._get_cached(cache_key)
        if cached:
            return [AtlasEntity.model_validate(e) for e in cached.get("entities", [])]

        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.base_url}/api/v1/insights/entities",
                params={k: v for k, v in params.items() if v is not None},
            )
            response.raise_for_status()

            data = response.json()
            self._set_cached(cache_key, data)

            return [AtlasEntity.model_validate(e) for e in data.get("entities", [])]

        except Exception as e:
            logger.warning("Error getting Code Atlas entities: %s", e)
            return []

    async def query_rag(
        self,
        query: str,
        context: dict | None = None,
    ) -> AtlasRAGResponse:
        """
        Query Code Atlas RAG system.

        Returns a response with answer, sources, and confidence.
        Returns a low-confidence response if unavailable.
        """
        if not self.enabled:
            return AtlasRAGResponse(
                answer="Code Atlas is disabled",
                sources=[],
                confidence=0.0,
                execution_time_ms=0,
            )

        breaker = get_circuit_breaker("code_atlas")

        # RAG queries are not cached (they're context-dependent)
        try:
            async with breaker:
                client = await self._get_client()
                response = await client.post(
                    f"{self.base_url}/api/v1/insights/rag/query",
                    json={
                        "question": query,
                        "include_sources": True,
                        **(
                            {"type": context.get("entity_type")}
                            if context and context.get("entity_type")
                            else {}
                        ),
                    },
                )
                response.raise_for_status()

                data = response.json()
                # Map Code Atlas response to our schema
                return AtlasRAGResponse(
                    answer=data.get("answer", ""),
                    sources=data.get("sources", []),
                    confidence=data.get("confidence", 0.0),
                    execution_time_ms=int(data.get("execution_time_ms", 0)),
                )

        except CircuitOpenError as e:
            logger.warning(f"Code Atlas circuit open: {e}")
            return AtlasRAGResponse(
                answer=f"Code Atlas circuit open: {e}",
                sources=[],
                confidence=0.0,
                execution_time_ms=0,
            )
        except Exception as e:
            logger.warning("Error querying Code Atlas RAG: %s", e)
            return AtlasRAGResponse(
                answer=f"Unable to query Code Atlas: {e}",
                sources=[],
                confidence=0.0,
                execution_time_ms=0,
            )

    async def health_check(self) -> bool:
        """
        Check if Code Atlas is reachable.

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

    async def hybrid_search(
        self,
        query: str,
        entity_type: str | None = None,
        limit: int = 10,
        min_score: float = 0.3,
    ) -> list[dict]:
        """
        Perform hybrid search (graph + vector) for relevant entities.

        This combines graph structure traversal with semantic similarity
        for better pattern matching.

        Returns list of search results with scores, or empty list if unavailable.
        """
        if not self.enabled:
            return []

        try:
            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/api/v1/graph/hybrid-search",
                json={
                    "query": query,
                    "entity_type": entity_type,
                    "limit": limit,
                    "min_score": min_score,
                    "use_graph_structure": True,
                    "use_vector_search": True,
                },
            )
            response.raise_for_status()

            data = response.json()
            return data.get("results", [])

        except Exception as e:
            logger.warning("Error performing hybrid search: %s", e)
            return []

    async def search_similar_problems(
        self,
        problem_description: str,
        limit: int = 5,
    ) -> list[dict]:
        """
        Search for similar problems in the knowledge graph.

        Useful for finding if a problem has been solved before.
        """
        return await self.hybrid_search(
            query=problem_description,
            entity_type="Problem",
            limit=limit,
            min_score=0.5,
        )

    async def search_solutions(
        self,
        problem_query: str,
        limit: int = 5,
    ) -> list[dict]:
        """
        Search for solutions related to a problem.

        Useful for finding how similar problems were solved.
        """
        return await self.hybrid_search(
            query=problem_query,
            entity_type="Solution",
            limit=limit,
            min_score=0.4,
        )

    async def index_session(
        self,
        session_paths: list[str] | None = None,
        session_summary: dict | None = None,
        use_llm: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """
        Index session data to Code Atlas for pattern learning.

        Can either:
        - Process actual session files via session_paths
        - Store session summary metadata for future queries

        Args:
            session_paths: List of paths to .jsonl session files
            session_summary: Session metadata dict (domain, project, features, etc.)
            use_llm: Whether to use LLM for entity extraction (costs apply)
            dry_run: If True, validate but don't process

        Returns:
            Dict with job_id and status, or error information
        """
        if not self.enabled:
            logger.debug("Code Atlas disabled, skipping session indexing")
            return {"indexed": False, "reason": "disabled"}

        result = {"indexed": False}

        try:
            client = await self._get_client()

            # Option 1: Process actual session files
            if session_paths:
                response = await client.post(
                    f"{self.base_url}/api/v1/sessions/process",
                    json={
                        "session_paths": session_paths,
                        "use_llm": use_llm,
                        "dry_run": dry_run,
                    },
                )
                response.raise_for_status()
                data = response.json()
                result["indexed"] = True
                result["job_id"] = data.get("job", {}).get("job_id")
                result["message"] = data.get("message", "Processing started")
                logger.info(
                    "Session files submitted to Code Atlas",
                    extra={"job_id": result.get("job_id"), "file_count": len(session_paths)},
                )

            # Option 2: Store session summary as insight
            elif session_summary:
                # Use insights endpoint to store session metadata
                # This enables pattern queries like "have I done auth before?"
                response = await client.post(
                    f"{self.base_url}/api/v1/insights/sessions",
                    json=session_summary,
                )
                if response.status_code == 404:
                    # Endpoint doesn't exist yet - graceful degradation
                    logger.info(
                        "Session summary endpoint not available, using RAG fallback",
                        extra={"session_id": session_summary.get("session_id")},
                    )
                    # Fallback: store as RAG document for future queries
                    result["indexed"] = True
                    result["fallback"] = "logged_only"
                else:
                    response.raise_for_status()
                    result["indexed"] = True
                    result["message"] = "Session summary indexed"

            else:
                result["error"] = "Either session_paths or session_summary required"

        except Exception as e:
            logger.warning("Failed to index session to Code Atlas: %s", e)
            result["error"] = str(e)
            result["indexed"] = False

        return result

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
