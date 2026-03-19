//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// TestReadJSONLMessages_NonExistentFile covers the os.IsNotExist branch — returns nil, nil.
func TestReadJSONLMessages_NonExistentFile(t *testing.T) {
	msgs, err := readJSONLMessages("/nonexistent-path-xyz/messages.jsonl")
	if err != nil {
		t.Errorf("expected nil error for non-existent file, got: %v", err)
	}
	if msgs != nil {
		t.Errorf("expected nil msgs for non-existent file, got: %v", msgs)
	}
}

// TestReadJSONLMessages_EmptyLines covers the empty-line skip path.
func TestReadJSONLMessages_EmptyLines(t *testing.T) {
	f, err := os.CreateTemp(t.TempDir(), "messages*.jsonl")
	if err != nil {
		t.Fatalf("create temp file: %v", err)
	}
	// Write some empty lines and one valid message.
	f.WriteString("\n\n   \n{\"id\":\"1\",\"from\":\"a\",\"to\":\"b\",\"type\":\"task\"}\n\n")
	f.Close()

	msgs, err := readJSONLMessages(f.Name())
	if err != nil {
		t.Fatalf("readJSONLMessages: %v", err)
	}
	if len(msgs) != 1 {
		t.Errorf("expected 1 message, got %d", len(msgs))
	}
}

// TestReadJSONLMessages_InvalidJSON covers the json.Unmarshal error path (silently skipped).
func TestReadJSONLMessages_InvalidJSON(t *testing.T) {
	f, err := os.CreateTemp(t.TempDir(), "messages*.jsonl")
	if err != nil {
		t.Fatalf("create temp file: %v", err)
	}
	// Mix valid and invalid JSON lines.
	f.WriteString("{invalid json}\n{\"id\":\"2\",\"from\":\"x\",\"to\":\"y\",\"type\":\"status\"}\n")
	f.Close()

	msgs, err := readJSONLMessages(f.Name())
	if err != nil {
		t.Fatalf("readJSONLMessages: %v", err)
	}
	// Invalid JSON line is skipped; only the valid one counts.
	if len(msgs) != 1 {
		t.Errorf("expected 1 valid message after skipping invalid JSON, got %d", len(msgs))
	}
}

// TestRewriteJSONL_InvalidPath covers the os.Create error path.
func TestRewriteJSONL_InvalidPath(t *testing.T) {
	err := rewriteJSONL("/nonexistent-dir-xyz/abc/messages.jsonl", nil)
	if err == nil {
		t.Error("expected error writing to non-existent directory, got nil")
	}
}

// TestRewriteJSONL_EmptyMessages verifies no error on empty message slice.
func TestRewriteJSONL_EmptyMessages(t *testing.T) {
	path := filepath.Join(t.TempDir(), "out.jsonl")
	err := rewriteJSONL(path, nil)
	if err != nil {
		t.Errorf("rewriteJSONL with empty slice: %v", err)
	}
}

// TestRewriteJSONL_WithMessages verifies messages are written and re-readable.
func TestRewriteJSONL_WithMessages(t *testing.T) {
	path := filepath.Join(t.TempDir(), "out.jsonl")
	msgs := []Message{
		{ID: "m1", From: "a", To: "b", Type: MsgTypeTask, Subject: "test", Body: "body"},
	}
	if err := rewriteJSONL(path, msgs); err != nil {
		t.Fatalf("rewriteJSONL: %v", err)
	}
	read, err := readJSONLMessages(path)
	if err != nil {
		t.Fatalf("readJSONLMessages after rewrite: %v", err)
	}
	if len(read) != 1 || read[0].ID != "m1" {
		t.Errorf("expected 1 message with ID 'm1', got: %v", read)
	}
}

// setForgeRoot temporarily sets FORGE_ROOT to a temp dir for message relay tests.
func setForgeRoot(t *testing.T) (root string, restore func()) {
	t.Helper()
	root = t.TempDir()
	old := os.Getenv("FORGE_ROOT")
	os.Setenv("FORGE_ROOT", root)
	return root, func() {
		os.Setenv("FORGE_ROOT", old)
	}
}

// TestAckMessage_InboxDirNotFound covers the os.IsNotExist path in AckMessage
// (message_relay.go:172.16,173.25 and 173.25,175.4) when inbox does not exist.
func TestAckMessage_InboxDirNotFound(t *testing.T) {
	_, restore := setForgeRoot(t)
	defer restore()

	// FORGE_ROOT is a fresh temp dir — inbox dir doesn't exist.
	err := AckMessage("nonexistent-msg-id")
	// Should return "inbox dir not found" error (os.IsNotExist branch).
	if err == nil {
		t.Error("expected error when inbox dir does not exist, got nil")
	}
}

// TestAckMessage_MessageNotFound covers the "message not found" error path
// (message_relay.go:206.2,206.74) when no inbox file contains the requested ID.
func TestAckMessage_MessageNotFound(t *testing.T) {
	root, restore := setForgeRoot(t)
	defer restore()

	// Create inbox dir with one message file containing a different ID.
	inboxDir := filepath.Join(root, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inboxDir, 0755); err != nil {
		t.Fatalf("create inbox dir: %v", err)
	}
	msg := Message{
		ID:        "other-msg-id",
		From:      "node-a",
		To:        "node-b",
		Timestamp: time.Now(),
		Type:      MsgTypeTask,
		Subject:   "test",
		Body:      "body",
		Ack:       false,
	}
	line, _ := json.Marshal(msg)
	if err := os.WriteFile(filepath.Join(inboxDir, "node-a.jsonl"), append(line, '\n'), 0644); err != nil {
		t.Fatalf("write inbox file: %v", err)
	}

	// Request a different message ID — should return "not found" error.
	err := AckMessage("nonexistent-msg-id")
	if err == nil {
		t.Error("expected error for missing message ID, got nil")
	}
}

