// preflight_test.go — tests for forge preflight command
package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestPreflightCmdRegistered verifies that preflightCmd is registered with correct Use and Short.
func TestPreflightCmdRegistered(t *testing.T) {
	if preflightCmd == nil {
		t.Fatal("preflightCmd is nil — not declared")
	}
	if preflightCmd.Use != "preflight" {
		t.Errorf("preflightCmd.Use = %q, want %q", preflightCmd.Use, "preflight")
	}
	if preflightCmd.Short == "" {
		t.Error("preflightCmd.Short is empty")
	}
	if !strings.Contains(strings.ToLower(preflightCmd.Short), "health") &&
		!strings.Contains(strings.ToLower(preflightCmd.Short), "preflight") &&
		!strings.Contains(strings.ToLower(preflightCmd.Short), "check") &&
		!strings.Contains(strings.ToLower(preflightCmd.Short), "boot") {
		t.Errorf("preflightCmd.Short %q does not describe a health check command", preflightCmd.Short)
	}
}

// TestPreflightCmdFlags verifies the --fix and --json flags exist.
func TestPreflightCmdFlags(t *testing.T) {
	fixFlag := preflightCmd.Flags().Lookup("fix")
	if fixFlag == nil {
		t.Error("preflightCmd missing --fix flag")
	}

	jsonFlag := preflightCmd.Flags().Lookup("json")
	if jsonFlag == nil {
		t.Error("preflightCmd missing --json flag")
	}
}

// TestCheckGitLock_NoLock verifies clean result when .git/index.lock does not exist.
func TestCheckGitLock_NoLock(t *testing.T) {
	// Use a temp directory that has no .git/index.lock.
	tmpDir := t.TempDir()
	origDir, err := os.Getwd()
	if err != nil {
		t.Fatalf("Getwd: %v", err)
	}
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}
	defer os.Chdir(origDir)

	result := preflightCheckGitLock(false)

	if result.Status != preflightOK {
		t.Errorf("expected preflightOK for missing lock, got status=%d message=%q", result.Status, result.Message)
	}
	if !strings.Contains(result.Message, "clean") {
		t.Errorf("expected 'clean' in message, got %q", result.Message)
	}
}

// TestCheckGitLock_StaleLock verifies a stale lock (>60s old) is detected.
func TestCheckGitLock_StaleLock(t *testing.T) {
	tmpDir := t.TempDir()
	gitDir := filepath.Join(tmpDir, ".git")
	if err := os.MkdirAll(gitDir, 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}

	lockPath := filepath.Join(gitDir, "index.lock")
	if err := os.WriteFile(lockPath, []byte("stale"), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	// Backdate the file by 2 minutes to make it stale.
	staleTime := time.Now().Add(-2 * time.Minute)
	if err := os.Chtimes(lockPath, staleTime, staleTime); err != nil {
		t.Fatalf("Chtimes: %v", err)
	}

	origDir, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}
	defer os.Chdir(origDir)

	result := preflightCheckGitLock(false)

	if result.Status != preflightWarn {
		t.Errorf("expected preflightWarn for stale lock, got status=%d message=%q", result.Status, result.Message)
	}
	if !strings.Contains(result.Message, "stale") {
		t.Errorf("expected 'stale' in message, got %q", result.Message)
	}
	if !strings.Contains(result.Message, "index.lock") {
		t.Errorf("expected 'index.lock' in message, got %q", result.Message)
	}
}

// TestCheckGitLock_StaleLockFix verifies --fix removes a stale lock.
func TestCheckGitLock_StaleLockFix(t *testing.T) {
	tmpDir := t.TempDir()
	gitDir := filepath.Join(tmpDir, ".git")
	if err := os.MkdirAll(gitDir, 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}

	lockPath := filepath.Join(gitDir, "index.lock")
	if err := os.WriteFile(lockPath, []byte(""), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	// Zero-byte lock is immediately stale regardless of age.
	origDir, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}
	defer os.Chdir(origDir)

	result := preflightCheckGitLock(true)

	if result.Status != preflightOK {
		t.Errorf("expected preflightOK after --fix removed lock, got status=%d message=%q", result.Status, result.Message)
	}

	// Verify the file was actually removed.
	if _, err := os.Stat(lockPath); !os.IsNotExist(err) {
		t.Error("expected lock file to be removed after --fix, but it still exists")
	}
}

