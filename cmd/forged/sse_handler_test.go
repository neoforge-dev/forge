package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAgentsSSEHandler(t *testing.T) {
	// Use a context that cancels immediately so the SSE loop exits
	// without running forever (we only need to assert headers)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	req, err := http.NewRequest("GET", "/api/agents/stream", nil)
	if err != nil {
		t.Fatalf("failed to create request: %v", err)
	}
	req = req.WithContext(ctx)

	rr := httptest.NewRecorder()

	agentsSSEHandler(rr, req)

	if ct := rr.Header().Get("Content-Type"); ct != "text/event-stream" {
		t.Errorf("expected Content-Type text/event-stream, got %s", ct)
	}
	if cc := rr.Header().Get("Cache-Control"); cc != "no-cache" {
		t.Errorf("expected Cache-Control no-cache, got %s", cc)
	}
	if conn := rr.Header().Get("Connection"); conn != "keep-alive" {
		t.Errorf("expected Connection keep-alive, got %s", conn)
	}
}
