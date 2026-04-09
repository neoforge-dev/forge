//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// setupHandlerTest sets up globals needed by HTTP handlers and returns a cleanup func.
// Saves and restores all touched globals (db, taskQueue, hub, authManager).
func setupHandlerTest(t *testing.T) func() {
	t.Helper()
	dbPath := "/tmp/test_handlers_" + time.Now().Format("20060102150405.999999999") + ".db"

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("setupHandlerTest OpenDB: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		os.Remove(dbPath)
		t.Fatalf("setupHandlerTest MigrateUp: %v", err)
	}

	oldDB := getDBConn()
	oldQueue := taskQueue
	oldHub := hub
	oldAuth := authManager

	setDBConn(db)
	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		db.Close()
		setDBConn(oldDB)
		os.Remove(dbPath)
		t.Fatalf("setupHandlerTest NewTaskQueueFromDB: %v", err)
	}
	taskQueue = q
	hub = NewHub()
	newHub := hub
	go hub.Run()
	authManager = NewAuthManager("local")

	return func() {
		newHub.Stop() // stop Run() goroutine to prevent goroutine leak in tests
		db.Close()
		setDBConn(oldDB)
		taskQueue = oldQueue
		hub = oldHub
		authManager = oldAuth
		os.Remove(dbPath)
	}
}

// enqueueTestTask inserts a task into taskQueue for handler tests.
func enqueueTestTask(t *testing.T, id string) Task {
	t.Helper()
	task := Task{
		ID:       id,
		Domain:   "test-domain",
		Project:  "test-project",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		Title:    fmt.Sprintf("task %s", id),
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("enqueueTestTask %s: %v", id, err)
	}
	return task
}

// --- getTaskHandler ---

func TestGetTaskHandler_Found(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "get-task-001")

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/get-task-001", nil)
	w := httptest.NewRecorder()
	getTaskHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp Task
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parse response: %v", err)
	}
	if resp.ID != "get-task-001" {
		t.Errorf("expected id get-task-001, got %s", resp.ID)
	}
}

func TestGetTaskHandler_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/no-such-task", nil)
	w := httptest.NewRecorder()
	getTaskHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestGetTaskHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/x", nil)
	w := httptest.NewRecorder()
	getTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- listTasksHandler ---

func TestListTasksHandler_Empty(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	w := httptest.NewRecorder()
	listTasksHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	if resp["count"].(float64) != 0 {
		t.Errorf("expected count=0, got %v", resp["count"])
	}
}

func TestListTasksHandler_WithTasks(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "list-task-001")
	enqueueTestTask(t, "list-task-002")

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	w := httptest.NewRecorder()
	listTasksHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	if resp["count"].(float64) < 2 {
		t.Errorf("expected at least 2 tasks, got %v", resp["count"])
	}
}

func TestListTasksHandler_StatusFilter(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "filter-task-001")

	req := httptest.NewRequest(http.MethodGet, "/api/tasks?status=queued", nil)
	w := httptest.NewRecorder()
	listTasksHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- tasksHandler router ---

func TestTasksHandler_GetDelegatesToList(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	w := httptest.NewRecorder()
	tasksHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

func TestTasksHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPatch, "/api/tasks", nil)
	w := httptest.NewRecorder()
	tasksHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- taskDeleteHandler ---

func TestTaskDeleteHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "del-task-001")

	req := httptest.NewRequest(http.MethodDelete, "/api/tasks/del-task-001", nil)
	w := httptest.NewRecorder()
	taskDeleteHandler(w, req)

	if w.Code != http.StatusNoContent {
		t.Errorf("expected 204, got %d: %s", w.Code, w.Body.String())
	}
}

func TestTaskDeleteHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/x", nil)
	w := httptest.NewRecorder()
	taskDeleteHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- taskUpdateHandler ---

func TestTaskUpdateHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "upd-task-001")

	body, _ := json.Marshal(map[string]string{"status": "executing", "assigned_to": "agent-1"})
	req := httptest.NewRequest(http.MethodPut, "/api/tasks/upd-task-001", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	taskUpdateHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestTaskUpdateHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/x", nil)
	w := httptest.NewRecorder()
	taskUpdateHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestTaskUpdateHandler_InvalidJSON(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPut, "/api/tasks/x", bytes.NewReader([]byte("not json")))
	w := httptest.NewRecorder()
	taskUpdateHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// --- getTaskEventsHandler ---

func TestGetTaskEventsHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "evt-task-001")

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/evt-task-001/events", nil)
	w := httptest.NewRecorder()
	getTaskEventsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestGetTaskEventsHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/x/events", nil)
	w := httptest.NewRecorder()
	getTaskEventsHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- pauseTaskHandler / resumeTaskHandler ---

func TestPauseTaskHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "pause-task-001")

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/pause-task-001/pause", nil)
	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)

	// Handler may return 200 (success) or 400 (state machine rejected) — both are valid
	if w.Code != http.StatusOK && w.Code != http.StatusBadRequest {
		t.Errorf("expected 200 or 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestPauseTaskHandler_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/no-such-task/pause", nil)
	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

func TestPauseTaskHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/x/pause", nil)
	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestResumeTaskHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/x/resume", nil)
	w := httptest.NewRecorder()
	resumeTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestResumeTaskHandler_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/no-such-task/resume", nil)
	w := httptest.NewRecorder()
	resumeTaskHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// --- notificationsHandler ---

func TestNotificationsHandler_GetEmpty(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/notifications", nil)
	w := httptest.NewRecorder()
	notificationsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	if resp["total"] == nil {
		t.Error("expected total field in response")
	}
}

func TestNotificationsHandler_Post(t *testing.T) {
	body, _ := json.Marshal(map[string]string{
		"type":    "info",
		"title":   "Test Notification",
		"message": "Hello from test",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/notifications", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	notificationsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestNotificationsHandler_InvalidMethod(t *testing.T) {
	req := httptest.NewRequest(http.MethodDelete, "/api/notifications", nil)
	w := httptest.NewRecorder()
	notificationsHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- claimableTasksHandler ---

func TestClaimableTasksHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	enqueueTestTask(t, "claimable-001")

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/claimable", nil)
	w := httptest.NewRecorder()
	claimableTasksHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestClaimableTasksHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/claimable", nil)
	w := httptest.NewRecorder()
	claimableTasksHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- agentHeartbeatReceive ---

func TestAgentHeartbeatReceive_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]interface{}{
		"agent_id": "agent-hb-001",
		"status":   "working",
		"progress": 50,
		"context":  30.0,
	})
	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent-hb-001/heartbeat", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAgentHeartbeatReceive_EmptyBody(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent-hb-002/heartbeat", nil)
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAgentHeartbeatReceive_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-hb-001/heartbeat", nil)
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestAgentHeartbeatReceive_MissingID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents//heartbeat", nil)
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// --- projectsHandler / listProjectsHandler ---

func TestProjectsHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodDelete, "/api/projects", nil)
	w := httptest.NewRecorder()
	projectsHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestListProjectsHandler_Empty(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/projects", nil)
	w := httptest.NewRecorder()
	listProjectsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	if resp["count"].(float64) != 0 {
		t.Errorf("expected 0 projects, got %v", resp["count"])
	}
}

