// Package internal provides shared types for the forge CLI.
package internal

import (
	"fmt"
	"strings"
	"time"
)

// Task represents a task in the FORGE system (V3 API format).
type Task struct {
	ID            string                 `json:"id"`
	Title         string                 `json:"title"`
	Description   string                 `json:"description"`
	Status        string                 `json:"status"`
	Priority      int                    `json:"priority"`
	Lane          string                 `json:"lane,omitempty"`
	AssignedAgent *string                `json:"assigned_agent"`
	CreatedAt     string                 `json:"created_at"`
	SourceFile    *string                `json:"source_file,omitempty"`
	ResultPath    *string                `json:"result_path,omitempty"`
	Metadata      map[string]interface{} `json:"metadata,omitempty"`

	// V3 API fields (populated when talking to the v3 daemon)
	Domain     string `json:"domain,omitempty"`
	Project    string `json:"project,omitempty"`
	TaskType   string `json:"type,omitempty"`
	State      string `json:"state,omitempty"`
	AssignedTo string `json:"assigned_to,omitempty"`
	UpdatedAt  string `json:"updated_at,omitempty"`
}

// DisplayTitle returns a human-readable label for display.
// Uses Title if set; otherwise constructs a fallback that makes it immediately
// clear the task has no explicit title (prefixed with "[auto]" so operators
// know to investigate or set a meaningful title).
func (t *Task) DisplayTitle() string {
	if t.Title != "" {
		return t.Title
	}
	if t.Domain != "" && t.Project != "" {
		if t.TaskType != "" {
			return fmt.Sprintf("[auto] %s/%s %s task", t.Domain, t.Project, t.TaskType)
		}
		return fmt.Sprintf("[auto] %s/%s task", t.Domain, t.Project)
	}
	return "[auto] untitled task"
}

// TaskListResponse represents the response from listing tasks (V3 format).
type TaskListResponse struct {
	Tasks []Task `json:"tasks"`
}

// Agent represents an agent in the FORGE system.
type Agent struct {
	ID           string    `json:"id"`
	Node         string    `json:"node"`
	Status       string    `json:"status"`
	Role         string    `json:"role"`
	Tier         string    `json:"tier"`
	ContextPct   float64   `json:"context_pct"`
	LastPong     time.Time `json:"last_pong"`
	ConnectedAt  time.Time `json:"connected_at"`
	Capabilities []string  `json:"capabilities"`
	CurrentTask  *string   `json:"current_task_id"`
}

// AgentListResponse represents the response from listing agents.
type AgentListResponse struct {
	Agents []Agent `json:"agents"`
	Count  int     `json:"count"`
}

// AgentHealth represents the health status of an agent.
type AgentHealth struct {
	AgentID      string    `json:"agent_id"`
	Node         string    `json:"node"`
	Status       string    `json:"status"`
	ContextPct   float64   `json:"context_pct"`
	LastSeen     time.Time `json:"last_seen"`
	ConnectedAt  time.Time `json:"connected_at"`
	Capabilities []string  `json:"capabilities"`
	CurrentTask  *string   `json:"current_task_id"`
}

// Config represents the FORGE configuration.
type Config struct {
	APIURL   string            `json:"api_url"`
	NodeID   string            `json:"node_id"`
	Domain   string            `json:"domain"`
	Settings map[string]string `json:"settings"`
}

// DaemonStatus represents the status of the FORGE daemon.
type DaemonStatus struct {
	Status       string `json:"status"`
	Version      string `json:"version"`
	Uptime       string `json:"uptime"`
	PendingTasks int    `json:"pending_tasks"`
	PID          int    `json:"pid,omitempty"`
}

