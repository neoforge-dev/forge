// Package internal provides the HTTP client for the forge CLI.
package internal

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math"
	"math/rand"
	"net"
	"net/http"
	"os"
	"strings"
	"time"
)

// DaemonUnreachableError indicates the CLI could not reach the API daemon.
// It embeds a recovery hint directly in the error text for improved UX.
type DaemonUnreachableError struct {
	URL string
	Err error
}

func (e *DaemonUnreachableError) Error() string {
	return fmt.Sprintf(`control plane unreachable

  URL: %s

  Possible fixes:
    • Check Tailscale:   tailscale status
    • Start daemon:      forge daemon start  (if this IS the control plane node)
    • Use local mode:    forge --config ~/.forge/config-local.toml ...
    • Change URL:        forge config set control_plane.url http://localhost:8081

  See: forge daemon status`, e.URL)
}

func (e *DaemonUnreachableError) Unwrap() error { return e.Err }

func isConnectionRefused(err error) bool {
	if err == nil {
		return false
	}

	// Unwrap common net/http wrappers (e.g. *url.Error) and use string matching
	// to keep this lightweight and portable across platforms.
	for e := err; e != nil; e = errors.Unwrap(e) {
		msg := strings.ToLower(e.Error())
		if strings.Contains(msg, "connection refused") ||
			strings.Contains(msg, "no such host") ||
			strings.Contains(msg, "dial tcp") {
			return true
		}
	}
	return false
}

// Client is the HTTP client for the FORGE API.
type Client struct {
	httpClient *http.Client
	baseURL    string
	token      string
	retryMax   int
	logger     *log.Logger
	debug      bool
}

// ClientOption defines a functional option for the Client.
type ClientOption func(*Client)

// WithTimeout sets the timeout for the HTTP client.
func WithTimeout(timeout time.Duration) ClientOption {
	return func(c *Client) {
		c.httpClient.Timeout = timeout
	}
}

// WithRetry sets the maximum number of retries for transient failures.
func WithRetry(retryMax int) ClientOption {
	return func(c *Client) {
		c.retryMax = retryMax
	}
}

// WithToken sets the authentication token.
func WithToken(token string) ClientOption {
	return func(c *Client) {
		c.token = token
	}
}

// WithDebug enables or disables debug logging.
func WithDebug(debug bool) ClientOption {
	return func(c *Client) {
		c.debug = debug
	}
}

// WithLogger sets a custom logger for the client.
func WithLogger(logger *log.Logger) ClientOption {
	return func(c *Client) {
		c.logger = logger
	}
}

// NewClient creates a new FORGE API client with production defaults.
func NewClient(opts ...ClientOption) *Client {
	baseURL := ResolveAPIBaseURL()

	// Production-ready HTTP client with connection pooling
	httpClient := &http.Client{
		Timeout: 30 * time.Second,
		Transport: &http.Transport{
			Proxy: http.ProxyFromEnvironment,
			DialContext: (&net.Dialer{
				Timeout:   10 * time.Second,
				KeepAlive: 30 * time.Second,
			}).DialContext,
			ForceAttemptHTTP2:     true,
			MaxIdleConns:          100,
			IdleConnTimeout:       90 * time.Second,
			TLSHandshakeTimeout:   10 * time.Second,
			ExpectContinueTimeout: 1 * time.Second,
		},
	}

	c := &Client{
		httpClient: httpClient,
		baseURL:    baseURL,
		token:      os.Getenv("FORGE_API_TOKEN"),
		retryMax:   3,
		logger:     log.New(os.Stderr, "[FORGE-CLIENT] ", log.LstdFlags),
		debug:      os.Getenv("FORGE_DEBUG") == "1",
	}

	for _, opt := range opts {
		opt(c)
	}

	return c
}

// NewClientWithURL creates a new FORGE API client with a custom base URL.
func NewClientWithURL(baseURL string, opts ...ClientOption) *Client {
	c := NewClient(opts...)
	c.baseURL = baseURL
	return c
}

