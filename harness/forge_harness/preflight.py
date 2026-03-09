"""
Pre-Flight Checklist Module
===========================

Validates project structure before coding begins.
Auto-fixes common issues like missing pyproject.toml, __init__.py files,
and dev dependencies.

Based on lessons learned from KiddoRewards autonomous session (2025-12-30).
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PreflightResult:
    """Result of running pre-flight checks."""

    passed: bool
    issues: list[str] = field(default_factory=list)
    fixes_applied: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """Generate human-readable summary."""
        status = "PASSED" if self.passed else "FAILED"
        lines = [f"Pre-flight: {status}"]

        if self.fixes_applied:
            lines.append(f"Auto-fixed {len(self.fixes_applied)} issues:")
            for fix in self.fixes_applied:
                lines.append(f"  + {fix}")

        if self.issues:
            lines.append(f"Remaining issues ({len(self.issues)}):")
            for issue in self.issues:
                lines.append(f"  - {issue}")

        return "\n".join(lines)


class PreflightChecker:
    """
    Validate project structure before coding begins.

    Checks:
    - pyproject.toml exists with correct packages config
    - __init__.py files exist in all required packages
    - Dev dependencies are installed
    - tests directory exists

    Auto-fixes common issues when possible.
    """

    # Required package directories for a FORGE backend
    REQUIRED_BACKEND_PACKAGES = [
        "",  # app/__init__.py
        "api",
        "api/v1",
        "core",
        "db",
        "models",
        "services",
    ]

    # Required dev dependencies
    REQUIRED_DEV_DEPS = [
        ("pytest", "pytest"),
        ("pytest-asyncio", "pytest_asyncio"),
        ("pytest-cov", "pytest_cov"),
        ("ruff", "ruff"),
        ("httpx", "httpx"),
    ]

    def __init__(self, project_dir: Path):
        """
        Initialize pre-flight checker.

        Args:
            project_dir: Path to the project directory
        """
        self.project_dir = Path(project_dir)
        self.backend_dir = self.project_dir / "backend"
        self.frontend_dir = self.project_dir / "frontend"

    def check_backend_structure(self) -> list[str]:
        """
        Validate backend has required structure.

        Returns:
            List of issues found
        """
        issues = []

        if not self.backend_dir.exists():
            # No backend directory - might be a different project structure
            return []

        # Check pyproject.toml exists
        pyproject = self.backend_dir / "pyproject.toml"
        if not pyproject.exists():
            issues.append("Missing backend/pyproject.toml")
        else:
            # Check it has the correct packages config
            content = pyproject.read_text()
            if "packages" not in content and "[tool.hatch.build" not in content:
                issues.append(
                    "pyproject.toml missing [tool.hatch.build.targets.wheel] packages config"
                )

        # Check __init__.py files in app packages
        app_dir = self.backend_dir / "app"
        if app_dir.exists():
            for pkg in self.REQUIRED_BACKEND_PACKAGES:
                if pkg:
                    init_path = app_dir / pkg / "__init__.py"
                    pkg_dir = app_dir / pkg
                else:
                    init_path = app_dir / "__init__.py"
                    pkg_dir = app_dir

                # Only check if the package directory exists
                if pkg_dir.exists() and not init_path.exists():
                    rel_path = init_path.relative_to(self.project_dir)
                    issues.append(f"Missing {rel_path}")

        # Check tests directory exists
        tests_dir = self.backend_dir / "tests"
        if not tests_dir.exists():
            issues.append("Missing backend/tests directory")
        elif not (tests_dir / "__init__.py").exists():
            issues.append("Missing backend/tests/__init__.py")

        return issues

    def check_frontend_structure(self) -> list[str]:
        """
        Validate frontend has required structure.

        Returns:
            List of issues found
        """
        issues = []

        if not self.frontend_dir.exists():
            return []

        # Check package.json exists
        package_json = self.frontend_dir / "package.json"
        if not package_json.exists():
            issues.append("Missing frontend/package.json")

        return issues

    def check_dependencies(self) -> list[str]:
        """
        Validate dev dependencies are installed.

        Returns:
            List of missing dependencies
        """
        issues = []

        if not self.backend_dir.exists():
            return []

        # First sync dependencies
        subprocess.run(
            ["uv", "sync"],
            cwd=self.backend_dir,
            capture_output=True,
            timeout=120,
        )

        # Check each required dev dependency
        for pip_name, import_name in self.REQUIRED_DEV_DEPS:
            result = subprocess.run(
                ["uv", "run", "python", "-c", f"import {import_name}"],
                cwd=self.backend_dir,
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                issues.append(f"Missing dev dependency: {pip_name}")

        return issues

    def auto_fix(self, issues: list[str]) -> list[str]:
        """
        Attempt to fix common issues automatically.

        Args:
            issues: List of issues to fix

        Returns:
            List of fixes that were applied
        """
        fixes = []

        for issue in issues:
            if "Missing backend/pyproject.toml" in issue:
                if self._create_pyproject_toml():
                    fixes.append("Created pyproject.toml with FORGE standard config")

            elif "pyproject.toml missing" in issue and "packages" in issue:
                if self._add_packages_config():
                    fixes.append("Added packages config to pyproject.toml")

            elif "__init__.py" in issue:
                # Extract the path from "Missing backend/app/api/__init__.py"
                path_str = issue.replace("Missing ", "")
                path = self.project_dir / path_str
                if self._create_init_file(path):
                    fixes.append(f"Created {path_str}")

            elif "Missing dev dependency:" in issue:
                dep = issue.split(": ")[1]
                if self._install_dev_dependency(dep):
                    fixes.append(f"Installed {dep}")

            elif "Missing backend/tests directory" in issue:
                if self._create_tests_directory():
                    fixes.append("Created tests directory")

            elif "Missing backend/tests/__init__.py" in issue:
                path = self.backend_dir / "tests" / "__init__.py"
                if self._create_init_file(path):
                    fixes.append("Created tests/__init__.py")

        return fixes

    def _create_pyproject_toml(self) -> bool:
        """Create standard FORGE pyproject.toml."""
        project_name = self.project_dir.name.replace("-", "_")

        content = f'''[project]
name = "{project_name}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "sqlmodel>=0.0.14",
    "asyncpg>=0.29.0",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",
    "python-jose[cryptography]>=3.3.0",
    "passlib[bcrypt]>=1.7.4",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=4.1.0",
    "httpx>=0.26.0",
    "ruff>=0.1.0",
]

[tool.hatch.build.targets.wheel]
packages = ["app"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "W"]
ignore = ["E501"]
'''
        try:
            (self.backend_dir / "pyproject.toml").write_text(content)
            return True
        except Exception:
            return False

    def _add_packages_config(self) -> bool:
        """Add packages config to existing pyproject.toml."""
        pyproject = self.backend_dir / "pyproject.toml"
        if not pyproject.exists():
            return False

        try:
            content = pyproject.read_text()
            if "[tool.hatch.build.targets.wheel]" not in content:
                content += '\n[tool.hatch.build.targets.wheel]\npackages = ["app"]\n'
                pyproject.write_text(content)
            return True
        except Exception:
            return False

    def _create_init_file(self, path: Path) -> bool:
        """Create an empty __init__.py file."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            return True
        except Exception:
            return False

    def _install_dev_dependency(self, dep: str) -> bool:
        """Install a dev dependency using uv."""
        try:
            result = subprocess.run(
                ["uv", "add", "--dev", dep],
                cwd=self.backend_dir,
                capture_output=True,
                timeout=120,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _create_tests_directory(self) -> bool:
        """Create tests directory with __init__.py."""
        try:
            tests_dir = self.backend_dir / "tests"
            tests_dir.mkdir(parents=True, exist_ok=True)
            (tests_dir / "__init__.py").touch()
            return True
        except Exception:
            return False

    def run(self, auto_fix: bool = True) -> PreflightResult:
        """
        Run all pre-flight checks.

        Args:
            auto_fix: Whether to attempt auto-fixing issues

        Returns:
            PreflightResult with check outcome
        """
        all_issues = []
        all_fixes = []

        # Check backend structure
        backend_issues = self.check_backend_structure()
        all_issues.extend(backend_issues)

        # Auto-fix structure issues first
        if auto_fix and backend_issues:
            structure_fixes = self.auto_fix(backend_issues)
            all_fixes.extend(structure_fixes)
            # Re-check after fixes
            backend_issues = self.check_backend_structure()
            all_issues = backend_issues

        # Check frontend structure
        frontend_issues = self.check_frontend_structure()
        all_issues.extend(frontend_issues)

        # Check dependencies (after potential pyproject.toml creation)
        dep_issues = self.check_dependencies()

        if auto_fix and dep_issues:
            dep_fixes = self.auto_fix(dep_issues)
            all_fixes.extend(dep_fixes)
            # Re-check after fixes
            dep_issues = self.check_dependencies()

        all_issues.extend(dep_issues)

        return PreflightResult(
            passed=len(all_issues) == 0,
            issues=all_issues,
            fixes_applied=all_fixes,
        )
