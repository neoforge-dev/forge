package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestWorkerWSMarshal(t *testing.T) {
	cases := []struct {
		input    interface{}
		expected string
	}{
		{map[string]string{"foo": "bar"}, `{"foo":"bar"}`},
		{make(chan int), `{}`}, // Json marshal error returns empty object
	}

	for _, c := range cases {
		got := string(wsMarshal(c.input))
		if got != c.expected {
			t.Errorf("wsMarshal(%v) = %s, want %s", c.input, got, c.expected)
		}
	}
}

func TestWorkerDeriveWSURL(t *testing.T) {
	os.Setenv("WS_PORT", "9999")
	defer os.Unsetenv("WS_PORT")

	got := deriveWSURL("http://localhost:8080")
	expected := "ws://localhost:9999/ws"
	if got != expected {
		t.Errorf("deriveWSURL() = %s, want %s", got, expected)
	}

	os.Unsetenv("WS_PORT")
	got = deriveWSURL("http://localhost:8080")
	expected = "ws://localhost:8082/ws"
	if got != expected {
		t.Errorf("deriveWSURL() (default) = %s, want %s", got, expected)
	}
}

func TestWorkerFormatAgo(t *testing.T) {
	now := time.Now()
	cases := []struct {
		input    time.Time
		expected string
	}{
		{time.Time{}, "unknown"},
		{now.Add(-10 * time.Second), "10s ago"},
		{now.Add(-10 * time.Minute), "10m ago"},
		{now.Add(-10 * time.Hour), "10h ago"},
		{now.Add(-10 * 24 * time.Hour), "10d ago"},
		{now.Add(10 * time.Second), "0s ago"}, // Future time
	}

	for _, c := range cases {
		got := formatAgo(c.input)
		if got != c.expected {
			t.Errorf("formatAgo(%v) = %s, want %s", c.input, got, c.expected)
		}
	}
}

func TestWorkerMin(t *testing.T) {
	if min(1*time.Second, 2*time.Second) != 1*time.Second {
		t.Error("min(1, 2) != 1")
	}
	if min(2*time.Second, 1*time.Second) != 1*time.Second {
		t.Error("min(2, 1) != 1")
	}
}

func TestWorkerGetWorkerPIDFile(t *testing.T) {
	os.Setenv("FORGE_ROOT", "/tmp/forge-test")
	defer os.Unsetenv("FORGE_ROOT")

	got := getWorkerPIDFile("test-worker")
	expected := filepath.Join("/tmp/forge-test", ".forge", "workers", "test-worker.pid")
	if got != expected {
		t.Errorf("getWorkerPIDFile() = %s, want %s", got, expected)
	}
}

func TestWorkerGetWorkerLogFile(t *testing.T) {
	os.Setenv("FORGE_ROOT", "/tmp/forge-test")
	defer os.Unsetenv("FORGE_ROOT")

	got := getWorkerLogFile("test-worker")
	expected := filepath.Join("/tmp/forge-test", ".forge", "logs", "workers", "test-worker.log")
	if got != expected {
		t.Errorf("getWorkerLogFile() = %s, want %s", got, expected)
	}
}

func TestWorkerCommand(t *testing.T) {
	runner := NewTestRunner(t)

	output, err := runner.Execute("worker", "--help")
	if err != nil {
		t.Fatalf("worker --help failed: %v", err)
	}
	if !strings.Contains(output, "Manage FORGE worker nodes") && !strings.Contains(output, "worker nodes that connect") {
		t.Errorf("unexpected help output: %s", output)
	}
}

func TestWorkerStatusEmpty(t *testing.T) {
	// Ensure no local workers are found by pointing to an empty temp dir
	// and pointing FORGE_API_URL to an unreachable address so daemon is not queried.
	tmpDir, err := os.MkdirTemp("", "forge-worker-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	t.Setenv("FORGE_ROOT", tmpDir)
	t.Setenv("FORGE_API_URL", "http://127.0.0.1:19999") // unreachable

	runner := NewTestRunner(t)
	output, err := runner.Execute("worker", "status")
	if err != nil {
		t.Fatalf("worker status failed: %v", err)
	}

	if !strings.Contains(output, "No workers found") && !strings.Contains(output, "unreachable") {
		t.Errorf("expected 'No workers found', got: %s", output)
	}
}

func TestWorkerListEmpty(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "forge-worker-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	t.Setenv("FORGE_ROOT", tmpDir)
	t.Setenv("FORGE_API_URL", "http://127.0.0.1:19999") // unreachable

	runner := NewTestRunner(t)
	output, err := runner.Execute("worker", "list")
	if err != nil {
		t.Fatalf("worker list failed: %v", err)
	}

	if !strings.Contains(output, "No workers found") && !strings.Contains(output, "unreachable") {
		t.Errorf("expected 'No workers found', got: %s", output)
	}
}

func TestWorkerUpValidation(t *testing.T) {
	runner := NewTestRunner(t)

	// Test with a worker that is "already running" by creating a PID file
	tmpDir, err := os.MkdirTemp("", "forge-worker-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Unsetenv("FORGE_ROOT")

	workerID := "test-running-worker"
	workersDir := filepath.Join(tmpDir, ".forge", "workers")
	os.MkdirAll(workersDir, 0755)
	pidFile := filepath.Join(workersDir, workerID+".pid")
	
	// Use our own PID so it's definitely "running"
	os.WriteFile(pidFile, []byte(fmt.Sprintf("%d", os.Getpid())), 0644)

	output, err := runner.Execute("worker", "up", "--id", workerID)
	if err == nil {
		t.Errorf("expected error for already running worker, got none. Output: %s", output)
	} else if !strings.Contains(err.Error(), "already running") {
		t.Errorf("expected 'already running' error, got: %v", err)
	}
}

// TestWorkerUpHelp tests worker up --help
func TestWorkerUpHelp(t *testing.T) {
	runner := NewTestRunner(t)

	output, err := runner.Execute("worker", "up", "--help")
	if err != nil {
		t.Fatalf("worker up --help failed: %v", err)
	}

	if !strings.Contains(output, "--id") {
		t.Errorf("expected output to contain '--id', got: %s", output)
	}
}
