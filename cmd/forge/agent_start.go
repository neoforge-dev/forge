package main

import (
	"fmt"
	"os/exec"
	"strings"

	"github.com/spf13/cobra"
)

// agentStartSubCmd: forge agent start <name>
//
// This is the "agent start" surface — a sibling to "fleet spawn" that is
// reachable via the agent noun. It shares the agentStartCommands map and
// helpers (tmuxHasSession, tmuxWindowExists, validAgentName) defined in
// workflow_fleet.go.
var agentStartSubCmd = &cobra.Command{
	Use:   "start <name>",
	Short: "Start an agent in a tmux window",
	Long: `Create a tmux window in the forge session and start the specified agent CLI.

The agent will be started with the correct environment variables and an optional
background heartbeat loop. If the window already exists, use --restart to kill
and recreate it.

Known agents: kimi, minimax, gemini, pi, opencode, kilo, glm, codex

Examples:
  forge agent start kimi
  forge agent start gemini --restart
  forge agent start opencode --session mysession
  forge agent start kilo --no-heartbeat
  forge agent start glm --dry-run`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		agentName := args[0]

		restart, _ := cmd.Flags().GetBool("restart")
		noHeartbeat, _ := cmd.Flags().GetBool("no-heartbeat")
		session, _ := cmd.Flags().GetString("session")
		dryRun, _ := cmd.Flags().GetBool("dry-run")

		return runAgentStartSubCmd(agentName, session, restart, noHeartbeat, dryRun)
	},
}

// runAgentStartSubCmd is the testable core of agentStartSubCmd.
func runAgentStartSubCmd(agentName, session string, restart, noHeartbeat, dryRun bool) error {
	// 1. Validate agent name — must be in the canonical map.
	cliCmd, ok := agentStartCommands[agentName]
	if !ok {
		known := make([]string, 0, len(agentStartCommands))
		for k := range agentStartCommands {
			known = append(known, k)
		}
		return fmt.Errorf(
			"unknown agent %q — known agents: %s\n  Add a new entry to agentStartCommands in workflow_fleet.go",
			agentName, strings.Join(known, ", "),
		)
	}

	windowTarget := fmt.Sprintf("%s:%s", session, agentName)

	if dryRun {
		fmt.Printf("[dry-run] Would start agent %q in tmux session %q\n", agentName, session)
		fmt.Printf("[dry-run] tmux window: %s\n", windowTarget)
		fmt.Printf("[dry-run] Agent command: %s\n", cliCmd)
		fmt.Printf("[dry-run] Env: FORGE_AGENT_TYPE=fleet FORGE_AGENT_NAME=%s FORGE_API_URL=http://localhost:8081\n", agentName)
		if !noHeartbeat {
			fmt.Printf("[dry-run] Heartbeat: while true; do forge heartbeat send --agent %s --quiet; sleep 120; done &\n", agentName)
		}
		return nil
	}

	// 2. Verify tmux is available.
	if _, err := exec.LookPath("tmux"); err != nil {
		return fmt.Errorf(
			"tmux not found: %w\n  Install tmux: apt install tmux (or brew install tmux)",
			err,
		)
	}

	// 3. Ensure the tmux session exists; create it if not.
	if !tmuxHasSession(session) {
		createSession := exec.Command("tmux", "new-session", "-d", "-s", session)
		if out, err := createSession.CombinedOutput(); err != nil {
			return fmt.Errorf(
				"failed to create tmux session %q: %w\n  Output: %s\n  Try manually: tmux new-session -d -s %s",
				session, err, strings.TrimSpace(string(out)), session,
			)
		}
	}

	// 4. Check if a window named <agent> already exists.
	if tmuxWindowExists(session, agentName) {
		if !restart {
			return fmt.Errorf(
				"agent %q already has a tmux window (%s).\n  Use --restart to kill and recreate it, or attach: tmux attach -t %s",
				agentName, windowTarget, windowTarget,
			)
		}
		// Kill the existing window so we can recreate it cleanly.
		killWindow := exec.Command("tmux", "kill-window", "-t", windowTarget)
		if out, err := killWindow.CombinedOutput(); err != nil {
			return fmt.Errorf(
				"failed to kill tmux window %s: %w\n  Output: %s",
				windowTarget, err, strings.TrimSpace(string(out)),
			)
		}
	}

	// 5. Create the new tmux window.
	newWindow := exec.Command("tmux", "new-window", "-t", session, "-n", agentName)
	if out, err := newWindow.CombinedOutput(); err != nil {
		return fmt.Errorf(
			"failed to create tmux window %s: %w\n  Output: %s",
			windowTarget, err, strings.TrimSpace(string(out)),
		)
	}

	// 6. Set env vars then launch the agent CLI.
	// A single export-and-exec line keeps the shell environment correct.
	envPrefix := fmt.Sprintf(
		"export FORGE_AGENT_TYPE=fleet FORGE_AGENT_NAME=%s FORGE_API_URL=http://localhost:8081",
		agentName,
	)
	launchLine := fmt.Sprintf("%s && %s", envPrefix, cliCmd)

	if err := tmuxSend(windowTarget, launchLine); err != nil {
		return fmt.Errorf(
			"failed to send launch command to %s: %w\n  Attach manually: tmux attach -t %s",
			windowTarget, err, windowTarget,
		)
	}

	// 7. Optionally start a background heartbeat loop.
	if !noHeartbeat {
		heartbeatLine := fmt.Sprintf(
			"while true; do forge heartbeat send --agent %s --quiet; sleep 120; done &",
			agentName,
		)
		// Best-effort: heartbeat failure should not abort the agent start.
		_ = tmuxSend(windowTarget, heartbeatLine)
	}

	fmt.Printf("Started agent %q in tmux window %s\n", agentName, windowTarget)
	return nil
}

func init() {
	agentCmd.AddCommand(agentStartSubCmd)

	agentStartSubCmd.Flags().Bool("restart", false, "Kill existing window and recreate it")
	agentStartSubCmd.Flags().Bool("no-heartbeat", false, "Skip starting the background heartbeat loop")
	agentStartSubCmd.Flags().String("session", "forge", "Tmux session name to use")
	agentStartSubCmd.Flags().Bool("dry-run", false, "Show what would be done without executing")
}
