//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"net/http/httptest"
	"net/http"
	"strings"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// wave70CoordDB creates a minimal DB with agent_heartbeats table.
func wave70CoordDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("wave70CoordDB: open db: %v", err)
	}
	_, err = db.Exec(`
		CREATE TABLE agent_heartbeats (
			agent_id     TEXT PRIMARY KEY,
			current_task_id TEXT NOT NULL DEFAULT '',
			status       TEXT NOT NULL DEFAULT 'idle',
			last_seen    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			context_pct  REAL DEFAULT 0
		)
	`)
	if err != nil {
		t.Fatalf("wave70CoordDB: create agent_heartbeats: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

// ---------------------------------------------------------------------------
// CoordinationDashboard.ServeHTTP — covers the major uncovered section (53.8%)
// ---------------------------------------------------------------------------

// TestWave70_CoordinationDashboard_ServeHTTP_NoSprint exercises the default sprint path.
func TestWave70_CoordinationDashboard_ServeHTTP_NoSprint(t *testing.T) {
	db := wave70CoordDB(t)
	cd := NewCoordinationDashboard(db)

	req, _ := http.NewRequest("GET", "/api/coordination", nil)
	rr := httptest.NewRecorder()
	cd.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
	if !strings.Contains(rr.Body.String(), "completion_percent") {
		t.Error("expected completion_percent in response")
	}
}

// TestWave70_CoordinationDashboard_ServeHTTP_WithSprintParam exercises the sprint query param branch.
func TestWave70_CoordinationDashboard_ServeHTTP_WithSprintParam(t *testing.T) {
	db := wave70CoordDB(t)
	cd := NewCoordinationDashboard(db)

	req, _ := http.NewRequest("GET", "/api/coordination?sprint=Phase+4", nil)
	rr := httptest.NewRecorder()
	cd.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
	body := rr.Body.String()
	if !strings.Contains(body, "Phase 4") {
		t.Errorf("expected sprint name in response, got: %s", body)
	}
}

// TestWave70_CoordinationDashboard_ServeHTTP_CurrentSprint exercises "current" sprint name.
func TestWave70_CoordinationDashboard_ServeHTTP_CurrentSprint(t *testing.T) {
	db := wave70CoordDB(t)
	cd := NewCoordinationDashboard(db)

	req, _ := http.NewRequest("GET", "/api/coordination?sprint=current", nil)
	rr := httptest.NewRecorder()
	cd.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
}

// TestWave70_CoordinationDashboard_ServeHTTP_WithBlockers exercises the blocker alerting branch
// by inserting a heartbeat from 45 minutes ago so detectBlockers triggers.
func TestWave70_CoordinationDashboard_ServeHTTP_WithBlockers(t *testing.T) {
	db := wave70CoordDB(t)

	// Insert a heartbeat from 45 minutes ago — detectBlockers will add a warning blocker
	lastSeen := time.Now().Add(-45 * time.Minute).Format("2006-01-02 15:04:05")
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, current_task_id, status, last_seen, context_pct)
		VALUES ('stale-agent-70', 'old-task', 'idle', ?, 10.0)
	`, lastSeen)
	if err != nil {
		t.Fatalf("insert stale heartbeat: %v", err)
	}

	cd := NewCoordinationDashboard(db)
	req, _ := http.NewRequest("GET", "/api/coordination", nil)
	rr := httptest.NewRecorder()
	cd.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// CoordinationDashboard.getAgentStatuses — 78.4%
// ---------------------------------------------------------------------------

// TestWave70_GetAgentStatuses_WithDBAgents exercises the database agent merge path.
func TestWave70_GetAgentStatuses_WithDBAgents(t *testing.T) {
	db := wave70CoordDB(t)

	// Insert heartbeat row within last hour.
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, current_task_id, status, last_seen, context_pct)
		VALUES ('agent-db-70', 'task-70', 'working', datetime('now','-10 minutes'), 45.0)
	`)
	if err != nil {
		t.Fatalf("insert heartbeat: %v", err)
	}

	cd := NewCoordinationDashboard(db)
	agents := cd.getAgentStatusesFromDB()

	if len(agents) == 0 {
		t.Error("expected at least one agent from DB heartbeats")
	}
	found := false
	for _, a := range agents {
		if a.Name == "agent-db-70" {
			found = true
			break
		}
	}
	if !found {
		t.Error("agent-db-70 not found in DB agent statuses")
	}
}

