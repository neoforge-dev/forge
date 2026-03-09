//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestPatternByIDOrRunsHandler_RunsSubroute(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patterns/some-id/runs", nil)
	w := httptest.NewRecorder()
	patternByIDOrRunsHandler(w, req)

	// Should query pattern_runs — returns 200 with empty list
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestPatternByIDOrRunsHandler_RunsSubroute_WithLimit(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patterns/some-id/runs?limit=10", nil)
	w := httptest.NewRecorder()
	patternByIDOrRunsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

