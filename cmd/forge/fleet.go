// fleet.go - V4-011 Fleet Workflow Commands
//
// Implements fleet management commands:
//   - forge fleet status     : Display fleet overview
//   - forge fleet health     : Detailed health metrics
//   - forge fleet agents     : List all agents
//   - forge fleet broadcast  : Send messages to agents
//   - forge fleet pause-all  : Pause all active agents
//   - forge fleet resume-all : Resume all paused agents

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// AgentState represents the state of an agent
type AgentState string

const (
	StateIdle       AgentState = "idle"
	StateAssigned   AgentState = "assigned"
	StateWorking    AgentState = "working"
	StatePaused     AgentState = "paused"
	StateCompleted  AgentState = "completed"
	StateFailed     AgentState = "failed"
	StateRecovering AgentState = "recovering"
	StateRestarting AgentState = "restarting"
	StateTerminated AgentState = "terminated"
)

// Agent represents a fleet agent
type FleetAgent struct {
	ID               string                 `json:"agent_id"`
	State            AgentState             `json:"state"`
	CurrentTask      string                 `json:"current_task,omitempty"`
	CurrentWorkflow  string                 `json:"current_workflow,omitempty"`
	ContextPercent   int                    `json:"context_percentage"`
	LastHeartbeat    *time.Time             `json:"last_heartbeat,omitempty"`
	LastStateChange  time.Time              `json:"last_state_change"`
	Capabilities     []string               `json:"capabilities"`
	Metadata         map[string]interface{} `json:"metadata"`
	StateHistory     []StateTransition      `json:"state_history"`
}

// StateTransition records a state change
type StateTransition struct {
	From      string    `json:"from"`
	To        string    `json:"to"`
	Timestamp time.Time `json:"timestamp"`
	Reason    string    `json:"reason,omitempty"`
}

// FleetSummary provides fleet-wide status
type FleetSummary struct {
	TotalAgents int                       `json:"total_agents"`
	ByState     map[string]int            `json:"by_state"`
	Available   int                       `json:"available"`
	Working     int                       `json:"working"`
	Failed      int                       `json:"failed"`
	Agents      []FleetAgent              `json:"agents"`
	Health      FleetHealthReport         `json:"health"`
	GeneratedAt time.Time                 `json:"generated_at"`
}

// FleetHealthReport contains health metrics
type FleetHealthReport struct {
	Healthy    int            `json:"healthy"`
	Stale      int            `json:"stale"`
	Overloaded int            `json:"overloaded"`
	Issues     []FleetIssue   `json:"issues,omitempty"`
}

// FleetIssue represents a health issue
type FleetIssue struct {
	AgentID string `json:"agent_id"`
	Type    string `json:"type"`
	Details string `json:"details"`
}

// BroadcastRecord tracks a broadcast message
type BroadcastRecord struct {
	Timestamp   time.Time `json:"timestamp"`
	Type        string    `json:"type"`
	Message     string    `json:"message"`
	TargetCount int       `json:"target_count"`
	Targets     []string  `json:"targets"`
}

// FleetStateManager handles fleet operations
type FleetStateManager struct {
	StateDir string
}

// Type aliases for test compatibility (fleet_test.go expected FleetManager/Agent from workflow_fleet)
type FleetManager = FleetStateManager
type Agent = FleetAgent

// NewFleetStateManager creates a new fleet state manager
func NewFleetStateManager() *FleetStateManager {
	stateDir := getFleetStateDir()
	return &FleetStateManager{StateDir: stateDir}
}

func getFleetStateDir() string {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		dir, _ := os.Getwd()
		for dir != "/" && dir != "." {
			if _, err := os.Stat(filepath.Join(dir, ".forge")); err == nil {
				forgeRoot = dir
				break
			}
			parent := filepath.Dir(dir)
			if parent == dir {
				break
			}
			dir = parent
		}
	}
	if forgeRoot == "" {
		forgeRoot, _ = os.Getwd()
	}
	return filepath.Join(forgeRoot, ".forge", "workflow", "state")
}

