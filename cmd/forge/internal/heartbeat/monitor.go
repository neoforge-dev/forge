// Package heartbeat provides fleet monitoring capabilities
package heartbeat

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// Monitor watches agent heartbeats and detects stalls
type Monitor struct {
	agentsDir    string
	stallThreshold time.Duration
}

// NewMonitor creates a new heartbeat monitor
func NewMonitor() *Monitor {
	return &Monitor{
		agentsDir:      ".forge/heartbeat/agents",
		stallThreshold: 30 * time.Minute,
	}
}

// CheckAll reads all agent heartbeats and returns status
func (m *Monitor) CheckAll() ([]Heartbeat, error) {
	var heartbeats []Heartbeat

	entries, err := os.ReadDir(m.agentsDir)
	if err != nil {
		return nil, err
	}

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		hb, err := m.readHeartbeat(entry.Name())
		if err != nil {
			continue
		}

		heartbeats = append(heartbeats, hb)
	}

	return heartbeats, nil
}

// readHeartbeat reads a single agent's heartbeat
func (m *Monitor) readHeartbeat(agentID string) (Heartbeat, error) {
	var hb Heartbeat

	path := filepath.Join(m.agentsDir, agentID, "heartbeat.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return hb, err
	}

	err = json.Unmarshal(data, &hb)
	return hb, err
}

// DetectStalls returns agents that haven't reported recently
func (m *Monitor) DetectStalls() ([]Heartbeat, error) {
	var stalls []Heartbeat

	heartbeats, err := m.CheckAll()
	if err != nil {
		return nil, err
	}

	now := time.Now()
	for _, hb := range heartbeats {
		if now.Sub(hb.LastActivity) > m.stallThreshold {
			stalls = append(stalls, hb)
		}
	}

	return stalls, nil
}

// PrintDashboard displays fleet status
func (m *Monitor) PrintDashboard() {
	heartbeats, err := m.CheckAll()
	if err != nil {
		fmt.Printf("Error reading heartbeats: %v\n", err)
		return
	}

	fmt.Println("╔════════════════════════════════════════════════════════════╗")
	fmt.Println("║                    FLEET DASHBOARD                         ║")
	fmt.Println("╠════════════════════════════════════════════════════════════╣")
	fmt.Printf("║ %-12s │ %-10s │ %-20s │ %-8s ║\n", "AGENT", "STATUS", "TASK", "SEEN")
	fmt.Println("╠════════════════════════════════════════════════════════════╣")

	now := time.Now()
	for _, hb := range heartbeats {
		ago := now.Sub(hb.LastActivity).Round(time.Minute)
		status := string(hb.Status)
		if ago > m.stallThreshold {
			status += " ⚠️"
		}

		task := hb.CurrentTask
		if len(task) > 18 {
			task = task[:15] + "..."
		}

		fmt.Printf("║ %-12s │ %-10s │ %-20s │ %-8s ║\n",
			hb.AgentID,
			status,
			task,
			ago,
		)
	}

	fmt.Println("╚════════════════════════════════════════════════════════════╝")
}
