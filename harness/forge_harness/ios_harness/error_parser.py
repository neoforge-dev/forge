"""
Xcode Build Error Parser
=========================

Parses xcodebuild output into structured ``CompilationError`` objects that can be
consumed by the fix generator, learning system, and CLI tooling.

Features:
- Regex-based parsing of xcodebuild error/warning/note lines
- Error categorization (missing_member, type_mismatch, argument_order, etc.)
- Context code extraction from surrounding lines
- Grouping by file path for batch processing
- Filtering by error type or severity

Usage::

    from forge_harness.ios_harness.error_parser import XcodeErrorParser, ErrorType

    parser = XcodeErrorParser()
    errors = parser.parse_build_log(xcodebuild_output)

    by_file = parser.group_by_file(errors)
    missing = parser.filter_by_type(errors, ErrorType.MISSING_MEMBER)
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ErrorType(Enum):
    """Categories of Swift compilation errors."""

    MISSING_MEMBER = "missing_member"
    TYPE_MISMATCH = "type_mismatch"
    ARGUMENT_ORDER = "argument_order"
    MISSING_METHOD = "missing_method"
    WRONG_PROPERTY_NAME = "wrong_property_name"
    MISSING_IMPORT = "missing_import"
    SYNTAX_ERROR = "syntax_error"
    OPTIONAL_UNWRAP = "optional_unwrap"
    PUBLISH_BINDING = "publish_binding"
    MACOS_API_USAGE = "macos_api_usage"
    CANNOT_FIND_IN_SCOPE = "cannot_find_in_scope"
    MISSING_ARGUMENT = "missing_argument"
    EXTRA_ARGUMENT = "extra_argument"
    AMBIGUOUS_REFERENCE = "ambiguous_reference"
    PROTOCOL_CONFORMANCE = "protocol_conformance"
    ACCESS_CONTROL = "access_control"
    UNAVAILABLE_API = "unavailable_api"
    UNKNOWN = "unknown"


class ErrorSeverity(Enum):
    """Severity level of a compilation diagnostic."""

    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CompilationError:
    """Structured representation of a single Xcode compilation diagnostic."""

    file_path: str
    line: int
    column: int
    error_type: ErrorType
    message: str
    severity: ErrorSeverity

    # Type-specific fields populated based on error_type
    type_name: str | None = None
    member_name: str | None = None
    expected_type: str | None = None
    actual_type: str | None = None
    context_code: str | None = None
    suggestion: str | None = None

    # Raw data
    raw_error: str = ""

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}:{self.column}: {self.severity.value}: {self.message}"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Compiled at module level for performance — used on every parse call.
_ERROR_LINE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<column>\d+):\s*"
    r"(?P<severity>error|warning|note):\s*(?P<message>.+)$",
    re.MULTILINE,
)

# --- Error categorisation patterns ---

_MISSING_MEMBER_RE = re.compile(
    r"value of type '(?P<type>[^']+)' has no member '(?P<member>[^']+)'"
)

_TYPE_MISMATCH_RE = re.compile(
    r"cannot convert (?:value|type) of type '(?P<actual>[^']+)' "
    r"to (?:expected |specified |)type '(?P<expected>[^']+)'"
)

_ARGUMENT_ORDER_RE = re.compile(
    r"argument '(?P<arg>[^']+)' must precede argument '(?P<other>[^']+)'"
)

_CANNOT_FIND_IN_SCOPE_RE = re.compile(
    r"cannot find '(?P<name>[^']+)' in scope"
)

_MISSING_ARGUMENT_RE = re.compile(
    r"missing argument for parameter '(?P<param>[^']+)'"
)

_EXTRA_ARGUMENT_RE = re.compile(
    r"extra argument '(?P<param>[^']+)' in call"
)

_MISSING_IMPORT_RE = re.compile(
    r"no such module '(?P<module>[^']+)'"
)

_PROTOCOL_CONFORMANCE_RE = re.compile(
    r"type '(?P<type>[^']+)' does not conform to protocol '(?P<protocol>[^']+)'"
)

_OPTIONAL_UNWRAP_RE = re.compile(
    r"value of optional type '(?P<type>[^']+)' must be unwrapped"
)

_PUBLISH_BINDING_RE = re.compile(
    r"cannot convert value of type 'Published<(?P<inner>[^>]+)>\.Publisher' "
    r"to expected type 'Binding<(?P<binding>[^>]+)>'"
)

_ACCESS_CONTROL_RE = re.compile(
    r"'(?P<name>[^']+)' is inaccessible due to '(?P<level>private|fileprivate|internal)' protection level"
)

_UNAVAILABLE_API_RE = re.compile(
    r"'(?P<name>[^']+)' (?:is only available|was deprecated|has been renamed) "
    r"(?:in|to) (?P<context>[^;]+)"
)

_AMBIGUOUS_REFERENCE_RE = re.compile(
    r"ambiguous (?:use|reference) of '(?P<name>[^']+)'"
)

_MACOS_API_RE = re.compile(
    r"'(?P<name>[^']+)' is unavailable in (?:iOS|watchOS|tvOS|visionOS)"
)

# Suggestion pattern: "did you mean 'X'?"
_DID_YOU_MEAN_RE = re.compile(r"did you mean '(?P<suggestion>[^']+)'\?")


class XcodeErrorParser:
    """Parse xcodebuild output into structured ``CompilationError`` objects."""

    def parse_build_log(self, log_text: str) -> list[CompilationError]:
        """Parse complete xcodebuild output.

        Args:
            log_text: Raw text output from ``xcodebuild``.

        Returns:
            Ordered list of ``CompilationError`` objects.
        """
        lines = log_text.split("\n")
        errors: list[CompilationError] = []

        for i, line in enumerate(lines):
            match = _ERROR_LINE_RE.match(line)
            if match:
                error = self._parse_error_line(match, lines, i)
                if error:
                    errors.append(error)

        return errors

    # ---- grouping / filtering helpers ----

    @staticmethod
    def group_by_file(
        errors: list[CompilationError],
    ) -> dict[str, list[CompilationError]]:
        """Group errors by file path for batch fixing."""
        result: dict[str, list[CompilationError]] = defaultdict(list)
        for error in errors:
            result[error.file_path].append(error)
        return dict(result)

    @staticmethod
    def filter_by_type(
        errors: list[CompilationError],
        error_type: ErrorType,
    ) -> list[CompilationError]:
        """Return only errors matching the given ``ErrorType``."""
        return [e for e in errors if e.error_type == error_type]

    @staticmethod
    def filter_by_severity(
        errors: list[CompilationError],
        severity: ErrorSeverity,
    ) -> list[CompilationError]:
        """Return only diagnostics matching the given severity."""
        return [e for e in errors if e.severity == severity]

    @staticmethod
    def summary(errors: list[CompilationError]) -> dict:
        """Return a compact summary dict of parse results."""
        by_type: dict[str, int] = defaultdict(int)
        by_severity: dict[str, int] = defaultdict(int)
        by_file: dict[str, int] = defaultdict(int)
        for e in errors:
            by_type[e.error_type.value] += 1
            by_severity[e.severity.value] += 1
            by_file[e.file_path] += 1
        return {
            "total": len(errors),
            "by_type": dict(by_type),
            "by_severity": dict(by_severity),
            "by_file": dict(by_file),
        }

    # ---- internal ----

    def _parse_error_line(
        self,
        match: re.Match,
        lines: list[str],
        line_idx: int,
    ) -> CompilationError:
        """Build a ``CompilationError`` from a regex match."""
        file_path = match.group("file")
        line_no = int(match.group("line"))
        column = int(match.group("column"))
        severity = ErrorSeverity(match.group("severity"))
        message = match.group("message")

        # Context: next non-empty line often shows source code
        context_code = None
        if line_idx + 1 < len(lines):
            candidate = lines[line_idx + 1].strip()
            # Skip caret-pointer lines (e.g. "~~~~~~^") and blank lines
            if candidate and not _is_caret_line(candidate):
                context_code = candidate

        error_type, metadata = self._categorize_error(message)

        return CompilationError(
            file_path=file_path,
            line=line_no,
            column=column,
            error_type=error_type,
            message=message,
            severity=severity,
            context_code=context_code,
            raw_error=lines[line_idx],
            **metadata,
        )

    def _categorize_error(self, message: str) -> tuple[ErrorType, dict]:
        """Classify an error message and extract type-specific metadata."""

        # Check for "did you mean" suggestion first (applies to many types)
        suggestion: str | None = None
        did_you_mean = _DID_YOU_MEAN_RE.search(message)
        if did_you_mean:
            suggestion = did_you_mean.group("suggestion")

        # Missing member: value of type 'X' has no member 'y'
        m = _MISSING_MEMBER_RE.search(message)
        if m:
            return ErrorType.MISSING_MEMBER, {
                "type_name": m.group("type"),
                "member_name": m.group("member"),
                "suggestion": suggestion,
            }

        # Published → Binding mismatch (must be checked BEFORE generic type mismatch)
        m = _PUBLISH_BINDING_RE.search(message)
        if m:
            return ErrorType.PUBLISH_BINDING, {
                "expected_type": f"Binding<{m.group('binding')}>",
                "actual_type": f"Published<{m.group('inner')}>.Publisher",
                "suggestion": suggestion,
            }

        # Type mismatch: cannot convert value of type 'X' to expected type 'Y'
        m = _TYPE_MISMATCH_RE.search(message)
        if m:
            return ErrorType.TYPE_MISMATCH, {
                "expected_type": m.group("expected"),
                "actual_type": m.group("actual"),
                "suggestion": suggestion,
            }

        # Argument order: argument 'x' must precede argument 'y'
        m = _ARGUMENT_ORDER_RE.search(message)
        if m:
            return ErrorType.ARGUMENT_ORDER, {
                "member_name": m.group("arg"),
                "suggestion": suggestion,
            }

        # Cannot find in scope
        m = _CANNOT_FIND_IN_SCOPE_RE.search(message)
        if m:
            return ErrorType.CANNOT_FIND_IN_SCOPE, {
                "member_name": m.group("name"),
                "suggestion": suggestion,
            }

        # Missing argument
        m = _MISSING_ARGUMENT_RE.search(message)
        if m:
            return ErrorType.MISSING_ARGUMENT, {
                "member_name": m.group("param"),
                "suggestion": suggestion,
            }

        # Extra argument
        m = _EXTRA_ARGUMENT_RE.search(message)
        if m:
            return ErrorType.EXTRA_ARGUMENT, {
                "member_name": m.group("param"),
                "suggestion": suggestion,
            }

        # Missing import
        m = _MISSING_IMPORT_RE.search(message)
        if m:
            return ErrorType.MISSING_IMPORT, {
                "member_name": m.group("module"),
                "suggestion": suggestion,
            }

        # Protocol conformance
        m = _PROTOCOL_CONFORMANCE_RE.search(message)
        if m:
            return ErrorType.PROTOCOL_CONFORMANCE, {
                "type_name": m.group("type"),
                "member_name": m.group("protocol"),
                "suggestion": suggestion,
            }

        # Optional unwrap
        m = _OPTIONAL_UNWRAP_RE.search(message)
        if m:
            return ErrorType.OPTIONAL_UNWRAP, {
                "type_name": m.group("type"),
                "suggestion": suggestion,
            }

        # Access control
        m = _ACCESS_CONTROL_RE.search(message)
        if m:
            return ErrorType.ACCESS_CONTROL, {
                "member_name": m.group("name"),
                "suggestion": suggestion,
            }

        # Unavailable API
        m = _UNAVAILABLE_API_RE.search(message)
        if m:
            return ErrorType.UNAVAILABLE_API, {
                "member_name": m.group("name"),
                "suggestion": suggestion,
            }

        # Ambiguous reference
        m = _AMBIGUOUS_REFERENCE_RE.search(message)
        if m:
            return ErrorType.AMBIGUOUS_REFERENCE, {
                "member_name": m.group("name"),
                "suggestion": suggestion,
            }

        # macOS-only API
        m = _MACOS_API_RE.search(message)
        if m:
            return ErrorType.MACOS_API_USAGE, {
                "member_name": m.group("name"),
                "suggestion": suggestion,
            }

        # Fallback
        return ErrorType.UNKNOWN, {"suggestion": suggestion}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CARET_RE = re.compile(r"^[\s~^]+$")


def _is_caret_line(text: str) -> bool:
    """Return True if the line is a caret-pointer line (e.g. ``~~~~~~^``)."""
    return bool(_CARET_RE.match(text))
