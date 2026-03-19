//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Target 1: triggerGithubMergeIfApplicable — uncovered error paths (72%→85%+)
// ---------------------------------------------------------------------------

// TestWave141_TriggerMerge_ApprovalNotFound covers the DB QueryRowContext error
// path: the approval ID doesn't exist → err != nil → early return.
func TestWave141_TriggerMerge_ApprovalNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer func() { setDBConn(nil) }()

	// Non-existent approval ID — QueryRow returns sql.ErrNoRows which we log and return from.
	triggerGithubMergeIfApplicable(context.Background(), "nonexistent-approval-id-zzz")
	// Passes without panic — the error is only logged.
}

// TestWave141_TriggerMerge_NoPRURL covers the "no PR URL in description" path
// for a merge-type approval that has no parseable GitHub pull URL.
func TestWave141_TriggerMerge_NoPRURL(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer func() { setDBConn(nil) }()

	_, err := db.Exec(`
		INSERT INTO approvals (id, type, task_id, agent_id, domain, title, description,
			risk_score, confidence_score, tier, status, created_at, expires_at)
		VALUES ('appr-nourl-001', 'merge', 'task-nourl', 'agent', 'forge',
			'Merge', 'no pr url here',
			0.3, 0.8, 'phone', 'approved', datetime('now'), datetime('now', '+7 days'))
	`)
	if err != nil {
		t.Fatalf("insert: %v", err)
	}

	// Should log WARN and return without calling merge.
	triggerGithubMergeIfApplicable(context.Background(), "appr-nourl-001")
}

// TestWave141_TriggerMerge_BadPRNumber covers strconv.Atoi failure when the PR
// number extracted from the URL is not a valid integer.
func TestWave141_TriggerMerge_BadPRNumber(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer func() { setDBConn(nil) }()

	_, err := db.Exec(`
		INSERT INTO approvals (id, type, task_id, agent_id, domain, title, description,
			risk_score, confidence_score, tier, status, created_at, expires_at)
		VALUES ('appr-badnum-001', 'merge', 'task-badnum', 'agent', 'forge',
			'Merge', 'PR URL: https://github.com/org/repo/pull/abc',
			0.3, 0.8, 'phone', 'approved', datetime('now'), datetime('now', '+7 days'))
	`)
	if err != nil {
		t.Fatalf("insert: %v", err)
	}

	// "abc" cannot be parsed as int → WARN and return.
	triggerGithubMergeIfApplicable(context.Background(), "appr-badnum-001")
}

// TestWave141_TriggerMerge_MergeAPIFails covers the mergePR failure path:
// the GitHub API returns an error → log WARN and return without panic.
func TestWave141_TriggerMerge_MergeAPIFails(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer func() { setDBConn(nil) }()

	_, err := db.Exec(`
		INSERT INTO approvals (id, type, task_id, agent_id, domain, title, description,
			risk_score, confidence_score, tier, status, created_at, expires_at)
		VALUES ('appr-fail-001', 'merge', 'task-fail', 'agent', 'forge',
			'Merge', 'PR URL: https://github.com/org/repo/pull/42',
			0.3, 0.8, 'phone', 'approved', datetime('now'), datetime('now', '+7 days'))
	`)
	if err != nil {
		t.Fatalf("insert: %v", err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnprocessableEntity)
		w.Write([]byte(`{"message":"Pull Request is not mergeable"}`))
	}))
	defer srv.Close()

	orig := githubAPIBaseURL
	githubAPIBaseURL = srv.URL
	defer func() { githubAPIBaseURL = orig }()

	t.Setenv("GITHUB_TOKEN", "test-token-fail")

	// Should log WARN (merge failed) and return without panic.
	triggerGithubMergeIfApplicable(context.Background(), "appr-fail-001")
}

// ---------------------------------------------------------------------------
// Target 2: routingResolveHandler — required_tier field (86%→95%+)
// ---------------------------------------------------------------------------

