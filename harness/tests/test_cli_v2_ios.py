"""
Tests for forge ios CLI commands.

Covers all six subcommands: bootstrap, build, test, sim, testflight, status.
External dependencies (iOS harness modules, Typer, Rich) are fully mocked.
Uses typer.testing.CliRunner which works natively with Typer apps.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import the Typer app under test
# ---------------------------------------------------------------------------
from forge_harness.cli_v2.ios import app, check_dependencies
from typer.testing import CliRunner

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner() -> CliRunner:
    """Typer CLI test runner."""
    return CliRunner()


@pytest.fixture()
def xcodeproj_dir(tmp_path: Path) -> Path:
    """Create a temp directory containing a fake .xcodeproj."""
    proj = tmp_path / "MyApp.xcodeproj"
    proj.mkdir()
    return tmp_path


@pytest.fixture()
def ipa_file(tmp_path: Path) -> Path:
    """Create a fake .ipa file."""
    ipa = tmp_path / "MyApp.ipa"
    ipa.write_bytes(b"fake ipa content")
    return ipa


# ---------------------------------------------------------------------------
# Patch target helpers
# ---------------------------------------------------------------------------

MODULES = "forge_harness.cli_v2.ios"


def _patch_deps(**overrides):
    """Return a dict of patches that replace all ios_harness classes with MagicMocks.

    Pass keyword overrides to replace a specific mock's attribute value.
    """
    return {
        "SimulatorHarness": overrides.get("SimulatorHarness", MagicMock()),
        "BuildHarness": overrides.get("BuildHarness", MagicMock()),
        "TestHarness": overrides.get("TestHarness", MagicMock()),
        "ProjectBootstrap": overrides.get("ProjectBootstrap", MagicMock()),
        "AppStoreHarness": overrides.get("AppStoreHarness", MagicMock()),
        "QualityGatesHarness": overrides.get("QualityGatesHarness", MagicMock()),
    }


def _patch_run_async(result: object) -> MagicMock:
    """Return a mock for _run_async that returns the given result."""
    mock_run_async = MagicMock()
    mock_run_async.return_value = result
    return mock_run_async


def _make_build_result(success: bool = True, build_time: float = 0.0) -> MagicMock:
    """Create a mock BuildResult object with .success and .build_time_seconds attributes."""
    result = MagicMock()
    result.success = success
    result.build_time_seconds = build_time
    return result


def _make_test_result(success: bool = True, passed: int = 0, failed: int = 0) -> MagicMock:
    """Create a mock TestResult object."""
    result = MagicMock()
    result.success = success
    result.passed = passed
    result.failed = failed
    return result


# ---------------------------------------------------------------------------
# check_dependencies helper
# ---------------------------------------------------------------------------


class TestCheckDependencies:
    """Unit tests for the check_dependencies() helper."""

    def test_raises_exit_when_simulator_harness_is_none(self):
        """Should raise typer.Exit(1) when SimulatorHarness is None."""
        import typer

        with patch(f"{MODULES}.SimulatorHarness", None):
            with pytest.raises(typer.Exit):
                check_dependencies()

    def test_does_not_raise_when_simulator_harness_available(self):
        """Should not raise when SimulatorHarness is a real class."""
        with patch(f"{MODULES}.SimulatorHarness", MagicMock()):
            check_dependencies()  # no exception


# ---------------------------------------------------------------------------
# bootstrap command
# ---------------------------------------------------------------------------


class TestBootstrapCommand:
    """Tests for 'forge ios bootstrap'."""

    def _run(self, runner: CliRunner, args: list[str], mocks: dict) -> object:
        with patch.multiple(MODULES, **mocks):
            return runner.invoke(app, args)

    def test_bootstrap_success(self, runner: CliRunner, tmp_path: Path):
        """bootstrap with a project name that succeeds prints project path."""
        mock_bootstrap_cls = MagicMock()
        mock_bootstrap_instance = MagicMock()
        mock_bootstrap_instance.create_project.return_value = {
            "success": True,
            "project_path": str(tmp_path / "ForgeApp"),
            "xcodeproj_path": str(tmp_path / "ForgeApp" / "ForgeApp.xcodeproj"),
        }
        mock_bootstrap_cls.return_value = mock_bootstrap_instance

        mocks = _patch_deps(ProjectBootstrap=mock_bootstrap_cls)
        result = self._run(runner, ["bootstrap", "ForgeApp"], mocks)

        assert result.exit_code == 0
        assert "ForgeApp" in result.output
        assert "forge ios build" in result.output

    def test_bootstrap_uses_custom_bundle_id(self, runner: CliRunner, tmp_path: Path):
        """--bundle-id option is forwarded to ProjectBootstrap."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.create_project.return_value = {
            "success": True,
            "project_path": "/tmp/ForgeApp",
            "xcodeproj_path": "/tmp/ForgeApp/ForgeApp.xcodeproj",
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(ProjectBootstrap=mock_cls)
        result = self._run(
            runner,
            ["bootstrap", "ForgeApp", "--bundle-id", "io.custom.ForgeApp"],
            mocks,
        )

        assert result.exit_code == 0
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["bundle_id"] == "io.custom.ForgeApp"

    def test_bootstrap_derives_bundle_id_from_org_and_name(self, runner: CliRunner, tmp_path: Path):
        """When no --bundle-id is given, bundle_id = {org}.{name}."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.create_project.return_value = {
            "success": True,
            "project_path": "/tmp/ForgeApp",
            "xcodeproj_path": "/tmp/ForgeApp/ForgeApp.xcodeproj",
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(ProjectBootstrap=mock_cls)
        result = self._run(
            runner,
            ["bootstrap", "ForgeApp", "--org", "com.acme"],
            mocks,
        )

        assert result.exit_code == 0
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["bundle_id"] == "com.acme.ForgeApp"

    def test_bootstrap_failure_exits_with_code_1(self, runner: CliRunner):
        """bootstrap exits 1 and prints error when create_project fails."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.create_project.return_value = {
            "success": False,
            "error": "Directory already exists",
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(ProjectBootstrap=mock_cls)
        result = self._run(runner, ["bootstrap", "ForgeApp"], mocks)

        assert result.exit_code == 1
        assert "Directory already exists" in result.output

    def test_bootstrap_missing_deps_exits_1(self, runner: CliRunner):
        """bootstrap exits 1 when ios_harness is unavailable."""
        mocks = _patch_deps()
        mocks["SimulatorHarness"] = None
        result = self._run(runner, ["bootstrap", "ForgeApp"], mocks)

        assert result.exit_code == 1

    def test_bootstrap_template_option_forwarded(self, runner: CliRunner):
        """--template is forwarded to create_project()."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.create_project.return_value = {
            "success": True,
            "project_path": "/tmp/ForgeLib",
            "xcodeproj_path": "/tmp/ForgeLib/ForgeLib.xcodeproj",
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(ProjectBootstrap=mock_cls)
        result = self._run(
            runner,
            ["bootstrap", "ForgeLib", "--template", "framework"],
            mocks,
        )

        assert result.exit_code == 0
        mock_instance.create_project.assert_called_once_with(template="framework")

    def test_bootstrap_default_template_is_app(self, runner: CliRunner):
        """Default template should be 'app'."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.create_project.return_value = {
            "success": True,
            "project_path": "/tmp/ForgeApp",
            "xcodeproj_path": "/tmp/ForgeApp/ForgeApp.xcodeproj",
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(ProjectBootstrap=mock_cls)
        self._run(runner, ["bootstrap", "ForgeApp"], mocks)

        mock_instance.create_project.assert_called_once_with(template="app")


