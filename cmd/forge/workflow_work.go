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
	cmd.Flags().Bool("execute", false, "Execute tasks after claiming (default: false, claim-only mode)")
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
		execute, _ := cmd.Flags().GetBool("execute")
		return runWorkDaemonExecute(agentID, interval, maxTasks, execute)
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

// workDaemonConfig holds tunable parameters for the work daemon backoff and
// circuit breaker. Extracted to a struct so tests can override defaults.
type workDaemonConfig struct {
	// BaseInterval is the normal poll interval when the queue has tasks.
	BaseInterval time.Duration
	// MaxInterval caps exponential backoff when the queue is empty.
	MaxInterval time.Duration
	// CircuitOpenAfter is the number of consecutive API errors before the
	// circuit breaker opens and polling is paused.
	CircuitOpenAfter int
	// Execute controls whether claimed tasks are executed immediately.
	// When false (default), the daemon only claims tasks and prints the dispatch path.
	// When true, executeTask() is called after every successful claim.
	Execute bool
}

// defaultWorkDaemonConfig returns production defaults.
func defaultWorkDaemonConfig(baseInterval time.Duration) workDaemonConfig {
	return workDaemonConfig{
		BaseInterval:     baseInterval,
		MaxInterval:      5 * time.Minute,
		CircuitOpenAfter: 3,
	}
}

// runWorkDaemon runs the autonomous task claim loop (claim-only mode).
// It polls /api/tasks?status=queued at the given interval and claims the next
// available task for agentID. Runs until interrupted or maxTasks claimed.
func runWorkDaemon(agentID string, interval time.Duration, maxTasks int) error {
	return runWorkDaemonWithConfig(agentID, maxTasks, defaultWorkDaemonConfig(interval))
}

// runWorkDaemonExecute is like runWorkDaemon but allows toggling task execution.
func runWorkDaemonExecute(agentID string, interval time.Duration, maxTasks int, execute bool) error {
	cfg := defaultWorkDaemonConfig(interval)
	cfg.Execute = execute
	return runWorkDaemonWithConfig(agentID, maxTasks, cfg)
}

