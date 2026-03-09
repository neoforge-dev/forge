"""
Failure Pattern Learning Module
===============================

Records and matches error patterns to provide better fix suggestions.
Learns from repeated failures to improve autonomous retry success rates.

Based on lessons learned from KiddoRewards session (2025-12-30).
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class FailurePattern:
    """A known failure pattern with its fix."""

    pattern: str  # Regex pattern to match
    fix: str  # Fix command or instruction
    category: str  # Error category: import, test, lint, etc.
    occurrences: int = 0
    last_seen: str | None = None

    def matches(self, error: str) -> bool:
        """Check if this pattern matches an error."""
        try:
            return bool(re.search(self.pattern, error, re.IGNORECASE))
        except re.error:
            return self.pattern.lower() in error.lower()


@dataclass
class PatternMatch:
    """Result of matching an error against patterns."""

    error: str
    pattern: FailurePattern | None
    fix: str | None
    confidence: float  # 0.0 to 1.0


class FailurePatternDB:
    """
    Database of failure patterns for improved retry success.

    Records errors and their fixes, then matches future errors
    to suggest appropriate fixes.
    """

    # Built-in patterns from KiddoRewards and other sessions
    BUILTIN_PATTERNS = [
        # Import errors
        FailurePattern(
            pattern=r"ModuleNotFoundError: No module named 'app'",
            fix="Create pyproject.toml with [tool.hatch.build.targets.wheel] packages=['app']",
            category="import",
        ),
        FailurePattern(
            pattern=r"ModuleNotFoundError: No module named '(\w+)'",
            fix="Run 'uv add {module}' to install the missing dependency",
            category="import",
        ),
        FailurePattern(
            pattern=r"ImportError: cannot import name '(\w+)'",
            fix="Check __init__.py files exist and export the required symbol",
            category="import",
        ),
        FailurePattern(
            pattern=r"No module named 'pytest'",
            fix="Run 'uv add --dev pytest pytest-asyncio pytest-cov'",
            category="import",
        ),
        # Test errors
        FailurePattern(
            pattern=r"FAILED tests/.*::test_.*",
            fix="Review failing test and fix the implementation",
            category="test",
        ),
        FailurePattern(
            pattern=r"fixture '(\w+)' not found",
            fix="Add missing fixture to conftest.py or import from pytest plugins",
            category="test",
        ),
        FailurePattern(
            pattern=r"async.*not awaited|coroutine.*never awaited",
            fix="Add 'await' before async function calls",
            category="test",
        ),
        # Lint errors
        FailurePattern(
            pattern=r"comparison to (True|False) should be",
            fix="Run 'uv run ruff check . --fix' to auto-fix style issues",
            category="lint",
        ),
        FailurePattern(
            pattern=r"unused import",
            fix="Run 'uv run ruff check . --fix' to remove unused imports",
            category="lint",
        ),
        FailurePattern(
            pattern=r"f-string without placeholders",
            fix="Run 'uv run ruff check . --fix' to convert to regular strings",
            category="lint",
        ),
        FailurePattern(
            pattern=r"line too long \((\d+) > 100",
            fix="Split long lines or disable line length check for specific lines",
            category="lint",
        ),
        # Syntax errors
        FailurePattern(
            pattern=r"SyntaxError: invalid syntax",
            fix="Check for missing colons, parentheses, or quotes in the affected file",
            category="syntax",
        ),
        FailurePattern(
            pattern=r"IndentationError",
            fix="Fix indentation - ensure consistent use of spaces (not tabs)",
            category="syntax",
        ),
        # Database errors
        FailurePattern(
            pattern=r"relation \"(\w+)\" does not exist",
            fix="Run database migrations: 'uv run alembic upgrade head'",
            category="database",
        ),
        FailurePattern(
            pattern=r"asyncpg.*Connection refused",
            fix="Ensure PostgreSQL is running and DATABASE_URL is correct",
            category="database",
        ),
        # Type errors
        FailurePattern(
            pattern=r"TypeError: '(\w+)' object is not (callable|iterable|subscriptable)",
            fix="Check variable types and ensure correct usage",
            category="type",
        ),
        FailurePattern(
            pattern=r"AttributeError: '(\w+)' object has no attribute '(\w+)'",
            fix="Check object type and available attributes",
            category="type",
        ),
        # Frontend errors
        FailurePattern(
            pattern=r"Cannot find module '(.+)'",
            fix="Run 'npm install' or add the missing package",
            category="frontend",
        ),
        FailurePattern(
            pattern=r"eslint.*error.*@typescript-eslint",
            fix="Fix TypeScript errors or update eslint config",
            category="frontend",
        ),
    ]

    def __init__(self, db_path: Path | None = None):
        """
        Initialize failure pattern database.

        Args:
            db_path: Path to persist learned patterns (optional)
        """
        self.db_path = db_path
        self.patterns: list[FailurePattern] = list(self.BUILTIN_PATTERNS)
        self.learned_patterns: list[FailurePattern] = []

        if db_path and db_path.exists():
            self._load()

    def _load(self) -> None:
        """Load learned patterns from disk."""
        if not self.db_path or not self.db_path.exists():
            return

        try:
            data = json.loads(self.db_path.read_text())
            for p in data.get("patterns", []):
                pattern = FailurePattern(
                    pattern=p["pattern"],
                    fix=p["fix"],
                    category=p.get("category", "unknown"),
                    occurrences=p.get("occurrences", 0),
                    last_seen=p.get("last_seen"),
                )
                self.learned_patterns.append(pattern)
        except (json.JSONDecodeError, KeyError):
            pass

    def _save(self) -> None:
        """Save learned patterns to disk."""
        if not self.db_path:
            return

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "patterns": [
                {
                    "pattern": p.pattern,
                    "fix": p.fix,
                    "category": p.category,
                    "occurrences": p.occurrences,
                    "last_seen": p.last_seen,
                }
                for p in self.learned_patterns
            ],
            "updated": datetime.now().isoformat(),
        }
        self.db_path.write_text(json.dumps(data, indent=2))

    def match(self, error: str) -> PatternMatch:
        """
        Find matching pattern for an error.

        Args:
            error: Error message to match

        Returns:
            PatternMatch with pattern and fix if found
        """
        # Check learned patterns first (higher priority)
        for pattern in self.learned_patterns:
            if pattern.matches(error):
                return PatternMatch(
                    error=error,
                    pattern=pattern,
                    fix=self._interpolate_fix(pattern.fix, error, pattern.pattern),
                    confidence=0.9,
                )

        # Check builtin patterns
        for pattern in self.patterns:
            if pattern.matches(error):
                return PatternMatch(
                    error=error,
                    pattern=pattern,
                    fix=self._interpolate_fix(pattern.fix, error, pattern.pattern),
                    confidence=0.8,
                )

        return PatternMatch(error=error, pattern=None, fix=None, confidence=0.0)

    def _interpolate_fix(self, fix: str, error: str, pattern_str: str) -> str:
        """Interpolate captured groups into fix string."""
        try:
            match = re.search(pattern_str, error, re.IGNORECASE)
            if match:
                groups = match.groups()
                if groups and "{module}" in fix:
                    fix = fix.replace("{module}", groups[0])
        except re.error:
            pass
        return fix

    def match_all(self, errors: list[str]) -> list[PatternMatch]:
        """
        Match multiple errors against patterns.

        Args:
            errors: List of error messages

        Returns:
            List of PatternMatch results
        """
        return [self.match(error) for error in errors]

    def get_fixes(self, errors: list[str]) -> list[str]:
        """
        Get unique fix suggestions for errors.

        Args:
            errors: List of error messages

        Returns:
            Deduplicated list of fix suggestions
        """
        fixes = []
        seen = set()

        for error in errors:
            match = self.match(error)
            if match.fix and match.fix not in seen:
                fixes.append(match.fix)
                seen.add(match.fix)

        return fixes

    def record(self, error: str, fix: str, category: str = "unknown") -> None:
        """
        Record a new error pattern.

        Args:
            error: Error message (will be generalized to pattern)
            fix: Fix that resolved the error
            category: Error category
        """
        # Generalize error to a pattern
        pattern_str = self._generalize_error(error)

        # Check if pattern already exists
        for p in self.learned_patterns:
            if p.pattern == pattern_str:
                p.occurrences += 1
                p.last_seen = datetime.now().isoformat()
                self._save()
                return

        # Add new pattern
        new_pattern = FailurePattern(
            pattern=pattern_str,
            fix=fix,
            category=category,
            occurrences=1,
            last_seen=datetime.now().isoformat(),
        )
        self.learned_patterns.append(new_pattern)
        self._save()

    def _generalize_error(self, error: str) -> str:
        """
        Generalize an error message to a reusable pattern.

        Replaces specific identifiers with regex groups.
        """
        pattern = error

        # Replace specific file paths with wildcards
        pattern = re.sub(r"/[^\s]+/([^/\s]+\.py)", r"[^/]+/\1", pattern)

        # Replace line numbers
        pattern = re.sub(r"line \d+", r"line \\d+", pattern)

        # Replace specific variable/function names (but keep error type)
        pattern = re.sub(r"'([^']+)'", r"'([^']+)'", pattern)

        # Escape special regex characters
        pattern = re.sub(r"([.+*?^${}()|[\]\\])", r"\\\1", pattern)

        return pattern

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about pattern usage."""
        return {
            "builtin_count": len(self.patterns),
            "learned_count": len(self.learned_patterns),
            "total_matches": sum(p.occurrences for p in self.learned_patterns),
            "categories": list(set(p.category for p in self.patterns + self.learned_patterns)),
        }


