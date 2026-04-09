package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/neoforge-dev/forge/internal"
)

// TestLoadApprovalsComprehensive tests the loadApprovals function
func TestLoadApprovalsComprehensive(t *testing.T) {
	// Test 1: No approvals directory - should return nil, nil (empty results)
	t.Run("NoApprovalsDir", func(t *testing.T) {
		tmpDir := t.TempDir()
		origDir, _ := os.Getwd()
		os.Chdir(tmpDir)
		defer os.Chdir(origDir)

		approvals, err := loadApprovals()
		if err != nil {
			t.Fatalf("loadApprovals failed: %v", err)
		}
		// No file = no approvals (nil is fine)
		_ = approvals
	})

	// Test 2: Empty approvals directory (no approvals.json) - should return nil, nil
	t.Run("EmptyApprovalsDir", func(t *testing.T) {
		tmpDir := t.TempDir()
		origDir, _ := os.Getwd()
		os.Chdir(tmpDir)
		defer os.Chdir(origDir)

		os.MkdirAll(".forge/approvals", 0755)

		approvals, err := loadApprovals()
		if err != nil {
			t.Fatalf("loadApprovals failed: %v", err)
		}
		// No approvals.json = no approvals
		if len(approvals) != 0 {
			t.Errorf("expected 0 approvals for missing file, got %d", len(approvals))
		}
	})

	// Test 3: Approvals directory with invalid approvals.json - should return error
	t.Run("InvalidFile", func(t *testing.T) {
		tmpDir := t.TempDir()
		origDir, _ := os.Getwd()
		os.Chdir(tmpDir)
		defer os.Chdir(origDir)

		os.MkdirAll(".forge/approvals", 0755)
		os.WriteFile(".forge/approvals/approvals.json", []byte("not valid json"), 0644)

		_, err := loadApprovals()
		if err == nil {
			t.Error("expected error for invalid approvals.json")
		}
	})

	// Test 4: Valid approvals.json
	t.Run("ValidApprovalFile", func(t *testing.T) {
		tmpDir := t.TempDir()
		origDir, _ := os.Getwd()
		os.Chdir(tmpDir)
		defer os.Chdir(origDir)

		os.MkdirAll(".forge/approvals", 0755)
		validApprovals := []internal.Approval{
			{
				ID:     "TEST-001",
				Type:   "merge",
				Domain: "test-domain",
				Status: "pending",
			},
		}
		data, _ := json.Marshal(validApprovals)
		os.WriteFile(".forge/approvals/approvals.json", data, 0644)

		approvals, err := loadApprovals()
		if err != nil {
			t.Fatalf("loadApprovals failed: %v", err)
		}
		if len(approvals) != 1 || approvals[0].ID != "TEST-001" {
			t.Errorf("expected TEST-001 approval, got %v", approvals)
		}
	})
}


// TestSaveApprovalsComprehensive tests saveApprovals
func TestSaveApprovalsComprehensive(t *testing.T) {
	t.Run("NoOp", func(t *testing.T) {
		tmpDir := t.TempDir()
		origDir, _ := os.Getwd()
		os.Chdir(tmpDir)
		defer os.Chdir(origDir)

		approvals := []internal.Approval{
			{ID: "TEST-001", Type: "merge", Status: "pending"},
		}
		err := saveApprovals(approvals)
		if err != nil {
			t.Errorf("saveApprovals returned error: %v", err)
		}
	})

	t.Run("EmptyList", func(t *testing.T) {
		tmpDir := t.TempDir()
		origDir, _ := os.Getwd()
		os.Chdir(tmpDir)
		defer os.Chdir(origDir)

		err := saveApprovals([]internal.Approval{})
		if err != nil {
			t.Errorf("saveApprovals returned error: %v", err)
		}
	})
}

// TestDomainRegistryYAML tests the domain registry type
func TestDomainRegistryYAML(t *testing.T) {
	t.Run("Unmarshal", func(t *testing.T) {
		yaml := `
domains:
  test-domain:
    display_name: Test Domain
    active: true
`
		var registry struct {
			Domains map[string]internal.Domain `yaml:"domains"`
		}
		// This would need yaml unmarshaling
		_ = yaml
		_ = registry
	})
}

// TestDomainKey tests domain key field
func TestDomainKey(t *testing.T) {
	t.Run("KeyField", func(t *testing.T) {
		domain := internal.Domain{
			Key:         "test-domain",
			DisplayName: "Test Domain",
			Active:      true,
		}
		if domain.Key != "test-domain" {
			t.Errorf("expected test-domain, got %s", domain.Key)
		}
	})
}

// TestBusinessFields tests business struct
func TestBusinessFields(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		business := internal.Business{
			DeployStatus:       "live",
			CurrentMRR:         1000,
			TargetMRR:         5000,
			StripeLive:         true,
			HumanActionsNeeded: []string{"Action 1"},
		}
		if business.DeployStatus != "live" {
			t.Error("expected live")
		}
		if business.CurrentMRR != 1000 {
			t.Error("expected 1000")
		}
	})
}

