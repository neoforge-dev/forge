---
name: ios-design
description: Create polished, HIG-compliant SwiftUI interfaces for FORGE iOS apps. Covers design systems, component patterns, accessibility, and platform-native aesthetics for CalmConnect, Forge Terminal, Voice Coach, and other iOS products.
auto_execute: true
disable-model-invocation: false
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep]
---

# iOS Design for FORGE Portfolio

Create polished, platform-native iOS interfaces that feel at home on Apple devices. This skill guides SwiftUI design decisions, component patterns, and visual aesthetics for all FORGE iOS apps.

## When to Use

- Designing new SwiftUI views for any FORGE iOS app
- Building reusable component libraries for iOS projects
- Implementing dark mode, Dynamic Type, and accessibility
- Creating onboarding flows, settings screens, dashboards
- Polishing existing views for App Store readiness
- Reviewing iOS UI for HIG compliance

## FORGE iOS Products

| App | Bundle ID | Aesthetic | Stage |
|-----|-----------|-----------|-------|
| **CalmConnect** | `io.calmconnect.Scheduler` | Calming Wellness | 95% complete |
| **Forge Terminal** | `com.codeswiftr.forge-terminal` | Tech Professional | App Store pending |
| **Voice Coach** | `com.brandfocus.voicecoach` | Warm Educational | Deploy-ready |

## Design System by Domain

### CalmConnect — Calming Wellness
```swift
// Colors
extension Color {
    static let ccPrimary = Color("Primary")       // Teal #0D9488
    static let ccBackground = Color(.systemBackground)
    static let ccSecondaryBg = Color(.secondarySystemBackground)
    static let ccAccent = Color("Accent")         // Purple #7C3AED
}

// Typography: SF Pro (system) — clean, accessible
// Shapes: Rounded (12-16pt corner radius)
// Spacing: Generous (16-24pt padding)
// Motion: Gentle, slow transitions (0.3s ease-in-out)
// Mood: Calm, reassuring, spacious
```

### Forge Terminal — Tech Professional
```swift
// Colors: Dark mode dominant
// Terminal green accents on dark backgrounds
// Monospace typography for terminal content
// Sharp corners (4-8pt radius), dense information
// Motion: Snappy, functional (0.15s ease-out)
// Mood: Professional, precise, powerful
```

### Voice Coach — Warm Educational
```swift
// Colors: Warm tones, encouraging oranges/greens
// Friendly rounded shapes (16-20pt radius)
// Larger text, generous spacing for readability
// Motion: Playful micro-interactions, progress celebrations
// Mood: Encouraging, friendly, motivating
```

## SwiftUI Component Patterns

### Card Pattern (Most-Used)
```swift
struct ContentCard<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        content
            .padding(16)
            .background(Color(.secondarySystemBackground))
            .cornerRadius(12)
    }
}
```

### Section Header
```swift
struct SectionHeader: View {
    let title: String
    let icon: String?

    var body: some View {
        HStack(spacing: 8) {
            if let icon {
                Image(systemName: icon)
                    .foregroundColor(.accentColor)
            }
            Text(title)
                .font(.headline)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}
```

### Stat Card (Dashboard Pattern)
```swift
struct StatCard: View {
    let title: String
    let value: String
    let subtitle: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.subheadline)
                .foregroundColor(.secondary)
            Text(value)
                .font(.title2)
                .fontWeight(.bold)
                .foregroundColor(color)
            Text(subtitle)
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Color(.secondarySystemBackground))
        .cornerRadius(12)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(title): \(value) \(subtitle)")
    }
}
```

### Empty State
```swift
struct EmptyStateView: View {
    let icon: String
    let title: String
    let message: String
    let actionTitle: String?
    let action: (() -> Void)?

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: icon)
                .font(.system(size: 48))
                .foregroundColor(.secondary)
            Text(title)
                .font(.headline)
            Text(message)
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .buttonStyle(.bordered)
            }
        }
        .padding(32)
    }
}
```

### Loading Overlay
```swift
struct LoadingOverlay: View {
    let message: String

    var body: some View {
        ZStack {
            Color.black.opacity(0.2)
                .ignoresSafeArea()
            VStack(spacing: 16) {
                ProgressView()
                Text(message)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
            }
            .padding(24)
            .background(.ultraThinMaterial)
            .cornerRadius(16)
        }
    }
}
```

## Apple Human Interface Guidelines — Key Rules

### Navigation
- Use `NavigationStack` (not deprecated `NavigationView`)
- Prefer tab bars for top-level navigation (max 5 tabs)
- Use `.sheet()` for creation/editing flows
- Use `.alert()` for confirmations and errors
- Back buttons should be automatic (no custom back)

