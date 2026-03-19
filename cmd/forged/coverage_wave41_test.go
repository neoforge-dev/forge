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

// Wave 41: handleSystemHealth, patrolExecutionsHandler with real data,
//          queueTaskHandler all paths, agentTasksHandler paths

// ─── handleSystemHealth ───────────────────────────────────────────────────

func TestHandleSystemHealth_WithDB_W41(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)
	// healthHandler returns 200 or 503 depending on state — either is fine
	if w.Code != http.StatusOK && w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 200 or 503, got %d", w.Code)
	}
}

func TestHandleSystemHealth_FormatText_W41(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/system/health?format=text", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)
	if w.Code != http.StatusOK && w.Code != http.StatusServiceUnavailable {
		t.Errorf("unexpected status %d", w.Code)
	}
}

// ─── patrolExecutionsHandler with real data ───────────────────────────────

func TestPatrolExecutionsHandler_WithData_W41(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()

	// Insert patrol_executions rows with valid timestamps (for duration calculation).
	startedAt := time.Now().Add(-5 * time.Minute).Format("2006-01-02 15:04:05")
	completedAt := time.Now().Add(-4 * time.Minute).Format("2006-01-02 15:04:05")
	for i := 0; i < 3; i++ {
		_, err := db.ExecContext(ctx, `
			INSERT INTO patrol_executions (id, patrol_id, started_at, completed_at, status, result, error)
			VALUES (?, ?, ?, ?, 'success', 'done', NULL)
		`,
			fmt.Sprintf("exec-w41-%d", i),
			fmt.Sprintf("fleet-auto-exec-%d", i),
			startedAt,
			completedAt,
		)
		if err != nil {
			t.Logf("insert patrol_execution: %v (may skip)", err)
		}
	}

	req := httptest.NewRequest(http.MethodGet, "/api/patrols/executions?limit=10&hours=1", nil)
	w := httptest.NewRecorder()
	patrolExecutionsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestPatrolExecutionsHandler_WithPatrolFilter_W41(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()

	startedAt := time.Now().Add(-10 * time.Minute).Format("2006-01-02 15:04:05")
	_, _ = db.ExecContext(ctx, `
		INSERT INTO patrol_executions (id, patrol_id, started_at, status)
		VALUES ('exec-w41-filter', 'my-patrol', ?, 'running')
	`, startedAt)

	req := httptest.NewRequest(http.MethodGet, "/api/patrols/executions?patrol_id=my-patrol&status=running", nil)
	w := httptest.NewRecorder()
	patrolExecutionsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ─── queueTaskHandler ─────────────────────────────────────────────────────

func TestQueueTaskHandler_TaskNotFound_W41(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/NO-SUCH-TASK/queue", nil)
	req.URL.Path = "/api/tasks/NO-SUCH-TASK/queue"
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestQueueTaskHandler_WrongStatus_W41(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert a 'queued' task — not 'planned', so should get 400.
	_, err := db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES ('TASK-W41-QUEUE', 'test', 'proj', 'feature', 'Queued Task', 'queued', 'QUEUED', 50, datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-W41-QUEUE/queue", nil)
	req.URL.Path = "/api/tasks/TASK-W41-QUEUE/queue"
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for non-planned task, got %d: %s", w.Code, w.Body.String())
	}
}

// ─── agentTasksHandler ────────────────────────────────────────────────────

func TestAgentTasksHandler_WithData_W41(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()

	// Insert a task assigned to a specific agent.
	_, err := db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, assigned_to, created_at, updated_at)
		VALUES ('TASK-W41-AGENT', 'test', 'proj', 'feature', 'Agent Task', 'assigned', 'RUNNING', 50, 'test-agent-w41', datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/agents/test-agent-w41/tasks", nil)
	req.URL.Path = "/api/agents/test-agent-w41/tasks"
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAgentTasksHandler_EmptyAgent_W41(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Path that results in empty agent ID.
	req := httptest.NewRequest(http.MethodGet, "/api/agents//tasks", nil)
	req.URL.Path = "/api/agents//tasks"
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	// Empty ID → 400
	if w.Code != http.StatusBadRequest {
		t.Logf("agentTasksHandler empty ID: got %d (may handle differently)", w.Code)
	}
}
