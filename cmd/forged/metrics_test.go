//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestMetrics(t *testing.T) {
	// Record some dummy data
	metrics.RecordRequest("GET", "/api/test", 200, 100*time.Millisecond)
	metrics.RecordTaskCreated("feature", 1)
	metrics.RecordTaskCompleted()
	metrics.RecordHeartbeat()

	// Test handler
	req, err := http.NewRequest("GET", "/metrics", nil)
	if err != nil {
		t.Fatal(err)
	}

	rr := httptest.NewRecorder()
	handler := http.HandlerFunc(MetricsHandler)
	handler.ServeHTTP(rr, req)

	if status := rr.Code; status != http.StatusOK {
		t.Errorf("handler returned wrong status code: got %v want %v", status, http.StatusOK)
	}

	// Check if common metrics are present in output
	body := rr.Body.String()
	expectedMetrics := []string{
		"forge_http_requests_total",
		"forge_tasks_total",
		"forge_heartbeats_received",
	}

	for _, m := range expectedMetrics {
		if !contains(body, m) {
			t.Errorf("expected metric %s not found in response", m)
		}
	}
}

func contains(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}

// Wave 28: InitMetrics, openclawEventsHandler, ListNodesHandler, handleQueueList

func TestInitMetrics_W28(t *testing.T) {
	// Exercises the goroutine spawn path — now with cleanup
	InitMetrics()
	defer StopMetrics()
}

func TestOpenclawEventsHandler_MethodNotAllowed_W28(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/events", nil)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestOpenclawEventsHandler_CancelledContext_W28(t *testing.T) {
	// httptest.ResponseRecorder implements http.Flusher, so the handler enters the SSE loop.
	// Cancel the context immediately so the handler exits via r.Context().Done().
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel before the request is made — handler exits on first select

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil).WithContext(ctx)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)
	// Handler exits cleanly via cancelled context — status 200 (SSE headers already set)
	_ = w.Code
}

func TestListNodesHandler_EmptyDB_W28(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes", nil)
	w := httptest.NewRecorder()
	xc.ListNodesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleQueueList_NilQueue_W28(t *testing.T) {
	// Save global state and restore
	orig := taskQueue
	taskQueue = nil
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 with nil queue, got %d", w.Code)
	}
}
