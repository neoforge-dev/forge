//go:build !tmux_bridge
// +build !tmux_bridge

package main

// ADR-027: Cross-node metric aggregation handlers.
//
// POST /api/nodes/{nodeID}/metrics  — worker nodes push their local metric
//   summaries to the lead (prya). Each metric is stored in the metrics table
//   with a source_node label so the dashboard can disaggregate per node.
//
// GET  /api/fleet/metrics           — aggregates the most-recent value per
//   (metric_name, source_node) pair and returns a fleet-level summary keyed
//   by node ID.

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)


// NodeMetricPayload is the body accepted by POST /api/nodes/{nodeID}/metrics.
// A worker node collects its local metric rollups and sends them in one batch.
type NodeMetricPayload struct {
	NodeID    string              `json:"node_id"`
	Period    string              `json:"period"`     // "1m", "5m", "1h"
	Timestamp string              `json:"timestamp"`  // RFC3339, optional — server uses now() if absent
	Metrics   []NodeMetricSample  `json:"metrics"`
}

// NodeMetricSample is a single metric name+value pair within a payload.
type NodeMetricSample struct {
	Name   string            `json:"name"`
	Value  float64           `json:"value"`
	Labels map[string]string `json:"labels,omitempty"`
}

// FleetMetricsResponse is returned by GET /api/fleet/metrics.
type FleetMetricsResponse struct {
	GeneratedAt string                       `json:"generated_at"`
	NodeCount   int                          `json:"node_count"`
	Nodes       map[string][]FleetMetricRow  `json:"nodes"`
}

// FleetMetricRow is one aggregated metric entry for a node.
type FleetMetricRow struct {
	MetricName string  `json:"metric_name"`
	Value      float64 `json:"value"`
	Period     string  `json:"period"`
	ComputedAt string  `json:"computed_at"`
}

// nodeMetricsReceiveHandler handles POST /api/nodes/{nodeID}/metrics.
// It persists each sample into the metrics table with a "source_node" label
// injected so fleet queries can group-by node.
func nodeMetricsReceiveHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Extract {nodeID} from path: /api/nodes/<nodeID>/metrics
	pathNodeID := strings.TrimPrefix(r.URL.Path, "/api/nodes/")
	pathNodeID = strings.TrimSuffix(pathNodeID, "/metrics")
	pathNodeID = sanitizeID(pathNodeID)
	if pathNodeID == "" {
		http.Error(w, "node ID required", http.StatusBadRequest)
		return
	}

	var payload NodeMetricPayload
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		http.Error(w, "invalid JSON body: "+err.Error(), http.StatusBadRequest)
		return
	}

	// Body node_id must match path if provided.
	if payload.NodeID != "" && payload.NodeID != pathNodeID {
		http.Error(w, "node_id mismatch between path and body", http.StatusBadRequest)
		return
	}
	if payload.NodeID == "" {
		payload.NodeID = pathNodeID
	}

	period := payload.Period
	if period == "" {
		period = "1m"
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "database not available", http.StatusServiceUnavailable)
		return
	}

	ctx := r.Context()
	stored := 0

	for _, sample := range payload.Metrics {
		if sample.Name == "" {
			continue
		}

		// Merge source_node into the per-sample labels before serialising.
		merged := make(map[string]string, len(sample.Labels)+1)
		for k, v := range sample.Labels {
			merged[k] = v
		}
		merged["source_node"] = payload.NodeID

		labelsJSON, err := json.Marshal(merged)
		if err != nil {
			log.Printf("[node-metrics] label marshal error for %s/%s: %v", payload.NodeID, sample.Name, err)
			continue
		}

		_, err = db.ExecContext(ctx,
			`INSERT INTO metrics (metric_name, value, labels, period, computed_at)
			 VALUES (?, ?, ?, ?, datetime('now'))`,
			sample.Name, sample.Value, string(labelsJSON), period,
		)
		if err != nil {
			log.Printf("[node-metrics] insert error for %s/%s: %v", payload.NodeID, sample.Name, err)
			continue
		}
		stored++
	}

	log.Printf("[node-metrics] received %d samples from node %s (%d stored)", len(payload.Metrics), payload.NodeID, stored)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":      "ok",
		"node_id":     payload.NodeID,
		"samples_in":  len(payload.Metrics),
		"stored":      stored,
		"received_at": time.Now().UTC().Format(time.RFC3339),
	})
}

