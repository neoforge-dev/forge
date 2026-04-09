//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestWatchSummaryHandler_JSONShape(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/watch/summary", nil)
	w := httptest.NewRecorder()
	watchSummaryHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var summary WatchSummary
	if err := json.NewDecoder(w.Body).Decode(&summary); err != nil {
		t.Fatalf("decode WatchSummary: %v", err)
	}

	// Validate required fields are present (zero values are acceptable in test DB)
	if summary.AgentsOnline < 0 {
		t.Errorf("agents_online should not be negative, got %d", summary.AgentsOnline)
	}
	if summary.AgentsTotal < 0 {
		t.Errorf("agents_total should not be negative, got %d", summary.AgentsTotal)
	}
	if summary.TasksRunning < 0 {
		t.Errorf("tasks_running should not be negative, got %d", summary.TasksRunning)
	}
	if summary.GatesPending < 0 {
		t.Errorf("gates_pending should not be negative, got %d", summary.GatesPending)
	}
	if summary.PendingDecisions == nil {
		t.Error("pending_decisions should not be nil (should be empty slice)")
	}
	if summary.LastUpdated.IsZero() {
		t.Error("last_updated should not be zero time")
	}
}

func TestWatchSummaryHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/watch/summary", nil)
	w := httptest.NewRecorder()
	watchSummaryHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for POST, got %d", w.Code)
	}
}

func TestWatchSummaryHandler_WithPendingApprovals(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Create a pending approval so pending_decisions is populated
	store := &sqliteApprovalStore{db: db}
	svc := NewApprovalService(store)

	ctx := context.Background()
	a := Approval{
		ID:              "watch-test-001",
		Type:            ApprovalTaskCompletion,
		AgentID:         "agent-watch",
		Domain:          "test",
		Title:           "Watch Test Approval",
		Description:     "for watch summary test",
		RiskScore:       0.5,
		ConfidenceScore: 0.8,
		Tier:            TierPhone,
		Status:          StatusPending,
		CreatedAt:       time.Now().UTC(),
		ExpiresAt:       time.Now().UTC().Add(24 * time.Hour),
	}
	if err := store.Create(ctx, a); err != nil {
		t.Fatalf("Create approval: %v", err)
	}

	// Override global service for this test
	old := watchSummaryApprovalService
	watchSummaryApprovalService = svc
	defer func() { watchSummaryApprovalService = old }()

	req := httptest.NewRequest(http.MethodGet, "/api/watch/summary", nil)
	w := httptest.NewRecorder()
	watchSummaryHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var summary WatchSummary
	if err := json.NewDecoder(w.Body).Decode(&summary); err != nil {
		t.Fatalf("decode: %v", err)
	}

	if summary.GatesPending == 0 {
		t.Error("expected gates_pending > 0 with one pending approval")
	}
	if len(summary.PendingDecisions) == 0 {
		t.Error("expected at least one entry in pending_decisions")
	}

	// Verify WatchAction fields are populated
	dec := summary.PendingDecisions[0]
	if dec.ID == "" {
		t.Error("WatchAction.ID is empty")
	}
	if len(dec.Buttons) == 0 {
		t.Error("WatchAction.Buttons is empty")
	}
}

func TestBuildWatchSummary_NoApprovalService(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	summary := buildWatchSummary(context.Background(), nil)
	if summary.PendingDecisions == nil {
		t.Error("pending_decisions should be non-nil empty slice when service is nil")
	}
	if len(summary.PendingDecisions) != 0 {
		t.Errorf("expected 0 pending decisions with nil service, got %d", len(summary.PendingDecisions))
	}
}

func TestWatchActionButtons(t *testing.T) {
	tests := []struct {
		tier     string
		wantMin  int
		mustHave string
	}{
		{string(TierWatch), 1, "approve"},
		{string(TierPhone), 2, "reject"},
		{string(TierDesktop), 3, "defer"},
		{"unknown", 2, "approve"},
	}
	for _, tc := range tests {
		buttons := watchActionButtons(tc.tier)
		if len(buttons) < tc.wantMin {
			t.Errorf("tier %s: got %d buttons, want at least %d", tc.tier, len(buttons), tc.wantMin)
		}
		found := false
		for _, b := range buttons {
			if b == tc.mustHave {
				found = true
			}
		}
		if !found {
			t.Errorf("tier %s: expected button %q in %v", tc.tier, tc.mustHave, buttons)
		}
	}
}
