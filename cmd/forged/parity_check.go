//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// ParityResult represents the comparison between v2 and v3 state
type ParityResult struct {
	Timestamp   int64       `json:"timestamp"`
	TasksMatch  bool        `json:"tasks_match"`
	TasksV2     int         `json:"tasks_v2"`
	TasksV3     int         `json:"tasks_v3"`
	TasksDiff   int         `json:"tasks_diff"`
	AgentsMatch bool        `json:"agents_match"`
	AgentsV2    int         `json:"agents_v2"`
	AgentsV3    int         `json:"agents_v3"`
	AgentsDiff  int         `json:"agents_diff"`
	Overall     bool        `json:"overall_match"`
	Errors      []string    `json:"errors,omitempty"`
}

// ParityChecker performs state comparison between v2 and v3
type ParityChecker struct {
	v2StatePath string
	v3DBPath    string
}

// NewParityChecker creates a new parity checker
func NewParityChecker(v2Path, v3DBPath string) *ParityChecker {
	if v2Path == "" {
		v2Path = ".forge"
	}
	if v3DBPath == "" {
		v3DBPath = ".forge/forge-v3.db"
	}
	return &ParityChecker{
		v2StatePath: v2Path,
		v3DBPath:    v3DBPath,
	}
}

// Check performs the parity check between v2 and v3 state
func (pc *ParityChecker) Check() (*ParityResult, error) {
	result := &ParityResult{
		Timestamp: timeNow(),
		Errors:    []string{},
	}

	// Count v2 tasks (JSON files in .forge/tasks/)
	tasksV2, err := pc.countV2Tasks()
	if err != nil {
		result.Errors = append(result.Errors, fmt.Sprintf("v2 tasks count failed: %v", err))
	}
	result.TasksV2 = tasksV2

	// Count v3 tasks (SQLite)
	tasksV3, err := pc.countV3Tasks()
	if err != nil {
		result.Errors = append(result.Errors, fmt.Sprintf("v3 tasks count failed: %v", err))
	}
	result.TasksV3 = tasksV3
	result.TasksDiff = tasksV3 - tasksV2
	result.TasksMatch = tasksV2 == tasksV3

	// Count v2 agents (from fleet/state.json)
	agentsV2, err := pc.countV2Agents()
	if err != nil {
		result.Errors = append(result.Errors, fmt.Sprintf("v2 agents count failed: %v", err))
	}
	result.AgentsV2 = agentsV2

	// Count v3 agents (SQLite)
	agentsV3, err := pc.countV3Agents()
	if err != nil {
		result.Errors = append(result.Errors, fmt.Sprintf("v3 agents count failed: %v", err))
	}
	result.AgentsV3 = agentsV3
	result.AgentsDiff = agentsV3 - agentsV2
	result.AgentsMatch = agentsV2 == agentsV3

	// Overall match
	result.Overall = result.TasksMatch && result.AgentsMatch && len(result.Errors) == 0

	return result, nil
}

// countV2Tasks counts task files in v2 (.forge/tasks/*.json)
func (pc *ParityChecker) countV2Tasks() (int, error) {
	tasksDir := filepath.Join(pc.v2StatePath, "tasks")
	files, err := filepath.Glob(filepath.Join(tasksDir, "*.json"))
	if err != nil {
		return 0, err
	}
	return len(files), nil
}

// countV3Tasks counts tasks in v3 SQLite
func (pc *ParityChecker) countV3Tasks() (int, error) {
	db, err := sql.Open("sqlite3", pc.v3DBPath)
	if err != nil {
		return 0, err
	}
	defer db.Close()

	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM tasks").Scan(&count)
	return count, err
}

// countV2Agents counts agents from v2 fleet/state.json
func (pc *ParityChecker) countV2Agents() (int, error) {
	stateFile := filepath.Join(pc.v2StatePath, "fleet", "state.json")
	data, err := os.ReadFile(stateFile)
	if err != nil {
		if os.IsNotExist(err) {
			return 0, nil // No state file = 0 agents
		}
		return 0, err
	}

	var state struct {
		Agents []interface{} `json:"agents"`
	}
	if err := json.Unmarshal(data, &state); err != nil {
		return 0, err
	}
	return len(state.Agents), nil
}

// countV3Agents counts agents in v3 SQLite
func (pc *ParityChecker) countV3Agents() (int, error) {
	db, err := sql.Open("sqlite3", pc.v3DBPath)
	if err != nil {
		return 0, err
	}
	defer db.Close()

	// Check if agents table exists
	var exists int
	err = db.QueryRow("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='agents'").Scan(&exists)
	if err != nil || exists == 0 {
		return 0, nil // No agents table = 0 agents
	}

	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM agents").Scan(&count)
	return count, err
}

// ParityCheck performs a parity check using default paths
func ParityCheck(db *sql.DB) (*ParityResult, error) {
	checker := NewParityChecker(".forge", ".forge/forge-v3.db")
	return checker.Check()
}

// timeNow returns current Unix timestamp
func timeNow() int64 {
	return time.Now().Unix()
}