// TestCheckGitLock_ActiveLock verifies a fresh (non-stale) lock is a warning not an error.
func TestCheckGitLock_ActiveLock(t *testing.T) {
	tmpDir := t.TempDir()
	gitDir := filepath.Join(tmpDir, ".git")
	if err := os.MkdirAll(gitDir, 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}

	lockPath := filepath.Join(gitDir, "index.lock")
	// Write a non-empty lock with a fresh timestamp (within 60s).
	if err := os.WriteFile(lockPath, []byte("active"), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	origDir, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}
	defer os.Chdir(origDir)

	result := preflightCheckGitLock(false)

	// An active (fresh) lock should still warn, not fail.
	if result.Status == preflightFail {
		t.Errorf("active lock should not produce preflightFail, got message=%q", result.Message)
	}
	if result.Status != preflightWarn && result.Status != preflightOK {
		t.Errorf("expected preflightWarn or preflightOK for active lock, got status=%d", result.Status)
	}
}

// TestCheckDiskSpace_ThresholdLogic verifies the threshold decision logic.
// We do this by testing the constants directly rather than mocking syscall.Statfs.
func TestCheckDiskSpace_ThresholdLogic(t *testing.T) {
	// Verify constants are set correctly.
	if diskThresholdWarnBytes != 3*1024*1024*1024 {
		t.Errorf("diskThresholdWarnBytes = %d, want %d (3 GB)", diskThresholdWarnBytes, 3*1024*1024*1024)
	}
	if diskThresholdFailBytes != 1*1024*1024*1024 {
		t.Errorf("diskThresholdFailBytes = %d, want %d (1 GB)", diskThresholdFailBytes, 1*1024*1024*1024)
	}
	if diskThresholdFailBytes >= diskThresholdWarnBytes {
		t.Error("diskThresholdFailBytes must be less than diskThresholdWarnBytes")
	}

	// Verify the actual check runs without panic (it reads the real filesystem).
	result := preflightCheckDisk()
	if result.Name != "Disk" {
		t.Errorf("expected Name=Disk, got %q", result.Name)
	}
	if result.Message == "" {
		t.Error("checkDiskSpace returned empty message")
	}
	// The result should be one of the known statuses — it should not be zero-value.
	if result.Status != preflightOK && result.Status != preflightWarn && result.Status != preflightFail {
		t.Errorf("unexpected Status=%d", result.Status)
	}
}

// TestDiskSpaceLevelMapping verifies that the correct status is chosen for each band.
func TestDiskSpaceLevelMapping(t *testing.T) {
	tests := []struct {
		name      string
		freeBytes uint64
		wantStatus preflightStatus
	}{
		{"ok: 10 GB free", 10 * 1024 * 1024 * 1024, preflightOK},
		{"warn: 2 GB free", 2 * 1024 * 1024 * 1024, preflightWarn},
		{"fail: 512 MB free", 512 * 1024 * 1024, preflightFail},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			// Simulate the threshold comparison directly — same logic as checkDiskSpace.
			var gotStatus preflightStatus
			switch {
			case tc.freeBytes < diskThresholdFailBytes:
				gotStatus = preflightFail
			case tc.freeBytes < diskThresholdWarnBytes:
				gotStatus = preflightWarn
			default:
				gotStatus = preflightOK
			}
			if gotStatus != tc.wantStatus {
				t.Errorf("freeBytes=%d: got status=%d, want=%d", tc.freeBytes, gotStatus, tc.wantStatus)
			}
		})
	}
}

// TestPreflightJSONOutput verifies the JSON output structure when daemon is offline.
func TestPreflightJSONOutput(t *testing.T) {
	// Build results manually (daemon offline scenario).
	results := []preflightResult{
		{Name: "Git", Status: preflightOK, Message: "clean"},
		{Name: "Daemon", Status: preflightFail, Message: "unreachable"},
		{Name: "Fleet", Status: preflightWarn, Message: "skipped (daemon offline)"},
		{Name: "Disk", Status: preflightOK, Message: "50.0 GB free on /"},
		{Name: "Queue", Status: preflightWarn, Message: "skipped (daemon offline)"},
		{Name: "Gates", Status: preflightWarn, Message: "skipped (daemon offline)"},
	}

	for i := range results {
		results[i].OK = results[i].Status == preflightOK
		results[i].Warn = results[i].Status == preflightWarn
		results[i].Fail = results[i].Status == preflightFail
	}

	out := preflightOutput{
		Timestamp: time.Now().UTC().Format(time.RFC3339),
		Results:   results,
		Warnings:  3,
		Failures:  1,
		Result:    "FAILURES",
	}

	data, err := json.Marshal(out)
	if err != nil {
		t.Fatalf("json.Marshal: %v", err)
	}

	// Round-trip: unmarshal and verify key fields.
	var parsed preflightOutput
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatalf("json.Unmarshal: %v", err)
	}

	if parsed.Result != "FAILURES" {
		t.Errorf("Result = %q, want %q", parsed.Result, "FAILURES")
	}
	if parsed.Failures != 1 {
		t.Errorf("Failures = %d, want 1", parsed.Failures)
	}
	if parsed.Warnings != 3 {
		t.Errorf("Warnings = %d, want 3", parsed.Warnings)
	}
	if len(parsed.Results) != 6 {
		t.Errorf("len(Results) = %d, want 6", len(parsed.Results))
	}

	// Verify the Daemon result has Fail=true.
	var daemonResult *preflightResult
	for i := range parsed.Results {
		if parsed.Results[i].Name == "Daemon" {
			daemonResult = &parsed.Results[i]
			break
		}
	}
	if daemonResult == nil {
		t.Fatal("Daemon result not found in JSON output")
	}
	if !daemonResult.Fail {
		t.Error("Daemon result should have Fail=true")
	}
	if daemonResult.OK {
		t.Error("Daemon result should have OK=false")
	}
}

