//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ============================================================
// coverage_wave234_test.go — handlers_github.go pure functions
// Target: sanitizeProjectName, inferTaskType, inferPriority,
//         validateGithubSignature, parseGithubIssueRef,
//         lookupTaskBranch, writeJSONSkipped, githubWebhookHandler (entry paths)
// ============================================================

// --- sanitizeProjectName ---

func TestWave234_SanitizeProjectName_Normal(t *testing.T) {
	got := sanitizeProjectName("my-project")
	if got != "my-project" {
		t.Errorf("expected 'my-project', got %s", got)
	}
}

func TestWave234_SanitizeProjectName_Dots(t *testing.T) {
	got := sanitizeProjectName("my.project.name")
	if strings.Contains(got, ".") {
		t.Errorf("expected no dots, got %s", got)
	}
}

func TestWave234_SanitizeProjectName_Underscores(t *testing.T) {
	got := sanitizeProjectName("my_project_name")
	if strings.Contains(got, "_") {
		t.Errorf("expected no underscores, got %s", got)
	}
}

func TestWave234_SanitizeProjectName_Empty(t *testing.T) {
	got := sanitizeProjectName("")
	if got != "unknown" {
		t.Errorf("expected 'unknown', got %s", got)
	}
}

func TestWave234_SanitizeProjectName_SpecialCharsOnly(t *testing.T) {
	got := sanitizeProjectName("!!!---")
	if got != "unknown" {
		t.Errorf("expected 'unknown' for all-special, got %s", got)
	}
}

func TestWave234_SanitizeProjectName_Uppercase(t *testing.T) {
	got := sanitizeProjectName("MyProject")
	if got != strings.ToLower(got) {
		t.Errorf("expected lowercase, got %s", got)
	}
}

func TestWave234_SanitizeProjectName_TooLong(t *testing.T) {
	long := strings.Repeat("a", 100)
	got := sanitizeProjectName(long)
	if len(got) > 64 {
		t.Errorf("expected max 64 chars, got len=%d", len(got))
	}
}

// --- inferTaskType ---

func TestWave234_InferTaskType_Bug(t *testing.T) {
	labels := []githubLabel{{Name: "bug"}}
	got := inferTaskType(labels)
	if got != TaskTypeBugfix {
		t.Errorf("expected bugfix, got %s", got)
	}
}

func TestWave234_InferTaskType_Feature(t *testing.T) {
	labels := []githubLabel{{Name: "feature"}}
	got := inferTaskType(labels)
	if got != TaskTypeFeature {
		t.Errorf("expected feature, got %s", got)
	}
}

func TestWave234_InferTaskType_Enhancement(t *testing.T) {
	labels := []githubLabel{{Name: "enhancement"}}
	got := inferTaskType(labels)
	if got != TaskTypeFeature {
		t.Errorf("expected feature for enhancement, got %s", got)
	}
}

func TestWave234_InferTaskType_NoMatch(t *testing.T) {
	labels := []githubLabel{{Name: "documentation"}, {Name: "help wanted"}}
	got := inferTaskType(labels)
	if got != TaskTypeFeature {
		t.Errorf("expected feature (default), got %s", got)
	}
}

func TestWave234_InferTaskType_Empty(t *testing.T) {
	got := inferTaskType(nil)
	if got != TaskTypeFeature {
		t.Errorf("expected feature (default for nil labels), got %s", got)
	}
}

// --- inferPriority ---

func TestWave234_InferPriority_Critical(t *testing.T) {
	labels := []githubLabel{{Name: "priority:critical"}}
	got := inferPriority(labels)
	if got != 10 {
		t.Errorf("expected 10, got %d", got)
	}
}

func TestWave234_InferPriority_High(t *testing.T) {
	labels := []githubLabel{{Name: "priority:high"}}
	got := inferPriority(labels)
	if got != 8 {
		t.Errorf("expected 8, got %d", got)
	}
}

func TestWave234_InferPriority_Low(t *testing.T) {
	labels := []githubLabel{{Name: "priority:low"}}
	got := inferPriority(labels)
	if got != 3 {
		t.Errorf("expected 3, got %d", got)
	}
}

func TestWave234_InferPriority_Default(t *testing.T) {
	got := inferPriority(nil)
	if got != 5 {
		t.Errorf("expected 5 (default), got %d", got)
	}
}

// --- validateGithubSignature ---

func computeSig234(secret string, payload []byte) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write(payload)
	return "sha256=" + hex.EncodeToString(mac.Sum(nil))
}

func TestWave234_ValidateSignature_Valid(t *testing.T) {
	payload := []byte(`{"action":"opened"}`)
	secret := "test-secret"
	sig := computeSig234(secret, payload)
	if !validateGithubSignature(secret, payload, sig) {
		t.Error("expected valid signature")
	}
}

func TestWave234_ValidateSignature_Invalid(t *testing.T) {
	payload := []byte(`{"action":"opened"}`)
	if validateGithubSignature("secret", payload, "sha256=badhash") {
		t.Error("expected invalid signature")
	}
}

func TestWave234_ValidateSignature_WrongSecret(t *testing.T) {
	payload := []byte(`{"action":"opened"}`)
	sig := computeSig234("right-secret", payload)
	if validateGithubSignature("wrong-secret", payload, sig) {
		t.Error("expected invalid signature with wrong secret")
	}
}

// --- parseGithubIssueRef ---

func TestWave234_ParseGithubIssueRef_Valid(t *testing.T) {
	title := "GH#42: Fix login bug"
	desc := "Source: https://github.com/owner/repo/issues/42"
	repo, num, ok := parseGithubIssueRef(title, desc)
	if !ok {
		t.Fatal("expected ok=true")
	}
	if repo != "owner/repo" {
		t.Errorf("expected 'owner/repo', got %s", repo)
	}
	if num != 42 {
		t.Errorf("expected 42, got %d", num)
	}
}

func TestWave234_ParseGithubIssueRef_NoTitleMatch(t *testing.T) {
	_, _, ok := parseGithubIssueRef("Regular task title", "Some desc")
	if ok {
		t.Error("expected ok=false for non-GH title")
	}
}

func TestWave234_ParseGithubIssueRef_NoDescURL(t *testing.T) {
	title := "GH#10: Something"
	_, _, ok := parseGithubIssueRef(title, "No URL here")
	if ok {
		t.Error("expected ok=false when no source URL in description")
	}
}

// --- writeJSONSkipped ---

func TestWave234_WriteJSONSkipped(t *testing.T) {
	w := httptest.NewRecorder()
	writeJSONSkipped(w, "event not supported")
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	body := w.Body.String()
	if !strings.Contains(body, "skipped") {
		t.Errorf("expected 'skipped' in body: %s", body)
	}
	if !strings.Contains(body, "event not supported") {
		t.Errorf("expected reason in body: %s", body)
	}
}

// --- githubWebhookHandler ---

func TestWave234_GithubWebhook_NonIssuesEvent(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/github/webhook", nil)
	req.Header.Set("X-GitHub-Event", "push")
	w := httptest.NewRecorder()
	githubWebhookHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 (skipped), got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "skipped") {
		t.Errorf("expected skipped response, got: %s", w.Body.String())
	}
}

// --- lookupTaskBranch ---

func TestWave234_LookupTaskBranch_NoDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	got := lookupTaskBranch("task-xyz")
	if got != "" {
		t.Errorf("expected empty string when no DB, got %s", got)
	}
}

func TestWave234_LookupTaskBranch_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	got := lookupTaskBranch("nonexistent-task-xyz")
	if got != "" {
		t.Errorf("expected empty string for unknown task, got %s", got)
	}
}
