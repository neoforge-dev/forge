//go:build !tmux_bridge

// notify.go — forge notify: send FORGE status/alerts to Telegram via openclaw
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// notifyCmd is the root "forge notify" command.
var notifyCmd = &cobra.Command{
	Use:   "notify",
	Short: "Send FORGE status/alerts to Telegram via openclaw",
	Long: `Send FORGE fleet data to Telegram using the openclaw CLI.

Subcommands wrap forge data commands and deliver formatted output
via 'openclaw message send'.

Prerequisites:
  - openclaw CLI installed and on PATH
  - FORGE_TELEGRAM_CHAT_ID env var set, OR
    ~/.openclaw/openclaw.json with channels.telegram.allowFrom configured

Examples:
  forge notify status              # Send fleet health snapshot
  forge notify gates               # Send pending human gates
  forge notify fleet               # Send agent list with context%
  forge notify tasks               # Send recent tasks
  forge notify daily               # Full daily digest
  forge notify alert "Deploy done" # Send custom alert
  forge notify test                # Verify setup with a test message`,
	Run: func(cmd *cobra.Command, args []string) {
		cmd.Help()
	},
}

// notifyStatusCmd sends fleet health to Telegram.
var notifyStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Send fleet health snapshot to Telegram",
	RunE:  runNotifyStatus,
}

// notifyGatesCmd sends pending human gates to Telegram.
var notifyGatesCmd = &cobra.Command{
	Use:   "gates",
	Short: "Send pending human gates to Telegram",
	RunE:  runNotifyGates,
}

// notifyFleetCmd sends agent list with context% to Telegram.
var notifyFleetCmd = &cobra.Command{
	Use:   "fleet",
	Short: "Send agent list with context% to Telegram",
	RunE:  runNotifyFleet,
}

// notifyTasksCmd sends recent tasks to Telegram.
var notifyTasksCmd = &cobra.Command{
	Use:   "tasks",
	Short: "Send recent tasks to Telegram",
	RunE:  runNotifyTasks,
}

// notifyDailyCmd sends a full daily digest (status + gates + queue).
var notifyDailyCmd = &cobra.Command{
	Use:   "daily",
	Short: "Send full daily digest (status + gates + queue) to Telegram",
	RunE:  runNotifyDaily,
}

// notifyAlertCmd sends a custom alert message to Telegram.
var notifyAlertCmd = &cobra.Command{
	Use:   "alert [message]",
	Short: "Send custom alert to Telegram",
	Args:  cobra.MinimumNArgs(1),
	RunE:  runNotifyAlert,
}

// notifyTestCmd sends a test message to verify setup.
var notifyTestCmd = &cobra.Command{
	Use:   "test",
	Short: "Send test message to verify setup",
	RunE:  runNotifyTest,
}

func init() {
	// Register flags on each subcommand.
	for _, sub := range []*cobra.Command{
		notifyStatusCmd,
		notifyGatesCmd,
		notifyFleetCmd,
		notifyTasksCmd,
		notifyDailyCmd,
		notifyAlertCmd,
		notifyTestCmd,
	} {
		sub.Flags().String("channel", "telegram", "Notification channel (telegram)")
		sub.Flags().String("target", "", "Target chat ID (default: FORGE_TELEGRAM_CHAT_ID env or config)")
		sub.Flags().Bool("dry-run", false, "Print message without sending")
		sub.Flags().Bool("quiet", false, "Suppress output (for cron)")
	}

	// Wire subcommands onto notifyCmd.
	notifyCmd.AddCommand(notifyStatusCmd)
	notifyCmd.AddCommand(notifyGatesCmd)
	notifyCmd.AddCommand(notifyFleetCmd)
	notifyCmd.AddCommand(notifyTasksCmd)
	notifyCmd.AddCommand(notifyDailyCmd)
	notifyCmd.AddCommand(notifyAlertCmd)
	notifyCmd.AddCommand(notifyTestCmd)
}

// ── Subcommand handlers ───────────────────────────────────────────────────────

func runNotifyStatus(cmd *cobra.Command, _ []string) error {
	msg, err := buildStatusMessage()
	if err != nil {
		return err
	}
	return dispatchNotify(cmd, msg)
}

func runNotifyGates(cmd *cobra.Command, _ []string) error {
	msg, err := buildGatesMessage()
	if err != nil {
		return err
	}
	return dispatchNotify(cmd, msg)
}

