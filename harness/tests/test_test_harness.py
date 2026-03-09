"""Tests for coverage harness module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.test_harness import (
    CoverageGap,
    CoverageHarness,
    CoverageReport,
    GeneratedTestResult,
    QualityReport,
    create_coverage_harness,
)


class TestCoverageGap:
    """Tests for CoverageGap dataclass."""

    def test_gap_creation(self):
        """CoverageGap can be created with required fields."""
        gap = CoverageGap(
            file_path="app/services/user.py",
            coverage_percent=45.0,
            missing_lines=[10, 11, 15, 20],
        )
        assert gap.file_path == "app/services/user.py"
        assert gap.coverage_percent == 45.0
        assert len(gap.missing_lines) == 4

    def test_gap_severity_critical(self):
        """Gap with <50% coverage is critical."""
        gap = CoverageGap(
            file_path="test.py",
            coverage_percent=30.0,
            missing_lines=[1, 2, 3],
        )
        assert gap.gap_severity == "critical"

    def test_gap_severity_high(self):
        """Gap with 50-70% coverage is high."""
        gap = CoverageGap(
            file_path="test.py",
            coverage_percent=60.0,
            missing_lines=[1, 2],
        )
        assert gap.gap_severity == "high"

    def test_gap_severity_medium(self):
        """Gap with 70-80% coverage is medium."""
        gap = CoverageGap(
            file_path="test.py",
            coverage_percent=75.0,
            missing_lines=[1],
        )
        assert gap.gap_severity == "medium"

    def test_gap_severity_low(self):
        """Gap with >=80% coverage is low."""
        gap = CoverageGap(
            file_path="test.py",
            coverage_percent=85.0,
            missing_lines=[],
        )
        assert gap.gap_severity == "low"


class TestCoverageReport:
    """Tests for CoverageReport dataclass."""

    def test_report_creation(self):
        """CoverageReport can be created."""
        gaps = [
            CoverageGap(file_path="a.py", coverage_percent=50.0, missing_lines=[]),
        ]
        report = CoverageReport(
            total_coverage=75.0,
            files_analyzed=10,
            gaps=gaps,
            recommendations=["Add more tests"],
        )
        assert report.total_coverage == 75.0
        assert report.files_analyzed == 10
        assert len(report.gaps) == 1


class TestGeneratedTestResult:
    """Tests for GeneratedTestResult dataclass."""

    def test_result_creation(self):
        """GeneratedTestResult can be created."""
        result = GeneratedTestResult(
            file_path="app/services/user.py",
            tests_generated=5,
            test_code="def test_foo(): pass",
            estimated_coverage_improvement=25.0,
        )
        assert result.tests_generated == 5
        assert "def test_foo" in result.test_code

    def test_result_with_errors(self):
        """GeneratedTestResult can include errors."""
        result = GeneratedTestResult(
            file_path="missing.py",
            tests_generated=0,
            test_code="",
            estimated_coverage_improvement=0.0,
            errors=["File not found"],
        )
        assert len(result.errors) == 1


class TestQualityReportClass:
    """Tests for QualityReport dataclass."""

    def test_quality_report_creation(self):
        """QualityReport can be created."""
        report = QualityReport(
            total_tests=100,
            passing_tests=95,
            failing_tests=5,
            test_duration_seconds=10.5,
            coverage_percent=80.0,
            quality_score=75.0,
            issues=["5 failing tests"],
        )
        assert report.total_tests == 100
        assert report.quality_score == 75.0


class TestCoverageHarness:
    """Tests for CoverageHarness class."""

    @pytest.fixture
    def mock_project(self, tmp_path):
        """Create mock project structure."""
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "app").mkdir()
        (backend / "tests").mkdir()

        # Create sample source file
        service_file = backend / "app" / "services" / "user.py"
        service_file.parent.mkdir(parents=True, exist_ok=True)
        service_file.write_text("""
class UserService:
    def get_user(self, user_id: int):
        return {"id": user_id}

    def create_user(self, data: dict):
        return {"id": 1, **data}

def validate_email(email: str) -> bool:
    return "@" in email