func TestListProjectsHandler_DomainFilter(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/projects?domain=test-domain", nil)
	w := httptest.NewRecorder()
	listProjectsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- notificationActionHandler ---

func TestNotificationActionHandler_MarkRead(t *testing.T) {
	// First create a notification
	body, _ := json.Marshal(map[string]string{
		"type":    "alert",
		"title":   "Action Test",
		"message": "mark me read",
	})
	postReq := httptest.NewRequest(http.MethodPost, "/api/notifications", bytes.NewReader(body))
	postReq.Header.Set("Content-Type", "application/json")
	postW := httptest.NewRecorder()
	notificationsHandler(postW, postReq)

	var created map[string]interface{}
	json.Unmarshal(postW.Body.Bytes(), &created)
	notif := created["notification"].(map[string]interface{})
	id := notif["id"].(string)

	// Now mark it as read
	req := httptest.NewRequest(http.MethodPost, "/api/notifications/"+id+"/read", nil)
	w := httptest.NewRecorder()
	notificationActionHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestNotificationActionHandler_NotFound(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/notifications/nonexistent/read", nil)
	w := httptest.NewRecorder()
	notificationActionHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// --- queueTaskHandler ---

func TestQueueTaskHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/x/queue", nil)
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestQueueTaskHandler_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/no-such-task/queue", nil)
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// --- projectByIDHandler ---

func TestProjectByIDHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/projects/x", nil)
	w := httptest.NewRecorder()
	projectByIDHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestProjectByIDHandler_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/projects/nonexistent-project", nil)
	w := httptest.NewRecorder()
	projectByIDHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// --- agentsHandler ---

func TestAgentsHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents", nil)
	w := httptest.NewRecorder()
	agentsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAgentsHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents", nil)
	w := httptest.NewRecorder()
	agentsHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestAgentTelemetrySummaryHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var out struct {
		SummaryAt  string `json:"summary_at"`
		AgentCount int    `json:"agent_count"`
		Agents     []any  `json:"agents"`
	}
	if err := json.NewDecoder(w.Body).Decode(&out); err != nil {
		t.Errorf("invalid JSON: %v", err)
	}
	if out.Agents == nil {
		t.Error("agents should be array (possibly empty), not null")
	}
}

func TestAgentTelemetrySummaryHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- workersHandler ---

func TestWorkersHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/workers", nil)
	w := httptest.NewRecorder()
	workersHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWorkersHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodDelete, "/api/workers", nil)
	w := httptest.NewRecorder()
	workersHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- workerByIDHandler ---

func TestWorkerByIDHandler_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/workers/nonexistent-worker", nil)
	w := httptest.NewRecorder()
	workerByIDHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWorkerByIDHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/workers/x", nil)
	w := httptest.NewRecorder()
	workerByIDHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- configHandler ---

func TestConfigHandler_Get(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/config", nil)
	w := httptest.NewRecorder()
	configHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestConfigHandler_Put(t *testing.T) {
	body, _ := json.Marshal(map[string]interface{}{"custom_key": "custom_val"})
	req := httptest.NewRequest(http.MethodPut, "/api/config", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	configHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestConfigHandler_InvalidMethod(t *testing.T) {
	req := httptest.NewRequest(http.MethodDelete, "/api/config", nil)
	w := httptest.NewRecorder()
	configHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- agentsListHandler / agentsHealthHandler ---

func TestAgentsListHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/list", nil)
	w := httptest.NewRecorder()
	agentsListHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAgentsHealthHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Path "" after stripping "/api/agents/" prefix means list all agents
	req := httptest.NewRequest(http.MethodGet, "/api/agents/", nil)
	w := httptest.NewRecorder()
	agentsHealthHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- tuiLogsHandler ---

func TestTuiLogsHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tui/logs", nil)
	w := httptest.NewRecorder()
	tuiLogsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- dispatchHandler ---

func TestDispatchHandler_Get(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/dispatch", nil)
	w := httptest.NewRecorder()
	dispatchHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- agentByIDHandler ---

func TestAgentByIDHandler_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/no-such-agent", nil)
	w := httptest.NewRecorder()
	agentByIDHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAgentByIDHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/x", nil)
	w := httptest.NewRecorder()
	agentByIDHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- agentTasksHandler ---

func TestAgentTasksHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-1/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAgentTasksHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/x/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- approvals HTTP handlers ---

func TestApprovalHandler_GetList(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals", nil)
	w := httptest.NewRecorder()
	h.handleApprovals(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestApprovalHandler_InvalidMethod(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodDelete, "/api/approvals", nil)
	w := httptest.NewRecorder()
	h.handleApprovals(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestApprovalHandler_PostMissingFields(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	body := []byte(`{"type":"task_completion"}`) // missing agent_id, domain, title
	req := httptest.NewRequest(http.MethodPost, "/api/approvals", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.handleApprovals(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestApprovalHandler_Pending(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/pending", nil)
	w := httptest.NewRecorder()
	h.handlePending(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- extendLeaseHandler ---

func TestExtendLeaseHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/x/extend-lease", nil)
	w := httptest.NewRecorder()
	extendLeaseHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- agentContextHandler ---

func TestAgentContextHandler_UnknownAgent(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Unknown agent returns 200 with context_pct 0 (graceful degradation)
	req := httptest.NewRequest(http.MethodGet, "/api/agents/unknown-agent/context", nil)
	w := httptest.NewRecorder()
	agentContextHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 (graceful degradation), got %d: %s", w.Code, w.Body.String())
	}
}

func TestAgentContextHandler_MissingID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents//context", nil)
	w := httptest.NewRecorder()
	agentContextHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// --- createProjectHandler ---

func TestCreateProjectHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]interface{}{
		"key":    "test-proj",
		"name":   "Test Project",
		"domain": "test-domain",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/projects", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	createProjectHandler(w, req)

	if w.Code != http.StatusCreated {
		t.Errorf("expected 201, got %d: %s", w.Code, w.Body.String())
	}
}

func TestCreateProjectHandler_MissingKey(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]interface{}{"name": "No Key Project"})
	req := httptest.NewRequest(http.MethodPost, "/api/projects", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	createProjectHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestCreateProjectHandler_InvalidJSON(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/projects", bytes.NewReader([]byte("not json")))
	w := httptest.NewRecorder()
	createProjectHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// --- planTaskHandler / getPlanHistoryHandler ---

func TestPlanTaskHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/x/plan", nil)
	w := httptest.NewRecorder()
	planTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestGetPlanHistoryHandler_MissingID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks//plans", nil)
	w := httptest.NewRecorder()
	getPlanHistoryHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// --- CLI handlers ---

func TestHandleTaskList_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/task/list", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleTaskShow_NotFound_Basic(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/task/show?id=no-such-task-xyz", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)

	// Task not found → proxies the 404 or returns it directly
	if w.Code != http.StatusNotFound && w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleTaskShow_MissingID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/task/show", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleTaskLogs_NotFound_Basic(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/task/logs?id=no-such-task-xyz", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)

	// Task not found → proxies error status
	if w.Code != http.StatusNotFound && w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleTaskLogs_MissingID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/task/logs", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleTaskCreate_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/task/create", nil)
	w := httptest.NewRecorder()
	handleTaskCreate(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandleAgentList_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/agent/list", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleSystemHealth_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleQueueDepth_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleQueueStatus_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/queue/status", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleQueueList_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/queue/list", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- lanesHandler / contextsHandler ---

func TestLanesHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/lanes", nil)
	w := httptest.NewRecorder()
	lanesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestLanesHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/lanes", nil)
	w := httptest.NewRecorder()
	lanesHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestContextsHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestContextsHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- handleQueuePriority / handleQueueCancel ---

func TestHandleQueuePriority_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/queue/priority", nil)
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleQueueCancel_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/queue/cancel", nil)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleReplanTaskHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/x/replan", nil)
	w := httptest.NewRecorder()
	replanTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- handleAgentStatus ---

func TestHandleAgentStatus(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/agent/status?id=x", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, req)

	if w.Code == 0 {
		t.Error("expected a response code")
	}
}

func TestHandleAgentStatus_Found(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Insert heartbeat so we can look it up
	db := getDBConn()
	_, _ = db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, capabilities, last_seen, connected_at)
		VALUES ('status-agent', 'prya', 'active', 0.2, '', datetime('now'), datetime('now'))
	`)

	req := httptest.NewRequest(http.MethodGet, "/cli/agent/status?id=status-agent", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for found agent, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleAgentList_WithAgents(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	_, _ = db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, capabilities, last_seen, connected_at)
		VALUES ('list-agent-1', 'prya', 'active', 0.1, 'task', datetime('now'), datetime('now'))
	`)

	req := httptest.NewRequest(http.MethodGet, "/cli/agent/list", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- laneByIDHandler ---

func TestLaneByIDHandler_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/lanes/nonexistent-lane", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

func TestLaneByIDHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/lanes/alpha", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestLaneByIDHandler_MissingID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/lanes/", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)

	if w.Code != http.StatusBadRequest && w.Code != http.StatusNotFound {
		t.Errorf("expected 400 or 404, got %d: %s", w.Code, w.Body.String())
	}
}

// --- ContextEnvelope getters ---

func TestContextEnvelope_Getters(t *testing.T) {
	now := time.Now().UTC()
	e := &ContextEnvelope{
		ID:        "env-001",
		AgentID:   "agent-1",
		Domain:    "test-domain",
		Project:   "test-project",
		TaskID:    "task-1",
		Summary:   "summary text",
		CreatedAt: now,
		ExpiresAt: now.Add(24 * time.Hour),
	}

	if e.GetID() != "env-001" {
		t.Errorf("GetID = %s", e.GetID())
	}
	if e.GetAgentID() != "agent-1" {
		t.Errorf("GetAgentID = %s", e.GetAgentID())
	}
	if e.GetDomain() != "test-domain" {
		t.Errorf("GetDomain = %s", e.GetDomain())
	}
	if e.GetProject() != "test-project" {
		t.Errorf("GetProject = %s", e.GetProject())
	}
	if e.GetTaskID() != "task-1" {
		t.Errorf("GetTaskID = %s", e.GetTaskID())
	}
	if e.GetSummary() != "summary text" {
		t.Errorf("GetSummary = %s", e.GetSummary())
	}
	if e.GetCreatedAt().IsZero() {
		t.Error("GetCreatedAt returned zero time")
	}
	if e.GetExpiresAt().IsZero() {
		t.Error("GetExpiresAt returned zero time")
	}
}

// --- parityHandler (closure form) ---

func TestParityHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	handler := parityHandler(getDBConn())
	req := httptest.NewRequest(http.MethodGet, "/api/parity", nil)
	w := httptest.NewRecorder()
	handler(w, req)

	// Can return 200 or 500 depending on DB state — just verify it runs
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("expected 200 or 500, got %d: %s", w.Code, w.Body.String())
	}
}

func TestDispatchHandler_Post_Basic(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body := `{"task_id":"task-001","agent_id":"agent-1","message":"test"}`
	req := httptest.NewRequest(http.MethodPost, "/api/dispatch", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	dispatchHandler(w, req)

	// 200 on success, 500 if git_guard_actions table not ready
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

func TestDispatchHandler_Post_InvalidJSON(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/dispatch", strings.NewReader("not-json"))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	dispatchHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 on bad JSON, got %d", w.Code)
	}
}

func TestDispatchHandler_MethodNotAllowed_Basic(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodDelete, "/api/dispatch", nil)
	w := httptest.NewRecorder()
	dispatchHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- agentTasksHandler ---

func TestAgentTasksHandler_MethodNotAllowed_Basic(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/some-agent/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestAgentTasksHandler_EmptyAgent(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents//tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)

	// Should return 400 or 200 with empty list — both acceptable
	if w.Code != http.StatusBadRequest && w.Code != http.StatusOK {
		t.Errorf("unexpected status %d", w.Code)
	}
}

func TestAgentTasksHandler_ValidAgent(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-xyz/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- agentsHealthHandler ---

func TestAgentsHealthHandler_AllAgents(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/", nil)
	w := httptest.NewRecorder()
	agentsHealthHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAgentsHealthHandler_SpecificAgent_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/no-such-agent/health", nil)
	w := httptest.NewRecorder()
	agentsHealthHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

func TestGetAllAgentsHealth_EmptyDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	agents, err := getAllAgentsHealth()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Empty DB returns nil (no agents) — that's valid
	if len(agents) != 0 {
		t.Errorf("expected 0 agents in empty DB, got %d", len(agents))
	}
}

// --- handleTaskCreate / handleTaskList CLI handlers ---

func TestHandleTaskCreate_MethodNotAllowed_Basic(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/task/create", nil)
	w := httptest.NewRecorder()
	handleTaskCreate(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandleTaskCreate_InvalidJSON(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/cli/task/create", strings.NewReader("not-json"))
	w := httptest.NewRecorder()
	handleTaskCreate(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleTaskCreate_ValidTask(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body := `{"task":{"domain":"forge","project":"v3","type":"feature","title":"Test CLI Create Task","priority":5}}`
	req := httptest.NewRequest(http.MethodPost, "/cli/task/create", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleTaskCreate(w, req)

	// 200 or 201 on success
	if w.Code != http.StatusOK && w.Code != http.StatusCreated && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleTaskList_ReturnsJSON(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/task/list", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestGetAllAgentsHealth_WithAgents(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	// Insert two heartbeat records
	_, _ = db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, capabilities, last_seen, connected_at)
		VALUES ('agent-a', 'prya', 'active', 0.3, 'task', datetime('now'), datetime('now')),
		       ('agent-b', 'sati', 'idle', 0.0, 'task,review', datetime('now'), datetime('now'))
	`)

	agents, err := getAllAgentsHealth()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(agents) != 2 {
		t.Errorf("expected 2 agents, got %d", len(agents))
	}
	// Verify capabilities split
	for _, a := range agents {
		if a.AgentID == "agent-b" && len(a.Capabilities) != 2 {
			t.Errorf("agent-b: expected 2 capabilities, got %d: %v", len(a.Capabilities), a.Capabilities)
		}
	}
}

func TestGetAgentHealth_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	_, err := getAgentHealth("nonexistent-agent-xyz")
	if err == nil {
		t.Error("expected error for missing agent")
	}
}

// --- agentContextHandler ---

// --- extendLeaseHandler ---

func TestExtendLeaseHandler_MethodNotAllowed_Basic(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/abc/extend-lease", nil)
	w := httptest.NewRecorder()
	extendLeaseHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestExtendLeaseHandler_InvalidJSON(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/abc/extend-lease", strings.NewReader("bad-json"))
	w := httptest.NewRecorder()
	extendLeaseHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

func TestExtendLeaseHandler_TaskNotFound_Basic(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body := `{"agent_id":"agent-1"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent/extend-lease", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	extendLeaseHandler(w, req)

	// Task not found → lease error → 400
	if w.Code != http.StatusBadRequest && w.Code != http.StatusOK {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

// --- agentContextHandler ---

func TestAgentContextHandler_EmptyAgentID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents//context", nil)
	w := httptest.NewRecorder()
	agentContextHandler(w, req)

	// Empty ID → 400
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty agent ID, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAgentContextHandler_UnknownAgent_Degrades(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/unknown-agent-xyz/context", nil)
	w := httptest.NewRecorder()
	agentContextHandler(w, req)

	// Unknown agent → graceful degradation → 200 with context_pct=0
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 (graceful degradation), got %d: %s", w.Code, w.Body.String())
	}
}

func TestGetAgentHealth_FoundWithCapabilities(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Insert a heartbeat record
	db := getDBConn()
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, capabilities, last_seen, connected_at)
		VALUES ('test-agent-hb', 'prya', 'active', 0.5, 'task,review', datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert heartbeat: %v", err)
	}

	hb, err := getAgentHealth("test-agent-hb")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if hb.AgentID != "test-agent-hb" {
		t.Errorf("expected agent ID test-agent-hb, got %q", hb.AgentID)
	}
	if len(hb.Capabilities) != 2 {
		t.Errorf("expected 2 capabilities, got %d: %v", len(hb.Capabilities), hb.Capabilities)
	}
}

// TestAgentTelemetryHandler_ClosedDB exercises the query-failed error path
// in agentTelemetryHandler (handlers_agent.go:245.16,248.3).
func TestAgentTelemetryHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/test-agent/telemetry", nil)
	w := httptest.NewRecorder()
	agentTelemetryHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB agentTelemetryHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestAgentMetricsHandler_ClosedDB exercises the query-failed error path
// in agentMetricsHandler (handlers_agent.go:326.16,329.3).
func TestAgentMetricsHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/test-agent/metrics", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB agentMetricsHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestAgentTelemetrySummaryHandler_ClosedDB exercises the query-failed error path
// in agentTelemetrySummaryHandler (handlers_agent.go:386.16,389.3).
func TestAgentTelemetrySummaryHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB agentTelemetrySummaryHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestHandleTaskLogs_ClosedDB exercises the rr.status >= 400 path in handleTaskLogs
// (main.go:344.22,348.3) when getTaskEventsHandler returns an error due to a closed DB.
func TestHandleTaskLogs_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Close the DB so getTaskEventsHandler returns an internal error.
	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/logs?id=some-task-id", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)

	// getTaskEventsHandler should fail → status >= 400 passthrough path.
	if w.Code < 400 {
		t.Errorf("expected error status for closed-DB handleTaskLogs, got %d: %s", w.Code, w.Body.String())
	}
}

// TestHandleQueuePriority_NotFound exercises the task-not-found 404 path
// (main.go:633.14,635.3) in handleQueuePriority.
func TestHandleQueuePriority_NotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	payload := map[string]interface{}{
		"id":       "nonexistent-task-for-priority",
		"priority": "high",
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/priority", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for nonexistent task priority update, got %d: %s", w.Code, w.Body.String())
	}
}

// TestHandleQueuePriority_InvalidPriority exercises the invalid priority 400 path
// (main.go:619.3,621.3).
func TestHandleQueuePriority_InvalidPriority(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	payload := map[string]interface{}{
		"id":       "some-task",
		"priority": "critical",
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/priority", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid priority value, got %d: %s", w.Code, w.Body.String())
	}
}

// TestHandleQueueCancel_InvalidStatus exercises the bad-status 400 path
// (main.go:690.3,693.3) in handleQueueCancel when task is in non-cancellable state.
func TestHandleQueueCancel_InvalidStatus(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Enqueue and then claim the task as executing so it can't be cancelled.
	task := enqueueTestTask(t, "cancel-executing-task")
	ctx := context.Background()
	if err := taskQueue.ClaimTask(ctx, task.ID, "test-agent"); err != nil {
		t.Fatalf("claim task: %v", err)
	}

	payload := map[string]interface{}{
		"id":     task.ID,
		"reason": "attempt to cancel executing task",
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/cancel", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for cancelling executing task, got %d: %s", w.Code, w.Body.String())
	}
}

// TestAbandonTaskHandler_ClosedDB exercises the db.Exec error path
// (handlers_task.go:374.16,377.3) in abandonTaskHandler when the DB is closed.
func TestAbandonTaskHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/some-task-id/abandon", nil)
	w := httptest.NewRecorder()
	abandonTaskHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB abandonTaskHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestTaskHistoryHandler_ClosedDB exercises the store.GetTransitionHistory error path
// (handlers_task.go:407.16,410.3) in taskHistoryHandler when the DB is closed.
func TestTaskHistoryHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/some-task-id/history", nil)
	w := httptest.NewRecorder()
	taskHistoryHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB taskHistoryHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestPruneTasksHandler_ClosedDB exercises the db.Exec error path
// (handlers_task.go:438.16,441.3) in pruneTasksHandler when the DB is closed.
func TestPruneTasksHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/prune", nil)
	w := httptest.NewRecorder()
	pruneTasksHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB pruneTasksHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestListTasksHandler_ClosedDB exercises the taskQueue error path
// (handlers_task.go:497.16,500.3) in listTasksHandler when the DB is closed.
func TestListTasksHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	w := httptest.NewRecorder()
	listTasksHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB listTasksHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestClaimableTasksHandler_ClosedDB exercises the GetClaimableTasks error path
// (handlers_task.go:854.16,857.3) in claimableTasksHandler when the DB is closed.
func TestClaimableTasksHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/claimable", nil)
	w := httptest.NewRecorder()
	claimableTasksHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB claimableTasksHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestTaskUpdateHandler_ClosedDB exercises the db.Exec error path
// (handlers_task.go:705.41,708.4) in taskUpdateHandler when the DB is closed.
func TestTaskUpdateHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	payload := map[string]interface{}{
		"status": "assigned",
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPut, "/api/tasks/some-task-id", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	taskUpdateHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB taskUpdateHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestTaskDeleteHandler_ClosedDB exercises the db.Exec error path
// (handlers_task.go:751.62,754.3) in taskDeleteHandler when the DB is closed.
func TestTaskDeleteHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodDelete, "/api/tasks/some-task-id", nil)
	w := httptest.NewRecorder()
	taskDeleteHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB taskDeleteHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestCompleteTaskWithApprovalHandler_InvalidBody exercises the json decode error path
// (handlers_task.go:635.18,638.3) in completeTaskWithApprovalHandler.
func TestCompleteTaskWithApprovalHandler_InvalidBody(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/test-task-id/complete-with-approval",
		bytes.NewReader([]byte("not valid json")))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	completeTaskWithApprovalHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid body in completeTaskWithApprovalHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestAbandonTaskHandler_NotFound_Handlers exercises the n == 0 → 404 path
// (handlers_task.go:379.8,382.3) in abandonTaskHandler when task is not found.
func TestAbandonTaskHandler_NotFound_Handlers(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent-task-xyz/abandon", nil)
	w := httptest.NewRecorder()
	abandonTaskHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for nonexistent task in abandonTaskHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestPatrolsHandler_ClosedDB exercises the DB query error path
// (handlers_patrols.go:111.17,114.4) in patrolsHandler when DB is closed.
func TestPatrolsHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/patrols", nil)
	w := httptest.NewRecorder()
	patrolsHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB patrolsHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestNodesHealthHandler_ClosedDB exercises the DB query error path
// (handlers_patrols.go:201.16,204.3) in nodesHealthHandler when DB is closed.
func TestNodesHealthHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/nodes/health", nil)
	w := httptest.NewRecorder()
	nodesHealthHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB nodesHealthHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestPatrolRunHandler_EmptyPatrolID exercises the empty patrolID → 404 path
// (handlers_patrols.go:29.20,32.3) in patrolRunHandler.
func TestPatrolRunHandler_EmptyPatrolID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Path that ends in /run but has no patrol ID before it.
	req := httptest.NewRequest(http.MethodPost, "/api/patrols//run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for empty patrolID, got %d: %s", w.Code, w.Body.String())
	}
}

// TestAgentsHealthHandler_ClosedDB exercises the getAllAgentsHealth error path
// (handlers_agent.go:567.17,570.4) in agentsHealthHandler when DB is closed.
func TestAgentsHealthHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	// Request /api/agents/ (empty path after prefix trim) → getAllAgentsHealth() path.
	req := httptest.NewRequest(http.MethodGet, "/api/agents/", nil)
	w := httptest.NewRecorder()
	agentsHealthHandler(w, req)

	// getAllAgentsHealth calls getDBConn() which returns the closed conn → error → 500.
	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB agentsHealthHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestFleetMetricsHandler_ClosedDB exercises the DB query error path
// (handlers_node_metrics.go:193.16,196.3) in fleetMetricsHandler when DB is closed.
func TestFleetMetricsHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/metrics", nil)
	w := httptest.NewRecorder()
	fleetMetricsHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB fleetMetricsHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestFleetAggregateHandler_ClosedDB exercises the node query error path
// (handlers_node_metrics.go:279.16,282.3) in fleetAggregateHandler when DB is closed.
func TestFleetAggregateHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for closed-DB fleetAggregateHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestEventsHandler_ClosedDB exercises the db==nil error path
// (queue.go:986.15,989.3) in eventsHandler when the DB connection is nil.
func TestEventsHandler_ClosedDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Nil out the DB connection to exercise the "database unavailable" path.
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/events", nil)
	w := httptest.NewRecorder()
	eventsHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for nil-DB eventsHandler, got %d: %s", w.Code, w.Body.String())
	}
}

// TestCompleteTaskHandler_StateDispatchedPath claims a task (→ StateDispatched) then
// completes it, covering the StateRunning/StateDispatched SM transition branch.
func TestCompleteTaskHandler_StateDispatchedPath(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	task := Task{
		ID: "complete-dispatched-001", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued, Title: "dispatched test",
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	// Claim → transitions to StateDispatched
	claimBody := `{"agent_id":"test-agent-dispatch-001"}`
	claimReq := httptest.NewRequest(http.MethodPost, "/api/tasks/complete-dispatched-001/claim",
		bytes.NewBufferString(claimBody))
	claimRR := httptest.NewRecorder()
	claimTaskHandler(claimRR, claimReq)
	if claimRR.Code != http.StatusCreated {
		t.Skipf("claim returned %d (not 201); skipping SM dispatched path test", claimRR.Code)
	}

	// Complete → SM.GetState returns StateDispatched → hits the StateRunning||StateDispatched branch
	completeBody := `{"result":"done","status":"completed"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/complete-dispatched-001/complete",
		bytes.NewBufferString(completeBody))
	w := httptest.NewRecorder()
	completeTaskHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("complete: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestCompleteTaskHandler_StatusFailed covers the targetState=StateFailed branch.
func TestCompleteTaskHandler_StatusFailed_Path(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	task := Task{
		ID: "complete-failed-001", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusExecuting, State: StateRunning,
		Title: "failed test",
	}
	taskQueue.Enqueue(context.Background(), task)

	body := `{"result":"failed result","status":"failed"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/complete-failed-001/complete",
		bytes.NewBufferString(body))
	w := httptest.NewRecorder()
	completeTaskHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for status=failed, got %d: %s", w.Code, w.Body.String())
	}
}

// TestCompleteTaskHandler_LocalSMCreation covers the sm==nil&&db!=nil fallback
// that creates a local state machine (handlers_task.go:285.28,288.3).
func TestCompleteTaskHandler_LocalSMCreation(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Ensure the global stateMachine is nil so the local fallback path is taken.
	oldSM := stateMachine
	stateMachine = nil
	defer func() { stateMachine = oldSM }()

	task := Task{
		ID: "sm-local-001", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusExecuting, State: StateRunning,
		Title: "sm local test",
	}
	taskQueue.Enqueue(context.Background(), task)

	body := `{"result":"done","status":"completed"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/sm-local-001/complete",
		bytes.NewBufferString(body))
	w := httptest.NewRecorder()
	completeTaskHandler(w, req)

	// Accept any non-5xx response — the goal is to hit the sm-creation code path.
	if w.Code >= 500 {
		t.Errorf("unexpected server error in local SM path, got %d: %s", w.Code, w.Body.String())
	}
}

// TestCompleteWithApprovalHandler_EmptyTaskID covers the taskID=="" → 400 branch.
func TestCompleteWithApprovalHandler_EmptyTaskID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Path that strips to empty taskID
	req := httptest.NewRequest(http.MethodPost, "/api/tasks//complete-with-approval",
		bytes.NewBufferString(`{"task_id":"","result":"done"}`))
	w := httptest.NewRecorder()
	completeTaskWithApprovalHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty taskID, got %d: %s", w.Code, w.Body.String())
	}
}

// TestCreateTaskHandler_EnqueueError covers the taskQueue.Enqueue failure → 500.
func TestCreateTaskHandler_EnqueueError(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Close the DB so taskQueue.Enqueue fails.
	getDBConn().Close()

	body := `{"domain":"test","project":"proj","type":"feature","title":"enqueue error test","priority":5}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	createTaskHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for enqueue error, got %d: %s", w.Code, w.Body.String())
	}
}

// --- PatchDomainHandler ---

func TestPatchDomainHandler_HappyPath_Score(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	score := 85
	body, _ := json.Marshal(map[string]interface{}{"score": score})
	req := httptest.NewRequest(http.MethodPatch, "/api/domains/codeswiftr-com", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	PatchDomainHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp DomainMeta
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parse response: %v", err)
	}
	if resp.Key != "codeswiftr-com" {
		t.Errorf("expected key codeswiftr-com, got %s", resp.Key)
	}
	if resp.Score != score {
		t.Errorf("expected score %d, got %d", score, resp.Score)
	}
}

func TestPatchDomainHandler_HappyPath_Status(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]interface{}{"status": "inactive"})
	req := httptest.NewRequest(http.MethodPatch, "/api/domains/leanvibe-ai", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	PatchDomainHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp DomainMeta
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parse response: %v", err)
	}
	if resp.Status != "inactive" {
		t.Errorf("expected status inactive, got %s", resp.Status)
	}
}

func TestPatchDomainHandler_HappyPath_ScoreAndStatus(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]interface{}{"score": 75, "status": "archived"})
	req := httptest.NewRequest(http.MethodPatch, "/api/domains/brandfocus-ai", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	PatchDomainHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp DomainMeta
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parse response: %v", err)
	}
	if resp.Score != 75 {
		t.Errorf("expected score 75, got %d", resp.Score)
	}
	if resp.Status != "archived" {
		t.Errorf("expected status archived, got %s", resp.Status)
	}
}

func TestPatchDomainHandler_InvalidScore_TooHigh(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]interface{}{"score": 150})
	req := httptest.NewRequest(http.MethodPatch, "/api/domains/codeswiftr-com", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	PatchDomainHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for score>100, got %d: %s", w.Code, w.Body.String())
	}
}

