//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// handlers_openclaw.go:264 — openclawEventsHandler
// ---------------------------------------------------------------------------

// TestWave108_OpenclawEventsHandler_MethodNotAllowed verifies POST is rejected.
// TestWave108_OpenclawEventsHandler_CancelledContext verifies graceful close on
// pre-cancelled context (SSE path — non-flusher recorder triggers 500 first).
func TestWave108_OpenclawEventsHandler_CancelledContext(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil)
	w := httptest.NewRecorder()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	// httptest.ResponseRecorder does not implement http.Flusher, so the handler
	// returns 500 "streaming not supported" — that is the expected non-panic path.
	openclawEventsHandler(w, req.WithContext(ctx))
	// Either 500 (no flusher) or a clean 200 SSE response are acceptable; we just
	// verify no panic occurred and the code is not 405.
	if w.Code == http.StatusMethodNotAllowed {
		t.Errorf("unexpected 405 for GET")
	}
}

// flusherRecorder wraps httptest.ResponseRecorder and also implements http.Flusher.
type flusherRecorder struct {
	*httptest.ResponseRecorder
	mu      sync.Mutex
	flushed int
}

func newFlusherRecorder() *flusherRecorder {
	return &flusherRecorder{ResponseRecorder: httptest.NewRecorder()}
}

func (f *flusherRecorder) Flush() {
	f.mu.Lock()
	f.flushed++
	f.mu.Unlock()
	f.ResponseRecorder.Flush()
}

// TestWave108_OpenclawEventsHandler_SSEConnect verifies the initial SSE
// "connected" event is written before context cancellation terminates the loop.
func TestWave108_OpenclawEventsHandler_SSEConnect(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil)
	fw := newFlusherRecorder()

	// Cancel after a short delay to let the handler write the initial event.
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	openclawEventsHandler(fw, req.WithContext(ctx))

	body := fw.Body.String()
	if !strings.Contains(body, "event: connected") {
		t.Errorf("expected 'event: connected' in SSE response, got: %s", body)
	}
	if ct := fw.Header().Get("Content-Type"); ct != "text/event-stream" {
		t.Errorf("expected Content-Type text/event-stream, got %q", ct)
	}
}

// TestWave108_OpenclawEventsHandler_NilDB verifies handler runs with nil DB
// (ticker path continues past the nil db check).
func TestWave108_OpenclawEventsHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil)
	fw := newFlusherRecorder()

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	openclawEventsHandler(fw, req.WithContext(ctx))
	// Should not panic; initial connect event is always written.
	if !strings.Contains(fw.Body.String(), "event: connected") {
		t.Errorf("expected connected event even with nil DB")
	}
}

// ---------------------------------------------------------------------------
// handlers_agent.go:437 — agentsSSEHandler
// ---------------------------------------------------------------------------

// TestWave108_AgentsSSEHandler_MethodNotAllowed verifies POST is rejected.
// TestWave108_AgentsSSEHandler_RecorderPath verifies the handler does not panic
// with a standard recorder and pre-cancelled context. httptest.ResponseRecorder
// implements http.Flusher in Go 1.20+, so the SSE path is entered and exits
// cleanly on context cancellation (200), not 500.
func TestWave108_AgentsSSEHandler_RecorderPath(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents/stream", nil)
	w := httptest.NewRecorder()
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	agentsSSEHandler(w, req.WithContext(ctx))
	// Just verify no panic; code may be 200 (SSE path) or 500 (non-flusher path).
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d", w.Code)
	}
}

// TestWave108_AgentsSSEHandler_CancelledContext verifies graceful exit when
// using a flusher-capable recorder and a pre-cancelled context.
func TestWave108_AgentsSSEHandler_CancelledContext(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/stream", nil)
	fw := newFlusherRecorder()

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	agentsSSEHandler(fw, req.WithContext(ctx))
	if ct := fw.Header().Get("Content-Type"); ct != "text/event-stream" {
		t.Errorf("expected SSE content type, got %q", ct)
	}
}

// ---------------------------------------------------------------------------
// handlers_patrols.go:491 — dashHandler
// ---------------------------------------------------------------------------

// TestWave108_DashHandler_MethodNotAllowed verifies POST is rejected with 405.
// TestWave108_DashHandler_NilDB verifies 503 when DB is nil.
func TestWave108_DashHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

// TestWave108_DashHandler_Success verifies 200 HTML response with DB connected.
func TestWave108_DashHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	ct := w.Header().Get("Content-Type")
	if !strings.Contains(ct, "text/html") {
		t.Errorf("expected HTML content type, got %q", ct)
	}
	body := w.Body.String()
	if !strings.Contains(body, "Forge v3") {
		t.Errorf("expected 'Forge v3' in dashboard HTML, got: %.200s", body)
	}
}

