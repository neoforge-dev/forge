"""
Tests for Pre-commit Test Runner
=================================

Tests the pre-commit test runner quality gate functionality.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.quality_gates.pre_commit_tests import (
    PreCommitTestRunner,
    TestFailure,
    TestResult,
    run_pre_commit_tests,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    import subprocess

    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    return repo_path


@pytest.fixture
def test_runner(temp_repo: Path) -> PreCommitTestRunner:
    """Create a PreCommitTestRunner instance."""
    return PreCommitTestRunner(repo_root=temp_repo)


class TestPreCommitTestRunner:
    """Test PreCommitTestRunner class."""

    async def test_get_staged_files_empty(self, test_runner: PreCommitTestRunner) -> None:
        """Test getting staged files when none exist."""
        files = await test_runner.get_staged_files()
        assert files == []

    async def test_get_staged_files_with_staged(
        self, test_runner: PreCommitTestRunner, temp_repo: Path
    ) -> None:
        """Test getting staged files."""
        # Create a test file and stage it
        test_file = temp_repo / "test.py"
        test_file.write_text("print('hello')\n")

        import subprocess

        subprocess.run(["git", "add", "test.py"], cwd=temp_repo, check=True)

        files = await test_runner.get_staged_files()
        assert len(files) == 1
        assert files[0].name == "test.py"

    def test_categorize_files_python(
        self, test_runner: PreCommitTestRunner, temp_repo: Path
    ) -> None:
        """Test categorizing Python files."""
        files = [
            temp_repo / "test.py",
            temp_repo / "main.py",
        ]

        categorized = test_runner.categorize_files(files)
        assert len(categorized["python"]) == 2
        assert len(categorized["js_ts"]) == 0
        assert len(categorized["other"]) == 0

    def test_categorize_files_js_ts(
        self, test_runner: PreCommitTestRunner, temp_repo: Path
    ) -> None:
        """Test categorizing JS/TS files."""
        files = [
            temp_repo / "app.js",
            temp_repo / "component.tsx",
            temp_repo / "utils.ts",
        ]

        categorized = test_runner.categorize_files(files)
        assert len(categorized["python"]) == 0
        assert len(categorized["js_ts"]) == 3
        assert len(categorized["other"]) == 0

    def test_categorize_files_mixed(
        self, test_runner: PreCommitTestRunner, temp_repo: Path
    ) -> None:
        """Test categorizing mixed file types."""
        files = [
            temp_repo / "test.py",
            temp_repo / "app.js",
            temp_repo / "component.tsx",
            temp_repo / "README.md",
        ]

        categorized = test_runner.categorize_files(files)
        assert len(categorized["python"]) == 1
        assert len(categorized["js_ts"]) == 2
        assert len(categorized["other"]) == 1

    def test_find_python_imports(self, test_runner: PreCommitTestRunner, temp_repo: Path) -> None:
        """Test extracting Python imports."""
        # Create a Python file with imports
        test_file = temp_repo / "test_module.py"
        test_file.write_text("""
import os
import sys
from pathlib import Path
from typing import List, Dict
from forge_harness.quality_gates import lint_gate

def main():
    pass
""")

        imports = test_runner.find_python_imports(test_file)
        assert "os" in imports
        assert "sys" in imports
        assert "pathlib" in imports
        assert "typing" in imports
        assert "forge_harness" in imports

    def test_find_affected_python_tests_direct(
        self, test_runner: PreCommitTestRunner, temp_repo: Path
    ) -> None:
        """Test finding affected tests when test file itself changed."""
        # Create a test file
        test_file = temp_repo / "test_example.py"
        test_file.write_text("def test_something(): pass\n")

        affected = test_runner.find_affected_python_tests([test_file])
        assert test_file in affected

    def test_find_affected_python_tests_import(
        self, test_runner: PreCommitTestRunner, temp_repo: Path
    ) -> None:
        """Test finding affected tests via import analysis."""
        # Create a module
        module_dir = temp_repo / "mymodule"
        module_dir.mkdir()
        (module_dir / "__init__.py").write_text("")

        module_file = module_dir / "core.py"
        module_file.write_text("def my_function(): pass\n")

        # Create a test that imports the module
        test_file = temp_repo / "test_mymodule.py"
        test_file.write_text("""
import mymodule.core

def test_my_function():
    mymodule.core.my_function()
""")

        # Change the module file
        affected = test_runner.find_affected_python_tests([module_file])

        # Should find the test that imports mymodule
        assert len(affected) > 0
        # The test file should be in the affected list
        assert any(f.name == "test_mymodule.py" for f in affected)

    def test_find_affected_js_tests_direct(
        self, test_runner: PreCommitTestRunner, temp_repo: Path
    ) -> None:
        """Test finding affected JS tests when test file itself changed."""
        # Create a test file
        test_file = temp_repo / "example.test.js"
        test_file.write_text("test('example', () => {})\n")

        affected = test_runner.find_affected_js_tests([test_file])
        assert test_file in affected

    def test_find_affected_js_tests_import(
        self, test_runner: PreCommitTestRunner, temp_repo: Path
    ) -> None:
        """Test finding affected JS tests via import analysis."""
        # Create a module
        module_file = temp_repo / "utils.js"
        module_file.write_text("export function helper() {}\n")

        # Create a test that imports the module
        test_file = temp_repo / "utils.test.js"
        test_file.write_text("""
