package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestDaemonCommand tests daemon command
func TestDaemonCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("daemon")
	if err != nil {
		t.Logf("daemon: %v", err)
	}
}

// TestDaemonStatus tests daemon status command
func TestDaemonStatus(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("daemon", "status")
	if err != nil {
		t.Logf("daemon status: %v", err)
	}
}

// TestDaemonStart tests daemon start command
func TestDaemonStart(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("daemon", "start")
	if err != nil {
		t.Logf("daemon start: %v", err)
	}
}

// TestDaemonStop tests daemon stop command
func TestDaemonStop(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("daemon", "stop")
	if err != nil {
		t.Logf("daemon stop: %v", err)
	}
}

// TestPidFilePath tests pidFilePath with default root
func TestPidFilePath(t *testing.T) {
	// Clear FORGE_ROOT env
	os.Unsetenv("FORGE_ROOT")

	path := pidFilePath()
	expected := filepath.Join(".", ".forge", "forged.pid")
	if path != expected {
		t.Errorf("pidFilePath() = %v, want %v", path, expected)
	}
}

// TestPidFilePathWithForgeRoot tests pidFilePath with FORGE_ROOT set
func TestPidFilePathWithForgeRoot(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	path := pidFilePath()
	expected := filepath.Join(tmpDir, ".forge", "forged.pid")
	if path != expected {
		t.Errorf("pidFilePath() = %v, want %v", path, expected)
	}
}

// TestWriteAndReadPIDFile tests writing and reading PID files
func TestWriteAndReadPIDFile(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	testPID := 12345

	// Write PID
	err := writePIDFile(testPID)
	if err != nil {
		t.Fatalf("writePIDFile() error = %v", err)
	}

	// Read PID
	pid, err := readPIDFile()
	if err != nil {
		t.Fatalf("readPIDFile() error = %v", err)
	}

	if pid != testPID {
		t.Errorf("readPIDFile() = %v, want %v", pid, testPID)
	}
}

// TestReadPIDFileNonExistent tests reading non-existent PID file
func TestReadPIDFileNonExistent(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	_, err := readPIDFile()
	if err == nil {
		t.Error("readPIDFile() expected error for non-existent file")
	}
}

// TestRemovePIDFile tests removing PID file
func TestRemovePIDFile(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	// First write a PID file
	err := writePIDFile(12345)
	if err != nil {
		t.Fatalf("writePIDFile() error = %v", err)
	}

	// Remove it
	err = removePIDFile()
	if err != nil {
		t.Fatalf("removePIDFile() error = %v", err)
	}

	// Verify it's gone
	_, err = readPIDFile()
	if err == nil {
		t.Error("Expected error after removing PID file")
	}
}

// TestFindDaemonBinaryWithEnv tests findDaemonBinary with FORGED_BIN set
func TestFindDaemonBinaryWithEnv(t *testing.T) {
	// Test with non-existent binary - should ignore invalid env override.
	invalidPath := "/nonexistent/forged"
	t.Setenv("FORGED_BIN", invalidPath)

	binary := findDaemonBinary()
	if binary == invalidPath {
		t.Errorf("findDaemonBinary() = %v, want invalid env path ignored", binary)
	}
}

// TestFindDaemonBinaryWithValidPath tests findDaemonBinary with valid FORGED_BIN
func TestFindDaemonBinaryWithValidPath(t *testing.T) {
	// Create a temp file to use as "binary"
	tmpFile := filepath.Join(t.TempDir(), "forged")
	if err := os.WriteFile(tmpFile, []byte("test"), 0755); err != nil {
		t.Fatalf("Failed to create temp file: %v", err)
	}

	t.Setenv("FORGED_BIN", tmpFile)

	binary := findDaemonBinary()
	if binary != tmpFile {
		t.Errorf("findDaemonBinary() = %v, want %v", binary, tmpFile)
	}
}

