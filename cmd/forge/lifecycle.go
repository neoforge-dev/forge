package main

import (
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"time"

	"github.com/spf13/cobra"
)

// upCmd: forge up [--skip-agents] [--tmux forge]
var upCmd = &cobra.Command{
	Use:   "up",
	Short: "Start the FORGE daemon and optionally launch agent windows",
	Long: `Start the forge-v3 daemon and poll until it is healthy.

When --tmux is given, a tmux session is created (or joined) and one window
per agent in .forge/forge.yaml is opened.

Examples:
  forge up
  forge up --skip-agents
  forge up --tmux forge`,
	RunE: func(cmd *cobra.Command, args []string) error {
		skipAgents, _ := cmd.Flags().GetBool("skip-agents")
		tmuxSession, _ := cmd.Flags().GetString("tmux")

		// Step 1: Start daemon (reuse daemonStartCmd logic)
		fmt.Println("[up] Starting daemon...")
		if err := daemonStartCmd.RunE(cmd, args); err != nil {
			return fmt.Errorf("[up] daemon start failed: %w\n\nRecovery: check that forge-v3 binary exists (run: go build ./cmd/forge-v3/)", err)
		}

		// Step 2: Poll :8081/health until OK (max 10s)
		fmt.Print("[up] Waiting for daemon health ")
		apiURL := resolveUpAPIURL()
		healthy := false
		for i := 0; i < 100; i++ {
			time.Sleep(100 * time.Millisecond)
			if checkDaemonHealth(apiURL) {
				healthy = true
				break
			}
			if i%10 == 9 {
				fmt.Print(".")
			}
		}
		fmt.Println()

		if !healthy {
			return fmt.Errorf("[up] daemon did not become healthy within 10s\n\nRecovery: run 'forge daemon logs' to see startup errors")
		}
		fmt.Printf("[up] Daemon healthy at %s\n", apiURL)

		// Step 3: Optionally create tmux windows for agents
		if !skipAgents && tmuxSession != "" {
			fmt.Printf("[up] Creating agent windows in tmux session '%s'...\n", tmuxSession)
			if err := createAgentWindows(tmuxSession); err != nil {
				// Non-fatal: agents might already be running
				fmt.Fprintf(os.Stderr, "[up] Warning: tmux setup error: %v\n", err)
			}
		}

		// Step 4: Summary
		fmt.Println()
		fmt.Printf("  Daemon URL : %s\n", apiURL)
		fmt.Printf("  Status     : forge daemon status\n")
		fmt.Printf("  Monitor    : forge monitor\n")
		if tmuxSession != "" && !skipAgents {
			fmt.Printf("  Agents     : tmux attach -t %s\n", tmuxSession)
		}
		return nil
	},
}

// downCmd: forge down [--force]
var downCmd = &cobra.Command{
	Use:   "down",
	Short: "Stop the FORGE daemon and gracefully shut down agent windows",
	Long: `Stop the running forge-v3 daemon.

When a forge tmux session exists, an 'exit' command is sent to each agent
window before the daemon is stopped.

Examples:
  forge down
  forge down --force`,
	RunE: func(cmd *cobra.Command, args []string) error {
		force, _ := cmd.Flags().GetBool("force")

		// Step 1: Gracefully shut down tmux agent windows if they exist
		tmuxSession := "forge"
		if tmuxExists(tmuxSession) {
			fmt.Printf("[down] Sending graceful shutdown to tmux session '%s'...\n", tmuxSession)
			shutdownAgentWindows(tmuxSession)
		}

		// Step 2: Stop daemon (pass force flag through)
		fmt.Println("[down] Stopping daemon...")
		// Set flag on daemonStopCmd if force is requested
		if force {
			daemonStopCmd.Flags().Set("force", "true") //nolint:errcheck
		}
		if err := daemonStopCmd.RunE(cmd, args); err != nil {
			return fmt.Errorf("[down] daemon stop failed: %w\n\nRecovery: run 'forge daemon stop --force' or kill the process manually with: kill $(lsof -ti :8081)", err)
		}

		fmt.Println("[down] Done.")
		return nil
	},
}

// resolveUpAPIURL returns the API URL for health polling.
func resolveUpAPIURL() string {
	if v := os.Getenv("FORGE_API_URL"); v != "" {
		return v
	}
	return "http://localhost:8081"
}