// CreateTaskRequest represents a request to create a task.
//
// Field names follow the V4 CLI terminology while remaining compatible with
// the underlying V3 API payload expectations.
type CreateTaskRequest struct {
	Subject     string                 `json:"title"`
	Description string                 `json:"description"`
	Priority    string                 `json:"priority"`
	Domain      string                 `json:"domain,omitempty"`
	Project     string                 `json:"project,omitempty"`
	Type        string                 `json:"type,omitempty"`
	SourceFile  *string                `json:"source_file,omitempty"`
	ResultPath  *string                `json:"result_path,omitempty"`
	Metadata    map[string]interface{} `json:"metadata,omitempty"`
}

// UpdateTaskRequest represents a request to update a task.
type UpdateTaskRequest struct {
	Subject     *string `json:"subject,omitempty"`
	Description *string `json:"description,omitempty"`
	Priority    *string `json:"priority,omitempty"`
	Status      *string `json:"status,omitempty"`
}

// ClaimTaskRequest represents a request to claim a task (V3 API expects agent_id).
type ClaimTaskRequest struct {
	AgentID string `json:"agent_id"`
}

// ClaimTaskResponse represents the v3 API response for task claim.
type ClaimTaskResponse struct {
	Task    *Task  `json:"task,omitempty"`
	Message string `json:"message,omitempty"`
}

// CompleteTaskRequest represents a request to complete a task (V3 format).
type CompleteTaskRequest struct {
	Agent         string `json:"agent"` // v3 may accept agent or agent_id
	ResultSummary string `json:"result_summary"`
}

// ErrorResponse represents an error response from the API.
type ErrorResponse struct {
	Error   string      `json:"error"`
	Message string      `json:"message,omitempty"`
	Status  int         `json:"status"`
	Detail  interface{} `json:"detail,omitempty"`
}

// ==================== Domain Types ====================

// Domain represents a domain in the FORGE portfolio.
type Domain struct {
	Key          string   `yaml:"-" json:"key"`
	DisplayName  string   `yaml:"display_name" json:"display_name"`
	Compliance   []string `yaml:"compliance" json:"compliance"`
	FrontendTier string   `yaml:"frontend_tier" json:"frontend_tier"`
	Products     []string `yaml:"products" json:"products"`
	Active       bool     `yaml:"active" json:"active"`
	Business     Business `yaml:"business" json:"business"`
}

// Business contains business-related domain information.
type Business struct {
	DeployStatus       string   `yaml:"deploy_status" json:"deploy_status"`
	CurrentMRR         int      `yaml:"current_mrr" json:"current_mrr"`
	TargetMRR          int      `yaml:"target_mrr" json:"target_mrr"`
	StripeLive         bool     `yaml:"stripe_live" json:"stripe_live"`
	HumanActionsNeeded []string `yaml:"human_actions_needed" json:"human_actions_needed"`
}

// DomainListResponse represents the response from listing domains.
type DomainListResponse struct {
	Domains []Domain `json:"domains"`
	Count   int      `json:"count"`
}

// CreateDomainRequest represents a request to create a domain.
type CreateDomainRequest struct {
	Key         string `json:"key"`
	DisplayName string `json:"displayName"`
	Compliance  string `json:"compliance,omitempty"`
}

// ==================== Project Types ====================

// Project represents a repository/project within a domain.
type Project struct {
	ID          string    `json:"id"`
	Key         string    `json:"key"`
	Name        string    `json:"name"`
	Domain      string    `json:"domain"`
	Description string    `json:"description"`
	Path        string    `json:"path"`
	Type        string    `json:"type"`
	Status      string    `json:"status"`
	CreatedAt   time.Time `json:"createdAt"`
	UpdatedAt   time.Time `json:"updatedAt"`
}

// ProjectListResponse represents the response from listing projects.
type ProjectListResponse struct {
	Projects []Project `json:"projects"`
	Count    int       `json:"count"`
}

// CreateProjectRequest represents a request to create a project.
type CreateProjectRequest struct {
	Key         string `json:"key"`
	Name        string `json:"name"`
	Domain      string `json:"domain"`
	Description string `json:"description,omitempty"`
	Path        string `json:"path,omitempty"`
	Type        string `json:"type,omitempty"`
}

// ==================== Portfolio Types ====================

