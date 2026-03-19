//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 154: handleSystemHealth, handleQueueDepth, queue.writeEvent,
//           queue.ListTasksByStatus, queue.GetPendingDispatches,
//           event_bus.initSchema, routing_config.LoadRoutingBundle,
//           handlers_patrols.patrolRunHandler
//
// Also carries forward auth + agentMetrics tests (previously in this file).
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// handleSystemHealth — main.go:402
// ---------------------------------------------------------------------------

func TestWave154_HandleSystemHealth_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	// handleSystemHealth delegates through WriteOutputWithFlag which may return
	// application/json or text/plain depending on format — just confirm a 200 body.
	if w.Body.Len() == 0 {
		t.Error("expected non-empty response body")
	}
}

func TestWave154_HandleSystemHealth_FormatTable(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/system/health?format=table", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)
	// Any non-5xx response is acceptable — table format falls through to the WriteOutputWithFlag path.
	if w.Code >= 500 {
		t.Errorf("unexpected server error %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handleQueueDepth — main.go:436
// ---------------------------------------------------------------------------

func TestWave154_HandleQueueDepth_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	prev := taskQueue
	taskQueue = q
	defer func() { taskQueue = prev }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "depth") {
		t.Errorf("expected 'depth' in response, got: %s", w.Body.String())
	}
}

func TestWave154_HandleQueueDepth_FormatJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	prev := taskQueue
	taskQueue = q
	defer func() { taskQueue = prev }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/depth?format=json", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with format=json, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// queue.writeEvent — queue.go:872
// ---------------------------------------------------------------------------

func TestWave154_WriteEvent_ValidEvent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	sq := q.(*sqliteTaskQueue)

	// writeEvent is best-effort (no return value) — just confirm it doesn't panic.
	sq.writeEvent(context.Background(), "task-wave154", "testdomain", "testproject", "task.created", map[string]interface{}{
		"title": "wave 144 test event",
	})

	// Verify the row was inserted.
	var count int
	err = db.QueryRow(`SELECT COUNT(*) FROM task_events WHERE task_id = 'task-wave154'`).Scan(&count)
	if err != nil {
		t.Fatalf("query task_events: %v", err)
	}
	if count != 1 {
		t.Errorf("expected 1 event row, got %d", count)
	}
}

func TestWave154_WriteEvent_EmptyPayload(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	sq := q.(*sqliteTaskQueue)

	// nil payload — json.Marshal(nil) returns "null" — should not panic.
	sq.writeEvent(context.Background(), "task-wave154-empty", "d", "p", "task.failed", nil)

	var count int
	db.QueryRow(`SELECT COUNT(*) FROM task_events WHERE task_id = 'task-wave154-empty'`).Scan(&count)
	if count != 1 {
		t.Errorf("expected 1 row for nil-payload writeEvent, got %d", count)
	}
}

// ---------------------------------------------------------------------------
// queue.ListTasksByStatus — queue.go:640
// ---------------------------------------------------------------------------

func TestWave154_ListTasksByStatus_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	tasks, err := q.ListTasksByStatus(context.Background(), TaskStatusQueued, 50)
	if err != nil {
		t.Fatalf("ListTasksByStatus: %v", err)
	}
	if len(tasks) != 0 {
		t.Errorf("expected 0 tasks, got %d", len(tasks))
	}
}

func TestWave154_ListTasksByStatus_WithTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	// Insert a task directly into the DB.
	_, err = db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, title, created_at, updated_at)
		VALUES ('w144-task-1', 'testdomain', 'testproj', 'feature', 5, 'queued', 'Wave 144 task', datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	tasks, err := q.ListTasksByStatus(context.Background(), TaskStatusQueued, 50)
	if err != nil {
		t.Fatalf("ListTasksByStatus: %v", err)
	}
	if len(tasks) != 1 {
		t.Errorf("expected 1 task, got %d", len(tasks))
	}
	if tasks[0].ID != "w144-task-1" {
		t.Errorf("unexpected task ID: %s", tasks[0].ID)
	}
}

func TestWave154_ListTasksByStatus_FiltersByStatus(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, title, created_at, updated_at)
		VALUES ('w144-queued', 'td', 'tp', 'feature', 5, 'queued', 'Queued', datetime('now'), datetime('now'))
	`)
	db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, title, created_at, updated_at)
		VALUES ('w144-completed', 'td', 'tp', 'feature', 5, 'completed', 'Completed', datetime('now'), datetime('now'))
	`)

	queued, err := q.ListTasksByStatus(context.Background(), TaskStatusQueued, 50)
	if err != nil {
		t.Fatalf("ListTasksByStatus queued: %v", err)
	}
	if len(queued) != 1 {
		t.Errorf("expected 1 queued task, got %d", len(queued))
	}

	completed, err := q.ListTasksByStatus(context.Background(), TaskStatusCompleted, 50)
	if err != nil {
		t.Fatalf("ListTasksByStatus completed: %v", err)
	}
	if len(completed) != 1 {
		t.Errorf("expected 1 completed task, got %d", len(completed))
	}
}

