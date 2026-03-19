//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 189: handler_helpers.go + handlers_misc.go simple handlers
// Targets:
//   writeJSON         (handler_helpers.go:14)
//   requireMethod     (handler_helpers.go:22) — match / mismatch
//   withDB            (handler_helpers.go:33) — nil DB / with DB
//   healthHandler     (handlers_misc.go:28)   — returns 200 JSON
//   statusHandler     (handlers_misc.go:37)   — returns 200 JSON
//   releaseTaskHandler (handlers_misc.go:220) — GET→405, nil DB
//   extendLeaseHandler (handlers_misc.go:264) — GET→405, nil DB
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// writeJSON
// ---------------------------------------------------------------------------

func TestWave189_WriteJSON_SetsContentType(t *testing.T) {
	w := httptest.NewRecorder()
	writeJSON(w, map[string]string{"key": "value"})
	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected application/json, got %q", ct)
	}
}

func TestWave189_WriteJSON_EncodesData(t *testing.T) {
	w := httptest.NewRecorder()
	writeJSON(w, map[string]int{"count": 42})
	if !strings.Contains(w.Body.String(), "42") {
		t.Errorf("expected body to contain '42', got %q", w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// requireMethod
// ---------------------------------------------------------------------------

func TestWave189_RequireMethod_Match(t *testing.T) {
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "/", nil)
	if !requireMethod(w, r, http.MethodPost) {
		t.Error("expected true for matching method")
	}
}

func TestWave189_RequireMethod_Mismatch(t *testing.T) {
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodGet, "/", nil)
	if requireMethod(w, r, http.MethodPost) {
		t.Error("expected false for mismatched method")
	}
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// withDB
// ---------------------------------------------------------------------------

func TestWave189_WithDB_NilDB(t *testing.T) {
	setDBConn(nil)
	w := httptest.NewRecorder()
	called := false
	withDB(w, func(db *sql.DB) {
		called = true
	})
	if called {
		t.Error("expected fn not called when DB is nil")
	}
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave189_WithDB_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	w := httptest.NewRecorder()
	called := false
	withDB(w, func(gotDB *sql.DB) {
		called = true
	})
	if !called {
		t.Error("expected fn to be called with valid DB")
	}
}

// ---------------------------------------------------------------------------
// healthHandler
// ---------------------------------------------------------------------------

func TestWave189_HealthHandler_Returns200(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	healthHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

func TestWave189_HealthHandler_ReturnsJSON(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	healthHandler(w, req)
	if ct := w.Header().Get("Content-Type"); !strings.Contains(ct, "json") {
		t.Errorf("expected JSON content-type, got %q", ct)
	}
}

// ---------------------------------------------------------------------------
// statusHandler
// ---------------------------------------------------------------------------

func TestWave189_StatusHandler_Returns200(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/status", nil)
	w := httptest.NewRecorder()
	statusHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// releaseTaskHandler
// ---------------------------------------------------------------------------

func TestWave189_ReleaseTaskHandler_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/release", nil)
	w := httptest.NewRecorder()
	releaseTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}


// ---------------------------------------------------------------------------
// extendLeaseHandler
// ---------------------------------------------------------------------------

func TestWave189_ExtendLeaseHandler_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/extend-lease", nil)
	w := httptest.NewRecorder()
	extendLeaseHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

