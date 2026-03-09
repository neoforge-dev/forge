package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// conventionalCommitRE validates conventional commit message format.
var conventionalCommitRE = regexp.MustCompile(
	`^(feat|fix|docs|style|refactor|test|chore|perf|ci|build|revert)(\(.+\))?: `,
)

const (
	staleLockThreshold = 120 * time.Second // seconds before index.lock is considered stale
	commitStatsFile    = ".forge/heartbeat/commit_stats.json"
)

// gitCmd represents the "git" noun
var gitCmd = &cobra.Command{
	Use:   "git",
	Short: "Git operations with fleet-safe retry",
	Long:  "Git utilities designed for the FORGE multi-agent environment where main advances continuously.",
}

// gitPushCmd: forge git push
var gitPushCmd = &cobra.Command{
	Use:   "push",
	Short: "Pull-rebase-push with fleet-safe retry",
	Long: `Push the current (or specified) branch to remote with automatic rebase and retry.

Designed for the FORGE multi-agent environment where main advances continuously.
On each attempt it:
  1. Removes a stale .git/index.lock if present
  2. Fetches the target branch from remote
  3. If on a feature branch: rebases onto remote/target, merges to target, pushes
  4. If already on target: pulls --rebase then pushes
  5. On push failure: sleeps N seconds (N = attempt number) and retries

Examples:
  forge git push                              # push current branch → main
  forge git push --branch feat/my-feature     # push specific branch
  forge git push --retry 3                    # fewer retries
  forge git push --remote upstream --target develop`,
	RunE: runGitPush,
}

func runGitPush(cmd *cobra.Command, args []string) error {
	maxRetry, _ := cmd.Flags().GetInt("retry")
	branchFlag, _ := cmd.Flags().GetString("branch")
	remote, _ := cmd.Flags().GetString("remote")
	target, _ := cmd.Flags().GetString("target")

	// Resolve branch: flag > current branch
	branch := branchFlag
	if branch == "" {
		out, err := runGitCmd("branch", "--show-current")
		if err != nil {
			return fmt.Errorf("[git push] ERROR: cannot determine current branch: %w\n  Fix: pass --branch explicitly", err)
		}
		branch = strings.TrimSpace(out)
		if branch == "" {
			return fmt.Errorf("[git push] ERROR: detached HEAD — cannot determine branch\n  Fix: checkout a branch or pass --branch explicitly")
		}
	}

	fmt.Printf("[git push] branch=%s target=%s/%s\n", branch, remote, target)

	for attempt := 1; attempt <= maxRetry; attempt++ {
		fmt.Printf("[git push] attempt %d/%d\n", attempt, maxRetry)

		if err := gitPushAttempt(branch, remote, target); err == nil {
			fmt.Printf("[git push] Pushed successfully on attempt %d\n", attempt)
			return nil
		} else if attempt < maxRetry {
			fmt.Printf("[git push] Push rejected (%v), waiting %ds before retry...\n", err, attempt)
			time.Sleep(time.Duration(attempt) * time.Second)
		} else {
			fmt.Printf("[git push] Push rejected (%v)\n", err)
		}
	}

	return fmt.Errorf("[git push] ERROR: all %d attempts exhausted\n  Recovery: run `git status` to inspect conflicts, then retry with `forge git push`", maxRetry)
}

