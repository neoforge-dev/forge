package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/neoforge-dev/forge/internal"
)

func TestParseAgentID(t *testing.T) {
	tests := []struct {
		in   string
		want string
	}{
		{"forge:kimi", "kimi"},
		{"kimi", "kimi"},
		{"  forge:gemini  ", "gemini"},
		{"  minimax  ", "minimax"},
		{"forge:claude", "claude"},
		{"forge:tech", "tech"},
		{"forge:", ""},
		{"", ""},
		{"   ", ""},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			got := parseAgentID(tt.in)
			if got != tt.want {
				t.Errorf("parseAgentID(%q) = %q, want %q", tt.in, got, tt.want)
			}
		})
	}
}

func TestDispatchCommandRegistered(t *testing.T) {
	if dispatchCmd == nil {
		t.Fatal("dispatchCmd is nil")
	}
	cmds := dispatchCmd.Commands()
	if len(cmds) < 2 {
		t.Errorf("dispatch should have at least 2 subcommands, got %d", len(cmds))
	}
	names := make([]string, len(cmds))
	for i, c := range cmds {
		names[i] = c.Name()
	}
	hasSend, hasShow := false, false
	for _, n := range names {
		if n == "send" {
			hasSend = true
		}
		if n == "show" {
			hasShow = true
		}
	}
	if !hasSend || !hasShow {
		t.Errorf("dispatch subcommands must include 'send' and 'show', got %v", names)
	}
}

// TestDispatchSendErrors exercises validation error paths (no API required).
func TestDispatchSendErrors(t *testing.T) {
	t.Run("missing agent and message", func(t *testing.T) {
		_, err := NewTestRunner(t).Execute("dispatch", "send")
		if err == nil {
			t.Fatal("expected error for missing agent")
		}
		if !strings.Contains(err.Error(), "agent") {
			t.Errorf("error should mention agent: %v", err)
		}
	})

	t.Run("missing message when inline", func(t *testing.T) {
		_, err := NewTestRunner(t).Execute("dispatch", "send", "forge:kimi")
		if err == nil {
			t.Fatal("expected error for missing message")
		}
		if !strings.Contains(err.Error(), "message") {
			t.Errorf("error should mention message: %v", err)
		}
	})

	t.Run("file without agent", func(t *testing.T) {
		_, err := NewTestRunner(t).Execute("dispatch", "send", "--file", ".forge/dispatches/nonexistent.md")
		if err == nil {
			t.Fatal("expected error when --file used without agent")
		}
		if !strings.Contains(err.Error(), "agent") {
			t.Errorf("error should mention agent: %v", err)
		}
	})

	t.Run("file with nonexistent file", func(t *testing.T) {
		_, err := NewTestRunner(t).Execute("dispatch", "send", "--file", "/tmp/forge-nonexistent-dispatch-12345.md", "forge:kimi")
		if err == nil {
			t.Fatal("expected error for missing file")
		}
		if !strings.Contains(err.Error(), "read") && !strings.Contains(err.Error(), "file") {
			t.Errorf("error should mention read/file: %v", err)
		}
	})

	t.Run("empty agent ID", func(t *testing.T) {
		_, err := NewTestRunner(t).Execute("dispatch", "send", "forge:", "some message")
		if err == nil {
			t.Fatal("expected error for empty agent ID")
		}
		if !strings.Contains(err.Error(), "agent") {
			t.Errorf("error should mention agent: %v", err)
		}
	})
}

func TestDispatchShowRequiresTaskID(t *testing.T) {
	_, err := NewTestRunner(t).Execute("dispatch", "show")
	if err == nil {
		t.Fatal("expected error for missing task ID")
	}
}