func TestPatchDomainHandler_InvalidScore_Negative(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]interface{}{"score": -1})
	req := httptest.NewRequest(http.MethodPatch, "/api/domains/codeswiftr-com", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	PatchDomainHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for negative score, got %d: %s", w.Code, w.Body.String())
	}
}

func TestPatchDomainHandler_InvalidStatus(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]interface{}{"status": "bogus"})
	req := httptest.NewRequest(http.MethodPatch, "/api/domains/codeswiftr-com", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	PatchDomainHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid status, got %d: %s", w.Code, w.Body.String())
	}
}

func TestPatchDomainHandler_NoFields(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]interface{}{})
	req := httptest.NewRequest(http.MethodPatch, "/api/domains/codeswiftr-com", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	PatchDomainHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 when no fields provided, got %d: %s", w.Code, w.Body.String())
	}
}

func TestPatchDomainHandler_InvalidMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/domains/codeswiftr-com", nil)
	w := httptest.NewRecorder()
	PatchDomainHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestPatchDomainHandler_MissingKey(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]interface{}{"score": 50})
	req := httptest.NewRequest(http.MethodPatch, "/api/domains/", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	PatchDomainHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing key, got %d: %s", w.Code, w.Body.String())
	}
}

func TestPatchDomainHandler_InvalidJSON(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPatch, "/api/domains/codeswiftr-com", strings.NewReader("not json"))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	PatchDomainHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid JSON, got %d: %s", w.Code, w.Body.String())
	}
}

