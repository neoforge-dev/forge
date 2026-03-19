package internal

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// TestClientWithMockServer tests the API client with a mock server
func TestClientWithMockServer(t *testing.T) {
	// Create mock server
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")

		switch r.URL.Path {
		case "/tasks/pending":
			handleTasksEndpoint(w, r)
		case "/tasks":
			if r.Method == http.MethodPost {
				handleCreateTask(w, r)
				return
			}
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
		case "/agents":
			handleAgentsEndpoint(w, r)
		case "/projects":
			handleProjectsEndpoint(w, r)
		case "/lanes":
			handleLanesEndpoint(w, r)
		case "/contexts":
			handleContextsEndpoint(w, r)
		default:
			// Handle dynamic paths
			if len(r.URL.Path) > 7 && r.URL.Path[:7] == "/tasks/" {
				handleTaskEndpoint(w, r)
			} else if len(r.URL.Path) > 8 && r.URL.Path[:8] == "/agents/" {
				handleAgentEndpoint(w, r)
			} else if len(r.URL.Path) > 10 && r.URL.Path[:10] == "/projects/" {
				handleProjectEndpoint(w, r)
			} else if len(r.URL.Path) > 10 && r.URL.Path[:10] == "/contexts/" {
				handleContextEndpoint(w, r)
			} else if len(r.URL.Path) > 6 && r.URL.Path[:6] == "/lanes/" {
				handleLaneEndpoint(w, r)
			} else {
				w.WriteHeader(http.StatusNotFound)
				json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
			}
		}
	}))
	defer server.Close()

	client := NewClientWithURL(server.URL)
	ctx := context.Background()

	t.Run("ListTasks", func(t *testing.T) {
		tasks, err := client.ListTasks(ctx, "", 10, "")
		if err != nil {
			t.Fatalf("ListTasks failed: %v", err)
		}
		if tasks == nil {
			t.Fatal("tasks is nil")
		}
		if len(tasks.Tasks) != 2 {
			t.Errorf("expected 2 tasks, got %d", len(tasks.Tasks))
		}
	})

	t.Run("GetTask", func(t *testing.T) {
		task, err := client.GetTask(ctx, "task-1")
		if err != nil {
			t.Fatalf("GetTask failed: %v", err)
		}
		if task == nil {
			t.Fatal("task is nil")
		}
		if task.ID != "task-1" {
			t.Errorf("expected task-1, got %s", task.ID)
		}
	})

	t.Run("GetTask_NotFound", func(t *testing.T) {
		_, err := client.GetTask(ctx, "nonexistent")
		if err == nil {
			t.Error("expected error for nonexistent task")
		}
	})

	t.Run("CreateTask", func(t *testing.T) {
		req := &CreateTaskRequest{
			Subject:     "New Task",
			Description: "Task description",
			Priority:    "high",
		}
		task, err := client.CreateTask(ctx, req)
		if err != nil {
			t.Fatalf("CreateTask failed: %v", err)
		}
		if task == nil {
			t.Fatal("task is nil")
		}
		if task.ID != "new-task-123" {
			t.Errorf("expected new-task-123, got %s", task.ID)
		}
	})

	t.Run("ClaimTask", func(t *testing.T) {
		task, err := client.ClaimTask(ctx, "task-1", "forge:kimi")
		if err != nil {
			t.Fatalf("ClaimTask failed: %v", err)
		}
		if task == nil {
			t.Fatal("task is nil")
		}
		if task.Status != "claimed" {
			t.Errorf("expected claimed, got %s", task.Status)
		}
	})

	t.Run("CompleteTask", func(t *testing.T) {
		task, err := client.CompleteTask(ctx, "task-1", "Task completed successfully")
		if err != nil {
			t.Fatalf("CompleteTask failed: %v", err)
		}
		if task == nil {
			t.Fatal("task is nil")
		}
		if task.Status != "completed" {
			t.Errorf("expected completed, got %s", task.Status)
		}
	})

	t.Run("ListProjects", func(t *testing.T) {
		result, err := client.ListProjects(ctx, "")
		if err != nil {
			t.Fatalf("ListProjects failed: %v", err)
		}
		if result == nil {
			t.Fatal("result is nil")
		}
		if result.Count != 1 {
			t.Errorf("expected 1 project, got %d", result.Count)
		}
	})

	t.Run("GetProject", func(t *testing.T) {
		project, err := client.GetProject(ctx, "test-project")
		if err != nil {
			t.Fatalf("GetProject failed: %v", err)
		}
		if project == nil {
			t.Fatal("project is nil")
		}
		if project.Key != "test-project" {
			t.Errorf("expected test-project, got %s", project.Key)
		}
	})

	t.Run("ListLanes", func(t *testing.T) {
		lanes, err := client.ListLanes(ctx)
		if err != nil {
			t.Fatalf("ListLanes failed: %v", err)
		}
		if lanes == nil {
			t.Fatal("lanes is nil")
		}
		if lanes.Count != 2 {
			t.Errorf("expected 2 lanes, got %d", lanes.Count)
		}
	})

	t.Run("ListContexts", func(t *testing.T) {
		contexts, err := client.ListContexts(ctx, "test-domain", "")
		if err != nil {
			t.Fatalf("ListContexts failed: %v", err)
		}
		if contexts == nil {
			t.Fatal("contexts is nil")
		}
		if contexts.Count != 1 {
			t.Errorf("expected 1 context, got %d", contexts.Count)
		}
	})

	t.Run("ListAgents", func(t *testing.T) {
		agents, err := client.ListAgents(ctx)
		if err != nil {
			t.Fatalf("ListAgents failed: %v", err)
		}
		if agents == nil {
			t.Fatal("agents is nil")
		}
		if agents.Count != 2 {
			t.Errorf("expected 2 agents, got %d", agents.Count)
		}
		if len(agents.Agents) != 2 {
			t.Errorf("expected 2 agents, got %d", len(agents.Agents))
		}
		if agents.Agents[0].ID == "" {
			t.Errorf("expected agent id to be set")
		}
	})

	t.Run("GetAgent", func(t *testing.T) {
		agent, err := client.GetAgent(ctx, "forge:kimi")
		if err != nil {
			t.Fatalf("GetAgent failed: %v", err)
		}
		if agent == nil {
			t.Fatal("agent is nil")
		}
		if agent.AgentID != "forge:kimi" {
			t.Errorf("expected forge:kimi, got %s", agent.AgentID)
		}
	})

	t.Run("GetAgentTasks", func(t *testing.T) {
		tasks, err := client.GetAgentTasks(ctx, "forge:kimi")
		if err != nil {
			t.Fatalf("GetAgentTasks failed: %v", err)
		}
		if tasks == nil {
			t.Fatal("tasks is nil")
		}
		if len(tasks.Tasks) != 1 {
			t.Errorf("expected 1 task, got %d", len(tasks.Tasks))
		}
	})

	t.Run("PromoteTask", func(t *testing.T) {
		err := client.PromoteTask(ctx, "task-1", &PromoteTaskRequest{TargetLane: "test"})
		if err != nil {
			t.Fatalf("PromoteTask failed: %v", err)
		}
	})

	// GetGateStatus removed - method not implemented in V3 API
}

