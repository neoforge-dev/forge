//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 202: coordination.go + context_sync.go
// Targets:
//   CoordinationDashboard.calculateCompletion (coordination.go:351)
//   CoordinationDashboard.estimateETA         (coordination.go:394)
//   CoordinationDashboard.ServeHTTP           (coordination.go:424)
//   CoordinationDashboard.GetCoordinationHTML (coordination.go:473)
//   NewContextSync                            (context_sync.go:41) — with DB
//   ContextSync.Stop                          (context_sync.go:97) — before start
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// calculateCompletion
// ---------------------------------------------------------------------------

func TestWave202_CalculateCompletion_NoAgentsNoCommitsNoBlockers(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cd := NewCoordinationDashboard(db)
	status := &SprintStatus{Agents: nil, Commits: CommitSummary{Total: 0}, Blockers: nil}
	result := cd.calculateCompletion(status)
	if result < 0 || result > 100 {
		t.Errorf("expected 0-100, got %f", result)
	}
}

func TestWave202_CalculateCompletion_WithActiveAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cd := NewCoordinationDashboard(db)
	status := &SprintStatus{
		Agents: []AgentSprintStatus{
			{Status: "working"},
			{Status: "working"},
			{Status: "idle"},
		},
		Commits:  CommitSummary{Total: 5},
		Blockers: nil,
	}
	result := cd.calculateCompletion(status)
	if result <= 0 {
		t.Errorf("expected positive completion with active agents, got %f", result)
	}
}

func TestWave202_CalculateCompletion_WithBlockers(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cd := NewCoordinationDashboard(db)
	status := &SprintStatus{
		Blockers: []Blocker{{Description: "test blocker"}},
	}
	result := cd.calculateCompletion(status)
	_ = result // just ensure no panic
}

// ---------------------------------------------------------------------------
// estimateETA
// ---------------------------------------------------------------------------

func TestWave202_EstimateETA_Complete(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cd := NewCoordinationDashboard(db)
	status := &SprintStatus{Completion: 100.0}
	if got := cd.estimateETA(status); got != "Complete" {
		t.Errorf("expected 'Complete', got %q", got)
	}
}

func TestWave202_EstimateETA_Zero(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cd := NewCoordinationDashboard(db)
	status := &SprintStatus{Completion: 0.0}
	if got := cd.estimateETA(status); got != "Unknown" {
		t.Errorf("expected 'Unknown', got %q", got)
	}
}

func TestWave202_EstimateETA_InProgress(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cd := NewCoordinationDashboard(db)
	status := &SprintStatus{
		Completion: 50.0,
		StartedAt:  time.Now().Add(-2 * time.Hour),
	}
	got := cd.estimateETA(status)
	// Should be non-empty non-unknown
	if got == "" {
		t.Error("expected non-empty ETA")
	}
}

func TestWave202_EstimateETA_ZeroTimeRunning(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cd := NewCoordinationDashboard(db)
	status := &SprintStatus{
		Completion: 50.0,
		StartedAt:  time.Now().Add(1 * time.Hour), // future → negative time
	}
	got := cd.estimateETA(status)
	if got != "Calculating..." {
		t.Errorf("expected 'Calculating...', got %q", got)
	}
}

// ---------------------------------------------------------------------------
// ServeHTTP
// ---------------------------------------------------------------------------

func TestWave202_CoordinationDashboard_ServeHTTP(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cd := NewCoordinationDashboard(db)

	req := httptest.NewRequest(http.MethodGet, "/api/coordination?sprint=S99", nil)
	w := httptest.NewRecorder()
	cd.ServeHTTP(w, req)
	// Returns 200 or 500 depending on data — just no panic
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// GetCoordinationHTML
// ---------------------------------------------------------------------------

func TestWave202_GetCoordinationHTML_NotEmpty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cd := NewCoordinationDashboard(db)
	html := cd.GetCoordinationHTML()
	if html == "" {
		t.Error("expected non-empty HTML")
	}
}

// ---------------------------------------------------------------------------
// NewContextSync + Stop
// ---------------------------------------------------------------------------

func TestWave202_NewContextSync_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")

	cs, err := NewContextSync(nil, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	if cs == nil {
		t.Error("expected non-nil ContextSync")
	}
	// Stop before start — should not panic
	cs.Stop()
}
