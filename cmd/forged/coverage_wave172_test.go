//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 172: metrics.go — Metrics methods + GetPatrolMetrics + handlers
// Targets:
//   Metrics.RecordTaskCreated    (metrics.go:148) — increments counter
//   Metrics.RecordTaskCompleted  (metrics.go:154) — increments counter
//   Metrics.RecordTaskFailed     (metrics.go:160) — increments counter
//   Metrics.RecordTaskState      (metrics.go:166) — positive delta
//   Metrics.RecordTaskDuration   (metrics.go:173) — observes duration
//   Metrics.RecordAgentTaskCompleted (metrics.go:178) — delegates to complete+duration
//   Metrics.RecordAgentTaskFailed    (metrics.go:184) — delegates to failed+duration
//   Metrics.RecordTaskEnqueued   (metrics.go:190) — increments enqueued
//   Metrics.RecordHeartbeat      (metrics.go:196) — increments heartbeats
//   Metrics.SetActiveConnections (metrics.go:269) — sets value
//   GetPatrolMetrics             (metrics.go:324) — returns non-nil map
//   DebugHandler                 (metrics.go:338) — returns JSON
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Metrics.RecordTaskCreated
// ---------------------------------------------------------------------------

func TestWave172_RecordTaskCreated(t *testing.T) {
	before := atomic.LoadInt64(&metrics.TasksCreated)
	metrics.RecordTaskCreated("feature", 5)
	after := atomic.LoadInt64(&metrics.TasksCreated)
	if after <= before {
		t.Error("expected TasksCreated to increment")
	}
}

// ---------------------------------------------------------------------------
// Metrics.RecordTaskCompleted
// ---------------------------------------------------------------------------

func TestWave172_RecordTaskCompleted(t *testing.T) {
	before := atomic.LoadInt64(&metrics.TasksCompleted)
	metrics.RecordTaskCompleted()
	after := atomic.LoadInt64(&metrics.TasksCompleted)
	if after <= before {
		t.Error("expected TasksCompleted to increment")
	}
}

// ---------------------------------------------------------------------------
// Metrics.RecordTaskFailed
// ---------------------------------------------------------------------------

func TestWave172_RecordTaskFailed(t *testing.T) {
	before := atomic.LoadInt64(&metrics.TasksFailed)
	metrics.RecordTaskFailed()
	after := atomic.LoadInt64(&metrics.TasksFailed)
	if after <= before {
		t.Error("expected TasksFailed to increment")
	}
}

// ---------------------------------------------------------------------------
// Metrics.RecordTaskState
// ---------------------------------------------------------------------------

func TestWave172_RecordTaskState_PositiveDelta(t *testing.T) {
	// positive delta should call tasksCreated gauge — no panic
	metrics.RecordTaskState("running", 1)
}

func TestWave172_RecordTaskState_ZeroDelta(t *testing.T) {
	// Zero delta — skips the gauge call
	metrics.RecordTaskState("running", 0)
}

// ---------------------------------------------------------------------------
// Metrics.RecordTaskDuration
// ---------------------------------------------------------------------------

func TestWave172_RecordTaskDuration(t *testing.T) {
	// Just verify no panic
	metrics.RecordTaskDuration(100 * time.Millisecond)
}

// ---------------------------------------------------------------------------
// Metrics.RecordAgentTaskCompleted / Failed
// ---------------------------------------------------------------------------

func TestWave172_RecordAgentTaskCompleted(t *testing.T) {
	before := atomic.LoadInt64(&metrics.TasksCompleted)
	metrics.RecordAgentTaskCompleted("kimi", 200*time.Millisecond)
	after := atomic.LoadInt64(&metrics.TasksCompleted)
	if after <= before {
		t.Error("expected TasksCompleted to increment via RecordAgentTaskCompleted")
	}
}

func TestWave172_RecordAgentTaskFailed(t *testing.T) {
	before := atomic.LoadInt64(&metrics.TasksFailed)
	metrics.RecordAgentTaskFailed("kimi", 50*time.Millisecond)
	after := atomic.LoadInt64(&metrics.TasksFailed)
	if after <= before {
		t.Error("expected TasksFailed to increment via RecordAgentTaskFailed")
	}
}

// ---------------------------------------------------------------------------
// Metrics.RecordTaskEnqueued
// ---------------------------------------------------------------------------

func TestWave172_RecordTaskEnqueued(t *testing.T) {
	before := atomic.LoadInt64(&metrics.TasksEnqueued)
	metrics.RecordTaskEnqueued()
	after := atomic.LoadInt64(&metrics.TasksEnqueued)
	if after <= before {
		t.Error("expected TasksEnqueued to increment")
	}
}

// ---------------------------------------------------------------------------
// Metrics.RecordHeartbeat
// ---------------------------------------------------------------------------

func TestWave172_RecordHeartbeat(t *testing.T) {
	before := atomic.LoadInt64(&metrics.HeartbeatsReceived)
	metrics.RecordHeartbeat()
	after := atomic.LoadInt64(&metrics.HeartbeatsReceived)
	if after <= before {
		t.Error("expected HeartbeatsReceived to increment")
	}
}

// ---------------------------------------------------------------------------
// Metrics.RecordRequest
// ---------------------------------------------------------------------------

func TestWave172_RecordRequest(t *testing.T) {
	before := atomic.LoadInt64(&metrics.RequestsTotal)
	metrics.RecordRequest("GET", "/api/tasks", 200, 50*time.Millisecond)
	after := atomic.LoadInt64(&metrics.RequestsTotal)
	if after <= before {
		t.Error("expected RequestsTotal to increment")
	}
}

// ---------------------------------------------------------------------------
// Metrics.SetActiveConnections
// ---------------------------------------------------------------------------

func TestWave172_SetActiveConnections(t *testing.T) {
	// Should not panic
	metrics.SetActiveConnections(42)
}

// ---------------------------------------------------------------------------
// GetPatrolMetrics
// ---------------------------------------------------------------------------

func TestWave172_GetPatrolMetrics_NonNilMap(t *testing.T) {
	m := GetPatrolMetrics()
	if m == nil {
		t.Error("expected non-nil map")
	}
	if _, ok := m["executions"]; !ok {
		t.Error("expected 'executions' key in patrol metrics")
	}
}

// ---------------------------------------------------------------------------
// DebugHandler
// ---------------------------------------------------------------------------

func TestWave172_DebugHandler_Returns200(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/debug", nil)
	w := httptest.NewRecorder()
	DebugHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected application/json, got %q", ct)
	}
}
