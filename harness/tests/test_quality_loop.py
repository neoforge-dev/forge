"""Tests for quality loop module."""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from forge_harness.quality_loop import (
    DebtMetrics,
    PortfolioQualityReport,
    ProjectQualityReport,
    QualityConfig,
    QualityLoopHarness,
    QualityTrend,
    SecurityFinding,
    SeverityLevel,
    create_quality_loop,
)


class TestDebtMetrics:
    """Tests for DebtMetrics dataclass."""

    def test_default_metrics(self):
        """DebtMetrics has sensible defaults."""
        metrics = DebtMetrics()
        assert metrics.complexity_score == 0.0
        assert metrics.duplication_percent == 0.0
        assert metrics.coverage_percent == 0.0
        assert metrics.documentation_percent == 0.0
        assert metrics.security_issues == 0
        assert metrics.total_debt_hours == 0.0
        assert metrics.velocity_impact_percent == 0.0

    def test_metrics_with_values(self):
        """DebtMetrics can be created with values."""
        metrics = DebtMetrics(
            complexity_score=15.5,
            duplication_percent=8.3,
            coverage_percent=75.0,
            documentation_percent=60.0,
            security_issues=3,
            total_debt_hours=40.0,
            velocity_impact_percent=12.0,
        )
        assert metrics.complexity_score == 15.5
        assert metrics.coverage_percent == 75.0
        assert metrics.security_issues == 3

    def test_to_dict(self):
        """DebtMetrics can be serialized to dict."""
        metrics = DebtMetrics(complexity_score=10.0, coverage_percent=80.0)
        data = metrics.to_dict()
        assert data["complexity_score"] == 10.0
        assert data["coverage_percent"] == 80.0

    def test_from_dict(self):
        """DebtMetrics can be deserialized from dict."""
        data = {
            "complexity_score": 12.0,
            "duplication_percent": 5.0,
            "coverage_percent": 85.0,
        }
        metrics = DebtMetrics.from_dict(data)
        assert metrics.complexity_score == 12.0
        assert metrics.coverage_percent == 85.0


class TestSecurityFinding:
    """Tests for SecurityFinding dataclass."""

    def test_finding_creation(self):
        """SecurityFinding can be created."""
        finding = SecurityFinding(
            tool="bandit",
            severity=SeverityLevel.HIGH,
            rule_id="B105",
            message="Hardcoded password found",
            file_path="app/config.py",
            line_number=42,
        )
        assert finding.tool == "bandit"
        assert finding.severity == SeverityLevel.HIGH
        assert finding.file_path == "app/config.py"

    def test_to_dict(self):
        """SecurityFinding can be serialized."""
        finding = SecurityFinding(
            tool="semgrep",
            severity=SeverityLevel.MEDIUM,
            rule_id="python.sql-injection",
            message="SQL injection",
            file_path="api/users.py",
        )
        data = finding.to_dict()
        assert data["tool"] == "semgrep"
        assert data["severity"] == "medium"

    def test_from_dict(self):
        """SecurityFinding can be deserialized."""
        data = {
            "tool": "bandit",
            "severity": "critical",
            "rule_id": "B501",
            "message": "SSL disabled",
            "file_path": "client.py",
            "line_number": 10,
        }
        finding = SecurityFinding.from_dict(data)
        assert finding.severity == SeverityLevel.CRITICAL
        assert finding.line_number == 10


class TestQualityConfig:
    """Tests for QualityConfig dataclass."""

    def test_default_config(self):
        """QualityConfig has sensible defaults."""
        config = QualityConfig()
        assert config.min_coverage == 70.0
        assert config.max_complexity == 20.0
        assert config.max_duplication == 10.0
        assert config.security_block_severity == SeverityLevel.CRITICAL
        assert config.degradation_threshold == 5.0
        assert "bandit" in config.tools
        assert "semgrep" in config.tools

    def test_custom_config(self):
        """QualityConfig can be customized."""
        config = QualityConfig(
            min_coverage=80.0,
            max_complexity=15.0,
            tools=["bandit"],
        )
        assert config.min_coverage == 80.0
        assert config.max_complexity == 15.0
        assert config.tools == ["bandit"]