// gitPushAttempt executes one push attempt: clear lock → fetch → rebase/pull → push.
func gitPushAttempt(branch, remote, target string) error {
	// Step 1: Remove stale index.lock
	lockPath := ".git/index.lock"
	if info, err := os.Stat(lockPath); err == nil {
		if info.Size() == 0 {
			if rmErr := os.Remove(lockPath); rmErr != nil {
				fmt.Printf("[git push] WARN: could not remove stale index.lock: %v\n", rmErr)
			} else {
				fmt.Println("[git push] Removed stale .git/index.lock")
			}
		}
	}

	// Step 2: Fetch target branch
	if _, err := runGitCmd("fetch", remote, target, "--quiet"); err != nil {
		// Non-fatal — continue with local state
		fmt.Printf("[git push] WARN: fetch failed (%v), continuing with local state\n", err)
	}

	// Step 3: Determine current branch
	currentOut, err := runGitCmd("branch", "--show-current")
	if err != nil {
		return fmt.Errorf("cannot determine current branch: %w", err)
	}
	current := strings.TrimSpace(currentOut)

	if current != target {
		// Feature branch path: rebase onto remote/target, merge to target, push
		remoteRef := remote + "/" + target

		if _, rebaseErr := runGitCmd("rebase", remoteRef, "--quiet"); rebaseErr != nil {
			// Rebase conflict — try to auto-resolve .forge/ state files then continue
			fmt.Printf("[git push] WARN: rebase conflict (%v) — attempting auto-resolve for .forge/ state files\n", rebaseErr)
			runGitCmdIgnoreErr("checkout", "--ours", ".forge/ops/heartbeat/")
			runGitCmdIgnoreErr("checkout", "--ours", ".forge/heartbeat/commit_stats.json")
			runGitCmdIgnoreErr("add", "-A")
			if _, contErr := runGitCmd("rebase", "--continue", "--no-edit"); contErr != nil {
				runGitCmdIgnoreErr("rebase", "--abort")
				return fmt.Errorf("rebase failed and could not continue: %w", contErr)
			}
		}

		// Merge rebased branch into target
		if _, checkoutErr := runGitCmd("checkout", target, "--quiet"); checkoutErr != nil {
			return fmt.Errorf("checkout %s: %w", target, checkoutErr)
		}
		if _, mergeErr := runGitCmd("merge", current, "--no-edit", "--quiet"); mergeErr != nil {
			// Try to restore original branch before returning error
			runGitCmdIgnoreErr("checkout", current, "--quiet")
			return fmt.Errorf("merge %s into %s: %w", current, target, mergeErr)
		}
	} else {
		// Already on target — pull --rebase
		if _, pullErr := runGitCmd("pull", remote, target, "--rebase", "--quiet"); pullErr != nil {
			// Fallback: plain pull
			if _, fallbackErr := runGitCmd("pull", remote, target, "--no-rebase", "--no-edit", "--quiet"); fallbackErr != nil {
				fmt.Printf("[git push] WARN: pull failed (%v), attempting push anyway\n", pullErr)
			}
		}
	}

	// Step 4: Push
	if _, pushErr := runGitCmd("push", remote, target, "--quiet"); pushErr != nil {
		// If we switched to target, go back to the original branch before returning
		if current != target {
			runGitCmdIgnoreErr("checkout", current, "--quiet")
		}
		return fmt.Errorf("push failed: %w", pushErr)
	}

	// Restore original branch if we switched
	if current != target {
		runGitCmdIgnoreErr("checkout", current, "--quiet")
	}

	return nil
}