// TestAckMessage_Success covers the found=true + rewriteJSONL path
// (message_relay.go:197.13,198.12 and 201.50,203.4).
func TestAckMessage_Success(t *testing.T) {
	root, restore := setForgeRoot(t)
	defer restore()

	inboxDir := filepath.Join(root, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inboxDir, 0755); err != nil {
		t.Fatalf("create inbox dir: %v", err)
	}
	msg := Message{
		ID:        "ack-test-msg-id",
		From:      "node-a",
		To:        "node-b",
		Timestamp: time.Now(),
		Type:      MsgTypeTask,
		Subject:   "test subject",
		Body:      "test body",
		Ack:       false,
	}
	line, _ := json.Marshal(msg)
	inboxFile := filepath.Join(inboxDir, "node-a.jsonl")
	if err := os.WriteFile(inboxFile, append(line, '\n'), 0644); err != nil {
		t.Fatalf("write inbox file: %v", err)
	}

	// AckMessage should find the message and mark it as acked.
	if err := AckMessage("ack-test-msg-id"); err != nil {
		t.Fatalf("AckMessage: %v", err)
	}

	// Verify the message is now marked as acked.
	msgs, err := readJSONLMessages(inboxFile)
	if err != nil {
		t.Fatalf("readJSONLMessages: %v", err)
	}
	if len(msgs) != 1 {
		t.Fatalf("expected 1 message, got %d", len(msgs))
	}
	if !msgs[0].Ack {
		t.Error("expected message to be marked as acked, got ack=false")
	}
}

// TestSendMessage_Success exercises SendMessage's happy path
// (message_relay.go:82.82 through 126.2).
func TestSendMessage_Success(t *testing.T) {
	root, restore := setForgeRoot(t)
	defer restore()

	err := SendMessage("target-node", MsgTypeTask, "test subject", "test body")
	if err != nil {
		t.Fatalf("SendMessage: %v", err)
	}

	// Verify the message was written to the outbox.
	outboxPath := filepath.Join(root, ".forge", "messages", "outbox", "target-node.jsonl")
	msgs, err := readJSONLMessages(outboxPath)
	if err != nil {
		t.Fatalf("readJSONLMessages: %v", err)
	}
	if len(msgs) != 1 {
		t.Fatalf("expected 1 message in outbox, got %d", len(msgs))
	}
	if msgs[0].To != "target-node" {
		t.Errorf("expected To=target-node, got %s", msgs[0].To)
	}
}

// TestReadInbox_EmptyInbox covers the ReadInbox with no messages
// (message_relay.go:131.37 - inbox dir not exist returns nil,nil).
func TestReadInbox_EmptyInbox(t *testing.T) {
	_, restore := setForgeRoot(t)
	defer restore()

	// No inbox dir → should return nil, nil (os.IsNotExist path).
	msgs, err := ReadInbox()
	if err != nil {
		t.Errorf("expected nil error for missing inbox, got: %v", err)
	}
	if msgs != nil {
		t.Errorf("expected nil msgs for missing inbox, got: %v", msgs)
	}
}

// TestReadInbox_WithMessages covers the ReadInbox with messages in inbox
// (message_relay.go:144.2 through 161.2, exercising the scan/filter loop).
func TestReadInbox_WithMessages(t *testing.T) {
	root, restore := setForgeRoot(t)
	defer restore()

	inboxDir := filepath.Join(root, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inboxDir, 0755); err != nil {
		t.Fatalf("create inbox dir: %v", err)
	}

	// One unacked message and one acked message.
	msgs := []Message{
		{ID: "msg-1", From: "n1", To: "n2", Timestamp: time.Now(), Type: MsgTypeTask, Ack: false},
		{ID: "msg-2", From: "n1", To: "n2", Timestamp: time.Now(), Type: MsgTypeStatus, Ack: true},
	}
	for _, m := range msgs {
		line, _ := json.Marshal(m)
		path := filepath.Join(inboxDir, m.From+".jsonl")
		f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
		if err != nil {
			t.Fatalf("open inbox file: %v", err)
		}
		f.Write(append(line, '\n'))
		f.Close()
	}

	// ReadInbox should return only unacked messages.
	unacked, err := ReadInbox()
	if err != nil {
		t.Fatalf("ReadInbox: %v", err)
	}
	// msg-1 is unacked, msg-2 is acked (filtered out).
	if len(unacked) != 1 {
		t.Errorf("expected 1 unacked message, got %d: %v", len(unacked), unacked)
	}
	if len(unacked) > 0 && unacked[0].ID != "msg-1" {
		t.Errorf("expected msg-1, got %s", unacked[0].ID)
	}
}

// TestAckMessage_SkipsDirectories exercises the e.IsDir filter
// (message_relay.go:180.58,181.12) when inbox contains a subdirectory.
func TestAckMessage_SkipsDirectories(t *testing.T) {
	root, restore := setForgeRoot(t)
	defer restore()

	inboxDir := filepath.Join(root, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inboxDir, 0755); err != nil {
		t.Fatalf("create inbox dir: %v", err)
	}

	// Create a subdirectory inside inbox — it should be skipped.
	subDir := filepath.Join(inboxDir, "subdir")
	if err := os.MkdirAll(subDir, 0755); err != nil {
		t.Fatalf("create subdir: %v", err)
	}

	// No .jsonl files → message not found.
	err := AckMessage("any-msg-id")
	if err == nil {
		t.Error("expected error when no JSONL files in inbox, got nil")
	}
}

// ensure time import is used
var _ = time.Now