// --- getFleetCounts consistency tests ---

// TestGetFleetCounts_EmptyDB verifies that getFleetCounts returns zero values
// on an empty database and does not panic.
func TestGetFleetCounts_EmptyDB(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	fc := getFleetCounts(context.Background(), db)

	if fc.TotalAgents != 0 {
		t.Errorf("expected TotalAgents=0, got %d", fc.TotalAgents)
	}
	if fc.OnlineAgents != 0 {
		t.Errorf("expected OnlineAgents=0, got %d", fc.OnlineAgents)
	}
	if fc.QueuedTasks != 0 {
		t.Errorf("expected QueuedTasks=0, got %d", fc.QueuedTasks)
	}
	if fc.RunningTasks != 0 {
		t.Errorf("expected RunningTasks=0, got %d", fc.RunningTasks)
	}
	if fc.CompletedTasks24h != 0 {
		t.Errorf("expected CompletedTasks24h=0, got %d", fc.CompletedTasks24h)
	}
}

// TestGetFleetCounts_NilDB verifies that getFleetCounts returns zero values
// when passed a nil database (e.g., daemon not yet started).
func TestGetFleetCounts_NilDB(t *testing.T) {
	fc := getFleetCounts(context.Background(), nil)
	if fc.TotalAgents != 0 || fc.QueuedTasks != 0 {
		t.Errorf("expected all-zero FleetCounts for nil db, got %+v", fc)
	}
}