// runGitCmd runs a git subcommand and returns (stdout, error).
// stderr is captured and included in the error message.
func runGitCmd(gitArgs ...string) (string, error) {
	cmd := exec.Command("git", gitArgs...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	if err != nil {
		se := strings.TrimSpace(stderr.String())
		if se != "" {
			return "", fmt.Errorf("git %s: %w\n  stderr: %s", strings.Join(gitArgs, " "), err, se)
		}
		return "", fmt.Errorf("git %s: %w", strings.Join(gitArgs, " "), err)
	}
	return stdout.String(), nil
}

// runGitCmdIgnoreErr runs a git command and silently ignores errors (best-effort cleanup).
func runGitCmdIgnoreErr(gitArgs ...string) {
	cmd := exec.Command("git", gitArgs...)
	_ = cmd.Run()
}

// ---------------------------------------------------------------------------
// forge git guard
// ---------------------------------------------------------------------------

var gitGuardCmd = &cobra.Command{
	Use:   "guard",
	Short: "Check repository for unsafe git state",
	Long: `Check the git repository for conditions that would make a commit unsafe.

Checks performed:
  - .git/index.lock present (stale if >120s old, fresh if newer)
  - .git/MERGE_HEAD present (merge in progress)
  - Uncommitted conflict markers (git status --porcelain)
  - Current branch is not main (warning only)

Exits 0 if safe, exits 1 if any hard issue is found.
Use --strict to also fail on "not on main" branch check.

Examples:
  forge git guard
  forge git guard --strict`,
	RunE: runGitGuard,
}

type guardIssue struct {
	level   string // "error" or "warning"
	check   string
	detail  string
}

func runGitGuard(cmd *cobra.Command, args []string) error {
	strict, _ := cmd.Flags().GetBool("strict")

	forgeRoot := resolveForgeRoot()
	gitDir := filepath.Join(forgeRoot, ".git")

	var issues []guardIssue

	// 1. Check index.lock
	lockPath := filepath.Join(gitDir, "index.lock")
	if info, err := os.Stat(lockPath); err == nil {
		age := time.Since(info.ModTime())
		if age > staleLockThreshold {
			issues = append(issues, guardIssue{
				level:  "error",
				check:  "index.lock",
				detail: fmt.Sprintf("LOCK (stale, %.0fs old) — fix: forge git clear-locks", age.Seconds()),
			})
		} else {
			issues = append(issues, guardIssue{
				level:  "error",
				check:  "index.lock",
				detail: fmt.Sprintf("LOCK (fresh, %.0fs old) — another process may be writing", age.Seconds()),
			})
		}
	} else {
		printGuardRow("ok", "index.lock", "clean")
	}

	// 2. Check MERGE_HEAD (merge in progress)
	mergeHead := filepath.Join(gitDir, "MERGE_HEAD")
	if _, err := os.Stat(mergeHead); err == nil {
		issues = append(issues, guardIssue{
			level:  "error",
			check:  "MERGE_HEAD",
			detail: "MERGE IN PROGRESS — resolve conflicts before committing",
		})
	} else {
		printGuardRow("ok", "MERGE_HEAD", "none")
	}

	// 3. Check uncommitted conflicts via git status --porcelain
	conflictOut, conflictErr := runGitCmdInDir(forgeRoot, "status", "--porcelain")
	if conflictErr == nil {
		conflicts := 0
		for _, line := range strings.Split(strings.TrimSpace(conflictOut), "\n") {
			if len(line) >= 2 {
				xy := line[:2]
				// UU = both modified (conflict), AA = both added, DD = both deleted
				if xy == "UU" || xy == "AA" || xy == "DD" {
					conflicts++
				}
			}
		}
		if conflicts > 0 {
			issues = append(issues, guardIssue{
				level:  "error",
				check:  "conflicts",
				detail: fmt.Sprintf("CONFLICTS (%d unresolved) — run: git status", conflicts),
			})
		} else {
			printGuardRow("ok", "conflicts", "none")
		}
	} else {
		printGuardRow("warning", "conflicts", "could not check (git unavailable)")
	}

	// 4. Branch check (warning or strict-error)
	branchOut, branchErr := runGitCmdInDir(forgeRoot, "rev-parse", "--abbrev-ref", "HEAD")
	branch := strings.TrimSpace(branchOut)
	if branchErr != nil || branch == "" || branch == "HEAD" {
		printGuardRow("warning", "branch", "cannot determine (detached HEAD?)")
	} else if branch != "main" && branch != "master" {
		msg := fmt.Sprintf("WRONG BRANCH (%s) — expected main", branch)
		if strict {
			issues = append(issues, guardIssue{
				level:  "error",
				check:  "branch",
				detail: msg,
			})
		} else {
			printGuardRow("warning", "branch", msg)
		}
	} else {
		printGuardRow("ok", "branch", branch)
	}

	// Print issues
	for _, iss := range issues {
		printGuardRow(iss.level, iss.check, iss.detail)
	}

	if len(issues) > 0 {
		fmt.Fprintf(os.Stderr, "\n%sUnsafe state detected — %d issue(s) found.%s\n",
			colorRed, len(issues), colorReset)
		return fmt.Errorf("unsafe git state: %d issue(s)", len(issues))
	}

	fmt.Printf("\n%sRepository state is safe.%s\n", colorGreen, colorReset)
	return nil
}

// printGuardRow prints a colored status row for the guard table.
func printGuardRow(level, check, detail string) {
	var sym string
	switch level {
	case "ok":
		sym = colorGreen + "✓" + colorReset
	case "warning":
		sym = colorYellow + "⚠" + colorReset
	default: // error
		sym = colorRed + "✗" + colorReset
	}
	fmt.Printf("  %s  %-14s %s\n", sym, check, detail)
}

// ---------------------------------------------------------------------------
// forge git commit
// ---------------------------------------------------------------------------

var gitCommitCmd = &cobra.Command{
	Use:   "commit",
	Short: "Conventional commit with retry + mutex",
	Long: `Commit staged (or modified) files with conventional commit validation and retry.

Steps:
  1. Run forge git guard (unless --skip-guard)
  2. Validate message against conventional commit pattern
  3. git add -u (or git add <files> if specified via --files)
  4. git commit -m MESSAGE with up to 3 retries (2s backoff) on lock collision
  5. Append result to .forge/heartbeat/commit_stats.json

Examples:
  forge git commit --message "feat(auth): add JWT support"
  forge git commit -m "fix(api): resolve timeout" --allow-empty
  forge git commit -m "chore: lint" --skip-guard`,
	RunE: runGitCommit,
}

// commitStats is the JSON structure for .forge/heartbeat/commit_stats.json.
type commitStats struct {
	Version            string              `json:"version"`
	TotalAttempts      int                 `json:"total_attempts"`
	SuccessfulCommits  int                 `json:"successful_commits"`
	FailedCommits      int                 `json:"failed_commits"`
	TotalRetries       int                 `json:"total_retries"`
	FirstAttemptSuccess int               `json:"first_attempt_success"`
	SuccessRate        float64             `json:"success_rate"`
	LastUpdated        string              `json:"last_updated"`
	History            []commitHistoryEntry `json:"history"`
}

type commitHistoryEntry struct {
	Timestamp string `json:"timestamp"`
	Success   bool   `json:"success"`
	Attempts  int    `json:"attempts"`
	Message   string `json:"message"`
}

func runGitCommit(cmd *cobra.Command, args []string) error {
	message, _ := cmd.Flags().GetString("message")
	allowEmpty, _ := cmd.Flags().GetBool("allow-empty")
	skipGuard, _ := cmd.Flags().GetBool("skip-guard")
	maxAttempts, _ := cmd.Flags().GetInt("max-attempts")

	if message == "" {
		return fmt.Errorf("--message / -m is required\n  Fix: forge git commit -m \"feat: description\"")
	}

	// Validate conventional commit format (warn, do not block)
	if !conventionalCommitRE.MatchString(message) {
		fmt.Printf("%s  warning  %s commit message does not match conventional format%s\n",
			colorYellow, "format", colorReset)
		fmt.Printf("           expected: type(scope): description\n")
		fmt.Printf("           valid types: feat fix docs style refactor test chore perf ci build revert\n")
	}

	forgeRoot := resolveForgeRoot()

	// Run guard first unless skipped
	if !skipGuard {
		fmt.Println("[git commit] Running guard check...")
		guardArgs := []string{}
		if err := runGitGuard(gitGuardCmd, guardArgs); err != nil {
			return fmt.Errorf("[git commit] Guard check failed — fix issues before committing\n  Skip with: --skip-guard")
		}
	}

	backoff := 2 * time.Second
	var lastErr error

	for attempt := 1; attempt <= maxAttempts; attempt++ {
		fmt.Printf("[git commit] Attempt %d/%d\n", attempt, maxAttempts)

		// Remove stale index.lock before each attempt
		lockPath := filepath.Join(forgeRoot, ".git", "index.lock")
		if info, err := os.Stat(lockPath); err == nil {
			age := time.Since(info.ModTime())
			if age > staleLockThreshold {
				if rmErr := os.Remove(lockPath); rmErr == nil {
					fmt.Println("[git commit]   Removed stale index.lock")
				}
			} else {
				fmt.Printf("[git commit]   index.lock is fresh (%.0fs) — waiting...\n", age.Seconds())
			}
		}

		// Wait until lock is gone (up to backoff duration)
		if err := waitForLockClear(filepath.Join(forgeRoot, ".git", "index.lock"), backoff); err != nil {
			fmt.Printf("[git commit]   Lock still present after %v, retrying...\n", backoff)
			if attempt < maxAttempts {
				time.Sleep(backoff)
				backoff *= 2
				continue
			}
			updateCommitStats(forgeRoot, false, attempt, message)
			return fmt.Errorf("[git commit] ERROR: index.lock held after %d attempts\n  Fix: forge git clear-locks", maxAttempts)
		}

		// Stage: git add -u (or explicit files not supported via flag currently)
		stageArgs := []string{"add", "-u"}
		if _, stageErr := runGitCmdInDir(forgeRoot, stageArgs...); stageErr != nil {
			lastErr = fmt.Errorf("git add failed: %w", stageErr)
			continue
		}

		// Check if anything is staged
		diffOut, _ := runGitCmdInDir(forgeRoot, "diff", "--cached", "--name-only")
		if strings.TrimSpace(diffOut) == "" && !allowEmpty {
			updateCommitStats(forgeRoot, false, attempt, message)
			return fmt.Errorf("[git commit] ERROR: nothing to commit (no staged changes)\n  Fix: stage files first, or use --allow-empty")
		}

		// Build commit command
		commitArgs := []string{"commit", "-m", message}
		if allowEmpty {
			commitArgs = append(commitArgs, "--allow-empty")
		}

		out, commitErr := runGitCmdInDir(forgeRoot, commitArgs...)
		if commitErr == nil {
			// Success
			fmt.Printf("[git commit] Commit successful on attempt %d\n", attempt)
			updateCommitStats(forgeRoot, true, attempt, message)

			// Print the commit SHA
			shaOut, shaErr := runGitCmdInDir(forgeRoot, "log", "-1", "--oneline")
			if shaErr == nil {
				fmt.Println(strings.TrimSpace(shaOut))
			}
			_ = out
			return nil
		}

		// Failure — check if lock-related
		stderr := commitErr.Error()
		if strings.Contains(stderr, "index.lock") || strings.Contains(stderr, "Another git process") {
			fmt.Printf("[git commit]   Lock collision on attempt %d, retrying in %v...\n", attempt, backoff)
			lastErr = commitErr
			if attempt < maxAttempts {
				time.Sleep(backoff)
				backoff *= 2
				continue
			}
		} else {
			// Non-lock failure — do not retry
			updateCommitStats(forgeRoot, false, attempt, message)
			return fmt.Errorf("[git commit] ERROR: commit failed: %w\n  Fix: check git status and resolve any issues", commitErr)
		}
	}

	updateCommitStats(forgeRoot, false, maxAttempts, message)
	return fmt.Errorf("[git commit] ERROR: all %d attempts exhausted: %v\n  Fix: forge git clear-locks, then retry", maxAttempts, lastErr)
}

// waitForLockClear polls until .git/index.lock is gone or deadline passes.
// Returns nil if clear, error if still locked when deadline exceeded.
func waitForLockClear(lockPath string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if _, err := os.Stat(lockPath); os.IsNotExist(err) {
			return nil
		}
		time.Sleep(200 * time.Millisecond)
	}
	if _, err := os.Stat(lockPath); os.IsNotExist(err) {
		return nil
	}
	return fmt.Errorf("lock still present after %v", timeout)
}

