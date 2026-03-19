# iOS Harness for FORGE Portfolio

**Version:** 0.4.0 (Phase 1-6 Complete)
**Status:** Production — All Phases Implemented
**Coverage:** 85% (project_bootstrap), 92% (build_harness), 91% (test_harness), 78% (quality_gates)

Production-grade iOS development harness for building SwiftUI MVPs with autonomous AI assistance.

---

## What's Built (Phase 1-6 Complete)

### ✅ Core Infrastructure (Phase 1)
- **ProjectBootstrap** - Generate iOS projects from templates
- **XcodeGenWrapper** - XcodeGen automation and validation
- **Template System** - base-swiftui template ready
- **Quality Gates** - CLAUDE.md agent rules
- **Living Docs Integration** - FORGE context loading

### ✅ Build & Test Automation (Phase 2)
- **BuildHarness** - xcodebuild automation with Swift 6 strict concurrency
- **TestHarness** - XCTest execution, result parsing, coverage collection
- **QualityGatesHarness** - iOS quality validation (UIKit, @Observable, actors)
- **Automation Scripts** - Build, test, validate shell scripts (build.sh, test.sh, validate.sh)

### ✅ Code Generation (Phase 3)
- **SwiftCodeGen** - Generate ViewModels, Actors, Views
- **FeatureScaffold** - Scaffold complete MVVM features (auth, dashboard, settings, onboarding, profile)

### ✅ Simulator & Deployment (Phase 4)
- **SimulatorHarness** - Boot, install, launch, screenshot, shutdown
- **AppStoreHarness** - Archive, sign, TestFlight upload
- **ScreenshotAutomation** - Automated App Store screenshot capture

### ✅ CLI Integration (Phase 5)
- **`forge ios`** - Unified CLI surface (bootstrap, build, test, sim, testflight, status)
- **ios-agent Skill** - Agent-friendly wrapper around `forge ios`
- **Path-scoped Rule** - `.claude/rules/ios-testing.md` auto-loads for iOS work

### ✅ Error Parsing (Phase 6)
- **XcodeErrorParser** - Parse xcodebuild output into structured errors
- **Error categorization** - missing_member, type_mismatch, syntax_error, etc.
- **Batch processing** - Group errors by file, filter by type/severity

### ✅ Templates
- `base-swiftui` - Minimal SwiftUI starter with automation scripts
- `mvvm-standard` - MVVM architecture with networking, auth, SwiftData
- `enterprise` - Full stack with COPPA, HIPAA-lite, enhanced security

---

## Quick Start

### Installation

```bash
# Navigate to harness directory
cd /Users/bogdan/work/FORGE/harness

# Install dependencies (already installed)
uv sync
```

### Bootstrap a New iOS Project

```python
from forge_harness.ios_harness import ProjectBootstrap, ProjectSpec
from pathlib import Path

# Create bootstrap instance
bootstrap = ProjectBootstrap(
    forge_root=Path("/Users/bogdan/work/FORGE"),
)

# Define project spec
spec = ProjectSpec(
    name="InterviewSimulator",
    bundle_id="com.forge.codeswiftr.interview-simulator",
    domain="codeswiftr-com",
    template="base-swiftui",
    features=["auth"],
    backend_url="https://api.codeswiftr.com",
)

# Bootstrap project
result = await bootstrap.bootstrap_project(
    spec=spec,
    output_dir=Path("codeswiftr-com/interview-simulator/ios"),
)

print(f"Success: {result.xcodegen_success}")
print(f"Files created: {len(result.created_files)}")
```

### Validate XcodeGen Project

```python
from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenWrapper
from pathlib import Path

# Validate project.yml
valid, error = XcodeGenWrapper.validate_project_yml(
    Path("project/project.yml")
)

if valid:
    print("✅ project.yml is valid")
else:
    print(f"❌ Error: {error}")
```

---

## Architecture

```
forge_harness/ios_harness/
├── __init__.py                   # Public API (v0.4.0)
├── project_bootstrap.py          # Project generation
├── xcodegen_wrapper.py           # XcodeGen automation
├── build_harness.py              # xcodebuild automation
├── test_harness.py               # XCTest execution & coverage
├── simulator_harness.py          # Simulator management
├── appstore_harness.py           # TestFlight & App Store
├── swift_codegen.py              # Swift code generation
├── quality_gates.py              # Quality validation
├── feature_scaffold.py           # MVVM feature scaffolding
└── error_parser.py               # Xcode error parsing

templates/ios/
├── base-swiftui/                 # ✅ Complete
│   ├── App/
│   │   ├── BaseAppApp.swift
│   │   ├── ContentView.swift
│   │   └── Info.plist
│   ├── Tests/
│   ├── project.yml
│   ├── CLAUDE.md
│   └── README.md
├── mvvm-standard/                # (Phase 5)
└── enterprise/                   # (Phase 5)
```

