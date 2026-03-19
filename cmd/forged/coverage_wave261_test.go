//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// TestWave261_AuthTokensHandler_Get_Success tests GET /api/auth/tokens
func TestWave261_AuthTokensHandler_Get_Success(t *testing.T) {
	auth := NewAuthManager("local")
	
	// Add a test token
	_, err := auth.GenerateToken("Test Token 1", []string{"read"})
	if err != nil {
		t.Fatalf("failed to generate token: %v", err)
	}
	_, err = auth.GenerateToken("Test Token 2", []string{"read", "write"})
	if err != nil {
		t.Fatalf("failed to generate token: %v", err)
	}
	
	handler := authTokensHandler(auth)
	
	req := httptest.NewRequest(http.MethodGet, "/api/auth/tokens", nil)
	w := httptest.NewRecorder()
	
	handler(w, req)
	
	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", w.Code, w.Body.String())
	}
	
	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	tokens, ok := resp["tokens"].([]interface{})
	if !ok {
		t.Fatal("expected tokens array in response")
	}
	if len(tokens) != 2 {
		t.Errorf("expected 2 tokens, got %d", len(tokens))
	}
}

// TestWave261_AuthTokensHandler_Get_Empty tests GET with no tokens
func TestWave261_AuthTokensHandler_Get_Empty(t *testing.T) {
	auth := NewAuthManager("local")
	
	handler := authTokensHandler(auth)
	
	req := httptest.NewRequest(http.MethodGet, "/api/auth/tokens", nil)
	w := httptest.NewRecorder()
	
	handler(w, req)
	
	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}
	
	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	tokens, ok := resp["tokens"].([]interface{})
	if !ok {
		t.Fatal("expected tokens array in response")
	}
	if len(tokens) != 0 {
		t.Errorf("expected 0 tokens, got %d", len(tokens))
	}
}

// TestWave261_AuthTokensHandler_Post_Success tests POST /api/auth/tokens
func TestWave261_AuthTokensHandler_Post_Success(t *testing.T) {
	auth := NewAuthManager("local")
	
	handler := authTokensHandler(auth)
	
	reqBody := `{"description": "New Test Token", "scopes": ["read", "write"]}`
	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	
	handler(w, req)
	
	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", w.Code, w.Body.String())
	}
	
	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	token, ok := resp["token"].(string)
	if !ok || token == "" {
		t.Error("expected non-empty token in response")
	}
	
	// Verify token was added
	tokens := auth.ListTokens()
	if len(tokens) != 1 {
		t.Errorf("expected 1 token in auth manager, got %d", len(tokens))
	}
}

// TestWave261_AuthTokensHandler_Post_DefaultScopes tests POST with default scopes
func TestWave261_AuthTokensHandler_Post_DefaultScopes(t *testing.T) {
	auth := NewAuthManager("local")
	
	handler := authTokensHandler(auth)
	
	// Request without scopes - should default to ["*"]
	reqBody := `{"description": "Token with default scopes"}`
	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	
	handler(w, req)
	
	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", w.Code, w.Body.String())
	}
	
	// Verify token was created
	tokens := auth.ListTokens()
	if len(tokens) != 1 {
		t.Errorf("expected 1 token, got %d", len(tokens))
	}
}

// TestWave261_AuthTokensHandler_Post_InvalidJSON tests POST with invalid JSON
func TestWave261_AuthTokensHandler_Post_InvalidJSON(t *testing.T) {
	auth := NewAuthManager("local")
	
	handler := authTokensHandler(auth)
	
	reqBody := `{"invalid json`
	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	
	handler(w, req)
	
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", w.Code)
	}
	
	if !strings.Contains(w.Body.String(), "invalid request body") {
		t.Errorf("expected 'invalid request body' error, got: %s", w.Body.String())
	}
}

// TestWave261_AuthTokensHandler_MethodNotAllowed tests unsupported HTTP methods
// TestWave261_HarnessFallbackHandler_NoProxy tests fallback without proxy
func TestWave261_HarnessFallbackHandler_NoProxy(t *testing.T) {
	// Ensure harnessProxy is nil
	harnessProxy = nil
	
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	w := httptest.NewRecorder()
	
	harnessFallbackHandler(w, req)
	
	if w.Code != http.StatusNotFound {
		t.Errorf("expected status 404, got %d", w.Code)
	}
	
	contentType := w.Header().Get("Content-Type")
	if contentType != "application/json" {
		t.Errorf("expected Content-Type 'application/json', got %s", contentType)
	}
	
	body := w.Body.String()
	if !strings.Contains(body, "not_found") {
		t.Errorf("expected 'not_found' in body, got: %s", body)
	}
	if !strings.Contains(body, "FORGE_HARNESS_URL") {
		t.Errorf("expected 'FORGE_HARNESS_URL' hint in body, got: %s", body)
	}
}

// TestWave261_InitHarnessProxy_NoEnv tests init with no environment variable
func TestWave261_InitHarnessProxy_NoEnv(t *testing.T) {
	// Clear the environment variable
	os.Unsetenv("FORGE_HARNESS_URL")
	
	// Reset harnessProxy
	harnessProxy = nil
	
	initHarnessProxy()
	
	if harnessProxy != nil {
		t.Error("expected harnessProxy to be nil when FORGE_HARNESS_URL is not set")
	}
}