# ---------------------------------------------------------------------------
# build command
# ---------------------------------------------------------------------------


class TestBuildCommand:
    """Tests for 'forge ios build'."""

    def _run(
        self, runner: CliRunner, args: list[str], mocks: dict, build_result: object = None
    ) -> object:
        """Run CLI with patches. If build_result provided, patch _run_async to return it."""
        all_mocks = dict(mocks)
        if build_result is not None:
            all_mocks["_run_async"] = _patch_run_async(build_result)
        with patch.multiple(MODULES, **all_mocks):
            return runner.invoke(app, args)

    def test_build_success_with_explicit_project(self, runner: CliRunner, xcodeproj_dir: Path):
        """build succeeds when --project points to an .xcodeproj dir."""
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        build_result = _make_build_result(success=True, build_time=45.0)
        mocks = _patch_deps()
        result = self._run(
            runner,
            ["build", "--project", str(project_path), "--sim"],
            mocks,
            build_result=build_result,
        )

        assert result.exit_code == 0
        assert "Build succeeded" in result.output
        assert "45" in result.output

    def test_build_auto_detects_xcodeproj_in_cwd(self, runner: CliRunner, xcodeproj_dir: Path):
        """build auto-detects .xcodeproj when --project is omitted."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.build.return_value = {"success": True}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(BuildHarness=mock_cls)
        # Patch Path.cwd so the glob runs against our temp dir with a .xcodeproj
        with patch(f"{MODULES}.Path") as mock_path_cls:
            # Restore normal Path behaviour for everything except cwd()
            mock_path_cls.side_effect = lambda *a, **kw: Path(*a, **kw)
            mock_path_cls.cwd.return_value = xcodeproj_dir
            with patch.multiple(MODULES, **mocks):
                result = runner.invoke(app, ["build"])

        assert result.exit_code == 0

    def test_build_exits_1_when_no_xcodeproj_found(self, runner: CliRunner, tmp_path: Path):
        """build exits 1 when no .xcodeproj exists in cwd."""
        mocks = _patch_deps()
        # Point cwd at tmp_path which has no .xcodeproj
        with patch(f"{MODULES}.Path") as mock_path_cls:
            mock_path_cls.side_effect = lambda *a, **kw: Path(*a, **kw)
            mock_path_cls.cwd.return_value = tmp_path
            with patch.multiple(MODULES, **mocks):
                result = runner.invoke(app, ["build"])

        assert result.exit_code == 1
        assert "No .xcodeproj found" in result.output

    def test_build_sim_flag_sets_simulator_destination(
        self, runner: CliRunner, xcodeproj_dir: Path
    ):
        """--sim flag sets destination to iOS Simulator iPhone 17 Pro."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.build.return_value = {"success": True}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(BuildHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        self._run(
            runner,
            ["build", "--project", str(project_path), "--sim"],
            mocks,
        )

        call_kwargs = mock_instance.build.call_args.kwargs
        assert "iPhone 17 Pro" in call_kwargs["destination"]
        assert "Simulator" in call_kwargs["destination"]

    def test_build_device_flag_sets_generic_platform(self, runner: CliRunner, xcodeproj_dir: Path):
        """--device flag sets destination to generic/platform=iOS."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.build.return_value = {"success": True}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(BuildHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        self._run(
            runner,
            ["build", "--project", str(project_path), "--device"],
            mocks,
        )

        call_kwargs = mock_instance.build.call_args.kwargs
        assert "generic/platform=iOS" in call_kwargs["destination"]

    def test_build_custom_destination_overrides_flags(self, runner: CliRunner, xcodeproj_dir: Path):
        """--destination overrides --sim and --device flags."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.build.return_value = {"success": True}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(BuildHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        custom_dest = "platform=iOS Simulator,name=iPad Pro"
        self._run(
            runner,
            ["build", "--project", str(project_path), "--destination", custom_dest],
            mocks,
        )

        call_kwargs = mock_instance.build.call_args.kwargs
        assert call_kwargs["destination"] == custom_dest

    def test_build_no_flags_defaults_to_simulator(self, runner: CliRunner, xcodeproj_dir: Path):
        """No --sim/--device/--destination defaults to simulator."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.build.return_value = {"success": True}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(BuildHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        self._run(
            runner,
            ["build", "--project", str(project_path)],
            mocks,
        )

        call_kwargs = mock_instance.build.call_args.kwargs
        assert "Simulator" in call_kwargs["destination"]

    def test_build_failure_exits_1(self, runner: CliRunner, xcodeproj_dir: Path):
        """build exits 1 and prints error on build failure."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.build.return_value = {
            "success": False,
            "error": "Undefined symbol",
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(BuildHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        result = self._run(
            runner,
            ["build", "--project", str(project_path)],
            mocks,
        )

        assert result.exit_code == 1
        assert "Undefined symbol" in result.output

    def test_build_clean_flag_forwarded(self, runner: CliRunner, xcodeproj_dir: Path):
        """--clean flag is passed through to BuildHarness.build()."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.build.return_value = {"success": True}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(BuildHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        self._run(
            runner,
            ["build", "--project", str(project_path), "--clean"],
            mocks,
        )

        call_kwargs = mock_instance.build.call_args.kwargs
        assert call_kwargs["clean"] is True

    def test_build_scheme_forwarded(self, runner: CliRunner, xcodeproj_dir: Path):
        """--scheme option is passed through to BuildHarness.build()."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.build.return_value = {"success": True}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(BuildHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        self._run(
            runner,
            ["build", "--project", str(project_path), "--scheme", "Production"],
            mocks,
        )

        call_kwargs = mock_instance.build.call_args.kwargs
        assert call_kwargs["scheme"] == "Production"

    def test_build_without_build_time_omits_time_line(self, runner: CliRunner, xcodeproj_dir: Path):
        """build does not print 'Build time' when result has no build_time key."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.build.return_value = {"success": True}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(BuildHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        result = self._run(
            runner,
            ["build", "--project", str(project_path)],
            mocks,
        )

        assert result.exit_code == 0
        assert "Build time" not in result.output


# ---------------------------------------------------------------------------
# test command
# ---------------------------------------------------------------------------


class TestTestCommand:
    """Tests for 'forge ios test'."""

    def _run(self, runner: CliRunner, args: list[str], mocks: dict) -> object:
        with patch.multiple(MODULES, **mocks):
            return runner.invoke(app, args)

    def test_test_all_passed_exits_0(self, runner: CliRunner, xcodeproj_dir: Path):
        """test command exits 0 when all tests pass."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run_tests.return_value = {
            "success": True,
            "passed": 42,
            "failed": 0,
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(TestHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        result = self._run(
            runner,
            ["test", "--project", str(project_path)],
            mocks,
        )

        assert result.exit_code == 0
        assert "42/42 passed" in result.output

    def test_test_some_failed_exits_1(self, runner: CliRunner, xcodeproj_dir: Path):
        """test command exits 1 when some tests fail."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run_tests.return_value = {
            "success": True,
            "passed": 10,
            "failed": 3,
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(TestHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        result = self._run(
            runner,
            ["test", "--project", str(project_path)],
            mocks,
        )

        assert result.exit_code == 1
        assert "3 test(s) failed" in result.output

    def test_test_run_failed_exits_1(self, runner: CliRunner, xcodeproj_dir: Path):
        """test exits 1 when run_tests itself returns failure."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run_tests.return_value = {
            "success": False,
            "error": "xcodebuild exited with code 65",
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(TestHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        result = self._run(
            runner,
            ["test", "--project", str(project_path)],
            mocks,
        )

        assert result.exit_code == 1
        assert "xcodebuild exited with code 65" in result.output

    def test_test_auto_detects_xcodeproj(self, runner: CliRunner, xcodeproj_dir: Path):
        """test auto-detects .xcodeproj when --project is omitted."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run_tests.return_value = {"success": True, "passed": 1, "failed": 0}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(TestHarness=mock_cls)
        with patch(f"{MODULES}.Path") as mock_path_cls:
            mock_path_cls.side_effect = lambda *a, **kw: Path(*a, **kw)
            mock_path_cls.cwd.return_value = xcodeproj_dir
            with patch.multiple(MODULES, **mocks):
                result = runner.invoke(app, ["test"])

        assert result.exit_code == 0

    def test_test_exits_1_when_no_xcodeproj_in_cwd(self, runner: CliRunner, tmp_path: Path):
        """test exits 1 when cwd has no .xcodeproj."""
        mocks = _patch_deps()
        with patch(f"{MODULES}.Path") as mock_path_cls:
            mock_path_cls.side_effect = lambda *a, **kw: Path(*a, **kw)
            mock_path_cls.cwd.return_value = tmp_path
            with patch.multiple(MODULES, **mocks):
                result = runner.invoke(app, ["test"])

        assert result.exit_code == 1
        assert "No .xcodeproj found" in result.output

    def test_test_scheme_and_plan_forwarded(self, runner: CliRunner, xcodeproj_dir: Path):
        """--scheme and --test-plan options are forwarded to TestHarness."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run_tests.return_value = {"success": True, "passed": 5, "failed": 0}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(TestHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        self._run(
            runner,
            [
                "test",
                "--project",
                str(project_path),
                "--scheme",
                "MyAppTests",
                "--test-plan",
                "Smoke",
            ],
            mocks,
        )

        call_kwargs = mock_instance.run_tests.call_args.kwargs
        assert call_kwargs["scheme"] == "MyAppTests"
        assert call_kwargs["test_plan"] == "Smoke"

    def test_test_only_testing_forwarded(self, runner: CliRunner, xcodeproj_dir: Path):
        """--only-testing option is forwarded to TestHarness."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run_tests.return_value = {"success": True, "passed": 1, "failed": 0}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(TestHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        self._run(
            runner,
            [
                "test",
                "--project",
                str(project_path),
                "--only-testing",
                "AuthTests/testLogin",
            ],
            mocks,
        )

        call_kwargs = mock_instance.run_tests.call_args.kwargs
        assert call_kwargs["only_testing"] == "AuthTests/testLogin"

    def test_test_coverage_flag_forwarded(self, runner: CliRunner, xcodeproj_dir: Path):
        """--coverage flag is forwarded to TestHarness."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run_tests.return_value = {"success": True, "passed": 5, "failed": 0}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(TestHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        self._run(
            runner,
            ["test", "--project", str(project_path), "--coverage"],
            mocks,
        )

        call_kwargs = mock_instance.run_tests.call_args.kwargs
        assert call_kwargs["coverage"] is True


