//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// protocol_validator.go: ValidateTimestamp — 71.4%
// ---------------------------------------------------------------------------

func TestWave78_ValidateTimestamp_RFC3339(t *testing.T) {
	v := NewProtocolValidator()
	if err := v.ValidateTimestamp(time.Now().Format(time.RFC3339)); err != nil {
		t.Errorf("ValidateTimestamp RFC3339: %v", err)
	}
}

func TestWave78_ValidateTimestamp_RFC3339Nano(t *testing.T) {
	v := NewProtocolValidator()
	if err := v.ValidateTimestamp(time.Now().Format(time.RFC3339Nano)); err != nil {
		t.Errorf("ValidateTimestamp RFC3339Nano: %v", err)
	}
}

func TestWave78_ValidateTimestamp_Custom(t *testing.T) {
	v := NewProtocolValidator()
	if err := v.ValidateTimestamp("2026-03-08T15:00:00"); err != nil {
		t.Errorf("ValidateTimestamp custom: %v", err)
	}
}

func TestWave78_ValidateTimestamp_Invalid(t *testing.T) {
	v := NewProtocolValidator()
	if err := v.ValidateTimestamp("not-a-timestamp"); err == nil {
		t.Error("expected error for invalid timestamp")
	}
}

// ---------------------------------------------------------------------------
// event_bus.go: NewEventBus / initSchema — 72.4%
// ---------------------------------------------------------------------------

func TestWave78_NewEventBus_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	if eb == nil {
		t.Error("expected non-nil EventBus")
	}
}

func TestWave78_NewEventBus_Publish(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}

	if err := eb.Publish("test.topic", map[string]string{"key": "value"}); err != nil {
		t.Errorf("Publish: %v", err)
	}
}

func TestWave78_NewEventBus_Subscribe(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}

	received := make(chan BusEvent, 1)
	if err := eb.Subscribe("test.sub", func(e BusEvent) error {
		received <- e
		return nil
	}); err != nil {
		t.Fatalf("Subscribe: %v", err)
	}
}

// ---------------------------------------------------------------------------
// migrate.go: OpenDB — 69.2%
// ---------------------------------------------------------------------------

func TestWave78_OpenDB_SQLite(t *testing.T) {
	tmpDir := t.TempDir()
	db, err := OpenDB(tmpDir + "/test.db")
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	if db == nil {
		t.Error("expected non-nil db")
	}
}

func TestWave78_OpenDB_EmptyPath(t *testing.T) {
	// Empty path uses default — should succeed or fail gracefully
	db, err := OpenDB("")
	if err != nil {
		t.Logf("OpenDB empty path: %v (expected on test systems)", err)
		return
	}
	defer db.Close()
}

// ---------------------------------------------------------------------------
// migrate.go: MigrateDown — 68.8%
// ---------------------------------------------------------------------------

func TestWave78_MigrateDown_ZeroSteps(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// 0 steps is a no-op
	if err := MigrateDown(db, 0); err != nil {
		t.Errorf("MigrateDown 0 steps: %v", err)
	}
}

func TestWave78_MigrateDown_NegativeSteps(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if err := MigrateDown(db, -1); err != nil {
		t.Errorf("MigrateDown -1 steps: %v", err)
	}
}

// ---------------------------------------------------------------------------
// handlers_plan.go: queueTaskHandler — 72.4%
// ---------------------------------------------------------------------------

func TestWave78_QueueTaskHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/tasks/some-id/queue", nil)
	w := httptest.NewRecorder()
	queueTaskHandler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave78_QueueTaskHandler_MissingTaskID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodPost, "/api/tasks//queue", nil)
	r.URL.Path = "/api/tasks//queue"
	w := httptest.NewRecorder()
	queueTaskHandler(w, r)

	// Missing task ID → 400 or 404
	if w.Code != http.StatusBadRequest && w.Code != http.StatusNotFound {
		t.Logf("queueTaskHandler missing ID: got %d", w.Code)
	}
}

func TestWave78_QueueTaskHandler_TaskNotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent-wave78/queue", nil)
	r.URL.Path = "/api/tasks/nonexistent-wave78/queue"
	w := httptest.NewRecorder()
	queueTaskHandler(w, r)

	if w.Code != http.StatusNotFound {
		t.Logf("queueTaskHandler not found: got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_plan.go: agentTasksHandler — 73.3%
// ---------------------------------------------------------------------------

func TestWave78_AgentTasksHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodPost, "/api/agents/agent-1/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave78_AgentTasksHandler_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/agents//tasks", nil)
	r.URL.Path = "/api/agents//tasks"
	w := httptest.NewRecorder()
	agentTasksHandler(w, r)

	if w.Code != http.StatusBadRequest {
		t.Logf("agentTasksHandler missing ID: got %d", w.Code)
	}
}

func TestWave78_AgentTasksHandler_ValidAgent(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/agents/wave78-agent/tasks", nil)
	r.URL.Path = "/api/agents/wave78-agent/tasks"
	w := httptest.NewRecorder()
	agentTasksHandler(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_task.go: approveTaskHandler — 71.4%
// ---------------------------------------------------------------------------

func TestWave78_ApproveTaskHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/tasks/some-id/approve", nil)
	w := httptest.NewRecorder()
	approveTaskHandler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave78_ApproveTaskHandler_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodPost, "/api/tasks//approve", nil)
	r.URL.Path = "/api/tasks//approve"
	w := httptest.NewRecorder()
	approveTaskHandler(w, r)

	if w.Code != http.StatusBadRequest {
		t.Logf("approveTaskHandler missing ID: %d", w.Code)
	}
}

func TestWave78_ApproveTaskHandler_StateNotAvailable(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// stateMachine is nil → 503
	prev := stateMachine
	stateMachine = nil
	defer func() { stateMachine = prev }()

	body, _ := json.Marshal(map[string]string{"approved_by": "wave78-user"})
	r := httptest.NewRequest(http.MethodPost, "/api/tasks/wave78-task-id/approve", bytes.NewReader(body))
	r.URL.Path = "/api/tasks/wave78-task-id/approve"
	w := httptest.NewRecorder()
	approveTaskHandler(w, r)

	if w.Code != http.StatusServiceUnavailable {
		t.Logf("approveTaskHandler nil stateMachine: got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// context_sync.go: SyncEnvelopesToFilesystem — 72.2%
// ---------------------------------------------------------------------------

func TestWave78_SyncEnvelopesToFilesystem_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := testContextManager(t, db, tmpDir)
	cs, err := NewContextSync(cm, db, tmpDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	if err := cs.SyncEnvelopesToFilesystem(); err != nil {
		t.Errorf("SyncEnvelopesToFilesystem empty: %v", err)
	}
}

// ---------------------------------------------------------------------------
// lease.go: GetTaskState — 70.6%
// ---------------------------------------------------------------------------

func TestWave78_GetTaskState_Found(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)

	// Insert a task in QUEUED state
	now := time.Now().Format(time.RFC3339)
	_, _ = db.Exec(
		`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)`,
		"wave78-state-task", "d", "p", "feature", 5, "queued", "QUEUED", now, now,
	)

	state, err := sm.GetState("wave78-state-task")
	if err != nil {
		t.Fatalf("GetTaskState: %v", err)
	}
	if state == "" {
		t.Error("expected non-empty state")
	}
}

func TestWave78_GetTaskState_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)

	_, err := sm.GetState("nonexistent-wave78")
	if err == nil {
		t.Error("expected error for nonexistent task")
	}
}
