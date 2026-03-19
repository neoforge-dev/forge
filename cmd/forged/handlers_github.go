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
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// githubAPIBaseURL is the base URL for GitHub REST API calls. Overridable in
// tests to point at a local httptest.Server.
var githubAPIBaseURL = "https://api.github.com"

// postGithubComment posts a comment on the given GitHub issue number inside
// repoFullName ("owner/repo"). It is a best-effort call: any error is logged
// but does not affect the HTTP response already sent to the caller.
//
// Requires GITHUB_TOKEN environment variable (Personal Access Token or GitHub
// App installation token with issues:write permission). If the variable is
// absent the call is a no-op.
func postGithubComment(repoFullName string, issueNumber int, body string) {
	token := os.Getenv("GITHUB_TOKEN")
	if token == "" {
		return
	}

	url := fmt.Sprintf("%s/repos/%s/issues/%d/comments", githubAPIBaseURL, repoFullName, issueNumber)
	payload, _ := json.Marshal(map[string]string{"body": body})

	req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, url, bytes.NewReader(payload))
	if err != nil {
		log.Printf("WARN: postGithubComment: build request: %v", err)
		return
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Printf("WARN: postGithubComment: %s#%d: %v", repoFullName, issueNumber, err)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusCreated {
		log.Printf("WARN: postGithubComment: %s#%d: unexpected status %d", repoFullName, issueNumber, resp.StatusCode)
	}
}

// githubLabel is a label on a GitHub issue.
type githubLabel struct {
	Name string `json:"name"`
}

// githubIssue is the relevant subset of a GitHub issue payload.
type githubIssue struct {
	Number  int           `json:"number"`
	Title   string        `json:"title"`
	Body    string        `json:"body"`
	HTMLURL string        `json:"html_url"`
	Labels  []githubLabel `json:"labels"`
}

// githubRepository is the repository field of a GitHub webhook payload.
type githubRepository struct {
	FullName string `json:"full_name"`
	Name     string `json:"name"`
}

// githubWebhookPayload is the top-level issues event payload.
type githubWebhookPayload struct {
	Action     string           `json:"action"`
	Issue      githubIssue      `json:"issue"`
	Repository githubRepository `json:"repository"`
}

// nonAlphanumDash matches characters that are not alphanumeric or hyphen.
var nonAlphanumDash = regexp.MustCompile(`[^a-zA-Z0-9\-]`)

// sanitizeProjectName converts a repository name to a valid FORGE project ID.
// GitHub repo names can contain dots and underscores; FORGE IDs allow only
// alphanumeric + hyphen + underscore + colon. Dots become hyphens.
func sanitizeProjectName(name string) string {
	s := strings.ReplaceAll(name, ".", "-")
	s = strings.ReplaceAll(s, "_", "-")
	s = nonAlphanumDash.ReplaceAllString(s, "")
	s = strings.Trim(s, "-")
	if s == "" {
		return "unknown"
	}
	if len(s) > 64 {
		s = s[:64]
	}
	return strings.ToLower(s)
}

// inferTaskType maps GitHub issue labels to a FORGE TaskType.
// "bug" → TaskTypeBugfix; "feature"/"enhancement" → TaskTypeFeature.
// Falls back to TaskTypeFeature when no matching label is present.
func inferTaskType(labels []githubLabel) TaskType {
	for _, l := range labels {
		switch strings.ToLower(l.Name) {
		case "bug":
			return TaskTypeBugfix
		case "feature", "enhancement":
			return TaskTypeFeature
		}
	}
	return TaskTypeFeature
}

// inferPriority maps GitHub priority labels to a FORGE numeric priority (1-10).
func inferPriority(labels []githubLabel) int {
	for _, l := range labels {
		switch strings.ToLower(l.Name) {
		case "priority:critical":
			return 10
		case "priority:high":
			return 8
		case "priority:low":
			return 3
		}
	}
	return 5
}

