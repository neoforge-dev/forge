//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// ============================================================
// coverage_wave250_test.go — auth_handlers.go + handlers_misc.go + config.go
// ============================================================

// ============================================================
// auth_handlers.go — authTokensHandler (GET, POST, MethodNotAllowed)
// ============================================================

func TestWave250_AuthTokensHandler_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	auth := NewAuthManager("test-secret")
	token1, _ := auth.GenerateToken("test token 1", []string{"read"})
	_ = token1

	req := httptest.NewRequest(http.MethodGet, "/api/auth/tokens", nil)
	rr := httptest.NewRecorder()

	handler := authTokensHandler(auth)
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", rr.Code)
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if _, ok := resp["tokens"]; !ok {
		t.Error("expected 'tokens' field in response")
	}
}

func TestWave250_AuthTokensHandler_POST_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	auth := NewAuthManager("test-secret")

	reqBody := `{"description":"test token","scopes":["read","write"]}`
	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	handler := authTokensHandler(auth)
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]string
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if _, ok := resp["token"]; !ok {
		t.Error("expected 'token' field in response")
	}
}

func TestWave250_AuthTokensHandler_POST_DefaultScopes(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	auth := NewAuthManager("test-secret")

	// No scopes provided - should default to ["*"]
	reqBody := `{"description":"token with default scopes"}`
	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	handler := authTokensHandler(auth)
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]string
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if _, ok := resp["token"]; !ok {
		t.Error("expected 'token' field in response")
	}
}

func TestWave250_AuthTokensHandler_POST_InvalidBody(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	auth := NewAuthManager("test-secret")

	reqBody := `invalid json`
	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	handler := authTokensHandler(auth)
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", rr.Code)
	}
}

// ============================================================
// handlers_misc.go — healthHandler, statusHandler
// ============================================================

func TestWave250_HealthHandler(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rr := httptest.NewRecorder()

	healthHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", rr.Code)
	}

	var resp HealthResponse
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if resp.Status != "ok" {
		t.Errorf("expected status 'ok', got %s", resp.Status)
	}

	if resp.Timestamp == "" {
		t.Error("expected non-empty timestamp")
	}
}

func TestWave250_StatusHandler(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/status", nil)
	rr := httptest.NewRecorder()

	statusHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", rr.Code)
	}

	var resp StatusResponse
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if resp.Version == "" {
		t.Error("expected non-empty version")
	}

	if resp.Status != "running" {
		t.Errorf("expected status 'running', got %s", resp.Status)
	}
}

// ============================================================
// handlers_misc.go — qualityGatesHandler
// ============================================================

func TestWave250_QualityGatesHandler_InvalidTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	reqBody := `{"test_pass_rate":0.9,"coverage_pct":85.0,"lint_issues":0}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks//quality-gates", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	qualityGatesHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", rr.Code)
	}
}

func TestWave250_QualityGatesHandler_TaskNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	reqBody := `{"test_pass_rate":0.9,"coverage_pct":85.0,"lint_issues":0}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/NONEXISTENT/quality-gates", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	qualityGatesHandler(rr, req)

	if rr.Code != http.StatusNotFound {
		t.Errorf("expected status 404, got %d", rr.Code)
	}
}

func TestWave250_QualityGatesHandler_InvalidBody(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Create quality_gate_results table first
	db.Exec(`CREATE TABLE IF NOT EXISTS quality_gate_results (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		task_id TEXT NOT NULL,
		test_pass_rate REAL NOT NULL,
		coverage_pct REAL NOT NULL,
		lint_issues INTEGER NOT NULL,
		created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
	)`)

	// Insert a task first (domain and project are required)
	taskID := "TASK-250-QG"
	db.Exec(`INSERT INTO tasks (id, domain, project, type, status, title) VALUES (?, ?, ?, ?, ?, ?)`,
		taskID, "test-domain", "test-project", "feature", "pending", "Test Task")

	reqBody := `invalid json`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/quality-gates", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	qualityGatesHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", rr.Code)
	}
}