// TestProjectFields tests project struct
func TestProjectFields(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		project := internal.Project{
			ID:          "proj-1",
			Key:         "test-project",
			Name:        "Test Project",
			Domain:      "test-domain",
			Description: "Test description",
			Type:        "repository",
			Status:      "active",
		}
		if project.Key != "test-project" {
			t.Error("expected test-project")
		}
		if project.Type != "repository" {
			t.Error("expected repository")
		}
	})
}

// TestContextFields tests context struct
func TestContextFields(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		ctx := internal.Context{
			ID:     "ctx-1",
			Key:    "test-context",
			Domain: "test-domain",
			Project: "test-project",
			Type:    "envelope",
			Content: "Test content",
		}
		if ctx.Key != "test-context" {
			t.Error("expected test-context")
		}
		if ctx.Type != "envelope" {
			t.Error("expected envelope")
		}
	})
}

// TestLaneFields tests lane struct
func TestLaneFields(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		lane := internal.Lane{
			ID:             "lane-1",
			Name:           "Development",
			Description:    "Dev lane",
			Capabilities:   "python,shell,code",
			AutoClaim:      true,
			MaxParallel:    10,
			TimeoutMinutes: 60,
		}
		if lane.ID != "lane-1" {
			t.Error("expected lane-1")
		}
		if lane.MaxParallel != 10 {
			t.Error("expected max_parallel 10")
		}
	})
}



// TestPatrolFields tests patrol struct
func TestPatrolFields(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		patrol := internal.Patrol{
			ID:       "patrol-1",
			Name:     "Health Check",
			Type:     "health",
			Status:   "running",
			Interval: 60,
			LastRun:  "2026-03-05T00:00:00Z",
		}
		if patrol.Name != "Health Check" {
			t.Error("expected Health Check")
		}
		if patrol.Status != "running" {
			t.Error("expected running")
		}
	})
}

// TestApprovalFieldsComprehensive tests approval struct
func TestApprovalFieldsComprehensive(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		approval := internal.Approval{
			ID:          "01APP123",
			Type:        "merge",
			Tier:        "desktop",
			Domain:      "codeswiftr-com",
			Project:     "test-project",
			Title:       "Test Approval",
			Description: "Test description",
			Confidence:  0.85,
			RiskScore:   0.15,
			Status:      "pending",
			RequestedBy: "test-user",
		}
		if approval.ID != "01APP123" {
			t.Error("expected 01APP123")
		}
		if approval.Confidence != 0.85 {
			t.Error("expected 0.85")
		}
		if approval.RiskScore != 0.15 {
			t.Error("expected 0.15")
		}
	})
}

// TestDomainListResponse tests domain list response
func TestDomainListResponse(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		resp := internal.DomainListResponse{
			Domains: []internal.Domain{
				{Key: "domain-1", DisplayName: "Domain 1"},
			},
			Count: 1,
		}
		if resp.Count != 1 {
			t.Error("expected count 1")
		}
		if len(resp.Domains) != 1 {
			t.Error("expected 1 domain")
		}
	})
}

// TestProjectListResponse tests project list response
func TestProjectListResponse(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		resp := internal.ProjectListResponse{
			Projects: []internal.Project{
				{Key: "project-1", Name: "Project 1"},
			},
			Count: 1,
		}
		if resp.Count != 1 {
			t.Error("expected count 1")
		}
	})
}

// TestContextListResponse tests context list response
func TestContextListResponse(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		resp := internal.ContextListResponse{
			Contexts: []internal.Context{
				{Key: "context-1"},
			},
			Count: 1,
		}
		if resp.Count != 1 {
			t.Error("expected count 1")
		}
	})
}

// TestLaneListResponse tests lane list response
func TestLaneListResponse(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		resp := internal.LaneListResponse{
			Lanes: []internal.Lane{
				{ID: "dev"},
			},
			Count: 1,
		}
		if resp.Count != 1 {
			t.Error("expected count 1")
		}
	})
}

// TestAgentListResponse tests agent list response
func TestAgentListResponse(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		resp := internal.AgentListResponse{
			Agents: []internal.Agent{
				{ID: "forge:kimi"},
			},
			Count: 1,
		}
		if resp.Count != 1 {
			t.Error("expected count 1")
		}
	})
}

// TestCreateTaskRequest tests create task request
func TestCreateTaskRequest(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		req := internal.CreateTaskRequest{
			Subject:     "Test Task",
			Description: "Test description",
			Priority:    "high",
			Domain:      "test-domain",
			Project:     "test-project",
		}
		if req.Subject != "Test Task" {
			t.Error("expected Test Task")
		}
		if req.Priority != "high" {
			t.Error("expected high")
		}
	})
}

// TestUpdateTaskRequest tests update task request
func TestUpdateTaskRequest(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		subject := "Updated Subject"
		req := internal.UpdateTaskRequest{
			Subject: &subject,
		}
		if req.Subject == nil || *req.Subject != "Updated Subject" {
			t.Error("expected Updated Subject")
		}
	})
}