// PortfolioState represents the tracked idea-to-revenue operating loop.
type PortfolioState struct {
	Version    string             `yaml:"version" json:"version"`
	Updated    string             `yaml:"updated" json:"updated"`
	NorthStar  string             `yaml:"north_star" json:"north_star"`
	Products   []PortfolioProduct `yaml:"products" json:"products"`
	StageOrder []string           `yaml:"-" json:"stage_order,omitempty"`
}

// PortfolioProduct represents one product moving through the operating loop.
type PortfolioProduct struct {
	Key            string `yaml:"key" json:"key"`
	Name           string `yaml:"name" json:"name"`
	Domain         string `yaml:"domain" json:"domain"`
	RepoPath       string `yaml:"repo_path,omitempty" json:"repo_path,omitempty"`
	Stage          string `yaml:"stage" json:"stage"`
	Status         string `yaml:"status" json:"status"`
	Owner          string `yaml:"owner,omitempty" json:"owner,omitempty"`
	ICP            string `yaml:"icp,omitempty" json:"icp,omitempty"`
	CurrentMRR     int    `yaml:"current_mrr" json:"current_mrr"`
	TargetMRR      int    `yaml:"target_mrr" json:"target_mrr"`
	DeployReady    bool   `yaml:"deploy_ready" json:"deploy_ready"`
	AnalyticsReady bool   `yaml:"analytics_ready" json:"analytics_ready"`
	BillingReady   bool   `yaml:"billing_ready" json:"billing_ready"`
	NextGate       string `yaml:"next_gate" json:"next_gate"`
	NextAction     string `yaml:"next_action" json:"next_action"`
	PrimaryMetric  string `yaml:"primary_metric,omitempty" json:"primary_metric,omitempty"`
	PrimaryRisk    string `yaml:"primary_risk,omitempty" json:"primary_risk,omitempty"`
}

// PortfolioSummary represents a portfolio-wide operating snapshot.
type PortfolioSummary struct {
	Updated              string             `json:"updated"`
	NorthStar            string             `json:"north_star"`
	ProductCount         int                `json:"product_count"`
	RevenueProductCount  int                `json:"revenue_product_count"`
	DeploymentReadyCount int                `json:"deployment_ready_count"`
	AnalyticsReadyCount  int                `json:"analytics_ready_count"`
	BillingReadyCount    int                `json:"billing_ready_count"`
	StageCounts          map[string]int     `json:"stage_counts"`
	PriorityProducts     []PortfolioProduct `json:"priority_products"`
}

// ==================== Context Types ====================

// Context represents a knowledge preservation unit (envelope/royal jelly).
type Context struct {
	ID        string                 `json:"id"`
	Key       string                 `json:"key"`
	Domain    string                 `json:"domain"`
	Project   string                 `json:"project"`
	Type      string                 `json:"type"` // envelope, jelly, memory
	Content   string                 `json:"content"`
	Metadata  map[string]interface{} `json:"metadata"`
	CreatedAt time.Time              `json:"createdAt"`
	UpdatedAt time.Time              `json:"updatedAt"`
	ExpiresAt *time.Time             `json:"expiresAt,omitempty"`
}

// ContextListResponse represents the response from listing contexts.
type ContextListResponse struct {
	Contexts []Context `json:"contexts"`
	Count    int       `json:"count"`
}

// CreateContextRequest represents a request to create a context.
type CreateContextRequest struct {
	Key      string                 `json:"key"`
	Domain   string                 `json:"domain"`
	Project  string                 `json:"project,omitempty"`
	Type     string                 `json:"type"`
	Content  string                 `json:"content"`
	Metadata map[string]interface{} `json:"metadata,omitempty"`
}

// ==================== Lane Types ====================

