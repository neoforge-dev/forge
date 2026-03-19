package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// relayCmd is the top-level "forge relay" noun for dispatch relay worker operations.
var relayCmd = &cobra.Command{
	Use:   "relay",
	Short: "Dispatch relay worker — deliver pending .forge/dispatches/ files to agents",
	Long: `Manage the dispatch relay worker that polls .forge/dispatches/ for pending .md
files and delivers them via "forge dispatch send".

Replaces the Python harness "relay.py" with a native Go implementation backed
by the forged HTTP API.

Commands:
  start        Start the relay worker (daemon or foreground)
  stop         Stop the relay worker
  status       Show worker state and delivery stats
  deliveries   List recent delivery records`,
}

// ── Types ─────────────────────────────────────────────────────────────────────

// relayStartPayload is the wire body for POST /api/relay/start.
type relayStartPayload struct {
	Interval string `json:"interval"`
}

// relayStatusResponse is the expected response from GET /api/relay/status.
type relayStatusResponse struct {
	State         string    `json:"state"`
	PendingCount  int       `json:"pending_count"`
	DeliveryRate  float64   `json:"delivery_rate"`
	LastRunAt     time.Time `json:"last_run_at"`
	IntervalSecs  int       `json:"interval_seconds"`
	ErrorCount    int       `json:"error_count"`
}

// relayDelivery is a single delivery record from GET /api/relay/deliveries.
type relayDelivery struct {
	ID          string    `json:"id"`
	Agent       string    `json:"agent"`
	File        string    `json:"file"`
	State       string    `json:"state"`
	AttemptedAt time.Time `json:"attempted_at"`
}

// relayDeliveriesResponse wraps the deliveries list from the API.
type relayDeliveriesResponse struct {
	Deliveries []relayDelivery `json:"deliveries"`
	Count      int             `json:"count"`
}

// ── relay graceful fallback ────────────────────────────────────────────────────

// relayEndpointMissing returns true when a response indicates the relay
// endpoints are not implemented in the daemon (HTTP 404 or 405).
func relayEndpointMissing(resp *http.Response) bool {
	return resp.StatusCode == http.StatusNotFound ||
		resp.StatusCode == http.StatusMethodNotAllowed
}

const relayFallbackMsg = `Relay endpoints are not yet implemented in this daemon version.
  To see relay-related patrols:  forge patrol list
  To run the relay in foreground: forge relay start --no-daemon`

// ── forge relay start ─────────────────────────────────────────────────────────

var relayStartCmd = &cobra.Command{
	Use:   "start",
	Short: "Start the relay worker",
	Long: `Start the relay worker.

Without --no-daemon, calls POST /api/relay/start on the daemon, which manages
the relay as a background patrol. The daemon handles polling and retries.

With --no-daemon, runs in the foreground — scanning .forge/dispatches/*.md on
every interval and delivering undelivered files via "forge dispatch send".

Examples:
  forge relay start
  forge relay start --interval 60s
  forge relay start --no-daemon`,
	RunE: func(cmd *cobra.Command, args []string) error {
		interval, _ := cmd.Flags().GetString("interval")
		noDaemon, _ := cmd.Flags().GetBool("no-daemon")

		if noDaemon {
			return relayRunForeground(interval)
		}

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()

		payload := &relayStartPayload{Interval: interval}
		resp, err := client.Post(ctx, "/relay/start", payload)
		if err != nil {
			return fmt.Errorf("relay start failed: %w\n  Check: forge daemon status", err)
		}
		defer resp.Body.Close()

		if relayEndpointMissing(resp) {
			fmt.Println(relayFallbackMsg)
			return nil
		}

		if err := internal.CheckResponse(resp); err != nil {
			return fmt.Errorf("relay start rejected: %w\n  Check: forge daemon status", err)
		}

		fmt.Printf("Relay worker started (interval: %s)\n", interval)
		fmt.Println("  Monitor:  forge relay status")
		fmt.Println("  Patrols:  forge patrol list")
		return nil
	},
}

// relayRunForeground scans .forge/dispatches/ and delivers files in a loop.
func relayRunForeground(interval string) error {
	dur, err := time.ParseDuration(interval)
	if err != nil {
		return fmt.Errorf("invalid --interval %q: %w\n  Fix: use a Go duration like 30s, 1m, 2m30s", interval, err)
	}

	dispatchDir := relayDispatchDir()
	fmt.Printf("Relay worker running in foreground (interval: %s, dispatches: %s)\n", interval, dispatchDir)
	fmt.Println("  Press Ctrl-C to stop.")

	for {
		if err := relayScanAndDeliver(dispatchDir); err != nil {
			fmt.Fprintf(os.Stderr, "relay scan error: %v\n", err)
		}
		time.Sleep(dur)
	}
}

