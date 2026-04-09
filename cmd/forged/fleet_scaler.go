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
	"strconv"
	"strings"
	"sync"
	"time"
)

// NodeHardCeilings maps node IDs to their maximum concurrent agent count.
// Council-validated 2026-03-07 (ADR-035 §Phase2).
// prya=2: 16GB RAM, orchestrator + SQLite + CC backend co-located.
// sati=6: 64GB RAM, agent workhorse.
// nova=4: 48GB RAM, power sessions.
// vega/gaea=2: 16GB RAM, auxiliary only.
var NodeHardCeilings = map[string]int{
	"prya": 2,
	"sati": 6,
	"nova": 4,
	"vega": 2,
	"gaea": 2,
}

// ForbiddenAgentTypes maps node IDs to agent types that must never spawn there.
// opencode (2.5GB) and kilo (1.4GB) cause OOM on 16GB nodes.
// claude/codex/cursor/amp are T1 premium agents — manual-only, never auto-spawned.
var ForbiddenAgentTypes = map[string]map[string]bool{
	"prya": {"opencode": true, "kilo": true, "claude": true, "codex": true, "cursor": true, "amp": true},
	"vega": {"opencode": true, "kilo": true, "claude": true, "codex": true, "cursor": true, "amp": true},
	"gaea": {"opencode": true, "kilo": true, "claude": true, "codex": true, "cursor": true, "amp": true},
}

// NoAutoSpawnNodes lists nodes where the fleet-auto-exec patrol is completely disabled.
// On resource-constrained nodes (16GB RAM), all agent spawning must be manual.
// fleet-scale-recommend may still create recommendations (for review), but nothing executes.
var NoAutoSpawnNodes = map[string]bool{
	"prya": true,
	"vega": true,
	"gaea": true,
}

// lightweightAgentTypes is the set of agent types that may be auto-approved
// (auto_execute=1) when all 7 inflate gates pass.
// Medium-tier (claude/cursor/amp) is auto-approved on sati only (64GB).
// Heavy-tier (opencode/kilo) is always manual.
var lightweightAgentTypes = map[string]bool{
	"kimi":    true,
	"minimax": true,
	"pi":      true,
	"gemini":  true,
	"glm":     true,
}

var mediumTierAutoApproveNodes = map[string]bool{
	"sati": true,
}

// spawnMu serialises the ceiling-check → spawn critical section so that two
// concurrent patrol invocations cannot both pass the ceiling gate and both
// spawn an agent before either has updated the tmux window list.
var spawnMu sync.Mutex

// agentTier returns "lightweight", "medium", or "heavy" for a given agent type.
func agentTier(agentType string) string {
	if lightweightAgentTypes[agentType] {
		return "lightweight"
	}
	switch agentType {
	case "claude", "cursor", "amp":
		return "medium"
	case "opencode", "kilo":
		return "heavy"
	}
	return "medium" // unknown defaults to medium (conservative)
}

// requiresHumanApproval returns true when a spawn needs manual approval.
// Implements the ADR-035 node-differentiated policy (kimi council Q8).
func requiresHumanApproval(agentType, nodeID string) bool {
	switch agentTier(agentType) {
	case "lightweight":
		return false // auto-approve everywhere
	case "medium":
		return !mediumTierAutoApproveNodes[nodeID] // auto on sati, manual elsewhere
	default: // heavy
		return true // always manual
	}
}

// isAgentForbiddenOnNode returns true if the agent type must not spawn on nodeID.
func isAgentForbiddenOnNode(agentType, nodeID string) bool {
	forbidden, ok := ForbiddenAgentTypes[nodeID]
	if !ok {
		return false
	}
	return forbidden[agentType]
}

// ScaleRecommendation is a recommendation to inflate or deflate the agent fleet
// on a given node. Phase 1 only generates recommendations — no autonomous spawning.
type ScaleRecommendation struct {
	ID          string `json:"id"`
	NodeID      string `json:"node_id"`
	Action      string `json:"action"`      // "inflate" or "deflate"
	AgentType   string `json:"agent_type"`
	Reason      string `json:"reason"`
	QueueDepth  int    `json:"queue_depth"`
	IdleAgents  int    `json:"idle_agents"`
	AutoExecute int    `json:"auto_execute"` // 1 for lightweight tier, 0 for medium/heavy (ADR-036)
	Status      string `json:"status"`       // "pending", "applied", "expired"
	CreatedAt   string `json:"created_at"`
	ExpiresAt   string `json:"expires_at"`
}

