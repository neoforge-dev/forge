"""Tests for EnhancedFailurePatternDB with Code Atlas integration.

Covers the enhanced failure pattern DB that wraps the base FailurePatternDB
with Code Atlas RAG queries, caching, error signatures, and learning store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge_harness.failure_patterns import (
    EnhancedFailurePatternDB,
    FixSuggestion,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeAtlasResponse:
    """Mimics a Code Atlas RAG response."""
    answer: str
    confidence: float


def _make_atlas_bridge(answer: str = "Run uv sync", confidence: float = 0.9):
    """Create a mock atlas bridge with configurable query_rag response."""
    bridge = AsyncMock()
    bridge.query_rag.return_value = FakeAtlasResponse(answer=answer, confidence=confidence)
    return bridge


def _make_learning_store():
    """Create a mock learning store."""
    store = MagicMock()
    store.record_pattern = MagicMock()
    return store


# ---------------------------------------------------------------------------
# FixSuggestion dataclass
# ---------------------------------------------------------------------------

class TestFixSuggestion:
    def test_creation(self):
        s = FixSuggestion(fix="do X", source="local_pattern", confidence=0.85)
        assert s.fix == "do X"
        assert s.source == "local_pattern"
        assert s.confidence == 0.85
        assert s.error_signature is None
        assert s.atlas_response is None

    def test_with_optional_fields(self):
        resp = {"raw": True}
        s = FixSuggestion(
            fix="do Y",
            source="code_atlas",
            confidence=0.95,
            error_signature="abc123",
            atlas_response=resp,
        )
        assert s.error_signature == "abc123"
        assert s.atlas_response is resp


# ---------------------------------------------------------------------------
# EnhancedFailurePatternDB.__init__
# ---------------------------------------------------------------------------

class TestInit:
    def test_defaults(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        assert db.atlas_bridge is None
        assert db.learning_store is None
        assert db.confidence_threshold == 0.7
        assert db._atlas_cache == {}

    def test_custom_threshold(self, tmp_path):
        db = EnhancedFailurePatternDB(
            db_path=tmp_path / "p.json",
            confidence_threshold=0.5,
        )
        assert db.confidence_threshold == 0.5

    def test_with_bridges(self, tmp_path):
        atlas = _make_atlas_bridge()
        store = _make_learning_store()
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            learning_store=store,
            db_path=tmp_path / "p.json",
        )
        assert db.atlas_bridge is atlas
        assert db.learning_store is store


# ---------------------------------------------------------------------------
# _get_error_signature
# ---------------------------------------------------------------------------

class TestGetErrorSignature:
    def test_deterministic(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        sig1 = db._get_error_signature("ModuleNotFoundError: No module named 'foo'")
        sig2 = db._get_error_signature("ModuleNotFoundError: No module named 'foo'")
        assert sig1 == sig2

    def test_normalizes_numbers(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        sig1 = db._get_error_signature("Error at line 42 col 10")
        sig2 = db._get_error_signature("Error at line 99 col 5")
        assert sig1 == sig2  # Numbers replaced with N

    def test_normalizes_filenames(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        sig1 = db._get_error_signature("Error in 'main.py'")
        sig2 = db._get_error_signature("Error in 'utils.py'")
        assert sig1 == sig2  # File names replaced

    def test_case_insensitive(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        sig1 = db._get_error_signature("TypeError: invalid")
        sig2 = db._get_error_signature("typeerror: invalid")
        assert sig1 == sig2

    def test_returns_hex_string(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        sig = db._get_error_signature("some error")
        assert len(sig) == 16
        assert all(c in "0123456789abcdef" for c in sig)


# ---------------------------------------------------------------------------
# get_fix_suggestion — async
# ---------------------------------------------------------------------------

class TestGetFixSuggestion:
    @pytest.mark.asyncio
    async def test_atlas_high_confidence(self, tmp_path):
        """Atlas response above threshold is returned."""
        atlas = _make_atlas_bridge(answer="Run uv sync", confidence=0.9)
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
        )
        result = await db.get_fix_suggestion("ModuleNotFoundError: No module named 'foo'")
        assert result.source == "code_atlas"
        assert result.fix == "Run uv sync"
        assert result.confidence == 0.9
        assert result.error_signature is not None

    @pytest.mark.asyncio
    async def test_atlas_low_confidence_falls_back(self, tmp_path):
        """Low-confidence Atlas response falls through to local patterns."""
        atlas = _make_atlas_bridge(answer="Maybe try X", confidence=0.3)
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
        )
        result = await db.get_fix_suggestion("ModuleNotFoundError: No module named 'pytest'")
        # Should fall back to local pattern
        assert result.source == "local_pattern"
        assert result.confidence > 0

    @pytest.mark.asyncio
    async def test_no_atlas_uses_local(self, tmp_path):
        """Without Atlas bridge, local patterns are used."""
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        result = await db.get_fix_suggestion("SyntaxError: invalid syntax")
        assert result.source == "local_pattern"

    @pytest.mark.asyncio
    async def test_no_match_returns_manual(self, tmp_path):
        """Completely unknown error returns manual fix."""
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        result = await db.get_fix_suggestion("xyzzy totally unknown error 42")
        assert result.source == "none"
        assert result.confidence == 0.0
        assert "manually" in result.fix.lower()

    @pytest.mark.asyncio
    async def test_cache_hit(self, tmp_path):
        """Cached Atlas suggestion is returned on second call."""
        atlas = _make_atlas_bridge(answer="cached fix", confidence=0.95)
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
        )

        error = "UniqueError: something specific"
        sig = db._get_error_signature(error)

        # Prepopulate cache
        db._atlas_cache[sig] = FixSuggestion(
            fix="cached fix",
            source="code_atlas",
            confidence=0.95,
            error_signature=sig,
        )

        result = await db.get_fix_suggestion(error)
        assert result.source == "atlas_cache"
        assert result.fix == "cached fix"
        # Atlas bridge should NOT have been called
        atlas.query_rag.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_atlas_exception_falls_back(self, tmp_path):
        """Atlas bridge exception falls back to local patterns."""
        atlas = AsyncMock()
        atlas.query_rag.side_effect = RuntimeError("Connection refused")
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
        )
        result = await db.get_fix_suggestion("SyntaxError: invalid syntax")
        # Should gracefully fall back
        assert result.source == "local_pattern"

    @pytest.mark.asyncio
    async def test_custom_confidence_threshold(self, tmp_path):
        """Custom threshold affects Atlas acceptance."""
        atlas = _make_atlas_bridge(answer="medium confidence fix", confidence=0.6)
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
            confidence_threshold=0.5,  # Lower threshold
        )
        result = await db.get_fix_suggestion("ModuleNotFoundError: No module named 'foo'")
        assert result.source == "code_atlas"  # 0.6 >= 0.5

    @pytest.mark.asyncio
    async def test_atlas_exactly_at_threshold(self, tmp_path):
        """Atlas response exactly at threshold is accepted."""
        atlas = _make_atlas_bridge(answer="threshold fix", confidence=0.7)
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
            confidence_threshold=0.7,
        )
        result = await db.get_fix_suggestion("ModuleNotFoundError: No module named 'foo'")
        assert result.source == "code_atlas"


# ---------------------------------------------------------------------------
# _query_atlas
# ---------------------------------------------------------------------------

class TestQueryAtlas:
    @pytest.mark.asyncio
    async def test_query_formats_error(self, tmp_path):
        """Atlas query includes error text."""
        atlas = _make_atlas_bridge()
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
        )
        await db._query_atlas("TypeError: bad call", {"domain": "test"})
        call_args = atlas.query_rag.call_args
        assert "TypeError: bad call" in call_args.kwargs["query"]

    @pytest.mark.asyncio
    async def test_query_truncates_long_errors(self, tmp_path):
        """Long errors are truncated in Atlas query."""
        atlas = _make_atlas_bridge()
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
        )
        long_error = "E" * 1000
        await db._query_atlas(long_error, None)
        call_args = atlas.query_rag.call_args
        # Query should contain at most ~500 chars of the error
        assert len(call_args.kwargs["query"]) < 600

    @pytest.mark.asyncio
    async def test_query_returns_none_on_low_confidence(self, tmp_path):
        """Returns None when Atlas confidence is below threshold."""
        atlas = _make_atlas_bridge(confidence=0.3)
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
            confidence_threshold=0.7,
        )
        result = await db._query_atlas("some error", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_query_returns_suggestion_on_high_confidence(self, tmp_path):
        """Returns FixSuggestion when Atlas confidence is high."""
        atlas = _make_atlas_bridge(answer="atlas fix", confidence=0.9)
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
        )
        result = await db._query_atlas("some error", None)
        assert result is not None
        assert result.source == "code_atlas"
        assert result.fix == "atlas fix"

    @pytest.mark.asyncio
    async def test_query_passes_context(self, tmp_path):
        """Context dict is passed to Atlas bridge."""
        atlas = _make_atlas_bridge()
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
        )
        ctx = {"domain": "codeswiftr-com", "project": "is"}
        await db._query_atlas("error", ctx)
        assert atlas.query_rag.call_args.kwargs["context"] is ctx

    @pytest.mark.asyncio
    async def test_query_exception_returns_none(self, tmp_path):
        """Exception in Atlas query returns None."""
        atlas = AsyncMock()
        atlas.query_rag.side_effect = Exception("boom")
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
        )
        result = await db._query_atlas("error", None)
        assert result is None


# ---------------------------------------------------------------------------
# record_fix_outcome
# ---------------------------------------------------------------------------

class TestRecordFixOutcome:
    def test_records_to_learning_store(self, tmp_path):
        store = _make_learning_store()
        db = EnhancedFailurePatternDB(
            learning_store=store,
            db_path=tmp_path / "p.json",
        )
        db.record_fix_outcome("sig123", success=True)
        store.record_pattern.assert_called_once_with(
            context_signature="sig123",
            pattern_type="fix_suggestion",
            pattern_data={"success": True},
        )

    def test_records_failure(self, tmp_path):
        store = _make_learning_store()
        db = EnhancedFailurePatternDB(
            learning_store=store,
            db_path=tmp_path / "p.json",
        )
        db.record_fix_outcome("sig456", success=False)
        store.record_pattern.assert_called_once_with(
            context_signature="sig456",
            pattern_type="fix_suggestion",
            pattern_data={"success": False},
        )

    def test_noop_on_empty_signature(self, tmp_path):
        store = _make_learning_store()
        db = EnhancedFailurePatternDB(
            learning_store=store,
            db_path=tmp_path / "p.json",
        )
        db.record_fix_outcome("", success=True)
        store.record_pattern.assert_not_called()

    def test_noop_without_learning_store(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        # Should not raise
        db.record_fix_outcome("sig789", success=True)

    def test_learning_store_exception_swallowed(self, tmp_path):
        store = _make_learning_store()
        store.record_pattern.side_effect = RuntimeError("DB down")
        db = EnhancedFailurePatternDB(
            learning_store=store,
            db_path=tmp_path / "p.json",
        )
        # Should not raise
        db.record_fix_outcome("sig_err", success=True)


# ---------------------------------------------------------------------------
# get_fix_suggestions (async, multiple errors)
# ---------------------------------------------------------------------------

class TestGetFixSuggestions:
    @pytest.mark.asyncio
    async def test_multiple_errors(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        errors = [
            "SyntaxError: invalid syntax",
            "ModuleNotFoundError: No module named 'foo'",
            "xyzzy unknown error",
        ]
        results = await db.get_fix_suggestions(errors)
        assert len(results) == 3
        assert results[0].source == "local_pattern"
        assert results[2].source == "none"

    @pytest.mark.asyncio
    async def test_empty_list(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        results = await db.get_fix_suggestions([])
        assert results == []

    @pytest.mark.asyncio
    async def test_with_context(self, tmp_path):
        atlas = _make_atlas_bridge(answer="atlas fix", confidence=0.9)
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
        )
        ctx = {"domain": "test"}
        results = await db.get_fix_suggestions(["some error"], context=ctx)
        assert len(results) == 1
        assert atlas.query_rag.call_args.kwargs["context"] is ctx


# ---------------------------------------------------------------------------
# get_fix_suggestions_sync
# ---------------------------------------------------------------------------

class TestGetFixSuggestionsSync:
    def test_returns_local_only(self, tmp_path):
        """Sync version only uses local patterns, no Atlas."""
        atlas = _make_atlas_bridge()
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
        )
        results = db.get_fix_suggestions_sync(["SyntaxError: invalid syntax"])
        assert len(results) == 1
        assert results[0].source == "local"
        # Atlas should not have been called
        atlas.query_rag.assert_not_awaited()

    def test_multiple_errors(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        errors = [
            "SyntaxError: invalid syntax",
            "xyzzy unknown",
        ]
        results = db.get_fix_suggestions_sync(errors)
        assert len(results) == 2

    def test_empty_list(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        results = db.get_fix_suggestions_sync([])
        assert results == []

    def test_includes_error_signature(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        results = db.get_fix_suggestions_sync(["TypeError: bad"])
        assert results[0].error_signature is not None
        assert len(results[0].error_signature) == 16


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStats:
    def test_includes_atlas_info(self, tmp_path):
        atlas = _make_atlas_bridge()
        db = EnhancedFailurePatternDB(
            atlas_bridge=atlas,
            db_path=tmp_path / "p.json",
        )
        stats = db.get_stats()
        assert stats["atlas_available"] is True
        assert stats["atlas_cache_size"] == 0
        assert stats["confidence_threshold"] == 0.7

    def test_without_atlas(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        stats = db.get_stats()
        assert stats["atlas_available"] is False
        assert "builtin_count" in stats

    def test_cache_size_tracked(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        db._atlas_cache["sig1"] = FixSuggestion(fix="x", source="atlas", confidence=0.9)
        db._atlas_cache["sig2"] = FixSuggestion(fix="y", source="atlas", confidence=0.8)
        stats = db.get_stats()
        assert stats["atlas_cache_size"] == 2

    def test_inherits_local_stats(self, tmp_path):
        db = EnhancedFailurePatternDB(db_path=tmp_path / "p.json")
        stats = db.get_stats()
        # Should include local DB stats
        assert "builtin_count" in stats
        assert "learned_count" in stats
