//go:build !tmux_bridge
// +build !tmux_bridge

// ADR-031: will move to plugin package when plugin system is implemented.

package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// OpenClawDispatchRequest is the payload for POST /api/openclaw/dispatch.
type OpenClawDispatchRequest struct {
	Agent    string `json:"agent"`
	Message  string `json:"message"`
	File     string `json:"file,omitempty"`
	Priority int    `json:"priority,omitempty"`
}

// OpenClawDispatchResponse is the response from POST /api/openclaw/dispatch.
type OpenClawDispatchResponse struct {
	TaskID  string `json:"task_id"`
	Agent   string `json:"agent"`
	Status  string `json:"status"`
	Message string `json:"message"`
}

// OpenClawNotifyRequest is the payload for POST /api/openclaw/notify.
type OpenClawNotifyRequest struct {
	Channel string `json:"channel"`
	Message string `json:"message"`
	Level   string `json:"level,omitempty"`
}

// OpenClawNotifyResponse is the response from POST /api/openclaw/notify.
type OpenClawNotifyResponse struct {
	OK      bool   `json:"ok"`
	Channel string `json:"channel"`
}

// portfolioProduct and portfolioState are defined in stage_gate.go.
// This file uses the extended version from there which includes
// ValidationHypothesis, KillCriteria, DistributionChannel fields.

// portfolioProductJSON is the JSON-enriched view for the portfolio endpoint.
type portfolioProductJSON struct {
	Key                  string  `yaml:"key"                  json:"key"`
	Name                 string  `yaml:"name"                 json:"name"`
	Domain               string  `yaml:"domain"               json:"domain"`
	RepoPath             string  `yaml:"repo_path"            json:"repo_path"`
	Stage                string  `yaml:"stage"                json:"stage"`
	Status               string  `yaml:"status"               json:"status"`
	Owner                string  `yaml:"owner"                json:"owner"`
	ICP                  string  `yaml:"icp"                  json:"icp"`
	CurrentMRR           float64 `yaml:"current_mrr"          json:"current_mrr"`
	TargetMRR            float64 `yaml:"target_mrr"           json:"target_mrr"`
	DeployReady          bool    `yaml:"deploy_ready"         json:"deploy_ready"`
	AnalyticsReady       bool    `yaml:"analytics_ready"      json:"analytics_ready"`
	BillingReady         bool    `yaml:"billing_ready"        json:"billing_ready"`
	NextGate             string  `yaml:"next_gate"            json:"next_gate"`
	NextAction           string  `yaml:"next_action"          json:"next_action"`
	PrimaryMetric        string  `yaml:"primary_metric"       json:"primary_metric"`
	PrimaryRisk          string  `yaml:"primary_risk"         json:"primary_risk"`
	KillCriteria         string  `yaml:"kill_criteria"        json:"kill_criteria,omitempty"`
	DistributionChannel  string  `yaml:"distribution_channel" json:"distribution_channel,omitempty"`
	ValidationHypothesis string  `yaml:"validation_hypothesis" json:"validation_hypothesis,omitempty"`
}

// portfolioStateJSON is the full YAML structure for JSON output.
type portfolioStateJSON struct {
	Version   string                 `yaml:"version"    json:"version"`
	Updated   string                 `yaml:"updated"    json:"updated"`
	NorthStar string                 `yaml:"north_star" json:"north_star"`
	Products  []portfolioProductJSON `yaml:"products"   json:"products"`
}