# ---------------------------------------------------------------------------
# sim command
# ---------------------------------------------------------------------------


class TestSimCommand:
    """Tests for 'forge ios sim'."""

    def _run(self, runner: CliRunner, args: list[str], mocks: dict) -> object:
        with patch.multiple(MODULES, **mocks):
            return runner.invoke(app, args)

    def test_sim_list_prints_table(self, runner: CliRunner):
        """sim --action list prints a table of simulators."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.list_devices.return_value = [
            {"name": "iPhone 17 Pro", "udid": "UDID-001", "state": "Shutdown"},
            {"name": "iPhone 16", "udid": "UDID-002", "state": "Booted"},
        ]
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(SimulatorHarness=mock_cls)
        result = self._run(runner, ["sim", "--action", "list"], mocks)

        assert result.exit_code == 0
        assert "iPhone 17 Pro" in result.output
        assert "UDID-001" in result.output

    def test_sim_list_empty(self, runner: CliRunner):
        """sim --action list with no devices shows empty table without error."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.list_devices.return_value = []
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(SimulatorHarness=mock_cls)
        result = self._run(runner, ["sim", "--action", "list"], mocks)

        assert result.exit_code == 0

    def test_sim_boot_success(self, runner: CliRunner):
        """sim --action boot succeeds and prints UDID."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.boot_simulator.return_value = {
            "success": True,
            "udid": "UDID-001",
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(SimulatorHarness=mock_cls)
        result = self._run(
            runner,
            ["sim", "--action", "boot", "--device", "iPhone 17 Pro"],
            mocks,
        )

        assert result.exit_code == 0
        assert "UDID-001" in result.output

    def test_sim_boot_failure_exits_1(self, runner: CliRunner):
        """sim --action boot exits 1 when boot_simulator returns failure."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.boot_simulator.return_value = {
            "success": False,
            "error": "Device not found",
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(SimulatorHarness=mock_cls)
        result = self._run(
            runner,
            ["sim", "--action", "boot", "--device", "iPhone 17 Pro"],
            mocks,
        )

        assert result.exit_code == 1
        assert "Device not found" in result.output

    def test_sim_shutdown_success(self, runner: CliRunner):
        """sim --action shutdown succeeds."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.shutdown_simulator.return_value = {"success": True}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(SimulatorHarness=mock_cls)
        result = self._run(
            runner,
            ["sim", "--action", "shutdown", "--device", "iPhone 17 Pro"],
            mocks,
        )

        assert result.exit_code == 0
        assert "shut down" in result.output

    def test_sim_shutdown_failure_exits_1(self, runner: CliRunner):
        """sim --action shutdown exits 1 on failure."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.shutdown_simulator.return_value = {
            "success": False,
            "error": "Simulator is not running",
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(SimulatorHarness=mock_cls)
        result = self._run(
            runner,
            ["sim", "--action", "shutdown"],
            mocks,
        )

        assert result.exit_code == 1
        assert "Simulator is not running" in result.output

    def test_sim_default_action_is_boot(self, runner: CliRunner):
        """sim without --action defaults to boot."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.boot_simulator.return_value = {"success": True, "udid": "X"}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(SimulatorHarness=mock_cls)
        result = self._run(runner, ["sim"], mocks)

        assert result.exit_code == 0
        mock_instance.boot_simulator.assert_called_once()

    def test_sim_missing_deps_exits_1(self, runner: CliRunner):
        """sim exits 1 when SimulatorHarness is unavailable."""
        mocks = _patch_deps()
        mocks["SimulatorHarness"] = None
        result = self._run(runner, ["sim", "--action", "list"], mocks)

        assert result.exit_code == 1

    def test_sim_list_passes_os_filter(self, runner: CliRunner):
        """sim --action list passes --os option to list_devices."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.list_devices.return_value = []
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(SimulatorHarness=mock_cls)
        self._run(runner, ["sim", "--action", "list", "--os", "tvOS"], mocks)

        mock_instance.list_devices.assert_called_once_with(os="tvOS")


