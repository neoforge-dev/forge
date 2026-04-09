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

## FORGE iOS Projects (Per-Project Profiles)

Use these canonical values — never guess scheme names or bundle IDs.

| Project | Scheme | Bundle ID | Sim Target | Domain |
|---------|--------|-----------|------------|--------|
| CalmConnect | `Scheduler` | `io.calmconnect.Scheduler` | iPhone 17 Pro | calmconnect-io |
| Forge Terminal | `ForgeTerminal` | `com.codeswiftr.forge-terminal` | iPhone 17 Pro | codeswiftr-com |
| Voice Coach | `VoiceCoach` | `com.brandfocus.voicecoach` | iPhone 17 Pro | brandfocus-ai |
| Math Sprinter | `MathSprinter` | `com.brightharbor.mathsprinter` | iPhone 17 Pro | thebrightharbor-com |
| Kiddo Rewards | `KiddoRewards` | `com.brightharbor.kiddorewards` | iPhone 17 Pro | thebrightharbor-com |

### Project Paths
```
ios/calm-connect-ios/Scheduler.xcodeproj
services/voice-coach/app/ios/VoiceCoach.xcodeproj
```

---

## Build Output Filtering

Raw xcodebuild output floods agent context. Always filter.

### Option 1: xcbeautify (recommended — install once)
```bash
# Install
brew install xcbeautify

# Use: pipe xcodebuild through it
xcodebuild -scheme Scheduler \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  build 2>&1 | xcbeautify --quieter

# --quieter: only errors and warnings (best for agents)
# --quiet: errors, warnings, and test results
```

### Option 2: grep filter (no install needed)
```bash
xcodebuild -scheme Scheduler \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  build 2>&1 | grep -E '(error:|warning:|BUILD SUCCEEDED|BUILD FAILED|Test Case|Executed)' | head -50
```

### Option 3: -quiet flag (suppresses too much)
```bash
# Only shows errors — misses warnings. Use xcbeautify instead.
xcodebuild -scheme Scheduler -destination '...' -quiet build
```

---

## Console Log Capture (Logging-First Debugging)

Agents debug via text, not breakpoints. Logging is king.

### Launch with Console Output
```bash
# Blocking — captures all console output to terminal
xcrun simctl launch --console-pty --terminate-running-process \
  booted io.calmconnect.Scheduler

# To file — for agent analysis
xcrun simctl launch --console-pty --terminate-running-process \
  booted io.calmconnect.Scheduler 2>&1 | tee /tmp/ios-console.log &
LOG_PID=$!

# ... interact with app or wait ...

kill $LOG_PID 2>/dev/null
# Agent reads /tmp/ios-console.log
```

### Structured Logging in Swift
```swift
import os

// Define per-subsystem loggers
extension Logger {
    static let auth = Logger(subsystem: "io.calmconnect.Scheduler", category: "auth")
    static let network = Logger(subsystem: "io.calmconnect.Scheduler", category: "network")
    static let ui = Logger(subsystem: "io.calmconnect.Scheduler", category: "ui")
}

// Usage
Logger.auth.info("Login attempt for user: \(userId, privacy: .public)")
Logger.network.error("API call failed: \(error.localizedDescription, privacy: .public)")
Logger.ui.debug("View appeared: \(viewName, privacy: .public)")
```

### The Logging-First Debug Loop

When debugging an iOS issue, follow this cycle:

```
1. IDENTIFY — Read the bug report / error
2. INJECT   — Add os.Logger statements at suspect points
3. BUILD    — xcodebuild ... 2>&1 | xcbeautify --quieter
4. RUN      — xcrun simctl launch --console-pty ... | tee /tmp/debug.log &
5. TRIGGER  — Navigate to the bug (deep link or manual)
6. CAPTURE  — Kill log process, read /tmp/debug.log
7. ANALYZE  — Find the root cause in logs
8. PATCH    — Fix the code
9. VERIFY   — Rebuild + run + confirm fix in logs
10. CLEAN   — Remove debug logging (keep useful logging)
```

### Deep Links for Navigation
```bash
# Open specific screen via URL scheme (if app supports it)
xcrun simctl openurl booted "calmconnect://appointments/new"
xcrun simctl openurl booted "calmconnect://therapist/profile/123"
```

### OSLog Analysis (on-device / advanced)
```bash
# Collect logs from device
sudo log collect --device --last 5m --output /tmp/device.logarchive

# Filter by subsystem
log show /tmp/device.logarchive --predicate 'subsystem == "io.calmconnect.Scheduler"' --style compact
```

---

## AXe Simulator Automation (Tier 2 — Experimental)

Programmatic UI interaction in the simulator. **Unstable** — coordinate-based taps can miss. Require human approval for long navigation runs.