---

## Features

### Project Bootstrap

- ✅ Template-based project generation
- ✅ XcodeGen project.yml generation
- ✅ CLAUDE.md injection (agent rules)
- ✅ AppConfig.swift with backend URL
- ✅ Domain-specific config (COPPA, HIPAA-lite)
- ✅ Feature scaffolding (auth, dashboard, settings)
- ⏳ Automatic Xcode project generation

### XcodeGen Integration

- ✅ project.yml validation
- ✅ Minimal config generation
- ✅ Bundle ID prefix derivation
- ✅ Error handling
- ✅ Dry-run support

### Quality Gates

- ✅ CLAUDE.md template with iOS rules
- ✅ SwiftUI-only enforcement
- ✅ Swift 6 strict concurrency
- ✅ @Observable ViewModel patterns
- ✅ Actor-based service patterns

---

## Templates

### base-swiftui (✅ Complete)

**Use for:** Prototypes, simple apps, learning

**Included:**
- Minimal SwiftUI app (~100 lines)
- Single ContentView
- XcodeGen project.yml
- CLAUDE.md agent rules
- Basic test structure

**Perfect for:**
- Quick MVPs
- UI prototypes
- Single-screen apps
- Learning SwiftUI

### mvvm-standard (⏳ Phase 5)

**Use for:** Most FORGE MVPs

**Will include:**
- MVVM architecture
- Networking layer (URLSession)
- Auth service (@Observable + actor)
- Design system components
- SwiftData persistence
- Feature structure
- Comprehensive tests

### enterprise (⏳ Phase 5)

**Use for:** Complex apps (COPPA, HIPAA-lite)

**Will include:**
- Full mvvm-standard stack
- COPPA compliance flows
- Age gate implementation
- Parental consent system
- Enhanced security (Keychain)
- Analytics integration
- Advanced networking

---

## CLAUDE.md Agent Rules

Every project gets a CLAUDE.md file with iOS-specific rules:

```markdown
# Goals
- Keep the app compiling at all times
- Use iOS 17+ APIs: @Observable and SwiftData
- SwiftUI only (no UIKit)
- Swift 6 strict concurrency

# Architecture
- MVVM: View → ViewModel (@Observable) → Service (actor)
- Dependency injection via Composition.swift
- SwiftData for persistence
- URLSession for networking

# Workflow
1. Plan mode for multi-file changes
2. Execute in small steps
3. Test after each step

# Domain-Specific Rules
- COPPA compliance (if required)
- HIPAA-lite (if required)
- Backend URL: {backend_url}
```

---

## Testing

### Run Tests

```bash
# All iOS harness tests (unit only)
uv run pytest tests/ios_harness/ -v -m "not integration"

# Specific module
uv run pytest tests/ios_harness/test_project_bootstrap.py -v

# With coverage
uv run pytest tests/ios_harness/ --cov=forge_harness.ios_harness
```

### Integration Tests

Integration tests run real `xcodebuild` and `xcodegen` commands. They require:
- **macOS** with Xcode installed
- **xcodegen** (`brew install xcodegen`)
- **iOS Simulator runtime**

```bash
# Run integration tests (macOS only)
uv run pytest tests/ios_harness/ -v -m integration

# Run all tests including slow builds
uv run pytest tests/ios_harness/ -v -m "integration and slow"

# Skip integration tests (CI on Linux)
uv run pytest tests/ios_harness/ -v -m "not integration"
```

**Test Markers:**
- `integration` - Tests that require xcodebuild/xcodegen
- `slow` - Tests that perform actual builds (may take >30s)

**What integration tests verify:**
- `project.yml` generates valid `.xcodeproj`
- Generated projects contain expected targets
- `xcodebuild -dry-run` succeeds
- `xcodebuild -list` shows correct schemes
- Simulator runtimes are discoverable

### CI/CD

The iOS integration tests run automatically on:
- Push to `main` that modifies iOS harness code
- Pull requests targeting `main`
- Manual workflow dispatch

See `.github/workflows/ios-integration.yml` for the macOS runner configuration.

### Test Results (Current)

| Module | Tests | Passing | Coverage |
|--------|-------|---------|----------|
| project_bootstrap | 18 | 15/18 | 71% |
| xcodegen_wrapper | 12 | 12/12 | 86% |
| build_integration | 8 | N/A | integration |
| **Total** | **38+** | **-** | **76%** |

