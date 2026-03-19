//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 192: handlers_plan.go + handlers_project.go
// Targets:
//   queueTaskHandler   (handlers_plan.go:17)  — GET→405, empty ID
//   pauseTaskHandler   (handlers_plan.go:55)  — GET→405, nil queue→503
//   resumeTaskHandler  (handlers_plan.go:96)  — GET→405, nil queue→503
//   agentTasksHandler  (handlers_plan.go:138) — POST→405, empty ID, GET with DB
//   projectsHandler    (handlers_project.go:30) — DELETE→405
//   listProjectsHandler(handlers_project.go:42) — GET all / GET with domain filter
//   projectByIDHandler (handlers_project.go:107)— POST→405, empty ID, not found
//   createProjectHandler(handlers_project.go:171)— invalid body, missing key, success
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// queueTaskHandler
// ---------------------------------------------------------------------------

func TestWave192_QueueTaskHandler_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/queue", nil)
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// pauseTaskHandler
// ---------------------------------------------------------------------------

func TestWave192_PauseTaskHandler_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/pause", nil)
	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave192_PauseTaskHandler_NilQueue(t *testing.T) {
	// With nil DB and nil taskQueue → 503
	setDBConn(nil)
	oldTQ := taskQueue
	taskQueue = nil
	defer func() { taskQueue = oldTQ }()
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-1/pause", nil)
	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 with nil queue, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// resumeTaskHandler
// ---------------------------------------------------------------------------

func TestWave192_ResumeTaskHandler_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/resume", nil)
	w := httptest.NewRecorder()
	resumeTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave192_ResumeTaskHandler_NilQueue(t *testing.T) {
	setDBConn(nil)
	oldTQ := taskQueue
	taskQueue = nil
	defer func() { taskQueue = oldTQ }()
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-1/resume", nil)
	w := httptest.NewRecorder()
	resumeTaskHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 with nil queue, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// agentTasksHandler
// ---------------------------------------------------------------------------

func TestWave192_AgentTasksHandler_Post405(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/kimi-1/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave192_AgentTasksHandler_EmptyID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents//tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty agent ID, got %d", w.Code)
	}
}

func TestWave192_AgentTasksHandler_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/kimi-192/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// projectsHandler
// ---------------------------------------------------------------------------

func TestWave192_ProjectsHandler_Delete405(t *testing.T) {
	req := httptest.NewRequest(http.MethodDelete, "/api/projects", nil)
	w := httptest.NewRecorder()
	projectsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// listProjectsHandler
// ---------------------------------------------------------------------------

func TestWave192_ListProjectsHandler_GetAll(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/projects", nil)
	w := httptest.NewRecorder()
	listProjectsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave192_ListProjectsHandler_WithDomainFilter(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/projects?domain=forge", nil)
	w := httptest.NewRecorder()
	listProjectsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with domain filter, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// projectByIDHandler
// ---------------------------------------------------------------------------

func TestWave192_ProjectByIDHandler_Post405(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/projects/proj-1", nil)
	w := httptest.NewRecorder()
	projectByIDHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave192_ProjectByIDHandler_EmptyID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/projects/", nil)
	w := httptest.NewRecorder()
	projectByIDHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty ID, got %d", w.Code)
	}
}

func TestWave192_ProjectByIDHandler_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/projects/nonexistent-proj", nil)
	w := httptest.NewRecorder()
	projectByIDHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// createProjectHandler
// ---------------------------------------------------------------------------

func TestWave192_CreateProjectHandler_InvalidBody(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/projects",
		strings.NewReader("not-json"))
	w := httptest.NewRecorder()
	createProjectHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid body, got %d", w.Code)
	}
}

func TestWave192_CreateProjectHandler_MissingKey(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/projects",
		strings.NewReader(`{"name":"Test Project"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	createProjectHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing key, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave192_CreateProjectHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/projects",
		strings.NewReader(`{"key":"wave192-proj","name":"Wave 192 Project","domain":"forge"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	createProjectHandler(w, req)
	if w.Code != http.StatusCreated {
		t.Errorf("expected 201, got %d: %s", w.Code, w.Body.String())
	}
}