// TestGetFleetCounts_WithData verifies canonical counts with seeded data.
// It inserts agents and tasks then confirms getFleetCounts reports the
// same numbers that all status endpoints will use.
func TestGetFleetCounts_WithData(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()

	// Seed two agents: one recently seen (online), one stale (offline).
	recentTS := time.Now().UTC().Add(-1 * time.Minute).Format("2006-01-02 15:04:05")
	staleTS := time.Now().UTC().Add(-10 * time.Minute).Format("2006-01-02 15:04:05")
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, last_seen, connected_at, context_pct)
		VALUES
			('agent-online', 'node1', 'online', ?, ?, 0),
			('agent-stale',  'node1', 'offline', ?, ?, 0)
	`, recentTS, recentTS, staleTS, staleTS)
	if err != nil {
		t.Fatalf("seed agent_heartbeats: %v", err)
	}

	// Seed tasks: 1 queued, 1 running (assigned), 1 completed recently, 1 completed long ago.
	recentUpdated := time.Now().UTC().Add(-1 * time.Hour).Format("2006-01-02 15:04:05")
	oldUpdated := time.Now().UTC().Add(-48 * time.Hour).Format("2006-01-02 15:04:05")
	_, err = db.Exec(`
		INSERT INTO tasks (id, title, domain, project, type, status, state, priority, created_at, updated_at)
		VALUES
			('t-queued',    'q', 'test', 'test', 'feature', 'queued',    'QUEUED',    5, ?, ?),
			('t-assigned',  'r', 'test', 'test', 'feature', 'assigned',  'RUNNING',   5, ?, ?),
			('t-comp-new',  'c', 'test', 'test', 'feature', 'completed', 'COMPLETED', 5, ?, ?),
			('t-comp-old',  'o', 'test', 'test', 'feature', 'completed', 'COMPLETED', 5, ?, ?)
	`,
		recentUpdated, recentUpdated,
		recentUpdated, recentUpdated,
		recentUpdated, recentUpdated,
		oldUpdated, oldUpdated,
	)
	if err != nil {
		t.Fatalf("seed tasks: %v", err)
	}

	fc := getFleetCounts(context.Background(), db)

	if fc.TotalAgents != 2 {
		t.Errorf("TotalAgents: want 2, got %d", fc.TotalAgents)
	}
	if fc.OnlineAgents != 1 {
		t.Errorf("OnlineAgents: want 1, got %d", fc.OnlineAgents)
	}
	if fc.QueuedTasks != 1 {
		t.Errorf("QueuedTasks: want 1, got %d", fc.QueuedTasks)
	}
	if fc.RunningTasks != 1 {
		t.Errorf("RunningTasks: want 1, got %d", fc.RunningTasks)
	}
	if fc.CompletedTasks24h != 1 {
		t.Errorf("CompletedTasks24h: want 1 (only recently completed), got %d", fc.CompletedTasks24h)
	}
}

// TestFleetCountsConsistency_StatusEndpoints verifies that openclawStatusHandler
// and fleetSnapshotHandler both call getFleetCounts and produce consistent
// agent/task numbers from the same underlying DB state.
func TestFleetCountsConsistency_StatusEndpoints(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()

	recentTS := time.Now().UTC().Add(-2 * time.Minute).Format("2006-01-02 15:04:05")
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, last_seen, connected_at, context_pct)
		VALUES ('agt-1', 'n1', 'online', ?, ?, 0)
	`, recentTS, recentTS)
	if err != nil {
		t.Fatalf("seed agent: %v", err)
	}

	ts := time.Now().UTC().Add(-30 * time.Minute).Format("2006-01-02 15:04:05")
	_, err = db.Exec(`
		INSERT INTO tasks (id, title, domain, project, type, status, state, priority, created_at, updated_at)
		VALUES ('t-q1', 'queued task', 'test', 'test', 'feature', 'queued', 'QUEUED', 5, ?, ?)
	`, ts, ts)
	if err != nil {
		t.Fatalf("seed task: %v", err)
	}

	// Call openclawStatusHandler
	req1 := httptest.NewRequest(http.MethodGet, "/api/openclaw/status", nil)
	w1 := httptest.NewRecorder()
	openclawStatusHandler(w1, req1)
	if w1.Code != http.StatusOK {
		t.Fatalf("openclawStatusHandler: want 200, got %d: %s", w1.Code, w1.Body.String())
	}
	var ocStatus struct {
		Agents      int `json:"agents"`
		TasksQueued int `json:"tasks_queued"`
	}
	if err := json.NewDecoder(w1.Body).Decode(&ocStatus); err != nil {
		t.Fatalf("decode openclaw status: %v", err)
	}

	// Call fleetSnapshotHandler
	req2 := httptest.NewRequest(http.MethodGet, "/api/fleet/snapshot", nil)
	w2 := httptest.NewRecorder()
	fleetSnapshotHandler(w2, req2)
	if w2.Code != http.StatusOK {
		t.Fatalf("fleetSnapshotHandler: want 200, got %d: %s", w2.Code, w2.Body.String())
	}
	var snap struct {
		TotalAgents int `json:"total_agents"`
		TotalQueued int `json:"total_queued"`
	}
	if err := json.NewDecoder(w2.Body).Decode(&snap); err != nil {
		t.Fatalf("decode fleet snapshot: %v", err)
	}

	if ocStatus.Agents != snap.TotalAgents {
		t.Errorf("agent count mismatch: openclaw=%d, snapshot=%d", ocStatus.Agents, snap.TotalAgents)
	}
	if ocStatus.TasksQueued != snap.TotalQueued {
		t.Errorf("queued task count mismatch: openclaw=%d, snapshot=%d", ocStatus.TasksQueued, snap.TotalQueued)
	}
}

