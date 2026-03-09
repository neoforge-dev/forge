#!/usr/bin/env python3
"""
Resolve merge conflicts in test files by keeping BOTH sides.

Strategy:
1. Parse conflict markers (3-way merge with ||||||| base markers)
2. Keep HEAD (ours) content and origin/main (theirs) content
3. Deduplicate imports
4. Ensure no duplicate class/function names (rename if needed)
"""

import re
import sys
from pathlib import Path


def parse_conflicts(text: str) -> list[dict]:
    """Parse a file into segments: either plain text or conflict blocks."""
    segments = []
    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]
        if line.startswith("<<<<<<< "):
            # Found a conflict
            ours_lines = []
            base_lines = []
            theirs_lines = []
            section = "ours"
            i += 1
            while i < len(lines):
                line = lines[i]
                if line.startswith("||||||| "):
                    section = "base"
                    i += 1
                    continue
                elif line.startswith("======="):
                    section = "theirs"
                    i += 1
                    continue
                elif line.startswith(">>>>>>> "):
                    i += 1
                    break

                if section == "ours":
                    ours_lines.append(line)
                elif section == "base":
                    base_lines.append(line)
                else:
                    theirs_lines.append(line)
                i += 1

            segments.append(
                {
                    "type": "conflict",
                    "ours": "\n".join(ours_lines),
                    "base": "\n".join(base_lines),
                    "theirs": "\n".join(theirs_lines),
                }
            )
        else:
            # Plain text
            plain_start = i
            while i < len(lines) and not lines[i].startswith("<<<<<<< "):
                i += 1
            segments.append(
                {
                    "type": "plain",
                    "text": "\n".join(lines[plain_start:i]),
                }
            )

    return segments


def resolve_file(filepath: str) -> str:
    """
    For test files specifically:
    We take a simpler approach - keep the HEAD version as-is for the main body,
    then append unique test classes/methods from theirs that don't exist in HEAD.

    But for imports and module-level code: merge both.
    """
    text = Path(filepath).read_text()

    if "<<<<<<< " not in text:
        print(f"  No conflicts in {filepath}")
        return text

    # For the complex test files, use a different approach:
    # Extract HEAD version and THEIRS version completely, then merge.

    # Get the HEAD version (resolve all conflicts keeping ours)
    head_version = resolve_keeping_side(text, "ours")
    # Get the theirs version (resolve all conflicts keeping theirs)
    theirs_version = resolve_keeping_side(text, "theirs")

    # Parse both into components
    head_imports, head_helpers, head_classes = parse_test_file(head_version)
    theirs_imports, theirs_helpers, theirs_classes = parse_test_file(theirs_version)

    # Merge imports (deduplicate)
    merged_imports = merge_imports(head_imports, theirs_imports)

    # Merge helpers (module-level fixtures, functions)
    merged_helpers = merge_helpers(head_helpers, theirs_helpers)

    # Merge test classes (keep all from both, rename duplicates)
    merged_classes = merge_classes(head_classes, theirs_classes)

    # Reconstruct file
    parts = [merged_imports]
    if merged_helpers.strip():
        parts.append(merged_helpers)
    for cls_text in merged_classes:
        parts.append(cls_text)

    result = "\n\n".join(p.strip() for p in parts if p.strip())
    return result + "\n"


def resolve_keeping_side(text: str, side: str) -> str:
    """Resolve all conflicts keeping either 'ours' or 'theirs' side."""
    segments = parse_conflicts(text)
    parts = []
    for seg in segments:
        if seg["type"] == "plain":
            parts.append(seg["text"])
        else:
            parts.append(seg[side])
    return "\n".join(parts)