// checkDaemonHealth returns true if the /health endpoint responds with 200.
func checkDaemonHealth(apiURL string) bool {
	c := &http.Client{Timeout: 500 * time.Millisecond}
	resp, err := c.Get(apiURL + "/health")
	if err != nil {
		return false
	}
	resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}

// tmuxExists returns true if the named tmux session exists.
func tmuxExists(session string) bool {
	cmd := exec.Command("tmux", "has-session", "-t", session)
	return cmd.Run() == nil
}

// createAgentWindows creates a window per agent in the tmux session.
// It reads agent names from .forge/forge.yaml (agents key) and creates
// a window for each one. If the file doesn't exist it uses a default set.
func createAgentWindows(session string) error {
	agents := readForgeYAMLAgents()

	// Ensure the tmux session exists
	if !tmuxExists(session) {
		createCmd := exec.Command("tmux", "new-session", "-d", "-s", session)
		if err := createCmd.Run(); err != nil {
			return fmt.Errorf("failed to create tmux session '%s': %w", session, err)
		}
	}

	for _, agent := range agents {
		// Create window named after the agent (ignore error if already exists)
		exec.Command("tmux", "new-window", "-t", session, "-n", agent).Run() //nolint:errcheck
	}

	return nil
}

// readForgeYAMLAgents reads the agents list from .forge/forge.yaml.
// Returns a default list if the file is absent or unparseable.
func readForgeYAMLAgents() []string {
	defaults := []string{"kimi", "glm", "gemini", "minimax", "pi"}

	// Try to find .forge/forge.yaml
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}
	path := forgeRoot + "/.forge/forge.yaml"
	data, err := os.ReadFile(path)
	if err != nil {
		return defaults
	}

	// Simple YAML parse: look for "agents:" key with list entries
	var agents []string
	inAgents := false
	for _, line := range splitLines(string(data)) {
		trimmed := trimSpaces(line)
		if trimmed == "agents:" {
			inAgents = true
			continue
		}
		if inAgents {
			if len(trimmed) > 0 && trimmed[0] != '-' && trimmed[0] != ' ' {
				break // end of agents block
			}
			if len(trimmed) > 2 && trimmed[0] == '-' {
				name := trimSpaces(trimmed[1:])
				if name != "" {
					agents = append(agents, name)
				}
			}
		}
	}

	if len(agents) == 0 {
		return defaults
	}
	return agents
}

// shutdownAgentWindows sends an 'exit' command to all non-first windows in
// the given tmux session.
func shutdownAgentWindows(session string) {
	out, err := exec.Command("tmux", "list-windows", "-t", session, "-F", "#I").Output()
	if err != nil {
		return
	}
	for _, idStr := range splitLines(string(out)) {
		idStr = trimSpaces(idStr)
		if idStr == "" || idStr == "0" {
			continue
		}
		target := session + ":" + idStr
		exec.Command("tmux", "send-keys", "-t", target, "exit", "Enter").Run() //nolint:errcheck
	}
}

// splitLines splits a string by newline characters.
func splitLines(s string) []string {
	var lines []string
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			lines = append(lines, s[start:i])
			start = i + 1
		}
	}
	if start < len(s) {
		lines = append(lines, s[start:])
	}
	return lines
}

// trimSpaces removes leading and trailing space/tab characters.
func trimSpaces(s string) string {
	start := 0
	for start < len(s) && (s[start] == ' ' || s[start] == '\t') {
		start++
	}
	end := len(s)
	for end > start && (s[end-1] == ' ' || s[end-1] == '\t' || s[end-1] == '\r') {
		end--
	}
	return s[start:end]
}

func init() {
	upCmd.Hidden = true
	upCmd.Deprecated = "use 'forge daemon start' instead"
	downCmd.Hidden = true
	downCmd.Deprecated = "use 'forge daemon stop' instead"
	upCmd.Flags().Bool("skip-agents", false, "Skip launching agent tmux windows")
	upCmd.Flags().String("tmux", "", "tmux session name to create agent windows in (e.g. forge)")

	downCmd.Flags().Bool("force", false, "Force-kill daemon (SIGKILL)")

	rootCmd.AddCommand(upCmd)
	rootCmd.AddCommand(downCmd)
}
