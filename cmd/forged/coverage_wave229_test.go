//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// ============================================================
// coverage_wave229_test.go — AuthManager methods + AuthMiddleware
// Target: auth.go (NewAuthManager, GenerateToken, RevokeToken, ListTokens,
//         ValidateToken, ValidateLocal, GetTokenInfo, HasScope,
//         SetTokenExpiry, AuthMiddleware)
// ============================================================

func TestWave229_NewAuthManager(t *testing.T) {
	am := NewAuthManager("local")
	if am == nil {
		t.Fatal("NewAuthManager returned nil")
	}
	if am.config.Mode != "local" {
		t.Errorf("expected mode=local, got %s", am.config.Mode)
	}
}

func TestWave229_GenerateToken_Success(t *testing.T) {
	am := NewAuthManager("api")
	tok, err := am.GenerateToken("test token", []string{"read", "write"})
	if err != nil {
		t.Fatalf("GenerateToken: %v", err)
	}
	if len(tok) == 0 {
		t.Error("expected non-empty token")
	}
	if len(tok) != 64 { // 32 bytes hex-encoded
		t.Errorf("expected 64-char token, got len=%d", len(tok))
	}
}

func TestWave229_RevokeToken_Success(t *testing.T) {
	am := NewAuthManager("api")
	tok, _ := am.GenerateToken("test", nil)
	if err := am.RevokeToken(tok); err != nil {
		t.Errorf("RevokeToken: %v", err)
	}
}

func TestWave229_RevokeToken_NotFound(t *testing.T) {
	am := NewAuthManager("api")
	err := am.RevokeToken("nonexistent-token-xyz")
	if err == nil {
		t.Error("expected error for nonexistent token")
	}
}

func TestWave229_ListTokens_Empty(t *testing.T) {
	am := NewAuthManager("api")
	tokens := am.ListTokens()
	if len(tokens) != 0 {
		t.Errorf("expected 0 tokens, got %d", len(tokens))
	}
}

func TestWave229_ListTokens_WithTokens(t *testing.T) {
	am := NewAuthManager("api")
	am.GenerateToken("token A", []string{"read"})
	am.GenerateToken("token B", []string{"write"})
	tokens := am.ListTokens()
	if len(tokens) != 2 {
		t.Errorf("expected 2 tokens, got %d", len(tokens))
	}
	// Verify masking
	for _, ti := range tokens {
		if !strings.HasPrefix(ti.Token, "****") {
			t.Errorf("expected masked token starting with ****, got %s", ti.Token)
		}
	}
}

func TestWave229_ValidateToken_ValidNoScope(t *testing.T) {
	am := NewAuthManager("api")
	tok, _ := am.GenerateToken("test", []string{"read"})
	if err := am.ValidateToken(tok, ""); err != nil {
		t.Errorf("ValidateToken (no scope): %v", err)
	}
}

func TestWave229_ValidateToken_ValidWithScope(t *testing.T) {
	am := NewAuthManager("api")
	tok, _ := am.GenerateToken("test", []string{"read", "write"})
	if err := am.ValidateToken(tok, "write"); err != nil {
		t.Errorf("ValidateToken (with scope): %v", err)
	}
}

func TestWave229_ValidateToken_WildcardScope(t *testing.T) {
	am := NewAuthManager("api")
	tok, _ := am.GenerateToken("admin", []string{"*"})
	if err := am.ValidateToken(tok, "anything"); err != nil {
		t.Errorf("ValidateToken (wildcard): %v", err)
	}
}

func TestWave229_ValidateToken_InvalidToken(t *testing.T) {
	am := NewAuthManager("api")
	if err := am.ValidateToken("bad-token", ""); err == nil {
		t.Error("expected error for invalid token")
	}
}

func TestWave229_ValidateToken_InsufficientScope(t *testing.T) {
	am := NewAuthManager("api")
	tok, _ := am.GenerateToken("limited", []string{"read"})
	if err := am.ValidateToken(tok, "write"); err == nil {
		t.Error("expected scope error")
	}
}

func TestWave229_ValidateToken_Expired(t *testing.T) {
	am := NewAuthManager("api")
	tok, _ := am.GenerateToken("exp", []string{"read"})
	past := time.Now().Add(-1 * time.Hour)
	am.SetTokenExpiry(tok, past)
	if err := am.ValidateToken(tok, ""); err == nil {
		t.Error("expected error for expired token")
	}
}

