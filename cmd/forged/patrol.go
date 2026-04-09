//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// Patrol represents a background monitoring task
type Patrol struct {
	ID       string
	Name     string
	Schedule time.Duration
	Action   func(ctx context.Context, db *sql.DB) error
}

// ContextAwarePatrol is a patrol that has access to the ContextManager
type ContextAwarePatrol struct {
	Patrol
	cm *ContextManager
}

// PatrolSystem manages all patrols
type PatrolSystem struct {
	db             *sql.DB
	cm             *ContextManager
	rj             *RoyalJelly
	patrols        []Patrol
	contextPatrols []ContextAwarePatrol
	stop           chan struct{}
	wg             sync.WaitGroup
}

// NewPatrolSystem creates a new patrol system
func NewPatrolSystem(db *sql.DB) *PatrolSystem {
	ps := &PatrolSystem{
		db:             db,
		patrols:        StandardPatrols(),
		contextPatrols: []ContextAwarePatrol{},
		stop:           make(chan struct{}),
	}
	// Clean up ghost patrol rows from pre-consolidation — delete execution
	// records for patrol IDs that no longer exist in StandardPatrols().
	if db != nil {
		liveIDs := make(map[string]bool, len(ps.patrols))
		for _, p := range ps.patrols {
			liveIDs[p.ID] = true
		}
		rows, err := db.Query(`SELECT DISTINCT patrol_id FROM patrol_executions`)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var pid string
				if rows.Scan(&pid) == nil && !liveIDs[pid] {
					db.Exec(`DELETE FROM patrol_executions WHERE patrol_id = ?`, pid)
					log.Printf("[PatrolSystem] Cleaned ghost patrol rows: %s", pid)
				}
			}
		}
	}
	return ps
}

// SetContextManager sets the context manager for context-aware patrols
func (ps *PatrolSystem) SetContextManager(cm *ContextManager) {
	ps.cm = cm
}

// SetRoyalJelly sets the royal jelly hook system
func (ps *PatrolSystem) SetRoyalJelly(rj *RoyalJelly) {
	ps.rj = rj
}

// AddContextPatrol adds a context-aware patrol
func (ps *PatrolSystem) AddContextPatrol(p ContextAwarePatrol) {
	ps.contextPatrols = append(ps.contextPatrols, p)
}

// isDarkFactoryEnabled returns true if FORGE_DARK_FACTORY env var is "true" or "1".
// Default is false — dark-factory patrols are disabled until explicitly enabled.
func isDarkFactoryEnabled() bool {
	v := os.Getenv("FORGE_DARK_FACTORY")
	return v == "true" || v == "1"
}

// StandardPatrols returns the standard patrols. Overlapping patrols have been
// consolidated into wrapper functions to reduce log spam and cognitive load.
// Original count: 38. Current count: 22 base + 3 dark-factory (gated by FORGE_DARK_FACTORY).
// Council A1: harness-gap patrol dropped (unanimous, 2026-03-28).
// Council A2: task-timeout + stale-task-ttl + dispatch-timeout + phantom-task-cleanup
//             consolidated into task-lifecycle (approved with conditions, 2026-03-28).
// Council S195 P1 + S196 T3: patrol consolidation 38→25 (S195: approved 3-0 2026-04-05; S196: applied 2026-04-05).
//   S196 killed: agent-ttl-cleanup, doc-drift, research-expiry, result-verification,
//                content-auto-approve, state-freshness, patterns-extraction, context-threshold.
//   S195 killed: queue-depth, daily-digest, royal-jelly-staleness, rails8-drift, git-cleanup.
//   Merged: xnode-ack-processor + xnode-retry-serializer → xnode-sync.
// Dark-factory patrols (auto-promote, result-monitor, work-strategy) require FORGE_DARK_FACTORY=true.
func StandardPatrols() []Patrol {
	patrols := []Patrol{
		// --- Agent health (merged: health-check + heartbeat-ttl + agent-liveness) ---
		{
			ID:       "agent-health",
			Name:     "Agent Health (health-check + heartbeat-ttl + agent-liveness)",
			Schedule: 2 * time.Minute,
			Action:   agentHealthPatrol,
		},
		// --- Task lifecycle (merged: task-timeout + stale-task-ttl + dispatch-timeout + phantom-task-cleanup) ---
		// Council A2: consolidated to reduce patrol table size. Each sub-function runs at its
		// original effective frequency via taskLifecycleCounter (see taskLifecyclePatrol).
		{
			ID:       "task-lifecycle",
			Name:     "Task Lifecycle (timeout + stale-ttl + dispatch-timeout + phantom-cleanup)",
			Schedule: 1 * time.Minute, // Keep 1min for phantom-cleanup responsiveness
			Action:   taskLifecyclePatrol,
		},
		{
			ID:       "approval-expiry",
			Name:     "Approval Expiry",
			Schedule: 5 * time.Minute,
			Action:   expireOldApprovals,
		},
		{
			ID:       "context-sync",
			Name:     "Context Sync",
			Schedule: 10 * time.Minute,
			Action:   syncRoyalJelly,
		},
		{
			ID:       "worktree-prune",
			Name:     "Worktree Prune (ADR-024)",
			Schedule: 6 * time.Hour,
			Action:   pruneStaleWorktrees,
		},
		{
			ID:       "metrics-rollup",
			Name:     "Metrics Rollup (ADR-27)",
			Schedule: 15 * time.Minute,
			Action:   computeMetricsRollups,
		},
		{
			ID:       "auto-requeue",
			Name:     "Auto-Requeue Retriable Tasks",
			Schedule: 2 * time.Minute,
			Action:   requeueRetriableTasks,
		},
		// auto-promote: dark-factory (gated — see isDarkFactoryEnabled)
		// result-monitor: dark-factory (gated — see isDarkFactoryEnabled)
		{
			ID:       "confidence-approve",
			Name:     "Confidence-Score Auto-Approve (ADR-033 F3)",
			Schedule: 3 * time.Minute,
			Action:   confidenceApproveCompletedTasks,
		},
		{
			ID:       "binary-freshness",
			Name:     "Binary Freshness — auto-rebuild + restart on source change",
			Schedule: 5 * time.Minute,
			Action:   checkBinaryFreshness,
		},
		// --- Fleet scaling (merged: fleet-scale + fleet-auto into one patrol) ---
		{
			ID:       "fleet-scaling",
			Name:     "Fleet Scaling (recommend + execute + deflate)",
			Schedule: 2 * time.Minute,
			Action:   fleetScalingPatrol,
		},
		{
			ID:       "token-budget",
			Name:     "Token Budget Cooldown Reset (ADR-035)",
			Schedule: 5 * time.Minute,
			Action:   tokenBudgetCooldownResetPatrol,
		},
		{
			ID:       "council-cleanup",
			Name:     "Council TTL Cleanup (ADR-035 P2)",
			Schedule: 10 * time.Minute,
			Action:   councilCleanupPatrol,
		},
		// work-strategy: dark-factory (gated — see isDarkFactoryEnabled)
		{
			ID:       "task-dispatcher",
			Name:     "Task Queue → Tmux Dispatcher (S173)",
			Schedule: 30 * time.Second,
			Action:   taskDispatcherPatrol,
		},
		{
			ID:       "data-retention",
			Name:     "Data Retention (auto-prune + VACUUM)",
			Schedule: 24 * time.Hour,
			Action:   dataRetentionPatrol,
		},
		{
			ID:       "node-metrics-push",
			Name:     "Node Metrics Push to Lead (ADR-027)",
			Schedule: 10 * time.Minute,
			Action:   nodeMetricsPushPatrol,
		},
		// --- XNode sync (merged: xnode-ack-processor + xnode-retry-serializer) ---
		// Council S195 P1: consolidated to reduce patrol count.
		{
			ID:       "xnode-sync",
			Name:     "XNode Sync (ack-processor + retry-serializer, ADR-023)",
			Schedule: 2 * time.Minute,
			Action:   xnodeSyncPatrol,
		},
		{
			ID:       "orchestrator-auto",
			Name:     "Orchestrator Auto-Dispatch (heartbeat + dispatch + requeue + openclaw-route)",
			Schedule: 60 * time.Second,
			Action:   orchestratorAutoDispatch,
		},
		{
			ID:       "message-relay",
			Name:     "Cross-Node Message Relay (git-based, no HTTP required)",
			Schedule: 60 * time.Second,
			Action:   messageRelayPatrol,
		},
		{
			ID:       "dispatch-hygiene",
			Name:     "Dispatch Hygiene — archive stale dispatch and result files (Council S118)",
			Schedule: 7 * 24 * time.Hour,
			Action:   dispatchHygienePatrol,
		},
		{
			ID:       "stale-dispatch",
			Name:     "Stale Dispatch ACK — requeue tasks stuck in DISPATCHED > 10min (Epic 4)",
			Schedule: 5 * time.Minute,
			Action:   staleDispatchPatrol,
		},
		{
			ID:       "agent-recovery",
			Name:     "Agent Auto-Recovery (S173 Epic 1)",
			Schedule: 1 * time.Minute,
			Action:   agentAutoRecoveryPatrol,
		},
		{
			ID:       "context-compaction",
			Name:     "Context Compaction — warn/compact agents approaching context ceiling",
			Schedule: 10 * time.Minute,
			Action:   contextCompactionPatrol,
		},
		{
			ID:       "voice-health",
			Name:     "Voice Command Health — success rate and confidence trend monitoring",
			Schedule: 2 * time.Minute,
			Action:   voiceHealthPatrol,
		},
	}

	if isDarkFactoryEnabled() {
		patrols = append(patrols,
			Patrol{ID: "auto-promote", Name: "Auto-Promote Completed Lane Tasks (ADR-033)", Schedule: 5 * time.Minute, Action: autoPromoteCompletedTasksInLane},
			Patrol{ID: "result-monitor", Name: "Result File Monitor (result-monitor + result-ingest)", Schedule: 2 * time.Minute, Action: resultMonitorPatrol},
			Patrol{ID: "work-strategy", Name: "Orchestrator Work Strategy (ADR-036 P4)", Schedule: 10 * time.Minute, Action: orchestratorWorkStrategyPatrol},
		)
	}

	return patrols
}

// DisablePatrol removes a patrol from the active set by ID. Used for emergency rollback.
func (ps *PatrolSystem) DisablePatrol(id string) bool {
	for i, p := range ps.patrols {
		if p.ID == id {
			ps.patrols = append(ps.patrols[:i], ps.patrols[i+1:]...)
			log.Printf("[Patrol:%s] Disabled (emergency rollback)", id)
			return true
		}
	}
	return false
}

func checkAgentHealth(ctx context.Context, db *sql.DB) error {
	// Check last activity from all agents
	// Mark stale agents as offline
	fiveMinutesAgo := time.Now().Add(-5 * time.Minute).UTC().Format(time.RFC3339)

	rows, err := db.QueryContext(ctx, `
		SELECT id, last_activity FROM agents
		WHERE last_activity < ?
		AND status = 'online'
	`, fiveMinutesAgo)
	if err != nil {
		return fmt.Errorf("query stale agents: %w", err)
	}
	defer rows.Close()

	var staleAgents []string
	for rows.Next() {
		var agentID string
		var lastActivity sql.NullString
		if err := rows.Scan(&agentID, &lastActivity); err != nil {
			log.Printf("[Patrol:health-check] Error scanning agent: %v", err)
			continue
		}

		activityStr := "unknown"
		if lastActivity.Valid {
			activityStr = lastActivity.String
		}

		log.Printf("[Patrol:health-check] Agent %s stale (last activity: %s)",
			agentID, activityStr)
		staleAgents = append(staleAgents, agentID)
	}

	// OPTIMIZATION: Batch update all stale agents in a single query
	// instead of N individual UPDATEs
	if len(staleAgents) > 0 {
		placeholders := make([]string, len(staleAgents))
		args := make([]interface{}, len(staleAgents))
		for i, id := range staleAgents {
			placeholders[i] = "?"
			args[i] = id
		}

		query := fmt.Sprintf(
			"UPDATE agents SET status = 'offline' WHERE id IN (%s)",
			strings.Join(placeholders, ","),
		)
		_, err := db.ExecContext(ctx, query, args...)
		if err != nil {
			log.Printf("[Patrol:health-check] Failed to batch mark agents offline: %v", err)
		}
	}

	if len(staleAgents) > 0 {
		log.Printf("[Patrol:health-check] Marked %d stale agents as offline", len(staleAgents))
		// Trigger lease recovery so orphaned tasks are reclaimed immediately
		// rather than waiting for the 5-minute recovery goroutine.
		if leaseManager != nil {
			if recovered, err := leaseManager.Recover(ctx); err != nil {
				log.Printf("[Patrol:health-check] Lease recovery error: %v", err)
			} else if len(recovered) > 0 {
				log.Printf("[Patrol:health-check] Recovered %d orphaned leases after agent health check", len(recovered))
			}
		}
	}

	return nil
}

