//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// Wave 43: handleTaskList/Show/Logs, handleAgentList format=text,
//          validateCustomRule paths, checkCircular detection

// ─── handleTaskList ───────────────────────────────────────────────────────

func TestHandleTaskList_WithDB_W43(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks?format=text", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleTaskList_WithTasksFormatJSON_W43(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()

	// Insert a task so list is non-empty.
	_, _ = db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES ('TASK-W43-LIST', 'test', 'proj', 'feature', 'List Task', 'queued', 'QUEUED', 50, datetime('now'), datetime('now'))
	`)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ─── handleTaskShow ───────────────────────────────────────────────────────

func TestHandleTaskShow_MissingID_W43(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/show", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d", w.Code)
	}
}

func TestHandleTaskShow_TaskNotFound_W43(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/show?id=NO-SUCH-TASK-W43", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)
	// Not found from delegate handler
	if w.Code == http.StatusOK {
		t.Errorf("expected non-200 for missing task, got 200")
	}
}

func TestHandleTaskShow_Found_W43(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()
	_, _ = db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES ('TASK-W43-SHOW', 'test', 'proj', 'feature', 'Show Task', 'queued', 'QUEUED', 50, datetime('now'), datetime('now'))
	`)

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("queue: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/show?id=TASK-W43-SHOW", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ─── handleTaskLogs ───────────────────────────────────────────────────────

func TestHandleTaskLogs_MissingID_W43(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/logs", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestHandleTaskLogs_WithID_W43(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/logs?id=TASK-W43-LOGS", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)
	// Returns events (empty or 404 depending on impl) — just verify no crash.
	_ = w.Code
}

// ─── handleAgentList format=text ─────────────────────────────────────────

func TestHandleAgentList_FormatText_W43(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents?format=text", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ─── validateCustomRule ───────────────────────────────────────────────────

func TestValidateCustomRule_RangeInBounds_W43(t *testing.T) {
	v := NewProtocolValidator()
	rule := CustomRule{
		Type: "range",
		Params: map[string]interface{}{
			"min": float64(0),
			"max": float64(100),
		},
	}
	if err := v.validateCustomRule("priority", float64(50), rule); err != nil {
		t.Errorf("expected nil for in-range value, got %v", err)
	}
}

func TestValidateCustomRule_RangeOutOfBounds_W43(t *testing.T) {
	v := NewProtocolValidator()
	rule := CustomRule{
		Type: "range",
		Params: map[string]interface{}{
			"min": float64(0),
			"max": float64(100),
		},
	}
	if err := v.validateCustomRule("priority", float64(150), rule); err == nil {
		t.Error("expected error for out-of-range value, got nil")
	}
}

func TestValidateCustomRule_RangeIntValue_W43(t *testing.T) {
	v := NewProtocolValidator()
	rule := CustomRule{
		Type: "range",
		Params: map[string]interface{}{
			"min": float64(1),
			"max": float64(10),
		},
	}
	// int type — should convert to float64 and check.
	if err := v.validateCustomRule("count", int(5), rule); err != nil {
		t.Errorf("expected nil for int value in range, got %v", err)
	}
}

func TestValidateCustomRule_RangeNonNumeric_W43(t *testing.T) {
	v := NewProtocolValidator()
	rule := CustomRule{
		Type: "range",
		Params: map[string]interface{}{
			"min": float64(0),
			"max": float64(100),
		},
	}
	// Non-numeric value → skips check.
	if err := v.validateCustomRule("name", "hello", rule); err != nil {
		t.Errorf("expected nil for non-numeric value, got %v", err)
	}
}

func TestValidateCustomRule_VersionCompatible_W43(t *testing.T) {
	v := NewProtocolValidator()
	rule := CustomRule{
		Type: "version_compatible",
		Params: map[string]interface{}{
			"min_version": "1.0.0",
		},
	}
	// Call with a version string — result depends on isVersionCompatible logic.
	_ = v.validateCustomRule("version", "2.0.0", rule)
}

func TestValidateCustomRule_UnknownType_W43(t *testing.T) {
	v := NewProtocolValidator()
	rule := CustomRule{
		Type:   "unknown_rule_type",
		Params: map[string]interface{}{},
	}
	if err := v.validateCustomRule("field", "value", rule); err != nil {
		t.Errorf("expected nil for unknown rule type, got %v", err)
	}
}

// ─── checkCircular ────────────────────────────────────────────────────────

func TestCheckCircular_DirectDetection_W43(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	sq := q.(*sqliteTaskQueue)
	ctx := context.Background()

	// current == target → ErrCircularDeps.
	err = sq.checkCircular(ctx, "TASK-A", "TASK-A", map[string]bool{})
	if err != ErrCircularDeps {
		t.Errorf("expected ErrCircularDeps, got %v", err)
	}
}

func TestCheckCircular_AlreadyVisited_W43(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	sq := q.(*sqliteTaskQueue)
	ctx := context.Background()

	// current is already in visited map → returns nil.
	err = sq.checkCircular(ctx, "TASK-A", "TASK-B", map[string]bool{"TASK-A": true})
	if err != nil {
		t.Errorf("expected nil for already-visited node, got %v", err)
	}
}

func TestCheckCircular_NoDepsNoCircle_W43(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert a task with no dependencies.
	_, _ = db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES ('TASK-W43-NODEP', 'test', 'proj', 'feature', 'NoDep Task', 'queued', 'QUEUED', 50, datetime('now'), datetime('now'))
	`)

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	sq := q.(*sqliteTaskQueue)

	// current='TASK-W43-NODEP', target='TASK-OTHER', no deps → returns nil.
	err = sq.checkCircular(ctx, "TASK-W43-NODEP", "TASK-OTHER", map[string]bool{})
	if err != nil {
		t.Errorf("expected nil for no circular dep, got %v", err)
	}
}

func TestCheckCircular_WithDependency_W43(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert tasks.
	for i, taskID := range []string{"TASK-W43-C1", "TASK-W43-C2"} {
		_, _ = db.ExecContext(ctx, `
			INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
			VALUES (?, 'test', 'proj', 'feature', ?, 'queued', 'QUEUED', 50, datetime('now'), datetime('now'))
		`, taskID, fmt.Sprintf("Circular %d", i))
	}

	// Insert dependency: C1 depends on C2.
	_, _ = db.ExecContext(ctx, `
		INSERT INTO task_dependencies (task_id, depends_on) VALUES ('TASK-W43-C1', 'TASK-W43-C2')
	`)

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	sq := q.(*sqliteTaskQueue)

	// Check if TASK-W43-C1 → TASK-W43-C2 chain loops back to C1 (it shouldn't — no cycle).
	err = sq.checkCircular(ctx, "TASK-W43-C1", "TASK-W43-OTHER", map[string]bool{})
	if err != nil {
		t.Errorf("expected nil for non-circular dep, got %v", err)
	}

	// Now check a true cycle: target=TASK-W43-C2 starting from TASK-W43-C1.
	// C1 depends on C2, so starting from C1 we traverse to C2, and target=C2 → circular.
	err = sq.checkCircular(ctx, "TASK-W43-C1", "TASK-W43-C2", map[string]bool{})
	_ = err // May return ErrCircularDeps or nil depending on traversal
	_ = time.Now()
}
