//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func createTaskHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var task Task
	if err := json.NewDecoder(r.Body).Decode(&task); err != nil {
		writeJSONError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	// Validate critical fields
	task.ID = sanitizeID(task.ID)
	if task.ID != "" && !isValidID(task.ID) {
		writeJSONError(w, http.StatusBadRequest, "invalid task ID format")
		return
	}

	task.Domain = sanitizeID(task.Domain)
	if task.Domain == "" || !isValidID(task.Domain) {
		writeJSONError(w, http.StatusBadRequest, "invalid or missing domain")
		return
	}

	task.Project = sanitizeID(task.Project)
	if task.Project == "" || !isValidID(task.Project) {
		writeJSONError(w, http.StatusBadRequest, "invalid or missing project")
		return
	}

	task.Type = TaskType(sanitizeID(string(task.Type)))
	if task.Type == "" || !isValidID(string(task.Type)) {
		writeJSONError(w, http.StatusBadRequest, "invalid or missing type")
		return
	}

	task.Title = sanitizeText(task.Title)
	if strings.TrimSpace(task.Title) == "" {
		// Dispatch requirement: exact error key + message.
		writeJSONError(w, http.StatusBadRequest, "title required")
		return
	}
	if len(task.Title) > 500 {
		writeJSONError(w, http.StatusBadRequest, "title too long")
		return
	}

	// Title quality gate — reject vague or auto-generated titles
	if isTitleJunk(task.Title) {
		writeJSONError(w, http.StatusBadRequest, "task title too vague: use a specific description (e.g. 'fix syncRoyalJelly updated_at bug')")
		return
	}

	// Normalize incoming priority into protocol scale 1-10.
	// Many callers historically use 0-100; map that into 1-10 for storage and ordering.
	switch {
	case task.Priority == 0:
		task.Priority = 5
	case task.Priority < 0:
		writeJSONError(w, http.StatusBadRequest, "priority must be 1-10")
		return
	case task.Priority > 10:
		// Legacy 0-100 style: 1-10 buckets.
		mapped := (task.Priority + 9) / 10
		if mapped < 1 {
			mapped = 1
		}
		if mapped > 10 {
			mapped = 10
		}
		task.Priority = mapped
	}
	if task.Priority < 1 || task.Priority > 10 {
		writeJSONError(w, http.StatusBadRequest, "priority must be 1-10")
		return
	}

	ctx := r.Context()

	// Preserve any provided assigned_to for context lookup, but do not persist
	// it as an assignment yet so that the normal claim lifecycle still applies.
	agentIDForContext := strings.TrimSpace(task.AssignedTo)
	task.AssignedTo = ""

	// Also allow agent ID to be provided via header for convenience.
	if agentIDForContext == "" {
		agentIDForContext = strings.TrimSpace(r.Header.Get("X-Agent-ID"))
	}

	// If no explicit envelope_id was provided, automatically attach the latest
	// envelope for this agent (if available).
	if task.EnvelopeID == "" && agentIDForContext != "" {
		if env, err := findLatestEnvelopeForAgent(ctx, agentIDForContext); err == nil {
			task.EnvelopeID = env.ID
		} else if err != sql.ErrNoRows {
			log.Printf("WARN: failed to auto-attach envelope for agent %s: %v", agentIDForContext, err)
		}
	}

	// Generate ID if not provided
	if task.ID == "" {
		task.ID = generateTaskID()
	}

	// Set default values - start at requested for agentic lifecycle
	if task.Status == "" {
		task.Status = TaskStatusRequested
	}
	if task.State == "" {
		task.State = StateQueued
	}
	task.CreatedAt = time.Now()
	task.UpdatedAt = time.Now()

	err := taskQueue.Enqueue(context.Background(), task)
	if err != nil {
		log.Printf("ERROR: failed to enqueue task %s: %v", task.ID, err)
		http.Error(w, fmt.Sprintf("failed to enqueue task: %v", err), http.StatusInternalServerError)
		return
	}

	// Record the initial FSM state via the TaskStore so that a transition log
	// entry is created in task_state_transitions.  The state column already has
	// DEFAULT 'QUEUED' in the schema, so the UPDATE inside RecordTransition is
	// a no-op data-wise, but the INSERT into task_state_transitions is the
	// important side-effect — it makes the birth of the task visible in the
	// FSM audit trail (ADR-028).
	//
	// Fall back to a raw UPDATE if taskStore is not yet initialized (e.g. during
	// early startup or in tests that do not wire the full FSM).
	if taskStore != nil {
		if storeErr := taskStore.RecordTransition(task.ID, "", StateQueued, "task created"); storeErr != nil {
			log.Printf("WARN: failed to record initial FSM transition for task %s: %v", task.ID, storeErr)
		}
	} else if db := getDBConn(); db != nil {
		if _, dbErr := db.Exec(`UPDATE tasks SET state=? WHERE id=?`, string(StateQueued), task.ID); dbErr != nil {
			log.Printf("WARN: failed to set initial FSM state for task %s: %v", task.ID, dbErr)
		}
	}

	// Metrics: record task creation and enqueue.
	metrics.RecordTaskCreated(string(task.Type), task.Priority)
	metrics.RecordTaskEnqueued()

	// If task is queued (legacy/direct), try to assign
	if task.Status == TaskStatusQueued {
		workers := hub.ListWorkers()
		if len(workers) > 0 {
			workerID := workers[0]
			task.Status = TaskStatusAssigned
			task.AssignedTo = workerID
			taskQueue.AssignTask(context.Background(), task.ID, workerID)
			hub.SendToWorker(workerID, "task.assigned", task.ID, task)
			log.Printf("assigned task %s to worker %s", task.ID, workerID)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(task)
}

func completeTaskHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	taskID = strings.TrimSuffix(taskID, "/complete")
	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}

	var req struct {
		Result string `json:"result"`
		Status string `json:"status"` // "completed" or "failed" or "done"
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	// Normalize status
	status := strings.ToLower(req.Status)
	if status == "done" || status == "" {
		status = string(TaskStatusCompleted)
	}

	ctx := context.Background()
	db := getDBConn()

	// Use existing queue or create a transient one for this request
	tq := taskQueue
	if tq == nil && db != nil {
		tq, _ = NewTaskQueueFromDB(db)
	}

	if tq == nil {
		http.Error(w, "task queue not initialized", http.StatusServiceUnavailable)
		return
	}

	// Get current task to know its state
	task, err := tq.GetTask(ctx, taskID)
	if err != nil {
		http.Error(w, fmt.Sprintf("task not found: %v", err), http.StatusNotFound)
		return
	}

	// Use state machine for transition if possible
	err = fmt.Errorf("db not initialized")
	// Prefer the global stateMachine (initialized at startup); fall back to a
	// locally-constructed one when the global is not yet ready (e.g. tests).
	sm := stateMachine
	if sm == nil && db != nil {
		store := NewTaskStore(db)
		sm = NewStateMachine(store, db)
	}

	targetState := StateCompleted
	if status == string(TaskStatusFailed) {
		targetState = StateFailed
	}

	if sm != nil {
		if currentState, gsErr := sm.GetState(taskID); gsErr == nil {
			if currentState == StateRunning || currentState == StateDispatched {
				err = sm.Transition(taskID, currentState, targetState, "task completed by agent")
			} else {
				err = sm.Transition(taskID, task.State, targetState, req.Result)
			}
		} else {
			err = sm.Transition(taskID, task.State, targetState, req.Result)
		}
	}
	if err != nil {
		// Fallback to manual update if state machine rejects (e.g. from wrong state)
		log.Printf("[completeTaskHandler] State machine transition failed: %v. Falling back to manual update.", err)
		if db == nil {
			http.Error(w, "db not initialized", http.StatusServiceUnavailable)
			return
		}
		_, err = db.Exec(`
			UPDATE tasks
			SET status = ?, state = ?, result = ?, completed_at = ?, updated_at = ?
			WHERE id = ?
		`, status, string(targetState), req.Result, time.Now().Format(time.RFC3339), time.Now().Format(time.RFC3339), taskID)

		if err != nil {
			http.Error(w, fmt.Sprintf("failed to complete task: %v", err), http.StatusInternalServerError)
			return
		}
	}

	// Release any lease on this task
	if db != nil {
		_, _ = db.Exec(`DELETE FROM leases WHERE task_id = ?`, taskID)

		// ADR-024: cleanup worktree on completion
		forgeRoot := os.Getenv("FORGE_ROOT")
		if forgeRoot == "" {
			forgeRoot, _ = os.Getwd()
		}
		wm := NewWorktreeManager(forgeRoot, "", db)
		if err := wm.RemoveWorktree(r.Context(), taskID); err != nil {
			log.Printf("[completeTaskHandler] worktree removal failed for %s: %v (non-fatal)", taskID, err)
		}
		_, _ = db.ExecContext(r.Context(),
			`UPDATE worktrees SET status='removed', removed_at=datetime('now') WHERE id=?`,
			taskID,
		)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok", "task_id": taskID})
}

// abandonTaskHandler marks a specific task as abandoned.
// POST /api/tasks/{id}/abandon
func abandonTaskHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	taskID = strings.TrimSuffix(taskID, "/abandon")
	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}
	db := getDBConn()
	if db == nil {
		http.Error(w, "db not initialized", http.StatusServiceUnavailable)
		return
	}
	now := time.Now().Format(time.RFC3339)
	res, err := db.Exec(`UPDATE tasks SET status='abandoned', state='ABANDONED', updated_at=? WHERE id=? AND status IN ('assigned','queued')`, now, taskID)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to abandon task: %v", err), http.StatusInternalServerError)
		return
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		http.Error(w, "task not found or already completed/abandoned", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"status": "ok", "task_id": taskID, "abandoned": n})
}

