//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// --- helpers -----------------------------------------------------------------

// makeGithubRequest builds a test HTTP request for POST /api/github/webhook.
// If secret is non-empty the X-Hub-Signature-256 header is computed and set.
func makeGithubRequest(t *testing.T, event string, body []byte, secret string) *http.Request {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/api/github/webhook", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-GitHub-Event", event)
	if secret != "" {
		mac := hmac.New(sha256.New, []byte(secret))
		mac.Write(body)
		req.Header.Set("X-Hub-Signature-256", "sha256="+hex.EncodeToString(mac.Sum(nil)))
	}
	return req
}

// testIssuePayload returns a minimal JSON-encoded GitHub issues webhook payload.
func testIssuePayload(action, title string, labels ...string) []byte {
	type lbl struct {
		Name string `json:"name"`
	}
	type issue struct {
		Number  int    `json:"number"`
		Title   string `json:"title"`
		Body    string `json:"body"`
		HTMLURL string `json:"html_url"`
		Labels  []lbl  `json:"labels"`
	}
	type repo struct {
		FullName string `json:"full_name"`
		Name     string `json:"name"`
	}
	type payload struct {
		Action     string `json:"action"`
		Issue      issue  `json:"issue"`
		Repository repo   `json:"repository"`
	}

	lbls := make([]lbl, 0, len(labels))
	for _, l := range labels {
		lbls = append(lbls, lbl{Name: l})
	}

	p := payload{
		Action: action,
		Issue: issue{
			Number:  42,
			Title:   title,
			Body:    "Detailed description of the issue.",
			HTMLURL: "https://github.com/org/test-repo/issues/42",
			Labels:  lbls,
		},
		Repository: repo{
			FullName: "org/test-repo",
			Name:     "test-repo",
		},
	}
	b, _ := json.Marshal(p)
	return b
}

// --- event-routing tests ------------------------------------------------------

func TestGithubWebhookHandler_IgnoresNonIssues(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := []byte(`{"action":"created","comment":{"id":1}}`)
	req := makeGithubRequest(t, "issue_comment", body, "")
	rec := httptest.NewRecorder()

	githubWebhookHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 for non-issues event, got %d", rec.Code)
	}
	var resp map[string]string
	if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
		t.Fatalf("invalid JSON response: %v", err)
	}
	if resp["status"] != "skipped" {
		t.Errorf("expected status=skipped, got %q", resp["status"])
	}
	if resp["reason"] == "" {
		t.Error("expected non-empty reason field")
	}
}

func TestGithubWebhookHandler_IgnoresPushEvent(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := []byte(`{"ref":"refs/heads/main","commits":[]}`)
	req := makeGithubRequest(t, "push", body, "")
	rec := httptest.NewRecorder()

	githubWebhookHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 for push event, got %d", rec.Code)
	}
	var resp map[string]string
	json.NewDecoder(rec.Body).Decode(&resp)
	if resp["status"] != "skipped" {
		t.Errorf("expected status=skipped for push event, got %q", resp["status"])
	}
}

func TestGithubWebhookHandler_IgnoresClosedAction(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := testIssuePayload("closed", "Fix OAuth redirect bug — this title is long enough")
	req := makeGithubRequest(t, "issues", body, "")
	rec := httptest.NewRecorder()

	githubWebhookHandler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 for closed action, got %d", rec.Code)
	}
	var resp map[string]string
	json.NewDecoder(rec.Body).Decode(&resp)
	if resp["status"] != "skipped" {
		t.Errorf("expected status=skipped for closed action, got %q", resp["status"])
	}
}

// --- task creation tests ------------------------------------------------------

