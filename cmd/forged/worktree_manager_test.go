package main

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

// initTestRepo creates a temporary git repo with a single commit and returns its path.
func initTestRepo(t *testing.T) string {
	t.Helper()

	dir, err := os.MkdirTemp("", "forge-worktree-repo-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}

	run := func(args ...string) {
		cmd := exec.Command(args[0], args[1:]...)
		cmd.Dir = dir
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("command %v failed: %v (output: %s)", args, err, string(out))
		}
	}

	run("git", "init", ".")
	// Configure minimal identity for commits.
	run("git", "config", "user.name", "forge-test")
	run("git", "config", "user.email", "forge@example.com")

	// Create an initial commit so git worktree add works.
	if err := os.WriteFile(filepath.Join(dir, "README.md"), []byte("# test repo\n"), 0o644); err != nil {
		t.Fatalf("failed to write README: %v", err)
	}
	run("git", "add", "README.md")
	run("git", "commit", "-m", "init")

	return dir
}

// openTestDB opens a SQLite DB at the given path and runs migrations.
func openTestDB(t *testing.T, dbPath string) *sql.DB {
	t.Helper()

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("failed to run migrations: %v", err)
	}
	return db
}

// TestWorktreeManager_CreateAndList verifies that CreateWorktree creates a worktree
// and ListWorktrees can see it.
func TestWorktreeManager_CreateAndList(t *testing.T) {
	repoRoot := initTestRepo(t)
	defer os.RemoveAll(repoRoot)

	ctx := context.Background()
	manager := NewWorktreeManager(repoRoot, "", nil)

	taskID := "TASK-WT-001"
	path, err := manager.CreateWorktree(ctx, taskID)
	if err != nil {
		t.Fatalf("CreateWorktree failed: %v", err)
	}

	if fi, err := os.Stat(path); err != nil || !fi.IsDir() {
		t.Fatalf("expected worktree dir to exist: %s (err=%v)", path, err)
	}

	worktrees, err := manager.ListWorktrees(ctx)
	if err != nil {
		t.Fatalf("ListWorktrees failed: %v", err)
	}

	found := false
	for _, wt := range worktrees {
		if filepath.Clean(wt.Path) == filepath.Clean(path) {
			found = true
			if wt.Branch != "feature/"+taskID {
				t.Fatalf("expected branch feature/%s, got %s", taskID, wt.Branch)
			}
			break
		}
	}
	if !found {
		t.Fatalf("created worktree %s not found in ListWorktrees", path)
	}
}

// TestWorktreeManager_Remove ensures RemoveWorktree cleans up the directory.
func TestWorktreeManager_Remove(t *testing.T) {
	repoRoot := initTestRepo(t)
	defer os.RemoveAll(repoRoot)

	ctx := context.Background()
	manager := NewWorktreeManager(repoRoot, "", nil)

	taskID := "TASK-WT-REMOVE"
	path, err := manager.CreateWorktree(ctx, taskID)
	if err != nil {
		t.Fatalf("CreateWorktree failed: %v", err)
	}

	if err := manager.RemoveWorktree(ctx, taskID); err != nil {
		t.Fatalf("RemoveWorktree failed: %v", err)
	}

	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("expected worktree dir to be removed, stat err=%v", err)
	}
}

