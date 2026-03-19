//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 185: approvals.go — ApprovalStore + ApprovalService + pure functions
// Targets:
//   GetApprovalTierForStage      (approvals.go:444) — empty/known/unknown
//   CalculateConfidence          (approvals.go:512) — high/medium/low confidence
//   NewApprovalStore             (approvals.go:121)
//   NewApprovalService           (approvals.go:460)
//   ApprovalStore.Create         (approvals.go:125)
//   ApprovalStore.Get            (approvals.go:144) — found / not found
//   ApprovalStore.ListPending    (approvals.go:210) — empty
//   ApprovalStore.ListByStatus   (approvals.go:214) — empty
//   ApprovalStore.ListByTier     (approvals.go:237) — empty
//   ApprovalStore.ListByAgent    (approvals.go:260) — empty
//   ApprovalStore.ListByTask     (approvals.go:283) — empty
//   ApprovalStore.ExpireOldApprovals (approvals.go:358) — no expired = 0
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// GetApprovalTierForStage
// ---------------------------------------------------------------------------

func TestWave185_GetApprovalTierForStage_Empty(t *testing.T) {
	tier, threshold := GetApprovalTierForStage("")
	if tier != "" || threshold != 0 {
		t.Errorf("expected empty tier and 0 threshold for empty stage, got %q %f", tier, threshold)
	}
}

func TestWave185_GetApprovalTierForStage_KnownIdea(t *testing.T) {
	tier, _ := GetApprovalTierForStage("idea")
	if tier != TierWatch {
		t.Errorf("expected TierWatch for 'idea', got %q", tier)
	}
}

func TestWave185_GetApprovalTierForStage_KnownDeploy(t *testing.T) {
	tier, threshold := GetApprovalTierForStage("deploy")
	if tier != TierDesktop {
		t.Errorf("expected TierDesktop for 'deploy', got %q", tier)
	}
	if threshold != 0.95 {
		t.Errorf("expected threshold 0.95, got %f", threshold)
	}
}

func TestWave185_GetApprovalTierForStage_KnownCaseInsensitive(t *testing.T) {
	tier, _ := GetApprovalTierForStage("BUILD")
	if tier != TierDesktop {
		t.Errorf("expected TierDesktop for 'BUILD', got %q", tier)
	}
}

func TestWave185_GetApprovalTierForStage_Unknown(t *testing.T) {
	tier, _ := GetApprovalTierForStage("unknownstage")
	if tier != TierDesktop {
		t.Errorf("expected TierDesktop fallback for unknown stage, got %q", tier)
	}
}

// ---------------------------------------------------------------------------
// CalculateConfidence
// ---------------------------------------------------------------------------

func TestWave185_CalculateConfidence_HighConfidence(t *testing.T) {
	svc := &ApprovalService{}
	ctx := ConfidenceContext{
		PatternMatch: 1.0,
		TestResults: &TestResult{
			Passed:   100,
			Failed:   0,
			Skipped:  0,
			Coverage: 95.0,
		},
		BlastRadius: 2,  // very small
		Reversible:  true,
	}
	result := svc.CalculateConfidence(ctx)
	if result.Score <= 0 {
		t.Error("expected positive confidence score")
	}
	if result.Tier == "" {
		t.Error("expected non-empty tier")
	}
	if result.Reasoning == "" {
		t.Error("expected non-empty reasoning")
	}
}

func TestWave185_CalculateConfidence_LowConfidence(t *testing.T) {
	svc := &ApprovalService{}
	ctx := ConfidenceContext{
		PatternMatch: 0.0,
		TestResults: &TestResult{
			Passed:   10,
			Failed:   90,
			Skipped:  0,
			Coverage: 10.0,
		},
		BlastRadius: 100, // very large
		Reversible:  false,
	}
	result := svc.CalculateConfidence(ctx)
	if result.Score <= 0 {
		t.Error("expected some score even for low confidence")
	}
	if result.AutoApprove {
		t.Error("expected no auto-approve for low confidence")
	}
}

func TestWave185_CalculateConfidence_NoTestResults(t *testing.T) {
	svc := &ApprovalService{}
	ctx := ConfidenceContext{
		PatternMatch: 0.5,
		TestResults:  nil, // no test results
		BlastRadius:  10,
		Reversible:   true,
	}
	result := svc.CalculateConfidence(ctx)
	// Should not panic, returns some score
	if result.Components == nil {
		t.Error("expected non-nil components map")
	}
}

