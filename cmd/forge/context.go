package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io/ioutil"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// contextStatusFile is the canonical location for context% state.
const contextStatusFile = ".forge/context/status.json"
const contextPercentFile = ".forge/heartbeat/context_percent"

type contextStatusData struct {
	Percentage float64 `json:"percentage"`
	UpdatedAt  string  `json:"updated_at"`
}

// contextCmd represents the context noun
var contextCmd = &cobra.Command{
	Use:   "context",
	Short: "Manage knowledge contexts",
	Long:  "List, show, and create knowledge contexts (envelopes, royal jelly, memories).",
}

// contextListCmd: forge context list
var contextListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all contexts",
	Long:  "List all contexts, optionally filtered by domain and project.",
	RunE: func(cmd *cobra.Command, args []string) error {
		domain, _ := cmd.Flags().GetString("domain")
		project, _ := cmd.Flags().GetString("project")
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.ListContexts(ctx, domain, project)
		if err != nil {
			return fmt.Errorf("failed to list contexts: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if err := formatter.FormatContexts(result.Contexts); err != nil {
			return err
		}

		if format == "table" {
			formatter.Printf("Total: %d contexts\n", result.Count)
		}
		return nil
	},
}

// contextShowCmd: forge context show <key>
var contextShowCmd = &cobra.Command{
	Use:   "show <key>",
	Short: "Show context details",
	Long:  "Display detailed information and content of a specific context.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		contextKey := args[0]
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		context, err := client.GetContext(ctx, contextKey)
		if err != nil {
			return fmt.Errorf("failed to get context: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatContext(context)
	},
}

// contextCreateCmd: forge context create --key ... --domain ...
var contextCreateCmd = &cobra.Command{
	Use:   "create",
	Short: "Create a new context",
	Long:  "Create a new knowledge context from a file or string.",
	RunE: func(cmd *cobra.Command, args []string) error {
		key, _ := cmd.Flags().GetString("key")
		domain, _ := cmd.Flags().GetString("domain")
		project, _ := cmd.Flags().GetString("project")
		cType, _ := cmd.Flags().GetString("type")
		file, _ := cmd.Flags().GetString("file")
		content, _ := cmd.Flags().GetString("content")
		format, _ := cmd.Flags().GetString("format")

		if key == "" || domain == "" {
			return fmt.Errorf("--key and --domain are required")
		}

		if file != "" {
			data, err := ioutil.ReadFile(file)
			if err != nil {
				return fmt.Errorf("failed to read context file: %w", err)
			}
			content = string(data)
		}

		if content == "" {
			return fmt.Errorf("--content or --file is required")
		}

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		req := &internal.CreateContextRequest{
			Key:     key,
			Domain:  domain,
			Project: project,
			Type:    cType,
			Content: content,
		}

		context, err := client.CreateContext(ctx, req)
		if err != nil {
			return fmt.Errorf("failed to create context: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(context)
		}
		formatter.Printf("Created: %s\n", context.Key)
		return nil
	},
}

// contextEnvelopeCmd: forge context envelope --task <id> --file <file>
var contextEnvelopeCmd = &cobra.Command{
	Use:   "envelope",
	Short: "Create a context envelope for a task",
	Long:  "Create a context envelope specifically for agent handoffs on a task.",
	RunE: func(cmd *cobra.Command, args []string) error {
		taskID, _ := cmd.Flags().GetString("task")
		file, _ := cmd.Flags().GetString("file")
		format, _ := cmd.Flags().GetString("format")

		if taskID == "" || file == "" {
			return fmt.Errorf("--task and --file are required")
		}

		data, err := ioutil.ReadFile(file)
		if err != nil {
			return fmt.Errorf("failed to read envelope file: %w", err)
		}

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		// For envelopes, key is derived from taskID
		key := fmt.Sprintf("envelope-%s", taskID)
		
		req := &internal.CreateContextRequest{
			Key:     key,
			Type:    "envelope",
			Content: string(data),
			Metadata: map[string]interface{}{
				"taskId": taskID,
			},
		}

		context, err := client.CreateContext(ctx, req)
		if err != nil {
			return fmt.Errorf("failed to create envelope: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(context)
		}
		formatter.Printf("Envelope created for task %s: %s\n", taskID, context.Key)
		return nil
	},
}

// contextStatusCmd: forge context status [--estimate]
var contextStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show context window usage and thresholds",
	RunE: func(cmd *cobra.Command, args []string) error {
		pct := readContextPercent()
		bar := buildBar(pct, 24)
		status := "OK"
		if pct >= 90 {
			status = "CRITICAL"
		} else if pct >= 75 {
			status = "COMPACT NOW"
		} else if pct >= 50 {
			status = "HANDOFF PREP"
		}
		fmt.Printf("Context: %.1f%% [%s]\n", pct, bar)
		fmt.Printf("Thresholds: 50%% handoff-prep  75%% compact  90%% CRITICAL\n")
		fmt.Printf("Status: %s\n", status)
		return nil
	},
}

// contextUpdateCmd: forge context update --percentage FLOAT
var contextUpdateCmd = &cobra.Command{
	Use:   "update",
	Short: "Write context% to status.json and context_percent file",
	RunE: func(cmd *cobra.Command, args []string) error {
		pct, _ := cmd.Flags().GetFloat64("percentage")
		if pct < 0 || pct > 100 {
			return fmt.Errorf("--percentage must be between 0 and 100")
		}
		data := contextStatusData{
			Percentage: pct,
			UpdatedAt:  time.Now().UTC().Format(time.RFC3339),
		}
		// Write status.json
		if err := os.MkdirAll(filepath.Dir(contextStatusFile), 0755); err != nil {
			return err
		}
		b, _ := json.MarshalIndent(data, "", "  ")
		if err := os.WriteFile(contextStatusFile, b, 0644); err != nil {
			return fmt.Errorf("failed to write status.json: %w", err)
		}
		// Write plain percent file
		if err := os.MkdirAll(filepath.Dir(contextPercentFile), 0755); err != nil {
			return err
		}
		if err := os.WriteFile(contextPercentFile, []byte(fmt.Sprintf("%.1f", pct)), 0644); err != nil {
			return fmt.Errorf("failed to write context_percent: %w", err)
		}
		fmt.Printf("Context: %.1f%%\n", pct)
		return nil
	},
}

// contextRecoverCmd: forge context recover [--agent NAME]
var contextRecoverCmd = &cobra.Command{
	Use:   "recover",
	Short: "Send /handoff + /clear + /continue to agent window",
	RunE: func(cmd *cobra.Command, args []string) error {
		agent, _ := cmd.Flags().GetString("agent")
		if agent == "" {
			return fmt.Errorf("--agent is required")
		}
		target := fmt.Sprintf("forge:%s", agent)
		steps := []string{"/handoff", "/clear", "/continue"}
		for _, s := range steps {
			if err := tmuxSend(target, s); err != nil {
				return fmt.Errorf("tmux send failed for %q: %w", s, err)
			}
			time.Sleep(500 * time.Millisecond)
		}
		fmt.Printf("Recovery sequence sent to forge:%s\n", agent)
		return nil
	},
}

// contextHandoffAgentCmd: forge context handoff-agent --agent NAME
var contextHandoffAgentCmd = &cobra.Command{
	Use:   "handoff-agent",
	Short: "Send /handoff to agent window",
	RunE: func(cmd *cobra.Command, args []string) error {
		agent, _ := cmd.Flags().GetString("agent")
		if agent == "" {
			return fmt.Errorf("--agent is required")
		}
		return tmuxSend(fmt.Sprintf("forge:%s", agent), "/handoff")
	},
}

// contextClearAgentCmd: forge context clear-agent --agent NAME
var contextClearAgentCmd = &cobra.Command{
	Use:   "clear-agent",
	Short: "Send /clear to agent window",
	RunE: func(cmd *cobra.Command, args []string) error {
		agent, _ := cmd.Flags().GetString("agent")
		if agent == "" {
			return fmt.Errorf("--agent is required")
		}
		return tmuxSend(fmt.Sprintf("forge:%s", agent), "/clear")
	},
}

// tmuxSend sends a text + Enter to a tmux target (two separate calls as per CLAUDE.md).
func tmuxSend(target, text string) error {
	if err := exec.Command("tmux", "send-keys", "-t", target, "-l", text).Run(); err != nil {
		return fmt.Errorf("tmux send-keys text: %w", err)
	}
	time.Sleep(100 * time.Millisecond)
	if err := exec.Command("tmux", "send-keys", "-t", target, "Enter").Run(); err != nil {
		return fmt.Errorf("tmux send-keys Enter: %w", err)
	}
	return nil
}

// readContextPercent reads the current context % from the status file.
func readContextPercent() float64 {
	if b, err := os.ReadFile(contextStatusFile); err == nil {
		var d contextStatusData
		if json.Unmarshal(b, &d) == nil {
			return d.Percentage
		}
	}
	// Fallback: plain percent file
	if b, err := os.ReadFile(contextPercentFile); err == nil {
		var pct float64
		fmt.Sscanf(string(b), "%f", &pct)
		return pct
	}
	return 0
}

// buildBar produces a visual progress bar of width n.
func buildBar(pct float64, n int) string {
	filled := int(math.Round(pct / 100 * float64(n)))
	if filled > n {
		filled = n
	}
	bar := make([]rune, n)
	for i := range bar {
		if i < filled {
			bar[i] = '█'
		} else {
			bar[i] = '░'
		}
	}
	return string(bar)
}

func init() {
	contextCmd.Hidden = true
	// context list flags
	contextListCmd.Flags().String("domain", "", "Filter by domain")
	contextListCmd.Flags().String("project", "", "Filter by project")

	// context create flags
	contextCreateCmd.Flags().String("key", "", "Context key (required)")
	contextCreateCmd.Flags().String("domain", "", "Domain (required)")
	contextCreateCmd.Flags().String("project", "", "Project")
	contextCreateCmd.Flags().String("type", "envelope", "Context type (envelope, jelly, memory)")
	contextCreateCmd.Flags().String("file", "", "Path to context file")
	contextCreateCmd.Flags().String("content", "", "Context content string")

	// context envelope flags
	contextEnvelopeCmd.Flags().String("task", "", "Task ID for the envelope (required)")
	contextEnvelopeCmd.Flags().String("file", "", "Path to envelope file (required)")

	// context status flags (none needed)

	// context update flags
	contextUpdateCmd.Flags().Float64("percentage", 0, "Context window usage percentage (0-100)")

	// context recover / handoff-agent / clear-agent flags
	contextRecoverCmd.Flags().String("agent", "", "Agent name (tmux window in forge session)")
	contextHandoffAgentCmd.Flags().String("agent", "", "Agent name (tmux window in forge session)")
	contextClearAgentCmd.Flags().String("agent", "", "Agent name (tmux window in forge session)")

	// Add all commands to context noun
	contextCmd.AddCommand(contextListCmd)
	contextCmd.AddCommand(contextShowCmd)
	contextCmd.AddCommand(contextCreateCmd)
	contextCmd.AddCommand(contextEnvelopeCmd)
	contextCmd.AddCommand(contextStatusCmd)
	contextCmd.AddCommand(contextUpdateCmd)
	contextCmd.AddCommand(contextRecoverCmd)
	contextCmd.AddCommand(contextHandoffAgentCmd)
	contextCmd.AddCommand(contextClearAgentCmd)
}
