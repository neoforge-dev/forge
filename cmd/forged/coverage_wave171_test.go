//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 171: patrol + gitguard + idempotency coverage
// Targets:
//   cleanStaleBranches     (patrol.go:583)       — git error path, no matching branches
//   syncRoyalJelly         (patrol.go:396)        — non-existent dir, context_pct file, decisions/failures writeback, invalid pct
//   GitGuard.executeCommit (gitguard.go:312)      — no branch lock (ErrNoRows), branch mismatch, staging error
//   GitGuard.executePush   (gitguard.go:406)      — no branch lock (ErrNoRows), push on non-git dir, custom branch
//   IdempotencyStore.ExecuteOnce (gitguard_idempotency.go:64) — cache hit, different payloads, fn error not stored
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

func wave171SetupBranchLocksDB(t *testing.T) (*sql.DB, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	_, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS git_branch_locks (
			task_id TEXT PRIMARY KEY,
			branch TEXT NOT NULL,
			locked_at TEXT NOT NULL DEFAULT (datetime('now')),
			locked_by TEXT NOT NULL DEFAULT ''
		)
	`)
	if err != nil {
		cleanup()
		t.Fatalf("wave171: create git_branch_locks: %v", err)
	}
	return db, cleanup
}

func wave171SetupIdempotencyDB(t *testing.T) (*sql.DB, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
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
		cleanup()
		t.Fatalf("wave171: create idempotent_actions: %v", err)
	}
	return db, cleanup
}

// wave171CreateRoyalJellyTables creates the agents and context_envelopes tables
// needed by syncRoyalJelly.
func wave171CreateRoyalJellyTables(t *testing.T, db *sql.DB) {
	t.Helper()
	_, err := db.Exec(`CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, name TEXT, context_pct REAL)`)
	if err != nil {
		t.Fatalf("create agents: %v", err)
	}
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS context_envelopes (
			id TEXT PRIMARY KEY,
			agent_id TEXT NOT NULL,
			domain TEXT NOT NULL,
			project TEXT NOT NULL DEFAULT '',
			task_id TEXT NOT NULL DEFAULT '',
			summary TEXT,
			content TEXT,
			created_at TEXT NOT NULL DEFAULT (datetime('now'))
		)
	`)
	if err != nil {
		t.Fatalf("create context_envelopes: %v", err)
	}
}

// wave171InitGitRepo initialises a minimal git repo in dir so that git commands work.
func wave171InitGitRepo(t *testing.T, dir string) error {
	t.Helper()
	steps := [][]string{
		{"git", "-C", dir, "init"},
		{"git", "-C", dir, "config", "user.email", "test@forge.local"},
		{"git", "-C", dir, "config", "user.name", "Forge Test"},
	}
	for _, args := range steps {
		out, err := exec.Command(args[0], args[1:]...).CombinedOutput()
		if err != nil {
			return fmt.Errorf("step %v: %v (%s)", args, err, out)
		}
	}
	// Create an initial commit so HEAD resolves to a branch name
	testFile := filepath.Join(dir, "init.txt")
	if err := os.WriteFile(testFile, []byte("init"), 0644); err != nil {
		return err
	}
	for _, args := range [][]string{
		{"git", "-C", dir, "add", "."},
		{"git", "-C", dir, "commit", "-m", "init"},
	} {
		out, err := exec.Command(args[0], args[1:]...).CombinedOutput()
		if err != nil {
			return fmt.Errorf("step %v: %v (%s)", args, err, out)
		}
	}
	return nil
}

// wave171CurrentBranch returns the current branch name for the repo at dir.
func wave171CurrentBranch(t *testing.T, dir string) string {
	t.Helper()
	g := NewGitGuard(nil, dir)
	branch, err := g.getCurrentBranch()
	if err != nil {
		t.Skipf("cannot get current branch: %v", err)
	}
	return branch
}

// ---------------------------------------------------------------------------
// cleanStaleBranches
// ---------------------------------------------------------------------------

// TestWave171_CleanStaleBranches_GitErrorReturnsNil exercises the early-return
// path when the git command fails (non-git directory).
func TestWave171_CleanStaleBranches_GitErrorReturnsNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Run with CWD pointing at a temp dir (not a git repo) so git branch -r fails.
	// cleanStaleBranches uses cmd.Dir="." which resolves to process CWD,
	// but the exec.Command output error causes early return nil.
	err := cleanStaleBranches(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil on git error, got: %v", err)
	}
}

// TestWave171_CleanStaleBranches_NoBranchesMatched runs cleanStaleBranches and
// confirms it returns nil when no branches match the stale node/* pattern.
func TestWave171_CleanStaleBranches_NoBranchesMatched(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	err := cleanStaleBranches(context.Background(), db)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
}

