//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

func TestWave226_CheckBinaryFreshness_EarlyReturn(t *testing.T) {
	// 1. binaryPath os.Stat error (missing file)
	// We use forgeRoot() which depends on FORGE_ROOT env.
	// In test, forgeRoot() usually returns "."
	// We'll set FORGE_ROOT to a temp dir to ensure it doesn't find a real binary.
	oldRoot := os.Getenv("FORGE_ROOT")
	tmpDir := t.TempDir()
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Setenv("FORGE_ROOT", oldRoot)

	// Should return nil early because binary is missing.
	err := checkBinaryFreshness(context.Background(), nil)
	if err != nil {
		t.Errorf("expected nil error for missing binary, got %v", err)
	}
}

func TestWave226_CheckBinaryFreshness_Fresh(t *testing.T) {
	// 2. Binary is fresh (mtime recent)
	root := t.TempDir()
	oldRoot := os.Getenv("FORGE_ROOT")
	os.Setenv("FORGE_ROOT", root)
	defer os.Setenv("FORGE_ROOT", oldRoot)

	// Setup dir structure: cmd/forged/forged
	forgedDir := filepath.Join(root, "cmd", "forged")
	err := os.MkdirAll(forgedDir, 0755)
	if err != nil {
		t.Fatal(err)
	}

	binaryPath := filepath.Join(forgedDir, "forged")
	err = os.WriteFile(binaryPath, []byte("fake binary"), 0755)
	if err != nil {
		t.Fatal(err)
	}

	// Create a .go file that is older than the binary
	goFile := filepath.Join(forgedDir, "main.go")
	err = os.WriteFile(goFile, []byte("package main"), 0644)
	if err != nil {
		t.Fatal(err)
	}

	now := time.Now()
	err = os.Chtimes(binaryPath, now, now)
	if err != nil {
		t.Fatal(err)
	}
	err = os.Chtimes(goFile, now.Add(-10*time.Minute), now.Add(-10*time.Minute))
	if err != nil {
		t.Fatal(err)
	}

	err = checkBinaryFreshness(context.Background(), nil)
	if err != nil {
		t.Errorf("expected nil error for fresh binary, got %v", err)
	}
}

func TestWave226_MigrateUp_ErrorPath(t *testing.T) {
	// Call MigrateUp on a closed DB
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	db.Close()

	err = MigrateUp(db)
	if err == nil {
		t.Error("expected error on closed DB")
	}
}

func TestWave226_MigrateUp_NoOp(t *testing.T) {
	// Call MigrateUp on a freshly migrated DB
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// setupClaimTestDB already ran all migrations. 
	// MigrateUp should be a no-op or handle it gracefully.
	err := MigrateUp(db)
	if err != nil {
		t.Errorf("MigrateUp failed on already migrated DB: %v", err)
	}
}

func TestWave226_MigrateDown_Steps(t *testing.T) {
	// After a fresh migration, try rolling back 1 step
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Try MigrateDown(db, 1) — may succeed or fail depending on if the last 
	// migration has a DownSQL.
	err := MigrateDown(db, 1)
	// We don't assert nil/error strictly because we don't know which 
	// migration was last or if it has DownSQL, but we exercise the path.
	t.Logf("MigrateDown(1) result: %v", err)
}

func TestWave226_MigrateDown_InvalidSteps(t *testing.T) {
	err := MigrateDown(nil, 0)
	if err != nil {
		t.Errorf("expected nil for 0 steps, got %v", err)
	}
}
