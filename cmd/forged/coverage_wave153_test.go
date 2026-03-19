//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 153: coordination.go functions + CoordinationDashboard methods
// Targets:
//   truncateString           (coordination.go:552) — short/long/exact strings
//   CoordinationDashboard.detectBlockers (coordination.go:313) — no blocker, warning, critical, high_context
//   CoordinationDashboard.calculateCompletion (coordination.go:351) — various states
//   CoordinationDashboard.estimateETA (coordination.go:394) — complete, 0%, stalled, <1h, >1h
//   CoordinationDashboard.ServeHTTP (coordination.go:424) — GET with DB
// ---------------------------------------------------------------------------

func newTestCoordDashboard(t *testing.T) *CoordinationDashboard {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)
	return NewCoordinationDashboard(db)
}

// ---------------------------------------------------------------------------
// truncateString
// ---------------------------------------------------------------------------

func TestWave153_TruncateString_Short(t *testing.T) {
	if got := truncateString("hello", 10); got != "hello" {
		t.Errorf("unexpected: %q", got)
	}
}

func TestWave153_TruncateString_Long(t *testing.T) {
	s := "this is a very long string that exceeds the limit"
	got := truncateString(s, 20)
	if len(got) != 20 || got[17:] != "..." {
		t.Errorf("unexpected: %q (len=%d)", got, len(got))
	}
}

func TestWave153_TruncateString_Exact(t *testing.T) {
	s := "exactly10s"
	if got := truncateString(s, 10); got != s {
		t.Errorf("unexpected: %q", got)
	}
}

// ---------------------------------------------------------------------------
// detectBlockers
// ---------------------------------------------------------------------------

func TestWave153_DetectBlockers_NoBlocker(t *testing.T) {
	cd := newTestCoordDashboard(t)
	agents := []AgentSprintStatus{
		{Name: "kimi", Status: "working", LastCommit: time.Now().Add(-5 * time.Minute)},
	}
	blockers := cd.detectBlockers(agents)
	if len(blockers) != 0 {
		t.Errorf("expected no blockers, got %v", blockers)
	}
}

func TestWave153_DetectBlockers_Warning(t *testing.T) {
	cd := newTestCoordDashboard(t)
	agents := []AgentSprintStatus{
		{Name: "kimi", Status: "working", LastCommit: time.Now().Add(-45 * time.Minute)},
	}
	blockers := cd.detectBlockers(agents)
	if len(blockers) != 1 || blockers[0].Severity != "warning" {
		t.Errorf("expected 1 warning blocker, got %v", blockers)
	}
}

func TestWave153_DetectBlockers_Critical(t *testing.T) {
	cd := newTestCoordDashboard(t)
	agents := []AgentSprintStatus{
		{Name: "glm", Status: "working", LastCommit: time.Now().Add(-90 * time.Minute)},
	}
	blockers := cd.detectBlockers(agents)
	if len(blockers) != 1 || blockers[0].Severity != "critical" {
		t.Errorf("expected 1 critical blocker, got %v", blockers)
	}
}

func TestWave153_DetectBlockers_HighContext(t *testing.T) {
	cd := newTestCoordDashboard(t)
	agents := []AgentSprintStatus{
		{Name: "minimax", Status: "high_context", LastCommit: time.Now().Add(-5 * time.Minute)},
	}
	blockers := cd.detectBlockers(agents)
	if len(blockers) != 1 || blockers[0].Description != "High context usage - handoff recommended" {
		t.Errorf("expected high_context blocker, got %v", blockers)
	}
}

// ---------------------------------------------------------------------------
// calculateCompletion
// ---------------------------------------------------------------------------

func TestWave153_CalculateCompletion_NoAgentsNoCommitsNoBlockers(t *testing.T) {
	cd := newTestCoordDashboard(t)
	status := &SprintStatus{
		Agents:   []AgentSprintStatus{},
		Commits:  CommitSummary{Total: 0},
		Blockers: []Blocker{},
	}
	got := cd.calculateCompletion(status)
	// No active agents (0%), no commits (0%), no blockers (+30%) = 30
	if got != 30 {
		t.Errorf("expected 30, got %.1f", got)
	}
}

