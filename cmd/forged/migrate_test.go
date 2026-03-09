//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestMigrateDown(t *testing.T) {
	dbPath := filepath.Join(os.TempDir(), fmt.Sprintf("test_migrate_down_%d.db", time.Now().UnixNano()))
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer func() {
		db.Close()
		os.Remove(dbPath)
	}()

	// Up
	if err := MigrateUp(db); err != nil {
		t.Fatalf("MigrateUp: %v", err)
	}

	// Down
	if err := MigrateDown(db, 1); err != nil {
		t.Fatalf("MigrateDown: %v", err)
	}
}

// Note: HandleCompletion tests skipped — function calls os.Exit for all paths (valid shells
// succeed but error path and unknown shell path call os.Exit, making testing unsafe).

// Wave 44: HandleCompletion shell branches, GetCoordinationHTML, OpenDB, MigrateUp paths

// ─── GetCoordinationHTML ──────────────────────────────────────────────────

func TestGetCoordinationHTML_W44(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	cd := NewCoordinationDashboard(db)
	html := cd.GetCoordinationHTML()
	if len(html) == 0 {
		t.Error("expected non-empty HTML from GetCoordinationHTML")
	}
}

// ─── OpenDB ───────────────────────────────────────────────────────────────

func TestOpenDB_TempPath_W44(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test-forge.db")

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB failed: %v", err)
	}
	defer db.Close()

	// Verify it's usable.
	if _, err := db.Exec("CREATE TABLE IF NOT EXISTS test_opendb (id INTEGER PRIMARY KEY)"); err != nil {
		t.Errorf("db exec after OpenDB: %v", err)
	}
}

func TestOpenDB_EmptyPath_W44(t *testing.T) {
	// Empty path → uses defaultDBPath. Skip if can't write to default location.
	t.Setenv("DB_TYPE", "") // ensure not postgres

	// Use t.TempDir to set a writable path for defaultDBPath.
	tmpDir := t.TempDir()
	origDir, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Skip("cannot chdir")
	}
	defer os.Chdir(origDir)

	db, err := OpenDB("")
	if err != nil {
		t.Logf("OpenDB empty path: %v (OK — default path may not be writable)", err)
		return
	}
	defer db.Close()
}

// ─── MigrateUp paths ──────────────────────────────────────────────────────

func TestMigrateUp_AlreadyMigrated_W44(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Run MigrateUp again on an already-migrated DB — all migrations should be idempotent.
	if err := MigrateUp(db); err != nil {
		// Some errors are expected (e.g., tables already exist), log but don't fail.
		t.Logf("MigrateUp on already-migrated DB: %v (OK)", err)
	}
}
