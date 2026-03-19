//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"os"
	"time"
)

// orchestratorAutoDispatch is the orchestrator automation patrol.
// It runs every 60 seconds and performs four routine orchestrator duties:
//
//  1. Auto-heartbeat — keeps the local node marked online in agent_heartbeats.
//  2. Auto-dispatch  — assigns queued tasks to online idle agents using role matching.
//  3. Stale requeue  — requeues tasks assigned to agents that have been offline > 30 min.
//  4. OpenClaw route — applies stage-gate + lane assignment to openclaw domain tasks.
func orchestratorAutoDispatch(ctx context.Context, db *sql.DB) error {
	if db == nil {
		return nil
	}

	nodeID := resolveNodeID()

	var errs []error

	if err := orchestratorHeartbeat(ctx, db, nodeID); err != nil {
		errs = append(errs, fmt.Errorf("heartbeat: %w", err))
	}

	// Council S118 Proposal G: disable auto-dispatch when FORGE_AUTO_DISPATCH=0
	if os.Getenv("FORGE_AUTO_DISPATCH") == "0" {
		log.Printf("[Patrol:orchestrator-auto] auto-dispatch disabled (FORGE_AUTO_DISPATCH=0)")
		return nil
	}

	if err := orchestratorDispatchQueued(ctx, db); err != nil {
		errs = append(errs, fmt.Errorf("dispatch: %w", err))
	}

	if err := orchestratorRequeueStale(ctx, db); err != nil {
		errs = append(errs, fmt.Errorf("requeue-stale: %w", err))
	}

	if err := orchestratorRouteOpenClaw(ctx, db); err != nil {
		errs = append(errs, fmt.Errorf("openclaw-route: %w", err))
	}

	if len(errs) > 0 {
		// Log each sub-error individually for visibility, then return them combined.
		for _, e := range errs {
			log.Printf("[Patrol:orchestrator-auto] sub-error: %v", e)
		}
		return fmt.Errorf("orchestrator-auto: %d sub-error(s): %v", len(errs), errs[0])
	}

	return nil
}

// resolveNodeID returns the NODE_ID env var, falling back to os.Hostname().
func resolveNodeID() string {
	if id := os.Getenv("NODE_ID"); id != "" {
		return id
	}
	if h, err := os.Hostname(); err == nil {
		return h
	}
	return "unknown"
}

// orchestratorHeartbeat ensures the local node's heartbeat row is kept current
// so remote fleet views always see it as online.
func orchestratorHeartbeat(ctx context.Context, db *sql.DB, nodeID string) error {
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.ExecContext(ctx, `
		UPDATE agent_heartbeats
		SET last_seen = ?, status = 'online'
		WHERE agent_id = ?
	`, now, nodeID)
	if err != nil {
		return fmt.Errorf("update heartbeat for %s: %w", nodeID, err)
	}
	return nil
}

// orchestratorDispatchQueued finds unassigned queued tasks and dispatches each
// to the most appropriate online idle agent using ResolveRoleForTask +
// PreferredAgentsForRole. Up to 10 tasks are processed per patrol tick.
func orchestratorDispatchQueued(ctx context.Context, db *sql.DB) error {
	// Load agent-role configuration; tolerate missing file.
	roles, _ := LoadAgentRoles(agentRolesConfigPath())

	// Find queued unassigned tasks ordered by priority (desc) then age (asc).
	taskRows, err := db.QueryContext(ctx, `
		SELECT id, domain, project, type, title, priority
		FROM tasks
		WHERE status = 'queued'
		  AND (assigned_to IS NULL OR assigned_to = '')
		ORDER BY priority DESC, created_at ASC
		LIMIT 10
	`)
	if err != nil {
		return fmt.Errorf("query queued tasks: %w", err)
	}
	defer taskRows.Close()

	type queuedTask struct {
		id       string
		domain   string
		project  string
		taskType string
		title    string
		priority int
	}

	var tasks []queuedTask
	for taskRows.Next() {
		var t queuedTask
		if err := taskRows.Scan(&t.id, &t.domain, &t.project, &t.taskType, &t.title, &t.priority); err != nil {
			log.Printf("[Patrol:orchestrator-auto] scan queued task: %v", err)
			continue
		}
		tasks = append(tasks, t)
	}
	if err := taskRows.Err(); err != nil {
		return fmt.Errorf("iterate queued tasks: %w", err)
	}

	if len(tasks) == 0 {
		return nil
	}

	// Collect the set of online idle agents once (reused for all tasks).
	agentRows, err := db.QueryContext(ctx, `
		SELECT agent_id
		FROM agent_heartbeats
		WHERE status = 'online'
		  AND (current_task_id IS NULL OR current_task_id = '')
	`)
	if err != nil {
		return fmt.Errorf("query idle agents: %w", err)
	}
	defer agentRows.Close()

	onlineIdle := make(map[string]bool)
	for agentRows.Next() {
		var agentID string
		if err := agentRows.Scan(&agentID); err != nil {
			continue
		}
		onlineIdle[agentID] = true
	}
	if err := agentRows.Err(); err != nil {
		return fmt.Errorf("iterate idle agents: %w", err)
	}

	if len(onlineIdle) == 0 {
		// No idle agents available — nothing to dispatch.
		return nil
	}

	dispatched := 0
	now := time.Now().UTC().Format(time.RFC3339)

	for _, t := range tasks {
		// Determine the gate result for stage/lane.
		gateResult := EnforceStageGate(t.domain, t.taskType)
		if !gateResult.Allowed {
			log.Printf("[Patrol:orchestrator-auto] stage gate blocked task %s (domain=%s type=%s): %s",
				t.id, t.domain, t.taskType, gateResult.BlockReason)
			continue
		}

		// Resolve the preferred role for this task.
		role := ResolveRoleForTask(roles, gateResult.Stage, t.taskType)

		// Get preferred agents for the resolved role.
		preferred := PreferredAgentsForRole(roles, role)

		// Find the first preferred agent that is online and idle.
		assigned := ""
		for _, candidate := range preferred {
			if onlineIdle[candidate] {
				assigned = candidate
				break
			}
		}

		// If no preferred agent is available, pick any online idle agent.
		if assigned == "" {
			for agentID := range onlineIdle {
				assigned = agentID
				break
			}
		}

		if assigned == "" {
			continue // still no agent found
		}

		// Claim the task for the chosen agent.
		result, err := db.ExecContext(ctx, `
			UPDATE tasks
			SET status      = 'assigned',
			    state       = 'DISPATCHED',
			    assigned_to = ?,
			    updated_at  = ?
			WHERE id     = ?
			  AND status  = 'queued'
			  AND (assigned_to IS NULL OR assigned_to = '')
		`, assigned, now, t.id)
		if err != nil {
			log.Printf("[Patrol:orchestrator-auto] failed to claim task %s for %s: %v", t.id, assigned, err)
			continue
		}
		if n, _ := result.RowsAffected(); n == 0 {
			// Another process claimed the task first — safe to skip.
			continue
		}

		log.Printf("[Patrol:orchestrator-auto] dispatched task %s (type=%s) to agent %s (role=%s)",
			t.id, t.taskType, assigned, role)

		// Mark the agent as busy so we do not double-assign to it this tick.
		delete(onlineIdle, assigned)
		dispatched++
	}

	if dispatched > 0 {
		log.Printf("[Patrol:orchestrator-auto] dispatched %d task(s) to idle agents", dispatched)
	}
	return nil
}