// BaseURL returns the base URL of the client.
func (c *Client) BaseURL() string {
	return c.baseURL
}

// withRetry executes a function with exponential backoff for transient failures.
func (c *Client) withRetry(ctx context.Context, fn func() (*http.Response, error)) (*http.Response, error) {
	var resp *http.Response
	var err error

	for attempt := 0; attempt <= c.retryMax; attempt++ {
		if attempt > 0 {
			// Exponential backoff with jitter
			backoff := time.Duration(math.Pow(2, float64(attempt))) * 100 * time.Millisecond
			jitter := time.Duration(rand.Int63n(int64(backoff / 2)))
			sleepTime := backoff + jitter

			if c.debug {
				c.logger.Printf("Retrying after %v (attempt %d/%d)...", sleepTime, attempt, c.retryMax)
			}

			select {
			case <-time.After(sleepTime):
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}

		resp, err = fn()

		// If no error, check if it's a retryable status code
		if err == nil {
			if resp.StatusCode == http.StatusServiceUnavailable ||
				resp.StatusCode == http.StatusGatewayTimeout ||
				resp.StatusCode == http.StatusTooManyRequests {
				resp.Body.Close() // Close body before retry
				continue
			}
			return resp, nil
		}

		// Check if error is retryable (transient network errors)
		if isConnectionRefused(err) || errors.Is(err, context.DeadlineExceeded) {
			continue
		}

		// Not retryable
		return nil, err
	}

	return resp, err
}

// do performs the actual HTTP request with auth, logging, and retries.
func (c *Client) do(ctx context.Context, method, path string, body io.Reader) (*http.Response, error) {
	url := c.baseURL + path

	fn := func() (*http.Response, error) {
		req, err := http.NewRequestWithContext(ctx, method, url, body)
		if err != nil {
			return nil, fmt.Errorf("failed to create request: %w", err)
		}

		req.Header.Set("Accept", "application/json")
		if body != nil {
			req.Header.Set("Content-Type", "application/json")
		}

		// Authentication
		if c.token != "" {
			req.Header.Set("Authorization", "Bearer "+c.token)
		}

		if c.debug {
			c.logger.Printf("--> %s %s", method, url)
			if c.token != "" {
				c.logger.Printf("Authorization: Bearer ***")
			}
		}

		start := time.Now()
		resp, err := c.httpClient.Do(req)
		duration := time.Since(start)

		if c.debug {
			if err != nil {
				c.logger.Printf("<-- %s %s ERROR: %v (%v)", method, url, err, duration)
			} else {
				c.logger.Printf("<-- %s %s %d (%v)", method, url, resp.StatusCode, duration)
			}
		}

		return resp, err
	}

	resp, err := c.withRetry(ctx, fn)
	if err != nil {
		if isConnectionRefused(err) {
			return nil, &DaemonUnreachableError{URL: c.baseURL, Err: err}
		}
		return nil, err
	}

	return resp, nil
}

// Get performs a GET request.
func (c *Client) Get(ctx context.Context, path string) (*http.Response, error) {
	return c.do(ctx, http.MethodGet, path, nil)
}

// Post performs a POST request.
func (c *Client) Post(ctx context.Context, path string, body interface{}) (*http.Response, error) {
	var bodyReader io.Reader
	if body != nil {
		jsonBody, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("failed to marshal request body: %w", err)
		}
		bodyReader = bytes.NewReader(jsonBody)
	}
	return c.do(ctx, http.MethodPost, path, bodyReader)
}

// Put performs a PUT request.
func (c *Client) Put(ctx context.Context, path string, body interface{}) (*http.Response, error) {
	var bodyReader io.Reader
	if body != nil {
		jsonBody, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("failed to marshal request body: %w", err)
		}
		bodyReader = bytes.NewReader(jsonBody)
	}
	return c.do(ctx, http.MethodPut, path, bodyReader)
}

// Delete performs a DELETE request.
func (c *Client) Delete(ctx context.Context, path string) (*http.Response, error) {
	return c.do(ctx, http.MethodDelete, path, nil)
}

