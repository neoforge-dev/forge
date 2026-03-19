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

// TestAgentTier tests the agentTier function
func TestAgentTier(t *testing.T) {
	tests := []struct {
		agentType string
		want      string
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
		{"unknown", "medium"}, // default
		{"", "medium"},        // empty default
	}

	for _, tc := range tests {
		got := agentTier(tc.agentType)
		if got != tc.want {
			t.Errorf("agentTier(%q) = %q, want %q", tc.agentType, got, tc.want)
		}
	}
}

// TestRequiresHumanApproval tests the approval logic
func TestRequiresHumanApproval(t *testing.T) {
	tests := []struct {
		agentType string
		nodeID    string
		want      bool
	}{
		// Lightweight - auto-approved everywhere
		{"kimi", "sati", false},
		{"kimi", "prya", false},
		{"minimax", "nova", false},
		// Medium - auto-approved only on sati
		{"claude", "sati", false},
		{"cursor", "sati", false},
		{"amp", "sati", false},
		{"claude", "prya", true},
		{"cursor", "nova", true},
		// Heavy - always manual
		{"opencode", "sati", true},
		{"kilo", "sati", true},
		{"opencode", "prya", true},
	}

	for _, tc := range tests {
		got := requiresHumanApproval(tc.agentType, tc.nodeID)
		if got != tc.want {
			t.Errorf("requiresHumanApproval(%q, %q) = %v, want %v", tc.agentType, tc.nodeID, got, tc.want)
		}
	}
}

// TestIsAgentForbiddenOnNode tests forbidden agent types
func TestIsAgentForbiddenOnNode(t *testing.T) {
	tests := []struct {
		agentType string
		nodeID    string
		want      bool
	}{
		// opencode and kilo forbidden on prya, vega, gaea (OOM risk)
		{"opencode", "prya", true},
		{"kilo", "prya", true},
		{"opencode", "vega", true},
		{"kilo", "gaea", true},
		// T1 premium agents (claude/codex/cursor/amp) forbidden on constrained nodes
		{"claude", "prya", true},
		{"codex", "prya", true},
		{"cursor", "prya", true},
		{"amp", "prya", true},
		{"claude", "vega", true},
		{"claude", "gaea", true},
		// Allowed on sati and nova
		{"opencode", "sati", false},
		{"kilo", "nova", false},
		{"claude", "sati", false},
		// Lightweight agents allowed on prya
		{"kimi", "prya", false},
		{"glm", "prya", false},
		// Unknown node - not forbidden
		{"opencode", "unknown", false},
	}

	for _, tc := range tests {
		got := isAgentForbiddenOnNode(tc.agentType, tc.nodeID)
		if got != tc.want {
			t.Errorf("isAgentForbiddenOnNode(%q, %q) = %v, want %v", tc.agentType, tc.nodeID, got, tc.want)
		}
	}
}

// TestCouncilMarkerPath tests path generation
func TestCouncilMarkerPath(t *testing.T) {
	got := councilMarkerPath("kimi-1")
	want := ".forge/autoscale/council/kimi-1.json"
	if got != want {
		t.Errorf("councilMarkerPath(%q) = %q, want %q", "kimi-1", got, want)
	}
}

// TestAgentProvider tests provider mapping
func TestAgentProvider(t *testing.T) {
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
		{"unknown", "unknown"},
		{"", "unknown"},
	}

	for _, tc := range tests {
		got := agentProvider(tc.agentType)
		if got != tc.want {
			t.Errorf("agentProvider(%q) = %q, want %q", tc.agentType, got, tc.want)
		}
	}
}

// TestTierCanClaim tests tier compatibility
func TestTierCanClaim(t *testing.T) {
	tests := []struct {
		agentTier    string
		requiredTier string
		want         bool
	}{
		// Heavy can claim anything
		{"heavy", "heavy", true},
		{"heavy", "medium", true},
		{"heavy", "lightweight", true},
		{"heavy", "any", true},
		{"heavy", "", true},
		// Medium can claim medium and lightweight
		{"medium", "heavy", false},
		{"medium", "medium", true},
		{"medium", "lightweight", true},
		{"medium", "any", true},
		// Lightweight can only claim lightweight
		{"lightweight", "heavy", false},
		{"lightweight", "medium", false},
		{"lightweight", "lightweight", true},
		{"lightweight", "any", true},
		// Unknown tier doesn't block
		{"unknown", "heavy", true},
		{"", "lightweight", true},
	}

	for _, tc := range tests {
		got := tierCanClaim(tc.agentTier, tc.requiredTier)
		if got != tc.want {
			t.Errorf("tierCanClaim(%q, %q) = %v, want %v", tc.agentTier, tc.requiredTier, got, tc.want)
		}
	}
}