// relayScanAndDeliver scans the dispatches directory and calls forge dispatch send
// for each .md file that appears to be undelivered.
func relayScanAndDeliver(dispatchDir string) error {
	entries, err := filepath.Glob(filepath.Join(dispatchDir, "*.md"))
	if err != nil {
		return fmt.Errorf("glob dispatches: %w", err)
	}

	if len(entries) == 0 {
		fmt.Printf("[%s] No pending dispatch files.\n", time.Now().Format(time.TimeOnly))
		return nil
	}

	fmt.Printf("[%s] Found %d dispatch file(s).\n", time.Now().Format(time.TimeOnly), len(entries))

	for _, fpath := range entries {
		agent := relayAgentFromFilename(filepath.Base(fpath))
		if agent == "" {
			fmt.Fprintf(os.Stderr, "  skip %s — cannot determine agent from filename\n", filepath.Base(fpath))
			continue
		}

		target := "forge:" + agent
		fmt.Printf("  delivering %s -> %s ...", filepath.Base(fpath), target)

		// Resolve forge binary from PATH so the subprocess uses the installed binary.
		forgeBin, err := exec.LookPath("forge")
		if err != nil {
			// Fall back to the running binary's path.
			forgeBin, _ = os.Executable()
		}

		out, err := exec.Command(forgeBin, "dispatch", "send", target, "--file", fpath).CombinedOutput()
		if err != nil {
			fmt.Printf(" FAILED\n")
			fmt.Fprintf(os.Stderr, "    error: %v\n    output: %s\n", err, strings.TrimSpace(string(out)))
		} else {
			fmt.Printf(" OK\n")
		}
	}

	return nil
}

// relayAgentFromFilename extracts the agent name from a dispatch filename.
// Expected patterns:
//   AGENT-TASKID-DATE.md   e.g. kimi-42-20260308.md  → "kimi"
//   AGENT.md               e.g. gemini.md             → "gemini"
func relayAgentFromFilename(name string) string {
	// Strip .md suffix.
	base := strings.TrimSuffix(name, ".md")
	if base == "" {
		return ""
	}
	// The agent name is everything before the first hyphen-digit sequence.
	// e.g. "kimi-42-20260308" → "kimi", "gemini" → "gemini"
	parts := strings.SplitN(base, "-", 2)
	if len(parts) == 0 {
		return ""
	}
	return parts[0]
}

// relayDispatchDir returns the canonical .forge/dispatches path, resolved
// relative to FORGE_ROOT (or CWD if root is unset).
func relayDispatchDir() string {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = findForgeRoot()
	}
	if root == "" {
		root = "."
	}
	return filepath.Join(root, ".forge", "dispatches")
}

// ── forge relay stop ──────────────────────────────────────────────────────────

var relayStopCmd = &cobra.Command{
	Use:   "stop",
	Short: "Stop the relay worker",
	Long: `Stop the relay worker by calling POST /api/relay/stop on the daemon.

Example:
  forge relay stop`,
	RunE: func(cmd *cobra.Command, args []string) error {
		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()

		resp, err := client.Post(ctx, "/relay/stop", nil)
		if err != nil {
			return fmt.Errorf("relay stop failed: %w\n  Check: forge daemon status", err)
		}
		defer resp.Body.Close()

		if relayEndpointMissing(resp) {
			fmt.Println(relayFallbackMsg)
			return nil
		}

		if err := internal.CheckResponse(resp); err != nil {
			return fmt.Errorf("relay stop rejected: %w\n  Check: forge relay status", err)
		}

		fmt.Println("Relay worker stopped.")
		return nil
	},
}

// ── forge relay status ────────────────────────────────────────────────────────

var relayStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show relay worker state and delivery statistics",
	Long: `Show relay worker state, pending count, and delivery rate.

If the daemon does not expose relay endpoints, falls back to showing
relay-related patrols from forge patrol list.

Example:
  forge relay status`,
	RunE: func(cmd *cobra.Command, args []string) error {
		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		resp, err := client.Get(ctx, "/relay/status")
		if err != nil {
			return fmt.Errorf("relay status failed: %w\n  Check: forge daemon status", err)
		}
		defer resp.Body.Close()

		if relayEndpointMissing(resp) {
			// Fallback: show relay-related patrols.
			return relayShowPatrolFallback(client)
		}

		if err := internal.CheckResponse(resp); err != nil {
			return fmt.Errorf("relay status error: %w", err)
		}

		var status relayStatusResponse
		if err := json.NewDecoder(resp.Body).Decode(&status); err != nil {
			return fmt.Errorf("decode relay status: %w", err)
		}

		fmt.Printf("State:          %s\n", status.State)
		fmt.Printf("Pending files:  %d\n", status.PendingCount)
		fmt.Printf("Delivery rate:  %.1f%%\n", status.DeliveryRate*100)
		fmt.Printf("Error count:    %d\n", status.ErrorCount)
		if status.IntervalSecs > 0 {
			fmt.Printf("Interval:       %ds\n", status.IntervalSecs)
		}
		if !status.LastRunAt.IsZero() {
			fmt.Printf("Last run:       %s\n", status.LastRunAt.Format(time.RFC3339))
		}
		return nil
	},
}

