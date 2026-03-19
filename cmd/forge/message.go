package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/spf13/cobra"
)

// ── Types ─────────────────────────────────────────────────────────────────────

// cliMessage mirrors the Message struct in cmd/forged but lives in the CLI
// package to avoid cross-module imports.
type cliMessage struct {
	ID        string    `json:"id"`
	From      string    `json:"from"`
	To        string    `json:"to"`
	Timestamp time.Time `json:"timestamp"`
	Type      string    `json:"type"`
	Subject   string    `json:"subject"`
	Body      string    `json:"body"`
	Ack       bool      `json:"ack"`
}

// ── Root command ──────────────────────────────────────────────────────────────

// messageCmd is the "forge message" noun.
var messageCmd = &cobra.Command{
	Use:   "message",
	Short: "Cross-node git-based message bus (no HTTP required)",
	Long: `Send and receive messages between FORGE nodes via git-tracked JSONL files.

Messages are written to .forge/messages/outbox/{target}.jsonl and delivered
when the target node does a git pull. The relay patrol (running every 60s)
processes incoming messages in .forge/messages/inbox/.

Valid types: task, status, research, result, directive, ack

Examples:
  forge message send --to sati --type task --subject "S116 test work" --body "..."
  forge message send --to trinity --type directive --subject "Research rotation" --body "..."
  forge message list
  forge message list --outbox`,
}

// ── forge message send ────────────────────────────────────────────────────────

var messageSendCmd = &cobra.Command{
	Use:   "send",
	Short: "Send a message to another node",
	Long: `Write a message to .forge/messages/outbox/{target}.jsonl.

The message is delivered when the target node does a git pull.

Examples:
  forge message send --to sati --type task --subject "Coverage wave" --body "Run wave 267"
  forge message send --to trinity --type directive --subject "Sprint update" --body "See PLAN.md"`,
	RunE: runMessageSend,
}

func init() {
	messageSendCmd.Flags().String("to", "", "Target node (e.g. sati, nova, trinity) — required")
	messageSendCmd.Flags().String("type", "task", "Message type: task|status|research|result|directive|ack")
	messageSendCmd.Flags().String("subject", "", "Message subject — required")
	messageSendCmd.Flags().String("body", "", "Message body (can be multiline JSON or plain text)")
	_ = messageSendCmd.MarkFlagRequired("to")
	_ = messageSendCmd.MarkFlagRequired("subject")
}

func runMessageSend(cmd *cobra.Command, _ []string) error {
	to, _ := cmd.Flags().GetString("to")
	msgType, _ := cmd.Flags().GetString("type")
	subject, _ := cmd.Flags().GetString("subject")
	body, _ := cmd.Flags().GetString("body")

	validTypes := map[string]bool{
		"task": true, "status": true, "research": true,
		"result": true, "directive": true, "ack": true,
	}
	if !validTypes[msgType] {
		return fmt.Errorf("invalid message type %q — valid: task, status, research, result, directive, ack", msgType)
	}

	msg := cliMessage{
		ID:        fmt.Sprintf("msg-%d", time.Now().UnixNano()),
		From:      cliNodeID(),
		To:        to,
		Timestamp: time.Now().UTC(),
		Type:      msgType,
		Subject:   subject,
		Body:      body,
		Ack:       false,
	}

	if err := cliSendMessage(to, msg); err != nil {
		return fmt.Errorf("failed to send message: %w\nRecovery: ensure .forge/messages/outbox/ exists and is writable", err)
	}

	fmt.Printf("Message sent to %s [%s]: %s\n", to, msgType, subject)
	fmt.Printf("ID: %s\n", msg.ID)
	fmt.Printf("Delivery: run 'git push' to propagate to remote; target node picks up on next git pull + relay patrol\n")
	return nil
}

// ── forge message list ────────────────────────────────────────────────────────

var messageListCmd = &cobra.Command{
	Use:   "list",
	Short: "List inbox (or outbox) messages",
	Long: `Show cross-node messages.

By default shows the local inbox (messages received from other nodes).
Use --outbox to show messages sent from this node.

Examples:
  forge message list             # show inbox
  forge message list --outbox    # show sent messages`,
	RunE: runMessageList,
}

