"""
Tests for forge_harness/ios_harness/error_parser.py — Xcode Build Error Parser.

Covers:
- ErrorType and ErrorSeverity enums
- CompilationError data model
- XcodeErrorParser.parse_build_log — full log parsing
- Error categorization for all 17 ErrorType variants
- Context code extraction
- Caret-line skipping
- Grouping by file
- Filtering by type and severity
- Summary generation
- Real forge-terminal build log fixture parsing
- Edge cases: empty logs, notes, multi-line data, "did you mean" suggestions
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_harness.ios_harness.error_parser import (
    CompilationError,
    ErrorSeverity,
    ErrorType,
    XcodeErrorParser,
    _is_caret_line,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def parser() -> XcodeErrorParser:
    return XcodeErrorParser()


@pytest.fixture
def forge_terminal_log() -> str:
    return (FIXTURE_DIR / "forge_terminal_errors.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestEnums:
    def test_error_type_values(self) -> None:
        assert ErrorType.MISSING_MEMBER.value == "missing_member"
        assert ErrorType.TYPE_MISMATCH.value == "type_mismatch"
        assert ErrorType.UNKNOWN.value == "unknown"

    def test_error_severity_values(self) -> None:
        assert ErrorSeverity.ERROR.value == "error"
        assert ErrorSeverity.WARNING.value == "warning"
        assert ErrorSeverity.NOTE.value == "note"

    def test_all_error_types_have_unique_values(self) -> None:
        values = [e.value for e in ErrorType]
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# CompilationError model
# ---------------------------------------------------------------------------


class TestCompilationError:
    def test_str_format(self) -> None:
        err = CompilationError(
            file_path="Foo.swift",
            line=42,
            column=10,
            error_type=ErrorType.MISSING_MEMBER,
            message="value of type 'X' has no member 'y'",
            severity=ErrorSeverity.ERROR,
        )
        assert str(err) == "Foo.swift:42:10: error: value of type 'X' has no member 'y'"

    def test_optional_fields_default_to_none(self) -> None:
        err = CompilationError(
            file_path="a.swift",
            line=1,
            column=1,
            error_type=ErrorType.UNKNOWN,
            message="msg",
            severity=ErrorSeverity.WARNING,
        )
        assert err.type_name is None
        assert err.member_name is None
        assert err.expected_type is None
        assert err.actual_type is None
        assert err.context_code is None
        assert err.suggestion is None
        assert err.raw_error == ""


# ---------------------------------------------------------------------------
# parse_build_log — basic parsing
# ---------------------------------------------------------------------------


class TestParseBuildLog:
    def test_empty_log(self, parser: XcodeErrorParser) -> None:
        assert parser.parse_build_log("") == []

    def test_no_errors(self, parser: XcodeErrorParser) -> None:
        log = "Compiling Foo.swift\nLinking ForgeTerminal\n** BUILD SUCCEEDED **\n"
        assert parser.parse_build_log(log) == []

    def test_single_error(self, parser: XcodeErrorParser) -> None:
        log = "/path/Foo.swift:10:5: error: use of undeclared type 'Bar'\n"
        errors = parser.parse_build_log(log)
        assert len(errors) == 1
        assert errors[0].file_path == "/path/Foo.swift"
        assert errors[0].line == 10
        assert errors[0].column == 5
        assert errors[0].severity == ErrorSeverity.ERROR
        assert errors[0].message == "use of undeclared type 'Bar'"

    def test_single_warning(self, parser: XcodeErrorParser) -> None:
        log = "/path/Foo.swift:20:3: warning: unused variable 'x'\n"
        errors = parser.parse_build_log(log)
        assert len(errors) == 1
        assert errors[0].severity == ErrorSeverity.WARNING

    def test_single_note(self, parser: XcodeErrorParser) -> None:
        log = "/path/Foo.swift:20:3: note: did you mean 'y'?\n"
        errors = parser.parse_build_log(log)
        assert len(errors) == 1
        assert errors[0].severity == ErrorSeverity.NOTE

    def test_multiple_errors(self, parser: XcodeErrorParser) -> None:
        log = (
            "/a.swift:1:1: error: first\n"
            "/b.swift:2:2: error: second\n"
            "/c.swift:3:3: warning: third\n"
        )
        errors = parser.parse_build_log(log)
        assert len(errors) == 3

    def test_context_code_extraction(self, parser: XcodeErrorParser) -> None:
        log = (
            "/Foo.swift:10:5: error: value of type 'X' has no member 'y'\n"
            "        let val = x.y\n"
            "                  ~~~^\n"
        )
        errors = parser.parse_build_log(log)
        assert len(errors) == 1
        assert errors[0].context_code == "let val = x.y"

    def test_caret_line_skipped_for_context(self, parser: XcodeErrorParser) -> None:
        log = (
            "/Foo.swift:10:5: error: some error\n"
            "    ~~~^\n"
        )
        errors = parser.parse_build_log(log)
        assert errors[0].context_code is None

    def test_raw_error_preserved(self, parser: XcodeErrorParser) -> None:
        line = "/Foo.swift:10:5: error: some error"
        errors = parser.parse_build_log(line + "\n")
        assert errors[0].raw_error == line

    def test_non_error_lines_ignored(self, parser: XcodeErrorParser) -> None:
        log = (
            "CompileSwift normal arm64 blah\n"
            "note: Using new build system\n"
            "** BUILD FAILED **\n"
        )
        assert parser.parse_build_log(log) == []


# ---------------------------------------------------------------------------
# Error categorization — each ErrorType
# ---------------------------------------------------------------------------


class TestCategorizeMissingMember:
    def test_missing_member(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: value of type 'TerminalColor' has no member 'a'\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.MISSING_MEMBER
        assert errors[0].type_name == "TerminalColor"
        assert errors[0].member_name == "a"


class TestCategorizeTypeMismatch:
    def test_type_mismatch_convert_value(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: cannot convert value of type 'NSColor' to expected type 'UIColor'\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.TYPE_MISMATCH
        assert errors[0].actual_type == "NSColor"
        assert errors[0].expected_type == "UIColor"

    def test_type_mismatch_specified_type(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: cannot convert value of type 'Data' to specified type 'ByteBuffer'\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.TYPE_MISMATCH
        assert errors[0].actual_type == "Data"
        assert errors[0].expected_type == "ByteBuffer"

    def test_type_mismatch_convert_type(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: cannot convert type of type 'Int' to expected type 'String'\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.TYPE_MISMATCH


class TestCategorizeArgumentOrder:
    def test_argument_order(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: argument 'columns' must precede argument 'rows'\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.ARGUMENT_ORDER
        assert errors[0].member_name == "columns"


class TestCategorizeCannotFindInScope:
    def test_cannot_find_in_scope(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: cannot find 'NSEvent' in scope\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.CANNOT_FIND_IN_SCOPE
        assert errors[0].member_name == "NSEvent"


class TestCategorizeMissingArgument:
    def test_missing_argument(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: missing argument for parameter 'encoding' in call\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.MISSING_ARGUMENT
        assert errors[0].member_name == "encoding"


class TestCategorizeExtraArgument:
    def test_extra_argument(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: extra argument 'animated' in call\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.EXTRA_ARGUMENT
        assert errors[0].member_name == "animated"


class TestCategorizeMissingImport:
    def test_missing_import(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: no such module 'TerminalParser'\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.MISSING_IMPORT
        assert errors[0].member_name == "TerminalParser"


class TestCategorizeProtocolConformance:
    def test_protocol_conformance(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: type 'TerminalEmulator' does not conform to protocol 'TerminalDelegate'\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.PROTOCOL_CONFORMANCE
        assert errors[0].type_name == "TerminalEmulator"
        assert errors[0].member_name == "TerminalDelegate"


class TestCategorizeOptionalUnwrap:
    def test_optional_unwrap(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: value of optional type 'String?' must be unwrapped to a value of type 'String'\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.OPTIONAL_UNWRAP
        assert errors[0].type_name == "String?"


class TestCategorizePublishBinding:
    def test_publish_binding(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: cannot convert value of type 'Published<TerminalBuffer>.Publisher' to expected type 'Binding<TerminalBuffer>'\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.PUBLISH_BINDING
        assert errors[0].actual_type == "Published<TerminalBuffer>.Publisher"
        assert errors[0].expected_type == "Binding<TerminalBuffer>"


class TestCategorizeAccessControl:
    def test_access_control(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: 'processOutput' is inaccessible due to 'private' protection level\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.ACCESS_CONTROL
        assert errors[0].member_name == "processOutput"


class TestCategorizeUnavailableAPI:
    def test_unavailable_api_deprecated(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: 'NSTouchBar' was deprecated in macOS 14.0\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.UNAVAILABLE_API
        assert errors[0].member_name == "NSTouchBar"

    def test_unavailable_api_renamed(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: 'oldName' has been renamed to newAPI 2.0\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.UNAVAILABLE_API


class TestCategorizeAmbiguousReference:
    def test_ambiguous_reference(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: ambiguous use of 'write'\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.AMBIGUOUS_REFERENCE
        assert errors[0].member_name == "write"


class TestCategorizeMacOSAPI:
    def test_macos_api(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: 'NSPasteboard' is unavailable in iOS\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.MACOS_API_USAGE
        assert errors[0].member_name == "NSPasteboard"


class TestCategorizeUnknown:
    def test_unknown_error(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: something completely unexpected happened\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.UNKNOWN


class TestDidYouMeanSuggestion:
    def test_suggestion_extracted(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:10:5: error: cannot find 'UserDefaults' in scope; did you mean 'UserDefaults'?\n"
        errors = parser.parse_build_log(log)
        assert errors[0].error_type == ErrorType.CANNOT_FIND_IN_SCOPE
        assert errors[0].suggestion == "UserDefaults"


# ---------------------------------------------------------------------------
# Grouping and filtering
# ---------------------------------------------------------------------------


class TestGroupByFile:
    def test_group_by_file(self, parser: XcodeErrorParser) -> None:
        log = (
            "/a.swift:1:1: error: first\n"
            "/b.swift:2:2: error: second\n"
            "/a.swift:3:3: error: third\n"
        )
        errors = parser.parse_build_log(log)
        grouped = parser.group_by_file(errors)
        assert len(grouped) == 2
        assert len(grouped["/a.swift"]) == 2
        assert len(grouped["/b.swift"]) == 1

    def test_group_empty_list(self, parser: XcodeErrorParser) -> None:
        assert parser.group_by_file([]) == {}


class TestFilterByType:
    def test_filter_by_type(self, parser: XcodeErrorParser) -> None:
        log = (
            "/F.swift:1:1: error: value of type 'X' has no member 'y'\n"
            "/F.swift:2:2: error: cannot find 'Z' in scope\n"
            "/F.swift:3:3: error: value of type 'A' has no member 'b'\n"
        )
        errors = parser.parse_build_log(log)
        missing = parser.filter_by_type(errors, ErrorType.MISSING_MEMBER)
        assert len(missing) == 2
        scope = parser.filter_by_type(errors, ErrorType.CANNOT_FIND_IN_SCOPE)
        assert len(scope) == 1

    def test_filter_returns_empty_on_no_match(self, parser: XcodeErrorParser) -> None:
        log = "/F.swift:1:1: error: something\n"
        errors = parser.parse_build_log(log)
        assert parser.filter_by_type(errors, ErrorType.MISSING_IMPORT) == []


class TestFilterBySeverity:
    def test_filter_by_severity(self, parser: XcodeErrorParser) -> None:
        log = (
            "/F.swift:1:1: error: err\n"
            "/F.swift:2:2: warning: warn\n"
            "/F.swift:3:3: note: note\n"
        )
        errors = parser.parse_build_log(log)
        assert len(parser.filter_by_severity(errors, ErrorSeverity.ERROR)) == 1
        assert len(parser.filter_by_severity(errors, ErrorSeverity.WARNING)) == 1
        assert len(parser.filter_by_severity(errors, ErrorSeverity.NOTE)) == 1


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summary(self, parser: XcodeErrorParser) -> None:
        log = (
            "/a.swift:1:1: error: value of type 'X' has no member 'y'\n"
            "/a.swift:2:2: warning: unused\n"
            "/b.swift:3:3: error: cannot find 'Z' in scope\n"
        )
        errors = parser.parse_build_log(log)
        s = parser.summary(errors)
        assert s["total"] == 3
        assert s["by_severity"]["error"] == 2
        assert s["by_severity"]["warning"] == 1
        assert s["by_file"]["/a.swift"] == 2
        assert s["by_file"]["/b.swift"] == 1

    def test_summary_empty(self, parser: XcodeErrorParser) -> None:
        s = parser.summary([])
        assert s["total"] == 0
        assert s["by_type"] == {}


# ---------------------------------------------------------------------------
# _is_caret_line helper
# ---------------------------------------------------------------------------


class TestIsCaretLine:
    def test_caret_only(self) -> None:
        assert _is_caret_line("    ^") is True

    def test_tilde_caret(self) -> None:
        assert _is_caret_line("~~~~~~^") is True

    def test_spaces_and_caret(self) -> None:
        assert _is_caret_line("          ~~~~~~^~~~~~~~~~") is True

    def test_code_line_is_not_caret(self) -> None:
        assert _is_caret_line("let val = x.y") is False

    def test_empty_string(self) -> None:
        assert _is_caret_line("") is False


# ---------------------------------------------------------------------------
# Real fixture: forge-terminal build log
# ---------------------------------------------------------------------------


class TestForgeTerminalFixture:
    def test_parses_all_errors(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        # The fixture has 37 error-level + 3 warning-level + 3 note-level = 43 diagnostics
        assert len(errors) >= 35

    def test_categorizes_missing_member(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        missing_member = parser.filter_by_type(errors, ErrorType.MISSING_MEMBER)
        # Renderer: 'a', 'foreground', 'background', 'toSIMD4'
        # SSHClient: 'authenticate', 'writeAndFlush'
        # SettingsView: 'fontSize'
        # Color+Terminal: 'red', 'green', 'blue'
        # ThemeManager: 'backgroundColor'
        assert len(missing_member) >= 10

    def test_categorizes_type_mismatch(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        mismatch = parser.filter_by_type(errors, ErrorType.TYPE_MISMATCH)
        # NSColor → UIColor (2x), Data → ByteBuffer, CGFloat → Double
        assert len(mismatch) >= 3

    def test_categorizes_cannot_find_in_scope(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        scope = parser.filter_by_type(errors, ErrorType.CANNOT_FIND_IN_SCOPE)
        # NSEvent, NWPathMonitor, UserDefaults (with suggestion)
        assert len(scope) >= 2

    def test_groups_by_file(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        grouped = parser.group_by_file(errors)
        # Should have errors in Renderer, TerminalView, TerminalEmulator, SSHClient, etc.
        assert len(grouped) >= 5

    def test_renderer_has_multiple_errors(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        grouped = parser.group_by_file(errors)
        renderer_key = [k for k in grouped if "Renderer.swift" in k]
        assert len(renderer_key) == 1
        assert len(grouped[renderer_key[0]]) >= 4

    def test_warnings_found(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        warnings = parser.filter_by_severity(errors, ErrorSeverity.WARNING)
        assert len(warnings) >= 2

    def test_notes_found(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        notes = parser.filter_by_severity(errors, ErrorSeverity.NOTE)
        assert len(notes) >= 2

    def test_summary_counts(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        s = parser.summary(errors)
        assert s["total"] >= 35
        assert "error" in s["by_severity"]
        assert len(s["by_file"]) >= 5

    def test_publish_binding_found(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        pb = parser.filter_by_type(errors, ErrorType.PUBLISH_BINDING)
        assert len(pb) >= 1
        assert pb[0].actual_type == "Published<TerminalBuffer>.Publisher"

    def test_missing_import_found(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        mi = parser.filter_by_type(errors, ErrorType.MISSING_IMPORT)
        assert len(mi) >= 1
        assert mi[0].member_name == "TerminalParser"

    def test_protocol_conformance_found(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        pc = parser.filter_by_type(errors, ErrorType.PROTOCOL_CONFORMANCE)
        assert len(pc) >= 1

    def test_access_control_found(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        ac = parser.filter_by_type(errors, ErrorType.ACCESS_CONTROL)
        assert len(ac) >= 1
        assert ac[0].member_name == "processOutput"

    def test_macos_api_found(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        mac = parser.filter_by_type(errors, ErrorType.MACOS_API_USAGE)
        assert len(mac) >= 1

    def test_argument_order_found(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        ao = parser.filter_by_type(errors, ErrorType.ARGUMENT_ORDER)
        assert len(ao) >= 1

    def test_context_code_extracted_for_some_errors(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        with_context = [e for e in errors if e.context_code is not None]
        assert len(with_context) > 0

    def test_did_you_mean_suggestion_extracted(self, parser: XcodeErrorParser, forge_terminal_log: str) -> None:
        errors = parser.parse_build_log(forge_terminal_log)
        with_suggestion = [e for e in errors if e.suggestion is not None]
        assert len(with_suggestion) >= 1
