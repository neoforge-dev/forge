"""Tests for pre-flight checklist module."""

from unittest.mock import MagicMock, patch

import pytest

from forge_harness.preflight import PreflightChecker, PreflightResult


class TestPreflightResult:
    """Tests for PreflightResult dataclass."""

    def test_result_passed(self):
        """PreflightResult can indicate passed status."""
        result = PreflightResult(
            passed=True,
            issues=[],
            fixes_applied=[],
        )
        assert result.passed is True
        assert len(result.issues) == 0

    def test_result_failed(self):
        """PreflightResult can indicate failed status with issues."""
        result = PreflightResult(
            passed=False,
            issues=["Missing pyproject.toml", "Missing __init__.py"],
            fixes_applied=[],
        )
        assert result.passed is False
        assert len(result.issues) == 2

    def test_result_with_fixes(self):
        """PreflightResult tracks applied fixes."""
        result = PreflightResult(
            passed=True,
            issues=[],
            fixes_applied=["Created pyproject.toml", "Created __init__.py"],
        )
        assert len(result.fixes_applied) == 2

    def test_result_summary_passed(self):
        """Summary shows passed status."""
        result = PreflightResult(passed=True, issues=[], fixes_applied=[])
        summary = result.summary
        assert "PASSED" in summary

    def test_result_summary_failed(self):
        """Summary shows failed status and issues."""
        result = PreflightResult(
            passed=False,
            issues=["Missing file"],
            fixes_applied=[],
        )
        summary = result.summary
        assert "FAILED" in summary
        assert "Missing file" in summary

    def test_result_summary_with_fixes(self):
        """Summary shows applied fixes."""
        result = PreflightResult(
            passed=True,
            issues=[],
            fixes_applied=["Created pyproject.toml"],
        )
        summary = result.summary
        assert "Auto-fixed" in summary
        assert "pyproject.toml" in summary