import { helper } from './utils';

test('helper works', () => {
  helper();
});
""")

        # Change the module file
        affected = test_runner.find_affected_js_tests([module_file])
        assert test_file in affected

    @patch("forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner._run_command")
    async def test_run_pytest_success(
        self, mock_run: AsyncMock, test_runner: PreCommitTestRunner, temp_repo: Path
    ) -> None:
        """Test running pytest with successful tests."""
        # Mock pytest JSON output
        pytest_output = json.dumps({"summary": {"total": 5, "passed": 5}, "tests": []})
        mock_run.return_value = pytest_output

        test_files = {temp_repo / "test_example.py"}
        failures, tests_run, exec_time = await test_runner.run_pytest(test_files)

        assert failures == []
        assert tests_run == 5
        assert exec_time >= 0
        mock_run.assert_called_once()

    @patch("forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner._run_command")
    async def test_run_pytest_with_failures(
        self, mock_run: AsyncMock, test_runner: PreCommitTestRunner, temp_repo: Path
    ) -> None:
        """Test running pytest with test failures."""
        # Mock pytest JSON output with failures
        pytest_output = json.dumps(
            {
                "summary": {"total": 5, "passed": 3, "failed": 2},
                "tests": [
                    {
                        "nodeid": "test_example.py::test_failure",
                        "outcome": "failed",
                        "lineno": 10,
                        "call": {"longrepr": "AssertionError: Expected True, got False"},
                    },
                    {
                        "nodeid": "test_example.py::test_error",
                        "outcome": "error",
                        "lineno": 20,
                        "call": {"longrepr": "ValueError: Invalid input"},
                    },
                ],
            }
        )
        mock_run.return_value = pytest_output

        test_files = {temp_repo / "test_example.py"}
        failures, tests_run, exec_time = await test_runner.run_pytest(test_files)

        assert len(failures) == 2
        assert tests_run == 5
        assert failures[0].test_name == "test_example.py::test_failure"
        assert "AssertionError" in failures[0].error_message

    @patch("forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner._command_exists")
    async def test_run_jest_not_found(
        self, mock_exists: AsyncMock, test_runner: PreCommitTestRunner, temp_repo: Path
    ) -> None:
        """Test running jest when npm is not available."""
        mock_exists.return_value = False

        test_files = {temp_repo / "example.test.js"}
        failures, tests_run, exec_time = await test_runner.run_jest(test_files)

        assert failures == []
        assert tests_run == 0

    @patch("forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner._command_exists")
    @patch("forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner._run_command")
    async def test_run_jest_success(
        self,
        mock_run: AsyncMock,
        mock_exists: AsyncMock,
        test_runner: PreCommitTestRunner,
        temp_repo: Path,
    ) -> None:
        """Test running jest with successful tests."""
        mock_exists.return_value = True

        # Create package.json with jest
        package_json = temp_repo / "package.json"
        package_json.write_text(json.dumps({"devDependencies": {"jest": "^29.0.0"}}))

        # Mock jest JSON output
        jest_output = json.dumps(
            {
                "numTotalTests": 3,
                "testResults": [{"name": "example.test.js", "assertionResults": []}],
            }
        )
        mock_run.return_value = jest_output

        test_files = {temp_repo / "example.test.js"}
        failures, tests_run, exec_time = await test_runner.run_jest(test_files)

        assert failures == []
        assert tests_run == 3

    @patch("forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner._command_exists")
    @patch("forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner._run_command")
    async def test_run_jest_with_failures(
        self,
        mock_run: AsyncMock,
        mock_exists: AsyncMock,
        test_runner: PreCommitTestRunner,
        temp_repo: Path,
    ) -> None:
        """Test running jest with test failures."""
        mock_exists.return_value = True

        # Create package.json with vitest
        package_json = temp_repo / "package.json"
        package_json.write_text(json.dumps({"devDependencies": {"vitest": "^1.0.0"}}))

        # Mock vitest JSON output with failures
        jest_output = json.dumps(
            {
                "numTotalTests": 3,
                "testResults": [
                    {
                        "name": "example.test.js",
                        "assertionResults": [
                            {
                                "fullName": "test suite > test case",
                                "status": "failed",
                                "failureMessages": ["Expected 2, got 3"],
                            }
                        ],
                    }
                ],
            }
        )
        mock_run.return_value = jest_output

        test_files = {temp_repo / "example.test.js"}
        failures, tests_run, exec_time = await test_runner.run_jest(test_files)

        assert len(failures) == 1
        assert tests_run == 3
        assert "Expected 2, got 3" in failures[0].error_message

    @patch("forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner.get_staged_files")
    @patch("forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner.run_pytest")
    async def test_run_gate_no_files(
        self,
        mock_pytest: AsyncMock,
        mock_get_files: AsyncMock,
        test_runner: PreCommitTestRunner,
    ) -> None:
        """Test running gate with no staged files."""
        mock_get_files.return_value = []

        result = await test_runner.run(check_only=True)

        assert result.passed is True
        assert result.files_changed == 0
        assert result.tests_run == 0
        mock_pytest.assert_not_called()

    @patch("forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner.get_staged_files")
    @patch(
        "forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner.find_affected_python_tests"
    )
    @patch("forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner.run_pytest")
    async def test_run_gate_python_tests_pass(
        self,
        mock_pytest: AsyncMock,
        mock_find_tests: MagicMock,
        mock_get_files: AsyncMock,
        test_runner: PreCommitTestRunner,
        temp_repo: Path,
    ) -> None:
        """Test running gate with Python tests that pass."""
        mock_get_files.return_value = [temp_repo / "module.py"]
        mock_find_tests.return_value = {temp_repo / "test_module.py"}
        mock_pytest.return_value = ([], 5, 1.5)  # No failures, 5 tests, 1.5s

        result = await test_runner.run(check_only=True)

        assert result.passed is True
        assert result.files_changed == 1
        assert result.tests_run == 5
        assert result.failure_count == 0
        assert result.execution_time > 0

    @patch("forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner.get_staged_files")
    @patch(
        "forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner.find_affected_python_tests"
    )
    @patch("forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner.run_pytest")
    async def test_run_gate_python_tests_fail(
        self,
        mock_pytest: AsyncMock,
        mock_find_tests: MagicMock,
        mock_get_files: AsyncMock,
        test_runner: PreCommitTestRunner,
        temp_repo: Path,
    ) -> None:
        """Test running gate with Python tests that fail."""
        mock_get_files.return_value = [temp_repo / "module.py"]
        mock_find_tests.return_value = {temp_repo / "test_module.py"}

        failure = TestFailure(
            test_file="test_module.py",
            test_name="test_function",
            error_message="Test failed",
        )
        mock_pytest.return_value = ([failure], 5, 1.5)

        result = await test_runner.run(check_only=True)

        assert result.passed is False
        assert result.files_changed == 1
        assert result.tests_run == 5
        assert result.failure_count == 1


class TestTestResult:
    """Test TestResult class."""

    def test_test_result_passed(self) -> None:
        """Test TestResult when passed."""
        result = TestResult(passed=True, tests_run=10, files_changed=3)
        assert result.passed is True
        assert result.tests_run == 10
        assert result.failure_count == 0

    def test_test_result_with_failures(self) -> None:
        """Test TestResult with failures."""
        failures = [
            TestFailure(
                test_file="test_example.py",
                test_name="test_function",
                error_message="AssertionError",
            ),
            TestFailure(
                test_file="test_other.py",
                test_name="test_other",
                error_message="ValueError",
            ),
        ]
        result = TestResult(
            passed=False,
            tests_run=10,
            files_changed=2,
            failures=failures,
            execution_time=2.5,
        )

        assert result.passed is False
        assert result.failure_count == 2
        assert result.execution_time == 2.5

    def test_test_result_summary(self) -> None:
        """Test summary generation."""
        result = TestResult(passed=True, tests_run=5, files_changed=2, execution_time=1.2)
        summary = result.summary()

        assert "PASSED" in summary
        assert "Tests run: 5" in summary
        assert "Files changed: 2" in summary
        assert "1.20s" in summary


class TestTestFailure:
    """Test TestFailure class."""

    def test_test_failure_str(self) -> None:
        """Test string representation of TestFailure."""
        failure = TestFailure(
            test_file="test_example.py",
            test_name="test_function",
            error_message="AssertionError: Expected True",
            line=42,
        )

        failure_str = str(failure)
        assert "test_example.py::test_function:42" in failure_str
        assert "AssertionError: Expected True" in failure_str

    def test_test_failure_str_no_line(self) -> None:
        """Test string representation without line number."""
        failure = TestFailure(
            test_file="test_example.py",
            test_name="test_function",
            error_message="Test failed",
        )

        failure_str = str(failure)
        assert "test_example.py::test_function" in failure_str
        assert "Test failed" in failure_str


@pytest.mark.asyncio
async def test_run_pre_commit_tests_factory() -> None:
    """Test run_pre_commit_tests factory function."""
    with patch(
        "forge_harness.quality_gates.pre_commit_tests.PreCommitTestRunner"
    ) as mock_runner_class:
        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(
            return_value=TestResult(passed=True, tests_run=0, files_changed=0)
        )
        mock_runner_class.return_value = mock_runner

        result = await run_pre_commit_tests(check_only=True)

        assert result.passed is True
        mock_runner.run.assert_called_once_with(check_only=True)
