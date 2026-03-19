---
description: iOS build, test, and simulator automation rules
globs:
  - "**/ios/**"
  - "**/*.xcodeproj/**"
  - "**/*.swift"
---

# iOS Testing & Automation

When working in iOS project directories, use the FORGE iOS Harness for build/test/simulator automation.

## Tools Available

| Need | Use | Location |
|------|-----|----------|
| Build automation | `BuildHarness` | `harness/forge_harness/ios_harness/build_harness.py` |
| Run tests | `TestHarness` | `harness/forge_harness/ios_harness/test_harness.py` |
| Simulator control | `SimulatorHarness` | `harness/forge_harness/ios_harness/simulator_harness.py` |
| Screenshots | `ScreenshotAutomation` | `harness/forge_harness/ios_harness/screenshot_automation.py` |
| Quality gates | `QualityGatesHarness` | `harness/forge_harness/ios_harness/quality_gates.py` |
| App Store deploy | `AppStoreHarness` | `harness/forge_harness/ios_harness/appstore_harness.py` |
| Code generation | `SwiftCodeGen` | `harness/forge_harness/ios_harness/swift_codegen.py` |
| Feature scaffold | `FeatureScaffold` | `harness/forge_harness/ios_harness/feature_scaffold.py` |
| Error parsing | `XcodeErrorParser` | `harness/forge_harness/ios_harness/error_parser.py` |
| Skill shortcut | `/ios-agent` | `.claude/skills/ios-agent/SKILL.md` |

## Quick Commands

```bash
# Build for simulator
xcodebuild -scheme SCHEME -destination 'platform=iOS Simulator,name=iPhone 17 Pro' -quiet build

# Run tests
xcodebuild test -scheme SCHEME -destination 'platform=iOS Simulator,name=iPhone 17 Pro'

# Take screenshot
xcrun simctl io booted screenshot ./screenshot.png
```

## Rules

- Always build for simulator (no signing required) unless deploying to device
- Use `-quiet` flag for builds to reduce output noise
- Run tests after any code change before committing
- Use `iPhone 17 Pro` as default simulator target (iOS 26.2 SDK)
- For multi-step automation, prefer the Python `ios_harness` API over raw shell commands
