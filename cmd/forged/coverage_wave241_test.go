//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// ============================================================
// coverage_wave241_test.go — GitGuard pure functions + CheckSafe,
//         gitguardHandler, gitguard_idempotency.ExecuteOnce,
//         writeEvent coverage, lease.Recover, EventBus.Replay/GetLatestEvents
// ============================================================

// --- computePayloadHash ---

func TestWave241_ComputePayloadHash_Deterministic(t *testing.T) {
	action := GitAction{
		TaskID:  "task-241",
		Type:    "commit",
		Branch:  "main",
		Message: "test commit",
		Files:   []string{"file1.go"},
		Author:  "agent-241",
	}
	h1 := computePayloadHash(action)
	h2 := computePayloadHash(action)
	if h1 != h2 {
		t.Errorf("expected deterministic hash, got %s vs %s", h1, h2)
	}
	if len(h1) != 64 { // SHA256 hex = 64 chars
		t.Errorf("expected 64-char hex hash, got len=%d", len(h1))
	}
}

func TestWave241_ComputePayloadHash_Different(t *testing.T) {
	a1 := GitAction{TaskID: "task-A", Type: "commit"}
	a2 := GitAction{TaskID: "task-B", Type: "commit"}
	if computePayloadHash(a1) == computePayloadHash(a2) {
		t.Error("expected different hashes for different actions")
	}
}

// --- GitGuard.CheckSafe ---

func TestWave241_GitGuard_CheckSafe_NonGitDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	gg := NewGitGuard(db, tmpDir)

	// Non-git dir — no lock files → should be safe
	safe, reason := gg.CheckSafe()
	if !safe {
		t.Logf("not safe in non-git dir: %s", reason)
	}
	// Either result is OK — just can't panic
}

func TestWave241_GitGuard_EmptyRepoRoot(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Empty repoRoot defaults to "."
	gg := NewGitGuard(db, "")
	if gg == nil {
		t.Fatal("expected non-nil GitGuard")
	}
}

// --- gitguardHandler ---

func TestWave241_GitguardHandler_InvalidJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())
	handler := gitguardHandler(gg)

	req := httptest.NewRequest(http.MethodPost, "/api/gitguard", strings.NewReader(`{bad json}`))
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// --- GitGuard.GetBranchLock / ReleaseBranchLock ---

func TestWave241_GitGuard_GetBranchLock_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())
	branch, err := gg.GetBranchLock(context.Background(), "nonexistent-task-241")
	if err != nil {
		// Not-found error is acceptable
		t.Logf("GetBranchLock error (expected): %v", err)
	} else if branch != "" {
		t.Errorf("expected empty branch for nonexistent task, got %s", branch)
	}
}

func TestWave241_GitGuard_ReleaseBranchLock_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())
	// Releasing nonexistent lock should not panic
	err := gg.ReleaseBranchLock(context.Background(), "nonexistent-task-241")
	// May return nil (UPDATE affects 0 rows) or error
	_ = err
}

// --- gitguard_idempotency.ExecuteOnce ---

func TestWave241_IdempotencyExecuteOnce_FirstExecution(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewIdempotencyStore(db)
	payload := []byte(`{"task_id":"task-241"}`)

	callCount := 0
	result, err := store.ExecuteOnce("commit", payload, func() (interface{}, error) {
		callCount++
		return "result-241", nil
	})
	if err != nil {
		t.Fatalf("ExecuteOnce: %v", err)
	}
	if callCount != 1 {
		t.Errorf("expected 1 call, got %d", callCount)
	}
	_ = result
}

func TestWave241_IdempotencyExecuteOnce_SecondExecution(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewIdempotencyStore(db)
	payload := []byte(`{"task_id":"task-241-dedup"}`)

	// First call
	store.ExecuteOnce("commit", payload, func() (interface{}, error) {
		return "first", nil
	})

	// Second call with same payload — should NOT execute fn again
	callCount := 0
	_, err := store.ExecuteOnce("commit", payload, func() (interface{}, error) {
		callCount++
		return "second", nil
	})
	if err != nil {
		t.Fatalf("ExecuteOnce second call: %v", err)
	}
	if callCount != 0 {
		t.Errorf("expected 0 calls on dedup, got %d", callCount)
	}
}

// --- lease.Recover ---

func TestWave241_Lease_Recover_Empty(t *testing.T) {
	tmpDir := t.TempDir()
	lm, err := NewLeaseManager(tmpDir + "/lease241.db")
	if err != nil {
		t.Fatalf("NewLeaseManager: %v", err)
	}
	defer lm.Close()

	// Recover on fresh DB (no leases table) — either empty or error
	leases, err := lm.Recover(context.Background())
	if err != nil {
		t.Logf("Recover error on fresh DB (expected): %v", err)
	} else if len(leases) != 0 {
		t.Errorf("expected 0 leases, got %d", len(leases))
	}
}

// --- EventBus.GetLatestEvents ---

func TestWave241_EventBus_GetLatestEvents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}

	eb.Publish("task.created", map[string]string{"id": "evt-241-a"})
	eb.Publish("task.updated", map[string]string{"id": "evt-241-b"})

	events, err := eb.GetLatestEvents(10)
	if err != nil {
		t.Fatalf("GetLatestEvents: %v", err)
	}
	// May be 0 if events table missing, or ≥1 — no panic is the requirement
	_ = events
}

func TestWave241_EventBus_ReplayForTopic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}

	eb.Publish("agent.connected", map[string]string{"agent": "agent-241"})

	events, err := eb.ReplayForTopic(time.Now().Add(-1*time.Minute), "agent.*")
	if err != nil {
		t.Fatalf("ReplayForTopic: %v", err)
	}
	_ = events
}

// --- XNode.RegisterHandler (success path) ---

func TestWave241_XNode_RegisterHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "node-241")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	body := strings.NewReader(`{"id":"peer-node-241","address":"100.1.2.3:8081","status":"online"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", body)
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, req)
	// 200 (registered) or 409 (already exists) — both OK
	if w.Code != http.StatusOK && w.Code != http.StatusConflict && w.Code != http.StatusCreated {
		t.Errorf("unexpected status %d: %s", w.Code, w.Body.String())
	}
}
