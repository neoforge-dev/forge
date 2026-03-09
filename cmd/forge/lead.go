package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"text/tabwriter"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// leadCmd is the top-level "forge lead" noun for cross-node XNode messaging.
var leadCmd = &cobra.Command{
	Use:   "lead",
	Short: "Cross-node XNode messaging for the FORGE fleet",
	Long: `Send and receive cross-node lead directives via the XNode mesh.

Replaces the Python harness "harness lead send/ack" commands with a
native Go implementation backed by the forged HTTP API.

Commands:
  send       Send a directive to another node
  inbox      List incoming messages
  ack        Acknowledge a message by ID
  acks       List all acknowledgements
  preflight  Verify a remote node is reachable`,
}

// ── Types ─────────────────────────────────────────────────────────────────────

// leadForwardPayload is the wire body for POST /api/xnode/forward.
type leadForwardPayload struct {
	ToNode  string `json:"to_node"`
	TaskID  string `json:"task_id"`
	Summary string `json:"summary"`
	Durable bool   `json:"durable"`
}

// leadForwardResponse is the expected response from POST /api/xnode/forward.
type leadForwardResponse struct {
	MessageID string `json:"message_id"`
	Status    string `json:"status"`
}

// leadMessage is a single inbox entry from GET /api/xnode/inbox.
type leadMessage struct {
	ID         string    `json:"id"`
	FromNode   string    `json:"from_node"`
	TaskID     string    `json:"task_id"`
	Summary    string    `json:"summary"`
	ReceivedAt time.Time `json:"received_at"`
	Durable    bool      `json:"durable"`
}

// leadAck is a single acknowledgement record from GET /api/xnode/acks.
type leadAck struct {
	MessageID string    `json:"message_id"`
	FromNode  string    `json:"from_node"`
	AckedAt   time.Time `json:"acked_at"`
}

// ── forge lead send ────────────────────────────────────────────────────────────

var leadSendCmd = &cobra.Command{
	Use:   "send",
	Short: "Send a directive to a remote node via the XNode mesh",
	Long: `Send a cross-node lead directive.

Example:
  forge lead send --to-node sati --task-id 42 --summary "run wave-99" --durable`,
	RunE: func(cmd *cobra.Command, args []string) error {
		toNode, _ := cmd.Flags().GetString("to-node")
		taskID, _ := cmd.Flags().GetString("task-id")
		summary, _ := cmd.Flags().GetString("summary")
		durable, _ := cmd.Flags().GetBool("durable")

		if toNode == "" {
			return fmt.Errorf("--to-node is required\n  Fix: forge lead send --to-node <node> --task-id <id> --summary \"msg\"")
		}
		if summary == "" {
			return fmt.Errorf("--summary is required\n  Fix: forge lead send --to-node %s --task-id <id> --summary \"msg\"", toNode)
		}

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()

		payload := &leadForwardPayload{
			ToNode:  toNode,
			TaskID:  taskID,
			Summary: summary,
			Durable: durable,
		}

		resp, err := client.Post(ctx, "/xnode/forward", payload)
		if err != nil {
			return fmt.Errorf("lead send failed: %w\n  Check: forge daemon status", err)
		}
		defer resp.Body.Close()

		if err := internal.CheckResponse(resp); err != nil {
			return fmt.Errorf("lead send rejected: %w\n  Check: forge node status %s", err, toNode)
		}

		var result leadForwardResponse
		if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
			// Non-fatal — daemon may return 200 with no body
			fmt.Printf("Sent directive to %s", toNode)
			if taskID != "" {
				fmt.Printf(" (task %s)", taskID)
			}
			fmt.Println()
			return nil
		}

		fmt.Printf("Sent directive to %s", toNode)
		if taskID != "" {
			fmt.Printf(" (task %s)", taskID)
		}
		if result.MessageID != "" {
			fmt.Printf("\n  message-id: %s", result.MessageID)
		}
		if result.Status != "" {
			fmt.Printf("\n  status:     %s", result.Status)
		}
		fmt.Println()
		return nil
	},
}

// ── forge lead inbox ───────────────────────────────────────────────────────────