class TestPreflightChecker:
    """Tests for PreflightChecker class."""

    @pytest.fixture
    def empty_project(self, tmp_path):
        """Create empty project directory."""
        return tmp_path

    @pytest.fixture
    def minimal_backend(self, tmp_path):
        """Create minimal backend structure."""
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "app").mkdir()
        (backend / "app" / "__init__.py").touch()
        (backend / "tests").mkdir()
        (backend / "tests" / "__init__.py").touch()
        (backend / "pyproject.toml").write_text(
            '[project]\nname = "test"\n\n[tool.hatch.build.targets.wheel]\npackages = ["app"]'
        )
        return tmp_path

    @pytest.fixture
    def complete_project(self, tmp_path):
        """Create complete project structure."""
        # Backend
        backend = tmp_path / "backend"
        backend.mkdir()
        app = backend / "app"
        app.mkdir()
        (app / "__init__.py").touch()
        for pkg in ["api", "api/v1", "core", "db", "models", "services"]:
            pkg_dir = app / pkg
            pkg_dir.mkdir(parents=True, exist_ok=True)
            (pkg_dir / "__init__.py").touch()
        (backend / "tests").mkdir()
        (backend / "tests" / "__init__.py").touch()
        (backend / "pyproject.toml").write_text(
            '[project]\nname = "test"\n\n[tool.hatch.build.targets.wheel]\npackages = ["app"]'
        )

        # Frontend
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text('{"name": "test"}')

        return tmp_path

    def test_checker_initialization(self, empty_project):
        """Checker initializes with project directory."""
        checker = PreflightChecker(empty_project)
        assert checker.project_dir == empty_project
        assert checker.backend_dir == empty_project / "backend"
        assert checker.frontend_dir == empty_project / "frontend"

    def test_check_backend_no_backend(self, empty_project):
        """No issues when backend directory doesn't exist."""
        checker = PreflightChecker(empty_project)
        issues = checker.check_backend_structure()
        assert issues == []

    def test_check_backend_missing_pyproject(self, tmp_path):
        """Detects missing pyproject.toml."""
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "app").mkdir()

        checker = PreflightChecker(tmp_path)
        issues = checker.check_backend_structure()
        assert any("pyproject.toml" in issue for issue in issues)

    def test_check_backend_missing_packages_config(self, tmp_path):
        """Detects missing packages config in pyproject.toml."""
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text('[project]\nname = "test"')

        checker = PreflightChecker(tmp_path)
        issues = checker.check_backend_structure()
        assert any("packages" in issue.lower() for issue in issues)

    def test_check_backend_missing_init_files(self, tmp_path):
        """Detects missing __init__.py files."""
        backend = tmp_path / "backend"
        backend.mkdir()
        app = backend / "app"
        app.mkdir()
        (app / "api").mkdir()  # No __init__.py
        (backend / "pyproject.toml").write_text(
            '[project]\n[tool.hatch.build.targets.wheel]\npackages = ["app"]'
        )

        checker = PreflightChecker(tmp_path)
        issues = checker.check_backend_structure()
        assert any("__init__.py" in issue for issue in issues)

    def test_check_backend_missing_tests(self, tmp_path):
        """Detects missing tests directory."""
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "app").mkdir()
        (backend / "app" / "__init__.py").touch()
        (backend / "pyproject.toml").write_text(
            '[project]\n[tool.hatch.build.targets.wheel]\npackages = ["app"]'
        )

        checker = PreflightChecker(tmp_path)
        issues = checker.check_backend_structure()
        assert any("tests" in issue for issue in issues)

    def test_check_backend_complete(self, complete_project):
        """No issues for complete backend."""
        checker = PreflightChecker(complete_project)
        issues = checker.check_backend_structure()
        assert issues == []

    def test_check_frontend_no_frontend(self, empty_project):
        """No issues when frontend doesn't exist."""
        checker = PreflightChecker(empty_project)
        issues = checker.check_frontend_structure()
        assert issues == []

    def test_check_frontend_missing_package_json(self, tmp_path):
        """Detects missing package.json."""
        frontend = tmp_path / "frontend"
        frontend.mkdir()

        checker = PreflightChecker(tmp_path)
        issues = checker.check_frontend_structure()
        assert any("package.json" in issue for issue in issues)

    def test_check_frontend_complete(self, tmp_path):
        """No issues for complete frontend."""
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text('{"name": "test"}')

        checker = PreflightChecker(tmp_path)
        issues = checker.check_frontend_structure()
        assert issues == []

    def test_check_dependencies_no_backend(self, empty_project):
        """No issues when backend doesn't exist."""
        checker = PreflightChecker(empty_project)
        issues = checker.check_dependencies()
        assert issues == []

    def test_check_dependencies_with_mock(self, minimal_backend):
        """Check dependencies using mocked subprocess."""
        checker = PreflightChecker(minimal_backend)

        with patch("subprocess.run") as mock_run:
            # uv sync succeeds
            mock_run.return_value = MagicMock(returncode=0)
            issues = checker.check_dependencies()
            # With successful mock, no missing deps
            assert issues == []

    def test_auto_fix_creates_pyproject(self, tmp_path):
        """Auto-fix creates pyproject.toml."""
        backend = tmp_path / "backend"
        backend.mkdir()

        checker = PreflightChecker(tmp_path)
        fixes = checker.auto_fix(["Missing backend/pyproject.toml"])

        assert len(fixes) == 1
        assert "pyproject.toml" in fixes[0]
        assert (backend / "pyproject.toml").exists()

    def test_auto_fix_creates_init_file(self, tmp_path):
        """Auto-fix creates __init__.py files."""
        backend = tmp_path / "backend"
        backend.mkdir()
        app = backend / "app"
        app.mkdir()
        api = app / "api"
        api.mkdir()

        checker = PreflightChecker(tmp_path)
        fixes = checker.auto_fix(["Missing backend/app/api/__init__.py"])

        assert len(fixes) == 1
        assert (api / "__init__.py").exists()

    def test_auto_fix_creates_tests_directory(self, tmp_path):
        """Auto-fix creates tests directory."""
        backend = tmp_path / "backend"
        backend.mkdir()

        checker = PreflightChecker(tmp_path)
        fixes = checker.auto_fix(["Missing backend/tests directory"])

        assert len(fixes) == 1
        assert (backend / "tests").exists()
        assert (backend / "tests" / "__init__.py").exists()

    def test_auto_fix_installs_dependency(self, minimal_backend):
        """Auto-fix installs missing dependencies."""
        checker = PreflightChecker(minimal_backend)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            fixes = checker.auto_fix(["Missing dev dependency: pytest"])

            assert len(fixes) == 1
            assert "pytest" in fixes[0]
            # Verify uv add was called
            mock_run.assert_called()

    def test_run_complete_project(self, complete_project):
        """run() passes for complete project."""
        checker = PreflightChecker(complete_project)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = checker.run(auto_fix=False)

        assert result.passed is True
        assert len(result.issues) == 0

    def test_run_with_auto_fix(self, tmp_path):
        """run() auto-fixes issues."""
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "app").mkdir()

        checker = PreflightChecker(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = checker.run(auto_fix=True)

        # Should have applied fixes
        assert len(result.fixes_applied) > 0
        # pyproject.toml should now exist
        assert (backend / "pyproject.toml").exists()

    def test_run_without_auto_fix(self, tmp_path):
        """run() reports issues without fixing."""
        backend = tmp_path / "backend"
        backend.mkdir()

        checker = PreflightChecker(tmp_path)
        result = checker.run(auto_fix=False)

        assert result.passed is False
        assert len(result.issues) > 0
        assert len(result.fixes_applied) == 0


class TestPreflightPyprojectContent:
    """Tests for generated pyproject.toml content."""

    def test_created_pyproject_has_project_section(self, tmp_path):
        """Created pyproject.toml has [project] section."""
        backend = tmp_path / "backend"
        backend.mkdir()

        checker = PreflightChecker(tmp_path)
        checker._create_pyproject_toml()

        content = (backend / "pyproject.toml").read_text()
        assert "[project]" in content

    def test_created_pyproject_has_packages_config(self, tmp_path):
        """Created pyproject.toml has packages config."""
        backend = tmp_path / "backend"
        backend.mkdir()

        checker = PreflightChecker(tmp_path)
        checker._create_pyproject_toml()

        content = (backend / "pyproject.toml").read_text()
        assert "packages" in content
        assert '"app"' in content

    def test_created_pyproject_has_dev_deps(self, tmp_path):
        """Created pyproject.toml includes dev dependencies."""
        backend = tmp_path / "backend"
        backend.mkdir()

        checker = PreflightChecker(tmp_path)
        checker._create_pyproject_toml()

        content = (backend / "pyproject.toml").read_text()
        assert "pytest" in content
        assert "ruff" in content

    def test_created_pyproject_has_fastapi(self, tmp_path):
        """Created pyproject.toml includes FastAPI."""
        backend = tmp_path / "backend"
        backend.mkdir()

        checker = PreflightChecker(tmp_path)
        checker._create_pyproject_toml()

        content = (backend / "pyproject.toml").read_text()
        assert "fastapi" in content


class TestPreflightMissingInitInTests:
    """Tests for the tests/__init__.py missing branch (line 134)."""

    def test_check_backend_tests_dir_exists_without_init(self, tmp_path):
        """Detects missing __init__.py in existing tests directory."""
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "app").mkdir()
        (backend / "app" / "__init__.py").touch()
        (backend / "tests").mkdir()
        # No tests/__init__.py — only the directory
        (backend / "pyproject.toml").write_text(
            '[project]\n[tool.hatch.build.targets.wheel]\npackages = ["app"]'
        )

        checker = PreflightChecker(tmp_path)
        issues = checker.check_backend_structure()
        assert any("tests/__init__.py" in issue for issue in issues)
        assert not any("tests directory" in issue for issue in issues)