func TestWave229_ValidateLocal(t *testing.T) {
	am := NewAuthManager("local")
	// Local UID should match current process
	if !am.ValidateLocal(am.config.LocalUID) {
		t.Error("expected ValidateLocal=true for own UID")
	}
	if am.ValidateLocal(am.config.LocalUID+1) {
		t.Error("expected ValidateLocal=false for different UID")
	}
}

func TestWave229_GetTokenInfo_Found(t *testing.T) {
	am := NewAuthManager("api")
	tok, _ := am.GenerateToken("info test", []string{"read"})
	info, err := am.GetTokenInfo(tok)
	if err != nil {
		t.Fatalf("GetTokenInfo: %v", err)
	}
	if info.Description != "info test" {
		t.Errorf("expected description 'info test', got %s", info.Description)
	}
}

func TestWave229_GetTokenInfo_NotFound(t *testing.T) {
	am := NewAuthManager("api")
	_, err := am.GetTokenInfo("nonexistent")
	if err == nil {
		t.Error("expected error for nonexistent token")
	}
}

func TestWave229_HasScope_True(t *testing.T) {
	am := NewAuthManager("api")
	tok, _ := am.GenerateToken("t", []string{"deploy"})
	if !am.HasScope(tok, "deploy") {
		t.Error("expected HasScope=true")
	}
}

func TestWave229_HasScope_Wildcard(t *testing.T) {
	am := NewAuthManager("api")
	tok, _ := am.GenerateToken("t", []string{"*"})
	if !am.HasScope(tok, "anything") {
		t.Error("expected HasScope=true for wildcard")
	}
}

func TestWave229_HasScope_False(t *testing.T) {
	am := NewAuthManager("api")
	tok, _ := am.GenerateToken("t", []string{"read"})
	if am.HasScope(tok, "write") {
		t.Error("expected HasScope=false")
	}
}

func TestWave229_HasScope_NoToken(t *testing.T) {
	am := NewAuthManager("api")
	if am.HasScope("missing", "read") {
		t.Error("expected HasScope=false for missing token")
	}
}

func TestWave229_SetTokenExpiry_Success(t *testing.T) {
	am := NewAuthManager("api")
	tok, _ := am.GenerateToken("t", nil)
	future := time.Now().Add(24 * time.Hour)
	if err := am.SetTokenExpiry(tok, future); err != nil {
		t.Fatalf("SetTokenExpiry: %v", err)
	}
	// Should still be valid
	if err := am.ValidateToken(tok, ""); err != nil {
		t.Errorf("expected valid token after setting future expiry: %v", err)
	}
}

func TestWave229_SetTokenExpiry_NotFound(t *testing.T) {
	am := NewAuthManager("api")
	if err := am.SetTokenExpiry("missing", time.Now()); err == nil {
		t.Error("expected error for missing token")
	}
}

// --- AuthMiddleware ---

func TestWave229_AuthMiddleware_HealthSkipped(t *testing.T) {
	am := NewAuthManager("api")
	handler := AuthMiddleware(am)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for /health, got %d", w.Code)
	}
}

func TestWave229_AuthMiddleware_LocalModeSkipped(t *testing.T) {
	am := NewAuthManager("local")
	handler := AuthMiddleware(am)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 in local mode, got %d", w.Code)
	}
}

func TestWave229_AuthMiddleware_NoHeader(t *testing.T) {
	am := NewAuthManager("api")
	handler := AuthMiddleware(am)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("expected 401 for missing auth header, got %d", w.Code)
	}
}

func TestWave229_AuthMiddleware_InvalidFormat(t *testing.T) {
	am := NewAuthManager("api")
	handler := AuthMiddleware(am)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	req.Header.Set("Authorization", "Basic dXNlcjpwYXNz")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("expected 401 for Basic auth, got %d", w.Code)
	}
}

func TestWave229_AuthMiddleware_InvalidToken(t *testing.T) {
	am := NewAuthManager("api")
	handler := AuthMiddleware(am)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	req.Header.Set("Authorization", "Bearer bad-token-xyz")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("expected 401 for invalid token, got %d", w.Code)
	}
}

func TestWave229_AuthMiddleware_ValidToken(t *testing.T) {
	am := NewAuthManager("api")
	tok, _ := am.GenerateToken("test", []string{"read"})
	handler := AuthMiddleware(am)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	req.Header.Set("Authorization", "Bearer "+tok)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for valid token, got %d", w.Code)
	}
}

func TestWave229_AuthMiddleware_WebhookSkipped(t *testing.T) {
	am := NewAuthManager("api")
	handler := AuthMiddleware(am)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodPost, "/api/github/webhook", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for webhook path, got %d", w.Code)
	}
}
