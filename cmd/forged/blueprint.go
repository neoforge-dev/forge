//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/oklog/ulid/v2"
	"gopkg.in/yaml.v3"
)

type Blueprint struct {
	ID          string          `json:"id" yaml:"id"`
	Name        string          `json:"name" yaml:"name"`
	Description string          `json:"description" yaml:"description"`
	Steps       []BlueprintSpec `json:"steps" yaml:"steps"`
}

type BlueprintSpec struct {
	Type     string   `json:"type" yaml:"type"`
	Name     string   `json:"name" yaml:"name"`
	Command  string   `json:"command,omitempty" yaml:"command,omitempty"`
	Agent    string   `json:"agent,omitempty" yaml:"agent,omitempty"`
	Evidence []string `json:"evidence,omitempty" yaml:"evidence,omitempty"`
}

type BlueprintRun struct {
	ID          string                `json:"id"`
	TaskID      string                `json:"task_id"`
	BlueprintID string                `json:"blueprint_id"`
	Status      string                `json:"status"`
	CurrentStep int                   `json:"current_step"`
	StartedAt   string                `json:"started_at"`
	CompletedAt *string               `json:"completed_at,omitempty"`
	Error       string                `json:"error,omitempty"`
	Steps       []BlueprintStepRecord `json:"steps,omitempty"`
}

type BlueprintStepRecord struct {
	ID          string  `json:"id"`
	RunID       string  `json:"run_id"`
	StepIndex   int     `json:"step_index"`
	StepType    string  `json:"step_type"`
	StepName    string  `json:"step_name"`
	Status      string  `json:"status"`
	StartedAt   *string `json:"started_at,omitempty"`
	CompletedAt *string `json:"completed_at,omitempty"`
	Evidence    string  `json:"evidence,omitempty"`
	Error       string  `json:"error,omitempty"`
}

type BlueprintRuntime struct {
	db        *sql.DB
	forgeRoot string
}

func NewBlueprintRuntime(db *sql.DB, root string) *BlueprintRuntime {
	if root == "" {
		root = forgeRoot()
	}
	return &BlueprintRuntime{db: db, forgeRoot: root}
}

func blueprintConfigRoot(root string) string {
	return filepath.Join(root, "config", "blueprints")
}

func (rt *BlueprintRuntime) resolveBlueprintPath(ref string) string {
	if ref == "" {
		return ""
	}
	if strings.HasSuffix(ref, ".yaml") || strings.HasSuffix(ref, ".yml") {
		if filepath.IsAbs(ref) {
			return ref
		}
		return filepath.Join(rt.forgeRoot, ref)
	}
	return filepath.Join(blueprintConfigRoot(rt.forgeRoot), ref+".yaml")
}

func (rt *BlueprintRuntime) loadBlueprint(ref string) (*Blueprint, error) {
	path := rt.resolveBlueprintPath(ref)
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read blueprint %s: %w", ref, err)
	}
	var bp Blueprint
	if err := yaml.Unmarshal(data, &bp); err != nil {
		return nil, fmt.Errorf("parse blueprint %s: %w", ref, err)
	}
	if bp.ID == "" {
		if strings.HasSuffix(ref, ".yaml") || strings.HasSuffix(ref, ".yml") {
			bp.ID = strings.TrimSuffix(strings.TrimSuffix(filepath.Base(ref), ".yaml"), ".yml")
		} else {
			bp.ID = ref
		}
	}
	if err := validateBlueprint(&bp); err != nil {
		return nil, err
	}
	return &bp, nil
}

