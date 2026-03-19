package main

import (
	"bytes"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestStatusUsesConfiguredControlPlane(t *testing.T) {
	// Skip if local daemon is already running on :8081 — we can't isolate
	// isPortListening() from the real system daemon in that case.
	if conn, err := net.DialTimeout("tcp", "localhost:8081", 50*time.Millisecond); err == nil {
		conn.Close()
		t.Skip("local daemon running on :8081; skipping offline-daemon assertion")
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")

		switch r.URL.Path {
		case "/health":
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"status":"ok"}`))
		case "/api/agents":
			_, _ = w.Write([]byte(`{"agents":[{"agent_id":"forge:kimi","status":"online","context_pct":23.5,"current_task_id":"TASK-123","node":"sati","last_seen":"2026-03-08T10:00:00Z"}]}`))
		case "/api/tasks":
			_, _ = w.Write([]byte(`{"count":2,"tasks":[{"status":"queued","updated_at":"` + time.Now().UTC().Format(time.RFC3339) + `"},{"status":"completed","updated_at":"` + time.Now().UTC().Format(time.RFC3339) + `"}]}`))
		case "/api/patrols":
			_, _ = w.Write([]byte(`{"count":3,"patrols":[{"last_error":"","last_run":"` + time.Now().UTC().Format(time.RFC3339) + `","error_count":0}]}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	tempHome := t.TempDir()
	configPath := filepath.Join(tempHome, ".forge", "config.toml")
	if err := os.MkdirAll(filepath.Dir(configPath), 0755); err != nil {
		t.Fatalf("mkdir config dir: %v", err)
	}
	if err := os.WriteFile(configPath, []byte("[control_plane]\nurl = \""+server.URL+"\"\n"), 0644); err != nil {
		t.Fatalf("write config: %v", err)
	}

	t.Setenv("HOME", tempHome)
	if err := os.Unsetenv("FORGE_API_URL"); err != nil {
		t.Fatalf("unset FORGE_API_URL: %v", err)
	}
	tempProject := t.TempDir()
	t.Setenv("FORGE_ROOT", tempProject)
	if err := os.MkdirAll(filepath.Join(tempProject, ".forge"), 0755); err != nil {
		t.Fatalf("mkdir project .forge: %v", err)
	}
	projectConfig := filepath.Join(tempProject, ".forge", "config.toml")
	if err := os.WriteFile(projectConfig, []byte("[control_plane]\nurl = \""+server.URL+"\"\n"), 0644); err != nil {
		t.Fatalf("write project config: %v", err)
	}
	t.Chdir(tempProject)

	statusCmd.Flags().Set("full", "false")
	statusCmd.Flags().Set("json", "false")
	output, err := captureStatusOutput(t, func() error {
		return statusCmdImpl(statusCmd, nil)
	})
	if err != nil {
		t.Fatalf("status failed: %v\n%s", err, output)
	}

	if !strings.Contains(output, "Control Plane: online ("+server.URL+")") {
		t.Fatalf("expected configured control plane in output, got:\n%s", output)
	}
	if !strings.Contains(output, "Local Daemon: offline") {
		t.Fatalf("expected local daemon offline status, got:\n%s", output)
	}
	if !strings.Contains(output, "forge:kimi") {
		t.Fatalf("expected remote agents to be queried, got:\n%s", output)
	}
}

func captureStatusOutput(t *testing.T, fn func() error) (string, error) {
	t.Helper()

	pr, pw, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	defer pr.Close()

	oldStdout := os.Stdout
	os.Stdout = pw
	defer func() {
		os.Stdout = oldStdout
	}()

	var buf bytes.Buffer
	done := make(chan struct{})
	go func() {
		_, _ = io.Copy(&buf, pr)
		close(done)
	}()

	runErr := fn()
	_ = pw.Close()
	<-done

	return buf.String(), runErr
}
