//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"strings"
	"testing"
)

// --- OpenPostgresDB tests ---

func TestOpenPostgresDB_NoServer_W22(t *testing.T) {
	// OpenPostgresDB uses env vars and tries to connect via pgx driver.
	// Without a running postgres server, this should return an error (not panic).
	db, err := OpenPostgresDB()
	if err == nil {
		db.Close()
		// If somehow postgres is running, that's fine too
		t.Log("Postgres connection succeeded (server running?)")
	} else {
		// Expected: error because no postgres server
		t.Logf("OpenPostgresDB returned error (expected): %v", err)
	}
}

// --- CompletionManager tests ---

func TestCompletionManager_GenerateBash_W22(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	err := m.GenerateBash(&buf)
	if err != nil {
		t.Fatalf("GenerateBash failed: %v", err)
	}
	if !strings.Contains(buf.String(), "complete -F _forge_completion forge") {
		t.Error("expected bash completion script to contain completion command")
	}
}

func TestCompletionManager_GenerateZsh_W22(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	err := m.GenerateZsh(&buf)
	if err != nil {
		t.Fatalf("GenerateZsh failed: %v", err)
	}
	if !strings.Contains(buf.String(), "#compdef forge") {
		t.Error("expected zsh completion script to contain #compdef")
	}
}

func TestCompletionManager_GenerateFish_W22(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	err := m.GenerateFish(&buf)
	if err != nil {
		t.Fatalf("GenerateFish failed: %v", err)
	}
	if !strings.Contains(buf.String(), "complete -c forge") {
		t.Error("expected fish completion script to contain complete command")
	}
}

// --- detectTailscaleIP tests ---

func TestDetectTailscaleIP_W22(t *testing.T) {
	// detectTailscaleIP calls `tailscale ip -4` and returns the IP or empty string.
	// It should never panic, just return a string.
	ip := detectTailscaleIP()
	// Result can be empty (tailscale not installed) or an IP address
	t.Logf("detectTailscaleIP returned: %q", ip)
	// Just verify it didn't panic and returned a string (possibly empty)
	_ = ip
}

// --- announceNodeToPeer tests ---

func TestAnnounceNodeToPeer_InvalidURL_W22(t *testing.T) {
	// announceNodeToPeer logs errors and returns without panic on invalid URL
	// This function doesn't return a value, just logs
	self := Node{
		ID:      "test-node",
		Address: "127.0.0.1:8081",
	}

	// Call with invalid URL - should log error but not panic
	announceNodeToPeer("not-a-valid-url", self)
	// Also test with empty URL
	announceNodeToPeer("", self)
	// Test with unreachable URL
	announceNodeToPeer("http://127.0.0.1:99999", self)
	// If we reach here, no panic occurred
}

// --- SendBootstrap tests ---

func TestSendBootstrap_NonExistentAgent_W22(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// SendBootstrap to a non-existent agent should log but not panic
	hub.SendBootstrap("non-existent-agent", "task-123", map[string]string{"key": "value"})
	// If we reach here without panic, the test passes
}

func TestSendBootstrap_ValidEnvelope_W22(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	envelope := map[string]interface{}{
		"domain":  "test-domain",
		"project": "test-project",
		"context": "some context data",
	}

	// Send to non-existent agent (won't deliver, but shouldn't panic)
	hub.SendBootstrap("ghost-agent", "task-456", envelope)
	// Reaching here means no panic
}