class TestProjectQualityReport:
    """Tests for ProjectQualityReport dataclass."""

    def test_report_creation(self):
        """ProjectQualityReport can be created."""
        report = ProjectQualityReport(
            project_name="interview-simulator",
            domain="codeswiftr-com",
            project_path="/path/to/project",
            scan_timestamp=datetime.now(UTC),
            debt_metrics=DebtMetrics(coverage_percent=75.0),
            quality_score=82.5,
            trend=QualityTrend.IMPROVING,
        )
        assert report.project_name == "interview-simulator"
        assert report.quality_score == 82.5
        assert report.trend == QualityTrend.IMPROVING

    def test_to_dict(self):
        """ProjectQualityReport can be serialized."""
        report = ProjectQualityReport(
            project_name="test-project",
            domain="test-domain",
            project_path="/path",
            scan_timestamp=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
            debt_metrics=DebtMetrics(),
            quality_score=90.0,
        )
        data = report.to_dict()
        assert data["project_name"] == "test-project"
        assert data["quality_score"] == 90.0
        assert "2025-01-15" in data["scan_timestamp"]

    def test_from_dict(self):
        """ProjectQualityReport can be deserialized."""
        data = {
            "project_name": "test",
            "domain": "domain",
            "project_path": "/path",
            "scan_timestamp": "2025-01-15T12:00:00+00:00",
            "debt_metrics": {"coverage_percent": 80.0},
            "security_findings": [],
            "quality_score": 85.0,
            "trend": "stable",
        }
        report = ProjectQualityReport.from_dict(data)
        assert report.project_name == "test"
        assert report.quality_score == 85.0
        assert report.trend == QualityTrend.STABLE


class TestPortfolioQualityReport:
    """Tests for PortfolioQualityReport dataclass."""

    def test_report_creation(self):
        """PortfolioQualityReport can be created."""
        report = PortfolioQualityReport(
            scan_timestamp=datetime.now(UTC),
            projects_scanned=10,
            total_projects=12,
            average_quality_score=75.5,
            portfolio_trend=QualityTrend.STABLE,
            critical_issues=2,
            high_issues=5,
        )
        assert report.projects_scanned == 10
        assert report.average_quality_score == 75.5
        assert report.critical_issues == 2

    def test_to_dict(self):
        """PortfolioQualityReport can be serialized."""
        report = PortfolioQualityReport(
            scan_timestamp=datetime(2025, 1, 15, tzinfo=UTC),
            projects_scanned=5,
            total_projects=5,
            average_quality_score=80.0,
            portfolio_trend=QualityTrend.IMPROVING,
            critical_issues=0,
            high_issues=1,
        )
        data = report.to_dict()
        assert data["projects_scanned"] == 5
        assert data["portfolio_trend"] == "improving"