// DecodeJSON decodes a JSON response into the given value.
func DecodeJSON(resp *http.Response, v interface{}) error {
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNoContent {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(v)
}

// CheckResponse checks if the response status code indicates success.
func CheckResponse(resp *http.Response) error {
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return nil
	}

	var errResp ErrorResponse
	// Attempt to decode error response, but don't fail if body is not JSON or empty
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()

	if err := json.Unmarshal(body, &errResp); err != nil {
		errResp.Error = http.StatusText(resp.StatusCode)
		errResp.Status = resp.StatusCode
		errResp.Message = string(body)
	}

	if errResp.Message != "" {
		return fmt.Errorf("API error: %s - %s (status %d)", errResp.Error, errResp.Message, resp.StatusCode)
	}
	return fmt.Errorf("API error: %s (status %d)", errResp.Error, resp.StatusCode)
}

// ==================== Task API (V3) ====================

// ListTasks lists tasks with optional status, limit, and domain filters.
func (c *Client) ListTasks(ctx context.Context, status string, limit int, domain string) (*TaskListResponse, error) {
	path := "/tasks?"
	params := []string{}
	if status != "" {
		params = append(params, "status="+status)
	}
	if domain != "" {
		params = append(params, "domain="+domain)
	}
	if limit > 0 {
		params = append(params, fmt.Sprintf("limit=%d", limit))
	}
	path += strings.Join(params, "&")

	resp, err := c.Get(ctx, path)
	if err != nil {
		return nil, err
	}

	// Backward compatibility: some daemons expose a legacy tasks list endpoint.
	if resp.StatusCode == http.StatusNotFound && (status == "" || strings.EqualFold(status, "pending")) {
		resp.Body.Close()
		resp, err = c.Get(ctx, "/tasks/pending")
		if err != nil {
			return nil, err
		}
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	body, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	// Preferred shape: {"tasks":[...], "count":N}
	var wrapped struct {
		Tasks []Task `json:"tasks"`
		Count int    `json:"count"`
	}
	if err := json.Unmarshal(body, &wrapped); err == nil {
		if wrapped.Tasks == nil {
			wrapped.Tasks = []Task{}
		}
		return &TaskListResponse{Tasks: wrapped.Tasks}, nil
	}

	// Backward/alternate shape: raw array of tasks
	var tasks []Task
	if err := json.Unmarshal(body, &tasks); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}
	if tasks == nil {
		tasks = []Task{}
	}
	return &TaskListResponse{Tasks: tasks}, nil
}

// GetTask gets a task by ID.
func (c *Client) GetTask(ctx context.Context, taskID string) (*Task, error) {
	resp, err := c.Get(ctx, fmt.Sprintf("/tasks/%s", taskID))
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	var task Task
	if err := DecodeJSON(resp, &task); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &task, nil
}

// v3CreatePayload is the wire format expected by the v3 /tasks endpoint.
type v3CreatePayload struct {
	Domain      string                 `json:"domain,omitempty"`
	Project     string                 `json:"project,omitempty"`
	Type        string                 `json:"type,omitempty"`
	Subject     string                 `json:"title,omitempty"`
	Description string                 `json:"description,omitempty"`
	Priority    int                    `json:"priority"`
	AssignedTo  string                 `json:"assigned_to,omitempty"`
	Metadata    map[string]interface{} `json:"metadata,omitempty"`
}

// CreateTask creates a new task.
func (c *Client) CreateTask(ctx context.Context, req *CreateTaskRequest) (*Task, error) {
	payload := &v3CreatePayload{
		Domain:      req.Domain,
		Project:     req.Project,
		Type:        req.Type,
		Subject:     req.Subject,
		Description: req.Description,
		Priority:    PriorityFromLabel(req.Priority),
		Metadata:    req.Metadata,
	}
	resp, err := c.Post(ctx, "/tasks", payload)
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	var task Task
	if err := DecodeJSON(resp, &task); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &task, nil
}

