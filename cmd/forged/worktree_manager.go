package main

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// WorktreeInfo represents a single git worktree entry.
type WorktreeInfo struct {
	Path   string
	Branch string
	Hash   string
}

// WorktreeManager manages git worktrees for isolated task execution, as
// described in ADR-024 (Orchestration-Level Worktree Isolation).
type WorktreeManager struct {
	repoRoot    string
	worktreeDir string // Typically .forge/worktrees/
	db          *sql.DB
}

// NewWorktreeManager creates a new WorktreeManager for the given repository
// root. If worktreeDir is empty, it defaults to ".forge/worktrees" under the
// repo root.
func NewWorktreeManager(repoRoot, worktreeDir string, db *sql.DB) *WorktreeManager {
	if repoRoot == "" {
		repoRoot, _ = os.Getwd()
	}
	if worktreeDir == "" {
		worktreeDir = filepath.Join(repoRoot, ".forge", "worktrees")
	}
	return &WorktreeManager{
		repoRoot:    repoRoot,
		worktreeDir: worktreeDir,
		db:          db,
	}
}

// CreateWorktree creates an isolated git worktree for the given task ID.
// It returns the filesystem path to the worktree.
//
// git worktree add .forge/worktrees/{taskID} -b feature/{taskID}
func (m *WorktreeManager) CreateWorktree(ctx context.Context, taskID string) (string, error) {
	if strings.TrimSpace(taskID) == "" {
		return "", fmt.Errorf("taskID is required")
	}

	if err := os.MkdirAll(m.worktreeDir, 0o755); err != nil {
		return "", fmt.Errorf("create worktree dir: %w", err)
	}

	path := filepath.Join(m.worktreeDir, taskID)
	branch := fmt.Sprintf("feature/%s", taskID)

	// If the directory already exists, assume the worktree is present.
	if fi, err := os.Stat(path); err == nil && fi.IsDir() {
		if resolved, err := filepath.EvalSymlinks(path); err == nil {
			return resolved, nil
		}
		return path, nil
	}

	args := []string{"-C", m.repoRoot, "worktree", "add", path, "-b", branch}
	cmd := exec.CommandContext(ctx, "git", args...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		outStr := string(output)
		// Handle already-exists / already-registered scenarios gracefully.
		if strings.Contains(outStr, "already exists") || strings.Contains(outStr, "already registered") {
			if resolved, err := filepath.EvalSymlinks(path); err == nil {
				return resolved, nil
			}
			return path, nil
		}
		return "", fmt.Errorf("git worktree add failed: %v (output: %s)", err, outStr)
	}

	if resolved, err := filepath.EvalSymlinks(path); err == nil {
		return resolved, nil
	}
	return path, nil
}

// RemoveWorktree removes the worktree for the given task ID and attempts to
// delete the corresponding branch. It is tolerant of missing worktrees or
// branches and performs best-effort cleanup.
func (m *WorktreeManager) RemoveWorktree(ctx context.Context, taskID string) error {
	if strings.TrimSpace(taskID) == "" {
		return fmt.Errorf("taskID is required")
	}

	path := filepath.Join(m.worktreeDir, taskID)
	branch := fmt.Sprintf("feature/%s", taskID)

	// Remove the worktree (force to handle dirty worktrees).
	args := []string{"-C", m.repoRoot, "worktree", "remove", "--force", path}
	cmd := exec.CommandContext(ctx, "git", args...)
	if output, err := cmd.CombinedOutput(); err != nil {
		// If git refuses because it does not recognise the worktree, fall back
		// to direct filesystem removal.
		if _, statErr := os.Stat(path); statErr == nil {
			_ = os.RemoveAll(path)
		}
		_ = output // log hook can be added later; ignore for now
	} else {
		_ = cmd // keep for parity; no-op
	}

	// Best-effort branch deletion; ignore errors if branch does not exist or
	// is not fully merged.
	delArgs := []string{"-C", m.repoRoot, "branch", "-D", branch}
	delCmd := exec.CommandContext(ctx, "git", delArgs...)
	_, _ = delCmd.CombinedOutput()

	// Ensure directory is gone even if git did not clean it up.
	if _, err := os.Stat(path); err == nil {
		if rmErr := os.RemoveAll(path); rmErr != nil {
			return fmt.Errorf("remove worktree dir: %w", rmErr)
		}
	}

	return nil
}

