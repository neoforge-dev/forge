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
	"strings"
	"time"
)

func queueTaskHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	taskID = strings.TrimSuffix(taskID, "/queue")
	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}
	task, err := taskQueue.GetTask(context.Background(), taskID)
	if err != nil {
		http.Error(w, "task not found", http.StatusNotFound)
		return
	}
	if task.Status != TaskStatusPlanned {
		http.Error(w, "only planned tasks can be queued", http.StatusBadRequest)
		return
	}
	task.Status = TaskStatusQueued
	task.UpdatedAt = time.Now()
	if err = taskQueue.Enqueue(context.Background(), task); err != nil {
		http.Error(w, fmt.Sprintf("failed to queue task: %v", err), http.StatusInternalServerError)
		return
	}
	if workers := hub.ListWorkers(); len(workers) > 0 {
		wid := workers[0]
		task.Status = TaskStatusAssigned
		task.AssignedTo = wid
		taskQueue.AssignTask(context.Background(), task.ID, wid)
		hub.SendToWorker(wid, "task.assigned", task.ID, task)
		log.Printf("assigned task %s to worker %s", task.ID, wid)
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(task)
}

func pauseTaskHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	taskID := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/api/tasks/"), "/pause")
	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}
	ctx := context.Background()
	db := getDBConn()
	tq := taskQueue
	if tq == nil && db != nil {
		tq, _ = NewTaskQueueFromDB(db)
	}
	if tq == nil {
		http.Error(w, "task queue not initialized", http.StatusServiceUnavailable)
		return
	}
	task, err := tq.GetTask(ctx, taskID)
	if err != nil {
		http.Error(w, fmt.Sprintf("task not found: %v", err), http.StatusNotFound)
		return
	}
	err = fmt.Errorf("db not initialized")
	if db != nil {
		sm := NewStateMachine(NewTaskStore(db), db)
		err = sm.Transition(taskID, task.State, StateBlocked, "Paused via API")
	}
	if err != nil {
		log.Printf("[pauseTaskHandler] state machine failed: %v, falling back", err)
		if err = tq.Pause(ctx, taskID); err != nil {
			http.Error(w, fmt.Sprintf("failed to pause task: %v", err), http.StatusBadRequest)
			return
		}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "paused", "task_id": taskID})
}

func resumeTaskHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	taskID := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/api/tasks/"), "/resume")
	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}
	ctx := context.Background()
	db := getDBConn()
	tq := taskQueue
	if tq == nil && db != nil {
		tq, _ = NewTaskQueueFromDB(db)
	}
	if tq == nil {
		http.Error(w, "task queue not initialized", http.StatusServiceUnavailable)
		return
	}
	task, err := tq.GetTask(ctx, taskID)
	if err != nil {
		http.Error(w, fmt.Sprintf("task not found: %v", err), http.StatusNotFound)
		return
	}
	err = fmt.Errorf("db not initialized")
	if db != nil {
		sm := NewStateMachine(NewTaskStore(db), db)
		err = sm.Transition(taskID, task.State, StateRunning, "Resumed via API")
	}
	if err != nil {
		log.Printf("[resumeTaskHandler] state machine failed: %v, falling back", err)
		if err = tq.Resume(ctx, taskID); err != nil {
			http.Error(w, fmt.Sprintf("failed to resume task: %v", err), http.StatusBadRequest)
			return
		}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "resumed", "task_id": taskID})
}

// agentTasksHandler returns tasks assigned to a specific agent.
func agentTasksHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Path
	id := strings.TrimPrefix(path, "/agents/")
	if strings.HasPrefix(path, "/api/agents/") {
		id = strings.TrimPrefix(path, "/api/agents/")
	}
	id = strings.TrimSuffix(id, "/tasks")
	if id == "" {
		http.Error(w, "missing agent id", http.StatusBadRequest)
		return
	}
	rows, err := getDBConn().Query(`
		SELECT id, assigned_to, domain, project, type, status, priority, created_at, updated_at, started_at
		FROM tasks WHERE assigned_to = ? ORDER BY created_at DESC`, id)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to query agent tasks: %v", err), http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	tasks := []Task{}
	for rows.Next() {
		var t Task
		var assignedTo, startedAt sql.NullString
		if err := rows.Scan(&t.ID, &assignedTo, &t.Domain, &t.Project, &t.Type, &t.Status, &t.Priority, &t.CreatedAt, &t.UpdatedAt, &startedAt); err != nil {
			continue
		}
		if assignedTo.Valid {
			t.AssignedTo = assignedTo.String
		}
		if startedAt.Valid {
			parsed, _ := time.Parse(time.RFC3339, startedAt.String)
			t.StartedAt = &parsed
		}
		tasks = append(tasks, t)
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"tasks": tasks, "count": len(tasks)})
}
