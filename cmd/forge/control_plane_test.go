package main

import (
	"os"
	"path/filepath"
	"testing"
)

// TestControlPlaneCommand tests control-plane command
func TestControlPlaneCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("control-plane")
	if err != nil {
		t.Logf("control-plane: %v", err)
	}
}

// TestControlPlaneStatus tests control-plane status command
func TestControlPlaneStatus(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("control-plane", "status")
	if err != nil {
		t.Logf("control-plane status: %v", err)
	}
}

// TestGetPIDFileDefault tests getPIDFile with no FORGE_ROOT
// Note: When FORGE_ROOT is not set, findForgeRoot() returns current directory
func TestGetPIDFileDefault(t *testing.T) {
	os.Unsetenv("FORGE_ROOT")

	path := getPIDFile()
	// When FORGE_ROOT is empty, findForgeRoot() returns cwd
	// So path should end with .forge/control-plane.pid
	if filepath.Base(path) != "control-plane.pid" {
		t.Errorf("getPIDFile() = %v, want path ending with control-plane.pid", path)
	}
}

// TestGetPIDFileWithForgeRoot tests getPIDFile with FORGE_ROOT set
func TestGetPIDFileWithForgeRoot(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	path := getPIDFile()
	expected := filepath.Join(tmpDir, ".forge", "control-plane.pid")
	if path != expected {
		t.Errorf("getPIDFile() = %v, want %v", path, expected)
	}
}

// TestGetLogFileDefault tests getLogFile with no FORGE_ROOT
// Note: When FORGE_ROOT is not set, findForgeRoot() returns current directory
func TestGetLogFileDefault(t *testing.T) {
	os.Unsetenv("FORGE_ROOT")

	path := getLogFile()
	// When FORGE_ROOT is empty, findForgeRoot() returns cwd
	// So path should end with .forge/logs/control-plane.log
	if filepath.Base(path) != "control-plane.log" {
		t.Errorf("getLogFile() = %v, want path ending with control-plane.log", path)
	}
}

// TestGetLogFileWithForgeRoot tests getLogFile with FORGE_ROOT set
func TestGetLogFileWithForgeRoot(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	path := getLogFile()
	expected := filepath.Join(tmpDir, ".forge", "logs", "control-plane.log")
	if path != expected {
		t.Errorf("getLogFile() = %v, want %v", path, expected)
	}
}

// TestIsControlPlaneRunning tests isControlPlaneRunning with no PID file
func TestIsControlPlaneRunning(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	running := isControlPlaneRunning()
	if running {
		t.Error("isControlPlaneRunning() = true, want false when no PID file exists")
	}
}

// TestControlPlaneAliases tests control-plane aliases (cp, server)
func TestControlPlaneAliases(t *testing.T) {
	runner := NewTestRunner(t)

	// Test "cp" alias
	_, err := runner.Execute("cp")
	if err != nil {
		t.Logf("cp alias: %v", err)
	}

	// Test "server" alias
	_, err = runner.Execute("server")
	if err != nil {
		t.Logf("server alias: %v", err)
	}
}