// validateGithubSignature verifies the X-Hub-Signature-256 header against the
// payload bytes using the provided secret.
func validateGithubSignature(secret string, payload []byte, sigHeader string) bool {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write(payload)
	expected := "sha256=" + hex.EncodeToString(mac.Sum(nil))
	return hmac.Equal([]byte(expected), []byte(sigHeader))
}

// githubWebhookHandler handles POST /api/github/webhook.
//
// It accepts GitHub "issues" events and creates a FORGE task for each
// "opened", "reopened", or "labeled" action. All other events and actions
// return a 200 with {"status":"skipped"}.
//
// HMAC-SHA256 validation is performed when GITHUB_WEBHOOK_SECRET is set.
// If the env var is absent validation is skipped (useful for local testing).
// No FORGE auth token is required — GitHub calls this endpoint externally.
func githubWebhookHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Only process issues events.
	event := r.Header.Get("X-GitHub-Event")
	if event != "issues" {
		writeJSONSkipped(w, fmt.Sprintf("event %q is not issues", event))
		return
	}

	// Read body for HMAC validation before decoding.
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20)) // 1 MB cap
	if err != nil {
		writeJSONError(w, http.StatusBadRequest, "failed to read request body")
		return
	}

	// Validate HMAC signature if secret is configured.
	secret := os.Getenv("GITHUB_WEBHOOK_SECRET")
	if secret != "" {
		sig := r.Header.Get("X-Hub-Signature-256")
		if sig == "" {
			writeJSONError(w, http.StatusUnauthorized, "missing X-Hub-Signature-256 header")
			return
		}
		if !validateGithubSignature(secret, body, sig) {
			writeJSONError(w, http.StatusUnauthorized, "invalid webhook signature")
			return
		}
	}

	// Decode the payload.
	var payload githubWebhookPayload
	if err := json.Unmarshal(body, &payload); err != nil {
		writeJSONError(w, http.StatusBadRequest, "invalid JSON payload")
		return
	}

	// Only process specific actions.
	switch payload.Action {
	case "opened", "reopened", "labeled":
		// proceed
	default:
		writeJSONSkipped(w, fmt.Sprintf("action %q skipped", payload.Action))
		return
	}

	// Validate issue has meaningful data.
	if payload.Issue.Number == 0 || strings.TrimSpace(payload.Issue.Title) == "" {
		writeJSONSkipped(w, "issue has no number or empty title")
		return
	}

	// Build the FORGE task.
	project := sanitizeProjectName(payload.Repository.Name)
	if project == "" {
		project = "github"
	}

	title := fmt.Sprintf("GH#%d: %s", payload.Issue.Number, strings.TrimSpace(payload.Issue.Title))
	// Truncate to 500 chars (createTaskHandler's limit).
	if len(title) > 500 {
		title = title[:500]
	}

	description := strings.TrimSpace(payload.Issue.Body)
	if description == "" {
		description = "(no description)"
	}
	description = fmt.Sprintf("%s\n\nSource: %s", description, payload.Issue.HTMLURL)

	task := Task{
		ID:          generateTaskID(),
		Title:       title,
		Description: description,
		Domain:      "github",
		Project:     project,
		Type:        inferTaskType(payload.Issue.Labels),
		Priority:    inferPriority(payload.Issue.Labels),
		Status:      TaskStatusRequested,
		State:       StateQueued,
		Lane:        "dev",
		CreatedAt:   time.Now().UTC(),
		UpdatedAt:   time.Now().UTC(),
	}

	ctx := context.Background()
	if err := taskQueue.Enqueue(ctx, task); err != nil {
		log.Printf("ERROR: githubWebhookHandler: failed to enqueue task %s: %v", task.ID, err)
		writeJSONError(w, http.StatusInternalServerError, fmt.Sprintf("failed to enqueue task: %v", err))
		return
	}

	// Record initial FSM transition (mirrors createTaskHandler pattern).
	if taskStore != nil {
		if storeErr := taskStore.RecordTransition(task.ID, "", StateQueued, "created via github webhook"); storeErr != nil {
			log.Printf("WARN: githubWebhookHandler: failed to record FSM transition for task %s: %v", task.ID, storeErr)
		}
	} else if db := getDBConn(); db != nil {
		if _, dbErr := db.Exec(`UPDATE tasks SET state=? WHERE id=?`, string(StateQueued), task.ID); dbErr != nil {
			log.Printf("WARN: githubWebhookHandler: failed to set FSM state for task %s: %v", task.ID, dbErr)
		}
	}

	// Track metrics the same way createTaskHandler does.
	metrics.RecordTaskCreated(string(task.Type), task.Priority)
	metrics.RecordTaskEnqueued()

	log.Printf("INFO: githubWebhookHandler: created task %s from GH#%d (%s/%s)",
		task.ID, payload.Issue.Number, payload.Repository.FullName, payload.Action)

	// Best-effort: post a comment back to the GitHub issue so the reporter
	// knows FORGE picked it up. Runs in a goroutine so it never blocks the
	// HTTP response.
	commentBody := fmt.Sprintf("FORGE task created: **%s**\n\nThis issue has been queued for autonomous processing. Track progress in the FORGE fleet dashboard.", task.ID)
	go postGithubComment(payload.Repository.FullName, payload.Issue.Number, commentBody)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]string{
		"task_id": task.ID,
		"status":  "created",
	})
}

