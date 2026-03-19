//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 186: auth.go — AuthManager methods + middleware
// Targets:
//   NewAuthManager      (auth.go:38)
//   GenerateToken       (auth.go:49)
//   RevokeToken         (auth.go:71) — found / not found
//   ListTokens          (auth.go:84) — empty / with tokens
//   ValidateToken       (auth.go:102)— invalid / expired / no scope / valid
//   ValidateLocal       (auth.go:134)
//   GetTokenInfo        (auth.go:139)— found / not found
//   HasScope            (auth.go:194)— no token / exact / wildcard / not found
//   SetTokenExpiry      (auth.go:212)— not found / success
//   AuthMiddleware      (auth.go:151)— health path / local mode / api mode
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewAuthManager
// ---------------------------------------------------------------------------

func TestWave186_NewAuthManager_Local(t *testing.T) {
	am := NewAuthManager("local")
	if am == nil {
		t.Error("expected non-nil AuthManager")
	}
	if am.config.Mode != "local" {
		t.Errorf("expected mode 'local', got %q", am.config.Mode)
	}
}

func TestWave186_NewAuthManager_API(t *testing.T) {
	am := NewAuthManager("api")
	if am.config.Mode != "api" {
		t.Errorf("expected mode 'api', got %q", am.config.Mode)
	}
}

// ---------------------------------------------------------------------------
// GenerateToken
// ---------------------------------------------------------------------------

func TestWave186_GenerateToken_Returns64CharHex(t *testing.T) {
	am := NewAuthManager("api")
	token, err := am.GenerateToken("test token", []string{"read"})
	if err != nil {
		t.Fatalf("GenerateToken: %v", err)
	}
	if len(token) != 64 {
		t.Errorf("expected 64-char hex token, got len %d", len(token))
	}
}

func TestWave186_GenerateToken_StoresToken(t *testing.T) {
	am := NewAuthManager("api")
	token, _ := am.GenerateToken("test", []string{"read"})

	// Token should be in the map
	if _, err := am.GetTokenInfo(token); err != nil {
		t.Errorf("expected token to be stored: %v", err)
	}
}

// ---------------------------------------------------------------------------
// RevokeToken
// ---------------------------------------------------------------------------

func TestWave186_RevokeToken_Success(t *testing.T) {
	am := NewAuthManager("api")
	token, _ := am.GenerateToken("revoke test", nil)
	if err := am.RevokeToken(token); err != nil {
		t.Errorf("RevokeToken: %v", err)
	}
}

func TestWave186_RevokeToken_NotFound(t *testing.T) {
	am := NewAuthManager("api")
	if err := am.RevokeToken("nonexistent-token"); err == nil {
		t.Error("expected error for nonexistent token")
	}
}

// ---------------------------------------------------------------------------
// ListTokens
// ---------------------------------------------------------------------------

func TestWave186_ListTokens_Empty(t *testing.T) {
	am := NewAuthManager("api")
	tokens := am.ListTokens()
	if len(tokens) != 0 {
		t.Errorf("expected empty list, got %d", len(tokens))
	}
}

func TestWave186_ListTokens_WithTokens(t *testing.T) {
	am := NewAuthManager("api")
	am.GenerateToken("token1", []string{"read"})
	am.GenerateToken("token2", []string{"write"})
	tokens := am.ListTokens()
	if len(tokens) != 2 {
		t.Errorf("expected 2 tokens, got %d", len(tokens))
	}
	// Token values should be masked (****XXXX)
	for _, ti := range tokens {
		if len(ti.Token) == 64 {
			t.Error("expected masked token in ListTokens output")
		}
	}
}

// ---------------------------------------------------------------------------
// ValidateToken
// ---------------------------------------------------------------------------

func TestWave186_ValidateToken_InvalidToken(t *testing.T) {
	am := NewAuthManager("api")
	if err := am.ValidateToken("bad-token", ""); err == nil {
		t.Error("expected error for invalid token")
	}
}

func TestWave186_ValidateToken_ExpiredToken(t *testing.T) {
	am := NewAuthManager("api")
	token, _ := am.GenerateToken("expiry test", nil)
	// Set expiry in the past
	past := time.Now().Add(-1 * time.Hour)
	am.SetTokenExpiry(token, past)

	if err := am.ValidateToken(token, ""); err == nil {
		t.Error("expected error for expired token")
	}
}

func TestWave186_ValidateToken_InsufficientScope(t *testing.T) {
	am := NewAuthManager("api")
	token, _ := am.GenerateToken("scope test", []string{"read"})
	if err := am.ValidateToken(token, "write"); err == nil {
		t.Error("expected error for insufficient scope")
	}
}

func TestWave186_ValidateToken_ValidWithScope(t *testing.T) {
	am := NewAuthManager("api")
	token, _ := am.GenerateToken("valid", []string{"read", "write"})
	if err := am.ValidateToken(token, "write"); err != nil {
		t.Errorf("expected valid token with scope: %v", err)
	}
}