# ---------------------------------------------------------------------------
# testflight command
# ---------------------------------------------------------------------------


class TestTestFlightCommand:
    """Tests for 'forge ios testflight'."""

    def _run(self, runner: CliRunner, args: list[str], mocks: dict) -> object:
        with patch.multiple(MODULES, **mocks):
            return runner.invoke(app, args)

    def test_testflight_success_prints_build_id(self, runner: CliRunner, ipa_file: Path):
        """testflight prints build ID on successful upload."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.upload_to_testflight.return_value = {
            "success": True,
            "build_id": "build-xyz-123",
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(AppStoreHarness=mock_cls)
        result = self._run(
            runner,
            ["testflight", str(ipa_file)],
            mocks,
        )

        assert result.exit_code == 0
        assert "Upload succeeded" in result.output
        assert "build-xyz-123" in result.output

    def test_testflight_success_without_build_id(self, runner: CliRunner, ipa_file: Path):
        """testflight exits 0 even when build_id is absent in response."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.upload_to_testflight.return_value = {"success": True}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(AppStoreHarness=mock_cls)
        result = self._run(
            runner,
            ["testflight", str(ipa_file)],
            mocks,
        )

        assert result.exit_code == 0
        assert "Build ID" not in result.output

    def test_testflight_ipa_not_found_exits_1(self, runner: CliRunner, tmp_path: Path):
        """testflight exits 1 when the .ipa file does not exist."""
        mocks = _patch_deps()
        result = self._run(
            runner,
            ["testflight", str(tmp_path / "missing.ipa")],
            mocks,
        )

        assert result.exit_code == 1
        assert "IPA not found" in result.output

    def test_testflight_upload_failure_exits_1(self, runner: CliRunner, ipa_file: Path):
        """testflight exits 1 when upload_to_testflight returns failure."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.upload_to_testflight.return_value = {
            "success": False,
            "error": "Invalid API key",
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(AppStoreHarness=mock_cls)
        result = self._run(
            runner,
            ["testflight", str(ipa_file)],
            mocks,
        )

        assert result.exit_code == 1
        assert "Invalid API key" in result.output

    def test_testflight_api_key_forwarded(self, runner: CliRunner, ipa_file: Path):
        """--api-key and --api-issuer are forwarded to AppStoreHarness."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.upload_to_testflight.return_value = {"success": True}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(AppStoreHarness=mock_cls)
        self._run(
            runner,
            [
                "testflight",
                str(ipa_file),
                "--api-key",
                "KEY123",
                "--api-issuer",
                "ISSUER456",
            ],
            mocks,
        )

        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["api_key_id"] == "KEY123"
        assert call_kwargs["api_issuer_id"] == "ISSUER456"

    def test_testflight_release_notes_forwarded(self, runner: CliRunner, ipa_file: Path):
        """--release-notes is forwarded to upload_to_testflight."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.upload_to_testflight.return_value = {"success": True}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(AppStoreHarness=mock_cls)
        self._run(
            runner,
            [
                "testflight",
                str(ipa_file),
                "--release-notes",
                "Bug fixes and performance improvements",
            ],
            mocks,
        )

        call_kwargs = mock_instance.upload_to_testflight.call_args.kwargs
        assert call_kwargs["release_notes"] == "Bug fixes and performance improvements"

    def test_testflight_missing_deps_exits_1(self, runner: CliRunner, ipa_file: Path):
        """testflight exits 1 when ios_harness is unavailable."""
        mocks = _patch_deps()
        mocks["SimulatorHarness"] = None
        result = self._run(runner, ["testflight", str(ipa_file)], mocks)

        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    """Tests for 'forge ios status'."""

    def _run(self, runner: CliRunner, args: list[str], mocks: dict) -> object:
        with patch.multiple(MODULES, **mocks):
            return runner.invoke(app, args)

    def _make_quality_mock(self, check_results: dict) -> MagicMock:
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run_health_check.return_value = check_results
        mock_cls.return_value = mock_instance
        return mock_cls

    def test_status_all_passed_exits_0(self, runner: CliRunner, xcodeproj_dir: Path):
        """status exits 0 when all health checks pass."""
        mock_cls = self._make_quality_mock(
            {
                "build": {"passed": True, "message": "Clean build"},
                "test": {"passed": True, "message": "100% pass rate"},
            }
        )

        mocks = _patch_deps(QualityGatesHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        result = self._run(
            runner,
            ["status", "--project", str(project_path)],
            mocks,
        )

        assert result.exit_code == 0
        assert "build" in result.output
        assert "Clean build" in result.output

    def test_status_any_check_failed_exits_1(self, runner: CliRunner, xcodeproj_dir: Path):
        """status exits 1 when at least one check fails."""
        mock_cls = self._make_quality_mock(
            {
                "build": {"passed": True, "message": "OK"},
                "lint": {"passed": False, "message": "3 SwiftLint errors"},
            }
        )

        mocks = _patch_deps(QualityGatesHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        result = self._run(
            runner,
            ["status", "--project", str(project_path)],
            mocks,
        )

        assert result.exit_code == 1

    def test_status_auto_detects_xcodeproj(self, runner: CliRunner, xcodeproj_dir: Path):
        """status auto-detects .xcodeproj when --project is omitted."""
        mock_cls = self._make_quality_mock({"build": {"passed": True, "message": "OK"}})

        mocks = _patch_deps(QualityGatesHarness=mock_cls)
        with patch(f"{MODULES}.Path") as mock_path_cls:
            mock_path_cls.side_effect = lambda *a, **kw: Path(*a, **kw)
            mock_path_cls.cwd.return_value = xcodeproj_dir
            with patch.multiple(MODULES, **mocks):
                result = runner.invoke(app, ["status"])

        assert result.exit_code == 0

    def test_status_exits_1_when_no_xcodeproj_in_cwd(self, runner: CliRunner, tmp_path: Path):
        """status exits 1 when no .xcodeproj in cwd."""
        mocks = _patch_deps()
        with patch(f"{MODULES}.Path") as mock_path_cls:
            mock_path_cls.side_effect = lambda *a, **kw: Path(*a, **kw)
            mock_path_cls.cwd.return_value = tmp_path
            with patch.multiple(MODULES, **mocks):
                result = runner.invoke(app, ["status"])

        assert result.exit_code == 1
        assert "No .xcodeproj found" in result.output

    def test_status_checks_option_forwarded(self, runner: CliRunner, xcodeproj_dir: Path):
        """--checks option is split by comma and forwarded to run_health_check."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run_health_check.return_value = {
            "build": {"passed": True, "message": "OK"},
            "test": {"passed": True, "message": "OK"},
        }
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(QualityGatesHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        self._run(
            runner,
            ["status", "--project", str(project_path), "--checks", "build,test"],
            mocks,
        )

        call_kwargs = mock_instance.run_health_check.call_args.kwargs
        assert call_kwargs["checks"] == ["build", "test"]

    def test_status_default_checks_is_all(self, runner: CliRunner, xcodeproj_dir: Path):
        """Default --checks value is 'all', split to ['all']."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run_health_check.return_value = {"all": {"passed": True, "message": "OK"}}
        mock_cls.return_value = mock_instance

        mocks = _patch_deps(QualityGatesHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        self._run(
            runner,
            ["status", "--project", str(project_path)],
            mocks,
        )

        call_kwargs = mock_instance.run_health_check.call_args.kwargs
        assert call_kwargs["checks"] == ["all"]

    def test_status_missing_deps_exits_1(self, runner: CliRunner, xcodeproj_dir: Path):
        """status exits 1 when ios_harness is unavailable."""
        mocks = _patch_deps()
        mocks["SimulatorHarness"] = None
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        result = self._run(
            runner,
            ["status", "--project", str(project_path)],
            mocks,
        )

        assert result.exit_code == 1

    def test_status_table_includes_check_name_and_message(
        self, runner: CliRunner, xcodeproj_dir: Path
    ):
        """status table rows contain check names and detail messages."""
        mock_cls = self._make_quality_mock(
            {
                "coverage": {"passed": True, "message": "87% coverage"},
                "security": {"passed": False, "message": "ATS not enabled"},
            }
        )

        mocks = _patch_deps(QualityGatesHarness=mock_cls)
        project_path = xcodeproj_dir / "MyApp.xcodeproj"
        result = self._run(
            runner,
            ["status", "--project", str(project_path)],
            mocks,
        )

        assert "coverage" in result.output
        assert "87% coverage" in result.output
        assert "security" in result.output
        assert "ATS not enabled" in result.output


# ---------------------------------------------------------------------------
# Integration: ios app is a valid Typer app with all subcommands
# ---------------------------------------------------------------------------


class TestIosAppStructure:
    """Structural tests for the Typer app itself."""

    def test_app_has_bootstrap_command(self, runner: CliRunner):
        """bootstrap subcommand should be registered."""
        result = runner.invoke(app, ["bootstrap", "--help"])
        assert result.exit_code == 0
        assert "Bootstrap" in result.output or "bootstrap" in result.output.lower()

    def test_app_has_build_command(self, runner: CliRunner):
        """build subcommand should be registered."""
        result = runner.invoke(app, ["build", "--help"])
        assert result.exit_code == 0

    def test_app_has_test_command(self, runner: CliRunner):
        """test subcommand should be registered."""
        result = runner.invoke(app, ["test", "--help"])
        assert result.exit_code == 0

    def test_app_has_sim_command(self, runner: CliRunner):
        """sim subcommand should be registered."""
        result = runner.invoke(app, ["sim", "--help"])
        assert result.exit_code == 0

    def test_app_has_testflight_command(self, runner: CliRunner):
        """testflight subcommand should be registered."""
        result = runner.invoke(app, ["testflight", "--help"])
        assert result.exit_code == 0

    def test_app_has_status_command(self, runner: CliRunner):
        """status subcommand should be registered."""
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0

    def test_app_root_help_lists_all_commands(self, runner: CliRunner):
        """Invoking the app with --help lists all six commands."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd in ("bootstrap", "build", "test", "sim", "testflight", "status"):
            assert cmd in result.output
