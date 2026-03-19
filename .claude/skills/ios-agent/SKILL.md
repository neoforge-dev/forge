---
name: ios-agent
description: Automate iOS app testing on simulators and devices using forge_harness.ios_harness
auto_execute: true
disable-model-invocation: false
allowed-tools: [Bash, Read]
---

# iOS Agent Skill

> ⚠️ **REMEMBER**: Use `forge ios` CLI for all iOS automation:
> - `forge ios status --project <path>` - Health checks (build, test, lint, coverage)
> - `forge ios build --project <path> --scheme <name>` - Build
> - `forge ios test --project <path> --scheme <name>` - Run tests
> - DO NOT use raw xcodebuild/xcrun for automation - use the harness

Automate iOS app building, testing, and simulator management using the FORGE iOS Harness (`harness/forge_harness/ios_harness/`). This is the iOS equivalent of Agent Browser for native apps.

## Backend: ios_harness

The skill wraps the Python `forge_harness.ios_harness` package, which provides:

| Module | Class | Purpose |
|--------|-------|---------|
| `simulator_harness.py` | `SimulatorHarness` | Boot/shutdown, install, launch, screenshot |
| `build_harness.py` | `BuildHarness` | xcodebuild automation, Swift 6 strict concurrency |
| `test_harness.py` | `TestHarness` | XCTest execution, result parsing, coverage |
| `quality_gates.py` | `QualityGatesHarness` | iOS quality validation (@Observable, actors) |
| `screenshot_automation.py` | `ScreenshotAutomation` | App Store screenshot capture |
| `appstore_harness.py` | `AppStoreHarness` | Archive, sign, TestFlight |
| `swift_codegen.py` | `SwiftCodeGen` | Generate ViewModels, Actors, Views |
| `project_bootstrap.py` | `ProjectBootstrap` | Generate iOS projects from templates |
| `feature_scaffold.py` | `FeatureScaffold` | Scaffold complete MVVM features (auth, dashboard, settings) |
| `error_parser.py` | `XcodeErrorParser` | Parse xcodebuild errors into structured objects |

**Location:** `harness/forge_harness/ios_harness/`
**Docs:** `harness/forge_harness/ios_harness/README.md`

## Prerequisites

```bash
# Xcode Command Line Tools (required)
xcode-select --install

# Harness package (already installed in FORGE)
cd /Users/bogdan/work/FORGE/harness && uv sync
```

## Quick Commands (Shell)

For simple one-off operations, use `xcrun simctl` directly:

```bash
# List simulators
xcrun simctl list devices available -j | python3 -c "import sys,json; [print(f'{d[\"udid\"]} {d[\"name\"]} ({d[\"state\"]})') for devs in json.load(sys.stdin)['devices'].values() for d in devs if d.get('isAvailable')]"

# Boot simulator
xcrun simctl boot "iPhone 17 Pro"

# Install app
xcrun simctl install booted /path/to/App.app

# Launch app
xcrun simctl launch booted com.codeswiftr.forge-terminal

# Take screenshot
xcrun simctl io booted screenshot ./screenshot.png

# Shutdown
xcrun simctl shutdown booted
```

## Python API (Recommended for Automation)

For multi-step workflows, use the Python harness:

```python
from forge_harness.ios_harness import (
    SimulatorHarness, BuildHarness, TestHarness,
    BuildConfig, TestConfig, ScreenshotAutomation,
    create_standard_screenshot_specs,
)
from pathlib import Path

# --- Simulator Management ---
sim = SimulatorHarness(default_device="iPhone 17 Pro")

# List available devices
devices = await sim.list_devices()
booted = [d for d in devices if d.state.value == "Booted"]

# Boot a device
await sim.boot_device("iPhone 17 Pro")

# Get device by name
device = await sim.get_device("iPhone 17 Pro")

# Install and launch app
await sim.install_app(device.udid, Path("build/ForgeTerminal.app"))
result = await sim.launch_app(device.udid, "com.codeswiftr.forge-terminal")

# Take screenshot
await sim.take_screenshot(device.udid, Path("./screenshot.png"))

# --- Build Automation ---
build = BuildHarness(project_path=Path("ios"))
result = await build.build(BuildConfig(
    scheme="ForgeTerminal",
    destination="platform=iOS Simulator,name=iPhone 17 Pro",
    enable_strict_concurrency=True,
    clean_build=True,
))
print(f"Build {'succeeded' if result.success else 'failed'}")

# --- Test Execution ---
test = TestHarness(project_path=Path("ios"))
result = await test.run_tests(TestConfig(scheme="ForgeTerminal"))
print(f"Tests: {result.passed}/{result.total}, Coverage: {result.coverage}%")

# --- Screenshot Automation ---
screenshots = ScreenshotAutomation()
specs = create_standard_screenshot_specs()
await screenshots.capture_set(specs=specs)
```

