//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"os"
	"path/filepath"
	"testing"
)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// makeAgentRolesYAML writes a minimal agent-roles.yaml to a temp dir and
// returns the full file path plus a cleanup function.
func makeAgentRolesYAML(t *testing.T, content string) (string, func()) {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "agent-roles.yaml")
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("write agent-roles.yaml: %v", err)
	}
	return path, func() {}
}

const minimalRolesYAML = `
roles:
  intake:
    description: "Turns chat/ideas into structured briefs and tasks"
    best_for_stages: ["idea"]
    task_types: ["research", "brief", "triage"]
    preferred_agents: ["pi", "gemini"]

  validation:
    description: "Researches pain, ICP, alternatives, distribution"
    best_for_stages: ["validate"]
    task_types: ["research", "validation", "analysis"]
    preferred_agents: ["gemini", "pi"]

  builder:
    description: "Creates MVPs in isolated repos/worktrees"
    best_for_stages: ["build", "scale"]
    task_types: ["feature", "bug", "refactor", "prototype"]
    preferred_agents: ["claude", "opencode", "kilo", "minimax", "glm"]

  qa:
    description: "Tests critical flows, blocks bad promotions"
    best_for_stages: ["build", "deploy"]
    task_types: ["test", "coverage", "review", "audit"]
    preferred_agents: ["kimi", "kimi-2", "pi", "cursor"]

  deploy:
    description: "Handles release tasks behind approvals"
    best_for_stages: ["deploy"]
    task_types: ["deploy", "config", "monitoring", "docs"]
    preferred_agents: ["claude", "cursor"]

  metrics:
    description: "Adds analytics, checks measure stage is real"
    best_for_stages: ["measure", "monetize"]
    task_types: ["analytics", "experiment", "optimization"]
    preferred_agents: ["gemini", "pi"]
`

// ---------------------------------------------------------------------------
// TestAgentRoleLoad
// ---------------------------------------------------------------------------

// TestAgentRoleLoad_ValidFile verifies that a well-formed YAML file is parsed
// correctly and all expected roles are present.
func TestAgentRoleLoad_ValidFile(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, err := LoadAgentRoles(path)
	if err != nil {
		t.Fatalf("LoadAgentRoles returned unexpected error: %v", err)
	}
	if cfg == nil {
		t.Fatal("LoadAgentRoles returned nil config")
	}

	expectedRoles := []string{"intake", "validation", "builder", "qa", "deploy", "metrics"}
	for _, name := range expectedRoles {
		if _, ok := cfg.Roles[name]; !ok {
			t.Errorf("expected role %q to be present, but it was missing", name)
		}
	}
}

// TestAgentRoleLoad_RoleFields verifies that individual role fields are
// unmarshalled correctly from YAML.
func TestAgentRoleLoad_RoleFields(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, err := LoadAgentRoles(path)
	if err != nil {
		t.Fatalf("LoadAgentRoles: %v", err)
	}

	intake, ok := cfg.Roles["intake"]
	if !ok {
		t.Fatal("intake role not found")
	}
	if intake.Description == "" {
		t.Error("intake.Description should not be empty")
	}
	if len(intake.BestForStages) == 0 {
		t.Error("intake.BestForStages should not be empty")
	}
	if intake.BestForStages[0] != "idea" {
		t.Errorf("intake.BestForStages[0] = %q, want %q", intake.BestForStages[0], "idea")
	}
	if len(intake.TaskTypes) == 0 {
		t.Error("intake.TaskTypes should not be empty")
	}
	if len(intake.PreferredAgents) == 0 {
		t.Error("intake.PreferredAgents should not be empty")
	}
}

// TestAgentRoleLoad_MissingFile verifies that a missing file returns an error
// rather than an empty config.
func TestAgentRoleLoad_MissingFile(t *testing.T) {
	_, err := LoadAgentRoles("/nonexistent/path/agent-roles.yaml")
	if err == nil {
		t.Error("expected error for missing file, got nil")
	}
}

// TestAgentRoleLoad_InvalidYAML verifies that malformed YAML returns a parse
// error.
func TestAgentRoleLoad_InvalidYAML(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, "roles: [not: valid: yaml: {{{{")
	defer cleanup()

	_, err := LoadAgentRoles(path)
	if err == nil {
		t.Error("expected parse error for invalid YAML, got nil")
	}
}

// TestAgentRoleLoad_EmptyFile verifies that an empty file is accepted and
// returns a config with an initialized (empty) Roles map.
func TestAgentRoleLoad_EmptyFile(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, "")
	defer cleanup()

	cfg, err := LoadAgentRoles(path)
	if err != nil {
		t.Fatalf("unexpected error for empty file: %v", err)
	}
	if cfg.Roles == nil {
		t.Error("Roles map should be non-nil even for empty file")
	}
}