var leadInboxCmd = &cobra.Command{
	Use:   "inbox",
	Short: "List incoming XNode messages",
	Long: `List messages in the cross-node inbox.

Example:
  forge lead inbox
  forge lead inbox --node sati
  forge lead inbox --format json`,
	RunE: func(cmd *cobra.Command, args []string) error {
		node, _ := cmd.Flags().GetString("node")
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		path := "/xnode/inbox"
		if node != "" {
			path += "?node=" + node
		}

		resp, err := client.Get(ctx, path)
		if err != nil {
			return fmt.Errorf("lead inbox failed: %w\n  Check: forge daemon status", err)
		}
		defer resp.Body.Close()

		if err := internal.CheckResponse(resp); err != nil {
			return fmt.Errorf("lead inbox error: %w", err)
		}

		// Accept either a wrapped {"messages":[...]} or a raw array.
		var messages []leadMessage
		var raw json.RawMessage
		if err := json.NewDecoder(resp.Body).Decode(&raw); err != nil {
			return fmt.Errorf("decode inbox: %w", err)
		}

		var wrapped struct {
			Messages []leadMessage `json:"messages"`
		}
		if err := json.Unmarshal(raw, &wrapped); err == nil && wrapped.Messages != nil {
			messages = wrapped.Messages
		} else {
			if err := json.Unmarshal(raw, &messages); err != nil {
				return fmt.Errorf("decode inbox messages: %w", err)
			}
		}

		if format == "json" {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(messages)
		}

		if len(messages) == 0 {
			fmt.Println("Inbox is empty.")
			fmt.Println("  Messages arrive via: forge lead send --to-node <this-node> ...")
			return nil
		}

		w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
		fmt.Fprintln(w, "FROM\tTASK_ID\tSUMMARY\tRECEIVED_AT")
		for _, m := range messages {
			receivedAt := m.ReceivedAt.Format(time.RFC3339)
			if m.ReceivedAt.IsZero() {
				receivedAt = "-"
			}
			fmt.Fprintf(w, "%s\t%s\t%s\t%s\n",
				m.FromNode, m.TaskID, truncate(m.Summary, 60), receivedAt)
		}
		return w.Flush()
	},
}

// ── forge lead ack ────────────────────────────────────────────────────────────

var leadAckCmd = &cobra.Command{
	Use:   "ack <message-id>",
	Short: "Acknowledge an XNode message by ID",
	Long: `Acknowledge a cross-node message, marking it as processed.

Example:
  forge lead ack msg-abc123
  forge lead ack msg-abc123 --node sati`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		messageID := args[0]
		node, _ := cmd.Flags().GetString("node")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		payload := map[string]string{"message_id": messageID}
		path := "/xnode/acks"
		if node != "" {
			path += "?node=" + node
		}

		resp, err := client.Post(ctx, path, payload)
		if err != nil {
			return fmt.Errorf("lead ack failed: %w\n  Check: forge daemon status", err)
		}
		defer resp.Body.Close()

		if err := internal.CheckResponse(resp); err != nil {
			return fmt.Errorf("lead ack error: %w\n  Verify message ID exists: forge lead inbox", err)
		}

		fmt.Printf("Acknowledged message: %s\n", messageID)
		return nil
	},
}

// ── forge lead acks ───────────────────────────────────────────────────────────

var leadAcksCmd = &cobra.Command{
	Use:   "acks",
	Short: "List all XNode message acknowledgements",
	Long: `List all acknowledgement records.

Example:
  forge lead acks
  forge lead acks --node sati
  forge lead acks --format json`,
	RunE: func(cmd *cobra.Command, args []string) error {
		node, _ := cmd.Flags().GetString("node")
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		path := "/xnode/acks"
		if node != "" {
			path += "?node=" + node
		}

		resp, err := client.Get(ctx, path)
		if err != nil {
			return fmt.Errorf("lead acks failed: %w\n  Check: forge daemon status", err)
		}
		defer resp.Body.Close()

		if err := internal.CheckResponse(resp); err != nil {
			return fmt.Errorf("lead acks error: %w", err)
		}

		var acks []leadAck
		var raw json.RawMessage
		if err := json.NewDecoder(resp.Body).Decode(&raw); err != nil {
			return fmt.Errorf("decode acks: %w", err)
		}

		var wrapped struct {
			Acks []leadAck `json:"acks"`
		}
		if err := json.Unmarshal(raw, &wrapped); err == nil && wrapped.Acks != nil {
			acks = wrapped.Acks
		} else {
			if err := json.Unmarshal(raw, &acks); err != nil {
				return fmt.Errorf("decode acks list: %w", err)
			}
		}

		if format == "json" {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(acks)
		}

		if len(acks) == 0 {
			fmt.Println("No acknowledgements found.")
			return nil
		}

		w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
		fmt.Fprintln(w, "MESSAGE_ID\tFROM\tACKED_AT")
		for _, a := range acks {
			ackedAt := a.AckedAt.Format(time.RFC3339)
			if a.AckedAt.IsZero() {
				ackedAt = "-"
			}
			fmt.Fprintf(w, "%s\t%s\t%s\n", a.MessageID, a.FromNode, ackedAt)
		}
		return w.Flush()
	},
}