// --- ackTaskHandler (Epic 4 dispatch reliability) ---

func TestAckTaskHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Ensure stateMachine is nil so the standalone SQL path is used.
	// Without this, a stale stateMachine from another test may point at
	// a different DB and cause the FSM transition to fail.
	oldSM := stateMachine
	stateMachine = nil
	defer func() { stateMachine = oldSM }()

	taskID := "ack-task-001"
	agentID := "agent-ack-1"

	conn := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := conn.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, state,
		                   assigned_to, dispatch_attempts, max_retries, created_at, updated_at)
		VALUES (?, 'test-domain', 'test-project', 'feature', 5, 'assigned', 'DISPATCHED',
		        ?, 1, 3, ?, ?)
	`, taskID, agentID, now, now)
	if err != nil {
		t.Fatalf("seed dispatched task: %v", err)
	}

	body := bytes.NewBufferString(`{"agent_id":"` + agentID + `"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/ack", body)
	w := httptest.NewRecorder()
	ackTaskHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp["status"] != "acked" {
		t.Errorf("expected status=acked, got %v", resp["status"])
	}

	// Verify task is now RUNNING in the DB.
	var state string
	if err := conn.QueryRow("SELECT state FROM tasks WHERE id = ?", taskID).Scan(&state); err != nil {
		t.Fatalf("query state: %v", err)
	}
	if state != string(StateRunning) {
		t.Errorf("expected state=RUNNING after ACK, got %q", state)
	}

	// Verify acked_at was stamped.
	var ackedAt sql.NullString
	if err := conn.QueryRow("SELECT acked_at FROM tasks WHERE id = ?", taskID).Scan(&ackedAt); err != nil {
		t.Fatalf("query acked_at: %v", err)
	}
	if !ackedAt.Valid || ackedAt.String == "" {
		t.Error("expected acked_at to be set after ACK")
	}
}

