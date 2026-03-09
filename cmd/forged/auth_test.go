// cmd/forged/auth_test.go
package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// TestAuth_LocalMode tests local mode authentication
func TestAuth_LocalMode(t *testing.T) {
	auth := NewAuthManager("local")

	// Local mode should allow requests
	if !auth.ValidateLocal(auth.config.LocalUID) {
		t.Error("expected local UID to be valid")
	}
}

// TestAuth_GenerateToken tests token generation
func TestAuth_GenerateToken(t *testing.T) {
	auth := NewAuthManager("api")

	token, err := auth.GenerateToken("test token", []string{"read", "write"})
	if err != nil {
		t.Fatalf("failed to generate token: %v", err)
	}

	if token == "" {
		t.Error("token should not be empty")
	}

	if len(token) != 64 { // 32 bytes = 64 hex chars
		t.Errorf("expected token length 64, got %d", len(token))
	}
}

// TestAuth_ValidateToken tests token validation
func TestAuth_ValidateToken(t *testing.T) {
	auth := NewAuthManager("api")

	token, _ := auth.GenerateToken("test", []string{"read"})

	// Valid token should pass
	err := auth.ValidateToken(token, "")
	if err != nil {
		t.Errorf("expected token to be valid: %v", err)
	}
}

// TestAuth_InvalidToken tests invalid token rejection
func TestAuth_InvalidToken(t *testing.T) {
	auth := NewAuthManager("api")

	err := auth.ValidateToken("nonexistent-token", "")
	if err == nil {
		t.Error("expected error for invalid token")
	}
}

// TestAuth_TokenRevocation tests token revocation
func TestAuth_TokenRevocation(t *testing.T) {
	auth := NewAuthManager("api")

	token, _ := auth.GenerateToken("test", []string{"read"})

	// Revoke token
	err := auth.RevokeToken(token)
	if err != nil {
		t.Fatalf("failed to revoke token: %v", err)
	}

	// Revoked token should fail
	err = auth.ValidateToken(token, "")
	if err == nil {
		t.Error("expected error for revoked token")
	}
}

// TestAuth_ExpiredToken tests expired token rejection
func TestAuth_ExpiredToken(t *testing.T) {
	auth := NewAuthManager("api")

	token, _ := auth.GenerateToken("test", []string{"read"})

	// Set token to expired
	past := time.Now().Add(-1 * time.Hour)
	auth.SetTokenExpiry(token, past)

	// Expired token should fail
	err := auth.ValidateToken(token, "")
	if err == nil {
		t.Error("expected error for expired token")
	}
}

// TestAuth_ScopeAuthorization tests scope-based authorization
func TestAuth_ScopeAuthorization(t *testing.T) {
	auth := NewAuthManager("api")

	token, _ := auth.GenerateToken("test", []string{"read"})

	// Token with "read" scope should pass "read"
	err := auth.ValidateToken(token, "read")
	if err != nil {
		t.Errorf("expected token with read scope to pass read check: %v", err)
	}

	// Token with "read" scope should fail "write"
	err = auth.ValidateToken(token, "write")
	if err == nil {
		t.Error("expected error for insufficient scope")
	}
}

// TestAuth_HasScope tests HasScope method
func TestAuth_HasScope(t *testing.T) {
	auth := NewAuthManager("api")

	token, _ := auth.GenerateToken("test", []string{"read", "write"})

	if !auth.HasScope(token, "read") {
		t.Error("expected token to have read scope")
	}

	if !auth.HasScope(token, "write") {
		t.Error("expected token to have write scope")
	}

	if auth.HasScope(token, "admin") {
		t.Error("expected token to not have admin scope")
	}
}

// TestAuth_ListTokens tests token listing
func TestAuth_ListTokens(t *testing.T) {
	auth := NewAuthManager("api")

	auth.GenerateToken("token1", []string{"read"})
	auth.GenerateToken("token2", []string{"write"})

	tokens := auth.ListTokens()
	if len(tokens) != 2 {
		t.Errorf("expected 2 tokens, got %d", len(tokens))
	}

	// Verify tokens are masked
	for _, info := range tokens {
		if info.Token == "token1" || info.Token == "token2" {
			t.Error("token should be masked")
		}
	}
}

// TestAuth_APIModeMiddleware tests API mode middleware
func TestAuth_APIModeMiddleware(t *testing.T) {
	auth := NewAuthManager("api")

	token, _ := auth.GenerateToken("test", []string{"read"})

	handler := AuthMiddleware(auth)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	// Test without token
	req := httptest.NewRequest("GET", "/test", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("expected 401 without token, got %d", w.Code)
	}

	// Test with valid token
	req = httptest.NewRequest("GET", "/test", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w = httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with token, got %d", w.Code)
	}

	// Test with invalid token
	req = httptest.NewRequest("GET", "/test", nil)
	req.Header.Set("Authorization", "Bearer invalid-token")
	w = httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("expected 401 with invalid token, got %d", w.Code)
	}
}

// TestAuth_LocalModeMiddleware tests local mode middleware
func TestAuth_LocalModeMiddleware(t *testing.T) {
	auth := NewAuthManager("local")

	handler := AuthMiddleware(auth)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	// Local mode should allow requests without token
	req := httptest.NewRequest("GET", "/test", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 in local mode, got %d", w.Code)
	}
}

// TestAuth_HealthSkip tests that health endpoint skips auth
func TestAuth_HealthSkip(t *testing.T) {
	auth := NewAuthManager("api")

	handler := AuthMiddleware(auth)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	// Health endpoint should work without token
	req := httptest.NewRequest("GET", "/health", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for health, got %d", w.Code)
	}
}

// TestContextAuthManager tests with context (for http server integration)
func TestContextAuthManager(t *testing.T) {
	_ = context.Background() // ctx available for future use
	auth := NewAuthManager("api")

	token, err := auth.GenerateToken("context-test", []string{"read"})
	if err != nil {
		t.Fatalf("failed to generate token: %v", err)
	}

	// Validate in context
	err = auth.ValidateToken(token, "read")
	if err != nil {
		t.Errorf("token should be valid in context: %v", err)
	}
}

func TestAuthMiddleware_LocalMode_PassesAll(t *testing.T) {
	auth := NewAuthManager("local")
	handler := AuthMiddleware(auth)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("local mode: expected 200, got %d", rr.Code)
	}
}

func TestAuthMiddleware_ApiMode_RejectsNoToken(t *testing.T) {
	auth := NewAuthManager("api")
	handler := AuthMiddleware(auth)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusUnauthorized {
		t.Errorf("api mode no token: expected 401, got %d", rr.Code)
	}
}

func TestAuthMiddleware_ApiMode_AcceptsValidToken(t *testing.T) {
	auth := NewAuthManager("api")
	token, _ := auth.GenerateToken("test", []string{"*"})
	handler := AuthMiddleware(auth)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("api mode valid token: expected 200, got %d", rr.Code)
	}
}

func TestAuthMiddleware_SkipsHealth(t *testing.T) {
	auth := NewAuthManager("api")
	handler := AuthMiddleware(auth)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("health should skip auth: expected 200, got %d", rr.Code)
	}
}