// taskHistoryHandler returns the state transition history for a task.
// GET /api/tasks/{id}/history
func taskHistoryHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	taskID = strings.TrimSuffix(taskID, "/history")
	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}
	db := getDBConn()
	if db == nil {
		http.Error(w, "db not initialized", http.StatusServiceUnavailable)
		return
	}
	store := NewTaskStore(db)
	transitions, err := store.GetTransitionHistory(taskID)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to get transition history: %v", err), http.StatusInternalServerError)
		return
	}
	if transitions == nil {
		transitions = []StateTransition{}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"task_id":     taskID,
		"transitions": transitions,
		"count":       len(transitions),
	})
}

// pruneTasksHandler abandons all assigned tasks with no heartbeat update in the last 2 hours.
// POST /api/tasks/prune
func pruneTasksHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	db := getDBConn()
	if db == nil {
		http.Error(w, "db not initialized", http.StatusServiceUnavailable)
		return
	}
	now := time.Now()
	threshold := now.Add(-2 * time.Hour).Format(time.RFC3339)
	res, err := db.Exec(`UPDATE tasks SET status='abandoned', state='ABANDONED', updated_at=? WHERE status='assigned' AND updated_at < ?`,
		now.Format(time.RFC3339), threshold)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to prune tasks: %v", err), http.StatusInternalServerError)
		return
	}
	n, _ := res.RowsAffected()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"status": "ok", "pruned": n, "threshold": threshold})
}

func getTaskHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")

	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}

	task, err := taskQueue.GetTask(context.Background(), taskID)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(NewNotFoundError("task", taskID))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(task)
}

func listTasksHandler(w http.ResponseWriter, r *http.Request) {
	// Parse query parameters
	status := sanitizeID(r.URL.Query().Get("status"))
	if status != "" && !isValidID(status) {
		http.Error(w, "invalid status format", http.StatusBadRequest)
		return
	}
	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		fmt.Sscanf(l, "%d", &limit)
		if limit > 100 {
			limit = 100
		}
	}

	var tasks []Task
	var err error

	ctx := context.Background()

	if status != "" {
		tasks, err = taskQueue.ListTasksByStatus(ctx, TaskStatus(status), limit)
	} else {
		tasks, err = taskQueue.ListAllTasks(ctx, limit)
	}

	if err != nil {
		http.Error(w, fmt.Sprintf("failed to list tasks: %v", err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"tasks": tasks,
		"count": len(tasks),
	})
}

func claimTaskHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	taskID = strings.TrimSuffix(taskID, "/claim")

	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}

	var req struct {
		AgentID string `json:"agent_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request", http.StatusBadRequest)
		return
	}

	if strings.TrimSpace(req.AgentID) == "" {
		http.Error(w, "agent_id required", http.StatusBadRequest)
		return
	}

	ctx := context.Background()

	// Get task to check if it's claimable
	task, err := taskQueue.GetTask(ctx, taskID)
	if err != nil {
		http.Error(w, "task not found", http.StatusNotFound)
		return
	}

	if task.State != StateQueued && task.Status != TaskStatusQueued && task.Status != TaskStatusRequested {
		http.Error(w, fmt.Sprintf("task not claimable, state: %s, status: %s", task.State, task.Status), http.StatusConflict)
		return
	}

	// Context gate: reject if agent has a recent heartbeat (< 15 min) with context_pct >= 90%
	var agentContextPct float64
	dbConn := getDBConn()
	if dbConn != nil {
		_ = dbConn.QueryRowContext(ctx, `
			SELECT COALESCE(context_pct, 0)
			FROM agent_heartbeats
			WHERE agent_id = ? AND last_seen > datetime('now', '-15 minutes')
			ORDER BY last_seen DESC LIMIT 1
		`, req.AgentID).Scan(&agentContextPct)
	}
	if agentContextPct >= 90.0 {
		http.Error(w, fmt.Sprintf("context too full: %.0f%% (gate: 90%%)", agentContextPct), http.StatusConflict)
		return
	}

	// ADR-036 P4: Tier routing — check agent tier vs task required_tier.
	if err := checkTierCompatibility(ctx, taskID, req.AgentID); err != nil {
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}

	// Claim the task using TaskStore (uses FSM properly, ADR-028)
	if taskStore != nil {
		err = taskStore.ClaimTask(taskID, req.AgentID)
	} else {
		// Fallback to queue for backwards compatibility
		err = taskQueue.ClaimTask(ctx, taskID, req.AgentID)
	}
	if err != nil {
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}

	if leaseManager != nil {
		if _, err := leaseManager.Claim(r.Context(), taskID, req.AgentID, 30*time.Minute); err != nil {
			log.Printf("WARN: lease claim failed for task %s: %v", taskID, err)
			// Best-effort — don't fail the request
		}
	}

	// ADR-024: Record worktree in DB for visibility (no filesystem op - worktrees created by agent scripts)
	if worktreeMgr != nil {
		branch := fmt.Sprintf("feature/%s", taskID)
		path := filepath.Join(".forge", "worktrees", taskID)
		if err := worktreeMgr.RecordWorktree(r.Context(), taskID, path, branch, req.AgentID); err != nil {
			log.Printf("WARN: worktree record failed for %s: %v", taskID, err) // non-fatal
		}
	}

	// Return updated task
	task, _ = taskQueue.GetTask(ctx, taskID)

	// If the task is linked to a context envelope, automatically bootstrap the
	// agent by sending the envelope over WebSocket.
	if strings.TrimSpace(task.EnvelopeID) != "" {
		if env, err := getEnvelopeByID(ctx, task.EnvelopeID); err != nil {
			log.Printf("WARN: failed to load envelope %s for bootstrap: %v", task.EnvelopeID, err)
		} else {
			sendBootstrapToAgent(req.AgentID, taskID, env)
		}
	}

	// Dispatch task to agent via WebSocket
	hub.SendToWorker(req.AgentID, MsgTaskAssigned, taskID, task)
	log.Printf("dispatched task %s to agent %s via WebSocket", taskID, req.AgentID)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":   "claimed",
		"task":     task,
		"agent_id": req.AgentID,
	})
}

// completeTaskWithApprovalHandler handles task completion with confidence checking and approval workflow
func completeTaskWithApprovalHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	taskID = strings.TrimSuffix(taskID, "/complete-with-approval")
	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}

	var req TaskCompletionRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	req.TaskID = taskID

	db := getDBConn()
	if db == nil {
		http.Error(w, "db not initialized", http.StatusServiceUnavailable)
		return
	}
	approvalStore := NewApprovalStore(db)
	approvalService := NewApprovalService(approvalStore)
	integration := NewTaskApprovalIntegration(taskQueue, approvalService)

	ctx := context.Background()
	resp, err := integration.HandleTaskCompletion(ctx, req)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to complete task: %v", err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if resp.Status == "pending_approval" {
		w.WriteHeader(http.StatusAccepted)
	} else {
		w.WriteHeader(http.StatusOK)
	}
	json.NewEncoder(w).Encode(resp)
}

// taskUpdateHandler handles PUT /api/tasks/{id} — updates task status and/or assigned_to
func taskUpdateHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPut {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	taskID = strings.Split(taskID, "/")[0]

	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}

	var req struct {
		Status     string `json:"status"`
		AssignedTo string `json:"assigned_to"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "db not initialized", http.StatusServiceUnavailable)
		return
	}

	now := time.Now().UTC().Format(time.RFC3339)
	if req.Status != "" {
		if _, err := db.Exec("UPDATE tasks SET status=?, updated_at=? WHERE id=?",
			req.Status, now, taskID); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
	}
	if req.AssignedTo != "" {
		if _, err := db.Exec("UPDATE tasks SET assigned_to=?, updated_at=? WHERE id=?",
			req.AssignedTo, now, taskID); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
	}

	task, err := taskQueue.GetTask(context.Background(), taskID)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(NewNotFoundError("task", taskID))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(task)
}