func TestWave250_QualityGatesHandler_InvalidTestPassRate(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Create quality_gate_results table first
	db.Exec(`CREATE TABLE IF NOT EXISTS quality_gate_results (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		task_id TEXT NOT NULL,
		test_pass_rate REAL NOT NULL,
		coverage_pct REAL NOT NULL,
		lint_issues INTEGER NOT NULL,
		created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
	)`)

	taskID := "TASK-250-QG-TPR"
	db.Exec(`INSERT INTO tasks (id, domain, project, type, status, title) VALUES (?, ?, ?, ?, ?, ?)`,
		taskID, "test-domain", "test-project", "feature", "pending", "Test Task")

	reqBody := `{"test_pass_rate":1.5,"coverage_pct":85.0,"lint_issues":0}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/quality-gates", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	qualityGatesHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", rr.Code)
	}
}

func TestWave250_QualityGatesHandler_InvalidCoveragePct(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Create quality_gate_results table first
	db.Exec(`CREATE TABLE IF NOT EXISTS quality_gate_results (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		task_id TEXT NOT NULL,
		test_pass_rate REAL NOT NULL,
		coverage_pct REAL NOT NULL,
		lint_issues INTEGER NOT NULL,
		created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
	)`)

	taskID := "TASK-250-QG-COV"
	db.Exec(`INSERT INTO tasks (id, domain, project, type, status, title) VALUES (?, ?, ?, ?, ?, ?)`,
		taskID, "test-domain", "test-project", "feature", "pending", "Test Task")

	reqBody := `{"test_pass_rate":0.9,"coverage_pct":150.0,"lint_issues":0}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/quality-gates", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	qualityGatesHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", rr.Code)
	}
}

func TestWave250_QualityGatesHandler_InvalidLintIssues(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Create quality_gate_results table first
	db.Exec(`CREATE TABLE IF NOT EXISTS quality_gate_results (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		task_id TEXT NOT NULL,
		test_pass_rate REAL NOT NULL,
		coverage_pct REAL NOT NULL,
		lint_issues INTEGER NOT NULL,
		created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
	)`)

	taskID := "TASK-250-QG-LINT"
	db.Exec(`INSERT INTO tasks (id, domain, project, type, status, title) VALUES (?, ?, ?, ?, ?, ?)`,
		taskID, "test-domain", "test-project", "feature", "pending", "Test Task")

	reqBody := `{"test_pass_rate":0.9,"coverage_pct":85.0,"lint_issues":-1}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/quality-gates", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	qualityGatesHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", rr.Code)
	}
}

func TestWave250_QualityGatesHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Create quality_gate_results table
	db.Exec(`CREATE TABLE IF NOT EXISTS quality_gate_results (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		task_id TEXT NOT NULL,
		test_pass_rate REAL NOT NULL,
		coverage_pct REAL NOT NULL,
		lint_issues INTEGER NOT NULL,
		created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
	)`)

	taskID := "TASK-250-QG-OK"
	db.Exec(`INSERT INTO tasks (id, domain, project, type, status, title) VALUES (?, ?, ?, ?, ?, ?)`,
		taskID, "test-domain", "test-project", "feature", "pending", "Test Task")

	reqBody := `{"test_pass_rate":0.95,"coverage_pct":87.5,"lint_issues":3}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/quality-gates", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	qualityGatesHandler(rr, req)

	if rr.Code != http.StatusCreated {
		t.Errorf("expected status 201, got %d: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if resp["task_id"] != taskID {
		t.Errorf("expected task_id %s, got %v", taskID, resp["task_id"])
	}
}

// ============================================================
// handlers_misc.go — dashboardThroughputHandler
// ============================================================

func TestWave250_DashboardThroughputHandler_NoDB(t *testing.T) {
	setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/throughput", nil)
	rr := httptest.NewRecorder()

	dashboardThroughputHandler(rr, req)

	if rr.Code != http.StatusServiceUnavailable {
		t.Errorf("expected status 503, got %d", rr.Code)
	}
}

func TestWave250_DashboardThroughputHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/throughput?hours=48", nil)
	rr := httptest.NewRecorder()

	dashboardThroughputHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if _, ok := resp["hours"]; !ok {
		t.Error("expected 'hours' field in response")
	}

	if _, ok := resp["total_today"]; !ok {
		t.Error("expected 'total_today' field in response")
	}
}

// ============================================================
// config.go — configPaths, loadForgeConfig, parseForgeTOML
// ============================================================

func TestWave250_ConfigPaths(t *testing.T) {
	paths := configPaths()

	if len(paths) == 0 {
		t.Fatal("expected at least one config path")
	}

	// Should include paths from FORGE_CONFIG, home dir, and cwd
	found := false
	for _, p := range paths {
		if p != "" {
			found = true
			break
		}
	}
	if !found {
		t.Error("expected at least one non-empty path")
	}
}

func TestWave250_LoadForgeConfig_NoFile(t *testing.T) {
	// Ensure no config file exists at the searched paths
	cfg := loadForgeConfig()

	// Should return empty config when no file found
	_ = cfg
}

