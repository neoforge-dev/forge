//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 200: dashboard.go pure functions
// Targets:
//   AgentStatus.String    (dashboard.go:51) — all values
//   Dashboard.calculateStatus (dashboard.go:602) — offline/error/idle
//   Dashboard.checkWarnings   (dashboard.go:615) — high context/stale/low success/high errors
//   splitCapabilities         (dashboard.go:836) — empty/multi
//   splitString               (dashboard.go:848) — no sep/with sep
//   trimSpace                 (dashboard.go:862) — no space/with space
//   Dashboard.GetAgentHealth  (dashboard.go:543) — with DB
//   Dashboard.GetDashboardData(dashboard.go:672) — with DB
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// AgentStatus.String
// ---------------------------------------------------------------------------

func TestWave200_AgentStatus_String(t *testing.T) {
	cases := []struct {
		s    AgentStatus
		want string
	}{
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
			t.Errorf("AgentStatus(%d).String(): expected %q, got %q", c.s, c.want, got)
		}
	}
}

// ---------------------------------------------------------------------------
// calculateStatus
// ---------------------------------------------------------------------------

func TestWave200_CalculateStatus_Offline(t *testing.T) {
	d := &Dashboard{}
	now := time.Now()
	status := d.calculateStatus(now.Add(-6*time.Minute), now)
	if status != AgentStatusOffline {
		t.Errorf("expected offline for 6m old heartbeat, got %s", status)
	}
}

func TestWave200_CalculateStatus_Error(t *testing.T) {
	d := &Dashboard{}
	now := time.Now()
	status := d.calculateStatus(now.Add(-3*time.Minute), now)
	if status != AgentStatusError {
		t.Errorf("expected error for 3m old heartbeat, got %s", status)
	}
}

func TestWave200_CalculateStatus_Idle(t *testing.T) {
	d := &Dashboard{}
	now := time.Now()
	status := d.calculateStatus(now.Add(-30*time.Second), now)
	if status != AgentStatusIdle {
		t.Errorf("expected idle for 30s old heartbeat, got %s", status)
	}
}

// ---------------------------------------------------------------------------
// checkWarnings
// ---------------------------------------------------------------------------

func TestWave200_CheckWarnings_HighContext(t *testing.T) {
	d := &Dashboard{}
	a := &AgentHealth{ContextPercent: 85.0, LastHeartbeat: time.Now()}
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

func TestWave200_CheckWarnings_StaleHeartbeat(t *testing.T) {
	d := &Dashboard{}
	a := &AgentHealth{LastHeartbeat: time.Now().Add(-5 * time.Minute)}
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

func TestWave200_CheckWarnings_LowSuccessRate(t *testing.T) {
	d := &Dashboard{}
	a := &AgentHealth{SuccessRate: 0.3, LastHeartbeat: time.Now()}
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

func TestWave200_CheckWarnings_HighErrorRate(t *testing.T) {
	d := &Dashboard{}
	a := &AgentHealth{ErrorsLastHour: 10, LastHeartbeat: time.Now()}
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

func TestWave200_CheckWarnings_Clean(t *testing.T) {
	d := &Dashboard{}
	a := &AgentHealth{
		ContextPercent: 50.0,
		LastHeartbeat:  time.Now(),
		SuccessRate:    0.9,
		ErrorsLastHour: 0,
	}
	warnings := d.checkWarnings(a)
	if len(warnings) != 0 {
		t.Errorf("expected no warnings for healthy agent, got %v", warnings)
	}
}

// ---------------------------------------------------------------------------
// splitCapabilities
// ---------------------------------------------------------------------------

func TestWave200_SplitCapabilities_Empty(t *testing.T) {
	result := splitCapabilities("")
	if result != nil {
		t.Errorf("expected nil for empty string, got %v", result)
	}
}

func TestWave200_SplitCapabilities_Multi(t *testing.T) {
	result := splitCapabilities("go-test, python, coverage")
	if len(result) != 3 {
		t.Errorf("expected 3 parts, got %d: %v", len(result), result)
	}
}

// ---------------------------------------------------------------------------
// splitString
// ---------------------------------------------------------------------------

func TestWave200_SplitString_NoSep(t *testing.T) {
	result := splitString("hello", ",")
	if len(result) != 1 || result[0] != "hello" {
		t.Errorf("expected ['hello'], got %v", result)
	}
}

func TestWave200_SplitString_WithSep(t *testing.T) {
	result := splitString("a,b,c", ",")
	if len(result) != 3 {
		t.Errorf("expected 3 parts, got %d: %v", len(result), result)
	}
}

// ---------------------------------------------------------------------------
// trimSpace
// ---------------------------------------------------------------------------

func TestWave200_TrimSpace_NoSpace(t *testing.T) {
	if got := trimSpace("hello"); got != "hello" {
		t.Errorf("expected 'hello', got %q", got)
	}
}

func TestWave200_TrimSpace_WithSpace(t *testing.T) {
	if got := trimSpace("  hello  "); got != "hello" {
		t.Errorf("expected 'hello', got %q", got)
	}
}

// ---------------------------------------------------------------------------
// GetAgentHealth + GetDashboardData
// ---------------------------------------------------------------------------

func TestWave200_GetAgentHealth_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := NewDashboard(db, nil)
	agents, err := d.GetAgentHealth(context.Background())
	if err != nil {
		t.Errorf("GetAgentHealth: %v", err)
	}
	_ = agents
}

func TestWave200_GetDashboardData_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := NewDashboard(db, nil)
	data, err := d.GetDashboardData(context.Background())
	if err != nil {
		t.Errorf("GetDashboardData: %v", err)
	}
	if data == nil {
		t.Error("expected non-nil data")
	}
}
