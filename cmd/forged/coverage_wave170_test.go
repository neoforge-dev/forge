//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 170: gitguard_idempotency.go pure functions + constructor
// Targets:
//   computeIdempotencyHash  (gitguard_idempotency.go:42) — deterministic hash
//   hashPayload             (gitguard_idempotency.go:55) — convenience hash
//   NewIdempotencyStore     (gitguard_idempotency.go:37) — constructor
//   NewGitGuardIdempotency  (gitguard_idempotency.go:249)— constructor
//   IdempotencyStore.IsDuplicate (gitguard_idempotency.go:142)— no table = err ignored
//   IdempotencyStore.Cleanup     (gitguard_idempotency.go:155)— no table = err
//   IdempotencyStore.GetByHash   (gitguard_idempotency.go:110)— not found = nil + no-rows err
// ---------------------------------------------------------------------------

// createIdempotentActionsTable creates the table with SQLite-compatible DDL.
func createIdempotentActionsTableSQLite(t *testing.T, db *sql.DB) {
	t.Helper()
	_, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS idempotent_actions (
			id TEXT PRIMARY KEY,
			type TEXT NOT NULL,
			payload_hash TEXT NOT NULL UNIQUE,
			executed_at TEXT NOT NULL DEFAULT (datetime('now')),
			result TEXT
		)
	`)
	if err != nil {
		t.Fatalf("create idempotent_actions: %v", err)
	}
}

// ---------------------------------------------------------------------------
// computeIdempotencyHash
// ---------------------------------------------------------------------------

func TestWave170_ComputeIdempotencyHash_Deterministic(t *testing.T) {
	key := IdempotencyKey{ActionType: "git_commit", Payload: []byte(`{"msg":"test"}`)}
	h1 := computeIdempotencyHash(key)
	h2 := computeIdempotencyHash(key)
	if h1 != h2 {
		t.Error("expected deterministic hash")
	}
	if h1 == "" {
		t.Error("expected non-empty hash")
	}
}

func TestWave170_ComputeIdempotencyHash_DifferentInputs(t *testing.T) {
	k1 := IdempotencyKey{ActionType: "a", Payload: []byte("data")}
	k2 := IdempotencyKey{ActionType: "b", Payload: []byte("data")}
	if computeIdempotencyHash(k1) == computeIdempotencyHash(k2) {
		t.Error("expected different hashes for different action types")
	}
}

func TestWave170_ComputeIdempotencyHash_WithResourceID(t *testing.T) {
	k1 := IdempotencyKey{ActionType: "commit", Payload: []byte("data"), ResourceID: ""}
	k2 := IdempotencyKey{ActionType: "commit", Payload: []byte("data"), ResourceID: "task-1"}
	if computeIdempotencyHash(k1) == computeIdempotencyHash(k2) {
		t.Error("expected different hashes when ResourceID differs")
	}
}

// ---------------------------------------------------------------------------
// hashPayload
// ---------------------------------------------------------------------------

func TestWave170_HashPayload_Deterministic(t *testing.T) {
	h1 := hashPayload("git_commit", []byte("payload"))
	h2 := hashPayload("git_commit", []byte("payload"))
	if h1 != h2 {
		t.Error("expected deterministic hashPayload")
	}
}

func TestWave170_HashPayload_DifferentPayloads(t *testing.T) {
	h1 := hashPayload("git_commit", []byte("payload1"))
	h2 := hashPayload("git_commit", []byte("payload2"))
	if h1 == h2 {
		t.Error("expected different hashes for different payloads")
	}
}

// ---------------------------------------------------------------------------
// NewIdempotencyStore
// ---------------------------------------------------------------------------

func TestWave170_NewIdempotencyStore_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	s := NewIdempotencyStore(db)
	if s == nil {
		t.Error("expected non-nil IdempotencyStore")
	}
}

// ---------------------------------------------------------------------------
// NewGitGuardIdempotency
// ---------------------------------------------------------------------------

func TestWave170_NewGitGuardIdempotency_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	s := NewIdempotencyStore(db)
	g := NewGitGuardIdempotency(s)
	if g == nil {
		t.Error("expected non-nil GitGuardIdempotency")
	}
}

// ---------------------------------------------------------------------------
// IdempotencyStore.GetByHash
// ---------------------------------------------------------------------------

func TestWave170_GetByHash_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	createIdempotentActionsTableSQLite(t, db)
	s := NewIdempotencyStore(db)

	action, err := s.GetByHash("nonexistent-hash-xyz")
	// sql.ErrNoRows is expected — GetByHash returns nil, ErrNoRows
	if err != nil && err != sql.ErrNoRows {
		t.Errorf("unexpected error: %v", err)
	}
	if action != nil {
		t.Error("expected nil action for nonexistent hash")
	}
}

// ---------------------------------------------------------------------------
// IdempotencyStore.IsDuplicate
// ---------------------------------------------------------------------------

func TestWave170_IsDuplicate_NewAction(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	createIdempotentActionsTableSQLite(t, db)
	s := NewIdempotencyStore(db)

	isDup, existing, err := s.IsDuplicate("test_action", []byte("unique-payload-wave170"))
	if err != nil {
		t.Fatalf("IsDuplicate: %v", err)
	}
	if isDup {
		t.Error("expected false for first action")
	}
	if existing != nil {
		t.Error("expected nil existing for first action")
	}
}

// ---------------------------------------------------------------------------
// IdempotencyStore.Cleanup
// ---------------------------------------------------------------------------

func TestWave170_Cleanup_EmptyTable(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	createIdempotentActionsTableSQLite(t, db)
	s := NewIdempotencyStore(db)

	// Cleanup with long duration should affect 0 rows (empty table)
	if err := s.Cleanup(24 * time.Hour); err != nil {
		t.Errorf("Cleanup: %v", err)
	}
}

// ---------------------------------------------------------------------------
// ExecuteOnce — idempotency caching
// ---------------------------------------------------------------------------

func TestWave170_ExecuteOnce_FirstCall(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	createIdempotentActionsTableSQLite(t, db)
	s := NewIdempotencyStore(db)

	callCount := 0
	result, err := s.ExecuteOnce("test_op", []byte("payload-once"), func() (interface{}, error) {
		callCount++
		return "result-value", nil
	})
	if err != nil {
		t.Fatalf("ExecuteOnce: %v", err)
	}
	if result != "result-value" {
		t.Errorf("unexpected result: %v", result)
	}
	if callCount != 1 {
		t.Errorf("expected 1 call, got %d", callCount)
	}
}

func TestWave170_ExecuteOnce_SecondCallCached(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	createIdempotentActionsTableSQLite(t, db)
	s := NewIdempotencyStore(db)

	payload := []byte("payload-cached")
	// First call
	s.ExecuteOnce("test_cache", payload, func() (interface{}, error) {
		return "cached-value", nil
	})

	// Second call — should return cached result without calling fn again
	callCount := 0
	result, err := s.ExecuteOnce("test_cache", payload, func() (interface{}, error) {
		callCount++
		return "should-not-be-called", nil
	})
	if err != nil {
		t.Fatalf("ExecuteOnce second: %v", err)
	}
	if callCount != 0 {
		t.Error("expected fn to NOT be called on second ExecuteOnce (cached)")
	}
	_ = result
}