func validateBlueprint(bp *Blueprint) error {
	if bp == nil {
		return fmt.Errorf("blueprint is nil")
	}
	if strings.TrimSpace(bp.ID) == "" {
		return fmt.Errorf("missing blueprint id")
	}
	if len(bp.Steps) == 0 {
		return fmt.Errorf("blueprint must have at least one step")
	}
	seen := map[string]bool{}
	for i, step := range bp.Steps {
		if step.Name == "" {
			return fmt.Errorf("step %d missing name", i)
		}
		if seen[step.Name] {
			return fmt.Errorf("duplicate step name: %s", step.Name)
		}
		seen[step.Name] = true
		switch step.Type {
		case "check", "shell":
			if strings.TrimSpace(step.Command) == "" {
				return fmt.Errorf("step %s requires command", step.Name)
			}
		case "dispatch", "review", "complete":
		default:
			return fmt.Errorf("step %s has unsupported type %s", step.Name, step.Type)
		}
	}
	return nil
}

func (rt *BlueprintRuntime) ListBlueprints() ([]Blueprint, error) {
	return loadBlueprintsFromConfig(rt.forgeRoot)
}

func loadBlueprintsFromConfig(root string) ([]Blueprint, error) {
	configRoot := blueprintConfigRoot(root)
	var out []Blueprint
	err := filepath.Walk(configRoot, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			return nil
		}
		if !strings.HasSuffix(path, ".yaml") && !strings.HasSuffix(path, ".yml") {
			return nil
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		var bp Blueprint
		if err := yaml.Unmarshal(data, &bp); err != nil {
			return err
		}
		if bp.ID == "" {
			rel, _ := filepath.Rel(configRoot, path)
			bp.ID = strings.TrimSuffix(strings.TrimSuffix(filepath.ToSlash(rel), ".yaml"), ".yml")
		}
		out = append(out, bp)
		return nil
	})
	if err != nil {
		if os.IsNotExist(err) {
			return []Blueprint{}, nil
		}
		return nil, err
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out, nil
}

func (rt *BlueprintRuntime) StartBlueprintRun(ctx context.Context, taskID, blueprintID string) (*BlueprintRun, error) {
	bp, err := rt.loadBlueprint(blueprintID)
	if err != nil {
		return nil, err
	}

	runID := ulid.Make().String()
	now := time.Now().UTC().Format(time.RFC3339)

	tx, err := rt.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()

	_, err = tx.ExecContext(ctx, `
		INSERT INTO blueprint_runs (id, task_id, blueprint_id, status, current_step, started_at)
		VALUES (?, ?, ?, 'pending', 0, ?)
	`, runID, taskID, bp.ID, now)
	if err != nil {
		return nil, err
	}

	for i, step := range bp.Steps {
		_, err = tx.ExecContext(ctx, `
			INSERT INTO blueprint_steps (id, run_id, step_index, step_type, step_name, status)
			VALUES (?, ?, ?, ?, ?, 'pending')
		`, ulid.Make().String(), runID, i, step.Type, step.Name)
		if err != nil {
			return nil, err
		}
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}

	// Runtime failures are persisted onto the run; the start call still succeeds.
	_ = rt.ResumeRun(ctx, runID)
	return rt.GetRun(ctx, runID)
}

func (rt *BlueprintRuntime) ResumeRun(ctx context.Context, runID string) error {
	run, err := rt.GetRun(ctx, runID)
	if err != nil {
		return err
	}
	if run.Status == "completed" || run.Status == "failed" {
		return nil
	}

	bp, err := rt.loadBlueprint(run.BlueprintID)
	if err != nil {
		return rt.failRun(ctx, runID, run.CurrentStep, err.Error())
	}

	if _, err := rt.db.ExecContext(ctx, `UPDATE blueprint_runs SET status='running' WHERE id = ?`, runID); err != nil {
		return err
	}

	for _, step := range run.Steps {
		if step.Status == "completed" || step.Status == "skipped" {
			continue
		}
		spec := bp.Steps[step.StepIndex]
		if err := rt.markStepRunning(ctx, runID, step.StepIndex); err != nil {
			return err
		}
		if err := rt.executeStep(ctx, run, spec, step.StepIndex); err != nil {
			return rt.failRun(ctx, runID, step.StepIndex, err.Error())
		}
		if _, err := rt.db.ExecContext(ctx, `UPDATE blueprint_runs SET current_step = ? WHERE id = ?`, step.StepIndex+1, runID); err != nil {
			return err
		}
	}

	_, err = rt.db.ExecContext(ctx, `
		UPDATE blueprint_runs
		SET status='completed', current_step=?, completed_at=?, error=''
		WHERE id = ?
	`, len(run.Steps), time.Now().UTC().Format(time.RFC3339), runID)
	return err
}

