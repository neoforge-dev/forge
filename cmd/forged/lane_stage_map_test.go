//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"os"
	"path/filepath"
	"testing"
)

// TestLaneStageMapping covers every branch in stageToLane.
func TestLaneStageMapping(t *testing.T) {
	tests := []struct {
		stage    string
		wantLane string
	}{
		// idea/validate → research
		{"idea", "research"},
		{"validate", "research"},
		// build → implementation
		{"build", "implementation"},
		// deploy → release
		{"deploy", "release"},
		// measure/monetize → analytics
		{"measure", "analytics"},
		{"monetize", "analytics"},
		// scale → implementation (scale = more building)
		{"scale", "implementation"},
		// kill → default (minimal work)
		{"kill", "default"},
		// unknown/empty → default
		{"", "default"},
		{"unknown-stage", "default"},
		{"IDEA", "research"},  // case-insensitive
		{"BUILD", "implementation"},
		{"DEPLOY", "release"},
		{"MEASURE", "analytics"},
		{"SCALE", "implementation"},
		{"KILL", "default"},
	}

	for _, tt := range tests {
		t.Run("stage="+tt.stage, func(t *testing.T) {
			got := stageToLane(tt.stage)
			if got != tt.wantLane {
				t.Errorf("stageToLane(%q) = %q, want %q", tt.stage, got, tt.wantLane)
			}
		})
	}
}

// TestAutoAssignLane_ExplicitOverride verifies that an explicit lane always
// takes precedence, regardless of domain or task type.
func TestAutoAssignLane_ExplicitOverride(t *testing.T) {
	tests := []struct {
		domain       string
		taskType     string
		explicitLane string
		wantLane     string
	}{
		{"finance", "feature", "fast", "fast"},
		{"health", "bug", "quality", "quality"},
		{"", "", "custom-lane", "custom-lane"},
		{"infrastructure", "research", "release", "release"},
	}

	for _, tt := range tests {
		t.Run(tt.domain+"/"+tt.taskType+"/"+tt.explicitLane, func(t *testing.T) {
			got := AutoAssignLane(tt.domain, tt.taskType, tt.explicitLane)
			if got != tt.wantLane {
				t.Errorf("AutoAssignLane(%q, %q, %q) = %q, want %q",
					tt.domain, tt.taskType, tt.explicitLane, got, tt.wantLane)
			}
		})
	}
}

// TestAutoAssignLane_UnknownDomain verifies that an unknown domain returns
// "default" (no product found in portfolio-state.yaml, gate passes with
// empty Stage).
func TestAutoAssignLane_UnknownDomain(t *testing.T) {
	// Point FORGE_ROOT at a temp dir with no portfolio-state.yaml so the
	// cache returns an empty portfolioState.
	tmp := t.TempDir()
	if err := os.MkdirAll(filepath.Join(tmp, "config", "portfolio"), 0o755); err != nil {
		t.Fatal(err)
	}

	orig := os.Getenv("FORGE_ROOT")
	t.Setenv("FORGE_ROOT", tmp)
	defer func() {
		os.Setenv("FORGE_ROOT", orig)
		// Reset the global portfolio cache so subsequent tests get a clean slate.
		globalPortfolioCache.mu.Lock()
		globalPortfolioCache.state = nil
		globalPortfolioCache.filePath = ""
		globalPortfolioCache.mu.Unlock()
	}()
	// Reset cache before test
	globalPortfolioCache.mu.Lock()
	globalPortfolioCache.state = nil
	globalPortfolioCache.filePath = ""
	globalPortfolioCache.mu.Unlock()

	got := AutoAssignLane("nonexistent-domain", "feature", "")
	if got != "default" {
		t.Errorf("AutoAssignLane for unknown domain = %q, want %q", got, "default")
	}
}

// TestAutoAssignLane_KnownProduct verifies end-to-end mapping from a known
// product domain through its portfolio stage to the correct execution lane.
func TestAutoAssignLane_KnownProduct(t *testing.T) {
	tests := []struct {
		stage    string
		wantLane string
	}{
		{"build", "implementation"},
		{"validate", "research"},
		{"deploy", "release"},
		{"measure", "analytics"},
		{"scale", "implementation"},
		{"kill", "default"},
	}

	for _, tt := range tests {
		t.Run("stage="+tt.stage, func(t *testing.T) {
			// Write a minimal portfolio-state.yaml that defines one product.
			tmp := t.TempDir()
			dir := filepath.Join(tmp, "config", "portfolio")
			if err := os.MkdirAll(dir, 0o755); err != nil {
				t.Fatal(err)
			}
			yaml := `version: "1"
products:
  - key: testprod
    name: Test Product
    domain: testdomain
    stage: ` + tt.stage + `
    icp: "developers"
    primary_metric: "active_users"
`
			if err := os.WriteFile(filepath.Join(dir, "portfolio-state.yaml"), []byte(yaml), 0o644); err != nil {
				t.Fatal(err)
			}

			orig := os.Getenv("FORGE_ROOT")
			os.Setenv("FORGE_ROOT", tmp)

			// Reset cache so the new file is loaded.
			globalPortfolioCache.mu.Lock()
			globalPortfolioCache.state = nil
			globalPortfolioCache.filePath = ""
			globalPortfolioCache.mu.Unlock()

			got := AutoAssignLane("testdomain", "feature", "")

			// Restore env and cache.
			os.Setenv("FORGE_ROOT", orig)
			globalPortfolioCache.mu.Lock()
			globalPortfolioCache.state = nil
			globalPortfolioCache.filePath = ""
			globalPortfolioCache.mu.Unlock()

			if got != tt.wantLane {
				t.Errorf("AutoAssignLane(testdomain, feature, \"\") with stage %q = %q, want %q",
					tt.stage, got, tt.wantLane)
			}
		})
	}
}

// TestAutoAssignLane_InfrastructureDomain checks that infra domains return
// "default" since their stage is empty (infrastructure bypass in EnforceStageGate).
func TestAutoAssignLane_InfrastructureDomain(t *testing.T) {
	infraDomains := []string{"forge", "forged", "infrastructure", "openclaw"}
	for _, domain := range infraDomains {
		t.Run(domain, func(t *testing.T) {
			got := AutoAssignLane(domain, "feature", "")
			if got != "default" {
				t.Errorf("AutoAssignLane(%q, feature, \"\") = %q, want %q", domain, got, "default")
			}
		})
	}
}
