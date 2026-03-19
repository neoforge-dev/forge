//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"os"
	"path/filepath"
	"testing"
)

// writePortfolioFixture writes a temporary portfolio-state.yaml and sets
// FORGE_ROOT so that loadPortfolioState() picks it up. It returns a cleanup
// function that restores the original env-var and resets the cache.
func writePortfolioFixture(t *testing.T, content string) func() {
	t.Helper()
	dir := t.TempDir()

	cfgDir := filepath.Join(dir, "config", "portfolio")
	if err := os.MkdirAll(cfgDir, 0o755); err != nil {
		t.Fatalf("mkdir %s: %v", cfgDir, err)
	}
	if err := os.WriteFile(filepath.Join(cfgDir, "portfolio-state.yaml"), []byte(content), 0o644); err != nil {
		t.Fatalf("write portfolio-state.yaml: %v", err)
	}

	origRoot := os.Getenv("FORGE_ROOT")
	os.Setenv("FORGE_ROOT", dir)

	// Reset the global cache so the new fixture is loaded on next call.
	globalPortfolioCache.mu.Lock()
	globalPortfolioCache.state = nil
	globalPortfolioCache.filePath = ""
	globalPortfolioCache.mu.Unlock()

	return func() {
		os.Setenv("FORGE_ROOT", origRoot)
		// Reset cache again so subsequent tests get a clean slate.
		globalPortfolioCache.mu.Lock()
		globalPortfolioCache.state = nil
		globalPortfolioCache.filePath = ""
		globalPortfolioCache.mu.Unlock()
	}
}

// minimalPortfolioYAML produces a minimal portfolio-state.yaml for use in
// tests.  Only the fields exercised by each test are present.
const testPortfolioYAML = `
version: "1"
products:
  - key: "idea-product"
    name: "Idea Product"
    domain: "idea-domain"
    stage: "idea"
    icp: "some icp"

  - key: "validate-product"
    name: "Validate Product"
    domain: "validate-domain"
    stage: "validate"
    icp: "some icp"
    primary_risk: "demand unknown"
    validation_hypothesis: "founders will pay to test ideas faster"

  - key: "build-product"
    name: "Build Product"
    domain: "build-domain"
    stage: "build"
    icp: "developers"
    primary_metric: "weekly active users"

  - key: "deploy-product"
    name: "Deploy Product"
    domain: "deploy-domain"
    stage: "deploy"
    deploy_ready: true
    analytics_ready: true

  - key: "measure-product"
    name: "Measure Product"
    domain: "measure-domain"
    stage: "measure"

  - key: "kill-product"
    name: "Kill Product"
    domain: "kill-domain"
    stage: "kill"

  - key: "missing-fields-product"
    name: "Missing Fields"
    domain: "missing-domain"
    stage: "build"
    # icp and primary_metric deliberately omitted
`

// TestStageGate_IdeaBlocksBuildTasks verifies that a product in 'idea' stage
// does not allow build-type tasks.
func TestStageGate_IdeaBlocksBuildTasks(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	blocked := []string{"feature", "bug", "test", "coverage", "deploy", "refactor", "analytics"}
	for _, tt := range blocked {
		result := EnforceStageGate("idea-domain", tt)
		if result.Allowed {
			t.Errorf("idea stage: expected task type %q to be blocked, but it was allowed", tt)
		}
		if result.BlockReason == "" {
			t.Errorf("idea stage: blocked result for %q should have a BlockReason", tt)
		}
	}
}

// TestStageGate_IdeaAllowsResearch verifies that research/validation/docs are
// permitted for products in the 'idea' stage.
func TestStageGate_IdeaAllowsResearch(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	allowed := []string{"research", "validation", "docs"}
	for _, tt := range allowed {
		result := EnforceStageGate("idea-domain", tt)
		if !result.Allowed {
			t.Errorf("idea stage: expected task type %q to be allowed, but it was blocked: %s", tt, result.BlockReason)
		}
	}
}

// TestStageGate_ValidateBlocksDeployAndBuild ensures that validate stage does
// not permit full build or deploy tasks.
func TestStageGate_ValidateBlocksDeployAndBuild(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	blocked := []string{"feature", "deploy", "bug", "coverage", "refactor", "analytics"}
	for _, tt := range blocked {
		result := EnforceStageGate("validate-domain", tt)
		if result.Allowed {
			t.Errorf("validate stage: expected task type %q to be blocked, got allowed", tt)
		}
	}
}

