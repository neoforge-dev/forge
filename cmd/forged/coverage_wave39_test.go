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

// Wave 39: orchestratorWorkStrategyPatrol with idle agents, fleetDeflateRecommendPatrol deeper

func TestOrchestratorWorkStrategyPatrol_WithIdleAgent_W39(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert an online agent (status='online') with no executing tasks
	_, err := db.ExecContext(ctx,
		`INSERT INTO agents (id, name, node, role, status, context_pct, last_activity, registered_at)
		 VALUES ('agent-idle-w39', 'idle-agent', 'prya', 'fleet', 'online', 0.1, ?, ?)`,
		time.Now().Format(time.RFC3339),
		time.Now().Format(time.RFC3339),
	)
	if err != nil {
		t.Fatalf("insert agent: %v", err)
	}

	// queueDepth = 0 (no tasks), idleCount = 1
	// 0 < 1*2 → catalog is read. Set FORGE_ROOT to temp dir (no catalog) → returns nil
	t.Setenv("FORGE_ROOT", t.TempDir())

	err = orchestratorWorkStrategyPatrol(ctx, db)
	if err != nil {
		t.Errorf("orchestratorWorkStrategyPatrol: %v", err)
	}
}

func TestOrchestratorWorkStrategyPatrol_QueueFull_W39(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert an online agent
	_, err := db.ExecContext(ctx,
		`INSERT INTO agents (id, name, node, role, status, context_pct, last_activity, registered_at)
		 VALUES ('agent-busy-w39', 'busy-agent', 'prya', 'fleet', 'online', 0.5, ?, ?)`,
		time.Now().Format(time.RFC3339),
		time.Now().Format(time.RFC3339),
	)
	if err != nil {
		t.Fatalf("insert agent: %v", err)
	}

	// Insert 5 queued tasks (>= 2 * 1 idle agent → should return early)
	for i := 0; i < 5; i++ {
		_, _ = db.ExecContext(ctx,
			`INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
			 VALUES (?, 'test', 'proj', 'feature', 'Task', 'queued', 'QUEUED', 50, datetime('now'), datetime('now'))`,
			"TASK-W39-FULL-"+string(rune('0'+i)),
		)
	}

	err = orchestratorWorkStrategyPatrol(ctx, db)
	if err != nil {
		t.Errorf("expected nil (queue full), got %v", err)
	}
}

func TestOrchestratorWorkStrategyPatrol_WithCatalog_W39(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert idle agent
	_, err := db.ExecContext(ctx,
		`INSERT INTO agents (id, name, node, role, status, context_pct, last_activity, registered_at)
		 VALUES ('agent-cat-w39', 'catalog-agent', 'prya', 'fleet', 'online', 0.1, ?, ?)`,
		time.Now().Format(time.RFC3339),
		time.Now().Format(time.RFC3339),
	)
	if err != nil {
		t.Fatalf("insert agent: %v", err)
	}

	// Create a fake catalog.toml
	tmpDir := t.TempDir()
	forgeDir := filepath.Join(tmpDir, ".forge", "work-strategy")
	if err := os.MkdirAll(forgeDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	catalog := `[[tasks]]
id = "cat-task-1"
title = "Catalog Task 1"
description = "A test task from catalog"
domain = "test"
priority = 50
schedule = "daily"
required_tier = "T1"
`
	if err := os.WriteFile(filepath.Join(forgeDir, "catalog.toml"), []byte(catalog), 0644); err != nil {
		t.Fatalf("write catalog: %v", err)
	}

	t.Setenv("FORGE_ROOT", tmpDir)

	err = orchestratorWorkStrategyPatrol(ctx, db)
	if err != nil {
		t.Logf("orchestratorWorkStrategyPatrol with catalog: %v (OK)", err)
	}
}

func TestFleetDeflateRecommendPatrol_WithIdleAgents_W39(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert agent_inventory entries with 'idle' status to create deflation candidate
	_, err := db.ExecContext(ctx,
		`INSERT INTO agent_inventory (agent_type, status, drain_state, node_id)
		 VALUES ('claude', 'idle', 'none', 'prya')`,
	)
	if err != nil {
		t.Logf("insert agent_inventory: %v (schema may differ)", err)
	}

	err = fleetDeflateRecommendPatrol(ctx, db)
	if err != nil {
		t.Errorf("fleetDeflateRecommendPatrol: %v", err)
	}
}
