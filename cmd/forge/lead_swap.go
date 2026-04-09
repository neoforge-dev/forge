package main

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// leadSwapCmd swaps the lead orchestrator to a different harness.
var leadSwapCmd = &cobra.Command{
	Use:   "swap",
	Short: "Hot-swap the lead orchestrator to a different harness",
	Long: `Swap the lead agent in the forge:${HOSTNAME} tmux window to a different
harness (e.g., from claude to codex, or from kimi to claude).

Pre-flight checks (hard gates):
  1. Working tree must be clean (no uncommitted changes)
  2. Target harness binary must be installed

The swap sequence:
  1. Validates pre-flight checks
  2. Writes swap breadcrumb to .forge/state/${HOSTNAME}/swap-context.md
  3. Sends Ctrl-C to current agent
  4. Waits for shell prompt
  5. Starts new harness with canonical command
  6. Updates FORGE_HARNESS_TYPE
  7. Sends /continue after boot

Examples:
  forge lead swap --to codex
  forge lead swap --to claude
  forge lead swap --to kimi --dry-run`,
	RunE: runLeadSwap,
}

func init() {
	leadCmd.AddCommand(leadSwapCmd)

	leadSwapCmd.Flags().String("to", "", "Target harness name (required)")
	leadSwapCmd.MarkFlagRequired("to") //nolint:errcheck
	leadSwapCmd.Flags().Bool("dry-run", false, "Validate preconditions without executing swap")
}

// harnessStartCmd returns the canonical start command for a harness.
func harnessStartCmd(name string) string {
	switch name {
	case "claude":
		return "claude --dangerously-skip-permissions"
	case "codex":
		return "codex --dangerously-bypass-approvals-and-sandbox"
	case "cursor":
		return "cursor-agent -f"
	case "amp":
		return "amp --dangerously-allow-all"
	case "kimi", "kimi-2":
		return "kimi -y"
	case "gemini", "gemini-2":
		return "gemini -y"
	case "pi":
		return "pi"
	case "opencode":
		return "opencode"
	case "kilo":
		return "kilo"
	case "glm":
		return "glm"
	case "minimax":
		return "minimax"
	default:
		return ""
	}
}

// harnessType maps agent name to harness type identifier.
func harnessType(name string) string {
	switch name {
	case "claude":
		return "claude-code"
	case "codex":
		return "codex"
	case "cursor":
		return "cursor"
	case "amp":
		return "amp"
	case "kimi", "kimi-2":
		return "kimi"
	case "gemini", "gemini-2":
		return "gemini-cli"
	case "pi":
		return "pi"
	case "opencode":
		return "opencode"
	case "kilo":
		return "kilo"
	case "glm":
		return "glm"
	case "minimax":
		return "minimax"
	default:
		return "unknown"
	}
}

// bootDelay returns seconds to wait for a harness to boot.
func bootDelay(name string) int {
	switch name {
	case "claude", "codex", "amp":
		return 5
	case "kimi", "gemini":
		return 10
	default:
		return 15
	}
}