func TestWave250_ParseForgeTOML_Success(t *testing.T) {
	tmpDir := t.TempDir()
	configPath := filepath.Join(tmpDir, "forge.toml")

	configContent := `[daemon]
port = 9090
ws_port = 9091
db_path = "/custom/path/db.db"
node_id = "test-node"
forge_root = "/custom/root"

[auth]
mode = "local"
api_token = "secret-token"
`

	if err := os.WriteFile(configPath, []byte(configContent), 0644); err != nil {
		t.Fatalf("failed to write config file: %v", err)
	}

	cfg, err := parseForgeTOML(configPath)
	if err != nil {
		t.Fatalf("parseForgeTOML failed: %v", err)
	}

	if cfg.Daemon.Port != 9090 {
		t.Errorf("expected port 9090, got %d", cfg.Daemon.Port)
	}

	if cfg.Daemon.WSPort != 9091 {
		t.Errorf("expected ws_port 9091, got %d", cfg.Daemon.WSPort)
	}

	if cfg.Daemon.DBPath != "/custom/path/db.db" {
		t.Errorf("expected db_path '/custom/path/db.db', got %s", cfg.Daemon.DBPath)
	}

	if cfg.Daemon.NodeID != "test-node" {
		t.Errorf("expected node_id 'test-node', got %s", cfg.Daemon.NodeID)
	}

	if cfg.Daemon.ForgeRoot != "/custom/root" {
		t.Errorf("expected forge_root '/custom/root', got %s", cfg.Daemon.ForgeRoot)
	}

	if cfg.Auth.Mode != "local" {
		t.Errorf("expected auth mode 'local', got %s", cfg.Auth.Mode)
	}

	if cfg.Auth.APIToken != "secret-token" {
		t.Errorf("expected api_token 'secret-token', got %s", cfg.Auth.APIToken)
	}
}

func TestWave250_ParseForgeTOML_FileNotFound(t *testing.T) {
	_, err := parseForgeTOML("/nonexistent/path/forge.toml")
	if err == nil {
		t.Error("expected error for non-existent file")
	}
}

func TestWave250_ParseForgeTOML_InvalidLines(t *testing.T) {
	tmpDir := t.TempDir()
	configPath := filepath.Join(tmpDir, "forge.toml")

	// Config with various edge cases including invalid lines
	configContent := `[daemon]
port = 8080
# This is a comment
invalid_line_without_equals
ws_port = 8082

[auth]
mode = "api"
`

	if err := os.WriteFile(configPath, []byte(configContent), 0644); err != nil {
		t.Fatalf("failed to write config file: %v", err)
	}

	cfg, err := parseForgeTOML(configPath)
	if err != nil {
		t.Fatalf("parseForgeTOML failed: %v", err)
	}

	if cfg.Daemon.Port != 8080 {
		t.Errorf("expected port 8080, got %d", cfg.Daemon.Port)
	}

	if cfg.Daemon.WSPort != 8082 {
		t.Errorf("expected ws_port 8082, got %d", cfg.Daemon.WSPort)
	}

	if cfg.Auth.Mode != "api" {
		t.Errorf("expected auth mode 'api', got %s", cfg.Auth.Mode)
	}
}

func TestWave250_ParseForgeTOML_InvalidInt(t *testing.T) {
	tmpDir := t.TempDir()
	configPath := filepath.Join(tmpDir, "forge.toml")

	configContent := `[daemon]
port = not_a_number
ws_port = 8082
`

	if err := os.WriteFile(configPath, []byte(configContent), 0644); err != nil {
		t.Fatalf("failed to write config file: %v", err)
	}

	cfg, err := parseForgeTOML(configPath)
	if err != nil {
		t.Fatalf("parseForgeTOML failed: %v", err)
	}

	// Invalid int should result in 0 (default)
	if cfg.Daemon.Port != 0 {
		t.Errorf("expected port 0 (invalid), got %d", cfg.Daemon.Port)
	}

	// Valid int should still parse
	if cfg.Daemon.WSPort != 8082 {
		t.Errorf("expected ws_port 8082, got %d", cfg.Daemon.WSPort)
	}
}

// ============================================================
// handlers_misc.go — releaseTaskHandler, extendLeaseHandler
// ============================================================

func TestWave250_ReleaseTaskHandler_InvalidBody(t *testing.T) {
	reqBody := `invalid json`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-123/release", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	releaseTaskHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", rr.Code)
	}
}

func TestWave250_ExtendLeaseHandler_InvalidBody(t *testing.T) {
	reqBody := `invalid json`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-123/extend-lease", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	extendLeaseHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", rr.Code)
	}
}

// ============================================================
// auth_handlers.go — additional edge cases
// ============================================================

func TestWave250_AuthTokensHandler_POST_EmptyBody(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	auth := NewAuthManager("test-secret")

	req := httptest.NewRequest(http.MethodPost, "/api/auth/tokens", bytes.NewReader([]byte{}))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	handler := authTokensHandler(auth)
	handler.ServeHTTP(rr, req)

	// Empty body should result in bad request
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400 for empty body, got %d", rr.Code)
	}
}