// Helper handlers for mock server
func handleTasksEndpoint(w http.ResponseWriter, r *http.Request) {
	tasks := []Task{
		{ID: "task-1", Title: "Test Task", Status: "pending"},
		{ID: "task-2", Title: "Another Task", Status: "queued"},
	}
	json.NewEncoder(w).Encode(tasks)
}

func handleTaskEndpoint(w http.ResponseWriter, r *http.Request) {
	taskID := r.URL.Path[7:] // Extract task ID from "/tasks/..."

	if r.Method == http.MethodGet {
		if taskID == "task-1" || taskID == "task-2" {
			task := Task{
				ID:          taskID,
				Title:       "Test Task",
				Description: "Test description",
				Status:      "pending",
			}
			json.NewEncoder(w).Encode(task)
			return
		}
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(ErrorResponse{Error: "not found", Status: 404})
		return
	}

	if r.Method == http.MethodPost {
		// Handle claim, complete, promote
		if len(taskID) > 9 && taskID[len(taskID)-9:] == "/complete" {
			task := Task{ID: taskID[:len(taskID)-10], Status: "completed"}
			json.NewEncoder(w).Encode(task)
			return
		}
		if len(taskID) > 6 && taskID[len(taskID)-6:] == "/claim" {
			task := Task{ID: taskID[:len(taskID)-7], Status: "claimed"}
			json.NewEncoder(w).Encode(map[string]interface{}{"task": task})
			return
		}
		if len(taskID) > 8 && taskID[len(taskID)-8:] == "/promote" {
			w.WriteHeader(http.StatusOK)
			return
		}
		// Unknown POST to /tasks/xxx
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(ErrorResponse{Error: "not found", Status: 404})
		return
	}
}

