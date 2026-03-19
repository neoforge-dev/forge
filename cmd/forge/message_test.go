// Package main — tests for the forge message command.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// setupMessageTestDir creates a temp directory, sets FORGE_ROOT, and returns
// the directory path.  The test's cleanup func resets FORGE_ROOT.
func setupMessageTestDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	prev := os.Getenv("FORGE_ROOT")
	t.Setenv("FORGE_ROOT", dir)
	t.Cleanup(func() {
		os.Setenv("FORGE_ROOT", prev)
	})
	return dir
}

// ── forge message send ────────────────────────────────────────────────────────

func TestMessageSendCreatesOutboxFile(t *testing.T) {
	dir := setupMessageTestDir(t)

	msg := cliMessage{
		ID:      "test-001",
		From:    "prya",
		To:      "sati",
		Type:    "task",
		Subject: "Hello sati",
		Body:    "Do the thing",
	}

	if err := cliSendMessage("sati", msg); err != nil {
		t.Fatalf("cliSendMessage: %v", err)
	}

	path := filepath.Join(dir, ".forge", "messages", "outbox", "sati.jsonl")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read outbox file: %v", err)
	}

	var got cliMessage
	if err := json.Unmarshal([]byte(strings.TrimSpace(string(data))), &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got.To != "sati" {
		t.Errorf("expected To=sati, got %q", got.To)
	}
	if got.Subject != "Hello sati" {
		t.Errorf("expected Subject='Hello sati', got %q", got.Subject)
	}
}

func TestMessageSendAppendsMultipleMessages(t *testing.T) {
	dir := setupMessageTestDir(t)

	for i := 0; i < 5; i++ {
		msg := cliMessage{
			ID:      fmt.Sprintf("msg-%d", i),
			From:    "prya",
			To:      "nova",
			Type:    "directive",
			Subject: fmt.Sprintf("Directive %d", i),
		}
		if err := cliSendMessage("nova", msg); err != nil {
			t.Fatalf("cliSendMessage %d: %v", i, err)
		}
	}

	path := filepath.Join(dir, ".forge", "messages", "outbox", "nova.jsonl")
	msgs, err := cliReadJSONLFile(path)
	if err != nil {
		t.Fatalf("cliReadJSONLFile: %v", err)
	}
	if len(msgs) != 5 {
		t.Errorf("expected 5 messages, got %d", len(msgs))
	}
}

// ── forge message list ────────────────────────────────────────────────────────

func TestMessageListInboxEmpty(t *testing.T) {
	dir := setupMessageTestDir(t)

	inDir := filepath.Join(dir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatal(err)
	}

	msgs, err := cliReadAllMessages(inDir)
	if err != nil {
		t.Fatalf("cliReadAllMessages on empty dir: %v", err)
	}
	if len(msgs) != 0 {
		t.Errorf("expected 0 messages in empty inbox, got %d", len(msgs))
	}
}

func TestMessageListReadsInbox(t *testing.T) {
	dir := setupMessageTestDir(t)

	inDir := filepath.Join(dir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Write two messages from different source files.
	sources := map[string]cliMessage{
		"sati": {ID: "s1", From: "sati", To: "prya", Type: "result", Subject: "Done"},
		"nova": {ID: "n1", From: "nova", To: "prya", Type: "status", Subject: "Online"},
	}
	for src, m := range sources {
		path := filepath.Join(inDir, src+".jsonl")
		line, _ := json.Marshal(m)
		if err := os.WriteFile(path, []byte(string(line)+"\n"), 0644); err != nil {
			t.Fatal(err)
		}
	}

	msgs, err := cliReadAllMessages(inDir)
	if err != nil {
		t.Fatalf("cliReadAllMessages: %v", err)
	}
	if len(msgs) != 2 {
		t.Errorf("expected 2 inbox messages, got %d", len(msgs))
	}
}

func TestMessageListOutbox(t *testing.T) {
	dir := setupMessageTestDir(t)

	// Send two messages to different targets.
	m1 := cliMessage{ID: "o1", From: "prya", To: "sati", Type: "task", Subject: "Task 1"}
	m2 := cliMessage{ID: "o2", From: "prya", To: "nova", Type: "directive", Subject: "Directive 1"}
	_ = cliSendMessage("sati", m1)
	_ = cliSendMessage("nova", m2)

	outDir := filepath.Join(dir, ".forge", "messages", "outbox")
	msgs, err := cliReadAllMessages(outDir)
	if err != nil {
		t.Fatalf("cliReadAllMessages: %v", err)
	}
	if len(msgs) != 2 {
		t.Errorf("expected 2 outbox messages, got %d", len(msgs))
	}
}

// ── CLI command surface ───────────────────────────────────────────────────────

func TestMessageCommandRegistered(t *testing.T) {
	found := false
	for _, c := range rootCmd.Commands() {
		if c.Use == "message" {
			found = true
			break
		}
	}
	if !found {
		t.Error("expected 'message' command to be registered on rootCmd")
	}
}

func TestMessageSendMissingRequired(t *testing.T) {
	runner := NewTestRunnerWithMessage(t)

	// Missing --to and --subject
	_, err := runner.Execute("message", "send")
	if err == nil {
		t.Error("expected error when --to and --subject are missing")
	}
}

func TestMessageSendInvalidType(t *testing.T) {
	setupMessageTestDir(t)
	runner := NewTestRunnerWithMessage(t)

	_, err := runner.Execute("message", "send",
		"--to", "sati",
		"--type", "invalid-type",
		"--subject", "Test")
	if err == nil {
		t.Error("expected error for invalid message type")
	}
}

func TestMessageListCommand(t *testing.T) {
	setupMessageTestDir(t)
	runner := NewTestRunnerWithMessage(t)

	// Should not error on empty inbox
	_, err := runner.Execute("message", "list")
	if err != nil {
		t.Errorf("message list on empty inbox: %v", err)
	}
}

func TestMessageListOutboxFlag(t *testing.T) {
	setupMessageTestDir(t)
	runner := NewTestRunnerWithMessage(t)

	_, err := runner.Execute("message", "list", "--outbox")
	if err != nil {
		t.Errorf("message list --outbox on empty dir: %v", err)
	}
}

// ── Helpers ───────────────────────────────────────────────────────────────────

// NewTestRunnerWithMessage is like NewTestRunner but adds the messageCmd.
func NewTestRunnerWithMessage(t *testing.T) *TestRunner {
	t.Helper()
	runner := NewTestRunner(t)
	runner.rootCmd.AddCommand(messageCmd)
	return runner
}
