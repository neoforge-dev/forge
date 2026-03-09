//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"log"
)

// RoyalJelly manages hooks for agent context events
type RoyalJelly struct {
	db    *sql.DB
	cm    *ContextManager
	hooks map[string][]Hook
}

// NewRoyalJelly creates a new RoyalJelly hook system
func NewRoyalJelly(db *sql.DB, cm *ContextManager) *RoyalJelly {
	return &RoyalJelly{
		db:    db,
		cm:    cm,
		hooks: make(map[string][]Hook),
	}
}

type Hook interface {
	Name() string
	Trigger(ctx context.Context, event HookEvent) error
}

type HookEvent struct {
	Type       string // "context_threshold", "agent_disconnect", etc.
	AgentID    string
	ContextPct float64
	Timestamp  int64
	Metadata   map[string]interface{}
}

// RegisterHook adds a hook for an event type
func (rj *RoyalJelly) RegisterHook(eventType string, hook Hook) {
	rj.hooks[eventType] = append(rj.hooks[eventType], hook)
}

// TriggerHooks executes all hooks for an event
func (rj *RoyalJelly) TriggerHooks(ctx context.Context, event HookEvent) error {
	hooks, exists := rj.hooks[event.Type]
	if !exists {
		return nil
	}
	
	log.Printf("[RoyalJelly] Triggering %d hooks for event type: %s", len(hooks), event.Type)
	
	for _, hook := range hooks {
		if err := hook.Trigger(ctx, event); err != nil {
			log.Printf("[RoyalJelly] Hook %s failed: %v", hook.Name(), err)
		} else {
			log.Printf("[RoyalJelly] Hook %s executed successfully", hook.Name())
		}
	}
	return nil
}

// ContextThresholdHook generates envelope at 50%
type ContextThresholdHook struct {
	cm *ContextManager
}

func (h *ContextThresholdHook) Name() string {
	return "context_threshold_envelope"
}

func (h *ContextThresholdHook) Trigger(ctx context.Context, event HookEvent) error {
	if event.ContextPct >= 50.0 {
		domain, _ := event.Metadata["domain"].(string)
		if domain == "" {
			domain = "default"
		}
		project, _ := event.Metadata["project"].(string)
		if project == "" {
			project = "default"
		}
		taskID, _ := event.Metadata["task_id"].(string)
		if taskID == "" {
			taskID = "auto-generated"
		}

		log.Printf("[RoyalJelly] Context Threshold Reached! %.2f%% - Generating envelope for %s (Task: %s)", event.ContextPct, event.AgentID, taskID)

		// Generate envelope
		_, err := h.cm.GenerateEnvelope(ctx, event.AgentID,
			domain,
			project,
			taskID,
			"Auto-generated at 50% context threshold")
		return err
	}
	return nil
}