// UpdateTask updates a task.
func (c *Client) UpdateTask(ctx context.Context, taskID string, req *UpdateTaskRequest) (*Task, error) {
	resp, err := c.Put(ctx, fmt.Sprintf("/tasks/%s", taskID), req)
	if err != nil {
		return nil, err
	}
	if err := CheckResponse(resp); err != nil {
		return nil, err
	}
	var task Task
	if err := DecodeJSON(resp, &task); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}
	return &task, nil
}

// DeleteTask deletes a task.
func (c *Client) DeleteTask(ctx context.Context, taskID string) error {
	resp, err := c.Delete(ctx, fmt.Sprintf("/tasks/%s", taskID))
	if err != nil {
		return err
	}
	if resp.Body != nil {
		resp.Body.Close()
	}
	if resp.StatusCode == http.StatusNoContent {
		return nil
	}
	return CheckResponse(resp)
}

// ClaimTask claims a task for an agent.
func (c *Client) ClaimTask(ctx context.Context, taskID string, agentID string) (*Task, error) {
	req := &ClaimTaskRequest{AgentID: agentID}
	resp, err := c.Post(ctx, fmt.Sprintf("/tasks/%s/claim", taskID), req)
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	var result ClaimTaskResponse
	if err := DecodeJSON(resp, &result); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	if result.Task != nil {
		return result.Task, nil
	}
	return &Task{ID: taskID, Status: "claimed"}, nil
}

// CompleteTask marks a task as complete.
func (c *Client) CompleteTask(ctx context.Context, taskID string, result string) (*Task, error) {
	// Need agent ID for complete - use from env or default
	agentID := os.Getenv("FORGE_AGENT_ID")
	if agentID == "" {
		agentID = "cli"
	}

	req := &CompleteTaskRequest{
		Agent:         agentID,
		ResultSummary: result,
	}
	resp, err := c.Post(ctx, fmt.Sprintf("/tasks/%s/complete", taskID), req)
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	var task Task
	if err := DecodeJSON(resp, &task); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &task, nil
}

// ==================== Project API ====================

// ListProjects lists all projects, optionally filtered by domain.
func (c *Client) ListProjects(ctx context.Context, domain string) (*ProjectListResponse, error) {
	path := "/projects"
	if domain != "" {
		path = fmt.Sprintf("/projects?domain=%s", domain)
	}

	resp, err := c.Get(ctx, path)
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	body, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	var wrapped struct {
		Projects []Project `json:"projects"`
		Count    int       `json:"count"`
	}
	if err := json.Unmarshal(body, &wrapped); err == nil && wrapped.Projects != nil {
		return &ProjectListResponse{Projects: wrapped.Projects, Count: wrapped.Count}, nil
	}

	// Backward/alternate shape: raw array of projects
	var projects []Project
	if err := json.Unmarshal(body, &projects); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}
	if projects == nil {
		projects = []Project{}
	}
	return &ProjectListResponse{Projects: projects, Count: len(projects)}, nil
}

// GetProject gets a project by key or ID.
func (c *Client) GetProject(ctx context.Context, projectKey string) (*Project, error) {
	resp, err := c.Get(ctx, fmt.Sprintf("/projects/%s", projectKey))
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	var project Project
	if err := DecodeJSON(resp, &project); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &project, nil
}

// CreateProject creates a new project.
func (c *Client) CreateProject(ctx context.Context, req *CreateProjectRequest) (*Project, error) {
	resp, err := c.Post(ctx, "/projects", req)
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	var project Project
	if err := DecodeJSON(resp, &project); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &project, nil
}

// ==================== Context API ====================