def parse_test_file(text: str) -> tuple[str, str, list[str]]:
    """Split a test file into imports, helpers, and class blocks."""
    lines = text.split("\n")

    # Find where imports end
    import_end = 0
    in_from_import = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (
            stripped.startswith(("import ", "from ", "#", '"""', "'''"))
            or stripped == ""
            or stripped == ")"
            or stripped.startswith(("from __future__",))
        ):
            import_end = i + 1
            continue
        # Multi-line imports
        if "(" in stripped and ")" not in stripped:
            in_from_import = True
            import_end = i + 1
            continue
        if in_from_import:
            import_end = i + 1
            if ")" in stripped:
                in_from_import = False
            continue
        break

    imports_text = "\n".join(lines[:import_end])

    # Find class blocks
    class_starts = []
    for i, line in enumerate(lines):
        if i < import_end:
            continue
        if re.match(r"^class \w+", line):
            class_starts.append(i)

    # Extract helper code (between imports and first class)
    helpers_start = import_end
    helpers_end = class_starts[0] if class_starts else len(lines)
    helpers_text = "\n".join(lines[helpers_start:helpers_end])

    # Extract class blocks
    classes = []
    for idx, start in enumerate(class_starts):
        end = class_starts[idx + 1] if idx + 1 < len(class_starts) else len(lines)
        cls_text = "\n".join(lines[start:end])
        classes.append(cls_text)

    return imports_text, helpers_text, classes


def get_class_name(cls_text: str) -> str:
    """Extract the class name from a class block."""
    match = re.match(r"^class (\w+)", cls_text)
    return match.group(1) if match else ""


def get_method_names(cls_text: str) -> set[str]:
    """Extract all method names from a class block."""
    return set(re.findall(r"def (test_\w+)", cls_text))


