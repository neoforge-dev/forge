//go:build !tmux_bridge

package main

import (
	"database/sql"
	"os"
	"path/filepath"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

func setupContextSyncDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite3", ":memory:?_journal_mode=WAL")
	if err != nil {
		t.Fatalf("setupContextSyncDB: %v", err)
	}
	_, err = db.Exec(`
		CREATE TABLE context_envelopes (
			id         TEXT PRIMARY KEY,
			agent_id   TEXT NOT NULL,
			domain     TEXT NOT NULL,
			project    TEXT NOT NULL DEFAULT '',
			task_id    TEXT NOT NULL DEFAULT '',
			summary    TEXT,
			content    TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			expires_at TIMESTAMP NOT NULL DEFAULT (datetime('now','+1 day'))
		)
	`)
	if err != nil {
		t.Fatalf("setupContextSyncDB create table: %v", err)
	}
	return db
}

func buildCS(t *testing.T, db *sql.DB) (*ContextSync, string) {
	t.Helper()
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	if err := os.MkdirAll(contextDir, 0755); err != nil {
		t.Fatalf("buildCS mkdir: %v", err)
	}

	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	
	t.Cleanup(func() {
		cs.Stop()
	})
	
	return cs, contextDir
}
