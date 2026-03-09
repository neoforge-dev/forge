//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// GitGuard manages single-writer git operations
type GitGuard struct {
	db       *sql.DB
	mu       sync.Mutex
	repoRoot string
}

// GitActionType represents types of git actions
type GitActionType string

const (
	GitActionCommit GitActionType = "commit"
	GitActionPush   GitActionType = "push"
	GitActionBranch GitActionType = "branch"
	GitActionMerge  GitActionType = "merge"
	GitActionPull   GitActionType = "pull"
)

// GitAction represents a git operation
type GitAction struct {
	ID          string        `json:"id"`
	TaskID      string        `json:"task_id"`
	Type        GitActionType `json:"type"`
	Branch      string        `json:"branch"`
	Message     string        `json:"message"`
	Files       []string      `json:"files"`
	Author      string        `json:"author"`
	PayloadHash string        `json:"payload_hash"`
	CreatedAt   time.Time     `json:"created_at"`
}

// GitResult represents the result of a git operation
type GitResult struct {
	Success   bool      `json:"success"`
	Output    string    `json:"output"`
	Error     string    `json:"error,omitempty"`
	CommitID  string    `json:"commit_id,omitempty"`
	Timestamp time.Time `json:"timestamp"`
}

// GitGuardRequest represents an incoming request to GitGuard
type GitGuardRequest struct {
	Action  string   `json:"action"` // commit, push, branch, merge, pull
	TaskID  string   `json:"task_id"`
	Branch  string   `json:"branch,omitempty"`
	Message string   `json:"message,omitempty"`
	Files   []string `json:"files,omitempty"`
	Author  string   `json:"author,omitempty"`
	Force   bool     `json:"force,omitempty"` // force push, force branch switch
}

// NewGitGuard creates a new GitGuard instance
func NewGitGuard(db *sql.DB, repoRoot string) *GitGuard {
	if repoRoot == "" {
		repoRoot = "."
	}
	return &GitGuard{
		db:       db,
		repoRoot: repoRoot,
	}
}

// computePayloadHash computes a SHA256 hash of the action payload for idempotency
func computePayloadHash(action GitAction) string {
	// Include all meaningful fields in the hash
	data := fmt.Sprintf("%s|%s|%s|%s|%v|%s",
		action.TaskID,
		action.Type,
		action.Branch,
		action.Message,
		action.Files,
		action.Author,
	)
	hash := sha256.Sum256([]byte(data))
	return hex.EncodeToString(hash[:])
}

// getExistingAction checks if an action with the same payload hash already exists
func (g *GitGuard) getExistingAction(ctx context.Context, payloadHash string) (*GitAction, error) {
	var action GitAction
	var filesJSON string
	err := g.db.QueryRowContext(ctx, `
		SELECT id, task_id, type, branch, message, files, author, payload_hash, created_at
		FROM git_guard_actions
		WHERE payload_hash = ? AND status = 'completed'
	`, payloadHash).Scan(
		&action.ID,
		&action.TaskID,
		&action.Type,
		&action.Branch,
		&action.Message,
		&filesJSON,
		&action.Author,
		&action.PayloadHash,
		&action.CreatedAt,
	)

	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}

	// Unmarshal files JSON
	if filesJSON != "" {
		if err := json.Unmarshal([]byte(filesJSON), &action.Files); err != nil {
			return nil, fmt.Errorf("failed to unmarshal files: %w", err)
		}
	}

	return &action, nil
}

// recordAction records a git action to the database
func (g *GitGuard) recordAction(ctx context.Context, action GitAction, result *GitResult) error {
	filesJSON, _ := json.Marshal(action.Files)

	_, err := g.db.ExecContext(ctx, `
		INSERT INTO git_guard_actions (id, task_id, type, payload_hash, branch, message, files, author, status, result, error, commit_id, created_at, completed_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`,
		action.ID,
		action.TaskID,
		action.Type,
		action.PayloadHash,
		action.Branch,
		action.Message,
		filesJSON,
		action.Author,
		"completed",
		result.Output,
		result.Error,
		result.CommitID,
		action.CreatedAt,
		result.Timestamp,
	)
	return err
}

