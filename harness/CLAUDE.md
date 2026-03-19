# FORGE Harness - iOS Development Automation

**Scope: iOS only** (ADR-040 complete, 2026-03-09). All fleet, task, dispatch, and daemon operations use the `forge` Go CLI (`cmd/forge/`). This Python harness exists solely for iOS build/test/deploy automation.

## Quick Reference

```bash
# CLI invocation (from harness/ directory)
cd harness && uv run python -m forge_harness.cli_v2 ios <command>

# Or via the harness alias:
harness ios <command>
```

| Command | Purpose |
|---------|---------|
| `harness ios bootstrap MyApp --bundle-id com.forge.myapp` | Scaffold a new iOS project |
| `harness ios build --sim` | Build for simulator |
| `harness ios build --device` | Build for device (requires signing) |
| `harness ios test --coverage` | Run tests with coverage |
| `harness ios sim --action list` | List available simulators |
| `harness ios sim --action boot --device "iPhone 17 Pro"` | Boot a simulator |
| `harness ios sim --action shutdown --device "iPhone 17 Pro"` | Shutdown a simulator |
| `harness ios testflight ./build/MyApp.ipa` | Upload to TestFlight |

**Exit codes:** 0 success, 1 general error, 2 usage error, 10 build failed, 11 test failed, 12 simulator error, 13 project not found.

## Architecture

```
forge_harness/
├── cli_v2/
│   ├── __init__.py       # Click CLI group (ios command only)
│   ├── __main__.py       # Module entrypoint
│   └── ios.py            # iOS CLI (Typer app wrapping ios_harness)
└── ios_harness/
    ├── build_harness.py        # Xcode build automation
    ├── test_harness.py         # Test runner with coverage
    ├── simulator_harness.py    # Simulator lifecycle management
    ├── screenshot_automation.py # Automated screenshot capture
    ├── quality_gates.py        # Pre-deploy quality checks
    ├── appstore_harness.py     # App Store / TestFlight uploads
    ├── swift_codegen.py        # Swift code generation
    ├── feature_scaffold.py     # Feature module scaffolding
    ├── error_parser.py         # Xcode error parsing and formatting
    ├── project_bootstrap.py    # New project bootstrapping
    └── xcodegen_wrapper.py     # XcodeGen project file generation
```

## iOS Harness Modules

| Module | Class | Purpose |
|--------|-------|---------|
| `build_harness` | `BuildHarness` | Wraps xcodebuild for simulator and device builds |
| `test_harness` | `TestHarness` | Runs XCTest suites, collects results and coverage |
| `simulator_harness` | `SimulatorHarness` | Boot, shutdown, list, and manage iOS simulators |
| `screenshot_automation` | `ScreenshotAutomation` | Capture screenshots from running simulators |
| `quality_gates` | `QualityGatesHarness` | Pre-deploy checks (test pass rate, coverage, lint) |
| `appstore_harness` | `AppStoreHarness` | App Store Connect / TestFlight upload automation |
| `swift_codegen` | `SwiftCodeGen` | Generate Swift boilerplate (models, views, etc.) |
| `feature_scaffold` | `FeatureScaffold` | Scaffold new feature modules with tests |
| `error_parser` | `XcodeErrorParser` | Parse xcodebuild output into structured errors |
| `project_bootstrap` | `ProjectBootstrap` | Create new iOS projects from templates |
| `xcodegen_wrapper` | `XcodeGenWrapper` | Generate .xcodeproj from YAML specs |

## Default Targets

- **Simulator:** iPhone 17 Pro (iOS 26.2 SDK)
- **Build flag:** Always use `-quiet` to reduce xcodebuild noise
- **iOS nodes:** node-3 (48 GB, M-series) is the primary iOS build node

## Testing

```bash
cd harness && uv run pytest tests/ -v
cd harness && uv run pytest tests/ --cov=forge_harness --cov-report=term-missing
```

## What Moved to `forge` Go CLI

The following were removed from this harness per ADR-040 and ADR-014:

- **Fleet operations** (task, agent, dispatch, patrol, daemon) -- use `forge` CLI
- **Command Center** (`:8080`) -- replaced by `forged` daemon (`:8081`) per ADR-014
- **webhook_server** -- deleted
- **dashboard.py** (Python TUI) -- replaced by HTMX UI at `http://localhost:8081/ui`
- **All cli_v2 commands except `ios`** -- removed

See root `CLAUDE.md` for the full `forge` Go CLI reference.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `GITHUB_TOKEN` | GitHub API access (for iOS CI workflows) |

## Related Docs

| Doc | Purpose |
|-----|---------|
| `.claude/rules/ios-testing.md` | iOS testing rules and quick commands |
| `.claude/skills/ios-agent/SKILL.md` | `/ios-agent` skill for Claude agents |
| Root `CLAUDE.md` | Full FORGE system docs and `forge` Go CLI reference |