func (rt *BlueprintRuntime) executeStep(ctx context.Context, run *BlueprintRun, step BlueprintSpec, stepIndex int) error {
	switch step.Type {
	case "check", "shell":
		return rt.executeCommandStep(ctx, run, step, stepIndex)
	case "dispatch":
		return rt.executeDispatchStep(ctx, run, step, stepIndex)
	case "review":
		return rt.completeStep(ctx, run.ID, stepIndex, map[string]interface{}{
			"status": "review-deferred",
			"note":   "review step recorded but no approval gate wired in Blueprint Runtime v1",
		})
	case "complete":
		return rt.executeCompleteStep(ctx, run, step, stepIndex)
	default:
		return fmt.Errorf("unsupported blueprint step type: %s", step.Type)
	}
}

// productAliasFromKey derives a short alias from a hyphen-separated product key.
// Each hyphen-separated segment contributes its first character.
// Examples: "interview-simulator" → "is", "voice-coach" → "vc", "study-flow" → "sf".
// If the key is empty the alias is also empty.
func productAliasFromKey(key string) string {
	if key == "" {
		return ""
	}
	parts := strings.Split(key, "-")
	var b strings.Builder
	for _, p := range parts {
		if len(p) > 0 {
			b.WriteByte(p[0])
		}
	}
	return b.String()
}

// productContextFromTask queries the task's domain and project from the database
// and returns a variable map ready for substitution into blueprint command strings.
// The returned map always contains all keys; values are empty strings on error or
// when the task is not found.
func productContextFromTask(db *sql.DB, taskID string) map[string]string {
	result := map[string]string{
		"DOMAIN":        "",
		"PRODUCT_KEY":   "",
		"PRODUCT_ALIAS": "",
	}
	if db == nil || taskID == "" {
		return result
	}
	var domain, project sql.NullString
	err := db.QueryRow(
		`SELECT domain, project FROM tasks WHERE id = ? LIMIT 1`, taskID,
	).Scan(&domain, &project)
	if err != nil {
		return result
	}
	if domain.Valid {
		result["DOMAIN"] = domain.String
	}
	if project.Valid {
		result["PRODUCT_KEY"] = project.String
		result["PRODUCT_ALIAS"] = productAliasFromKey(project.String)
	}
	return result
}

func (rt *BlueprintRuntime) executeCommandStep(ctx context.Context, run *BlueprintRun, step BlueprintSpec, stepIndex int) error {
	cmdText := strings.ReplaceAll(step.Command, "${TASK_ID}", run.TaskID)
	cmdText = strings.ReplaceAll(cmdText, "${RUN_ID}", run.ID)
	cmdText = strings.ReplaceAll(cmdText, "${BLUEPRINT_ID}", run.BlueprintID)

	// Enrich with product context variables.
	productCtx := productContextFromTask(rt.db, run.TaskID)
	cmdText = strings.ReplaceAll(cmdText, "${DOMAIN}", productCtx["DOMAIN"])
	cmdText = strings.ReplaceAll(cmdText, "${PRODUCT_KEY}", productCtx["PRODUCT_KEY"])
	cmdText = strings.ReplaceAll(cmdText, "${PRODUCT_ALIAS}", productCtx["PRODUCT_ALIAS"])

	cmd := exec.CommandContext(ctx, "sh", "-c", cmdText)
	cmd.Dir = rt.forgeRoot
	cmd.Env = append(os.Environ(),
		"TASK_ID="+run.TaskID,
		"BLUEPRINT_RUN_ID="+run.ID,
		"BLUEPRINT_ID="+run.BlueprintID,
		"DOMAIN="+productCtx["DOMAIN"],
		"PRODUCT_KEY="+productCtx["PRODUCT_KEY"],
		"PRODUCT_ALIAS="+productCtx["PRODUCT_ALIAS"],
	)
	output, err := cmd.CombinedOutput()
	evidence := map[string]interface{}{
		"command":  cmdText,
		"output":   string(output),
		"stepType": step.Type,
	}
	if err != nil {
		return rt.failStep(ctx, run.ID, stepIndex, evidence, err.Error())
	}
	return rt.completeStep(ctx, run.ID, stepIndex, evidence)
}