""")

        return tmp_path

    @pytest.fixture
    def harness(self, mock_project):
        """Create CoverageHarness instance."""
        return CoverageHarness(project_dir=mock_project)

    def test_harness_initialization(self, harness, mock_project):
        """CoverageHarness initializes correctly."""
        assert harness.project_dir == mock_project
        assert harness.target_coverage == 80.0

    def test_harness_with_custom_target(self, mock_project):
        """CoverageHarness accepts custom coverage target."""
        harness = CoverageHarness(
            project_dir=mock_project,
            target_coverage=90.0,
        )
        assert harness.target_coverage == 90.0

    def test_coverage_targets_defined(self, harness):
        """Coverage targets are defined for different file types."""
        targets = harness.COVERAGE_TARGETS
        assert "api" in targets
        assert "services" in targets
        assert "default" in targets

    def test_get_coverage_target(self, harness):
        """_get_coverage_target returns correct target."""
        assert harness._get_coverage_target("app/api/v1/users.py") == 85.0
        assert harness._get_coverage_target("app/services/user.py") == 80.0
        assert harness._get_coverage_target("app/utils/helpers.py") == 75.0
        assert harness._get_coverage_target("app/random.py") == 70.0

    def test_extract_functions(self, harness):
        """_extract_functions finds function definitions."""
        source = """
def public_func():
    pass

def another_func(arg):
    return arg

def _private_func():
    pass

async def async_func():
    pass
"""
        functions = harness._extract_functions(source)
        assert "public_func" in functions
        assert "another_func" in functions
        assert "async_func" in functions
        assert "_private_func" not in functions  # Private excluded

    def test_extract_classes(self, harness):
        """_extract_classes finds class definitions."""
        source = """
class UserService:
    pass

class OrderHandler(BaseHandler):
    pass
"""
        classes = harness._extract_classes(source)
        assert "UserService" in classes
        assert "OrderHandler" in classes

    @pytest.mark.asyncio
    async def test_generate_tests_for_file(self, harness, mock_project):
        """generate_tests_for_file creates test stubs."""
        file_path = mock_project / "backend" / "app" / "services" / "user.py"
        result = await harness.generate_tests_for_file(file_path)

        assert isinstance(result, GeneratedTestResult)
        assert result.tests_generated > 0
        assert "def test_" in result.test_code
        assert "UserService" in result.test_code

    @pytest.mark.asyncio
    async def test_generate_tests_file_not_found(self, harness, mock_project):
        """generate_tests_for_file handles missing file."""
        file_path = mock_project / "nonexistent.py"
        result = await harness.generate_tests_for_file(file_path)

        assert result.tests_generated == 0
        assert len(result.errors) > 0
        assert "not found" in result.errors[0].lower()

    def test_generate_test_stubs(self, harness, mock_project):
        """_generate_test_stubs creates proper pytest code."""
        file_path = mock_project / "backend" / "app" / "example.py"

        test_code = harness._generate_test_stubs(
            file_path=file_path,
            functions=["process_data", "validate_input"],
            classes=["DataProcessor"],
            missing_lines=None,
        )

        # Check structure
        assert "import pytest" in test_code
        assert "class TestDataProcessor:" in test_code
        assert "def test_process_data" in test_code
        assert "@pytest.fixture" in test_code

    def test_generate_recommendations(self, harness):
        """_generate_recommendations creates actionable advice."""
        gaps = [
            CoverageGap(file_path="critical.py", coverage_percent=30.0, missing_lines=[]),
            CoverageGap(file_path="services/user.py", coverage_percent=55.0, missing_lines=[]),
        ]

        recommendations = harness._generate_recommendations(gaps, total_coverage=60.0)

        assert len(recommendations) > 0
        assert any("critical" in r.lower() for r in recommendations)

    def test_calculate_quality_score(self, harness):
        """_calculate_quality_score computes correct score."""
        # Perfect passing, good coverage, no failures
        score = harness._calculate_quality_score(
            passing_ratio=1.0,
            coverage=80.0,
            failing=0,
        )
        assert score == 72.0  # 40 + 32

        # All passing, low coverage
        score = harness._calculate_quality_score(
            passing_ratio=1.0,
            coverage=50.0,
            failing=0,
        )
        assert score == 60.0  # 40 + 20

        # Some failures
        score = harness._calculate_quality_score(
            passing_ratio=0.9,
            coverage=80.0,
            failing=2,
        )
        assert score == 58.0  # 36 + 32 - 10

    @pytest.mark.asyncio
    async def test_analyze_coverage_gaps_no_backend(self, tmp_path):
        """analyze_coverage_gaps handles missing backend."""
        harness = CoverageHarness(project_dir=tmp_path)
        report = await harness.analyze_coverage_gaps()

        assert report.total_coverage == 0.0
        assert report.files_analyzed == 0

    @pytest.mark.asyncio
    async def test_assess_test_quality_no_backend(self, tmp_path):
        """assess_test_quality handles missing backend."""
        harness = CoverageHarness(project_dir=tmp_path)
        report = await harness.assess_test_quality()

        assert report.total_tests == 0
        assert "not found" in report.issues[0].lower()


class TestCreateCoverageHarness:
    """Tests for factory function."""

    def test_create_coverage_harness(self, tmp_path):
        """Factory creates harness instance."""
        harness = create_coverage_harness(project_dir=tmp_path)

        assert isinstance(harness, CoverageHarness)
        assert harness.project_dir == tmp_path
        assert harness.target_coverage == 80.0

    def test_create_coverage_harness_custom_target(self, tmp_path):
        """Factory accepts custom target."""
        harness = create_coverage_harness(
            project_dir=tmp_path,
            target_coverage=90.0,
        )

        assert harness.target_coverage == 90.0


class TestCoverageHarnessWithMocks:
    """Tests for CoverageHarness with mocked subprocess."""

    @pytest.fixture
    def mock_project_with_backend(self, tmp_path):
        """Create mock project with backend dir."""
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "app").mkdir()

        # Create source file with low coverage
        service_file = backend / "app" / "services" / "user.py"
        service_file.parent.mkdir(parents=True, exist_ok=True)
        service_file.write_text("""