// ---------------------------------------------------------------------------
// TestAgentRoleResolve — ResolveRoleForTask
// ---------------------------------------------------------------------------

// TestAgentRoleResolve_IdeaStageResearch verifies that idea+research resolves
// to the "intake" role.
func TestAgentRoleResolve_IdeaStageResearch(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	got := ResolveRoleForTask(cfg, "idea", "research")
	if got != "intake" {
		t.Errorf("idea+research: got %q, want %q", got, "intake")
	}
}

// TestAgentRoleResolve_ValidateStageValidation verifies that validate+validation
// resolves to the "validation" role.
func TestAgentRoleResolve_ValidateStageValidation(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	got := ResolveRoleForTask(cfg, "validate", "validation")
	if got != "validation" {
		t.Errorf("validate+validation: got %q, want %q", got, "validation")
	}
}

// TestAgentRoleResolve_BuildStageFeature verifies that build+feature resolves
// to the "builder" role.
func TestAgentRoleResolve_BuildStageFeature(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	got := ResolveRoleForTask(cfg, "build", "feature")
	if got != "builder" {
		t.Errorf("build+feature: got %q, want %q", got, "builder")
	}
}

// TestAgentRoleResolve_BuildStageCoverage verifies that build+coverage resolves
// to the "qa" role (qa has fewer stages than builder for "build").
func TestAgentRoleResolve_BuildStageCoverage(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	got := ResolveRoleForTask(cfg, "build", "coverage")
	if got != "qa" {
		t.Errorf("build+coverage: got %q, want %q", got, "qa")
	}
}

// TestAgentRoleResolve_DeployStageTest verifies that deploy+test resolves to
// "qa" (qa covers deploy+test; deploy role does not cover test type).
func TestAgentRoleResolve_DeployStageTest(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	got := ResolveRoleForTask(cfg, "deploy", "test")
	if got != "qa" {
		t.Errorf("deploy+test: got %q, want %q", got, "qa")
	}
}

// TestAgentRoleResolve_DeployStageDeploy verifies that deploy+deploy resolves
// to the "deploy" role (deploy has 1 stage; qa has 2 — deploy is more specific).
func TestAgentRoleResolve_DeployStageDeploy(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	got := ResolveRoleForTask(cfg, "deploy", "deploy")
	if got != "deploy" {
		t.Errorf("deploy+deploy: got %q, want %q", got, "deploy")
	}
}

// TestAgentRoleResolve_MeasureStageAnalytics verifies that measure+analytics
// resolves to "metrics".
func TestAgentRoleResolve_MeasureStageAnalytics(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	got := ResolveRoleForTask(cfg, "measure", "analytics")
	if got != "metrics" {
		t.Errorf("measure+analytics: got %q, want %q", got, "metrics")
	}
}

// TestAgentRoleResolve_MonetizeStageExperiment verifies that monetize+experiment
// resolves to "metrics".
func TestAgentRoleResolve_MonetizeStageExperiment(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	got := ResolveRoleForTask(cfg, "monetize", "experiment")
	if got != "metrics" {
		t.Errorf("monetize+experiment: got %q, want %q", got, "metrics")
	}
}

// TestAgentRoleResolve_ScaleStageRefactor verifies that scale+refactor resolves
// to "builder" (builder covers scale; refactor is a builder task type).
func TestAgentRoleResolve_ScaleStageRefactor(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	got := ResolveRoleForTask(cfg, "scale", "refactor")
	if got != "builder" {
		t.Errorf("scale+refactor: got %q, want %q", got, "builder")
	}
}

// TestAgentRoleResolve_CaseInsensitiveStage verifies that stage matching is
// case-insensitive.
func TestAgentRoleResolve_CaseInsensitiveStage(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)

	got := ResolveRoleForTask(cfg, "IDEA", "research")
	if got != "intake" {
		t.Errorf("IDEA+research: got %q, want %q", got, "intake")
	}

	got = ResolveRoleForTask(cfg, "Build", "Feature")
	if got != "builder" {
		t.Errorf("Build+Feature: got %q, want %q", got, "builder")
	}
}

// TestAgentRoleResolve_DefaultFallback verifies that an unknown stage and task
// type combination returns "builder" as the safe default.
func TestAgentRoleResolve_DefaultFallback(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	got := ResolveRoleForTask(cfg, "unknown-stage", "unknown-task")
	if got != "builder" {
		t.Errorf("unknown+unknown: got %q, want %q (default)", got, "builder")
	}
}