// GetAgents loads all agents from state directory
func (fsm *FleetStateManager) GetAgents() ([]FleetAgent, error) {
	agents := []FleetAgent{}
	
	entries, err := os.ReadDir(fsm.StateDir)
	if err != nil {
		if os.IsNotExist(err) {
			return agents, nil
		}
		return nil, err
	}
	
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		
		path := filepath.Join(fsm.StateDir, entry.Name())
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		
		var agent FleetAgent
		if err := json.Unmarshal(data, &agent); err != nil {
			continue
		}
		agents = append(agents, agent)
	}
	
	return agents, nil
}

// GetAgent loads a specific agent
func (fsm *FleetStateManager) GetAgent(id string) (*FleetAgent, error) {
	path := filepath.Join(fsm.StateDir, id+".json")
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	
	var agent FleetAgent
	if err := json.Unmarshal(data, &agent); err != nil {
		return nil, err
	}
	return &agent, nil
}

// SaveAgent saves an agent to state directory
func (fsm *FleetStateManager) SaveAgent(agent *FleetAgent) error {
	if err := os.MkdirAll(fsm.StateDir, 0755); err != nil {
		return err
	}
	
	path := filepath.Join(fsm.StateDir, agent.ID+".json")
	data, err := json.MarshalIndent(agent, "", "  ")
	if err != nil {
		return err
	}
	
	return os.WriteFile(path, data, 0644)
}

// GetSummary generates fleet summary
func (fsm *FleetStateManager) GetSummary() (*FleetSummary, error) {
	agents, err := fsm.GetAgents()
	if err != nil {
		return nil, err
	}
	
	summary := &FleetSummary{
		TotalAgents: len(agents),
		ByState:     make(map[string]int),
		Agents:      agents,
		GeneratedAt: time.Now().UTC(),
	}
	
	now := time.Now().UTC()
	staleThreshold := 30 * time.Minute
	
	for _, agent := range agents {
		summary.ByState[string(agent.State)]++
		
		if agent.State == StateWorking {
			summary.Working++
		}
		
		if agent.State == StateIdle || agent.State == StateCompleted {
			summary.Available++
		}
		
		if agent.State == StateFailed {
			summary.Failed++
		}
		
		// Check health
		isStale := false
		if agent.LastHeartbeat != nil {
			if now.Sub(*agent.LastHeartbeat) > staleThreshold {
				isStale = true
				summary.Health.Stale++
			}
		}
		
		if agent.ContextPercent > 60 {
			summary.Health.Overloaded++
		}
		
		if !isStale && agent.State != StateFailed && agent.State != StateTerminated {
			summary.Health.Healthy++
		}
		
		// Record issues
		if isStale {
			summary.Health.Issues = append(summary.Health.Issues, FleetIssue{
				AgentID: agent.ID,
				Type:    "stale",
				Details: "No heartbeat for >30 minutes",
			})
		}
		if agent.ContextPercent > 60 {
			summary.Health.Issues = append(summary.Health.Issues, FleetIssue{
				AgentID: agent.ID,
				Type:    "overloaded",
				Details: fmt.Sprintf("Context at %d%%", agent.ContextPercent),
			})
		}
	}
	
	return summary, nil
}

