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
// Wave 208: metrics.go — Metrics methods + patrol record + handlers
// Targets:
//   Metrics.RecordRequest       (metrics.go:129)
//   Metrics.RecordTaskCreated   (metrics.go:148)
//   Metrics.RecordTaskCompleted (metrics.go:154)
//   Metrics.RecordTaskFailed    (metrics.go:160)
//   Metrics.RecordTaskState     (metrics.go:166)
//   Metrics.RecordTaskDuration  (metrics.go:173)
//   Metrics.RecordAgentTaskCompleted (metrics.go:178)
//   Metrics.RecordAgentTaskFailed    (metrics.go:184)
//   Metrics.RecordTaskEnqueued  (metrics.go:190)
//   Metrics.RecordHeartbeat     (metrics.go:196)
//   Metrics.SetActiveConnections (metrics.go:269)
//   RecordPatrolExecution       (metrics.go:274) — nil db
//   RecordPatrolError           (metrics.go:288) — nil db
//   RecordPatrolSuccess         (metrics.go:306) — nil db
//   GetPatrolMetrics            (metrics.go:324)
//   DebugHandler                (metrics.go:338)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Metrics methods — use global metrics instance
// ---------------------------------------------------------------------------

func TestWave208_Metrics_RecordRequest(t *testing.T) {
	metrics.RecordRequest(http.MethodGet, "/api/test", 200, 10*time.Millisecond)
}

func TestWave208_Metrics_RecordTaskCreated(t *testing.T) {
	metrics.RecordTaskCreated("feature", 5)
}

func TestWave208_Metrics_RecordTaskCompleted(t *testing.T) {
	metrics.RecordTaskCompleted()
}

func TestWave208_Metrics_RecordTaskFailed(t *testing.T) {
	metrics.RecordTaskFailed()
}

func TestWave208_Metrics_RecordTaskState(t *testing.T) {
	metrics.RecordTaskState("RUNNING", 1)
	metrics.RecordTaskState("RUNNING", 0) // delta=0 branch
}

func TestWave208_Metrics_RecordTaskDuration(t *testing.T) {
	metrics.RecordTaskDuration(5 * time.Second)
}

func TestWave208_Metrics_RecordAgentTaskCompleted(t *testing.T) {
	metrics.RecordAgentTaskCompleted("agent-wave208", 2*time.Second)
}

func TestWave208_Metrics_RecordAgentTaskFailed(t *testing.T) {
	metrics.RecordAgentTaskFailed("agent-wave208", 1*time.Second)
}

func TestWave208_Metrics_RecordTaskEnqueued(t *testing.T) {
	metrics.RecordTaskEnqueued()
}

func TestWave208_Metrics_RecordHeartbeat(t *testing.T) {
	metrics.RecordHeartbeat()
}

func TestWave208_Metrics_SetActiveConnections(t *testing.T) {
	metrics.SetActiveConnections(5)
}

// ---------------------------------------------------------------------------
// RecordPatrolExecution/Error/Success — nil db path (no-op)
// ---------------------------------------------------------------------------

func TestWave208_RecordPatrolExecution_NilDB(t *testing.T) {
	RecordPatrolExecution(nil, "test-patrol")
}

func TestWave208_RecordPatrolError_NilDB(t *testing.T) {
	RecordPatrolError(nil, "test-patrol", "some error")
}

func TestWave208_RecordPatrolSuccess_NilDB(t *testing.T) {
	RecordPatrolSuccess(nil, "test-patrol")
}

func TestWave208_RecordPatrolExecution_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	RecordPatrolExecution(db, "wave208-patrol")
}

func TestWave208_RecordPatrolError_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	RecordPatrolExecution(db, "wave208-patrol-err")
	RecordPatrolError(db, "wave208-patrol-err", "test error message")
}

func TestWave208_RecordPatrolSuccess_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	RecordPatrolExecution(db, "wave208-patrol-ok")
	RecordPatrolSuccess(db, "wave208-patrol-ok")
}

// ---------------------------------------------------------------------------
// GetPatrolMetrics
// ---------------------------------------------------------------------------

func TestWave208_GetPatrolMetrics_NonNil(t *testing.T) {
	m := GetPatrolMetrics()
	if m == nil {
		t.Error("expected non-nil patrol metrics")
	}
}

// ---------------------------------------------------------------------------
// DebugHandler
// ---------------------------------------------------------------------------

func TestWave208_DebugHandler_GET(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/debug", nil)
	w := httptest.NewRecorder()
	DebugHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}