// markStaleHeartbeatsOffline marks agents in agent_heartbeats as offline if they
// haven't sent a heartbeat in > 3 minutes. This is the ADR-027 heartbeat TTL patrol
// and complements checkAgentHealth which targets the legacy agents table.
//
// Node-level heuristic (Bug A2): before marking agents offline, the function
// groups stale candidates by node.  If ALL agents on a given node are stale
// simultaneously (and the node has more than one registered agent), the patrol
// logs a warning that the cron heartbeat-refresh script may have stopped running
// on that node, rather than attributing the silence to individual agent crashes.
func markStaleHeartbeatsOffline(ctx context.Context, db *sql.DB) error {
	threeMinutesAgo := time.Now().Add(-3 * time.Minute).UTC().Format(time.RFC3339)

	// --- Node-level cron diagnostics ---
	// Count total agents per node, then count stale agents per node.  When the
	// two counts match for any node that has >1 agent, that is a strong signal
	// the heartbeat-refresh cron is broken on that node rather than all agents
	// crashing simultaneously.
	staleRows, err := db.QueryContext(ctx, `
		SELECT node, COUNT(*) AS stale_count
		FROM agent_heartbeats
		WHERE last_seen < ?
		AND status != 'offline'
		GROUP BY node
	`, threeMinutesAgo)
	if err == nil {
		defer staleRows.Close()

		// Build map of node -> stale count
		staleByNode := make(map[string]int)
		for staleRows.Next() {
			var node string
			var cnt int
			if scanErr := staleRows.Scan(&node, &cnt); scanErr == nil {
				staleByNode[node] = cnt
			}
		}
		staleRows.Close() // close early so we can run the next query

		if len(staleByNode) > 0 {
			// Total agents per node
			totalRows, qErr := db.QueryContext(ctx, `
				SELECT node, COUNT(*) AS total_count
				FROM agent_heartbeats
				GROUP BY node
			`)
			if qErr == nil {
				defer totalRows.Close()
				for totalRows.Next() {
					var node string
					var total int
					if scanErr := totalRows.Scan(&node, &total); scanErr == nil {
						if stale, ok := staleByNode[node]; ok && total > 1 && stale == total {
							log.Printf("[Patrol:heartbeat-ttl] WARNING: all %d agents on node %q are simultaneously stale — heartbeat-refresh cron may be broken on that node. Recovery: SSH to %s and run: .forge/scripts/heartbeat-refresh.sh", total, node, node)
						}
					}
				}
				totalRows.Close()
			}
		}
	}
	// --- end node-level diagnostics ---

	result, err := db.ExecContext(ctx, `
		UPDATE agent_heartbeats
		SET status = 'offline'
		WHERE last_seen < ?
		AND status != 'offline'
	`, threeMinutesAgo)
	if err != nil {
		return fmt.Errorf("mark stale heartbeats offline: %w", err)
	}

	if rows, _ := result.RowsAffected(); rows > 0 {
		log.Printf("[Patrol:heartbeat-ttl] Marked %d stale agents offline (no heartbeat > 3min)", rows)
	}

	return nil
}

// agentTTLCleanup deletes stale (non-online) agents from agent_heartbeats that
// have not been seen in more than 24 hours. Runs every 6 hours.
func agentTTLCleanup(ctx context.Context, db *sql.DB) error {
	result, err := db.ExecContext(ctx, `
		DELETE FROM agent_heartbeats
		WHERE status != 'online'
		AND last_seen < datetime('now', '-24 hours')
	`)
	if err != nil {
		return fmt.Errorf("agent-ttl-cleanup: %w", err)
	}

	if rows, _ := result.RowsAffected(); rows > 0 {
		log.Printf("[Patrol:agent-ttl-cleanup] Deleted %d stale agents (non-online, last_seen > 24h)", rows)
	}

	return nil
}

// requeueRetriableTasks automatically requeues failed tasks that have retry_count < 3
// and have been failed for at least 60 seconds. It builds a failure_context JSON
// blob for each task so downstream agents can make remediation-aware decisions.
func requeueRetriableTasks(ctx context.Context, db *sql.DB) error {
	sixtySecondsAgo := time.Now().Add(-60 * time.Second).UTC().Format(time.RFC3339)
	rows, err := db.QueryContext(ctx, `
		SELECT id, COALESCE(error, ''), COALESCE(result, ''), retry_count
		FROM tasks
		WHERE status = 'failed'
		AND (state = 'FAILED' OR state IS NULL)
		AND retry_count < 3
		AND updated_at < ?
	`, sixtySecondsAgo)
	if err != nil {
		return fmt.Errorf("auto-requeue query: %w", err)
	}
	defer rows.Close()

	requeued := 0
	for rows.Next() {
		var id, taskError, taskResult string
		var retryCount int
		if err := rows.Scan(&id, &taskError, &taskResult, &retryCount); err != nil {
			continue
		}
		// Truncate to avoid bloating DB
		if len(taskError) > 500 {
			taskError = taskError[:500]
		}
		if len(taskResult) > 200 {
			taskResult = taskResult[:200]
		}
		// Use json.Marshal for safe encoding (handles newlines, quotes, etc.)
		fcMap := map[string]interface{}{
			"attempt":     retryCount + 1,
			"error":       taskError,
			"output_tail": taskResult,
		}
		fcBytes, _ := json.Marshal(fcMap)
		fc := string(fcBytes)

		_, execErr := db.ExecContext(ctx, `
			UPDATE tasks
			SET status = 'queued', state = 'QUEUED',
				assigned_to = NULL,
				failure_context = ?,
				updated_at = datetime('now')
			WHERE id = ?
		`, fc, id)
		if execErr != nil {
			log.Printf("[Patrol:auto-requeue] requeue error %s: %v", id, execErr)
			continue
		}
		requeued++
	}
	if requeued > 0 {
		log.Printf("[Patrol:auto-requeue] Requeued %d tasks with failure context", requeued)
	}
	return rows.Err()
}

func handleTimedOutTasks(ctx context.Context, db *sql.DB) error {
	// Find tasks executing for > 30 minutes
	// Release them back to queued state
	thirtyMinutesAgo := time.Now().UTC().Add(-30 * time.Minute).Format(time.RFC3339)
	now := time.Now().UTC().Format(time.RFC3339)

	result, err := db.ExecContext(ctx, `
		UPDATE tasks
		SET status = ?, assigned_to = NULL, updated_at = ?
		WHERE status = ?
		AND started_at < ?
	`, TaskStatusQueued, now, TaskStatusExecuting, thirtyMinutesAgo)

	if err != nil {
		return fmt.Errorf("update timed out tasks: %w", err)
	}

	if rows, _ := result.RowsAffected(); rows > 0 {
		log.Printf("[Patrol:task-timeout] Released %d timed out tasks", rows)
	}

	return nil
}

func expireOldApprovals(ctx context.Context, db *sql.DB) error {
	// Mark expired approvals
	now := time.Now().UTC().Format(time.RFC3339)

	result, err := db.ExecContext(ctx, `
		UPDATE approvals
		SET status = 'expired'
		WHERE status = 'pending'
		AND expires_at < ?
	`, now)

	if err != nil {
		return fmt.Errorf("expire old approvals: %w", err)
	}

	if rows, _ := result.RowsAffected(); rows > 0 {
		log.Printf("[Patrol:approval-expiry] Expired %d approvals", rows)
	}

	return nil
}

func syncRoyalJelly(ctx context.Context, db *sql.DB) error {
	// Royal Jelly: Bidirectional sync between SQLite context_envelopes and filesystem
	// 1. Filesystem -> DB: context_pct files update agent context percentages
	// 2. DB -> Filesystem: Recently updated envelopes write back to lead-context.md

	// Use forgeRoot() so this works regardless of daemon CWD (e.g. started from cmd/forged/).
	contextDir := filepath.Join(forgeRoot(), ".forge", "context")

	// Phase 1: Filesystem -> DB (existing behavior)
	entries, err := os.ReadDir(contextDir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil // Context directory doesn't exist yet
		}
		return fmt.Errorf("read context directory: %w", err)
	}

	syncCount := 0
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}

		domain := e.Name()
		pctPath := filepath.Join(contextDir, domain, "context_pct")

		data, err := os.ReadFile(pctPath)
		if err != nil {
			continue // File doesn't exist, skip
		}

		var pct float64
		if _, err := fmt.Sscanf(strings.TrimSpace(string(data)), "%f", &pct); err != nil {
			continue
		}

		// Update agent context percentage if agent exists for this domain
		_, err = db.ExecContext(ctx, `
			UPDATE agents 
			SET context_pct = ? 
			WHERE id = ? OR name = ?
		`, pct, domain, domain)

		if err == nil {
			syncCount++
		}
	}

	// Phase 2: DB -> Filesystem (Royal Jelly write-back)
	// Query envelopes updated in the last hour and sync to filesystem
	rows, err := db.QueryContext(ctx, `
		SELECT domain, content, created_at
		FROM context_envelopes
		WHERE created_at > datetime('now', '-1 hour')
		ORDER BY domain, created_at DESC
	`)
	if err != nil {
		log.Printf("[Patrol:royal-jelly] Failed to query envelopes: %v", err)
		// Don't fail the whole patrol - continue with Phase 1 results
	} else {
		defer rows.Close()

		type envelopeInfo struct {
			domain    string
			content   string
			createdAt string
		}

		// Track latest envelope per domain
		domainEnvelopes := make(map[string]envelopeInfo)
		for rows.Next() {
			var info envelopeInfo
			if err := rows.Scan(&info.domain, &info.content, &info.createdAt); err != nil {
				log.Printf("[Patrol:royal-jelly] Error scanning envelope: %v", err)
				continue
			}
			// Keep the most recent envelope per domain
			if existing, ok := domainEnvelopes[info.domain]; !ok || info.createdAt > existing.createdAt {
				domainEnvelopes[info.domain] = info
			}
		}

		// Write each domain's envelope back to filesystem
		writebackCount := 0
		for domain, info := range domainEnvelopes {
			domainDir := filepath.Join(contextDir, domain)
			if err := os.MkdirAll(domainDir, 0755); err != nil {
				log.Printf("[Patrol:royal-jelly] Failed to create domain dir %s: %v", domainDir, err)
				continue
			}

			// Parse envelope content to extract lead-context
			var envelope struct {
				Metadata map[string]interface{} `json:"metadata"`
				Decisions []Decision            `json:"decisions"`
				Failures  []Failure             `json:"failures"`
				Summary   string                `json:"summary"`
			}
			if err := json.Unmarshal([]byte(info.content), &envelope); err != nil {
				log.Printf("[Patrol:royal-jelly] Failed to parse envelope for %s: %v", domain, err)
				continue
			}

			// Write lead-context.md
			leadContextPath := filepath.Join(domainDir, "lead-context.md")
			if leadContext, ok := envelope.Metadata["lead_context"].(string); ok && leadContext != "" {
				if err := os.WriteFile(leadContextPath, []byte(leadContext), 0644); err != nil {
					log.Printf("[Patrol:royal-jelly] Failed to write %s: %v", leadContextPath, err)
					continue
				}
			} else {
				// Create a default lead-context.md from envelope summary
				content := buildLeadContextFromEnvelope(domain, envelope.Summary, envelope.Decisions, envelope.Failures)
				if err := os.WriteFile(leadContextPath, []byte(content), 0644); err != nil {
					log.Printf("[Patrol:royal-jelly] Failed to write %s: %v", leadContextPath, err)
					continue
				}
			}

			// Write decisions.json if decisions exist
			if len(envelope.Decisions) > 0 {
				decisionsPath := filepath.Join(domainDir, "decisions.json")
				decisionsData, _ := json.MarshalIndent(envelope.Decisions, "", "  ")
				if err := os.WriteFile(decisionsPath, decisionsData, 0644); err != nil {
					log.Printf("[Patrol:royal-jelly] Failed to write %s: %v", decisionsPath, err)
				}
			}

			// Write failures.json if failures exist
			if len(envelope.Failures) > 0 {
				failuresPath := filepath.Join(domainDir, "failures.json")
				failuresData, _ := json.MarshalIndent(envelope.Failures, "", "  ")
				if err := os.WriteFile(failuresPath, failuresData, 0644); err != nil {
					log.Printf("[Patrol:royal-jelly] Failed to write %s: %v", failuresPath, err)
				}
			}

			writebackCount++
			log.Printf("[Patrol:royal-jelly] Synced envelope to %s/lead-context.md", domain)
		}

		if writebackCount > 0 {
			log.Printf("[Patrol:royal-jelly] Synced %d envelopes to filesystem", writebackCount)
		}
	}

	if syncCount > 0 {
		log.Printf("[Patrol:context-sync] Synced %d context envelopes", syncCount)
	}

	// Royal Jelly staleness detection (ADR-052 Phase 2)
	// Skip symlinks (P2-2) and node-level contexts (P2-1: low-frequency, only update on handoffs)
	nodeContexts := map[string]bool{
		"forge": true, "gaea": true, "nova": true, "oc": true, "prya": true, "sati": true,
		"archive": true, "pace": true,
	}
	staleThreshold := 7 * 24 * time.Hour
	if entries, err := os.ReadDir(contextDir); err == nil {
		var staleDomains []string
		for _, entry := range entries {
			if !entry.IsDir() {
				continue
			}
			// Skip symlinks — they duplicate real domain dirs
			if entry.Type()&os.ModeSymlink != 0 {
				continue
			}
			// Skip node-level contexts — they only update on handoffs (expected staleness)
			if nodeContexts[entry.Name()] {
				continue
			}
			lcPath := filepath.Join(contextDir, entry.Name(), "lead-context.md")
			if info, err := os.Stat(lcPath); err == nil {
				if time.Since(info.ModTime()) > staleThreshold {
					staleDomains = append(staleDomains, entry.Name())
				}
			}
		}
		if len(staleDomains) > 0 {
			log.Printf("[Patrol:royal-jelly] Stale contexts detected: count=%d domains=%s threshold=7d",
				len(staleDomains), strings.Join(staleDomains, ", "))
		}
	}

	return nil
}