// recordPendingAction records a pending action to the database
func (g *GitGuard) recordPendingAction(ctx context.Context, action GitAction) error {
	filesJSON, _ := json.Marshal(action.Files)

	_, err := g.db.ExecContext(ctx, `
		INSERT INTO git_guard_actions (id, task_id, type, payload_hash, branch, message, files, author, status, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
	`,
		action.ID,
		action.TaskID,
		action.Type,
		action.PayloadHash,
		action.Branch,
		action.Message,
		filesJSON,
		action.Author,
		action.CreatedAt,
	)
	return err
}

// CheckSafe checks if git operations are safe to perform
func (g *GitGuard) CheckSafe() (bool, string) {
	// Check for index.lock
	indexLock := filepath.Join(g.repoRoot, ".git", "index.lock")
	if _, err := os.Stat(indexLock); err == nil {
		return false, "index.lock exists - another git operation in progress"
	}

	// Check for rebase in progress
	rebaseDir := filepath.Join(g.repoRoot, ".git", "rebase-merge")
	if _, err := os.Stat(rebaseDir); err == nil {
		return false, "rebase in progress"
	}

	rebaseApply := filepath.Join(g.repoRoot, ".git", "rebase-apply")
	if _, err := os.Stat(rebaseApply); err == nil {
		return false, "rebase in progress (apply)"
	}

	// Check for merge conflict state
	mergeHead := filepath.Join(g.repoRoot, ".git", "MERGE_HEAD")
	if _, err := os.Stat(mergeHead); err == nil {
		return false, "merge conflict in progress"
	}

	// Check for bisecting
	bisectStart := filepath.Join(g.repoRoot, ".git", "BISECT_START")
	if _, err := os.Stat(bisectStart); err == nil {
		return false, "bisect in progress"
	}

	return true, ""
}

// getCurrentBranch gets the current git branch
func (g *GitGuard) getCurrentBranch() (string, error) {
	cmd := exec.Command("git", "rev-parse", "--abbrev-ref", "HEAD")
	cmd.Dir = g.repoRoot
	out, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("failed to get current branch: %w", err)
	}
	return strings.TrimSpace(string(out)), nil
}

// getModifiedFiles gets the list of modified files
func (g *GitGuard) getModifiedFiles() ([]string, error) {
	cmd := exec.Command("git", "status", "--porcelain")
	cmd.Dir = g.repoRoot
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("failed to get modified files: %w", err)
	}

	var files []string
	lines := strings.Split(string(out), "\n")
	for _, line := range lines {
		if len(line) >= 3 {
			// Format: XY filename - take everything after the first 3 chars
			files = append(files, strings.TrimSpace(line[3:]))
		}
	}
	return files, nil
}

// executeBranch creates a new branch or switches to an existing one
func (g *GitGuard) executeBranch(action GitAction) *GitResult {
	result := &GitResult{Timestamp: time.Now()}

	// Branch-per-task enforcement: check if task already has a locked branch
	var existingBranch string
	err := g.db.QueryRow(`
		SELECT branch FROM git_branch_locks WHERE task_id = ?
	`, action.TaskID).Scan(&existingBranch)

	if err == nil {
		// Task already has a branch locked
		if existingBranch != action.Branch {
			result.Success = false
			result.Error = fmt.Sprintf("task %s is already locked to branch %s, cannot switch to %s", action.TaskID, existingBranch, action.Branch)
			return result
		}
		// Same branch - just switch to it
	} else if err != sql.ErrNoRows {
		result.Success = false
		result.Error = fmt.Sprintf("failed to check branch lock: %v", err)
		return result
	}

	// Check if branch exists
	cmd := exec.Command("git", "show-ref", "--verify", "--quiet", "refs/heads/"+action.Branch)
	cmd.Dir = g.repoRoot
	branchExists := cmd.Run() == nil

	if branchExists {
		// Checkout existing branch
		cmd = exec.Command("git", "checkout", action.Branch)
	} else {
		// Create and switch to new branch
		cmd = exec.Command("git", "checkout", "-b", action.Branch)
	}
	cmd.Dir = g.repoRoot
	out, err := cmd.CombinedOutput()

	if err != nil {
		result.Success = false
		result.Error = fmt.Sprintf("failed to create/switch branch: %s - %v", string(out), err)
		return result
	}

	// Lock the branch to this task
	_, err = g.db.Exec(`
		INSERT OR REPLACE INTO git_branch_locks (task_id, branch, locked_at, locked_by)
		VALUES (?, ?, ?, ?)
	`, action.TaskID, action.Branch, time.Now(), action.Author)

	if err != nil {
		result.Success = false
		result.Error = fmt.Sprintf("failed to lock branch: %v", err)
		return result
	}

	result.Success = true
	result.Output = fmt.Sprintf("Branch %s locked to task %s", action.Branch, action.TaskID)
	return result
}

