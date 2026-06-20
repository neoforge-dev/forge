//go:build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestNotifyCommandRegistered verifies that notifyCmd is registered on rootCmd.
func TestNotifyCommandRegistered(t *testing.T) {
	for _, sub := range rootCmd.Commands() {
		if sub.Use == "notify" {
			return
		}
	}
	t.Error("notifyCmd not found in rootCmd.Commands()")
}

// TestNotifySubcommands verifies all required subcommands are present.
func TestNotifySubcommands(t *testing.T) {
	required := []string{"status", "gates", "fleet", "tasks", "daily", "alert", "test"}

	found := map[string]bool{}
	for _, sub := range notifyCmd.Commands() {
		found[sub.Use] = true
	}

	// alert has args in its Use field: "alert [message]"
	// Normalise by checking prefix.
	for _, name := range required {
		matched := false
		for use := range found {
			if use == name || strings.HasPrefix(use, name+" ") {
				matched = true
				break
			}
		}
		if !matched {
			t.Errorf("notify subcommand %q not found", name)
		}
	}
}

// TestNotifyTargetResolution_EnvVar verifies FORGE_TELEGRAM_CHAT_ID is used first.
func TestNotifyTargetResolution_EnvVar(t *testing.T) {
	t.Setenv("FORGE_TELEGRAM_CHAT_ID", "12345678")

	target := resolveNotifyTarget()
	if target != "12345678" {
		t.Errorf("expected 12345678 from env var, got %q", target)
	}
}

// TestNotifyTargetResolution_ConfigFile verifies fallback to ~/.openclaw/openclaw.json.
func TestNotifyTargetResolution_ConfigFile(t *testing.T) {
	// Ensure env var is not set.
	t.Setenv("FORGE_TELEGRAM_CHAT_ID", "")

	// Build a temp home dir with a config file.
	tmpHome := t.TempDir()
	ocDir := filepath.Join(tmpHome, ".openclaw")
	if err := os.MkdirAll(ocDir, 0o755); err != nil {
		t.Fatal(err)
	}

	cfg := map[string]interface{}{
		"channels": map[string]interface{}{
			"telegram": map[string]interface{}{
				"allowFrom": []string{"98765432"},
			},
		},
	}
	data, _ := json.Marshal(cfg)
	if err := os.WriteFile(filepath.Join(ocDir, "openclaw.json"), data, 0o644); err != nil {
		t.Fatal(err)
	}

	t.Setenv("HOME", tmpHome)

	target := resolveNotifyTarget()
	if target != "98765432" {
		t.Errorf("expected 98765432 from config file, got %q", target)
	}
}

// TestNotifyTargetResolution_NoConfig verifies empty string when nothing is configured.
func TestNotifyTargetResolution_NoConfig(t *testing.T) {
	t.Setenv("FORGE_TELEGRAM_CHAT_ID", "")
	t.Setenv("HOME", t.TempDir()) // empty home, no .openclaw dir

	target := resolveNotifyTarget()
	if target != "" {
		t.Errorf("expected empty target when nothing configured, got %q", target)
	}
}

// TestNotifyDryRun verifies --dry-run prints message without calling openclaw.
func TestNotifyDryRun(t *testing.T) {
	// Capture stdout.
	pr, pw, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	oldStdout := os.Stdout
	os.Stdout = pw
	defer func() { os.Stdout = oldStdout }()

	var buf bytes.Buffer
	done := make(chan struct{})
	go func() {
		_, _ = io.Copy(&buf, pr)
		close(done)
	}()

	// Call dispatchNotify with dry-run flag set.
	cmd := notifyTestCmd
	cmd.Flags().Set("dry-run", "true")
	cmd.Flags().Set("quiet", "false")

	err = dispatchNotify(cmd, "test dry-run message")
	pw.Close()
	<-done

	// Reset flag for other tests.
	cmd.Flags().Set("dry-run", "false")

	if err != nil {
		t.Fatalf("dispatchNotify returned error: %v", err)
	}

	output := buf.String()
	if !strings.Contains(output, "dry-run") {
		t.Errorf("expected [dry-run] in output, got:\n%s", output)
	}
	if !strings.Contains(output, "test dry-run message") {
		t.Errorf("expected message content in output, got:\n%s", output)
	}
}

// TestNotifyAlertRequiresMessage verifies alert without args returns usage error.
func TestNotifyAlertRequiresMessage(t *testing.T) {
	err := notifyAlertCmd.Args(notifyAlertCmd, []string{})
	if err == nil {
		t.Error("expected error when no message args provided to alert subcommand")
	}
}

