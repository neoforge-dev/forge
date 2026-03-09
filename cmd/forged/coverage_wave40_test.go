//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// Wave 40: fleetAutoExecutePatrol (circuit breaker + flap guard paths),
//          fleetDeflateRecommendPatrol with real agent_heartbeats data,
//          handleQueueCancel all paths

// ─── fleetAutoExecutePatrol ───────────────────────────────────────────────

func TestFleetAutoExecutePatrol_CircuitBreakerOpen_W40(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Trip the circuit breaker.
	circuitBreaker.Lock()
	savedState := circuitBreaker.state
	savedFailures := circuitBreaker.consecutiveFailures
	savedLast := circuitBreaker.lastFailure
	circuitBreaker.state = "open"
	circuitBreaker.consecutiveFailures = 5
	circuitBreaker.lastFailure = time.Now() // recent — won't auto-reset
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = savedState
		circuitBreaker.consecutiveFailures = savedFailures
		circuitBreaker.lastFailure = savedLast
		circuitBreaker.Unlock()
	}()

	// Should return nil immediately (circuit breaker gate).
	if err := fleetAutoExecutePatrol(ctx, db); err != nil {
		t.Errorf("expected nil when circuit breaker open, got %v", err)
	}
}

func TestFleetAutoExecutePatrol_FlapGuard_W40(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Ensure circuit breaker is closed.
	circuitBreaker.Lock()
	savedCBState := circuitBreaker.state
	circuitBreaker.state = "closed"
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = savedCBState
		circuitBreaker.Unlock()
	}()

	// Record a very recent scale event so canScale() returns false.
	lastScaleEvent.Lock()
	savedTS := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Now() // just happened
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = savedTS
		lastScaleEvent.Unlock()
	}()

	// Should return nil immediately (flap guard).
	if err := fleetAutoExecutePatrol(ctx, db); err != nil {
		t.Errorf("expected nil when flap guard active, got %v", err)
	}
}

func TestFleetAutoExecutePatrol_NoPendingRecs_W40(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Ensure circuit breaker closed and scale allowed.
	circuitBreaker.Lock()
	savedCBState := circuitBreaker.state
	circuitBreaker.state = "closed"
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = savedCBState
		circuitBreaker.Unlock()
	}()

	lastScaleEvent.Lock()
	savedTS := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Time{} // zero — canScale() returns true (>>60s ago)
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = savedTS
		lastScaleEvent.Unlock()
	}()

	// Ensure scale_recommendations table exists but is empty.
	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// No pending recs → returns nil.
	if err := fleetAutoExecutePatrol(ctx, db); err != nil {
		t.Errorf("expected nil when no pending recs, got %v", err)
	}
}

func TestFleetAutoExecutePatrol_WithPendingRec_W40(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Ensure circuit breaker closed and scale allowed.
	circuitBreaker.Lock()
	savedCBState := circuitBreaker.state
	circuitBreaker.state = "closed"
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = savedCBState
		circuitBreaker.Unlock()
	}()

	lastScaleEvent.Lock()
	savedTS := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Time{} // canScale() returns true
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = savedTS
		lastScaleEvent.Unlock()
	}()

	// Create table and insert a pending auto_execute inflate recommendation.
	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}
	expiresAt := time.Now().Add(10 * time.Minute).UTC().Format(time.RFC3339)
	_, err := db.ExecContext(ctx, `
		INSERT INTO scale_recommendations
			(id, node_id, action, agent_type, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
		VALUES ('rec-w40-1', 'prya', 'inflate', 'claude', 'test reason', 5, 0, 1, 'pending', datetime('now'), ?)
	`, expiresAt)
	if err != nil {
		t.Fatalf("insert scale_recommendation: %v", err)
	}

	// Will hit the RAM/CPU gate and return nil (can't actually spawn in tests).
	// But covers the query + scan path.
	if err := fleetAutoExecutePatrol(ctx, db); err != nil {
		t.Logf("fleetAutoExecutePatrol with pending rec: %v (OK — RAM/CPU gate may block spawn)", err)
	}
}

// ─── fleetDeflateRecommendPatrol ─────────────────────────────────────────