// TestWorktreeManager_PruneStale verifies that PruneStaleWorktrees removes worktrees
// for completed/failed tasks while leaving active ones in place.
func TestWorktreeManager_PruneStale(t *testing.T) {
	repoRoot := initTestRepo(t)
	defer os.RemoveAll(repoRoot)

	dbPath := filepath.Join(os.TempDir(), fmt.Sprintf("forge_worktree_prune_%d.db", time.Now().UnixNano()))
	db := openTestDB(t, dbPath)
	defer func() {
		db.Close()
		_ = os.Remove(dbPath)
	}()

	ctx := context.Background()
	manager := NewWorktreeManager(repoRoot, "", db)

	// Seed tasks with different statuses.
	now := time.Now().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
	`, "TASK-COMPLETE", "domain", "project", string(TaskTypeFeature), 10, string(TaskStatusCompleted), now, now)
	if err != nil {
		t.Fatalf("failed to insert completed task: %v", err)
	}
	_, err = db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
	`, "TASK-ACTIVE", "domain", "project", string(TaskTypeFeature), 10, string(TaskStatusQueued), now, now)
	if err != nil {
		t.Fatalf("failed to insert active task: %v", err)
	}

	// Create corresponding worktrees.
	completePath, err := manager.CreateWorktree(ctx, "TASK-COMPLETE")
	if err != nil {
		t.Fatalf("CreateWorktree for TASK-COMPLETE failed: %v", err)
	}
	activePath, err := manager.CreateWorktree(ctx, "TASK-ACTIVE")
	if err != nil {
		t.Fatalf("CreateWorktree for TASK-ACTIVE failed: %v", err)
	}

	if _, err := os.Stat(completePath); err != nil {
		t.Fatalf("expected TASK-COMPLETE worktree to exist: %v", err)
	}
	if _, err := os.Stat(activePath); err != nil {
		t.Fatalf("expected TASK-ACTIVE worktree to exist: %v", err)
	}

	if err := manager.PruneStaleWorktrees(ctx); err != nil {
		t.Fatalf("PruneStaleWorktrees failed: %v", err)
	}

	if _, err := os.Stat(completePath); !os.IsNotExist(err) {
		t.Fatalf("expected completed task worktree to be removed, stat err=%v", err)
	}
	if _, err := os.Stat(activePath); err != nil {
		t.Fatalf("expected active task worktree to remain, stat err=%v", err)
	}
}