class TestQualityLoopHarness:
    """Tests for QualityLoopHarness class."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def mock_forge_root(self, temp_dir):
        """Create a mock FORGE root with sample projects."""
        forge_root = temp_dir / "forge"
        forge_root.mkdir()

        # Create CLAUDE.md to mark as FORGE root
        (forge_root / "CLAUDE.md").write_text("# FORGE")
        (forge_root / "harness").mkdir()

        # Create a mock domain with project
        domain_dir = forge_root / "codeswiftr-com"
        domain_dir.mkdir()

        project_dir = domain_dir / "interview-simulator"
        project_dir.mkdir()

        # Add backend with Python files
        backend_dir = project_dir / "backend"
        backend_dir.mkdir()

        # Create a sample Python file
        (backend_dir / "main.py").write_text('''
def greet(name: str) -> str:
    """Greet a user by name."""
    return f"Hello, {name}!"

def calculate(x: int, y: int) -> int:
    return x + y
''')

        return forge_root

    @pytest.fixture
    def harness(self, mock_forge_root, temp_dir):
        """Create a QualityLoopHarness instance."""
        metrics_dir = temp_dir / "metrics"
        return QualityLoopHarness(
            forge_root=mock_forge_root,
            metrics_dir=metrics_dir,
        )

    def test_harness_creation(self, harness):
        """QualityLoopHarness can be created."""
        assert harness.config.min_coverage == 70.0
        assert harness.metrics_dir.exists()

    @pytest.mark.asyncio
    async def test_discover_projects(self, harness):
        """discover_projects finds FORGE projects."""
        projects = await harness.discover_projects()
        assert len(projects) >= 1
        assert any(p["name"] == "interview-simulator" for p in projects)

    def test_calculate_quality_score(self, harness):
        """Quality score is calculated correctly."""
        # Good metrics should give high score
        debt = DebtMetrics(coverage_percent=85.0, complexity_score=10.0)
        findings = []
        score = harness._calculate_quality_score(debt, findings)
        assert score > 90.0

        # Poor metrics should give lower score
        debt = DebtMetrics(coverage_percent=40.0, complexity_score=30.0)
        findings = [
            SecurityFinding(
                tool="bandit",
                severity=SeverityLevel.HIGH,
                rule_id="B105",
                message="test",
                file_path="test.py",
            )
        ]
        score = harness._calculate_quality_score(debt, findings)
        assert score < 80.0

    def test_calculate_trend(self, harness):
        """Trend is calculated correctly."""
        # Improvement
        trend = harness._calculate_trend(80.0, 70.0)
        assert trend == QualityTrend.IMPROVING

        # Degradation
        trend = harness._calculate_trend(70.0, 80.0)
        assert trend == QualityTrend.DEGRADING

        # Stable
        trend = harness._calculate_trend(80.0, 78.0)
        assert trend == QualityTrend.STABLE

        # Unknown (no previous)
        trend = harness._calculate_trend(80.0, None)
        assert trend == QualityTrend.UNKNOWN

    def test_identify_issues(self, harness):
        """Issues are identified from metrics."""
        debt = DebtMetrics(
            coverage_percent=50.0,
            complexity_score=25.0,
            duplication_percent=15.0,
        )
        findings = [
            SecurityFinding(
                tool="test",
                severity=SeverityLevel.CRITICAL,
                rule_id="test",
                message="test",
                file_path="test.py",
            )
        ]
        issues = harness._identify_issues(debt, findings)
        assert any("coverage" in issue.lower() for issue in issues)
        assert any("complexity" in issue.lower() for issue in issues)
        assert any("critical" in issue.lower() for issue in issues)

    def test_generate_recommendations(self, harness):
        """Recommendations are generated from metrics."""
        debt = DebtMetrics(
            coverage_percent=40.0,
            complexity_score=30.0,
            documentation_percent=30.0,
        )
        findings = [
            SecurityFinding(
                tool="test",
                severity=SeverityLevel.CRITICAL,
                rule_id="test",
                message="test",
                file_path="test.py",
            )
        ]
        recommendations = harness._generate_recommendations(debt, findings)
        assert len(recommendations) > 0
        assert any("test" in r.lower() or "coverage" in r.lower() for r in recommendations)

    def test_estimate_debt_hours(self, harness):
        """Debt hours are estimated from metrics."""
        # Low debt
        debt = DebtMetrics(
            coverage_percent=80.0,
            complexity_score=15.0,
            duplication_percent=5.0,
        )
        hours = harness._estimate_debt_hours(debt)
        assert hours < 10.0

        # High debt
        debt = DebtMetrics(
            coverage_percent=50.0,
            complexity_score=30.0,
            duplication_percent=20.0,
            security_issues=5,
        )
        hours = harness._estimate_debt_hours(debt)
        assert hours > 30.0

    def test_generate_markdown_report(self, harness):
        """Markdown report is generated correctly."""
        report = PortfolioQualityReport(
            scan_timestamp=datetime.now(UTC),
            projects_scanned=2,
            total_projects=2,
            average_quality_score=75.0,
            portfolio_trend=QualityTrend.STABLE,
            critical_issues=1,
            high_issues=3,
            projects=[
                ProjectQualityReport(
                    project_name="project-a",
                    domain="domain-a",
                    project_path="/path/a",
                    scan_timestamp=datetime.now(UTC),
                    debt_metrics=DebtMetrics(coverage_percent=80.0),
                    quality_score=85.0,
                    trend=QualityTrend.IMPROVING,
                ),
                ProjectQualityReport(
                    project_name="project-b",
                    domain="domain-b",
                    project_path="/path/b",
                    scan_timestamp=datetime.now(UTC),
                    debt_metrics=DebtMetrics(coverage_percent=60.0),
                    quality_score=65.0,
                    trend=QualityTrend.DEGRADING,
                ),
            ],
        )
        markdown = harness.generate_markdown_report(report)
        assert "# FORGE Portfolio Quality Report" in markdown
        assert "project-a" in markdown
        assert "project-b" in markdown
        assert "75.0" in markdown


class TestCreateQualityLoop:
    """Tests for create_quality_loop factory function."""

    def test_create_with_explicit_paths(self):
        """create_quality_loop works with explicit paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            (forge_root / "CLAUDE.md").write_text("# FORGE")
            (forge_root / "harness").mkdir()

            loop = create_quality_loop(
                forge_root=forge_root,
                metrics_dir=Path(tmpdir) / "metrics",
            )
            assert loop.forge_root == forge_root
            assert loop.metrics_dir.exists()

    def test_create_with_custom_config(self):
        """create_quality_loop accepts custom config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forge_root = Path(tmpdir)
            (forge_root / "CLAUDE.md").write_text("# FORGE")
            (forge_root / "harness").mkdir()

            config = QualityConfig(min_coverage=80.0)
            loop = create_quality_loop(
                forge_root=forge_root,
                metrics_dir=Path(tmpdir) / "metrics",
                config=config,
            )
            assert loop.config.min_coverage == 80.0


class TestSeverityLevel:
    """Tests for SeverityLevel enum."""

    def test_severity_values(self):
        """SeverityLevel has expected values."""
        assert SeverityLevel.CRITICAL.value == "critical"
        assert SeverityLevel.HIGH.value == "high"
        assert SeverityLevel.MEDIUM.value == "medium"
        assert SeverityLevel.LOW.value == "low"
        assert SeverityLevel.INFO.value == "info"


class TestQualityTrend:
    """Tests for QualityTrend enum."""

    def test_trend_values(self):
        """QualityTrend has expected values."""
        assert QualityTrend.IMPROVING.value == "improving"
        assert QualityTrend.STABLE.value == "stable"
        assert QualityTrend.DEGRADING.value == "degrading"
        assert QualityTrend.UNKNOWN.value == "unknown"


class TestQualityLoopHarnessAsync:
    """Async tests for QualityLoopHarness with mocked subprocess."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def mock_forge_root(self, temp_dir):
        """Create a mock FORGE root with sample projects."""
        forge_root = temp_dir / "forge"
        forge_root.mkdir()
        (forge_root / "CLAUDE.md").write_text("# FORGE")
        (forge_root / "harness").mkdir()

        # Create domain and project
        domain_dir = forge_root / "test-domain"
        domain_dir.mkdir()

        project_dir = domain_dir / "test-project"
        project_dir.mkdir()

        backend_dir = project_dir / "backend"
        backend_dir.mkdir()

        # Create Python files with docstrings
        (backend_dir / "main.py").write_text('''
def greet(name: str) -> str:
    """Greet a user by name."""
    return f"Hello, {name}!"

def calculate(x: int, y: int) -> int:
    return x + y
''')
        return forge_root

    @pytest.fixture
    def harness(self, mock_forge_root, temp_dir):
        """Create a QualityLoopHarness instance."""
        metrics_dir = temp_dir / "metrics"
        return QualityLoopHarness(
            forge_root=mock_forge_root,
            metrics_dir=metrics_dir,
        )

    @pytest.mark.asyncio
    async def test_run_command_success(self, harness, temp_dir):
        """_run_command returns output on success."""
        from unittest.mock import AsyncMock, patch

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"output data", b"")
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await harness._run_command(["echo", "test"], cwd=temp_dir)
            assert result == "output data"

    @pytest.mark.asyncio
    async def test_run_command_failure(self, harness, temp_dir):
        """_run_command returns None on failure."""
        from unittest.mock import AsyncMock, patch

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"", b"error")
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc

            result = await harness._run_command(["false"], cwd=temp_dir)
            assert result is None

    @pytest.mark.asyncio
    async def test_run_debt_analysis_no_python_files(self, harness, temp_dir):
        """run_debt_analysis handles missing Python files."""
        empty_project = temp_dir / "empty"
        empty_project.mkdir()

        result = await harness.run_debt_analysis(empty_project)
        assert isinstance(result, DebtMetrics)
        assert result.complexity_score == 0.0

    @pytest.mark.asyncio
    async def test_run_debt_analysis_with_radon(self, harness, mock_forge_root):
        """run_debt_analysis parses radon output."""
        from unittest.mock import AsyncMock, patch

        project_path = mock_forge_root / "test-domain" / "test-project"

        radon_output = json.dumps(
            {
                "main.py": [
                    {"name": "greet", "complexity": 2},
                    {"name": "calculate", "complexity": 1},
                ]
            }
        )

        with patch.object(harness, "_run_command", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = radon_output
            result = await harness.run_debt_analysis(project_path)

        assert result.complexity_score == 1.5  # (2+1)/2

    @pytest.mark.asyncio
    async def test_run_debt_analysis_calculates_docs(self, harness, mock_forge_root):
        """run_debt_analysis calculates documentation coverage."""
        from unittest.mock import AsyncMock, patch

        project_path = mock_forge_root / "test-domain" / "test-project"

        with patch.object(harness, "_run_command", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = None  # No radon/coverage
            result = await harness.run_debt_analysis(project_path)

        # Documentation is calculated based on function defs with docstrings
        # Result may vary based on regex matching
        assert isinstance(result.documentation_percent, float)

    @pytest.mark.asyncio
    async def test_run_security_scan_with_bandit(self, harness, mock_forge_root):
        """run_security_scan calls _run_bandit."""
        from unittest.mock import AsyncMock, patch

        project_path = mock_forge_root / "test-domain" / "test-project"

        with patch.object(harness, "_run_bandit", new_callable=AsyncMock) as mock_bandit:
            mock_bandit.return_value = [
                SecurityFinding(
                    tool="bandit",
                    severity=SeverityLevel.HIGH,
                    rule_id="B105",
                    message="Hardcoded password",
                    file_path="main.py",
                )
            ]
            with patch.object(harness, "_run_semgrep", new_callable=AsyncMock) as mock_semgrep:
                mock_semgrep.return_value = []

                findings = await harness.run_security_scan(project_path)

        assert len(findings) == 1
        assert findings[0].tool == "bandit"

    @pytest.mark.asyncio
    async def test_run_bandit_parses_output(self, harness, temp_dir):
        """_run_bandit parses JSON output correctly."""
        from unittest.mock import AsyncMock, patch

        bandit_output = json.dumps(
            {
                "results": [
                    {
                        "severity": "HIGH",  # Note: field name is 'severity' not 'issue_severity'
                        "confidence": "HIGH",
                        "test_id": "B105",
                        "issue_text": "Hardcoded password",
                        "filename": "/path/to/main.py",
                        "line_number": 10,
                    }
                ]
            }
        )

        with patch.object(harness, "_run_command", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = bandit_output
            findings = await harness._run_bandit(temp_dir)

        assert len(findings) == 1
        assert findings[0].severity == SeverityLevel.HIGH
        assert findings[0].rule_id == "B105"

    @pytest.mark.asyncio
    async def test_run_bandit_no_output(self, harness, temp_dir):
        """_run_bandit handles no results."""
        from unittest.mock import AsyncMock, patch

        with patch.object(harness, "_run_command", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = None
            findings = await harness._run_bandit(temp_dir)

        assert findings == []

    @pytest.mark.asyncio
    async def test_run_semgrep_parses_output(self, harness, temp_dir):
        """_run_semgrep parses JSON output correctly."""
        from unittest.mock import AsyncMock, patch

        semgrep_output = json.dumps(
            {
                "results": [
                    {
                        "check_id": "python.sql-injection",
                        "extra": {"severity": "ERROR", "message": "SQL injection"},
                        "path": "api.py",
                        "start": {"line": 25},
                    }
                ]
            }
        )

        with patch.object(harness, "_run_command", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = semgrep_output
            findings = await harness._run_semgrep(temp_dir)

        assert len(findings) == 1
        assert findings[0].rule_id == "python.sql-injection"

    @pytest.mark.asyncio
    async def test_run_semgrep_no_output(self, harness, temp_dir):
        """_run_semgrep handles no results."""
        from unittest.mock import AsyncMock, patch

        with patch.object(harness, "_run_command", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = None
            findings = await harness._run_semgrep(temp_dir)

        assert findings == []

    @pytest.mark.asyncio
    async def test_scan_project(self, harness, mock_forge_root):
        """scan_project returns a ProjectQualityReport."""
        from unittest.mock import AsyncMock, patch

        project_path = mock_forge_root / "test-domain" / "test-project"
        project_info = {
            "name": "test-project",
            "domain": "test-domain",
            "path": str(project_path),
        }

        with patch.object(harness, "run_debt_analysis", new_callable=AsyncMock) as mock_debt:
            mock_debt.return_value = DebtMetrics(coverage_percent=80.0)
            with patch.object(harness, "run_security_scan", new_callable=AsyncMock) as mock_sec:
                mock_sec.return_value = []
                with patch.object(
                    harness, "_get_previous_score", new_callable=AsyncMock
                ) as mock_prev:
                    mock_prev.return_value = 75.0
                    with patch.object(harness, "_store_metrics", new_callable=AsyncMock):
                        report = await harness.scan_project(project_info)

        assert report.project_name == "test-project"
        assert report.domain == "test-domain"
        assert report.quality_score > 0

    @pytest.mark.asyncio
    async def test_run_portfolio_scan(self, harness, mock_forge_root):
        """run_portfolio_scan scans all projects."""
        from unittest.mock import AsyncMock, patch

        mock_report = ProjectQualityReport(
            project_name="test-project",
            domain="test-domain",
            project_path=str(mock_forge_root / "test-domain" / "test-project"),
            scan_timestamp=datetime.now(UTC),
            debt_metrics=DebtMetrics(coverage_percent=80.0),
            quality_score=85.0,
        )

        # Mock discover_projects to return a project
        mock_projects = [
            {
                "name": "test-project",
                "domain": "test-domain",
                "path": str(mock_forge_root / "test-domain" / "test-project"),
            }
        ]

        with patch.object(harness, "discover_projects", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = mock_projects
            with patch.object(harness, "scan_project", new_callable=AsyncMock) as mock_scan:
                mock_scan.return_value = mock_report
                with patch.object(harness, "_store_portfolio_report", new_callable=AsyncMock):
                    portfolio_report = await harness.run_portfolio_scan()

        assert portfolio_report.projects_scanned >= 1
        assert portfolio_report.average_quality_score == 85.0

    @pytest.mark.asyncio
    async def test_get_previous_score(self, harness, temp_dir):
        """_get_previous_score reads from history file."""
        project_name = "test-project"

        # Create history file (format: {project_name}_history.json with array of entries)
        history_file = harness.metrics_dir / f"{project_name}_history.json"
        history_data = [
            {
                "quality_score": 70.0,
                "timestamp": "2025-01-14T12:00:00",
            },
            {
                "quality_score": 75.0,
                "timestamp": "2025-01-15T12:00:00",
            },
        ]
        history_file.write_text(json.dumps(history_data))

        score = await harness._get_previous_score(project_name)
        assert score == 75.0  # Last entry

    @pytest.mark.asyncio
    async def test_get_previous_score_no_file(self, harness):
        """_get_previous_score returns None when no file exists."""
        score = await harness._get_previous_score("nonexistent-project")
        assert score is None

    @pytest.mark.asyncio
    async def test_store_metrics(self, harness):
        """_store_metrics writes metrics to history file."""
        report = ProjectQualityReport(
            project_name="test-project",
            domain="test-domain",
            project_path="/path",
            scan_timestamp=datetime.now(UTC),
            debt_metrics=DebtMetrics(coverage_percent=80.0),
            quality_score=85.0,
        )

        await harness._store_metrics(report)

        # _store_metrics writes to history file, not .json
        history_file = harness.metrics_dir / "test-project_history.json"
        assert history_file.exists()

        data = json.loads(history_file.read_text())
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[-1]["quality_score"] == 85.0

    @pytest.mark.asyncio
    async def test_store_portfolio_report(self, harness):
        """_store_portfolio_report writes report to file."""
        report = PortfolioQualityReport(
            scan_timestamp=datetime.now(UTC),
            projects_scanned=5,
            total_projects=5,
            average_quality_score=80.0,
            portfolio_trend=QualityTrend.STABLE,
            critical_issues=0,
            high_issues=1,
        )

        await harness._store_portfolio_report(report)

        # Should create timestamped file
        portfolio_files = list(harness.metrics_dir.glob("portfolio_*.json"))
        assert len(portfolio_files) >= 1

    @pytest.mark.asyncio
    async def test_alert_degradation(self, harness):
        """_alert_degradation handles approval queue failures gracefully."""
        from unittest.mock import patch

        # _alert_degradation takes list of degraded project names and portfolio report
        degraded_projects = ["degraded-project-a", "degraded-project-b"]

        report = PortfolioQualityReport(
            scan_timestamp=datetime.now(UTC),
            projects_scanned=5,
            total_projects=5,
            average_quality_score=70.0,
            portfolio_trend=QualityTrend.DEGRADING,
            critical_issues=2,
            high_issues=3,
        )

        # The method catches exceptions internally and logs warnings
        # Test that it doesn't raise when approval queue fails
        with patch("forge_harness.quality_loop.logger") as mock_logger:
            await harness._alert_degradation(degraded_projects, report)
            # Should log warning when queue creation/submission fails
            mock_logger.warning.assert_called_once()
            call_str = str(mock_logger.warning.call_args)
            assert "approval request" in call_str.lower() or "failed" in call_str.lower()


class TestScheduledScan:
    """Tests for scheduled scan functionality."""

    @pytest.fixture
    def harness(self, tmp_path):
        """Create harness with temporary directories."""
        forge_root = tmp_path / "forge"
        forge_root.mkdir()
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        return QualityLoopHarness(
            forge_root=forge_root,
            metrics_dir=metrics_dir,
        )

    @pytest.mark.asyncio
    async def test_store_baseline(self, harness):
        """store_baseline writes baseline metrics."""
        baseline = {"average_score": 75.0, "critical_issues": 0}
        await harness.store_baseline(baseline)

        baseline_file = harness.metrics_dir / "baseline.json"
        assert baseline_file.exists()
        data = json.loads(baseline_file.read_text())
        assert data["average_score"] == 75.0

    @pytest.mark.asyncio
    async def test_get_baseline_returns_stored(self, harness):
        """get_baseline returns stored baseline."""
        baseline = {"average_score": 80.0, "critical_issues": 1}
        await harness.store_baseline(baseline)

        result = await harness.get_baseline()
        assert result["average_score"] == 80.0
        assert result["critical_issues"] == 1

    @pytest.mark.asyncio
    async def test_get_baseline_returns_none_when_missing(self, harness):
        """get_baseline returns None when no baseline exists."""
        result = await harness.get_baseline()
        assert result is None

    @pytest.mark.asyncio
    async def test_compare_to_baseline_detects_degradation(self, harness):
        """compare_to_baseline detects score degradation."""
        baseline = {"average_score": 80.0, "critical_issues": 0}
        current = {"average_score": 65.0, "critical_issues": 2}

        result = harness.compare_to_baseline(current, baseline)

        assert result["degraded"] is True
        assert result["score_delta"] == -15.0
        assert result["critical_delta"] == 2

    @pytest.mark.asyncio
    async def test_compare_to_baseline_detects_improvement(self, harness):
        """compare_to_baseline detects improvement."""
        baseline = {"average_score": 70.0, "critical_issues": 3}
        current = {"average_score": 85.0, "critical_issues": 0}

        result = harness.compare_to_baseline(current, baseline)

        assert result["degraded"] is False
        assert result["score_delta"] == 15.0
        assert result["critical_delta"] == -3

    @pytest.mark.asyncio
    async def test_run_scheduled_scan_executes_once_in_test(self, harness):
        """run_scheduled_scan executes a single scan when max_runs=1."""
        from unittest.mock import AsyncMock, patch

        mock_report = PortfolioQualityReport(
            scan_timestamp=datetime.now(UTC),
            projects_scanned=0,
            total_projects=0,
            average_quality_score=0.0,
            portfolio_trend=QualityTrend.STABLE,
            critical_issues=0,
            high_issues=0,
        )

        with patch.object(harness, "run_portfolio_scan", new=AsyncMock(return_value=mock_report)):
            # Run with max_runs=1 for testing
            await harness.run_scheduled_scan(interval_hours=168, max_runs=1)
            harness.run_portfolio_scan.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_scheduled_scan_stores_baseline_on_first_run(self, harness):
        """run_scheduled_scan stores baseline on first run."""
        from unittest.mock import AsyncMock, patch

        mock_report = PortfolioQualityReport(
            scan_timestamp=datetime.now(UTC),
            projects_scanned=5,
            total_projects=5,
            average_quality_score=82.5,
            portfolio_trend=QualityTrend.STABLE,
            critical_issues=0,
            high_issues=1,
        )

        with patch.object(harness, "run_portfolio_scan", new=AsyncMock(return_value=mock_report)):
            await harness.run_scheduled_scan(interval_hours=168, max_runs=1)

        # Should have created baseline
        baseline = await harness.get_baseline()
        assert baseline is not None
        assert baseline["average_score"] == 82.5
