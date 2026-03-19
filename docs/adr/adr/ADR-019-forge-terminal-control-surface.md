# ADR-019: Forge Terminal iOS as Mobile Control Surface

**Date:** 2026-03-05
**Status:** HOLD — iOS domain (node-3 node); not Phase 1 V3 backend work
**Decision Makers:**
- Bogdan Veliscu (CTO, FORGE)

---

## Context

FORGE operators currently monitor and control the fleet through:

1. **React PWA** (`command_center/src/`, ~15K LOC): Browser-based dashboard, being retired (ADR-014)
2. **TUI** (`forge status --watch`): BubbleTea terminal dashboard, excellent but requires SSH/terminal access
3. **Forge Terminal iOS app**: Native Swift app at P4 completion (602 tests passing), currently focused on terminal emulation and session management

With the React PWA being retired, there's no mobile-friendly control surface for approvals, fleet monitoring, or task management. The Forge Terminal iOS app is the natural candidate — it already connects to FORGE infrastructure and has a mature codebase.

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| New React PWA | Web-native, cross-platform | Reintroduces React build pipeline we're retiring | ❌ REJECTED |
| Mobile-responsive TUI (Mosh) | Reuse existing TUI | Poor touch UX, no push notifications, battery drain | ❌ REJECTED |
| Third-party mobile app (Retool) | Fast to build | External dependency, data leaves our infra | ❌ REJECTED |
| **Forge Terminal iOS + HTMX web fallback** | **Native UX, push notifications, existing codebase + universal web fallback** | **iOS-only for full experience** | ✅ **ACCEPTED** |

---

## Decision

The **Forge Terminal iOS app** (602 tests, P4 complete) becomes the primary mobile control surface. It connects directly to v3's WebSocket (`:8082`) and HTTP (`:8081`) APIs. The Go binary also serves a minimal **HTMX web UI** at `/ui/*` as a fallback for non-iOS users.

### New iOS Views

| View | Purpose | Data Source |
|------|---------|-------------|
| `FleetDashboardView` | Real-time fleet status, agent cards with context %, task assignment | WS `agent.telemetry` subscription |
| `ApprovalInboxView` | Pending approvals with approve/reject actions, priority sorting | WS `approval.*` + `POST /api/approvals/:id/decide` |
| `TaskDetailView` | Task lifecycle, events, logs, related pattern stats | `GET /api/tasks/:id` + WS `task.*` |
| `PatrolAlertView` | Active patrol findings and resolution status | WS `patrol.*` subscription |
| `NodeHealthView` | Per-node CPU/RAM/disk, agent count, connectivity | `GET /api/nodes/health` |

### Push Notifications via APNs

**PHONE-tier approvals** (ADR-008 §7) trigger push notifications with actionable buttons:

```swift
// Notification payload
{
  "aps": {
    "alert": {
      "title": "Approval Required: merge → main",
      "subtitle": "interview-simulator • claude-node-3-1",
      "body": "feat: add OAuth2 login flow (confidence: 0.82)"
    },
    "category": "APPROVAL_ACTION",
    "sound": "default"
  },
  "approval_id": "01JQXYZ...",
  "tier": "PHONE",
  "task_id": "01JQABC..."
}
```

**Actions:**
- ✅ **Approve**: `POST /api/approvals/:id/decide` with `{"decision": "approved"}`
- ❌ **Reject**: Opens ApprovalInboxView for reject reason
- 👁 **View Details**: Opens TaskDetailView

**APNs Integration:**
- v3 Go binary sends push via APNs HTTP/2 API when approval is created with tier ≥ PHONE
- Device tokens stored in `agent_devices` SQLite table, registered on iOS app launch
- Token refresh handled by iOS app on `didRegisterForRemoteNotificationsWithDeviceToken`

### HTMX Web Fallback

For non-iOS users, the v3 Go binary serves a minimal web UI:

```
GET /ui/                  → Fleet dashboard (HTMX + Go templates)
GET /ui/approvals         → Approval inbox
GET /ui/tasks/:id         → Task detail
GET /ui/nodes             → Node health
```

**Stack:** Go `html/template` + HTMX for dynamic updates + minimal CSS (no build pipeline).

HTMX pages use SSE endpoint (ADR-017) for live updates:

```html
<div hx-ext="sse" sse-connect="/api/events/stream?topics=agent">
  <div sse-swap="agent.telemetry">
    <!-- Agent cards update in real-time -->
  </div>
</div>
```

### What This Replaces

| Current Component | Replaced By |
|-------------------|-------------|
| React PWA (`command_center/src/`) | Forge Terminal iOS + HTMX web UI |
| Browser-based CC dashboard | iOS `FleetDashboardView` + `/ui/` |
| No mobile approvals | APNs push notifications with approve/reject |
| No mobile fleet monitoring | iOS `FleetDashboardView` with WS telemetry |

### iOS App Architecture Addition

The new views integrate into the existing Forge Terminal architecture:

```
ForgeTerminal/
├── Views/
│   ├── Terminal/          # Existing terminal emulation
│   ├── Sessions/          # Existing session management
│   ├── Fleet/             # NEW
│   │   ├── FleetDashboardView.swift
│   │   ├── AgentCardView.swift
│   │   └── NodeHealthView.swift
│   ├── Approvals/         # NEW
│   │   ├── ApprovalInboxView.swift
│   │   └── ApprovalActionView.swift
│   └── Tasks/             # NEW
│       ├── TaskDetailView.swift
│       └── PatrolAlertView.swift
├── Services/
│   ├── ForgeWebSocket.swift    # NEW - WS client for v3
│   ├── ForgeAPIClient.swift    # NEW - HTTP client for v3
│   └── PushNotificationManager.swift  # NEW - APNs registration
```

---

## Consequences

### Positive

1. **Native mobile UX**: iOS-native views with proper touch interaction, no browser limitations
2. **Push notifications**: PHONE-tier approvals reach operators immediately, actionable from lock screen
3. **No React pipeline**: HTMX web fallback requires zero JS build tooling
4. **Leverages existing app**: 602 tests, P4 complete — proven iOS codebase
5. **Real-time**: WebSocket subscriptions provide sub-second fleet updates on mobile

### Negative

1. **iOS-only full experience**: Android users limited to HTMX web fallback
2. **APNs complexity**: Requires Apple Developer account, certificate management, token refresh
3. **Two UI codebases**: iOS (Swift) + HTMX (Go templates) — but both are thin layers over same API

### Neutral

1. **HTMX is intentionally minimal**: Not a full SPA replacement, just enough for approve/monitor
2. **App Store distribution**: Forge Terminal is already on TestFlight; new views ship as app update
3. **Offline**: iOS app caches last-known fleet state; HTMX requires connectivity

---

## Related Decisions

- ADR-008: FORGE CLI v3 Rewrite (provides HTTP + WS APIs consumed by iOS and HTMX)
- ADR-014: Retire Command Center (removes React PWA this replaces)
- ADR-015: Agent Telemetry Protocol (telemetry displayed in FleetDashboardView)
- ADR-017: Unified Event Bus (SSE shim powers HTMX live updates)

---

**Status: PROPOSED**

Decision review target: 2026-03-10
