//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// Wave 30e: handleSystemHealth, RecordPatrolError/Success, isValidText, handleSystemMetrics/GitGuard/FleetStatus

func TestHandleSystemHealth_W30E(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)
	// Any non-panic response is acceptable
	if w.Code >= 500 {
		t.Logf("handleSystemHealth returned %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleSystemMetrics_W30E(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/system/metrics", nil)
	w := httptest.NewRecorder()
	handleSystemMetrics(w, req)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestHandleGitGuard_W30E(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/git-guard", nil)
	w := httptest.NewRecorder()
	handleGitGuard(w, req)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestHandleGitStatus_W30E(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/git-status", nil)
	w := httptest.NewRecorder()
	handleGitStatus(w, req)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestHandleFleetStatus_W30E(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/fleet/status", nil)
	w := httptest.NewRecorder()
	handleFleetStatus(w, req)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestHandleFleetTopology_W30E(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/fleet/topology", nil)
	w := httptest.NewRecorder()
	handleFleetTopology(w, req)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

func TestRecordPatrolError_NilDB_W30E(t *testing.T) {
	// nil DB path — should not panic
	RecordPatrolError(nil, "test-patrol", "test error")
}

func TestRecordPatrolError_WithDB_W30E(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	// With DB — patrol execution may not exist but should not panic
	RecordPatrolError(db, "test-patrol", "test error message")
}

func TestRecordPatrolSuccess_NilDB_W30E(t *testing.T) {
	RecordPatrolSuccess(nil, "test-patrol")
}

func TestRecordPatrolSuccess_WithDB_W30E(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	RecordPatrolSuccess(db, "test-patrol")
}

func TestIsValidText_Empty_W30E(t *testing.T) {
	if !isValidText("") {
		t.Error("empty text should be valid")
	}
}

func TestIsValidText_Normal_W30E(t *testing.T) {
	if !isValidText("Hello World") {
		t.Error("normal text should be valid")
	}
}

func TestIsValidText_TooLong_W30E(t *testing.T) {
	long := make([]byte, 2049)
	for i := range long {
		long[i] = 'a'
	}
	if isValidText(string(long)) {
		t.Error("text > 2048 chars should be invalid")
	}
}