// executeCommit creates a git commit with the specified files
func (g *GitGuard) executeCommit(action GitAction) *GitResult {
	result := &GitResult{Timestamp: time.Now()}

	// Verify we're on the correct branch
	currentBranch, err := g.getCurrentBranch()
	if err != nil {
		result.Success = false
		result.Error = fmt.Sprintf("failed to get current branch: %v", err)
		return result
	}

	// Branch-per-task: verify task is locked to this branch
	var lockedBranch string
	err = g.db.QueryRow(`
		SELECT branch FROM git_branch_locks WHERE task_id = ?
	`, action.TaskID).Scan(&lockedBranch)

	if err == sql.ErrNoRows {
		result.Success = false
		result.Error = fmt.Sprintf("task %s has no branch lock - must create/switch to branch first", action.TaskID)
		return result
	}
	if err != nil {
		result.Success = false
		result.Error = fmt.Sprintf("failed to check branch lock: %v", err)
		return result
	}

	if lockedBranch != currentBranch {
		result.Success = false
		result.Error = fmt.Sprintf("task %s is locked to branch %s, but current branch is %s", action.TaskID, lockedBranch, currentBranch)
		return result
	}

	// Stage specified files, or all files if none specified
	var filesToStage []string
	if len(action.Files) > 0 {
		filesToStage = action.Files
	} else {
		// Stage all modified files
		filesToStage, err = g.getModifiedFiles()
		if err != nil {
			result.Success = false
			result.Error = fmt.Sprintf("failed to get modified files: %v", err)
			return result
		}
	}

	if len(filesToStage) == 0 {
		result.Success = false
		result.Error = "no files to commit"
		return result
	}

	// Stage files
	args := append([]string{"add"}, filesToStage...)
	cmd := exec.Command("git", args...)
	cmd.Dir = g.repoRoot
	if out, err := cmd.CombinedOutput(); err != nil {
		result.Success = false
		result.Error = fmt.Sprintf("failed to stage files: %s - %v", string(out), err)
		return result
	}

	// Create commit with ULID-style timestamp in message
	commitMsg := fmt.Sprintf("%s\n\n[task:%s]", action.Message, action.TaskID)

	cmd = exec.Command("git", "commit", "-m", commitMsg)
	cmd.Dir = g.repoRoot
	out, err := cmd.CombinedOutput()

	if err != nil {
		result.Success = false
		result.Error = fmt.Sprintf("failed to commit: %s - %v", string(out), err)
		return result
	}

	// Get commit ID
	cmd = exec.Command("git", "rev-parse", "HEAD")
	cmd.Dir = g.repoRoot
	commitID, err := cmd.Output()
	if err != nil {
		result.Success = false
		result.Error = fmt.Sprintf("failed to get commit ID: %v", err)
		return result
	}

	result.Success = true
	result.CommitID = strings.TrimSpace(string(commitID))
	result.Output = fmt.Sprintf("Committed to %s: %s", currentBranch, result.CommitID[:8])
	return result
}

