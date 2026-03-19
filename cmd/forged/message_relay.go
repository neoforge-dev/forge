//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// MessageType enumerates the valid cross-node message types.
type MessageType string

const (
	MsgTypeTask      MessageType = "task"
	MsgTypeStatus    MessageType = "status"
	MsgTypeResearch  MessageType = "research"
	MsgTypeResult    MessageType = "result"
	MsgTypeDirective MessageType = "directive"
	MsgTypeAck       MessageType = "ack"
)

// maxMessagesPerFile is the rotation threshold for outbox files.
const maxMessagesPerFile = 100

// Message is a single cross-node message written to a JSONL file.
// One JSON object per line; no trailing comma.
type Message struct {
	ID        string      `json:"id"`
	From      string      `json:"from"`
	To        string      `json:"to"`
	Timestamp time.Time   `json:"timestamp"`
	Type      MessageType `json:"type"`
	Subject   string      `json:"subject"`
	Body      string      `json:"body"`
	Ack       bool        `json:"ack"`
}

// messageRelayMu guards file I/O so concurrent patrol ticks don't interleave writes.
var messageRelayMu sync.Mutex

// messagesDir returns the root .forge/messages path, honouring FORGE_ROOT.
func messagesDir() string {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	return filepath.Join(root, ".forge", "messages")
}

// outboxPath returns the path to the outbox file for target node.
func outboxPath(target string) string {
	return filepath.Join(messagesDir(), "outbox", target+".jsonl")
}

// inboxPath returns the path to the inbox file for source node.
func inboxPath(source string) string {
	return filepath.Join(messagesDir(), "inbox", source+".jsonl")
}

// nodeID returns this node's identity: NODE_ID env or hostname.
func nodeID() string {
	if id := os.Getenv("NODE_ID"); id != "" {
		return id
	}
	h, err := os.Hostname()
	if err != nil {
		return "unknown"
	}
	return h
}

// SendMessage writes a message to .forge/messages/outbox/{target}.jsonl.
// If the file already contains maxMessagesPerFile entries it is rotated
// (renamed with a timestamp suffix) before the new message is appended.
func SendMessage(target string, msgType MessageType, subject, body string) error {
	messageRelayMu.Lock()
	defer messageRelayMu.Unlock()

	outDir := filepath.Join(messagesDir(), "outbox")
	if err := os.MkdirAll(outDir, 0755); err != nil {
		return fmt.Errorf("message-relay: create outbox dir: %w", err)
	}

	path := outboxPath(target)

	// Rotation: count existing lines to enforce the per-file limit.
	if count, err := countJSONLLines(path); err == nil && count >= maxMessagesPerFile {
		rotated := fmt.Sprintf("%s.%d", path, time.Now().UnixMilli())
		if rerr := os.Rename(path, rotated); rerr != nil {
			return fmt.Errorf("message-relay: rotate outbox: %w", rerr)
		}
	}

	msg := Message{
		ID:        generateMsgID(),
		From:      nodeID(),
		To:        target,
		Timestamp: time.Now().UTC(),
		Type:      msgType,
		Subject:   subject,
		Body:      body,
		Ack:       false,
	}

	line, err := json.Marshal(msg)
	if err != nil {
		return fmt.Errorf("message-relay: marshal message: %w", err)
	}

	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return fmt.Errorf("message-relay: open outbox: %w", err)
	}
	defer f.Close()

	if _, err := fmt.Fprintf(f, "%s\n", line); err != nil {
		return fmt.Errorf("message-relay: write outbox: %w", err)
	}
	return nil
}

// ReadInbox reads all unprocessed (ack=false) messages from every file in
// .forge/messages/inbox/.  Returns an empty slice when the inbox is empty.
func ReadInbox() ([]Message, error) {
	messageRelayMu.Lock()
	defer messageRelayMu.Unlock()

	inDir := filepath.Join(messagesDir(), "inbox")
	entries, err := os.ReadDir(inDir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("message-relay: read inbox dir: %w", err)
	}

	var msgs []Message
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".jsonl") {
			continue
		}
		path := filepath.Join(inDir, e.Name())
		batch, err := readJSONLMessages(path)
		if err != nil {
			// Log and continue — a corrupt file must not block other inboxes.
			continue
		}
		for _, m := range batch {
			if !m.Ack {
				msgs = append(msgs, m)
			}
		}
	}
	return msgs, nil
}

// AckMessage marks the message with msgID as acknowledged in whichever inbox
// file contains it.  It rewrites the file atomically (write-to-tmp + rename).
func AckMessage(msgID string) error {
	messageRelayMu.Lock()
	defer messageRelayMu.Unlock()

	inDir := filepath.Join(messagesDir(), "inbox")
	entries, err := os.ReadDir(inDir)
	if err != nil {
		if os.IsNotExist(err) {
			return fmt.Errorf("message-relay: inbox dir not found")
		}
		return fmt.Errorf("message-relay: read inbox dir: %w", err)
	}

	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".jsonl") {
			continue
		}
		path := filepath.Join(inDir, e.Name())
		msgs, err := readJSONLMessages(path)
		if err != nil {
			continue
		}

		found := false
		for i := range msgs {
			if msgs[i].ID == msgID {
				msgs[i].Ack = true
				found = true
				break
			}
		}
		if !found {
			continue
		}

		if err := rewriteJSONL(path, msgs); err != nil {
			return fmt.Errorf("message-relay: rewrite inbox: %w", err)
		}
		return nil
	}
	return fmt.Errorf("message-relay: message %q not found in inbox", msgID)
}

// ── Internal helpers ─────────────────────────────────────────────────────────

// generateMsgID produces a simple timestamp-based ID.  The relay does not
// require ULIDs — timestamp precision is sufficient for dedup.
func generateMsgID() string {
	return fmt.Sprintf("msg-%d", time.Now().UnixNano())
}

// readJSONLMessages parses every line of a JSONL file into []Message.
// Blank lines and lines that fail to parse are silently skipped so that a
// single corrupt entry does not break the whole file.
func readJSONLMessages(path string) ([]Message, error) {
	f, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	defer f.Close()

	var msgs []Message
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var m Message
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			continue
		}
		msgs = append(msgs, m)
	}
	return msgs, scanner.Err()
}

// rewriteJSONL atomically overwrites path with the serialised slice.
func rewriteJSONL(path string, msgs []Message) error {
	tmp := path + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}

	for _, m := range msgs {
		line, err := json.Marshal(m)
		if err != nil {
			f.Close()
			os.Remove(tmp)
			return err
		}
		fmt.Fprintf(f, "%s\n", line)
	}
	if err := f.Close(); err != nil {
		os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, path)
}

// countJSONLLines counts non-blank lines in a JSONL file.
// Returns 0 if the file does not exist.
func countJSONLLines(path string) (int, error) {
	f, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return 0, nil
		}
		return 0, err
	}
	defer f.Close()

	count := 0
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		if strings.TrimSpace(scanner.Text()) != "" {
			count++
		}
	}
	return count, scanner.Err()
}