class TestPreflightDependencyFailure:
    """Tests for dependency check failure path (line 186)."""

    def test_check_dependencies_reports_missing_dep(self, tmp_path):
        """Reports missing dependency when import check fails."""
        backend = tmp_path / "backend"
        backend.mkdir()

        checker = PreflightChecker(tmp_path)

        # uv sync succeeds (returncode=0), but every dep import fails (returncode=1)
        sync_result = MagicMock(returncode=0)
        import_fail = MagicMock(returncode=1)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [sync_result] + [import_fail] * len(
                checker.REQUIRED_DEV_DEPS
            )
            issues = checker.check_dependencies()

        assert len(issues) == len(checker.REQUIRED_DEV_DEPS)
        for pip_name, _ in checker.REQUIRED_DEV_DEPS:
            assert any(pip_name in issue for issue in issues)

    def test_check_dependencies_reports_some_missing(self, tmp_path):
        """Reports only the failing dependencies."""
        backend = tmp_path / "backend"
        backend.mkdir()

        checker = PreflightChecker(tmp_path)
        num_deps = len(checker.REQUIRED_DEV_DEPS)

        sync_result = MagicMock(returncode=0)
        # First dep fails, rest succeed
        results = [sync_result, MagicMock(returncode=1)] + [
            MagicMock(returncode=0)
        ] * (num_deps - 1)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = results
            issues = checker.check_dependencies()

        assert len(issues) == 1
        first_dep_name = checker.REQUIRED_DEV_DEPS[0][0]
        assert first_dep_name in issues[0]