func (rt *BlueprintRuntime) executeDispatchStep(ctx context.Context, run *BlueprintRun, step BlueprintSpec, stepIndex int) error {
	agent := step.Agent
	if agent == "" {
		agent = "auto"
	}
	evidence := map[string]interface{}{
		"agent":         agent,
		"task_id":       run.TaskID,
		"blueprint_run": run.ID,
		"status":        "recorded",
	}
	if taskQueue != nil && run.TaskID != "" {
		_ = taskQueue.QueuePendingDispatch(ctx, run.TaskID, agent, evidence)
	}
	return rt.completeStep(ctx, run.ID, stepIndex, evidence)
}

func (rt *BlueprintRuntime) executeCompleteStep(ctx context.Context, run *BlueprintRun, step BlueprintSpec, stepIndex int) error {
	evidence := map[string]interface{}{
		"evidence_keys": step.Evidence,
		"task_id":       run.TaskID,
	}
	if run.TaskID != "" {
		_, _ = rt.db.ExecContext(ctx, `
			UPDATE tasks
			SET status = ?, state = ?, updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
		`, string(TaskStatusCompleted), string(StateCompleted), run.TaskID)
	}
	return rt.completeStep(ctx, run.ID, stepIndex, evidence)
}

func (rt *BlueprintRuntime) markStepRunning(ctx context.Context, runID string, stepIndex int) error {
	_, err := rt.db.ExecContext(ctx, `
		UPDATE blueprint_steps
		SET status='running', started_at=?
		WHERE run_id = ? AND step_index = ?
	`, time.Now().UTC().Format(time.RFC3339), runID, stepIndex)
	return err
}

func (rt *BlueprintRuntime) completeStep(ctx context.Context, runID string, stepIndex int, evidence map[string]interface{}) error {
	raw, _ := json.Marshal(evidence)
	_, err := rt.db.ExecContext(ctx, `
		UPDATE blueprint_steps
		SET status='completed', completed_at=?, evidence=?, error=''
		WHERE run_id = ? AND step_index = ?
	`, time.Now().UTC().Format(time.RFC3339), string(raw), runID, stepIndex)
	return err
}

func (rt *BlueprintRuntime) failStep(ctx context.Context, runID string, stepIndex int, evidence map[string]interface{}, errMsg string) error {
	raw, _ := json.Marshal(evidence)
	_, err := rt.db.ExecContext(ctx, `
		UPDATE blueprint_steps
		SET status='failed', completed_at=?, evidence=?, error=?
		WHERE run_id = ? AND step_index = ?
	`, time.Now().UTC().Format(time.RFC3339), string(raw), errMsg, runID, stepIndex)
	if err != nil {
		return err
	}
	return errors.New(errMsg)
}

func (rt *BlueprintRuntime) failRun(ctx context.Context, runID string, stepIndex int, errMsg string) error {
	_, err := rt.db.ExecContext(ctx, `
		UPDATE blueprint_runs
		SET status='failed', current_step=?, completed_at=?, error=?
		WHERE id = ?
	`, stepIndex, time.Now().UTC().Format(time.RFC3339), errMsg, runID)
	return err
}

