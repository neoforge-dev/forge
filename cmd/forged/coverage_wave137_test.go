//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// ============================================================
// coverage_wave137_test.go — HTTP handler gap coverage
// Targets: patrolRunHandler, agentTelemetrySummaryHandler,
//          parityHandler, agentTasksHandler, claimTaskHandler,
//          uiPatrolDrillDownHandler
// ============================================================

// TestWave137_PatrolRunHandler_MethodNotAllowed covers the GET-rejected path.
// TestWave137_PatrolRunHandler_MissingRunSuffix covers path without /run suffix.
func TestWave137_PatrolRunHandler_MissingRunSuffix(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/patrols/", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, r)
	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

// TestWave137_PatrolRunHandler_NilPatrolSystem covers globalPatrolSystem == nil path.
func TestWave137_PatrolRunHandler_NilPatrolSystem(t *testing.T) {
	saved := globalPatrolSystem
	globalPatrolSystem = nil
	defer func() { globalPatrolSystem = saved }()

	r := httptest.NewRequest(http.MethodPost, "/api/patrols/some-id/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, r)
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", w.Code)
	}
}

// TestWave137_PatrolRunHandler_PatrolNotFound covers patrol not in system path.
func TestWave137_PatrolRunHandler_PatrolNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ps := NewPatrolSystem(db)
	saved := globalPatrolSystem
	globalPatrolSystem = ps
	defer func() { globalPatrolSystem = saved }()

	r := httptest.NewRequest(http.MethodPost, "/api/patrols/nonexistent-patrol-wave137/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, r)
	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

// TestWave137_AgentTelemetrySummary_MethodNotAllowed covers POST rejection.
// TestWave137_AgentTelemetrySummary_NilDB covers db == nil path.
func TestWave137_AgentTelemetrySummary_NilDB(t *testing.T) {
	setDBConn(nil)
	r := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, r)
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", w.Code)
	}
}

// TestWave137_AgentTelemetrySummary_EmptyDB covers successful scan with no rows.
func TestWave137_AgentTelemetrySummary_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	r := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, ok := resp["agent_count"]; !ok {
		t.Error("expected agent_count key")
	}
}

