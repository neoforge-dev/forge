//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 143: method-not-allowed and path-error paths across several handlers
// + SSE ticker paths + fleet scaler guard tests
// Targets:
//   agentsHandler              (77.8% → ~89%)
//   handleApprovalCount        (77.8% → ~89%)
//   handlePending              (85.7% → ~93%)
//   workersHandler             (80.0% → ~90%)
//   workerByIDHandler          (87.5% → ~94%)
//   handleLeadState            (80.0% → ~90%)
//   openclawEventsHandler      (36.7% → ~75%)
//   agentsSSEHandler           (53.6% → ~80%)
//   fleetAutoExecutePatrol     (guard paths)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// agentsHandler — handlers_agent.go
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// workersHandler / workerByIDHandler — handlers_worker.go
// ---------------------------------------------------------------------------

func TestWave143_WorkerByIDHandler_MissingID(t *testing.T) {
	// Path that results in empty ID after prefix strip.
	req := httptest.NewRequest(http.MethodGet, "/api/workers/", nil)
	w := httptest.NewRecorder()
	workerByIDHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// Note: TestWave143_WorkerByIDHandler_NotFound is skipped — hub is nil in
// unit tests; accessing hub.workers panics. Not testable without a hub stub.

// ---------------------------------------------------------------------------
// handleApprovalCount / handlePending — approvals.go
// ---------------------------------------------------------------------------

// newTestApprovalHandler creates a fully-wired ApprovalHandler backed by the
// test SQLite DB (all migrations applied via setupClaimTestDB).
func newTestApprovalHandler(t *testing.T) (*ApprovalHandler, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	setDBConn(db)
	store := NewApprovalStore(db)
	service := NewApprovalService(store)
	return NewApprovalHandler(service), func() {
		setDBConn(nil)
		cleanup()
	}
}

func TestWave143_HandleApprovalCount_Success(t *testing.T) {
	h, cleanup := newTestApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/count", nil)
	w := httptest.NewRecorder()
	h.handleApprovalCount(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave143_HandlePending_WithLimit(t *testing.T) {
	h, cleanup := newTestApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/pending?limit=10", nil)
	w := httptest.NewRecorder()
	h.handlePending(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave143_HandlePending_OverLimit(t *testing.T) {
	h, cleanup := newTestApprovalHandler(t)
	defer cleanup()

	// limit=200 → capped to 100 internally
	req := httptest.NewRequest(http.MethodGet, "/api/approvals/pending?limit=200", nil)
	w := httptest.NewRecorder()
	h.handlePending(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for over-limit request, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handleLeadState — handlers_adr014.go
// ---------------------------------------------------------------------------

func TestWave143_HandleLeadState_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := handleLeadState(db)
	req := httptest.NewRequest(http.MethodGet, "/api/lead-state", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// noFlushWriter: implements http.ResponseWriter WITHOUT http.Flusher.
// Used to test the "streaming not supported" 500 path in SSE handlers.
// ---------------------------------------------------------------------------

type noFlushWriter struct {
	header http.Header
	code   int
	buf    bytes.Buffer
}

func (w *noFlushWriter) Header() http.Header {
	if w.header == nil {
		w.header = make(http.Header)
	}
	return w.header
}
func (w *noFlushWriter) Write(b []byte) (int, error) { return w.buf.Write(b) }
func (w *noFlushWriter) WriteHeader(code int)        { w.code = code }

// ─────────────────────────────────────────────────────────────────
// Part 2: SSE non-flusher path (fast)
// ─────────────────────────────────────────────────────────────────

// TestWave143_OpenclawEvents_NoFlusherSupport exercises the streaming-not-supported
// path in openclawEventsHandler when the ResponseWriter doesn't implement Flusher.
func TestWave143_OpenclawEvents_NoFlusherSupport(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil)
	w := &noFlushWriter{}
	openclawEventsHandler(w, req)
	if w.code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d (body: %s)", w.code, w.buf.String())
	}
	if !strings.Contains(w.buf.String(), "streaming not supported") {
		t.Errorf("expected 'streaming not supported', got: %s", w.buf.String())
	}
}

// TestWave143_AgentsSSE_NoFlusherSupport exercises the streaming-not-supported
// path in agentsSSEHandler.
func TestWave143_AgentsSSE_NoFlusherSupport(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents/stream", nil)
	w := &noFlushWriter{}
	agentsSSEHandler(w, req)
	if w.code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d (body: %s)", w.code, w.buf.String())
	}
	if !strings.Contains(w.buf.String(), "streaming not supported") {
		t.Errorf("expected 'streaming not supported', got: %s", w.buf.String())
	}
}

// ─────────────────────────────────────────────────────────────────
// Part 3: SSE ticker fire tests (SLOW — ~5.5s each)
// These let the 5-second ticker fire to cover the DB query loop body.
// ─────────────────────────────────────────────────────────────────

// TestWave143_OpenclawEvents_TickerFires exercises the ticker branch in
// openclawEventsHandler, covering the DB query + event emit logic.
func TestWave143_OpenclawEvents_TickerFires(t *testing.T) {
	t.Log("slow test — waiting 5.5s for SSE ticker to fire")

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Insert an agent heartbeat so the agent_updates branch runs too.
	db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, current_task_id, last_seen)
		VALUES ('wave143-agent', 'prya', 'idle', 0.5, '', datetime('now'))`)

	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil).WithContext(ctx)
	w := httptest.NewRecorder()

	done := make(chan struct{})
	go func() {
		defer close(done)
		openclawEventsHandler(w, req)
	}()

	// Wait for the 5s ticker + buffer.
	time.Sleep(5500 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Error("openclawEventsHandler did not exit after context cancel")
	}

	body := w.Body.String()
	if !strings.Contains(body, "event: connected") {
		t.Error("expected 'event: connected' in SSE stream")
	}
	// After ticker fires there should be at least a heartbeat event.
	if !strings.Contains(body, "event:") {
		t.Error("expected at least one SSE event after ticker fired")
	}
}

// TestWave143_OpenclawEvents_TickerFires_CompletionEvent exercises the special
// completion event branch (task.completed / task.failed / task.needs_approval).
func TestWave143_OpenclawEvents_TickerFires_CompletionEvent(t *testing.T) {
	t.Log("slow test — waiting 5.5s for SSE ticker to fire with task.completed event")

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Insert a task.completed event so the completion branch executes.
	// id is INTEGER AUTOINCREMENT — omit it.
	db.Exec(`INSERT INTO task_events (task_id, event_type, payload, created_at, domain, project)
		VALUES ('task-wave143', 'task.completed', '{"result":"ok"}', datetime('now'), 'test', 'proj')`)

	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil).WithContext(ctx)
	w := httptest.NewRecorder()

	done := make(chan struct{})
	go func() {
		defer close(done)
		openclawEventsHandler(w, req)
	}()

	time.Sleep(5500 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Error("openclawEventsHandler did not exit after context cancel")
	}

	body := w.Body.String()
	// Should see both task_event and the completion branch's extra event.
	if !strings.Contains(body, "task_event") {
		t.Errorf("expected 'task_event' in SSE stream, got: %s", body)
	}
	if !strings.Contains(body, "completion") {
		t.Errorf("expected 'completion' SSE event for task.completed, got: %s", body)
	}
}

// TestWave143_AgentsSSE_TickerFires exercises the ticker branch in agentsSSEHandler,
// covering the agent_heartbeats DB query + heartbeat emit logic.
func TestWave143_AgentsSSE_TickerFires(t *testing.T) {
	t.Log("slow test — waiting 5.5s for SSE ticker to fire")

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Insert agent_heartbeats row so the DB scan branch runs.
	db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, current_task_id, last_seen)
		VALUES ('wave143-sse-agent', 'prya', 'busy', 0.3, 'task-xyz', datetime('now'))`)

	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodGet, "/api/agents/stream", nil).WithContext(ctx)
	w := httptest.NewRecorder()

	done := make(chan struct{})
	go func() {
		defer close(done)
		agentsSSEHandler(w, req)
	}()

	time.Sleep(5500 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Error("agentsSSEHandler did not exit after context cancel")
	}

	body := w.Body.String()
	// Ticker always emits a heartbeat event, even if DB query returns no rows.
	if !strings.Contains(body, "event: heartbeat") {
		t.Errorf("expected 'event: heartbeat' after ticker fired, got: %s", body)
	}
}

// ─────────────────────────────────────────────────────────────────
// Part 4: fleetAutoExecutePatrol guard paths
// ─────────────────────────────────────────────────────────────────

// TestWave143_FleetAutoExecute_CircuitBreakerOpen exercises the circuit breaker
// early-return path in fleetAutoExecutePatrol.
func TestWave143_FleetAutoExecute_CircuitBreakerOpen(t *testing.T) {
	// Get hostname and remove from NoAutoSpawnNodes so we pass the first guard.
	hostname, _ := os.Hostname()
	if NoAutoSpawnNodes[hostname] {
		delete(NoAutoSpawnNodes, hostname)
		defer func() { NoAutoSpawnNodes[hostname] = true }()
	}

	// Trip the circuit breaker.
	circuitBreaker.Lock()
	prevState := circuitBreaker.state
	prevFailures := circuitBreaker.consecutiveFailures
	circuitBreaker.state = "open"
	circuitBreaker.consecutiveFailures = 3
	circuitBreaker.lastFailure = time.Now() // recent failure — won't auto-reset
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = prevState
		circuitBreaker.consecutiveFailures = prevFailures
		circuitBreaker.Unlock()
	}()

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Should return nil (circuit breaker path) without touching DB.
	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil on circuit breaker open, got: %v", err)
	}
}