// writeJSONSkipped sends a 200 JSON response indicating the webhook was
// intentionally ignored.
func writeJSONSkipped(w http.ResponseWriter, reason string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{
		"status": "skipped",
		"reason": reason,
	})
}

// reGithubIssueTitle matches titles of the form "GH#123: ...".
var reGithubIssueTitle = regexp.MustCompile(`^GH#(\d+):`)

// reGithubSourceURL matches the "Source: https://github.com/owner/repo/..." line
// written by githubWebhookHandler into each task description.
var reGithubSourceURL = regexp.MustCompile(`github\.com/([^/]+/[^/]+)/issues/\d+`)

// parseGithubIssueRef extracts the GitHub repo full name ("owner/repo") and
// issue number from a task title + description created by githubWebhookHandler.
// Returns ok=false if either piece is missing.
func parseGithubIssueRef(title, description string) (repoFullName string, issueNumber int, ok bool) {
	m := reGithubIssueTitle.FindStringSubmatch(title)
	if len(m) < 2 {
		return "", 0, false
	}
	n, err := strconv.Atoi(m[1])
	if err != nil || n <= 0 {
		return "", 0, false
	}

	rm := reGithubSourceURL.FindStringSubmatch(description)
	if len(rm) < 2 {
		return "", 0, false
	}
	return rm[1], n, true
}

// lookupTaskBranch returns the git branch locked to taskID in git_branch_locks,
// or an empty string if none is recorded. Uses the global DB connection.
func lookupTaskBranch(taskID string) string {
	db := getDBConn()
	if db == nil {
		return ""
	}
	var branch string
	_ = db.QueryRow(`SELECT branch FROM git_branch_locks WHERE task_id = ?`, taskID).Scan(&branch)
	return branch
}

// githubPRPayload is the request body for the GitHub "create a pull request" API.
type githubPRPayload struct {
	Title               string `json:"title"`
	Head                string `json:"head"`
	Base                string `json:"base"`
	Body                string `json:"body"`
	MaintainerCanModify bool   `json:"maintainer_can_modify"`
}

// githubPRResponse is the relevant subset of the GitHub PR creation response.
type githubPRResponse struct {
	HTMLURL string `json:"html_url"`
	Number  int    `json:"number"`
}

