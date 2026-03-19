// lock.go — forge lock: lightweight multi-scope lock manager.
//
// Ports .forge/scripts/git-lock.sh to Go.
// All operations are pure local file I/O — no daemon required.
//
// Lock files live in:
//
//	.forge/locks/state/{scope}.lock   — active lock (JSON)
//	.forge/locks/archive/             — expired / released / forced copies
//
// JSON format:
//
//	{"scope":"repo","agent":"forge:prya","task":"manual-sync",
//	 "ttl":1800,"acquired_at":"2026-03-09T08:00:00Z","expires_at":"2026-03-09T08:30:00Z"}
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const (
	lockStateDir   = ".forge/locks/state"
	lockArchiveDir = ".forge/locks/archive"

	lockDefaultTTL   = 300
	lockDefaultAgent = ""
)

// validScopes mirrors the bash script's accepted values.
var validLockScopes = map[string]bool{
	"repo":     true,
	"submodule": true,
	"domain":   true,
	"project":  true,
	"dispatch": true,
}

// sanitizeKeyRe strips characters not suitable for a filename key.
var sanitizeKeyRe = regexp.MustCompile(`[^a-zA-Z0-9_.\-]`)

// ---------------------------------------------------------------------------
// Lock JSON type
// ---------------------------------------------------------------------------

type lockRecord struct {
	Scope      string `json:"scope"`
	Agent      string `json:"agent"`
	Task       string `json:"task,omitempty"`
	TTL        int64  `json:"ttl"`
	AcquiredAt string `json:"acquired_at"`
	ExpiresAt  string `json:"expires_at"`
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

// lockForgeRoot returns the FORGE root, mirroring detectStateRoot.
func lockForgeRoot() string {
	if root := os.Getenv("FORGE_ROOT"); root != "" {
		return root
	}
	dir, err := os.Getwd()
	if err != nil {
		return "."
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, ".forge")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return "."
}

// lockStateDir returns the absolute path to the state directory.
func lockStateDirAbs() string {
	return filepath.Join(lockForgeRoot(), lockStateDir)
}

// lockArchiveDirAbs returns the absolute path to the archive directory.
func lockArchiveDirAbs() string {
	return filepath.Join(lockForgeRoot(), lockArchiveDir)
}

// sanitizeKey converts a path/scope string into a safe filename component.
// Mirrors: printf "%s" "$1" | tr '/: ' '___' | tr -cd '[:alnum:]_.-'
func sanitizeKey(s string) string {
	// First replace /, :, and space with underscore
	r := strings.NewReplacer("/", "_", ":", "_", " ", "_")
	s = r.Replace(s)
	// Then strip all characters that are not alphanumeric, _, ., or -
	return sanitizeKeyRe.ReplaceAllString(s, "")
}

// buildLockKey builds the filename key from scope + optional path/task.
func buildLockKey(scope, pathArg, taskArg string) (string, error) {
	switch scope {
	case "repo":
		return "repo-main", nil
	case "submodule", "domain", "project":
		if pathArg == "" {
			return "", fmt.Errorf("--path is required for scope=%s\n\nFix: re-run with --path <value>", scope)
		}
		return scope + "-" + sanitizeKey(pathArg), nil
	case "dispatch":
		if taskArg == "" {
			return "", fmt.Errorf("--task is required for scope=dispatch\n\nFix: re-run with --task <value>")
		}
		return "dispatch-" + sanitizeKey(taskArg), nil
	default:
		return "", fmt.Errorf("invalid scope %q — valid: repo, submodule, domain, project, dispatch", scope)
	}
}

// lockFilePath returns the full path to a state lock file.
func lockFilePath(key string) string {
	return filepath.Join(lockStateDirAbs(), key+".lock")
}

// ensureLockDirs creates the state and archive directories if missing.
func ensureLockDirs() error {
	for _, d := range []string{lockStateDirAbs(), lockArchiveDirAbs()} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			return fmt.Errorf("create lock dir %s: %w\n\nFix: check permissions on .forge/locks/", d, err)
		}
	}
	return nil
}

// readLockRecord parses a lock JSON file; returns nil + error on failure.
func readLockRecord(path string) (*lockRecord, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var lr lockRecord
	if err := json.Unmarshal(data, &lr); err != nil {
		return nil, fmt.Errorf("malformed lock file %s: %w", path, err)
	}
	return &lr, nil
}

// writeLockRecord serialises and writes a lock record atomically.
func writeLockRecord(path string, lr *lockRecord) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("mkdir lock dir: %w", err)
	}
	data, err := json.MarshalIndent(lr, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal lock: %w", err)
	}
	// Write atomically via O_CREATE|O_EXCL to detect races
	f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
	if err != nil {
		return err // caller inspects os.IsExist
	}
	defer f.Close()
	if _, err := f.Write(data); err != nil {
		os.Remove(path) // clean up partial write
		return fmt.Errorf("write lock file: %w", err)
	}
	return nil
}