// TestNotifyDryRunQuiet verifies --dry-run --quiet produces no output.
func TestNotifyDryRunQuiet(t *testing.T) {
	pr, pw, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	oldStdout := os.Stdout
	os.Stdout = pw
	defer func() { os.Stdout = oldStdout }()

	var buf bytes.Buffer
	done := make(chan struct{})
	go func() {
		_, _ = io.Copy(&buf, pr)
		close(done)
	}()

	cmd := notifyTestCmd
	cmd.Flags().Set("dry-run", "true")
	cmd.Flags().Set("quiet", "true")

	err = dispatchNotify(cmd, "quiet message")
	pw.Close()
	<-done

	// Reset flags.
	cmd.Flags().Set("dry-run", "false")
	cmd.Flags().Set("quiet", "false")

	if err != nil {
		t.Fatalf("dispatchNotify quiet dry-run returned error: %v", err)
	}
	if buf.Len() != 0 {
		t.Errorf("expected no output with --quiet, got: %q", buf.String())
	}
}

// TestNotifyMissingTargetError verifies a helpful error is returned when no target is set.
func TestNotifyMissingTargetError(t *testing.T) {
	t.Setenv("FORGE_TELEGRAM_CHAT_ID", "")
	t.Setenv("HOME", t.TempDir())

	cmd := notifyTestCmd
	// dry-run=false so it will attempt to send; target is empty.
	cmd.Flags().Set("dry-run", "false")
	cmd.Flags().Set("target", "")

	err := dispatchNotify(cmd, "test message")
	if err == nil {
		// If openclaw is on PATH, it might try to send. We only fail if the
		// error message is wrong — both "no target" and "openclaw not found" are valid.
		return
	}

	msg := err.Error()
	if !strings.Contains(msg, "target") && !strings.Contains(msg, "openclaw") {
		t.Errorf("expected error to mention target or openclaw config, got: %q", msg)
	}
}

// TestBuildStatusMessage_WithMockServer verifies buildStatusMessage returns a non-empty string
// when the control plane is reachable.
func TestBuildStatusMessage_WithMockServer(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/health":
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"status":"ok"}`))
		case "/api/agents":
			_, _ = w.Write([]byte(`{"agents":[{"agent_id":"forge:kimi","status":"online","context_pct":42.0,"node":"sati","last_seen":"` + time.Now().UTC().Format(time.RFC3339) + `"}]}`))
		case "/api/tasks":
			_, _ = w.Write([]byte(`{"count":3,"tasks":[{"status":"queued","updated_at":"` + time.Now().UTC().Format(time.RFC3339) + `"},{"status":"completed","updated_at":"` + time.Now().UTC().Format(time.RFC3339) + `"},{"status":"running","updated_at":"` + time.Now().UTC().Format(time.RFC3339) + `"}]}`))
		case "/api/patrols":
			_, _ = w.Write([]byte(`{"count":2,"patrols":[]}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	t.Setenv("FORGE_API_URL", server.URL)

	msg, err := buildStatusMessage()
	if err != nil {
		t.Fatalf("buildStatusMessage returned error: %v", err)
	}
	if msg == "" {
		t.Error("expected non-empty status message")
	}
	if !strings.Contains(msg, "FORGE Status") {
		t.Errorf("expected 'FORGE Status' in message, got:\n%s", msg)
	}
	if !strings.Contains(msg, "online") {
		t.Errorf("expected 'online' in message, got:\n%s", msg)
	}
}

// TestBuildStatusMessage_OfflineControlPlane verifies offline control plane is reported.
func TestBuildStatusMessage_OfflineControlPlane(t *testing.T) {
	// Point at a port that refuses connections.
	t.Setenv("FORGE_API_URL", "http://127.0.0.1:19999")

	msg, err := buildStatusMessage()
	// Should not error — offline is a valid state to report.
	if err != nil {
		t.Fatalf("buildStatusMessage should not error on offline control plane: %v", err)
	}
	if !strings.Contains(msg, "OFFLINE") {
		t.Errorf("expected 'OFFLINE' in status message, got:\n%s", msg)
	}
}

// TestBuildGatesMessage_NoContextDir verifies graceful error when context dir is missing.
func TestBuildGatesMessage_NoContextDir(t *testing.T) {
	t.Setenv("FORGE_ROOT", filepath.Join(t.TempDir(), "nonexistent"))

	_, err := buildGatesMessage()
	if err == nil {
		t.Error("expected error when context dir is missing")
	}
}