// openclawDispatchHandler handles POST /api/openclaw/dispatch.
// Creates a task in the queue and claims it for the specified agent.
func openclawDispatchHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSONError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	var req OpenClawDispatchRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSONError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	if strings.TrimSpace(req.Agent) == "" {
		writeJSONError(w, http.StatusBadRequest, "agent is required")
		return
	}
	if strings.TrimSpace(req.Message) == "" {
		writeJSONError(w, http.StatusBadRequest, "message is required")
		return
	}

	priority := req.Priority
	if priority <= 0 || priority > 10 {
		priority = 5
	}

	task := Task{
		ID:       generateTaskID(),
		Title:    req.Message,
		Domain:   "openclaw",
		Project:  "dispatch",
		Type:     TaskTypeFeature,
		Priority: priority,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}

	if taskQueue == nil {
		writeJSONError(w, http.StatusInternalServerError, "task queue not available")
		return
	}

	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		log.Printf("openclaw/dispatch: failed to enqueue task: %v", err)
		writeJSONError(w, http.StatusInternalServerError, "failed to create task")
		return
	}

	if err := taskQueue.ClaimTask(context.Background(), task.ID, req.Agent); err != nil {
		log.Printf("openclaw/dispatch: failed to claim task %s for agent %s: %v", task.ID, req.Agent, err)
		// Task was created; return partial success with the error surfaced.
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(OpenClawDispatchResponse{
			TaskID:  task.ID,
			Agent:   req.Agent,
			Status:  "error",
			Message: "task created but claim failed: " + err.Error(),
		})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(OpenClawDispatchResponse{
		TaskID:  task.ID,
		Agent:   req.Agent,
		Status:  "dispatched",
		Message: "task dispatched to " + req.Agent,
	})
}

// openclawNotifyHandler handles POST /api/openclaw/notify.
// Logs the notification and persists it to task_events.
//
// TODO: add Telegram/webhook channel integration when bot token is wired.
func openclawNotifyHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSONError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	var req OpenClawNotifyRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSONError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	if strings.TrimSpace(req.Message) == "" {
		writeJSONError(w, http.StatusBadRequest, "message is required")
		return
	}

	channel := strings.TrimSpace(req.Channel)
	if channel == "" {
		channel = "log"
	}

	level := strings.TrimSpace(req.Level)
	if level == "" {
		level = "info"
	}

	log.Printf("openclaw/notify [%s] [%s]: %s", channel, level, req.Message)

	db := getDBConn()
	if db != nil {
		payload, _ := json.Marshal(map[string]interface{}{
			"channel": channel,
			"level":   level,
			"message": req.Message,
		})
		_, err := db.Exec(
			`INSERT INTO task_events (task_id, domain, project, event_type, payload, created_at)
			 VALUES (?, ?, ?, ?, ?, ?)`,
			"openclaw-notify",
			"openclaw",
			"notify",
			"openclaw.notify",
			string(payload),
			time.Now().UTC().Format(time.RFC3339),
		)
		if err != nil {
			log.Printf("openclaw/notify: failed to persist event: %v", err)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(OpenClawNotifyResponse{
		OK:      true,
		Channel: channel,
	})
}

// openclawPortfolioHandler handles GET /api/openclaw/portfolio.
// Returns the full portfolio state from config/portfolio/portfolio-state.yaml as JSON,
// augmented with computed fields total_mrr and blocked_count.
func openclawPortfolioHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSONError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}

	yamlPath := filepath.Join(forgeRoot, "config", "portfolio", "portfolio-state.yaml")
	data, err := os.ReadFile(yamlPath)
	if err != nil {
		log.Printf("openclaw/portfolio: failed to read portfolio-state.yaml: %v", err)
		writeJSONError(w, http.StatusInternalServerError, "failed to read portfolio state")
		return
	}

	var state portfolioStateJSON
	if err := yaml.Unmarshal(data, &state); err != nil {
		log.Printf("openclaw/portfolio: failed to parse portfolio-state.yaml: %v", err)
		writeJSONError(w, http.StatusInternalServerError, "failed to parse portfolio state")
		return
	}

	// Compute derived fields.
	var totalMRR float64
	var blockedCount int
	for _, p := range state.Products {
		totalMRR += p.CurrentMRR
		if strings.TrimSpace(p.NextGate) != "" {
			blockedCount++
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"version":       state.Version,
		"updated":       state.Updated,
		"north_star":    state.NorthStar,
		"products":      state.Products,
		"total_mrr":     totalMRR,
		"blocked_count": blockedCount,
	})
}