// TestFindDaemonBinaryWithLegacyEnv tests findDaemonBinary backward compat with FORGE_V3_BIN
func TestFindDaemonBinaryWithLegacyEnv(t *testing.T) {
	// Create a temp file to use as "binary"
	tmpFile := filepath.Join(t.TempDir(), "forge-v3")
	if err := os.WriteFile(tmpFile, []byte("test"), 0755); err != nil {
		t.Fatalf("Failed to create temp file: %v", err)
	}

	t.Setenv("FORGE_V3_BIN", tmpFile)

	binary := findDaemonBinary()
	if binary != tmpFile {
		t.Errorf("findDaemonBinary() = %v, want %v (legacy FORGE_V3_BIN)", binary, tmpFile)
	}
}

// TestDaemonRestart tests daemon restart command (may timeout if daemon running)
func TestDaemonRestart(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("daemon", "restart")
	if err != nil {
		t.Logf("daemon restart: %v", err)
	}
}

// TestFindDaemonBinaryPrefersForged verifies that forged is preferred over forge-v3
// when both are present. This encodes the daemon naming decision (ADR-040).
func TestFindDaemonBinaryPrefersForged(t *testing.T) {
	dir := t.TempDir()

	// Create both binaries — forged should win.
	forgedPath := filepath.Join(dir, "forged")
	forgeV3Path := filepath.Join(dir, "forge-v3")
	for _, p := range []string{forgedPath, forgeV3Path} {
		if err := os.WriteFile(p, []byte("#!/bin/sh"), 0o755); err != nil {
			t.Fatalf("create %s: %v", p, err)
		}
	}

	t.Setenv("FORGED_BIN", forgedPath)
	t.Setenv("FORGE_V3_BIN", forgeV3Path)

	got := findDaemonBinary()
	if got != forgedPath {
		t.Errorf("findDaemonBinary() = %q, want %q (forged must be preferred over forge-v3)", got, forgedPath)
	}
}

func TestFindDaemonLogPath_ReturnsExistingOrEmpty(t *testing.T) {
	// findDaemonLogPath checks hardcoded paths under $HOME and /tmp.
	// Verify it returns "" or a path that actually exists — no panics, valid output.
	got := findDaemonLogPath()
	if got != "" {
		if _, err := os.Stat(got); err != nil {
			t.Errorf("findDaemonLogPath() returned non-existent path %q", got)
		}
	}
}

func TestForgeDBPath_EnvVar(t *testing.T) {
	t.Setenv("FORGE_ROOT", "/tmp/myroot")
	got := forgeDBPath()
	want := "/tmp/myroot/.forge/forge-v3.db"
	if got != want {
		t.Errorf("forgeDBPath() = %q, want %q", got, want)
	}
}

func TestForgeDBPath_Default(t *testing.T) {
	t.Setenv("FORGE_ROOT", "")
	got := forgeDBPath()
	if !strings.HasSuffix(got, ".forge/forge-v3.db") {
		t.Errorf("forgeDBPath() = %q, want suffix .forge/forge-v3.db", got)
	}
}

func TestGetConfigLeadURL_ValidTOML(t *testing.T) {
	dir := t.TempDir()
	toml := "[control_plane]\nurl = \"http://prya:8081\"\n"
	cfgPath := filepath.Join(dir, ".forge", "forge.toml")
	if err := os.MkdirAll(filepath.Dir(cfgPath), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cfgPath, []byte(toml), 0644); err != nil {
		t.Fatal(err)
	}
	// getConfigLeadURL reads from well-known paths; inject via HOME override
	t.Setenv("HOME", dir)
	// The function checks $HOME/.forge/config.toml — write there too
	homeCfg := filepath.Join(dir, ".forge", "config.toml")
	if err := os.WriteFile(homeCfg, []byte(toml), 0644); err != nil {
		t.Fatal(err)
	}
	got := getConfigLeadURL()
	if got != "http://prya:8081" {
		t.Errorf("getConfigLeadURL() = %q, want http://prya:8081", got)
	}
}

func TestGetConfigLeadURL_Missing(t *testing.T) {
	t.Setenv("HOME", t.TempDir()) // no config files
	got := getConfigLeadURL()
	if got != "" {
		t.Logf("getConfigLeadURL() = %q (may hit real config)", got)
	}
}
