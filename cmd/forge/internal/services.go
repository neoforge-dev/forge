package internal

import "context"

// TaskService defines the operations available for working with tasks.
//
// This interface is implemented by the HTTP client against the v3 daemon and
// can also be implemented by any future offline/direct storage backend.
type TaskService interface {
	// ListTasks returns a list of tasks filtered by status (when supported)
	// and limited to the given maximum number of results.
	ListTasks(ctx context.Context, status string, limit int, domain string) (*TaskListResponse, error)

	// GetTask retrieves a single task by its ID.
	GetTask(ctx context.Context, taskID string) (*Task, error)

	// CreateTask creates a new task using the provided request payload.
	CreateTask(ctx context.Context, req *CreateTaskRequest) (*Task, error)

	// UpdateTask updates an existing task with the provided fields.
	UpdateTask(ctx context.Context, taskID string, req *UpdateTaskRequest) (*Task, error)

	// DeleteTask deletes a task by its ID.
	DeleteTask(ctx context.Context, taskID string) error

	// ClaimTask claims a task for the given agent.
	ClaimTask(ctx context.Context, taskID string, agentID string) (*Task, error)

	// CompleteTask marks a task as complete with an optional result summary.
	CompleteTask(ctx context.Context, taskID string, result string) (*Task, error)
}

// AgentService defines the operations available for working with agents.
//
// This interface is implemented by the HTTP client against the v3 daemon and
// can also be implemented by any future offline/direct storage backend.
type AgentService interface {
	// ListAgents returns all known agents.
	ListAgents(ctx context.Context) (*AgentListResponse, error)

	// GetAgent returns health information for a specific agent.
	GetAgent(ctx context.Context, agentID string) (*AgentHealth, error)

	// GetAgentTasks lists tasks associated with a specific agent.
	GetAgentTasks(ctx context.Context, agentID string) (*TaskListResponse, error)
}

// Ensure Client implements the service interfaces.
var (
	_ TaskService  = (*Client)(nil)
	_ AgentService = (*Client)(nil)
)