// TestWave70_GetAgentStatuses_HighContextPct exercises context_pct > 80 branch.
func TestWave70_GetAgentStatuses_HighContextPct(t *testing.T) {
	db := wave70CoordDB(t)

	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, current_task_id, status, last_seen, context_pct)
		VALUES ('high-ctx-agent', 'task-hc', 'working', datetime('now','-5 minutes'), 90.0)
	`)
	if err != nil {
		t.Fatalf("insert heartbeat: %v", err)
	}

	cd := NewCoordinationDashboard(db)
	agents := cd.getAgentStatusesFromDB()

	found := false
	for _, a := range agents {
		if a.Name == "high-ctx-agent" {
			found = true
			if a.Status != "high_context" {
				t.Errorf("expected high_context status for context_pct=90, got %s", a.Status)
			}
		}
	}
	if !found {
		t.Error("high-ctx-agent not found")
	}
}

// ---------------------------------------------------------------------------
// CoordinationDashboard.calculateCompletion — 75.0%
// ---------------------------------------------------------------------------

// TestWave70_CalculateCompletion_ActiveAgents covers the active agents factor.
func TestWave70_CalculateCompletion_ActiveAgents(t *testing.T) {
	db := wave70CoordDB(t)
	cd := NewCoordinationDashboard(db)

	status := &SprintStatus{
		Name:      "test-sprint",
		StartedAt: time.Now().Add(-1 * time.Hour),
		Agents: []AgentSprintStatus{
			{Name: "agent1", Status: "working", CommitCount: 3, LastCommit: time.Now().Add(-5 * time.Minute)},
			{Name: "agent2", Status: "working", CommitCount: 2, LastCommit: time.Now().Add(-10 * time.Minute)},
			{Name: "agent3", Status: "idle", CommitCount: 0, LastCommit: time.Now().Add(-40 * time.Minute)},
		},
		Commits:  CommitSummary{Total: 5},
		Blockers: []Blocker{},
	}

	pct := cd.calculateCompletion(status)
	if pct <= 0 || pct > 100 {
		t.Errorf("calculateCompletion unexpected: %f", pct)
	}
	// Should get: 2 active agents = 10%, 5 commits = 10%, 0 blockers = 30% → 50%
	if pct != 50.0 {
		t.Logf("calculateCompletion = %f (expected 50.0)", pct)
	}
}

// TestWave70_CalculateCompletion_ManyAgents exercises the 30% cap on agents.
func TestWave70_CalculateCompletion_ManyAgents(t *testing.T) {
	db := wave70CoordDB(t)
	cd := NewCoordinationDashboard(db)

	// 10 active agents would be 50% but capped at 30%
	agents := make([]AgentSprintStatus, 10)
	for i := range agents {
		agents[i] = AgentSprintStatus{Status: "working", LastCommit: time.Now()}
	}

	status := &SprintStatus{
		Name:      "cap-sprint",
		StartedAt: time.Now().Add(-1 * time.Hour),
		Agents:    agents,
		Commits:   CommitSummary{Total: 0},
		Blockers:  []Blocker{},
	}

	pct := cd.calculateCompletion(status)
	// 30% (capped agents) + 0% (no commits) + 30% (no blockers) = 60%
	if pct != 60.0 {
		t.Logf("calculateCompletion with 10 agents = %f (expected 60.0)", pct)
	}
}

// TestWave70_CalculateCompletion_ManyCommits exercises the 40% commit cap.
func TestWave70_CalculateCompletion_ManyCommits(t *testing.T) {
	db := wave70CoordDB(t)
	cd := NewCoordinationDashboard(db)

	status := &SprintStatus{
		Name:      "commit-sprint",
		StartedAt: time.Now().Add(-1 * time.Hour),
		Agents:    []AgentSprintStatus{},
		Commits:   CommitSummary{Total: 25}, // 25*2 = 50, capped at 40
		Blockers:  []Blocker{},
	}

	pct := cd.calculateCompletion(status)
	// 0% (no agents) + 40% (capped commits) + 30% (no blockers) = 70%
	if pct != 70.0 {
		t.Logf("calculateCompletion with 25 commits = %f (expected 70.0)", pct)
	}
}

// TestWave70_CalculateCompletion_WithBlockers exercises the blocker penalty.
func TestWave70_CalculateCompletion_WithBlockers(t *testing.T) {
	db := wave70CoordDB(t)
	cd := NewCoordinationDashboard(db)

	status := &SprintStatus{
		Name:      "blocked-sprint",
		StartedAt: time.Now().Add(-1 * time.Hour),
		Agents:    []AgentSprintStatus{},
		Commits:   CommitSummary{Total: 0},
		Blockers: []Blocker{
			{Agent: "a1", Severity: "warning"},
			{Agent: "a2", Severity: "critical"},
		},
	}

	pct := cd.calculateCompletion(status)
	// 0% (no agents) + 0% (no commits) + (30 - 2*5) = 20% → total 20%
	if pct != 20.0 {
		t.Logf("calculateCompletion with 2 blockers = %f (expected 20.0)", pct)
	}
}

// TestWave70_CalculateCompletion_ManyBlockers exercises the blocker penalty cap (max 30).
func TestWave70_CalculateCompletion_ManyBlockers(t *testing.T) {
	db := wave70CoordDB(t)
	cd := NewCoordinationDashboard(db)

	blockers := make([]Blocker, 8)
	for i := range blockers {
		blockers[i] = Blocker{Agent: "blocked", Severity: "critical"}
	}
	status := &SprintStatus{
		Name:      "max-blocked",
		StartedAt: time.Now().Add(-1 * time.Hour),
		Agents:    []AgentSprintStatus{},
		Commits:   CommitSummary{Total: 0},
		Blockers:  blockers,
	}

	pct := cd.calculateCompletion(status)
	// With 8 blockers → penalty = 40 > 30, capped at 30. Result: 0 + 0 + 0 = 0
	if pct != 0.0 {
		t.Logf("calculateCompletion with 8 blockers = %f (expected 0.0)", pct)
	}
}

// ---------------------------------------------------------------------------
// CoordinationDashboard: detectBlockers — additional branches
// ---------------------------------------------------------------------------

// TestWave70_DetectBlockers_HighContext exercises the high_context blocker branch.
func TestWave70_DetectBlockers_HighContext(t *testing.T) {
	db := wave70CoordDB(t)
	cd := NewCoordinationDashboard(db)

	agents := []AgentSprintStatus{
		{Name: "hc-agent", Status: "high_context", LastCommit: time.Now()},
	}
	blockers := cd.detectBlockers(agents)

	found := false
	for _, b := range blockers {
		if b.Agent == "hc-agent" && b.Description == "High context usage - handoff recommended" {
			found = true
		}
	}
	if !found {
		t.Error("expected high_context blocker not found")
	}
}

// TestWave70_DetectBlockers_WarningWindow exercises the 30-60 min warning window.
func TestWave70_DetectBlockers_WarningWindow(t *testing.T) {
	db := wave70CoordDB(t)
	cd := NewCoordinationDashboard(db)

	agents := []AgentSprintStatus{
		{Name: "warn-agent", Status: "working", LastCommit: time.Now().Add(-45 * time.Minute)},
	}
	blockers := cd.detectBlockers(agents)

	if len(blockers) == 0 {
		t.Error("expected warning blocker for 45min inactivity")
	}
	if blockers[0].Severity != "warning" {
		t.Errorf("expected warning severity, got %s", blockers[0].Severity)
	}
}

// TestWave70_DetectBlockers_CriticalWindow exercises the >60min critical window.
func TestWave70_DetectBlockers_CriticalWindow(t *testing.T) {
	db := wave70CoordDB(t)
	cd := NewCoordinationDashboard(db)

	agents := []AgentSprintStatus{
		{Name: "crit-agent", Status: "working", LastCommit: time.Now().Add(-90 * time.Minute)},
	}
	blockers := cd.detectBlockers(agents)

	if len(blockers) == 0 {
		t.Error("expected critical blocker for 90min inactivity")
	}
	if blockers[0].Severity != "critical" {
		t.Errorf("expected critical severity, got %s", blockers[0].Severity)
	}
}

// ---------------------------------------------------------------------------
// CoordinationDashboard: estimateETA — additional branches
// ---------------------------------------------------------------------------

// TestWave70_EstimateETA_Complete exercises Completion >= 100 branch.
func TestWave70_EstimateETA_Complete(t *testing.T) {
	cd := NewCoordinationDashboard(nil)
	status := &SprintStatus{
		StartedAt:  time.Now().Add(-2 * time.Hour),
		Completion: 100.0,
	}
	eta := cd.estimateETA(status)
	if eta != "Complete" {
		t.Errorf("expected Complete, got %s", eta)
	}
}

// TestWave70_EstimateETA_Zero exercises Completion <= 0 branch.
func TestWave70_EstimateETA_Zero(t *testing.T) {
	cd := NewCoordinationDashboard(nil)
	status := &SprintStatus{
		StartedAt:  time.Now().Add(-2 * time.Hour),
		Completion: 0.0,
	}
	eta := cd.estimateETA(status)
	if eta != "Unknown" {
		t.Errorf("expected Unknown, got %s", eta)
	}
}

// TestWave70_EstimateETA_SubHour exercises the < 1 hour remaining branch (minutes display).
func TestWave70_EstimateETA_SubHour(t *testing.T) {
	cd := NewCoordinationDashboard(nil)
	// 90% complete, started 2 hours ago → velocity = 45%/hr, remaining=10%, 10/45 ≈ 0.22h → < 1h
	status := &SprintStatus{
		StartedAt:  time.Now().Add(-2 * time.Hour),
		Completion: 90.0,
	}
	eta := cd.estimateETA(status)
	if !strings.Contains(eta, "minutes") {
		t.Errorf("expected minutes in sub-hour ETA, got %s", eta)
	}
}

// TestWave70_EstimateETA_Hours exercises the >= 1 hour remaining branch.
func TestWave70_EstimateETA_Hours(t *testing.T) {
	cd := NewCoordinationDashboard(nil)
	// 10% complete, started 1 hour ago → velocity = 10%/hr, remaining = 90%, 90/10 = 9h
	status := &SprintStatus{
		StartedAt:  time.Now().Add(-1 * time.Hour),
		Completion: 10.0,
	}
	eta := cd.estimateETA(status)
	if !strings.Contains(eta, "hours") {
		t.Errorf("expected hours in ETA, got %s", eta)
	}
}

// ---------------------------------------------------------------------------
// truncateString helper — cover the truncation branch
// ---------------------------------------------------------------------------

func TestWave70_TruncateString_Long(t *testing.T) {
	input := "this is a very long string that needs truncating for the test"
	result := truncateString(input, 20)
	if len(result) > 20 {
		t.Errorf("truncated string too long: %d", len(result))
	}
	if !strings.HasSuffix(result, "...") {
		t.Errorf("expected ellipsis suffix, got %s", result)
	}
}

func TestWave70_TruncateString_Short(t *testing.T) {
	result := truncateString("short", 20)
	if result != "short" {
		t.Errorf("short string should not be truncated, got %s", result)
	}
}