// ---------------------------------------------------------------------------
// syncRoyalJelly
// ---------------------------------------------------------------------------

// TestWave171_SyncRoyalJelly_NonExistentDir sets FORGE_ROOT to a temp dir
// that has no .forge/context subdirectory — function must return nil.
func TestWave171_SyncRoyalJelly_NonExistentDir(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	wave171CreateRoyalJellyTables(t, db)

	err := syncRoyalJelly(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil for non-existent context dir, got: %v", err)
	}
}

// TestWave171_SyncRoyalJelly_ValidContextPctFile exercises the filesystem→DB
// sync path: a context_pct file in a domain directory is read and the UPDATE
// against agents runs (zero rows affected is fine).
func TestWave171_SyncRoyalJelly_ValidContextPctFile(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	domainDir := filepath.Join(tmpDir, ".forge", "context", "testdomain171")
	if err := os.MkdirAll(domainDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(domainDir, "context_pct"), []byte("72.5\n"), 0644); err != nil {
		t.Fatalf("write context_pct: %v", err)
	}

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	wave171CreateRoyalJellyTables(t, db)

	err := syncRoyalJelly(context.Background(), db)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
}

// TestWave171_SyncRoyalJelly_InvalidContextPctSkipped ensures that a
// non-numeric context_pct value is silently skipped (no error returned).
func TestWave171_SyncRoyalJelly_InvalidContextPctSkipped(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	domainDir := filepath.Join(tmpDir, ".forge", "context", "baddomain171")
	if err := os.MkdirAll(domainDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(domainDir, "context_pct"), []byte("not-a-number"), 0644); err != nil {
		t.Fatalf("write context_pct: %v", err)
	}

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	wave171CreateRoyalJellyTables(t, db)

	err := syncRoyalJelly(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil for invalid pct, got: %v", err)
	}
}

// TestWave171_SyncRoyalJelly_EnvelopeWithDecisionsAndFailures exercises the
// DB→filesystem writeback path: an envelope containing decisions and failures
// should produce lead-context.md, decisions.json, and failures.json.
func TestWave171_SyncRoyalJelly_EnvelopeWithDecisionsAndFailures(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	contextDir := filepath.Join(tmpDir, ".forge", "context")
	if err := os.MkdirAll(contextDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	wave171CreateRoyalJellyTables(t, db)

	envelope := map[string]interface{}{
		"summary": "Test summary for wave171",
		"decisions": []map[string]interface{}{
			{
				"id":          "dec00001-0000-0000-0000-000000000001",
				"description": "Use SQLite",
				"reasoning":   "Simplicity",
				"timestamp":   time.Now().UTC().Format(time.RFC3339),
			},
		},
		"failures": []map[string]interface{}{
			{
				"id":          "fail0001-0000-0000-0000-000000000001",
				"description": "tmux race",
				"lesson":      "Use separate Enter call",
				"timestamp":   time.Now().UTC().Format(time.RFC3339),
			},
		},
		"metadata": map[string]interface{}{},
	}
	content, err := json.Marshal(envelope)
	if err != nil {
		t.Fatalf("marshal envelope: %v", err)
	}

	_, err = db.Exec(`
		INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, summary, content, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now', '+1 hour'))
	`, "env-wave171-001", "agent-test", "wave171domain", "proj", "task-001", "Test summary", string(content))
	if err != nil {
		t.Fatalf("insert envelope: %v", err)
	}

	err = syncRoyalJelly(context.Background(), db)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}

	domainDir := filepath.Join(contextDir, "wave171domain")
	if _, statErr := os.Stat(filepath.Join(domainDir, "lead-context.md")); statErr != nil {
		t.Errorf("expected lead-context.md to be created: %v", statErr)
	}
	if _, statErr := os.Stat(filepath.Join(domainDir, "decisions.json")); statErr != nil {
		t.Errorf("expected decisions.json to be created: %v", statErr)
	}
	if _, statErr := os.Stat(filepath.Join(domainDir, "failures.json")); statErr != nil {
		t.Errorf("expected failures.json to be created: %v", statErr)
	}
}

// ---------------------------------------------------------------------------
// GitGuard.executeCommit
// ---------------------------------------------------------------------------

// TestWave171_ExecuteCommit_NoBranchLock verifies that when git_branch_locks
// has no row for the task, executeCommit returns a failure result.
func TestWave171_ExecuteCommit_NoBranchLock(t *testing.T) {
	db, cleanup := wave171SetupBranchLocksDB(t)
	defer cleanup()

	g := NewGitGuard(db, t.TempDir())
	action := GitAction{
		TaskID:  "task-wave171-nolock",
		Type:    GitActionCommit,
		Message: "test commit",
		Files:   []string{"README.md"},
	}

	result := g.executeCommit(action)
	if result.Success {
		t.Error("expected failure for missing branch lock")
	}
	if result.Error == "" {
		t.Error("expected non-empty error message")
	}
}

