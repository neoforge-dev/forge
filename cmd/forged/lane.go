//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"time"
)

// Lane represents a stage in the Dark Factory progression
type Lane string

const (
	LaneDev    Lane = "dev"
	LaneTest   Lane = "test"
	LaneDeploy Lane = "deploy"
	LaneDone   Lane = "done"
)

// GateResult represents the outcome of a lane gate check
type GateResult struct {
	TargetLane Lane               `json:"target_lane"`
	Passed     bool               `json:"passed"`
	Criteria   map[string]bool    `json:"criteria"`
	Errors     []string           `json:"errors"`
	Metrics    map[string]float64 `json:"metrics,omitempty"`
}

// LaneManager handles task progression between lanes
type LaneManager interface {
	ProgressTask(ctx context.Context, taskID string) (*Task, error)
	CheckGates(ctx context.Context, task *Task, targetLane Lane) (*GateResult, error)
	HandleLaneComplete(w http.ResponseWriter, r *http.Request)
	HandleLaneStatus(w http.ResponseWriter, r *http.Request)
}

type laneManager struct {
	queue           TaskQueue
	approvalService *ApprovalService
	laneConfigs     map[Lane]LaneConfig
}

// NewLaneManager creates a new lane manager
func NewLaneManager(queue TaskQueue, approvalService *ApprovalService, laneConfigs map[Lane]LaneConfig) LaneManager {
	return &laneManager{
		queue:           queue,
		approvalService: approvalService,
		laneConfigs:     laneConfigs,
	}
}

// ProgressTask moves a task to the next logical lane if gates pass
func (m *laneManager) ProgressTask(ctx context.Context, taskID string) (*Task, error) {
	log.Printf("[LaneManager] Progressing task %s", taskID)
	task, err := m.queue.GetTask(ctx, taskID)
	if err != nil {
		log.Printf("[LaneManager] Failed to get task %s: %v", taskID, err)
		return nil, err
	}
	log.Printf("[LaneManager] Current lane: %s", task.Lane)

	var nextLane Lane
	switch Lane(task.Lane) {
	case LaneDev:
		nextLane = LaneTest
	case LaneTest:
		nextLane = LaneDeploy
	case LaneDeploy:
		nextLane = LaneDone
	default:
		return nil, fmt.Errorf("task %s already in final lane or unknown lane: %s", taskID, task.Lane)
	}

	result, err := m.CheckGates(ctx, &task, nextLane)
	if err != nil {
		return nil, err
	}

	if !result.Passed {
		// Check if there is an existing approved request for this transition
		approvals, err := m.approvalService.store.ListByTask(ctx, task.ID, 10)
		if err == nil {
			for _, app := range approvals {
				if app.Type == ApprovalLane && app.Status == StatusApproved {
					log.Printf("[LaneManager] Found approved override for task %s", task.ID)
					goto proceed
				}
			}
		}

		// Create approval request if gate fails
		_, appErr := m.approvalService.CreateApprovalRequest(
			ctx,
			ApprovalLane,
			&task.ID,
			task.AssignedTo,
			task.Domain,
			fmt.Sprintf("Lane Transition: %s -> %s", task.Lane, nextLane),
			fmt.Sprintf("Gate criteria not met: %s", strings.Join(result.Errors, ", ")),
			nil,
			ConfidenceContext{
				Task:        &task,
				TestResults: nil, // Would fill this for test -> deploy
				BlastRadius: 10,
				Reversible:  true,
			},
		)
		if appErr != nil {
			log.Printf("ERROR: failed to create lane approval: %v", appErr)
		}

		return nil, fmt.Errorf("lane transition blocked by gates: %s", strings.Join(result.Errors, ", "))
	}

proceed:
	// Update task lane
	task.Lane = string(nextLane)
	task.UpdatedAt = time.Now()

	// Note: We use Enqueue to update the task in the DB
	err = m.queue.Enqueue(ctx, task)
	if err != nil {
		return nil, err
	}

	return &task, nil
}

