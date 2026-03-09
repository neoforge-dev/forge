//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

func setupADR014TestDB(t *testing.T) (*sql.DB, func()) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("failed to open test db: %v", err)
	}

	// Create tables
	schema := `
		CREATE TABLE tasks (
			id TEXT PRIMARY KEY,
			state TEXT DEFAULT 'queued',
			created_at TEXT DEFAULT CURRENT_TIMESTAMP
		);
		CREATE TABLE agents (
			id TEXT PRIMARY KEY,
			status TEXT DEFAULT 'offline',
			created_at TEXT DEFAULT CURRENT_TIMESTAMP
		);
		CREATE TABLE approvals (
			id TEXT PRIMARY KEY,
			status TEXT DEFAULT 'pending',
			created_at TEXT DEFAULT CURRENT_TIMESTAMP
		);
	`
	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("failed to create schema: %v", err)
	}

	// Insert test data
	db.Exec("INSERT INTO tasks (id, state) VALUES ('task-1', 'queued')")
	db.Exec("INSERT INTO tasks (id, state) VALUES ('task-2', 'running')")
	db.Exec("INSERT INTO tasks (id, state) VALUES ('task-3', 'completed')")
	db.Exec("INSERT INTO agents (id, status) VALUES ('agent-1', 'online')")
	db.Exec("INSERT INTO agents (id, status) VALUES ('agent-2', 'offline')")
	db.Exec("INSERT INTO approvals (id, status) VALUES ('approval-1', 'pending')")
	db.Exec("INSERT INTO approvals (id, status) VALUES ('approval-2', 'approved')")

	return db, func() { db.Close() }
}

func TestHandleLeadState(t *testing.T) {
	db, cleanup := setupADR014TestDB(t)
	defer cleanup()

	os.Setenv("FORGE_NODE_ID", "test-node")
	defer os.Unsetenv("FORGE_NODE_ID")

	req := httptest.NewRequest(http.MethodGet, "/api/lead-state", nil)
	rr := httptest.NewRecorder()

	handler := handleLeadState(db)
	handler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var response map[string]interface{}
	if err := json.Unmarshal(rr.Body.Bytes(), &response); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	// Check timestamp exists
	if _, ok := response["timestamp"]; !ok {
		t.Error("expected timestamp in response")
	}

	// Check node_id
	if response["node_id"] != "test-node" {
		t.Errorf("expected node_id 'test-node', got %v", response["node_id"])
	}

	// Check tasks structure
	tasks, ok := response["tasks"].(map[string]interface{})
	if !ok {
		t.Fatal("expected tasks object")
	}

	byState, ok := tasks["by_state"].(map[string]interface{})
	if !ok {
		t.Fatal("expected tasks.by_state object")
	}

	if byState["queued"] != float64(1) {
		t.Errorf("expected 1 queued task, got %v", byState["queued"])
	}

	// Check queue_depth
	if tasks["queue_depth"] != float64(1) {
		t.Errorf("expected queue_depth 1, got %v", tasks["queue_depth"])
	}

	// Check agents
	agents, ok := response["agents"].(map[string]interface{})
	if !ok {
		t.Fatal("expected agents object")
	}

	byStatus, ok := agents["by_status"].(map[string]interface{})
	if !ok {
		t.Fatal("expected agents.by_status object")
	}

	if byStatus["online"] != float64(1) {
		t.Errorf("expected 1 online agent, got %v", byStatus["online"])
	}

	// Check approvals
	approvals, ok := response["approvals"].(map[string]interface{})
	if !ok {
		t.Fatal("expected approvals object")
	}

	if approvals["pending"] != float64(1) {
		t.Errorf("expected 1 pending approval, got %v", approvals["pending"])
	}
}

