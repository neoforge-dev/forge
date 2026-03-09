package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// heartbeatCmd: forge heartbeat --agent NAME [--interval 60s] [--daemon] [--context-pct N]
var heartbeatCmd = &cobra.Command{
	Use:   "heartbeat",
	Short: "Send agent heartbeats to the daemon",
	Long: `Send a heartbeat for an agent, reporting it as online.

Without --daemon, sends a single heartbeat and exits.
With --daemon, loops at the given interval until Ctrl+C (SIGINT/SIGTERM).

The --context-pct value defaults to the FORGE_CONTEXT_PCT environment
variable if that is set; otherwise it defaults to 0.

Examples:
  forge heartbeat --agent kimi
  forge heartbeat --agent kimi --context-pct 42.5
  forge heartbeat --agent kimi --interval 60s --daemon
  forge heartbeat --agent minimax --interval 3m --daemon`,
	RunE: runHeartbeat,
}

func init() {
	heartbeatCmd.Flags().String("agent", "", "Agent ID / name to send heartbeat for (required)")
	heartbeatCmd.MarkFlagRequired("agent")
	heartbeatCmd.Flags().Duration("interval", 60*time.Second, "Interval between heartbeats (used with --daemon)")
	heartbeatCmd.Flags().Bool("daemon", false, "Loop forever, sending heartbeats at --interval until Ctrl+C")
	heartbeatCmd.Flags().Float64("context-pct", contextPctDefault(), "Context window usage percentage (0-100); defaults to $FORGE_CONTEXT_PCT")
}

// contextPctDefault reads FORGE_CONTEXT_PCT from the environment at init time.
func contextPctDefault() float64 {
	if v := os.Getenv("FORGE_CONTEXT_PCT"); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return 0.0
}

func runHeartbeat(cmd *cobra.Command, args []string) error {
	agentID, _ := cmd.Flags().GetString("agent")
	interval, _ := cmd.Flags().GetDuration("interval")
	daemon, _ := cmd.Flags().GetBool("daemon")
	contextPct, _ := cmd.Flags().GetFloat64("context-pct")

	client := internal.NewClient()

	send := func() error {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		if err := client.SendHeartbeat(ctx, agentID, contextPct); err != nil {
			return err
		}
		fmt.Printf("[heartbeat] agent=%s status=online context=%.1f%% sent=%s\n",
			agentID, contextPct, time.Now().UTC().Format(time.RFC3339))
		return nil
	}

	if !daemon {
		return send()
	}

	// Daemon mode: send immediately, then tick.
	if err := send(); err != nil {
		fmt.Fprintf(os.Stderr, "[heartbeat] error: %v (will retry)\n", err)
	}

	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	defer signal.Stop(sigs)

	fmt.Printf("[heartbeat] daemon started for agent=%s interval=%s\n", agentID, interval)

	for {
		select {
		case <-ticker.C:
			if err := send(); err != nil {
				// Soft-fail: log and continue so a transient daemon outage doesn't kill the loop.
				fmt.Fprintf(os.Stderr, "[heartbeat] error: %v (will retry in %s)\n", err, interval)
			}
		case sig := <-sigs:
			fmt.Printf("[heartbeat] received %s — shutting down\n", sig)
			return nil
		}
	}
}
