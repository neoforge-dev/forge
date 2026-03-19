//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 194: handlers_worker.go + handlers_relay.go
// Targets:
//   workersHandler        (handlers_worker.go:14)  — POST→405, GET with hub
//   workerByIDHandler     (handlers_worker.go:43)  — POST→405, empty ID, not found
//   relayDeliveriesHandler(handlers_relay.go:41)   — POST→405, nil DB→503, GET with DB
//   relayDispatchHandler  (handlers_relay.go:80)   — GET→405, nil DB→503, invalid body, missing fields, success
//   relayAckHandler       (handlers_relay.go:125)  — GET→405, nil DB→503, not found
//   relayPrefixHandler    (handlers_relay.go:176)  — non-ack path→404, ack delegates
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// workersHandler
// ---------------------------------------------------------------------------

func TestWave194_WorkersHandler_Post405(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/workers", nil)
	w := httptest.NewRecorder()
	workersHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave194_WorkersHandler_Get(t *testing.T) {
	oldHub := hub
	hub = NewHub()
	defer func() { hub = oldHub }()

	req := httptest.NewRequest(http.MethodGet, "/api/workers", nil)
	w := httptest.NewRecorder()
	workersHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// workerByIDHandler
// ---------------------------------------------------------------------------

func TestWave194_WorkerByIDHandler_Post405(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/workers/w-1", nil)
	w := httptest.NewRecorder()
	workerByIDHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave194_WorkerByIDHandler_EmptyID(t *testing.T) {
	oldHub := hub
	hub = NewHub()
	defer func() { hub = oldHub }()

	req := httptest.NewRequest(http.MethodGet, "/api/workers/", nil)
	w := httptest.NewRecorder()
	workerByIDHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty ID, got %d", w.Code)
	}
}

func TestWave194_WorkerByIDHandler_NotFound(t *testing.T) {
	oldHub := hub
	hub = NewHub()
	defer func() { hub = oldHub }()

	req := httptest.NewRequest(http.MethodGet, "/api/workers/nonexistent-worker", nil)
	w := httptest.NewRecorder()
	workerByIDHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// relayDeliveriesHandler
// ---------------------------------------------------------------------------

func TestWave194_RelayDeliveriesHandler_Post405(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/relay/deliveries", nil)
	w := httptest.NewRecorder()
	relayDeliveriesHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave194_RelayDeliveriesHandler_NilDB503(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/relay/deliveries", nil)
	w := httptest.NewRecorder()
	relayDeliveriesHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave194_RelayDeliveriesHandler_GetWithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/relay/deliveries", nil)
	w := httptest.NewRecorder()
	relayDeliveriesHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// relayDispatchHandler
// ---------------------------------------------------------------------------

func TestWave194_RelayDispatchHandler_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/relay/dispatch", nil)
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave194_RelayDispatchHandler_NilDB503(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodPost, "/api/relay/dispatch",
		strings.NewReader(`{"agent_id":"kimi-1","message":"hello"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave194_RelayDispatchHandler_InvalidBody(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/relay/dispatch",
		strings.NewReader("not-json"))
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid body, got %d", w.Code)
	}
}

func TestWave194_RelayDispatchHandler_MissingFields(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/relay/dispatch",
		strings.NewReader(`{"agent_id":"kimi-1"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing fields, got %d", w.Code)
	}
}

func TestWave194_RelayDispatchHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/relay/dispatch",
		strings.NewReader(`{"agent_id":"kimi-1","message":"wave194 dispatch"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)
	if w.Code != http.StatusCreated {
		t.Errorf("expected 201, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// relayAckHandler
// ---------------------------------------------------------------------------

func TestWave194_RelayAckHandler_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/relay/some-id/ack", nil)
	w := httptest.NewRecorder()
	relayAckHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave194_RelayAckHandler_NilDB503(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodPost, "/api/relay/some-id/ack", nil)
	w := httptest.NewRecorder()
	relayAckHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave194_RelayAckHandler_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/relay/nonexistent-id/ack", nil)
	w := httptest.NewRecorder()
	relayAckHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// relayPrefixHandler
// ---------------------------------------------------------------------------

func TestWave194_RelayPrefixHandler_NonAckPath(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/relay/something-else", nil)
	w := httptest.NewRecorder()
	relayPrefixHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestWave194_RelayPrefixHandler_AckDelegates(t *testing.T) {
	setDBConn(nil) // nil DB → 503 from relayAckHandler
	req := httptest.NewRequest(http.MethodPost, "/api/relay/some-id/ack", nil)
	w := httptest.NewRecorder()
	relayPrefixHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 (delegated to relayAckHandler with nil db), got %d", w.Code)
	}
}