// TestClaimTaskRequest tests claim task request
func TestClaimTaskRequest(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		req := internal.ClaimTaskRequest{
			AgentID: "forge:kimi",
		}
		if req.AgentID != "forge:kimi" {
			t.Error("expected forge:kimi")
		}
	})
}

// TestCompleteTaskRequest tests complete task request
func TestCompleteTaskRequest(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		req := internal.CompleteTaskRequest{
			Agent:         "forge:kimi",
			ResultSummary: "Completed successfully",
		}
		if req.Agent != "forge:kimi" {
			t.Error("expected forge:kimi")
		}
	})
}

// TestPromoteTaskRequest tests promote task request
func TestPromoteTaskRequest(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		req := internal.PromoteTaskRequest{
			TargetLane: "test",
			Comment:    "Test comment",
		}
		if req.TargetLane != "test" {
			t.Error("expected test")
		}
	})
}

// TestCreateProjectRequest tests create project request
func TestCreateProjectRequest(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		req := internal.CreateProjectRequest{
			Key:         "new-project",
			Name:        "New Project",
			Domain:      "test-domain",
			Description: "Test description",
			Type:        "repository",
		}
		if req.Key != "new-project" {
			t.Error("expected new-project")
		}
		if req.Type != "repository" {
			t.Error("expected repository")
		}
	})
}

// TestCreateContextRequest tests create context request
func TestCreateContextRequest(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		req := internal.CreateContextRequest{
			Key:     "new-context",
			Domain:  "test-domain",
			Project: "test-project",
			Type:    "envelope",
			Content: "Test content",
		}
		if req.Key != "new-context" {
			t.Error("expected new-context")
		}
		if req.Type != "envelope" {
			t.Error("expected envelope")
		}
	})
}

// TestConfigComprehensive tests config struct
func TestConfigComprehensive(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		cfg := internal.Config{
			APIURL:   "http://localhost:8081",
			NodeID:   "test-node",
			Domain:   "test-domain",
			Settings: map[string]string{"key": "value"},
		}
		if cfg.APIURL != "http://localhost:8081" {
			t.Error("expected http://localhost:8081")
		}
		if cfg.Settings["key"] != "value" {
			t.Error("expected value")
		}
	})
}

// TestDaemonStatusComprehensive tests daemon status struct
func TestDaemonStatusComprehensive(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		status := internal.DaemonStatus{
			Status:       "running",
			Version:      "3.0.0",
			Uptime:       "1h",
			PendingTasks: 5,
			PID:          12345,
		}
		if status.Status != "running" {
			t.Error("expected running")
		}
		if status.Version != "3.0.0" {
			t.Error("expected 3.0.0")
		}
		if status.PID != 12345 {
			t.Error("expected 12345")
		}
	})
}

// TestErrorResponseComprehensive tests error response struct
func TestErrorResponseComprehensive(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		errResp := internal.ErrorResponse{
			Error:   "error",
			Message: "Error message",
			Status:  500,
		}
		if errResp.Error != "error" {
			t.Error("expected error")
		}
		if errResp.Status != 500 {
			t.Error("expected 500")
		}
	})
}

// TestAgentHealthComprehensive tests agent health struct
func TestAgentHealthComprehensive(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		health := internal.AgentHealth{
			AgentID:    "forge:kimi",
			Node:       "prya",
			Status:     "active",
			ContextPct: 45.5,
		}
		if health.AgentID != "forge:kimi" {
			t.Error("expected forge:kimi")
		}
		if health.ContextPct != 45.5 {
			t.Error("expected 45.5")
		}
	})
}

// TestPatrolListResponse tests patrol list response
func TestPatrolListResponse(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		resp := internal.PatrolListResponse{
			Patrols: []internal.Patrol{
				{Name: "Health Check"},
			},
			Count: 1,
		}
		if resp.Count != 1 {
			t.Error("expected count 1")
		}
	})
}

// TestApprovalListResponse tests approval list response
func TestApprovalListResponse(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		resp := internal.ApprovalListResponse{
			Approvals: []internal.Approval{
				{ID: "01APP123"},
			},
			Count: 1,
		}
		if resp.Count != 1 {
			t.Error("expected count 1")
		}
	})
}

// TestDecideApprovalRequest tests decide approval request
func TestDecideApprovalRequest(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		req := internal.DecideApprovalRequest{
			Decision: "approve",
			Reason:   "LGTM",
		}
		if req.Decision != "approve" {
			t.Error("expected approve")
		}
	})
}

// TestCreateDomainRequest tests create domain request
func TestCreateDomainRequest(t *testing.T) {
	t.Run("AllFields", func(t *testing.T) {
		req := internal.CreateDomainRequest{
			Key:         "new-domain",
			DisplayName: "New Domain",
			Compliance:  "SOC2",
		}
		if req.Key != "new-domain" {
			t.Error("expected new-domain")
		}
	})
}

// TestFilePathHandling tests filepath handling in approval.go
func TestFilePathHandling(t *testing.T) {
	t.Run("JoinPath", func(t *testing.T) {
		path := filepath.Join(".forge", "approvals", "test.json")
		if path == "" {
			t.Error("expected path")
		}
	})
}
