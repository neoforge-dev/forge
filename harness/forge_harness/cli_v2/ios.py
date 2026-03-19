"""
forge ios - iOS development orchestration CLI

High-level wrapper around forge_harness.ios_harness async modules.

Examples:

    # Bootstrap new iOS project
    forge ios bootstrap MyApp --bundle-id com.forge.myapp --org com.forge

    # Build for simulator
    forge ios build --sim

    # Build for device
    forge ios build --device

    # Run tests
    forge ios test --coverage

    # List available simulators
    forge ios sim --action list

    # Boot a simulator
    forge ios sim --action boot --device "iPhone 17 Pro"

    # Shutdown simulator
    forge ios sim --action shutdown --device "iPhone 17 Pro"

    # Upload to TestFlight
    forge ios testflight ./build/MyApp.ipa

Exit Codes:

    0   Success
    1   General error (ios_harness not installed)
    2   Usage error (invalid arguments)
    10  Build failed
    11  Test failed
    12  Simulator error
    13  Project not found
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

try:
    from forge_harness.ios_harness import (
        AppStoreHarness,
        BuildConfig,
        BuildHarness,
        ProjectBootstrap,
        ProjectSpec,
        QualityGatesHarness,
        TestConfig,
        TestHarness,
        UploadConfig,
    )
except Exception as exc:  # pragma: no cover - graceful CLI fallback
    AppStoreHarness = None
    BuildConfig = None
    BuildHarness = None
    ProjectBootstrap = None
    ProjectSpec = None
    QualityGatesHarness = None
    TestConfig = None
    TestHarness = None
    UploadConfig = None
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None

try:
    from forge_harness.ios_harness import SimulatorHarness
except Exception as exc:  # pragma: no cover - graceful CLI fallback
    SimulatorHarness = None
    if _IMPORT_ERROR is None:
        _IMPORT_ERROR = exc

app = typer.Typer(
    name="ios",
    help="iOS development orchestration (thin wrapper around ios_harness)",
    no_args_is_help=True,
)
console = Console()

_DEFAULT_SIM_DESTINATION = "platform=iOS Simulator,name=iPhone 17 Pro"
_FORGE_ROOT = Path(__file__).resolve().parents[3]


def _run_async(coro):
    return asyncio.run(coro)


def check_dependencies() -> None:
    """Check if ios_harness modules are available."""
    required = (
        SimulatorHarness,
        BuildHarness,
        TestHarness,
        ProjectBootstrap,
        AppStoreHarness,
        QualityGatesHarness,
    )
    if any(item is None for item in required):
        console.print("[red]✗[/red] ios_harness modules are not importable")
        console.print("[yellow]Expected:[/yellow] harness/forge_harness/ios_harness/")
        if _IMPORT_ERROR is not None:
            console.print(f"[dim]Import error: {_IMPORT_ERROR}[/dim]")
        raise typer.Exit(1)


def _resolve_project_paths(project: Path | None) -> tuple[Path, Path]:
    """
    Resolve project directory and .xcodeproj path.

    Accepts either:
    - a .xcodeproj path
    - a directory containing a .xcodeproj
    - None (auto-detect in current directory)
    """
    if project is None:
        project_dir = Path.cwd()
        projects = list(project_dir.glob("*.xcodeproj"))
        if not projects:
            console.print("[red]✗[/red] No .xcodeproj found in current directory")
            raise typer.Exit(1)
        return project_dir, projects[0]

    candidate = project.expanduser().resolve()
    if candidate.suffix == ".xcodeproj":
        if not candidate.exists():
            console.print(f"[red]✗[/red] Project not found: {candidate}")
            raise typer.Exit(1)
        return candidate.parent, candidate

    if candidate.is_dir():
        projects = list(candidate.glob("*.xcodeproj"))
        if not projects:
            console.print(f"[red]✗[/red] No .xcodeproj found in: {candidate}")
            raise typer.Exit(1)
        return candidate, projects[0]

    console.print(f"[red]✗[/red] Invalid project path: {candidate}")
    raise typer.Exit(1)


def _resolve_scheme(project_dir: Path, explicit_scheme: str | None) -> str | None:
    if explicit_scheme:
        return explicit_scheme

    build_handler = BuildHarness(project_dir)
    schemes = _run_async(build_handler.list_schemes())
    return schemes[0] if schemes else None


def _format_build_failure(result: Any) -> str:
    if getattr(result, "errors", None):
        first = result.errors[0]
        file_part = f"{first.file_path}:{first.line}" if first.file_path else "build"
        return f"{file_part} - {first.message}"
    if getattr(result, "stderr", None):
        return result.stderr.strip().splitlines()[-1]
    return "unknown build failure"


@app.command()
def bootstrap(
    name: str = typer.Argument(..., help="Project name (e.g. ForgeTerminal)"),
    bundle_id: str | None = typer.Option(None, help="Bundle identifier"),
    org: str = typer.Option("com.forge", help="Organization identifier"),
    output_dir: Path = typer.Option(Path.cwd(), help="Output directory"),
    template: str = typer.Option("app", help="Template: app, framework, test"),
    domain: str = typer.Option("codeswiftr-com", help="FORGE domain key"),
    backend_url: str = typer.Option("http://localhost:8000", help="Backend URL"),
):
    """
    Bootstrap new iOS project with best practices.
    """
    check_dependencies()

    template_map = {
        "app": "base-swiftui",
        "framework": "mvvm-standard",
        "test": "base-swiftui",
    }
    resolved_template = template_map.get(template, template)
    supported_templates = {"base-swiftui", "mvvm-standard", "enterprise"}
    if resolved_template not in supported_templates:
        console.print(
            "[red]✗[/red] Invalid template. Use one of: "
            "app, framework, test, base-swiftui, mvvm-standard, enterprise"
        )
        raise typer.Exit(1)

    project_path = output_dir / name
    spec = ProjectSpec(
        name=name,
        bundle_id=bundle_id or f"{org}.{name}",
        domain=domain,
        template=resolved_template,
        features=[],
        backend_url=backend_url,
    )
    bootstrap_handler = ProjectBootstrap(forge_root=_FORGE_ROOT)

    console.print(f"[cyan]Bootstrapping iOS project:[/cyan] {name}")
    result = _run_async(
        bootstrap_handler.bootstrap_project(
            spec=spec,
            output_dir=project_path,
            overwrite=False,
        )
    )

    if result.errors:
        console.print(f"[red]✗[/red] Bootstrap failed: {result.errors[0]}")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Project created: {result.project_path}")
    console.print(f"[green]✓[/green] Template: {result.template_used}")
    console.print(
        "[green]✓[/green] XcodeGen: "
        + ("succeeded" if result.xcodegen_success else "not generated")
    )
    if result.features_scaffolded:
        console.print(f"[dim]Features: {', '.join(result.features_scaffolded)}[/dim]")


@app.command()
def build(
    project: Path | None = typer.Option(None, help="Path to .xcodeproj or project directory"),
    scheme: str | None = typer.Option(None, help="Scheme name"),
    sim: bool = typer.Option(False, help="Build for simulator"),
    device: bool = typer.Option(False, help="Build for device"),
    destination: str | None = typer.Option(None, help="Custom destination"),
    clean: bool = typer.Option(False, help="Clean before build"),
    quiet: bool = typer.Option(False, help="Suppress build output"),
):
    """
    Build iOS project for simulator or device.
    """
    check_dependencies()

    project_dir, xcodeproj = _resolve_project_paths(project)
    resolved_scheme = _resolve_scheme(project_dir, scheme)
    if not resolved_scheme:
        console.print("[red]✗[/red] Could not resolve an Xcode scheme")
        raise typer.Exit(1)

    resolved_destination = destination
    if not resolved_destination:
        resolved_destination = (
            "generic/platform=iOS" if device and not sim else _DEFAULT_SIM_DESTINATION
        )

    if quiet:
        console.print("[dim]Note: --quiet is accepted but full quiet mode is not implemented.[/dim]")

    console.print(f"[cyan]Building:[/cyan] {xcodeproj.name} ({resolved_scheme})")
    build_handler = BuildHarness(project_dir)
    config = BuildConfig(
        scheme=resolved_scheme,
        destination=resolved_destination,
        clean_build=clean,
        enable_strict_concurrency=False,
    )
    result = _run_async(build_handler.build(config))

    if result.success:
        console.print("[green]✓[/green] Build succeeded")
        console.print(f"[dim]Build time: {result.build_time_seconds}s[/dim]")
        return

    console.print(f"[red]✗[/red] Build failed: {_format_build_failure(result)}")
    raise typer.Exit(1)


@app.command()
def test(
    project: Path | None = typer.Option(None, help="Path to .xcodeproj or project directory"),
    scheme: str | None = typer.Option(None, help="Scheme name"),
    test_plan: str | None = typer.Option(None, help="Test plan name"),
    only_testing: str | None = typer.Option(None, help="Specific test target (best-effort)"),
    parallel: bool = typer.Option(True, help="Run tests in parallel"),
    coverage: bool = typer.Option(False, help="Generate code coverage"),
):
    """
    Run unit and UI tests.
    """
    check_dependencies()

    if only_testing:
        console.print(
            "[yellow]only_testing is not currently supported by ios_harness;"
            " running full suite.[/yellow]"
        )

    project_dir, xcodeproj = _resolve_project_paths(project)
    resolved_scheme = _resolve_scheme(project_dir, scheme)
    if not resolved_scheme:
        console.print("[red]✗[/red] Could not resolve an Xcode scheme")
        raise typer.Exit(1)

    test_handler = TestHarness(project_dir)
    config = TestConfig(
        scheme=resolved_scheme,
        destination=_DEFAULT_SIM_DESTINATION,
        enable_code_coverage=coverage,
        test_plan=test_plan,
        parallel_testing=parallel,
    )

    console.print(f"[cyan]Running tests:[/cyan] {xcodeproj.name} ({resolved_scheme})")
    result = _run_async(test_handler.test(config))

    total = result.total_tests
    passed = result.passed_tests
    failed = result.failed_tests
    if result.success:
        console.print(f"[green]✓[/green] Tests completed: {passed}/{total} passed")
        if coverage and result.coverage is not None:
            console.print(f"[dim]Coverage: {result.coverage.line_coverage:.2f}%[/dim]")
        return

    console.print(f"[red]✗[/red] Tests failed: {passed}/{total} passed")
    if failed:
        console.print(f"[red]✗[/red] {failed} test(s) failed")
    if result.stderr.strip():
        console.print(f"[dim]{result.stderr.strip().splitlines()[-1]}[/dim]")
    raise typer.Exit(1)


@app.command()
def sim(
    device: str = typer.Option("iPhone 17 Pro", help="Device name"),
    os: str = typer.Option("iOS", help="OS/runtime filter (e.g. iOS 26.2)"),
    action: str = typer.Option("boot", help="Action: boot, shutdown, list"),
):
    """
    Manage iOS simulators.
    """
    check_dependencies()

    sim_handler = SimulatorHarness(default_device=device)
    normalized_action = action.lower().strip()

    if normalized_action == "list":
        runtime_filter = None if os.lower() == "ios" else os
        devices = _run_async(sim_handler.list_devices(runtime=runtime_filter))

        table = Table(title="Available Simulators")
        table.add_column("Name", style="cyan")
        table.add_column("UDID", style="dim")
        table.add_column("State", style="green")
        table.add_column("Runtime", style="magenta")

        for dev in devices:
            table.add_row(dev.name, dev.udid, dev.state.value, dev.runtime)

        console.print(table)
        return

    if normalized_action == "boot":
        console.print(f"[cyan]Booting:[/cyan] {device}")
        try:
            booted = _run_async(sim_handler.boot(device_name=device))
        except Exception as exc:
            console.print(f"[red]✗[/red] Failed: {exc}")
            raise typer.Exit(1)
        console.print(f"[green]✓[/green] Simulator booted: {booted.udid}")
        return

    if normalized_action == "shutdown":
        console.print(f"[cyan]Shutting down:[/cyan] {device}")
        if device.lower() == "all":
            cmd = ["xcrun", "simctl", "shutdown", "all"]
            proc = _run_async(
                asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            )
            _run_async(proc.communicate())
            if proc.returncode != 0:
                console.print("[red]✗[/red] Failed to shutdown all simulators")
                raise typer.Exit(1)
            console.print("[green]✓[/green] All simulators shut down")
            return

        ok = _run_async(sim_handler.shutdown(device_name=device))
        if ok:
            console.print("[green]✓[/green] Simulator shut down")
            return
        console.print(f"[red]✗[/red] Failed to shutdown simulator: {device}")
        raise typer.Exit(1)

    console.print("[red]✗[/red] Invalid action. Use: list, boot, shutdown")
    raise typer.Exit(1)


@app.command()
def testflight(
    ipa: Path = typer.Argument(..., help="Path to .ipa file"),
    api_key: str | None = typer.Option(None, help="App Store Connect API key ID"),
    api_issuer: str | None = typer.Option(None, help="API issuer ID"),
    api_key_path: Path | None = typer.Option(
        None, help="Path to App Store Connect .p8 API key file"
    ),
    release_notes: str | None = typer.Option(None, help="Release notes (logged only)"),
):
    """
    Upload build to TestFlight.
    """
    check_dependencies()

    if not ipa.exists():
        console.print(f"[red]✗[/red] IPA not found: {ipa}")
        raise typer.Exit(1)

    resolved_api_key = api_key or os.environ.get("APPSTORE_API_KEY_ID")
    resolved_issuer = api_issuer or os.environ.get("APPSTORE_API_ISSUER_ID")
    resolved_key_path = api_key_path or (
        Path(os.environ["APPSTORE_API_KEY_PATH"])
        if os.environ.get("APPSTORE_API_KEY_PATH")
        else None
    )

    if not resolved_api_key or not resolved_issuer:
        console.print("[red]✗[/red] Missing API credentials")
        console.print(
            "[yellow]Provide --api-key/--api-issuer or set APPSTORE_API_KEY_ID and "
            "APPSTORE_API_ISSUER_ID[/yellow]"
        )
        raise typer.Exit(1)
    if resolved_key_path is None:
        console.print("[red]✗[/red] Missing API key path (--api-key-path or APPSTORE_API_KEY_PATH)")
        raise typer.Exit(1)
    if not resolved_key_path.exists():
        console.print(f"[red]✗[/red] API key file not found: {resolved_key_path}")
        raise typer.Exit(1)

    if release_notes:
        console.print("[dim]Release notes are accepted but not pushed by this command.[/dim]")

    console.print(f"[cyan]Uploading to TestFlight:[/cyan] {ipa.name}")
    appstore_handler = AppStoreHarness(workspace_dir=Path.cwd() / ".forge_ios_artifacts")
    upload_result = _run_async(
        appstore_handler.upload_to_testflight(
            UploadConfig(
                ipa_path=ipa,
                api_key_id=resolved_api_key,
                api_issuer_id=resolved_issuer,
                api_key_path=resolved_key_path,
            )
        )
    )

    if upload_result.success:
        console.print("[green]✓[/green] Upload succeeded")
        if upload_result.build_id:
            console.print(f"[dim]Build ID: {upload_result.build_id}[/dim]")
        return

    console.print("[red]✗[/red] Upload failed")
    if upload_result.errors:
        console.print(f"[dim]{upload_result.errors[0]}[/dim]")
    raise typer.Exit(1)


@app.command()
def status(
    project: Path | None = typer.Option(None, help="Path to .xcodeproj or project directory"),
    checks: str = typer.Option("all", help="Checks: all, build, test, lint, coverage"),
):
    """
    Run health checks on iOS project.
    """
    check_dependencies()

    project_dir, xcodeproj = _resolve_project_paths(project)
    requested_checks = {part.strip().lower() for part in checks.split(",") if part.strip()}
    valid_checks = {"all", "build", "test", "lint", "coverage"}
    unknown = requested_checks - valid_checks
    if unknown:
        console.print(f"[red]✗[/red] Unknown checks: {', '.join(sorted(unknown))}")
        raise typer.Exit(1)
    if "all" in requested_checks or not requested_checks:
        requested_checks = {"build", "test", "lint", "coverage"}

    console.print(f"[cyan]Checking health:[/cyan] {xcodeproj.name}")
    results: dict[str, dict[str, Any]] = {}

    schemes: list[str] | None = None
    test_targets: list[str] | None = None

    if "build" in requested_checks:
        build_handler = BuildHarness(project_dir)
        schemes = _run_async(build_handler.list_schemes())
        results["build"] = {
            "passed": len(schemes) > 0,
            "message": f"schemes={len(schemes)}",
        }

    if "test" in requested_checks:
        test_handler = TestHarness(project_dir)
        test_targets = _run_async(test_handler.list_test_targets())
        results["test"] = {
            "passed": len(test_targets) > 0,
            "message": f"targets={len(test_targets)}",
        }

    if "lint" in requested_checks:
        quality_handler = QualityGatesHarness(project_dir)
        report = _run_async(quality_handler.validate(strict_mode=False))
        results["lint"] = {
            "passed": report.errors == 0,
            "message": (
                f"errors={report.errors}, warnings={report.warnings}, infos={report.infos}"
            ),
        }

    if "coverage" in requested_checks:
        has_xcrun = shutil.which("xcrun") is not None
        if test_targets is None:
            test_handler = TestHarness(project_dir)
            test_targets = _run_async(test_handler.list_test_targets())
        results["coverage"] = {
            "passed": has_xcrun and len(test_targets) > 0,
            "message": (
                f"xcrun={'yes' if has_xcrun else 'no'}, "
                f"test_targets={len(test_targets)}"
            ),
        }

    table = Table(title="iOS Project Health")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Details", style="dim")

    for check_name, data in results.items():
        passed = bool(data.get("passed"))
        style = "green" if passed else "red"
        table.add_row(
            check_name,
            f"[{style}]{'✓' if passed else '✗'}[/{style}]",
            str(data.get("message", "")),
        )

    console.print(table)

    if not all(bool(data.get("passed")) for data in results.values()):
        raise typer.Exit(1)