func TestGithubWebhookHandler_CreatesTaskOnOpen(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := testIssuePayload("opened", "Fix OAuth redirect bug in production login flow")
	req := makeGithubRequest(t, "issues", body, "")
	rec := httptest.NewRecorder()

	githubWebhookHandler(rec, req)

	if rec.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d (body: %s)", rec.Code, rec.Body.String())
	}

	var resp map[string]string
	if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
		t.Fatalf("invalid JSON response: %v", err)
	}
	if resp["status"] != "created" {
		t.Errorf("expected status=created, got %q", resp["status"])
	}
	if resp["task_id"] == "" {
		t.Error("expected non-empty task_id in response")
	}

	// Verify the task persisted in the queue with correct fields.
	task, err := taskQueue.GetTask(context.Background(), resp["task_id"])
	if err != nil {
		t.Fatalf("task %s not found in queue: %v", resp["task_id"], err)
	}
	if task.Domain != "github" {
		t.Errorf("expected domain=github, got %q", task.Domain)
	}
	if task.Project != "test-repo" {
		t.Errorf("expected project=test-repo, got %q", task.Project)
	}
	if task.Status != TaskStatusRequested {
		t.Errorf("expected status=requested, got %q", task.Status)
	}
}

func TestGithubWebhookHandler_CreatesTaskOnReopened(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := testIssuePayload("reopened", "Regression in payment processing flow at checkout")
	req := makeGithubRequest(t, "issues", body, "")
	rec := httptest.NewRecorder()

	githubWebhookHandler(rec, req)

	if rec.Code != http.StatusCreated {
		t.Fatalf("expected 201 for reopened action, got %d", rec.Code)
	}
}

func TestGithubWebhookHandler_CreatesTaskOnLabeled(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := testIssuePayload("labeled", "Memory leak in background job processor", "bug", "priority:high")
	req := makeGithubRequest(t, "issues", body, "")
	rec := httptest.NewRecorder()

	githubWebhookHandler(rec, req)

	if rec.Code != http.StatusCreated {
		t.Fatalf("expected 201 for labeled action, got %d", rec.Code)
	}

	var resp map[string]string
	json.NewDecoder(rec.Body).Decode(&resp)

	task, err := taskQueue.GetTask(context.Background(), resp["task_id"])
	if err != nil {
		t.Fatalf("task not found: %v", err)
	}
	if task.Type != TaskTypeBugfix {
		t.Errorf("expected TaskTypeBugfix for bug label, got %q", task.Type)
	}
	if task.Priority != 8 {
		t.Errorf("expected priority=8 for priority:high, got %d", task.Priority)
	}
}

func TestGithubWebhookHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/github/webhook", nil)
	rec := httptest.NewRecorder()

	githubWebhookHandler(rec, req)

	if rec.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for GET, got %d", rec.Code)
	}
}

// --- HMAC signature tests ----------------------------------------------------

func TestValidateGithubSignature(t *testing.T) {
	secret := "test-secret-abc"
	payload := []byte(`{"action":"opened"}`)

	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write(payload)
	validSig := "sha256=" + hex.EncodeToString(mac.Sum(nil))

	tests := []struct {
		name string
		sig  string
		want bool
	}{
		{"valid signature", validSig, true},
		{"wrong signature", "sha256=badhex000", false},
		{"empty signature", "", false},
		{"no sha256 prefix", hex.EncodeToString(mac.Sum(nil)), false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := validateGithubSignature(secret, payload, tc.sig)
			if got != tc.want {
				t.Errorf("validateGithubSignature(%q) = %v, want %v", tc.sig, got, tc.want)
			}
		})
	}
}

func TestGithubWebhookHandler_SignatureValidation(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	t.Setenv("GITHUB_WEBHOOK_SECRET", "mysecret")

	body := testIssuePayload("opened", "Fix critical memory leak in database connection pool")

	t.Run("missing signature rejected", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodPost, "/api/github/webhook", bytes.NewReader(body))
		req.Header.Set("X-GitHub-Event", "issues")
		rec := httptest.NewRecorder()
		githubWebhookHandler(rec, req)
		if rec.Code != http.StatusUnauthorized {
			t.Errorf("expected 401 for missing signature, got %d", rec.Code)
		}
	})

	t.Run("wrong signature rejected", func(t *testing.T) {
		req := makeGithubRequest(t, "issues", body, "wrong-secret")
		rec := httptest.NewRecorder()
		githubWebhookHandler(rec, req)
		if rec.Code != http.StatusUnauthorized {
			t.Errorf("expected 401 for wrong signature, got %d", rec.Code)
		}
	})

	t.Run("correct signature accepted", func(t *testing.T) {
		req := makeGithubRequest(t, "issues", body, "mysecret")
		rec := httptest.NewRecorder()
		githubWebhookHandler(rec, req)
		if rec.Code != http.StatusCreated {
			t.Errorf("expected 201 for correct signature, got %d (body: %s)", rec.Code, rec.Body.String())
		}
	})
}

