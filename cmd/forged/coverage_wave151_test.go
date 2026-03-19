//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 151: dashboard.go pure functions + Dashboard methods
// Targets:
//   AgentStatus.String()       (dashboard.go:51) — all status variants
//   splitCapabilities          (dashboard.go:836) — empty + multi-capability
//   splitString                (dashboard.go:848) — basic split
//   trimSpace                  (dashboard.go:862) — whitespace variants
//   Dashboard.calculateStatus  (dashboard.go:602) — offline/error/idle branches
//   Dashboard.checkWarnings    (dashboard.go:615) — all 4 warning conditions
//   Dashboard.ServeDashboardJSON (dashboard.go:890) — GET with DB
//   Dashboard.ServeAgentHealth   (dashboard.go:903) — empty ID→400, not found→404
//   Dashboard.ServeAgentsDashboard (dashboard.go:929) — POST→405, GET with DB
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// AgentStatus.String()
// ---------------------------------------------------------------------------

func TestWave151_AgentStatus_String(t *testing.T) {
	cases := []struct {
		s    AgentStatus
		want string
	}{
		{AgentStatusUnknown, "unknown"},
		{AgentStatusOffline, "offline"},
		{AgentStatusIdle, "idle"},
		{AgentStatusWorking, "working"},
		{AgentStatusBlocked, "blocked"},
		{AgentStatusError, "error"},
		{AgentStatusMaintenance, "maintenance"},
		{AgentStatus(99), "unknown"},
	}
	for _, c := range cases {
		if got := c.s.String(); got != c.want {
			t.Errorf("AgentStatus(%d).String() = %q, want %q", c.s, got, c.want)
		}
	}
}

// ---------------------------------------------------------------------------
// splitCapabilities / splitString / trimSpace
// ---------------------------------------------------------------------------

func TestWave151_SplitCapabilities_Empty(t *testing.T) {
	if got := splitCapabilities(""); got != nil {
		t.Errorf("expected nil for empty string, got %v", got)
	}
}

func TestWave151_SplitCapabilities_Single(t *testing.T) {
	got := splitCapabilities("read")
	if len(got) != 1 || got[0] != "read" {
		t.Errorf("unexpected: %v", got)
	}
}

func TestWave151_SplitCapabilities_Multi(t *testing.T) {
	got := splitCapabilities("read, write , exec")
	if len(got) != 3 {
		t.Errorf("expected 3 capabilities, got %v", got)
	}
	if got[0] != "read" || got[1] != "write" || got[2] != "exec" {
		t.Errorf("unexpected values: %v", got)
	}
}

func TestWave151_SplitString_Basic(t *testing.T) {
	got := splitString("a,b,c", ",")
	if len(got) != 3 || got[0] != "a" || got[1] != "b" || got[2] != "c" {
		t.Errorf("unexpected split: %v", got)
	}
}

func TestWave151_TrimSpace_Variants(t *testing.T) {
	cases := []struct {
		in, want string
	}{
		{"  hello  ", "hello"},
		{"\t world\n", "world"},
		{"no-space", "no-space"},
		{"", ""},
	}
	for _, c := range cases {
		if got := trimSpace(c.in); got != c.want {
			t.Errorf("trimSpace(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

// ---------------------------------------------------------------------------
// Dashboard.calculateStatus
// ---------------------------------------------------------------------------

func TestWave151_CalculateStatus_Offline(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	now := time.Now()
	last := now.Add(-6 * time.Minute) // > 5 min → offline
	if got := d.calculateStatus(last, now); got != AgentStatusOffline {
		t.Errorf("expected offline, got %v", got)
	}
}

func TestWave151_CalculateStatus_Error(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	now := time.Now()
	last := now.Add(-3 * time.Minute) // > 2 min but ≤ 5 min → error
	if got := d.calculateStatus(last, now); got != AgentStatusError {
		t.Errorf("expected error, got %v", got)
	}
}

func TestWave151_CalculateStatus_Idle(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	now := time.Now()
	last := now.Add(-30 * time.Second) // ≤ 2 min → idle
	if got := d.calculateStatus(last, now); got != AgentStatusIdle {
		t.Errorf("expected idle, got %v", got)
	}
}

// ---------------------------------------------------------------------------
// Dashboard.checkWarnings
// ---------------------------------------------------------------------------

func TestWave151_CheckWarnings_HighContext(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	a := &AgentHealth{ContextPercent: 85, LastHeartbeat: time.Now(), SuccessRate: 1.0}
	warnings := d.checkWarnings(a)
	found := false
	for _, w := range warnings {
		if w == "High context usage" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected 'High context usage' warning, got %v", warnings)
	}
}

func TestWave151_CheckWarnings_StaleHeartbeat(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	a := &AgentHealth{
		ContextPercent: 50,
		LastHeartbeat:  time.Now().Add(-3 * time.Minute), // stale
		SuccessRate:    1.0,
	}
	warnings := d.checkWarnings(a)
	found := false
	for _, w := range warnings {
		if w == "Stale heartbeat" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected 'Stale heartbeat' warning, got %v", warnings)
	}
}

func TestWave151_CheckWarnings_LowSuccessRate(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	a := &AgentHealth{ContextPercent: 50, LastHeartbeat: time.Now(), SuccessRate: 0.3}
	warnings := d.checkWarnings(a)
	found := false
	for _, w := range warnings {
		if w == "Low success rate" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected 'Low success rate' warning, got %v", warnings)
	}
}

func TestWave151_CheckWarnings_HighErrorRate(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	a := &AgentHealth{ContextPercent: 50, LastHeartbeat: time.Now(), SuccessRate: 1.0, ErrorsLastHour: 10}
	warnings := d.checkWarnings(a)
	found := false
	for _, w := range warnings {
		if w == "High error rate" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected 'High error rate' warning, got %v", warnings)
	}
}

func TestWave151_CheckWarnings_NoWarnings(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	a := &AgentHealth{ContextPercent: 50, LastHeartbeat: time.Now(), SuccessRate: 1.0, ErrorsLastHour: 0}
	warnings := d.checkWarnings(a)
	if len(warnings) != 0 {
		t.Errorf("expected no warnings, got %v", warnings)
	}
}

// ---------------------------------------------------------------------------
// Dashboard HTTP methods
// ---------------------------------------------------------------------------

func TestWave151_ServeDashboardJSON_GetWithDB(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard", nil)
	w := httptest.NewRecorder()
	d.ServeDashboardJSON(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave151_ServeAgentHealth_EmptyID(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/agents/", nil)
	w := httptest.NewRecorder()
	d.ServeAgentHealth(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty agent ID, got %d", w.Code)
	}
}

func TestWave151_ServeAgentHealth_NotFound(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/agents/nonexistent-agent", nil)
	w := httptest.NewRecorder()
	d.ServeAgentHealth(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for missing agent, got %d", w.Code)
	}
}

func TestWave151_ServeAgentsDashboard_GetWithDB(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/dashboard", nil)
	w := httptest.NewRecorder()
	d.ServeAgentsDashboard(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave151_GetDashboardData_WithDB(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	data, err := d.GetDashboardData(context.Background())
	if err != nil {
		t.Fatalf("GetDashboardData failed: %v", err)
	}
	if data == nil {
		t.Fatal("expected non-nil dashboard data")
	}
}