// relayShowPatrolFallback queries patrol list and shows relay-related entries.
func relayShowPatrolFallback(client *internal.Client) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	patrols, err := client.ListPatrols(ctx)
	if err != nil {
		fmt.Println(relayFallbackMsg)
		return nil
	}

	// Collect indices of relay-related patrols (avoids unexported type annotation).
	var relayIdxs []int
	for i, p := range patrols {
		lower := strings.ToLower(p.Name)
		if strings.Contains(lower, "relay") || strings.Contains(lower, "dispatch") {
			relayIdxs = append(relayIdxs, i)
		}
	}

	if len(relayIdxs) == 0 {
		fmt.Println("No relay-related patrols found in the daemon.")
		fmt.Println("  Start relay in foreground: forge relay start --no-daemon")
		fmt.Println("  View all patrols:          forge patrol list")
		return nil
	}

	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "NAME\tSTATUS\tLAST_RUN\tINTERVAL")
	for _, i := range relayIdxs {
		p := patrols[i]
		lastRun := "-"
		if p.LastRun != nil {
			lastRun = *p.LastRun
		}
		fmt.Fprintf(w, "%s\t%s\t%s\t%ds\n", p.Name, p.Status, lastRun, p.IntervalSeconds)
	}
	return w.Flush()
}

// ── forge relay deliveries ────────────────────────────────────────────────────

var relayDeliveriesCmd = &cobra.Command{
	Use:   "deliveries",
	Short: "List recent relay delivery records",
	Long: `List recent delivery records from the relay worker.

Each row shows the agent, dispatch file, delivery state, and when the
delivery was attempted.

Examples:
  forge relay deliveries
  forge relay deliveries --tail 50
  forge relay deliveries --state pending
  forge relay deliveries --state failed`,
	RunE: func(cmd *cobra.Command, args []string) error {
		tail, _ := cmd.Flags().GetInt("tail")
		state, _ := cmd.Flags().GetString("state")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		path := fmt.Sprintf("/relay/deliveries?tail=%d", tail)
		if state != "" {
			path += "&state=" + state
		}

		resp, err := client.Get(ctx, path)
		if err != nil {
			return fmt.Errorf("relay deliveries failed: %w\n  Check: forge daemon status", err)
		}
		defer resp.Body.Close()

		if relayEndpointMissing(resp) {
			fmt.Println(relayFallbackMsg)
			return nil
		}

		if err := internal.CheckResponse(resp); err != nil {
			return fmt.Errorf("relay deliveries error: %w", err)
		}

		var result relayDeliveriesResponse
		var raw json.RawMessage
		if err := json.NewDecoder(resp.Body).Decode(&raw); err != nil {
			return fmt.Errorf("decode deliveries: %w", err)
		}

		// Accept either {"deliveries":[...]} or a raw array.
		if err := json.Unmarshal(raw, &result); err != nil || result.Deliveries == nil {
			var deliveries []relayDelivery
			if err := json.Unmarshal(raw, &deliveries); err != nil {
				return fmt.Errorf("decode deliveries list: %w", err)
			}
			result.Deliveries = deliveries
		}

		if len(result.Deliveries) == 0 {
			if state != "" {
				fmt.Printf("No %q delivery records found.\n", state)
			} else {
				fmt.Println("No delivery records found.")
			}
			fmt.Println("  Start relay: forge relay start")
			return nil
		}

		w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
		fmt.Fprintln(w, "ID\tAGENT\tFILE\tSTATE\tATTEMPTED_AT")
		for _, d := range result.Deliveries {
			attemptedAt := "-"
			if !d.AttemptedAt.IsZero() {
				attemptedAt = d.AttemptedAt.Format(time.RFC3339)
			}
			fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\n",
				d.ID,
				d.Agent,
				filepath.Base(d.File),
				d.State,
				attemptedAt,
			)
		}
		return w.Flush()
	},
}

// ── init ──────────────────────────────────────────────────────────────────────

func init() {
	relayCmd.Hidden = true
	relayCmd.AddCommand(relayStartCmd)
	relayCmd.AddCommand(relayStopCmd)
	relayCmd.AddCommand(relayStatusCmd)
	relayCmd.AddCommand(relayDeliveriesCmd)

	// forge relay start flags
	relayStartCmd.Flags().String("interval", "30s", "Poll interval (e.g. 30s, 1m)")
	relayStartCmd.Flags().Bool("no-daemon", false, "Run in foreground instead of delegating to daemon")

	// forge relay deliveries flags
	relayDeliveriesCmd.Flags().Int("tail", 20, "Show last N delivery records")
	relayDeliveriesCmd.Flags().String("state", "", "Filter by state: pending, delivered, failed")
}