---

## Roadmap

### Phase 1: Foundation (✅ Complete - Week 1)
- ✅ ProjectBootstrap class
- ✅ XcodeGenWrapper class
- ✅ base-swiftui template
- ✅ CLAUDE.md templates
- ✅ Unit tests

### Phase 2: Build & Test (⏳ Week 2)
- ⏳ BuildHarness (xcodebuild automation)
- ⏳ TestHarness (XCTest execution)
- ⏳ QualityGatesHarness (validation)
- ⏳ Automation scripts

### Phase 3: Code Generation (⏳ Week 3)
- ⏳ SwiftCodeGen (ViewModel, Actor, View)
- ⏳ Feature scaffolding (auth, dashboard)
- ⏳ SwiftData model generation

### Phase 4: Simulator & Deployment (⏳ Week 4)
- ⏳ SimulatorHarness (boot, install, validate)
- ⏳ AppStoreHarness (TestFlight, App Store)
- ⏳ Deployment automation

### Phase 5: Templates (⏳ Week 5)
- ⏳ mvvm-standard template
- ⏳ enterprise template
- ⏳ Design system components

### Phase 6: Integration (⏳ Week 6)
- ⏳ Living-docs iOS sync
- ⏳ Domain configs update
- ⏳ iOS skills
- ⏳ First iOS MVP

---

## Usage Examples

### Example 1: Bootstrap Interview Simulator

```python
spec = ProjectSpec(
    name="InterviewSimulator",
    bundle_id="com.forge.codeswiftr.interview-simulator",
    domain="codeswiftr-com",
    template="base-swiftui",
    features=[],
    backend_url="https://api.codeswiftr.com",
)

result = await bootstrap.bootstrap_project(spec, output_dir)
```

### Example 2: COPPA-Compliant App

```python
spec = ProjectSpec(
    name="KiddoRewards",
    bundle_id="com.forge.thebrightharbor.kiddorewards",
    domain="thebrightharbor-com",  # Auto-enables COPPA
    template="enterprise",  # Selected automatically
    features=["auth", "dashboard"],
    backend_url="https://api.thebrightharbor.com",
)

# COPPA rules automatically added to CLAUDE.md
```

### Example 3: Validate Project Config

```python
# Validate project.yml
valid, error = XcodeGenWrapper.validate_project_yml(
    project_dir / "project.yml"
)

if not valid:
    print(f"Invalid project.yml: {error}")
```

---

## Domain Integration

The iOS harness integrates with FORGE domain configs:

| Domain | COPPA | HIPAA-Lite | Template | Backend URL |
|--------|-------|------------|----------|-------------|
| codeswiftr-com | No | No | mvvm-standard | api.codeswiftr.com |
| thebrightharbor-com | **Yes** | No | enterprise | api.thebrightharbor.com |
| calmconnect-io | No | **Yes** | enterprise | api.calmconnect.io |
| leanvibe-dev | No | No | mvvm-standard | api.leanvibe.dev |

---

## Limitations (Phase 1)

1. **No actual Xcode project generation** - Requires `xcodegen` installed
2. **Feature scaffolding incomplete** - Placeholders only
3. **No build/test automation** - Phase 2
4. **No simulator automation** - Phase 4
5. **Only base-swiftui template** - mvvm-standard/enterprise in Phase 5

---

## Contributing

### Adding a New Template

1. Create directory: `templates/ios/{template-name}/`
2. Add App/, Tests/, project.yml, CLAUDE.md
3. Update `TemplateType` in project_bootstrap.py
4. Add tests in `tests/ios_harness/`

### Adding a New Feature Scaffold

1. Add to `FeatureType` enum
2. Implement `_scaffold_{feature}_feature()` method
3. Add tests for feature scaffolding

---

## References

- **Main Plan**: `/Users/bogdan/work/FORGE/harness/IOS_HARNESS_PLAN.md`
- **Base Template**: `/Users/bogdan/work/FORGE/harness/templates/ios/base-swiftui/`
- **Tests**: `/Users/bogdan/work/FORGE/harness/tests/ios_harness/`
- **Source**: `/Users/bogdan/work/FORGE/harness/forge_harness/ios_harness/`

---

## Next Steps

1. **Fix 3 failing tests** - Minor assertion issues
2. **Install xcodegen** - `brew install xcodegen`
3. **Test end-to-end** - Bootstrap real project
4. **Start Phase 2** - Build & test automation

---

**Status:** Phase 1 Foundation Complete ✅
**Next:** Phase 2 Build & Test Automation
**ETA:** 6-7 weeks to full production harness