func TestHandleLeadStateMethodNotAllowed(t *testing.T) {
	db, cleanup := setupADR014TestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/lead-state", nil)
	rr := httptest.NewRecorder()

	handler := handleLeadState(db)
	handler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestHandleAuthMe(t *testing.T) {
	auth := NewAuthManager("local")

	os.Setenv("FORGE_NODE_ID", "test-node")
	os.Setenv("FORGE_NODE_NAME", "Test Node")
	defer func() {
		os.Unsetenv("FORGE_NODE_ID")
		os.Unsetenv("FORGE_NODE_NAME")
	}()

	req := httptest.NewRequest(http.MethodGet, "/api/auth/me", nil)
	rr := httptest.NewRecorder()

	handler := handleAuthMe(auth)
	handler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var response map[string]interface{}
	if err := json.Unmarshal(rr.Body.Bytes(), &response); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if response["node_id"] != "test-node" {
		t.Errorf("expected node_id 'test-node', got %v", response["node_id"])
	}

	if response["node_name"] != "Test Node" {
		t.Errorf("expected node_name 'Test Node', got %v", response["node_name"])
	}

	if response["auth_mode"] != "local" {
		t.Errorf("expected auth_mode 'local', got %v", response["auth_mode"])
	}

	if response["version"] != "v3.0" {
		t.Errorf("expected version 'v3.0', got %v", response["version"])
	}
}

func TestHandleAuthMeMethodNotAllowed(t *testing.T) {
	auth := NewAuthManager("local")

	req := httptest.NewRequest(http.MethodPost, "/api/auth/me", nil)
	rr := httptest.NewRecorder()

	handler := handleAuthMe(auth)
	handler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestHandleAuthLogin(t *testing.T) {
	auth := NewAuthManager("api")

	os.Setenv("FORGE_WEBHOOK_TOKEN", "test-webhook-token")
	defer os.Unsetenv("FORGE_WEBHOOK_TOKEN")

	body := `{"token": "test-webhook-token"}`
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	handler := handleAuthLogin(auth)
	handler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var response map[string]interface{}
	if err := json.Unmarshal(rr.Body.Bytes(), &response); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if response["token_type"] != "bearer" {
		t.Errorf("expected token_type 'bearer', got %v", response["token_type"])
	}

	if response["expires_in"] != float64(86400) {
		t.Errorf("expected expires_in 86400, got %v", response["expires_in"])
	}

	if _, ok := response["token"]; !ok {
		t.Error("expected token in response")
	}
}

func TestHandleAuthLoginInvalidToken(t *testing.T) {
	auth := NewAuthManager("api")

	os.Setenv("FORGE_WEBHOOK_TOKEN", "test-webhook-token")
	defer os.Unsetenv("FORGE_WEBHOOK_TOKEN")

	body := `{"token": "wrong-token"}`
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	handler := handleAuthLogin(auth)
	handler(rr, req)

	if rr.Code != http.StatusUnauthorized {
		t.Errorf("expected status 401, got %d", rr.Code)
	}
}

func TestHandleAuthLoginNotConfigured(t *testing.T) {
	auth := NewAuthManager("api")

	// No FORGE_WEBHOOK_TOKEN set
	os.Unsetenv("FORGE_WEBHOOK_TOKEN")

	body := `{"token": "anything"}`
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	handler := handleAuthLogin(auth)
	handler(rr, req)

	if rr.Code != http.StatusServiceUnavailable {
		t.Errorf("expected status 503, got %d", rr.Code)
	}
}

func TestHandleAuthLoginMethodNotAllowed(t *testing.T) {
	auth := NewAuthManager("api")

	req := httptest.NewRequest(http.MethodGet, "/api/auth/login", nil)
	rr := httptest.NewRecorder()

	handler := handleAuthLogin(auth)
	handler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestHandleAuthRefresh(t *testing.T) {
	auth := NewAuthManager("api")

	req := httptest.NewRequest(http.MethodPost, "/api/auth/refresh", nil)
	rr := httptest.NewRecorder()

	handler := handleAuthRefresh(auth)
	handler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var response map[string]interface{}
	if err := json.Unmarshal(rr.Body.Bytes(), &response); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if response["token_type"] != "bearer" {
		t.Errorf("expected token_type 'bearer', got %v", response["token_type"])
	}

	if _, ok := response["token"]; !ok {
		t.Error("expected token in response")
	}
}

func TestHandleAuthRefreshMethodNotAllowed(t *testing.T) {
	auth := NewAuthManager("api")

	req := httptest.NewRequest(http.MethodGet, "/api/auth/refresh", nil)
	rr := httptest.NewRecorder()

	handler := handleAuthRefresh(auth)
	handler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

// TestProxyFallback404 verifies 404 response when FORGE_HARNESS_URL is unset
func TestProxyFallback404(t *testing.T) {
	// Ensure FORGE_HARNESS_URL is not set
	os.Unsetenv("FORGE_HARNESS_URL")

	req := httptest.NewRequest(http.MethodGet, "/api/unknown-endpoint", nil)
	rr := httptest.NewRecorder()

	harnessFallbackHandler(rr, req)

	if rr.Code != http.StatusNotFound {
		t.Errorf("expected status 404, got %d", rr.Code)
	}

	body := rr.Body.String()
	if !strings.Contains(body, "not_found") && !strings.Contains(body, "404") {
		t.Errorf("expected 404 message in body, got: %s", body)
	}
}
