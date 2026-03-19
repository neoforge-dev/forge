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
// Wave 144: auth handlers + agentMetricsHandler uncovered branches
// Targets:
//   handleAuthRefresh  (81.8% → ~91%)
//   authTokensHandler  (88.9% → ~100%)
//   agentMetricsHandler (80.9% → ~90%)
// ---------------------------------------------------------------------------

// newTestAuthManager creates a local-mode AuthManager for testing.
func newTestAuthManager() *AuthManager {
	return NewAuthManager("local")
}

// ---------------------------------------------------------------------------
// handleAuthRefresh — handlers_adr014.go:184
// ---------------------------------------------------------------------------

func TestWave144_HandleAuthRefresh_Success(t *testing.T) {
	auth := newTestAuthManager()
	handler := handleAuthRefresh(auth)
	req := httptest.NewRequest(http.MethodPost, "/api/auth/refresh", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// authTokensHandler — auth_handlers.go
// ---------------------------------------------------------------------------

func TestWave144_AuthTokensHandler_Get(t *testing.T) {
	auth := newTestAuthManager()
	handler := authTokensHandler(auth)
	req := httptest.NewRequest(http.MethodGet, "/api/auth/tokens", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave144_AuthTokensHandler_Post_GenerateToken(t *testing.T) {
	auth := newTestAuthManager()
	handler := authTokensHandler(auth)
	body := `{"description":"test-token","scopes":["read"]}`
	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave144_AuthTokensHandler_Post_EmptyScopes(t *testing.T) {
	// Empty scopes should default to ["*"]
	auth := newTestAuthManager()
	handler := authTokensHandler(auth)
	body := `{"description":"test-token-no-scopes"}`
	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for empty scopes, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave144_AuthTokensHandler_Post_BadJSON(t *testing.T) {
	auth := newTestAuthManager()
	handler := authTokensHandler(auth)
	body := `{invalid json`
	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// agentMetricsHandler — handlers_agent.go:279
// ---------------------------------------------------------------------------

func TestWave144_AgentMetricsHandler_MissingAgentID(t *testing.T) {
	// Path that yields empty agent ID after stripping prefix/suffix.
	req := httptest.NewRequest(http.MethodGet, "/api/agents//metrics", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty agent ID, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave144_AgentMetricsHandler_LimitCap(t *testing.T) {
	// limit=300 > 200 → gets capped to 200; DB nil → returns empty result.
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/agents/kimi/metrics?limit=300", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave144_AgentMetricsHandler_SinceParam(t *testing.T) {
	// Valid RFC3339 since param — covers the since branch; DB nil returns empty.
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/agents/kimi/metrics?since=2026-01-01T00:00:00Z", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave144_AgentMetricsHandler_InvalidSince(t *testing.T) {
	// Invalid since param → skipped (since stays empty); DB nil → empty result.
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/agents/kimi/metrics?since=not-a-date", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for invalid since, got %d: %s", w.Code, w.Body.String())
	}
}