### Typography
- Use system dynamic type sizes (`.font(.headline)`, `.font(.body)`)
- Never hardcode font sizes unless intentional (code/terminal)
- Support Dynamic Type — use `@ScaledMetric` for custom sizes
- Maximum 3 font weights per screen

### Color
- Always use semantic colors: `Color.primary`, `Color.secondary`, `Color(.systemBackground)`
- Named asset colors (`Color("Primary")`) for brand colors — they adapt to dark mode
- Never hardcode `Color.black` or `Color.white` (breaks dark mode)
- Use `.opacity()` for subtle backgrounds, not gray constants

### Spacing
- Standard padding: 16pt (cards, sections)
- Inter-section spacing: 24pt
- Inter-element spacing: 8-12pt
- Use `.padding()` defaults when possible

### Accessibility (MANDATORY for Healthcare)
- Every interactive element needs `.accessibilityLabel`
- Action elements need `.accessibilityHint` ("Double tap to...")
- Group related content with `.accessibilityElement(children: .combine)`
- Use `.accessibilityAddTraits` for roles (`.isHeader`, `.isButton`, `.isSelected`)
- Decorative images: `.accessibilityHidden(true)`
- Test with VoiceOver before shipping

### Dark Mode
- Always test both appearances
- Use semantic colors exclusively
- Card backgrounds: `Color(.secondarySystemBackground)` (adapts)
- Text: `Color.primary` (adapts) + `Color.secondary` (adapts)
- Borders/dividers: `Color(.separator)` (adapts)

### Forms & Settings
- Use `Form` with `Section` for settings screens
- Toggle labels on the left, toggle on the right
- Use `Picker` with `.pickerStyle(.menu)` for in-form selectors
- Destructive actions: `Button(role: .destructive)`
- Confirmation for destructive actions: `.alert(isPresented:)`

## iOS Design Anti-Patterns

### Never Do
- Custom tab bars (use SwiftUI `TabView`)
- Custom navigation chrome (use `NavigationStack`)
- Non-standard gestures (users expect swipe-back, pull-to-refresh)
- Alert dialogs for non-critical info (use inline messaging)
- Full-screen modals for simple input (use `.sheet()`)
- Hamburger menus (use tab bars or sidebar on iPad)

### Avoid
- More than 5 tabs
- Deep navigation stacks (>3 levels)
- Horizontal scrolling lists without clear affordance
- Text smaller than 11pt
- Touch targets smaller than 44x44pt
- Color as the only indicator of state (accessibility)

## Platform-Specific Patterns

### Charts (iOS 16+ Swift Charts)
```swift
import Charts

// Use @available guards
if #available(iOS 16.0, *) {
    Chart(data) { item in
        LineMark(x: .value("Date", item.date), y: .value("Value", item.value))
    }
    .chartYAxis { AxisMarks(values: .automatic) }
    .frame(height: 200)
} else {
    Text("Charts require iOS 16+")
}
```

### Notifications
```swift
// Always request permission before scheduling
let granted = try await UNUserNotificationCenter.current()
    .requestAuthorization(options: [.alert, .badge, .sound])
```

### Keychain (Sensitive Data)
```swift
// Use Keychain for tokens, NEVER UserDefaults
let query: [String: Any] = [
    kSecClass as String: kSecClassGenericPassword,
    kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock
]
```

## Output Checklist

Before shipping any iOS view:
- [ ] Builds without warnings
- [ ] Works in dark mode
- [ ] Works with Dynamic Type (accessibility sizes)
- [ ] All interactive elements have `.accessibilityLabel`
- [ ] No hardcoded colors (use semantic/named)
- [ ] Uses `NavigationStack` (not `NavigationView`)
- [ ] Touch targets >= 44x44pt
- [ ] Loading states for async operations
- [ ] Error states with recovery actions
- [ ] Empty states with guidance
- [ ] Pull-to-refresh where appropriate
- [ ] Keyboard avoidance on forms

## Integration with /ios-agent

Use `/ios-agent` for build/test automation AFTER designing:
```bash
# Build and verify
xcodebuild -project X.xcodeproj -scheme X -destination 'platform=iOS Simulator,name=iPhone 17 Pro' build

# Take screenshot for review
xcrun simctl io booted screenshot ./review-screenshot.png
```

## Integration with /frontend-design

`/frontend-design` is for WEB interfaces (React, Lit, HTMX).
`/ios-design` is for NATIVE iOS interfaces (SwiftUI).

Same design thinking process applies — define aesthetic direction before coding.
But the implementation uses Apple frameworks, not CSS/HTML.