// TestDispatchSendWithMockServer runs dispatch send against a mock API to hit success and format paths.
func TestDispatchSendWithMockServer(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/api/tasks" && r.Method == http.MethodPost:
			var req internal.CreateTaskRequest
			json.NewDecoder(r.Body).Decode(&req)
			task := internal.Task{
				ID:          "task-mock-001",
				Title:       req.Subject,
				Description: req.Description,
				Status:      "queued",
			}
			w.WriteHeader(http.StatusCreated)
			json.NewEncoder(w).Encode(task)
		case strings.HasPrefix(r.URL.Path, "/api/tasks/") && strings.HasSuffix(r.URL.Path, "/claim") && r.Method == http.MethodPost:
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(map[string]interface{}{"task": map[string]string{"id": "task-mock-001", "status": "claimed"}})
		default:
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
		}
	}))
	defer server.Close()

	restore := setenv("FORGE_API_URL", server.URL)
	defer restore()

	runner := NewTestRunner(t)

	t.Run("table output contains Dispatched", func(t *testing.T) {
		output, err := runner.Execute("dispatch", "send", "forge:kimi", "Test task message")
		if err != nil {
			t.Fatalf("dispatch send failed: %v", err)
		}
		if !strings.Contains(output, "Dispatched") {
			t.Errorf("output should contain 'Dispatched', got: %s", output)
		}
		if !strings.Contains(output, "task-mock-001") {
			t.Errorf("output should contain task ID, got: %s", output)
		}
		if !strings.Contains(output, "kimi") {
			t.Errorf("output should contain agent name, got: %s", output)
		}
	})

	t.Run("json format", func(t *testing.T) {
		output, err := runner.Execute("dispatch", "send", "forge:gemini", "JSON test", "--format", "json")
		if err != nil {
			t.Fatalf("dispatch send --format json failed: %v", err)
		}
		trimmed := strings.TrimSpace(output)
		if !strings.HasPrefix(trimmed, "{") {
			t.Errorf("expected JSON object, got: %s", output)
		}
		var m map[string]string
		if err := json.Unmarshal([]byte(output), &m); err != nil {
			t.Errorf("invalid JSON: %v", err)
		}
		if m["dispatched"] != "task-mock-001" || m["agent"] != "gemini" {
			t.Errorf("unexpected JSON content: %v", m)
		}
	})

	t.Run("quiet format", func(t *testing.T) {
		output, err := runner.Execute("dispatch", "send", "forge:minimax", "Quiet task", "--format", "quiet")
		if err != nil {
			t.Fatalf("dispatch send --format quiet failed: %v", err)
		}
		if strings.TrimSpace(output) != "" {
			t.Errorf("quiet format should produce no output, got: %q", output)
		}
	})
}

// TestDispatchShowWithMock runs dispatch show against a mock API.
func TestDispatchShowWithMock(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/api/tasks/show-task-001" && r.Method == http.MethodGet {
			task := internal.Task{
				ID:          "show-task-001",
				Title:       "Mock task",
				Description: "Description",
				Status:      "dispatched",
			}
			json.NewEncoder(w).Encode(task)
			return
		}
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
	}))
	defer server.Close()

	restore := setenv("FORGE_API_URL", server.URL)
	defer restore()

	output, err := NewTestRunner(t).Execute("dispatch", "show", "show-task-001")
	if err != nil {
		t.Fatalf("dispatch show failed: %v", err)
	}
	if !strings.Contains(output, "show-task-001") || !strings.Contains(output, "Mock task") {
		t.Errorf("output should contain task ID and title, got: %s", output)
	}
}

// TestDispatchSendSubjectTruncation uses mock to verify long messages are truncated for subject.
func TestDispatchSendSubjectTruncation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/api/tasks" && r.Method == http.MethodPost {
			var req internal.CreateTaskRequest
			json.NewDecoder(r.Body).Decode(&req)
			if len(req.Subject) > 200 {
				t.Errorf("subject should be truncated to 200, got len %d", len(req.Subject))
			}
			task := internal.Task{ID: "t1", Title: req.Subject, Status: "queued"}
			w.WriteHeader(http.StatusCreated)
			json.NewEncoder(w).Encode(task)
			return
		}
		if strings.HasPrefix(r.URL.Path, "/api/tasks/") && strings.HasSuffix(r.URL.Path, "/claim") {
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(map[string]interface{}{"task": map[string]string{"id": "t1", "status": "claimed"}})
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	restore := setenv("FORGE_API_URL", server.URL)
	defer restore()

	longMsg := strings.Repeat("x", 250)
	_, err := NewTestRunner(t).Execute("dispatch", "send", "forge:kimi", longMsg)
	if err != nil {
		t.Fatalf("dispatch send with long message failed: %v", err)
	}
}