// fleetMetricsHandler handles GET /api/fleet/metrics.
// It returns the most-recent value per (metric_name, source_node) pair from
// the last 10 minutes so stale pushes from offline nodes are automatically
// excluded from the summary.
func fleetMetricsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "database not available", http.StatusServiceUnavailable)
		return
	}

	// Pull the latest metric per (metric_name, source_node) within the last 10 minutes.
	// json_extract is available in SQLite 3.9+ (2015) — prya runs a modern SQLite.
	// All column references are explicitly qualified to avoid "ambiguous column name" errors
	// when SQLite sees both the outer metrics table and the subquery sharing column names.
	rows, err := db.QueryContext(r.Context(), `
		SELECT
			json_extract(m.labels, '$.source_node') AS source_node,
			m.metric_name,
			m.value,
			m.period,
			m.computed_at
		FROM metrics m
		INNER JOIN (
			SELECT
				json_extract(labels, '$.source_node') AS sn,
				metric_name                           AS mn,
				MAX(computed_at)                      AS max_at
			FROM metrics
			WHERE computed_at > datetime('now', '-10 minutes')
			  AND json_extract(labels, '$.source_node') IS NOT NULL
			GROUP BY sn, mn
		) latest
		  ON json_extract(m.labels, '$.source_node') = latest.sn
		 AND m.metric_name = latest.mn
		 AND m.computed_at = latest.max_at
		ORDER BY source_node, m.metric_name
	`)
	if err != nil {
		http.Error(w, "query failed: "+err.Error(), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	nodeMetrics := make(map[string][]FleetMetricRow)

	for rows.Next() {
		var sourceNode, metricName, period, computedAt string
		var value float64
		if err := rows.Scan(&sourceNode, &metricName, &value, &period, &computedAt); err != nil {
			continue
		}
		nodeMetrics[sourceNode] = append(nodeMetrics[sourceNode], FleetMetricRow{
			MetricName: metricName,
			Value:      value,
			Period:     period,
			ComputedAt: computedAt,
		})
	}
	if err := rows.Err(); err != nil {
		http.Error(w, "row iteration error: "+err.Error(), http.StatusInternalServerError)
		return
	}

	resp := FleetMetricsResponse{
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		NodeCount:   len(nodeMetrics),
		Nodes:       nodeMetrics,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// --- Fleet aggregate endpoint (ADR-027) ---

// FleetNodeStatus is the per-node summary returned by GET /api/fleet/aggregate.
type FleetNodeStatus struct {
	NodeID        string `json:"node_id"`
	Address       string `json:"address"`
	Status        string `json:"status"`   // "online" | "offline" | "unreachable"
	Reachable     bool   `json:"reachable"`
	ActiveAgents  int    `json:"active_agents"`
	QueuedTasks   int    `json:"queued_tasks"`
	RunningTasks  int    `json:"running_tasks"`
	DaemonVersion string `json:"daemon_version,omitempty"`
	ProbeLatencyMs int64 `json:"probe_latency_ms"`
	ProbedAt      string `json:"probed_at"`
}

// FleetAggregateResponse is returned by GET /api/fleet/aggregate.
type FleetAggregateResponse struct {
	GeneratedAt   string            `json:"generated_at"`
	TotalNodes    int               `json:"total_nodes"`
	OnlineNodes   int               `json:"online_nodes"`
	TotalAgents   int               `json:"total_agents"`
	TotalQueued   int               `json:"total_queued"`
	TotalRunning  int               `json:"total_running"`
	Nodes         []FleetNodeStatus `json:"nodes"`
}

// fleetAggregateHandler handles GET /api/fleet/aggregate.
// It fans out to every registered node's /health and /api/agents/health endpoints
// concurrently (3 s timeout per node) and returns a unified fleet summary.
func fleetAggregateHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "database not available", http.StatusServiceUnavailable)
		return
	}

	// Load all known nodes from the registry.
	type nodeRec struct {
		ID      string
		Address string
		Status  string
	}
	rows, err := db.QueryContext(r.Context(),
		`SELECT id, address, status FROM nodes ORDER BY id`)
	if err != nil {
		http.Error(w, "node query failed: "+err.Error(), http.StatusInternalServerError)
		return
	}
	var nodeRecs []nodeRec
	for rows.Next() {
		var n nodeRec
		if err := rows.Scan(&n.ID, &n.Address, &n.Status); err == nil {
			nodeRecs = append(nodeRecs, n)
		}
	}
	rows.Close()

	// Include self if not already in the DB (single-node deployment).
	selfID := os.Getenv("NODE_ID")
	if selfID == "" {
		selfID, _ = os.Hostname()
	}
	selfInList := false
	for _, n := range nodeRecs {
		if n.ID == selfID {
			selfInList = true
			break
		}
	}
	if !selfInList {
		selfPort := os.Getenv("PORT")
		if selfPort == "" {
			selfPort = "8081"
		}
		nodeRecs = append([]nodeRec{{ID: selfID, Address: "http://localhost:" + selfPort, Status: "online"}}, nodeRecs...)
	}

	apiKey := os.Getenv("FORGE_API_KEY")
	client := &http.Client{Timeout: 3 * time.Second}

	type probeResult struct {
		idx    int
		status FleetNodeStatus
	}
	results := make(chan probeResult, len(nodeRecs))

	// probe calls /health and /api/agents/health on a single node.
	probe := func(idx int, n nodeRec) {
		s := FleetNodeStatus{
			NodeID:   n.ID,
			Address:  n.Address,
			Status:   n.Status,
			ProbedAt: time.Now().UTC().Format(time.RFC3339),
		}
		if n.Address == "" {
			results <- probeResult{idx, s}
			return
		}
		base := strings.TrimRight(n.Address, "/")

		// Probe /health
		t0 := time.Now()
		req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, base+"/health", nil)
		if err == nil && apiKey != "" {
			req.Header.Set("Authorization", "Bearer "+apiKey)
		}
		var healthResp struct {
			Status  string `json:"status"`
			Version string `json:"version"`
		}
		if err == nil {
			if resp, err2 := client.Do(req); err2 == nil {
				s.Reachable = true
				s.Status = "online"
				s.ProbeLatencyMs = time.Since(t0).Milliseconds()
				_ = json.NewDecoder(resp.Body).Decode(&healthResp)
				resp.Body.Close()
				s.DaemonVersion = healthResp.Version
			} else {
				s.Status = "unreachable"
			}
		} else {
			s.Status = "unreachable"
		}

		// Probe /api/agents/health for agent counts (best-effort).
		if s.Reachable {
			req2, err2 := http.NewRequestWithContext(r.Context(), http.MethodGet, base+"/api/agents/health", nil)
			if err2 == nil && apiKey != "" {
				req2.Header.Set("Authorization", "Bearer "+apiKey)
			}
			if err2 == nil {
				if resp2, err3 := client.Do(req2); err3 == nil {
					var agentPayload struct {
						Agents []struct {
							Status string `json:"status"`
						} `json:"agents"`
					}
					if json.NewDecoder(resp2.Body).Decode(&agentPayload) == nil {
						for _, a := range agentPayload.Agents {
							if a.Status == "online" || a.Status == "connected" {
								s.ActiveAgents++
							}
						}
					}
					resp2.Body.Close()
				}
			}

			// Probe /api/tasks for queue depth (best-effort).
			req3, err3 := http.NewRequestWithContext(r.Context(), http.MethodGet, base+"/api/tasks?limit=0", nil)
			if err3 == nil && apiKey != "" {
				req3.Header.Set("Authorization", "Bearer "+apiKey)
			}
			if err3 == nil {
				if resp3, err4 := client.Do(req3); err4 == nil {
					var taskPayload struct {
						Tasks []struct {
							Status string `json:"status"`
						} `json:"tasks"`
					}
					if json.NewDecoder(resp3.Body).Decode(&taskPayload) == nil {
						for _, t := range taskPayload.Tasks {
							switch t.Status {
							case "queued", "requested":
								s.QueuedTasks++
							case "assigned", "executing":
								s.RunningTasks++
							}
						}
					}
					resp3.Body.Close()
				}
			}
		}

		results <- probeResult{idx, s}
	}

	for i, n := range nodeRecs {
		go probe(i, n)
	}

	statuses := make([]FleetNodeStatus, len(nodeRecs))
	for range nodeRecs {
		pr := <-results
		statuses[pr.idx] = pr.status
	}

	resp := FleetAggregateResponse{
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		TotalNodes:  len(statuses),
		Nodes:       statuses,
	}
	for _, s := range statuses {
		if s.Reachable {
			resp.OnlineNodes++
			resp.TotalAgents += s.ActiveAgents
			resp.TotalQueued += s.QueuedTasks
			resp.TotalRunning += s.RunningTasks
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// nodeMetricsPushPatrol collects this node's local metric rollups and POSTs
// them to the lead node's /api/nodes/{nodeID}/metrics endpoint (ADR-027).
//
// Triggered by: the "node-metrics-push" entry in StandardPatrols (patrol.go).
// Env vars:
//
//	FORGE_LEAD_URL        — base URL of the lead daemon (e.g. http://prya:8081).
//	                        If empty this is the lead — patrol is a no-op.
//	NODE_ID               — identity of this node (default: hostname).
//	FORGE_API_KEY         — bearer token for the lead's auth middleware.
func nodeMetricsPushPatrol(ctx context.Context, db *sql.DB) error {
	leadURL := os.Getenv("FORGE_LEAD_URL")
	if leadURL == "" {
		// No lead URL configured — this node IS the lead; nothing to push.
		return nil
	}

	nodeID := os.Getenv("NODE_ID")
	if nodeID == "" {
		if h, err := os.Hostname(); err == nil {
			nodeID = h
		} else {
			nodeID = "unknown"
		}
	}

	// Collect agent count from agent_heartbeats table.
	var agentCount float64
	row := db.QueryRowContext(ctx, `SELECT COUNT(*) FROM agent_heartbeats WHERE last_seen > datetime('now', '-5 minutes')`)
	if err := row.Scan(&agentCount); err != nil {
		log.Printf("[Patrol:node-metrics-push] agent_heartbeats query failed: %v — using 0", err)
		agentCount = 0
	}

	// Collect task count from tasks table (active tasks only).
	var taskCount float64
	row = db.QueryRowContext(ctx, `SELECT COUNT(*) FROM tasks WHERE status NOT IN ('completed', 'abandoned', 'failed')`)
	if err := row.Scan(&taskCount); err != nil {
		log.Printf("[Patrol:node-metrics-push] tasks query failed: %v — using 0", err)
		taskCount = 0
	}

	samples := []NodeMetricSample{
		{Name: "agent_count", Value: agentCount},
		{Name: "task_count", Value: taskCount},
	}

	// Also collect the latest 1-minute rollup per metric from the last 2 minutes,
	// excluding rows already carrying a source_node label (pushed by other nodes).
	rows, err := db.QueryContext(ctx, `
		SELECT metric_name, value, labels
		FROM metrics
		WHERE period = '1m'
		  AND computed_at > datetime('now', '-2 minutes')
		  AND (labels IS NULL OR labels = '{}' OR json_extract(labels, '$.source_node') IS NULL)
		ORDER BY computed_at DESC
	`)
	if err != nil {
		log.Printf("[Patrol:node-metrics-push] local metrics query failed: %v", err)
	} else {
		defer rows.Close()
		seen := make(map[string]bool)
		seen["agent_count"] = true
		seen["task_count"] = true
		for rows.Next() {
			var name, labelsJSON string
			var value float64
			if err := rows.Scan(&name, &value, &labelsJSON); err != nil {
				continue
			}
			if seen[name] {
				continue
			}
			seen[name] = true
			var labels map[string]string
			_ = json.Unmarshal([]byte(labelsJSON), &labels)
			samples = append(samples, NodeMetricSample{Name: name, Value: value, Labels: labels})
		}
		if err := rows.Err(); err != nil {
			log.Printf("[Patrol:node-metrics-push] row iteration error: %v", err)
		}
	}

	payload := NodeMetricPayload{
		NodeID:    nodeID,
		Period:    "1m",
		Timestamp: time.Now().UTC().Format(time.RFC3339),
		Metrics:   samples,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("node-metrics-push: marshal payload: %w", err)
	}

	url := strings.TrimRight(leadURL, "/") + "/api/nodes/" + nodeID + "/metrics"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("node-metrics-push: build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if tok := os.Getenv("FORGE_API_KEY"); tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}

	httpClient := &http.Client{Timeout: 10 * time.Second}
	resp, err := httpClient.Do(req)
	if err != nil {
		// Soft-fail — a connectivity blip must not crash the patrol loop.
		log.Printf("[Patrol:node-metrics-push] POST %s failed: %v", url, err)
		return nil
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		log.Printf("[Patrol:node-metrics-push] lead returned HTTP %d for node %s", resp.StatusCode, nodeID)
	} else {
		log.Printf("[Patrol:node-metrics-push] pushed %d metrics from node %s to lead %s", len(samples), nodeID, leadURL)
	}
	return nil
}