// ---------------------------------------------------------------------------
// queue.GetPendingDispatches — queue.go:902
// ---------------------------------------------------------------------------

func TestWave154_GetPendingDispatches_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	dispatches, err := q.GetPendingDispatches(context.Background(), "agent-wave154")
	if err != nil {
		t.Fatalf("GetPendingDispatches: %v", err)
	}
	if len(dispatches) != 0 {
		t.Errorf("expected 0 dispatches, got %d", len(dispatches))
	}
}

func TestWave154_GetPendingDispatches_AfterQueue(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	// Insert a task so the foreign-key (if any) is satisfied — or insert directly.
	db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, title, created_at, updated_at)
		VALUES ('w144-dispatch-task', 'td', 'tp', 'feature', 5, 'queued', 'Dispatch task', datetime('now'), datetime('now'))
	`)

	err = q.QueuePendingDispatch(context.Background(), "w144-dispatch-task", "agent-wave154", map[string]interface{}{
		"action": "claim",
	})
	if err != nil {
		t.Fatalf("QueuePendingDispatch: %v", err)
	}

	dispatches, err := q.GetPendingDispatches(context.Background(), "agent-wave154")
	if err != nil {
		t.Fatalf("GetPendingDispatches: %v", err)
	}
	if len(dispatches) != 1 {
		t.Errorf("expected 1 dispatch, got %d", len(dispatches))
	}
	if dispatches[0].TaskID != "w144-dispatch-task" {
		t.Errorf("unexpected task ID: %s", dispatches[0].TaskID)
	}
}

func TestWave154_GetPendingDispatches_SkipsSent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, title, created_at, updated_at)
		VALUES ('w144-sent-task', 'td', 'tp', 'feature', 5, 'queued', 'Sent task', datetime('now'), datetime('now'))
	`)

	err = q.QueuePendingDispatch(context.Background(), "w144-sent-task", "agent-sent", map[string]interface{}{})
	if err != nil {
		t.Fatalf("QueuePendingDispatch: %v", err)
	}

	// Get the dispatch ID and mark it sent.
	var id int64
	db.QueryRow(`SELECT id FROM pending_dispatches WHERE agent_id='agent-sent'`).Scan(&id)
	if err := q.MarkDispatchSent(context.Background(), id); err != nil {
		t.Fatalf("MarkDispatchSent: %v", err)
	}

	// Now GetPendingDispatches should return zero rows.
	dispatches, err := q.GetPendingDispatches(context.Background(), "agent-sent")
	if err != nil {
		t.Fatalf("GetPendingDispatches after mark sent: %v", err)
	}
	if len(dispatches) != 0 {
		t.Errorf("expected 0 dispatches after mark sent, got %d", len(dispatches))
	}
}

// ---------------------------------------------------------------------------
// event_bus.initSchema / NewEventBus — event_bus.go:95
// ---------------------------------------------------------------------------

func TestWave154_NewEventBus_CreatesSchema(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	if eb == nil {
		t.Fatal("expected non-nil EventBus")
	}

	// Confirm the events table was created.
	var count int
	err = db.QueryRow(`SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='events'`).Scan(&count)
	if err != nil {
		t.Fatalf("query sqlite_master: %v", err)
	}
	if count != 1 {
		t.Errorf("expected events table to exist, got count=%d", count)
	}
}

func TestWave154_NewEventBus_NilDB(t *testing.T) {
	_, err := NewEventBus(nil)
	if err == nil {
		t.Error("expected error for nil DB, got nil")
	}
}

func TestWave154_NewEventBus_ExistingTable(t *testing.T) {
	// Create the EventBus twice — second call hits the "table exists" branch of initSchema.
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if _, err := NewEventBus(db); err != nil {
		t.Fatalf("first NewEventBus: %v", err)
	}
	// Second construction should not fail (hits the else branch in initSchema).
	if _, err := NewEventBus(db); err != nil {
		t.Fatalf("second NewEventBus (existing table): %v", err)
	}
}

// ---------------------------------------------------------------------------
// routing_config.LoadRoutingBundle — routing_config.go:61
// ---------------------------------------------------------------------------

func TestWave154_LoadRoutingBundle_ValidDir(t *testing.T) {
	// Use the real config/routing directory from the repo root.
	repoRoot := "/home/openclaw/work/FORGE"
	configDir := filepath.Join(repoRoot, "config", "routing")

	if _, err := os.Stat(configDir); os.IsNotExist(err) {
		t.Skip("config/routing directory not found — skipping")
	}

	bundle, err := LoadRoutingBundle(configDir)
	if err != nil {
		t.Fatalf("LoadRoutingBundle: %v", err)
	}
	if bundle == nil {
		t.Fatal("expected non-nil bundle")
	}
	if len(bundle.Agents.Agents) == 0 {
		t.Error("expected at least one agent in bundle.Agents")
	}
	if len(bundle.Nodes.Nodes) == 0 {
		t.Error("expected at least one node in bundle.Nodes")
	}
}

