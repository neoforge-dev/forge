"""Tests for iOS test harness."""

from unittest.mock import AsyncMock, patch

import pytest

from forge_harness.ios_harness.test_harness import (
    TestCase,
    TestConfig,
    TestHarness,
    TestResult,
    TestSuite,
)


@pytest.fixture
def project_dir(tmp_path):
    """Create temporary iOS project directory."""
    project = tmp_path / "TestApp"
    project.mkdir()

    # Create .xcodeproj
    xcodeproj = project / "TestApp.xcodeproj"
    xcodeproj.mkdir()
    (xcodeproj / "project.pbxproj").write_text("// Xcode project")

    return project


@pytest.fixture
def test_harness(project_dir):
    """Create TestHarness instance."""
    return TestHarness(project_dir)


class TestTestHarness:
    """Test TestHarness class."""

    def test_init_validates_project_exists(self, tmp_path):
        """Should raise error if project doesn't exist."""
        with pytest.raises(ValueError, match="Project path does not exist"):
            TestHarness(tmp_path / "nonexistent")

    def test_init_validates_xcodeproj_exists(self, tmp_path):
        """Should raise error if no .xcodeproj found."""
        project = tmp_path / "TestApp"
        project.mkdir()

        with pytest.raises(ValueError, match="No .xcodeproj found"):
            TestHarness(project)

    def test_init_finds_xcodeproj(self, project_dir):
        """Should find and store .xcodeproj path."""
        harness = TestHarness(project_dir)

        assert harness.xcodeproj_path == project_dir / "TestApp.xcodeproj"

    @pytest.mark.asyncio
    async def test_run_tests_success(self, test_harness):
        """Should run tests successfully."""
        config = TestConfig(scheme="TestApp")

        # Mock subprocess
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(
                b"""Test Case '-[TestAppTests.AuthTests testLogin]' passed (0.123 seconds).
Test Case '-[TestAppTests.AuthTests testLogout]' passed (0.045 seconds).
** TEST SUCCEEDED **
""",
                b"",
            )
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await test_harness.test(config)

            assert result.success is True
            assert result.exit_code == 0
            assert result.total_tests == 2
            assert result.passed_tests == 2
            assert result.failed_tests == 0

    @pytest.mark.asyncio
    async def test_run_tests_with_failures(self, test_harness):
        """Should handle test failures."""
        config = TestConfig(scheme="TestApp")

        # Mock subprocess with failures
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(
                b"""Test Case '-[TestAppTests.AuthTests testLogin]' passed (0.123 seconds).
Test Case '-[TestAppTests.AuthTests testLogout]' failed (0.045 seconds).
** TEST FAILED **
""",
                b"",
            )
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await test_harness.test(config)

            assert result.success is False
            assert result.exit_code == 1
            assert result.total_tests == 2
            assert result.passed_tests == 1
            assert result.failed_tests == 1

    @pytest.mark.asyncio
    async def test_run_tests_xcodebuild_not_found(self, test_harness):
        """Should handle xcodebuild not being installed."""
        config = TestConfig(scheme="TestApp")

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
            result = await test_harness.test(config)

            assert result.success is False
            assert "xcodebuild not found" in result.stderr

    def test_build_test_command_basic(self, test_harness):
        """Should construct basic test command."""
        config = TestConfig(
            scheme="TestApp",
            enable_code_coverage=False,
            parallel_testing=False,
        )

        cmd = test_harness._build_test_command(config)

        assert cmd[0] == "xcodebuild"
        assert "test" in cmd
        assert "-scheme" in cmd
        assert "TestApp" in cmd

    def test_build_test_command_with_coverage(self, test_harness):
        """Should enable code coverage."""
        config = TestConfig(scheme="TestApp", enable_code_coverage=True)

        cmd = test_harness._build_test_command(config)

        assert "-enableCodeCoverage" in cmd
        assert "YES" in cmd

    def test_build_test_command_with_parallel(self, test_harness):
        """Should enable parallel testing."""
        config = TestConfig(scheme="TestApp", parallel_testing=True)

        cmd = test_harness._build_test_command(config)

        assert "-parallel-testing-enabled" in cmd
        assert "YES" in cmd

    def test_parse_test_output_passed(self, test_harness):
        """Should parse passed tests."""
        output = """
Test Case '-[TestAppTests.AuthTests testLogin]' passed (0.123 seconds).
Test Case '-[TestAppTests.UserTests testProfile]' passed (0.056 seconds).
        """

        suites = test_harness._parse_test_output(output)

        assert len(suites) == 2  # AuthTests, UserTests
        auth_suite = next(s for s in suites if s.name == "AuthTests")
        assert auth_suite.total_count == 1
        assert auth_suite.pass_count == 1
        assert auth_suite.passed is True

    def test_parse_test_output_failed(self, test_harness):
        """Should parse failed tests."""
        output = """
Test Case '-[TestAppTests.AuthTests testLogin]' failed (0.045 seconds).
        """

        suites = test_harness._parse_test_output(output)

        assert len(suites) == 1
        assert suites[0].total_count == 1
        assert suites[0].fail_count == 1
        assert suites[0].passed is False

    def test_parse_test_output_mixed(self, test_harness):
        """Should parse mixed pass/fail tests."""
        output = """
Test Case '-[TestAppTests.AuthTests testLogin]' passed (0.123 seconds).
Test Case '-[TestAppTests.AuthTests testLogout]' failed (0.045 seconds).
Test Case '-[TestAppTests.AuthTests testRefresh]' passed (0.089 seconds).
        """

        suites = test_harness._parse_test_output(output)

        assert len(suites) == 1
        suite = suites[0]
        assert suite.name == "AuthTests"
        assert suite.total_count == 3
        assert suite.pass_count == 2
        assert suite.fail_count == 1
        assert suite.passed is False  # Suite fails if any test fails

    @pytest.mark.asyncio
    async def test_list_test_targets(self, test_harness):
        """Should list test targets."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(
                b'{"project": {"targets": ["TestApp", "TestAppTests", "TestAppUITests"]}}',
                b"",
            )
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            targets = await test_harness.list_test_targets()

            assert len(targets) == 2
            assert "TestAppTests" in targets
            assert "TestAppUITests" in targets
            assert "TestApp" not in targets  # Main target, not test


class TestTestSuite:
    """Test TestSuite dataclass."""

    def test_pass_count(self):
        """Should count passed tests."""
        suite = TestSuite(
            name="AuthTests",
            test_cases=[
                TestCase("testLogin", "AuthTests", True, 0.1),
                TestCase("testLogout", "AuthTests", True, 0.2),
                TestCase("testRefresh", "AuthTests", False, 0.3),
            ],
        )

        assert suite.pass_count == 2

    def test_fail_count(self):
        """Should count failed tests."""
        suite = TestSuite(
            name="AuthTests",
            test_cases=[
                TestCase("testLogin", "AuthTests", True, 0.1),
                TestCase("testLogout", "AuthTests", False, 0.2),
            ],
        )

        assert suite.fail_count == 1

    def test_total_count(self):
        """Should count total tests."""
        suite = TestSuite(
            name="AuthTests",
            test_cases=[
                TestCase("test1", "AuthTests", True, 0.1),
                TestCase("test2", "AuthTests", False, 0.2),
                TestCase("test3", "AuthTests", True, 0.3),
            ],
        )

        assert suite.total_count == 3


class TestTestResult:
    """Test TestResult dataclass."""

    def test_total_tests(self):
        """Should count total tests across all suites."""
        result = TestResult(
            success=True,
            test_suites=[
                TestSuite(
                    "Suite1",
                    [
                        TestCase("test1", "Suite1", True, 0.1),
                        TestCase("test2", "Suite1", True, 0.2),
                    ],
                ),
                TestSuite("Suite2", [TestCase("test3", "Suite2", True, 0.1)]),
            ],
        )

        assert result.total_tests == 3

    def test_passed_tests(self):
        """Should count passed tests across all suites."""
        result = TestResult(
            success=False,
            test_suites=[
                TestSuite(
                    "Suite1",
                    [
                        TestCase("test1", "Suite1", True, 0.1),
                        TestCase("test2", "Suite1", False, 0.2),
                    ],
                ),
            ],
        )

        assert result.passed_tests == 1

    def test_failed_tests(self):
        """Should count failed tests across all suites."""
        result = TestResult(
            success=False,
            test_suites=[
                TestSuite(
                    "Suite1",
                    [
                        TestCase("test1", "Suite1", True, 0.1),
                        TestCase("test2", "Suite1", False, 0.2),
                        TestCase("test3", "Suite1", False, 0.3),
                    ],
                ),
            ],
        )

        assert result.failed_tests == 2

    def test_pass_rate(self):
        """Should calculate pass rate percentage."""
        result = TestResult(
            success=False,
            test_suites=[
                TestSuite(
                    "Suite1",
                    [
                        TestCase("test1", "Suite1", True, 0.1),
                        TestCase("test2", "Suite1", True, 0.2),
                        TestCase("test3", "Suite1", False, 0.3),
                        TestCase("test4", "Suite1", True, 0.4),
                    ],
                ),
            ],
        )

        assert result.pass_rate == 75.0  # 3 out of 4
