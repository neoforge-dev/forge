//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

// --- handleAuthLogin tests ---

func TestAuthLogin_MissingBody(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Set webhook token for auth
	os.Setenv("FORGE_WEBHOOK_TOKEN", "test-token-123")
	defer os.Unsetenv("FORGE_WEBHOOK_TOKEN")

	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", nil)
	w := httptest.NewRecorder()
	handleAuthLogin(authManager)(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAuthLogin_InvalidJSON(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	os.Setenv("FORGE_WEBHOOK_TOKEN", "test-token-123")
	defer os.Unsetenv("FORGE_WEBHOOK_TOKEN")

	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader([]byte("not json")))
	w := httptest.NewRecorder()
	handleAuthLogin(authManager)(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAuthLogin_NoTokenConfigured(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Ensure no token is set
	os.Unsetenv("FORGE_WEBHOOK_TOKEN")

	body, _ := json.Marshal(map[string]string{"token": "any-token"})
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleAuthLogin(authManager)(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAuthLogin_InvalidCredentials(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	os.Setenv("FORGE_WEBHOOK_TOKEN", "correct-token")
	defer os.Unsetenv("FORGE_WEBHOOK_TOKEN")

	body, _ := json.Marshal(map[string]string{"token": "wrong-token"})
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleAuthLogin(authManager)(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("expected 401, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAuthLogin_ValidCredentials(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	os.Setenv("FORGE_WEBHOOK_TOKEN", "correct-token")
	defer os.Unsetenv("FORGE_WEBHOOK_TOKEN")

	body, _ := json.Marshal(map[string]string{"token": "correct-token"})
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleAuthLogin(authManager)(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parse response: %v", err)
	}
	if resp["token"] == nil || resp["token"] == "" {
		t.Errorf("expected token in response, got %v", resp)
	}
	if resp["token_type"] != "bearer" {
		t.Errorf("expected token_type bearer, got %v", resp["token_type"])
	}
}

func TestAuthLogin_WrongMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/auth/login", nil)
	w := httptest.NewRecorder()
	handleAuthLogin(authManager)(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- handleAuthMe tests ---

func TestAuthMe_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/auth/me", nil)
	w := httptest.NewRecorder()
	handleAuthMe(authManager)(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parse response: %v", err)
	}
	if resp["auth_mode"] != "local" {
		t.Errorf("expected auth_mode local, got %v", resp["auth_mode"])
	}
	if resp["version"] != "v3.0" {
		t.Errorf("expected version v3.0, got %v", resp["version"])
	}
}

func TestAuthMe_WrongMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/auth/me", nil)
	w := httptest.NewRecorder()
	handleAuthMe(authManager)(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- handleAuthRefresh tests ---

func TestAuthRefresh_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/auth/refresh", nil)
	w := httptest.NewRecorder()
	handleAuthRefresh(authManager)(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parse response: %v", err)
	}
	if resp["token"] == nil || resp["token"] == "" {
		t.Errorf("expected token in response, got %v", resp)
	}
	if resp["token_type"] != "bearer" {
		t.Errorf("expected token_type bearer, got %v", resp["token_type"])
	}
	if resp["expires_in"].(float64) != 86400 {
		t.Errorf("expected expires_in 86400, got %v", resp["expires_in"])
	}
}

func TestAuthRefresh_WrongMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/auth/refresh", nil)
	w := httptest.NewRecorder()
	handleAuthRefresh(authManager)(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}