// Lane represents a stage in the Dark Factory workflow.
// Fields match the V3 daemon API response from GET /lanes and GET /lanes/{id}.
type Lane struct {
	ID             string `json:"id"`
	Name           string `json:"name"`
	Description    string `json:"description"`
	Capabilities   string `json:"capabilities"`
	AutoClaim      bool   `json:"auto_claim"`
	MaxParallel    int    `json:"max_parallel"`
	TimeoutMinutes int    `json:"timeout_minutes"`
	CreatedAt      string `json:"created_at"`
}

// LaneListResponse represents the response from listing lanes.
type LaneListResponse struct {
	Lanes []Lane `json:"lanes"`
	Count int    `json:"count"`
}

// PromoteTaskRequest represents a request to promote a task to the next lane.
type PromoteTaskRequest struct {
	TargetLane string `json:"targetLane,omitempty"`
	Comment    string `json:"comment,omitempty"`
}

// ==================== Approval Types ====================

// Approval represents a human-in-the-loop approval request.
type Approval struct {
	ID          string                 `json:"id"`
	Type        string                 `json:"type"` // merge, deploy, feature, release
	Tier        string                 `json:"tier"` // watch, phone, desktop
	Domain      string                 `json:"domain"`
	Project     string                 `json:"project"`
	Title       string                 `json:"title"`
	Description string                 `json:"description"`
	Confidence  float64                `json:"confidence"` // 0.0 - 1.0
	RiskScore   float64                `json:"riskScore"`  // 0.0 - 1.0
	Status      string                 `json:"status"`     // pending, approved, rejected, expired
	RequestedBy string                 `json:"requestedBy"`
	RequestedAt time.Time              `json:"requestedAt"`
	ExpiresAt   time.Time              `json:"expiresAt"`
	DecidedAt   *time.Time             `json:"decidedAt,omitempty"`
	DecidedBy   string                 `json:"decidedBy,omitempty"`
	Decision    string                 `json:"decision,omitempty"` // approve, reject
	Reason      string                 `json:"reason,omitempty"`   // reason for decision
	Metadata    map[string]interface{} `json:"metadata,omitempty"`
}

// ApprovalListResponse represents the response from listing approvals.
type ApprovalListResponse struct {
	Approvals []Approval `json:"approvals"`
	Count     int        `json:"count"`
}

// DecideApprovalRequest represents a request to decide on an approval.
type DecideApprovalRequest struct {
	Decision string `json:"decision"` // approve, reject
	Reason   string `json:"reason,omitempty"`
}

// ==================== Queue Types ====================

// QueueDepth represents the depth of the task queue by state.
type QueueDepth struct {
	Queued     int `json:"queued"`
	Dispatched int `json:"dispatched"`
	Running    int `json:"running"`
	Blocked    int `json:"blocked"`
	Completed  int `json:"completed"`
	Failed     int `json:"failed"`
	Approved   int `json:"approved"`
}

// QueueItem represents a single item in the task queue.
type QueueItem struct {
	ID          string `json:"id"`
	Title       string `json:"title"`
	Status      string `json:"status"`
	Assignee    string `json:"assignee,omitempty"`
	Lane        string `json:"lane"`
	Priority    string `json:"priority"`
	CreatedAt   string `json:"created_at,omitempty"`
	CompletedAt string `json:"completed_at,omitempty"`
}

// ==================== Patrol Types ====================

// Patrol represents a background monitoring process.
type Patrol struct {
	ID       string                 `json:"id"`
	Name     string                 `json:"name"`
	Type     string                 `json:"type"`     // health, heartbeat, quality, deploy
	Status   string                 `json:"status"`   // running, stopped, error
	Interval int                    `json:"interval"` // seconds
	LastRun  string                 `json:"lastRun"`
	Config   map[string]interface{} `json:"config,omitempty"`
}

// PatrolListResponse represents the response from listing patrols.
type PatrolListResponse struct {
	Patrols []Patrol `json:"patrols"`
	Count   int      `json:"count"`
}

// ==================== Work Context (forge work) ====================

