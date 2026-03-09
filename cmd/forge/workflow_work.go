package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// readContextPct returns the agent's current context window usage as a
// percentage in the 0-100 range, using the following priority:
//  1. FORGE_CONTEXT_PCT environment variable (float string, e.g. "42.5")
//  2. .forge/heartbeat/context_percent file written by the context_guard hook
//  3. 0.0 if neither source is available
func readContextPct() float64 {
	if v := os.Getenv("FORGE_CONTEXT_PCT"); v != "" {
		if f, err := strconv.ParseFloat(strings.TrimSpace(v), 64); err == nil {
			return f
		}
	}

	// Resolve the sidecar file relative to FORGE_ROOT (or a found root).
	forgeRoot := getForgeRoot()
	var sidecarPath string
	if forgeRoot != "" {
		sidecarPath = filepath.Join(forgeRoot, ".forge", "heartbeat", "context_percent")
	} else {
		sidecarPath = filepath.Join(".forge", "heartbeat", "context_percent")
	}

	if data, err := os.ReadFile(sidecarPath); err == nil {
		if f, err := strconv.ParseFloat(strings.TrimSpace(string(data)), 64); err == nil {
			return f
		}
	}

	return 0.0
}

var workCmd = NewWorkCmd()

// NewWorkCmd returns a new work command (used by tests to avoid flag reuse).
func NewWorkCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "work [domain/project]",
		Short: "Enter project context",
		Long: `Set or show the current FORGE domain/project context.

  forge work codeswiftr-com/interview-simulator   Set context
  forge work                                     Show current context
  forge work --show                              Show current context
  forge work --clear                             Clear context

Context is persisted to .forge/context/current.json and can be used to set
FORGE_DOMAIN and FORGE_PROJECT (e.g. eval $(forge work -e domain/project)).
`,
		RunE: runWork,
	}
	cmd.Flags().Bool("show", false, "Show current context")
	cmd.Flags().Bool("clear", false, "Clear current context")
	cmd.Flags().BoolP("export", "e", false, "Print export statements for current shell (use with domain/project)")
	cmd.Flags().Bool("daemon", false, "Autonomous claim loop: poll /api/tasks and claim next available task")
	cmd.Flags().String("agent", "", "Agent ID for claim loop (default: FORGE_AGENT_NAME or hostname)")
	cmd.Flags().Duration("interval", 30*time.Second, "Poll interval for --daemon mode")
	cmd.Flags().Int("max-tasks", 0, "Max tasks to claim in --daemon mode (0 = unlimited)")
	return cmd
}

func init() {
	rootCmd.AddCommand(workCmd)
}

const workContextDir = ".forge/context"
const workContextFile = "current.json"

func workContextPath(forgeRoot string) string {
	return filepath.Join(forgeRoot, workContextDir, workContextFile)
}

func getForgeRoot() string {
	if r := os.Getenv("FORGE_ROOT"); r != "" {
		return r
	}
	return findForgeRoot()
}

func runWork(cmd *cobra.Command, args []string) error {
	_, _ = cmd.Flags().GetBool("show") // --show: show current context (same as no args)
	clear, _ := cmd.Flags().GetBool("clear")
	doExport, _ := cmd.Flags().GetBool("export")
	daemonMode, _ := cmd.Flags().GetBool("daemon")
	format, _ := cmd.Flags().GetString("format")

	// --daemon: autonomous task claim loop
	if daemonMode {
		agentID, _ := cmd.Flags().GetString("agent")
		if agentID == "" {
			agentID = os.Getenv("FORGE_AGENT_NAME")
		}
		if agentID == "" {
			agentID, _ = os.Hostname()
		}
		interval, _ := cmd.Flags().GetDuration("interval")
		maxTasks, _ := cmd.Flags().GetInt("max-tasks")
		return runWorkDaemon(agentID, interval, maxTasks)
	}

	out := io.Writer(os.Stdout)
	if root := cmd.Root(); root != nil {
		out = root.OutOrStdout()
	}
	formatter := internal.NewFormatter(format, out)
	forgeRoot := getForgeRoot()
	if forgeRoot == "" {
		return fmt.Errorf("FORGE_ROOT not set and no .forge directory found (run from FORGE repo or set FORGE_ROOT)")
	}
	ctxPath := workContextPath(forgeRoot)

	// --clear: remove context
	if clear {
		if err := os.Remove(ctxPath); err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("clear context: %w", err)
		}
		if !formatter.IsQuiet() {
			formatter.Println("Context cleared")
		}
		return nil
	}

	// Set context: domain/project argument
	if len(args) >= 1 && strings.Contains(args[0], "/") {
		parts := strings.SplitN(args[0], "/", 2)
		domain, project := strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1])
		if domain == "" || project == "" {
			return fmt.Errorf("invalid domain/project: use domain/project (e.g. codeswiftr-com/interview-simulator)")
		}
		ctx := &internal.WorkContext{
			Domain:      domain,
			Project:     project,
			SetAt:       time.Now().UTC().Format(time.RFC3339),
			ShellPrompt: fmt.Sprintf("(%s:%s)", domain, project),
		}
		dir := filepath.Join(forgeRoot, workContextDir)
		if err := os.MkdirAll(dir, 0755); err != nil {
			return fmt.Errorf("create context dir: %w", err)
		}
		data, err := json.MarshalIndent(ctx, "", "  ")
		if err != nil {
			return fmt.Errorf("serialize context: %w", err)
		}
		if err := os.WriteFile(ctxPath, data, 0644); err != nil {
			return fmt.Errorf("write context: %w", err)
		}
		if doExport {
			formatter.Printf("export FORGE_DOMAIN=%q\n", domain)
			formatter.Printf("export FORGE_PROJECT=%q\n", project)
			return nil
		}
		if !formatter.IsQuiet() {
			formatter.Printf("Context set: %s / %s\n", domain, project)
			formatter.Printf("Environment: FORGE_DOMAIN=%s, FORGE_PROJECT=%s\n", domain, project)
		}
		return nil
	}

	// Show context: --show or no args
	data, err := os.ReadFile(ctxPath)
	if err != nil {
		if os.IsNotExist(err) {
			if !formatter.IsQuiet() {
				formatter.Println("No context set")
			}
			return nil
		}
		return fmt.Errorf("read context: %w", err)
	}
	var ctx internal.WorkContext
	if err := json.Unmarshal(data, &ctx); err != nil {
		return fmt.Errorf("parse context: %w", err)
	}
	return formatter.FormatWorkContext(&ctx)
}

