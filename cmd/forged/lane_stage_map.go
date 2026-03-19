//go:build !tmux_bridge
// +build !tmux_bridge

package main

import "strings"

// stageToLane maps a portfolio stage to the appropriate execution lane.
// Returns "default" for unmapped or unknown stages.
//
// Mapping rationale:
//   - idea/validate → research: early discovery work has minimal quality gates
//   - build         → implementation: feature development with test+lint+build gates
//   - deploy        → release: deployment-ready code requires approval + strict gates
//   - measure/monetize → analytics: metrics-focused work with relaxed coverage gates
//   - scale         → implementation: scaling is still building, reuses the same lane
//   - kill          → default: minimal work, no special lane needed
func stageToLane(stage string) string {
	switch strings.ToLower(stage) {
	case "idea", "validate":
		return "research"
	case "build":
		return "implementation"
	case "deploy":
		return "release"
	case "measure", "monetize":
		return "analytics"
	case "scale":
		return "implementation" // scale = more building
	case "kill":
		return "default" // minimal work
	default:
		return "default"
	}
}

// AutoAssignLane determines the execution lane for a task based on the product's
// portfolio stage.
//
// Precedence rules (highest to lowest):
//  1. explicitLane — caller provided a specific lane; always honoured.
//  2. Portfolio stage lookup via EnforceStageGate — resolves domain → stage → lane.
//  3. "default" — fallback when domain is unknown or stage is empty.
//
// The function is intentionally side-effect-free: it reads portfolio-state.yaml
// through the cached EnforceStageGate path and never writes anything.
func AutoAssignLane(domain string, taskType string, explicitLane string) string {
	if explicitLane != "" {
		return explicitLane
	}
	result := EnforceStageGate(domain, taskType)
	if result.Stage == "" {
		return "default"
	}
	return stageToLane(result.Stage)
}