class TestPreflightAutoFixPackagesConfig:
    """Tests for auto_fix adding packages config to existing pyproject.toml (lines 208-209)."""

    def test_auto_fix_adds_packages_config(self, tmp_path):
        """Auto-fix appends packages config to pyproject.toml that is missing it."""
        backend = tmp_path / "backend"
        backend.mkdir()
        # Write pyproject.toml without the hatch wheel section
        (backend / "pyproject.toml").write_text('[project]\nname = "test"\n')

        checker = PreflightChecker(tmp_path)
        fixes = checker.auto_fix(
            ["pyproject.toml missing [tool.hatch.build.targets.wheel] packages config"]
        )

        assert len(fixes) == 1
        assert "packages config" in fixes[0]
        content = (backend / "pyproject.toml").read_text()
        assert "[tool.hatch.build.targets.wheel]" in content
        assert 'packages = ["app"]' in content

    def test_auto_fix_packages_config_already_present(self, tmp_path):
        """Auto-fix is idempotent — does not duplicate existing packages config."""
        backend = tmp_path / "backend"
        backend.mkdir()
        original = '[project]\nname = "test"\n\n[tool.hatch.build.targets.wheel]\npackages = ["app"]\n'
        (backend / "pyproject.toml").write_text(original)

        checker = PreflightChecker(tmp_path)
        fixes = checker.auto_fix(
            ["pyproject.toml missing [tool.hatch.build.targets.wheel] packages config"]
        )

        # Fix is still reported as applied (method returns True)
        assert len(fixes) == 1
        content = (backend / "pyproject.toml").read_text()
        # Config should not be doubled
        assert content.count("[tool.hatch.build.targets.wheel]") == 1

    def test_add_packages_config_no_pyproject(self, tmp_path):
        """_add_packages_config returns False when pyproject.toml is absent."""
        backend = tmp_path / "backend"
        backend.mkdir()

        checker = PreflightChecker(tmp_path)
        result = checker._add_packages_config()
        assert result is False


class TestPreflightAutoFixTestsInit:
    """Tests for auto_fix creating tests/__init__.py (lines 227-230)."""

    def test_auto_fix_creates_tests_init(self, tmp_path):
        """Auto-fix creates tests/__init__.py when tests dir exists but init is missing."""
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "tests").mkdir()

        checker = PreflightChecker(tmp_path)
        fixes = checker.auto_fix(["Missing backend/tests/__init__.py"])

        assert len(fixes) == 1
        assert "tests/__init__.py" in fixes[0]
        assert (backend / "tests" / "__init__.py").exists()