// updateCommitStats reads, updates, and writes commit_stats.json.
func updateCommitStats(forgeRoot string, success bool, attempts int, message string) {
	statsPath := filepath.Join(forgeRoot, commitStatsFile)

	// Read existing stats
	var stats commitStats
	if data, err := os.ReadFile(statsPath); err == nil {
		_ = json.Unmarshal(data, &stats)
	}
	if stats.Version == "" {
		stats.Version = "1.0"
	}

	stats.TotalAttempts += attempts
	if success {
		stats.SuccessfulCommits++
		if attempts == 1 {
			stats.FirstAttemptSuccess++
		}
	} else {
		stats.FailedCommits++
	}
	if attempts > 1 {
		stats.TotalRetries += attempts - 1
	}

	total := stats.SuccessfulCommits + stats.FailedCommits
	if total > 0 {
		stats.SuccessRate = float64(stats.SuccessfulCommits) / float64(total) * 100
	}

	now := time.Now().UTC().Format(time.RFC3339)
	stats.LastUpdated = now

	msg := message
	if len(msg) > 80 {
		msg = msg[:80]
	}
	stats.History = append(stats.History, commitHistoryEntry{
		Timestamp: now,
		Success:   success,
		Attempts:  attempts,
		Message:   msg,
	})
	// Cap history at 50 entries
	if len(stats.History) > 50 {
		stats.History = stats.History[len(stats.History)-50:]
	}

	// Write stats
	if err := os.MkdirAll(filepath.Dir(statsPath), 0o755); err == nil {
		if data, err := json.MarshalIndent(stats, "", "  "); err == nil {
			_ = os.WriteFile(statsPath, data, 0o644)
		}
	}
}

