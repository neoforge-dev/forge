//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"os"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

// ---------------------------------------------------------------------------
// MigrateUp tests (migrate.go:242) - 64.7%
// ---------------------------------------------------------------------------

func TestWave262_MigrateUp_NoMigrations(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Already migrated by setupClaimTestDB, but should be idempotent
	err := MigrateUp(db)
	if err != nil {
		t.Errorf("MigrateUp should succeed on already-migrated DB: %v", err)
	}
}

func TestWave262_MigrateUp_ClosedDB(t *testing.T) {
	db, err := sql.Open("sqlite3", "/tmp/wave262_test.db")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	db.Close()
	defer os.Remove("/tmp/wave262_test.db")

	err = MigrateUp(db)
	if err == nil {
		t.Error("expected error with closed DB")
	}
}

// ---------------------------------------------------------------------------
// MigrateDown tests (migrate.go:311) - 70.8%
// ---------------------------------------------------------------------------

func TestWave262_MigrateDown_ZeroSteps(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// steps=0 should be a no-op
	err := MigrateDown(db, 0)
	if err != nil {
		t.Errorf("MigrateDown with steps=0 should succeed: %v", err)
	}
}

func TestWave262_MigrateDown_NegativeSteps(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// steps=-1 should be a no-op
	err := MigrateDown(db, -1)
	if err != nil {
		t.Errorf("MigrateDown with steps=-1 should succeed: %v", err)
	}
}

func TestWave262_MigrateDown_ClosedDB(t *testing.T) {
	db, err := sql.Open("sqlite3", "/tmp/wave262_down_test.db")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	db.Close()
	defer os.Remove("/tmp/wave262_down_test.db")

	err = MigrateDown(db, 1)
	if err == nil {
		t.Error("expected error with closed DB")
	}
}

// ---------------------------------------------------------------------------
// ensureMigrationsTable tests (migrate.go:396) - 71.4%
// ---------------------------------------------------------------------------

func TestWave262_EnsureMigrationsTable_ClosedDB(t *testing.T) {
	db, err := sql.Open("sqlite3", "/tmp/wave262_ensure_test.db")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	db.Close()
	defer os.Remove("/tmp/wave262_ensure_test.db")

	err = ensureMigrationsTable(db, SQLite)
	if err == nil {
		t.Error("expected error with closed DB")
	}
}

// ---------------------------------------------------------------------------
// Summary test
// ---------------------------------------------------------------------------

func TestWave262_CoverageSummary(t *testing.T) {
	t.Log("Wave 262 Coverage Targets:")
	t.Log("  - MigrateUp no migrations (migrate.go:242)")
	t.Log("  - MigrateUp closed DB (migrate.go:242)")
	t.Log("  - MigrateDown zero steps (migrate.go:311)")
	t.Log("  - MigrateDown negative steps (migrate.go:311)")
	t.Log("  - MigrateDown closed DB (migrate.go:311)")
	t.Log("  - ensureMigrationsTable closed DB (migrate.go:396)")
}