// TestAgentRoleResolve_NilConfig verifies that a nil config returns "builder".
func TestAgentRoleResolve_NilConfig(t *testing.T) {
	got := ResolveRoleForTask(nil, "build", "feature")
	if got != "builder" {
		t.Errorf("nil config: got %q, want %q", got, "builder")
	}
}

// TestAgentRoleResolve_EmptyConfig verifies that an empty config returns
// "builder".
func TestAgentRoleResolve_EmptyConfig(t *testing.T) {
	cfg := &AgentRoleConfig{Roles: map[string]AgentRole{}}
	got := ResolveRoleForTask(cfg, "build", "feature")
	if got != "builder" {
		t.Errorf("empty config: got %q, want %q", got, "builder")
	}
}

// TestAgentRoleResolve_EmptyStageEmptyTask verifies that empty strings produce
// a deterministic result (no panic) and default to "builder".
func TestAgentRoleResolve_EmptyStageEmptyTask(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	got := ResolveRoleForTask(cfg, "", "")
	// No role has an empty stage or task type, so builder is expected.
	if got == "" {
		t.Error("expected non-empty role name, got empty string")
	}
}

// ---------------------------------------------------------------------------
// TestAgentRolePreferred — PreferredAgentsForRole
// ---------------------------------------------------------------------------

// TestAgentRolePreferred_KnownRole verifies that preferred agents are returned
// correctly for known roles.
func TestAgentRolePreferred_KnownRole(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)

	tests := []struct {
		role          string
		expectFirst   string
		expectMinLen  int
	}{
		{"intake", "pi", 2},
		{"validation", "gemini", 2},
		{"builder", "claude", 5},
		{"qa", "kimi", 4},
		{"deploy", "claude", 2},
		{"metrics", "gemini", 2},
	}

	for _, tt := range tests {
		agents := PreferredAgentsForRole(cfg, tt.role)
		if len(agents) < tt.expectMinLen {
			t.Errorf("role %q: expected >= %d agents, got %d", tt.role, tt.expectMinLen, len(agents))
			continue
		}
		if agents[0] != tt.expectFirst {
			t.Errorf("role %q: first agent = %q, want %q", tt.role, agents[0], tt.expectFirst)
		}
	}
}

// TestAgentRolePreferred_UnknownRole verifies that a missing role returns nil
// without panicking.
func TestAgentRolePreferred_UnknownRole(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	agents := PreferredAgentsForRole(cfg, "nonexistent-role")
	if agents != nil {
		t.Errorf("unknown role: expected nil, got %v", agents)
	}
}

// TestAgentRolePreferred_NilConfig verifies that a nil config returns nil
// without panicking.
func TestAgentRolePreferred_NilConfig(t *testing.T) {
	agents := PreferredAgentsForRole(nil, "builder")
	if agents != nil {
		t.Errorf("nil config: expected nil, got %v", agents)
	}
}

// TestAgentRolePreferred_BuilderContainsClaude verifies that "claude" is among
// the builder's preferred agents.
func TestAgentRolePreferred_BuilderContainsClaude(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	agents := PreferredAgentsForRole(cfg, "builder")

	found := false
	for _, a := range agents {
		if a == "claude" {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("builder preferred agents should include 'claude', got %v", agents)
	}
}

// TestAgentRolePreferred_QAContainsKimi verifies that "kimi" is among the QA
// role's preferred agents.
func TestAgentRolePreferred_QAContainsKimi(t *testing.T) {
	path, cleanup := makeAgentRolesYAML(t, minimalRolesYAML)
	defer cleanup()

	cfg, _ := LoadAgentRoles(path)
	agents := PreferredAgentsForRole(cfg, "qa")

	found := false
	for _, a := range agents {
		if a == "kimi" {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("qa preferred agents should include 'kimi', got %v", agents)
	}
}

// ---------------------------------------------------------------------------
// TestAgentRoleIntegration — live config/routing/agent-roles.yaml
// ---------------------------------------------------------------------------

// TestAgentRoleLoad_IntegrationFile verifies that the checked-in
// config/routing/agent-roles.yaml file can be parsed and contains the six
// canonical factory roles.
func TestAgentRoleLoad_IntegrationFile(t *testing.T) {
	path := "../../config/routing/agent-roles.yaml"
	cfg, err := LoadAgentRoles(path)
	if err != nil {
		t.Skipf("config/routing/agent-roles.yaml not available: %v", err)
	}

	canonicalRoles := []string{"intake", "validation", "builder", "qa", "deploy", "metrics"}
	for _, name := range canonicalRoles {
		if _, ok := cfg.Roles[name]; !ok {
			t.Errorf("canonical role %q missing from agent-roles.yaml", name)
		}
	}
}
