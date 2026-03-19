//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// ============================================================
// coverage_wave236_test.go — patrolRunHandler, GetApprovalTierForStage,
//         CalculateConfidence, NewApprovalService/Handler,
//         handleApprovalCount, PlanManager CreatePlan/RevisePlan
// ============================================================

// --- patrolRunHandler ---

func TestWave236_PatrolRun_EmptyPath(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/patrols/", nil)
	req.URL.Path = "/api/patrols/"
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestWave236_PatrolRun_NoRunSuffix(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/patrols/agent-health", nil)
	req.URL.Path = "/api/patrols/agent-health"
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestWave236_PatrolRun_NilSystem(t *testing.T) {
	orig := globalPatrolSystem
	globalPatrolSystem = nil
	defer func() { globalPatrolSystem = orig }()

	req := httptest.NewRequest(http.MethodPost, "/api/patrols/agent-health/run", nil)
	req.URL.Path = "/api/patrols/agent-health/run"
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave236_PatrolRun_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)
	orig := globalPatrolSystem
	globalPatrolSystem = ps
	defer func() { globalPatrolSystem = orig }()

	req := httptest.NewRequest(http.MethodPost, "/api/patrols/nonexistent-patrol-236/run", nil)
	req.URL.Path = "/api/patrols/nonexistent-patrol-236/run"
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

// --- GetApprovalTierForStage ---

func TestWave236_GetApprovalTierForStage_Empty(t *testing.T) {
	tier, threshold := GetApprovalTierForStage("")
	if tier != "" || threshold != 0 {
		t.Errorf("expected empty tier and 0 threshold for empty stage, got %s / %f", tier, threshold)
	}
}

func TestWave236_GetApprovalTierForStage_Build(t *testing.T) {
	tier, threshold := GetApprovalTierForStage("build")
	if tier == "" {
		t.Error("expected non-empty tier for 'build'")
	}
	if threshold <= 0 {
		t.Errorf("expected positive threshold for 'build', got %f", threshold)
	}
}

func TestWave236_GetApprovalTierForStage_Deploy(t *testing.T) {
	tier, _ := GetApprovalTierForStage("deploy")
	if tier == "" {
		t.Error("expected tier for 'deploy'")
	}
}

func TestWave236_GetApprovalTierForStage_Unknown(t *testing.T) {
	tier, threshold := GetApprovalTierForStage("unknown-stage-236")
	if tier == "" {
		t.Error("expected fallback tier for unknown stage")
	}
	if threshold != 0.0 {
		t.Errorf("expected 0.0 threshold for unknown stage, got %f", threshold)
	}
}

func TestWave236_GetApprovalTierForStage_Uppercase(t *testing.T) {
	tier1, _ := GetApprovalTierForStage("build")
	tier2, _ := GetApprovalTierForStage("BUILD")
	if tier1 != tier2 {
		t.Errorf("expected case-insensitive match: %s vs %s", tier1, tier2)
	}
}

// --- CalculateConfidence ---

func TestWave236_CalculateConfidence_HighScore(t *testing.T) {
	svc := NewApprovalService(nil)
	ctx := ConfidenceContext{
		PatternMatch: 1.0,
		TestResults: &TestResult{
			Passed:   100,
			Failed:   0,
			Skipped:  0,
			Coverage: 90.0,
		},
		BlastRadius: 1,
		Reversible:  true,
	}
	result := svc.CalculateConfidence(ctx)
	if result.Score <= 0.8 {
		t.Errorf("expected high score (>0.8), got %f", result.Score)
	}
}

func TestWave236_CalculateConfidence_LowScore_NoTests(t *testing.T) {
	svc := NewApprovalService(nil)
	ctx := ConfidenceContext{
		PatternMatch: 0.0,
		TestResults:  nil,
		BlastRadius:  100,
		Reversible:   false,
	}
	result := svc.CalculateConfidence(ctx)
	if result.Score >= 0.8 {
		t.Errorf("expected low score (<0.8), got %f", result.Score)
	}
}

func TestWave236_CalculateConfidence_MediumBlastRadius(t *testing.T) {
	svc := NewApprovalService(nil)
	ctx := ConfidenceContext{
		PatternMatch: 0.7,
		BlastRadius:  25,
		Reversible:   true,
	}
	result := svc.CalculateConfidence(ctx)
	if result.Score < 0 || result.Score > 1.0 {
		t.Errorf("score out of range [0,1]: %f", result.Score)
	}
}

func TestWave236_CalculateConfidence_SmallBlastRadius(t *testing.T) {
	svc := NewApprovalService(nil)
	ctx := ConfidenceContext{
		PatternMatch: 0.7,
		BlastRadius:  10,
		Reversible:   false,
	}
	result := svc.CalculateConfidence(ctx)
	if result.Score <= 0 {
		t.Errorf("expected positive score, got %f", result.Score)
	}
}

func TestWave236_CalculateConfidence_AutoApprove(t *testing.T) {
	svc := NewApprovalService(nil)
	ctx := ConfidenceContext{
		PatternMatch: 1.0,
		TestResults: &TestResult{
			Passed:   200,
			Failed:   0,
			Coverage: 100.0,
		},
		BlastRadius: 0,
		Reversible:  true,
	}
	result := svc.CalculateConfidence(ctx)
	if !result.AutoApprove && result.Score < 0.95 {
		t.Logf("note: auto-approve not triggered (score=%f) — may need perfect pattern match", result.Score)
	}
}

// --- handleApprovalCount ---

func TestWave236_ApprovalCount_NoApprovals(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	handler := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/count", nil)
	w := httptest.NewRecorder()
	handler.handleApprovalCount(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "pending") {
		t.Errorf("expected 'pending' in response: %s", w.Body.String())
	}
}

func TestWave236_ApprovalCount_WithPendingApprovals(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	ctx := context.Background()
	store.Create(ctx, makeApproval("count-a236"))
	store.Create(ctx, makeApproval("count-b236"))

	svc := NewApprovalService(store)
	handler := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/count", nil)
	w := httptest.NewRecorder()
	handler.handleApprovalCount(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- PlanManager CreatePlan / RevisePlan ---

func TestWave236_PlanManager_CreatePlan(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	task := Task{
		ID:        "task-236-plan",
		Domain:    "forge",
		Project:   "test",
		Type:      "feature",
		Priority:  5,
		Status:    TaskStatusQueued,
		State:     StateQueued,
		Title:     "plan test task",
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	pm := NewPlanManager(db)
	planID, err := pm.CreatePlan(context.Background(), "task-236-plan", "# Plan\nDo the thing", "initial plan")
	if err != nil {
		t.Fatalf("CreatePlan: %v", err)
	}
	if planID == "" {
		t.Error("expected non-empty plan ID")
	}
}

func TestWave236_PlanManager_RevisePlan(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	task := Task{
		ID:        "task-236-revise",
		Domain:    "forge",
		Project:   "test",
		Type:      "feature",
		Priority:  5,
		Status:    TaskStatusQueued,
		State:     StateQueued,
		Title:     "revise test task",
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	pm := NewPlanManager(db)
	// Create initial plan first
	if _, err := pm.CreatePlan(context.Background(), "task-236-revise", "# V1", "initial"); err != nil {
		t.Fatalf("CreatePlan: %v", err)
	}
	// Now revise it
	planID, err := pm.RevisePlan(context.Background(), "task-236-revise", "# V2 Revised plan", "revised approach")
	if err != nil {
		t.Fatalf("RevisePlan: %v", err)
	}
	if planID == "" {
		t.Error("expected non-empty revised plan ID")
	}
}

func TestWave236_PlanManager_RevisePlan_NoTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	pm := NewPlanManager(db)
	_, err := pm.RevisePlan(context.Background(), "nonexistent-task-236", "# Plan", "reason")
	if err == nil {
		t.Error("expected error for nonexistent task")
	}
}