func init() {
	messageListCmd.Flags().Bool("outbox", false, "Show outbox (sent messages) instead of inbox")
	messageListCmd.Flags().String("from", "", "Filter by sender node")
	messageListCmd.Flags().String("type", "", "Filter by message type")
}

func runMessageList(cmd *cobra.Command, _ []string) error {
	outbox, _ := cmd.Flags().GetBool("outbox")
	filterFrom, _ := cmd.Flags().GetString("from")
	filterType, _ := cmd.Flags().GetString("type")

	var dir string
	if outbox {
		dir = filepath.Join(cliMessagesDir(), "outbox")
	} else {
		dir = filepath.Join(cliMessagesDir(), "inbox")
	}

	msgs, err := cliReadAllMessages(dir)
	if err != nil {
		return fmt.Errorf("failed to read messages: %w\nRecovery: ensure .forge/messages/ directory exists (run 'mkdir -p .forge/messages/inbox .forge/messages/outbox')", err)
	}

	// Apply filters.
	filtered := msgs[:0]
	for _, m := range msgs {
		if filterFrom != "" && m.From != filterFrom {
			continue
		}
		if filterType != "" && m.Type != filterType {
			continue
		}
		filtered = append(filtered, m)
	}

	if len(filtered) == 0 {
		if outbox {
			fmt.Println("Outbox is empty.")
		} else {
			fmt.Println("Inbox is empty.")
		}
		return nil
	}

	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "ID\tFROM\tTO\tTYPE\tACK\tSUBJECT\tTIMESTAMP")
	fmt.Fprintln(w, "──\t────\t──\t────\t───\t───────\t─────────")
	for _, m := range filtered {
		ack := "no"
		if m.Ack {
			ack = "yes"
		}
		ts := m.Timestamp.Format("2006-01-02 15:04")
		subject := m.Subject
		if len(subject) > 40 {
			subject = subject[:37] + "..."
		}
		fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\t%s\t%s\n",
			m.ID, m.From, m.To, m.Type, ack, subject, ts)
	}
	_ = w.Flush()
	fmt.Printf("\nTotal: %d messages\n", len(filtered))
	return nil
}

// ── Internal helpers ──────────────────────────────────────────────────────────

// cliMessagesDir returns the .forge/messages root using FORGE_ROOT or cwd.
func cliMessagesDir() string {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	return filepath.Join(root, ".forge", "messages")
}

// cliNodeID returns this node's identity from NODE_ID env or hostname.
func cliNodeID() string {
	if id := os.Getenv("NODE_ID"); id != "" {
		return id
	}
	h, err := os.Hostname()
	if err != nil {
		return "unknown"
	}
	return h
}

// cliSendMessage writes msg as a JSONL line to .forge/messages/outbox/{target}.jsonl.
func cliSendMessage(target string, msg cliMessage) error {
	outDir := filepath.Join(cliMessagesDir(), "outbox")
	if err := os.MkdirAll(outDir, 0755); err != nil {
		return fmt.Errorf("create outbox dir: %w", err)
	}

	path := filepath.Join(outDir, target+".jsonl")

	line, err := json.Marshal(msg)
	if err != nil {
		return fmt.Errorf("marshal message: %w", err)
	}

	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return fmt.Errorf("open outbox file: %w", err)
	}
	defer f.Close()

	_, err = fmt.Fprintf(f, "%s\n", line)
	return err
}

// cliReadAllMessages reads every *.jsonl file in dir, returning all messages.
func cliReadAllMessages(dir string) ([]cliMessage, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}

	var all []cliMessage
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".jsonl") {
			continue
		}
		msgs, err := cliReadJSONLFile(filepath.Join(dir, e.Name()))
		if err != nil {
			continue // skip corrupt files
		}
		all = append(all, msgs...)
	}
	return all, nil
}

// cliReadJSONLFile parses a JSONL file into []cliMessage.
func cliReadJSONLFile(path string) ([]cliMessage, error) {
	f, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	defer f.Close()

	var msgs []cliMessage
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var m cliMessage
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			continue
		}
		msgs = append(msgs, m)
	}
	return msgs, scanner.Err()
}

// init registers message subcommands and adds messageCmd to rootCmd.
func init() {
	messageCmd.AddCommand(messageSendCmd)
	messageCmd.AddCommand(messageListCmd)
}