// taskDeleteHandler handles DELETE /api/tasks/{id} — soft-deletes by setting status to "deleted"
func taskDeleteHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodDelete {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	taskID = strings.Split(taskID, "/")[0]

	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "db not initialized", http.StatusServiceUnavailable)
		return
	}

	if _, err := db.Exec("UPDATE tasks SET status='deleted', updated_at=? WHERE id=?",
		time.Now().UTC().Format(time.RFC3339), taskID); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// getTaskEventsHandler returns the event sourcing history for a task
func getTaskEventsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	path = strings.TrimSuffix(path, "/events")

	if path == "" || path == "/events" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}

	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		fmt.Sscanf(l, "%d", &limit)
		if limit > 100 {
			limit = 100
		}
	}

	events, err := taskQueue.GetTaskEvents(context.Background(), path, limit)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to get task events: %v", err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"task_id": path,
		"events":  events,
		"count":   len(events),
	})
}

// extractTaskID strips a known suffix from a URL path to get the task ID.
func extractTaskID(path, suffix string) string {
	path = strings.TrimPrefix(path, "/api/tasks/")
	return strings.TrimSuffix(path, suffix)
}

// approveTaskHandler handles POST /api/tasks/:id/approve — FSM COMPLETED→APPROVED
func approveTaskHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	taskID := extractTaskID(r.URL.Path, "/approve")
	taskID = sanitizeID(taskID)
	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}

	var req struct {
		ApprovedBy string `json:"approved_by"`
		Note       string `json:"note"`
	}
	json.NewDecoder(r.Body).Decode(&req) // non-fatal

	if stateMachine == nil {
		http.Error(w, "state machine not available", http.StatusServiceUnavailable)
		return
	}

	reason := "approved by " + req.ApprovedBy
	if req.Note != "" {
		reason += ": " + req.Note
	}
	if err := stateMachine.Transition(taskID, StateCompleted, StateApproved, reason); err != nil {
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"task_id":     taskID,
		"state":       string(StateApproved),
		"approved_by": req.ApprovedBy,
	})
}

func claimableTasksHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	limit := 10
	if l := r.URL.Query().Get("limit"); l != "" {
		fmt.Sscanf(l, "%d", &limit)
	}

	tasks, err := taskQueue.GetClaimableTasks(context.Background(), limit)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to get claimable tasks: %v", err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"tasks": tasks,
		"count": len(tasks),
	})
}
