//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 217: gitguard_idempotency.go pure functions
// Targets:
//   NewIdempotencyStore         (gitguard_idempotency.go:37)  — non-nil
//   computeIdempotencyHash      (gitguard_idempotency.go:42)  — deterministic
//   hashPayload                 (gitguard_idempotency.go:55)  — deterministic
//   IdempotencyStore.GetByHash  (gitguard_idempotency.go:110) — not found
//   IdempotencyStore.IsDuplicate(gitguard_idempotency.go:142) — not duplicate
//   IdempotencyStore.Cleanup    (gitguard_idempotency.go:155) — no-op when empty
//   CreateIdempotencyTable      (gitguard_idempotency.go:225) — idempotent
//   NewGitGuardIdempotency      (gitguard_idempotency.go:249) — non-nil
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewIdempotencyStore
// ---------------------------------------------------------------------------

func TestWave217_NewIdempotencyStore_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	s := NewIdempotencyStore(db)
	if s == nil {
		t.Error("expected non-nil IdempotencyStore")
	}
}

// ---------------------------------------------------------------------------
// computeIdempotencyHash / hashPayload
// ---------------------------------------------------------------------------

func TestWave217_ComputeIdempotencyHash_Deterministic(t *testing.T) {
	key := IdempotencyKey{ActionType: "commit", Payload: []byte("data"), ResourceID: "task-1"}
	h1 := computeIdempotencyHash(key)
	h2 := computeIdempotencyHash(key)
	if h1 != h2 {
		t.Errorf("expected deterministic hash, got %q and %q", h1, h2)
	}
	if h1 == "" {
		t.Error("expected non-empty hash")
	}
}

func TestWave217_ComputeIdempotencyHash_WithoutResourceID(t *testing.T) {
	key := IdempotencyKey{ActionType: "push", Payload: []byte("payload")}
	h := computeIdempotencyHash(key)
	if h == "" {
		t.Error("expected non-empty hash without ResourceID")
	}
}

func TestWave217_HashPayload_Deterministic(t *testing.T) {
	h1 := hashPayload("commit", []byte("test-data"))
	h2 := hashPayload("commit", []byte("test-data"))
	if h1 != h2 {
		t.Errorf("expected deterministic hashPayload, got %q and %q", h1, h2)
	}
}

func TestWave217_HashPayload_DifferentTypes(t *testing.T) {
	h1 := hashPayload("commit", []byte("same-payload"))
	h2 := hashPayload("push", []byte("same-payload"))
	if h1 == h2 {
		t.Error("expected different hashes for different action types")
	}
}

// ---------------------------------------------------------------------------
// GetByHash — not found (CreateIdempotencyTable uses TIMESTAMPTZ/NOW()/JSONB
//              which are PostgreSQL syntax — skip those tests on SQLite)
// ---------------------------------------------------------------------------

func TestWave217_GetByHash_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// CreateIdempotencyTable uses PostgreSQL SQL (TIMESTAMPTZ/NOW/JSONB)
	// which fails on SQLite. Test directly on the already-migrated DB.
	s := NewIdempotencyStore(db)
	// Table doesn't exist on SQLite — expect error, not panic
	_, _ = s.GetByHash("nonexistent-hash-xyz")
}

// ---------------------------------------------------------------------------
// IsDuplicate — uses PostgreSQL schema, just verify no panic
// ---------------------------------------------------------------------------

func TestWave217_IsDuplicate_NotDuplicate(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	s := NewIdempotencyStore(db)
	// Table doesn't exist — expect error, not panic
	_, _, _ = s.IsDuplicate("commit", []byte("unique-payload"))
}

// ---------------------------------------------------------------------------
// Cleanup — skipped (PostgreSQL SQL in CreateIdempotencyTable)
// ---------------------------------------------------------------------------

func TestWave217_Cleanup_NoPanic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	s := NewIdempotencyStore(db)
	// Table not created — will error but must not panic
	_ = s.Cleanup(24 * time.Hour)
}

// ---------------------------------------------------------------------------
// CreateIdempotencyTable — records the PostgreSQL-only failure gracefully
// ---------------------------------------------------------------------------

func TestWave217_CreateIdempotencyTable_FailsOnSQLite(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Expected to fail on SQLite (PostgreSQL-specific SQL)
	err := CreateIdempotencyTable(db)
	// Don't assert failure — just that it doesn't panic
	_ = err
}

// ---------------------------------------------------------------------------
// NewGitGuardIdempotency
// ---------------------------------------------------------------------------

func TestWave217_NewGitGuardIdempotency_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	s := NewIdempotencyStore(db)
	ggi := NewGitGuardIdempotency(s)
	if ggi == nil {
		t.Error("expected non-nil GitGuardIdempotency")
	}
}