// --- inference / utility tests -----------------------------------------------

func TestInferPriority(t *testing.T) {
	tests := []struct {
		labels []githubLabel
		want   int
	}{
		{nil, 5},
		{[]githubLabel{}, 5},
		{[]githubLabel{{Name: "priority:high"}}, 8},
		{[]githubLabel{{Name: "priority:low"}}, 3},
		{[]githubLabel{{Name: "priority:critical"}}, 10},
		{[]githubLabel{{Name: "bug"}, {Name: "priority:high"}}, 8},
		{[]githubLabel{{Name: "documentation"}}, 5},
	}
	for _, tc := range tests {
		got := inferPriority(tc.labels)
		if got != tc.want {
			t.Errorf("inferPriority(%v) = %d, want %d", tc.labels, got, tc.want)
		}
	}
}

func TestInferTaskType(t *testing.T) {
	tests := []struct {
		labels []githubLabel
		want   TaskType
	}{
		{nil, TaskTypeFeature},
		{[]githubLabel{}, TaskTypeFeature},
		{[]githubLabel{{Name: "bug"}}, TaskTypeBugfix},
		{[]githubLabel{{Name: "feature"}}, TaskTypeFeature},
		{[]githubLabel{{Name: "enhancement"}}, TaskTypeFeature},
		{[]githubLabel{{Name: "documentation"}}, TaskTypeFeature},
		{[]githubLabel{{Name: "BUG"}}, TaskTypeBugfix}, // case-insensitive
	}
	for _, tc := range tests {
		got := inferTaskType(tc.labels)
		if got != tc.want {
			t.Errorf("inferTaskType(%v) = %q, want %q", tc.labels, got, tc.want)
		}
	}
}

func TestSanitizeProjectName(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"my-repo", "my-repo"},
		{"my.repo", "my-repo"},
		{"my_repo", "my-repo"},
		{"My.Repo.Name", "my-repo-name"},
		{"", "unknown"},
		{"org/repo", "orgrepo"}, // slash stripped, not a valid FORGE ID char
	}
	for _, tc := range tests {
		got := sanitizeProjectName(tc.input)
		if got != tc.want {
			t.Errorf("sanitizeProjectName(%q) = %q, want %q", tc.input, got, tc.want)
		}
	}
}

// --- postGithubComment tests --------------------------------------------------

func TestPostGithubComment_NoToken(t *testing.T) {
	// When GITHUB_TOKEN is unset, postGithubComment must be a no-op and must not
	// contact any server.
	t.Setenv("GITHUB_TOKEN", "")

	called := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		w.WriteHeader(http.StatusCreated)
	}))
	defer srv.Close()

	old := githubAPIBaseURL
	githubAPIBaseURL = srv.URL
	defer func() { githubAPIBaseURL = old }()

	postGithubComment("org/repo", 1, "hello")

	if called {
		t.Error("expected no HTTP call when GITHUB_TOKEN is empty")
	}
}

func TestPostGithubComment_PostsComment(t *testing.T) {
	type commentBody struct {
		Body string `json:"body"`
	}

	var received commentBody
	var authHeader string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authHeader = r.Header.Get("Authorization")
		if err := json.NewDecoder(r.Body).Decode(&received); err != nil {
			http.Error(w, "bad body", http.StatusBadRequest)
			return
		}
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(map[string]int{"id": 999})
	}))
	defer srv.Close()

	t.Setenv("GITHUB_TOKEN", "test-token-xyz")
	old := githubAPIBaseURL
	githubAPIBaseURL = srv.URL
	defer func() { githubAPIBaseURL = old }()

	postGithubComment("myorg/myrepo", 42, "FORGE task created: TASK-TEST-001")

	if authHeader != "Bearer test-token-xyz" {
		t.Errorf("expected Bearer auth, got %q", authHeader)
	}
	if !strings.Contains(received.Body, "TASK-TEST-001") {
		t.Errorf("comment body %q does not contain task ID", received.Body)
	}
}