func (rt *BlueprintRuntime) GetRun(ctx context.Context, runID string) (*BlueprintRun, error) {
	var run BlueprintRun
	var completedAt sql.NullString
	var errText sql.NullString
	err := rt.db.QueryRowContext(ctx, `
		SELECT id, task_id, blueprint_id, status, current_step, started_at, completed_at, error
		FROM blueprint_runs
		WHERE id = ?
	`, runID).Scan(&run.ID, &run.TaskID, &run.BlueprintID, &run.Status, &run.CurrentStep, &run.StartedAt, &completedAt, &errText)
	if err != nil {
		return nil, err
	}
	if completedAt.Valid {
		run.CompletedAt = &completedAt.String
	}
	if errText.Valid {
		run.Error = errText.String
	}

	rows, err := rt.db.QueryContext(ctx, `
		SELECT id, run_id, step_index, step_type, step_name, status, started_at, completed_at, evidence, error
		FROM blueprint_steps
		WHERE run_id = ?
		ORDER BY step_index ASC
	`, runID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	for rows.Next() {
		var step BlueprintStepRecord
		var startedAt, completedAt, evidence, errText sql.NullString
		if err := rows.Scan(
			&step.ID, &step.RunID, &step.StepIndex, &step.StepType, &step.StepName,
			&step.Status, &startedAt, &completedAt, &evidence, &errText,
		); err != nil {
			return nil, err
		}
		if startedAt.Valid {
			step.StartedAt = &startedAt.String
		}
		if completedAt.Valid {
			step.CompletedAt = &completedAt.String
		}
		if evidence.Valid {
			step.Evidence = evidence.String
		}
		if errText.Valid {
			step.Error = errText.String
		}
		run.Steps = append(run.Steps, step)
	}
	return &run, rows.Err()
}

func blueprintRuntime() *BlueprintRuntime {
	db := getDBConn()
	if db == nil {
		return nil
	}
	return NewBlueprintRuntime(db, forgeRoot())
}

func blueprintsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	rt := blueprintRuntime()
	if rt == nil {
		writeJSONError(w, http.StatusServiceUnavailable, "database unavailable")
		return
	}
	blueprints, err := rt.ListBlueprints()
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"blueprints": blueprints,
		"count":      len(blueprints),
	})
}

func blueprintRunsHandler(w http.ResponseWriter, r *http.Request) {
	rt := blueprintRuntime()
	if rt == nil {
		writeJSONError(w, http.StatusServiceUnavailable, "database unavailable")
		return
	}
	switch r.Method {
	case http.MethodPost:
		var req struct {
			TaskID      string `json:"task_id"`
			BlueprintID string `json:"blueprint_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSONError(w, http.StatusBadRequest, "invalid request body")
			return
		}
		if req.TaskID == "" || req.BlueprintID == "" {
			writeJSONError(w, http.StatusBadRequest, "task_id and blueprint_id required")
			return
		}
		run, err := rt.StartBlueprintRun(r.Context(), req.TaskID, req.BlueprintID)
		if err != nil {
			writeJSONError(w, http.StatusBadRequest, err.Error())
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(run)
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func blueprintRunByIDHandler(w http.ResponseWriter, r *http.Request) {
	rt := blueprintRuntime()
	if rt == nil {
		writeJSONError(w, http.StatusServiceUnavailable, "database unavailable")
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/api/blueprints/runs/")
	path = strings.Trim(path, "/")
	if path == "" {
		writeJSONError(w, http.StatusBadRequest, "run id required")
		return
	}

	if strings.HasSuffix(path, "/resume") {
		runID := strings.TrimSuffix(path, "/resume")
		runID = strings.Trim(runID, "/")
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if err := rt.ResumeRun(r.Context(), runID); err != nil {
			writeJSONError(w, http.StatusInternalServerError, err.Error())
			return
		}
		run, err := rt.GetRun(r.Context(), runID)
		if err != nil {
			writeJSONError(w, http.StatusInternalServerError, err.Error())
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(run)
		return
	}

	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	run, err := rt.GetRun(r.Context(), path)
	if err != nil {
		if err == sql.ErrNoRows {
			writeJSONError(w, http.StatusNotFound, "blueprint run not found")
			return
		}
		writeJSONError(w, http.StatusInternalServerError, err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(run)
}