// buildLeadContextFromEnvelope creates a markdown lead-context from envelope data
func buildLeadContextFromEnvelope(domain, summary string, decisions []Decision, failures []Failure) string {
	var b strings.Builder
	b.WriteString(fmt.Sprintf("# %s Domain Context\n\n", strings.ToUpper(domain)))
	b.WriteString(fmt.Sprintf("**Last Updated:** %s\n\n", time.Now().Format("2006-01-02")))

	if summary != "" {
		b.WriteString("## Summary\n\n")
		b.WriteString(summary)
		b.WriteString("\n\n")
	}

	if len(decisions) > 0 {
		b.WriteString("## Recent Decisions\n\n")
		for _, d := range decisions {
			b.WriteString(fmt.Sprintf("- **%s**: %s\n", d.ID[:8], d.Description))
		}
		b.WriteString("\n")
	}

	if len(failures) > 0 {
		b.WriteString("## Lessons Learned\n\n")
		for _, f := range failures {
			b.WriteString(fmt.Sprintf("- **%s**: %s\n", f.ID[:8], f.Lesson))
		}
		b.WriteString("\n")
	}

	b.WriteString("---\n\n")
	b.WriteString("*Auto-generated by Royal Jelly sync*\n")

	return b.String()
}

// pruneStaleWorktrees removes worktrees for completed/failed tasks (ADR-024).
// Called periodically by the patrol system.
func pruneStaleWorktrees(ctx context.Context, db *sql.DB) error {
	repoRoot := os.Getenv("FORGE_ROOT")
	if repoRoot == "" {
		repoRoot = "."
	}
	wm := NewWorktreeManager(repoRoot, "", db)
	return wm.PruneStaleWorktrees(ctx)
}

// checkContextThreshold detects agents at >= 50% context and auto-generates envelopes
func checkContextThreshold(ctx context.Context, db *sql.DB, cm *ContextManager, rj *RoyalJelly) error {
	if cm == nil {
		return nil // Context manager not initialized yet
	}

	// Query agents with context_pct >= 50 that haven't had an envelope created in the last hour
	rows, err := db.QueryContext(ctx, `
		SELECT ah.agent_id, ah.context_pct, ah.node, ah.current_task_id
		FROM agent_heartbeats ah
		WHERE ah.context_pct >= 50
		AND ah.agent_id NOT IN (
			SELECT DISTINCT agent_id 
			FROM context_envelopes 
			WHERE created_at > datetime('now', '-1 hour')
		)
	`)
	if err != nil {
		return fmt.Errorf("query agents at threshold: %w", err)
	}
	defer rows.Close()

	type thresholdAgent struct {
		agentID    string
		contextPct float64
		node       string
		taskID     string
	}

	var agents []thresholdAgent
	for rows.Next() {
		var a thresholdAgent
		if err := rows.Scan(&a.agentID, &a.contextPct, &a.node, &a.taskID); err != nil {
			log.Printf("[Patrol:context-threshold] Error scanning agent: %v", err)
			continue
		}
		agents = append(agents, a)
	}

	for _, agent := range agents {
		// Generate envelope for this agent
		log.Printf("[Patrol:context-threshold] Agent %s at %.0f%% context - generating envelope", agent.agentID, agent.contextPct)

		envelope, err := cm.GenerateEnvelope(ctx, agent.agentID, agent.node, "", agent.taskID,
			fmt.Sprintf("Auto-generated: Agent reached %.0f%% context threshold", agent.contextPct))
		if err != nil {
			log.Printf("[Patrol:context-threshold] Failed to generate envelope for %s: %v", agent.agentID, err)
			continue
		}

		log.Printf("[Patrol:context-threshold] Successfully generated envelope %s for %s", envelope.ID, agent.agentID)

		// Trigger RoyalJelly hooks if available
		if rj != nil {
			event := HookEvent{
				Type:       "context_threshold",
				AgentID:    agent.agentID,
				ContextPct: agent.contextPct,
				Timestamp:  time.Now().Unix(),
				Metadata: map[string]interface{}{
					"domain":      agent.node,
					"project":     "",
					"task_id":     agent.taskID,
					"envelope_id": envelope.ID,
				},
			}
			if err := rj.TriggerHooks(ctx, event); err != nil {
				log.Printf("[Patrol:context-threshold] Error triggering hooks: %v", err)
			}
		}
	}

	return nil
}

// Start begins all patrols
func (ps *PatrolSystem) Start() {
	totalPatrols := len(ps.patrols) + len(ps.contextPatrols)
	log.Printf("[Patrol] Starting %d patrols", totalPatrols)

	// Start standard patrols
	for _, patrol := range ps.patrols {
		ps.wg.Add(1)
		go func(p Patrol) {
			defer ps.wg.Done()
			ps.runPatrol(p)
		}(patrol)
	}

	// Start context-aware patrols
	for _, patrol := range ps.contextPatrols {
		ps.wg.Add(1)
		go func(p ContextAwarePatrol) {
			defer ps.wg.Done()
			ps.runContextPatrol(p)
		}(patrol)
	}
}

// Stop halts all patrols and waits for goroutines to complete
func (ps *PatrolSystem) Stop() {
	log.Println("[Patrol] Stopping all patrols")
	close(ps.stop)
	ps.wg.Wait() // Wait for all patrol goroutines to exit
	log.Println("[Patrol] All patrols stopped")
}

func (ps *PatrolSystem) runPatrol(patrol Patrol) {
	log.Printf("[Patrol:%s] Starting with schedule %v", patrol.ID, patrol.Schedule)

	ticker := time.NewTicker(patrol.Schedule)
	defer ticker.Stop()

	// Run immediately on start
	ps.executePatrol(patrol)

	for {
		select {
		case <-ticker.C:
			ps.executePatrol(patrol)

		case <-ps.stop:
			log.Printf("[Patrol:%s] Stopping", patrol.ID)
			return
		}
	}
}

func (ps *PatrolSystem) runContextPatrol(patrol ContextAwarePatrol) {
	log.Printf("[Patrol:%s] Starting with schedule %v", patrol.ID, patrol.Schedule)

	ticker := time.NewTicker(patrol.Schedule)
	defer ticker.Stop()

	// Run immediately on start
	ps.executeContextPatrol(patrol)

	for {
		select {
		case <-ticker.C:
			ps.executeContextPatrol(patrol)

		case <-ps.stop:
			log.Printf("[Patrol:%s] Stopping", patrol.ID)
			return
		}
	}
}

func (ps *PatrolSystem) executeContextPatrol(patrol ContextAwarePatrol) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()

	start := time.Now()
	RecordPatrolExecution(ps.db, patrol.ID)

	// Use the context-aware action with ContextManager
	if err := patrol.Action(ctx, ps.db); err != nil {
		RecordPatrolError(ps.db, patrol.ID, err.Error())
		log.Printf("[Patrol:%s] Error: %v", patrol.ID, err)
		return
	}

	// Run the threshold check with ContextManager
	if err := checkContextThreshold(ctx, ps.db, patrol.cm, ps.rj); err != nil {
		RecordPatrolError(ps.db, patrol.ID, err.Error())
		log.Printf("[Patrol:%s] Threshold check error: %v", patrol.ID, err)
		return
	}

	RecordPatrolSuccess(ps.db, patrol.ID)
	log.Printf("[Patrol:%s] Completed in %s", patrol.ID, time.Since(start))
}

func (ps *PatrolSystem) executePatrol(patrol Patrol) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()

	start := time.Now()
	RecordPatrolExecution(ps.db, patrol.ID)

	if err := patrol.Action(ctx, ps.db); err != nil {
		RecordPatrolError(ps.db, patrol.ID, err.Error())
		log.Printf("[Patrol:%s] Error: %v", patrol.ID, err)
		return
	}

	RecordPatrolSuccess(ps.db, patrol.ID)
	log.Printf("[Patrol:%s] Completed in %s", patrol.ID, time.Since(start))
}

// ListPatrols returns the list of configured patrols
func (ps *PatrolSystem) ListPatrols() []Patrol {
	return ps.patrols
}

// RunPatrolByID runs a single patrol by ID once (for "Run Now" from UI). Returns true if the patrol was found and executed.
func (ps *PatrolSystem) RunPatrolByID(id string) bool {
	for _, p := range ps.patrols {
		if p.ID == id {
			ps.executePatrol(p)
			return true
		}
	}
	for _, cp := range ps.contextPatrols {
		if cp.ID == id {
			ps.executeContextPatrol(cp)
			return true
		}
	}
	return false
}

// autoPromoteCompletedTasksInLane advances tasks that are COMPLETED in a lane
// to the next lane via ProgressTask(). Runs every 5 minutes. If gates fail,
// ProgressTask returns an error — this patrol just calls it and logs.
func autoPromoteCompletedTasksInLane(ctx context.Context, db *sql.DB) error {
	rows, err := db.QueryContext(ctx, `
		SELECT id, title, lane FROM tasks
		WHERE state = 'COMPLETED'
		AND lane IS NOT NULL AND lane != '' AND lane != 'done'
		AND updated_at < datetime('now', '-60 seconds')
		LIMIT 20
	`)
	if err != nil {
		return fmt.Errorf("autoPromote query: %w", err)
	}
	defer rows.Close()

	promoted, blocked := 0, 0
	for rows.Next() {
		var id, title, lane string
		if err := rows.Scan(&id, &title, &lane); err != nil {
			continue
		}
		if globalLaneManager == nil {
			break
		}
		if _, err := globalLaneManager.ProgressTask(ctx, id); err != nil {
			log.Printf("[Patrol:auto-promote] blocked %s (%s→next): %v", id, lane, err)
			blocked++
		} else {
			log.Printf("[Patrol:auto-promote] promoted %s from %s", id, lane)
			promoted++
		}
	}
	if promoted+blocked > 0 {
		log.Printf("[Patrol:auto-promote] promoted=%d blocked=%d", promoted, blocked)
	}
	return rows.Err()
}