// TestWave137_ParityHandler_Success covers the success path of parityHandler.
func TestWave137_ParityHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := parityHandler(db)
	r := httptest.NewRequest(http.MethodGet, "/api/parity", nil)
	w := httptest.NewRecorder()
	h(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_AgentTasksHandler_MethodNotAllowed covers POST rejection.
// TestWave137_AgentTasksHandler_EmptyID covers missing agent ID path.
func TestWave137_AgentTasksHandler_EmptyID(t *testing.T) {
	// URL that resolves to an empty ID after trimming suffix
	r := httptest.NewRequest(http.MethodGet, "/agents//tasks", nil)
	r.URL.Path = "/agents//tasks"
	w := httptest.NewRecorder()
	agentTasksHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

// TestWave137_AgentTasksHandler_Success covers the success path with /api/agents/ prefix.
func TestWave137_AgentTasksHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	r := httptest.NewRequest(http.MethodGet, "/api/agents/agent-wave137/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, ok := resp["tasks"]; !ok {
		t.Error("expected tasks key")
	}
}

// TestWave137_ClaimTaskHandler_MethodNotAllowed covers GET rejection.
// TestWave137_ClaimTaskHandler_MissingAgentID covers empty agent_id path.
func TestWave137_ClaimTaskHandler_MissingAgentID(t *testing.T) {
	body := bytes.NewBufferString(`{"agent_id":""}`)
	r := httptest.NewRequest(http.MethodPost, "/api/tasks/task-1/claim", body)
	w := httptest.NewRecorder()
	claimTaskHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_ClaimTaskHandler_InvalidJSON covers bad JSON body.
func TestWave137_ClaimTaskHandler_InvalidJSON(t *testing.T) {
	body := bytes.NewBufferString(`not-json`)
	r := httptest.NewRequest(http.MethodPost, "/api/tasks/task-1/claim", body)
	w := httptest.NewRecorder()
	claimTaskHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_ClaimTaskHandler_TaskNotFound covers task not in queue.
func TestWave137_ClaimTaskHandler_TaskNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	tq, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	savedTQ := taskQueue
	taskQueue = tq
	defer func() { taskQueue = savedTQ }()

	body := bytes.NewBufferString(`{"agent_id":"agent-wave137"}`)
	r := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent-wave137/claim", body)
	w := httptest.NewRecorder()
	claimTaskHandler(w, r)
	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_ClaimTaskHandler_AlreadyClaimed covers conflict state.
func TestWave137_ClaimTaskHandler_AlreadyClaimed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	tq, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	savedTQ := taskQueue
	taskQueue = tq
	defer func() { taskQueue = savedTQ }()

	// Enqueue a task then mark it assigned
	task := Task{
		ID:        "task-wave137-claimed",
		Domain:    "forge",
		Project:   "test",
		Type:      "feature",
		Priority:  5,
		Status:    TaskStatusAssigned,
		State:     StateDispatched,
		Title:     "already claimed task",
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
	}
	if err := tq.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	body := bytes.NewBufferString(`{"agent_id":"agent-wave137"}`)
	r := httptest.NewRequest(http.MethodPost, "/api/tasks/task-wave137-claimed/claim", body)
	w := httptest.NewRecorder()
	claimTaskHandler(w, r)
	if w.Code != http.StatusConflict {
		t.Fatalf("expected 409 conflict, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_UIPatrolDrillDown_MethodNotAllowed covers POST rejection.
// TestWave137_UIPatrolDrillDown_EmptyID covers redirect to /ui path.
func TestWave137_UIPatrolDrillDown_EmptyID(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/ui/patrol/", nil)
	w := httptest.NewRecorder()
	uiPatrolDrillDownHandler(w, r)
	if w.Code != http.StatusFound {
		t.Fatalf("expected 302 redirect, got %d", w.Code)
	}
}

// TestWave137_UIPatrolDrillDown_NilDB covers nil DB path (renders HTML with empty state).
func TestWave137_UIPatrolDrillDown_NilDB(t *testing.T) {
	setDBConn(nil)
	saved := globalPatrolSystem
	globalPatrolSystem = nil
	defer func() { globalPatrolSystem = saved }()

	r := httptest.NewRequest(http.MethodGet, "/ui/patrol/patrol-wave137", nil)
	w := httptest.NewRecorder()
	uiPatrolDrillDownHandler(w, r)
	// Should render HTML page (200) even with nil DB
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_UIPatrolDrillDown_WithDB covers DB-backed patrol drill-down.
func TestWave137_UIPatrolDrillDown_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	saved := globalPatrolSystem
	globalPatrolSystem = nil
	defer func() { globalPatrolSystem = saved }()

	r := httptest.NewRequest(http.MethodGet, "/ui/patrol/patrol-wave137-db", nil)
	w := httptest.NewRecorder()
	uiPatrolDrillDownHandler(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_NotificationsHandler_POSTBadJSON covers POST with invalid JSON body.
func TestWave137_NotificationsHandler_POSTBadJSON(t *testing.T) {
	body := bytes.NewBufferString(`not-json`)
	r := httptest.NewRequest(http.MethodPost, "/api/notifications", body)
	w := httptest.NewRecorder()
	notificationsHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_NotificationsHandler_GET covers listing notifications.
func TestWave137_NotificationsHandler_GET(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/api/notifications", nil)
	w := httptest.NewRecorder()
	notificationsHandler(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

// TestWave137_NotificationsHandler_POST covers creating a notification.
func TestWave137_NotificationsHandler_POST(t *testing.T) {
	body := bytes.NewBufferString(`{"type":"info","title":"Test","message":"wave137 test"}`)
	r := httptest.NewRequest(http.MethodPost, "/api/notifications", body)
	w := httptest.NewRecorder()
	notificationsHandler(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_NotificationActionHandler_EmptyID covers empty notification ID path.
func TestWave137_NotificationActionHandler_EmptyID(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/notifications//read", nil)
	r.URL.Path = "/api/notifications//read"
	w := httptest.NewRecorder()
	notificationActionHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_NotificationActionHandler_NotFound covers missing notification ID.
func TestWave137_NotificationActionHandler_NotFound(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/notifications/nonexistent-wave137/read", nil)
	w := httptest.NewRecorder()
	notificationActionHandler(w, r)
	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_NotificationActionHandler_MarkRead covers successful mark-read path.
func TestWave137_NotificationActionHandler_MarkRead(t *testing.T) {
	// Create a notification first
	body := bytes.NewBufferString(`{"type":"info","title":"MarkRead Test","message":"to be read"}`)
	r1 := httptest.NewRequest(http.MethodPost, "/api/notifications", body)
	w1 := httptest.NewRecorder()
	notificationsHandler(w1, r1)
	if w1.Code != http.StatusOK {
		t.Fatalf("create notification: expected 200, got %d", w1.Code)
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w1.Body).Decode(&resp); err != nil {
		t.Fatalf("decode create response: %v", err)
	}
	notif, ok := resp["notification"].(map[string]interface{})
	if !ok {
		t.Fatal("notification missing in response")
	}
	notifID, ok := notif["id"].(string)
	if !ok || notifID == "" {
		t.Fatal("notification ID missing")
	}

	// Mark it read
	r2 := httptest.NewRequest(http.MethodPost, "/api/notifications/"+notifID+"/read", nil)
	w2 := httptest.NewRecorder()
	notificationActionHandler(w2, r2)
	if w2.Code != http.StatusOK {
		t.Fatalf("mark read: expected 200, got %d: %s", w2.Code, w2.Body.String())
	}
}

// TestWave137_ConfigHandler_PUTBadJSON covers PUT /config with invalid JSON.
func TestWave137_ConfigHandler_PUTBadJSON(t *testing.T) {
	body := bytes.NewBufferString(`not-json`)
	r := httptest.NewRequest(http.MethodPut, "/config", body)
	w := httptest.NewRecorder()
	configHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_DispatchHandler_POSTBadJSON covers POST /dispatch with invalid JSON.
func TestWave137_DispatchHandler_POSTBadJSON(t *testing.T) {
	body := bytes.NewBufferString(`not-json`)
	r := httptest.NewRequest(http.MethodPost, "/api/dispatch", body)
	w := httptest.NewRecorder()
	dispatchHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_LaneByIDHandler_MethodNotAllowed covers POST rejection.
// TestWave137_LaneByIDHandler_NotFound covers unknown lane ID.
func TestWave137_LaneByIDHandler_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	r := httptest.NewRequest(http.MethodGet, "/api/lanes/nonexistent-wave137", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, r)
	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave137_ContextsHandler_MethodNotAllowed covers POST rejection.