def get_user(user_id: int):
    return {"id": user_id}

def create_user(data: dict):
    return {"id": 1, **data}
""")
        return tmp_path

    def test_run_coverage_with_coverage_json(self, mock_project_with_backend):
        """_run_coverage parses coverage.json correctly."""
        harness = CoverageHarness(project_dir=mock_project_with_backend)
        backend = mock_project_with_backend / "backend"

        # Create coverage.json file
        cov_data = {
            "totals": {"percent_covered": 75.5},
            "files": {
                "app/services/user.py": {
                    "summary": {"percent_covered": 60.0},
                    "missing_lines": [10, 11, 12],
                }
            },
        }
        (backend / "coverage.json").write_text(json.dumps(cov_data))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = harness._run_coverage()

        assert result is not None
        assert result["totals"]["percent_covered"] == 75.5

    def test_run_coverage_with_invalid_json(self, mock_project_with_backend):
        """_run_coverage handles invalid JSON gracefully."""
        harness = CoverageHarness(project_dir=mock_project_with_backend)
        backend = mock_project_with_backend / "backend"

        # Create invalid JSON file
        (backend / "coverage.json").write_text("not valid json{{{")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = harness._run_coverage()

        assert result is None

    def test_run_coverage_no_coverage_file(self, mock_project_with_backend):
        """_run_coverage returns None when coverage.json doesn't exist."""
        harness = CoverageHarness(project_dir=mock_project_with_backend)
        backend = mock_project_with_backend / "backend"

        # Make sure no coverage.json exists
        cov_file = backend / "coverage.json"
        if cov_file.exists():
            cov_file.unlink()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = harness._run_coverage()

        assert result is None

    @pytest.mark.asyncio
    async def test_analyze_coverage_gaps_with_data(self, mock_project_with_backend):
        """analyze_coverage_gaps processes coverage data correctly."""
        harness = CoverageHarness(project_dir=mock_project_with_backend)
        backend = mock_project_with_backend / "backend"

        # Create coverage.json with gap below target
        cov_data = {
            "totals": {"percent_covered": 65.0},
            "files": {
                "app/api/v1/users.py": {
                    "summary": {"percent_covered": 50.0},
                    "missing_lines": [10, 11, 12, 13, 14],
                },
                "app/services/user.py": {
                    "summary": {"percent_covered": 90.0},
                    "missing_lines": [],
                },
            },
        }
        (backend / "coverage.json").write_text(json.dumps(cov_data))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            report = await harness.analyze_coverage_gaps()

        assert report.total_coverage == 65.0
        assert report.files_analyzed == 2
        # api file has 50% < 85% target, so it should be in gaps
        assert len(report.gaps) >= 1
        api_gap = next((g for g in report.gaps if "api" in g.file_path), None)
        assert api_gap is not None
        assert api_gap.coverage_percent == 50.0

    @pytest.mark.asyncio
    async def test_assess_test_quality_with_results(self, mock_project_with_backend):
        """assess_test_quality parses test output correctly."""
        harness = CoverageHarness(project_dir=mock_project_with_backend)
        backend = mock_project_with_backend / "backend"

        # Create coverage.json
        cov_data = {"totals": {"percent_covered": 80.0}}
        (backend / "coverage.json").write_text(json.dumps(cov_data))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="10 passed 2 failed in 5.0s",
                stderr="",
            )
            report = await harness.assess_test_quality()

        assert report.total_tests == 2  # "passed" + "failed" counted
        assert report.coverage_percent == 80.0

    @pytest.mark.asyncio
    async def test_assess_test_quality_low_coverage_issue(self, mock_project_with_backend):
        """assess_test_quality flags low coverage as issue."""
        harness = CoverageHarness(project_dir=mock_project_with_backend)
        backend = mock_project_with_backend / "backend"

        # Create coverage.json with low coverage
        cov_data = {"totals": {"percent_covered": 50.0}}
        (backend / "coverage.json").write_text(json.dumps(cov_data))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="5 passed in 1.0s",
                stderr="",
            )
            report = await harness.assess_test_quality()

        assert report.coverage_percent == 50.0
        # Should flag low coverage
        assert any("below" in issue.lower() or "50" in issue for issue in report.issues)

    @pytest.mark.asyncio
    async def test_assess_test_quality_with_failures(self, mock_project_with_backend):
        """assess_test_quality flags failing tests as issue."""
        harness = CoverageHarness(project_dir=mock_project_with_backend)
        backend = mock_project_with_backend / "backend"

        cov_data = {"totals": {"percent_covered": 80.0}}
        (backend / "coverage.json").write_text(json.dumps(cov_data))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="5 passed 3 failed in 2.0s",
                stderr="",
            )
            report = await harness.assess_test_quality()

        assert report.failing_tests > 0
        # Should flag failing tests
        assert any("failing" in issue.lower() for issue in report.issues)

    @pytest.mark.asyncio
    async def test_get_untested_functions(self, mock_project_with_backend):
        """get_untested_functions returns functions with low coverage."""
        harness = CoverageHarness(project_dir=mock_project_with_backend)
        backend = mock_project_with_backend / "backend"

        # Create a source file with functions
        service_file = backend / "app" / "services" / "user.py"
        service_file.parent.mkdir(parents=True, exist_ok=True)
        service_file.write_text("""
def get_user(user_id: int):
    return {"id": user_id}

def create_user(data: dict):
    return {"id": 1, **data}
""")

        # Create coverage.json with low coverage
        cov_data = {
            "totals": {"percent_covered": 40.0},
            "files": {
                "app/services/user.py": {
                    "summary": {"percent_covered": 30.0},
                    "missing_lines": [2, 3, 4, 5, 6, 7, 8],
                }
            },
        }
        (backend / "coverage.json").write_text(json.dumps(cov_data))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            untested = await harness.get_untested_functions()

        assert len(untested) >= 1
        user_file = next((u for u in untested if "user.py" in u["file"]), None)
        assert user_file is not None
        assert user_file["coverage"] == 30.0
        assert "get_user" in user_file["functions"] or "create_user" in user_file["functions"]

    @pytest.mark.asyncio
    async def test_get_untested_functions_no_gaps(self, mock_project_with_backend):
        """get_untested_functions returns empty when coverage is good."""
        harness = CoverageHarness(project_dir=mock_project_with_backend)
        backend = mock_project_with_backend / "backend"

        # Create coverage.json with high coverage
        cov_data = {
            "totals": {"percent_covered": 90.0},
            "files": {
                "app/services/user.py": {
                    "summary": {"percent_covered": 95.0},
                    "missing_lines": [],
                }
            },
        }
        (backend / "coverage.json").write_text(json.dumps(cov_data))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            untested = await harness.get_untested_functions()

        # All files above 50%, so no untested functions
        assert len(untested) == 0