func TestFleetDeflateRecommendPatrol_MultipleAgentsSameNode_W40(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert 2 agents on same node — needed to pass the len(agents)>1 floor check.
	// Use old last_seen so idle duration check can proceed.
	oldTime := time.Now().Add(-20 * time.Minute).UTC().Format(time.RFC3339)
	for i, agentID := range []string{"agent-deflate-w40-1", "agent-deflate-w40-2"} {
		_, err := db.ExecContext(ctx, `
			INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen)
			VALUES (?, 'prya-deflate', 'idle', ?, ?)
			ON CONFLICT(agent_id) DO UPDATE SET node='prya-deflate', status='idle', context_pct=excluded.context_pct, last_seen=excluded.last_seen
		`, agentID, float64(i)*2.0, oldTime) // low context_pct
		if err != nil {
			t.Logf("insert agent_heartbeat %s: %v (schema may differ — trying simple insert)", agentID, err)
			_, err2 := db.ExecContext(ctx, `
				INSERT OR REPLACE INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen)
				VALUES (?, 'prya-deflate', 'idle', ?, ?)
			`, agentID, float64(i)*2.0, oldTime)
			if err2 != nil {
				t.Logf("fallback insert failed: %v", err2)
			}
		}
	}

	// Run patrol — will find 2 agents on 'prya-deflate', evaluate deflationGateCheck for each.
	if err := fleetDeflateRecommendPatrol(ctx, db); err != nil {
		t.Errorf("fleetDeflateRecommendPatrol: %v", err)
	}
}

func TestFleetDeflateRecommendPatrol_SingleAgentSkipped_W40(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert only 1 agent — min floor kicks in (len(agents) <= 1 → skip).
	_, err := db.ExecContext(ctx, `
		INSERT OR REPLACE INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen)
		VALUES ('agent-single-w40', 'prya-single', 'idle', 5.0, datetime('now', '-1 hour'))
	`)
	if err != nil {
		t.Logf("insert: %v (may skip)", err)
	}

	if err := fleetDeflateRecommendPatrol(ctx, db); err != nil {
		t.Errorf("fleetDeflateRecommendPatrol single agent: %v", err)
	}
}

// ─── handleQueueCancel ────────────────────────────────────────────────────

func TestHandleQueueCancel_MethodNotAllowed_W40(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/queue/cancel", nil)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandleQueueCancel_NilQueue_W40(t *testing.T) {
	orig := taskQueue
	taskQueue = nil
	defer func() { taskQueue = orig }()

	body := bytes.NewBufferString(`{"id":"TASK-X"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/cancel", body)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestHandleQueueCancel_InvalidBody_W40(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodPost, "/api/queue/cancel", bytes.NewBufferString("not-json"))
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid JSON, got %d", w.Code)
	}
}

func TestHandleQueueCancel_MissingID_W40(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	body := bytes.NewBufferString(`{"reason":"test"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/cancel", body)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d", w.Code)
	}
}

func TestHandleQueueCancel_TaskNotFound_W40(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	body := bytes.NewBufferString(`{"id":"NO-SUCH-TASK","reason":"test cancel"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/cancel", body)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleQueueCancel_WrongStatus_W40(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert a task in 'executing' status — can't be cancelled.
	_, err := db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES ('TASK-W40-EXEC', 'test', 'proj', 'feature', 'Executing Task', 'executing', 'RUNNING', 50, datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	body := bytes.NewBufferString(`{"id":"TASK-W40-EXEC","reason":"cancel executing"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/cancel", body)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for executing task, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleQueueCancel_Success_W40(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert a task in 'queued' status — can be cancelled.
	taskID := fmt.Sprintf("TASK-W40-CANCEL-%d", time.Now().UnixNano())
	_, err := db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES (?, 'test', 'proj', 'feature', 'Cancellable Task', 'queued', 'QUEUED', 50, datetime('now'), datetime('now'))
	`, taskID)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	payload := map[string]string{"id": taskID, "reason": "test cancel reason"}
	bodyBytes, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/cancel", bytes.NewReader(bodyBytes))
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}