def merge_imports(head_imports: str, theirs_imports: str) -> str:
    """Merge imports from both sides, deduplicating."""
    head_lines = head_imports.strip().split("\n")
    theirs_lines = theirs_imports.strip().split("\n")

    # Parse structured imports
    docstring_lines = []
    regular_imports = []
    from_imports = {}  # module -> set of names
    other_lines = []

    for source_lines in [head_lines, theirs_lines]:
        i = 0
        in_docstring = False
        in_from_import = False
        current_from = ""
        while i < len(source_lines):
            line = source_lines[i]
            stripped = line.strip()

            # Handle docstrings
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if in_docstring:
                    in_docstring = False
                    if stripped not in [l.strip() for l in docstring_lines]:
                        docstring_lines.append(line)
                elif stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                    # Single-line docstring
                    if stripped not in [l.strip() for l in docstring_lines]:
                        docstring_lines.append(line)
                else:
                    in_docstring = True
                    if stripped not in [l.strip() for l in docstring_lines]:
                        docstring_lines.append(line)
                i += 1
                continue

            if in_docstring:
                if stripped not in [l.strip() for l in docstring_lines]:
                    docstring_lines.append(line)
                i += 1
                continue

            # Handle from X import (...)
            if stripped.startswith("from ") and "(" in stripped:
                # Get the module
                match = re.match(r"from\s+(\S+)\s+import\s*\(", stripped)
                if match:
                    current_from = match.group(1)
                    if current_from not in from_imports:
                        from_imports[current_from] = set()
                    # Check if names are on same line
                    after_paren = stripped.split("(", 1)[1]
                    if ")" in after_paren:
                        names = after_paren.rstrip(")").strip()
                        for n in re.split(r",\s*", names):
                            n = n.strip()
                            if n:
                                from_imports[current_from].add(n)
                        in_from_import = False
                    else:
                        # Multi-line
                        names = after_paren.strip()
                        for n in re.split(r",\s*", names):
                            n = n.strip()
                            if n:
                                from_imports[current_from].add(n)
                        in_from_import = True
                i += 1
                continue

            if in_from_import:
                if ")" in stripped:
                    before_paren = stripped.split(")")[0]
                    for n in re.split(r",\s*", before_paren):
                        n = n.strip()
                        if n:
                            from_imports[current_from].add(n)
                    in_from_import = False
                else:
                    for n in re.split(r",\s*", stripped.rstrip(",")):
                        n = n.strip()
                        if n:
                            from_imports[current_from].add(n)
                i += 1
                continue

            # Handle simple from X import Y
            if stripped.startswith("from "):
                match = re.match(r"from\s+(\S+)\s+import\s+(.+)", stripped)
                if match:
                    mod = match.group(1)
                    names_str = match.group(2)
                    if mod not in from_imports:
                        from_imports[mod] = set()
                    for n in re.split(r",\s*", names_str):
                        n = n.strip()
                        if n:
                            from_imports[mod].add(n)
                i += 1
                continue

            # Handle regular import
            if stripped.startswith("import "):
                if stripped not in regular_imports:
                    regular_imports.append(stripped)
                i += 1
                continue

            # Blank lines and comments in import section
            if stripped == "" or stripped.startswith("#"):
                i += 1
                continue

            # Other lines (like module-level mock setup)
            if stripped and stripped not in [l.strip() for l in other_lines]:
                other_lines.append(line)
            i += 1

    # Reconstruct imports
    result_lines = []

    # Docstring first
    if docstring_lines:
        # Take the longer/more detailed docstring
        result_lines.extend(docstring_lines)
        result_lines.append("")

    # __future__ imports first
    if "__future__" in from_imports:
        names = sorted(from_imports.pop("__future__"))
        result_lines.append(f"from __future__ import {', '.join(names)}")
        result_lines.append("")

    # Standard library imports
    stdlib_imports = sorted(set(regular_imports))
    if stdlib_imports:
        for imp in stdlib_imports:
            result_lines.append(imp)
        result_lines.append("")

    # From imports (sorted by module)
    std_from = {}
    third_party_from = {}
    local_from = {}

    for mod, names in sorted(from_imports.items()):
        if mod.startswith("forge_harness"):
            local_from[mod] = names
        elif mod in ("pathlib", "unittest.mock", "datetime", "pathlib"):
            std_from[mod] = names
        else:
            third_party_from[mod] = names

    # Standard lib from imports
    for mod, names in sorted({**std_from}.items()):
        sorted_names = sorted(names)
        result_lines.append(f"from {mod} import {', '.join(sorted_names)}")

    if std_from:
        result_lines.append("")

    # Third party from imports
    for mod, names in sorted(third_party_from.items()):
        sorted_names = sorted(names)
        result_lines.append(f"from {mod} import {', '.join(sorted_names)}")

    if third_party_from:
        result_lines.append("")

    # Local from imports
    for mod, names in sorted(local_from.items()):
        sorted_names = sorted(names)
        if len(sorted_names) > 3:
            result_lines.append(f"from {mod} import (")
            for n in sorted_names:
                result_lines.append(f"    {n},")
            result_lines.append(")")
        else:
            result_lines.append(f"from {mod} import {', '.join(sorted_names)}")

    if local_from:
        result_lines.append("")

    # Other lines (like mock setup code)
    if other_lines:
        for line in other_lines:
            result_lines.append(line)
        result_lines.append("")

    return "\n".join(result_lines)


def merge_helpers(head_helpers: str, theirs_helpers: str) -> str:
    """Merge module-level helper code (fixtures, functions)."""
    head_lines = head_helpers.strip()
    theirs_lines = theirs_helpers.strip()

    if not head_lines and not theirs_lines:
        return ""
    if not theirs_lines:
        return head_lines
    if not head_lines:
        return theirs_lines

    # Parse functions/fixtures from each
    head_funcs = extract_top_level_defs(head_lines)
    theirs_funcs = extract_top_level_defs(theirs_lines)

    # Start with all of HEAD's helpers
    result_parts = [head_lines]

    # Add any fixtures/functions from theirs that aren't in HEAD
    head_func_names = {name for name, _ in head_funcs}
    for name, body in theirs_funcs:
        if name not in head_func_names:
            result_parts.append(body)

    return "\n\n".join(result_parts)