// Broadcast sends a message to agents
func (fsm *FleetStateManager) Broadcast(message, msgType string, filterState, filterCapability string) (*BroadcastRecord, error) {
	agents, err := fsm.GetAgents()
	if err != nil {
		return nil, err
	}
	
	// Filter agents
	var targets []FleetAgent
	for _, agent := range agents {
		if filterState != "" && string(agent.State) != filterState {
			continue
		}
		if filterCapability != "" && !containsString(agent.Capabilities, filterCapability) {
			continue
		}
		targets = append(targets, agent)
	}
	
	if len(targets) == 0 {
		return &BroadcastRecord{
			Timestamp:   time.Now().UTC(),
			Type:        msgType,
			Message:     message,
			TargetCount: 0,
			Targets:     []string{},
		}, nil
	}
	
	// Record broadcast in agent metadata
	now := time.Now().UTC()
	for i := range targets {
		if targets[i].Metadata == nil {
			targets[i].Metadata = make(map[string]interface{})
		}
		
		broadcasts, _ := targets[i].Metadata["broadcasts"].([]interface{})
		broadcasts = append(broadcasts, map[string]interface{}{
			"type":    msgType,
			"message": message,
			"time":    now.Format(time.RFC3339),
		})
		targets[i].Metadata["broadcasts"] = broadcasts
		
		fsm.SaveAgent(&targets[i])
	}
	
	targetIDs := make([]string, len(targets))
	for i, a := range targets {
		targetIDs[i] = a.ID
	}
	
	return &BroadcastRecord{
		Timestamp:   now,
		Type:        msgType,
		Message:     message,
		TargetCount: len(targets),
		Targets:     targetIDs,
	}, nil
}

// PauseAll pauses all working/assigned agents
func (fsm *FleetStateManager) PauseAll(reason string) (int, int, error) {
	agents, err := fsm.GetAgents()
	if err != nil {
		return 0, 0, err
	}
	
	paused := 0
	targets := 0
	
	for i := range agents {
		if agents[i].State == StateWorking || agents[i].State == StateAssigned {
			targets++
			if fsm.transitionAgent(&agents[i], StatePaused, reason) {
				paused++
			}
		}
	}
	
	return paused, targets, nil
}

// ResumeAll resumes all paused agents
func (fsm *FleetStateManager) ResumeAll(reason string) (int, int, error) {
	agents, err := fsm.GetAgents()
	if err != nil {
		return 0, 0, err
	}
	
	resumed := 0
	targets := 0
	
	for i := range agents {
		if agents[i].State == StatePaused {
			targets++
			if fsm.transitionAgent(&agents[i], StateIdle, reason) {
				resumed++
			}
		}
	}
	
	return resumed, targets, nil
}

func (fsm *FleetStateManager) transitionAgent(agent *FleetAgent, newState AgentState, reason string) bool {
	validTransitions := map[AgentState][]AgentState{
		StateIdle:       {StateAssigned, StatePaused, StateTerminated},
		StateAssigned:   {StateWorking, StateIdle, StatePaused, StateFailed},
		StateWorking:    {StateCompleted, StateFailed, StatePaused, StateRecovering},
		StatePaused:     {StateWorking, StateIdle, StateTerminated},
		StateCompleted:  {StateIdle, StateAssigned},
		StateFailed:     {StateRecovering, StateIdle, StateRestarting, StateTerminated},
		StateRecovering: {StateIdle, StateWorking, StateFailed},
		StateRestarting: {StateIdle, StateFailed},
	}
	
	valid, ok := validTransitions[agent.State]
	if !ok {
		return false
	}
	
	canTransition := false
	for _, s := range valid {
		if s == newState {
			canTransition = true
			break
		}
	}
	
	if !canTransition {
		return false
	}
	
	// Record transition
	oldState := agent.State
	agent.StateHistory = append(agent.StateHistory, StateTransition{
		From:      string(oldState),
		To:        string(newState),
		Timestamp: time.Now().UTC(),
		Reason:    reason,
	})
	agent.State = newState
	agent.LastStateChange = time.Now().UTC()
	
	return fsm.SaveAgent(agent) == nil
}

func containsString(slice []string, item string) bool {
	for _, s := range slice {
		if s == item {
			return true
		}
	}
	return false
}

// Note: Fleet workflow commands are defined in workflow_fleet.go
// This file contains the FleetStateManager and related types only.