// isExpired reports whether a lock's TTL has elapsed.
func isExpired(lr *lockRecord) bool {
	t, err := time.Parse(time.RFC3339, lr.AcquiredAt)
	if err != nil {
		return false
	}
	return time.Since(t) > time.Duration(lr.TTL)*time.Second
}

// archiveLock moves a lock file to the archive directory with a suffix tag.
func archiveLock(key, srcPath, tag string) error {
	ts := time.Now().UTC().Format("20060102-150405")
	dst := filepath.Join(lockArchiveDirAbs(), fmt.Sprintf("%s.%s.%s.lock", key, tag, ts))
	if err := os.Rename(srcPath, dst); err != nil {
		return fmt.Errorf("archive lock %s: %w", key, err)
	}
	return nil
}

// agentDefault returns the agent identifier, falling back to hostname.
func agentDefault(agentFlag string) string {
	if agentFlag != "" {
		return agentFlag
	}
	h, err := os.Hostname()
	if err != nil || h == "" {
		return "unknown"
	}
	return "forge:" + h
}

// ---------------------------------------------------------------------------
// Top-level lockCmd
// ---------------------------------------------------------------------------

var lockCmd = &cobra.Command{
	Use:   "lock",
	Short: "Manage FORGE multi-scope file locks",
	Long: `Lightweight lock manager for multi-node FORGE operations.

Lock files are stored as JSON under .forge/locks/state/.
Expired or released locks are archived to .forge/locks/archive/.

Scopes:
  repo       — whole-repo git lock (key: repo-main)
  submodule  — single submodule (requires --path)
  domain     — domain subtree  (requires --path)
  project    — project subtree (requires --path)
  dispatch   — dispatch task   (requires --task)

Examples:
  forge lock acquire --scope repo --agent forge:prya --ttl 3600
  forge lock acquire --scope dispatch --task hourly-sync --ttl 300
  forge lock status  --scope repo
  forge lock release --scope repo --agent forge:prya
  forge lock list
  forge lock cleanup`,
	Run: func(cmd *cobra.Command, args []string) {
		cmd.Help()
	},
}

func init() {
	lockCmd.Hidden = true
	lockCmd.AddCommand(lockAcquireCmd)
	lockCmd.AddCommand(lockReleaseCmd)
	lockCmd.AddCommand(lockStatusCmd)
	lockCmd.AddCommand(lockListCmd)
	lockCmd.AddCommand(lockCleanupCmd)
}

// ---------------------------------------------------------------------------
// forge lock acquire
// ---------------------------------------------------------------------------

var lockAcquireCmd = &cobra.Command{
	Use:   "acquire",
	Short: "Acquire a lock for the given scope",
	Long: `Create a lock file under .forge/locks/state/ for the specified scope.

The lock is created atomically — if another process holds it, the command fails
unless the lock is expired (auto-removed) or --force is given.

Examples:
  forge lock acquire --scope repo --agent forge:prya --ttl 3600
  forge lock acquire --scope dispatch --task build-123 --force`,
	RunE: func(cmd *cobra.Command, args []string) error {
		scope, _ := cmd.Flags().GetString("scope")
		pathArg, _ := cmd.Flags().GetString("path")
		taskArg, _ := cmd.Flags().GetString("task")
		agentFlag, _ := cmd.Flags().GetString("agent")
		ttl, _ := cmd.Flags().GetInt64("ttl")
		force, _ := cmd.Flags().GetBool("force")

		if scope == "" {
			return fmt.Errorf("--scope is required\n\nFix: add --scope <repo|submodule|domain|project|dispatch>")
		}
		if !validLockScopes[scope] {
			return fmt.Errorf("invalid scope %q — valid: repo, submodule, domain, project, dispatch", scope)
		}

		if err := ensureLockDirs(); err != nil {
			return err
		}

		key, err := buildLockKey(scope, pathArg, taskArg)
		if err != nil {
			return err
		}
		path := lockFilePath(key)
		agent := agentDefault(agentFlag)

		// Check for existing lock
		if _, statErr := os.Stat(path); statErr == nil {
			existing, readErr := readLockRecord(path)
			if readErr == nil && isExpired(existing) {
				// Auto-remove stale lock
				if archErr := archiveLock(key, path, "stale"); archErr != nil {
					return archErr
				}
				fmt.Printf("[INFO] Removed stale lock: %s\n", key)
			} else if force {
				if archErr := archiveLock(key, path, "forced"); archErr != nil {
					return archErr
				}
				fmt.Printf("[INFO] Force-replaced lock: %s\n", key)
			} else {
				ownerAgent := "unknown"
				if readErr == nil {
					ownerAgent = existing.Agent
				}
				return fmt.Errorf(
					"lock already held: %s (agent=%s)\n\nFix: wait for the lock to expire, or use --force to override",
					key, ownerAgent,
				)
			}
		}

		now := time.Now().UTC()
		expires := now.Add(time.Duration(ttl) * time.Second)
		lr := &lockRecord{
			Scope:      scope,
			Agent:      agent,
			Task:       taskArg,
			TTL:        ttl,
			AcquiredAt: now.Format(time.RFC3339),
			ExpiresAt:  expires.Format(time.RFC3339),
		}

		if err := writeLockRecord(path, lr); err != nil {
			if os.IsExist(err) {
				return fmt.Errorf(
					"lock race: %s was acquired by another process between check and write\n\nFix: retry the command",
					key,
				)
			}
			return fmt.Errorf("write lock: %w", err)
		}

		fmt.Printf("[INFO] Acquired lock: %s (agent=%s ttl=%ds)\n", key, agent, ttl)
		return nil
	},
}