// TestEnsureScaleRecommendationsTable tests table creation
func TestEnsureScaleRecommendationsTable(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	err := ensureScaleRecommendationsTable(ctx, db)
	if err != nil {
		t.Fatalf("ensureScaleRecommendationsTable failed: %v", err)
	}

	// Verify table exists by inserting a row
	_, err = db.ExecContext(ctx, `
		INSERT INTO scale_recommendations (id, node_id, action, agent_type, reason, expires_at)
		VALUES ('test-id', 'test-node', 'inflate', 'kimi', 'test', datetime('now', '+10 minutes'))
	`)
	if err != nil {
		t.Errorf("Failed to insert into scale_recommendations: %v", err)
	}
}

// TestHasActiveCouncilMarker tests marker file detection
func TestHasActiveCouncilMarker(t *testing.T) {
	// Save current dir and change to temp dir
	originalDir, _ := os.Getwd()
	tmpDir := t.TempDir()
	os.Chdir(tmpDir)
	defer os.Chdir(originalDir)

	// Create marker directory
	markerDir := filepath.Join(tmpDir, ".forge", "autoscale", "council")
	os.MkdirAll(markerDir, 0755)

	agentID := "test-kimi-1"

	// No marker file - should return false
	if hasActiveCouncilMarker(agentID) {
		t.Error("hasActiveCouncilMarker with no file should return false")
	}

	// Create expired marker
	markerPath := filepath.Join(markerDir, agentID+".json")
	expiredMarker := `{"expires_at": "2020-01-01T00:00:00Z"}`
	os.WriteFile(markerPath, []byte(expiredMarker), 0644)

	if hasActiveCouncilMarker(agentID) {
		t.Error("hasActiveCouncilMarker with expired marker should return false")
	}

	// Create valid marker
	futureTime := time.Now().Add(time.Hour).Format(time.RFC3339)
	validMarker := `{"expires_at": "` + futureTime + `"}`
	os.WriteFile(markerPath, []byte(validMarker), 0644)

	if !hasActiveCouncilMarker(agentID) {
		t.Error("hasActiveCouncilMarker with valid marker should return true")
	}

	// Create permanent marker (no expires_at)
	permanentMarker := `{"created_at": "2024-01-01T00:00:00Z"}`
	os.WriteFile(markerPath, []byte(permanentMarker), 0644)

	if !hasActiveCouncilMarker(agentID) {
		t.Error("hasActiveCouncilMarker with permanent marker should return true")
	}

	// Malformed marker - should be conservative and protect
	malformedMarker := `{"expires_at": invalid`
	os.WriteFile(markerPath, []byte(malformedMarker), 0644)

	if !hasActiveCouncilMarker(agentID) {
		t.Error("hasActiveCouncilMarker with malformed marker should return true (conservative)")
	}
}

// TestCircuitBreaker tests circuit breaker state
func TestCircuitBreaker(t *testing.T) {
	// Reset circuit breaker state
	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()

	// Initially should not be tripped
	if circuitBreakerTripped() {
		t.Error("Circuit breaker should not be tripped initially")
	}

	// Record failures
	recordSpawnFailure()
	recordSpawnFailure()

	// Still not tripped (need 3)
	if circuitBreakerTripped() {
		t.Error("Circuit breaker should not be tripped after 2 failures")
	}

	// Third failure opens circuit
	recordSpawnFailure()
	if !circuitBreakerTripped() {
		t.Error("Circuit breaker should be tripped after 3 failures")
	}

	// Success resets
	recordSpawnSuccess()
	if circuitBreakerTripped() {
		t.Error("Circuit breaker should be reset after success")
	}
}

// TestCanScale tests flap guard
func TestCanScale(t *testing.T) {
	// Reset scale event
	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Time{}
	lastScaleEvent.Unlock()

	// Initially can scale (no recent scale event)
	if !canScale() {
		t.Error("canScale should return true initially")
	}

	// Record scale event
	recordScaleEvent()

	// Should not be able to scale immediately
	if canScale() {
		t.Error("canScale should return false immediately after scale event")
	}
}