func runNotifyFleet(cmd *cobra.Command, _ []string) error {
	msg, err := buildFleetMessage()
	if err != nil {
		return err
	}
	return dispatchNotify(cmd, msg)
}

func runNotifyTasks(cmd *cobra.Command, _ []string) error {
	msg, err := buildTasksMessage()
	if err != nil {
		return err
	}
	return dispatchNotify(cmd, msg)
}

func runNotifyDaily(cmd *cobra.Command, _ []string) error {
	apiURL := internal.ResolveControlPlaneURL()
	now := time.Now().UTC()

	var buf bytes.Buffer
	fmt.Fprintf(&buf, "FORGE Daily — %s\n\n", now.Format("Jan 2, 15:04 UTC"))

	// Executive summary: one line per metric
	online := checkControlPlane(apiURL)
	if !online {
		fmt.Fprintf(&buf, "⚠️ Daemon OFFLINE\n\n")
	} else {
		agents, _ := fetchAgents(apiURL)
		activeCount := 0
		for _, a := range agents {
			if a.Status == "online" {
				activeCount++
			}
		}
		tasks, _ := fetchTaskSummary(apiURL)
		patrolCount, patrolErrors := fetchPatrolSummary(apiURL, 24*time.Hour)
		fmt.Fprintf(&buf, "Fleet: %d agents | %d tasks done (24h) | %d failed\n", activeCount, tasks.Completed, tasks.Failed)
		fmt.Fprintf(&buf, "Queue: %d waiting | %d running\n", tasks.Queued, tasks.Running)
		if patrolErrors > 0 {
			fmt.Fprintf(&buf, "⚠️ %d patrol errors (24h)\n", patrolErrors)
		} else {
			fmt.Fprintf(&buf, "Patrols: %d active, clean\n", patrolCount)
		}
		fmt.Fprintf(&buf, "\n")
	}

	// Human gates — the actionable part
	if g, err := buildGatesMessage(); err == nil {
		fmt.Fprintf(&buf, "%s\n", g)
	}

	// Last 3 commits — what shipped
	commits := fetchRecentCommits(3)
	if len(commits) > 0 {
		fmt.Fprintf(&buf, "Shipped:\n")
		for _, c := range commits {
			fmt.Fprintf(&buf, "  %s\n", c)
		}
	}

	if buf.Len() == 0 {
		return fmt.Errorf("failed to build daily digest\n  Recovery: check 'forge daemon status'")
	}

	return dispatchNotify(cmd, buf.String())
}

func runNotifyAlert(cmd *cobra.Command, args []string) error {
	hostname, _ := os.Hostname()
	text := strings.Join(args, " ")
	msg := fmt.Sprintf("⚠️ [%s] %s\n%s", hostname, text, time.Now().UTC().Format("15:04 UTC"))
	return dispatchNotify(cmd, msg)
}

func runNotifyTest(cmd *cobra.Command, _ []string) error {
	hostname, _ := os.Hostname()
	msg := fmt.Sprintf("FORGE notification test from %s\n%s", hostname, time.Now().UTC().Format("2006-01-02 15:04 UTC"))
	return dispatchNotify(cmd, msg)
}

// ── Message builders ──────────────────────────────────────────────────────────

func buildStatusMessage() (string, error) {
	apiURL := internal.ResolveControlPlaneURL()
	now := time.Now().UTC()

	var buf bytes.Buffer
	fmt.Fprintf(&buf, "FORGE Status — %s\n", now.Format("2006-01-02 15:04 UTC"))

	online := checkControlPlane(apiURL)
	if online {
		fmt.Fprintf(&buf, "Control Plane: online (%s)\n", apiURL)
	} else {
		fmt.Fprintf(&buf, "Control Plane: OFFLINE (%s)\n", apiURL)
	}

	if online {
		agents, _ := fetchAgents(apiURL)
		activeCount := 0
		for _, a := range agents {
			if a.Status == "online" {
				activeCount++
			}
		}
		fmt.Fprintf(&buf, "Agents: %d active / %d registered\n", activeCount, len(agents))

		tasks, _ := fetchTaskSummary(apiURL)
		fmt.Fprintf(&buf, "Queue: %d queued, %d running, %d done (24h), %d failed (24h)\n",
			tasks.Queued, tasks.Running, tasks.Completed, tasks.Failed)

		patrolCount, patrolErrors := fetchPatrolSummary(apiURL, 1*time.Hour)
		fmt.Fprintf(&buf, "Patrols: %d active, %d errors (1h)\n", patrolCount, patrolErrors)
	}

	commits := fetchRecentCommits(3)
	if len(commits) > 0 {
		fmt.Fprintf(&buf, "Recent commits:\n")
		for _, c := range commits {
			fmt.Fprintf(&buf, "  %s\n", c)
		}
	}

	return buf.String(), nil
}

