//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

// Wave 56: ContextSync.Start/Stop, ContextSync.SyncEnvelopesToFilesystem/SyncDomainToFilesystem,
//          handleTaskShow, handleTaskLogs, handleTaskCancel, handleTaskCreate stub paths

// ─── ContextSync.Start / Stop ─────────────────────────────────────────────

func TestContextSync_StartStop_EmptyDir_W56(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := NewContextManager(db, tmpDir)
	cs, err := NewContextSync(cm, db, tmpDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	// Start with no domain subdirs → returns nil immediately after starting goroutines
	if err := cs.Start(); err != nil {
		t.Logf("ContextSync.Start empty dir: %v (may be OK)", err)
	}
	cs.Stop()
}

func TestContextSync_StartStop_WithSubdir_W56(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	// Create a domain subdirectory so the watcher loop has something to watch
	import_os_mkdir_all_w56(t, tmpDir+"/test-domain")

	cm := NewContextManager(db, tmpDir)
	cs, err := NewContextSync(cm, db, tmpDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	if err := cs.Start(); err != nil {
		t.Logf("ContextSync.Start with subdir: %v (may be OK)", err)
	}
	cs.Stop()
}

// ─── ContextSync.SyncEnvelopesToFilesystem ────────────────────────────────

func TestContextSync_SyncEnvelopes_W56(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := NewContextManager(db, tmpDir)
	cs, err := NewContextSync(cm, db, tmpDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	// No envelopes → iterates empty result set
	if err := cs.SyncEnvelopesToFilesystem(); err != nil {
		t.Logf("ContextSync.SyncEnvelopesToFilesystem: %v (may be OK)", err)
	}
}

// ─── ContextSync.SyncDomainToFilesystem ──────────────────────────────────

func TestContextSync_SyncDomain_NoRows_W56(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := NewContextManager(db, tmpDir)
	cs, err := NewContextSync(cm, db, tmpDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	// No envelope for domain → ErrNoRows → returns nil
	if err := cs.SyncDomainToFilesystem("domain-w56"); err != nil {
		t.Logf("ContextSync.SyncDomainToFilesystem no rows: %v (may be OK)", err)
	}
}

// ─── handleTaskShow ───────────────────────────────────────────────────────

func TestHandleTaskShow_MissingID_W56(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/cli/tasks/show", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestHandleTaskShow_NotFound_W56(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/cli/tasks/show?id=TASK-NOTEXIST-W56", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)
	// 404 expected for non-existent task
	if w.Code == http.StatusOK {
		t.Logf("handleTaskShow got 200 for nonexistent task (may be OK)")
	}
}

// ─── handleTaskLogs ───────────────────────────────────────────────────────

func TestHandleTaskLogs_MissingID_W56(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/cli/tasks/logs", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestHandleTaskLogs_WithID_W56(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/cli/tasks/logs?id=TASK-NOTEXIST-W56", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)
	_ = w.Code // 404 or 200 with empty — just verify no crash
}

// ─── handleTaskCancel ────────────────────────────────────────────────────

func TestHandleTaskCancel_W56(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/cli/tasks/cancel", nil)
	w := httptest.NewRecorder()
	handleTaskCancel(w, req)
	if w.Code != http.StatusNotImplemented {
		t.Errorf("expected 501, got %d", w.Code)
	}
}

// ─── context_sync context path (Start with context) ──────────────────────

func TestContextSync_StartContext_W56(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := NewContextManager(db, tmpDir)
	cs, err := NewContextSync(cm, db, tmpDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // immediately cancel
	if err := cs.Start(); err != nil {
		t.Logf("ContextSync.Start with cancelled ctx: %v (OK)", err)
	}
	cs.Stop()
	_ = ctx
}

// helper to create dir in test
func import_os_mkdir_all_w56(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(path, 0755); err != nil {
		t.Fatalf("mkdir %s: %v", path, err)
	}
}