func handleProjectsEndpoint(w http.ResponseWriter, r *http.Request) {
	projects := []Project{
		{ID: "proj-1", Key: "test-project", Name: "Test Project", Domain: "test-domain"},
	}
	json.NewEncoder(w).Encode(projects)
}

func handleProjectEndpoint(w http.ResponseWriter, r *http.Request) {
	projectKey := r.URL.Path[10:]
	project := Project{
		ID:     "proj-1",
		Key:    projectKey,
		Name:   "Test Project",
		Domain: "test-domain",
	}
	json.NewEncoder(w).Encode(project)
}

func handleLanesEndpoint(w http.ResponseWriter, r *http.Request) {
	lanes := []Lane{
		{ID: "backlog", Name: "Backlog"},
		{ID: "dev", Name: "Development"},
	}
	json.NewEncoder(w).Encode(lanes)
}

func handleCreateTask(w http.ResponseWriter, r *http.Request) {
	var req CreateTaskRequest
	json.NewDecoder(r.Body).Decode(&req)
	w.WriteHeader(http.StatusCreated)
	task := Task{
		ID:          "new-task-123",
		Title:       req.Subject,
		Description: req.Description,
		Status:      "queued",
	}
	json.NewEncoder(w).Encode(task)
}

func handleContextsEndpoint(w http.ResponseWriter, r *http.Request) {
	contexts := []Context{
		{ID: "ctx-1", Key: "test-context", Type: "envelope", Domain: "test-domain"},
	}
	json.NewEncoder(w).Encode(contexts)
}

func handleContextEndpoint(w http.ResponseWriter, r *http.Request) {
	contextKey := r.URL.Path[10:]
	ctx := Context{
		ID:     "ctx-1",
		Key:    contextKey,
		Type:   "envelope",
		Domain: "test-domain",
	}
	json.NewEncoder(w).Encode(ctx)
}

func handleLaneEndpoint(w http.ResponseWriter, r *http.Request) {
	laneKey := r.URL.Path[6:]
	lane := Lane{
		ID:   laneKey,
		Name: "Development",
	}
	if laneKey == "dev" {
		lane.Name = "Development"
	}
	json.NewEncoder(w).Encode(lane)
}

func handleAgentsEndpoint(w http.ResponseWriter, r *http.Request) {
	now := time.Now().UTC()
	agents := []map[string]interface{}{
		{
			"agent_id":        "forge:kimi",
			"node":            "sati",
			"status":          "idle",
			"current_task_id": nil,
			"context_pct":     12.5,
			"capabilities":    []string{"go", "tests"},
			"last_seen":       now,
			"connected_at":    now.Add(-1 * time.Minute),
		},
		{
			"agent_id":        "forge:glm",
			"node":            "prya",
			"status":          "working",
			"current_task_id": "TASK-123",
			"context_pct":     33.0,
			"capabilities":    []string{"go"},
			"last_seen":       now,
			"connected_at":    now.Add(-5 * time.Minute),
		},
	}
	json.NewEncoder(w).Encode(map[string]interface{}{
		"agents": agents,
		"count":  len(agents),
	})
}

func handleAgentEndpoint(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path[len("/agents/"):]
	if strings.HasSuffix(path, "/tasks") {
		// /agents/{id}/tasks
		json.NewEncoder(w).Encode(map[string]interface{}{
			"tasks": []Task{{ID: "task-1", Title: "Agent Task", Status: "queued"}},
			"count": 1,
		})
		return
	}

	agentID := path
	if agentID == "" {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(ErrorResponse{Error: "not found", Status: 404})
		return
	}

	now := time.Now().UTC()
	json.NewEncoder(w).Encode(map[string]interface{}{
		"agent_id":        agentID,
		"node":            "sati",
		"status":          "idle",
		"current_task_id": nil,
		"context_pct":     10.0,
		"capabilities":    []string{"go"},
		"last_seen":       now,
		"connected_at":    now.Add(-2 * time.Minute),
	})
}

