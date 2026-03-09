// Package heartbeat provides agent status reporting for fleet monitoring
package heartbeat

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"time"
)

// Status represents agent's current state
type Status string

const (
	StatusIdle     Status = "idle"
	StatusWorking  Status = "working"
	StatusBlocked  Status = "blocked"
	StatusError    Status = "error"
	StatusComplete Status = "complete"
)

// Heartbeat represents a single agent status report
type Heartbeat struct {
	AgentID       string    `json:"agent_id"`
	Node          string    `json:"node"`
	Status        Status    `json:"status"`
	CurrentTask   string    `json:"current_task,omitempty"`
	Progress      int       `json:"progress"` // 0-100
	LastActivity  time.Time `json:"last_activity"`
	BlockedReason string    `json:"blocked_reason,omitempty"`
	ContextPct    float64   `json:"context_pct,omitempty"`
}

// Reporter sends heartbeats to daemon
type Reporter struct {
	agentID   string
	node      string
	serverURL string
	client    *http.Client
}

// NewReporter creates a new heartbeat reporter
func NewReporter(agentID, node string) *Reporter {
	url := os.Getenv("FORGE_SERVER_URL")
	if url == "" {
		url = "http://localhost:8081"
	}

	return &Reporter{
		agentID:   agentID,
		node:      node,
		serverURL: url,
		client:    &http.Client{Timeout: 5 * time.Second},
	}
}

// Send reports current status to server
func (r *Reporter) Send(status Status, task string, progress int) error {
	heartbeat := Heartbeat{
		AgentID:      r.agentID,
		Node:         r.node,
		Status:       status,
		CurrentTask:  task,
		Progress:     progress,
		LastActivity: time.Now(),
	}

	// Write to file-based heartbeat for monitoring
	return r.writeToFile(heartbeat)
}

// writeToFile writes heartbeat to local file for monitoring
func (r *Reporter) writeToFile(hb Heartbeat) error {
	dir := fmt.Sprintf(".forge/heartbeat/agents/%s", r.agentID)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}

	path := fmt.Sprintf("%s/heartbeat.json", dir)
	data, err := json.MarshalIndent(hb, "", "  ")
	if err != nil {
		return err
	}

	return os.WriteFile(path, data, 0644)
}

// Start begins periodic heartbeat reporting
func (r *Reporter) Start(interval time.Duration) {
	ticker := time.NewTicker(interval)
	go func() {
		for range ticker.C {
			// Send idle heartbeat if no other status set
			r.Send(StatusIdle, "", 0)
		}
	}()
}