// TestBuildGatesMessage_NoGates verifies message when all gates are resolved.
func TestBuildGatesMessage_NoGates(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("FORGE_ROOT", tmp)

	// Create minimal context structure with no gates.
	ctxDir := filepath.Join(tmp, ".forge", "context", "forge")
	if err := os.MkdirAll(ctxDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(ctxDir, "lead-context.md"), []byte("# FORGE\nNo gates here.\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	msg, err := buildGatesMessage()
	if err != nil {
		t.Fatalf("buildGatesMessage returned error: %v", err)
	}
	if !strings.Contains(msg, "resolved") {
		t.Errorf("expected 'resolved' message when no gates, got:\n%s", msg)
	}
}

// TestBuildGatesMessage_WithPendingGates verifies pending gates appear in message.
func TestBuildGatesMessage_WithPendingGates(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("FORGE_ROOT", tmp)

	ctxDir := filepath.Join(tmp, ".forge", "context", "brandfocus-ai")
	if err := os.MkdirAll(ctxDir, 0o755); err != nil {
		t.Fatal(err)
	}
	content := "- GATE-VC (15 min) — railway deploy\n- GATE-C (30 min) — App Store Connect\n"
	if err := os.WriteFile(filepath.Join(ctxDir, "lead-context.md"), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	forgeDir := filepath.Join(tmp, ".forge", "context", "forge")
	if err := os.MkdirAll(forgeDir, 0o755); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(forgeDir, "lead-context.md"), []byte("# FORGE\n"), 0o644)

	msg, err := buildGatesMessage()
	if err != nil {
		t.Fatalf("buildGatesMessage returned error: %v", err)
	}
	if !strings.Contains(msg, "GATE-VC") {
		t.Errorf("expected GATE-VC in gates message, got:\n%s", msg)
	}
	if !strings.Contains(msg, "GATE-C") {
		t.Errorf("expected GATE-C in gates message, got:\n%s", msg)
	}
}

// TestStatusEmoji verifies correct icons for known statuses.
func TestStatusEmoji(t *testing.T) {
	cases := []struct {
		status string
		want   string
	}{
		{"queued", "[ ]"},
		{"pending", "[ ]"},
		{"running", "[>]"},
		{"assigned", "[>]"},
		{"completed", "[x]"},
		{"done", "[x]"},
		{"failed", "[!]"},
		{"abandoned", "[!]"},
		{"unknown", "[-]"},
	}

	for _, tc := range cases {
		got := statusEmoji(tc.status)
		if got != tc.want {
			t.Errorf("statusEmoji(%q) = %q, want %q", tc.status, got, tc.want)
		}
	}
}

// TestNotifyAlertCmd_BuildsCorrectMessage verifies the alert message format.
func TestNotifyAlertCmd_BuildsCorrectMessage(t *testing.T) {
	pr, pw, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	oldStdout := os.Stdout
	os.Stdout = pw
	defer func() { os.Stdout = oldStdout }()

	var buf bytes.Buffer
	done := make(chan struct{})
	go func() {
		_, _ = io.Copy(&buf, pr)
		close(done)
	}()

	// Use dry-run so we don't call openclaw.
	notifyAlertCmd.Flags().Set("dry-run", "true")
	notifyAlertCmd.Flags().Set("quiet", "false")
	defer func() {
		notifyAlertCmd.Flags().Set("dry-run", "false")
		notifyAlertCmd.Flags().Set("quiet", "false")
	}()

	err = runNotifyAlert(notifyAlertCmd, []string{"Deploy", "complete"})
	pw.Close()
	<-done

	if err != nil {
		t.Fatalf("runNotifyAlert returned error: %v", err)
	}

	output := buf.String()
	hostname, _ := os.Hostname()
	if !strings.Contains(output, "["+hostname+"]") {
		t.Errorf("expected node marker in output, got:\n%s", output)
	}
	if !strings.Contains(output, "Deploy complete") {
		t.Errorf("expected alert text in output, got:\n%s", output)
	}
}

// TestNotifyTestCmd_BuildsCorrectMessage verifies the test message format.
func TestNotifyTestCmd_BuildsCorrectMessage(t *testing.T) {
	pr, pw, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	oldStdout := os.Stdout
	os.Stdout = pw
	defer func() { os.Stdout = oldStdout }()

	var buf bytes.Buffer
	done := make(chan struct{})
	go func() {
		_, _ = io.Copy(&buf, pr)
		close(done)
	}()

	notifyTestCmd.Flags().Set("dry-run", "true")
	notifyTestCmd.Flags().Set("quiet", "false")
	defer func() {
		notifyTestCmd.Flags().Set("dry-run", "false")
		notifyTestCmd.Flags().Set("quiet", "false")
	}()

	err = runNotifyTest(notifyTestCmd, nil)
	pw.Close()
	<-done

	if err != nil {
		t.Fatalf("runNotifyTest returned error: %v", err)
	}

	output := buf.String()
	if !strings.Contains(output, "FORGE notification test") {
		t.Errorf("expected 'FORGE notification test' in output, got:\n%s", output)
	}
}
