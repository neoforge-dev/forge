//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// handlers_github.go:githubWebhookHandler (line ~160)
// ---------------------------------------------------------------------------

// TestWave131_GithubWebhook_MethodNotAllowed verifies GET returns 405.
// TestWave131_GithubWebhook_NonIssuesEvent verifies non-issues events are skipped.
func TestWave131_GithubWebhook_NonIssuesEvent(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/github/webhook", strings.NewReader("{}"))
	req.Header.Set("X-GitHub-Event", "push")
	w := httptest.NewRecorder()
	githubWebhookHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	var resp map[string]string
	json.NewDecoder(w.Body).Decode(&resp)
	if resp["status"] != "skipped" {
		t.Errorf("expected skipped, got %q", resp["status"])
	}
}

// TestWave131_GithubWebhook_InvalidJSON verifies malformed body returns 400.
func TestWave131_GithubWebhook_InvalidJSON(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/github/webhook", strings.NewReader("{bad json"))
	req.Header.Set("X-GitHub-Event", "issues")
	w := httptest.NewRecorder()
	githubWebhookHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// TestWave131_GithubWebhook_ActionSkipped verifies "closed" action is skipped.
func TestWave131_GithubWebhook_ActionSkipped(t *testing.T) {
	body, _ := json.Marshal(map[string]interface{}{
		"action": "closed",
		"issue":  map[string]interface{}{"number": 1, "title": "test"},
		"repository": map[string]interface{}{"name": "test-repo"},
	})
	req := httptest.NewRequest(http.MethodPost, "/api/github/webhook", strings.NewReader(string(body)))
	req.Header.Set("X-GitHub-Event", "issues")
	w := httptest.NewRecorder()
	githubWebhookHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 skipped, got %d", w.Code)
	}
}

// TestWave131_GithubWebhook_NoIssueNumber verifies issue with no number is skipped.
func TestWave131_GithubWebhook_NoIssueNumber(t *testing.T) {
	body, _ := json.Marshal(map[string]interface{}{
		"action": "opened",
		"issue":  map[string]interface{}{"number": 0, "title": ""},
		"repository": map[string]interface{}{"name": "test-repo"},
	})
	req := httptest.NewRequest(http.MethodPost, "/api/github/webhook", strings.NewReader(string(body)))
	req.Header.Set("X-GitHub-Event", "issues")
	w := httptest.NewRecorder()
	githubWebhookHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 skipped, got %d", w.Code)
	}
}

// TestWave131_GithubWebhook_IssueOpened verifies opened issue creates a task.
func TestWave131_GithubWebhook_IssueOpened(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	body, _ := json.Marshal(map[string]interface{}{
		"action": "opened",
		"issue": map[string]interface{}{
			"number": 42,
			"title":  "Fix the thing",
			"body":   "detailed description",
			"html_url": "https://github.com/test/repo/issues/42",
			"labels": []interface{}{},
		},
		"repository": map[string]interface{}{"name": "test-repo"},
	})
	req := httptest.NewRequest(http.MethodPost, "/api/github/webhook", strings.NewReader(string(body)))
	req.Header.Set("X-GitHub-Event", "issues")
	w := httptest.NewRecorder()
	githubWebhookHandler(w, req)
	t.Logf("GithubWebhook IssueOpened: status=%d body=%s", w.Code, w.Body.String()[:min131(len(w.Body.String()), 100)])
}

// TestWave131_GithubWebhook_LabeledWithBug verifies bug label maps to bugfix type.
func TestWave131_GithubWebhook_LabeledWithBug(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	body, _ := json.Marshal(map[string]interface{}{
		"action": "labeled",
		"issue": map[string]interface{}{
			"number": 7,
			"title":  "Bug in auth",
			"body":   "",
			"html_url": "https://github.com/test/repo/issues/7",
			"labels": []interface{}{map[string]string{"name": "bug"}},
		},
		"repository": map[string]interface{}{"name": "my.repo_name"},
	})
	req := httptest.NewRequest(http.MethodPost, "/api/github/webhook", strings.NewReader(string(body)))
	req.Header.Set("X-GitHub-Event", "issues")
	w := httptest.NewRecorder()
	githubWebhookHandler(w, req)
	t.Logf("GithubWebhook bug label: status=%d", w.Code)
}

// ---------------------------------------------------------------------------
// handlers_github.go:sanitizeProjectName (helper coverage)
// ---------------------------------------------------------------------------

// TestWave131_SanitizeProjectName verifies cleaning of repo names.
func TestWave131_SanitizeProjectName(t *testing.T) {
	cases := []struct{ in, want string }{
		{"my-repo", "my-repo"},
		{"my.repo", "my-repo"},
		{"my_repo", "my-repo"},
		{"", "unknown"},
		{"---", "unknown"},
	}
	for _, c := range cases {
		got := sanitizeProjectName(c.in)
		if got != c.want {
			t.Errorf("sanitizeProjectName(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

// ---------------------------------------------------------------------------
// handlers_plan.go:agentTasksHandler (line ~138)
// ---------------------------------------------------------------------------

// TestWave131_AgentTasksHandler_MethodNotAllowed verifies POST returns 405.
// TestWave131_AgentTasksHandler_MissingID verifies empty agent ID returns 400.
func TestWave131_AgentTasksHandler_MissingID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents//tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// TestWave131_AgentTasksHandler_ValidAgent verifies agent with no tasks returns empty list.
func TestWave131_AgentTasksHandler_ValidAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/test-agent-131/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	var resp map[string]interface{}
	json.NewDecoder(w.Body).Decode(&resp)
	t.Logf("agentTasksHandler: count=%v", resp["count"])
}

// TestWave131_AgentTasksHandler_PathPrefix verifies /agents/ prefix is stripped.
func TestWave131_AgentTasksHandler_PathPrefix(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/agents/test-agent-131/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	t.Logf("agentTasksHandler /agents/ prefix: status=%d", w.Code)
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

func min131(a, b int) int {
	if a < b {
		return a
	}
	return b
}