// ListWorktrees returns the active git worktrees by parsing:
//   git worktree list --porcelain
func (m *WorktreeManager) ListWorktrees(ctx context.Context) ([]WorktreeInfo, error) {
	args := []string{"-C", m.repoRoot, "worktree", "list", "--porcelain"}
	cmd := exec.CommandContext(ctx, "git", args...)
	output, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("git worktree list failed: %w", err)
	}

	lines := strings.Split(string(output), "\n")
	var (
		result []WorktreeInfo
		cur    *WorktreeInfo
	)

	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}

		switch {
		case strings.HasPrefix(line, "worktree "):
			// Flush previous entry.
			if cur != nil {
				result = append(result, *cur)
			}
			rawPath := strings.TrimSpace(strings.TrimPrefix(line, "worktree "))
			// Resolve symlinks so callers can compare with os.* paths (macOS: /var → /private/var).
			if resolved, err := filepath.EvalSymlinks(rawPath); err == nil {
				rawPath = resolved
			}
			cur = &WorktreeInfo{
				Path: rawPath,
			}
		case strings.HasPrefix(line, "HEAD "):
			if cur != nil {
				cur.Hash = strings.TrimSpace(strings.TrimPrefix(line, "HEAD "))
			}
		case strings.HasPrefix(line, "branch "):
			if cur != nil {
				ref := strings.TrimSpace(strings.TrimPrefix(line, "branch "))
				cur.Branch = strings.TrimPrefix(ref, "refs/heads/")
			}
		default:
			// ignore other porcelain lines (locked, prunable, etc.)
		}
	}

	if cur != nil {
		result = append(result, *cur)
	}

	return result, nil
}

// RecordWorktree inserts a worktree row without touching the filesystem.
// Used by the daemon to track worktrees created by agent scripts.
func (m *WorktreeManager) RecordWorktree(ctx context.Context, taskID, path, branch, agentID string) error {
	_, err := m.db.ExecContext(ctx,
		`INSERT OR REPLACE INTO worktrees (id, path, branch, agent_id, status, created_at)
		 VALUES (?, ?, ?, ?, 'active', datetime('now'))`,
		taskID, path, branch, agentID,
	)
	return err
}

// IsWorktreeDirty checks if the worktree has uncommitted changes (including untracked files).
func (m *WorktreeManager) IsWorktreeDirty(ctx context.Context, taskID string) (bool, error) {
	path := filepath.Join(m.worktreeDir, taskID)
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return false, nil
	}

	// git status --porcelain shows all changes (staged, unstaged, untracked)
	args := []string{"-C", path, "status", "--porcelain"}
	cmd := exec.CommandContext(ctx, "git", args...)
	output, err := cmd.Output()
	if err != nil {
		// If it's not a git repo or other error, we can't reliably say it's clean.
		return true, fmt.Errorf("git status failed: %w", err)
	}

	return len(strings.TrimSpace(string(output))) > 0, nil
}

// PruneStaleWorktrees removes worktrees for completed/failed tasks. It is
// intended to be called periodically by patrols. If db is nil, it performs
// a pure filesystem-based cleanup of orphaned directories.
// It preserves "dirty" worktrees even if they are stale to prevent data loss.
func (m *WorktreeManager) PruneStaleWorktrees(ctx context.Context) error {
	entries, err := os.ReadDir(m.worktreeDir)
	if err != nil {
		// If the directory does not exist yet, there is nothing to prune.
		if os.IsNotExist(err) {
			return nil
		}
		return fmt.Errorf("read worktree dir: %w", err)
	}

	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		taskID := e.Name()

		shouldRemove := false
		if m.db == nil {
			// Without DB, treat all entries as candidates for pruning only if
			// their directories are empty.
			path := filepath.Join(m.worktreeDir, taskID)
			if dirEntries, _ := os.ReadDir(path); len(dirEntries) == 0 {
				shouldRemove = true
			}
		} else {
			var status string
			err := m.db.QueryRowContext(ctx,
				`SELECT status FROM tasks WHERE id = ?`,
				taskID,
			).Scan(&status)
			if err == sql.ErrNoRows {
				// No corresponding task; safe to prune.
				shouldRemove = true
			} else if err != nil {
				// On DB error, skip this worktree and continue.
				continue
			} else {
				if status == string(TaskStatusCompleted) || status == string(TaskStatusFailed) {
					shouldRemove = true
				}
			}
		}

		if shouldRemove {
			// Verification: don't prune dirty worktrees (Proposal #3 in S157 retro).
			dirty, err := m.IsWorktreeDirty(ctx, taskID)
			if err == nil && dirty {
				// Log or skip? Proposal says "Warn and preserve".
				// For now, we just preserve by skipping.
				continue
			}
			_ = m.RemoveWorktree(ctx, taskID)
		}
	}

	return nil
}