func TestWave186_ValidateToken_WildcardScope(t *testing.T) {
	am := NewAuthManager("api")
	token, _ := am.GenerateToken("wildcard", []string{"*"})
	if err := am.ValidateToken(token, "anything"); err != nil {
		t.Errorf("expected wildcard scope to pass: %v", err)
	}
}

func TestWave186_ValidateToken_NoScopeRequired(t *testing.T) {
	am := NewAuthManager("api")
	token, _ := am.GenerateToken("no scope", nil)
	if err := am.ValidateToken(token, ""); err != nil {
		t.Errorf("expected success with no scope required: %v", err)
	}
}

// ---------------------------------------------------------------------------
// ValidateLocal
// ---------------------------------------------------------------------------

func TestWave186_ValidateLocal_MatchingUID(t *testing.T) {
	am := NewAuthManager("local")
	// config.LocalUID = os.Getuid()
	if !am.ValidateLocal(am.config.LocalUID) {
		t.Error("expected true for matching UID")
	}
}

func TestWave186_ValidateLocal_NonMatchingUID(t *testing.T) {
	am := NewAuthManager("local")
	if am.ValidateLocal(am.config.LocalUID + 1) {
		t.Error("expected false for non-matching UID")
	}
}

// ---------------------------------------------------------------------------
// GetTokenInfo
// ---------------------------------------------------------------------------

func TestWave186_GetTokenInfo_Found(t *testing.T) {
	am := NewAuthManager("api")
	token, _ := am.GenerateToken("info test", []string{"admin"})
	info, err := am.GetTokenInfo(token)
	if err != nil {
		t.Errorf("GetTokenInfo: %v", err)
	}
	if info.Description != "info test" {
		t.Errorf("expected description 'info test', got %q", info.Description)
	}
}

func TestWave186_GetTokenInfo_NotFound(t *testing.T) {
	am := NewAuthManager("api")
	_, err := am.GetTokenInfo("nonexistent")
	if err == nil {
		t.Error("expected error for nonexistent token")
	}
}

// ---------------------------------------------------------------------------
// HasScope
// ---------------------------------------------------------------------------

func TestWave186_HasScope_NoToken(t *testing.T) {
	am := NewAuthManager("api")
	if am.HasScope("bad-token", "read") {
		t.Error("expected false for nonexistent token")
	}
}

func TestWave186_HasScope_ExactMatch(t *testing.T) {
	am := NewAuthManager("api")
	token, _ := am.GenerateToken("scope", []string{"read"})
	if !am.HasScope(token, "read") {
		t.Error("expected true for exact scope match")
	}
}

func TestWave186_HasScope_WildcardMatch(t *testing.T) {
	am := NewAuthManager("api")
	token, _ := am.GenerateToken("wildcard", []string{"*"})
	if !am.HasScope(token, "anything") {
		t.Error("expected true for wildcard scope")
	}
}

func TestWave186_HasScope_NoMatch(t *testing.T) {
	am := NewAuthManager("api")
	token, _ := am.GenerateToken("no match", []string{"read"})
	if am.HasScope(token, "write") {
		t.Error("expected false for scope not in list")
	}
}

// ---------------------------------------------------------------------------
// SetTokenExpiry
// ---------------------------------------------------------------------------

func TestWave186_SetTokenExpiry_NotFound(t *testing.T) {
	am := NewAuthManager("api")
	if err := am.SetTokenExpiry("bad-token", time.Now().Add(time.Hour)); err == nil {
		t.Error("expected error for nonexistent token")
	}
}

func TestWave186_SetTokenExpiry_Success(t *testing.T) {
	am := NewAuthManager("api")
	token, _ := am.GenerateToken("expiry", nil)
	future := time.Now().Add(24 * time.Hour)
	if err := am.SetTokenExpiry(token, future); err != nil {
		t.Errorf("SetTokenExpiry: %v", err)
	}
}

// ---------------------------------------------------------------------------
// AuthMiddleware
// ---------------------------------------------------------------------------

func TestWave186_AuthMiddleware_HealthPathSkipsAuth(t *testing.T) {
	am := NewAuthManager("api")
	handler := AuthMiddleware(am)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for /health path, got %d", w.Code)
	}
}

func TestWave186_AuthMiddleware_LocalModeAllows(t *testing.T) {
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

func TestWave186_AuthMiddleware_APIModeNoToken(t *testing.T) {
	am := NewAuthManager("api")
	handler := AuthMiddleware(am)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("expected 401 for missing token in API mode, got %d", w.Code)
	}
}

func TestWave186_AuthMiddleware_APIModeInvalidBearerFormat(t *testing.T) {
	am := NewAuthManager("api")
	handler := AuthMiddleware(am)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	req.Header.Set("Authorization", "InvalidFormat")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("expected 401 for invalid auth format, got %d", w.Code)
	}
}

func TestWave186_AuthMiddleware_APIModeValidToken(t *testing.T) {
	am := NewAuthManager("api")
	token, _ := am.GenerateToken("middleware test", nil)

	handler := AuthMiddleware(am)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for valid token, got %d", w.Code)
	}
}

func TestWave186_AuthMiddleware_APIGitHubWebhookSkipsAuth(t *testing.T) {
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
