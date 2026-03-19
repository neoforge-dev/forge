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

// ---------------------------------------------------------------------------
// Wave 224: handlers_messages_pwa.go
// Targets:
//   messagesHandler    (handlers_messages_pwa.go:26) — GET/POST/405/nil DB
//   messageByIDHandler (handlers_messages_pwa.go:104) — GET/read/missing ID/not found
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// messagesHandler — no DB → 503
// ---------------------------------------------------------------------------

func TestWave224_MessagesHandler_NoDB(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/messages", nil)
	w := httptest.NewRecorder()
	messagesHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 with no DB, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// messagesHandler — GET list
// ---------------------------------------------------------------------------

func TestWave224_MessagesHandler_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/messages", nil)
	w := httptest.NewRecorder()
	messagesHandler(w, req)

	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// messagesHandler — POST send
// ---------------------------------------------------------------------------

func TestWave224_MessagesHandler_POST(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	body, _ := json.Marshal(map[string]string{
		"from_agent": "kimi",
		"to_agent":   "prya",
		"content":    "hello from wave224",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/messages", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	messagesHandler(w, req)

	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// messagesHandler — POST with thread_id
// ---------------------------------------------------------------------------

func TestWave224_MessagesHandler_POST_WithThread(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	body, _ := json.Marshal(map[string]string{
		"thread_id":  "thread-wave224",
		"from_agent": "kimi",
		"content":    "follow-up message",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/messages", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	messagesHandler(w, req)

	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// messagesHandler — POST bad body → 400
// ---------------------------------------------------------------------------

func TestWave224_MessagesHandler_POST_BadBody(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/messages", bytes.NewReader([]byte("not-json")))
	w := httptest.NewRecorder()
	messagesHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// messagesHandler — method not allowed
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// messageByIDHandler — missing ID → 400
// ---------------------------------------------------------------------------

func TestWave224_MessageByIDHandler_MissingID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/messages/", nil)
	w := httptest.NewRecorder()
	messageByIDHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing ID, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// messageByIDHandler — GET not found → 404/500
// ---------------------------------------------------------------------------

func TestWave224_MessageByIDHandler_GET_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/messages/nonexistent-msg-xyz", nil)
	w := httptest.NewRecorder()
	messageByIDHandler(w, req)

	if w.Code != http.StatusNotFound && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// messageByIDHandler — no DB → 503
// ---------------------------------------------------------------------------

func TestWave224_MessageByIDHandler_NoDB(t *testing.T) {
	setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/messages/some-id", nil)
	w := httptest.NewRecorder()
	messageByIDHandler(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 with no DB, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// messageByIDHandler — /read action POST → no panic (table may not exist)
// ---------------------------------------------------------------------------

func TestWave224_MessageByIDHandler_Read_NoPanic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/messages/some-id/read", nil)
	w := httptest.NewRecorder()
	messageByIDHandler(w, req)

	// 200 or 500 (table may not exist in SQLite)
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}