func buildGatesMessage() (string, error) {
	root := forgeRootDir()

	domainProducts, _ := loadDomainProducts(root)

	gates, err := collectGates(root, domainProducts)
	if err != nil {
		return "", fmt.Errorf("failed to collect gates: %w\n  Recovery: verify .forge/context/ exists under FORGE_ROOT", err)
	}

	var buf bytes.Buffer
	fmt.Fprintf(&buf, "FORGE Human Gates\n")

	var pending []Gate
	for _, g := range gates {
		if g.Status == "PENDING" {
			pending = append(pending, g)
		}
	}

	if len(pending) == 0 {
		fmt.Fprintf(&buf, "No blocking gates — all resolved!\n")
		return buf.String(), nil
	}

	fmt.Fprintf(&buf, "Blocking revenue (%d gate", len(pending))
	if len(pending) != 1 {
		fmt.Fprintf(&buf, "s")
	}
	fmt.Fprintf(&buf, "):\n")

	for _, g := range pending {
		if g.Duration != "" {
			fmt.Fprintf(&buf, "  %s (%s)", g.ID, g.Duration)
		} else {
			fmt.Fprintf(&buf, "  %s", g.ID)
		}
		if g.What != "" {
			fmt.Fprintf(&buf, " — %s", g.What)
		}
		fmt.Fprintf(&buf, "\n")
		if g.Domain != "" && g.Domain != "forge" {
			fmt.Fprintf(&buf, "    domain: %s", g.Domain)
			if g.Product != "" {
				fmt.Fprintf(&buf, " | product: %s", g.Product)
			}
			fmt.Fprintf(&buf, "\n")
		}
	}

	return buf.String(), nil
}

func buildFleetMessage() (string, error) {
	apiURL := internal.ResolveControlPlaneURL()

	agents, err := fetchAgents(apiURL)
	if err != nil {
		return "", fmt.Errorf("failed to fetch agents: %w\n  Recovery: check 'forge daemon status'", err)
	}

	var buf bytes.Buffer
	fmt.Fprintf(&buf, "FORGE Fleet — %s\n", time.Now().UTC().Format("2006-01-02 15:04 UTC"))

	if len(agents) == 0 {
		fmt.Fprintf(&buf, "No agents registered.\n")
		return buf.String(), nil
	}

	activeCount := 0
	for _, a := range agents {
		if a.Status == "online" {
			activeCount++
		}
	}
	fmt.Fprintf(&buf, "Agents: %d active / %d registered\n", activeCount, len(agents))

	for _, a := range agents {
		statusIcon := "o"
		if a.Status == "online" {
			statusIcon = "+"
		}
		taskPart := ""
		if a.CurrentTaskID != "" {
			taskPart = fmt.Sprintf(" [task: %s]", a.CurrentTaskID)
		}
		nodePart := ""
		if a.Node != "" {
			nodePart = fmt.Sprintf(" (%s)", a.Node)
		}
		fmt.Fprintf(&buf, "  [%s] %-14s ctx:%.1f%%%s%s\n",
			statusIcon, a.AgentID, a.ContextPct, nodePart, taskPart)
	}

	return buf.String(), nil
}