class TestPreflightExceptionPaths:
    """Tests for exception-handling return-False branches in private helpers."""

    def test_create_pyproject_toml_write_failure(self, tmp_path):
        """_create_pyproject_toml returns False when write fails."""
        backend = tmp_path / "backend"
        backend.mkdir()

        checker = PreflightChecker(tmp_path)

        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            result = checker._create_pyproject_toml()

        assert result is False

    def test_create_init_file_exception(self, tmp_path):
        """_create_init_file returns False when mkdir/touch raises."""
        checker = PreflightChecker(tmp_path)
        target = tmp_path / "backend" / "app" / "__init__.py"

        with patch("pathlib.Path.mkdir", side_effect=PermissionError("denied")):
            result = checker._create_init_file(target)

        assert result is False

    def test_install_dev_dependency_exception(self, tmp_path):
        """_install_dev_dependency returns False when subprocess raises."""
        backend = tmp_path / "backend"
        backend.mkdir()
        checker = PreflightChecker(tmp_path)

        with patch(
            "subprocess.run", side_effect=FileNotFoundError("uv not found")
        ):
            result = checker._install_dev_dependency("pytest")

        assert result is False

    def test_install_dev_dependency_nonzero_exit(self, tmp_path):
        """_install_dev_dependency returns False when uv add exits non-zero."""
        backend = tmp_path / "backend"
        backend.mkdir()
        checker = PreflightChecker(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = checker._install_dev_dependency("some-package")

        assert result is False

    def test_create_tests_directory_exception(self, tmp_path):
        """_create_tests_directory returns False when filesystem operation raises."""
        backend = tmp_path / "backend"
        backend.mkdir()
        checker = PreflightChecker(tmp_path)

        with patch("pathlib.Path.mkdir", side_effect=PermissionError("denied")):
            result = checker._create_tests_directory()

        assert result is False

    def test_add_packages_config_write_exception(self, tmp_path):
        """_add_packages_config returns False when write fails."""
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text('[project]\nname = "test"\n')
        checker = PreflightChecker(tmp_path)

        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            result = checker._add_packages_config()

        assert result is False


class TestPreflightRunDepAutoFix:
    """Tests for run() dependency auto-fix branch (lines 363-366)."""

    def test_run_auto_fixes_missing_dependencies(self, tmp_path):
        """run() auto-fixes dependency issues and re-checks."""
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "app").mkdir()
        (backend / "app" / "__init__.py").touch()
        (backend / "tests").mkdir()
        (backend / "tests" / "__init__.py").touch()
        (backend / "pyproject.toml").write_text(
            '[project]\nname = "test"\n\n[tool.hatch.build.targets.wheel]\npackages = ["app"]'
        )

        checker = PreflightChecker(tmp_path)

        # Round 1: uv sync ok, all imports fail -> triggers auto_fix
        # Round 2 (re-check): uv sync ok, all imports succeed
        sync_ok = MagicMock(returncode=0)
        import_fail = MagicMock(returncode=1)
        import_ok = MagicMock(returncode=0)
        num_deps = len(checker.REQUIRED_DEV_DEPS)

        # uv add calls: one per dep (auto-fix loop)
        uv_add_ok = MagicMock(returncode=0)

        first_round = [sync_ok] + [import_fail] * num_deps
        uv_adds = [uv_add_ok] * num_deps
        second_round = [sync_ok] + [import_ok] * num_deps

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = first_round + uv_adds + second_round
            result = checker.run(auto_fix=True)

        assert result.passed is True
        assert len(result.fixes_applied) > 0

    def test_run_no_auto_fix_reports_dep_issues(self, tmp_path):
        """run(auto_fix=False) reports missing deps without attempting fixes."""
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "app").mkdir()
        (backend / "app" / "__init__.py").touch()
        (backend / "tests").mkdir()
        (backend / "tests" / "__init__.py").touch()
        (backend / "pyproject.toml").write_text(
            '[project]\nname = "test"\n\n[tool.hatch.build.targets.wheel]\npackages = ["app"]'
        )

        checker = PreflightChecker(tmp_path)
        num_deps = len(checker.REQUIRED_DEV_DEPS)
        sync_ok = MagicMock(returncode=0)
        import_fail = MagicMock(returncode=1)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [sync_ok] + [import_fail] * num_deps
            result = checker.run(auto_fix=False)

        assert result.passed is False
        assert len(result.issues) == num_deps
        assert len(result.fixes_applied) == 0