// ListContexts lists all contexts, optionally filtered by domain and project.
func (c *Client) ListContexts(ctx context.Context, domain string, project string) (*ContextListResponse, error) {
	path := "/contexts"
	if domain != "" {
		path = fmt.Sprintf("/contexts?domain=%s", domain)
		if project != "" {
			path = fmt.Sprintf("/contexts?domain=%s&project=%s", domain, project)
		}
	}

	resp, err := c.Get(ctx, path)
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	body, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	var wrapped struct {
		Contexts []Context `json:"contexts"`
		Count    int       `json:"count"`
	}
	if err := json.Unmarshal(body, &wrapped); err == nil && wrapped.Contexts != nil {
		return &ContextListResponse{Contexts: wrapped.Contexts, Count: wrapped.Count}, nil
	}

	// Backward/alternate shape: raw array of contexts
	var contexts []Context
	if err := json.Unmarshal(body, &contexts); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}
	if contexts == nil {
		contexts = []Context{}
	}
	return &ContextListResponse{Contexts: contexts, Count: len(contexts)}, nil
}

// GetContext gets a context by key or ID.
func (c *Client) GetContext(ctx context.Context, contextKey string) (*Context, error) {
	resp, err := c.Get(ctx, fmt.Sprintf("/contexts/%s", contextKey))
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	var context Context
	if err := DecodeJSON(resp, &context); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &context, nil
}

// CreateContext creates a new context.
func (c *Client) CreateContext(ctx context.Context, req *CreateContextRequest) (*Context, error) {
	resp, err := c.Post(ctx, "/contexts", req)
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	var context Context
	if err := DecodeJSON(resp, &context); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &context, nil
}

// ==================== Lane API ====================

// ListLanes lists all lanes in the workflow.
func (c *Client) ListLanes(ctx context.Context) (*LaneListResponse, error) {
	resp, err := c.Get(ctx, "/lanes")
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	body, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	var wrapped struct {
		Lanes []Lane `json:"lanes"`
		Count int    `json:"count"`
	}
	if err := json.Unmarshal(body, &wrapped); err == nil && wrapped.Lanes != nil {
		return &LaneListResponse{Lanes: wrapped.Lanes, Count: wrapped.Count}, nil
	}

	// Backward/alternate shape: raw array of lanes
	var lanes []Lane
	if err := json.Unmarshal(body, &lanes); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}
	if lanes == nil {
		lanes = []Lane{}
	}
	return &LaneListResponse{Lanes: lanes, Count: len(lanes)}, nil
}

// GetLane gets a lane by key or ID.
func (c *Client) GetLane(ctx context.Context, laneKey string) (*Lane, error) {
	resp, err := c.Get(ctx, fmt.Sprintf("/lanes/%s", laneKey))
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	var lane Lane
	if err := DecodeJSON(resp, &lane); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &lane, nil
}

// PromoteTask promotes a task to the next lane.
func (c *Client) PromoteTask(ctx context.Context, taskID string, req *PromoteTaskRequest) error {
	resp, err := c.Post(ctx, fmt.Sprintf("/tasks/%s/promote", taskID), req)
	if err != nil {
		return err
	}
	return CheckResponse(resp)
}

// ==================== Agent API ====================

// ListAgents lists all agents.
func (c *Client) ListAgents(ctx context.Context) (*AgentListResponse, error) {
	resp, err := c.Get(ctx, "/agents")
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	var raw v3AgentsListResponse
	if err := DecodeJSON(resp, &raw); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	agents := make([]Agent, 0, len(raw.Agents))
	for _, a := range raw.Agents {
		agents = append(agents, Agent{
			ID:           a.AgentID,
			Node:         a.Node,
			Status:       a.Status,
			Role:         "",
			Tier:         "",
			ContextPct:   a.ContextPct,
			LastPong:     a.LastSeen,
			ConnectedAt:  a.ConnectedAt,
			Capabilities: a.Capabilities,
			CurrentTask:  a.CurrentTask,
		})
	}

	return &AgentListResponse{
		Agents: agents,
		Count:  raw.Count,
	}, nil
}

// GetAgent gets an agent by ID.
func (c *Client) GetAgent(ctx context.Context, agentID string) (*AgentHealth, error) {
	resp, err := c.Get(ctx, fmt.Sprintf("/agents/%s", agentID))
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	var health AgentHealth
	if err := DecodeJSON(resp, &health); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &health, nil
}