// createGithubPR opens a pull request on repoFullName ("owner/repo") with head
// branch targeting base (typically "main"). Returns the PR HTML URL on success.
// Requires GITHUB_TOKEN env var. Returns ("", nil) when no token is set.
func createGithubPR(repoFullName, head, base, prTitle, prBody string) (prURL string, err error) {
	token := os.Getenv("GITHUB_TOKEN")
	if token == "" {
		return "", nil
	}

	payload := githubPRPayload{
		Title:               prTitle,
		Head:                head,
		Base:                base,
		Body:                prBody,
		MaintainerCanModify: true,
	}
	raw, _ := json.Marshal(payload)

	url := fmt.Sprintf("%s/repos/%s/pulls", githubAPIBaseURL, repoFullName)
	req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, url, bytes.NewReader(raw))
	if err != nil {
		return "", fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("http: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusCreated {
		return "", fmt.Errorf("github API status %d: %s", resp.StatusCode, string(body))
	}

	var pr githubPRResponse
	if err := json.Unmarshal(body, &pr); err != nil {
		return "", fmt.Errorf("decode response: %w", err)
	}
	return pr.HTMLURL, nil
}

// notifyGithubTaskCompleted posts a best-effort completion comment on the
// originating GitHub issue when a FORGE task with domain="github" finishes.
// If the task had a branch locked (via git_branch_locks), it attempts to open
// a GitHub PR and includes the PR URL in the comment.
// Runs in a goroutine — never blocks the HTTP response.
func notifyGithubTaskCompleted(taskID, title, description string, failed bool) {
	repo, issueNum, ok := parseGithubIssueRef(title, description)
	if !ok {
		return
	}

	// Attempt PR creation for successful completions with a known branch.
	var prURL string
	if !failed {
		if branch := lookupTaskBranch(taskID); branch != "" {
			prBody := fmt.Sprintf(
				"Closes #%d\n\nAutonomously implemented by FORGE task `%s`.\n\nReview the changes and merge when ready.",
				issueNum, taskID,
			)
			prTitle := strings.TrimPrefix(title, fmt.Sprintf("GH#%d: ", issueNum))
			if u, prErr := createGithubPR(repo, branch, "main", prTitle, prBody); prErr != nil {
				log.Printf("WARN: notifyGithubTaskCompleted: PR creation for %s#%d failed: %v", repo, issueNum, prErr)
			} else {
				prURL = u
			}
		}
	}

	var body string
	switch {
	case failed:
		body = fmt.Sprintf(
			"❌ FORGE task `%s` **failed**.\n\nThe autonomous agent could not complete this issue. "+
				"The task has been returned to the queue for review. Check the FORGE fleet dashboard for details.",
			taskID,
		)
	case prURL != "":
		body = fmt.Sprintf(
			"✅ FORGE task `%s` **completed**. Pull request opened: %s\n\nReview the PR and merge when satisfied. "+
				"This issue will be closed automatically on merge.",
			taskID, prURL,
		)
	default:
		body = fmt.Sprintf(
			"✅ FORGE task `%s` **completed**.\n\nThe autonomous agent has finished processing this issue. "+
				"Review the result in the FORGE fleet dashboard and close this issue if the work is satisfactory.",
			taskID,
		)
	}

	// Phase 3: create a FORGE merge-approval record so the PR shows up in
	// `forge approval list` for human review.
	if prURL != "" {
		if approvalID, approvalErr := createMergeApprovalRecord(taskID, repo, prURL); approvalErr != nil {
			log.Printf("WARN: notifyGithubTaskCompleted: merge approval creation failed: %v", approvalErr)
		} else {
			log.Printf("INFO: notifyGithubTaskCompleted: merge approval %s created for PR %s", approvalID, prURL)
		}
	}

	go postGithubComment(repo, issueNum, body)
	log.Printf("INFO: notifyGithubTaskCompleted: queued comment on %s#%d for task %s (failed=%v, pr=%q)",
		repo, issueNum, taskID, failed, prURL)
}