// TestWave261_InitHarnessProxy_InvalidURL tests init with invalid URL
func TestWave261_InitHarnessProxy_InvalidURL(t *testing.T) {
	os.Setenv("FORGE_HARNESS_URL", "://invalid-url")
	defer os.Unsetenv("FORGE_HARNESS_URL")
	
	// Reset harnessProxy
	harnessProxy = nil
	
	initHarnessProxy()
	
	if harnessProxy != nil {
		t.Error("expected harnessProxy to be nil when URL is invalid")
	}
}

// TestWave261_InitHarnessProxy_ValidURL tests init with valid URL
func TestWave261_InitHarnessProxy_ValidURL(t *testing.T) {
	os.Setenv("FORGE_HARNESS_URL", "http://localhost:8080")
	defer os.Unsetenv("FORGE_HARNESS_URL")
	
	// Reset harnessProxy
	harnessProxy = nil
	
	initHarnessProxy()
	
	if harnessProxy == nil {
		t.Error("expected harnessProxy to be initialized when FORGE_HARNESS_URL is valid")
	}
	
	// Reset after test
	harnessProxy = nil
}

// TestWave261_InitHarnessProxy_HTTPSURL tests init with HTTPS URL
func TestWave261_InitHarnessProxy_HTTPSURL(t *testing.T) {
	os.Setenv("FORGE_HARNESS_URL", "https://harness.example.com:8443")
	defer os.Unsetenv("FORGE_HARNESS_URL")
	
	// Reset harnessProxy
	harnessProxy = nil
	
	initHarnessProxy()
	
	if harnessProxy == nil {
		t.Error("expected harnessProxy to be initialized for HTTPS URL")
	}
	
	// Reset after test
	harnessProxy = nil
}

// TestWave261_TokenInfo_Struct tests TokenInfo struct fields
func TestWave261_TokenInfo_Struct(t *testing.T) {
	expiresAt := time.Now().Add(24 * time.Hour)
	info := TokenInfo{
		Token:       "test-token",
		Description: "Test Description",
		CreatedAt:   time.Now(),
		ExpiresAt:   &expiresAt,
		Scopes:      []string{"read", "write"},
	}
	
	if info.Token != "test-token" {
		t.Error("Token field mismatch")
	}
	if info.Description != "Test Description" {
		t.Error("Description field mismatch")
	}
	if len(info.Scopes) != 2 {
		t.Errorf("expected 2 scopes, got %d", len(info.Scopes))
	}
	if info.ExpiresAt == nil {
		t.Error("ExpiresAt should not be nil")
	}
}

// TestWave261_AuthConfig_Struct tests AuthConfig struct
func TestWave261_AuthConfig_Struct(t *testing.T) {
	config := AuthConfig{
		Mode:      "api",
		APITokens: make(map[string]TokenInfo),
		LocalUID:  1000,
	}
	
	if config.Mode != "api" {
		t.Error("Mode field mismatch")
	}
	if config.APITokens == nil {
		t.Error("APITokens should not be nil")
	}
	if config.LocalUID != 1000 {
		t.Errorf("expected LocalUID 1000, got %d", config.LocalUID)
	}
}

// TestWave261_AuthManager_RevokeToken tests token revocation
func TestWave261_AuthManager_RevokeToken(t *testing.T) {
	auth := NewAuthManager("local")
	
	// Generate a token
	token, err := auth.GenerateToken("Test Token", []string{"read"})
	if err != nil {
		t.Fatalf("failed to generate token: %v", err)
	}
	
	// Verify token exists
	tokens := auth.ListTokens()
	if len(tokens) != 1 {
		t.Errorf("expected 1 token, got %d", len(tokens))
	}
	
	// Revoke the token
	err = auth.RevokeToken(token)
	if err != nil {
		t.Errorf("failed to revoke token: %v", err)
	}
	
	// Verify token is gone
	tokens = auth.ListTokens()
	if len(tokens) != 0 {
		t.Errorf("expected 0 tokens after revocation, got %d", len(tokens))
	}
}

// TestWave261_AuthManager_RevokeToken_NotFound tests revoking non-existent token
func TestWave261_AuthManager_RevokeToken_NotFound(t *testing.T) {
	auth := NewAuthManager("local")
	
	err := auth.RevokeToken("non-existent-token")
	if err == nil {
		t.Error("expected error when revoking non-existent token")
	}
	if !strings.Contains(err.Error(), "token not found") {
		t.Errorf("expected 'token not found' error, got: %v", err)
	}
}

// TestWave261_AuthManager_GenerateToken_Unique tests that generated tokens are unique
func TestWave261_AuthManager_GenerateToken_Unique(t *testing.T) {
	auth := NewAuthManager("local")
	
	tokens := make(map[string]bool)
	
	// Generate 10 tokens
	for i := 0; i < 10; i++ {
		token, err := auth.GenerateToken("Test", []string{"read"})
		if err != nil {
			t.Fatalf("failed to generate token: %v", err)
		}
		
		if tokens[token] {
			t.Error("generated duplicate token")
		}
		tokens[token] = true
	}
	
	if len(tokens) != 10 {
		t.Errorf("expected 10 unique tokens, got %d", len(tokens))
	}
}

// TestWave261_NewAuthManager tests AuthManager initialization
func TestWave261_NewAuthManager(t *testing.T) {
	auth := NewAuthManager("api")
	
	if auth == nil {
		t.Fatal("expected non-nil AuthManager")
	}
	
	if auth.config.Mode != "api" {
		t.Errorf("expected mode 'api', got %s", auth.config.Mode)
	}
	
	if auth.config.APITokens == nil {
		t.Error("expected non-nil APITokens map")
	}
}

// errTest is a test error
var errTest = errors.New("test error")
