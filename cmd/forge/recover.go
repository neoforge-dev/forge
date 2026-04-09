package main

import (
	"fmt"
	"os"
	"time"

	"github.com/spf13/cobra"
)

// recoverCmd cleans up common git blockers that affect all agents.
// This replaces the Claude-only fleet_error_recovery.sh hook with a universal CLI command.
var recoverCmd = &cobra.Command{
	Use:   "recover",
	Short: "Clean up common blockers (stale locks, rebase state)",
	Long: `Detect and clean up common git blockers that prevent agents from working.

This command is the universal equivalent of the Claude-only fleet_error_recovery.sh hook.
All agent types (kimi, gemini, pi, glm, minimax, codex, claude) can use it.

Actions:
  - Remove stale .git/index.lock (0-byte or older than 60 seconds)
  - Detect rebase/merge/bisect states
  - Report any blockers that need manual resolution

Examples:
  forge recover           # Auto-fix what's safe, report the rest
  forge recover --dry-run # Show what would be fixed without changing anything`,
	RunE: runRecover,
}

func init() {
	recoverCmd.Flags().Bool("dry-run", false, "Show what would be fixed without making changes")
}

func runRecover(cmd *cobra.Command, args []string) error {
	dryRun, _ := cmd.Flags().GetBool("dry-run")
	fixed := 0
	blocked := 0

	// Check index.lock
	if info, err := os.Stat(".git/index.lock"); err == nil {
		age := time.Since(info.ModTime())
		if info.Size() == 0 || age > 60*time.Second {
			if dryRun {
				fmt.Printf("[dry-run] Would remove stale .git/index.lock (size=%d, age=%v)\n", info.Size(), age.Round(time.Second))
			} else {
				if err := os.Remove(".git/index.lock"); err != nil {
					fmt.Fprintf(os.Stderr, "Failed to remove .git/index.lock: %v\n", err)
					blocked++
				} else {
					fmt.Printf("Removed stale .git/index.lock (size=%d, age=%v)\n", info.Size(), age.Round(time.Second))
					fixed++
				}
			}
		} else {
			fmt.Printf("Active .git/index.lock (age=%v) — git operation may be in progress\n", age.Round(time.Second))
			blocked++
		}
	}

	// Check rebase/merge/bisect states
	gitStates := map[string]string{
		".git/rebase-merge": "git rebase --abort",
		".git/rebase-apply": "git rebase --abort",
		".git/MERGE_HEAD":   "git merge --abort",
		".git/BISECT_START": "git bisect reset",
	}
	for path, fix := range gitStates {
		if _, err := os.Stat(path); err == nil {
			fmt.Printf("Blocker: %s exists — run: %s\n", path, fix)
			blocked++
		}
	}

	// Summary
	if fixed == 0 && blocked == 0 {
		fmt.Println("No blockers found — workspace is clean")
	} else if blocked > 0 {
		fmt.Printf("\n%d blocker(s) need manual resolution\n", blocked)
	}
	if fixed > 0 {
		fmt.Printf("%d issue(s) auto-fixed\n", fixed)
	}

	return nil
}