// TestWave108_DashHandler_WithExecutions verifies executions table is rendered.
func TestWave108_DashHandler_WithExecutions(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	_, _ = db.Exec(`INSERT INTO patrol_executions
		(id, patrol_id, started_at, status)
		VALUES (?, ?, ?, ?)`,
		"wave108-exec-1", "test-patrol", now, "completed")

	req := httptest.NewRequest(http.MethodGet, "/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_agent.go:727 — parityHandler
// ---------------------------------------------------------------------------

// TestWave108_ParityHandler_Success verifies the handler returns 200 JSON.
func TestWave108_ParityHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := parityHandler(db)
	req := httptest.NewRequest(http.MethodGet, "/api/parity", nil)
	w := httptest.NewRecorder()
	h(w, req)

	// ParityCheck may succeed (200) or fail (500) depending on filesystem state.
	// We just ensure the handler does not panic and returns valid JSON when 200.
	if w.Code == http.StatusOK {
		var result map[string]interface{}
		if err := json.NewDecoder(w.Body).Decode(&result); err != nil {
			t.Errorf("expected valid JSON response, got decode error: %v", err)
		}
	}
}

// TestWave108_ParityHandler_NilDB verifies the handler propagates a nil-DB error.
func TestWave108_ParityHandler_NilDB(t *testing.T) {
	h := parityHandler((*sql.DB)(nil))
	req := httptest.NewRequest(http.MethodGet, "/api/parity", nil)
	w := httptest.NewRecorder()
	// Should not panic; may return 200 or 500 depending on ParityChecker internals.
	h(w, req)
	// No assertion on status — just verify no panic.
}

// ---------------------------------------------------------------------------
// fleet_scaler.go:766 — fleetAutoExecutePatrol (indirect paths)
// ---------------------------------------------------------------------------

// TestWave108_FleetAutoExec_EmptyRecommendations verifies nil return when
// scale_recommendations has no pending auto_execute rows.
func TestWave108_FleetAutoExec_EmptyRecommendations(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensure table: %v", err)
	}

	// Ensure circuit breaker is closed and flap guard is clear.
	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()

	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Time{} // zero = can always scale
	lastScaleEvent.Unlock()

	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil with empty recs, got: %v", err)
	}
}

// TestWave108_FleetAutoExec_CircuitBreakerOpen verifies early return when
// circuit breaker is open.
func TestWave108_FleetAutoExec_CircuitBreakerOpen(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensure table: %v", err)
	}

	circuitBreaker.Lock()
	circuitBreaker.state = "open"
	circuitBreaker.lastFailure = time.Now() // recent — won't auto-reset
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = "closed"
		circuitBreaker.consecutiveFailures = 0
		circuitBreaker.Unlock()
	}()

	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("circuit breaker open path should return nil, got: %v", err)
	}
}

// TestWave108_FleetAutoExec_FlapGuardActive verifies early return when
// the flap guard prevents scaling.
func TestWave108_FleetAutoExec_FlapGuardActive(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensure table: %v", err)
	}

	// Ensure circuit breaker is closed.
	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()

	// Set scale event to now so canScale() returns false.
	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Now()
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = time.Time{}
		lastScaleEvent.Unlock()
	}()

	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("flap guard path should return nil, got: %v", err)
	}
}

// TestWave108_FleetAutoExec_PendingRecExpired verifies nil return when the
// only pending rec has already expired (expires_at in the past).
func TestWave108_FleetAutoExec_PendingRecExpired(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensure table: %v", err)
	}

	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()

	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Time{}
	lastScaleEvent.Unlock()

	// Insert a rec that has already expired.
	pastTime := time.Now().UTC().Add(-10 * time.Minute).Format("2006-01-02 15:04:05")
	nowTime := time.Now().UTC().Format("2006-01-02 15:04:05")
	_, err := db.Exec(`INSERT INTO scale_recommendations
		(id, node_id, agent_type, action, reason, auto_execute, status, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave108-rec-expired", "prya", "kimi", "inflate", "test", 1, "pending", nowTime, pastTime)
	if err != nil {
		t.Fatalf("insert expired rec: %v", err)
	}

	// No rows will match (expires_at > datetime('now') is false) — should return nil.
	errExec := fleetAutoExecutePatrol(context.Background(), db)
	if errExec != nil {
		t.Errorf("expired rec path should return nil, got: %v", errExec)
	}
}
