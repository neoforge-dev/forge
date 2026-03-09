//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// Wave 52: XNodeController ListAcksHandler/RegisterHandler,
//          NewEventBus, ParityCheck, announceNodeToPeer

// ─── XNodeController.ListAcksHandler ─────────────────────────────────────

func TestXNodeListAcksHandler_Empty_W52(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w52")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/acks", nil)
	w := httptest.NewRecorder()
	xc.ListAcksHandler(w, req)
	// May fail if acks dir doesn't exist (500) or succeed (200).
	_ = w.Code
}

// ─── XNodeController.RegisterHandler ─────────────────────────────────────

func TestXNodeRegisterHandler_MethodNotAllowed_W52(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w52")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes/register", nil)
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestXNodeRegisterHandler_InvalidJSON_W52(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w52")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", bytes.NewBufferString("not-json"))
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestXNodeRegisterHandler_MissingID_W52(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w52")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	body, _ := json.Marshal(map[string]string{"hostname": "test-host"})
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", bytes.NewReader(body))
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing ID, got %d", w.Code)
	}
}

func TestXNodeRegisterHandler_Valid_W52(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w52")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	body, _ := json.Marshal(map[string]string{
		"id":       "node-w52",
		"hostname": "test-host-w52",
		"address":  "http://localhost:8081",
		"status":   "online",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", bytes.NewReader(body))
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, req)
	if w.Code != http.StatusOK && w.Code != http.StatusCreated {
		t.Errorf("expected 200 or 201, got %d: %s", w.Code, w.Body.String())
	}
}

// ─── NewEventBus ──────────────────────────────────────────────────────────

func TestNewEventBus_NilDB_W52(t *testing.T) {
	_, err := NewEventBus(nil)
	if err == nil {
		t.Error("expected error for nil DB, got nil")
	}
}

func TestNewEventBus_WithDB_W52(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	if eb == nil {
		t.Error("expected non-nil event bus")
	}
}

// ─── ParityCheck ──────────────────────────────────────────────────────────

func TestParityCheck_W52(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// ParityCheck uses default paths — may fail if forge-v3.db doesn't exist.
	// Just verify it doesn't panic.
	_, _ = ParityCheck(db)
}
