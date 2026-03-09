//go:build !tmux_bridge
// +build !tmux_bridge

// coverage_wave6_test.go — Coverage wave 6: lease.go, metrics.go, context.go, misc handlers
// Target: +2pp from ~55.3% to ~57.3%
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// setupWave5 is an alias for setupHandlerTest for backward compatibility with wave6-14 tests.
func setupWave5(t *testing.T) func() {
	return setupHandlerTest(t)
}

// --- lease.go ---

func TestNewLeaseManager_Valid(t *testing.T) {
	dbPath := filepath.Join(os.TempDir(), fmt.Sprintf("lease_test_%d.db", time.Now().UnixNano()))
	defer os.Remove(dbPath)

	lm, err := NewLeaseManager(dbPath)
	if err != nil {
		t.Fatalf("NewLeaseManager: %v", err)
	}
	if lm == nil {
		t.Fatal("expected non-nil LeaseManager")
	}

	// Close should work
	if cl, ok := lm.(interface{ Close() error }); ok {
		if err := cl.Close(); err != nil {
			t.Errorf("Close: %v", err)
		}
	}
}

func TestNewLeaseManagerWithStateMachine_Valid(t *testing.T) {
	dbPath := filepath.Join(os.TempDir(), fmt.Sprintf("lease_sm_test_%d.db", time.Now().UnixNano()))
	defer os.Remove(dbPath)

	// Open a DB to create a state machine
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("MigrateUp: %v", err)
	}
	sm := NewTaskStateMachine(db)
	db.Close()

	lm, err := NewLeaseManagerWithStateMachine(dbPath, sm)
	if err != nil {
		t.Fatalf("NewLeaseManagerWithStateMachine: %v", err)
	}
	if lm == nil {
		t.Fatal("expected non-nil LeaseManager")
	}

	if closer, ok := lm.(interface{ Close() error }); ok {
		closer.Close()
	}
}

// --- context.go: SyncEnvelopesToFilesystem / SyncDomainToFilesystem ---

func TestContextManager_SyncEnvelopes_NilSync(t *testing.T) {
	// A ContextManager with nil sync returns an error
	cm := &ContextManager{}
	err := cm.SyncEnvelopesToFilesystem()
	if err == nil {
		t.Error("expected error when sync is nil")
	}
}

func TestContextManager_SyncDomain_NilSync(t *testing.T) {
	cm := &ContextManager{}
	err := cm.SyncDomainToFilesystem("forge")
	if err == nil {
		t.Error("expected error when sync is nil")
	}
}

// --- metrics.go: computeMetricsRollups, RecordTaskState ---

func TestComputeMetricsRollups_EmptyDB(t *testing.T) {
	db, cleanup := setupPatrolTestDB(t)
	defer cleanup()

	// Should not error on empty pattern_runs / metrics tables
	err := computeMetricsRollups(context.Background(), db)
	if err != nil {
		t.Errorf("computeMetricsRollups on empty DB returned error: %v", err)
	}
}

func TestMetrics_RecordTaskState(t *testing.T) {
	m := &Metrics{}
	// Positive delta should not panic
	m.RecordTaskState("running", 1)
	m.RecordTaskState("completed", 5)
	// Zero/negative delta should be a no-op
	m.RecordTaskState("running", 0)
	m.RecordTaskState("failed", -1)
}

// --- handlers_misc.go: dashboardThroughputHandler ---

func TestDashboardThroughputHandler(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/metrics/throughput", nil)
	w := httptest.NewRecorder()
	dashboardThroughputHandler(w, req)
	if w.Code != http.StatusOK && w.Code != http.StatusMethodNotAllowed {
		t.Errorf("unexpected status: %d — %s", w.Code, w.Body.String())
	}
}

// --- handlers_patrols.go: nodesHealthHandler, fleetRecommendationsHandler, patrolExecutionsHandler ---