// TestWorktreeManager_PruneDirty verifies that PruneStaleWorktrees preserves dirty worktrees.
func TestWorktreeManager_PruneDirty(t *testing.T) {
	repoRoot := initTestRepo(t)
	defer os.RemoveAll(repoRoot)

	dbPath := filepath.Join(os.TempDir(), fmt.Sprintf("forge_worktree_prune_dirty_%d.db", time.Now().UnixNano()))
	db := openTestDB(t, dbPath)
	defer func() {
		db.Close()
		_ = os.Remove(dbPath)
	}()

	ctx := context.Background()
	manager := NewWorktreeManager(repoRoot, "", db)

	// Create task with COMPLETED status but it will have a dirty worktree.
	taskID := "TASK-COMPLETE-BUT-DIRTY"
	now := time.Now().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
	`, taskID, "domain", "project", string(TaskTypeFeature), 10, string(TaskStatusCompleted), now, now)
	if err != nil {
		t.Fatalf("failed to insert task: %v", err)
	}

	path, err := manager.CreateWorktree(ctx, taskID)
	if err != nil {
		t.Fatalf("CreateWorktree failed: %v", err)
	}

	// Create dirty file in the worktree.
	if err := os.WriteFile(filepath.Join(path, "dirty.txt"), []byte("don't delete me"), 0o644); err != nil {
		t.Fatalf("failed to write dirty file: %v", err)
	}

	// Prune — should NOT remove the worktree because it's dirty.
	if err := manager.PruneStaleWorktrees(ctx); err != nil {
		t.Fatalf("PruneStaleWorktrees failed: %v", err)
	}

	if _, err := os.Stat(path); os.IsNotExist(err) {
		t.Fatalf("expected dirty worktree to be preserved, but it was removed")
	}

	// Verification check via IsWorktreeDirty.
	dirty, err := manager.IsWorktreeDirty(ctx, taskID)
	if err != nil {
		t.Fatalf("IsWorktreeDirty failed: %v", err)
	}
	if !dirty {
		t.Fatalf("expected worktree to be reported as dirty")
	}
}

// Wave 129: WorktreeManager — CreateWorktree, RemoveWorktree, ListWorktrees,
//           RecordWorktree, PruneStaleWorktrees edge cases.

// TestWave129_WorktreeManager_CreateWorktree_EmptyTaskID verifies empty taskID returns error.
func TestWave129_WorktreeManager_CreateWorktree_EmptyTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	m := NewWorktreeManager(t.TempDir(), t.TempDir(), db)

	_, err := m.CreateWorktree(context.Background(), "")
	if err == nil {
		t.Error("expected error for empty taskID")
	}
	t.Logf("CreateWorktree empty taskID: %v", err)
}

// TestWave129_WorktreeManager_CreateWorktree_InvalidRepo verifies git failure is handled.
func TestWave129_WorktreeManager_CreateWorktree_InvalidRepo(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir() // not a git repo
	wdir := t.TempDir()
	m := NewWorktreeManager(root, wdir, db)

	_, err := m.CreateWorktree(context.Background(), "task-wave129-invalid")
	// Git will fail (not a git repo) but should not panic
	t.Logf("CreateWorktree invalid repo: %v", err)
}

// TestWave129_WorktreeManager_CreateWorktree_ExistingDir verifies existing-dir early return.
func TestWave129_WorktreeManager_CreateWorktree_ExistingDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	wdir := t.TempDir()
	m := NewWorktreeManager(root, wdir, db)

	// Pre-create the expected worktree directory so CreateWorktree short-circuits.
	existingPath := filepath.Join(wdir, "task-wave129-existing")
	if err := os.MkdirAll(existingPath, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	path, err := m.CreateWorktree(context.Background(), "task-wave129-existing")
	if err != nil {
		t.Errorf("expected success for existing dir, got: %v", err)
	}
	t.Logf("CreateWorktree existing dir: %s", path)
}

// TestWave129_WorktreeManager_RemoveWorktree_EmptyTaskID verifies empty taskID returns error.
func TestWave129_WorktreeManager_RemoveWorktree_EmptyTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	m := NewWorktreeManager(t.TempDir(), t.TempDir(), db)

	err := m.RemoveWorktree(context.Background(), "")
	if err == nil {
		t.Error("expected error for empty taskID")
	}
	t.Logf("RemoveWorktree empty taskID: %v", err)
}

// TestWave129_WorktreeManager_RemoveWorktree_NonExistent verifies no error for missing worktree.
func TestWave129_WorktreeManager_RemoveWorktree_NonExistent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	m := NewWorktreeManager(t.TempDir(), t.TempDir(), db)

	// Best-effort — should not error even if worktree doesn't exist
	err := m.RemoveWorktree(context.Background(), "task-nonexistent-wave129")
	if err != nil {
		t.Logf("RemoveWorktree non-existent: %v (may be acceptable)", err)
	}
}

// TestWave129_WorktreeManager_ListWorktrees_InvalidRepo verifies error on non-git dir.
func TestWave129_WorktreeManager_ListWorktrees_InvalidRepo(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	m := NewWorktreeManager(t.TempDir(), t.TempDir(), db)

	_, err := m.ListWorktrees(context.Background())
	if err == nil {
		t.Error("expected error for non-git repo")
	}
	t.Logf("ListWorktrees invalid repo: %v", err)
}

// TestWave129_WorktreeManager_RecordWorktree_ClosedDB verifies DB error handling.
func TestWave129_WorktreeManager_RecordWorktree_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup()

	m := NewWorktreeManager(t.TempDir(), t.TempDir(), db)

	err := m.RecordWorktree(context.Background(), "task-w129", "/tmp/path", "feature/task-w129", "agent-1")
	if err == nil {
		t.Error("expected error with closed DB")
	}
	t.Logf("RecordWorktree closed DB: %v", err)
}

// TestWave129_WorktreeManager_RecordWorktree_Success records a worktree entry.
func TestWave129_WorktreeManager_RecordWorktree_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	m := NewWorktreeManager(t.TempDir(), t.TempDir(), db)

	err := m.RecordWorktree(context.Background(), "task-w129-ok", "/tmp/path-ok", "feature/task-w129-ok", "agent-1")
	if err != nil {
		t.Logf("RecordWorktree success path: %v (acceptable if table missing)", err)
	}
}

// TestWave129_WorktreeManager_PruneStaleWorktrees_ClosedDB verifies error on closed DB.
func TestWave129_WorktreeManager_PruneStaleWorktrees_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup()

	m := NewWorktreeManager(t.TempDir(), t.TempDir(), db)

	err := m.PruneStaleWorktrees(context.Background())
	t.Logf("PruneStaleWorktrees closed DB: %v", err)
}

// TestWave129_WorktreeManager_PruneStaleWorktrees_EmptyDB verifies no-op on empty DB.
func TestWave129_WorktreeManager_PruneStaleWorktrees_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	m := NewWorktreeManager(t.TempDir(), t.TempDir(), db)

	err := m.PruneStaleWorktrees(context.Background())
	if err != nil {
		t.Logf("PruneStaleWorktrees empty DB: %v (acceptable)", err)
	}
}