// ---------------------------------------------------------------------------
// forge git mutex wait
// ---------------------------------------------------------------------------

var gitMutexWaitCmd = &cobra.Command{
	Use:   "mutex",
	Short: "Git mutex coordination subcommands",
	Long:  "Mutex subcommands for coordinating git operations across multiple agents.",
}

var gitMutexWaitSubCmd = &cobra.Command{
	Use:   "wait",
	Short: "Poll until .git/index.lock is gone",
	Long: `Poll the repository until .git/index.lock no longer exists, then exit 0.
Useful in scripts that want to wait for another agent to finish committing.

Exits 0 when the lock is clear.
Exits 1 on timeout.

Examples:
  forge git mutex wait
  forge git mutex wait --timeout 30`,
	RunE: runGitMutexWait,
}

func runGitMutexWait(cmd *cobra.Command, args []string) error {
	timeout, _ := cmd.Flags().GetInt("timeout")

	forgeRoot := resolveForgeRoot()
	lockPath := filepath.Join(forgeRoot, ".git", "index.lock")
	deadline := time.Now().Add(time.Duration(timeout) * time.Second)

	fmt.Printf("[git mutex wait] Waiting up to %ds for index.lock to clear...\n", timeout)

	for time.Now().Before(deadline) {
		if _, err := os.Stat(lockPath); os.IsNotExist(err) {
			fmt.Printf("[git mutex wait] %sLock is clear.%s\n", colorGreen, colorReset)
			return nil
		}
		time.Sleep(500 * time.Millisecond)
	}

	// Final check at deadline
	if _, err := os.Stat(lockPath); os.IsNotExist(err) {
		fmt.Printf("[git mutex wait] %sLock is clear.%s\n", colorGreen, colorReset)
		return nil
	}

	fmt.Fprintf(os.Stderr, "[git mutex wait] %sTimeout after %ds — index.lock still present.%s\n",
		colorRed, timeout, colorReset)
	fmt.Fprintf(os.Stderr, "  Fix: forge git clear-locks\n")
	os.Exit(1)
	return nil
}

