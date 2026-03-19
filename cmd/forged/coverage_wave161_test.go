//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 161: fleet_scaler.go pure functions + global-state helpers
// Targets:
//   agentTier                  (fleet_scaler.go:70)  — lightweight/medium/heavy/unknown
//   requiresHumanApproval      (fleet_scaler.go:85)  — lightweight→false, heavy→true, medium varies
//   isAgentForbiddenOnNode     (fleet_scaler.go:97)  — known forbidden, unknown node, allowed
//   councilMarkerPath          (fleet_scaler.go:318) — path format
//   hasActiveCouncilMarker     (fleet_scaler.go:324) — no file, expired, active, malformed
//   circuitBreakerTripped      (fleet_scaler.go:638) — closed/open states
//   recordSpawnFailure         (fleet_scaler.go:655) — increments failures
//   recordSpawnSuccess         (fleet_scaler.go:668) — resets failures
//   canScale                   (fleet_scaler.go:682) — respects 60s flap guard
//   recordScaleEvent           (fleet_scaler.go:689) — updates timestamp
//   tierCanClaim               (fleet_scaler.go:1248)— tier compatibility
//   agentProvider              (fleet_scaler.go:1000)— provider mapping
//   ensureScaleRecommendations (fleet_scaler.go:124) — idempotent table creation
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// agentTier
// ---------------------------------------------------------------------------

func TestWave161_AgentTier_Lightweight(t *testing.T) {
	if got := agentTier("kimi"); got != "lightweight" {
		t.Errorf("expected lightweight, got %q", got)
	}
}

func TestWave161_AgentTier_Medium(t *testing.T) {
	if got := agentTier("claude"); got != "medium" {
		t.Errorf("expected medium, got %q", got)
	}
}

func TestWave161_AgentTier_Heavy(t *testing.T) {
	if got := agentTier("opencode"); got != "heavy" {
		t.Errorf("expected heavy, got %q", got)
	}
}

func TestWave161_AgentTier_Unknown(t *testing.T) {
	// Unknown types default to medium (conservative)
	if got := agentTier("mystery-agent"); got != "medium" {
		t.Errorf("expected medium for unknown, got %q", got)
	}
}

// ---------------------------------------------------------------------------
// requiresHumanApproval
// ---------------------------------------------------------------------------

func TestWave161_RequiresHumanApproval_Lightweight(t *testing.T) {
	// Lightweight never requires approval
	if requiresHumanApproval("kimi", "prya") {
		t.Error("expected lightweight to auto-approve")
	}
}

func TestWave161_RequiresHumanApproval_HeavyAlwaysManual(t *testing.T) {
	if !requiresHumanApproval("opencode", "sati") {
		t.Error("expected heavy to always require manual approval")
	}
}

func TestWave161_RequiresHumanApproval_MediumOnSati(t *testing.T) {
	// sati is in mediumTierAutoApproveNodes — medium auto-approved there
	if requiresHumanApproval("claude", "sati") {
		t.Error("expected medium to auto-approve on sati")
	}
}

func TestWave161_RequiresHumanApproval_MediumOnPrya(t *testing.T) {
	// prya is NOT in mediumTierAutoApproveNodes — medium requires manual
	if !requiresHumanApproval("claude", "prya") {
		t.Error("expected medium to require manual approval on prya")
	}
}

// ---------------------------------------------------------------------------
// isAgentForbiddenOnNode
// ---------------------------------------------------------------------------

func TestWave161_IsAgentForbiddenOnNode_Forbidden(t *testing.T) {
	// opencode is forbidden on prya
	if !isAgentForbiddenOnNode("opencode", "prya") {
		t.Error("expected opencode to be forbidden on prya")
	}
}

func TestWave161_IsAgentForbiddenOnNode_Allowed(t *testing.T) {
	// kimi is allowed on prya
	if isAgentForbiddenOnNode("kimi", "prya") {
		t.Error("expected kimi to be allowed on prya")
	}
}

func TestWave161_IsAgentForbiddenOnNode_UnknownNode(t *testing.T) {
	// Unknown node has no restrictions
	if isAgentForbiddenOnNode("opencode", "unknownnode99") {
		t.Error("expected no restriction on unknown node")
	}
}

// ---------------------------------------------------------------------------
// councilMarkerPath
// ---------------------------------------------------------------------------

func TestWave161_CouncilMarkerPath_Format(t *testing.T) {
	got := councilMarkerPath("kimi-42")
	want := ".forge/autoscale/council/kimi-42.json"
	if got != want {
		t.Errorf("councilMarkerPath = %q, want %q", got, want)
	}
}

// ---------------------------------------------------------------------------
// hasActiveCouncilMarker
// ---------------------------------------------------------------------------

func TestWave161_HasActiveCouncilMarker_NoFile(t *testing.T) {
	// No marker file → false
	if hasActiveCouncilMarker("nonexistent-agent-xyz") {
		t.Error("expected false for missing marker file")
	}
}

func TestWave161_HasActiveCouncilMarker_ActiveMarker(t *testing.T) {
	// Write a marker file with future expiry
	agentID := "kimi-wave161-test"
	path := councilMarkerPath(agentID)
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	marker := map[string]string{
		"expires_at": time.Now().Add(1 * time.Hour).Format(time.RFC3339),
	}
	data, _ := json.Marshal(marker)
	if err := os.WriteFile(path, data, 0644); err != nil {
		t.Fatalf("write: %v", err)
	}
	t.Cleanup(func() { os.Remove(path) })

	if !hasActiveCouncilMarker(agentID) {
		t.Error("expected true for active (non-expired) marker")
	}
}