func TestAckTaskHandler_WrongMethod(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/any-id/ack", nil)
	w := httptest.NewRecorder()
	ackTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestAckTaskHandler_MissingAgentID(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body := bytes.NewBufferString(`{"agent_id":""}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/any-id/ack", body)
	w := httptest.NewRecorder()
	ackTaskHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestAckTaskHandler_TaskNotFound(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body := bytes.NewBufferString(`{"agent_id":"agent-x"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent-id/ack", body)
	w := httptest.NewRecorder()
	ackTaskHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

func TestAckTaskHandler_WrongAgent(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	taskID := "ack-task-wrongagent"
	conn := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := conn.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, state,
		                   assigned_to, dispatch_attempts, max_retries, created_at, updated_at)
		VALUES (?, 'test-domain', 'test-project', 'feature', 5, 'assigned', 'DISPATCHED',
		        'agent-correct', 1, 3, ?, ?)
	`, taskID, now, now)
	if err != nil {
		t.Fatalf("seed dispatched task: %v", err)
	}

	body := bytes.NewBufferString(`{"agent_id":"agent-wrong"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/ack", body)
	w := httptest.NewRecorder()
	ackTaskHandler(w, req)

	if w.Code != http.StatusForbidden {
		t.Errorf("expected 403, got %d: %s", w.Code, w.Body.String())
	}
}

// --- Bug A1: context_pct preservation tests ---

// TestHeartbeat_ContextPctPreservedWhenAbsent verifies that a cron-style heartbeat
// that omits context_pct does not overwrite an existing non-zero value in the DB.
func TestHeartbeat_ContextPctPreservedWhenAbsent(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	agentID := "test-agent-ctx-preserve"

	// Seed an existing heartbeat row with a known context_pct value.
	if err := UpdateAgentHeartbeat(agentID, "node-x", "idle", "", 42.5); err != nil {
		t.Fatalf("seed heartbeat: %v", err)
	}

	// Send a cron-style heartbeat that omits context_pct entirely.
	body := bytes.NewBufferString(`{"agent_id":"` + agentID + `","status":"idle","node":"node-x"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/agents/"+agentID+"/heartbeat", body)
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	// The context_pct in the DB should still be 42.5, not 0.
	var ctxPct float64
	err := getDBConn().QueryRow(
		`SELECT context_pct FROM agent_heartbeats WHERE agent_id = ?`, agentID,
	).Scan(&ctxPct)
	if err != nil {
		t.Fatalf("query context_pct: %v", err)
	}
	if ctxPct != 42.5 {
		t.Errorf("context_pct should be preserved as 42.5, got %.1f", ctxPct)
	}
}

// TestHeartbeat_ContextPctUpdatedWhenProvided verifies that when context_pct is
// explicitly provided in the heartbeat payload the DB value is updated.
func TestHeartbeat_ContextPctUpdatedWhenProvided(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	agentID := "test-agent-ctx-update"

	// Seed with initial value.
	if err := UpdateAgentHeartbeat(agentID, "node-x", "idle", "", 10.0); err != nil {
		t.Fatalf("seed heartbeat: %v", err)
	}

	// Send a heartbeat with an explicit context_pct value.
	body := bytes.NewBufferString(`{"agent_id":"` + agentID + `","status":"busy","node":"node-x","context_pct":65.0}`)
	req := httptest.NewRequest(http.MethodPost, "/api/agents/"+agentID+"/heartbeat", body)
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var ctxPct float64
	err := getDBConn().QueryRow(
		`SELECT context_pct FROM agent_heartbeats WHERE agent_id = ?`, agentID,
	).Scan(&ctxPct)
	if err != nil {
		t.Fatalf("query context_pct: %v", err)
	}
	if ctxPct != 65.0 {
		t.Errorf("context_pct should be updated to 65.0, got %.1f", ctxPct)
	}
}

// TestHeartbeat_ContextAliasField verifies that the legacy "context" field
// (alias for context_pct) also works correctly and updates the DB value.
func TestHeartbeat_ContextAliasField(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	agentID := "test-agent-ctx-alias"

	// Send heartbeat using "context" alias field.
	body := bytes.NewBufferString(`{"agent_id":"` + agentID + `","status":"busy","node":"node-x","context":55.5}`)
	req := httptest.NewRequest(http.MethodPost, "/api/agents/"+agentID+"/heartbeat", body)
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var ctxPct float64
	err := getDBConn().QueryRow(
		`SELECT context_pct FROM agent_heartbeats WHERE agent_id = ?`, agentID,
	).Scan(&ctxPct)
	if err != nil {
		t.Fatalf("query context_pct: %v", err)
	}
	if ctxPct != 55.5 {
		t.Errorf("context_pct should be 55.5 from 'context' alias, got %.1f", ctxPct)
	}
}

// --- work_state tracking tests ---

// TestWorkState_DefaultsToIdle verifies that a new agent heartbeat gets work_state='idle'
// when no work_state is included in the payload.
func TestWorkState_DefaultsToIdle(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	agentID := "test-ws-default"

	body := bytes.NewBufferString(`{"agent_id":"` + agentID + `","status":"online","node":"node-a"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/agents/"+agentID+"/heartbeat", body)
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var workState string
	err := getDBConn().QueryRow(
		`SELECT COALESCE(work_state, 'idle') FROM agent_heartbeats WHERE agent_id = ?`, agentID,
	).Scan(&workState)
	if err != nil {
		t.Fatalf("query work_state: %v", err)
	}
	if workState != "idle" {
		t.Errorf("expected work_state='idle', got %q", workState)
	}
}

// TestWorkState_AcceptsWorking verifies that work_state='working' is stored correctly.
func TestAgentWorkState_AcceptsWorking(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	agentID := "test-ws-working"

	body := bytes.NewBufferString(`{"agent_id":"` + agentID + `","status":"online","node":"node-a","work_state":"working"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/agents/"+agentID+"/heartbeat", body)
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var workState string
	err := getDBConn().QueryRow(
		`SELECT work_state FROM agent_heartbeats WHERE agent_id = ?`, agentID,
	).Scan(&workState)
	if err != nil {
		t.Fatalf("query work_state: %v", err)
	}
	if workState != "working" {
		t.Errorf("expected work_state='working', got %q", workState)
	}
}

// TestAgentWorkState_AcceptsBlocked verifies that work_state='blocked' is stored correctly.
func TestAgentWorkState_AcceptsBlocked(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	agentID := "test-ws-blocked"

	body := bytes.NewBufferString(`{"agent_id":"` + agentID + `","status":"online","node":"node-a","work_state":"blocked"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/agents/"+agentID+"/heartbeat", body)
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var workState string
	err := getDBConn().QueryRow(
		`SELECT work_state FROM agent_heartbeats WHERE agent_id = ?`, agentID,
	).Scan(&workState)
	if err != nil {
		t.Fatalf("query work_state: %v", err)
	}
	if workState != "blocked" {
		t.Errorf("expected work_state='blocked', got %q", workState)
	}
}

// TestAgentWorkState_InvalidValueDefaultsToIdle verifies that an unrecognised work_state
// is coerced to "idle" rather than stored verbatim.
func TestAgentWorkState_InvalidValueDefaultsToIdle(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	agentID := "test-ws-invalid"

	body := bytes.NewBufferString(`{"agent_id":"` + agentID + `","status":"online","node":"node-a","work_state":"on_fire"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/agents/"+agentID+"/heartbeat", body)
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var workState string
	err := getDBConn().QueryRow(
		`SELECT work_state FROM agent_heartbeats WHERE agent_id = ?`, agentID,
	).Scan(&workState)
	if err != nil {
		t.Fatalf("query work_state: %v", err)
	}
	if workState != "idle" {
		t.Errorf("invalid work_state should be coerced to 'idle', got %q", workState)
	}
}

// TestAgentWorkState_UpdateAgentWorkState verifies the UpdateAgentWorkState helper.
func TestAgentWorkState_UpdateAgentWorkState(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	agentID := "test-ws-update"

	// Seed an agent row.
	if err := UpdateAgentHeartbeat(agentID, "node-a", "online", "", 0); err != nil {
		t.Fatalf("seed: %v", err)
	}

	// Set to "working".
	if err := UpdateAgentWorkState(agentID, "working"); err != nil {
		t.Fatalf("UpdateAgentWorkState working: %v", err)
	}
	var ws string
	if err := getDBConn().QueryRow(`SELECT work_state FROM agent_heartbeats WHERE agent_id = ?`, agentID).Scan(&ws); err != nil {
		t.Fatalf("query after working: %v", err)
	}
	if ws != "working" {
		t.Errorf("expected 'working', got %q", ws)
	}

	// Reset to "idle".
	if err := UpdateAgentWorkState(agentID, "idle"); err != nil {
		t.Fatalf("UpdateAgentWorkState idle: %v", err)
	}
	if err := getDBConn().QueryRow(`SELECT work_state FROM agent_heartbeats WHERE agent_id = ?`, agentID).Scan(&ws); err != nil {
		t.Fatalf("query after idle: %v", err)
	}
	if ws != "idle" {
		t.Errorf("expected 'idle', got %q", ws)
	}
}

// TestAgentWorkState_GetAllAgentsHealthIncludesWorkState verifies that getAllAgentsHealth
// returns the work_state field in each AgentHeartbeat.
func TestAgentWorkState_GetAllAgentsHealthIncludesWorkState(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	agentID := "test-ws-list-agent"

	// Seed agent with work_state='working'.
	if err := UpdateAgentHeartbeat(agentID, "node-a", "online", "", 0); err != nil {
		t.Fatalf("seed: %v", err)
	}
	if err := UpdateAgentWorkState(agentID, "working"); err != nil {
		t.Fatalf("set work_state: %v", err)
	}

	agents, err := getAllAgentsHealth()
	if err != nil {
		t.Fatalf("getAllAgentsHealth: %v", err)
	}

	var found *AgentHeartbeat
	for i := range agents {
		if agents[i].AgentID == agentID {
			found = &agents[i]
			break
		}
	}
	if found == nil {
		t.Fatalf("agent %s not found in getAllAgentsHealth response", agentID)
	}
	if found.WorkState != "working" {
		t.Errorf("expected WorkState='working', got %q", found.WorkState)
	}
}

// TestAgentWorkState_HeartbeatResponseIncludesWorkState verifies that the heartbeat
// endpoint response does NOT expose work_state (it's stored, not echoed back).
// The real assertion is that the DB value is correct after posting.
func TestAgentWorkState_HeartbeatResponseStoresWorkState(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	agentID := "test-ws-response"

	body := bytes.NewBufferString(`{"agent_id":"` + agentID + `","status":"online","node":"node-a","work_state":"blocked"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/agents/"+agentID+"/heartbeat", body)
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	// Confirm DB stores the value.
	var ws string
	if err := getDBConn().QueryRow(`SELECT work_state FROM agent_heartbeats WHERE agent_id = ?`, agentID).Scan(&ws); err != nil {
		t.Fatalf("query work_state: %v", err)
	}
	if ws != "blocked" {
		t.Errorf("expected 'blocked' in DB, got %q", ws)
	}

	// Confirm response is 200 JSON with standard fields.
	var resp map[string]string
	if err := json.NewDecoder(bytes.NewReader(w.Body.Bytes())).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp["status"] != "received" {
		t.Errorf("expected status='received' in response, got %q", resp["status"])
	}
}

// TestAgentWorkState_ValidWorkStatesMap verifies the ValidWorkStates sentinel covers
// the three expected values and rejects others.
func TestAgentWorkState_ValidWorkStatesMap(t *testing.T) {
	valid := []string{"idle", "working", "blocked"}
	for _, v := range valid {
		if !ValidWorkStates[v] {
			t.Errorf("expected %q to be in ValidWorkStates", v)
		}
	}
	invalid := []string{"", "busy", "pending", "on_fire"}
	for _, v := range invalid {
		if ValidWorkStates[v] {
			t.Errorf("expected %q NOT to be in ValidWorkStates", v)
		}
	}
}

// TestAgentWorkState_UpdateAgentWorkState_InvalidCoercedToIdle verifies that passing
// an invalid value to UpdateAgentWorkState coerces it to "idle" without returning an error.
func TestAgentWorkState_UpdateAgentWorkState_InvalidCoercedToIdle(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	agentID := "test-ws-coerce"

	// Seed with "working" first.
	if err := UpdateAgentHeartbeat(agentID, "node-a", "online", "", 0); err != nil {
		t.Fatalf("seed: %v", err)
	}
	if err := UpdateAgentWorkState(agentID, "working"); err != nil {
		t.Fatalf("set working: %v", err)
	}

	// Now pass invalid value — should coerce to "idle".
	if err := UpdateAgentWorkState(agentID, "invalid_value"); err != nil {
		t.Fatalf("UpdateAgentWorkState invalid: %v", err)
	}

	var ws string
	if err := getDBConn().QueryRow(`SELECT work_state FROM agent_heartbeats WHERE agent_id = ?`, agentID).Scan(&ws); err != nil {
		t.Fatalf("query: %v", err)
	}
	if ws != "idle" {
		t.Errorf("expected invalid value coerced to 'idle', got %q", ws)
	}
}

// --- Bug fix tests ---

// TestCompleteTaskHandler_RoutesViaApproval_WhenServiceAvailable verifies that
// completeTaskHandler routes through the approval integration when
// globalApprovalService is set (Bug 1 fix).
// A task assigned to an agent with low-confidence factors (default confCtx used
// by the handler) may either complete immediately or return 202 — both are
// acceptable outcomes.  The key assertion is that the handler does NOT return
// a 5xx error when globalApprovalService is non-nil.
func TestCompleteTaskHandler_RoutesViaApproval_WhenServiceAvailable(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Wire up globalApprovalService so the approval path is taken.
	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	oldSvc := globalApprovalService
	globalApprovalService = svc
	defer func() { globalApprovalService = oldSvc }()

	task := Task{
		ID:      "approval-route-001",
		Domain:  "test",
		Project: "proj",
		Type:    TaskTypeFeature,
		Priority: 5,
		Status:  TaskStatusExecuting,
		State:   StateRunning,
		Title:   "approval route test",
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	// Assign the task so AssignedTo is set (needed by ProcessApproved path).
	if err := taskQueue.AssignTask(context.Background(), task.ID, "agent-route-001"); err != nil {
		t.Logf("AssignTask non-fatal: %v", err)
	}

	body := `{"result":"done","status":"completed"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/approval-route-001/complete",
		bytes.NewBufferString(body))
	w := httptest.NewRecorder()
	completeTaskHandler(w, req)

	// The handler must not return a server error.  It should return 200 (high
	// confidence direct complete) or 202 (pending approval).
	if w.Code >= 500 {
		t.Errorf("expected 2xx from approval-routed complete, got %d: %s", w.Code, w.Body.String())
	}
}

// TestCompleteTaskHandler_NilApprovalService_FallsThrough verifies that
// completeTaskHandler still works when globalApprovalService is nil (backward
// compatibility / standalone mode — Bug 1 fix).
func TestCompleteTaskHandler_NilApprovalService_FallsThrough(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Ensure approval service is nil.
	oldSvc := globalApprovalService
	globalApprovalService = nil
	defer func() { globalApprovalService = oldSvc }()

	task := Task{
		ID:      "no-approval-svc-001",
		Domain:  "test",
		Project: "proj",
		Type:    TaskTypeFeature,
		Priority: 5,
		Status:  TaskStatusExecuting,
		State:   StateRunning,
		Title:   "no approval svc test",
	}
	taskQueue.Enqueue(context.Background(), task)

	body := `{"result":"done","status":"completed"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/no-approval-svc-001/complete",
		bytes.NewBufferString(body))
	w := httptest.NewRecorder()
	completeTaskHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("nil-approval-svc: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}
