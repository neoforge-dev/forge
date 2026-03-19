//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// TestWave68_AgentTier verifies agentTier returns correct tier for each type.
func TestWave68_AgentTier(t *testing.T) {
	tests := []struct {
		agentType string
		wantTier  string
	}{
		{"kimi", "lightweight"},
		{"minimax", "lightweight"},
		{"pi", "lightweight"},
		{"gemini", "lightweight"},
		{"glm", "lightweight"},
		{"claude", "medium"},
		{"cursor", "medium"},
		{"amp", "medium"},
		{"opencode", "heavy"},
		{"kilo", "heavy"},
		{"unknown-agent", "medium"}, // unknown defaults to medium (conservative)
	}
	for _, tt := range tests {
		t.Run(tt.agentType, func(t *testing.T) {
			got := agentTier(tt.agentType)
			if got != tt.wantTier {
				t.Errorf("agentTier(%q) = %q, want %q", tt.agentType, got, tt.wantTier)
			}
		})
	}
}

// TestWave68_RequiresHumanApproval verifies the node-differentiated approval policy.
func TestWave68_RequiresHumanApproval(t *testing.T) {
	tests := []struct {
		agentType string
		nodeID    string
		want      bool
	}{
		{"kimi", "prya", false},     // lightweight — always auto
		{"gemini", "sati", false},   // lightweight — always auto
		{"claude", "sati", false},   // medium on sati — auto
		{"claude", "prya", true},    // medium on non-sati — manual
		{"opencode", "sati", true},  // heavy — always manual
		{"kilo", "prya", true},      // heavy — always manual
	}
	for _, tt := range tests {
		t.Run(tt.agentType+"@"+tt.nodeID, func(t *testing.T) {
			got := requiresHumanApproval(tt.agentType, tt.nodeID)
			if got != tt.want {
				t.Errorf("requiresHumanApproval(%q, %q) = %v, want %v", tt.agentType, tt.nodeID, got, tt.want)
			}
		})
	}
}

// TestWave68_IsAgentForbiddenOnNode verifies forbidden agent rules.
func TestWave68_IsAgentForbiddenOnNode(t *testing.T) {
	tests := []struct {
		agentType string
		nodeID    string
		want      bool
	}{
		{"opencode", "prya", true},
		{"kilo", "prya", true},
		{"kimi", "prya", false},
		{"opencode", "sati", false}, // sati not in forbidden map
		{"opencode", "vega", true},
		{"kilo", "gaea", true},
		{"kimi", "unknown-node", false},
	}
	for _, tt := range tests {
		t.Run(tt.agentType+"@"+tt.nodeID, func(t *testing.T) {
			got := isAgentForbiddenOnNode(tt.agentType, tt.nodeID)
			if got != tt.want {
				t.Errorf("isAgentForbiddenOnNode(%q, %q) = %v, want %v", tt.agentType, tt.nodeID, got, tt.want)
			}
		})
	}
}

// TestWave68_AgentProvider verifies agentProvider returns correct provider strings.
func TestWave68_AgentProvider(t *testing.T) {
	tests := []struct {
		agentType string
		want      string
	}{
		{"kimi", "moonshot"},
		{"minimax", "minimax"},
		{"pi", "inflection"},
		{"gemini", "google"},
		{"claude", "anthropic"},
		{"amp", "anthropic"},
		{"cursor", "cursor"},
		{"opencode", "openai"},
		{"kilo", "openai"},
		{"mystery-agent", "unknown"},
	}
	for _, tt := range tests {
		t.Run(tt.agentType, func(t *testing.T) {
			got := agentProvider(tt.agentType)
			if got != tt.want {
				t.Errorf("agentProvider(%q) = %q, want %q", tt.agentType, got, tt.want)
			}
		})
	}
}

// TestWave68_CheckTokenBudgetGate_NoBudgetFile verifies the gate blocks when no budget file.
// P0 fix: fail-closed — no budget file → block spawn until file is restored.
func TestWave68_CheckTokenBudgetGate_NoBudgetFile(t *testing.T) {
	ok, reason := checkTokenBudgetGate("moonshot")
	if ok {
		t.Errorf("expected gate to block (fail-closed) when no budget file, got pass; reason: %s", reason)
	}
}

