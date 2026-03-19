package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/neoforge-dev/forge/internal"
)

const testPortfolioState = `version: "1"
updated: "2026-03-08"
north_star: "Ship fewer products, but compound the ones with real revenue signals."
products:
  - key: "interview-simulator"
    name: "Interview Simulator"
    domain: "codeswiftr-com"
    repo_path: "codeswiftr-com/interview-simulator"
    stage: "measure"
    status: "active"
    owner: "prya-lead"
    icp: "job seekers preparing for behavioral interviews"
    current_mrr: 1200
    target_mrr: 10000
    deploy_ready: true
    analytics_ready: true
    billing_ready: true
    next_gate: "activation baseline"
    next_action: "publish and measure funnel performance"
    primary_metric: "weekly activated users"
    primary_risk: "top-of-funnel volume still low"
  - key: "voice-coach"
    name: "Voice Coach"
    domain: "brandfocus-ai"
    repo_path: "brandfocus-ai/voice-coach"
    stage: "deploy"
    status: "blocked"
    owner: "nova"
    icp: "founders practicing high-stakes speaking"
    current_mrr: 0
    target_mrr: 10000
    deploy_ready: true
    analytics_ready: false
    billing_ready: false
    next_gate: "public launch readiness"
    next_action: "finish launch checklist and analytics wiring"
    primary_metric: "first 20 qualified signups"
    primary_risk: "launch path not standardized"
  - key: "mvp-validator"
    name: "MVP Validator"
    domain: "leanvibe-ai"
    repo_path: "leanvibe-ai/mvp-validator"
    stage: "validate"
    status: "active"
    owner: "sati"
    icp: "solo founders validating B2B ideas"
    current_mrr: 0
    target_mrr: 3000
    deploy_ready: false
    analytics_ready: false
    billing_ready: false
    next_gate: "problem interviews complete"
    next_action: "run 10 interviews before building more"
    primary_metric: "number of validated pain points"
    primary_risk: "weak demand proof"
`

func TestLoadPortfolioStateFromEnv(t *testing.T) {
	tempFile := writeTestPortfolioState(t)
	t.Setenv("FORGE_PORTFOLIO_FILE", tempFile)

	state, err := loadPortfolioState()
	if err != nil {
		t.Fatalf("loadPortfolioState: %v", err)
	}

	if state.NorthStar == "" {
		t.Fatalf("expected north star to be loaded")
	}
	if len(state.Products) != 3 {
		t.Fatalf("expected 3 products, got %d", len(state.Products))
	}
}

func TestPortfolioStatusJSON(t *testing.T) {
	tempFile := writeTestPortfolioState(t)
	t.Setenv("FORGE_PORTFOLIO_FILE", tempFile)

	runner := NewTestRunner(t)
	output, err := runner.Execute("portfolio", "status", "--format", "json")
	if err != nil {
		t.Fatalf("portfolio status: %v\n%s", err, output)
	}

	var summary internal.PortfolioSummary
	if err := json.Unmarshal([]byte(output), &summary); err != nil {
		t.Fatalf("unmarshal summary: %v\n%s", err, output)
	}

	if summary.ProductCount != 3 {
		t.Fatalf("expected 3 products, got %d", summary.ProductCount)
	}
	if summary.RevenueProductCount != 1 {
		t.Fatalf("expected 1 revenue product, got %d", summary.RevenueProductCount)
	}
	if summary.StageCounts["measure"] != 1 {
		t.Fatalf("expected measure stage count, got %+v", summary.StageCounts)
	}
	if len(summary.PriorityProducts) == 0 || summary.PriorityProducts[0].Key != "voice-coach" {
		t.Fatalf("expected voice-coach to be top priority, got %+v", summary.PriorityProducts)
	}
}

func TestPortfolioListStageFilter(t *testing.T) {
	tempFile := writeTestPortfolioState(t)
	t.Setenv("FORGE_PORTFOLIO_FILE", tempFile)

	runner := NewTestRunner(t)
	output, err := runner.Execute("portfolio", "list", "--stage", "deploy")
	if err != nil {
		t.Fatalf("portfolio list: %v\n%s", err, output)
	}

	if !strings.Contains(output, "voice-coach") {
		t.Fatalf("expected deploy-stage product in output, got:\n%s", output)
	}
	if strings.Contains(output, "mvp-validator") {
		t.Fatalf("did not expect validate-stage product in filtered output:\n%s", output)
	}
}

func TestPortfolioShow(t *testing.T) {
	tempFile := writeTestPortfolioState(t)
	t.Setenv("FORGE_PORTFOLIO_FILE", tempFile)

	runner := NewTestRunner(t)
	output, err := runner.Execute("portfolio", "show", "interview-simulator")
	if err != nil {
		t.Fatalf("portfolio show: %v\n%s", err, output)
	}

	if !strings.Contains(output, "Current MRR:      $1200") {
		t.Fatalf("expected MRR details in output, got:\n%s", output)
	}
	if !strings.Contains(output, "Stage:            measure") {
		t.Fatalf("expected stage details in output, got:\n%s", output)
	}
}

