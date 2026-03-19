//go:build !tmux_bridge

package main

import (
	"database/sql"
	"testing"
)

// testContextManager creates a ContextManager for tests and registers
// t.Cleanup(cm.Stop) to prevent goroutine leaks from fsnotify watchers.
func testContextManager(t *testing.T, db *sql.DB, contextDir string) *ContextManager {
	t.Helper()
	cm := NewContextManager(db, contextDir)
	t.Cleanup(cm.Stop)
	return cm
}