// SendHeartbeat sends a heartbeat for an agent, reporting its current context
// window usage percentage. contextPct is in the 0-100 range (the value shown
// by the Claude status line); the API expects 0.0-1.0 so we divide by 100.
func (c *Client) SendHeartbeat(ctx context.Context, agentID string, contextPct float64) error {
	hostname, _ := os.Hostname()
	payload := map[string]interface{}{
		"context_pct": contextPct / 100.0,
		"node":        hostname,
	}
	resp, err := c.Post(ctx, fmt.Sprintf("/agents/%s/heartbeat", agentID), payload)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return CheckResponse(resp)
}

// GetAgentTasks lists tasks for an agent.
func (c *Client) GetAgentTasks(ctx context.Context, agentID string) (*TaskListResponse, error) {
	resp, err := c.Get(ctx, fmt.Sprintf("/agents/%s/tasks", agentID))
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	body, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	var wrapped TaskListResponse
	if err := json.Unmarshal(body, &wrapped); err == nil && wrapped.Tasks != nil {
		return &wrapped, nil
	}

	// Backward/alternate shape: raw array of tasks
	var tasks []Task
	if err := json.Unmarshal(body, &tasks); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}
	return &TaskListResponse{Tasks: tasks}, nil
}

type v3AgentHeartbeat struct {
	AgentID      string    `json:"agent_id"`
	Node         string    `json:"node"`
	Status       string    `json:"status"`
	CurrentTask  *string   `json:"current_task_id,omitempty"`
	ContextPct   float64   `json:"context_pct"`
	Capabilities []string  `json:"capabilities,omitempty"`
	LastSeen     time.Time `json:"last_seen"`
	ConnectedAt  time.Time `json:"connected_at"`
}

type v3AgentsListResponse struct {
	Agents []v3AgentHeartbeat `json:"agents"`
	Count  int                `json:"count"`
}

// ==================== Approval API (V3) ====================

// v3Approval is the wire-format shape returned by the v3 /api/approvals endpoint.
// Field names differ from the CLI-side internal.Approval type, so we decode into
// this struct first and then project to the shared type.
type v3Approval struct {
	ID              string     `json:"id"`
	Type            string     `json:"type"`
	Tier            string     `json:"tier"`
	AgentID         string     `json:"agent_id"`
	Domain          string     `json:"domain"`
	Title           string     `json:"title"`
	Description     string     `json:"description"`
	RiskScore       float64    `json:"risk_score"`
	ConfidenceScore float64    `json:"confidence_score"`
	Recommendation  string     `json:"recommendation"`
	Status          string     `json:"status"`
	CreatedAt       time.Time  `json:"created_at"`
	ExpiresAt       time.Time  `json:"expires_at"`
	ResolvedAt      *time.Time `json:"resolved_at,omitempty"`
	ResolvedBy      *string    `json:"resolved_by,omitempty"`
	TaskID          *string    `json:"task_id,omitempty"`
}

// toApproval converts a v3 wire approval to the CLI-side Approval type.
func (v v3Approval) toApproval() Approval {
	a := Approval{
		ID:          v.ID,
		Type:        v.Type,
		Tier:        v.Tier,
		Domain:      v.Domain,
		Title:       v.Title,
		Description: v.Description,
		RiskScore:   v.RiskScore,
		Confidence:  v.ConfidenceScore,
		Status:      string(v.Status),
		RequestedBy: v.AgentID,
		RequestedAt: v.CreatedAt,
		ExpiresAt:   v.ExpiresAt,
	}
	if v.ResolvedAt != nil {
		a.DecidedAt = v.ResolvedAt
	}
	if v.ResolvedBy != nil {
		a.DecidedBy = *v.ResolvedBy
	}
	return a
}