func writeTestPortfolioState(t *testing.T) string {
	t.Helper()

	tempDir := t.TempDir()
	path := filepath.Join(tempDir, "portfolio-state.yaml")
	if err := os.WriteFile(path, []byte(testPortfolioState), 0644); err != nil {
		t.Fatalf("write portfolio state: %v", err)
	}
	return path
}

// ---------------------------------------------------------------------------
// portfolioAdvanceCmd tests
// ---------------------------------------------------------------------------

// TestPortfolioStageAdvance_NextStage verifies that advancing one step from a
// known stage yields the immediately following stage.
func TestPortfolioStageAdvance_NextStage(t *testing.T) {
	cases := []struct {
		from string
		want string
	}{
		{"idea", "validate"},
		{"validate", "build"},
		{"build", "deploy"},
		{"deploy", "measure"},
		{"measure", "monetize"},
		{"monetize", "scale"},
		{"scale", "kill"},
	}
	for _, tc := range cases {
		idx := portfolioStageIndex(tc.from)
		if idx >= len(portfolioStageOrder)-1 {
			t.Errorf("stage %q reported as final but should advance to %q", tc.from, tc.want)
			continue
		}
		got := portfolioStageOrder[idx+1]
		if got != tc.want {
			t.Errorf("advance from %q: got %q, want %q", tc.from, got, tc.want)
		}
	}
}

// TestPortfolioStageAdvance_FinalStageError verifies that trying to advance
// from "kill" (the last stage) returns an error.
func TestPortfolioStageAdvance_FinalStageError(t *testing.T) {
	tempFile := writeTestPortfolioState(t)

	// Add a product at the "kill" stage to the state file.
	killYAML := `version: "1"
updated: "2026-03-08"
north_star: "test"
products:
  - key: "dead-product"
    name: "Dead Product"
    domain: "test"
    repo_path: "test/dead"
    stage: "kill"
    status: "inactive"
    owner: "nobody"
    icp: "none"
    current_mrr: 0
    target_mrr: 0
    deploy_ready: false
    analytics_ready: false
    billing_ready: false
    next_gate: ""
    next_action: ""
    primary_metric: ""
    primary_risk: ""
`
	killFile, err := os.CreateTemp(t.TempDir(), "portfolio-*.yaml")
	if err != nil {
		t.Fatalf("create kill file: %v", err)
	}
	if _, err := killFile.WriteString(killYAML); err != nil {
		t.Fatalf("write kill file: %v", err)
	}
	killFile.Close()
	_ = tempFile // keep temp file alive for the env var pattern

	t.Setenv("FORGE_PORTFOLIO_FILE", killFile.Name())

	runner := NewTestRunner(t)
	_, err = runner.Execute("portfolio", "advance", "dead-product")
	if err == nil {
		t.Fatal("expected error advancing from final stage, got nil")
	}
	if !strings.Contains(err.Error(), "already at final stage") {
		t.Errorf("unexpected error: %v", err)
	}
}

// TestPortfolioAdvance_DryRun verifies the --dry-run flag prints the transition
// and does not write the file.
func TestPortfolioAdvance_DryRun(t *testing.T) {
	tempFile := writeTestPortfolioState(t)
	t.Setenv("FORGE_PORTFOLIO_FILE", tempFile)

	// Record the file's modification time before the dry-run.
	beforeStat, err := os.Stat(tempFile)
	if err != nil {
		t.Fatalf("stat before: %v", err)
	}

	runner := NewTestRunner(t)
	output, err := runner.Execute("portfolio", "advance", "voice-coach", "--dry-run")
	if err != nil {
		t.Fatalf("portfolio advance --dry-run: %v\n%s", err, output)
	}

	if !strings.Contains(output, "[dry-run]") {
		t.Errorf("expected [dry-run] in output, got:\n%s", output)
	}
	if !strings.Contains(output, "deploy → measure") {
		t.Errorf("expected stage transition in output, got:\n%s", output)
	}
	if !strings.Contains(output, "no changes written") {
		t.Errorf("expected 'no changes written' in output, got:\n%s", output)
	}

	// File must not have been modified.
	afterStat, err := os.Stat(tempFile)
	if err != nil {
		t.Fatalf("stat after: %v", err)
	}
	if afterStat.ModTime() != beforeStat.ModTime() {
		t.Errorf("file was modified during --dry-run")
	}
}

// TestPortfolioAdvance_WriteToFile verifies that a successful advance updates
// the product stage in the YAML file.
func TestPortfolioAdvance_WriteToFile(t *testing.T) {
	tempFile := writeTestPortfolioState(t)
	t.Setenv("FORGE_PORTFOLIO_FILE", tempFile)

	runner := NewTestRunner(t)
	output, err := runner.Execute("portfolio", "advance", "voice-coach")
	if err != nil {
		t.Fatalf("portfolio advance: %v\n%s", err, output)
	}

	if !strings.Contains(output, "deploy → measure") {
		t.Errorf("expected transition in output, got:\n%s", output)
	}

	// Re-load the file and verify the stage was persisted.
	state, err := loadPortfolioState()
	if err != nil {
		t.Fatalf("reload state: %v", err)
	}
	product, err := findPortfolioProduct(state.Products, "voice-coach")
	if err != nil {
		t.Fatalf("find product after advance: %v", err)
	}
	if product.Stage != "measure" {
		t.Errorf("expected stage=measure after advance, got %q", product.Stage)
	}
}