def extract_top_level_defs(text: str) -> list[tuple[str, str]]:
    """Extract top-level function/fixture definitions."""
    funcs = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        # Match top-level def or @pytest.fixture decorated def
        if re.match(r"^(def |@)", line):
            start = i
            # Include decorators
            while i < len(lines) and lines[i].strip().startswith("@"):
                i += 1
            if i < len(lines) and lines[i].strip().startswith("def "):
                match = re.match(r"^def (\w+)", lines[i])
                name = match.group(1) if match else f"unknown_{i}"
                i += 1
                # Read body
                while i < len(lines) and (
                    lines[i].startswith(" ") or lines[i].startswith("\t") or lines[i].strip() == ""
                ):
                    i += 1
                funcs.append((name, "\n".join(lines[start:i])))
            else:
                i += 1
        else:
            i += 1
    return funcs


def merge_classes(head_classes: list[str], theirs_classes: list[str]) -> list[str]:
    """Merge test classes from both sides."""
    result = []
    head_class_map = {}  # name -> text

    for cls_text in head_classes:
        name = get_class_name(cls_text)
        if name:
            head_class_map[name] = cls_text
            result.append(cls_text)

    for cls_text in theirs_classes:
        name = get_class_name(cls_text)
        if not name:
            continue

        if name not in head_class_map:
            # New class from theirs - add it
            result.append(cls_text)
        else:
            # Same class name - merge methods
            head_methods = get_method_names(head_class_map[name])
            theirs_methods = get_method_names(cls_text)
            new_methods = theirs_methods - head_methods

            if new_methods:
                # Extract new methods from theirs and add to existing class
                merged = add_methods_to_class(head_class_map[name], cls_text, new_methods)
                # Replace in result
                for i, r in enumerate(result):
                    if get_class_name(r) == name:
                        result[i] = merged
                        break

    return result


def add_methods_to_class(head_cls: str, theirs_cls: str, new_method_names: set[str]) -> str:
    """Add specific methods from theirs_cls to head_cls."""
    theirs_lines = theirs_cls.split("\n")
    methods_to_add = []

    for method_name in new_method_names:
        # Find this method in theirs
        i = 0
        while i < len(theirs_lines):
            line = theirs_lines[i]
            # Look for the method def
            if re.match(rf"    (async )?def {re.escape(method_name)}\b", line):
                # Include any decorators above
                start = i
                while start > 0 and theirs_lines[start - 1].strip().startswith("@"):
                    start -= 1
                # Include the body
                end = i + 1
                while end < len(theirs_lines):
                    if theirs_lines[end].strip() == "" or theirs_lines[end].startswith("    "):
                        end += 1
                    else:
                        break
                # Trim trailing blank lines
                while end > start and theirs_lines[end - 1].strip() == "":
                    end -= 1
                methods_to_add.append("\n".join(theirs_lines[start:end]))
                break
            i += 1

    if not methods_to_add:
        return head_cls

    # Append methods to head class
    head_lines = head_cls.rstrip().split("\n")
    # Remove trailing blank lines
    while head_lines and head_lines[-1].strip() == "":
        head_lines.pop()

    result = "\n".join(head_lines)
    for method in methods_to_add:
        result += "\n\n" + method
    result += "\n"

    return result


if __name__ == "__main__":
    files = [
        "harness/tests/test_codex_agent.py",
        "harness/tests/test_dashboard_service.py",
        "harness/tests/test_deployment_legacy.py",
        "harness/tests/test_portfolio_service.py",
        "harness/tests/test_xnode_listener.py",
    ]

    root = Path("/Users/bogdan/work/FORGE")

    for f in files:
        filepath = root / f
        print(f"\nResolving: {f}")
        try:
            resolved = resolve_file(str(filepath))
            filepath.write_text(resolved)
            print(f"  Written: {len(resolved)} chars")
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback

            traceback.print_exc()
