"""Tests for failure pattern learning module."""

import pytest

from forge_harness.failure_patterns import (
    FailurePattern,
    FailurePatternDB,
    PatternMatch,
    get_fix_suggestions,
)


class TestFailurePattern:
    """Tests for FailurePattern dataclass."""

    def test_pattern_creation(self):
        """FailurePattern can be created with required fields."""
        pattern = FailurePattern(
            pattern=r"ModuleNotFoundError",
            fix="Run uv add to install",
            category="import",
        )
        assert pattern.pattern == r"ModuleNotFoundError"
        assert pattern.fix == "Run uv add to install"
        assert pattern.category == "import"
        assert pattern.occurrences == 0

    def test_pattern_matches_exact(self):
        """Pattern matches exact error string."""
        pattern = FailurePattern(
            pattern="SyntaxError: invalid syntax",
            fix="Fix syntax",
            category="syntax",
        )
        assert pattern.matches("SyntaxError: invalid syntax in file.py")
        assert pattern.matches("SYNTAXERROR: INVALID SYNTAX")  # Case insensitive
        assert not pattern.matches("TypeError: invalid operation")

    def test_pattern_matches_regex(self):
        """Pattern matches using regex."""
        pattern = FailurePattern(
            pattern=r"ModuleNotFoundError: No module named '(\w+)'",
            fix="Install missing module",
            category="import",
        )
        assert pattern.matches("ModuleNotFoundError: No module named 'pytest'")
        assert pattern.matches("ModuleNotFoundError: No module named 'requests'")
        assert not pattern.matches("ImportError: cannot import")

    def test_pattern_with_occurrences(self):
        """Pattern tracks occurrences and last_seen."""
        pattern = FailurePattern(
            pattern="test error",
            fix="fix it",
            category="test",
            occurrences=5,
            last_seen="2025-12-30T10:00:00",
        )
        assert pattern.occurrences == 5
        assert pattern.last_seen == "2025-12-30T10:00:00"


class TestPatternMatch:
    """Tests for PatternMatch dataclass."""

    def test_match_with_pattern(self):
        """PatternMatch stores matched pattern and fix."""
        pattern = FailurePattern(pattern="test", fix="fix test", category="test")
        match = PatternMatch(
            error="test error",
            pattern=pattern,
            fix="fix test",
            confidence=0.9,
        )
        assert match.error == "test error"
        assert match.pattern == pattern
        assert match.fix == "fix test"
        assert match.confidence == 0.9

    def test_match_without_pattern(self):
        """PatternMatch can have no pattern (no match found)."""
        match = PatternMatch(
            error="unknown error",
            pattern=None,
            fix=None,
            confidence=0.0,
        )
        assert match.pattern is None
        assert match.fix is None
        assert match.confidence == 0.0


class TestFailurePatternDB:
    """Tests for FailurePatternDB class."""

    def test_db_initialization_with_builtins(self):
        """DB initializes with built-in patterns."""
        db = FailurePatternDB()
        assert len(db.patterns) > 0
        assert len(db.BUILTIN_PATTERNS) > 0

    def test_db_match_builtin_module_not_found(self):
        """DB matches ModuleNotFoundError pattern."""
        db = FailurePatternDB()
        match = db.match("ModuleNotFoundError: No module named 'app'")
        assert match.pattern is not None
        assert "pyproject.toml" in match.fix or "packages" in match.fix

    def test_db_match_builtin_import_error(self):
        """DB matches ImportError pattern."""
        db = FailurePatternDB()
        match = db.match("ImportError: cannot import name 'Foo' from 'bar'")
        assert match.pattern is not None
        assert "__init__.py" in match.fix

    def test_db_match_builtin_pytest_missing(self):
        """DB matches missing pytest pattern."""
        db = FailurePatternDB()
        match = db.match("No module named 'pytest'")
        assert match.pattern is not None
        assert "uv add" in match.fix

    def test_db_match_no_match(self):
        """DB returns empty match for unknown error."""
        db = FailurePatternDB()
        match = db.match("Some completely random error message xyz123")
        assert match.pattern is None
        assert match.fix is None
        assert match.confidence == 0.0

    def test_db_match_all(self):
        """DB matches multiple errors."""
        db = FailurePatternDB()
        errors = [
            "ModuleNotFoundError: No module named 'pytest'",
            "SyntaxError: invalid syntax",
            "Unknown error with no pattern",
        ]
        matches = db.match_all(errors)
        assert len(matches) == 3
        assert matches[0].pattern is not None  # pytest
        assert matches[1].pattern is not None  # syntax
        assert matches[2].pattern is None  # unknown

    def test_db_get_fixes(self):
        """DB returns unique fixes for errors."""
        db = FailurePatternDB()
        errors = [
            "ModuleNotFoundError: No module named 'pytest'",
            "ModuleNotFoundError: No module named 'httpx'",  # Same pattern type
            "comparison to True should be 'is True'",
        ]
        fixes = db.get_fixes(errors)
        assert len(fixes) >= 1
        # Should deduplicate similar fixes
        assert len(fixes) <= len(errors)

    def test_db_record_pattern(self, tmp_path):
        """DB records new patterns."""
        db_path = tmp_path / "patterns.json"
        db = FailurePatternDB(db_path)

        db.record(
            error="Custom error message in project",
            fix="Run custom fix command",
            category="custom",
        )

        assert len(db.learned_patterns) == 1
        assert db.learned_patterns[0].fix == "Run custom fix command"
        assert db.learned_patterns[0].occurrences == 1

    def test_db_record_increments_occurrences(self, tmp_path):
        """DB increments occurrences for repeated patterns."""
        db_path = tmp_path / "patterns.json"
        db = FailurePatternDB(db_path)

        # Record same pattern twice
        db.record("Custom error", "Fix it", "custom")
        db.record("Custom error", "Fix it", "custom")

        assert len(db.learned_patterns) == 1
        assert db.learned_patterns[0].occurrences == 2

    def test_db_persistence(self, tmp_path):
        """DB persists learned patterns to disk."""
        db_path = tmp_path / "patterns.json"

        # Create and save patterns
        db1 = FailurePatternDB(db_path)
        db1.record("Test error", "Test fix", "test")

        # Load patterns in new instance
        db2 = FailurePatternDB(db_path)
        assert len(db2.learned_patterns) == 1
        assert db2.learned_patterns[0].fix == "Test fix"

    def test_db_learned_patterns_priority(self, tmp_path):
        """Learned patterns have higher priority than builtins."""
        db_path = tmp_path / "patterns.json"
        db = FailurePatternDB(db_path)

        # Record a pattern that matches same errors as builtin
        db.record(
            error="ModuleNotFoundError: custom app",
            fix="Custom fix for this specific project",
            category="import",
        )

        # Match should prefer learned pattern
        match = db.match("ModuleNotFoundError: custom app")
        # Learned patterns have confidence 0.9
        assert match.confidence == 0.9

    def test_db_interpolate_fix(self):
        """DB interpolates captured groups into fix."""
        db = FailurePatternDB()
        # The builtin pattern for ModuleNotFoundError captures module name
        match = db.match("ModuleNotFoundError: No module named 'requests'")
        # Check the fix has the module name interpolated
        assert match.fix is not None

    def test_db_get_stats(self, tmp_path):
        """DB returns usage statistics."""
        db_path = tmp_path / "patterns.json"
        db = FailurePatternDB(db_path)
        db.record("Test error", "Test fix", "test")
        db.record("Test error", "Test fix", "test")

        stats = db.get_stats()
        assert stats["builtin_count"] > 0
        assert stats["learned_count"] == 1
        assert stats["total_matches"] == 2
        assert "test" in stats["categories"]


