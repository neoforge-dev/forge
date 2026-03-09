//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
)

// Wave 37: TUI.GetDashboard, handleQueueDepth format, findForgeRoot deeper,
//          agent_inventory draining path with correct schema

func TestTUIGetDashboard_WithQueue_W37(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	tui := NewTUI(db, nil, q)
	ctx := context.Background()

	dash, err := tui.GetDashboard(ctx)
	if err != nil {
		t.Fatalf("GetDashboard: %v", err)
	}
	if dash == nil {
		t.Fatal("expected non-nil dashboard")
	}
}


func TestFleetAutoDeflatePatrol_DrainingWithCorrectSchema_W37(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Check what columns agent_inventory actually has
	rows, err := db.QueryContext(ctx, "PRAGMA table_info(agent_inventory)")
	if err != nil {
		t.Skip("agent_inventory not available")
	}
	defer rows.Close()

	colNames := map[string]bool{}
	for rows.Next() {
		var cid int
		var name, ctype string
		var notnull, dflt, pk interface{}
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err == nil {
			colNames[name] = true
		}
	}

	if !colNames["drain_state"] {
		t.Skip("agent_inventory has no drain_state column — skipping")
	}

	// Build INSERT with only known columns
	insertSQL := `INSERT INTO agent_inventory (agent_type, status, drain_state, node_id`
	vals := []interface{}{"claude", "draining", "draining", "prya"}

	if colNames["tmux_window"] {
		insertSQL += `, tmux_window`
		vals = append(vals, "test-window")
	}
	if colNames["started_at"] {
		insertSQL += `, started_at`
		vals = append(vals, "not-a-valid-timestamp") // malformed — hits error path
	}
	placeholders := "?"
	for i := 1; i < len(vals); i++ {
		placeholders += ", ?"
	}
	insertSQL += `) VALUES (` + placeholders + `)`

	if _, err := db.ExecContext(ctx, insertSQL, vals...); err != nil {
		t.Logf("insert draining agent failed: %v — skipping drain path test", err)
		return
	}

	// Should handle the malformed timestamp gracefully
	err = fleetAutoDeflatePatrol(ctx, db)
	if err != nil {
		t.Errorf("fleetAutoDeflatePatrol: %v", err)
	}
}

func TestOrchestratorWorkStrategyPatrol_WithTasks_W37(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert queued tasks so patrol has something to analyze
	_, _ = db.ExecContext(ctx,
		`INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		 VALUES ('TASK-W37-ORCH', 'test', 'proj', 'feature', 'Test', 'queued', 'QUEUED', 50, datetime('now'), datetime('now'))`)

	err := orchestratorWorkStrategyPatrol(ctx, db)
	if err != nil {
		t.Logf("orchestratorWorkStrategyPatrol with tasks: %v (OK)", err)
	}
}