func runWorkDaemonWithConfig(agentID string, maxTasks int, cfg workDaemonConfig) error {
	client := internal.NewClient()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)

	claimed := 0
	fmt.Printf("Work daemon started\n")
	fmt.Printf("  Agent:    %s\n", agentID)
	fmt.Printf("  Interval: %s (max backoff: %s)\n", cfg.BaseInterval, cfg.MaxInterval)
	if maxTasks > 0 {
		fmt.Printf("  Max:      %d tasks\n", maxTasks)
	}
	fmt.Printf("  Press Ctrl+C to stop\n\n")

	// Register this agent as a node in the xnode mesh so it appears in forge node list.
	// Best-effort: errors are logged but never fatal.
	go func() {
		nodeAddr := ""
		if out, err := exec.Command("tailscale", "ip", "-4").Output(); err == nil {
			nodeAddr = strings.TrimSpace(string(out))
		}
		apiPort := os.Getenv("PORT")
		if apiPort == "" {
			apiPort = internal.ResolveAPIPort()
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

	// backoffInterval returns a capped exponential interval based on how many
	// consecutive empty-queue polls have occurred.
	backoffInterval := func(emptyStreak int) time.Duration {
		d := cfg.BaseInterval
		for i := 0; i < emptyStreak && d < cfg.MaxInterval; i++ {
			d *= 2
		}
		if d > cfg.MaxInterval {
			d = cfg.MaxInterval
		}
		return d
	}

	// Poll state
	emptyStreak := 0  // consecutive empty-queue responses
	errStreak := 0    // consecutive API errors (circuit breaker)
	nextPoll := time.Now() // when to next attempt a claim

	// Run immediately on first iteration.
	for {
		// Check for shutdown before sleeping.
		select {
		case <-sigCh:
			fmt.Printf("\nShutting down (claimed %d tasks)\n", claimed)
			return nil
		default:
		}

		// Wait until nextPoll, checking for shutdown every 200ms.
		for time.Now().Before(nextPoll) {
			remaining := time.Until(nextPoll)
			wait := remaining
			if wait > 200*time.Millisecond {
				wait = 200 * time.Millisecond
			}
			select {
			case <-sigCh:
				fmt.Printf("\nShutting down (claimed %d tasks)\n", claimed)
				return nil
			case <-time.After(wait):
			}
		}

		// Circuit breaker: if API errors exceed threshold, skip and back off.
		if errStreak >= cfg.CircuitOpenAfter {
			backoff := backoffInterval(errStreak - cfg.CircuitOpenAfter + 1)
			fmt.Fprintf(os.Stderr, "[%s] Circuit open after %d errors — backing off %s\n",
				time.Now().Format("15:04:05"), errStreak, backoff)
			nextPoll = time.Now().Add(backoff)
			sendHeartbeat()
			continue
		}

		// Attempt to claim a task.
		claimCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		claimedTask, err := tryClaimTaskCtx(claimCtx, client, agentID)
		cancel()

		switch {
		case err == nil:
			// Success: reset all streak counters, poll at base interval.
			claimed++
			emptyStreak = 0
			errStreak = 0
			fmt.Printf("[%s] Claimed task #%d\n", time.Now().Format("15:04:05"), claimed)

			// ACK the task to transition DISPATCHED → RUNNING
			if claimedTask != nil {
				ackCtx, ackCancel := context.WithTimeout(context.Background(), 10*time.Second)
				if _, ackErr := client.AckTask(ackCtx, claimedTask.ID, agentID); ackErr != nil {
					fmt.Fprintf(os.Stderr, "[%s] ACK warning for task %s: %v\n",
						time.Now().Format("15:04:05"), claimedTask.ID, ackErr)
					// Non-fatal: task is still claimed, just not ACK'd yet
				}
				ackCancel()
			}

			if cfg.Execute && claimedTask != nil {
				execCtx, execCancel := context.WithTimeout(context.Background(), 10*time.Minute)
				if execErr := executeTask(execCtx, client, claimedTask, agentID); execErr != nil {
					fmt.Fprintf(os.Stderr, "[%s] Execute error for task %s: %v\n",
						time.Now().Format("15:04:05"), claimedTask.ID, execErr)
				}
				execCancel()
			}
			if maxTasks > 0 && claimed >= maxTasks {
				fmt.Printf("Reached max tasks (%d). Stopping.\n", maxTasks)
				return nil
			}
			nextPoll = time.Now().Add(cfg.BaseInterval)

		case isNoTasksAvailable(err):
			// Queue is empty: apply exponential backoff.
			emptyStreak++
			errStreak = 0
			sleep := backoffInterval(emptyStreak)
			if emptyStreak > 1 {
				fmt.Printf("[%s] Queue empty (streak %d) — next poll in %s\n",
					time.Now().Format("15:04:05"), emptyStreak, sleep)
			}
			nextPoll = time.Now().Add(sleep)

		default:
			// API error: increment error streak toward circuit breaker.
			errStreak++
			emptyStreak = 0
			fmt.Fprintf(os.Stderr, "[%s] Claim error (%d/%d): %v\n",
				time.Now().Format("15:04:05"), errStreak, cfg.CircuitOpenAfter, err)
			nextPoll = time.Now().Add(cfg.BaseInterval)
		}

		sendHeartbeat()
	}
}

func tryClaimTask(client *internal.Client, agentID string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_, err := tryClaimTaskCtx(ctx, client, agentID)
	return err
}

// tryClaimTaskCtx finds and claims the next available queued/requested task.
// Returns the claimed Task on success, errNoTasks if the queue is empty or all
// candidates are already assigned to other agents, or another error if the API
// call failed.
//
// It fetches up to 5 candidates per status bucket so that tasks already
// dispatched to other agents via tmux (which sets AssignedTo) are skipped
// rather than attempted. Attempting to claim an already-assigned task causes a
// 409 Conflict response which would increment the circuit-breaker error streak.
func tryClaimTaskCtx(ctx context.Context, client *internal.Client, agentID string) (*internal.Task, error) {
	// Fetch up to 5 candidates so we can skip tasks already assigned elsewhere.
	const candidateLimit = 5

	// findEligible returns the first task from tasks that is either unassigned
	// or already assigned to agentID (self-reclaim is fine).
	findEligible := func(tasks []internal.Task) *internal.Task {
		for i := range tasks {
			t := &tasks[i]
			if t.AssignedTo == "" || t.AssignedTo == agentID {
				return t
			}
		}
		return nil
	}

	// Try "queued" first.
	result, err := client.ListTasks(ctx, "queued", candidateLimit, "")
	if err != nil {
		return nil, fmt.Errorf("list tasks: %w", err)
	}

	var eligible *internal.Task
	if result != nil && len(result.Tasks) > 0 {
		eligible = findEligible(result.Tasks)
	}

	// Fall back to "requested" if no eligible queued task found.
	if eligible == nil {
		result, err = client.ListTasks(ctx, "requested", candidateLimit, "")
		if err != nil {
			return nil, fmt.Errorf("list tasks: %w", err)
		}
		if result != nil && len(result.Tasks) > 0 {
			eligible = findEligible(result.Tasks)
		}
	}

	// All candidates are assigned to other agents (or both buckets are empty).
	if eligible == nil {
		return nil, errNoTasks
	}

	claimed, err := client.ClaimTask(ctx, eligible.ID, agentID)
	if err != nil {
		return nil, fmt.Errorf("claim task %s: %w", eligible.ID, err)
	}

	fmt.Printf("[%s] Claimed: %s → %s\n",
		time.Now().Format("15:04:05"), claimed.ID, agentID)
	fmt.Printf("  Read: .forge/dispatches/%s.md (if exists)\n", claimed.ID)
	return claimed, nil
}

var errNoTasks = fmt.Errorf("no tasks available")

func isNoTasksAvailable(err error) bool {
	return err == errNoTasks || strings.Contains(err.Error(), "no tasks available")
}

// agentCommands maps FORGE_AGENT_NAME values to their CLI invocation.
// The slice is the base argv; executeTask appends task-specific arguments as needed.
var agentCommands = map[string][]string{
	"kimi":     {"kimi", "-y"},
	"gemini":   {"gemini", "-y"},
	"pi":       {"pi"},
	"minimax":  {"minimax"},
	"glm":      {"glm"},
	"opencode": {"opencode"},
	"kilo":     {"kilo"},
	"claude":   {"claude", "--dangerously-skip-permissions", "-p"},
	"codex":    {"codex", "--dangerously-bypass-approvals-and-sandbox"},
}

// executeTask runs the given task for agentID.
//
// For "claude", the task is executed as a subprocess in print-mode (-p) and the
// captured stdout is written to .forge/heartbeat/results/{agentID}-{taskID}.md.
// CompleteTask is called after the subprocess exits.
//
// For all other agents the task prompt is pushed into the agent's tmux window
// using the 2-step send-keys protocol. The function returns immediately after
// the tmux push without waiting for the agent to finish; the agent is expected
// to write its own result file and the daemon's result-file-monitor patrol will
// detect completion.
//
// In both paths a dispatch file is written first so the agent has a persistent
// record of the task content.
func executeTask(ctx context.Context, client *internal.Client, task *internal.Task, agentID string) error {
	forgeRoot := getForgeRoot()
	if forgeRoot == "" {
		forgeRoot = "."
	}

	// Build the human-readable task prompt.
	title := task.Title
	if title == "" {
		title = task.ID
	}
	description := task.Description
	if description == "" {
		description = title
	}
	prompt := fmt.Sprintf("Task %s: %s\n\n%s", task.ID, title, description)

	// Write dispatch file so the agent has a durable record.
	dispatchDir := filepath.Join(forgeRoot, ".forge", "dispatches")
	if err := os.MkdirAll(dispatchDir, 0755); err != nil {
		return fmt.Errorf("create dispatches dir: %w", err)
	}
	dispatchFile := filepath.Join(dispatchDir, task.ID+".md")
	dispatchContent := fmt.Sprintf("# Task %s\n\n**Title:** %s\n\n**Description:**\n\n%s\n",
		task.ID, title, description)
	if writeErr := os.WriteFile(dispatchFile, []byte(dispatchContent), 0644); writeErr != nil {
		// Non-fatal: log and continue.
		fmt.Fprintf(os.Stderr, "[warn] write dispatch file %s: %v\n", dispatchFile, writeErr)
	}

	// Determine agent name from env (strip any node prefix like "forge:kimi").
	agentName := os.Getenv("FORGE_AGENT_NAME")
	if agentName == "" {
		agentName = agentID
	}
	// Normalise "forge:kimi" → "kimi"
	if idx := strings.LastIndex(agentName, ":"); idx >= 0 {
		agentName = agentName[idx+1:]
	}

	if agentName == "claude" {
		return executeTaskClaude(ctx, client, task, agentID, prompt, forgeRoot)
	}
	return executeTaskTmux(agentID, agentName, task.ID, prompt)
}

// executeTaskClaude runs claude non-interactively (-p flag), captures output,
// writes a result file, then marks the task complete.
func executeTaskClaude(ctx context.Context, client *internal.Client, task *internal.Task, agentID, prompt, forgeRoot string) error {
	baseCmd, ok := agentCommands["claude"]
	if !ok {
		return fmt.Errorf("claude not found in agentCommands")
	}

	// argv: claude --dangerously-skip-permissions -p "<prompt>"
	argv := append(append([]string{}, baseCmd...), prompt)
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Env = os.Environ()

	// ACK the task before executing to transition DISPATCHED → RUNNING
	ackClient := internal.NewClient()
	ackCtx, ackCancel := context.WithTimeout(ctx, 10*time.Second)
	if _, ackErr := ackClient.AckTask(ackCtx, task.ID, agentID); ackErr != nil {
		fmt.Fprintf(os.Stderr, "[%s] ACK warning: %v\n", time.Now().Format("15:04:05"), ackErr)
	}
	ackCancel()

	fmt.Printf("[%s] Executing claude for task %s\n", time.Now().Format("15:04:05"), task.ID)
	output, err := cmd.Output()
	if err != nil {
		// Capture stderr if available.
		exitErr, _ := err.(*exec.ExitError)
		stderr := ""
		if exitErr != nil {
			stderr = string(exitErr.Stderr)
		}
		return fmt.Errorf("claude subprocess failed: %w\nstderr: %s", err, stderr)
	}

	// Write result file.
	resultsDir := filepath.Join(forgeRoot, ".forge", "heartbeat", "results")
	if mkErr := os.MkdirAll(resultsDir, 0755); mkErr != nil {
		return fmt.Errorf("create results dir: %w", mkErr)
	}
	resultFile := filepath.Join(resultsDir, agentID+"-"+task.ID+".md")
	resultContent := fmt.Sprintf("# Result: %s\n\n**Agent:** %s\n**Task:** %s\n\n## Output\n\n%s\n",
		task.ID, agentID, task.Title, string(output))
	if writeErr := os.WriteFile(resultFile, []byte(resultContent), 0644); writeErr != nil {
		return fmt.Errorf("write result file: %w", writeErr)
	}
	fmt.Printf("[%s] Result written: %s\n", time.Now().Format("15:04:05"), resultFile)

	// Mark complete.
	summary := string(output)
	if len(summary) > 500 {
		summary = summary[:500] + "..."
	}
	if _, completeErr := client.CompleteTask(ctx, task.ID, summary); completeErr != nil {
		return fmt.Errorf("complete task %s: %w", task.ID, completeErr)
	}
	fmt.Printf("[%s] Task %s marked complete\n", time.Now().Format("15:04:05"), task.ID)
	return nil
}

// executeTaskTmux pushes the task prompt to the agent's tmux window using the
// 2-step send-keys protocol and returns immediately (fire-and-forget).
// The daemon's result-file-monitor patrol is responsible for detecting completion.
func executeTaskTmux(agentID, agentName, taskID, prompt string) error {
	tmuxSession := "forge"
	fmt.Printf("[%s] Pushing task %s to tmux %s:%s\n",
		time.Now().Format("15:04:05"), taskID, tmuxSession, agentID)
	if err := notifyAgentViaTmux(tmuxSession, agentID, prompt); err != nil {
		// Non-fatal: agent may not be running in a tmux window.
		fmt.Fprintf(os.Stderr, "[warn] tmux push for task %s: %v\n", taskID, err)
	}
	// Don't mark complete — the agent will do it when it writes results.
	return nil
}