// executePush pushes commits to the remote
func (g *GitGuard) executePush(action GitAction) *GitResult {
	result := &GitResult{Timestamp: time.Now()}

	// Verify task has a branch lock
	var lockedBranch string
	err := g.db.QueryRow(`
		SELECT branch FROM git_branch_locks WHERE task_id = ?
	`, action.TaskID).Scan(&lockedBranch)

	if err == sql.ErrNoRows {
		result.Success = false
		result.Error = fmt.Sprintf("task %s has no branch lock - cannot push", action.TaskID)
		return result
	}
	if err != nil {
		result.Success = false
		result.Error = fmt.Sprintf("failed to check branch lock: %v", err)
		return result
	}

	// Build push command
	var cmd *exec.Cmd
	if action.Branch != "" {
		cmd = exec.Command("git", "push", "origin", lockedBranch+":"+action.Branch)
	} else {
		cmd = exec.Command("git", "push", "origin", lockedBranch)
	}
	cmd.Dir = g.repoRoot

	out, err := cmd.CombinedOutput()
	if err != nil {
		result.Success = false
		result.Error = fmt.Sprintf("failed to push: %s - %v", string(out), err)
		return result
	}

	result.Success = true
	result.Output = fmt.Sprintf("Pushed branch %s", lockedBranch)
	return result
}

// executeMerge merges a branch into the current branch
func (g *GitGuard) executeMerge(action GitAction) *GitResult {
	result := &GitResult{Timestamp: time.Now()}

	// Verify task has a branch lock
	var lockedBranch string
	err := g.db.QueryRow(`
		SELECT branch FROM git_branch_locks WHERE task_id = ?
	`, action.TaskID).Scan(&lockedBranch)

	if err == sql.ErrNoRows {
		result.Success = false
		result.Error = fmt.Sprintf("task %s has no branch lock - cannot merge", action.TaskID)
		return result
	}
	if err != nil {
		result.Success = false
		result.Error = fmt.Sprintf("failed to check branch lock: %v", err)
		return result
	}

	// Execute merge
	cmd := exec.Command("git", "merge", "--no-ff", "-m", action.Message, action.Branch)
	cmd.Dir = g.repoRoot

	out, err := cmd.CombinedOutput()
	if err != nil {
		result.Success = false
		result.Error = fmt.Sprintf("failed to merge: %s - %v", string(out), err)
		return result
	}

	result.Success = true
	result.Output = fmt.Sprintf("Merged %s into %s", action.Branch, lockedBranch)
	return result
}

// Execute performs a git action with idempotency
func (g *GitGuard) Execute(ctx context.Context, req GitGuardRequest) (*GitResult, error) {
	// Create action from request
	action := GitAction{
		ID:        fmt.Sprintf("gg-%d", time.Now().UnixNano()),
		TaskID:    req.TaskID,
		Type:      GitActionType(req.Action),
		Branch:    req.Branch,
		Message:   req.Message,
		Files:     req.Files,
		Author:    req.Author,
		CreatedAt: time.Now(),
	}
	action.PayloadHash = computePayloadHash(action)

	// 1. Check idempotency - has this exact action been executed?
	existing, err := g.getExistingAction(ctx, action.PayloadHash)
	if err != nil {
		return nil, fmt.Errorf("failed to check idempotency: %w", err)
	}
	if existing != nil {
		return &GitResult{
			Success:   true,
			Output:    "Already executed (idempotent)",
			CommitID:  existing.ID,
			Timestamp: time.Now(),
		}, nil
	}

	// 2. Acquire lock (single-writer)
	g.mu.Lock()
	defer g.mu.Unlock()

	// 3. Check git safety
	if safe, reason := g.CheckSafe(); !safe {
		return &GitResult{
			Success:   false,
			Error:     fmt.Sprintf("Git not safe: %s", reason),
			Timestamp: time.Now(),
		}, nil
	}

	// 4. Record pending action
	if err := g.recordPendingAction(ctx, action); err != nil {
		log.Printf("Warning: failed to record pending action: %v", err)
	}

	// 5. Execute based on action type
	var result *GitResult
	switch action.Type {
	case GitActionCommit:
		result = g.executeCommit(action)
	case GitActionPush:
		result = g.executePush(action)
	case GitActionBranch:
		result = g.executeBranch(action)
	case GitActionMerge:
		result = g.executeMerge(action)
	default:
		result = &GitResult{
			Success:   false,
			Error:     fmt.Sprintf("Unknown action type: %s", action.Type),
			Timestamp: time.Now(),
		}
	}

	// 6. Record result if successful
	if result.Success {
		if err := g.recordAction(ctx, action, result); err != nil {
			log.Printf("Warning: failed to record action result: %v", err)
		}
	}

	return result, nil
}