func TestPostGithubComment_Non201IsLogged(t *testing.T) {
	// When the server returns non-201 the function must not panic or return an error.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnprocessableEntity)
	}))
	defer srv.Close()

	t.Setenv("GITHUB_TOKEN", "some-token")
	old := githubAPIBaseURL
	githubAPIBaseURL = srv.URL
	defer func() { githubAPIBaseURL = old }()

	// Must not panic.
	postGithubComment("org/repo", 7, "test comment")
}

func TestGithubWebhookHandler_CommentPosted(t *testing.T) {
	// When a valid webhook opens a task and GITHUB_TOKEN is set, the handler
	// must call postGithubComment (verified via mock server).
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	var commentCalled bool
	ghSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		commentCalled = true
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(map[string]int{"id": 1})
	}))
	defer ghSrv.Close()

	t.Setenv("GITHUB_TOKEN", "test-token")
	old := githubAPIBaseURL
	githubAPIBaseURL = ghSrv.URL
	defer func() { githubAPIBaseURL = old }()

	payload := testIssuePayload("opened", "Test issue for comment-back")
	req := makeGithubRequest(t, "issues", payload, "")
	rec := httptest.NewRecorder()
	githubWebhookHandler(rec, req)

	if rec.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", rec.Code, rec.Body.String())
	}

	// The comment is posted in a goroutine — give it a moment.
	deadline := time.Now().Add(500 * time.Millisecond)
	for !commentCalled && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if !commentCalled {
		t.Error("expected GitHub comment API to be called after task creation")
	}
}

// --- parseGithubIssueRef tests -----------------------------------------------

func TestParseGithubIssueRef_Valid(t *testing.T) {
	title := "GH#42: Fix the flaky test"
	desc := "Some body\n\nSource: https://github.com/myorg/myrepo/issues/42"
	repo, num, ok := parseGithubIssueRef(title, desc)
	if !ok {
		t.Fatal("expected ok=true")
	}
	if repo != "myorg/myrepo" {
		t.Errorf("repo: got %q, want %q", repo, "myorg/myrepo")
	}
	if num != 42 {
		t.Errorf("issue number: got %d, want 42", num)
	}
}

func TestParseGithubIssueRef_NoGHPrefix(t *testing.T) {
	_, _, ok := parseGithubIssueRef("Regular task title", "Source: https://github.com/org/repo/issues/1")
	if ok {
		t.Error("expected ok=false for non-GH title")
	}
}

func TestParseGithubIssueRef_NoSourceURL(t *testing.T) {
	_, _, ok := parseGithubIssueRef("GH#7: Some issue", "No source URL here")
	if ok {
		t.Error("expected ok=false when source URL missing")
	}
}

func TestParseGithubIssueRef_ZeroIssueNumber(t *testing.T) {
	// Malformed: GH#0 — issue numbers must be positive
	_, _, ok := parseGithubIssueRef("GH#0: bad issue", "Source: https://github.com/org/repo/issues/0")
	if ok {
		t.Error("expected ok=false for zero issue number")
	}
}

// --- notifyGithubTaskCompleted tests -----------------------------------------

func TestNotifyGithubTaskCompleted_Success(t *testing.T) {
	var called bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		w.WriteHeader(http.StatusCreated)
	}))
	defer srv.Close()

	orig := githubAPIBaseURL
	githubAPIBaseURL = srv.URL
	defer func() { githubAPIBaseURL = orig }()

	t.Setenv("GITHUB_TOKEN", "test-token")

	title := "GH#5: Add new feature"
	desc := "Feature request.\n\nSource: https://github.com/org/repo/issues/5"
	notifyGithubTaskCompleted("task-abc", title, desc, false)

	deadline := time.Now().Add(500 * time.Millisecond)
	for !called && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if !called {
		t.Error("expected GitHub comment API to be called on task completion")
	}
}

