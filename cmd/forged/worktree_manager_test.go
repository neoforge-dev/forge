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