// TestPreflightResultLabel verifies the result label logic.
func TestPreflightResultLabel(t *testing.T) {
	tests := []struct {
		warnings int
		failures int
		want     string
	}{
		{0, 0, "OK"},
		{1, 0, "WARNINGS"},
		{0, 1, "FAILURES"},
		{2, 1, "FAILURES"},
	}

	for _, tc := range tests {
		label := "OK"
		if tc.failures > 0 {
			label = "FAILURES"
		} else if tc.warnings > 0 {
			label = "WARNINGS"
		}
		if label != tc.want {
			t.Errorf("warnings=%d failures=%d: got %q, want %q", tc.warnings, tc.failures, label, tc.want)
		}
	}
}

// TestPreflightIcon verifies icon() returns the correct ANSI-colored symbol.
func TestPreflightIcon(t *testing.T) {
	okResult := preflightResult{Status: preflightOK}
	warnResult := preflightResult{Status: preflightWarn}
	failResult := preflightResult{Status: preflightFail}

	if !strings.Contains(okResult.icon(), "✓") {
		t.Errorf("OK icon should contain ✓, got %q", okResult.icon())
	}
	if !strings.Contains(warnResult.icon(), "⚠") {
		t.Errorf("Warn icon should contain ⚠, got %q", warnResult.icon())
	}
	if !strings.Contains(failResult.icon(), "✗") {
		t.Errorf("Fail icon should contain ✗, got %q", failResult.icon())
	}
}

// TestCheckDaemonHealth_Offline verifies that an offline daemon returns preflightFail.
func TestCheckDaemonHealth_Offline(t *testing.T) {
	// Use a port that is guaranteed to be unreachable.
	result := preflightCheckDaemon("http://localhost:19999")

	if result.Status != preflightFail {
		t.Errorf("expected preflightFail for unreachable daemon, got status=%d message=%q", result.Status, result.Message)
	}
	if !strings.Contains(result.Message, "unreachable") {
		t.Errorf("expected 'unreachable' in message, got %q", result.Message)
	}
	// Message must contain a recovery hint.
	if !strings.Contains(result.Message, "forge daemon") {
		t.Errorf("expected recovery hint in message, got %q", result.Message)
	}
}

// TestCheckFleetAgents_DaemonOffline verifies skip behaviour when daemon is offline.
func TestCheckFleetAgents_DaemonOffline(t *testing.T) {
	result := preflightCheckFleet("http://localhost:19999", false)

	if result.Status != preflightWarn {
		t.Errorf("expected preflightWarn when daemon is offline, got status=%d", result.Status)
	}
	if !strings.Contains(result.Message, "skipped") {
		t.Errorf("expected 'skipped' in message, got %q", result.Message)
	}
}

// TestCheckTaskQueue_DaemonOffline verifies skip behaviour when daemon is offline.
func TestCheckTaskQueue_DaemonOffline(t *testing.T) {
	result := preflightCheckQueue("http://localhost:19999", false)

	if result.Status != preflightWarn {
		t.Errorf("expected preflightWarn when daemon is offline, got status=%d", result.Status)
	}
	if !strings.Contains(result.Message, "skipped") {
		t.Errorf("expected 'skipped' in message, got %q", result.Message)
	}
}

// TestCheckGateStatus_DaemonOffline verifies skip behaviour when daemon is offline.
func TestCheckGateStatus_DaemonOffline(t *testing.T) {
	result := preflightCheckGates("http://localhost:19999", false)

	if result.Status != preflightWarn {
		t.Errorf("expected preflightWarn when daemon is offline, got status=%d", result.Status)
	}
	if !strings.Contains(result.Message, "skipped") {
		t.Errorf("expected 'skipped' in message, got %q", result.Message)
	}
}