// TestClientErrors tests error handling
func TestClientErrors(t *testing.T) {
	t.Run("ServerError", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "internal error"})
		}))
		defer server.Close()

		client := NewClientWithURL(server.URL)
		ctx := context.Background()

		_, err := client.ListTasks(ctx, "", 10, "")
		if err == nil {
			t.Error("expected error for 500 response")
		}
	})

	t.Run("Timeout", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			time.Sleep(2 * time.Second)
		}))
		defer server.Close()

		client := NewClientWithURL(server.URL)
		ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
		defer cancel()

		_, err := client.ListTasks(ctx, "", 10, "")
		if err == nil {
			t.Error("expected timeout error")
		}
	})

	t.Run("InvalidJSON", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte("invalid json"))
		}))
		defer server.Close()

		client := NewClientWithURL(server.URL)
		ctx := context.Background()

		_, err := client.ListTasks(ctx, "", 10, "")
		if err == nil {
			t.Error("expected error for invalid JSON")
		}
	})

	t.Run("ConnectionRefused", func(t *testing.T) {
		client := NewClientWithURL("http://localhost:59999")
		ctx, cancel := context.WithTimeout(context.Background(), 1*time.Second)
		defer cancel()

		_, err := client.ListTasks(ctx, "", 10, "")
		if err == nil {
			t.Error("expected connection error")
		}
	})

	t.Run("NotFound", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
		}))
		defer server.Close()

		client := NewClientWithURL(server.URL)
		ctx := context.Background()

		_, err := client.GetTask(ctx, "nonexistent")
		if err == nil {
			t.Error("expected error for 404 response")
		}
	})
}

// TestClientFactory tests client creation
func TestClientFactory(t *testing.T) {
	t.Run("NewClient_DefaultURL", func(t *testing.T) {
		t.Setenv("HOME", t.TempDir())
		t.Setenv("FORGE_API_URL", "")
		t.Chdir(t.TempDir())

		client := NewClient()
		if client.BaseURL() != "http://localhost:8081/api" {
			t.Errorf("expected http://localhost:8081/api, got %s", client.BaseURL())
		}
	})

	t.Run("NewClientWithURL_CustomURL", func(t *testing.T) {
		client := NewClientWithURL("http://custom:9090")
		if client.BaseURL() != "http://custom:9090" {
			t.Errorf("expected http://custom:9090, got %s", client.BaseURL())
		}
	})
}

// TestHTTPMethods tests low-level HTTP methods
func TestHTTPMethods(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.Method {
		case http.MethodGet:
			w.Write([]byte(`{"method": "GET"}`))
		case http.MethodPost:
			w.Write([]byte(`{"method": "POST"}`))
		case http.MethodPut:
			w.Write([]byte(`{"method": "PUT"}`))
		case http.MethodDelete:
			w.Write([]byte(`{"method": "DELETE"}`))
		}
	}))
	defer server.Close()

	client := NewClientWithURL(server.URL)
	ctx := context.Background()

	t.Run("Get", func(t *testing.T) {
		resp, err := client.Get(ctx, "/test")
		if err != nil {
			t.Fatalf("Get failed: %v", err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Errorf("expected 200, got %d", resp.StatusCode)
		}
	})

	t.Run("Post", func(t *testing.T) {
		resp, err := client.Post(ctx, "/test", map[string]string{"key": "value"})
		if err != nil {
			t.Fatalf("Post failed: %v", err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Errorf("expected 200, got %d", resp.StatusCode)
		}
	})

	t.Run("Put", func(t *testing.T) {
		resp, err := client.Put(ctx, "/test", map[string]string{"key": "value"})
		if err != nil {
			t.Fatalf("Put failed: %v", err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Errorf("expected 200, got %d", resp.StatusCode)
		}
	})

	t.Run("Delete", func(t *testing.T) {
		resp, err := client.Delete(ctx, "/test")
		if err != nil {
			t.Fatalf("Delete failed: %v", err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Errorf("expected 200, got %d", resp.StatusCode)
		}
	})

	t.Run("PostNilBody", func(t *testing.T) {
		resp, err := client.Post(ctx, "/test", nil)
		if err != nil {
			t.Fatalf("Post with nil body failed: %v", err)
		}
		defer resp.Body.Close()
	})
}

// TestCheckResponse tests response checking
func TestCheckResponse(t *testing.T) {
	t.Run("SuccessResponse", func(t *testing.T) {
		resp := &http.Response{StatusCode: 200}
		if err := CheckResponse(resp); err != nil {
			t.Errorf("expected no error for 200, got %v", err)
		}
	})

	t.Run("CreatedResponse", func(t *testing.T) {
		resp := &http.Response{StatusCode: 201}
		if err := CheckResponse(resp); err != nil {
			t.Errorf("expected no error for 201, got %v", err)
		}
	})

	t.Run("ErrorResponse", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "bad request"})
		}))
		defer server.Close()

		resp, err := http.Get(server.URL)
		if err != nil {
			t.Fatalf("failed to get response: %v", err)
		}
		defer resp.Body.Close()

		if err := CheckResponse(resp); err == nil {
			t.Error("expected error for 400 response")
		}
	})
}