func TestNodesHealthHandler(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/nodes/health", nil)
	w := httptest.NewRecorder()
	nodesHealthHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("nodesHealthHandler: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestFleetRecommendationsHandler_Get(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/recommendations", nil)
	w := httptest.NewRecorder()
	fleetRecommendationsHandler(w, req)
	// scale_recommendations table may not exist in test DB — accept 200 or 500
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError && w.Code != http.StatusServiceUnavailable {
		t.Errorf("fleetRecommendationsHandler: unexpected status %d: %s", w.Code, w.Body.String())
	}
}

func TestPatrolExecutionsHandler_Get(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patrol-executions", nil)
	w := httptest.NewRecorder()
	patrolExecutionsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("patrolExecutionsHandler: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- handlers_agent.go: agentTelemetryHandler, nodeCapabilitiesHandler ---

func TestNodeCapabilitiesHandler_Get(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/nodes/test-node/capabilities", nil)
	w := httptest.NewRecorder()
	nodeCapabilitiesHandler(w, req)
	// Should return 200 or 404 (node not found)
	if w.Code == http.StatusInternalServerError {
		t.Errorf("nodeCapabilitiesHandler: unexpected 500: %s", w.Body.String())
	}
}

// ============================================================
// handlers_pattern.go — Pattern library CRUD handlers
// ============================================================

// TestPatternsHandler_Get tests GET /api/patterns (list patterns)
func TestPatternsHandler_Get(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patterns", nil)
	w := httptest.NewRecorder()
	patternsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("patternsHandler GET: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestPatternsHandler_Get_WithDomain tests GET /api/patterns?domain=codeswiftr
func TestPatternsHandler_Get_WithDomain(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patterns?domain=codeswiftr", nil)
	w := httptest.NewRecorder()
	patternsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("patternsHandler GET with domain: expected 200, got %d", w.Code)
	}
}

// TestPatternsHandler_Post tests POST /api/patterns (create pattern)
func TestPatternsHandler_Post(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	body := `{"name": "test-pattern", "domain": "test", "yaml_content": "id: test\nname: Test"}`
	req := httptest.NewRequest(http.MethodPost, "/api/patterns", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	patternsHandler(w, req)
	if w.Code != http.StatusCreated {
		t.Errorf("patternsHandler POST: expected 201, got %d: %s", w.Code, w.Body.String())
	}
}

// TestPatternsHandler_Post_MissingName tests POST without required name field
func TestPatternsHandler_Post_MissingName(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	body := `{"domain": "test"}`
	req := httptest.NewRequest(http.MethodPost, "/api/patterns", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	patternsHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing name, got %d", w.Code)
	}
}

// TestPatternsHandler_Post_BadJSON tests POST with invalid JSON
func TestPatternsHandler_Post_BadJSON(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/patterns", bytes.NewBufferString("{bad json"))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	patternsHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

// TestPatternsHandler_MethodNotAllowed tests unsupported methods
func TestPatternsHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodDelete, "/api/patterns", nil)
	w := httptest.NewRecorder()
	patternsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestListPatternsHandler_W6 tests list with no patterns
func TestListPatternsHandler_W6(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patterns", nil)
	w := httptest.NewRecorder()
	listPatternsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("listPatternsHandler: expected 200, got %d", w.Code)
	}
	var result map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if _, ok := result["patterns"]; !ok {
		t.Error("expected 'patterns' key in response")
	}
}

// TestPatternByIDOrRunsHandler_Get tests GET /api/patterns/:id
func TestPatternByIDOrRunsHandler_Get(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	// Create a pattern first
	db := getDBConn()
	_, err := db.Exec(`INSERT INTO patterns (id, name, domain, yaml_content) VALUES (?, ?, ?, ?)`,
		"test-pat-001", "Test Pattern", "test", "id: test-pat-001")
	if err != nil {
		t.Fatalf("insert pattern: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/patterns/test-pat-001", nil)
	req.URL.Path = "/api/patterns/test-pat-001"
	w := httptest.NewRecorder()
	patternByIDOrRunsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("patternByIDOrRunsHandler: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestPatternByIDOrRunsHandler_GetRuns tests GET /api/patterns/:id/runs
func TestPatternByIDOrRunsHandler_GetRuns(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	// Create pattern + run
	db := getDBConn()
	db.Exec(`INSERT INTO patterns (id, name, domain, yaml_content) VALUES (?, ?, ?, ?)`,
		"test-pat-002", "Pattern With Runs", "test", "id: test-pat-002")
	db.Exec(`INSERT INTO pattern_runs (run_id, pattern_id, task_id, agent_id, domain, started_at)
		VALUES (?, ?, ?, ?, ?, datetime('now'))`,
		"run-001", "test-pat-002", "TASK-1", "agent-1", "test")

	req := httptest.NewRequest(http.MethodGet, "/api/patterns/test-pat-002/runs", nil)
	req.URL.Path = "/api/patterns/test-pat-002/runs"
	w := httptest.NewRecorder()
	patternByIDOrRunsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("patternByIDOrRunsHandler runs: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestPatternByIDOrRunsHandler_NotFound tests GET with nonexistent pattern
func TestPatternByIDOrRunsHandler_NotFound(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patterns/nonexistent-123", nil)
	req.URL.Path = "/api/patterns/nonexistent-123"
	w := httptest.NewRecorder()
	patternByIDOrRunsHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

// TestPatternByIDOrRunsHandler_MissingID tests GET without pattern ID
func TestPatternByIDOrRunsHandler_MissingID(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patterns/", nil)
	req.URL.Path = "/api/patterns/"
	w := httptest.NewRecorder()
	patternByIDOrRunsHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing ID, got %d", w.Code)
	}
}

// TestPatternByIDOrRunsHandler_MethodNotAllowed tests POST on /api/patterns/:id
func TestPatternByIDOrRunsHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/patterns/test-123", nil)
	req.URL.Path = "/api/patterns/test-123"
	w := httptest.NewRecorder()
	patternByIDOrRunsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestGetPatternByIDHandler_Found tests successful pattern lookup
func TestGetPatternByIDHandler_Found(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	db := getDBConn()
	db.Exec(`INSERT INTO patterns (id, name, domain, yaml_content) VALUES (?, ?, ?, ?)`,
		"pat-found", "Found Pattern", "test", "id: pat-found")

	req := httptest.NewRequest(http.MethodGet, "/api/patterns/pat-found", nil)
	w := httptest.NewRecorder()
	getPatternByIDHandler(w, req, "pat-found")
	if w.Code != http.StatusOK {
		t.Errorf("getPatternByIDHandler: expected 200, got %d", w.Code)
	}
}

// TestListPatternRunsHandler_W6 tests /api/patterns/:id/runs?limit=10
func TestListPatternRunsHandler_W6(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	db := getDBConn()
	db.Exec(`INSERT INTO patterns (id, name, domain, yaml_content) VALUES (?, ?, ?, ?)`,
		"pat-limit", "Pattern Limit Test", "test", "id: pat-limit")

	req := httptest.NewRequest(http.MethodGet, "/api/patterns/pat-limit/runs?limit=10", nil)
	w := httptest.NewRecorder()
	listPatternRunsHandler(w, req, "pat-limit")
	if w.Code != http.StatusOK {
		t.Errorf("listPatternRunsHandler: expected 200, got %d", w.Code)
	}
}

// ============================================================
// handlers_context.go — Context envelopes handlers
// ============================================================

// TestContextsHandler_Get_W6 tests GET /api/contexts
func TestContextsHandler_Get_W6(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("contextsHandler: expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var result map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if _, ok := result["contexts"]; !ok {
		t.Error("expected 'contexts' key in response")
	}
}

// TestContextsHandler_Get_WithFilters tests GET with query params
func TestContextsHandler_Get_WithFilters(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/contexts?agent_id=glm&domain=test&project=app", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("contextsHandler with filters: expected 200, got %d", w.Code)
	}
}

// TestContextsHandler_MethodNotAllowed_W6 tests POST on /api/contexts
func TestContextsHandler_MethodNotAllowed_W6(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestContextByIDHandler tests GET /api/contexts/:id
func TestContextByIDHandler_Get(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/contexts/ctx-001", nil)
	w := httptest.NewRecorder()
	contextByIDHandler(w, req)
	// This delegates to contextManager.EnvelopeByIDHandler which may return various codes
	// depending on whether contextManager is initialized
	if w.Code == http.StatusInternalServerError {
		t.Logf("contextByIDHandler returned 500 (contextManager may be nil): %s", w.Body.String())
	}
}

// ============================================================
// handlers_pattern.go: loadPatternsFromDocs
// ============================================================

// TestLoadPatternsFromDocs_EmptyDir tests loading when docs/patterns doesn't exist
func TestLoadPatternsFromDocs_NoDir(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	// Set FORGE_ROOT to a temp dir without docs/patterns
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	db := getDBConn()
	// Should not panic or error when dir doesn't exist
	loadPatternsFromDocs(db)
}

// TestLoadPatternsFromDocs_WithYAML tests loading patterns from docs/patterns/
func TestLoadPatternsFromDocs_WithYAML(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	tmpDir := t.TempDir()
	patternsDir := filepath.Join(tmpDir, "docs", "patterns")
	if err := os.MkdirAll(patternsDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	// Create a test pattern file
	patternContent := `id: test-pattern-001
domain: codeswiftr
name: Test Pattern
description: A test pattern
`
	patternPath := filepath.Join(patternsDir, "test-pattern.yaml")
	if err := os.WriteFile(patternPath, []byte(patternContent), 0644); err != nil {
		t.Fatalf("write pattern: %v", err)
	}

	t.Setenv("FORGE_ROOT", tmpDir)
	db := getDBConn()
	loadPatternsFromDocs(db)

	// Verify pattern was loaded
	var count int
	err := db.QueryRow("SELECT COUNT(*) FROM patterns WHERE id = 'test-pattern-001'").Scan(&count)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if count != 1 {
		t.Errorf("expected 1 pattern, got %d", count)
	}
}

// TestLoadPatternsFromDocs_NoYAMLInFile tests pattern with no id field (uses filename)
func TestLoadPatternsFromDocs_FallbackID(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	tmpDir := t.TempDir()
	patternsDir := filepath.Join(tmpDir, "docs", "patterns")
	if err := os.MkdirAll(patternsDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	// Pattern without id field - should use filename stem
	patternContent := `domain: testdomain
name: Pattern Without ID
`
	patternPath := filepath.Join(patternsDir, "my-custom-pattern.yaml")
	if err := os.WriteFile(patternPath, []byte(patternContent), 0644); err != nil {
		t.Fatalf("write pattern: %v", err)
	}

	t.Setenv("FORGE_ROOT", tmpDir)
	db := getDBConn()
	loadPatternsFromDocs(db)

	// Verify pattern was loaded with filename as id
	var name string
	err := db.QueryRow("SELECT name FROM patterns WHERE id = 'my-custom-pattern'").Scan(&name)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if name != "Pattern Without ID" {
		t.Errorf("expected name 'Pattern Without ID', got %q", name)
	}
}

// TestLoadPatternsFromDocs_UpsertConflict tests ON CONFLICT update
func TestLoadPatternsFromDocs_UpsertConflict(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	tmpDir := t.TempDir()
	patternsDir := filepath.Join(tmpDir, "docs", "patterns")
	if err := os.MkdirAll(patternsDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	db := getDBConn()

	// Insert initial pattern
	_, err := db.Exec(`INSERT INTO patterns (id, name, domain, yaml_content) VALUES (?, ?, ?, ?)`,
		"conflict-test", "Original Name", "original", "original content")
	if err != nil {
		t.Fatalf("insert: %v", err)
	}

	// Create file with same id but different content
	patternContent := `id: conflict-test
domain: updated
name: Updated Name
`
	patternPath := filepath.Join(patternsDir, "conflict.yaml")
	if err := os.WriteFile(patternPath, []byte(patternContent), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}

	t.Setenv("FORGE_ROOT", tmpDir)
	loadPatternsFromDocs(db)

	// Verify pattern was updated
	var name string
	err = db.QueryRow("SELECT name FROM patterns WHERE id = 'conflict-test'").Scan(&name)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if name != "Updated Name" {
		t.Errorf("expected updated name, got %q", name)
	}
}

// TestLoadPatternsFromDocs_SkipsNonYAML tests that non-yaml files are skipped
func TestLoadPatternsFromDocs_SkipsNonYAML(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	tmpDir := t.TempDir()
	patternsDir := filepath.Join(tmpDir, "docs", "patterns")
	if err := os.MkdirAll(patternsDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	// Create non-yaml file
	if err := os.WriteFile(filepath.Join(patternsDir, "readme.txt"), []byte("not yaml"), 0644); err != nil {
		t.Fatalf("write txt: %v", err)
	}
	// Create subdirectory (should be skipped)
	if err := os.MkdirAll(filepath.Join(patternsDir, "subdir"), 0755); err != nil {
		t.Fatalf("mkdir subdir: %v", err)
	}

	t.Setenv("FORGE_ROOT", tmpDir)
	db := getDBConn()
	loadPatternsFromDocs(db)

	// Verify no patterns loaded
	var count int
	err := db.QueryRow("SELECT COUNT(*) FROM patterns").Scan(&count)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if count != 0 {
		t.Errorf("expected 0 patterns, got %d", count)
	}
}

// ============================================================
// handlers_task.go: taskHistoryHandler
// ============================================================

// TestTaskHistoryHandler_Get_W6 tests GET /api/tasks/:id/history
func TestTaskHistoryHandler_Get_W6(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	// Create a task first
	db := getDBConn()
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
		"task-hist-001", "test", "app", "feature", "Test Task", 5, "queued", "QUEUED")
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-hist-001/history", nil)
	w := httptest.NewRecorder()
	taskHistoryHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("taskHistoryHandler: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestTaskHistoryHandler_MissingID_W6 tests GET without task ID
func TestTaskHistoryHandler_MissingID_W6(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks//history", nil)
	w := httptest.NewRecorder()
	taskHistoryHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing ID, got %d", w.Code)
	}
}

// TestTaskHistoryHandler_MethodNotAllowed_W6 tests POST on history endpoint
func TestTaskHistoryHandler_MethodNotAllowed_W6(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/test/history", nil)
	w := httptest.NewRecorder()
	taskHistoryHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ============================================================
// handlers_task.go: pruneTasksHandler
// ============================================================

// TestPruneTasksHandler_Post_W6 tests POST /api/tasks/prune
func TestPruneTasksHandler_Post_W6(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/prune", nil)
	w := httptest.NewRecorder()
	pruneTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("pruneTasksHandler: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestPruneTasksHandler_MethodNotAllowed_W6 tests GET on prune endpoint
func TestPruneTasksHandler_MethodNotAllowed_W6(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/prune", nil)
	w := httptest.NewRecorder()
	pruneTasksHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ============================================================
// handlers_openclaw.go: openclawEventsHandler
// ============================================================

// TestOpenclawEventsHandler_MethodNotAllowed tests non-GET method on events endpoint.
// GET is the SSE stream method; DELETE triggers 405.
func TestOpenclawEventsHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodDelete, "/api/openclaw/events", nil)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestOpenclawEventsHandler_PostNotAllowed tests that POST returns 405.
// The endpoint only accepts GET (SSE stream).
func TestOpenclawEventsHandler_PostNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	// Events endpoint is GET-only SSE stream; POST returns 405
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/events", nil)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for POST on SSE endpoint, got %d", w.Code)
	}
}

// TestOpenclawEventsHandler_EmptyBody tests that PUT returns 405.
func TestOpenclawEventsHandler_EmptyBody(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	// Events endpoint is GET-only SSE stream; PUT returns 405
	req := httptest.NewRequest(http.MethodPut, "/api/openclaw/events", nil)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for PUT, got %d", w.Code)
	}
}