// monitorResultFiles scans .forge/heartbeat/results/ for agent result files
// and auto-completes tasks whose agents have written results. File naming
// convention: {agent}-{task-id}.md — the task ID is matched by checking which
// active task ID appears as a substring in the filename. Processed files are
// moved to results/processed/.
func monitorResultFiles(ctx context.Context, db *sql.DB) error {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}
	resultsDir := filepath.Join(forgeRoot, ".forge", "heartbeat", "results")
	processedDir := filepath.Join(resultsDir, "processed")
	if err := os.MkdirAll(processedDir, 0755); err != nil {
		return fmt.Errorf("monitorResultFiles mkdir: %w", err)
	}

	entries, err := os.ReadDir(resultsDir)
	if err != nil {
		return fmt.Errorf("monitorResultFiles readdir: %w", err)
	}

	ingested := 0
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".md") {
			continue
		}

		name := strings.TrimSuffix(e.Name(), ".md")
		parts := strings.Split(name, "-")
		if len(parts) < 2 {
			continue
		}

		filePath := filepath.Join(resultsDir, e.Name())
		content, readErr := os.ReadFile(filePath)
		if readErr != nil {
			continue
		}

		// Find matching task: look for task IDs whose ID appears in the filename
		taskID, found := findTaskIDInFilename(ctx, db, name)
		if !found {
			continue // no task matched — skip
		}

		// Check if task is in a completable state
		var state string
		err := db.QueryRowContext(ctx, `SELECT state FROM tasks WHERE id = ?`, taskID).Scan(&state)
		if err != nil || state == "COMPLETED" || state == "APPROVED" || state == "CANCELLED" {
			continue // already done or not found
		}

		// Mark as complete with result summary
		summary := extractResultSummary(string(content))
		_, execErr := db.ExecContext(ctx, `
			UPDATE tasks SET state='COMPLETED', status='completed',
			result=?, updated_at=datetime('now')
			WHERE id=? AND state IN ('RUNNING','DISPATCHED','QUEUED')
		`, summary, taskID)
		if execErr != nil {
			log.Printf("[Patrol:result-monitor] failed to complete %s: %v", taskID, execErr)
			continue
		}

		// NOTE: Per-task Telegram alerts removed (S179+ — too noisy).
		// Task completions are batched in the daily digest and gate alerts.
		// Only alert on genuinely important events (failures, blockers, gates).

		// Best-effort quality gate results insertion — never blocks auto-completion.
		// Pass full file content so structured <!-- QUALITY_GATE --> blocks are detected
		// before falling back to regex heuristics on the summary.
		go insertQualityGateResults(ctx, db, taskID, string(content))

		// Move to processed/
		dest := filepath.Join(processedDir, e.Name())
		if mvErr := os.Rename(filePath, dest); mvErr != nil {
			log.Printf("[Patrol:result-monitor] rename failed %s: %v", e.Name(), mvErr)
		}
		log.Printf("[Patrol:result-monitor] auto-completed task %s from %s", taskID, e.Name())
		ingested++
	}
	if ingested > 0 {
		log.Printf("[Patrol:result-monitor] ingested %d result files", ingested)
	}
	return nil
}

// findTaskIDInFilename checks the DB for task IDs that appear as a substring of filename.
func findTaskIDInFilename(ctx context.Context, db *sql.DB, filename string) (string, bool) {
	rows, err := db.QueryContext(ctx, `
		SELECT id FROM tasks
		WHERE state IN ('RUNNING','DISPATCHED','QUEUED')
		LIMIT 200
	`)
	if err != nil {
		return "", false
	}
	defer rows.Close()
	for rows.Next() {
		var id string
		if rows.Scan(&id) == nil && strings.Contains(filename, id) {
			return id, true
		}
	}
	return "", false
}

// calculateConfidenceScore queries quality_gate_results for the task and computes
// a weighted confidence score. Falls back to 0.70 if no gate results exist.
//
// Weights:
//   - 40%: test pass rate (0.0–1.0)
//   - 30%: coverage / 100.0
//   - 20%: max(0, 1.0 - lint_issues/50.0)
//   - 10%: fixed 1.0 (git-based = reversible)
func calculateConfidenceScore(ctx context.Context, db *sql.DB, task *Task) float64 {
	const defaultScore = 0.70

	// Query quality_gate_results — table may not exist yet; handle gracefully.
	var testPassRate, coveragePct float64
	var lintIssues int
	err := db.QueryRowContext(ctx, `
		SELECT
			COALESCE(test_pass_rate, 0.0),
			COALESCE(coverage_pct, 0.0),
			COALESCE(lint_issues, 0)
		FROM quality_gate_results
		WHERE task_id = ?
		ORDER BY created_at DESC
		LIMIT 1
	`, task.ID).Scan(&testPassRate, &coveragePct, &lintIssues)

	if err != nil {
		// Table missing or no quality gate row for this task.
		// Return a sentinel value BELOW the auto-approve threshold so the
		// patrol creates a human approval request rather than auto-approving
		// work that has no measured quality signal.
		// The caller (confidenceApproveCompletedTasks) treats score < 0.65
		// as "needs human review".
		return 0.0
	}

	testScore := testPassRate                                        // 0.0–1.0
	coverageScore := coveragePct / 100.0                            // 0.0–1.0
	lintScore := 1.0 - float64(lintIssues)/50.0                    // linear decay
	if lintScore < 0 {
		lintScore = 0
	}
	gitScore := 1.0 // git-based change = always reversible

	score := testScore*0.40 + coverageScore*0.30 + lintScore*0.20 + gitScore*0.10
	return score
}

// blastRadiusFromResult attempts to parse the number of files changed from a
// task result JSON string. Expected key: "files_changed" (int). Returns 1 if
// the result field is absent, blank, or not parseable.
func blastRadiusFromResult(result string) int {
	if result == "" {
		return 1
	}
	var payload struct {
		FilesChanged int `json:"files_changed"`
		BlastRadius  int `json:"blast_radius"`
	}
	if err := json.Unmarshal([]byte(result), &payload); err != nil {
		return 1
	}
	if payload.FilesChanged > 0 {
		return payload.FilesChanged
	}
	if payload.BlastRadius > 0 {
		return payload.BlastRadius
	}
	return 1
}

// confidenceApproveCompletedTasks scans COMPLETED tasks and auto-promotes them
// to APPROVED if their confidence score meets the threshold (ADR-033 F3).
// Uses blast_radius from the task result JSON and quality_gate_results metadata.
// Auto-approve threshold: 0.65. Below threshold creates a human approval request.
func confidenceApproveCompletedTasks(ctx context.Context, db *sql.DB) error {
	if stateMachine == nil || globalApprovalService == nil {
		return nil // Not initialized yet
	}

	rows, err := db.QueryContext(ctx, `
		SELECT id, title, assigned_to, domain, COALESCE(result, '')
		FROM tasks
		WHERE state = 'COMPLETED'
		AND updated_at < datetime('now', '-30 seconds')
		LIMIT 20
	`)
	if err != nil {
		return fmt.Errorf("confidenceApprove query: %w", err)
	}
	defer rows.Close()

	const autoApproveThreshold = 0.65

	approved, pending := 0, 0
	for rows.Next() {
		var id, title, assignedTo, domain, taskResult string
		if err := rows.Scan(&id, &title, &assignedTo, &domain, &taskResult); err != nil {
			continue
		}

		task := &Task{ID: id, Title: title, AssignedTo: assignedTo, Domain: domain, Result: taskResult}

		// Compute real confidence score from quality gate results.
		score := calculateConfidenceScore(ctx, db, task)

		// Build confidence context with real values.
		ctx2 := ConfidenceContext{
			BlastRadius: blastRadiusFromResult(taskResult),
			Reversible:  true, // All FORGE tasks are git-based, so reversible
		}

		result := globalApprovalService.CalculateConfidence(ctx2)
		// Override the score with the real quality-gate-weighted score.
		// Keep tier/reasoning from CalculateConfidence but apply our threshold.
		result.Score = score
		result.AutoApprove = score >= autoApproveThreshold

		if result.AutoApprove {
			if err := stateMachine.Transition(id, StateCompleted, StateApproved,
				fmt.Sprintf("auto-approved: confidence=%.0f%% (%s)", result.Score*100, result.Reasoning)); err != nil {
				log.Printf("[Patrol:confidence-approve] transition error for %s: %v", id, err)
			} else {
				log.Printf("[Patrol:confidence-approve] approved %s (%.0f%%)", id, result.Score*100)
				approved++
			}
		} else {
			// Check if an approval request already exists for this task
			existing, err := globalApprovalService.GetPending(ctx, 100)
			if err == nil {
				alreadyPending := false
				for _, app := range existing {
					if app.TaskID != nil && *app.TaskID == id {
						alreadyPending = true
						break
					}
				}
				if alreadyPending {
					continue
				}
			}

			// Create approval request for low-confidence tasks
			_, appErr := globalApprovalService.CreateApprovalRequest(
				ctx,
				ApprovalTaskCompletion,
				&id,
				assignedTo,
				domain,
				fmt.Sprintf("Approval needed: %s", title),
				fmt.Sprintf("Confidence %.0f%% below auto-approve threshold: %s", result.Score*100, result.Reasoning),
				nil,
				ctx2,
			)
			if appErr != nil {
				log.Printf("[Patrol:confidence-approve] approval request error for %s: %v", id, appErr)
			} else {
				log.Printf("[Patrol:confidence-approve] approval requested for %s (%.0f%%)", id, result.Score*100)
				pending++
			}
		}
	}
	if approved+pending > 0 {
		log.Printf("[Patrol:confidence-approve] auto-approved=%d pending-review=%d", approved, pending)
	}
	return rows.Err()
}

// extractResultSummary extracts a brief summary from result file content.
func extractResultSummary(content string) string {
	lines := strings.Split(content, "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "## Status") || strings.HasPrefix(line, "# Status") {
			return line
		}
	}
	if len(content) > 500 {
		return content[:500]
	}
	return content
}

// dataRetentionPatrol auto-abandons stale queued tasks (older than 7 days)
// and runs SQLite VACUUM to reclaim space. Writes summary to data-retention-summary.json.
func dataRetentionPatrol(ctx context.Context, db *sql.DB) error {
	cutoff := time.Now().Add(-7 * 24 * time.Hour).UTC().Format("2006-01-02 15:04:05")
	updatedAt := time.Now().UTC().Format("2006-01-02 15:04:05")

	// Only abandon truly orphaned queued tasks:
	// - Must be unassigned (no agent claimed it)
	// - Must not be in a Dark Factory lane (lane IS NULL or empty)
	// - Must not have a pending human approval (approval would block completion anyway)
	// This prevents silently destroying valid pipeline tasks waiting on dependencies.
	result, err := db.ExecContext(ctx, `
		UPDATE tasks SET status = 'abandoned', updated_at = ?
		WHERE status = 'queued'
		  AND created_at < ?
		  AND (assigned_to IS NULL OR assigned_to = '')
		  AND (lane IS NULL OR lane = '')
	`, updatedAt, cutoff)
	if err != nil {
		return fmt.Errorf("data-retention abandon: %w", err)
	}
	abandoned, _ := result.RowsAffected()

	_, err = db.ExecContext(ctx, "VACUUM")
	vacuumOK := err == nil
	if err != nil {
		log.Printf("[Patrol:data-retention] VACUUM failed: %v", err)
	}

	type summary struct {
		Timestamp string `json:"timestamp"`
		Abandoned int64  `json:"abandoned_count"`
		VacuumOK  bool   `json:"vacuum_ok"`
	}
	s := summary{
		Timestamp: time.Now().UTC().Format(time.RFC3339),
		Abandoned: abandoned,
		VacuumOK:  vacuumOK,
	}
	data, _ := json.MarshalIndent(s, "", "  ")
	root := forgeRoot()
	resultPath := filepath.Join(root, ".forge", "heartbeat", "results", "data-retention-summary.json")
	if err := os.MkdirAll(filepath.Dir(resultPath), 0755); err != nil {
		return fmt.Errorf("data-retention mkdir: %w", err)
	}
	if err := os.WriteFile(resultPath, data, 0644); err != nil {
		return fmt.Errorf("data-retention write: %w", err)
	}
	if abandoned > 0 {
		log.Printf("[Patrol:data-retention] abandoned %d stale queued tasks, vacuum_ok=%v", abandoned, vacuumOK)
	}
	return nil
}