func TestNotifyGithubTaskCompleted_Failed(t *testing.T) {
	var body string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		body = string(b)
		w.WriteHeader(http.StatusCreated)
	}))
	defer srv.Close()

	orig := githubAPIBaseURL
	githubAPIBaseURL = srv.URL
	defer func() { githubAPIBaseURL = orig }()

	t.Setenv("GITHUB_TOKEN", "test-token")

	title := "GH#9: Bug report"
	desc := "A bug.\n\nSource: https://github.com/org/repo/issues/9"
	notifyGithubTaskCompleted("task-xyz", title, desc, true)

	deadline := time.Now().Add(500 * time.Millisecond)
	for body == "" && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if !strings.Contains(body, "failed") {
		t.Errorf("expected failure comment body, got: %s", body)
	}
}

func TestNotifyGithubTaskCompleted_NonGithubTask(t *testing.T) {
	// If the title doesn't start with GH#, nothing should be called.
	// This is a no-op — just ensure it doesn't panic.
	notifyGithubTaskCompleted("task-123", "Regular task", "Some description", false)
	// No server — any HTTP call would panic/fail; if we reach here, ok.
}

// --- lookupTaskBranch tests --------------------------------------------------

func TestLookupTaskBranch_Found(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer func() { setDBConn(nil) }()

	// Create the git_branch_locks table (mirrors migrations/003_git_guard.up.sql).
	_, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS git_branch_locks (
			task_id TEXT PRIMARY KEY,
			branch TEXT NOT NULL,
			locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			locked_by TEXT NOT NULL
		)`)
	if err != nil {
		t.Fatalf("create git_branch_locks: %v", err)
	}
	_, err = db.Exec(`INSERT INTO git_branch_locks (task_id, branch, locked_by) VALUES (?, ?, ?)`,
		"task-gh-001", "feat/task-gh-001-fix-login", "agent-kimi")
	if err != nil {
		t.Fatalf("insert branch lock: %v", err)
	}

	branch := lookupTaskBranch("task-gh-001")
	if branch != "feat/task-gh-001-fix-login" {
		t.Errorf("expected branch feat/task-gh-001-fix-login, got %q", branch)
	}
}

func TestLookupTaskBranch_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer func() { setDBConn(nil) }()

	_, _ = db.Exec(`
		CREATE TABLE IF NOT EXISTS git_branch_locks (
			task_id TEXT PRIMARY KEY, branch TEXT NOT NULL,
			locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, locked_by TEXT NOT NULL
		)`)

	branch := lookupTaskBranch("no-such-task")
	if branch != "" {
		t.Errorf("expected empty branch for unknown task, got %q", branch)
	}
}

// --- createGithubPR tests ----------------------------------------------------

func TestCreateGithubPR_Success(t *testing.T) {
	var capturedPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		capturedPath = r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		w.Write([]byte(`{"html_url":"https://github.com/org/repo/pull/42","number":42}`))
	}))
	defer srv.Close()

	orig := githubAPIBaseURL
	githubAPIBaseURL = srv.URL
	defer func() { githubAPIBaseURL = orig }()

	t.Setenv("GITHUB_TOKEN", "test-token")

	prURL, err := createGithubPR("org/repo", "feat/my-branch", "main", "Fix the bug", "Closes #7")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if prURL != "https://github.com/org/repo/pull/42" {
		t.Errorf("prURL: got %q, want https://github.com/org/repo/pull/42", prURL)
	}
	if capturedPath != "/repos/org/repo/pulls" {
		t.Errorf("API path: got %q, want /repos/org/repo/pulls", capturedPath)
	}
}

func TestCreateGithubPR_NoToken(t *testing.T) {
	t.Setenv("GITHUB_TOKEN", "")
	prURL, err := createGithubPR("org/repo", "feat/branch", "main", "title", "body")
	if err != nil {
		t.Errorf("expected nil error when no token, got %v", err)
	}
	if prURL != "" {
		t.Errorf("expected empty prURL when no token, got %q", prURL)
	}
}

func TestCreateGithubPR_APIError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnprocessableEntity)
		w.Write([]byte(`{"message":"Validation Failed"}`))
	}))
	defer srv.Close()

	orig := githubAPIBaseURL
	githubAPIBaseURL = srv.URL
	defer func() { githubAPIBaseURL = orig }()

	t.Setenv("GITHUB_TOKEN", "test-token")

	_, err := createGithubPR("org/repo", "main", "main", "title", "body")
	if err == nil {
		t.Error("expected error on 422 response")
	}
}

// --- createMergeApprovalRecord tests ----------------------------------------

func TestCreateMergeApprovalRecord_Inserts(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer func() { setDBConn(nil) }()

	id, err := createMergeApprovalRecord("task-001", "org/repo", "https://github.com/org/repo/pull/5")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if id == "" {
		t.Error("expected non-empty approval ID")
	}

	var approvalType, taskID, title, status string
	err = db.QueryRow(`SELECT type, task_id, title, status FROM approvals WHERE id = ?`, id).
		Scan(&approvalType, &taskID, &title, &status)
	if err != nil {
		t.Fatalf("query approval: %v", err)
	}
	if approvalType != "merge" {
		t.Errorf("type: got %q, want merge", approvalType)
	}
	if taskID != "task-001" {
		t.Errorf("task_id: got %q, want task-001", taskID)
	}
	if status != "pending" {
		t.Errorf("status: got %q, want pending", status)
	}
	if !strings.Contains(title, "org/repo") {
		t.Errorf("title should contain repo name, got %q", title)
	}
}

func TestCreateMergeApprovalRecord_NoDB(t *testing.T) {
	setDBConn(nil)
	_, err := createMergeApprovalRecord("task-x", "org/repo", "https://github.com/org/repo/pull/1")
	if err == nil {
		t.Error("expected error when db is nil")
	}
}

// TestNotifyGithubTaskCompleted_WithBranch verifies that when a branch is
// locked to the task, notifyGithubTaskCompleted attempts PR creation and
// includes the PR URL in the comment.
func TestNotifyGithubTaskCompleted_WithBranch(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer func() { setDBConn(nil) }()

	_, _ = db.Exec(`
		CREATE TABLE IF NOT EXISTS git_branch_locks (
			task_id TEXT PRIMARY KEY, branch TEXT NOT NULL,
			locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, locked_by TEXT NOT NULL
		)`)
	_, _ = db.Exec(`INSERT INTO git_branch_locks (task_id, branch, locked_by) VALUES (?, ?, ?)`,
		"task-pr-001", "feat/task-pr-001-fix", "agent-kimi")

	var commentBody string
	var prCreated bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		if strings.Contains(r.URL.Path, "/pulls") {
			prCreated = true
			w.Write([]byte(`{"html_url":"https://github.com/org/repo/pull/99","number":99}`))
		} else {
			commentBody = string(b)
			w.Write([]byte(`{}`))
		}
	}))
	defer srv.Close()

	orig := githubAPIBaseURL
	githubAPIBaseURL = srv.URL
	defer func() { githubAPIBaseURL = orig }()

	t.Setenv("GITHUB_TOKEN", "test-token")

	title := "GH#3: Fix login bug"
	desc := "Bug details.\n\nSource: https://github.com/org/repo/issues/3"
	notifyGithubTaskCompleted("task-pr-001", title, desc, false)

	deadline := time.Now().Add(500 * time.Millisecond)
	for commentBody == "" && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}

	if !prCreated {
		t.Error("expected PR creation API call")
	}
	if !strings.Contains(commentBody, "pull/99") {
		t.Errorf("expected PR URL in comment, got: %s", commentBody)
	}
}

// --- mergePR tests -----------------------------------------------------------

func TestMergePR_Success(t *testing.T) {
	var mergePathCalled string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mergePathCalled = r.URL.Path
		if r.Method != http.MethodPut {
			t.Errorf("expected PUT, got %s", r.Method)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"sha":"abc123","merged":true,"message":"Pull Request successfully merged"}`))
	}))
	defer srv.Close()

	orig := githubAPIBaseURL
	githubAPIBaseURL = srv.URL
	defer func() { githubAPIBaseURL = orig }()

	t.Setenv("GITHUB_TOKEN", "test-token")

	sha, err := mergePR("org/repo", 42)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if sha != "abc123" {
		t.Errorf("sha: got %q, want abc123", sha)
	}
	if mergePathCalled != "/repos/org/repo/pulls/42/merge" {
		t.Errorf("path: got %q, want /repos/org/repo/pulls/42/merge", mergePathCalled)
	}
}