// TestDecodeJSON tests JSON decoding
func TestDecodeJSON(t *testing.T) {
	t.Run("ValidJSON", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte(`{"key": "value"}`))
		}))
		defer server.Close()

		resp, err := http.Get(server.URL)
		if err != nil {
			t.Fatalf("failed to get response: %v", err)
		}
		defer resp.Body.Close()

		var result map[string]string
		if err := DecodeJSON(resp, &result); err != nil {
			t.Errorf("DecodeJSON failed: %v", err)
		}
		if result["key"] != "value" {
			t.Errorf("expected value, got %s", result["key"])
		}
	})

	t.Run("NoContent", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusNoContent)
		}))
		defer server.Close()

		resp, err := http.Get(server.URL)
		if err != nil {
			t.Fatalf("failed to get response: %v", err)
		}
		defer resp.Body.Close()

		var result map[string]string
		if err := DecodeJSON(resp, &result); err != nil {
			t.Errorf("DecodeJSON failed for no content: %v", err)
		}
	})
}

// TestConfigOperations tests config operations
func TestConfigOperations(t *testing.T) {
	client := NewClient()
	ctx := context.Background()

	t.Run("GetConfig", func(t *testing.T) {
		_, err := client.GetConfig(ctx)
		if err == nil {
			t.Error("expected error for config API not available")
		}
	})

	t.Run("SetConfig", func(t *testing.T) {
		err := client.SetConfig(ctx, "key", "value")
		if err == nil {
			t.Error("expected error for config API not available")
		}
	})
}

// TestContextOperations tests context operations
func TestContextOperations(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/contexts/test-context" {
			json.NewEncoder(w).Encode(Context{
				ID: "ctx-1", Key: "test-context", Type: "envelope", Domain: "test-domain",
			})
			return
		}
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
	}))
	defer server.Close()

	client := NewClientWithURL(server.URL)
	ctx := context.Background()

	t.Run("GetContext", func(t *testing.T) {
		context, err := client.GetContext(ctx, "test-context")
		if err != nil {
			t.Fatalf("GetContext failed: %v", err)
		}
		if context == nil {
			t.Fatal("context is nil")
		}
		if context.Key != "test-context" {
			t.Errorf("expected test-context, got %s", context.Key)
		}
	})
}

// TestLaneOperations tests lane operations
func TestLaneOperations(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/lanes/dev" {
			json.NewEncoder(w).Encode(Lane{
				ID: "dev", Name: "Development",
			})
			return
		}
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
	}))
	defer server.Close()

	client := NewClientWithURL(server.URL)
	ctx := context.Background()

	t.Run("GetLane", func(t *testing.T) {
		lane, err := client.GetLane(ctx, "dev")
		if err != nil {
			t.Fatalf("GetLane failed: %v", err)
		}
		if lane == nil {
			t.Fatal("lane is nil")
		}
		if lane.ID != "dev" {
			t.Errorf("expected dev, got %s", lane.ID)
		}
	})
}

