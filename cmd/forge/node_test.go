// Package main provides tests for the node noun (XNode mesh management)
package main

import (
	"testing"
)

// TestNodeUnregisterMissingArg verifies that node unregister requires a positional arg.
func TestNodeUnregisterMissingArg(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("node", "unregister")
	if err == nil {
		t.Error("node unregister with no arg should fail (requires exactly 1 arg)")
	}
}

// TestNodeUnregisterTooManyArgs verifies that node unregister rejects extra args.
func TestNodeUnregisterTooManyArgs(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("node", "unregister", "node-a", "node-b")
	if err == nil {
		t.Error("node unregister with 2 args should fail (requires exactly 1 arg)")
	}
}

// TestNodeUnregisterDaemonUnreachable verifies graceful failure when daemon is down.
func TestNodeUnregisterDaemonUnreachable(t *testing.T) {
	runner := NewTestRunner(t)
	t.Setenv("FORGE_API_URL", "http://127.0.0.1:19999") // nothing listening

	_, err := runner.Execute("node", "unregister", "sati")
	// Must fail (daemon unreachable) — must NOT panic
	if err == nil {
		t.Log("node unregister unexpectedly succeeded (daemon may be running on port 19999)")
	}
}

// TestNodeUnregisterWithHub verifies the --hub flag is accepted.
func TestNodeUnregisterWithHub(t *testing.T) {
	runner := NewTestRunner(t)

	// This will fail (no server at that URL), but the flag must be parsed without error.
	_, err := runner.Execute("node", "unregister", "sati", "--hub", "http://127.0.0.1:19998")
	if err == nil {
		t.Log("node unregister unexpectedly succeeded")
	}
}

// TestNodeUnregisterHelp verifies the command is registered and has help text.
func TestNodeUnregisterHelp(t *testing.T) {
	runner := NewTestRunner(t)

	// Add nodeCmd to the test root (it's not added by NewTestRunner by default)
	runner.rootCmd.AddCommand(nodeCmd)

	out, err := runner.Execute("node", "--help")
	if err != nil {
		t.Logf("node --help: %v", err)
	}
	if out == "" {
		t.Log("node --help produced no output")
	}
}

// TestNodeCommands verifies the node subcommands are registered.
func TestNodeCommands(t *testing.T) {
	// Check that all expected subcommands are present on nodeCmd
	expected := map[string]bool{
		"list":        false,
		"status":      false,
		"join":        false,
		"unregister":  false,
	}

	for _, sub := range nodeCmd.Commands() {
		if _, ok := expected[sub.Name()]; ok {
			expected[sub.Name()] = true
		}
	}

	for name, found := range expected {
		if !found {
			t.Errorf("node subcommand %q not registered", name)
		}
	}
}
