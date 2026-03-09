package main

import (
	"encoding/json"
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