// orchestratorRequeueStale requeues tasks that are "assigned" but whose agent
// has been offline for more than 30 minutes.  Those tasks would otherwise block
// indefinitely until a human intervened.
func orchestratorRequeueStale(ctx context.Context, db *sql.DB) error {
	now := time.Now().UTC().Format(time.RFC3339)
	// Use a Go-computed threshold string (RFC3339) so it compares correctly
	// against last_seen values stored in the same format.
	thirtyMinutesAgo := time.Now().Add(-30 * time.Minute).UTC().Format(time.RFC3339)

	result, err := db.ExecContext(ctx, `
		UPDATE tasks
		SET status      = 'queued',
		    state       = 'QUEUED',
		    assigned_to = NULL,
		    updated_at  = ?
		WHERE status = 'assigned'
		  AND assigned_to IN (
		    SELECT agent_id
		    FROM agent_heartbeats
		    WHERE status    = 'offline'
		      AND last_seen < ?
		  )
	`, now, thirtyMinutesAgo)
	if err != nil {
		return fmt.Errorf("requeue stale tasks: %w", err)
	}

	if rows, _ := result.RowsAffected(); rows > 0 {
		log.Printf("[Patrol:orchestrator-auto] requeued %d task(s) from offline agents", rows)
	}
	return nil
}

// orchestratorRouteOpenClaw applies stage-gate checks and lane assignment to
// unrouted openclaw-domain tasks.  Up to 5 tasks are processed per tick.
func orchestratorRouteOpenClaw(ctx context.Context, db *sql.DB) error {
	rows, err := db.QueryContext(ctx, `
		SELECT id, type, title
		FROM tasks
		WHERE domain = 'openclaw'
		  AND status = 'queued'
		  AND (lane IS NULL OR lane = '')
		LIMIT 5
	`)
	if err != nil {
		return fmt.Errorf("query openclaw tasks: %w", err)
	}
	defer rows.Close()

	type openclawTask struct {
		id       string
		taskType string
		title    string
	}

	var tasks []openclawTask
	for rows.Next() {
		var t openclawTask
		if err := rows.Scan(&t.id, &t.taskType, &t.title); err != nil {
			log.Printf("[Patrol:orchestrator-auto] scan openclaw task: %v", err)
			continue
		}
		tasks = append(tasks, t)
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("iterate openclaw tasks: %w", err)
	}

	if len(tasks) == 0 {
		return nil
	}

	now := time.Now().UTC().Format(time.RFC3339)
	routed := 0

	for _, t := range tasks {
		// EnforceStageGate: openclaw is an infrastructure domain, always passes.
		gate := EnforceStageGate("openclaw", t.taskType)
		lane := AutoAssignLane("openclaw", t.taskType, "")

		if !gate.Allowed {
			log.Printf("[Patrol:orchestrator-auto] openclaw task %s blocked by stage gate: %s",
				t.id, gate.BlockReason)
			continue
		}

		_, err := db.ExecContext(ctx, `
			UPDATE tasks
			SET lane       = ?,
			    updated_at = ?
			WHERE id = ?
			  AND (lane IS NULL OR lane = '')
		`, lane, now, t.id)
		if err != nil {
			log.Printf("[Patrol:orchestrator-auto] failed to assign lane to openclaw task %s: %v", t.id, err)
			continue
		}

		log.Printf("[Patrol:orchestrator-auto] openclaw task %s assigned lane=%s (stage=%s)",
			t.id, lane, gate.Stage)
		routed++
	}

	if routed > 0 {
		log.Printf("[Patrol:orchestrator-auto] routed %d openclaw task(s) to lanes", routed)
	}
	return nil
}