// TestWave141_RoutingHandler_DeployStage verifies that portfolio_stage="deploy"
// results in required_tier="phone" in the response.
func TestWave141_RoutingHandler_DeployStage(t *testing.T) {
	// Use a temp dir with valid routing YAML so LoadRoutingBundle succeeds.
	dir := t.TempDir()
	writeMinimalRoutingYAML141(t, dir)
	t.Setenv("FORGE_ROOT", dir)

	body := `{"task_type":"coverage","portfolio_stage":"deploy"}`
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	routingResolveHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp routingResolveResponse
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.RequiredTier != "phone" {
		t.Errorf("expected required_tier=phone for deploy stage, got %q", resp.RequiredTier)
	}
}

// TestWave141_RoutingHandler_BuildStage verifies that portfolio_stage="build"
// results in required_tier="watch" (auto-approve tier).
func TestWave141_RoutingHandler_BuildStage(t *testing.T) {
	dir := t.TempDir()
	writeMinimalRoutingYAML141(t, dir)
	t.Setenv("FORGE_ROOT", dir)

	body := `{"task_type":"coverage","portfolio_stage":"build"}`
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", strings.NewReader(body))
	w := httptest.NewRecorder()

	routingResolveHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp routingResolveResponse
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.RequiredTier != "watch" {
		t.Errorf("expected required_tier=watch for build stage, got %q", resp.RequiredTier)
	}
}

// TestWave141_RoutingHandler_NoStage verifies that when portfolio_stage is empty,
// required_tier defaults to "watch".
func TestWave141_RoutingHandler_NoStage(t *testing.T) {
	dir := t.TempDir()
	writeMinimalRoutingYAML141(t, dir)
	t.Setenv("FORGE_ROOT", dir)

	body := `{"task_type":"coverage"}`
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", strings.NewReader(body))
	w := httptest.NewRecorder()

	routingResolveHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp routingResolveResponse
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.RequiredTier != "watch" {
		t.Errorf("expected required_tier=watch for empty stage, got %q", resp.RequiredTier)
	}
}

// TestWave141_RoutingHandler_MethodNotAllowed verifies POST-only enforcement.
// TestWave141_RoutingHandler_NoEligibleAgent verifies 503 when no agent matches
// the request (config has no OK agents, or all are forbidden).
func TestWave141_RoutingHandler_NoEligibleAgent(t *testing.T) {
	dir := t.TempDir()
	// Write a config where the only agent has status=BUSY (not OK) → no eligible agents.
	configDir := dir + "/config/routing"
	_ = os.MkdirAll(configDir, 0755)
	agentYAML := "agents:\n  glm:\n    home_node: prya\n    provider: zhipu\n    model: glm-4\n    cost_class: cheap\n    ram_gb: 0.3\n    weight: light\n    forbidden_nodes: []\n    best_at: []\n    avoid_for: []\n    status: BUSY\n    quirks: \"\"\n    stage_preference: \"\"\n"
	_ = os.WriteFile(configDir+"/agent-registry.yaml", []byte(agentYAML), 0644)
	nodeYAML := "nodes:\n  prya:\n    role: hub\n    ram_gb: 16\n    cpu_cores: 8\n    class: light\n    allowed_workloads: [small-edits]\n    forbidden_workloads: []\n    max_agents: 2\n    notes: \"\"\n"
	_ = os.WriteFile(configDir+"/node-registry.yaml", []byte(nodeYAML), 0644)
	_ = os.WriteFile(configDir+"/task-envelope-v1.yaml", []byte("version: \"1\"\n"), 0644)
	t.Setenv("FORGE_ROOT", dir)

	body := `{"task_type":"coverage"}`
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", strings.NewReader(body))
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 when no eligible agents, got %d", w.Code)
	}
}