// WorkContext represents the current forge work context (domain/project)
// persisted in .forge/context/current.json for forge work workflow.
type WorkContext struct {
	Domain      string `json:"domain"`
	Project     string `json:"project"`
	SetAt       string `json:"set_at"`       // RFC3339
	ShellPrompt string `json:"shell_prompt"` // e.g. "(codeswiftr-com:interview-simulator)"
}

// ==================== Fleet Types (V4-011) ====================

// FleetNode represents a node in the FORGE fleet.
type FleetNode struct {
	ID           string            `json:"id"`
	Hostname     string            `json:"hostname"`
	Region       string            `json:"region"`
	Status       string            `json:"status"`
	AgentCount   int               `json:"agent_count"`
	QueueDepth   int               `json:"queue_depth"`
	CPUPercent   int               `json:"cpu_percent"`
	RAMPercent   int               `json:"ram_percent"`
	Capabilities []string          `json:"capabilities"`
	LastSeen     time.Time         `json:"last_seen"`
	Metadata     map[string]string `json:"metadata,omitempty"`
}

// FleetStatus represents the overall fleet status.
type FleetStatus struct {
	Nodes       []FleetNode `json:"nodes"`
	TotalNodes  int         `json:"total_nodes"`
	TotalAgents int         `json:"total_agents"`
	Healthy     int         `json:"healthy"`
	Stale       int         `json:"stale"`
	Overloaded  int         `json:"overloaded"`
	GeneratedAt time.Time   `json:"generated_at"`
}

// FleetHealth represents detailed health metrics for the fleet.
type FleetHealth struct {
	Nodes       []NodeHealth `json:"nodes"`
	TotalNodes  int          `json:"total_nodes"`
	Healthy     int          `json:"healthy"`
	Stale       int          `json:"stale"`
	Overloaded  int          `json:"overloaded"`
	Issues      []FleetIssue `json:"issues,omitempty"`
	GeneratedAt time.Time    `json:"generated_at"`
}

// NodeHealth represents health information for a single node.
type NodeHealth struct {
	NodeID     string    `json:"node_id"`
	Status     string    `json:"status"`
	AgentCount int       `json:"agent_count"`
	Healthy    bool      `json:"healthy"`
	CPUPercent int       `json:"cpu_percent"`
	RAMPercent int       `json:"ram_percent"`
	LastSeen   time.Time `json:"last_seen"`
	Issues     []string  `json:"issues,omitempty"`
}

// FleetIssue represents a fleet health issue.
type FleetIssue struct {
	NodeID  string `json:"node_id"`
	Type    string `json:"type"`
	Details string `json:"details"`
}

// BroadcastRequest represents a request to broadcast a message.
type BroadcastRequest struct {
	Message      string   `json:"message"`
	Type         string   `json:"type"`
	FilterState  string   `json:"filter_state,omitempty"`
	FilterAgents []string `json:"filter_agents,omitempty"`
}

// BroadcastResult represents the result of a broadcast operation.
type BroadcastResult struct {
	Timestamp   time.Time `json:"timestamp"`
	Message     string    `json:"message"`
	Type        string    `json:"type"`
	TargetCount int       `json:"target_count"`
	Targets     []string  `json:"targets"`
	Success     bool      `json:"success"`
}

// ==================== Priority Types ====================

// PriorityLabel maps human-readable priority strings to v3 integer values (1-10).
// Unknown labels default to 5 (medium).
var PriorityLabel = map[string]int{
	"low":      3,
	"medium":   5,
	"high":     7,
	"urgent":   8,
	"critical": 10,
}

// PriorityFromLabel converts a priority label to the v3 integer priority.
// Returns 5 (medium) for unknown labels.
func PriorityFromLabel(label string) int {
	if p, ok := PriorityLabel[strings.ToLower(strings.TrimSpace(label))]; ok {
		return p
	}
	return 5
}

// PriorityToLabel converts a v3 integer priority to a display label.
func PriorityToLabel(p int) string {
	switch {
	case p >= 9:
		return "critical"
	case p >= 7:
		return "high"
	case p >= 5:
		return "medium"
	default:
		return "low"
	}
}
