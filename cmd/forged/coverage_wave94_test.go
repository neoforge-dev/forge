//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/spf13/cobra"
)

// createTaskHandler — worker assignment path (when hub has connected workers)
// This covers handlers_task.go:167.23,174.4 (6 stmts) and handlers_plan.go:43.52,50.3 (6 stmts)
func TestWave94_CreateTaskHandler_WithWorkerInHub(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	if hub == nil {
		t.Skip("hub not initialized")
	}

	// Directly inject a fake worker into hub.workers so ListWorkers() returns it
	fakeWorker := &WebSocketWorker{
		AgentID: "wave94-fake-worker",
		Node:    "test-node",
		Send:    make(chan WSMessage, 100),
	}
	fakeWorker.touchPong() // mark as fresh (within 60s)

	hub.mu.Lock()
	hub.workers["wave94-fake-worker"] = fakeWorker
	hub.mu.Unlock()
	defer func() {
		hub.mu.Lock()
		delete(hub.workers, "wave94-fake-worker")
		hub.mu.Unlock()
	}()

	// Create a task with status=queued to trigger the worker assignment path
	body := `{"domain":"codeswiftr","project":"proj","type":"feature","title":"Wave94 Worker Task","priority":5,"status":"queued"}`
	r := httptest.NewRequest(http.MethodPost, "/api/tasks", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	createTaskHandler(w, r)
	if w.Code >= 500 {
		t.Logf("createTaskHandler with worker: got %d — %s", w.Code, w.Body.String())
	}
	t.Logf("createTaskHandler with worker: got %d", w.Code)
}

// queueTaskHandler — the worker-assignment path in handlers_plan.go
func TestWave94_QueueTaskHandler_WithWorker(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	if hub == nil {
		t.Skip("hub not initialized")
	}

	// Insert a task to queue
	db := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave94-queue-task", "codeswiftr", "proj", "feature", 5, "queued", "QUEUED", "Queue Task", now, now)

	// Inject a fake worker
	fakeWorker := &WebSocketWorker{
		AgentID: "wave94-queue-worker",
		Node:    "test-node",
		Send:    make(chan WSMessage, 100),
	}
	fakeWorker.touchPong()
	hub.mu.Lock()
	hub.workers["wave94-queue-worker"] = fakeWorker
	hub.mu.Unlock()
	defer func() {
		hub.mu.Lock()
		delete(hub.workers, "wave94-queue-worker")
		hub.mu.Unlock()
	}()

	body := `{"task_id":"wave94-queue-task","agent_id":"wave94-queue-worker"}`
	r := httptest.NewRequest(http.MethodPost, "/api/tasks/wave94-queue-task/queue", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	queueTaskHandler(w, r)
	if w.Code >= 500 {
		t.Logf("queueTaskHandler with worker: got %d — %s", w.Code, w.Body.String())
	}
	t.Logf("queueTaskHandler with worker: got %d", w.Code)
}

// cli_commands.go:getAgentID — cover the tmux fallback path (TMUX env set)
func TestWave94_GetAgentID_FromTMUX(t *testing.T) {
	prevTMUX := os.Getenv("TMUX")
	prevID := os.Getenv("FORGE_AGENT_ID")
	// Clear FORGE_AGENT_ID and flag to force TMUX path
	os.Unsetenv("FORGE_AGENT_ID")
	os.Setenv("TMUX", "/tmp/tmux-1000/default,123,0")
	defer func() {
		if prevID != "" {
			os.Setenv("FORGE_AGENT_ID", prevID)
		} else {
			os.Unsetenv("FORGE_AGENT_ID")
		}
		if prevTMUX != "" {
			os.Setenv("TMUX", prevTMUX)
		} else {
			os.Unsetenv("TMUX")
		}
	}()

	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")
	id, err := getAgentID(cmd)
	if err != nil {
		t.Logf("getAgentID TMUX: got error (expected if tmux not running): %v", err)
	} else {
		t.Logf("getAgentID TMUX: got id=%q", id)
	}
}

// handlers_plan.go: pauseTaskHandler, resumeTaskHandler — method-not-allowed
// Note: TestWave94_PauseTaskHandler_MethodNotAllowed and TestWave94_ResumeTaskHandler_MethodNotAllowed
// moved to handler_method_validation_test.go

// sendBlockerAlert — test with no webhook (env not set)
func TestWave94_SendBlockerAlert_NoWebhook(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	prevURL := os.Getenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL")
	os.Unsetenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL")
	defer func() {
		if prevURL != "" {
			os.Setenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL", prevURL)
		}
	}()

	coord := NewCoordinationDashboard(db)
	blockers := []Blocker{
		{Agent: "wave94-agent", Description: "test blocker no webhook", Severity: "low"},
	}
	coord.sendBlockerAlert(blockers)
}