// TestWave68_CheckTokenBudgetGate_Cooldown verifies the gate blocks COOLDOWN providers.
func TestWave68_CheckTokenBudgetGate_Cooldown(t *testing.T) {
	// Write a fake budget file in the expected location.
	dir := ".forge/heartbeat"
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	budgetFile := filepath.Join(dir, "token-budgets-prya.json")
	content := `{"providers":{"moonshot":{"status":"COOLDOWN"},"google":{"status":"OK"}}}`
	if err := os.WriteFile(budgetFile, []byte(content), 0o644); err != nil {
		t.Fatalf("write budget file: %v", err)
	}
	defer os.Remove(budgetFile)

	ok, reason := checkTokenBudgetGate("moonshot")
	if ok {
		t.Errorf("expected gate to FAIL for COOLDOWN provider, reason: %s", reason)
	}

	// Non-cooldown provider should pass.
	ok2, _ := checkTokenBudgetGate("google")
	if !ok2 {
		t.Error("expected gate to PASS for OK provider")
	}
}

// TestWave68_CheckTokenBudgetGate_Depleted verifies the gate blocks DEPLETED providers.
func TestWave68_CheckTokenBudgetGate_Depleted(t *testing.T) {
	dir := ".forge/heartbeat"
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	budgetFile := filepath.Join(dir, "token-budgets-prya.json")
	content := `{"providers":{"openai":{"status":"DEPLETED"}}}`
	if err := os.WriteFile(budgetFile, []byte(content), 0o644); err != nil {
		t.Fatalf("write budget file: %v", err)
	}
	defer os.Remove(budgetFile)

	ok, reason := checkTokenBudgetGate("openai")
	if ok {
		t.Errorf("expected gate to FAIL for DEPLETED provider, reason: %s", reason)
	}
	if reason != "provider DEPLETED" {
		t.Errorf("expected reason 'provider DEPLETED', got: %s", reason)
	}
}

// TestWave68_CheckTokenBudgetGate_Red verifies the gate blocks RED zone providers.
func TestWave68_CheckTokenBudgetGate_Red(t *testing.T) {
	dir := ".forge/heartbeat"
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	budgetFile := filepath.Join(dir, "token-budgets-prya.json")
	content := `{"providers":{"anthropic":{"status":"RED"}}}`
	if err := os.WriteFile(budgetFile, []byte(content), 0o644); err != nil {
		t.Fatalf("write budget file: %v", err)
	}
	defer os.Remove(budgetFile)

	ok, reason := checkTokenBudgetGate("anthropic")
	if ok {
		t.Errorf("expected gate to FAIL for RED provider, reason: %s", reason)
	}
	if reason != "provider in RED zone" {
		t.Errorf("expected reason 'provider in RED zone', got: %s", reason)
	}
}

// TestWave68_CheckTokenBudgetGate_UnknownProvider verifies gate passes for provider not in budget.
func TestWave68_CheckTokenBudgetGate_UnknownProvider(t *testing.T) {
	dir := ".forge/heartbeat"
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	budgetFile := filepath.Join(dir, "token-budgets-prya.json")
	content := `{"providers":{"moonshot":{"status":"OK"}}}`
	if err := os.WriteFile(budgetFile, []byte(content), 0o644); err != nil {
		t.Fatalf("write budget file: %v", err)
	}
	defer os.Remove(budgetFile)

	// Provider "inflection" not in budget file — should allow.
	ok, reason := checkTokenBudgetGate("inflection")
	if !ok {
		t.Errorf("expected gate to PASS for unknown provider, reason: %s", reason)
	}
}

// TestWave68_CheckTokenBudgetGate_MalformedJSON verifies gate allows on parse error.
func TestWave68_CheckTokenBudgetGate_MalformedJSON(t *testing.T) {
	dir := ".forge/heartbeat"
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	budgetFile := filepath.Join(dir, "token-budgets-prya.json")
	if err := os.WriteFile(budgetFile, []byte("{malformed"), 0o644); err != nil {
		t.Fatalf("write budget file: %v", err)
	}
	defer os.Remove(budgetFile)

	ok, reason := checkTokenBudgetGate("moonshot")
	// P0 fix: fail-closed — malformed budget file → block spawn until file is valid.
	if ok {
		t.Errorf("expected gate to block (fail-closed) on malformed JSON, got pass; reason: %s", reason)
	}
}