// createMergeApprovalRecord inserts a pending "merge" approval into the FORGE
// approvals table, making the PR visible in `forge approval list`.
// The pr_url is stored in the description field for display and for Phase 4
// (auto-merge on approval decide).
// Returns the new approval ID on success.
func createMergeApprovalRecord(taskID, repoFullName, prURL string) (string, error) {
	db := getDBConn()
	if db == nil {
		return "", fmt.Errorf("db not initialized")
	}

	id := generateTaskID() // re-use the same ID generator — produces unique strings
	now := time.Now().UTC()
	expires := now.Add(7 * 24 * time.Hour) // approvals expire after 7 days

	_, err := db.Exec(`
		INSERT INTO approvals
			(id, type, task_id, agent_id, domain, title, description, risk_score, confidence_score,
			 tier, status, created_at, expires_at)
		VALUES (?, 'merge', ?, 'github-loop', 'github', ?, ?, 0.3, 0.8, 'phone', 'pending', ?, ?)
	`,
		id,
		taskID,
		fmt.Sprintf("Merge PR: %s", repoFullName),
		fmt.Sprintf("PR opened automatically by FORGE GitHub Loop.\nRepository: %s\nPR URL: %s\n\nApprove to merge, reject to close.", repoFullName, prURL),
		now.Format(time.RFC3339),
		expires.Format(time.RFC3339),
	)
	if err != nil {
		return "", fmt.Errorf("insert approval: %w", err)
	}
	return id, nil
}

// reGithubPRURL matches a GitHub PR URL in the approval description.
// Written by createMergeApprovalRecord as "PR URL: https://github.com/org/repo/pull/N".
var reGithubPRURL = regexp.MustCompile(`github\.com/([^/]+/[^/]+)/pull/(\d+)`)

// mergePR sends a PUT request to the GitHub "merge a pull request" API.
// Returns the merge commit SHA on success.
func mergePR(repoFullName string, prNumber int) (string, error) {
	token := os.Getenv("GITHUB_TOKEN")
	if token == "" {
		return "", nil // no-op if no token
	}

	url := fmt.Sprintf("%s/repos/%s/pulls/%d/merge", githubAPIBaseURL, repoFullName, prNumber)
	payload, _ := json.Marshal(map[string]string{
		"commit_title":   fmt.Sprintf("Merge PR #%d (approved via FORGE)", prNumber),
		"merge_method":   "squash",
	})

	req, err := http.NewRequestWithContext(context.Background(), http.MethodPut, url, bytes.NewReader(payload))
	if err != nil {
		return "", fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("http: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("github API status %d: %s", resp.StatusCode, string(body))
	}

	var result struct {
		SHA string `json:"sha"`
	}
	_ = json.Unmarshal(body, &result)
	return result.SHA, nil
}

// triggerGithubMergeIfApplicable reads the approval from the DB and, if it is
// a "merge" type, extracts the PR URL from the description and merges the PR.
// Called in a goroutine from the approval approve handler — best-effort only.
func triggerGithubMergeIfApplicable(ctx context.Context, approvalID string) {
	db := getDBConn()
	if db == nil {
		return
	}

	var approvalType, description string
	err := db.QueryRowContext(ctx,
		`SELECT type, COALESCE(description,'') FROM approvals WHERE id = ?`, approvalID,
	).Scan(&approvalType, &description)
	if err != nil {
		log.Printf("WARN: triggerGithubMergeIfApplicable: lookup %s: %v", approvalID, err)
		return
	}

	if approvalType != string(ApprovalMerge) {
		return // not a GitHub merge approval
	}

	m := reGithubPRURL.FindStringSubmatch(description)
	if len(m) < 3 {
		log.Printf("WARN: triggerGithubMergeIfApplicable: no PR URL in description for %s", approvalID)
		return
	}

	repo := m[1]
	prNum, err := strconv.Atoi(m[2])
	if err != nil || prNum <= 0 {
		log.Printf("WARN: triggerGithubMergeIfApplicable: bad PR number %q for %s", m[2], approvalID)
		return
	}

	sha, err := mergePR(repo, prNum)
	if err != nil {
		log.Printf("WARN: triggerGithubMergeIfApplicable: merge %s#%d failed: %v", repo, prNum, err)
		return
	}
	if sha != "" {
		log.Printf("INFO: triggerGithubMergeIfApplicable: merged %s#%d sha=%s (approval %s)", repo, prNum, sha, approvalID)
	}
}
