"""
Tests for Meta-Learning Bridges (Code Atlas and Tech Diligence).

Tests graceful degradation, caching, and HTTP client behavior.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from forge_harness.meta_learning.bridges.code_atlas import CodeAtlasBridge
from forge_harness.meta_learning.bridges.tech_diligence import TechDiligenceBridge
from forge_harness.meta_learning.config import CodeAtlasConfig, TechDiligenceConfig
from forge_harness.meta_learning.schemas import (
    AnalysisJobStatus,
    AtlasSignals,
    DiligenceReport,
    DiligenceSignals,
)


class TestCodeAtlasBridge:
    """Tests for Code Atlas Bridge."""

    @pytest.fixture
    def bridge(self):
        """Create a CodeAtlasBridge instance."""
        return CodeAtlasBridge(
            base_url="http://localhost:8080",
            cache_ttl_seconds=60,
        )

    @pytest.fixture
    def disabled_bridge(self):
        """Create a disabled CodeAtlasBridge instance."""
        return CodeAtlasBridge(
            base_url="http://localhost:8080",
            enabled=False,
        )

    def test_from_config(self):
        """Test bridge creation from config."""
        config = CodeAtlasConfig(
            base_url="http://test:9000",
            api_key="test-key",
            timeout_seconds=45,
            cache_ttl_seconds=120,
        )
        bridge = CodeAtlasBridge.from_config(config)
        assert bridge.base_url == "http://test:9000"
        assert bridge.api_key == "test-key"
        assert bridge.timeout_seconds == 45
        assert bridge.cache_ttl_seconds == 120

    @pytest.mark.asyncio
    async def test_disabled_returns_empty_signals(self, disabled_bridge):
        """Test that disabled bridge returns empty signals."""
        signals = await disabled_bridge.get_signals("domain", "project")
        assert isinstance(signals, AtlasSignals)
        assert signals.related_patterns == []
        assert signals.similar_decisions == []

    @pytest.mark.asyncio
    async def test_disabled_returns_empty_patterns(self, disabled_bridge):
        """Test that disabled bridge returns empty patterns."""
        patterns = await disabled_bridge.get_patterns()
        assert patterns == []

    @pytest.mark.asyncio
    async def test_disabled_returns_empty_entities(self, disabled_bridge):
        """Test that disabled bridge returns empty entities."""
        entities = await disabled_bridge.get_entities()
        assert entities == []

    @pytest.mark.asyncio
    async def test_disabled_returns_low_confidence_rag(self, disabled_bridge):
        """Test that disabled bridge returns low-confidence RAG response."""
        response = await disabled_bridge.query_rag("test query")
        assert response.confidence == 0.0
        assert "disabled" in response.answer.lower()

    @pytest.mark.asyncio
    async def test_disabled_health_check_returns_false(self, disabled_bridge):
        """Test that disabled bridge fails health check."""
        result = await disabled_bridge.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_get_signals_success(self, bridge):
        """Test successful signals retrieval from multiple API calls."""
        # Mock responses for different endpoints
        recurring_problems_response = MagicMock()
        recurring_problems_response.status_code = 200
        recurring_problems_response.json.return_value = {
            "problems": [{"entity": {"name": "issue-1"}, "session_count": 3}]
        }

        hybrid_search_response = MagicMock()
        hybrid_search_response.status_code = 200
        hybrid_search_response.json.return_value = {
            "results": [
                {
                    "entity_id": "pat-1",
                    "entity_name": "Auth Pattern",
                    "combined_score": 0.8,
                }
            ]
        }

        entity_search_response = MagicMock()
        entity_search_response.status_code = 200
        entity_search_response.json.return_value = {
            "results": [{"entity": {"id": "dec-1"}, "score": 0.7}]
        }

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()

            # Mock get for recurring-problems and entity-search
            async def mock_get(url, **kwargs):
                if "recurring-problems" in url:
                    return recurring_problems_response
                elif "entities/search" in url:
                    return entity_search_response
                return MagicMock(status_code=404)

            # Mock post for hybrid-search
            async def mock_post(url, **kwargs):
                if "hybrid-search" in url:
                    return hybrid_search_response
                return MagicMock(status_code=404)

            client.get = mock_get
            client.post = mock_post
            mock_client.return_value = client

            signals = await bridge.get_signals("domain", "project")

            assert len(signals.related_patterns) == 1
            assert signals.related_patterns[0].pattern_id == "pat-1"
            assert "dec-1" in signals.similar_decisions
            assert "issue-1" in signals.recurring_problems

    @pytest.mark.asyncio
    async def test_get_signals_timeout(self, bridge):
        """Test graceful degradation on timeout."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            # Both get and post should timeout
            client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.return_value = client

            signals = await bridge.get_signals("domain", "project")

            # Should return empty signals, not raise
            assert isinstance(signals, AtlasSignals)
            assert signals.related_patterns == []

    @pytest.mark.asyncio
    async def test_get_signals_connection_error(self, bridge):
        """Test graceful degradation on connection error."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            # Both get and post should fail with connection error
            client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            mock_client.return_value = client

            signals = await bridge.get_signals("domain", "project")

            assert isinstance(signals, AtlasSignals)
            assert signals.related_patterns == []

    @pytest.mark.asyncio
    async def test_caching(self, bridge):
        """Test response caching."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "related_patterns": [],
            "similar_decisions": [],
            "recurring_problems": [],
        }
        mock_response.raise_for_status = MagicMock()

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_response

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = mock_post
            mock_client.return_value = client

            # First call - should hit the API
            await bridge.get_signals("domain", "project")
            assert call_count == 1

            # Second call - should use cache
            await bridge.get_signals("domain", "project")
            assert call_count == 1  # Not incremented

    def test_cache_clear(self, bridge):
        """Test cache clearing."""
        # Add something to cache
        bridge._cache["test"] = MagicMock()
        assert len(bridge._cache) == 1

        bridge.clear_cache()
        assert len(bridge._cache) == 0

    def test_cache_stats(self, bridge):
        """Test cache statistics."""
        stats = bridge.get_cache_stats()
        assert "total_entries" in stats
        assert "active_entries" in stats
        assert "expired_entries" in stats

    @pytest.mark.asyncio
    async def test_index_session_disabled(self, disabled_bridge):
        """Test that disabled bridge skips indexing."""
        result = await disabled_bridge.index_session(session_summary={"session_id": "test"})
        assert result["indexed"] is False
        assert result["reason"] == "disabled"

    @pytest.mark.asyncio
    async def test_index_session_with_files_success(self, bridge):
        """Test successful session indexing with file paths."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "job": {"job_id": "job-abc123"},
            "message": "Processing job created",
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            result = await bridge.index_session(
                session_paths=["/path/to/session.jsonl"],
                use_llm=False,
            )

            assert result["indexed"] is True
            assert result["job_id"] == "job-abc123"
            client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_index_session_with_summary_success(self, bridge):
        """Test successful session indexing with summary."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            result = await bridge.index_session(
                session_summary={
                    "session_id": "test-session",
                    "domain": "test-domain",
                    "project": "test-project",
                }
            )

            assert result["indexed"] is True

    @pytest.mark.asyncio
    async def test_index_session_with_summary_fallback(self, bridge):
        """Test graceful fallback when insights endpoint not available."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            result = await bridge.index_session(
                session_summary={
                    "session_id": "test-session",
                    "domain": "test-domain",
                    "project": "test-project",
                }
            )

            # Should still mark as indexed with fallback
            assert result["indexed"] is True
            assert result.get("fallback") == "logged_only"

    @pytest.mark.asyncio
    async def test_index_session_requires_input(self, bridge):
        """Test that index_session requires either paths or summary."""
        result = await bridge.index_session()
        assert result["indexed"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_index_session_handles_error(self, bridge):
        """Test graceful error handling on index failure."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=Exception("Connection failed"))
            mock_client.return_value = client

            result = await bridge.index_session(session_summary={"session_id": "test"})

            assert result["indexed"] is False
            assert "error" in result

    @pytest.mark.asyncio
    async def test_get_patterns_success(self, bridge):
        """Test successful patterns retrieval."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "patterns": [
                {
                    "pattern_id": "pat-1",
                    "pattern_type": "auth",
                    "entities": ["JWT", "OAuth"],
                    "session_count": 5,
                    "confidence": 0.9,
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            patterns = await bridge.get_patterns(pattern_type="auth", limit=10)

            assert len(patterns) == 1
            assert patterns[0].pattern_id == "pat-1"
            assert patterns[0].pattern_type == "auth"
            assert patterns[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_get_patterns_error(self, bridge):
        """Test graceful degradation on patterns error."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=Exception("Connection failed"))
            mock_client.return_value = client

            patterns = await bridge.get_patterns()

            assert patterns == []

    @pytest.mark.asyncio
    async def test_get_patterns_cached(self, bridge):
        """Test patterns caching."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"patterns": []}
        mock_response.raise_for_status = MagicMock()

        call_count = 0

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_response

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = mock_get
            mock_client.return_value = client

            await bridge.get_patterns(pattern_type="test")
            await bridge.get_patterns(pattern_type="test")

            assert call_count == 1  # Second call used cache

    @pytest.mark.asyncio
    async def test_get_entities_success(self, bridge):
        """Test successful entities retrieval."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "entities": [
                {
                    "id": "ent-1",
                    "entity_type": "Solution",
                    "name": "JWT Authentication",
                    "mention_count": 3,
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            entities = await bridge.get_entities(entity_type="Solution", limit=5)

            assert len(entities) == 1
            assert entities[0].id == "ent-1"
            assert entities[0].name == "JWT Authentication"

    @pytest.mark.asyncio
    async def test_get_entities_error(self, bridge):
        """Test graceful degradation on entities error."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=httpx.RequestError("Failed"))
            mock_client.return_value = client

            entities = await bridge.get_entities()

            assert entities == []

    @pytest.mark.asyncio
    async def test_query_rag_success(self, bridge):
        """Test successful RAG query."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "answer": "JWT authentication uses signed tokens for stateless auth.",
            "sources": ["session-1", "session-2"],
            "confidence": 0.85,
            "execution_time_ms": 150,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            response = await bridge.query_rag("How does authentication work?")

            assert "JWT" in response.answer
            assert response.confidence == 0.85
            assert len(response.sources) == 2
            assert response.execution_time_ms == 150

    @pytest.mark.asyncio
    async def test_query_rag_with_context(self, bridge):
        """Test RAG query with entity type context."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "answer": "Found solutions",
            "sources": [],
            "confidence": 0.7,
            "execution_time_ms": 100,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            response = await bridge.query_rag("Find patterns", context={"entity_type": "Solution"})

            assert response.confidence == 0.7
            # Verify the context was passed
            call_args = client.post.call_args
            assert call_args[1]["json"]["type"] == "Solution"

    @pytest.mark.asyncio
    async def test_query_rag_error(self, bridge):
        """Test graceful degradation on RAG error."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=Exception("RAG service down"))
            mock_client.return_value = client

            response = await bridge.query_rag("test query")

            assert response.confidence == 0.0
            assert "Unable to query" in response.answer

    @pytest.mark.asyncio
    async def test_health_check_success(self, bridge):
        """Test successful health check."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            result = await bridge.health_check()

            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, bridge):
        """Test health check when service is down."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client.return_value = client

            result = await bridge.health_check()

            assert result is False

    @pytest.mark.asyncio
    async def test_health_check_non_200(self, bridge):
        """Test health check with non-200 response."""
        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            result = await bridge.health_check()

            assert result is False

    @pytest.mark.asyncio
    async def test_hybrid_search_success(self, bridge):
        """Test successful hybrid search."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "entity_id": "ent-1",
                    "entity_name": "Auth Pattern",
                    "combined_score": 0.85,
                    "graph_score": 0.8,
                    "vector_score": 0.9,
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            results = await bridge.hybrid_search(
                query="authentication",
                entity_type="Solution",
                limit=5,
                min_score=0.5,
            )

            assert len(results) == 1
            assert results[0]["entity_id"] == "ent-1"
            assert results[0]["combined_score"] == 0.85

    @pytest.mark.asyncio
    async def test_hybrid_search_disabled(self, disabled_bridge):
        """Test hybrid search when disabled."""
        results = await disabled_bridge.hybrid_search("test query")
        assert results == []

    @pytest.mark.asyncio
    async def test_hybrid_search_error(self, bridge):
        """Test graceful degradation on hybrid search error."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client.return_value = client

            results = await bridge.hybrid_search("test query")

            assert results == []

    @pytest.mark.asyncio
    async def test_search_similar_problems(self, bridge):
        """Test search_similar_problems delegates to hybrid_search."""
        with patch.object(bridge, "hybrid_search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [{"entity_id": "prob-1"}]

            results = await bridge.search_similar_problems("Login fails", limit=3)

            mock_search.assert_called_once_with(
                query="Login fails",
                entity_type="Problem",
                limit=3,
                min_score=0.5,
            )
            assert results == [{"entity_id": "prob-1"}]

    @pytest.mark.asyncio
    async def test_search_solutions(self, bridge):
        """Test search_solutions delegates to hybrid_search."""
        with patch.object(bridge, "hybrid_search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [{"entity_id": "sol-1"}]

            results = await bridge.search_solutions("Fix auth", limit=3)

            mock_search.assert_called_once_with(
                query="Fix auth",
                entity_type="Solution",
                limit=3,
                min_score=0.4,
            )
            assert results == [{"entity_id": "sol-1"}]

    @pytest.mark.asyncio
    async def test_get_client_with_api_key(self):
        """Test that API key is set in headers."""
        bridge = CodeAtlasBridge(
            base_url="http://localhost:8080",
            api_key="test-api-key-123",
        )

        client = await bridge._get_client()

        assert client is not None
        assert client.headers.get("X-API-Key") == "test-api-key-123"
        assert client.headers.get("Content-Type") == "application/json"

        await bridge.close()

    @pytest.mark.asyncio
    async def test_get_client_without_api_key(self):
        """Test client creation without API key."""
        bridge = CodeAtlasBridge(
            base_url="http://localhost:8080",
            api_key=None,
        )

        client = await bridge._get_client()

        assert client is not None
        assert "X-API-Key" not in client.headers
        assert client.headers.get("Content-Type") == "application/json"

        await bridge.close()

    @pytest.mark.asyncio
    async def test_close_client(self, bridge):
        """Test closing the HTTP client."""
        # Create client first
        client = await bridge._get_client()
        assert bridge._client is not None
        assert not bridge._client.is_closed

        # Close it
        await bridge.close()
        assert bridge._client is None

    @pytest.mark.asyncio
    async def test_close_when_no_client(self, bridge):
        """Test close is safe when no client exists."""
        assert bridge._client is None
        await bridge.close()  # Should not raise
        assert bridge._client is None

    @pytest.mark.asyncio
    async def test_get_client_recreates_when_closed(self, bridge):
        """Test that _get_client creates new client if previous was closed."""
        # Create and close
        client1 = await bridge._get_client()
        await bridge.close()

        # Should create new client
        client2 = await bridge._get_client()

        assert client2 is not None
        assert not client2.is_closed

        await bridge.close()

    def test_cache_key_deterministic(self, bridge):
        """Test cache key generation is deterministic."""
        key1 = bridge._cache_key("/api/test", {"a": 1, "b": 2})
        key2 = bridge._cache_key("/api/test", {"b": 2, "a": 1})  # Different order

        assert key1 == key2  # Should be same (sorted)

    def test_cache_key_unique(self, bridge):
        """Test cache keys are unique for different inputs."""
        key1 = bridge._cache_key("/api/test", {"a": 1})
        key2 = bridge._cache_key("/api/test", {"a": 2})
        key3 = bridge._cache_key("/api/other", {"a": 1})

        assert key1 != key2
        assert key1 != key3

    def test_cache_expiration(self, bridge):
        """Test cache entries expire correctly."""
        from forge_harness.meta_learning.bridges.code_atlas import CacheEntry

        # Create entry with 0 TTL (already expired)
        entry = CacheEntry({"data": "value"}, ttl_seconds=0)
        assert entry.is_expired() is True

        # Create entry with long TTL
        entry_long = CacheEntry({"data": "value"}, ttl_seconds=3600)
        assert entry_long.is_expired() is False

    def test_get_cached_removes_expired(self, bridge):
        """Test that _get_cached removes expired entries."""
        from forge_harness.meta_learning.bridges.code_atlas import CacheEntry

        # Add expired entry
        expired_entry = CacheEntry({"data": "old"}, ttl_seconds=0)
        bridge._cache["expired-key"] = expired_entry

        # Should return None and remove entry
        result = bridge._get_cached("expired-key")

        assert result is None
        assert "expired-key" not in bridge._cache

    def test_set_cached_respects_ttl_zero(self, bridge):
        """Test that caching is disabled when TTL is 0."""
        bridge.cache_ttl_seconds = 0
        bridge._set_cached("test-key", {"data": "value"})

        assert "test-key" not in bridge._cache

    @pytest.mark.asyncio
    async def test_get_signals_http_status_error(self, bridge):
        """Test graceful degradation on HTTP status error."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            error = httpx.HTTPStatusError(
                "Server Error",
                request=MagicMock(),
                response=MagicMock(status_code=500),
            )
            client.get = AsyncMock(side_effect=error)
            client.post = AsyncMock(side_effect=error)
            mock_client.return_value = client

            signals = await bridge.get_signals("domain", "project")

            assert isinstance(signals, AtlasSignals)
            assert signals.related_patterns == []


class TestTechDiligenceBridge:
    """Tests for Tech Diligence Bridge."""

    @pytest.fixture
    def bridge(self):
        """Create a TechDiligenceBridge instance."""
        return TechDiligenceBridge(
            base_url="http://localhost:8081",
            cache_ttl_seconds=300,
            poll_interval_seconds=1,  # Fast polling for tests
        )

    @pytest.fixture
    def disabled_bridge(self):
        """Create a disabled TechDiligenceBridge instance."""
        return TechDiligenceBridge(
            base_url="http://localhost:8081",
            enabled=False,
        )

    def test_from_config(self):
        """Test bridge creation from config."""
        config = TechDiligenceConfig(
            base_url="http://test:9001",
            api_key="test-key",
            timeout_seconds=90,
            poll_interval_seconds=10,
            cache_ttl_seconds=1800,
        )
        bridge = TechDiligenceBridge.from_config(config)
        assert bridge.base_url == "http://test:9001"
        assert bridge.api_key == "test-key"
        assert bridge.timeout_seconds == 90
        assert bridge.poll_interval_seconds == 10

    @pytest.mark.asyncio
    async def test_disabled_returns_empty_signals(self, disabled_bridge):
        """Test that disabled bridge returns empty signals."""
        signals = await disabled_bridge.get_signals("domain", "project")
        assert isinstance(signals, DiligenceSignals)
        assert signals.overall_score == 100.0
        assert signals.risk_level == "low"

    @pytest.mark.asyncio
    async def test_disabled_returns_none_for_submit(self, disabled_bridge):
        """Test that disabled bridge returns None for submit."""
        result = await disabled_bridge.submit_analysis("/path/to/repo")
        assert result is None

    @pytest.mark.asyncio
    async def test_disabled_returns_none_for_report(self, disabled_bridge):
        """Test that disabled bridge returns None for get_report."""
        result = await disabled_bridge.get_report("report-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_disabled_health_check_returns_false(self, disabled_bridge):
        """Test that disabled bridge fails health check."""
        result = await disabled_bridge.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_get_signals_success(self, bridge):
        """Test successful signals retrieval."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "report_id": "rep-1",
            "summary_score": 85.0,
            "findings": [
                {
                    "finding_id": "f-1",
                    "title": "SQL Injection",
                    "severity": "critical",
                    "category": "security",
                    "scanner": "bandit",
                },
                {
                    "finding_id": "f-2",
                    "title": "Unused import",
                    "severity": "low",
                    "category": "code_quality",
                    "scanner": "semgrep",
                },
            ],
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            signals = await bridge.get_signals("domain", "project")

            assert signals.overall_score == 85.0
            assert signals.risk_level == "critical"
            assert len(signals.blocking_issues) == 1
            assert len(signals.warnings) == 1

    @pytest.mark.asyncio
    async def test_get_signals_not_found(self, bridge):
        """Test signals when no report exists."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            signals = await bridge.get_signals("domain", "project")

            # Should return clean signals for 404
            assert signals.overall_score == 100.0
            assert signals.risk_level == "low"

    @pytest.mark.asyncio
    async def test_get_signals_timeout(self, bridge):
        """Test graceful degradation on timeout."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.return_value = client

            signals = await bridge.get_signals("domain", "project")

            assert isinstance(signals, DiligenceSignals)
            assert signals.overall_score == 100.0

    @pytest.mark.asyncio
    async def test_submit_analysis(self, bridge):
        """Test job submission."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"job_id": "job-123"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            job_id = await bridge.submit_analysis("/path/to/repo")

            assert job_id == "job-123"

    @pytest.mark.asyncio
    async def test_get_job_status(self, bridge):
        """Test job status retrieval."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "job_id": "job-123",
            "status": "running",
            "progress_percent": 50,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            status = await bridge.get_job_status("job-123")

            assert status is not None
            assert status.job_id == "job-123"
            assert status.status == "running"
            assert status.progress_percent == 50

    def test_report_to_signals_risk_levels(self, bridge):
        """Test risk level determination."""
        # Critical finding
        signals = bridge._report_to_signals(
            {
                "summary_score": 50.0,
                "findings": [
                    {
                        "finding_id": "f1",
                        "title": "Critical",
                        "severity": "critical",
                        "category": "security",
                    },
                ],
            }
        )
        assert signals.risk_level == "critical"

        # High finding only
        signals = bridge._report_to_signals(
            {
                "summary_score": 70.0,
                "findings": [
                    {
                        "finding_id": "f1",
                        "title": "High",
                        "severity": "high",
                        "category": "security",
                    },
                ],
            }
        )
        assert signals.risk_level == "high"

        # Medium finding only
        signals = bridge._report_to_signals(
            {
                "summary_score": 85.0,
                "findings": [
                    {
                        "finding_id": "f1",
                        "title": "Medium",
                        "severity": "medium",
                        "category": "code_quality",
                    },
                ],
            }
        )
        assert signals.risk_level == "medium"

        # Low finding only
        signals = bridge._report_to_signals(
            {
                "summary_score": 95.0,
                "findings": [
                    {
                        "finding_id": "f1",
                        "title": "Low",
                        "severity": "low",
                        "category": "code_quality",
                    },
                ],
            }
        )
        assert signals.risk_level == "low"

        # No findings
        signals = bridge._report_to_signals(
            {
                "summary_score": 100.0,
                "findings": [],
            }
        )
        assert signals.risk_level == "low"

    def test_report_to_signals_deduplication(self, bridge):
        """Test finding deduplication."""
        signals = bridge._report_to_signals(
            {
                "summary_score": 80.0,
                "findings": [
                    {
                        "finding_id": "f1",
                        "title": "Same Issue",
                        "severity": "high",
                        "category": "security",
                        "scanner": "bandit",
                    },
                    {
                        "finding_id": "f2",
                        "title": "Same Issue",
                        "severity": "high",
                        "category": "security",
                        "scanner": "semgrep",
                    },
                    {
                        "finding_id": "f3",
                        "title": "Different Issue",
                        "severity": "medium",
                        "category": "security",
                        "scanner": "bandit",
                    },
                ],
            }
        )
        # Should only have 2 unique findings (deduplicated by title)
        total_findings = len(signals.blocking_issues) + len(signals.warnings)
        assert total_findings == 2

    def test_cache_operations(self, bridge):
        """Test cache operations."""
        # Add to cache
        bridge._set_cached("test-key", {"data": "value"})
        assert bridge._get_cached("test-key") == {"data": "value"}

        # Clear cache
        bridge.clear_cache()
        assert bridge._get_cached("test-key") is None

        # Stats
        stats = bridge.get_cache_stats()
        assert stats["total_entries"] == 0

    @pytest.mark.asyncio
    async def test_get_client_with_api_key(self):
        """Test that API key is set in Authorization header."""
        bridge = TechDiligenceBridge(
            base_url="http://localhost:8081",
            api_key="test-bearer-token",
        )

        client = await bridge._get_client()

        assert client is not None
        assert client.headers.get("Authorization") == "Bearer test-bearer-token"
        assert client.headers.get("Content-Type") == "application/json"

        await bridge.close()

    @pytest.mark.asyncio
    async def test_get_client_without_api_key(self):
        """Test client creation without API key."""
        bridge = TechDiligenceBridge(
            base_url="http://localhost:8081",
            api_key=None,
        )

        client = await bridge._get_client()

        assert client is not None
        assert "Authorization" not in client.headers
        assert client.headers.get("Content-Type") == "application/json"

        await bridge.close()

    @pytest.mark.asyncio
    async def test_close_client(self, bridge):
        """Test closing the HTTP client."""
        client = await bridge._get_client()
        assert bridge._client is not None
        assert not bridge._client.is_closed

        await bridge.close()
        assert bridge._client is None

    @pytest.mark.asyncio
    async def test_close_when_no_client(self, bridge):
        """Test close is safe when no client exists."""
        assert bridge._client is None
        await bridge.close()
        assert bridge._client is None

    @pytest.mark.asyncio
    async def test_get_client_recreates_when_closed(self, bridge):
        """Test that _get_client creates new client if previous was closed."""
        client1 = await bridge._get_client()
        await bridge.close()

        client2 = await bridge._get_client()

        assert client2 is not None
        assert not client2.is_closed

        await bridge.close()

    @pytest.mark.asyncio
    async def test_get_signals_connection_error(self, bridge):
        """Test graceful degradation on connection error."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client.return_value = client

            signals = await bridge.get_signals("domain", "project")

            assert isinstance(signals, DiligenceSignals)
            assert signals.overall_score == 100.0

    @pytest.mark.asyncio
    async def test_get_signals_http_status_error(self, bridge):
        """Test graceful degradation on HTTP status error."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            error = httpx.HTTPStatusError(
                "Server Error",
                request=MagicMock(),
                response=MagicMock(status_code=500),
            )
            client.get = AsyncMock(side_effect=error)
            mock_client.return_value = client

            signals = await bridge.get_signals("domain", "project")

            assert isinstance(signals, DiligenceSignals)
            assert signals.overall_score == 100.0

    @pytest.mark.asyncio
    async def test_get_signals_cached(self, bridge):
        """Test signals caching."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "summary_score": 90.0,
            "findings": [],
        }
        mock_response.raise_for_status = MagicMock()

        call_count = 0

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_response

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = mock_get
            mock_client.return_value = client

            await bridge.get_signals("domain", "project")
            await bridge.get_signals("domain", "project")

            assert call_count == 1  # Second call used cache

    @pytest.mark.asyncio
    async def test_submit_analysis_error(self, bridge):
        """Test graceful error handling on submit failure."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=Exception("Connection failed"))
            mock_client.return_value = client

            job_id = await bridge.submit_analysis("/path/to/repo")

            assert job_id is None

    @pytest.mark.asyncio
    async def test_submit_analysis_with_scanners(self, bridge):
        """Test job submission with custom scanners."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"job_id": "job-456"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            job_id = await bridge.submit_analysis("/path/to/repo", scanners=["bandit", "semgrep"])

            assert job_id == "job-456"
            call_args = client.post.call_args
            assert call_args[1]["json"]["scanners"] == ["bandit", "semgrep"]

    @pytest.mark.asyncio
    async def test_get_job_status_disabled(self, disabled_bridge):
        """Test that disabled bridge returns None for job status."""
        result = await disabled_bridge.get_job_status("job-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_job_status_error(self, bridge):
        """Test graceful error handling on job status failure."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=Exception("Connection failed"))
            mock_client.return_value = client

            status = await bridge.get_job_status("job-123")

            assert status is None

    @pytest.mark.asyncio
    async def test_get_report_success(self, bridge):
        """Test successful report retrieval."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "report_id": "rep-1",
            "created_at": "2024-01-01T00:00:00Z",
            "repository": "/path/to/repo",
            "summary_score": 85.0,
            "findings": [],
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            report = await bridge.get_report("rep-1")

            assert report is not None
            assert report.report_id == "rep-1"
            assert report.summary_score == 85.0

    @pytest.mark.asyncio
    async def test_get_report_error(self, bridge):
        """Test graceful error handling on report retrieval failure."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=Exception("Not found"))
            mock_client.return_value = client

            report = await bridge.get_report("rep-1")

            assert report is None

    @pytest.mark.asyncio
    async def test_analyze_and_wait_disabled(self, disabled_bridge):
        """Test that disabled bridge returns None for analyze_and_wait."""
        result = await disabled_bridge.analyze_and_wait("/path/to/repo")
        assert result is None

    @pytest.mark.asyncio
    async def test_analyze_and_wait_submit_fails(self, bridge):
        """Test analyze_and_wait when submit fails."""
        with patch.object(bridge, "submit_analysis", new_callable=AsyncMock) as mock_submit:
            mock_submit.return_value = None

            report = await bridge.analyze_and_wait("/path/to/repo")

            assert report is None

    @pytest.mark.asyncio
    async def test_analyze_and_wait_success(self, bridge):
        """Test successful analyze_and_wait."""
        mock_report = DiligenceReport(
            report_id="rep-1",
            created_at="2024-01-01T00:00:00Z",
            repository="/path/to/repo",
            summary_score=90.0,
            findings=[],
        )

        with (
            patch.object(bridge, "submit_analysis", new_callable=AsyncMock) as mock_submit,
            patch.object(bridge, "get_job_status", new_callable=AsyncMock) as mock_status,
            patch.object(bridge, "get_report", new_callable=AsyncMock) as mock_report_fn,
        ):
            mock_submit.return_value = "job-123"
            mock_status.return_value = AnalysisJobStatus(
                job_id="job-123",
                status="completed",
                report_id="rep-1",
            )
            mock_report_fn.return_value = mock_report

            report = await bridge.analyze_and_wait("/path/to/repo", max_wait_seconds=10)

            assert report is not None
            assert report.report_id == "rep-1"

    @pytest.mark.asyncio
    async def test_analyze_and_wait_job_fails(self, bridge):
        """Test analyze_and_wait when job fails."""
        with (
            patch.object(bridge, "submit_analysis", new_callable=AsyncMock) as mock_submit,
            patch.object(bridge, "get_job_status", new_callable=AsyncMock) as mock_status,
        ):
            mock_submit.return_value = "job-123"
            mock_status.return_value = AnalysisJobStatus(
                job_id="job-123",
                status="failed",
                error_message="Analysis crashed",
            )

            report = await bridge.analyze_and_wait("/path/to/repo", max_wait_seconds=10)

            assert report is None

    @pytest.mark.asyncio
    async def test_analyze_and_wait_status_check_fails(self, bridge):
        """Test analyze_and_wait when status check fails."""
        with (
            patch.object(bridge, "submit_analysis", new_callable=AsyncMock) as mock_submit,
            patch.object(bridge, "get_job_status", new_callable=AsyncMock) as mock_status,
        ):
            mock_submit.return_value = "job-123"
            mock_status.return_value = None  # Status check failed

            report = await bridge.analyze_and_wait("/path/to/repo", max_wait_seconds=10)

            assert report is None

    @pytest.mark.asyncio
    async def test_analyze_and_wait_timeout(self, bridge):
        """Test analyze_and_wait timeout."""
        # Use a short timeout bridge
        bridge.poll_interval_seconds = 0.1

        with (
            patch.object(bridge, "submit_analysis", new_callable=AsyncMock) as mock_submit,
            patch.object(bridge, "get_job_status", new_callable=AsyncMock) as mock_status,
        ):
            mock_submit.return_value = "job-123"
            # Job stays running forever
            mock_status.return_value = AnalysisJobStatus(
                job_id="job-123",
                status="running",
                progress_percent=50,
            )

            report = await bridge.analyze_and_wait(
                "/path/to/repo",
                max_wait_seconds=0.2,  # Very short timeout
            )

            assert report is None

    @pytest.mark.asyncio
    async def test_health_check_success(self, bridge):
        """Test successful health check."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            result = await bridge.health_check()

            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, bridge):
        """Test health check when service is down."""
        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client.return_value = client

            result = await bridge.health_check()

            assert result is False

    @pytest.mark.asyncio
    async def test_health_check_non_200(self, bridge):
        """Test health check with non-200 response."""
        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch.object(bridge, "_get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            result = await bridge.health_check()

            assert result is False

    def test_cache_key_deterministic(self, bridge):
        """Test cache key generation is deterministic."""
        key1 = bridge._cache_key("/api/test", {"a": 1, "b": 2})
        key2 = bridge._cache_key("/api/test", {"b": 2, "a": 1})

        assert key1 == key2

    def test_cache_key_unique(self, bridge):
        """Test cache keys are unique for different inputs."""
        key1 = bridge._cache_key("/api/test", {"a": 1})
        key2 = bridge._cache_key("/api/test", {"a": 2})
        key3 = bridge._cache_key("/api/other", {"a": 1})

        assert key1 != key2
        assert key1 != key3

    def test_cache_expiration(self, bridge):
        """Test cache entries expire correctly."""
        from forge_harness.meta_learning.bridges.tech_diligence import CacheEntry

        entry = CacheEntry({"data": "value"}, ttl_seconds=0)
        assert entry.is_expired() is True

        entry_long = CacheEntry({"data": "value"}, ttl_seconds=3600)
        assert entry_long.is_expired() is False

    def test_get_cached_removes_expired(self, bridge):
        """Test that _get_cached removes expired entries."""
        from forge_harness.meta_learning.bridges.tech_diligence import CacheEntry

        expired_entry = CacheEntry({"data": "old"}, ttl_seconds=0)
        bridge._cache["expired-key"] = expired_entry

        result = bridge._get_cached("expired-key")

        assert result is None
        assert "expired-key" not in bridge._cache

    def test_set_cached_respects_ttl_zero(self, bridge):
        """Test that caching is disabled when TTL is 0."""
        bridge.cache_ttl_seconds = 0
        bridge._set_cached("test-key", {"data": "value"})

        assert "test-key" not in bridge._cache