// TestWave68_CanScale_ZeroTime verifies canScale returns true when no scale event recorded.
func TestWave68_CanScale_ZeroTime(t *testing.T) {
	// Save and restore.
	lastScaleEvent.Lock()
	origTS := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Time{} // zero
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = origTS
		lastScaleEvent.Unlock()
	}()

	if !canScale() {
		t.Error("canScale should return true when no scale event recorded (zero time)")
	}
}

// TestWave68_CanScale_RecentEvent verifies canScale returns false within 60s of last scale.
func TestWave68_CanScale_RecentEvent(t *testing.T) {
	lastScaleEvent.Lock()
	origTS := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Now() // just now
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = origTS
		lastScaleEvent.Unlock()
	}()

	if canScale() {
		t.Error("canScale should return false within 60s of last scale event")
	}
}

// TestWave68_CircuitBreakerTripped_Closed verifies circuitBreakerTripped returns false when closed.
func TestWave68_CircuitBreakerTripped_Closed(t *testing.T) {
	circuitBreaker.Lock()
	orig := circuitBreaker.state
	circuitBreaker.state = "closed"
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = orig
		circuitBreaker.Unlock()
	}()

	if circuitBreakerTripped() {
		t.Error("circuitBreakerTripped should return false when state=closed")
	}
}

// TestWave68_FleetAutoExecute_PendingRec_LightweightKimi verifies that
// fleetAutoExecutePatrol proceeds past the RAM gate for a kimi (lightweight) agent
// on macOS where /proc/meminfo is absent (readLiveRAMMB returns error → returns nil).
func TestWave68_FleetAutoExecute_PendingRec_LightweightKimi(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Reset circuit breaker and flap guard.
	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()

	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Time{} // allow scaling
	lastScaleEvent.Unlock()

	expires := time.Now().UTC().Add(5 * time.Minute).Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO scale_recommendations (id, node_id, agent_type, action, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
	`, "wave68-kimi-rec", "prya", "kimi", "inflate", "queue backlog", 5, 0, 1, "pending", expires)
	if err != nil {
		t.Fatalf("insert recommendation: %v", err)
	}

	// Should not panic or error — on macOS readLiveRAMMB fails and function returns nil.
	err = fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil from fleetAutoExecutePatrol, got: %v", err)
	}
}

// TestWave68_MarkRecDeferred verifies markRecDeferred does not panic or return error.
// Note: the function uses _, _ = so it silently ignores SQL errors (e.g. missing updated_at column).
func TestWave68_MarkRecDeferred(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	expires := time.Now().UTC().Add(5 * time.Minute).Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO scale_recommendations (id, node_id, agent_type, action, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
	`, "wave68-defer-rec", "prya", "kimi", "inflate", "test", 3, 0, 1, "pending", expires)
	if err != nil {
		t.Fatalf("insert rec: %v", err)
	}

	// Should not panic — function silently ignores SQL errors.
	markRecDeferred(context.Background(), db, "wave68-defer-rec", "deferred-ram")
	markRecDeferred(context.Background(), db, "nonexistent-rec", "deferred-ram")
}

// TestWave68_MarkRecFailed verifies markRecFailed does not panic.
func TestWave68_MarkRecFailed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	expires := time.Now().UTC().Add(5 * time.Minute).Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO scale_recommendations (id, node_id, agent_type, action, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
	`, "wave68-fail-rec", "prya", "kimi", "inflate", "test", 3, 0, 1, "pending", expires)
	if err != nil {
		t.Fatalf("insert rec: %v", err)
	}

	// Should not panic — function silently ignores SQL errors.
	markRecFailed(context.Background(), db, "wave68-fail-rec", "spawn error: tmux not available")
	markRecFailed(context.Background(), db, "nonexistent-rec", "another error")
}