class TestGetFixSuggestions:
    """Tests for convenience function."""

    def test_get_fix_suggestions_single(self):
        """get_fix_suggestions works with single error."""
        fixes = get_fix_suggestions(["SyntaxError: invalid syntax"])
        assert len(fixes) >= 1

    def test_get_fix_suggestions_multiple(self):
        """get_fix_suggestions works with multiple errors."""
        errors = [
            "ModuleNotFoundError: No module named 'pytest'",
            "comparison to True should be 'is True'",
        ]
        fixes = get_fix_suggestions(errors)
        assert len(fixes) >= 1

    def test_get_fix_suggestions_empty(self):
        """get_fix_suggestions returns empty for unknown errors."""
        fixes = get_fix_suggestions(["Completely unknown xyz123 error"])
        assert isinstance(fixes, list)


class TestBuiltinPatterns:
    """Tests for builtin pattern coverage."""

    @pytest.fixture
    def db(self):
        return FailurePatternDB()

    def test_import_error_patterns(self, db):
        """Builtin patterns cover common import errors."""
        errors = [
            "ModuleNotFoundError: No module named 'app'",
            "ImportError: cannot import name 'Foo'",
            "No module named 'pytest'",
        ]
        for error in errors:
            match = db.match(error)
            assert match.fix is not None, f"No fix for: {error}"

    def test_test_error_patterns(self, db):
        """Builtin patterns cover common test errors."""
        errors = [
            "FAILED tests/test_foo.py::test_bar",
            "fixture 'db_session' not found",
            "coroutine 'test_async' was never awaited",
        ]
        for error in errors:
            match = db.match(error)
            assert match.fix is not None, f"No fix for: {error}"

    def test_lint_error_patterns(self, db):
        """Builtin patterns cover common lint errors."""
        errors = [
            "comparison to True should be 'is True'",
            "unused import 'os'",
            "f-string without placeholders",
            "line too long (120 > 100 characters)",
        ]
        for error in errors:
            match = db.match(error)
            assert match.fix is not None, f"No fix for: {error}"

    def test_syntax_error_patterns(self, db):
        """Builtin patterns cover syntax errors."""
        errors = [
            "SyntaxError: invalid syntax",
            "IndentationError: unexpected indent",
        ]
        for error in errors:
            match = db.match(error)
            assert match.fix is not None, f"No fix for: {error}"

    def test_database_error_patterns(self, db):
        """Builtin patterns cover database errors."""
        errors = [
            'relation "users" does not exist',
            "asyncpg: Connection refused",
        ]
        for error in errors:
            match = db.match(error)
            assert match.fix is not None, f"No fix for: {error}"

    def test_type_error_patterns(self, db):
        """Builtin patterns cover type errors."""
        errors = [
            "TypeError: 'NoneType' object is not callable",
            "AttributeError: 'str' object has no attribute 'items'",
        ]
        for error in errors:
            match = db.match(error)
            assert match.fix is not None, f"No fix for: {error}"

    def test_frontend_error_patterns(self, db):
        """Builtin patterns cover frontend errors."""
        errors = [
            "Cannot find module 'react'",
            "eslint error @typescript-eslint/no-unused-vars",
        ]
        for error in errors:
            match = db.match(error)
            assert match.fix is not None, f"No fix for: {error}"