# Convenience function for one-off matching
def get_fix_suggestions(errors: list[str]) -> list[str]:
    """
    Get fix suggestions for a list of errors.

    Uses a temporary in-memory pattern database.

    Args:
        errors: List of error messages

    Returns:
        List of fix suggestions
    """
    db = FailurePatternDB()
    return db.get_fixes(errors)


# =============================================================================
# Enhanced Failure Pattern DB with Code Atlas Integration
# =============================================================================


@dataclass
class FixSuggestion:
    """A fix suggestion with source tracking."""

    fix: str
    source: str  # 'code_atlas', 'local_pattern', 'learned'
    confidence: float
    error_signature: str | None = None
    atlas_response: Any | None = None


class EnhancedFailurePatternDB:
    """
    Failure pattern DB enhanced with Code Atlas integration.

    Queries Code Atlas for historical fixes before falling back to local patterns.
    Records outcomes to improve future suggestions.

    Usage:
        from forge_harness.meta_learning.bridges.code_atlas import CodeAtlasBridge

        atlas = CodeAtlasBridge(...)
        db = EnhancedFailurePatternDB(atlas_bridge=atlas)

        # Get fix suggestion
        suggestion = await db.get_fix_suggestion(error, context)

        # Later, record outcome
        db.record_fix_outcome(suggestion.error_signature, success=True)
    """

    def __init__(
        self,
        atlas_bridge: Any | None = None,
        learning_store: Any | None = None,
        db_path: Path | None = None,
        confidence_threshold: float = 0.7,
    ):
        """Initialize enhanced failure pattern DB.

        Args:
            atlas_bridge: CodeAtlasBridge for historical fix lookup
            learning_store: LearningStore for pattern persistence
            db_path: Path to persist local patterns
            confidence_threshold: Min confidence to use Atlas suggestions
        """
        self.atlas_bridge = atlas_bridge
        self.learning_store = learning_store
        self.confidence_threshold = confidence_threshold
        self._local_db = FailurePatternDB(db_path)

        # Cache successful Atlas suggestions for fast lookup
        self._atlas_cache: dict[str, FixSuggestion] = {}

    async def get_fix_suggestion(
        self,
        error: str,
        context: dict[str, Any] | None = None,
    ) -> FixSuggestion:
        """Get fix suggestion, querying Atlas first.

        Args:
            error: Error message
            context: Optional context (domain, project, file_paths)

        Returns:
            FixSuggestion with source tracking
        """
        error_sig = self._get_error_signature(error)

        # Check local cache of successful Atlas suggestions
        if error_sig in self._atlas_cache:
            cached = self._atlas_cache[error_sig]
            return FixSuggestion(
                fix=cached.fix,
                source="atlas_cache",
                confidence=cached.confidence,
                error_signature=error_sig,
            )

        # Query Code Atlas if available
        if self.atlas_bridge:
            atlas_suggestion = await self._query_atlas(error, context)
            if atlas_suggestion and atlas_suggestion.confidence >= self.confidence_threshold:
                return atlas_suggestion

        # Fall back to local patterns
        local_match = self._local_db.match(error)
        if local_match.fix:
            return FixSuggestion(
                fix=local_match.fix,
                source="local_pattern",
                confidence=local_match.confidence,
                error_signature=error_sig,
            )

        # No match found
        return FixSuggestion(
            fix="Review the error and fix manually",
            source="none",
            confidence=0.0,
            error_signature=error_sig,
        )

    async def _query_atlas(
        self,
        error: str,
        context: dict[str, Any] | None,
    ) -> FixSuggestion | None:
        """Query Code Atlas RAG for historical fix."""
        try:
            # Format query for RAG
            query = f"How was this error fixed before: {error[:500]}"

            response = await self.atlas_bridge.query_rag(
                query=query,
                context=context,
            )

            if response.confidence >= self.confidence_threshold:
                error_sig = self._get_error_signature(error)
                return FixSuggestion(
                    fix=response.answer,
                    source="code_atlas",
                    confidence=response.confidence,
                    error_signature=error_sig,
                    atlas_response=response,
                )

        except Exception:
            pass

        return None

    def _get_error_signature(self, error: str) -> str:
        """Generate a signature for an error message."""
        import hashlib

        # Normalize error
        normalized = re.sub(r"\d+", "N", error)  # Replace numbers
        normalized = re.sub(r"'[^']+\.py'", "'FILE.py'", normalized)  # Replace file names
        normalized = normalized.lower().strip()

        return hashlib.md5(normalized.encode()).hexdigest()[:16]

    def record_fix_outcome(self, error_signature: str, success: bool) -> None:
        """Record whether a fix suggestion worked.

        Args:
            error_signature: Signature from FixSuggestion
            success: Whether the fix resolved the error
        """
        if not error_signature:
            return

        # If successful Atlas suggestion, cache it
        if success and error_signature in self._atlas_cache:
            pass  # Already cached

        # Record to learning store if available
        if self.learning_store and error_signature:
            try:
                self.learning_store.record_pattern(
                    context_signature=error_signature,
                    pattern_type="fix_suggestion",
                    pattern_data={"success": success},
                )
            except Exception:
                pass

    async def get_fix_suggestions(
        self,
        errors: list[str],
        context: dict[str, Any] | None = None,
    ) -> list[FixSuggestion]:
        """Get fix suggestions for multiple errors.

        Args:
            errors: List of error messages
            context: Optional context

        Returns:
            List of FixSuggestion objects
        """
        suggestions = []
        for error in errors:
            suggestion = await self.get_fix_suggestion(error, context)
            suggestions.append(suggestion)
        return suggestions

    def get_fix_suggestions_sync(
        self,
        errors: list[str],
        context: dict[str, Any] | None = None,
    ) -> list[FixSuggestion]:
        """Synchronous version of get_fix_suggestions for Verifier compatibility.

        Falls back to local pattern matching only (no async Code Atlas queries).

        Args:
            errors: List of error messages
            context: Optional context (unused in sync mode)

        Returns:
            List of FixSuggestion objects
        """
        suggestions = []
        for error in errors:
            # Use local DB only (no async Atlas queries)
            match = self._local_db.match(error)
            suggestion = FixSuggestion(
                fix=match.fix,
                source="local",
                confidence=match.confidence,
                error_signature=self._get_error_signature(error),
            )
            suggestions.append(suggestion)
        return suggestions

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about pattern usage."""
        local_stats = self._local_db.get_stats()
        return {
            **local_stats,
            "atlas_cache_size": len(self._atlas_cache),
            "atlas_available": self.atlas_bridge is not None,
            "confidence_threshold": self.confidence_threshold,
        }