// TestStageGate_ValidateAllowsPrototype verifies that validate stage allows
// research, validation, prototype, and docs tasks.
func TestStageGate_ValidateAllowsPrototype(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	allowed := []string{"research", "validation", "prototype", "docs"}
	for _, tt := range allowed {
		result := EnforceStageGate("validate-domain", tt)
		if !result.Allowed {
			t.Errorf("validate stage: expected task type %q to be allowed, got blocked: %s", tt, result.BlockReason)
		}
	}
}

// TestStageGate_BuildAllowsFeatureTasks confirms that build stage permits
// standard development task types.
func TestStageGate_BuildAllowsFeatureTasks(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	allowed := []string{"feature", "bug", "test", "coverage", "refactor", "docs"}
	for _, tt := range allowed {
		result := EnforceStageGate("build-domain", tt)
		if !result.Allowed {
			t.Errorf("build stage: expected task type %q to be allowed, got blocked: %s", tt, result.BlockReason)
		}
	}
}

// TestStageGate_BuildBlocksDeploy verifies that deploy tasks are not allowed
// for products still in the build stage.
func TestStageGate_BuildBlocksDeploy(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	result := EnforceStageGate("build-domain", "deploy")
	if result.Allowed {
		t.Error("build stage: expected 'deploy' task type to be blocked, but it was allowed")
	}
}

// TestStageGate_DeployAllowsDeployAndBugFix checks that deploy-stage products
// can receive deploy, bug, test, and config tasks.
func TestStageGate_DeployAllowsDeployAndBugFix(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	allowed := []string{"deploy", "bug", "test", "config", "monitoring", "docs", "coverage"}
	for _, tt := range allowed {
		result := EnforceStageGate("deploy-domain", tt)
		if !result.Allowed {
			t.Errorf("deploy stage: expected task type %q to be allowed, got blocked: %s", tt, result.BlockReason)
		}
	}
}

// TestStageGate_MeasureAllowsAnalytics confirms measure-stage analytics tasks.
func TestStageGate_MeasureAllowsAnalytics(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	allowed := []string{"analytics", "experiment", "bug", "optimization", "research", "docs", "test", "coverage"}
	for _, tt := range allowed {
		result := EnforceStageGate("measure-domain", tt)
		if !result.Allowed {
			t.Errorf("measure stage: expected task type %q to be allowed, got blocked: %s", tt, result.BlockReason)
		}
	}
}

// TestStageGate_MeasureBlocksBuildFeatures ensures build-style features are not
// appropriate in the measure stage.
func TestStageGate_MeasureBlocksBuildFeatures(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	blocked := []string{"feature", "refactor", "deploy", "config"}
	for _, tt := range blocked {
		result := EnforceStageGate("measure-domain", tt)
		if result.Allowed {
			t.Errorf("measure stage: expected task type %q to be blocked, got allowed", tt)
		}
	}
}

// TestStageGate_KillStageOnlyAllowsBugAndDocs verifies that kill-stage products
// only accept bug fixes and documentation tasks.
func TestStageGate_KillStageOnlyAllowsBugAndDocs(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	allowed := []string{"bug", "docs"}
	for _, tt := range allowed {
		result := EnforceStageGate("kill-domain", tt)
		if !result.Allowed {
			t.Errorf("kill stage: expected task type %q to be allowed, got blocked: %s", tt, result.BlockReason)
		}
	}

	blocked := []string{"feature", "deploy", "coverage", "analytics", "refactor"}
	for _, tt := range blocked {
		result := EnforceStageGate("kill-domain", tt)
		if result.Allowed {
			t.Errorf("kill stage: expected task type %q to be blocked, got allowed", tt)
		}
	}
}

// TestStageGate_MissingRequiredFields checks that missing required fields
// produce an advisory warning but do not block the task.
func TestStageGate_MissingRequiredFields(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	// "missing-domain" is in stage "build" but has no icp or primary_metric.
	result := EnforceStageGate("missing-domain", "feature")
	if !result.Allowed {
		t.Errorf("missing required fields: expected gate to pass (advisory), got blocked: %s", result.BlockReason)
	}
	if result.Warning == "" {
		t.Error("missing required fields: expected a Warning message about missing fields, got empty")
	}
}

// TestStageGate_UnknownProductPassesThrough verifies that tasks for domains
// not listed in portfolio-state.yaml are always allowed.
func TestStageGate_UnknownProductPassesThrough(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	taskTypes := []string{"feature", "deploy", "bug", "analytics", "any-random-type"}
	for _, tt := range taskTypes {
		result := EnforceStageGate("completely-unknown-domain-xyz", tt)
		if !result.Allowed {
			t.Errorf("unknown domain: expected pass-through for task type %q, got blocked: %s", tt, result.BlockReason)
		}
	}
}

