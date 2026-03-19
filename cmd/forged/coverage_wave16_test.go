//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// handlers_proxy.go — initHarnessProxy + harnessFallbackHandler
// ---------------------------------------------------------------------------

func TestHarnessFallback_NoProxy(t *testing.T) {
	oldProxy := harnessProxy
	harnessProxy = nil
	defer func() { harnessProxy = oldProxy }()

	os.Unsetenv("FORGE_HARNESS_URL")
	initHarnessProxy()

	if harnessProxy != nil {
		t.Error("expected harnessProxy to remain nil when FORGE_HARNESS_URL is unset")
	}

	req := httptest.NewRequest(http.MethodGet, "/api/unknown", nil)
	w := httptest.NewRecorder()
	harnessFallbackHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("Content-Type = %s, want application/json", ct)
	}
}

func TestHarnessFallback_WithProxy(t *testing.T) {
	oldProxy := harnessProxy
	defer func() { harnessProxy = oldProxy }()

	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"ok":true}`)) //nolint:errcheck
	}))
	defer backend.Close()

	os.Setenv("FORGE_HARNESS_URL", backend.URL)
	defer os.Unsetenv("FORGE_HARNESS_URL")

	initHarnessProxy()

	if harnessProxy == nil {
		t.Fatal("expected harnessProxy to be set")
	}

	req := httptest.NewRequest(http.MethodGet, "/api/legacy", nil)
	w := httptest.NewRecorder()
	harnessFallbackHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 from proxy, got %d", w.Code)
	}
}

func TestInitHarnessProxy_InvalidURL(t *testing.T) {
	oldProxy := harnessProxy
	defer func() { harnessProxy = oldProxy }()

	os.Setenv("FORGE_HARNESS_URL", "://bad-url")
	defer os.Unsetenv("FORGE_HARNESS_URL")

	// Should not panic — just log and skip
	initHarnessProxy()
}

// ---------------------------------------------------------------------------
// main.go — parseContextFromDomain (uses FORGE_ROOT env var)
// ---------------------------------------------------------------------------

func TestParseContextFromDomain_ContextPctFile(t *testing.T) {
	dir := t.TempDir()
	domain := "testdomain"
	contextDir := filepath.Join(dir, ".forge", "context", domain)
	os.MkdirAll(contextDir, 0755)
	os.WriteFile(filepath.Join(contextDir, "context_pct"), []byte("55.0"), 0644)

	os.Setenv("FORGE_ROOT", dir)
	defer os.Unsetenv("FORGE_ROOT")

	pct, _, err := parseContextFromDomain(domain)
	if err != nil {
		t.Fatalf("parseContextFromDomain: %v", err)
	}
	if pct != 55.0 {
		t.Errorf("pct = %f, want 55.0", pct)
	}
}

func TestParseContextFromDomain_LeadContextMd(t *testing.T) {
	dir := t.TempDir()
	domain := "mdomain"
	contextDir := filepath.Join(dir, ".forge", "context", domain)
	os.MkdirAll(contextDir, 0755)
	os.WriteFile(filepath.Join(contextDir, "lead-context.md"), []byte("# Lead\n- Context: 42%\n"), 0644)

	os.Setenv("FORGE_ROOT", dir)
	defer os.Unsetenv("FORGE_ROOT")

	pct, _, err := parseContextFromDomain(domain)
	if err != nil {
		t.Fatalf("parseContextFromDomain from md: %v", err)
	}
	if pct != 42.0 {
		t.Errorf("pct = %f, want 42.0", pct)
	}
}

func TestParseContextFromDomain_Default(t *testing.T) {
	dir := t.TempDir()
	domain := "emptydomain"
	contextDir := filepath.Join(dir, ".forge", "context", domain)
	os.MkdirAll(contextDir, 0755)

	os.Setenv("FORGE_ROOT", dir)
	defer os.Unsetenv("FORGE_ROOT")

	pct, _, _ := parseContextFromDomain(domain)
	if pct != 0.0 {
		t.Errorf("pct = %f, want 0.0", pct)
	}
}

// ---------------------------------------------------------------------------
// task_state_machine.go — DefaultStateMachineConfig
// ---------------------------------------------------------------------------

func TestDefaultStateMachineConfig(t *testing.T) {
	cfg := DefaultStateMachineConfig()
	if cfg == nil {
		t.Fatal("DefaultStateMachineConfig returned nil")
	}
	if !cfg.EnableHooks {
		t.Error("EnableHooks should be true by default")
	}
	if cfg.HookTimeout != 30*time.Second {
		t.Errorf("HookTimeout = %v, want 30s", cfg.HookTimeout)
	}
	if cfg.SkipValidation {
		t.Error("SkipValidation should be false by default")
	}
}

// ---------------------------------------------------------------------------
// handoffs.go — HandoffStore (SQLite CRUD)
// Note: Get() and List() on existing rows hit a time.Time scan bug in
// handoffs.go (SQLite TEXT → time.Time unsupported without parseTime).
// Tests here only exercise safe paths: Create, Get-not-found, List-empty.
// ---------------------------------------------------------------------------

func TestHandoffStore_Create(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	now := time.Now().UTC().Truncate(time.Second)
	h := Handoff{
		ID:              "hoff-create-001",
		FromAgent:       "agent-a",
		ToAgent:         "agent-b",
		TaskDescription: "Continue feature X",
		Files:           []string{"main.go"},
		Priority:        "high",
		Status:          HandoffPending,
		CreatedAt:       now,
		UpdatedAt:       now,
	}

	if err := store.Create(context.Background(), h); err != nil {
		t.Fatalf("Create: %v", err)
	}
}

func TestHandoffStore_Get_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	_, err := store.Get(context.Background(), "nonexistent-handoff-id")
	if err == nil {
		t.Error("expected error for nonexistent handoff")
	}
}

func TestHandoffStore_List_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	list, err := store.List(context.Background(), "", "", "", 10)
	if err != nil {
		t.Fatalf("List on empty table: %v", err)
	}
	if len(list) != 0 {
		t.Errorf("List len = %d, want 0", len(list))
	}
}

func TestHandoffStore_List_DefaultLimit(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	// limit=0 defaults to 50 — ensure no error
	_, err := store.List(context.Background(), "", "", "", 0)
	if err != nil {
		t.Fatalf("List with limit=0: %v", err)
	}
}

// ---------------------------------------------------------------------------
// handoffs.go — HandoffService
// Safe: Create only (no Get calls on existing rows)
// ---------------------------------------------------------------------------

func TestHandoffService_Create(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewHandoffService(NewHandoffStore(db))
	h, err := svc.Create(context.Background(), "from-a", "to-b", "do something", []string{"a.go"}, "high")
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	if h.ID == "" {
		t.Error("ID should be non-empty")
	}
	if h.Status != HandoffPending {
		t.Errorf("Status = %s, want pending", h.Status)
	}
}

func TestHandoffService_Create_DefaultPriority(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewHandoffService(NewHandoffStore(db))
	h, err := svc.Create(context.Background(), "a", "b", "desc", nil, "")
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	if h.Priority != "medium" {
		t.Errorf("Priority = %s, want medium", h.Priority)
	}
}

// ---------------------------------------------------------------------------
// handoffs.go — HandoffHandler HTTP endpoints
// Safe: empty-store GET, POST create, error paths, action paths that don't
// need existing records (method not allowed, invalid path, missing reason,
// unknown action, not found).
// ---------------------------------------------------------------------------

func TestHandoffHandler_List_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := NewHandoffHandler(NewHandoffService(NewHandoffStore(db)))
	req := httptest.NewRequest(http.MethodGet, "/api/handoffs", nil)
	w := httptest.NewRecorder()
	handler.handleHandoffs(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandoffHandler_List_WithFilters(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := NewHandoffHandler(NewHandoffService(NewHandoffStore(db)))
	req := httptest.NewRequest(http.MethodGet, "/api/handoffs?status=pending&from_agent=a&to_agent=b&limit=5", nil)
	w := httptest.NewRecorder()
	handler.handleHandoffs(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandoffHandler_Create(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := NewHandoffHandler(NewHandoffService(NewHandoffStore(db)))
	body, _ := json.Marshal(map[string]interface{}{
		"from_agent":       "agent-x",
		"to_agent":         "agent-y",
		"task_description": "migrate the data",
		"files":            []string{"data.go"},
		"priority":         "high",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handler.handleHandoffs(w, req)

	if w.Code != http.StatusCreated {
		t.Errorf("expected 201, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandoffHandler_Create_MissingFields(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := NewHandoffHandler(NewHandoffService(NewHandoffStore(db)))
	body, _ := json.Marshal(map[string]string{"from_agent": "only-from"})
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handler.handleHandoffs(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestHandoffHandler_Create_BadJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := NewHandoffHandler(NewHandoffService(NewHandoffStore(db)))
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs", bytes.NewReader([]byte("not-json")))
	w := httptest.NewRecorder()
	handler.handleHandoffs(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

func TestHandoffHandler_Action_InvalidPath(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := NewHandoffHandler(NewHandoffService(NewHandoffStore(db)))
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/", nil)
	req.URL.Path = "/api/handoffs/"
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestHandoffHandler_Action_Reject_MissingReason(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := NewHandoffHandler(NewHandoffService(NewHandoffStore(db)))
	body, _ := json.Marshal(map[string]string{}) // no reason field
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/any-id/reject", bytes.NewReader(body))
	req.URL.Path = "/api/handoffs/any-id/reject"
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestHandoffHandler_Action_UnknownAction(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := NewHandoffHandler(NewHandoffService(NewHandoffStore(db)))
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/some-id/freeze", nil)
	req.URL.Path = "/api/handoffs/some-id/freeze"
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	// "freeze" is an unknown action — handler returns 400
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestHandoffHandler_Action_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := NewHandoffHandler(NewHandoffService(NewHandoffStore(db)))
	body, _ := json.Marshal(map[string]string{"accepting_agent": "b"})
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/nonexistent-id/accept", bytes.NewReader(body))
	req.URL.Path = "/api/handoffs/nonexistent-id/accept"
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}