func init() {
	lockAcquireCmd.Flags().String("scope", "", "Lock scope: repo|submodule|domain|project|dispatch")
	lockAcquireCmd.Flags().String("path", "", "Sub-path for scope submodule/domain/project")
	lockAcquireCmd.Flags().String("task", "", "Task identifier for scope dispatch")
	lockAcquireCmd.Flags().String("agent", "", "Agent identifier (default: forge:<hostname>)")
	lockAcquireCmd.Flags().Int64("ttl", lockDefaultTTL, "Lock TTL in seconds")
	lockAcquireCmd.Flags().Bool("force", false, "Overwrite an existing (non-expired) lock")
}

// ---------------------------------------------------------------------------
// forge lock release
// ---------------------------------------------------------------------------

var lockReleaseCmd = &cobra.Command{
	Use:   "release",
	Short: "Release a held lock",
	Long: `Remove the lock file for the specified scope and move it to the archive.

By default the command refuses to release a lock owned by a different agent.
Use --force to override the owner check.

Examples:
  forge lock release --scope repo --agent forge:prya
  forge lock release --scope dispatch --task build-123 --force`,
	RunE: func(cmd *cobra.Command, args []string) error {
		scope, _ := cmd.Flags().GetString("scope")
		pathArg, _ := cmd.Flags().GetString("path")
		taskArg, _ := cmd.Flags().GetString("task")
		agentFlag, _ := cmd.Flags().GetString("agent")
		force, _ := cmd.Flags().GetBool("force")

		if scope == "" {
			return fmt.Errorf("--scope is required\n\nFix: add --scope <repo|submodule|domain|project|dispatch>")
		}
		if !validLockScopes[scope] {
			return fmt.Errorf("invalid scope %q — valid: repo, submodule, domain, project, dispatch", scope)
		}

		if err := ensureLockDirs(); err != nil {
			return err
		}

		key, err := buildLockKey(scope, pathArg, taskArg)
		if err != nil {
			return err
		}
		path := lockFilePath(key)
		agent := agentDefault(agentFlag)

		if _, statErr := os.Stat(path); os.IsNotExist(statErr) {
			fmt.Printf("[INFO] No lock present: %s\n", key)
			return nil
		}

		existing, readErr := readLockRecord(path)
		if readErr == nil && !force && existing.Agent != "" && existing.Agent != agent {
			return fmt.Errorf(
				"refusing to release lock owned by %q (use --force to override)\n\nFix: pass --force or use the correct --agent value",
				existing.Agent,
			)
		}

		if err := archiveLock(key, path, "released"); err != nil {
			// Fall back to plain remove if archive fails
			if rmErr := os.Remove(path); rmErr != nil {
				return fmt.Errorf("release lock %s: %w", key, rmErr)
			}
		}

		fmt.Printf("[INFO] Released lock: %s\n", key)
		return nil
	},
}

func init() {
	lockReleaseCmd.Flags().String("scope", "", "Lock scope: repo|submodule|domain|project|dispatch")
	lockReleaseCmd.Flags().String("path", "", "Sub-path for scope submodule/domain/project")
	lockReleaseCmd.Flags().String("task", "", "Task identifier for scope dispatch")
	lockReleaseCmd.Flags().String("agent", "", "Agent identifier (default: forge:<hostname>)")
	lockReleaseCmd.Flags().Bool("force", false, "Release even if owned by a different agent")
}

// ---------------------------------------------------------------------------
// forge lock status
// ---------------------------------------------------------------------------

var lockStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show the status of a single lock",
	Long: `Display who holds the lock, when it expires, and whether it is stale.

Prints "unlocked <key>" when no lock exists.

Examples:
  forge lock status --scope repo
  forge lock status --scope dispatch --task build-123`,
	RunE: func(cmd *cobra.Command, args []string) error {
		scope, _ := cmd.Flags().GetString("scope")
		pathArg, _ := cmd.Flags().GetString("path")
		taskArg, _ := cmd.Flags().GetString("task")

		if scope == "" {
			return fmt.Errorf("--scope is required\n\nFix: add --scope <repo|submodule|domain|project|dispatch>")
		}
		if !validLockScopes[scope] {
			return fmt.Errorf("invalid scope %q — valid: repo, submodule, domain, project, dispatch", scope)
		}

		if err := ensureLockDirs(); err != nil {
			return err
		}

		key, err := buildLockKey(scope, pathArg, taskArg)
		if err != nil {
			return err
		}
		path := lockFilePath(key)

		if _, statErr := os.Stat(path); os.IsNotExist(statErr) {
			fmt.Printf("unlocked %s\n", key)
			return nil
		}

		lr, err := readLockRecord(path)
		if err != nil {
			return err
		}

		acquiredAt, _ := time.Parse(time.RFC3339, lr.AcquiredAt)
		age := int(time.Since(acquiredAt).Seconds())
		stale := "no"
		if isExpired(lr) {
			stale = "yes"
		}

		fmt.Printf("locked %s agent=%s age=%ds ttl=%ds stale=%s\n",
			key, lr.Agent, age, lr.TTL, stale)
		return nil
	},
}

func init() {
	lockStatusCmd.Flags().String("scope", "", "Lock scope: repo|submodule|domain|project|dispatch")
	lockStatusCmd.Flags().String("path", "", "Sub-path for scope submodule/domain/project")
	lockStatusCmd.Flags().String("task", "", "Task identifier for scope dispatch")
}

// ---------------------------------------------------------------------------
// forge lock list
// ---------------------------------------------------------------------------

var lockListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all active locks",
	Long: `Show every lock file in .forge/locks/state/ with key, agent, age, and TTL.

Prints "no-locks" when the state directory is empty.

Examples:
  forge lock list`,
	RunE: func(cmd *cobra.Command, args []string) error {
		if err := ensureLockDirs(); err != nil {
			return err
		}

		entries, err := os.ReadDir(lockStateDirAbs())
		if err != nil {
			return fmt.Errorf("read lock state dir: %w\n\nFix: verify .forge/locks/state/ exists", err)
		}

		found := false
		for _, entry := range entries {
			if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".lock") {
				continue
			}
			found = true
			path := filepath.Join(lockStateDirAbs(), entry.Name())
			lr, err := readLockRecord(path)
			if err != nil {
				fmt.Fprintf(os.Stderr, "[WARN] skipping malformed lock file %s: %v\n", entry.Name(), err)
				continue
			}

			acquiredAt, _ := time.Parse(time.RFC3339, lr.AcquiredAt)
			age := int(time.Since(acquiredAt).Seconds())
			key := strings.TrimSuffix(entry.Name(), ".lock")

			fmt.Printf("key=%s scope=%s agent=%s task=%s age=%ds ttl=%ds\n",
				key, lr.Scope, lr.Agent, lr.Task, age, lr.TTL)
		}

		if !found {
			fmt.Println("no-locks")
		}
		return nil
	},
}

// ---------------------------------------------------------------------------
// forge lock cleanup
// ---------------------------------------------------------------------------

var lockCleanupCmd = &cobra.Command{
	Use:   "cleanup",
	Short: "Remove all expired locks",
	Long: `Scan .forge/locks/state/ and archive any lock whose TTL has elapsed.

Safe to run as a cron job or patrol step.

Examples:
  forge lock cleanup`,
	RunE: func(cmd *cobra.Command, args []string) error {
		if err := ensureLockDirs(); err != nil {
			return err
		}

		entries, err := os.ReadDir(lockStateDirAbs())
		if err != nil {
			return fmt.Errorf("read lock state dir: %w\n\nFix: verify .forge/locks/state/ exists", err)
		}

		cleaned := 0
		for _, entry := range entries {
			if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".lock") {
				continue
			}
			path := filepath.Join(lockStateDirAbs(), entry.Name())
			lr, err := readLockRecord(path)
			if err != nil {
				fmt.Fprintf(os.Stderr, "[WARN] skipping malformed lock file %s: %v\n", entry.Name(), err)
				continue
			}
			if isExpired(lr) {
				key := strings.TrimSuffix(entry.Name(), ".lock")
				if archErr := archiveLock(key, path, "cleanup"); archErr != nil {
					fmt.Fprintf(os.Stderr, "[WARN] could not archive %s: %v\n", key, archErr)
					continue
				}
				cleaned++
			}
		}

		fmt.Printf("[INFO] Cleaned stale locks: %d\n", cleaned)
		return nil
	},
}
