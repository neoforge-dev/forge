//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 177: handoffs.go pure functions + store/service, coordination.go
// Targets:
//   parseHandoffTime       (handoffs.go:85)  — RFC3339/RFC3339Nano/custom/invalid
//   parseHandoffNullTime   (handoffs.go:94)  — null/valid
//   NewHandoffStore        (handoffs.go:62)
//   NewHandoffService      (handoffs.go:249)
//   HandoffStore.Create    (handoffs.go:66)
//   HandoffStore.Get       (handoffs.go:102) — found / not found
//   HandoffStore.List      (handoffs.go:168) — empty filter
//   HandoffService.Create  (handoffs.go:254) — default priority
//   truncateString         (coordination.go:552) — short / long
//   NewCoordinationDashboard (coordination.go:59)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// parseHandoffTime
// ---------------------------------------------------------------------------

func TestWave177_ParseHandoffTime_RFC3339(t *testing.T) {
	ts := "2026-03-10T10:00:00Z"
	result := parseHandoffTime(ts)
	if result.IsZero() {
		t.Errorf("expected non-zero time for %q", ts)
	}
}

func TestWave177_ParseHandoffTime_RFC3339Nano(t *testing.T) {
	ts := "2026-03-10T10:00:00.123456789Z"
	result := parseHandoffTime(ts)
	if result.IsZero() {
		t.Errorf("expected non-zero time for %q", ts)
	}
}

func TestWave177_ParseHandoffTime_CustomFormat(t *testing.T) {
	ts := "2026-03-10 10:00:00"
	result := parseHandoffTime(ts)
	if result.IsZero() {
		t.Errorf("expected non-zero time for %q", ts)
	}
}

func TestWave177_ParseHandoffTime_Invalid(t *testing.T) {
	ts := "not-a-time"
	result := parseHandoffTime(ts)
	if !result.IsZero() {
		t.Error("expected zero time for invalid input")
	}
}

// ---------------------------------------------------------------------------
// parseHandoffNullTime
// ---------------------------------------------------------------------------

func TestWave177_ParseHandoffNullTime_Null(t *testing.T) {
	ns := sql.NullString{Valid: false}
	result := parseHandoffNullTime(ns)
	if result != nil {
		t.Error("expected nil for null string")
	}
}

func TestWave177_ParseHandoffNullTime_Empty(t *testing.T) {
	ns := sql.NullString{Valid: true, String: ""}
	result := parseHandoffNullTime(ns)
	if result != nil {
		t.Error("expected nil for empty string")
	}
}

func TestWave177_ParseHandoffNullTime_Valid(t *testing.T) {
	ns := sql.NullString{Valid: true, String: "2026-03-10T10:00:00Z"}
	result := parseHandoffNullTime(ns)
	if result == nil {
		t.Error("expected non-nil for valid time string")
	}
}

// ---------------------------------------------------------------------------
// NewHandoffStore
// ---------------------------------------------------------------------------

func TestWave177_NewHandoffStore_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	s := NewHandoffStore(db)
	if s == nil {
		t.Error("expected non-nil HandoffStore")
	}
}

// ---------------------------------------------------------------------------
// NewHandoffService
// ---------------------------------------------------------------------------

func TestWave177_NewHandoffService_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	if svc == nil {
		t.Error("expected non-nil HandoffService")
	}
}

// ---------------------------------------------------------------------------
// HandoffStore.Create + Get
// ---------------------------------------------------------------------------

func TestWave177_HandoffStore_CreateAndGet(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewHandoffStore(db)

	now := time.Now()
	h := Handoff{
		ID:              "handoff-wave177",
		FromAgent:       "kimi-1",
		ToAgent:         "kimi-2",
		TaskDescription: "coverage wave test",
		Files:           []string{"file1.go"},
		Priority:        "high",
		Status:          HandoffPending,
		CreatedAt:       now,
		UpdatedAt:       now,
	}

	if err := store.Create(context.Background(), h); err != nil {
		t.Fatalf("Create: %v", err)
	}

	got, err := store.Get(context.Background(), "handoff-wave177")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.ID != "handoff-wave177" {
		t.Errorf("expected ID 'handoff-wave177', got %q", got.ID)
	}
	if got.FromAgent != "kimi-1" {
		t.Errorf("expected FromAgent 'kimi-1', got %q", got.FromAgent)
	}
}

func TestWave177_HandoffStore_Get_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewHandoffStore(db)

	_, err := store.Get(context.Background(), "nonexistent-handoff")
	if err == nil {
		t.Error("expected error for non-existent handoff")
	}
}

// ---------------------------------------------------------------------------
// HandoffStore.List
// ---------------------------------------------------------------------------

func TestWave177_HandoffStore_List_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewHandoffStore(db)

	// Empty filter — should return empty list without error
	handoffs, err := store.List(context.Background(), "", "", "", 50)
	if err != nil {
		t.Errorf("List: %v", err)
	}
	_ = handoffs
}

// ---------------------------------------------------------------------------
// HandoffService.Create (default priority applied when empty)
// ---------------------------------------------------------------------------

func TestWave177_HandoffService_Create_DefaultPriority(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewHandoffStore(db)
	svc := NewHandoffService(store)

	h, err := svc.Create(context.Background(), "kimi-1", "kimi-2", "do something", nil, "")
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	if h.Priority != "medium" {
		t.Errorf("expected default priority 'medium', got %q", h.Priority)
	}
	if h.Status != HandoffPending {
		t.Errorf("expected pending status, got %q", h.Status)
	}
}

func TestWave177_HandoffService_Create_ExplicitPriority(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewHandoffStore(db)
	svc := NewHandoffService(store)

	h, err := svc.Create(context.Background(), "kimi-1", "kimi-2", "urgent task", []string{"a.go"}, "high")
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	if h.Priority != "high" {
		t.Errorf("expected priority 'high', got %q", h.Priority)
	}
}

// ---------------------------------------------------------------------------
// truncateString
// ---------------------------------------------------------------------------

func TestWave177_TruncateString_Short(t *testing.T) {
	got := truncateString("hello", 10)
	if got != "hello" {
		t.Errorf("expected 'hello', got %q", got)
	}
}

func TestWave177_TruncateString_Exact(t *testing.T) {
	got := truncateString("hello", 5)
	if got != "hello" {
		t.Errorf("expected 'hello', got %q", got)
	}
}

func TestWave177_TruncateString_Long(t *testing.T) {
	got := truncateString("hello world", 8)
	if got != "hello..." {
		t.Errorf("expected 'hello...', got %q", got)
	}
}

// ---------------------------------------------------------------------------
// NewCoordinationDashboard
// ---------------------------------------------------------------------------

func TestWave177_NewCoordinationDashboard_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cd := NewCoordinationDashboard(db)
	if cd == nil {
		t.Error("expected non-nil CoordinationDashboard")
	}
}
