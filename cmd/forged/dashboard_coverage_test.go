package main

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestCalculateStatus(t *testing.T) {
	d := &Dashboard{}
	now := time.Now()

	assert.Equal(t, AgentStatusIdle, d.calculateStatus(now, now))
	assert.Equal(t, AgentStatusIdle, d.calculateStatus(now.Add(-1*time.Minute), now))
	assert.Equal(t, AgentStatusError, d.calculateStatus(now.Add(-3*time.Minute), now))
	assert.Equal(t, AgentStatusOffline, d.calculateStatus(now.Add(-6*time.Minute), now))
}

func TestCheckWarnings(t *testing.T) {
	d := &Dashboard{}
	now := time.Now()

	a := &AgentHealth{
		AgentID:        "test-agent",
		ContextPercent: 50.0,
		LastHeartbeat:  now,
		SuccessRate:    0.9,
	}

	// Healthy agent
	assert.Empty(t, d.checkWarnings(a))

	// High context
	a.ContextPercent = 85.0
	assert.Contains(t, d.checkWarnings(a), "High context usage")

	// Stale heartbeat
	a.ContextPercent = 50.0
	a.LastHeartbeat = now.Add(-3 * time.Minute)
	assert.Contains(t, d.checkWarnings(a), "Stale heartbeat")

	// Low success rate
	a.LastHeartbeat = now
	a.SuccessRate = 0.4
	assert.Contains(t, d.checkWarnings(a), "Low success rate")
}

func TestCalculateSummary(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	d := NewDashboard(getDBConn(), nil)
	
	agents := []AgentHealth{
		{Status: AgentStatusIdle, ContextPercent: 20.0},
		{Status: AgentStatusWorking, ContextPercent: 80.0, CurrentTask: strPtr("task1")},
		{Status: AgentStatusError, ContextPercent: 50.0},
		{Status: AgentStatusOffline, ContextPercent: 0.0},
	}

	summary := d.calculateSummary(agents)

	assert.Equal(t, 4, summary.TotalAgents)
	assert.Equal(t, 3, summary.OnlineAgents)
	assert.Equal(t, 1, summary.OfflineAgents)
	assert.Equal(t, 1, summary.BusyAgents)
	assert.Equal(t, 1, summary.ErrorAgents)
	assert.Equal(t, 1, summary.ActiveTasks)
	assert.Equal(t, 37.5, summary.AvgContextUsage)
	assert.Equal(t, "warning", summary.SystemHealth)
}

func TestGetNodeInfo(t *testing.T) {
	d := &Dashboard{}
	agents := []AgentHealth{
		{Node: "node1", Status: AgentStatusIdle},
		{Node: "node1", Status: AgentStatusWorking},
		{Node: "node2", Status: AgentStatusIdle},
	}

	nodes := d.getNodeInfo(agents)
	assert.Equal(t, 2, len(nodes))
	assert.Equal(t, "node1", nodes[0].Name)
	assert.Equal(t, 2, nodes[0].AgentCount)
	assert.Equal(t, "node2", nodes[1].Name)
	assert.Equal(t, 1, nodes[1].AgentCount)
}

func TestGetAgentStats(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	agentID := "test-agent"

	// Create some tasks
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, status, assigned_to, duration_ms, created_at)
		VALUES 
		('t1', 'd', 'p', 't', 'completed', ?, 100, datetime('now')),
		('t2', 'd', 'p', 't', 'completed', ?, 200, datetime('now')),
		('t3', 'd', 'p', 't', 'failed', ?, 0, datetime('now'))
	`, agentID, agentID, agentID)
	require.NoError(t, err)

	d := NewDashboard(db, nil)
	count, rate, duration := d.getAgentStats(context.Background(), agentID)

	assert.Equal(t, 3, count)
	assert.InDelta(t, 66.66, rate, 0.01)
	assert.Equal(t, 150*time.Millisecond, duration)
}

func TestSplitCapabilities(t *testing.T) {
	assert.Nil(t, splitCapabilities(""))
	assert.Equal(t, []string{"cap1", "cap2"}, splitCapabilities("cap1,cap2"))
	assert.Equal(t, []string{"cap1", "cap2"}, splitCapabilities("cap1, cap2 "))
}

func TestPrintSchemas(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	
	for _, table := range []string{"tasks", "patrol_executions"} {
		t.Logf("Schema for %s:", table)
		rows, err := db.Query(fmt.Sprintf("PRAGMA table_info(%s)", table))
		require.NoError(t, err)
		for rows.Next() {
			var cid int
			var name, ctype string
			var notnull, pk int
			var dflt_value interface{}
			err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt_value, &pk)
			require.NoError(t, err)
			t.Logf("  Column: %s (%s)", name, ctype)
		}
		rows.Close()
	}
}

func strPtr(s string) *string {
	return &s
}