// TestProjectCreate tests project creation
func TestProjectCreate(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && r.URL.Path == "/projects" {
			w.WriteHeader(http.StatusCreated)
			json.NewEncoder(w).Encode(Project{
				ID:     "proj-new",
				Key:    "new-project",
				Name:   "New Project",
				Domain: "test-domain",
			})
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	client := NewClientWithURL(server.URL)
	ctx := context.Background()

	t.Run("CreateProject", func(t *testing.T) {
		req := &CreateProjectRequest{
			Key:         "new-project",
			Name:        "New Project",
			Domain:      "test-domain",
			Description: "Test description",
		}
		project, err := client.CreateProject(ctx, req)
		if err != nil {
			t.Fatalf("CreateProject failed: %v", err)
		}
		if project == nil {
			t.Fatal("project is nil")
		}
		if project.Key != "new-project" {
			t.Errorf("expected new-project, got %s", project.Key)
		}
	})
}

// TestContextCreate tests context creation
func TestContextCreate(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && r.URL.Path == "/contexts" {
			w.WriteHeader(http.StatusCreated)
			json.NewEncoder(w).Encode(Context{
				ID:      "ctx-new",
				Key:     "new-context",
				Type:    "envelope",
				Content: "Test content",
			})
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	client := NewClientWithURL(server.URL)
	ctx := context.Background()

	t.Run("CreateContext", func(t *testing.T) {
		req := &CreateContextRequest{
			Key:     "new-context",
			Domain:  "test-domain",
			Type:    "envelope",
			Content: "Test content",
		}
		context, err := client.CreateContext(ctx, req)
		if err != nil {
			t.Fatalf("CreateContext failed: %v", err)
		}
		if context == nil {
			t.Fatal("context is nil")
		}
		if context.Key != "new-context" {
			t.Errorf("expected new-context, got %s", context.Key)
		}
	})
}

// TestUpdateTask tests task update (not implemented)
func TestUpdateTask(t *testing.T) {
	client := NewClient()
	ctx := context.Background()

	t.Run("UpdateTask_NotImplemented", func(t *testing.T) {
		_, err := client.UpdateTask(ctx, "task-1", &UpdateTaskRequest{})
		if err == nil {
			t.Error("expected error for not implemented")
		}
	})
}

// TestDeleteTask tests task deletion
func TestDeleteTask(t *testing.T) {
	ctx := context.Background()

	t.Run("DeleteTask_Success", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodDelete || r.URL.Path != "/tasks/task-1" {
				http.Error(w, "unexpected request", http.StatusBadRequest)
				return
			}
			w.WriteHeader(http.StatusNoContent)
		}))
		defer server.Close()
		client := NewClientWithURL(server.URL)
		err := client.DeleteTask(ctx, "task-1")
		if err != nil {
			t.Errorf("expected success, got: %v", err)
		}
	})

	t.Run("DeleteTask_Error", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Error(w, "not found", http.StatusNotFound)
		}))
		defer server.Close()
		client := NewClientWithURL(server.URL)
		err := client.DeleteTask(ctx, "task-missing")
		if err == nil {
			t.Error("expected error for 404 response")
		}
	})
}

// TestDaemonStatus tests daemon status
func TestDaemonStatus(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health" {
			json.NewEncoder(w).Encode(DaemonStatus{
				Status:  "running",
				Version: "3.0.0",
				Uptime:  "1h",
			})
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	client := NewClientWithURL(server.URL)
	ctx := context.Background()

	t.Run("GetDaemonStatus", func(t *testing.T) {
		status, err := client.GetDaemonStatus(ctx)
		if err != nil {
			t.Fatalf("GetDaemonStatus failed: %v", err)
		}
		if status == nil {
			t.Fatal("status is nil")
		}
		if status.Status != "running" {
			t.Errorf("expected running, got %s", status.Status)
		}
	})
}

// TestPriorityFromLabel tests priority label conversion to integer values
func TestPriorityFromLabel(t *testing.T) {
	cases := []struct {
		label string
		want  int
	}{
		{"low", 3},
		{"medium", 5},
		{"high", 7},
		{"critical", 10},
		{"urgent", 8},
		{"MEDIUM", 5},
		{"  high  ", 7},
		{"unknown", 5},
		{"", 5},
	}
	for _, c := range cases {
		t.Run(c.label, func(t *testing.T) {
			if got := PriorityFromLabel(c.label); got != c.want {
				t.Errorf("PriorityFromLabel(%q) = %d, want %d", c.label, got, c.want)
			}
		})
	}
}

// TestPriorityToLabel tests integer priority conversion to display labels
func TestPriorityToLabel(t *testing.T) {
	cases := []struct {
		p    int
		want string
	}{
		{10, "critical"},
		{9, "critical"},
		{8, "high"},
		{7, "high"},
		{6, "medium"},
		{5, "medium"},
		{4, "low"},
		{3, "low"},
		{1, "low"},
		{0, "low"},
	}
	for _, c := range cases {
		t.Run(fmt.Sprintf("priority_%d", c.p), func(t *testing.T) {
			if got := PriorityToLabel(c.p); got != c.want {
				t.Errorf("PriorityToLabel(%d) = %q, want %q", c.p, got, c.want)
			}
		})
	}
}