// runWorkDaemon runs the autonomous task claim loop.
// It polls /api/tasks?status=queued at the given interval and claims the next
// available task for agentID. Runs until interrupted or maxTasks claimed.
func runWorkDaemon(agentID string, interval time.Duration, maxTasks int) error {
	client := internal.NewClient()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)

	claimed := 0
	fmt.Printf("Work daemon started\n")
	fmt.Printf("  Agent:    %s\n", agentID)
	fmt.Printf("  Interval: %s\n", interval)
	if maxTasks > 0 {
		fmt.Printf("  Max:      %d tasks\n", maxTasks)
	}
	fmt.Printf("  Press Ctrl+C to stop\n\n")

	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	// Register this agent as a node in the xnode mesh so it appears in forge node list.
	// Best-effort: errors are logged but never fatal.
	go func() {
		nodeAddr := ""
		if out, err := exec.Command("tailscale", "ip", "-4").Output(); err == nil {
			nodeAddr = strings.TrimSpace(string(out))
		}
		apiPort := os.Getenv("PORT")
		if apiPort == "" {
			apiPort = "8081"
		}
		if nodeAddr != "" {
			nodeAddr = nodeAddr + ":" + apiPort
		} else {
			// Fall back to hostname:port — at least resolvable on LAN.
			if h, err := os.Hostname(); err == nil {
				nodeAddr = h + ":" + apiPort
			}
		}
		regBody := map[string]interface{}{
			"id":             agentID,
			"hostname":       agentID,
			"address":        nodeAddr,
			"status":         "online",
			"last_heartbeat": time.Now().Format(time.RFC3339),
		}
		regCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if resp, err := client.Post(regCtx, "/api/xnode/nodes/register", regBody); err != nil {
			fmt.Fprintf(os.Stderr, "[work] node registration failed: %v\n", err)
		} else {
			resp.Body.Close()
			fmt.Printf("  Node:     registered as %s at %s\n", agentID, nodeAddr)
		}
	}()

	// sendHeartbeat is a best-effort helper: errors are logged but never fatal.
	sendHeartbeat := func() {
		pct := readContextPct()
		hbCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := client.SendHeartbeat(hbCtx, agentID, pct); err != nil {
			// Daemon may not be running; don't block the work loop.
			fmt.Fprintf(os.Stderr, "[%s] heartbeat: %v\n", time.Now().Format("15:04:05"), err)
		}
	}

	// Run immediately on first tick
	if err := tryClaimTask(client, agentID); err == nil {
		claimed++
		if maxTasks > 0 && claimed >= maxTasks {
			fmt.Printf("Reached max tasks (%d). Stopping.\n", maxTasks)
			return nil
		}
	}
	sendHeartbeat()

	for {
		select {
		case <-sigCh:
			fmt.Printf("\nShutting down (claimed %d tasks)\n", claimed)
			return nil
		case <-ticker.C:
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			err := tryClaimTaskCtx(ctx, client, agentID)
			cancel()
			if err == nil {
				claimed++
				fmt.Printf("[%s] Claimed task #%d\n", time.Now().Format("15:04:05"), claimed)
				if maxTasks > 0 && claimed >= maxTasks {
					fmt.Printf("Reached max tasks (%d). Stopping.\n", maxTasks)
					return nil
				}
			} else if !isNoTasksAvailable(err) {
				fmt.Fprintf(os.Stderr, "[%s] Claim error: %v\n", time.Now().Format("15:04:05"), err)
			}
			sendHeartbeat()
		}
	}
}

func tryClaimTask(client *internal.Client, agentID string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return tryClaimTaskCtx(ctx, client, agentID)
}

func tryClaimTaskCtx(ctx context.Context, client *internal.Client, agentID string) error {
	// Get next queued or requested task (both are claimable per claimTaskHandler)
	// Try "queued" first, then "requested" if no queued tasks
	result, err := client.ListTasks(ctx, "queued", 1, "")
	if err != nil {
		return fmt.Errorf("list tasks: %w", err)
	}
	if result == nil || len(result.Tasks) == 0 {
		// Try "requested" status
		result, err = client.ListTasks(ctx, "requested", 1, "")
		if err != nil {
			return fmt.Errorf("list tasks: %w", err)
		}
		if result == nil || len(result.Tasks) == 0 {
			return errNoTasks
		}
	}

	task := result.Tasks[0]
	claimed, err := client.ClaimTask(ctx, task.ID, agentID)
	if err != nil {
		return fmt.Errorf("claim task %s: %w", task.ID, err)
	}

	fmt.Printf("[%s] Claimed: %s → %s\n",
		time.Now().Format("15:04:05"), claimed.ID, agentID)
	fmt.Printf("  Read: .forge/dispatches/%s.md (if exists)\n", claimed.ID)
	return nil
}

var errNoTasks = fmt.Errorf("no tasks available")

func isNoTasksAvailable(err error) bool {
	return err == errNoTasks || strings.Contains(err.Error(), "no tasks available")
}
