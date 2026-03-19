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
// Wave 150: relay handlers + dashHandler
// Targets:
//   relayDeliveriesHandler (handlers_relay.go:41) — POST→405, nil DB→503, GET with DB
//   relayDispatchHandler   (handlers_relay.go:80) — GET→405, nil DB→503, bad JSON→400,
//                            missing fields→400, valid POST→201
//   relayAckHandler        (handlers_relay.go:125) — GET→405, nil DB→503, missing ID→400,
//                            not found→404, valid ack→200
//   dashHandler            (handlers_patrols.go:523) — POST→405, nil DB→503, GET with DB
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// relayDeliveriesHandler — handlers_relay.go:41
// ---------------------------------------------------------------------------

func TestWave150_RelayDeliveriesHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/relay/deliveries", nil)
	w := httptest.NewRecorder()
	relayDeliveriesHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil DB, got %d", w.Code)
	}
}

func TestWave150_RelayDeliveriesHandler_GetWithDB(t *testing.T) {
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
// relayDispatchHandler — handlers_relay.go:80
// ---------------------------------------------------------------------------

func TestWave150_RelayDispatchHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	body := `{"agent_id":"kimi","message":"hello"}`
	req := httptest.NewRequest(http.MethodPost, "/api/relay/dispatch", strings.NewReader(body))
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil DB, got %d", w.Code)
	}
}

func TestWave150_RelayDispatchHandler_BadJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/relay/dispatch", strings.NewReader("{bad"))
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

func TestWave150_RelayDispatchHandler_MissingFields(t *testing.T) {
	// Empty agent_id and message → 400
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	body := `{"agent_id":"","message":""}`
	req := httptest.NewRequest(http.MethodPost, "/api/relay/dispatch", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing fields, got %d", w.Code)
	}
}

func TestWave150_RelayDispatchHandler_ValidPost(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	body := `{"agent_id":"kimi","message":"run task 42"}`
	req := httptest.NewRequest(http.MethodPost, "/api/relay/dispatch", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)
	if w.Code != http.StatusCreated {
		t.Errorf("expected 201, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "kimi") {
		t.Errorf("expected agent_id in response, got: %s", w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// relayAckHandler — handlers_relay.go:125
// ---------------------------------------------------------------------------

func TestWave150_RelayAckHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodPost, "/api/relay/some-id/ack", nil)
	w := httptest.NewRecorder()
	relayAckHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil DB, got %d", w.Code)
	}
}

func TestWave150_RelayAckHandler_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/relay/nonexistent-id/ack", nil)
	w := httptest.NewRecorder()
	relayAckHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for nonexistent delivery, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave150_RelayAckHandler_ValidAck(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// First dispatch a delivery, then ack it
	deliveryID := "test-delivery-001"
	_, err := db.Exec(
		`INSERT INTO relay_deliveries (id, agent_id, message, status, created_at) VALUES (?, ?, ?, 'pending', datetime('now'))`,
		deliveryID, "kimi", "run task 42",
	)
	if err != nil {
		t.Fatalf("failed to insert test delivery: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/api/relay/"+deliveryID+"/ack", nil)
	w := httptest.NewRecorder()
	relayAckHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// dashHandler — handlers_patrols.go:523
// ---------------------------------------------------------------------------

func TestWave150_DashHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil DB, got %d", w.Code)
	}
}

func TestWave150_DashHandler_GetWithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "Patrol Dashboard") {
		t.Errorf("expected dashboard HTML, got: %s", w.Body.String()[:100])
	}
}