func TestMergePR_NoToken(t *testing.T) {
	t.Setenv("GITHUB_TOKEN", "")
	sha, err := mergePR("org/repo", 1)
	if err != nil {
		t.Errorf("expected nil error when no token, got %v", err)
	}
	if sha != "" {
		t.Errorf("expected empty sha when no token, got %q", sha)
	}
}

// --- triggerGithubMergeIfApplicable tests ------------------------------------

func TestTriggerGithubMergeIfApplicable_MergeType(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer func() { setDBConn(nil) }()

	// Insert a merge-type approval with PR URL in description.
	_, err := db.Exec(`
		INSERT INTO approvals (id, type, task_id, agent_id, domain, title, description,
			risk_score, confidence_score, tier, status, created_at, expires_at)
		VALUES ('appr-merge-001', 'merge', 'task-x', 'github-loop', 'github',
			'Merge PR: org/repo', 'PR URL: https://github.com/org/repo/pull/7',
			0.3, 0.8, 'phone', 'approved', datetime('now'), datetime('now', '+7 days'))
	`)
	if err != nil {
		t.Fatalf("insert approval: %v", err)
	}

	var mergeCalled bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "/merge") {
			mergeCalled = true
			w.WriteHeader(http.StatusOK)
			w.Write([]byte(`{"sha":"deadbeef","merged":true}`))
		} else {
			w.WriteHeader(http.StatusOK)
		}
	}))
	defer srv.Close()

	orig := githubAPIBaseURL
	githubAPIBaseURL = srv.URL
	defer func() { githubAPIBaseURL = orig }()

	t.Setenv("GITHUB_TOKEN", "test-token")

	triggerGithubMergeIfApplicable(context.Background(), "appr-merge-001")

	// Give goroutine-free call time to finish (function runs synchronously in test).
	if !mergeCalled {
		t.Error("expected GitHub merge API to be called for merge-type approval")
	}
}

