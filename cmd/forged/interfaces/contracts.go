// Package interfaces defines explicit contracts for FORGE v3 components.
// All agents and packages should code against these interfaces to prevent
// integration mismatches (e.g. SetContextManager vs SetRoyalJelly signature drift).
package interfaces

import (
	"context"
	"net/http"
	"time"
)

// ContextEnvelope represents a context envelope for agent handoffs
type ContextEnvelope struct {
	ID          string            `json:"id"`
	AgentID     string            `json:"agent_id"`
	Domain      string            `json:"domain"`
	Project     string            `json:"project"`
	TaskID      string            `json:"task_id"`
	CreatedAt   time.Time         `json:"created_at"`
	ExpiresAt   time.Time         `json:"expires_at"`
	Summary     string            `json:"summary"`
	Decisions   []Decision        `json:"decisions"`
	Failures    []Failure         `json:"failures"`
	Calibration map[string]string `json:"calibration"`
	KeyFiles    []string          `json:"key_files"`
	Metadata    map[string]any    `json:"metadata"`
}

// Decision represents a decision made by an agent
type Decision struct {
	ID          string    `json:"id"`
	Description string    `json:"description"`
	Reasoning   string    `json:"reasoning"`
	Timestamp   time.Time `json:"timestamp"`
}

// Failure represents a failure that occurred
type Failure struct {
	ID          string    `json:"id"`
	Description string    `json:"description"`
	Lesson      string    `json:"lesson"`
	Timestamp   time.Time `json:"timestamp"`
}

// ContextManager defines the interface for context operations.
// Implementations: *ContextManager in main package.
type ContextManager interface {
	GenerateEnvelope(ctx context.Context, agentID, domain, project, taskID, reason string) (*ContextEnvelope, error)
	BootstrapAgent(ctx context.Context, agentID, taskID string) (*ContextEnvelope, error)
	SyncEnvelopesToFilesystem() error
	SyncDomainToFilesystem(domain string) error
	Stop()
}

// HookEvent represents an event that can trigger hooks
type HookEvent struct {
	Type       string                 `json:"type"`
	AgentID    string                 `json:"agent_id"`
	ContextPct float64                `json:"context_pct"`
	Timestamp  int64                  `json:"timestamp"`
	Metadata   map[string]interface{} `json:"metadata"`
}

// Hook is a function that responds to events
type Hook interface {
	Name() string
	Trigger(ctx context.Context, event HookEvent) error
}

// RoyalJelly defines the interface for the hook system.
type RoyalJelly interface {
	RegisterHook(eventType string, hook Hook)
	TriggerHooks(ctx context.Context, event HookEvent) error
}

// PatrolSystem defines the interface for patrol operations.
type PatrolSystem interface {
	SetContextManager(cm ContextManager)
	SetRoyalJelly(rj RoyalJelly)
	Start()
	Stop()
}

// HTTPHandler defines interface for HTTP route registration.
type HTTPHandler interface {
	RegisterRoutes(mux *http.ServeMux)
}