// TestStageGate_InfrastructureDomainBypass verifies that infrastructure domains
// (forge, forged, openclaw, github) are never blocked regardless of task type.
func TestStageGate_InfrastructureDomainBypass(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	infraDomains := []string{"forge", "forged", "openclaw", "github", "infrastructure"}
	taskTypes := []string{"deploy", "feature", "bug", "analytics", "refactor", "coverage"}

	for _, domain := range infraDomains {
		for _, tt := range taskTypes {
			result := EnforceStageGate(domain, tt)
			if !result.Allowed {
				t.Errorf("infra domain %q: expected bypass for task type %q, got blocked: %s", domain, tt, result.BlockReason)
			}
			if result.Warning != "" {
				t.Errorf("infra domain %q: expected no warning, got: %s", domain, result.Warning)
			}
		}
	}
}

// TestStageGate_ProductKeyLookup verifies EnforceStageGateByKey resolves
// products by key instead of domain.
func TestStageGate_ProductKeyLookup(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	// "idea-product" is in idea stage — feature tasks should be blocked.
	result := EnforceStageGateByKey("idea-product", "feature")
	if result.Allowed {
		t.Error("key lookup: expected 'feature' to be blocked for idea-product, got allowed")
	}

	// Research should be allowed.
	result = EnforceStageGateByKey("idea-product", "research")
	if !result.Allowed {
		t.Errorf("key lookup: expected 'research' to be allowed for idea-product, got blocked: %s", result.BlockReason)
	}
}

// TestStageGate_UnknownKeyPassesThrough verifies that an unrecognised product
// key does not block any task type.
func TestStageGate_UnknownKeyPassesThrough(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	result := EnforceStageGateByKey("nonexistent-product-key-xyz", "deploy")
	if !result.Allowed {
		t.Errorf("unknown key: expected pass-through, got blocked: %s", result.BlockReason)
	}
}

// TestStageGate_ResultFields checks that a blocked result carries the expected
// metadata fields.
func TestStageGate_ResultFields(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	result := EnforceStageGate("idea-domain", "deploy")
	if result.Allowed {
		t.Fatal("expected blocked, got allowed")
	}
	if result.ProductKey != "idea-product" {
		t.Errorf("expected ProductKey='idea-product', got %q", result.ProductKey)
	}
	if result.Stage != "idea" {
		t.Errorf("expected Stage='idea', got %q", result.Stage)
	}
	if result.BlockReason == "" {
		t.Error("expected non-empty BlockReason")
	}
}

// TestStageGate_MissingConfigFile verifies that a missing portfolio-state.yaml
// causes the gate to pass with a warning rather than blocking everything.
func TestStageGate_MissingConfigFile(t *testing.T) {
	// Use an empty temp dir with no config file.
	dir := t.TempDir()
	origRoot := os.Getenv("FORGE_ROOT")
	os.Setenv("FORGE_ROOT", dir)
	globalPortfolioCache.mu.Lock()
	globalPortfolioCache.state = nil
	globalPortfolioCache.filePath = ""
	globalPortfolioCache.mu.Unlock()
	defer func() {
		os.Setenv("FORGE_ROOT", origRoot)
		globalPortfolioCache.mu.Lock()
		globalPortfolioCache.state = nil
		globalPortfolioCache.filePath = ""
		globalPortfolioCache.mu.Unlock()
	}()

	result := EnforceStageGate("idea-domain", "feature")
	if !result.Allowed {
		t.Errorf("missing config: expected pass-through, got blocked: %s", result.BlockReason)
	}
}

// TestStageGate_CaseInsensitiveTaskType checks that task type matching is
// case-insensitive.
func TestStageGate_CaseInsensitiveTaskType(t *testing.T) {
	cleanup := writePortfolioFixture(t, testPortfolioYAML)
	defer cleanup()

	result := EnforceStageGate("idea-domain", "RESEARCH")
	if !result.Allowed {
		t.Errorf("expected 'RESEARCH' (uppercase) to be allowed in idea stage, got blocked: %s", result.BlockReason)
	}

	result = EnforceStageGate("idea-domain", "Feature")
	if result.Allowed {
		t.Errorf("expected 'Feature' (mixed case) to be blocked in idea stage, got allowed")
	}
}
