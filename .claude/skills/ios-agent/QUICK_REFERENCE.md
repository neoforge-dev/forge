# iOS Agent Quick Reference

Quick command reference for iOS simulator and build automation using `xcrun simctl` and `forge_harness.ios_harness`.

## One-Liners (xcrun simctl)

```bash
# Get first booted device UDID
UDID=$(xcrun simctl list devices booted -j | python3 -c "import sys,json; devs=[d for ds in json.load(sys.stdin)['devices'].values() for d in ds if d['state']=='Booted']; print(devs[0]['udid'] if devs else '')")

# Boot iPhone 17 Pro
xcrun simctl boot "iPhone 17 Pro"

# Install app on booted simulator
xcrun simctl install booted /path/to/App.app

# Launch app
xcrun simctl launch booted com.codeswiftr.forge-terminal

# Take screenshot
xcrun simctl io booted screenshot ./screenshot.png

# Shutdown all simulators
xcrun simctl shutdown all
```

## Common Workflows

### Quick Build + Test
```bash
# Build and test in one go
cd /Users/bogdan/work/FORGE/codeswiftr-com/forge-terminal/ios
xcodebuild test -scheme ForgeTerminal \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -quiet 2>&1 | tail -30
```

### Visual Smoke Test
```bash
# Boot -> Install -> Launch -> Screenshot
xcrun simctl boot "iPhone 17 Pro" 2>/dev/null || true
sleep 3
xcrun simctl install booted /path/to/ForgeTerminal.app
xcrun simctl launch booted com.codeswiftr.forge-terminal
sleep 3
xcrun simctl io booted screenshot ./smoke-test.png
```

### Device Discovery
```bash
# List all available simulators
xcrun simctl list devices available

# List booted only
xcrun simctl list devices booted

# Check installed runtimes
xcrun simctl list runtimes
```

### App Management
```bash
# Launch with environment variables
xcrun simctl launch --console booted com.codeswiftr.forge-terminal

# Terminate app
xcrun simctl terminate booted com.codeswiftr.forge-terminal

# Uninstall app
xcrun simctl uninstall booted com.codeswiftr.forge-terminal

# Open URL in simulator
xcrun simctl openurl booted "https://example.com"
```

### Screenshot Management
```bash
# PNG screenshot
xcrun simctl io booted screenshot ./screenshot.png

# Timestamped screenshot
xcrun simctl io booted screenshot "./test_$(date +%Y%m%d-%H%M%S).png"

# Record video (stop with Ctrl+C)
xcrun simctl io booted recordVideo ./recording.mp4
```

## Python API (forge_harness.ios_harness)

```python
from forge_harness.ios_harness import SimulatorHarness, BuildHarness, TestHarness
from forge_harness.ios_harness import BuildConfig, TestConfig
from pathlib import Path

# Simulator
sim = SimulatorHarness(default_device="iPhone 17 Pro")
devices = await sim.list_devices()
device = await sim.get_device("iPhone 17 Pro")
await sim.boot_device("iPhone 17 Pro")
await sim.install_app(device.udid, Path("build/App.app"))
await sim.launch_app(device.udid, "com.example.app")
await sim.take_screenshot(device.udid, Path("./shot.png"))

# Build
build = BuildHarness(project_path=Path("ios"))
result = await build.build(BuildConfig(scheme="ForgeTerminal"))

# Test
test = TestHarness(project_path=Path("ios"))
result = await test.run_tests(TestConfig(scheme="ForgeTerminal"))
```

## Troubleshooting

```bash
# Kill stuck simulators
pkill -9 Simulator

# Reset simulator content
xcrun simctl erase all

# Check Xcode is selected
xcode-select -p

# Download missing simulator runtimes
xcodebuild -downloadAllPlatforms
```

## FORGE Bundle IDs

| App | Bundle ID |
|-----|-----------|
| Forge Terminal | `com.codeswiftr.forge-terminal` |
| Voice Coach | `com.brandfocus.voicecoach` |
| Math Sprinter | `com.brightharbor.mathsprinter` |
| Kiddo Rewards | `com.brightharbor.kiddorewards` |

## See Also

- Full skill docs: `.claude/skills/ios-agent/SKILL.md`
- Harness README: `harness/forge_harness/ios_harness/README.md`
- Path-scoped rule: `.claude/rules/ios-testing.md`