// CheckGates evaluates criteria for entering a specific lane
func (m *laneManager) CheckGates(ctx context.Context, task *Task, targetLane Lane) (*GateResult, error) {
	result := &GateResult{
		TargetLane: targetLane,
		Passed:     true,
		Criteria:   make(map[string]bool),
		Errors:     []string{},
		Metrics:    make(map[string]float64),
	}

	config, ok := m.laneConfigs[targetLane]
	if !ok {
		return result, nil // No config, allow transition
	}

	// Get quality gate checker
	checker, err := NewQualityGateChecker("")
	if err != nil {
		return nil, err
	}

	for _, gate := range config.Gates {
		switch gate {
		case "coverage":
			coverage, err := checker.CheckCoverage(ctx)
			if err != nil {
				result.Criteria["coverage"] = false
				result.Errors = append(result.Errors, fmt.Sprintf("coverage check failed: %v", err))
			} else {
				result.Metrics["coverage"] = coverage
				threshold := config.Thresholds.Coverage
				if threshold == 0 {
					threshold = checker.config.Coverage.Threshold
				}
				result.Criteria["coverage"] = coverage >= threshold
				if !result.Criteria["coverage"] {
					result.Errors = append(result.Errors,
						fmt.Sprintf("Coverage (%.1f%%) below threshold (%.1f%%)",
							coverage, threshold))
				}
			}
		case "lint":
			issues, err := checker.CheckLint(ctx)
			if err != nil {
				result.Criteria["lint"] = false
				result.Errors = append(result.Errors, fmt.Sprintf("lint check failed: %v", err))
			} else {
				result.Metrics["lint_issues"] = float64(len(issues))
				maxIssues := config.Thresholds.MaxLintIssues
				result.Criteria["lint"] = len(issues) <= maxIssues
				if !result.Criteria["lint"] {
					result.Errors = append(result.Errors,
						fmt.Sprintf("Lint issues (%d) exceed max (%d)",
							len(issues), maxIssues))
				}
			}
		case "type_check":
			if err := checker.CheckTypeCheck(ctx); err != nil {
				result.Criteria["type_check"] = false
				result.Errors = append(result.Errors, err.Error())
			} else {
				result.Criteria["type_check"] = true
			}
		case "test":
			var testErr error
			if targetLane == LaneTest {
				// dev→test: fast build check only
				testErr = runBuildCheck(ctx)
			} else if targetLane == LaneDeploy {
				// test→deploy: full test suite with 90s timeout
				testErr = runTestSuite(ctx)
			}
			if testErr != nil {
				result.Criteria["test"] = false
				result.Errors = append(result.Errors, fmt.Sprintf("test gate failed: %v", testErr))
			} else {
				result.Criteria["test"] = true
			}
		default:
			// Unknown gate types fail closed — silent pass would allow unreviewed code through.
			result.Criteria[gate] = false
			result.Errors = append(result.Errors, fmt.Sprintf("unknown gate type: %s", gate))
		}
	}

	// Overall pass
	for _, passed := range result.Criteria {
		if !passed {
			result.Passed = false
			break
		}
	}

	return result, nil
}

// forgeRoot returns the FORGE project root directory for running build/test commands.
// It prefers the FORGE_ROOT env var and falls back to the current working directory.
func forgeRoot() string {
	if root := os.Getenv("FORGE_ROOT"); root != "" {
		return root
	}
	return "."
}

// runBuildCheck runs "go build ./..." in the FORGE root to verify the codebase compiles.
// Used as the gate check when advancing a task from dev to test lane.
// FORGE_TEST_BUILD_CMD overrides the command — used in tests to avoid spawning a real build.
func runBuildCheck(ctx context.Context) error {
	buildCmd := "go build ./..."
	if override := os.Getenv("FORGE_TEST_BUILD_CMD"); override != "" {
		buildCmd = override
	}
	var stderr bytes.Buffer
	cmd := exec.CommandContext(ctx, "sh", "-c", buildCmd)
	cmd.Dir = forgeRoot()
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		output := strings.TrimSpace(stderr.String())
		if output != "" {
			return fmt.Errorf("go build ./... failed: %s", output)
		}
		return fmt.Errorf("go build ./... failed: %v", err)
	}
	return nil
}

// runTestSuite runs "go test ./..." with a 90-second timeout in the FORGE root.
// Used as the gate check when advancing a task from test to deploy lane.
func runTestSuite(ctx context.Context) error {
	// Enforce a 90-second hard cap regardless of parent context deadline.
	testCtx, cancel := context.WithTimeout(ctx, 90*time.Second)
	defer cancel()

	var buf bytes.Buffer
	cmd := exec.CommandContext(testCtx, "go", "test", "./...")
	cmd.Dir = forgeRoot()
	cmd.Stdout = &buf
	cmd.Stderr = &buf
	if err := cmd.Run(); err != nil {
		output := strings.TrimSpace(buf.String())
		// Trim to last 500 chars to keep error messages manageable.
		if len(output) > 500 {
			output = "..." + output[len(output)-500:]
		}
		if testCtx.Err() == context.DeadlineExceeded {
			return fmt.Errorf("go test ./... timed out after 90s")
		}
		if output != "" {
			return fmt.Errorf("go test ./... failed: %s", output)
		}
		return fmt.Errorf("go test ./... failed: %v", err)
	}
	return nil
}

func (m *laneManager) HandleLaneComplete(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	path := r.URL.Path
	taskID := strings.TrimPrefix(path, "/api/tasks/")
	taskID = strings.TrimSuffix(taskID, "/lane/complete")

	task, err := m.ProgressTask(r.Context(), taskID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(task)
}

func (m *laneManager) HandleLaneStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	path := r.URL.Path
	taskID := strings.TrimPrefix(path, "/api/tasks/")
	taskID = strings.TrimSuffix(taskID, "/lane/status")

	task, err := m.queue.GetTask(r.Context(), taskID)
	if err != nil {
		http.Error(w, "task not found", http.StatusNotFound)
		return
	}

	var nextLane Lane
	switch Lane(task.Lane) {
	case LaneDev:
		nextLane = LaneTest
	case LaneTest:
		nextLane = LaneDeploy
	case LaneDeploy:
		nextLane = LaneDone
	}

	var gateResult *GateResult
	if nextLane != "" {
		gateResult, _ = m.CheckGates(r.Context(), &task, nextLane)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"task_id":      taskID,
		"current_lane": task.Lane,
		"next_lane":    nextLane,
		"gate_result":  gateResult,
	})
}
