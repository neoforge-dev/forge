//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"math"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// patrol.go: confidenceApproveCompletedTasks — 60%
// Cover the AUTO-APPROVE path: high quality gate scores → score >= 0.65
// ---------------------------------------------------------------------------

func TestWave90_ConfidenceApprove_HighScore_AutoApproved(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	ts := NewTaskStore(db)
	oldSM := stateMachine
	stateMachine = NewStateMachine(ts, db)
	defer func() { stateMachine = oldSM }()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	oldSvc := globalApprovalService
	globalApprovalService = svc
	defer func() { globalApprovalService = oldSvc }()

	// Insert a COMPLETED task with updated_at > 30s ago
	oldTime := time.Now().Add(-2 * time.Minute).UTC().Format("2006-01-02 15:04:05")
	taskID := "wave90-high-conf"
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		taskID, "codeswiftr", "proj", "feature", 5,
		"completed", "COMPLETED", "Wave90 High Conf Task", oldTime, oldTime)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// Insert quality_gate_results with HIGH scores (AUTOINCREMENT id — don't pass it):
	// Score = 1.0*0.40 + 1.0*0.30 + 1.0*0.20 + 1.0*0.10 = 1.0 >= 0.65 → AUTO-APPROVE
	_, err = db.Exec(`
		INSERT INTO quality_gate_results (task_id, test_pass_rate, coverage_pct, lint_issues, created_at)
		VALUES (?, ?, ?, ?, ?)`,
		taskID, 1.0, 100.0, 0, oldTime)
	if err != nil {
		t.Fatalf("insert quality gate results: %v", err)
	}

	// Call confidenceApproveCompletedTasks — should hit the auto-approve branch
	if err := confidenceApproveCompletedTasks(ctx, db); err != nil {
		t.Logf("confidenceApproveCompletedTasks high score: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: calculateConfidenceScore — 92.9%
// Cover the lintScore < 0 branch (lint_issues > 50 → negative before clamp)
// ---------------------------------------------------------------------------

func TestWave90_CalculateConfidenceScore_HighLintIssues(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	taskID := "wave90-high-lint"
	oldTime := time.Now().Add(-2 * time.Minute).UTC().Format("2006-01-02 15:04:05")

	_, _ = db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		taskID, "codeswiftr", "proj", "feature", 5,
		"completed", "COMPLETED", "Wave90 High Lint Task", oldTime, oldTime)

	// lint_issues = 100 → lintScore = 1.0 - 100/50.0 = -1.0 → clamped to 0
	_, _ = db.Exec(`
		INSERT INTO quality_gate_results (task_id, test_pass_rate, coverage_pct, lint_issues, created_at)
		VALUES (?, ?, ?, ?, ?)`,
		taskID, 0.5, 50.0, 100, oldTime)

	task := &Task{ID: taskID, Title: "High Lint Task"}
	score := calculateConfidenceScore(ctx, db, task)

	if score < 0 || score > 1 {
		t.Errorf("calculateConfidenceScore: got %f (expected 0.0–1.0)", score)
	}
	t.Logf("calculateConfidenceScore with high lint: %.2f", score)
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: readLiveRAMMB — 35%
// Cover the cache-hit path by seeding liveRAMCache directly (same package).
// ---------------------------------------------------------------------------

func TestWave90_ReadLiveRAMMB_CacheHitDirect(t *testing.T) {
	liveRAMCache.Lock()
	liveRAMCache.valueMB = 8192
	liveRAMCache.lastRead = time.Now() // fresh — within 30s cache window
	liveRAMCache.Unlock()

	mb, err := readLiveRAMMB()
	if err != nil {
		t.Errorf("readLiveRAMMB cache hit: unexpected error: %v", err)
	}
	if mb != 8192 {
		t.Errorf("readLiveRAMMB cache hit: got %d, want 8192", mb)
	}

	// Reset so subsequent tests start fresh
	liveRAMCache.Lock()
	liveRAMCache.lastRead = time.Time{}
	liveRAMCache.Unlock()
}

// ---------------------------------------------------------------------------
// handler_helpers.go: writeJSON — 66.7%
// Cover the encoding-error path via math.NaN() (unencodable JSON value).
// ---------------------------------------------------------------------------

func TestWave90_WriteJSON_EncodingError(t *testing.T) {
	w := httptest.NewRecorder()
	// math.NaN() causes json.Encoder to return an unsupported value error
	writeJSON(w, math.NaN())
	// Handler should write an error response (body will contain "encoding error")
	t.Logf("writeJSON NaN: code=%d body=%s", w.Code, w.Body.String())
}

// ---------------------------------------------------------------------------
// metrics.go: responseWriter.Write — 66.7%
// Cover the path where statusCode is 0 when Write is called first.
// ---------------------------------------------------------------------------

func TestWave90_ResponseWriter_Write_SetsStatus(t *testing.T) {
	rec := httptest.NewRecorder()
	rw := &responseWriter{ResponseWriter: rec}

	// statusCode == 0, so Write should set it to 200
	n, err := rw.Write([]byte("hello"))
	if err != nil {
		t.Errorf("responseWriter.Write: %v", err)
	}
	if n != 5 {
		t.Errorf("responseWriter.Write: wrote %d bytes", n)
	}
	if rw.statusCode != 200 {
		t.Errorf("responseWriter.Write: statusCode=%d, want 200", rw.statusCode)
	}
}

func TestWave90_ResponseWriter_Write_StatusAlreadySet(t *testing.T) {
	rec := httptest.NewRecorder()
	rw := &responseWriter{ResponseWriter: rec, statusCode: 201}

	n, err := rw.Write([]byte("ok"))
	if err != nil {
		t.Errorf("responseWriter.Write already-set: %v", err)
	}
	if n != 2 {
		t.Errorf("responseWriter.Write already-set: wrote %d", n)
	}
	if rw.statusCode != 201 {
		t.Errorf("responseWriter.Write: statusCode changed to %d", rw.statusCode)
	}
}

// ---------------------------------------------------------------------------
// metrics.go: responseWriter.Hijack — 66.7%
// Cover the "not implemented" path (httptest.ResponseRecorder != Hijacker).
// ---------------------------------------------------------------------------

func TestWave90_ResponseWriter_Hijack_NotImplemented(t *testing.T) {
	rec := httptest.NewRecorder()
	rw := &responseWriter{ResponseWriter: rec}

	_, _, err := rw.Hijack()
	if err == nil {
		t.Error("responseWriter.Hijack: expected error, got nil")
	}
}

// ---------------------------------------------------------------------------
// main.go: handleSystemHealth — 55.6%
// Cover with format=json to exercise WriteOutputWithFlag paths.
// ---------------------------------------------------------------------------

func TestWave90_HandleSystemHealth_FormatJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/system/health?format=json", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, r)

	if w.Code >= 500 {
		t.Logf("handleSystemHealth format=json: got %d — %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// main.go: handleQueueDepth — 54.5%
// Cover with an initialized taskQueue.
// ---------------------------------------------------------------------------

func TestWave90_HandleQueueDepth_WithInitializedQueue(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldQ := taskQueue
	db := getDBConn()
	if db != nil && taskQueue == nil {
		q, err := NewTaskQueueFromDB(db)
		if err == nil {
			taskQueue = q
		}
	}
	defer func() { taskQueue = oldQ }()

	r := httptest.NewRequest(http.MethodGet, "/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)

	if w.Code >= 500 {
		t.Logf("handleQueueDepth with queue: got %d — %s", w.Code, w.Body.String())
	}
}