func buildTasksMessage() (string, error) {
	apiURL := internal.ResolveControlPlaneURL()
	client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(apiURL))

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/tasks?limit=10")
	if err != nil {
		return "", fmt.Errorf("failed to fetch tasks: %w\n  Recovery: check 'forge daemon status'", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("failed to read tasks response: %w", err)
	}

	var result struct {
		Tasks []struct {
			ID        string `json:"id"`
			Title     string `json:"title"`
			Status    string `json:"status"`
			State     string `json:"state"`
			Domain    string `json:"domain"`
			AgentID   string `json:"agent_id"`
			Priority  int    `json:"priority"`
		} `json:"tasks"`
		Count int `json:"count"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("failed to parse tasks response: %w", err)
	}

	var buf bytes.Buffer
	fmt.Fprintf(&buf, "FORGE Tasks (recent 10) — %s\n", time.Now().UTC().Format("2006-01-02 15:04 UTC"))
	fmt.Fprintf(&buf, "Total tasks: %d\n", result.Count)

	if len(result.Tasks) == 0 {
		fmt.Fprintf(&buf, "No tasks found.\n")
		return buf.String(), nil
	}

	for _, t := range result.Tasks {
		status := t.Status
		if status == "" {
			status = t.State
		}

		title := t.Title
		if len(title) > 50 {
			title = title[:47] + "..."
		}

		agentPart := ""
		if t.AgentID != "" {
			agentPart = fmt.Sprintf(" [%s]", t.AgentID)
		}

		statusIcon := statusEmoji(status)
		fmt.Fprintf(&buf, "  %s %-10s %s%s\n", statusIcon, status, title, agentPart)
	}

	return buf.String(), nil
}

// statusEmoji returns a simple text icon for a task status.
func statusEmoji(status string) string {
	switch strings.ToLower(status) {
	case "queued", "pending":
		return "[ ]"
	case "running", "assigned", "dispatched":
		return "[>]"
	case "completed", "done", "approved":
		return "[x]"
	case "failed", "abandoned":
		return "[!]"
	default:
		return "[-]"
	}
}

// ── Dispatch (openclaw invocation) ────────────────────────────────────────────

// dispatchNotify sends a formatted message via openclaw, or prints it on --dry-run.
func dispatchNotify(cmd *cobra.Command, message string) error {
	channel, _ := cmd.Flags().GetString("channel")
	target, _ := cmd.Flags().GetString("target")
	dryRun, _ := cmd.Flags().GetBool("dry-run")
	quiet, _ := cmd.Flags().GetBool("quiet")

	// Resolve target: flag > env > config file fallback.
	if target == "" {
		target = resolveNotifyTarget()
	}

	if dryRun {
		if !quiet {
			fmt.Printf("[dry-run] channel=%s target=%s\n", channel, target)
			fmt.Println("--- message ---")
			fmt.Println(message)
			fmt.Println("---------------")
		}
		return nil
	}

	if target == "" {
		return fmt.Errorf("no notification target configured\n  Recovery: set FORGE_TELEGRAM_CHAT_ID env var, or add channels.telegram to ~/.openclaw/openclaw.json")
	}

	// Shell out to openclaw — no import, just exec.
	ocPath, err := exec.LookPath("openclaw")
	if err != nil {
		return fmt.Errorf("openclaw CLI not found on PATH\n  Recovery: install openclaw or set FORGE_TELEGRAM_CHAT_ID + configure openclaw channels")
	}

	ocCmd := exec.Command(ocPath, "message", "send", "--channel", channel, "--target", target, "-m", message)
	ocCmd.Stderr = os.Stderr

	if out, err := ocCmd.Output(); err != nil {
		return fmt.Errorf("openclaw send failed: %w\n  Recovery: run 'openclaw message send --channel %s --target %s -m test' to debug", err, channel, target)
	} else if !quiet {
		trimmed := strings.TrimSpace(string(out))
		if trimmed != "" {
			fmt.Println(trimmed)
		}
	}

	return nil
}

// resolveNotifyTarget resolves the notification target ID.
// Priority: FORGE_TELEGRAM_CHAT_ID env var > ~/.openclaw/openclaw.json allowFrom[0].
func resolveNotifyTarget() string {
	if id := os.Getenv("FORGE_TELEGRAM_CHAT_ID"); id != "" {
		return id
	}

	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}

	cfgPath := filepath.Join(home, ".openclaw", "openclaw.json")
	data, err := os.ReadFile(cfgPath)
	if err != nil {
		return ""
	}

	var cfg struct {
		Channels struct {
			Telegram struct {
				AllowFrom []string `json:"allowFrom"`
			} `json:"telegram"`
		} `json:"channels"`
	}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return ""
	}

	if len(cfg.Channels.Telegram.AllowFrom) > 0 {
		return cfg.Channels.Telegram.AllowFrom[0]
	}
	return ""
}