func TestWave185_CalculateConfidence_MediumBlastRadius(t *testing.T) {
	svc := &ApprovalService{}
	ctx := ConfidenceContext{
		PatternMatch: 0.6,
		BlastRadius:  30, // medium blast radius (>20, <=50)
		Reversible:   true,
	}
	result := svc.CalculateConfidence(ctx)
	_ = result.Score
}

func TestWave185_CalculateConfidence_SmallBlastRadius(t *testing.T) {
	svc := &ApprovalService{}
	ctx := ConfidenceContext{
		PatternMatch: 0.6,
		BlastRadius:  8, // small blast radius (>5, <=20)
		Reversible:   false,
	}
	result := svc.CalculateConfidence(ctx)
	_ = result.Score
}

// ---------------------------------------------------------------------------
// NewApprovalStore
// ---------------------------------------------------------------------------

func TestWave185_NewApprovalStore_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewApprovalStore(db)
	if store == nil {
		t.Error("expected non-nil ApprovalStore")
	}
}

// ---------------------------------------------------------------------------
// NewApprovalService
// ---------------------------------------------------------------------------

func TestWave185_NewApprovalService_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	svc := NewApprovalService(NewApprovalStore(db))
	if svc == nil {
		t.Error("expected non-nil ApprovalService")
	}
}

// ---------------------------------------------------------------------------
// ApprovalStore CRUD
// ---------------------------------------------------------------------------

func TestWave185_ApprovalStore_CreateAndGet(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewApprovalStore(db)

	now := time.Now()
	a := Approval{
		ID:        "approval-wave179",
		Type:      ApprovalTaskCompletion,
		AgentID:   "kimi-1",
		Domain:    "forge",
		Title:     "test approval",
		Tier:      TierPhone,
		Status:    StatusPending,
		CreatedAt: now,
		ExpiresAt: now.Add(24 * time.Hour),
	}

	if err := store.Create(context.Background(), a); err != nil {
		t.Fatalf("Create: %v", err)
	}

	got, err := store.Get(context.Background(), "approval-wave179")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.ID != "approval-wave179" {
		t.Errorf("expected ID 'approval-wave179', got %q", got.ID)
	}
	if got.AgentID != "kimi-1" {
		t.Errorf("expected AgentID 'kimi-1', got %q", got.AgentID)
	}
}

func TestWave185_ApprovalStore_Get_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewApprovalStore(db)

	_, err := store.Get(context.Background(), "nonexistent-approval")
	if err == nil {
		t.Error("expected error for non-existent approval")
	}
}

// ---------------------------------------------------------------------------
// List methods (empty)
// ---------------------------------------------------------------------------

func TestWave185_ApprovalStore_ListPending_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewApprovalStore(db)

	approvals, err := store.ListPending(context.Background(), 50)
	if err != nil {
		t.Errorf("ListPending: %v", err)
	}
	if len(approvals) != 0 {
		t.Errorf("expected empty, got %d", len(approvals))
	}
}

func TestWave185_ApprovalStore_ListByStatus_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewApprovalStore(db)

	approvals, err := store.ListByStatus(context.Background(), StatusPending, 50)
	if err != nil {
		t.Errorf("ListByStatus: %v", err)
	}
	_ = approvals
}

func TestWave185_ApprovalStore_ListByTier_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewApprovalStore(db)

	approvals, err := store.ListByTier(context.Background(), TierDesktop, 50)
	if err != nil {
		t.Errorf("ListByTier: %v", err)
	}
	_ = approvals
}

func TestWave185_ApprovalStore_ListByAgent_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewApprovalStore(db)

	approvals, err := store.ListByAgent(context.Background(), "kimi-99", 50)
	if err != nil {
		t.Errorf("ListByAgent: %v", err)
	}
	_ = approvals
}

func TestWave185_ApprovalStore_ListByTask_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewApprovalStore(db)

	approvals, err := store.ListByTask(context.Background(), "task-nonexistent", 50)
	if err != nil {
		t.Errorf("ListByTask: %v", err)
	}
	_ = approvals
}

// ---------------------------------------------------------------------------
// ApprovalStore.ExpireOldApprovals
// ---------------------------------------------------------------------------

func TestWave185_ApprovalStore_ExpireOld_NoneExpired(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewApprovalStore(db)

	// Expire approvals older than 1 year ago — none should match
	count, err := store.ExpireOldApprovals(context.Background(), time.Now().Add(-365*24*time.Hour))
	if err != nil {
		t.Errorf("ExpireOldApprovals: %v", err)
	}
	if count != 0 {
		t.Errorf("expected 0 expired, got %d", count)
	}
}
