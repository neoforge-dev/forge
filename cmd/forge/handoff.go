package main

import (
	cryptoRand "crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// handoffCmd represents the handoff noun
var handoffCmd = &cobra.Command{
	Use:   "handoff",
	Short: "Create and manage agent handoff documents",
	Long: `Create and manage agent handoff documents for session continuity.

Examples:
  forge handoff clean                    # Create handoff with auto-detection
  forge handoff clean --skip-commit      # Skip git checkpoint
  forge handoff clean --agent kimi       # Specify agent name
  forge handoff read <id>                # Read handoff document`,
}

// HandoffDoc represents a handoff document
type HandoffDoc struct {
	ID          string                 `json:"id"`
	FromAgent   string                 `json:"from_agent"`
	ToAgent     string                 `json:"to_agent,omitempty"`
	Task        string                 `json:"task"`
	Status      string                 `json:"status"`
	Priority    string                 `json:"priority"`
	Description string                 `json:"description,omitempty"`
	Context     map[string]interface{} `json:"context,omitempty"`
	CreatedAt   string                 `json:"created_at"`
}

// handoffCleanCmd: forge handoff clean
var handoffCleanCmd = &cobra.Command{
	Use:   "clean",
	Short: "Create a clean handoff with session state",
	Long: `Automate the complete handoff workflow.

Creates a handoff document, saves session state to memories, and
optionally commits a git checkpoint — all in one step.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		agent, _ := cmd.Flags().GetString("agent")
		reason, _ := cmd.Flags().GetString("reason")
		skipCommit, _ := cmd.Flags().GetBool("skip-commit")
		outputJSON, _ := cmd.Flags().GetBool("json")

		// Detect forge root
		forgeRoot := findForgeRoot()
		if forgeRoot == "" {
			return fmt.Errorf("not in FORGE repository")
		}

		// Get agent name
		agentName := agent
		if agentName == "" {
			agentName = os.Getenv("FORGE_AGENT")
		}
		if agentName == "" {
			agentName = os.Getenv("FORGE_AGENT_NAME")
		}
		if agentName == "" {
			agentName = "unknown"
		}

		// Generate handoff ID and timestamp
		handoffID := generateHandoffID()
		createdAt := time.Now().UTC().Format(time.RFC3339)

		// Get git info
		branch := getGitBranch(forgeRoot)
		gitLog := getGitLog(forgeRoot, 15)
		modifiedFiles := getModifiedFiles(forgeRoot)

		// Build task summary from recent commits
		taskSummary := fmt.Sprintf("Clean handoff — %s", reason)
		if len(gitLog) > 0 {
			if len(gitLog) == 1 {
				taskSummary = gitLog[0]
			} else {
				taskSummary = fmt.Sprintf("%s (+%d more)", gitLog[0], len(gitLog)-1)
			}
		}

		// Create handoff document
		handoff := HandoffDoc{
			ID:        handoffID,
			FromAgent: agentName,
			Task:      taskSummary,
			Status:    "pending",
			Priority:  "medium",
			Description: reason,
			Context: map[string]interface{}{
				"branch":         branch,
				"modified_files": modifiedFiles,
				"created_at":     createdAt,
			},
			CreatedAt: createdAt,
		}

		// Save handoff document
		handoffPath, err := saveHandoff(forgeRoot, &handoff)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Warning: could not save handoff: %v\n", err)
		}

		// Save session summary to memories
		memoriesPath, err := saveSessionSummary(forgeRoot, &handoff, gitLog, modifiedFiles)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Warning: could not save session summary: %v\n", err)
		}

		// Git checkpoint
		var commitSHA string
		if !skipCommit {
			sha, err := gitCheckpoint(forgeRoot, handoffID)
			if err != nil {
				fmt.Fprintf(os.Stderr, "Warning: git checkpoint failed: %v\n", err)
			} else {
				commitSHA = sha
			}
		}

		// Clear fleet agent contexts
		skipFleetClear, _ := cmd.Flags().GetBool("skip-fleet-clear")
		var clearedAgents []string
		if !skipFleetClear {
			clearedAgents = clearFleetAgents()
		}

		// Output
		if outputJSON {
			result := map[string]interface{}{
				"handoff_id":     handoffID,
				"agent":          agentName,
				"reason":         reason,
				"branch":         branch,
				"handoff_path":   handoffPath,
				"memories_path":  memoriesPath,
				"commit_sha":     commitSHA,
				"cleared_agents": clearedAgents,
			}
			encoder := json.NewEncoder(os.Stdout)
			encoder.SetIndent("", "  ")
			return encoder.Encode(result)
		}

		// Human-readable output
		fmt.Println()
		fmt.Println("=== Clean Handoff Complete ===")
		fmt.Println()
		fmt.Printf("Handoff ID: %s\n", handoffID)
		fmt.Printf("Agent:      %s\n", agentName)
		fmt.Printf("Reason:     %s\n", reason)
		fmt.Println()

		if handoffPath != "" {
			rel, _ := filepath.Rel(forgeRoot, handoffPath)
			fmt.Printf("Saved: %s\n", rel)
		}
		if memoriesPath != "" {
			rel, _ := filepath.Rel(forgeRoot, memoriesPath)
			fmt.Printf("        %s\n", rel)
		}
		fmt.Println()

		if commitSHA != "" {
			fmt.Printf("Git checkpoint: %s checkpoint: handoff %s\n", commitSHA, handoffID)
		} else if skipCommit {
			fmt.Println("Git checkpoint: skipped (--skip-commit)")
		}
		fmt.Println()

		if len(clearedAgents) > 0 {
			fmt.Printf("Fleet cleared:  %s\n", strings.Join(clearedAgents, ", "))
		} else if skipFleetClear {
			fmt.Println("Fleet cleared:  skipped (--skip-fleet-clear)")
		} else {
			fmt.Println("Fleet cleared:  no agents found")
		}
		fmt.Println()

		fmt.Printf("Resume with: forge handoff read %s\n", handoffID)
		return nil
	},
}

// handoffReadCmd: forge handoff read <id>
var handoffReadCmd = &cobra.Command{
	Use:   "read <handoff-id>",
	Short: "Read a handoff document",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		handoffID := args[0]
		outputJSON, _ := cmd.Flags().GetBool("json")

		forgeRoot := findForgeRoot()
		if forgeRoot == "" {
			return fmt.Errorf("not in FORGE repository")
		}

		handoff, err := loadHandoff(forgeRoot, handoffID)
		if err != nil {
			return fmt.Errorf("handoff not found: %s", handoffID)
		}

		if outputJSON {
			encoder := json.NewEncoder(os.Stdout)
			encoder.SetIndent("", "  ")
			return encoder.Encode(handoff)
		}

		// Human-readable output
		fmt.Printf("Handoff: %s\n", handoff.ID)
		fmt.Printf("From:    %s\n", handoff.FromAgent)
		fmt.Printf("Status:  %s\n", handoff.Status)
		fmt.Printf("Task:    %s\n", handoff.Task)
		fmt.Printf("Created: %s\n", handoff.CreatedAt)
		if handoff.Description != "" {
			fmt.Printf("Reason:  %s\n", handoff.Description)
		}
		if handoff.Context != nil {
			if branch, ok := handoff.Context["branch"]; ok {
				fmt.Printf("Branch:  %v\n", branch)
			}
		}
		return nil
	},
}

func init() {
	// handoff clean flags
	handoffCleanCmd.Flags().StringP("agent", "a", "", "Agent name (default: $FORGE_AGENT)")
	handoffCleanCmd.Flags().StringP("reason", "r", "context", "Handoff reason (context|complete|blocked|switch)")
	handoffCleanCmd.Flags().Bool("skip-commit", false, "Skip git checkpoint commit")
	handoffCleanCmd.Flags().Bool("skip-fleet-clear", false, "Skip sending /clear to fleet agents")
	handoffCleanCmd.Flags().Bool("json", false, "Output as JSON")

	// handoff read flags
	handoffReadCmd.Flags().Bool("json", false, "Output as JSON")

	handoffCmd.AddCommand(handoffCleanCmd)
	handoffCmd.AddCommand(handoffReadCmd)
}

// Helper functions

func generateHandoffID() string {
	b := make([]byte, 4)
	cryptoRand.Read(b)
	return hex.EncodeToString(b)
}

func getGitBranch(forgeRoot string) string {
	cmd := exec.Command("git", "-C", forgeRoot, "rev-parse", "--abbrev-ref", "HEAD")
	output, err := cmd.Output()
	if err != nil {
		return "unknown"
	}
	return strings.TrimSpace(string(output))
}

func getGitLog(forgeRoot string, maxCount int) []string {
	cmd := exec.Command("git", "-C", forgeRoot, "log", fmt.Sprintf("--max-count=%d", maxCount), "--oneline")
	output, err := cmd.Output()
	if err != nil {
		return nil
	}
	var logs []string
	for _, line := range strings.Split(string(output), "\n") {
		if line = strings.TrimSpace(line); line != "" {
			logs = append(logs, line)
		}
	}
	return logs
}

func getModifiedFiles(forgeRoot string) []string {
	cmd := exec.Command("git", "-C", forgeRoot, "status", "--porcelain")
	output, err := cmd.Output()
	if err != nil {
		return nil
	}
	var files []string
	for _, line := range strings.Split(string(output), "\n") {
		if line = strings.TrimSpace(line); line != "" && len(line) > 3 {
			// Porcelain format: XY filename
			file := line[3:]
			if strings.Contains(file, " -> ") {
				file = strings.Split(file, " -> ")[1]
			}
			files = append(files, file)
		}
	}
	return files
}

func saveHandoff(forgeRoot string, handoff *HandoffDoc) (string, error) {
	handoffsDir := filepath.Join(forgeRoot, ".forge", "handoffs")
	if err := os.MkdirAll(handoffsDir, 0755); err != nil {
		return "", err
	}

	path := filepath.Join(handoffsDir, handoff.ID+".json")
	data, err := json.MarshalIndent(handoff, "", "  ")
	if err != nil {
		return "", err
	}
	return path, os.WriteFile(path, data, 0644)
}

func loadHandoff(forgeRoot, handoffID string) (*HandoffDoc, error) {
	path := filepath.Join(forgeRoot, ".forge", "handoffs", handoffID+".json")
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var handoff HandoffDoc
	if err := json.Unmarshal(data, &handoff); err != nil {
		return nil, err
	}
	return &handoff, nil
}

func saveSessionSummary(forgeRoot string, handoff *HandoffDoc, gitLog, modifiedFiles []string) (string, error) {
	memoriesDir := filepath.Join(forgeRoot, ".forge", "memories")
	if err := os.MkdirAll(memoriesDir, 0755); err != nil {
		return "", err
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# Session Live State — %s\n\n", handoff.CreatedAt[:10]))
	sb.WriteString(fmt.Sprintf("**Handoff ID:** `%s`\n", handoff.ID))
	sb.WriteString(fmt.Sprintf("**Agent:** %s\n", handoff.FromAgent))
	sb.WriteString(fmt.Sprintf("**Reason:** %s\n", handoff.Description))
	if branch, ok := handoff.Context["branch"].(string); ok {
		sb.WriteString(fmt.Sprintf("**Branch:** %s\n", branch))
	}
	sb.WriteString(fmt.Sprintf("**Timestamp:** %s\n\n", handoff.CreatedAt))

	sb.WriteString("## What Was Accomplished\n\n")
	if len(gitLog) > 0 {
		for _, entry := range gitLog {
			sb.WriteString(fmt.Sprintf("- %s\n", entry))
		}
	} else {
		sb.WriteString("_No recent commits found._\n")
	}
	sb.WriteString("\n")

	sb.WriteString("## Current State\n\n")
	if len(modifiedFiles) > 0 {
		for _, f := range modifiedFiles {
			sb.WriteString(fmt.Sprintf("- `%s`\n", f))
		}
	} else {
		sb.WriteString("_Working tree is clean._\n")
	}
	sb.WriteString("\n")

	sb.WriteString("---\n")
	sb.WriteString(fmt.Sprintf("_Generated by `forge handoff clean` — handoff `%s`_\n", handoff.ID))

	path := filepath.Join(memoriesDir, "session-live.md")
	return path, os.WriteFile(path, []byte(sb.String()), 0644)
}

// agentClearCommand returns the command to clear/reset context for a given agent.
// Most agents use /clear, but some have different conventions.
func agentClearCommand(agentName string) string {
	// pi uses /new to start a fresh conversation
	if strings.HasPrefix(agentName, "pi") {
		return "/new"
	}
	return "/clear"
}

// clearFleetAgents sends the appropriate clear command to all fleet agent tmux windows
// in the forge session. It detects the orchestrator window (hostname match) and skips it.
// Returns the list of agent names that were cleared.
func clearFleetAgents() []string {
	hostname, _ := os.Hostname()

	// List tmux windows in forge session: "window_name"
	out, err := exec.Command("tmux", "list-windows", "-t", "forge", "-F", "#{window_name}").Output()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Warning: could not list tmux windows: %v\n", err)
		return nil
	}

	var cleared []string
	for _, name := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		name = strings.TrimSpace(name)
		if name == "" {
			continue
		}
		// Skip orchestrator window (matches hostname) and forge-monitor
		if name == hostname || name == "forge-monitor" || name == "monitor" {
			continue
		}

		clearCmd := agentClearCommand(name)

		// Send clear command to the agent via tmux
		// Use -l flag to send literal text, then Enter separately (per CLAUDE.md tmux pattern)
		sendCmd := exec.Command("tmux", "send-keys", "-t", "forge:"+name, "-l", clearCmd)
		if err := sendCmd.Run(); err != nil {
			fmt.Fprintf(os.Stderr, "Warning: could not clear agent %s: %v\n", name, err)
			continue
		}
		// Small delay then send Enter
		time.Sleep(100 * time.Millisecond)
		enterCmd := exec.Command("tmux", "send-keys", "-t", "forge:"+name, "Enter")
		if err := enterCmd.Run(); err != nil {
			fmt.Fprintf(os.Stderr, "Warning: could not send Enter to agent %s: %v\n", name, err)
			continue
		}
		cleared = append(cleared, fmt.Sprintf("%s(%s)", name, clearCmd))
	}

	if len(cleared) > 0 {
		fmt.Printf("[handoff] Cleared %d fleet agent(s): %s\n", len(cleared), strings.Join(cleared, ", "))
	}

	return cleared
}

func gitCheckpoint(forgeRoot, handoffID string) (string, error) {
	// Stage .forge/ and docs/PROMPT.md
	stageCmd := exec.Command("git", "-C", forgeRoot, "add", ".forge/", "docs/PROMPT.md")
	if output, err := stageCmd.CombinedOutput(); err != nil {
		return "", fmt.Errorf("git add failed: %s", string(output))
	}

	// Commit
	commitMsg := fmt.Sprintf("checkpoint: handoff %s", handoffID)
	commitCmd := exec.Command("git", "-C", forgeRoot, "commit", "-m", commitMsg)
	output, err := commitCmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("git commit failed: %s", string(output))
	}

	// Extract SHA from output like "[main abc1234] checkpoint: handoff xyz"
	outputStr := string(output)
	for _, line := range strings.Split(outputStr, "\n") {
		if strings.HasPrefix(line, "[") {
			parts := strings.Fields(line)
			if len(parts) >= 2 {
				return strings.TrimSuffix(parts[1], "]"), nil
			}
		}
	}
	return "", nil
}