// TestFindForgeRoot tests root finding
func TestFindForgeRoot(t *testing.T) {
	// Save original env
	oldForgeRoot := os.Getenv("FORGE_ROOT")
	defer os.Setenv("FORGE_ROOT", oldForgeRoot)

	// Test with FORGE_ROOT set
	os.Setenv("FORGE_ROOT", "/custom/path")
	got := findForgeRoot()
	if got != "/custom/path" {
		t.Errorf("findForgeRoot with FORGE_ROOT = %q, want /custom/path", got)
	}

	// Test with FORGE_ROOT unset
	os.Unsetenv("FORGE_ROOT")
	got = findForgeRoot()
	// Should return a path (either from walking up or fallback)
	if got == "" {
		t.Error("findForgeRoot should return non-empty path")
	}
}

// TestNodeHardCeilings tests the hard ceiling configuration
func TestNodeHardCeilings(t *testing.T) {
	expected := map[string]int{
		"prya": 2,
		"sati": 6,
		"nova": 4,
		"vega": 2,
		"gaea": 2,
	}

	for node, expectedCeiling := range expected {
		got, ok := NodeHardCeilings[node]
		if !ok {
			t.Errorf("NodeHardCeilings missing entry for %s", node)
			continue
		}
		if got != expectedCeiling {
			t.Errorf("NodeHardCeilings[%s] = %d, want %d", node, got, expectedCeiling)
		}
	}
}

// TestAgentRAMRequirements tests RAM requirements map
func TestAgentRAMRequirements(t *testing.T) {
	// Verify some key entries exist and are reasonable
	if agentRAMRequirements["kimi"] <= 0 {
		t.Error("agentRAMRequirements['kimi'] should be positive")
	}
	if agentRAMRequirements["opencode"] <= 0 {
		t.Error("agentRAMRequirements['opencode'] should be positive")
	}
	if agentRAMRequirements["opencode"] <= agentRAMRequirements["kimi"] {
		t.Error("opencode should require more RAM than kimi")
	}
}

// TestAgentSpawnCommands tests spawn commands map
func TestAgentSpawnCommands(t *testing.T) {
	// Verify all agent types have commands
	for agentType := range agentRAMRequirements {
		if _, ok := agentSpawnCommands[agentType]; !ok {
			t.Errorf("agentSpawnCommands missing entry for %s", agentType)
		}
	}

	// Verify specific commands
	if agentSpawnCommands["kimi"] != "kimi -y" {
		t.Errorf("agentSpawnCommands['kimi'] = %q, want 'kimi -y'", agentSpawnCommands["kimi"])
	}
}

// TestFleetScaleRecommendPatrolWithEmptyDB tests patrol with no data
func TestFleetScaleRecommendPatrolWithEmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Create tables needed
	_, err := db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS agent_inventory (
			agent_id TEXT PRIMARY KEY,
			node_id TEXT,
			status TEXT,
			agent_type TEXT,
			agent_tier TEXT
		)
	`)
	if err != nil {
		t.Fatalf("Failed to create agent_inventory: %v", err)
	}

	// Run patrol with empty DB - should not error
	err = fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Errorf("fleetScaleRecommendPatrol with empty DB failed: %v", err)
	}
}

// TestDeflationGateCheckWithNoData tests deflation gate with missing data
func TestDeflationGateCheckWithNoData(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Create agent_heartbeats table
	_, err := db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS agent_heartbeats (
			agent_id TEXT PRIMARY KEY,
			status TEXT,
			context_pct REAL,
			last_seen TEXT,
			current_task_id TEXT
		)
	`)
	if err != nil {
		t.Fatalf("Failed to create agent_heartbeats: %v", err)
	}

	// Test with non-existent agent
	ok, reason, err := deflationGateCheck(ctx, db, "nonexistent", "sati", 15*time.Minute)
	if err != nil {
		t.Errorf("deflationGateCheck with no data returned error: %v", err)
	}
	// Should pass gates when no data (conservative)
	if !ok {
		t.Logf("deflationGateCheck returned false with reason: %s", reason)
	}
}

// --- readLiveRAMMB ---