func TestWave161_HasActiveCouncilMarker_ExpiredMarker(t *testing.T) {
	agentID := "kimi-wave161-expired"
	path := councilMarkerPath(agentID)
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	marker := map[string]string{
		"expires_at": time.Now().Add(-1 * time.Hour).Format(time.RFC3339),
	}
	data, _ := json.Marshal(marker)
	if err := os.WriteFile(path, data, 0644); err != nil {
		t.Fatalf("write: %v", err)
	}
	t.Cleanup(func() { os.Remove(path) })

	if hasActiveCouncilMarker(agentID) {
		t.Error("expected false for expired marker")
	}
}

func TestWave161_HasActiveCouncilMarker_MalformedJSON(t *testing.T) {
	// Malformed JSON → conservative: true
	agentID := "kimi-wave161-malformed"
	path := councilMarkerPath(agentID)
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(path, []byte("not json"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}
	t.Cleanup(func() { os.Remove(path) })

	if !hasActiveCouncilMarker(agentID) {
		t.Error("expected true (conservative) for malformed marker")
	}
}

// ---------------------------------------------------------------------------
// circuitBreakerTripped / recordSpawnFailure / recordSpawnSuccess
// ---------------------------------------------------------------------------

func TestWave161_CircuitBreaker_ClosedByDefault(t *testing.T) {
	// Reset to known state first
	recordSpawnSuccess()
	if circuitBreakerTripped() {
		t.Error("expected circuit breaker closed after reset")
	}
}

func TestWave161_CircuitBreaker_OpensAfterThreeFailures(t *testing.T) {
	recordSpawnSuccess() // reset
	recordSpawnFailure()
	recordSpawnFailure()
	recordSpawnFailure()
	tripped := circuitBreakerTripped()
	recordSpawnSuccess() // cleanup — reset for other tests
	if !tripped {
		t.Error("expected circuit breaker open after 3 failures")
	}
}

func TestWave161_CircuitBreaker_SuccessResets(t *testing.T) {
	recordSpawnFailure()
	recordSpawnFailure()
	recordSpawnFailure()
	recordSpawnSuccess()
	if circuitBreakerTripped() {
		t.Error("expected circuit breaker closed after success reset")
	}
}

// ---------------------------------------------------------------------------
// canScale / recordScaleEvent
// ---------------------------------------------------------------------------

func TestWave161_CanScale_AfterLongIdle(t *testing.T) {
	// Reset last scale event to far past
	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Now().Add(-5 * time.Minute)
	lastScaleEvent.Unlock()

	if !canScale() {
		t.Error("expected canScale=true after 5 minutes idle")
	}
}

func TestWave161_CanScale_BlockedAfterRecentEvent(t *testing.T) {
	recordScaleEvent()
	if canScale() {
		t.Error("expected canScale=false immediately after scale event")
	}
	// Reset for other tests
	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Time{}
	lastScaleEvent.Unlock()
}

// ---------------------------------------------------------------------------
// tierCanClaim
// ---------------------------------------------------------------------------

func TestWave161_TierCanClaim_EmptyRequired(t *testing.T) {
	if !tierCanClaim("lightweight", "") {
		t.Error("expected true for empty required tier")
	}
}

func TestWave161_TierCanClaim_AnyRequired(t *testing.T) {
	if !tierCanClaim("lightweight", "any") {
		t.Error("expected true for 'any' required tier")
	}
}

func TestWave161_TierCanClaim_HeavyClaimsAll(t *testing.T) {
	if !tierCanClaim("heavy", "lightweight") {
		t.Error("expected heavy to claim lightweight tasks")
	}
	if !tierCanClaim("heavy", "medium") {
		t.Error("expected heavy to claim medium tasks")
	}
	if !tierCanClaim("heavy", "heavy") {
		t.Error("expected heavy to claim heavy tasks")
	}
}

func TestWave161_TierCanClaim_MediumCannotClaimHeavy(t *testing.T) {
	if tierCanClaim("medium", "heavy") {
		t.Error("expected medium cannot claim heavy tasks")
	}
}

func TestWave161_TierCanClaim_LightweightCannotClaimMedium(t *testing.T) {
	if tierCanClaim("lightweight", "medium") {
		t.Error("expected lightweight cannot claim medium tasks")
	}
}

// ---------------------------------------------------------------------------
// agentProvider
// ---------------------------------------------------------------------------

func TestWave161_AgentProvider_KnownProviders(t *testing.T) {
	cases := []struct{ agent, want string }{
		{"kimi", "moonshot"},
		{"gemini", "google"},
		{"claude", "anthropic"},
		{"amp", "anthropic"},
		{"opencode", "openai"},
		{"kilo", "openai"},
	}
	for _, tc := range cases {
		if got := agentProvider(tc.agent); got != tc.want {
			t.Errorf("agentProvider(%q) = %q, want %q", tc.agent, got, tc.want)
		}
	}
}

func TestWave161_AgentProvider_Unknown(t *testing.T) {
	if got := agentProvider("mystery"); got != "unknown" {
		t.Errorf("expected 'unknown', got %q", got)
	}
}

// ---------------------------------------------------------------------------
// ensureScaleRecommendationsTable
// ---------------------------------------------------------------------------

func TestWave161_EnsureScaleRecommendationsTable_Idempotent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()

	// Should succeed on first call
	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("first call: %v", err)
	}
	// Should succeed on second call (IF NOT EXISTS)
	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("second call (idempotent): %v", err)
	}
}