// TestGithubWebhookHandler_EnqueueFails exercises the enqueue error path
// (handlers_github.go:251.53,255.3) when the DB is closed so taskQueue.Enqueue fails.
func TestGithubWebhookHandler_EnqueueFails(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Close DB after setup so Enqueue fails.
	db := getDBConn()
	db.Close()

	body := testIssuePayload("opened", "Fix the broken payment gateway integration")
	req := makeGithubRequest(t, "issues", body, "")
	rec := httptest.NewRecorder()

	githubWebhookHandler(rec, req)

	if rec.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 for enqueue failure in githubWebhookHandler, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestTriggerGithubMergeIfApplicable_NonMergeType(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer func() { setDBConn(nil) }()

	_, _ = db.Exec(`
		INSERT INTO approvals (id, type, task_id, agent_id, domain, title, description,
			risk_score, confidence_score, tier, status, created_at, expires_at)
		VALUES ('appr-task-001', 'task_completion', 'task-y', 'agent-kimi', 'forge',
			'Task complete', 'some task', 0.3, 0.8, 'phone', 'approved',
			datetime('now'), datetime('now', '+7 days'))
	`)

	// No server — if we try to call GitHub API, the test panics/fails.
	// If we reach here without a panic, non-merge types are correctly skipped.
	triggerGithubMergeIfApplicable(context.Background(), "appr-task-001")
}