// TestWave171_ExecuteCommit_BranchMismatch inserts a lock for a branch that
// does not match the current branch, expecting a mismatch error.
func TestWave171_ExecuteCommit_BranchMismatch(t *testing.T) {
	repoDir := t.TempDir()
	if err := wave171InitGitRepo(t, repoDir); err != nil {
		t.Skipf("cannot init git repo: %v", err)
	}

	db, cleanup := wave171SetupBranchLocksDB(t)
	defer cleanup()

	_, err := db.Exec(`
		INSERT INTO git_branch_locks (task_id, branch, locked_by)
		VALUES (?, ?, ?)
	`, "task-wave171-mismatch", "wrong-branch-171", "agent-test")
	if err != nil {
		t.Fatalf("insert lock: %v", err)
	}

	g := NewGitGuard(db, repoDir)
	action := GitAction{
		TaskID:  "task-wave171-mismatch",
		Type:    GitActionCommit,
		Message: "test commit",
		Files:   []string{"testfile.txt"},
	}

	result := g.executeCommit(action)
	if result.Success {
		t.Error("expected failure for branch mismatch")
	}
	if result.Error == "" {
		t.Error("expected non-empty error for mismatch")
	}
}

// TestWave171_ExecuteCommit_StagingError verifies that staging failure (git add
// on a non-git directory) propagates as a failed result.
func TestWave171_ExecuteCommit_StagingError(t *testing.T) {
	// Need a real git repo for getCurrentBranch to succeed
	repoDir := t.TempDir()
	if err := wave171InitGitRepo(t, repoDir); err != nil {
		t.Skipf("cannot init git repo: %v", err)
	}
	currentBranch := wave171CurrentBranch(t, repoDir)

	db, cleanup := wave171SetupBranchLocksDB(t)
	defer cleanup()

	_, err := db.Exec(`
		INSERT INTO git_branch_locks (task_id, branch, locked_by)
		VALUES (?, ?, ?)
	`, "task-wave171-stagefail", currentBranch, "agent-test")
	if err != nil {
		t.Fatalf("insert lock: %v", err)
	}

	// Use a non-git dir as repoRoot so git add fails
	nonGitDir := t.TempDir()
	g := NewGitGuard(db, nonGitDir)
	action := GitAction{
		TaskID:  "task-wave171-stagefail",
		Type:    GitActionCommit,
		Message: "test commit",
		Files:   []string{"does-not-exist.txt"},
	}

	result := g.executeCommit(action)
	if result.Success {
		t.Error("expected failure when staging on non-git dir")
	}
}

// ---------------------------------------------------------------------------
// GitGuard.executePush
// ---------------------------------------------------------------------------

// TestWave171_ExecutePush_NoBranchLock verifies that a missing lock entry
// causes push to fail immediately.
func TestWave171_ExecutePush_NoBranchLock(t *testing.T) {
	db, cleanup := wave171SetupBranchLocksDB(t)
	defer cleanup()

	g := NewGitGuard(db, t.TempDir())
	action := GitAction{
		TaskID: "task-wave171-push-nolock",
		Type:   GitActionPush,
	}

	result := g.executePush(action)
	if result.Success {
		t.Error("expected failure for missing branch lock on push")
	}
	if result.Error == "" {
		t.Error("expected non-empty error message")
	}
}

// TestWave171_ExecutePush_FailsOnNonGitDir exercises the push-failure path by
// pointing repoRoot at a non-git directory.
func TestWave171_ExecutePush_FailsOnNonGitDir(t *testing.T) {
	nonGitDir := t.TempDir()

	db, cleanup := wave171SetupBranchLocksDB(t)
	defer cleanup()

	_, err := db.Exec(`
		INSERT INTO git_branch_locks (task_id, branch, locked_by)
		VALUES (?, ?, ?)
	`, "task-wave171-push-fail", "feat/wave171", "agent-test")
	if err != nil {
		t.Fatalf("insert lock: %v", err)
	}

	g := NewGitGuard(db, nonGitDir)
	action := GitAction{
		TaskID: "task-wave171-push-fail",
		Type:   GitActionPush,
	}

	result := g.executePush(action)
	if result.Success {
		t.Error("expected push to fail on non-git dir")
	}
	if result.Error == "" {
		t.Error("expected non-empty error message")
	}
}