// ListApprovals fetches all approvals from the v3 /api/approvals endpoint.
// An optional status filter may be provided (e.g. "pending").
func (c *Client) ListApprovals(ctx context.Context, status string) ([]Approval, error) {
	path := "/approvals"
	if status != "" {
		path = "/approvals?status=" + status
	}

	resp, err := c.Get(ctx, path)
	if err != nil {
		return nil, err
	}
	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	body, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	var wrapped struct {
		Approvals []v3Approval `json:"approvals"`
		Count     int          `json:"count"`
	}
	if err := json.Unmarshal(body, &wrapped); err != nil {
		return nil, fmt.Errorf("failed to decode approvals: %w", err)
	}

	result := make([]Approval, 0, len(wrapped.Approvals))
	for _, v := range wrapped.Approvals {
		result = append(result, v.toApproval())
	}
	return result, nil
}

// ApproveApproval sends a POST to /api/approvals/{id}/approve.
// The caller provides an optional user/reason string.
func (c *Client) ApproveApproval(ctx context.Context, id, user string) error {
	body := map[string]string{"user": user}
	resp, err := c.Post(ctx, fmt.Sprintf("/approvals/%s/approve", id), body)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return CheckResponse(resp)
}

// RejectApproval sends a POST to /api/approvals/{id}/reject.
func (c *Client) RejectApproval(ctx context.Context, id, user string) error {
	body := map[string]string{"user": user}
	resp, err := c.Post(ctx, fmt.Sprintf("/approvals/%s/reject", id), body)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return CheckResponse(resp)
}

// ==================== Pattern API (V3) ====================

// v3Pattern is the wire-format shape returned by the v3 /api/patterns endpoint.
type v3Pattern struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	Domain      string    `json:"domain"`
	YAMLContent string    `json:"yaml_content"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}

// ListPatterns fetches all patterns from the v3 /api/patterns endpoint.
// Returns the v3 pattern records mapped onto a minimal subset of the local
// Pattern struct (only fields the API exposes).  The caller is responsible for
// merging with built-in patterns when the daemon is unreachable.
func (c *Client) ListPatterns(ctx context.Context) ([]v3Pattern, error) {
	resp, err := c.Get(ctx, "/patterns")
	if err != nil {
		return nil, err
	}
	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	body, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	var wrapped struct {
		Patterns []v3Pattern `json:"patterns"`
		Count    int         `json:"count"`
	}
	if err := json.Unmarshal(body, &wrapped); err != nil {
		return nil, fmt.Errorf("failed to decode patterns: %w", err)
	}

	return wrapped.Patterns, nil
}

// ==================== Patrol API ====================

// v3PatrolStatus is the wire type returned by GET /api/patrols.
type v3PatrolStatus struct {
	ID              string  `json:"id"`
	Name            string  `json:"name"`
	Status          string  `json:"status"`
	LastRun         *string `json:"last_run"`
	IntervalSeconds int     `json:"interval_seconds"`
	ErrorCount      int     `json:"error_count"`
}

// ListPatrols returns live patrol status from the v3 daemon.
func (c *Client) ListPatrols(ctx context.Context) ([]v3PatrolStatus, error) {
	resp, err := c.Get(ctx, "/patrols")
	if err != nil {
		return nil, err
	}
	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	body, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	var wrapped struct {
		Patrols []v3PatrolStatus `json:"patrols"`
	}
	if err := json.Unmarshal(body, &wrapped); err != nil {
		return nil, fmt.Errorf("failed to decode patrols: %w", err)
	}

	return wrapped.Patrols, nil
}

// ==================== Config API ====================

// GetConfig gets the configuration.
func (c *Client) GetConfig(ctx context.Context) (*Config, error) {
	// Config is local only in V3
	return nil, fmt.Errorf("config API not available")
}

// SetConfig sets a configuration value.
func (c *Client) SetConfig(ctx context.Context, key, value string) error {
	// Config is local only in V3
	return fmt.Errorf("config API not available")
}

// ==================== Daemon API ====================

// GetDaemonStatus gets the daemon status.
func (c *Client) GetDaemonStatus(ctx context.Context) (*DaemonStatus, error) {
	resp, err := c.Get(ctx, "/health")
	if err != nil {
		return nil, err
	}

	if err := CheckResponse(resp); err != nil {
		return nil, err
	}

	var status DaemonStatus
	if err := DecodeJSON(resp, &status); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &status, nil
}