// ── forge lead preflight ──────────────────────────────────────────────────────

var leadPreflightCmd = &cobra.Command{
	Use:   "preflight",
	Short: "Verify a remote node is reachable before sending directives",
	Long: `Check connectivity to a remote node by hitting its /health endpoint.

Example:
  forge lead preflight --to-node sati
  forge lead preflight --to-node nova`,
	RunE: func(cmd *cobra.Command, args []string) error {
		toNode, _ := cmd.Flags().GetString("to-node")
		if toNode == "" {
			return fmt.Errorf("--to-node is required\n  Fix: forge lead preflight --to-node <node>")
		}

		nodeURL := fmt.Sprintf("http://%s:8081", toNode)
		fmt.Printf("Preflight check for %s (%s)... ", toNode, nodeURL)

		hc := &http.Client{Timeout: 5 * time.Second}
		resp, err := hc.Get(nodeURL + "/health")
		if err != nil {
			fmt.Println("UNREACHABLE")
			fmt.Printf("  Error: %v\n", err)
			fmt.Printf("  Check: tailscale status | grep %s\n", toNode)
			fmt.Printf("  Check: forge node status %s\n", toNode)
			return fmt.Errorf("node %s unreachable", toNode)
		}
		defer resp.Body.Close()

		if resp.StatusCode == http.StatusOK {
			fmt.Println("OK")
			fmt.Printf("  Node %s is reachable — safe to send directives.\n", toNode)
		} else {
			fmt.Printf("HTTP %d\n", resp.StatusCode)
			fmt.Printf("  Node responded but returned an unexpected status.\n")
			fmt.Printf("  Check: forge daemon status on node %s\n", toNode)
		}
		return nil
	},
}

// ── init ──────────────────────────────────────────────────────────────────────

func init() {
	leadCmd.AddCommand(leadSendCmd)
	leadCmd.AddCommand(leadInboxCmd)
	leadCmd.AddCommand(leadAckCmd)
	leadCmd.AddCommand(leadAcksCmd)
	leadCmd.AddCommand(leadPreflightCmd)

	// forge lead send flags
	leadSendCmd.Flags().String("to-node", "", "Destination node ID (required)")
	leadSendCmd.Flags().String("task-id", "", "Task ID to reference in the directive")
	leadSendCmd.Flags().String("summary", "", "Human-readable summary / message body (required)")
	leadSendCmd.Flags().Bool("durable", false, "Store message in durable queue (survives restarts)")
	leadSendCmd.MarkFlagRequired("to-node")  //nolint:errcheck
	leadSendCmd.MarkFlagRequired("summary")  //nolint:errcheck

	// forge lead inbox flags
	leadInboxCmd.Flags().String("node", "", "Filter inbox by source node")
	leadInboxCmd.Flags().String("format", "table", "Output format: table or json")

	// forge lead ack flags
	leadAckCmd.Flags().String("node", "", "Node context (optional)")

	// forge lead acks flags
	leadAcksCmd.Flags().String("node", "", "Filter acks by node")
	leadAcksCmd.Flags().String("format", "table", "Output format: table or json")

	// forge lead preflight flags
	leadPreflightCmd.Flags().String("to-node", "", "Remote node ID to check (required)")
	leadPreflightCmd.MarkFlagRequired("to-node") //nolint:errcheck
}

// truncate clips a string to n runes and appends "…" if truncated.
func truncate(s string, n int) string {
	runes := []rune(s)
	if len(runes) <= n {
		return s
	}
	return string(runes[:n-1]) + "…"
}