// TestPortfolioAdvance_JumpToStage verifies --to flag jumps directly to the
// specified stage.
func TestPortfolioAdvance_JumpToStage(t *testing.T) {
	tempFile := writeTestPortfolioState(t)
	t.Setenv("FORGE_PORTFOLIO_FILE", tempFile)

	runner := NewTestRunner(t)
	output, err := runner.Execute("portfolio", "advance", "voice-coach", "--to", "monetize")
	if err != nil {
		t.Fatalf("portfolio advance --to: %v\n%s", err, output)
	}

	if !strings.Contains(output, "deploy → monetize") {
		t.Errorf("expected deploy → monetize in output, got:\n%s", output)
	}

	state, err := loadPortfolioState()
	if err != nil {
		t.Fatalf("reload state: %v", err)
	}
	product, err := findPortfolioProduct(state.Products, "voice-coach")
	if err != nil {
		t.Fatalf("find product: %v", err)
	}
	if product.Stage != "monetize" {
		t.Errorf("expected stage=monetize, got %q", product.Stage)
	}
}

// TestStageTier verifies the tier lookup table is correct.
func TestStageTier(t *testing.T) {
	cases := []struct {
		stage string
		tier  string
	}{
		{"deploy", "desktop"},
		{"build", "desktop"},
		{"monetize", "desktop"},
		{"validate", "phone"},
		{"measure", "phone"},
		{"scale", "phone"},
		{"idea", "watch"},
		{"kill", "watch"},
	}
	for _, tc := range cases {
		got := stageTier(tc.stage)
		if got != tc.tier {
			t.Errorf("stageTier(%q) = %q, want %q", tc.stage, got, tc.tier)
		}
	}
}

// TestPortfolioStateCandidatesPreferConfigOnly verifies that config/portfolio/ is the
// first candidate and that the legacy docs/ path emits a deprecation notice.
func TestPortfolioStateCandidatesPreferConfigOnly(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("FORGE_ROOT", dir)
	// Unset explicit env overrides so the candidate list is used.
	t.Setenv("FORGE_PORTFOLIO_STATE", "")
	t.Setenv("FORGE_PORTFOLIO_FILE", "")

	candidates := portfolioStateCandidates()
	if len(candidates) == 0 {
		t.Fatal("portfolioStateCandidates() returned empty slice")
	}

	// First candidate must be the canonical config/ path with no deprecation notice.
	first := candidates[0]
	if !strings.Contains(first.path, "config/portfolio/portfolio-state.yaml") {
		t.Errorf("first candidate path = %q, want path containing config/portfolio/portfolio-state.yaml", first.path)
	}
	if first.deprecationNotice != "" {
		t.Errorf("first candidate has unexpected deprecation notice: %q", first.deprecationNotice)
	}

	// The legacy docs/ path must appear somewhere after the first candidate and carry a notice.
	found := false
	for _, c := range candidates[1:] {
		if strings.Contains(c.path, "docs/portfolio/portfolio-state.yaml") {
			found = true
			if c.deprecationNotice == "" {
				t.Errorf("legacy docs/ candidate has no deprecation notice")
			}
		}
	}
	if !found {
		t.Errorf("legacy docs/portfolio/portfolio-state.yaml candidate not found in list (needed for backward compat)")
	}
}

// --- queryRoutingForStage tests ----------------------------------------------

func TestQueryRoutingForStage_NoServer(t *testing.T) {
	// When the daemon is unreachable, must return "".
	t.Setenv("FORGE_API_URL", "http://127.0.0.1:1") // port 1 → connection refused
	t.Setenv("FORGE_API_KEY", "")
	got := queryRoutingForStage("build")
	if got != "" {
		t.Errorf("expected empty string on connection failure, got %q", got)
	}
}

func TestQueryRoutingForStage_Success(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/routing/resolve" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{
			"agent":  "kimi",
			"node":   "sati",
			"reason": "build stage weight=heavy",
		})
	}))
	defer srv.Close()

	t.Setenv("FORGE_API_URL", srv.URL)
	t.Setenv("FORGE_API_KEY", "test-key")

	got := queryRoutingForStage("build")
	if !strings.Contains(got, "kimi") {
		t.Errorf("expected agent kimi in result, got %q", got)
	}
	if !strings.Contains(got, "sati") {
		t.Errorf("expected node sati in result, got %q", got)
	}
}

func TestQueryRoutingForStage_ErrorResponse(t *testing.T) {
	// Non-200 responses → return "".
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "no routes configured", http.StatusNotFound)
	}))
	defer srv.Close()

	t.Setenv("FORGE_API_URL", srv.URL)
	got := queryRoutingForStage("deploy")
	if got != "" {
		t.Errorf("expected empty string on non-200, got %q", got)
	}
}