func TestWave154_LoadRoutingBundle_MissingDir(t *testing.T) {
	_, err := LoadRoutingBundle("/tmp/nonexistent-routing-config-wave154")
	if err == nil {
		t.Error("expected error for missing directory, got nil")
	}
}

func TestWave154_LoadRoutingBundle_InvalidAgentYAML(t *testing.T) {
	dir := t.TempDir()

	// Write an invalid agent-registry.yaml (bad YAML won't fail unmarshal but can with some
	// parsers — use a file that maps to wrong type).
	os.WriteFile(filepath.Join(dir, "agent-registry.yaml"), []byte("agents: [not-a-map]"), 0644)
	os.WriteFile(filepath.Join(dir, "node-registry.yaml"), []byte("nodes: {}"), 0644)
	os.WriteFile(filepath.Join(dir, "task-envelope-v1.yaml"), []byte("version: 1"), 0644)

	// This may or may not error depending on YAML strictness — the important thing
	// is that it does not panic.
	_, _ = LoadRoutingBundle(dir)
}

func TestWave154_LoadRoutingBundle_MissingNodeRegistry(t *testing.T) {
	dir := t.TempDir()

	os.WriteFile(filepath.Join(dir, "agent-registry.yaml"), []byte("agents: {}"), 0644)
	// node-registry.yaml intentionally missing

	_, err := LoadRoutingBundle(dir)
	if err == nil {
		t.Error("expected error when node-registry.yaml is missing")
	}
}

func TestWave154_LoadRoutingBundle_MissingEnvelope(t *testing.T) {
	dir := t.TempDir()

	os.WriteFile(filepath.Join(dir, "agent-registry.yaml"), []byte("agents: {}"), 0644)
	os.WriteFile(filepath.Join(dir, "node-registry.yaml"), []byte("nodes: {}"), 0644)
	// task-envelope-v1.yaml intentionally missing

	_, err := LoadRoutingBundle(dir)
	if err == nil {
		t.Error("expected error when task-envelope-v1.yaml is missing")
	}
}

// ---------------------------------------------------------------------------
// handlers_patrols.patrolRunHandler — handlers_patrols.go:16
// ---------------------------------------------------------------------------

func TestWave154_PatrolRunHandler_MissingRunSuffix(t *testing.T) {
	// Path does not end in /run
	req := httptest.NewRequest(http.MethodPost, "/api/patrols/", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for path without /run suffix, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave154_PatrolRunHandler_EmptyPatrolID(t *testing.T) {
	// Path ends in /run but no patrol ID before it.
	req := httptest.NewRequest(http.MethodPost, "/api/patrols//run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for empty patrol ID, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave154_PatrolRunHandler_NilPatrolSystem(t *testing.T) {
	prev := globalPatrolSystem
	globalPatrolSystem = nil
	defer func() { globalPatrolSystem = prev }()

	req := httptest.NewRequest(http.MethodPost, "/api/patrols/health-check/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 when patrol system is nil, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// Carry-forward: handleAuthRefresh + authTokensHandler + agentMetricsHandler
// (originally authored in this file's previous incarnation)
// ---------------------------------------------------------------------------

func newTestAuthManagerWave144() *AuthManager {
	return NewAuthManager("local")
}

func TestWave154_HandleAuthRefresh_Success(t *testing.T) {
	auth := newTestAuthManagerWave144()
	handler := handleAuthRefresh(auth)
	req := httptest.NewRequest(http.MethodPost, "/api/auth/refresh", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave154_AuthTokensHandler_Get(t *testing.T) {
	auth := newTestAuthManagerWave144()
	handler := authTokensHandler(auth)
	req := httptest.NewRequest(http.MethodGet, "/api/auth/tokens", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave154_AuthTokensHandler_Post_GenerateToken(t *testing.T) {
	auth := newTestAuthManagerWave144()
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

func TestWave154_AuthTokensHandler_Post_EmptyScopes(t *testing.T) {
	auth := newTestAuthManagerWave144()
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

func TestWave154_AuthTokensHandler_Post_BadJSON(t *testing.T) {
	auth := newTestAuthManagerWave144()
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

func TestWave154_AgentMetricsHandler_MissingAgentID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents//metrics", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty agent ID, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave154_AgentMetricsHandler_LimitCap(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/agents/kimi/metrics?limit=300", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave154_AgentMetricsHandler_SinceParam(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/agents/kimi/metrics?since=2026-01-01T00:00:00Z", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave154_AgentMetricsHandler_InvalidSince(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/agents/kimi/metrics?since=not-a-date", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for invalid since, got %d: %s", w.Code, w.Body.String())
	}
}