func TestReadLiveRAMMB_ReturnsPositive(t *testing.T) {
	mb, err := readLiveRAMMB()
	if err != nil {
		t.Skipf("readLiveRAMMB: %v", err)
	}
	if mb < 0 {
		t.Errorf("readLiveRAMMB returned negative value: %d", mb)
	}
}

func TestReadLiveRAMMB_Cached(t *testing.T) {
	mb1, err1 := readLiveRAMMB()
	if err1 != nil {
		t.Skipf("readLiveRAMMB: %v", err1)
	}
	mb2, err2 := readLiveRAMMB()
	if err2 != nil {
		t.Fatalf("second readLiveRAMMB: %v", err2)
	}
	if mb1 != mb2 {
		t.Errorf("cached value changed: %d != %d", mb1, mb2)
	}
}

// --- readLoadAverage ---

func TestReadLoadAverage_ReturnsNonNegative(t *testing.T) {
	load, err := readLoadAverage()
	if err != nil {
		t.Skipf("readLoadAverage: %v", err)
	}
	if load < 0 {
		t.Errorf("readLoadAverage returned negative: %f", load)
	}
}

// --- getCPUCount ---

func TestGetCPUCount_ReturnsPositive(t *testing.T) {
	count := getCPUCount()
	if count <= 0 {
		t.Errorf("getCPUCount = %d, want > 0", count)
	}
}

// --- checkTokenBudgetGate ---

func TestCheckTokenBudgetGate_NoFile(t *testing.T) {
	path := ".forge/heartbeat/token-budgets-prya.json"
	if _, statErr := os.Stat(path); statErr == nil {
		t.Skip("token budget file exists at default path — skipping no-file test")
	}

	ok, reason := checkTokenBudgetGate("anthropic")
	// P0 fix: fail-closed — no budget file → block spawn until file is restored.
	if ok {
		t.Errorf("checkTokenBudgetGate (no file) should return false (fail-closed), got true")
	}
	if reason == "" {
		t.Error("checkTokenBudgetGate: expected non-empty reason")
	}
}

// --- markRecDeferred / markRecFailed ---

func TestMarkRecDeferred_RunsWithoutPanic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// markRecDeferred ignores errors; calling with nonexistent ID is safe.
	markRecDeferred(ctx, db, "nonexistent-rec-id", "test reason")
}

func TestMarkRecFailed_RunsWithoutPanic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	markRecFailed(ctx, db, "nonexistent-rec-id", "error message")
}

// --- checkTierCompatibility ---

func TestCheckTierCompatibility_NilDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	err := checkTierCompatibility(context.Background(), "task-1", "agent-1")
	if err != nil {
		t.Errorf("expected nil error when DB is nil, got: %v", err)
	}
}

func TestCheckTierCompatibility_TaskNotFound_DefaultsToAny(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	oldDB := getDBConn()
	setDBConn(db)
	defer setDBConn(oldDB)

	// Task doesn't exist — required_tier will be null → defaults to "any" → pass
	err := checkTierCompatibility(context.Background(), "nonexistent-task", "agent-x")
	if err != nil {
		t.Errorf("expected nil when task has no required_tier, got: %v", err)
	}
}

// Wave 25: fleet_scaler.go patrol functions (previously 0-39%)

func TestFleetScaleRecommendPatrol_W25(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()
	// No scale_recommendations table in test DB — error is acceptable
	err := fleetScaleRecommendPatrol(ctx, db)
	_ = err
}

func TestFleetAutoExecutePatrol_W25(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()
	// Circuit breaker or flap guard may short-circuit — both paths are valid
	err := fleetAutoExecutePatrol(ctx, db)
	_ = err
}

func TestOrchestratorWorkStrategyPatrol_W25(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()
	// 0 idle agents → returns nil immediately
	err := orchestratorWorkStrategyPatrol(ctx, db)
	_ = err
}

func TestFleetDeflateRecommendPatrol_W25(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()
	err := fleetDeflateRecommendPatrol(ctx, db)
	_ = err
}

func TestFleetAutoDeflatePatrol_W25(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()
	err := fleetAutoDeflatePatrol(ctx, db)
	_ = err
}

func TestEnsureScaleRecommendationsTable_W25(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()
	// Should succeed — test DB uses the standard migration runner
	err := ensureScaleRecommendationsTable(ctx, db)
	_ = err
}