// ---------------------------------------------------------------------------
// forge git clear-locks
// ---------------------------------------------------------------------------

var gitClearLocksCmd = &cobra.Command{
	Use:   "clear-locks",
	Short: "Remove stale .git/index.lock (>120s old)",
	Long: `Remove .git/index.lock if it is stale (older than 120 seconds).

If the lock file is fresh (< 120s old), a warning is printed but the file is
NOT removed, to avoid interfering with a live git process.

Exits 0 in all cases (including no lock found, or stale lock removed).
Exits 1 only if the lock is fresh (another process may be writing).

Examples:
  forge git clear-locks`,
	RunE: runGitClearLocks,
}

func runGitClearLocks(cmd *cobra.Command, args []string) error {
	forgeRoot := resolveForgeRoot()
	lockPath := filepath.Join(forgeRoot, ".git", "index.lock")

	info, err := os.Stat(lockPath)
	if os.IsNotExist(err) {
		fmt.Printf("%s  ✓  index.lock%s  not present — nothing to do\n", colorGreen, colorReset)
		return nil
	}
	if err != nil {
		return fmt.Errorf("[git clear-locks] ERROR: cannot stat index.lock: %w", err)
	}

	age := time.Since(info.ModTime())

	if age < staleLockThreshold {
		fmt.Printf("%s  ⚠  index.lock%s  is only %.0fs old (threshold %ds) — NOT removing\n",
			colorYellow, colorReset, age.Seconds(), int(staleLockThreshold.Seconds()))
		fmt.Println("  WARNING: A live git process may be writing. Wait or use `forge git mutex wait`.")
		// Exit 1 so callers know the lock is still present
		os.Exit(1)
		return nil
	}

	// Stale — remove it
	if rmErr := os.Remove(lockPath); rmErr != nil {
		return fmt.Errorf("[git clear-locks] ERROR: could not remove index.lock: %w\n  Fix: rm -f %s", rmErr, lockPath)
	}

	fmt.Printf("%s  ✓  index.lock%s  removed (was %.0fs old — stale)\n",
		colorGreen, colorReset, age.Seconds())
	return nil
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

// resolveForgeRoot returns FORGE_ROOT env var or current directory.
func resolveForgeRoot() string {
	if root := os.Getenv("FORGE_ROOT"); root != "" {
		return root
	}
	return "."
}

// runGitCmdInDir runs a git subcommand in the specified directory.
func runGitCmdInDir(dir string, gitArgs ...string) (string, error) {
	c := exec.Command("git", gitArgs...)
	c.Dir = dir
	var stdout, stderr bytes.Buffer
	c.Stdout = &stdout
	c.Stderr = &stderr
	err := c.Run()
	if err != nil {
		se := strings.TrimSpace(stderr.String())
		if se != "" {
			return "", fmt.Errorf("git %s: %w\n  stderr: %s", strings.Join(gitArgs, " "), err, se)
		}
		return "", fmt.Errorf("git %s: %w", strings.Join(gitArgs, " "), err)
	}
	return stdout.String(), nil
}

func init() {
	// Wire verbs into the noun
	gitCmd.AddCommand(gitPushCmd)
	gitCmd.AddCommand(gitGuardCmd)
	gitCmd.AddCommand(gitCommitCmd)
	gitCmd.AddCommand(gitMutexWaitCmd)
	gitCmd.AddCommand(gitClearLocksCmd)

	// Wire mutex subcommand
	gitMutexWaitCmd.AddCommand(gitMutexWaitSubCmd)

	// gitPushCmd flags
	gitPushCmd.Flags().Int("retry", 5, "Maximum push attempts before giving up")
	gitPushCmd.Flags().String("branch", "", "Branch to push (default: current branch from git branch --show-current)")
	gitPushCmd.Flags().String("remote", "origin", "Git remote name")
	gitPushCmd.Flags().String("target", "main", "Target branch to merge and push into")

	// gitGuardCmd flags
	gitGuardCmd.Flags().Bool("strict", false, "Also fail if not on main branch")

	// gitCommitCmd flags
	gitCommitCmd.Flags().StringP("message", "m", "", "Commit message (required, conventional commit format recommended)")
	gitCommitCmd.Flags().Bool("allow-empty", false, "Allow commit with no staged changes")
	gitCommitCmd.Flags().Bool("skip-guard", false, "Skip forge git guard check before committing")
	gitCommitCmd.Flags().Int("max-attempts", 3, "Maximum commit attempts on lock collision")
	_ = gitCommitCmd.MarkFlagRequired("message")

	// gitMutexWaitSubCmd flags
	gitMutexWaitSubCmd.Flags().Int("timeout", 60, "Seconds to wait for lock to clear (default 60)")
}
