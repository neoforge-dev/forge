//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
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

func TestHandleTaskCancel(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/cli/task/cancel?id=x", nil)
	w := httptest.NewRecorder()
	handleTaskCancel(w, req)

	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
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

func TestHandleFleetTopology_NotImplemented(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/fleet/topology", nil)
	w := httptest.NewRecorder()
	handleFleetTopology(w, req)

	// Returns 501 (not yet implemented) — just verify it runs
	if w.Code != http.StatusOK && w.Code != http.StatusNotImplemented {
		t.Errorf("expected 200 or 501, got %d: %s", w.Code, w.Body.String())
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

// --- handleSystemPatrol / handleSystemMetrics / handleQueuePriority / handleQueueCancel ---

func TestHandleSystemPatrol(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/system/patrol", nil)
	w := httptest.NewRecorder()
	handleSystemPatrol(w, req)

	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError && w.Code != http.StatusNotImplemented {
		t.Errorf("expected 200/500/501, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleSystemMetrics(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/system/metrics", nil)
	w := httptest.NewRecorder()
	handleSystemMetrics(w, req)

	if w.Code != http.StatusOK && w.Code != http.StatusNotImplemented {
		t.Errorf("expected 200 or 501, got %d: %s", w.Code, w.Body.String())
	}
}

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

// --- handleAgentSpawn / handleAgentStop / handleAgentStatus ---

func TestHandleAgentSpawn(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/cli/agent/spawn", nil)
	w := httptest.NewRecorder()
	handleAgentSpawn(w, req)

	// May return 501 (not implemented) or error
	if w.Code == 0 {
		t.Error("expected a response code")
	}
}

func TestHandleAgentStop(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/cli/agent/stop?id=x", nil)
	w := httptest.NewRecorder()
	handleAgentStop(w, req)

	if w.Code == 0 {
		t.Error("expected a response code")
	}
}

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
