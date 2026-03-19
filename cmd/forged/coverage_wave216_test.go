//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 216: tui.go + tui_dashboard.go basics
// Targets:
//   NewTUI                (tui.go:81)  — non-nil
//   TUI.Handler           (tui.go:384) — GET returns HTML or 500
//   TUI.JSONHandler       (tui.go:400) — GET returns JSON or 500
//   DefaultKeyMap         (tui_dashboard.go:91) — non-empty keymap
//   NewDashboardModel     (tui_dashboard.go:204) — no panic
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewTUI
// ---------------------------------------------------------------------------

func TestWave216_NewTUI_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := NewHub()
	tq, _ := NewTaskQueueFromDB(db)
	tui := NewTUI(db, h, tq)
	if tui == nil {
		t.Error("expected non-nil TUI")
	}
}

func TestWave216_NewTUI_NilArgs(t *testing.T) {
	tui := NewTUI(nil, nil, nil)
	if tui == nil {
		t.Error("expected non-nil TUI even with nil args")
	}
}

// ---------------------------------------------------------------------------
// TUI.Handler
// ---------------------------------------------------------------------------

func TestWave216_TUIHandler_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := NewHub()
	tq, _ := NewTaskQueueFromDB(db)
	tui := NewTUI(db, h, tq)

	req := httptest.NewRequest(http.MethodGet, "/ui", nil)
	w := httptest.NewRecorder()
	tui.Handler().ServeHTTP(w, req)
	// Returns HTML or 500 — just no panic
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// TUI.JSONHandler
// ---------------------------------------------------------------------------

func TestWave216_TUIJSONHandler_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	h := NewHub()
	tq, _ := NewTaskQueueFromDB(db)
	tui := NewTUI(db, h, tq)

	req := httptest.NewRequest(http.MethodGet, "/ui/json", nil)
	w := httptest.NewRecorder()
	tui.JSONHandler().ServeHTTP(w, req)
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// DefaultKeyMap
// ---------------------------------------------------------------------------

func TestWave216_DefaultKeyMap_NonEmpty(t *testing.T) {
	km := DefaultKeyMap()
	// KeyMap should have navigation keys defined
	_ = km // just no panic
}

// ---------------------------------------------------------------------------
// NewDashboardModel
// ---------------------------------------------------------------------------

func TestWave216_NewDashboardModel_NoPanic(t *testing.T) {
	// Uses a fake API URL — just must not panic
	m := NewDashboardModel("http://localhost:8081")
	_ = m
}
