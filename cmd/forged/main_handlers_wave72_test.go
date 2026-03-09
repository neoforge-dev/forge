//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// handleTaskList — 60% coverage
// ---------------------------------------------------------------------------

func TestWave72_HandleTaskList_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tasks", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave72_HandleTaskList_WithFormat(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tasks?format=json", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

func TestWave72_HandleTaskList_WithTasks(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Enqueue a task so list returns data
	_ = taskQueue.Enqueue(context.Background(), Task{
		ID: "wave72-list-task", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	r := httptest.NewRequest(http.MethodGet, "/tasks", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "wave72-list-task") {
		t.Error("expected task ID in response")
	}
}

// ---------------------------------------------------------------------------
// handleTaskShow — 92.9% coverage — missing the id-required branch
// ---------------------------------------------------------------------------

func TestWave72_HandleTaskShow_MissingID(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/tasks/show", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d", w.Code)
	}
}

func TestWave72_HandleTaskShow_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tasks/show?id=nonexistent-id", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, r)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for missing task, got %d", w.Code)
	}
}

func TestWave72_HandleTaskShow_Found(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_ = taskQueue.Enqueue(context.Background(), Task{
		ID: "wave72-show-task", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	r := httptest.NewRequest(http.MethodGet, "/tasks/show?id=wave72-show-task", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handleAgentStatus — 90.9% — missing the id-required branch
// ---------------------------------------------------------------------------

func TestWave72_HandleAgentStatus_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/agents/status", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing agent id, got %d", w.Code)
	}
}

func TestWave72_HandleAgentStatus_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/agents/status?id=ghost-agent", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, r)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for unknown agent, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handleSystemHealth — 55.6% — covers the path through healthHandler
// ---------------------------------------------------------------------------

func TestWave72_HandleSystemHealth_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave72_HandleSystemHealth_WithFormat(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/system/health?format=json", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Errorf("response not JSON: %v", err)
	}
}

// ---------------------------------------------------------------------------
// handleQueueDepth — 54.5% — covers claimable task path
// ---------------------------------------------------------------------------

func TestWave72_HandleQueueDepth_Empty(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave72_HandleQueueDepth_WithTasks(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_ = taskQueue.Enqueue(context.Background(), Task{
		ID: "wave72-depth-task", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	r := httptest.NewRequest(http.MethodGet, "/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Errorf("response not JSON: %v", err)
	}
	if _, ok := resp["depth"]; !ok {
		t.Error("expected 'depth' key in response")
	}
}

// ---------------------------------------------------------------------------
// handleQueueStatus — covers the stats calculation path
// ---------------------------------------------------------------------------

func TestWave72_HandleQueueStatus_Empty(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/queue/status", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave72_HandleQueueStatus_WithTasks(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_ = taskQueue.Enqueue(context.Background(), Task{
		ID: "wave72-qs-task1", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 8, Status: TaskStatusQueued,
	})
	_ = taskQueue.Enqueue(context.Background(), Task{
		ID: "wave72-qs-task2", Domain: "d", Project: "p",
		Type: TaskTypeBugfix, Priority: 3, Status: TaskStatusQueued,
	})

	r := httptest.NewRequest(http.MethodGet, "/queue/status", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Errorf("response not JSON: %v", err)
	}
	if _, ok := resp["total"]; !ok {
		t.Error("expected 'total' key in response")
	}
}

// ---------------------------------------------------------------------------
// handleQueueList — covers the sort path with multiple tasks
// ---------------------------------------------------------------------------

func TestWave72_HandleQueueList_Empty(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/queue/list", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave72_HandleQueueList_WithLimit(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	for i := 0; i < 3; i++ {
		_ = taskQueue.Enqueue(context.Background(), Task{
			ID: "wave72-ql-task" + string(rune('0'+i)), Domain: "d", Project: "p",
			Type: TaskTypeFeature, Priority: i + 1, Status: TaskStatusQueued,
		})
	}

	r := httptest.NewRequest(http.MethodGet, "/queue/list?limit=2", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

func TestWave72_HandleQueueList_InvalidLimit(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/queue/list?limit=0", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handleQueuePriority — covers priority update paths
// ---------------------------------------------------------------------------

func TestWave72_HandleQueuePriority_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/queue/priority", nil)
	w := httptest.NewRecorder()
	handleQueuePriority(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave72_HandleQueuePriority_MissingFields(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := `{"id": ""}`
	r := httptest.NewRequest(http.MethodPost, "/queue/priority", strings.NewReader(body))
	w := httptest.NewRecorder()
	handleQueuePriority(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing fields, got %d", w.Code)
	}
}

func TestWave72_HandleQueuePriority_InvalidPriority(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{"id": "some-task", "priority": "urgent"})
	r := httptest.NewRequest(http.MethodPost, "/queue/priority", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handleQueuePriority(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid priority, got %d", w.Code)
	}
}

func TestWave72_HandleQueuePriority_TaskNotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{"id": "nonexistent-task", "priority": "high"})
	r := httptest.NewRequest(http.MethodPost, "/queue/priority", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handleQueuePriority(w, r)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for missing task, got %d", w.Code)
	}
}

func TestWave72_HandleQueuePriority_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_ = taskQueue.Enqueue(context.Background(), Task{
		ID: "wave72-pri-task", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	body, _ := json.Marshal(map[string]string{"id": "wave72-pri-task", "priority": "high"})
	r := httptest.NewRequest(http.MethodPost, "/queue/priority", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handleQueuePriority(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handleQueueCancel — covers cancel paths
// ---------------------------------------------------------------------------

func TestWave72_HandleQueueCancel_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/queue/cancel", nil)
	w := httptest.NewRecorder()
	handleQueueCancel(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave72_HandleQueueCancel_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := `{"reason": "test"}`
	r := httptest.NewRequest(http.MethodPost, "/queue/cancel", strings.NewReader(body))
	w := httptest.NewRecorder()
	handleQueueCancel(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d", w.Code)
	}
}

func TestWave72_HandleQueueCancel_TaskNotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{"id": "ghost-task", "reason": "test"})
	r := httptest.NewRequest(http.MethodPost, "/queue/cancel", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handleQueueCancel(w, r)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestWave72_HandleQueueCancel_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_ = taskQueue.Enqueue(context.Background(), Task{
		ID: "wave72-cancel-task", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	body, _ := json.Marshal(map[string]string{"id": "wave72-cancel-task", "reason": "wave72 test"})
	r := httptest.NewRequest(http.MethodPost, "/queue/cancel", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handleQueueCancel(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave72_HandleQueueCancel_AlreadyAssigned(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_ = taskQueue.Enqueue(context.Background(), Task{
		ID: "wave72-cancel-assigned", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})
	_ = taskQueue.ClaimTask(context.Background(), "wave72-cancel-assigned", "agent-x")

	body, _ := json.Marshal(map[string]string{"id": "wave72-cancel-assigned", "reason": "test"})
	r := httptest.NewRequest(http.MethodPost, "/queue/cancel", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handleQueueCancel(w, r)

	// Assigned task cannot be cancelled — expect 400
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for assigned task cancel, got %d: %s", w.Code, w.Body.String())
	}
}