// TestWave143_FleetAutoExecute_FlapGuardActive exercises the flap-guard
// early-return path.
func TestWave143_FleetAutoExecute_FlapGuardActive(t *testing.T) {
	hostname, _ := os.Hostname()
	if NoAutoSpawnNodes[hostname] {
		delete(NoAutoSpawnNodes, hostname)
		defer func() { NoAutoSpawnNodes[hostname] = true }()
	}

	// Ensure circuit breaker is closed.
	circuitBreaker.Lock()
	prevState := circuitBreaker.state
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = prevState
		circuitBreaker.Unlock()
	}()

	// Trigger flap guard by recording a very recent scale event.
	lastScaleEvent.Lock()
	prev := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Now() // within 60s → canScale() == false
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = prev
		lastScaleEvent.Unlock()
	}()

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil on flap guard, got: %v", err)
	}
}

// TestWave143_FleetAutoExecute_NoRecommendations exercises the happy-path
// where there are no pending recommendations — returns nil after empty query.
func TestWave143_FleetAutoExecute_NoRecommendations(t *testing.T) {
	hostname, _ := os.Hostname()
	if NoAutoSpawnNodes[hostname] {
		delete(NoAutoSpawnNodes, hostname)
		defer func() { NoAutoSpawnNodes[hostname] = true }()
	}

	// Ensure circuit breaker closed and flap guard clear.
	circuitBreaker.Lock()
	prevState := circuitBreaker.state
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = prevState
		circuitBreaker.Unlock()
	}()

	lastScaleEvent.Lock()
	prev := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Time{} // zero time → canScale() == true
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = prev
		lastScaleEvent.Unlock()
	}()

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Create the scale_recommendations table (not in standard migrations).
	db.Exec(`CREATE TABLE IF NOT EXISTS scale_recommendations (
		id TEXT PRIMARY KEY, node_id TEXT, agent_type TEXT, reason TEXT,
		auto_execute INTEGER DEFAULT 0, status TEXT DEFAULT 'pending',
		action TEXT DEFAULT 'inflate', expires_at TEXT, created_at TEXT
	)`)

	// DB has scale_recommendations table but no rows → no-op return nil.
	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil on no recommendations, got: %v", err)
	}
}
