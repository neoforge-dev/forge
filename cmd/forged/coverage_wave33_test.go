//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// Wave 33: handleQueueList sort path (multiple tasks), handleQueuePriority,
//          handleQueueStatus, fleetAggregateHandler with fake self server

func setupQueueWithTasks(t *testing.T, n int) (TaskQueue, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		cleanup()
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	// Insert n tasks with varying priorities so sort path executes
	for i := 0; i < n; i++ {
		priority := (i % 3) * 40 // 0, 40, 80 cycling
		if priority == 0 {
			priority = 10
		}
		_, err := db.ExecContext(context.Background(),
			`INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
			 VALUES (?, 'test', 'proj', 'feature', ?, 'queued', 'QUEUED', ?, ?, ?)`,
			fmt.Sprintf("TASK-W33-%d", i),
			fmt.Sprintf("Task %d", i),
			priority,
			time.Now().Add(time.Duration(i)*time.Millisecond).Format(time.RFC3339),
			time.Now().Format(time.RFC3339),
		)
		if err != nil {
			cleanup()
			t.Fatalf("insert task %d: %v", i, err)
		}
	}
	return q, cleanup
}

func TestHandleQueueList_SortPath_W33(t *testing.T) {
	q, cleanup := setupQueueWithTasks(t, 5)
	defer cleanup()

	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/list?limit=50", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleQueuePriority_MethodNotAllowed_W33(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/queue/priority", nil)
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandleQueuePriority_NilDB_W33(t *testing.T) {
	orig := getDBConn()
	setDBConn(nil)
	defer setDBConn(orig)

	body := strings.NewReader(`{"id":"TASK-1","priority":"high"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/priority", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestHandleQueuePriority_InvalidBody_W33(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/queue/priority", strings.NewReader("notjson"))
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestHandleQueuePriority_MissingFields_W33(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	body := strings.NewReader(`{"id":""}`)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/priority", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing fields, got %d", w.Code)
	}
}

func TestHandleQueuePriority_InvalidPriority_W33(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	body := strings.NewReader(`{"id":"TASK-1","priority":"urgent"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/priority", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid priority, got %d", w.Code)
	}
}

func TestHandleQueuePriority_TaskNotFound_W33(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	body := strings.NewReader(`{"id":"NONEXISTENT-TASK","priority":"high"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/priority", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleQueueStatus_NilQueue_W33(t *testing.T) {
	orig := taskQueue
	taskQueue = nil
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/status", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestHandleQueueStatus_WithQueue_W33(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/status", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestFleetAggregateHandler_WithFakeServer_W33(t *testing.T) {
	// Set up a fake "self" node HTTP server
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status":"ok","tasks":0,"agents":0}`))
	}))
	defer ts.Close()

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Set PORT so self-node URL is known
	t.Setenv("PORT", ts.URL[len("http://localhost:"):])
	t.Setenv("NODE_ID", "test-self-w33")

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, req)
	// Should not panic; status varies
	_ = w.Code
}