### Prerequisites
```bash
# Install AXe (Accessibility Explorer)
brew install ax  # or build from source: https://github.com/nicklockwood/AXe

# Install ImageMagick for screenshot processing
brew install imagemagick
```

### Basic Operations
```bash
# Tap at coordinates (x, y)
ax tap 200 400

# Swipe
ax swipe 200 600 200 200  # from (200,600) to (200,200) = scroll up

# Swipe from left edge (back navigation)
ax swipe 5 400 200 400

# Type text
ax type "hello@example.com"
```

### Agent-Safe Automation Flow

Always: screenshot → identify target → act → verify.

```bash
#!/bin/bash
# 1. Screenshot current state
xcrun simctl io booted screenshot /tmp/step1.png

# 2. Agent analyzes screenshot (via Read tool on the PNG)
#    Identifies tap target coordinates

# 3. Tap
ax tap $X $Y

# 4. Wait for UI to settle
sleep 0.5

# 5. Verify with another screenshot
xcrun simctl io booted screenshot /tmp/step2.png

# 6. Agent verifies expected state
```

### Screenshot Processing
```bash
# Resize for faster agent analysis (1x instead of 3x)
magick /tmp/screen.png -resize 33.333% /tmp/screen_1x.png

# Diff two screenshots to verify state change
magick compare /tmp/step1.png /tmp/step2.png /tmp/diff.png
```

### Reliability Notes
- Coordinate taps fail ~10-15% of the time due to animations
- Always add `sleep 0.3-0.5` after taps before screenshotting
- Prefer deep links (`openurl`) over tap sequences when possible
- For forms: `xcrun simctl keyup/keydown` can be more reliable than AXe for text input
- **Never run unattended long AXe sequences** — they compound errors

### Simctl Keyboard Input (Alternative to AXe for Text)
```bash
# Paste text from clipboard (most reliable for forms)
echo "test@example.com" | pbcopy
xcrun simctl keyup booted Cmd-V
```

---

## XcodeGen / Tuist — Declarative Project Generation

For **new iOS projects**, use XcodeGen to avoid agents touching `.xcodeproj` XML.

### Setup (New Project)
```bash
brew install xcodegen

# Create project.yml
cat > project.yml << 'EOF'
name: MyApp
options:
  bundleIdPrefix: com.forge
  deploymentTarget:
    iOS: "18.2"
targets:
  MyApp:
    type: application
    platform: iOS
    sources: [Sources]
    settings:
      SWIFT_STRICT_CONCURRENCY: complete
  MyAppTests:
    type: bundle.unit-test
    platform: iOS
    sources: [Tests]
    dependencies:
      - target: MyApp
EOF

# Generate .xcodeproj from YAML
xcodegen generate
```

### Workflow for Agents
```
1. Agent edits project.yml (add source groups, dependencies, targets)
2. Agent runs: xcodegen generate
3. Agent builds: xcodebuild -scheme MyApp ...
4. Never touch .xcodeproj XML directly
```

### Retrofitting Existing Projects
For existing projects like CalmConnect (85% done):
- **NOT recommended** — too disruptive at this stage
- Instead, use file-system sync (Xcode 15+ feature) to auto-detect new files
- Save XcodeGen for the next iOS project from scratch

### SPM Modularization (For Scaling)
```
MyApp/
├── project.yml           # XcodeGen config
├── Sources/
│   └── App/              # Main app target (thin shell)
├── Packages/
│   ├── FeatureAuth/      # SPM package — login, signup
│   ├── FeatureSchedule/  # SPM package — appointments
│   ├── CoreNetwork/      # SPM package — API client
│   └── CoreDesign/       # SPM package — design system
└── Tests/
```

Each feature module has its own test target → agents can build/test in isolation.

---

## Capability Tiers

| Tier | Capability | Status | When to Use |
|------|-----------|--------|-------------|
| **0** | Build + logs | **Baseline** | Every iOS task |
| **1** | Screenshots | **Default** | Visual verification, smoke tests |
| **2** | AXe simulator input | **Experimental** | E2E flows, form testing |
| **3** | On-device loop | **Manual** | TestFlight prep, device-specific bugs |

---

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

### xcbeautify not installed
```bash
brew install xcbeautify  # macOS
# Fallback: use grep filter (see Build Output Filtering above)
```

## Related

- **Harness README:** `harness/forge_harness/ios_harness/README.md`
- **Quick Reference:** `.claude/skills/ios-agent/QUICK_REFERENCE.md`
- **Path-scoped rule:** `.claude/rules/ios-testing.md` (auto-loads for `ios/` directories)
- **Design skill:** `/ios-design` for HIG-compliant SwiftUI patterns
- **Browser equivalent:** `/agent-browser` skill