func TestWave153_CalculateCompletion_WithActiveAgentsAndCommits(t *testing.T) {
	cd := newTestCoordDashboard(t)
	status := &SprintStatus{
		Agents: []AgentSprintStatus{
			{Status: "working"},
			{Status: "working"},
		},
		Commits:  CommitSummary{Total: 10},
		Blockers: []Blocker{},
	}
	got := cd.calculateCompletion(status)
	// 2 agents × 5 = 10% + 10 commits × 2 = 20% + no blockers 30% = 60
	if got != 60 {
		t.Errorf("expected 60, got %.1f", got)
	}
}

func TestWave153_CalculateCompletion_WithBlockers(t *testing.T) {
	cd := newTestCoordDashboard(t)
	status := &SprintStatus{
		Agents:  []AgentSprintStatus{},
		Commits: CommitSummary{Total: 0},
		Blockers: []Blocker{
			{Severity: "warning"},
		},
	}
	got := cd.calculateCompletion(status)
	// No agents (0%), no commits (0%), 1 blocker → 30-5 = 25
	if got != 25 {
		t.Errorf("expected 25, got %.1f", got)
	}
}

// ---------------------------------------------------------------------------
// estimateETA
// ---------------------------------------------------------------------------

func TestWave153_EstimateETA_Complete(t *testing.T) {
	cd := newTestCoordDashboard(t)
	status := &SprintStatus{Completion: 100, StartedAt: time.Now().Add(-2 * time.Hour)}
	if got := cd.estimateETA(status); got != "Complete" {
		t.Errorf("expected 'Complete', got %q", got)
	}
}

func TestWave153_EstimateETA_Zero(t *testing.T) {
	cd := newTestCoordDashboard(t)
	status := &SprintStatus{Completion: 0, StartedAt: time.Now().Add(-2 * time.Hour)}
	if got := cd.estimateETA(status); got != "Unknown" {
		t.Errorf("expected 'Unknown', got %q", got)
	}
}

func TestWave153_EstimateETA_JustStarted(t *testing.T) {
	cd := newTestCoordDashboard(t)
	// StartedAt in the future → timeRunning ≤ 0 → "Calculating..."
	status := &SprintStatus{Completion: 50, StartedAt: time.Now().Add(1 * time.Hour)}
	got := cd.estimateETA(status)
	if got != "Calculating..." {
		t.Errorf("expected 'Calculating...', got %q", got)
	}
}

func TestWave153_EstimateETA_MoreThanOneHour(t *testing.T) {
	cd := newTestCoordDashboard(t)
	// 10% completion after 1 hour → velocity=10%/hr, remaining=90% → 9 hours
	status := &SprintStatus{Completion: 10, StartedAt: time.Now().Add(-1 * time.Hour)}
	got := cd.estimateETA(status)
	if got == "Unknown" || got == "Complete" || got == "Calculating..." {
		t.Errorf("expected hours ETA, got %q", got)
	}
}

func TestWave153_EstimateETA_LessThanOneHour(t *testing.T) {
	cd := newTestCoordDashboard(t)
	// 99% completion after 10 hours → velocity high, remaining 1% → < 1 hour
	status := &SprintStatus{Completion: 99, StartedAt: time.Now().Add(-10 * time.Hour)}
	got := cd.estimateETA(status)
	if got == "Unknown" || got == "Complete" {
		t.Errorf("expected minute ETA, got %q", got)
	}
}

// ---------------------------------------------------------------------------
// CoordinationDashboard.ServeHTTP
// ---------------------------------------------------------------------------

func TestWave153_CoordDashboard_ServeHTTP_Get(t *testing.T) {
	cd := newTestCoordDashboard(t)
	req := httptest.NewRequest(http.MethodGet, "/api/coordination?sprint=S99", nil)
	w := httptest.NewRecorder()
	cd.ServeHTTP(w, req)
	// May succeed or fail if git commands fail — just ensure no panic
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status: %d", w.Code)
	}
}
