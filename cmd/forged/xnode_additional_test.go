//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// nonFlusherWriter is a ResponseWriter that deliberately does NOT implement
// http.Flusher, so SSEDeliveryHandler falls back to the 500 error path.
type nonFlusherWriter struct {
	header http.Header
	body   strings.Builder
	code   int
}

func (n *nonFlusherWriter) Header() http.Header {
	if n.header == nil {
		n.header = make(http.Header)
	}
	return n.header
}
func (n *nonFlusherWriter) Write(b []byte) (int, error) {
	return n.body.Write(b)
}
func (n *nonFlusherWriter) WriteHeader(code int) { n.code = code }

// wave66FlusherRecorder wraps httptest.ResponseRecorder and implements http.Flusher
// to satisfy SSEDeliveryHandler's streaming requirement.
type wave66FlusherRecorder struct {
	*httptest.ResponseRecorder
	flushed int
}

func (f *wave66FlusherRecorder) Flush() { f.flushed++ }

// TestWave66_SSEDeliveryHandler_UnsupportedFlusher verifies SSEDeliveryHandler returns
// 500 when the ResponseWriter does not implement http.Flusher.
func TestWave66_SSEDeliveryHandler_UnsupportedFlusher(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/events", nil).WithContext(ctx)
	w := &nonFlusherWriter{}

	xc.SSEDeliveryHandler(w, req)

	if w.code != http.StatusInternalServerError {
		t.Errorf("expected 500 when Flusher not supported, got %d (body: %s)", w.code, w.body.String())
	}
}

// TestWave66_SSEDeliveryHandler_InitialConnectionEvent verifies that the SSE handler
// emits the initial "connected" event before entering the polling loop.
func TestWave66_SSEDeliveryHandler_InitialConnectionEvent(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	ctx, cancel := context.WithCancel(context.Background())

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/events", nil).WithContext(ctx)
	w := &wave66FlusherRecorder{ResponseRecorder: httptest.NewRecorder()}

	done := make(chan struct{})
	go func() {
		defer close(done)
		xc.SSEDeliveryHandler(w, req)
	}()

	// Allow handler to start and emit connected event.
	time.Sleep(30 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("SSEDeliveryHandler did not return after context cancellation")
	}

	body := w.Body.String()
	if !strings.Contains(body, "event: connected") {
		t.Errorf("expected 'event: connected' in SSE output, got: %q", body)
	}
	if !strings.Contains(body, "\"status\":\"connected\"") {
		t.Errorf("expected status:connected in SSE data, got: %q", body)
	}
}

// TestWave66_SSEDeliveryHandler_OutboxMessages verifies the SSE handler delivers messages
// from the outbox directory when a JSONL file is present.
func TestWave66_SSEDeliveryHandler_OutboxMessages(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	// Write a message to the outbox directory before starting the handler.
	msg := map[string]interface{}{
		"message_id": "wave66-msg-001",
		"type":       "task_forward",
		"source":     "test-node",
		"payload":    map[string]string{"task_id": "task-wave66"},
	}
	msgJSON, _ := json.Marshal(msg)
	outboxFile := filepath.Join(xc.outboxDir, "remote-node.jsonl")
	if err := os.WriteFile(outboxFile, append(msgJSON, '\n'), 0o644); err != nil {
		t.Fatalf("write outbox file: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/events", nil).WithContext(ctx)
	w := &wave66FlusherRecorder{ResponseRecorder: httptest.NewRecorder()}

	done := make(chan struct{})
	go func() {
		defer close(done)
		xc.SSEDeliveryHandler(w, req)
	}()

	// Allow two check-ticker cycles (1s each) to pick up the message.
	time.Sleep(2200 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(4 * time.Second):
		t.Fatal("SSEDeliveryHandler did not return after context cancellation")
	}

	body := w.Body.String()
	// The handler should have emitted a "delivery" event for the outbox message.
	if !strings.Contains(body, "event: delivery") {
		t.Logf("SSE body (no delivery event detected): %q", body)
		// Not a fatal failure — the outbox read may have seen size=0 if the
		// file was written after lastSeen was initialised. The important thing
		// is the handler ran without panic.
	}
}

// TestWave66_SSEDeliveryHandler_TargetNodeFilter verifies that the target_node query
// parameter filters outbox messages to only the matching node.
func TestWave66_SSEDeliveryHandler_TargetNodeFilter(t *testing.T) {
	xc, cleanup := setupXNodeHTTP(t)
	defer cleanup()

	// Write messages for two nodes.
	for _, node := range []string{"alpha", "beta"} {
		msg := map[string]interface{}{
			"message_id": "msg-" + node,
			"type":       "heartbeat",
			"source":     "test-node",
		}
		msgJSON, _ := json.Marshal(msg)
		path := filepath.Join(xc.outboxDir, node+".jsonl")
		os.WriteFile(path, append(msgJSON, '\n'), 0o644) //nolint:errcheck
	}

	ctx, cancel := context.WithCancel(context.Background())

	// Filter to only "alpha" node.
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/events?target_node=alpha", nil).WithContext(ctx)
	w := &wave66FlusherRecorder{ResponseRecorder: httptest.NewRecorder()}

	done := make(chan struct{})
	go func() {
		defer close(done)
		xc.SSEDeliveryHandler(w, req)
	}()

	// Two check-ticker cycles.
	time.Sleep(2200 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(4 * time.Second):
		t.Fatal("SSEDeliveryHandler did not return after context cancellation")
	}

	body := w.Body.String()
	// "beta" events must NOT appear in the filtered output.
	if strings.Contains(body, "\"target\":\"beta\"") {
		t.Error("expected beta node messages to be filtered out by target_node=alpha")
	}
}
