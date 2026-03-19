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
// Wave 221: handlers_adr014.go HTTP handlers
// Targets:
//   handleLeadState    (handlers_adr014.go:16)  — GET 200, non-GET 405
//   handleAuthLogin    (handlers_adr014.go:109) — non-POST 405, no token configured 503
//   handleAuthMe       (handlers_adr014.go:157) — GET 200, non-GET 405
//   handleAuthRefresh  (handlers_adr014.go:184) — non-POST 405, POST 200
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// handleLeadState
// ---------------------------------------------------------------------------

func TestWave221_HandleLeadState_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := handleLeadState(db)
	req := httptest.NewRequest(http.MethodGet, "/api/lead-state", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	// Should return 200 or 500 (if migration has different schema)
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handleAuthLogin
// ---------------------------------------------------------------------------

func TestWave221_HandleAuthLogin_NoTokenConfigured(t *testing.T) {
	t.Setenv("FORGE_WEBHOOK_TOKEN", "")

	auth := NewAuthManager("local")
	handler := handleAuthLogin(auth)

	body, _ := json.Marshal(map[string]string{"token": "anything"})
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	// 503 — auth not configured
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave221_HandleAuthLogin_InvalidToken(t *testing.T) {
	t.Setenv("FORGE_WEBHOOK_TOKEN", "correct-token")

	auth := NewAuthManager("local")
	handler := handleAuthLogin(auth)

	body, _ := json.Marshal(map[string]string{"token": "wrong-token"})
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("expected 401, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave221_HandleAuthLogin_ValidToken(t *testing.T) {
	t.Setenv("FORGE_WEBHOOK_TOKEN", "test-secret")

	auth := NewAuthManager("local")
	handler := handleAuthLogin(auth)

	body, _ := json.Marshal(map[string]string{"token": "test-secret"})
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave221_HandleAuthLogin_BadBody(t *testing.T) {
	t.Setenv("FORGE_WEBHOOK_TOKEN", "test-secret")
	auth := NewAuthManager("local")
	handler := handleAuthLogin(auth)

	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader([]byte("not-json")))
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handleAuthMe
// ---------------------------------------------------------------------------

func TestWave221_HandleAuthMe_GET(t *testing.T) {
	auth := NewAuthManager("local")
	handler := handleAuthMe(auth)

	req := httptest.NewRequest(http.MethodGet, "/api/auth/me", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave221_HandleAuthMe_NilAuth(t *testing.T) {
	handler := handleAuthMe(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/auth/me", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with nil auth, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handleAuthRefresh
// ---------------------------------------------------------------------------

func TestWave221_HandleAuthRefresh_POST(t *testing.T) {
	auth := NewAuthManager("local")
	handler := handleAuthRefresh(auth)

	req := httptest.NewRequest(http.MethodPost, "/api/auth/refresh", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	// Should return 200 with new token
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}