// checkBinaryFreshness compares the mtime of the running daemon binary vs.
// the newest Go source file in cmd/forged/. If the binary is more than
// 2 minutes stale it rebuilds via `go build` and restarts the daemon using
// `forge daemon restart`. The threshold is short so code changes are picked
// up automatically without manual intervention.
func checkBinaryFreshness(ctx context.Context, db *sql.DB) error {
	root := forgeRoot()
	binaryPath := filepath.Join(root, "cmd", "forged", "forged")
	srcDir := filepath.Join(root, "cmd", "forged")

	// Cooldown: skip if a recent build failure exists (retry after 1 hour or when new source arrives)
	failureReport := filepath.Join(root, ".forge", "heartbeat", "results", "binary-build-failure.md")
	if failInfo, err := os.Stat(failureReport); err == nil {
		failAge := time.Since(failInfo.ModTime())
		if failAge < 1*time.Hour {
			// Check if new source arrived after the failure
			// (newestSrc computation happens later, so do a quick check here)
			entries, _ := os.ReadDir(filepath.Join(root, "cmd", "forged"))
			var newestSrcQuick time.Time
			for _, e := range entries {
				if !e.IsDir() && strings.HasSuffix(e.Name(), ".go") {
					if info, err2 := e.Info(); err2 == nil && info.ModTime().After(newestSrcQuick) {
						newestSrcQuick = info.ModTime()
					}
				}
			}
			if newestSrcQuick.Before(failInfo.ModTime()) {
				return nil // No new source since failure — cooldown active
			}
			// New source arrived after failure — remove stale report and retry
			_ = os.Remove(failureReport)
		} else {
			// Cooldown expired — remove stale report
			_ = os.Remove(failureReport)
		}
	}

	binaryInfo, err := os.Stat(binaryPath)
	if err != nil {
		return nil // binary missing in test/CI context — skip
	}
	binaryMtime := binaryInfo.ModTime()

	// Find newest .go source file
	var newestSrc time.Time
	entries, err := os.ReadDir(srcDir)
	if err != nil {
		return fmt.Errorf("read src dir: %w", err)
	}
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".go") {
			continue
		}
		if info, err2 := e.Info(); err2 == nil && info.ModTime().After(newestSrc) {
			newestSrc = info.ModTime()
		}
	}

	// No source change detected — nothing to do.
	if newestSrc.IsZero() || !newestSrc.After(binaryMtime.Add(2*time.Minute)) {
		return nil
	}

	staleness := newestSrc.Sub(binaryMtime).Round(time.Second)
	log.Printf("[Patrol:binary-freshness] source newer than binary by %s — rebuilding", staleness)

	// Build new binary inside cmd/forged/. Use a context with generous timeout
	// so slow machines don't abort mid-compile.
	buildCtx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()

	// Build to a temp file first — never overwrite the running binary directly.
	// This prevents the daemon from being killed mid-build by an overwritten executable.
	tmpBinary := binaryPath + ".new"
	buildCmd := exec.CommandContext(buildCtx, "go", "build", "-o", tmpBinary, ".")
	buildCmd.Dir = srcDir
	if out, buildErr := buildCmd.CombinedOutput(); buildErr != nil {
		log.Printf("[Patrol:binary-freshness] build FAILED: %v\n%s", buildErr, out)
		_ = os.Remove(tmpBinary) // clean up failed build artifact
		msg := fmt.Sprintf("# Build Failure Alert\n\nbuild failed after detecting stale binary (+%s):\n```\n%s\n```\n", staleness, out)
		resultPath := filepath.Join(root, ".forge", "heartbeat", "results", "binary-build-failure.md")
		_ = os.WriteFile(resultPath, []byte(msg), 0644)
		return nil // build failure is logged; don't fail the patrol
	}

	// Atomic rename: replace running binary only after successful build.
	if err := os.Rename(tmpBinary, binaryPath); err != nil {
		log.Printf("[Patrol:binary-freshness] rename %s → %s failed: %v", tmpBinary, binaryPath, err)
		_ = os.Remove(tmpBinary)
		return nil
	}

	log.Printf("[Patrol:binary-freshness] build succeeded — triggering daemon restart via forge CLI")

	// Delegate the actual stop+restart to `forge daemon restart --skip-build`
	// (binary is already fresh). Run fire-and-forget in a goroutine so this
	// patrol function returns before the process is replaced.
	go func() {
		time.Sleep(500 * time.Millisecond) // let patrol goroutine clean up
		restartCmd := exec.Command("forge", "daemon", "restart", "--skip-build")
		restartCmd.Dir = root
		if out, restartErr := restartCmd.CombinedOutput(); restartErr != nil {
			log.Printf("[Patrol:binary-freshness] restart failed: %v\n%s", restartErr, out)
		}
	}()

	return nil
}

// agentLivenessPatrol detects "zombie" agents: agents that have a heartbeat row
// in agent_heartbeats but whose last_seen is older than 3 minutes (stale).
// Per kimi council wildcard (ADR-035): prevents task loss from silent agents.
//
// Phase 2 P1 action: mark zombie status in agent_heartbeats + log.
// Phase 2 P2 (future): auto-deflate and respawn via deflation compound gate.
func agentLivenessPatrol(ctx context.Context, db *sql.DB) error {
	threeMinutesAgo := time.Now().Add(-3 * time.Minute).UTC().Format(time.RFC3339)

	// Find agents that are non-offline but haven't sent a heartbeat in >3min.
	rows, err := db.QueryContext(ctx, `
		SELECT agent_id, node, status, last_seen, current_task_id
		FROM agent_heartbeats
		WHERE last_seen < ?
		AND status != 'offline'
		AND status != 'zombie'
	`, threeMinutesAgo)
	if err != nil {
		return fmt.Errorf("agentLiveness query: %w", err)
	}
	defer rows.Close()

	type zombieCandidate struct {
		agentID       string
		node          string
		status        string
		lastSeen      string
		currentTaskID sql.NullString
	}
	var zombies []zombieCandidate
	for rows.Next() {
		var z zombieCandidate
		if err := rows.Scan(&z.agentID, &z.node, &z.status, &z.lastSeen, &z.currentTaskID); err != nil {
			continue
		}
		zombies = append(zombies, z)
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("agentLiveness iterate: %w", err)
	}

	if len(zombies) == 0 {
		return nil
	}

	for _, z := range zombies {
		age := "unknown"
		if t, err := time.Parse(time.RFC3339, z.lastSeen); err == nil {
			age = time.Since(t).Round(time.Second).String()
		}

		taskNote := ""
		if z.currentTaskID.Valid && z.currentTaskID.String != "" {
			taskNote = fmt.Sprintf(" (assigned task: %s — may be lost)", z.currentTaskID.String)
		}

		log.Printf("[Patrol:agent-liveness] ZOMBIE detected: agent=%s node=%s last_seen=%s ago%s",
			z.agentID, z.node, age, taskNote)

		// Mark as zombie so subsequent heartbeat patrol doesn't re-flag,
		// and so forge fleet health can show zombie count.
		_, err := db.ExecContext(ctx, `
			UPDATE agent_heartbeats
			SET status = 'zombie'
			WHERE agent_id = ?
		`, z.agentID)
		if err != nil {
			log.Printf("[Patrol:agent-liveness] failed to mark zombie %s: %v", z.agentID, err)
		}
	}

	log.Printf("[Patrol:agent-liveness] marked %d zombie agent(s)", len(zombies))
	return nil
}

// councilCleanupPatrol removes expired council manual-spawn markers from
// .forge/autoscale/council/ and emits a deflate recommendation for each
// expired council agent so the deflation patrol can clean them up.
// Runs every 2 minutes per ADR-035 §Council lifecycle.
func councilCleanupPatrol(ctx context.Context, db *sql.DB) error {
	markerDir := ".forge/autoscale/council"
	entries, err := os.ReadDir(markerDir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil // no council sessions — nothing to do
		}
		return fmt.Errorf("councilCleanup read dir: %w", err)
	}

	type councilMarker struct {
		Agent     string `json:"agent"`
		Purpose   string `json:"purpose"`
		TTLMin    int    `json:"ttl_minutes"`
		StartedAt string `json:"started_at"`
		ExpiresAt string `json:"expires_at"`
	}

	now := time.Now().UTC()
	cleaned := 0

	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}

		markerPath := filepath.Join(markerDir, entry.Name())
		data, err := os.ReadFile(markerPath)
		if err != nil {
			log.Printf("[Patrol:council-cleanup] read marker %s: %v", entry.Name(), err)
			continue
		}

		var m councilMarker
		if err := json.Unmarshal(data, &m); err != nil {
			log.Printf("[Patrol:council-cleanup] parse marker %s: %v", entry.Name(), err)
			continue
		}

		// Check expiry.
		if m.ExpiresAt == "" {
			continue // no TTL — permanent marker, skip
		}
		expiresAt, err := time.Parse(time.RFC3339, m.ExpiresAt)
		if err != nil {
			log.Printf("[Patrol:council-cleanup] parse expires_at for %s: %v", m.Agent, err)
			continue
		}
		if now.Before(expiresAt) {
			remaining := expiresAt.Sub(now).Round(time.Second)
			log.Printf("[Patrol:council-cleanup] agent=%s council active, expires in %s", m.Agent, remaining)
			continue
		}

		// Marker expired — remove it and emit deflate recommendation.
		log.Printf("[Patrol:council-cleanup] council TTL expired for agent=%s — removing marker", m.Agent)
		if err := os.Remove(markerPath); err != nil {
			log.Printf("[Patrol:council-cleanup] remove marker %s: %v", markerPath, err)
			continue
		}

		// Emit a deflate recommendation so the operator can clean up the agent.
		// auto_execute=0: council agent cleanup still requires human approval.
		if db != nil {
			recID := fmt.Sprintf("cd-%s-%d", m.Agent, now.UnixNano())
			recExpiresAt := now.Add(30 * time.Minute).Format(time.RFC3339)
			reason := fmt.Sprintf("council TTL expired for agent=%s (was %s, ttl=%dmin)", m.Agent, m.StartedAt, m.TTLMin)
			_, dbErr := db.ExecContext(ctx, `
				INSERT OR IGNORE INTO scale_recommendations
					(id, node_id, action, agent_type, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
				VALUES (?, ?, 'deflate', ?, ?, 0, 0, 0, 'pending', datetime('now'), ?)
			`, recID, nodeIDFromEnv(), m.Agent, reason, recExpiresAt)
			if dbErr != nil {
				log.Printf("[Patrol:council-cleanup] insert deflate rec for %s: %v", m.Agent, dbErr)
			}
		}
		cleaned++
	}

	if cleaned > 0 {
		log.Printf("[Patrol:council-cleanup] cleaned %d expired council markers", cleaned)
	}
	return nil
}

// dispatchTimeoutPatrol scans .forge/dispatches/ for dispatch files older than 2h
// that have no corresponding result in .forge/heartbeat/results/.
// For each timed-out dispatch it writes a TIMEOUT marker file so the orchestrator
// can see which dispatches went unanswered.
func dispatchTimeoutPatrol(ctx context.Context, db *sql.DB) error {
	root := forgeRoot()
	dispatchDir := filepath.Join(root, ".forge", "dispatches")
	resultsDir := filepath.Join(root, ".forge", "heartbeat", "results")

	entries, err := os.ReadDir(dispatchDir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil // no dispatches yet
		}
		return fmt.Errorf("dispatch-timeout: readdir: %w", err)
	}

	timeout := 2 * time.Hour
	now := time.Now()
	timedOut := 0

	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".md" {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		// Only consider dispatches older than the timeout window.
		if now.Sub(info.ModTime()) < timeout {
			continue
		}

		// Derive expected result file stem: strip date suffix (last -YYYYMMDD or -YYYYMMDD...).
		// Pattern: AGENT-TASKID-DATE.md → AGENT-TASKID.md in results/
		stem := strings.TrimSuffix(entry.Name(), ".md")
		// Drop trailing date segment (8-digit date or full date-time like 20260307)
		parts := strings.Split(stem, "-")
		// Heuristic: if last part is 8 digits (YYYYMMDD), drop it.
		if len(parts) > 1 {
			last := parts[len(parts)-1]
			if len(last) == 8 {
				allDigits := true
				for _, c := range last {
					if c < '0' || c > '9' {
						allDigits = false
						break
					}
				}
				if allDigits {
					parts = parts[:len(parts)-1]
					stem = strings.Join(parts, "-")
				}
			}
		}

		resultFile := filepath.Join(resultsDir, stem+".md")
		if _, err := os.Stat(resultFile); err == nil {
			// Result exists — dispatch completed, skip.
			continue
		}

		// No result found — write a TIMEOUT marker.
		timeoutFile := filepath.Join(resultsDir, stem+"-TIMEOUT.md")
		if _, err := os.Stat(timeoutFile); err == nil {
			continue // already marked
		}

		content := fmt.Sprintf("# TIMEOUT: %s\n\nDispatch file: %s\nAge: %s\nMarked: %s\n\nNo result file found at: %s\n",
			entry.Name(),
			entry.Name(),
			now.Sub(info.ModTime()).Round(time.Minute),
			now.UTC().Format(time.RFC3339),
			resultFile,
		)
		if err := os.WriteFile(timeoutFile, []byte(content), 0644); err != nil {
			log.Printf("[Patrol:dispatch-timeout] failed to write timeout marker for %s: %v", entry.Name(), err)
			continue
		}
		log.Printf("[Patrol:dispatch-timeout] TIMEOUT: %s (age=%s)", entry.Name(), now.Sub(info.ModTime()).Round(time.Minute))
		timedOut++
	}

	if timedOut > 0 {
		log.Printf("[Patrol:dispatch-timeout] marked %d dispatches as TIMEOUT", timedOut)
	}
	return nil
}