// ReleaseBranchLock releases the branch lock for a task
func (g *GitGuard) ReleaseBranchLock(ctx context.Context, taskID string) error {
	_, err := g.db.ExecContext(ctx, `DELETE FROM git_branch_locks WHERE task_id = ?`, taskID)
	return err
}

// GetBranchLock returns the branch locked to a task
func (g *GitGuard) GetBranchLock(ctx context.Context, taskID string) (string, error) {
	var branch string
	err := g.db.QueryRowContext(ctx, `SELECT branch FROM git_branch_locks WHERE task_id = ?`, taskID).Scan(&branch)
	if err == sql.ErrNoRows {
		return "", nil
	}
	return branch, err
}

// gitguardHandler creates an HTTP handler for GitGuard
func gitguardHandler(gg *GitGuard) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")

		if r.Method == http.MethodGet {
			// List git guard actions
			taskID := r.URL.Query().Get("task_id")
			limit := 50
			if l := r.URL.Query().Get("limit"); l != "" {
				fmt.Sscanf(l, "%d", &limit)
			}

			var rows *sql.Rows
			var err error

			if taskID != "" {
				rows, err = gg.db.Query(`
					SELECT id, task_id, type, branch, message, status, commit_id, created_at, completed_at
					FROM git_guard_actions
					WHERE task_id = ?
					ORDER BY created_at DESC
					LIMIT ?
				`, taskID, limit)
			} else {
				rows, err = gg.db.Query(`
					SELECT id, task_id, type, branch, message, status, commit_id, created_at, completed_at
					FROM git_guard_actions
					ORDER BY created_at DESC
					LIMIT ?
				`, limit)
			}

			if err != nil {
				http.Error(w, fmt.Sprintf("failed to query actions: %v", err), http.StatusInternalServerError)
				return
			}
			defer rows.Close()

			var actions []map[string]interface{}
			for rows.Next() {
				var a struct {
					ID          string
					TaskID      string
					Type        string
					Branch      sql.NullString
					Message     sql.NullString
					Status      string
					CommitID    sql.NullString
					CreatedAt   time.Time
					CompletedAt sql.NullTime
				}
				if err := rows.Scan(&a.ID, &a.TaskID, &a.Type, &a.Branch, &a.Message, &a.Status, &a.CommitID, &a.CreatedAt, &a.CompletedAt); err != nil {
					continue
				}
				actions = append(actions, map[string]interface{}{
					"id":           a.ID,
					"task_id":      a.TaskID,
					"type":         a.Type,
					"branch":       a.Branch.String,
					"message":      a.Message.String,
					"status":       a.Status,
					"commit_id":    a.CommitID.String,
					"created_at":   a.CreatedAt,
					"completed_at": a.CompletedAt.Time,
				})
			}

			json.NewEncoder(w).Encode(map[string]interface{}{
				"actions": actions,
				"count":   len(actions),
			})
			return
		}

		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		var req GitGuardRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid request body", http.StatusBadRequest)
			return
		}

		// Validate required fields
		if req.TaskID == "" {
			http.Error(w, "task_id is required", http.StatusBadRequest)
			return
		}
		if req.Action == "" {
			http.Error(w, "action is required", http.StatusBadRequest)
			return
		}

		result, err := gg.Execute(r.Context(), req)
		if err != nil {
			http.Error(w, fmt.Sprintf("execution failed: %v", err), http.StatusInternalServerError)
			return
		}

		w.Header().Set("Content-Type", "application/json")
		if result.Success {
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status":     "success",
				"result":     result,
				"idempotent": result.Output == "Already executed (idempotent)",
			})
		} else {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status": "failed",
				"result": result,
			})
		}
	}
}