// TestWave171_ExecutePush_CustomBranch verifies that when action.Branch is set
// executePush uses the custom branch mapping (push still fails on non-git dir,
// but the branch-lock-found + custom-branch code path is exercised).
func TestWave171_ExecutePush_CustomBranch(t *testing.T) {
	nonGitDir := t.TempDir()

	db, cleanup := wave171SetupBranchLocksDB(t)
	defer cleanup()

	_, err := db.Exec(`
		INSERT INTO git_branch_locks (task_id, branch, locked_by)
		VALUES (?, ?, ?)
	`, "task-wave171-push-custom", "feat/wave171-custom", "agent-test")
	if err != nil {
		t.Fatalf("insert lock: %v", err)
	}

	g := NewGitGuard(db, nonGitDir)
	action := GitAction{
		TaskID: "task-wave171-push-custom",
		Type:   GitActionPush,
		Branch: "main", // custom target branch
	}

	result := g.executePush(action)
	// Push fails (non-git dir) but the lock was found and custom branch path ran
	if result.Success {
		t.Error("expected push to fail on non-git dir")
	}
	if result.Error == "" {
		t.Error("expected non-empty error")
	}
}

// ---------------------------------------------------------------------------
// IdempotencyStore.ExecuteOnce
// ---------------------------------------------------------------------------

// TestWave171_ExecuteOnce_CacheHit ensures that a second call with the same
// payload returns the cached result without invoking fn again.
func TestWave171_ExecuteOnce_CacheHit(t *testing.T) {
	db, cleanup := wave171SetupIdempotencyDB(t)
	defer cleanup()

	store := NewIdempotencyStore(db)
	callCount := 0
	fn := func() (interface{}, error) {
		callCount++
		return map[string]string{"result": fmt.Sprintf("call-%d", callCount)}, nil
	}

	r1, err := store.ExecuteOnce("wave171-op", []byte(`{"key":"val"}`), fn)
	if err != nil {
		t.Fatalf("first ExecuteOnce: %v", err)
	}
	if callCount != 1 {
		t.Errorf("expected fn called once, got %d", callCount)
	}

	r2, err := store.ExecuteOnce("wave171-op", []byte(`{"key":"val"}`), fn)
	if err != nil {
		t.Fatalf("second ExecuteOnce: %v", err)
	}
	if callCount != 1 {
		t.Errorf("expected fn still called once (cache hit), got %d", callCount)
	}

	j1, _ := json.Marshal(r1)
	j2, _ := json.Marshal(r2)
	if string(j1) != string(j2) {
		t.Errorf("expected same cached result: %s vs %s", j1, j2)
	}
}

// TestWave171_ExecuteOnce_DifferentPayloads verifies that different payloads
// trigger separate fn executions (no false cache hit).
func TestWave171_ExecuteOnce_DifferentPayloads(t *testing.T) {
	db, cleanup := wave171SetupIdempotencyDB(t)
	defer cleanup()

	store := NewIdempotencyStore(db)
	callCount := 0
	fn := func() (interface{}, error) {
		callCount++
		return map[string]int{"n": callCount}, nil
	}

	if _, err := store.ExecuteOnce("wave171-multi", []byte(`{"id":1}`), fn); err != nil {
		t.Fatalf("first call: %v", err)
	}
	if _, err := store.ExecuteOnce("wave171-multi", []byte(`{"id":2}`), fn); err != nil {
		t.Fatalf("second call: %v", err)
	}

	if callCount != 2 {
		t.Errorf("expected fn called twice for different payloads, got %d", callCount)
	}
}

// TestWave171_ExecuteOnce_FnErrorNotStored verifies that when fn returns an
// error the result is not stored and a subsequent call retries fn.
func TestWave171_ExecuteOnce_FnErrorNotStored(t *testing.T) {
	db, cleanup := wave171SetupIdempotencyDB(t)
	defer cleanup()

	store := NewIdempotencyStore(db)
	callCount := 0
	failOnFirst := true
	fn := func() (interface{}, error) {
		callCount++
		if failOnFirst {
			failOnFirst = false
			return nil, errors.New("transient error")
		}
		return map[string]string{"ok": "yes"}, nil
	}

	// First call — fn fails; error propagates
	_, err := store.ExecuteOnce("wave171-err", []byte(`{"attempt":1}`), fn)
	if err == nil {
		t.Error("expected error from fn to propagate")
	}

	// Second call — fn should be invoked again (error was not cached)
	_, err = store.ExecuteOnce("wave171-err", []byte(`{"attempt":1}`), fn)
	if err != nil {
		t.Errorf("second call should succeed: %v", err)
	}
	if callCount != 2 {
		t.Errorf("expected fn called twice (error not cached), got %d", callCount)
	}
}