## Workflows

### Workflow 1: Build + Test + Screenshot

```bash
#!/bin/bash
# Full CI-style workflow for any iOS project
set -e

PROJECT_DIR="$1"  # e.g., codeswiftr-com/forge-terminal/ios
SCHEME="$2"       # e.g., ForgeTerminal

cd /Users/bogdan/work/FORGE/$PROJECT_DIR

# Build
xcodebuild -scheme "$SCHEME" \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -quiet build 2>&1 | tail -5

# Test
xcodebuild test -scheme "$SCHEME" \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  2>&1 | tail -30

# Screenshot
xcrun simctl io booted screenshot "./test-screenshot-$(date +%s).png"
```

### Workflow 2: Visual Smoke Test

```bash
#!/bin/bash
# Boot simulator, install app, take screenshots at key screens
set -e

BUNDLE_ID="com.codeswiftr.forge-terminal"
APP_PATH="$1"

# Boot
xcrun simctl boot "iPhone 17 Pro" 2>/dev/null || true
sleep 3

# Install & launch
xcrun simctl install booted "$APP_PATH"
xcrun simctl launch booted "$BUNDLE_ID"
sleep 3

# Capture home screen
xcrun simctl io booted screenshot ./smoke-01-home.png

# Capture after interaction (tap coordinates vary by app)
# Use Read tool on screenshot to determine tap targets
```

### Workflow 3: Multi-Device Testing

```python
import asyncio
from forge_harness.ios_harness import SimulatorHarness, BuildHarness, BuildConfig
from pathlib import Path

async def test_on_devices():
    sim = SimulatorHarness()
    devices_to_test = ["iPhone 17 Pro", "iPad Air 11-inch (M2)"]

    for device_name in devices_to_test:
        device = await sim.get_device(device_name)
        if device:
            await sim.boot_device(device_name)
            result = await sim.launch_app(device.udid, "com.codeswiftr.forge-terminal")
            await sim.take_screenshot(device.udid, Path(f"./test-{device_name}.png"))
            print(f"{device_name}: {'OK' if result.success else 'FAIL'}")

asyncio.run(test_on_devices())
```

## FORGE iOS Projects

| Project | Bundle ID | Domain |
|---------|-----------|--------|
| Forge Terminal | `com.codeswiftr.forge-terminal` | codeswiftr-com |
| Calm Connect | `com.calmconnect.app` | calmconnect-io |
| Voice Coach | `com.brandfocus.voicecoach` | brandfocus-ai |
| Math Sprinter | `com.brightharbor.mathsprinter` | thebrightharbor-com |
| Kiddo Rewards | `com.brightharbor.kiddorewards` | thebrightharbor-com |

## Troubleshooting

### No simulators available
```bash
xcrun simctl list runtimes  # Check installed runtimes
xcodebuild -downloadAllPlatforms  # Download missing runtimes
```

### Build fails with signing errors
```bash
# Use simulator destination (no signing required)
xcodebuild -scheme X -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

### Simulator won't boot
```bash
pkill -9 Simulator  # Kill stuck simulator
xcrun simctl shutdown all  # Shutdown all
xcrun simctl boot "iPhone 17 Pro"  # Retry
```

## Related

- **Harness README:** `harness/forge_harness/ios_harness/README.md`
- **Quick Reference:** `.claude/skills/ios-agent/QUICK_REFERENCE.md`
- **Path-scoped rule:** `.claude/rules/ios-testing.md` (auto-loads for `ios/` directories)
- **Browser equivalent:** `/agent-browser` skill