// resultIngestPatrol detects new fleet agent result files written in the last 15 minutes
// and logs them so the orchestrator can review. (Task #49 — kimi-2 dispatch)
func resultIngestPatrol(ctx context.Context, db *sql.DB) error {
	resultsDir := filepath.Join(os.Getenv("FORGE_ROOT"), ".forge/heartbeat/results")
	if resultsDir == "/.forge/heartbeat/results" {
		resultsDir = ".forge/heartbeat/results"
	}

	entries, err := os.ReadDir(resultsDir)
	if err != nil {
		return nil // dir not exists = no results, not an error
	}

	cutoff := time.Now().Add(-15 * time.Minute)
	newResults := 0

	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".md") {
			continue
		}
		if strings.Contains(e.Name(), "TIMEOUT") {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		if info.ModTime().After(cutoff) {
			newResults++
			log.Printf("[Patrol:result-ingest] NEW result: %s (modified %s)",
				e.Name(), info.ModTime().Format("15:04"))
		}
	}

	if newResults > 0 {
		log.Printf("[Patrol:result-ingest] %d new result file(s) detected — orchestrator should review", newResults)
	}

	return nil
}

// xnodeSyncPatrol is a merged patrol combining xnodeAckProcessorPatrol and
// xnodeRetrySerializerPatrol. Council S195 P1: consolidated 2026-04-05.
// Runs both sequentially; errors from each are logged but do not abort the other.
func xnodeSyncPatrol(ctx context.Context, db *sql.DB) error {
	if err := xnodeAckProcessorPatrol(ctx, db); err != nil {
		log.Printf("[xnode-sync] ack-processor error: %v", err)
	}
	if err := xnodeRetrySerializerPatrol(ctx, db); err != nil {
		log.Printf("[xnode-sync] retry-serializer error: %v", err)
	}
	return nil
}

// xnodeAckProcessorPatrol reads ack files from .forge/xnode/acks/ and updates
// xnode_outbox status from 'serialized' to 'acked'. Implements ADR-023 durable queue.
func xnodeAckProcessorPatrol(ctx context.Context, db *sql.DB) error {
	ackDir := XNodeAcksDir
	if ackDir == "" {
		return nil // not configured
	}

	entries, err := os.ReadDir(ackDir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil // no acks dir yet
		}
		return fmt.Errorf("xnode-ack-processor: read acks dir: %w", err)
	}

	var processed int
	for _, entry := range entries {
		if !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}

		ackPath := filepath.Join(ackDir, entry.Name())
		data, err := os.ReadFile(ackPath)
		if err != nil {
			log.Printf("[Patrol:xnode-ack] failed to read %s: %v", ackPath, err)
			continue
		}

		var ack struct {
			MessageID string `json:"message_id"`
			Status    string `json:"status"`
		}
		if err := json.Unmarshal(data, &ack); err != nil {
			log.Printf("[Patrol:xnode-ack] failed to parse %s: %v", ackPath, err)
			continue
		}

		if ack.Status != "acked" || ack.MessageID == "" {
			continue
		}

		// Update outbox status
		now := time.Now().UTC().Format(time.RFC3339)
		res, err := db.ExecContext(ctx, `
			UPDATE xnode_outbox
			SET status = 'acked', acked_at = ?
			WHERE id = ? AND status = 'serialized'
		`, now, ack.MessageID)
		if err != nil {
			log.Printf("[Patrol:xnode-ack] failed to update %s: %v", ack.MessageID, err)
			continue
		}

		if n, _ := res.RowsAffected(); n > 0 {
			processed++
			// Remove ack file after successful update
			os.Remove(ackPath)
		}
	}

	if processed > 0 {
		log.Printf("[Patrol:xnode-ack] processed %d acks", processed)
	}
	return nil
}