// TestWave141_RoutingHandler_BadConfigDir verifies 503 when config dir is missing.
func TestWave141_RoutingHandler_BadConfigDir(t *testing.T) {
	t.Setenv("FORGE_ROOT", "/nonexistent-path-wave139")
	body := `{"task_type":"coverage"}`
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", strings.NewReader(body))
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for bad config dir, got %d", w.Code)
	}
}

// TestWave141_RoutingHandler_InvalidJSON verifies 400 on bad request body.
func TestWave141_RoutingHandler_InvalidJSON(t *testing.T) {
	body := `{invalid json`
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", strings.NewReader(body))
	req.ContentLength = int64(len(body))
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// Target 3: postGithubComment — non-201 status path (80%→90%+)
// ---------------------------------------------------------------------------

// TestWave141_PostGithubComment_Non201 covers the log path when the API
// returns a non-201 response (e.g. 422 Unprocessable Entity).
func TestWave141_PostGithubComment_Non201(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnprocessableEntity)
		w.Write([]byte(`{"message":"Validation Failed"}`))
	}))
	defer srv.Close()

	orig := githubAPIBaseURL
	githubAPIBaseURL = srv.URL
	defer func() { githubAPIBaseURL = orig }()

	t.Setenv("GITHUB_TOKEN", "test-token-warn")

	// Should log WARN but not panic or return error.
	postGithubComment("org/repo", 99, "test comment body")
}

// TestWave141_PostGithubComment_201 covers the success path (status 201).
func TestWave141_PostGithubComment_201(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusCreated)
		w.Write([]byte(`{"id":1}`))
	}))
	defer srv.Close()

	orig := githubAPIBaseURL
	githubAPIBaseURL = srv.URL
	defer func() { githubAPIBaseURL = orig }()

	t.Setenv("GITHUB_TOKEN", "test-token-ok")
	postGithubComment("org/repo", 100, "hello")
}

// ---------------------------------------------------------------------------
// Helper: writeMinimalRoutingYAML creates a valid routing config in dir.
// Re-exported from routing_config_test.go logic; duplicated here to keep
// wave 139 self-contained.
// ---------------------------------------------------------------------------

func writeMinimalRoutingYAML141(t *testing.T, root string) {
	t.Helper()
	configDir := root + "/config/routing"
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	agentYAML := `agents:
  kimi:
    home_node: sati
    provider: moonshot
    model: kimi-k2
    cost_class: cheap
    ram_gb: 0.5
    weight: light
    forbidden_nodes: []
    best_at: [go-test, coverage]
    avoid_for: []
    status: OK
    quirks: ""
    stage_preference: ""
`
	if err := os.WriteFile(configDir+"/agent-registry.yaml", []byte(agentYAML), 0644); err != nil {
		t.Fatalf("write agent yaml: %v", err)
	}

	nodeYAML := `nodes:
  sati:
    role: worker
    ram_gb: 64
    cpu_cores: 32
    class: heavy
    allowed_workloads: [go-test, ios-builds, docs, small-edits, complex-edits, complex-reasoning, research, coverage]
    forbidden_workloads: []
    max_agents: 6
    notes: ""
`
	if err := os.WriteFile(configDir+"/node-registry.yaml", []byte(nodeYAML), 0644); err != nil {
		t.Fatalf("write node yaml: %v", err)
	}

	envelopeYAML := `version: "1"
fields: []
`
	if err := os.WriteFile(configDir+"/task-envelope-v1.yaml", []byte(envelopeYAML), 0644); err != nil {
		t.Fatalf("write envelope yaml: %v", err)
	}

	// Also write portfolio-state.yaml so lookupPortfolioStage doesn't fail in
	// other wave tests that need it — not required here but keeps dirs complete.
	_ = os.MkdirAll(root+"/config/portfolio", 0755)
	portfolioYAML := "version: \"1\"\nproducts: []\n"
	_ = os.WriteFile(root+"/config/portfolio/portfolio-state.yaml", []byte(portfolioYAML), 0644)
}