func runLeadSwap(cmd *cobra.Command, args []string) error {
	target, _ := cmd.Flags().GetString("to")
	dryRun, _ := cmd.Flags().GetBool("dry-run")

	hostname, err := os.Hostname()
	if err != nil {
		return fmt.Errorf("cannot detect hostname: %w\nRecovery: set HOSTNAME env var", err)
	}
	// Use short hostname (strip domain)
	if idx := strings.Index(hostname, "."); idx > 0 {
		hostname = hostname[:idx]
	}

	tmuxTarget := fmt.Sprintf("forge:%s", hostname)

	// ── Pre-flight 1: target harness binary exists ──────────────────
	startCmd := harnessStartCmd(target)
	if startCmd == "" {
		return fmt.Errorf("unknown harness %q\nKnown: claude, codex, cursor, amp, kimi, gemini, pi, opencode, kilo, glm, minimax", target)
	}

	binaryName := strings.Fields(startCmd)[0]
	if _, err := exec.LookPath(binaryName); err != nil {
		return fmt.Errorf("harness binary %q not found on PATH\nRecovery: install %s first, then retry", binaryName, target)
	}

	// ── Pre-flight 2: clean working tree ────────────────────────────
	gitStatus, err := exec.Command("git", "status", "--porcelain").Output()
	if err != nil {
		return fmt.Errorf("git status failed: %w\nRecovery: ensure you're in a git repo", err)
	}
	if len(strings.TrimSpace(string(gitStatus))) > 0 {
		return fmt.Errorf("working tree is dirty — uncommitted changes would be lost\nRecovery: run /handoff first, then commit and push before swapping\n\nDirty files:\n%s", string(gitStatus))
	}

	// ── Pre-flight 3: tmux session exists ───────────────────────────
	if err := exec.Command("tmux", "has-session", "-t", "forge").Run(); err != nil {
		return fmt.Errorf("tmux session 'forge' not found\nRecovery: run 'forge up' to create the session first (target: %s)", target)
	}

	fmt.Printf("Pre-flight checks passed:\n")
	fmt.Printf("  ✓ Binary %q found\n", binaryName)
	fmt.Printf("  ✓ Working tree clean\n")
	fmt.Printf("  ✓ tmux session forge exists\n")
	fmt.Printf("  Target: %s → %s (harness: %s)\n", tmuxTarget, target, harnessType(target))

	if dryRun {
		fmt.Println("\n--dry-run: would swap but stopping here.")
		return nil
	}

	// ── Write swap breadcrumb ───────────────────────────────────────
	stateDir := fmt.Sprintf(".forge/state/%s", hostname)
	os.MkdirAll(stateDir, 0o755) //nolint:errcheck
	breadcrumb := fmt.Sprintf("# Swap Context\n\nSwapped to **%s** at %s\nPrevious harness: check git log\nResume with: /continue\n",
		target, time.Now().Format("2006-01-02 15:04:05"))
	os.WriteFile(fmt.Sprintf("%s/swap-context.md", stateDir), []byte(breadcrumb), 0o644) //nolint:errcheck

	// ── Kill current agent ──────────────────────────────────────────
	fmt.Printf("\nSending Ctrl-C to current agent in %s...\n", tmuxTarget)
	exec.Command("tmux", "send-keys", "-t", tmuxTarget, "C-c", "").Run() //nolint:errcheck
	time.Sleep(2 * time.Second)

	// Send 'exit' in case Ctrl-C wasn't enough (some agents need explicit exit)
	exec.Command("tmux", "send-keys", "-t", tmuxTarget, "exit", "Enter").Run() //nolint:errcheck
	time.Sleep(1 * time.Second)

	// ── Wait for shell prompt (readiness poll) ──────────────────────
	fmt.Print("Waiting for shell prompt")
	ready := false
	for i := 0; i < 30; i++ {
		out, err := exec.Command("tmux", "display-message", "-t", tmuxTarget, "-p", "#{pane_current_command}").Output()
		if err == nil {
			paneCmd := strings.TrimSpace(string(out))
			if paneCmd == "bash" || paneCmd == "zsh" || paneCmd == "" {
				ready = true
				break
			}
		}
		fmt.Print(".")
		time.Sleep(1 * time.Second)
	}
	fmt.Println()

	if !ready {
		return fmt.Errorf("timed out waiting for shell prompt in %s\nRecovery: manually kill the agent in tmux, then retry", tmuxTarget)
	}

	// ── Update env vars ─────────────────────────────────────────────
	newHarness := harnessType(target)
	envExport := fmt.Sprintf("export FORGE_HARNESS_TYPE=%s", newHarness)
	exec.Command("tmux", "send-keys", "-t", tmuxTarget, envExport, "Enter").Run() //nolint:errcheck
	exec.Command("tmux", "set-environment", "-t", "forge", "FORGE_HARNESS_TYPE", newHarness).Run() //nolint:errcheck
	time.Sleep(500 * time.Millisecond)

	// Persist to file
	os.WriteFile(fmt.Sprintf("%s/harness.type", stateDir), []byte(newHarness+"\n"), 0o644) //nolint:errcheck

	// ── Start new harness ───────────────────────────────────────────
	fmt.Printf("Starting %s in %s...\n", target, tmuxTarget)
	exec.Command("tmux", "send-keys", "-t", tmuxTarget, "-l", startCmd).Run() //nolint:errcheck
	time.Sleep(100 * time.Millisecond)
	exec.Command("tmux", "send-keys", "-t", tmuxTarget, "Enter").Run() //nolint:errcheck

	// ── Send /continue after boot delay ─────────────────────────────
	delay := bootDelay(target)
	fmt.Printf("Waiting %ds for %s to boot...\n", delay, target)

	// Use readiness poll: wait for agent to be running (not at shell)
	agentReady := false
	for i := 0; i < delay+15; i++ {
		out, err := exec.Command("tmux", "display-message", "-t", tmuxTarget, "-p", "#{pane_current_command}").Output()
		if err == nil {
			paneCmd := strings.TrimSpace(string(out))
			if paneCmd != "bash" && paneCmd != "zsh" && paneCmd != "" {
				agentReady = true
				break
			}
		}
		time.Sleep(1 * time.Second)
	}

	if !agentReady {
		return fmt.Errorf("new harness %s did not start within %ds\nRecovery: check tmux window forge:%s manually", target, delay+15, hostname)
	}

	// Determine onboarding message
	onboardMsg := "/continue"
	switch target {
	case "claude", "codex", "amp":
		// These support /continue skill
		onboardMsg = "/continue"
	default:
		// Explicit onboarding for agents without /continue
		onboardMsg = fmt.Sprintf("You are the lead orchestrator on %s. Read: 1) CLAUDE.md 2) docs/PROMPT-%s.md. Then run 'forge status' and 'forge task list'. Delegate tasks, do not write code.", hostname, hostname)
	}

	time.Sleep(2 * time.Second) // Extra settling time
	exec.Command("tmux", "send-keys", "-t", tmuxTarget, "-l", onboardMsg).Run() //nolint:errcheck
	time.Sleep(100 * time.Millisecond)
	exec.Command("tmux", "send-keys", "-t", tmuxTarget, "Enter").Run() //nolint:errcheck

	fmt.Printf("\n✓ Lead swapped to %s (harness: %s) in %s\n", target, newHarness, tmuxTarget)
	fmt.Printf("  Onboarding sent: %s\n", onboardMsg[:min(60, len(onboardMsg))])
	return nil
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