// xnodeRetrySerializerPatrol re-serializes messages that have been in 'serialized'
// state for more than 30 minutes without receiving an ack. Implements ADR-023 durable queue.
func xnodeRetrySerializerPatrol(ctx context.Context, db *sql.DB) error {
	// Find messages serialized > 30 min ago without ack
	cutoff := time.Now().Add(-30 * time.Minute).UTC().Format(time.RFC3339)

	rows, err := db.QueryContext(ctx, `
		SELECT id, target_node, message_type, payload, idempotency_key
		FROM xnode_outbox
		WHERE status = 'serialized' AND serialized_at < ?
	`, cutoff)
	if err != nil {
		return fmt.Errorf("xnode-retry-serializer: query: %w", err)
	}
	defer rows.Close()

	type msg struct {
		ID             string
		TargetNode     string
		MessageType    string
		Payload        string
		IdempotencyKey sql.NullString
	}
	var messages []msg

	for rows.Next() {
		var m msg
		if err := rows.Scan(&m.ID, &m.TargetNode, &m.MessageType, &m.Payload, &m.IdempotencyKey); err != nil {
			continue
		}
		messages = append(messages, m)
	}
	rows.Close()

	if len(messages) == 0 {
		return nil
	}

	// Group by target node
	byNode := make(map[string][]msg)
	for _, m := range messages {
		byNode[m.TargetNode] = append(byNode[m.TargetNode], m)
	}

	var retried int
	for targetNode, nodeMsgs := range byNode {
		outboxDir := XNodeOutboxDir
		if outboxDir == "" {
			continue
		}

		outboxPath := filepath.Join(outboxDir, targetNode+".jsonl")
		f, err := os.OpenFile(outboxPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
		if err != nil {
			log.Printf("[Patrol:xnode-retry] failed to open %s: %v", outboxPath, err)
			continue
		}

		for _, m := range nodeMsgs {
			message := map[string]interface{}{
				"v":          "1.0",
				"message_id": m.ID,
				"type":       m.MessageType,
				"source":     "retry",
				"target":     m.TargetNode,
				"ts":         time.Now().Format(time.RFC3339),
			}

			var payload map[string]interface{}
			if err := json.Unmarshal([]byte(m.Payload), &payload); err == nil {
				message["payload"] = payload
			}

			if m.IdempotencyKey.Valid {
				message["idempotency_key"] = m.IdempotencyKey.String
			}

			data, err := json.Marshal(message)
			if err != nil {
				continue
			}

			if _, err := f.Write(append(data, '\n')); err != nil {
				continue
			}

			// Update serialized_at timestamp
			db.ExecContext(ctx, `
				UPDATE xnode_outbox SET serialized_at = datetime('now') WHERE id = ?
			`, m.ID)

			retried++
		}
		f.Close()
	}

	if retried > 0 {
		log.Printf("[Patrol:xnode-retry] reserialized %d unacked messages", retried)
	}
	return nil
}

// ─── Consolidated patrol wrappers ────────────────────────────────────────────
// Each wrapper calls the original patrol functions in sequence.
// If the first call returns an error it is logged but execution continues so
// the remaining functions always run.

// agentHealthPatrol merges health-check + heartbeat-ttl + agent-liveness.
// Schedule: 2 min (matches former heartbeat-ttl cadence).
func agentHealthPatrol(ctx context.Context, db *sql.DB) error {
	if err := checkAgentHealth(ctx, db); err != nil {
		log.Printf("[Patrol:agent-health] checkAgentHealth: %v", err)
	}
	if err := markStaleHeartbeatsOffline(ctx, db); err != nil {
		log.Printf("[Patrol:agent-health] markStaleHeartbeatsOffline: %v", err)
	}
	return agentLivenessPatrol(ctx, db)
}

// resultMonitorPatrol merges result-monitor + result-ingest.
// Schedule: 2 min (matches former result-monitor cadence).
func resultMonitorPatrol(ctx context.Context, db *sql.DB) error {
	if err := monitorResultFiles(ctx, db); err != nil {
		log.Printf("[Patrol:result-monitor] monitorResultFiles: %v", err)
	}
	return resultIngestPatrol(ctx, db)
}

// fleetScalePatrol merges fleet-scale-recommend + fleet-deflate-recommend.
// Schedule: 2 min (matches former fleet-scale-recommend cadence).
func fleetScalePatrol(ctx context.Context, db *sql.DB) error {
	if err := fleetScaleRecommendPatrol(ctx, db); err != nil {
		log.Printf("[Patrol:fleet-scale] fleetScaleRecommendPatrol: %v", err)
	}
	return fleetDeflateRecommendPatrol(ctx, db)
}

// fleetAutoPatrol merges fleet-auto-exec + fleet-auto-deflate.
// Schedule: 2 min (unchanged — both ran at 2 min).
func fleetAutoPatrol(ctx context.Context, db *sql.DB) error {
	if err := fleetAutoExecutePatrol(ctx, db); err != nil {
		log.Printf("[Patrol:fleet-auto] fleetAutoExecutePatrol: %v", err)
	}
	return fleetAutoDeflatePatrol(ctx, db)
}

// fleetScalingPatrol merges fleet-scale and fleet-auto into one patrol.
// Runs the full cycle: recommend scaling, recommend deflation, execute scaling, execute deflation.
func fleetScalingPatrol(ctx context.Context, db *sql.DB) error {
	if err := fleetScaleRecommendPatrol(ctx, db); err != nil {
		log.Printf("[Patrol:fleet-scaling] fleetScaleRecommendPatrol: %v", err)
	}
	if err := fleetDeflateRecommendPatrol(ctx, db); err != nil {
		log.Printf("[Patrol:fleet-scaling] fleetDeflateRecommendPatrol: %v", err)
	}
	if err := fleetAutoExecutePatrol(ctx, db); err != nil {
		log.Printf("[Patrol:fleet-scaling] fleetAutoExecutePatrol: %v", err)
	}
	return fleetAutoDeflatePatrol(ctx, db)
}

// docDriftPatrol checks key documentation files for staleness (last modified
// older than 14 days). Stale files are reported in .forge/reports/doc-drift.md.
// Returns nil even when stale files are found — the report file is the artifact.
// Schedule: 6 hours (group: hygiene).
func docDriftPatrol(ctx context.Context, db *sql.DB) error {
	const staleThreshold = 14 * 24 * time.Hour

	root := forgeRoot()

	// Canonical set of documentation files to monitor for drift.
	keyDocs := []string{
		"docs/PROMPT.md",
		"CLAUDE.md",
		"AGENTS.md",
		"docs/PLAN.md",
		"config/portfolio/portfolio-state.yaml",
	}

	now := time.Now()
	type staleEntry struct {
		Path    string
		DaysOld int
		ModTime time.Time
	}

	var stale []staleEntry
	var missing []string

	for _, rel := range keyDocs {
		full := filepath.Join(root, rel)
		info, err := os.Stat(full)
		if err != nil {
			// File absent — record but do not abort.
			missing = append(missing, rel)
			continue
		}
		age := now.Sub(info.ModTime())
		if age > staleThreshold {
			stale = append(stale, staleEntry{
				Path:    rel,
				DaysOld: int(age.Hours() / 24),
				ModTime: info.ModTime(),
			})
		}
	}

	// Build report markdown.
	var sb strings.Builder
	sb.WriteString("# Doc Drift Report\n\n")
	sb.WriteString(fmt.Sprintf("Generated: %s\n\n", now.UTC().Format(time.RFC3339)))
	sb.WriteString(fmt.Sprintf("Threshold: %d days\n\n", int(staleThreshold.Hours()/24)))

	if len(stale) == 0 && len(missing) == 0 {
		sb.WriteString("All key documentation files are up to date.\n")
	} else {
		if len(stale) > 0 {
			sb.WriteString(fmt.Sprintf("## Stale Files (%d)\n\n", len(stale)))
			for _, e := range stale {
				sb.WriteString(fmt.Sprintf("- `%s` — %d days old (last modified: %s)\n",
					e.Path, e.DaysOld, e.ModTime.UTC().Format("2006-01-02")))
			}
			sb.WriteString("\n")
		}
		if len(missing) > 0 {
			sb.WriteString(fmt.Sprintf("## Missing Files (%d)\n\n", len(missing)))
			for _, p := range missing {
				sb.WriteString(fmt.Sprintf("- `%s`\n", p))
			}
			sb.WriteString("\n")
		}
	}

	// Write report to .forge/reports/doc-drift.md.
	reportDir := filepath.Join(root, ".forge", "reports")
	if err := os.MkdirAll(reportDir, 0755); err != nil {
		return fmt.Errorf("doc-drift mkdir: %w", err)
	}
	reportPath := filepath.Join(reportDir, "doc-drift.md")
	if err := os.WriteFile(reportPath, []byte(sb.String()), 0644); err != nil {
		return fmt.Errorf("doc-drift write: %w", err)
	}

	staleCount := len(stale)
	if staleCount > 0 || len(missing) > 0 {
		log.Printf("[Patrol:doc-drift] stale=%d missing=%d — report: %s", staleCount, len(missing), reportPath)
	} else {
		log.Printf("[Patrol:doc-drift] all %d key docs up to date", len(keyDocs))
	}

	return nil
}

// dispatchHygienePatrol archives .md files older than 7 days from the
// .forge/dispatches/ and .forge/heartbeat/results/ directories. Archived files
// are moved into an archive/ subdirectory within each directory. Missing
// directories are not treated as errors — the patrol is a best-effort hygiene
// task. Schedule: 24 hours.
func dispatchHygienePatrol(ctx context.Context, db *sql.DB) error {
	const staleAge = 7 * 24 * time.Hour

	root := forgeRoot()
	now := time.Now()

	archiveDir := func(dir string) (int, error) {
		archivePath := filepath.Join(dir, "archive")

		entries, err := os.ReadDir(dir)
		if err != nil {
			if os.IsNotExist(err) {
				return 0, nil
			}
			return 0, fmt.Errorf("read dir %s: %w", dir, err)
		}

		var archived int
		for _, entry := range entries {
			if entry.IsDir() {
				continue
			}
			if filepath.Ext(entry.Name()) != ".md" {
				continue
			}
			info, err := entry.Info()
			if err != nil {
				log.Printf("[Patrol:dispatch-hygiene] stat %s: %v", entry.Name(), err)
				continue
			}
			if now.Sub(info.ModTime()) < staleAge {
				continue
			}
			// Ensure archive directory exists (lazy creation on first match).
			if err := os.MkdirAll(archivePath, 0755); err != nil {
				return archived, fmt.Errorf("mkdir archive %s: %w", archivePath, err)
			}
			src := filepath.Join(dir, entry.Name())
			dst := filepath.Join(archivePath, entry.Name())
			if err := os.Rename(src, dst); err != nil {
				log.Printf("[Patrol:dispatch-hygiene] move %s -> %s: %v", src, dst, err)
				continue
			}
			archived++
		}
		return archived, nil
	}

	dispatchDir := filepath.Join(root, ".forge", "dispatches")
	resultsDir := filepath.Join(root, ".forge", "heartbeat", "results")

	dispatchArchived, err := archiveDir(dispatchDir)
	if err != nil {
		return fmt.Errorf("dispatch-hygiene dispatches: %w", err)
	}

	resultsArchived, err := archiveDir(resultsDir)
	if err != nil {
		return fmt.Errorf("dispatch-hygiene results: %w", err)
	}

	log.Printf("[Patrol:dispatch-hygiene] archived=%d dispatches, %d results", dispatchArchived, resultsArchived)
	return nil
}

// researchExpiryPatrol scans .forge/heartbeat/results/ for research reports
// older than 3 days that haven't been reflected in portfolio-state.yaml.
// Writes a warning report to .forge/reports/research-expiry.md.
// Council S159 P0: prevents the 30-min stale-state reconciliation gap.
// Schedule: 6 hours (group: hygiene).
func researchExpiryPatrol(ctx context.Context, db *sql.DB) error {
	const expiryThreshold = 3 * 24 * time.Hour

	root := forgeRoot()
	resultsDir := filepath.Join(root, ".forge", "heartbeat", "results")
	portfolioPath := filepath.Join(root, "config", "portfolio", "portfolio-state.yaml")

	// Read portfolio-state.yaml modification time as a proxy for "last reconciled."
	portfolioInfo, err := os.Stat(portfolioPath)
	if err != nil {
		// No portfolio file — nothing to reconcile against.
		log.Printf("[Patrol:research-expiry] portfolio-state.yaml not found, skipping")
		return nil
	}
	portfolioModTime := portfolioInfo.ModTime()

	// Scan results directory for *research* files.
	entries, err := os.ReadDir(resultsDir)
	if err != nil {
		// Directory missing is not an error — just no results yet.
		log.Printf("[Patrol:research-expiry] results dir not found, skipping")
		return nil
	}

	now := time.Now()
	type staleReport struct {
		Name    string
		DaysOld int
		ModTime time.Time
	}
	var stale []staleReport

	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		name := e.Name()
		// Only check files matching *research* pattern.
		if !strings.Contains(name, "research") {
			continue
		}
		if !strings.HasSuffix(name, ".md") {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		age := now.Sub(info.ModTime())
		// Report is stale if: older than threshold AND newer than last portfolio update.
		// (If portfolio was updated AFTER the report, it's been reconciled.)
		if age > expiryThreshold && info.ModTime().After(portfolioModTime) {
			stale = append(stale, staleReport{
				Name:    name,
				DaysOld: int(age.Hours() / 24),
				ModTime: info.ModTime(),
			})
		}
	}

	// Build report.
	var sb strings.Builder
	sb.WriteString("# Research Expiry Report\n\n")
	sb.WriteString(fmt.Sprintf("Generated: %s\n\n", now.UTC().Format(time.RFC3339)))
	sb.WriteString(fmt.Sprintf("Expiry threshold: %d days\n", int(expiryThreshold.Hours()/24)))
	sb.WriteString(fmt.Sprintf("Portfolio last updated: %s\n\n", portfolioModTime.UTC().Format("2006-01-02 15:04")))

	if len(stale) == 0 {
		sb.WriteString("No stale research reports found. All findings have been reconciled.\n")
	} else {
		sb.WriteString(fmt.Sprintf("## Stale Reports (%d)\n\n", len(stale)))
		sb.WriteString("These research reports are >3 days old and were created AFTER the last portfolio update.\n")
		sb.WriteString("**Action required:** Run portfolio reconciliation or update portfolio-state.yaml.\n\n")
		for _, r := range stale {
			sb.WriteString(fmt.Sprintf("- `%s` — %d days old (created: %s)\n",
				r.Name, r.DaysOld, r.ModTime.UTC().Format("2006-01-02")))
		}
		sb.WriteString("\n")
	}

	// Write report.
	reportDir := filepath.Join(root, ".forge", "reports")
	if err := os.MkdirAll(reportDir, 0755); err != nil {
		return fmt.Errorf("research-expiry mkdir: %w", err)
	}
	reportPath := filepath.Join(reportDir, "research-expiry.md")
	if err := os.WriteFile(reportPath, []byte(sb.String()), 0644); err != nil {
		return fmt.Errorf("research-expiry write: %w", err)
	}

	if len(stale) > 0 {
		log.Printf("[Patrol:research-expiry] WARNING: %d stale research reports need reconciliation — %s", len(stale), reportPath)
	} else {
		log.Printf("[Patrol:research-expiry] all research reports reconciled")
	}

	return nil
}

// taskLifecycleCounter tracks invocations of taskLifecyclePatrol so that heavier
// sub-functions can run at their original effective frequencies rather than every minute.
var taskLifecycleCounter int

// taskLifecyclePatrol is the consolidated task lifecycle patrol (Council A2, 2026-03-28).
// It replaces four separate patrols — task-timeout, stale-task-ttl, dispatch-timeout,
// and phantom-task-cleanup — with a single 1-minute entry.  Each sub-function retains
// its own code path and runs at its original effective frequency:
//
//   - phantom-cleanup:   every invocation  → effective period: 1 min  (was 1 min)
//   - task-timeout:      every 5th tick    → effective period: 5 min  (was 5 min)
//   - stale-task-ttl:    every 5th tick    → effective period: 5 min  (was 5 min)
//   - dispatch-timeout:  every 15th tick   → effective period: 15 min (was 15 min)
//
// Errors from sub-functions are logged but do not abort the other sub-functions.
func taskLifecyclePatrol(ctx context.Context, db *sql.DB) error {
	taskLifecycleCounter++

	// Sub-function 1: Phantom task auto-complete (Council S157) — every invocation (1 min effective).
	if err := phantomTaskAutoCompletePatrol(ctx, db); err != nil {
		log.Printf("[task-lifecycle] phantom-cleanup error: %v", err)
	}

	// Sub-function 2: Task timeout — every 5th invocation (5 min effective).
	if taskLifecycleCounter%5 == 0 {
		if err := handleTimedOutTasks(ctx, db); err != nil {
			log.Printf("[task-lifecycle] task-timeout error: %v", err)
		}
	}

	// Sub-function 3: Stale task TTL — every 5th invocation (5 min effective).
	if taskLifecycleCounter%5 == 0 {
		if err := staleTaskTTLPatrol(ctx, db); err != nil {
			log.Printf("[task-lifecycle] stale-task-ttl error: %v", err)
		}
	}

	// Sub-function 4: Dispatch timeout (mark TIMEOUT after 2h) — every 15th invocation (15 min effective).
	if taskLifecycleCounter%15 == 0 {
		if err := dispatchTimeoutPatrol(ctx, db); err != nil {
			log.Printf("[task-lifecycle] dispatch-timeout error: %v", err)
		}
	}

	return nil
}

// harnessGapPatrol scans for tasks that exhausted retries (retry_count >= 3)
// and auto-creates coverage tasks so the failure mode gets a test case.
// Deduplicates by checking if a patrol:harness-gap task already references the original.
//
// Deprecated: removed from StandardPatrols() by Council A1 (unanimous, 2026-03-28).
// Kept for existing tests and potential future re-enablement.
func harnessGapPatrol(ctx context.Context, db *sql.DB) error {
	rows, err := db.QueryContext(ctx, `
		SELECT t.id, t.title, t.domain, t.project,
		       COALESCE(t.error, ''), COALESCE(t.failure_context, '')
		FROM tasks t
		WHERE t.state = 'FAILED' AND t.retry_count >= 3
		AND t.updated_at > datetime('now', '-7 days')
		AND NOT EXISTS (
			SELECT 1 FROM tasks t2
			WHERE t2.origin = 'patrol:harness-gap'
			AND instr(t2.description, t.id) > 0
		)
		LIMIT 10
	`)
	if err != nil {
		return fmt.Errorf("harness-gap query: %w", err)
	}
	defer rows.Close()

	created := 0
	for rows.Next() {
		var origID, title, domain, project, taskError, failureCtx string
		if err := rows.Scan(&origID, &title, &domain, &project, &taskError, &failureCtx); err != nil {
			continue
		}
		if domain == "" {
			domain = "forge"
		}
		if project == "" {
			project = "infrastructure"
		}

		taskID := "HG-" + generateULID()

		desc := fmt.Sprintf("## Harness Gap\n\nOriginal task `%s` failed %s.\n\n**Title:** %s\n\n**Error:** %s\n\n**Failure Context:** %s\n\n**Action:** Add a test case that covers this failure mode so it cannot regress silently.",
			origID, "3x (retries exhausted)", title, taskError, failureCtx)

		_, execErr := db.ExecContext(ctx, `
			INSERT INTO tasks (id, domain, project, type, priority, status, state,
			                   title, description, origin, created_at, updated_at)
			VALUES (?, ?, ?, 'bugfix', 7, 'queued', 'QUEUED',
			        ?, ?, 'patrol:harness-gap', datetime('now'), datetime('now'))
		`, taskID, domain, project,
			fmt.Sprintf("Coverage gap: %s", truncateStr(title, 60)),
			desc)
		if execErr != nil {
			log.Printf("[Patrol:harness-gap] insert error for %s: %v", origID, execErr)
			continue
		}
		log.Printf("[Patrol:harness-gap] Created coverage task %s for failed %s", taskID, origID)
		created++
	}
	if created > 0 {
		log.Printf("[Patrol:harness-gap] Created %d coverage tasks from exhausted failures", created)
	}
	return rows.Err()
}

// staleDispatchPatrol requeues tasks that have been stuck in DISPATCHED state for
// more than 10 minutes without an ACK (Epic 4 — dispatch reliability).
//
// Logic:
//   - If dispatch_attempts >= max_retries: transition to FAILED (max retries exceeded).
//   - Else: reset to QUEUED for the next dispatch attempt, incrementing dispatch_attempts.
func staleDispatchPatrol(ctx context.Context, db *sql.DB) error {
	rows, err := db.QueryContext(ctx, `
		SELECT id, COALESCE(assigned_to, ''), dispatch_attempts, max_retries
		FROM tasks
		WHERE state = 'DISPATCHED'
		AND updated_at < datetime('now', '-10 minutes')
	`)
	if err != nil {
		return fmt.Errorf("stale-dispatch query: %w", err)
	}
	defer rows.Close()

	now := time.Now().UTC().Format(time.RFC3339)
	failed := 0
	requeued := 0

	for rows.Next() {
		var id, assignedTo string
		var attempts, maxRetries int
		if err := rows.Scan(&id, &assignedTo, &attempts, &maxRetries); err != nil {
			log.Printf("[Patrol:stale-dispatch] scan error: %v", err)
			continue
		}

		if attempts >= maxRetries {
			// Exhausted retries — mark FAILED permanently.
			_, execErr := db.ExecContext(ctx, `
				UPDATE tasks
				SET state = 'FAILED', status = 'failed',
				    error = 'dispatch max retries exceeded: agent did not ACK within 10 minutes',
				    assigned_to = NULL, updated_at = ?
				WHERE id = ? AND state = 'DISPATCHED'
			`, now, id)
			if execErr != nil {
				log.Printf("[Patrol:stale-dispatch] fail update error %s: %v", id, execErr)
				continue
			}
			log.Printf("[Patrol:stale-dispatch] FAILED task %s (attempts=%d >= max=%d, agent=%s)", id, attempts, maxRetries, assignedTo)
			failed++
		} else {
			// Reset to QUEUED for re-dispatch. dispatch_attempts already counted the failed
			// attempt during ClaimTask/ClaimTransition, so we just clear the state fields.
			_, execErr := db.ExecContext(ctx, `
				UPDATE tasks
				SET state = 'QUEUED', status = 'queued',
				    assigned_to = NULL, started_at = NULL, updated_at = ?
				WHERE id = ? AND state = 'DISPATCHED'
			`, now, id)
			if execErr != nil {
				log.Printf("[Patrol:stale-dispatch] requeue error %s: %v", id, execErr)
				continue
			}
			log.Printf("[Patrol:stale-dispatch] requeued task %s (attempt %d/%d, agent=%s did not ACK)", id, attempts, maxRetries, assignedTo)
			requeued++
		}
	}

	if failed+requeued > 0 {
		log.Printf("[Patrol:stale-dispatch] processed %d stale dispatches: %d requeued, %d failed", failed+requeued, requeued, failed)
	}
	return rows.Err()
}

// contextCompactionPatrol detects agents approaching their context window ceiling and
// marks them for handoff/compaction before they stall. This prevents the 3am dark-factory
// stall where agents hit ~75% context and can no longer accept new work.
//
// Thresholds:
//   - context_pct > 75: WARNING — set status to "wrapup" so orchestrator knows to handoff
//   - context_pct > 90: CRITICAL — set status to "wrapup" with a restart flag in the log
//
// The patrol is advisory only — it never terminates agents. It returns nil always.
func contextCompactionPatrol(ctx context.Context, db *sql.DB) error {
	// Ensure context_pct column exists in agent_heartbeats — add it if absent.
	// This is a safety net for nodes that skipped migration 010.
	var colExists int
	err := db.QueryRowContext(ctx, `
		SELECT COUNT(*) FROM pragma_table_info('agent_heartbeats') WHERE name='context_pct'
	`).Scan(&colExists)
	if err == nil && colExists == 0 {
		if _, alterErr := db.ExecContext(ctx, `ALTER TABLE agent_heartbeats ADD COLUMN context_pct REAL DEFAULT 0`); alterErr != nil {
			log.Printf("[context-compaction] could not add context_pct column: %v", alterErr)
		} else {
			log.Printf("[context-compaction] added missing context_pct column to agent_heartbeats")
		}
	}

	// Query all non-offline agents with meaningful context usage (> 0).
	rows, err := db.QueryContext(ctx, `
		SELECT agent_id, COALESCE(context_pct, 0), COALESCE(status, 'idle')
		FROM agent_heartbeats
		WHERE status != 'offline'
		AND COALESCE(context_pct, 0) > 0
	`)
	if err != nil {
		// Table may not exist yet on a fresh node — treat as no agents.
		log.Printf("[context-compaction] query error (skipping): %v", err)
		return nil
	}
	defer rows.Close()

	type agentCtx struct {
		id         string
		contextPct float64
		status     string
	}

	var warn, critical []agentCtx

	for rows.Next() {
		var a agentCtx
		if err := rows.Scan(&a.id, &a.contextPct, &a.status); err != nil {
			continue
		}
		if a.contextPct > 90 {
			critical = append(critical, a)
		} else if a.contextPct > 75 {
			warn = append(warn, a)
		}
	}
	if err := rows.Err(); err != nil {
		log.Printf("[context-compaction] rows error: %v", err)
	}

	now := time.Now().UTC().Format(time.RFC3339)

	for _, a := range warn {
		log.Printf("[context-compaction] WARNING agent=%s context=%.1f%% action=wrapup", a.id, a.contextPct)
		if a.status != "wrapup" {
			_, _ = db.ExecContext(ctx, `
				UPDATE agent_heartbeats SET status = 'wrapup', last_seen = ? WHERE agent_id = ?
			`, now, a.id)
		}
	}

	for _, a := range critical {
		log.Printf("[context-compaction] CRITICAL agent=%s context=%.1f%% action=restart-needed", a.id, a.contextPct)
		if a.status != "wrapup" {
			_, _ = db.ExecContext(ctx, `
				UPDATE agent_heartbeats SET status = 'wrapup', last_seen = ? WHERE agent_id = ?
			`, now, a.id)
		}
	}

	total := len(warn) + len(critical)
	if total > 0 {
		log.Printf("[context-compaction] processed %d agents: %d warn (>75%%), %d critical (>90%%)", total, len(warn), len(critical))
	}

	return nil
}

// stateFreshnessGitTimestamp is the function used by stateFreshnessPatrol to
// retrieve the last-commit Unix timestamp for a file. It is a package-level
// variable so tests can replace it without spawning real git processes.
var stateFreshnessGitTimestamp = func(root, relPath string) (int64, error) {
	out, err := exec.Command(
		"git", "-C", root, "log", "-1", "--format=%ct", "--", relPath,
	).Output()
	if err != nil {
		return 0, fmt.Errorf("git log %s: %w", relPath, err)
	}
	ts := strings.TrimSpace(string(out))
	if ts == "" {
		return 0, nil // file not yet committed
	}
	var epoch int64
	if _, err := fmt.Sscanf(ts, "%d", &epoch); err != nil {
		return 0, fmt.Errorf("parse timestamp %q: %w", ts, err)
	}
	return epoch, nil
}

// stateFreshnessPatrol checks the last-commit date for key state files
// (docs/PROMPT-*.md, docs/PLAN.md, docs/BACKLOG.md) and reports files that
// have not been updated recently. Warn threshold: 3 days. Critical (auto-
// archive proposal) threshold: 14 days. Report: .forge/reports/state-freshness.md.
// Council S194. Schedule: 24 hours.
func stateFreshnessPatrol(ctx context.Context, db *sql.DB) error {
	const warnDays = 3
	const criticalDays = 14

	root := forgeRoot()

	// Resolve docs/PROMPT-*.md glob, plus fixed files.
	promptMatches, _ := filepath.Glob(filepath.Join(root, "docs", "PROMPT-*.md"))
	targets := make([]string, 0, len(promptMatches)+2)
	for _, abs := range promptMatches {
		rel, _ := filepath.Rel(root, abs)
		targets = append(targets, rel)
	}
	targets = append(targets, "docs/PLAN.md", "docs/BACKLOG.md")

	type fileStatus struct {
		Path      string
		DaysSince int
		Status    string // fresh / stale / critical / unknown
	}

	now := time.Now().Unix()
	var stale []fileStatus
	allStatuses := make([]fileStatus, 0, len(targets))

	for _, rel := range targets {
		epoch, err := stateFreshnessGitTimestamp(root, rel)
		if err != nil {
			log.Printf("[Patrol:state-freshness] git error for %s: %v", rel, err)
			continue
		}
		var days int
		status := "fresh"
		if epoch == 0 {
			days = -1
			status = "unknown" // never committed
		} else {
			days = int((now - epoch) / 86400)
			if days >= criticalDays {
				status = "critical"
			} else if days >= warnDays {
				status = "stale"
			}
		}
		fs := fileStatus{Path: rel, DaysSince: days, Status: status}
		allStatuses = append(allStatuses, fs)
		if status == "stale" || status == "critical" {
			stale = append(stale, fs)
		}
	}

	// Build report markdown.
	var sb strings.Builder
	sb.WriteString("# State Freshness Report\n\n")
	sb.WriteString(fmt.Sprintf("Generated: %s\n\n", time.Now().UTC().Format(time.RFC3339)))
	sb.WriteString(fmt.Sprintf("Warn threshold: %d days | Critical threshold: %d days\n\n", warnDays, criticalDays))

	sb.WriteString("## File Status\n\n")
	sb.WriteString("| File | Days Since Commit | Status |\n")
	sb.WriteString("|------|------------------|--------|\n")
	for _, fs := range allStatuses {
		daysStr := fmt.Sprintf("%d", fs.DaysSince)
		if fs.DaysSince < 0 {
			daysStr = "never committed"
		}
		sb.WriteString(fmt.Sprintf("| `%s` | %s | %s |\n", fs.Path, daysStr, fs.Status))
	}
	sb.WriteString("\n")

	if len(stale) > 0 {
		sb.WriteString(fmt.Sprintf("## Action Required (%d stale)\n\n", len(stale)))
		for _, fs := range stale {
			action := "update this file"
			if fs.Status == "critical" {
				action = "auto-archive proposal: file has not been updated in 14+ days"
			}
			sb.WriteString(fmt.Sprintf("- `%s` (%d days) — %s\n", fs.Path, fs.DaysSince, action))
		}
		sb.WriteString("\n")
	} else {
		sb.WriteString("All state files are up to date.\n")
	}

	reportDir := filepath.Join(root, ".forge", "reports")
	if err := os.MkdirAll(reportDir, 0755); err != nil {
		return fmt.Errorf("state-freshness mkdir: %w", err)
	}
	reportPath := filepath.Join(reportDir, "state-freshness.md")
	if err := os.WriteFile(reportPath, []byte(sb.String()), 0644); err != nil {
		return fmt.Errorf("state-freshness write: %w", err)
	}

	if len(stale) > 0 {
		log.Printf("[Patrol:state-freshness] WARNING: %d stale state files — report: %s", len(stale), reportPath)
	} else {
		log.Printf("[Patrol:state-freshness] all %d state files are fresh", len(allStatuses))
	}

	return nil
}

// patternsExtractionPatrol scans recent heartbeat results and counts entries in
// docs/PATTERNS.md and docs/COMMON_MISTAKES.md to surface extraction opportunities.
// Report: .forge/reports/patterns-extraction.md. Schedule: 24 hours (flywheel).
func patternsExtractionPatrol(ctx context.Context, db *sql.DB) error {
	root := forgeRoot()
	now := time.Now()
	cutoff := now.Add(-48 * time.Hour)

	// Count .md result files modified in last 48h.
	resultsDir := filepath.Join(root, ".forge", "heartbeat", "results")
	var resultCount int
	if entries, err := os.ReadDir(resultsDir); err == nil {
		for _, e := range entries {
			if e.IsDir() || !strings.HasSuffix(e.Name(), ".md") {
				continue
			}
			info, err := e.Info()
			if err == nil && info.ModTime().After(cutoff) {
				resultCount++
			}
		}
	}

	// Count ## headings in docs/PATTERNS.md.
	patternCount := countMarkdownH2Headings(filepath.Join(root, "docs", "PATTERNS.md"))
	mistakeCount := countMarkdownH2Headings(filepath.Join(root, "docs", "COMMON_MISTAKES.md"))

	// Build report.
	var sb strings.Builder
	sb.WriteString("# Patterns Extraction Report\n\n")
	sb.WriteString(fmt.Sprintf("Generated: %s\n\n", now.UTC().Format(time.RFC3339)))
	sb.WriteString(fmt.Sprintf("Recent result files (last 48h): **%d**\n\n", resultCount))
	sb.WriteString(fmt.Sprintf("Entries in PATTERNS.md: **%d**\n\n", patternCount))
	sb.WriteString(fmt.Sprintf("Entries in COMMON_MISTAKES.md: **%d**\n\n", mistakeCount))

	if resultCount > 0 {
		sb.WriteString("## Action\n\n")
		sb.WriteString(fmt.Sprintf("Review %d recent result files and extract new patterns or common mistakes.\n", resultCount))
		sb.WriteString("Dispatch to gemini/codex for analysis if the ratio of results to patterns is high.\n")
	} else {
		sb.WriteString("No recent result files to process.\n")
	}

	reportDir := filepath.Join(root, ".forge", "reports")
	if err := os.MkdirAll(reportDir, 0755); err != nil {
		return fmt.Errorf("patterns-extraction mkdir: %w", err)
	}
	reportPath := filepath.Join(reportDir, "patterns-extraction.md")
	if err := os.WriteFile(reportPath, []byte(sb.String()), 0644); err != nil {
		return fmt.Errorf("patterns-extraction write: %w", err)
	}

	log.Printf("[Patrol:patterns-extraction] results=%d patterns=%d mistakes=%d", resultCount, patternCount, mistakeCount)
	return nil
}

// countMarkdownH2Headings counts the number of "## " headings in a file.
// Returns 0 if the file cannot be read.
func countMarkdownH2Headings(path string) int {
	data, err := os.ReadFile(path)
	if err != nil {
		return 0
	}
	count := 0
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, "## ") {
			count++
		}
	}
	return count
}

// fileExists returns true if path exists and is a regular file.
func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

// dirExists returns true if path exists and is a directory.
func dirExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}