// ensureScaleRecommendationsTable creates the scale_recommendations table if it
// does not already exist. Called at the top of fleetScaleRecommendPatrol so the
// table is available even when migration 037 has not been applied yet.
func ensureScaleRecommendationsTable(ctx context.Context, db *sql.DB) error {
	_, err := db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS scale_recommendations (
			id TEXT PRIMARY KEY,
			node_id TEXT NOT NULL,
			action TEXT NOT NULL,
			agent_type TEXT NOT NULL,
			reason TEXT NOT NULL,
			queue_depth INTEGER NOT NULL DEFAULT 0,
			idle_agents INTEGER NOT NULL DEFAULT 0,
			auto_execute INTEGER NOT NULL DEFAULT 0,
			status TEXT NOT NULL DEFAULT 'pending',
			created_at TEXT NOT NULL DEFAULT (datetime('now')),
			expires_at TEXT NOT NULL
		)
	`)
	if err != nil {
		return fmt.Errorf("ensure scale_recommendations table: %w", err)
	}
	return nil
}

// fleetScaleRecommendPatrol is the ADR-034 Phase 1 patrol.
// It queries queue depth and idle agent count, then inserts a scale
// recommendation row whenever queued_count > idle_count*2.
// auto_execute=1 for lightweight tier (requiresHumanApproval returns false).
// fleetAutoExecutePatrol picks up these recommendations and spawns agents with circuit breakers.
func fleetScaleRecommendPatrol(ctx context.Context, db *sql.DB) error {
	// 0. Skip recommendation on no-auto-spawn nodes: nothing will execute them anyway,
	// and stale recommendations cause spurious "HARD RULE VIOLATION" logs every 2 min.
	localNodeCheck, _ := os.Hostname()
	if NoAutoSpawnNodes[localNodeCheck] {
		return nil
	}

	// 1. Create table if needed.
	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		return err
	}

	// 2. Expire pending recommendations whose expiry has passed.
	now := time.Now().UTC().Format(time.RFC3339)
	if _, err := db.ExecContext(ctx, `
		UPDATE scale_recommendations
		SET status = 'expired'
		WHERE status = 'pending' AND expires_at < ?
	`, now); err != nil {
		return fmt.Errorf("expire stale recommendations: %w", err)
	}

	// 3. Count queued tasks.
	var queuedCount int
	if err := db.QueryRowContext(ctx, `
		SELECT COUNT(*) FROM tasks
		WHERE status IN ('queued', 'requested')
	`).Scan(&queuedCount); err != nil {
		return fmt.Errorf("count queued tasks: %w", err)
	}

	// 4. Count idle agents per node from agent_inventory.
	//    agent_inventory may not exist yet (table created by migration 037).
	//    If the table is absent we skip gracefully.
	type nodeIdle struct {
		nodeID     string
		idleAgents int
	}
	rows, err := db.QueryContext(ctx, `
		SELECT node_id, COUNT(*) AS idle_count
		FROM agent_inventory
		WHERE status = 'idle'
		GROUP BY node_id
	`)
	if err != nil {
		// Table likely doesn't exist yet — log and exit cleanly.
		log.Printf("[Patrol:fleet-scale-recommend] agent_inventory not ready: %v", err)
		return nil
	}
	defer rows.Close()

	var nodes []nodeIdle
	for rows.Next() {
		var n nodeIdle
		if err := rows.Scan(&n.nodeID, &n.idleAgents); err != nil {
			log.Printf("[Patrol:fleet-scale-recommend] scan error: %v", err)
			continue
		}
		nodes = append(nodes, n)
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("iterate agent_inventory rows: %w", err)
	}

	// If no nodes are registered at all, use the local node so ceiling checks work.
	if len(nodes) == 0 {
		localHostname, _ := os.Hostname()
		if localHostname == "" {
			localHostname = os.Getenv("NODE_ID")
		}
		if localHostname == "" {
			// No way to identify node — skip recommendation to avoid runaway spawning.
			log.Printf("[Patrol:fleet-scale-recommend] no nodes registered and NODE_ID unset — skipping")
			return nil
		}
		nodes = []nodeIdle{{nodeID: localHostname, idleAgents: 0}}
	}

	// 5. Insert recommendations where queue depth exceeds threshold.
	recommended := 0
	expiresAt := time.Now().UTC().Add(10 * time.Minute).Format(time.RFC3339)

	// Default recommendation agent: pick cheapest available per node.
	// Prefer lightweight (kimi) so auto-approve fires where possible.
	const defaultAgentType = "kimi"

	for _, n := range nodes {
		if queuedCount <= n.idleAgents*2 {
			continue
		}

		// Hard ceiling gate: count total agents on this node.
		var totalAgents int
		if err := db.QueryRowContext(ctx, `
			SELECT COUNT(*) FROM agent_inventory
			WHERE node_id = ? AND status != 'offline'
		`, n.nodeID).Scan(&totalAgents); err != nil {
			log.Printf("[Patrol:fleet-scale-recommend] ceiling check error (node=%s): %v", n.nodeID, err)
			totalAgents = 0
		}
		ceiling, ok := NodeHardCeilings[n.nodeID]
		if !ok {
			ceiling = 2 // conservative default for unknown nodes
		}
		if totalAgents >= ceiling {
			log.Printf("[Patrol:fleet-scale-recommend] node=%s at ceiling %d/%d — skip", n.nodeID, totalAgents, ceiling)
			continue
		}

		// Forbidden agent type gate.
		if isAgentForbiddenOnNode(defaultAgentType, n.nodeID) {
			log.Printf("[Patrol:fleet-scale-recommend] agent %s forbidden on node=%s — skip", defaultAgentType, n.nodeID)
			continue
		}

		// Race guard: only insert if no other pending inflate rec exists for this node+type.
		// Prevents two recommend patrol cycles from creating two recs before execute runs,
		// which would cause both to be executed and violate the node ceiling.
		var existingPending int
		if err := db.QueryRowContext(ctx, `
			SELECT COUNT(*) FROM scale_recommendations
			WHERE node_id = ? AND agent_type = ? AND action = 'inflate' AND status = 'pending'
		`, n.nodeID, defaultAgentType).Scan(&existingPending); err != nil {
			log.Printf("[Patrol:fleet-scale-recommend] pending check error (node=%s): %v", n.nodeID, err)
			continue
		}
		if existingPending > 0 {
			log.Printf("[Patrol:fleet-scale-recommend] node=%s already has pending inflate rec for %s — skip", n.nodeID, defaultAgentType)
			continue
		}

		// Determine auto-execute based on node-differentiated approval policy.
		autoExecute := 0
		if !requiresHumanApproval(defaultAgentType, n.nodeID) {
			autoExecute = 1
		}

		reason := fmt.Sprintf("queue_depth %d > idle_agents %d * 2 (ceiling %d/%d)", queuedCount, n.idleAgents, totalAgents, ceiling)
		id := fmt.Sprintf("sr-%s-%d", n.nodeID, time.Now().UnixNano())

		_, err := db.ExecContext(ctx, `
			INSERT INTO scale_recommendations
				(id, node_id, action, agent_type, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
			VALUES
				(?, ?, 'inflate', ?, ?, ?, ?, ?, 'pending', datetime('now'), ?)
		`, id, n.nodeID, defaultAgentType, reason, queuedCount, n.idleAgents, autoExecute, expiresAt)
		if err != nil {
			log.Printf("[Patrol:fleet-scale-recommend] insert recommendation error (node=%s): %v", n.nodeID, err)
			continue
		}

		log.Printf("[Patrol:fleet-scale-recommend] RECOMMEND inflate node=%s agent=%s auto_execute=%d — %s",
			n.nodeID, defaultAgentType, autoExecute, reason)
		recommended++
	}

	if recommended > 0 {
		log.Printf("[Patrol:fleet-scale-recommend] emitted %d scale recommendation(s)", recommended)
	}

	return nil
}

// councilMarkerPath returns the path of a manual-spawn marker for an agent.
// Council agents have markers at .forge/autoscale/council/{agentID}.json.
// If the marker exists and has not expired, the agent is protected from deflation.
func councilMarkerPath(agentID string) string {
	return fmt.Sprintf(".forge/autoscale/council/%s.json", agentID)
}

// hasActiveCouncilMarker returns true if a manual-spawn marker exists for agentID
// and its TTL has not expired.
func hasActiveCouncilMarker(agentID string) bool {
	data, err := os.ReadFile(councilMarkerPath(agentID))
	if err != nil {
		return false // no marker — not protected
	}
	var m struct {
		ExpiresAt string `json:"expires_at"`
	}
	if err := json.Unmarshal(data, &m); err != nil {
		return true // malformed but exists — be conservative, protect agent
	}
	if m.ExpiresAt == "" {
		return true // no expiry — permanent marker
	}
	t, err := time.Parse(time.RFC3339, m.ExpiresAt)
	if err != nil {
		return true
	}
	return time.Now().Before(t)
}

// deflationGateCheck evaluates the 4-signal compound gate for an agent candidate.
// All 4 signals must be true before a deflation recommendation is emitted:
//  1. context_pct < 10%  (agent context not in use)
//  2. idle_duration >= idleThreshold (agent has been idle long enough)
//  3. no assigned task (no task with status=assigned/executing pointing to this agent)
//  4. queue is not growing (queued count ≤ idle_agents*2 — same inflate threshold)
//  5. no active council marker (agent is not protected by manual-spawn)
//
// This is an evaluation-only gate; it does NOT kill agents. The deflate
// recommendation (auto_execute=0) must be approved via `forge approval decide`.
func deflationGateCheck(ctx context.Context, db *sql.DB, agentID, nodeID string, idleThreshold time.Duration) (bool, string, error) {
	// Signal 1: context_pct < 10%.
	var contextPct float64
	err := db.QueryRowContext(ctx, `
		SELECT COALESCE(context_pct, 0)
		FROM agent_heartbeats
		WHERE agent_id = ?
	`, agentID).Scan(&contextPct)
	if err != nil && err != sql.ErrNoRows {
		return false, "", fmt.Errorf("deflate gate ctx query: %w", err)
	}
	if contextPct >= 10.0 {
		return false, fmt.Sprintf("context_pct %.0f%% >= 10%%", contextPct), nil
	}

	// Signal 2: idle_duration >= threshold.
	var lastSeen string
	err = db.QueryRowContext(ctx, `
		SELECT last_seen FROM agent_heartbeats WHERE agent_id = ?
	`, agentID).Scan(&lastSeen)
	if err != nil && err != sql.ErrNoRows {
		return false, "", fmt.Errorf("deflate gate last_seen query: %w", err)
	}
	if lastSeen != "" {
		if t, err := time.Parse(time.RFC3339, lastSeen); err == nil {
			idle := time.Since(t)
			if idle < idleThreshold {
				return false, fmt.Sprintf("idle only %s (need %s)", idle.Round(time.Second), idleThreshold), nil
			}
		}
	}

	// Signal 3: no assigned task.
	var assignedCount int
	if err := db.QueryRowContext(ctx, `
		SELECT COUNT(*) FROM tasks
		WHERE assigned_to = ?
		AND status IN ('assigned', 'executing')
	`, agentID).Scan(&assignedCount); err != nil && err != sql.ErrNoRows {
		return false, "", fmt.Errorf("deflate gate assigned query: %w", err)
	}
	if assignedCount > 0 {
		return false, fmt.Sprintf("agent has %d assigned task(s)", assignedCount), nil
	}

	// Signal 4: queue is not growing (don't deflate if work is arriving).
	var queuedCount, idleCount int
	_ = db.QueryRowContext(ctx, `SELECT COUNT(*) FROM tasks WHERE status IN ('queued','requested')`).Scan(&queuedCount)
	_ = db.QueryRowContext(ctx, `SELECT COUNT(*) FROM agent_inventory WHERE node_id = ? AND status = 'idle'`, nodeID).Scan(&idleCount)
	if queuedCount > idleCount*2 {
		return false, fmt.Sprintf("queue growing (%d queued, %d idle) — inflate needed instead", queuedCount, idleCount), nil
	}

	// Signal 5: no active council marker (agent protected from deflation).
	if hasActiveCouncilMarker(agentID) {
		return false, "active council marker — agent protected", nil
	}

	return true, "all 5 deflation signals green", nil
}

// fleetDeflateRecommendPatrol is the ADR-035 Phase 2 deflation patrol.
// Runs every 5 minutes, checks idle agents against the 4-signal compound gate,
// and emits deflate recommendations for agents that are safe to shut down.
// Min floor: never deflates the last agent on a node (keeps 1 lightweight alive).
func fleetDeflateRecommendPatrol(ctx context.Context, db *sql.DB) error {
	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		return err
	}

	// Expire stale pending recommendations.
	now := time.Now().UTC().Format(time.RFC3339)
	if _, err := db.ExecContext(ctx, `
		UPDATE scale_recommendations SET status = 'expired'
		WHERE action = 'deflate' AND status = 'pending' AND expires_at < ?
	`, now); err != nil {
		return fmt.Errorf("deflate: expire stale: %w", err)
	}

	// Vacuum old expired/applied rows older than 7 days.
	cutoff := time.Now().UTC().Add(-7 * 24 * time.Hour).Format(time.RFC3339)
	if _, err := db.ExecContext(ctx, `
		DELETE FROM scale_recommendations
		WHERE status IN ('expired', 'applied') AND expires_at < ?
	`, cutoff); err != nil {
		log.Printf("[Patrol:fleet-deflate] vacuum old recs: %v", err)
	}

	// Get all online agents grouped by node.
	rows, err := db.QueryContext(ctx, `
		SELECT agent_id, node, context_pct, last_seen
		FROM agent_heartbeats
		WHERE status NOT IN ('offline', 'zombie')
		ORDER BY node, last_seen ASC
	`)
	if err != nil {
		log.Printf("[Patrol:fleet-deflate] agent_heartbeats query error: %v", err)
		return nil
	}
	defer rows.Close()

	type agentRow struct {
		agentID    string
		node       string
		contextPct float64
		lastSeen   string
	}
	nodeAgents := make(map[string][]agentRow)
	for rows.Next() {
		var a agentRow
		if err := rows.Scan(&a.agentID, &a.node, &a.contextPct, &a.lastSeen); err != nil {
			continue
		}
		nodeAgents[a.node] = append(nodeAgents[a.node], a)
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("deflate: iterate agents: %w", err)
	}

	const idleThreshold = 15 * time.Minute
	expiresAt := time.Now().UTC().Add(10 * time.Minute).Format(time.RFC3339)
	recommended := 0

	for nodeID, agents := range nodeAgents {
		// Min floor: always keep at least 1 agent per node alive.
		if len(agents) <= 1 {
			continue
		}

		// Evaluate oldest-idle agents first (sorted by last_seen ASC).
		for _, a := range agents {
			ok, reason, err := deflationGateCheck(ctx, db, a.agentID, nodeID, idleThreshold)
			if err != nil {
				log.Printf("[Patrol:fleet-deflate] gate error agent=%s: %v", a.agentID, err)
				continue
			}
			if !ok {
				log.Printf("[Patrol:fleet-deflate] agent=%s SKIP: %s", a.agentID, reason)
				continue
			}

			id := fmt.Sprintf("sd-%s-%d", nodeID, time.Now().UnixNano())
			deflateReason := fmt.Sprintf("agent %s idle >%s: %s", a.agentID, idleThreshold, reason)

			_, err = db.ExecContext(ctx, `
				INSERT INTO scale_recommendations
					(id, node_id, action, agent_type, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
				VALUES (?, ?, 'deflate', ?, ?, 0, ?, 0, 'pending', datetime('now'), ?)
			`, id, nodeID, a.agentID, deflateReason, len(agents), expiresAt)
			if err != nil {
				log.Printf("[Patrol:fleet-deflate] insert error agent=%s: %v", a.agentID, err)
				continue
			}

			log.Printf("[Patrol:fleet-deflate] RECOMMEND deflate node=%s agent=%s — %s", nodeID, a.agentID, deflateReason)
			recommended++

			// Only recommend deflating one agent per node per cycle to avoid
			// over-deflating — let the next cycle re-evaluate.
			break
		}
	}

	if recommended > 0 {
		log.Printf("[Patrol:fleet-deflate] emitted %d deflation recommendation(s)", recommended)
	}
	return nil
}

// ============================================================================
// ADR-036 Phase 3: Autonomous Fleet Execution
// ============================================================================

// agentSpawnCommands maps agent types to their CLI spawn commands.
// Hardcoded for compile-time safety (same philosophy as NodeHardCeilings).
var agentSpawnCommands = map[string]string{
	"kimi":     "kimi -y",
	"minimax":  "minimax",
	"pi":       "pi",
	"gemini":   "gemini -y",
	"glm":      "glm",
	"claude":   "claude --dangerously-skip-permissions",
	"cursor":   "cursor-agent -f",
	"amp":      "amp --dangerously-allow-all",
	"opencode": "opencode",
	"kilo":     "kilo",
}

// agentRAMRequirements maps agent types to their estimated RAM in MB.
// Includes 1.3x safety margin from ADR-035.
var agentRAMRequirements = map[string]int{
	"kimi":     130,   // 100MB * 1.3
	"minimax":  130,
	"pi":       130,
	"glm":      130,   // Claude Code wrapper ~100MB * 1.3
	"gemini":   390,   // 300MB * 1.3
	"claude":   1040,  // 800MB * 1.3
	"cursor":   1040,
	"amp":      1040,
	"opencode": 3250,  // 2500MB * 1.3
	"kilo":     1820,  // 1400MB * 1.3
}

// liveRAMCache caches /proc/meminfo reads to avoid syscall spam.
var liveRAMCache struct {
	sync.Mutex
	lastRead time.Time
	valueMB  int
}

// readLiveRAMMB reads /proc/meminfo and returns MemAvailable in MB.
// Cached for 30s to prevent syscall spam across patrol cycles.
// Uses MemAvailable (not MemFree) — accounts for reclaimable buff/cache.
func readLiveRAMMB() (int, error) {
	liveRAMCache.Lock()
	defer liveRAMCache.Unlock()

	// Return cached value if fresh (< 30s old).
	if time.Since(liveRAMCache.lastRead) < 30*time.Second {
		return liveRAMCache.valueMB, nil
	}

	data, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return 0, fmt.Errorf("read /proc/meminfo: %w", err)
	}

	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, "MemAvailable:") {
			fields := strings.Fields(line)
			if len(fields) < 2 {
				return 0, fmt.Errorf("malformed MemAvailable line")
			}
			kb, err := strconv.Atoi(fields[1])
			if err != nil {
				return 0, fmt.Errorf("parse MemAvailable kB: %w", err)
			}
			mb := kb / 1024
			liveRAMCache.valueMB = mb
			liveRAMCache.lastRead = time.Now()
			return mb, nil
		}
	}

	return 0, fmt.Errorf("MemAvailable not found in /proc/meminfo")
}

// readLoadAverage returns the 1-minute load average.
func readLoadAverage() (float64, error) {
	data, err := os.ReadFile("/proc/loadavg")
	if err != nil {
		return 0, fmt.Errorf("read /proc/loadavg: %w", err)
	}
	fields := strings.Fields(string(data))
	if len(fields) < 1 {
		return 0, fmt.Errorf("malformed /proc/loadavg")
	}
	return strconv.ParseFloat(fields[0], 64)
}

// getCPUCount returns the number of CPU cores.
func getCPUCount() int {
	// Try /proc/cpuinfo first.
	data, err := os.ReadFile("/proc/cpuinfo")
	if err == nil {
		count := strings.Count(string(data), "processor\t:")
		if count > 0 {
			return count
		}
	}
	// Fallback to runtime.
	return 4 // conservative default
}

// circuitBreaker tracks consecutive spawn failures to prevent flapping.
var circuitBreaker struct {
	sync.Mutex
	consecutiveFailures int
	lastFailure         time.Time
	state               string // "closed", "open"
}

// circuitBreakerTripped returns true if the circuit breaker is open (too many failures).
func circuitBreakerTripped() bool {
	circuitBreaker.Lock()
	defer circuitBreaker.Unlock()

	if circuitBreaker.state == "open" {
		// Auto-reset after 5 minutes.
		if time.Since(circuitBreaker.lastFailure) > 5*time.Minute {
			circuitBreaker.state = "closed"
			circuitBreaker.consecutiveFailures = 0
			return false
		}
		return true
	}
	return false
}

// recordSpawnFailure increments the circuit breaker.
func recordSpawnFailure() {
	circuitBreaker.Lock()
	defer circuitBreaker.Unlock()
	circuitBreaker.consecutiveFailures++
	circuitBreaker.lastFailure = time.Now()
	if circuitBreaker.consecutiveFailures >= 3 {
		circuitBreaker.state = "open"
		log.Printf("[fleet-auto-exec] CIRCUIT BREAKER OPEN after %d consecutive failures",
			circuitBreaker.consecutiveFailures)
	}
}

// recordSpawnSuccess resets the circuit breaker.
func recordSpawnSuccess() {
	circuitBreaker.Lock()
	defer circuitBreaker.Unlock()
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.state = "closed"
}

// lastScaleEvent tracks the last scale time to prevent flapping.
var lastScaleEvent struct {
	sync.Mutex
	timestamp time.Time
}

// canScale checks if enough time has passed since the last scale event (flap guard).
func canScale() bool {
	lastScaleEvent.Lock()
	defer lastScaleEvent.Unlock()
	return time.Since(lastScaleEvent.timestamp) >= 60*time.Second
}

// recordScaleEvent records a scale event for flap prevention.
func recordScaleEvent() {
	lastScaleEvent.Lock()
	defer lastScaleEvent.Unlock()
	lastScaleEvent.timestamp = time.Now()
}

// spawnAgent executes tmux commands to spawn a new agent.
// Returns the tmux window name on success.
func spawnAgent(ctx context.Context, agentType, nodeID string) (string, error) {
	cmd, ok := agentSpawnCommands[agentType]
	if !ok {
		return "", fmt.Errorf("unknown agent type: %s", agentType)
	}

	// Find next available window number.
	// List existing windows in 'forge' session, find next N for agentType-N.
	existingWindows, _ := exec.Command("tmux", "list-windows", "-t", "forge", "-F", "#{window_name}").Output()
	existingNames := strings.Split(strings.TrimSpace(string(existingWindows)), "\n")
	nextNum := 1
	for _, name := range existingNames {
		if strings.HasPrefix(name, agentType+"-") {
			// Parse suffix number.
			suffix := strings.TrimPrefix(name, agentType+"-")
			if n, err := strconv.Atoi(suffix); err == nil && n >= nextNum {
				nextNum = n + 1
			}
		}
	}
	// If the base window (exact agentType name) exists, nextNum must be >= 2.
	for _, name := range existingNames {
		if name == agentType && nextNum < 2 {
			nextNum = 2
		}
	}
	windowName := fmt.Sprintf("%s-%d", agentType, nextNum)

	// Environment variables for the spawned agent.
	forgeAPIURL := os.Getenv("FORGE_API_URL")
	if forgeAPIURL == "" {
		forgeAPIURL = "http://prya:8081"
	}
	envVars := []string{
		"FORGE_AGENT_TYPE=fleet",
		fmt.Sprintf("FORGE_AGENT_NAME=%s", windowName),
		fmt.Sprintf("FORGE_API_URL=%s", forgeAPIURL),
	}

	// Create the tmux window and start the agent.
	// Step 1: Create new window.
	if err := exec.Command("tmux", "new-window", "-t", "forge", "-n", windowName).Run(); err != nil {
		return "", fmt.Errorf("tmux new-window: %w", err)
	}

	// Step 2: Send environment + command.
	// Build the full command with env vars.
	fullCmd := fmt.Sprintf("export %s && cd %s && %s",
		strings.Join(envVars, " && export "),
		findForgeRoot(),
		cmd)

	// Use -l flag for literal strings to prevent escape misinterpretation.
	tmuxTarget := fmt.Sprintf("forge:%s", windowName)
	if err := exec.Command("tmux", "send-keys", "-t", tmuxTarget, "-l", fullCmd).Run(); err != nil {
		return "", fmt.Errorf("tmux send-keys cmd: %w", err)
	}
	if err := exec.Command("tmux", "send-keys", "-t", tmuxTarget, "").Run(); err != nil {
		return "", fmt.Errorf("tmux send-keys enter: %w", err)
	}

	log.Printf("[fleet-auto-exec] SPAWNED agent %s in window %s", agentType, windowName)
	return windowName, nil
}

// findForgeRoot locates the FORGE repository root.
// Priority: FORGE_ROOT env var → executable path walk → CWD walk → hardcoded fallback.
// The executable-path walk distinguishes repo root from subdirs by the absence of go.mod
// (the repo root has CLAUDE.md but no go.mod; cmd/forged has both).
func findForgeRoot() string {
	// Try FORGE_ROOT env var first.
	if root := os.Getenv("FORGE_ROOT"); root != "" {
		return root
	}
	// Walk up from the daemon executable's location — reliable regardless of CWD at startup.
	if exe, err := os.Executable(); err == nil {
		dir := filepath.Dir(exe)
		for i := 0; i < 5; i++ {
			_, hasClaude := os.Stat(filepath.Join(dir, "CLAUDE.md"))
			_, hasGoMod := os.Stat(filepath.Join(dir, "go.mod"))
			if hasClaude == nil && hasGoMod != nil {
				// Has CLAUDE.md, no go.mod — this is the repo root.
				return dir
			}
			parent := filepath.Dir(dir)
			if parent == dir {
				break
			}
			dir = parent
		}
	}
	// Fall back to CWD walk, same heuristic.
	wd, _ := os.Getwd()
	for {
		_, hasClaude := os.Stat(filepath.Join(wd, "CLAUDE.md"))
		_, hasGoMod := os.Stat(filepath.Join(wd, "go.mod"))
		if hasClaude == nil && hasGoMod != nil {
			return wd
		}
		parent := filepath.Dir(wd)
		if parent == wd {
			break
		}
		wd = parent
	}
	return "/home/openclaw/work/FORGE" // absolute fallback
}

// fleetAutoExecutePatrol is the ADR-036 Phase 3 patrol.
// Reads auto_execute=1, status='pending' recommendations and spawns agents.
// Runs every 2 minutes. Disabled on NoAutoSpawnNodes (prya/vega/gaea).
func fleetAutoExecutePatrol(ctx context.Context, db *sql.DB) error {
	// 0. NoAutoSpawnNodes: completely disable on resource-constrained nodes.
	// All agent spawning on these nodes is manual-only.
	localNodeCheck, _ := os.Hostname()
	if NoAutoSpawnNodes[localNodeCheck] {
		return nil
	}

	// 1. Circuit breaker gate.
	if circuitBreakerTripped() {
		log.Printf("[Patrol:fleet-auto-exec] CIRCUIT BREAKER OPEN — skipping cycle")
		return nil
	}

	// 2. Flap guard: ensure 60s since last scale event.
	if !canScale() {
		log.Printf("[Patrol:fleet-auto-exec] flap guard active — skipping cycle")
		return nil
	}

	// 3. Query pending auto_execute recommendations.
	rows, err := db.QueryContext(ctx, `
		SELECT id, node_id, agent_type, reason
		FROM scale_recommendations
		WHERE auto_execute = 1
		  AND status = 'pending'
		  AND action = 'inflate'
		  AND expires_at > datetime('now')
		ORDER BY created_at ASC
		LIMIT 1
	`)
	if err != nil {
		return fmt.Errorf("query pending recs: %w", err)
	}
	defer rows.Close()

	var recID, nodeID, agentType, reason string
	if rows.Next() {
		if err := rows.Scan(&recID, &nodeID, &agentType, &reason); err != nil {
			return fmt.Errorf("scan rec: %w", err)
		}
	} else {
		// No pending recs.
		return nil
	}
	rows.Close()

	// Atomically claim the recommendation — prevents concurrent patrol double-execution.
	res, err := db.ExecContext(ctx, `
		UPDATE scale_recommendations
		SET status = 'executing'
		WHERE id = ? AND status = 'pending'
	`, recID)
	if err != nil {
		return fmt.Errorf("claim rec: %w", err)
	}
	if n, _ := res.RowsAffected(); n == 0 {
		// Another patrol cycle already claimed this rec.
		return nil
	}

	// 3b. Executor-side ForbiddenAgentTypes guard (defense-in-depth against stale recs).
	execLocalNode, _ := os.Hostname()
	if execLocalNode == "" {
		execLocalNode = nodeID
	}
	if forbidden, ok := ForbiddenAgentTypes[execLocalNode]; ok && forbidden[agentType] {
		log.Printf("[Patrol:fleet-auto-exec] HARD RULE VIOLATION prevented: agent type %q is forbidden on node %q", agentType, execLocalNode)
		markRecDeferred(ctx, db, recID, "deferred-forbidden")
		return nil
	}

	// 4. LIVE RAM gate (not heartbeat JSON).
	availableRAM, err := readLiveRAMMB()
	if err != nil {
		log.Printf("[Patrol:fleet-auto-exec] RAM read error: %v", err)
		return nil
	}
	ramRequired := agentRAMRequirements[agentType]
	// Reserve 500MB for orchestrator.
	if availableRAM < ramRequired+500 {
		log.Printf("[Patrol:fleet-auto-exec] RAM gate FAIL: available %dMB < required %dMB + 500MB reserve",
			availableRAM, ramRequired)
		markRecDeferred(ctx, db, recID, "deferred-ram")
		return nil
	}

	// 5. CPU stress gate (load > 80% of cores).
	load, _ := readLoadAverage()
	ncpu := getCPUCount()
	if load > float64(ncpu)*0.8 {
		log.Printf("[Patrol:fleet-auto-exec] CPU gate FAIL: load %.2f > 80%% of %d cores", load, ncpu)
		markRecDeferred(ctx, db, recID, "deferred-cpu")
		return nil
	}

	// 6–8. Ceiling check → spawn: held under spawnMu so two concurrent patrol
	// invocations cannot both pass the ceiling gate before either has created
	// its tmux window.
	spawnMu.Lock()
	// 6. Ceiling check: use LOCAL hostname (not recommendation's node_id which may be "unknown").
	localNode, _ := os.Hostname()
	if localNode == "" {
		localNode = nodeID
	}

	// Count agent_inventory + tmux windows for hard gate.
	// agent_inventory may be stale (agents don't always deregister), so use tmux as source of truth.
	tmuxOut, _ := exec.Command("tmux", "list-windows", "-t", "forge", "-F", "#{window_name}").Output()
	tmuxWindowCount := 0
	agentTypeWindowCount := 0
	for _, line := range strings.Split(strings.TrimSpace(string(tmuxOut)), "\n") {
		wname := strings.TrimSpace(line)
		if wname == "" {
			continue
		}
		// Exclude the orchestrator window (named after the hostname) from the agent ceiling count.
		if wname == localNode {
			continue
		}
		tmuxWindowCount++
		// Count windows for this specific agent type (e.g. "kimi", "kimi-2", "kimi-10")
		if wname == agentType || strings.HasPrefix(wname, agentType+"-") {
			agentTypeWindowCount++
		}
	}

	ceiling, ok := NodeHardCeilings[localNode]
	if !ok {
		ceiling = 2 // conservative default
	}

	// Hard gate: total tmux windows (minus prya:1 = forge window) must be < ceiling
	nonForgeWindows := tmuxWindowCount // tmux list-windows on forge session = all agent windows
	if nonForgeWindows >= ceiling {
		spawnMu.Unlock()
		log.Printf("[Patrol:fleet-auto-exec] ceiling gate FAIL: node %s at %d/%d tmux windows",
			localNode, nonForgeWindows, ceiling)
		markRecDeferred(ctx, db, recID, "deferred-ceiling")
		return nil
	}

	// Per-agent-type cap: max 1 of any type by default (prevents runaway kimi spawning)
	maxPerType := 1
	if agentTypeWindowCount >= maxPerType {
		spawnMu.Unlock()
		log.Printf("[Patrol:fleet-auto-exec] per-type cap FAIL: already %d %s window(s) running",
			agentTypeWindowCount, agentType)
		markRecDeferred(ctx, db, recID, "deferred-type-cap")
		return nil
	}

	// 7. Token budget gate: check provider status.
	provider := agentProvider(agentType)
	if budgetOK, budgetReason := checkTokenBudgetGate(provider); !budgetOK {
		spawnMu.Unlock()
		log.Printf("[Patrol:fleet-auto-exec] budget gate FAIL: %s — %s", provider, budgetReason)
		markRecDeferred(ctx, db, recID, "deferred-budget")
		return nil
	}

	// 8. All gates passed — SPAWN.
	windowName, err := spawnAgent(ctx, agentType, nodeID)
	spawnMu.Unlock()
	if err != nil {
		log.Printf("[Patrol:fleet-auto-exec] SPAWN FAILED: %v", err)
		recordSpawnFailure()
		markRecFailed(ctx, db, recID, err.Error())
		return nil
	}

	// 9. Success — update rec and inventory.
	recordSpawnSuccess()
	recordScaleEvent()

	_, err = db.ExecContext(ctx, `
		UPDATE scale_recommendations
		SET status = 'executed', executed_at = datetime('now')
		WHERE id = ?
	`, recID)
	if err != nil {
		log.Printf("[Patrol:fleet-auto-exec] update rec status error: %v", err)
	}

	// Insert/update agent_inventory.
	_, err = db.ExecContext(ctx, `
		INSERT OR REPLACE INTO agent_inventory
			(agent_id, node_id, status, tmux_window, spawn_command, spawned_at, agent_tier)
		VALUES (?, ?, 'spawning', ?, ?, datetime('now'), ?)
	`, windowName, nodeID, windowName, agentSpawnCommands[agentType], agentTier(agentType))
	if err != nil {
		log.Printf("[Patrol:fleet-auto-exec] inventory insert error: %v", err)
	}

	log.Printf("[Patrol:fleet-auto-exec] EXECUTED inflate node=%s agent=%s window=%s reason=%s",
		nodeID, agentType, windowName, reason)
	return nil
}

// agentProvider maps agent types to their token providers.
func agentProvider(agentType string) string {
	switch agentType {
	case "kimi":
		return "moonshot"
	case "minimax":
		return "minimax"
	case "pi":
		return "inflection"
	case "gemini":
		return "google"
	case "claude", "amp":
		return "anthropic"
	case "cursor":
		return "cursor"
	case "opencode", "kilo":
		return "openai"
	default:
		return "unknown"
	}
}

// checkTokenBudgetGate checks if a provider has available token budget.
func checkTokenBudgetGate(provider string) (bool, string) {
	// Read token budget JSON.
	data, err := os.ReadFile(".forge/heartbeat/token-budgets-prya.json")
	if err != nil {
		// Fail closed: missing budget file means we cannot verify budget safety.
		return false, "no budget file — blocking spawn until budget file is restored"
	}

	var budgets struct {
		Providers map[string]struct {
			Status string `json:"status"`
		} `json:"providers"`
	}
	if err := json.Unmarshal(data, &budgets); err != nil {
		// Fail closed: corrupted budget data should not allow unchecked spawning.
		return false, "budget parse error — blocking spawn until budget file is valid"
	}

	p, ok := budgets.Providers[provider]
	if !ok {
		return true, "provider not in budget — allow"
	}

	switch p.Status {
	case "COOLDOWN":
		return false, "provider in COOLDOWN"
	case "DEPLETED":
		return false, "provider DEPLETED"
	case "RED":
		return false, "provider in RED zone"
	default:
		return true, "provider OK or CAUTION"
	}
}

// markRecDeferred marks a recommendation as deferred with reason.
func markRecDeferred(ctx context.Context, db *sql.DB, recID, reason string) {
	_, _ = db.ExecContext(ctx, `
		UPDATE scale_recommendations
		SET status = ?, updated_at = datetime('now')
		WHERE id = ?
	`, reason, recID)
}

// markRecFailed marks a recommendation as failed.
func markRecFailed(ctx context.Context, db *sql.DB, recID, errMsg string) {
	_, _ = db.ExecContext(ctx, `
		UPDATE scale_recommendations
		SET status = 'failed', error_message = ?, updated_at = datetime('now')
		WHERE id = ?
	`, errMsg, recID)
}

// fleetAutoDeflatePatrol manages the drain-kill lifecycle for deflation.
// Runs every 2 minutes. Checks draining agents and kills them after timeout.
func fleetAutoDeflatePatrol(ctx context.Context, db *sql.DB) error {
	// 0. No auto-deflate on no-auto-spawn nodes: we don't auto-spawn, so nothing to auto-kill.
	localNodeCheck, _ := os.Hostname()
	if NoAutoSpawnNodes[localNodeCheck] {
		return nil
	}

	// 1. Find agents in 'draining' state.
	rows, err := db.QueryContext(ctx, `
		SELECT agent_type, COALESCE(tmux_window,''), node_id, COALESCE(started_at, datetime('now','-20 minutes'))
		FROM agent_inventory
		WHERE drain_state = 'draining'
	`)
	if err != nil {
		log.Printf("[Patrol:fleet-auto-deflate] query draining agents error: %v", err)
		return nil
	}
	defer rows.Close()

	type drainingAgent struct {
		agentID        string
		tmuxWindow     string
		nodeID         string
		drainStartedAt string
	}

	var agents []drainingAgent
	for rows.Next() {
		var a drainingAgent
		if err := rows.Scan(&a.agentID, &a.tmuxWindow, &a.nodeID, &a.drainStartedAt); err != nil {
			continue
		}
		agents = append(agents, a)
	}
	rows.Close()

	const drainTimeout = 10 * time.Minute
	for _, a := range agents {
		// Check if drain timeout has passed.
		drainStart, err := time.Parse(time.RFC3339, a.drainStartedAt)
		if err != nil {
			// Malformed timestamp — kill immediately.
			log.Printf("[Patrol:fleet-auto-deflate] malformed drain_started_at for %s: %v", a.agentID, err)
			killAgent(ctx, db, a.agentID, a.tmuxWindow, "drain-timeout-malformed")
			continue
		}

		if time.Since(drainStart) < drainTimeout {
			// Still in drain window — check if task completed.
			var assignedCount int
			_ = db.QueryRowContext(ctx, `
				SELECT COUNT(*) FROM tasks
				WHERE assigned_to = ? AND status IN ('assigned', 'executing')
			`, a.agentID).Scan(&assignedCount)

			if assignedCount == 0 {
				// Task completed early — safe to kill.
				killAgent(ctx, db, a.agentID, a.tmuxWindow, "task-completed")
			} else {
				log.Printf("[Patrol:fleet-auto-deflate] agent %s still draining (%d task(s), %s remaining)",
					a.agentID, assignedCount, drainTimeout-time.Since(drainStart))
			}
			continue
		}

		// Timeout reached — kill regardless of task state.
		killAgent(ctx, db, a.agentID, a.tmuxWindow, "drain-timeout")
	}

	// 2. Process approved deflation recommendations.
	// Check for approved deflate recs and start drain.
	deflateRows, err := db.QueryContext(ctx, `
		SELECT r.id, r.agent_type, r.node_id
		FROM scale_recommendations r
		WHERE r.action = 'deflate'
		  AND r.status = 'approved'
		  AND r.processed_at IS NULL
	`)
	if err != nil {
		log.Printf("[Patrol:fleet-auto-deflate] query approved deflates error: %v", err)
		return nil
	}
	defer deflateRows.Close()

	for deflateRows.Next() {
		var recID, agentID, nodeID string
		if err := deflateRows.Scan(&recID, &agentID, &nodeID); err != nil {
			continue
		}

		// Start drain for this agent.
		startDrain(ctx, db, agentID, nodeID, recID)
	}

	return nil
}

// staleTaskTTLPatrol re-queues or abandons tasks assigned to agents with no recent heartbeat.
// Runs every 5 minutes. Prevents stale "assigned" tasks from inflating queue_depth signal.
// ADR-036 guardrail: without this, auto-execute sees phantom demand and spawns endlessly.
func staleTaskTTLPatrol(ctx context.Context, db *sql.DB) error {
	// Re-queue tasks assigned to agents with no heartbeat in 15 minutes (short window).
	// These agents are likely offline but may reconnect — give them back to the queue.
	requeued, err := db.ExecContext(ctx, `
		UPDATE tasks
		SET status = 'queued', assigned_to = NULL, updated_at = datetime('now')
		WHERE status = 'assigned'
		  AND assigned_to IS NOT NULL
		  AND assigned_to NOT IN (
		    SELECT agent_id FROM agent_heartbeats
		    WHERE last_seen > datetime('now', '-15 minutes')
		  )
		  AND updated_at < datetime('now', '-15 minutes')
		  AND updated_at > datetime('now', '-2 hours')
	`)
	if err != nil {
		log.Printf("[Patrol:stale-task-ttl] re-queue error: %v", err)
	} else if n, _ := requeued.RowsAffected(); n > 0 {
		log.Printf("[Patrol:stale-task-ttl] re-queued %d tasks from offline agents", n)
	}

	// Abandon tasks assigned >2 hours ago to offline agents — they won't be coming back.
	abandoned, err := db.ExecContext(ctx, `
		UPDATE tasks
		SET status = 'abandoned', updated_at = datetime('now')
		WHERE status = 'assigned'
		  AND assigned_to IS NOT NULL
		  AND assigned_to NOT IN (
		    SELECT agent_id FROM agent_heartbeats
		    WHERE last_seen > datetime('now', '-15 minutes')
		  )
		  AND updated_at < datetime('now', '-2 hours')
	`)
	if err != nil {
		log.Printf("[Patrol:stale-task-ttl] abandon error: %v", err)
	} else if n, _ := abandoned.RowsAffected(); n > 0 {
		log.Printf("[Patrol:stale-task-ttl] abandoned %d tasks (>2hr, agent offline)", n)
	}

	return nil
}

// startDrain sets an agent to draining state.
func startDrain(ctx context.Context, db *sql.DB, agentID, nodeID, recID string) {
	// Check council marker — protected agents cannot be drained.
	if hasActiveCouncilMarker(agentID) {
		log.Printf("[Patrol:fleet-auto-deflate] agent %s has council marker — skip drain", agentID)
		_, _ = db.ExecContext(ctx, `UPDATE scale_recommendations SET status = 'rejected', processed_at = datetime('now') WHERE id = ?`, recID)
		return
	}

	// Set drain state.
	_, err := db.ExecContext(ctx, `
		UPDATE agent_inventory
		SET drain_state = 'draining',
		    drain_started_at = datetime('now')
		WHERE agent_id = ?
	`, agentID)
	if err != nil {
		log.Printf("[Patrol:fleet-auto-deflate] set drain state error for %s: %v", agentID, err)
		return
	}

	// Mark recommendation as processing.
	_, _ = db.ExecContext(ctx, `UPDATE scale_recommendations SET status = 'processing', processed_at = datetime('now') WHERE id = ?`, recID)

	log.Printf("[Patrol:fleet-auto-deflate] STARTED drain for agent %s on node %s", agentID, nodeID)
}

// tierCanClaim returns true if an agent of agentTier can claim a task with requiredTier.
// Hierarchy: heavy >= medium >= lightweight. 'any' is open to all.
func tierCanClaim(agentTier, requiredTier string) bool {
	if requiredTier == "" || requiredTier == "any" {
		return true
	}
	// Normalize
	switch agentTier {
	case "heavy":
		return true // heavy can claim anything
	case "medium":
		return requiredTier == "lightweight" || requiredTier == "medium"
	case "lightweight":
		return requiredTier == "lightweight"
	default:
		return true // unknown tier — don't block
	}
}

// checkTierCompatibility queries the task's required_tier and agent's tier
// and returns an error if the agent cannot claim this task.
func checkTierCompatibility(ctx context.Context, taskID, agentID string) error {
	db := getDBConn()
	if db == nil {
		return nil // no DB — skip check
	}

	var requiredTier sql.NullString
	_ = db.QueryRowContext(ctx, `SELECT required_tier FROM tasks WHERE id = ?`, taskID).Scan(&requiredTier)

	rt := "any"
	if requiredTier.Valid && requiredTier.String != "" {
		rt = requiredTier.String
	}
	if rt == "any" {
		return nil
	}

	var agentTier sql.NullString
	_ = db.QueryRowContext(ctx, `SELECT agent_tier FROM agent_inventory WHERE agent_id = ?`, agentID).Scan(&agentTier)

	at := "medium" // default if not registered
	if agentTier.Valid && agentTier.String != "" {
		at = agentTier.String
	}

	if !tierCanClaim(at, rt) {
		return fmt.Errorf("agent tier %q cannot claim task with required_tier %q", at, rt)
	}
	return nil
}

// orchestratorWorkStrategyPatrol reads the work-strategy catalog and creates
// tasks for idle agents when the queue is shallow. Runs every 10 minutes.
// ADR-036 Phase 4: FILL pillar — keep agents busy with meaningful work.
func orchestratorWorkStrategyPatrol(ctx context.Context, db *sql.DB) error {
	// 1. Count idle agents (online, no current task).
	var idleCount int
	_ = db.QueryRowContext(ctx, `
		SELECT COUNT(DISTINCT a.id) FROM agents a
		WHERE a.status = 'online'
		  AND NOT EXISTS (
		    SELECT 1 FROM tasks t
		    WHERE t.assigned_to = a.id
		      AND t.status IN ('assigned', 'executing')
		  )
	`).Scan(&idleCount)

	if idleCount == 0 {
		return nil // All agents busy — no fill needed.
	}

	// 2. Count queued tasks — don't over-create if queue is healthy.
	var queueDepth int
	_ = db.QueryRowContext(ctx, `SELECT COUNT(*) FROM tasks WHERE status = 'queued'`).Scan(&queueDepth)
	if queueDepth >= idleCount*2 {
		return nil // Queue has enough work already.
	}

	// 3. Load catalog.
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}
	catalogPath := filepath.Join(forgeRoot, ".forge", "work-strategy", "catalog.toml")
	catalogData, err := os.ReadFile(catalogPath)
	if err != nil {
		return nil // No catalog — skip silently.
	}

	type CatalogTask struct {
		ID           string `toml:"id"`
		Title        string `toml:"title"`
		Description  string `toml:"description"`
		RequiredTier string `toml:"required_tier"`
		Schedule     string `toml:"schedule"`
		Domain       string `toml:"domain"`
		Priority     int    `toml:"priority"`
	}
	type Catalog struct {
		Tasks []CatalogTask `toml:"tasks"`
	}

	// Simple TOML parser for catalog (avoids external dependency).
	var entries []CatalogTask
	var current *CatalogTask
	for _, line := range strings.Split(string(catalogData), "\n") {
		line = strings.TrimSpace(line)
		if line == "[[tasks]]" {
			if current != nil {
				entries = append(entries, *current)
			}
			current = &CatalogTask{}
			continue
		}
		if current == nil || line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		val := strings.Trim(strings.TrimSpace(parts[1]), `"`)
		switch key {
		case "id":
			current.ID = val
		case "title":
			current.Title = val
		case "description":
			current.Description = val
		case "required_tier":
			current.RequiredTier = val
		case "schedule":
			current.Schedule = val
		case "domain":
			current.Domain = val
		case "priority":
			fmt.Sscanf(val, "%d", &current.Priority)
		}
	}
	if current != nil {
		entries = append(entries, *current)
	}

	if len(entries) == 0 {
		return nil
	}

	nodeID := os.Getenv("NODE_ID")
	if nodeID == "" {
		nodeID, _ = os.Hostname()
	}

	created := 0
	for _, entry := range entries {
		if created >= idleCount {
			break // Don't over-create.
		}

		// Check work_strategy_log — was this catalog entry run recently?
		var lastRun sql.NullString
		_ = db.QueryRowContext(ctx, `
			SELECT created_at FROM work_strategy_log
			WHERE catalog_id = ? AND node_id = ?
			ORDER BY created_at DESC LIMIT 1
		`, entry.ID, nodeID).Scan(&lastRun)

		if lastRun.Valid {
			lastRunTime, err := time.Parse("2006-01-02T15:04:05Z", lastRun.String)
			if err != nil {
				lastRunTime, _ = time.Parse("2006-01-02 15:04:05", lastRun.String)
			}
			var cooldown time.Duration
			switch entry.Schedule {
			case "daily":
				cooldown = 20 * time.Hour
			case "weekly":
				cooldown = 6 * 24 * time.Hour
			default: // on-idle: 4h minimum
				cooldown = 4 * time.Hour
			}
			if time.Since(lastRunTime) < cooldown {
				continue // Too soon to re-run.
			}
		}

		// Create the task.
		taskID := fmt.Sprintf("WS-%s-%s", strings.ToUpper(entry.ID), time.Now().Format("20060102"))
		priority := entry.Priority
		if priority == 0 {
			priority = 3
		}

		_, err := db.ExecContext(ctx, `
			INSERT OR IGNORE INTO tasks
			  (id, domain, project, type, title, description, status, priority, required_tier, created_at, updated_at)
			VALUES
			  (?, ?, 'forge', 'maintenance', ?, ?, 'queued', ?, ?, datetime('now'), datetime('now'))
		`, taskID, entry.Domain, entry.Title, entry.Description, priority, entry.RequiredTier)
		if err != nil {
			log.Printf("[Patrol:work-strategy] failed to create task %s: %v", taskID, err)
			continue
		}

		// Log to work_strategy_log.
		logID := fmt.Sprintf("wsl-%s-%d", entry.ID, time.Now().UnixNano())
		_, _ = db.ExecContext(ctx, `
			INSERT INTO work_strategy_log (id, catalog_id, task_id, node_id, created_at, status)
			VALUES (?, ?, ?, ?, datetime('now'), 'created')
		`, logID, entry.ID, taskID, nodeID)

		log.Printf("[Patrol:work-strategy] created catalog task %s (%s, tier=%s)",
			taskID, entry.Title, entry.RequiredTier)
		created++
	}

	if created > 0 {
		log.Printf("[Patrol:work-strategy] created %d maintenance task(s) for %d idle agent(s)", created, idleCount)
	}
	return nil
}

// phantomTaskAutoCompletePatrol completes auto-generated tasks that match
// known noise patterns. Council S157 Proposal #1.
//
// The orchestratorWorkStrategyPatrol creates tasks assigned to the orchestrator
// node (e.g. "Fleet has idle agents", "health check failed"). These flood the
// orchestrator's session with notifications, wasting ~40% of context. This
// patrol auto-completes them within 60 seconds of creation so they never reach
// the orchestrator.
func phantomTaskAutoCompletePatrol(ctx context.Context, db *sql.DB) error {
	// Auto-complete tasks that:
	// 1. Are in queued/assigned status
	// 2. Match known noise patterns in title
	// 3. Were created more than 60 seconds ago (grace period for legitimate claims)
	patterns := []string{
		"%Fleet%idle%",
		"%Fleet%down%",
		"%Fleet%0 agents%",
		"%fleet status%",
		"%Test message%",
		"%health check%",
		"%spawn agents%",
		"%delegate work%",
		"%Check forge%",
	}

	for _, pattern := range patterns {
		result, err := db.ExecContext(ctx, `
			UPDATE tasks
			SET status = 'completed',
			    updated_at = datetime('now')
			WHERE status IN ('queued', 'assigned')
			  AND title LIKE ?
			  AND created_at < datetime('now', '-60 seconds')
		`, pattern)
		if err != nil {
			return fmt.Errorf("phantom task cleanup pattern %q: %w", pattern, err)
		}
		if n, _ := result.RowsAffected(); n > 0 {
			log.Printf("[Patrol:phantom-task-cleanup] auto-completed %d task(s) matching %q", n, pattern)
		}
	}
	return nil
}

// taskDispatcherPatrol picks up queued tasks and sends them to idle local tmux
// agent windows. This bridges the gap between the task queue (daemon) and agents
// that only respond to tmux send-keys (not daemon-mode polling).
//
// Flow: query queued tasks → list idle tmux agents → match → send via tmux → mark assigned.
func taskDispatcherPatrol(ctx context.Context, db *sql.DB) error {
	// 1. Get queued tasks (limit to prevent flooding).
	rows, err := db.QueryContext(ctx, `
		SELECT id, title, description
		FROM tasks
		WHERE status IN ('queued') AND (state IN ('QUEUED', '') OR state IS NULL)
		ORDER BY created_at ASC
		LIMIT 5
	`)
	if err != nil {
		return fmt.Errorf("query queued tasks: %w", err)
	}
	defer rows.Close()

	type queuedTask struct {
		ID, Title, Description string
	}
	var tasks []queuedTask
	for rows.Next() {
		var t queuedTask
		if err := rows.Scan(&t.ID, &t.Title, &t.Description); err != nil {
			continue
		}
		tasks = append(tasks, t)
	}
	if len(tasks) == 0 {
		return nil
	}

	// 2. List tmux agent windows and find idle ones.
	// Idle = running an agent process (not bash/zsh) AND not currently assigned a task.
	tmuxOut, err := exec.Command("tmux", "list-windows", "-t", "forge",
		"-F", "#{window_name}\t#{pane_current_command}").Output()
	if err != nil {
		// No tmux session — nothing to dispatch to.
		return nil
	}

	hostname, _ := os.Hostname()
	agentProcesses := map[string]bool{
		"claude": true, "opencode": true, "kimi": true, "codex": true,
		"kilo": true, "amp": true, "gemini": true, "aider": true,
		"node": true, "pi": true, "glm": true, "cursor": true, "minimax": true,
		// Actual process names that differ from agent names:
		"python3.13":       true, // kimi runs as python3.13
		"python3.12":       true, // kimi on older systems
		"python3":          true, // kimi fallback
		"codex-aarch64-a":  true, // codex binary on ARM64 macOS
		"codex-x86_64-l":   true, // codex binary on x86 Linux
		"cursor-agent":     true, // cursor runs as cursor-agent
	}

	var idleAgents []string
	for _, line := range strings.Split(strings.TrimSpace(string(tmuxOut)), "\n") {
		parts := strings.SplitN(line, "\t", 2)
		if len(parts) != 2 {
			continue
		}
		windowName := strings.TrimSpace(parts[0])
		paneCmd := strings.ToLower(strings.TrimSpace(parts[1]))

		// Skip orchestrator window (named after hostname).
		if windowName == hostname {
			continue
		}
		// Skip non-agent processes (bare shells).
		if !agentProcesses[paneCmd] {
			continue
		}
		// Check if agent already has an assigned task.
		var activeCount int
		db.QueryRowContext(ctx, `
			SELECT COUNT(*) FROM tasks
			WHERE assigned_to = ? AND status IN ('assigned', 'executing')
		`, windowName).Scan(&activeCount)
		if activeCount > 0 {
			continue
		}
		// Skip agents that are blocked (work_state='blocked') — they cannot accept tasks.
		var workState string
		db.QueryRowContext(ctx, `
			SELECT COALESCE(work_state, 'idle') FROM agent_heartbeats WHERE agent_id = ?
		`, windowName).Scan(&workState)
		if workState == "blocked" {
			log.Printf("[Patrol:task-dispatcher] agent %s is blocked — skipping", windowName)
			continue
		}
		idleAgents = append(idleAgents, windowName)
	}

	if len(idleAgents) == 0 {
		return nil
	}

	// 3. Match tasks to idle agents and dispatch.
	dispatched := 0
	for i, t := range tasks {
		if i >= len(idleAgents) {
			break
		}
		agent := idleAgents[i]
		target := fmt.Sprintf("forge:%s", agent)

		// Build prompt.
		prompt := t.Description
		if prompt == "" {
			prompt = t.Title
		}
		taskPrompt := fmt.Sprintf("Task %s: %s", t.ID, prompt)

		// Send via tmux (2-step protocol).
		if err := exec.Command("tmux", "send-keys", "-t", target, "-l", taskPrompt).Run(); err != nil {
			log.Printf("[Patrol:task-dispatcher] tmux send to %s failed: %v", agent, err)
			continue
		}
		time.Sleep(100 * time.Millisecond)
		if err := exec.Command("tmux", "send-keys", "-t", target, "", "Enter").Run(); err != nil {
			log.Printf("[Patrol:task-dispatcher] tmux Enter to %s failed: %v", agent, err)
			continue
		}

		// Mark task as assigned.
		_, _ = db.ExecContext(ctx, `
			UPDATE tasks SET status = 'assigned', assigned_to = ?, state = 'DISPATCHED',
				updated_at = datetime('now')
			WHERE id = ?
		`, agent, t.ID)

		// Update agent work_state to "working" so dispatch routing skips it.
		_, _ = db.ExecContext(ctx, `
			UPDATE agent_heartbeats SET work_state = 'working' WHERE agent_id = ?
		`, agent)

		log.Printf("[Patrol:task-dispatcher] dispatched %s to %s (work_state=working)", t.ID, agent)
		dispatched++
	}

	if dispatched > 0 {
		log.Printf("[Patrol:task-dispatcher] dispatched %d task(s) to %d idle agent(s)", dispatched, dispatched)
	}
	return nil
}

// agentRecoveryRateLimit tracks per-agent recovery timestamps for the
// agentAutoRecoveryPatrol rate limiter. Max 3 recoveries per agent per hour.
var agentRecoveryRateLimit struct {
	sync.Mutex
	// timestamps maps agentName → slice of recovery times within the last hour.
	timestamps map[string][]time.Time
}

func init() {
	agentRecoveryRateLimit.timestamps = make(map[string][]time.Time)
}

// agentAutoRecoveryPatrol detects tmux agent windows whose process has reverted
// to a bare shell (bash/zsh), meaning the agent crashed.  If the crashed agent
// has an active task (status assigned or executing) it restarts the agent and
// re-dispatches the task via tmux send-keys.
//
// Rate limit: at most 3 recoveries per agent name per rolling hour.
// All recovery actions are logged with the [Patrol:agent-recovery] prefix.
func agentAutoRecoveryPatrol(ctx context.Context, db *sql.DB) error {
	// 1. List all tmux windows in the forge session.
	tmuxOut, err := exec.Command("tmux", "list-windows", "-t", "forge",
		"-F", "#{window_name}\t#{pane_current_command}").Output()
	if err != nil {
		// No tmux session available — nothing to do.
		return nil
	}

	hostname, _ := os.Hostname()

	shellProcesses := map[string]bool{
		"bash": true,
		"zsh":  true,
		"sh":   true,
	}

	// 2. Iterate windows and find crashed agents (shell running instead of agent).
	for _, line := range strings.Split(strings.TrimSpace(string(tmuxOut)), "\n") {
		parts := strings.SplitN(line, "\t", 2)
		if len(parts) != 2 {
			continue
		}
		agentName := strings.TrimSpace(parts[0])
		paneCmd := strings.ToLower(strings.TrimSpace(parts[1]))

		// Skip the orchestrator window.
		if agentName == hostname {
			continue
		}
		// Skip windows where a real agent process is running.
		if !shellProcesses[paneCmd] {
			continue
		}

		// 3. Agent window is running a shell — check for an assigned task.
		var taskID, taskTitle string
		err := db.QueryRowContext(ctx, `
			SELECT id, title FROM tasks
			WHERE assigned_to = ? AND status IN ('assigned', 'executing')
			LIMIT 1
		`, agentName).Scan(&taskID, &taskTitle)
		if err != nil {
			// No active task for this dead agent — nothing to recover.
			continue
		}

		// 4. Rate-limit: allow at most 3 recoveries per agent per rolling hour.
		agentRecoveryRateLimit.Lock()
		now := time.Now()
		cutoff := now.Add(-1 * time.Hour)
		recent := agentRecoveryRateLimit.timestamps[agentName]
		// Prune timestamps older than 1 hour.
		filtered := recent[:0]
		for _, ts := range recent {
			if ts.After(cutoff) {
				filtered = append(filtered, ts)
			}
		}
		agentRecoveryRateLimit.timestamps[agentName] = filtered
		if len(filtered) >= 3 {
			agentRecoveryRateLimit.Unlock()
			log.Printf("[Patrol:agent-recovery] rate-limit hit for %s (%d recoveries in last hour) — skipping",
				agentName, len(filtered))
			continue
		}
		agentRecoveryRateLimit.timestamps[agentName] = append(filtered, now)
		agentRecoveryRateLimit.Unlock()

		// 5. Determine the agent type from the window name.
		// Window names follow the pattern agentType or agentType-N (e.g. kimi, kimi-2).
		agentType := agentName
		if idx := strings.LastIndex(agentName, "-"); idx != -1 {
			// Only strip the numeric suffix.
			suffix := agentName[idx+1:]
			allDigits := len(suffix) > 0
			for _, c := range suffix {
				if c < '0' || c > '9' {
					allDigits = false
					break
				}
			}
			if allDigits {
				agentType = agentName[:idx]
			}
		}

		spawnCmd, ok := agentSpawnCommands[agentType]
		if !ok {
			log.Printf("[Patrol:agent-recovery] unknown agent type %q for window %s — skipping restart",
				agentType, agentName)
			continue
		}

		// 6. Restart the agent in-place by sending the spawn command to the existing window.
		forgeAPIURL := os.Getenv("FORGE_API_URL")
		if forgeAPIURL == "" {
			forgeAPIURL = "http://prya:8081"
		}
		forgeRoot := findForgeRoot()
		envSetup := fmt.Sprintf("export FORGE_AGENT_TYPE=fleet && export FORGE_AGENT_NAME=%s && export FORGE_API_URL=%s && cd %s",
			agentName, forgeAPIURL, forgeRoot)
		fullRestartCmd := fmt.Sprintf("%s && %s", envSetup, spawnCmd)

		tmuxTarget := fmt.Sprintf("forge:%s", agentName)
		if err := exec.Command("tmux", "send-keys", "-t", tmuxTarget, "-l", fullRestartCmd).Run(); err != nil {
			log.Printf("[Patrol:agent-recovery] failed to send restart cmd to %s: %v", agentName, err)
			continue
		}
		if err := exec.Command("tmux", "send-keys", "-t", tmuxTarget, "", "Enter").Run(); err != nil {
			log.Printf("[Patrol:agent-recovery] failed to send Enter to %s: %v", agentName, err)
			continue
		}

		// 7. Re-dispatch the task: reset status to queued so taskDispatcherPatrol
		// picks it up, then immediately send it directly to this agent window.
		taskPrompt := fmt.Sprintf("Task %s: %s", taskID, taskTitle)
		time.Sleep(200 * time.Millisecond) // brief pause so the shell resets before we send the task
		if err := exec.Command("tmux", "send-keys", "-t", tmuxTarget, "-l", taskPrompt).Run(); err != nil {
			log.Printf("[Patrol:agent-recovery] failed to re-dispatch task %s to %s: %v", taskID, agentName, err)
		} else {
			time.Sleep(100 * time.Millisecond)
			_ = exec.Command("tmux", "send-keys", "-t", tmuxTarget, "", "Enter").Run()
		}

		// Keep the task marked as assigned to this agent (it is being re-delivered).
		_, _ = db.ExecContext(ctx, `
			UPDATE tasks SET status = 'assigned', state = 'DISPATCHED', updated_at = datetime('now')
			WHERE id = ?
		`, taskID)

		log.Printf("[Patrol:agent-recovery] recovered %s — restarted + re-dispatched %s", agentName, taskID)
	}

	return nil
}

// resultVerificationPatrol checks tasks stuck in 'assigned' or 'executing' status
// and resolves them by checking whether a result file exists on disk.
// Tasks with a result file → completed. Tasks >2h with no result → requeued.
func resultVerificationPatrol(ctx context.Context, db *sql.DB) error {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}

	rows, err := db.QueryContext(ctx, `
		SELECT id, title, assigned_to, status, updated_at FROM tasks
		WHERE status IN ('assigned', 'executing')
		AND updated_at < datetime('now', '-30 minutes')
		ORDER BY updated_at ASC LIMIT 10
	`)
	if err != nil {
		return fmt.Errorf("query stale tasks: %w", err)
	}
	defer rows.Close()

	type staleTask struct {
		ID, Title, AssignedTo, Status, UpdatedAt string
	}
	var tasks []staleTask
	for rows.Next() {
		var t staleTask
		if err := rows.Scan(&t.ID, &t.Title, &t.AssignedTo, &t.Status, &t.UpdatedAt); err != nil {
			continue
		}
		tasks = append(tasks, t)
	}

	for _, t := range tasks {
		// Check for result file.
		resultPath := filepath.Join(forgeRoot, ".forge", "heartbeat", "results",
			fmt.Sprintf("%s-%s.md", t.AssignedTo, t.ID))
		if _, err := os.Stat(resultPath); err == nil {
			// Result file exists — mark completed.
			_, _ = db.ExecContext(ctx, `
				UPDATE tasks SET status = 'completed', state = 'COMPLETED',
					updated_at = datetime('now') WHERE id = ?`, t.ID)
			log.Printf("[Patrol:result-verify] task %s completed (result file found)", t.ID)
			continue
		}

		// Parse task age.
		var updatedAt time.Time
		for _, layout := range []string{
			"2006-01-02 15:04:05",
			time.RFC3339,
			"2006-01-02T15:04:05Z",
			"2006-01-02 15:04:05.999999-07:00",
		} {
			if parsed, err := time.Parse(layout, t.UpdatedAt); err == nil {
				updatedAt = parsed
				break
			}
		}
		if updatedAt.IsZero() {
			continue
		}

		taskAge := time.Since(updatedAt)
		if taskAge > 2*time.Hour {
			// No result after 2h — requeue.
			_, _ = db.ExecContext(ctx, `
				UPDATE tasks SET status = 'queued', state = 'QUEUED',
					assigned_to = '', updated_at = datetime('now') WHERE id = ?`, t.ID)
			log.Printf("[Patrol:result-verify] task %s requeued (no result after 2h)", t.ID)
		}
		// Under 2h — agent may still be working, skip.
	}
	return nil
}

// killAgent terminates a tmux window and updates inventory.
func killAgent(ctx context.Context, db *sql.DB, agentID, tmuxWindow, reason string) {
	if tmuxWindow != "" {
		// Kill the tmux window.
		if err := exec.Command("tmux", "kill-window", "-t", fmt.Sprintf("forge:%s", tmuxWindow)).Run(); err != nil {
			log.Printf("[Patrol:fleet-auto-deflate] tmux kill-window error for %s: %v", tmuxWindow, err)
		}
	}

	// Update inventory.
	_, _ = db.ExecContext(ctx, `
		UPDATE agent_inventory
		SET status = 'offline',
		    drain_state = 'drained'
		WHERE agent_type = ?
	`, agentID)

	// Mark recommendation as executed (find matching deflate rec).
	_, _ = db.ExecContext(ctx, `
		UPDATE scale_recommendations
		SET status = 'executed', executed_at = datetime('now')
		WHERE agent_type = ? AND action = 'deflate' AND status IN ('processing', 'approved')
	`, agentID)

	recordScaleEvent()
	log.Printf("[Patrol:fleet-auto-deflate] KILLED agent %s (window=%s) reason=%s", agentID, tmuxWindow, reason)
}
