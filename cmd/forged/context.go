//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/oklog/ulid/v2"
)

// ContextEnvelope represents a portable context package
type ContextEnvelope struct {
	ID        string    `json:"id"`
	AgentID   string    `json:"agent_id"`
	Domain    string    `json:"domain"`
	Project   string    `json:"project"`
	TaskID    string    `json:"task_id"`
	CreatedAt time.Time `json:"created_at"`
	ExpiresAt time.Time `json:"expires_at"`

	// Context content
	Summary     string            `json:"summary"`
	Decisions   []Decision        `json:"decisions"`
	Failures    []Failure         `json:"failures"`
	Calibration map[string]string `json:"calibration"`
	KeyFiles    []string          `json:"key_files"`
	Metadata    map[string]any    `json:"metadata"`
}

type Decision struct {
	ID          string    `json:"id"`
	Description string    `json:"description"`
	Reasoning   string    `json:"reasoning"`
	Timestamp   time.Time `json:"timestamp"`
}

type Failure struct {
	ID          string    `json:"id"`
	Description string    `json:"description"`
	Lesson      string    `json:"lesson"`
	Timestamp   time.Time `json:"timestamp"`
}

// ContextManager manages context envelopes
type ContextManager struct {
	db         *sql.DB
	contextDir string
	sync       *ContextSync
}

// Stop stops the background context sync goroutines
func (cm *ContextManager) Stop() {
	if cm.sync != nil {
		cm.sync.Stop()
	}
}

// NewContextManager creates a new ContextManager
func NewContextManager(db *sql.DB, contextDir string) *ContextManager {
	if contextDir == "" {
		contextDir = ".forge/context"
	}
	cm := &ContextManager{
		db:         db,
		contextDir: contextDir,
	}

	// Initialize bidirectional sync
	sync, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		log.Printf("Warning: failed to create context sync: %v", err)
	} else {
		cm.sync = sync
		if err := sync.Start(); err != nil {
			log.Printf("Warning: failed to start context sync: %v", err)
		}
	}

	return cm
}

func generateULID() string {
	t := time.Now()
	entropy := ulid.Monotonic(rand.New(rand.NewSource(t.UnixNano())), 0)
	id := ulid.MustNew(ulid.Timestamp(t), entropy)
	return id.String()
}

// generateTaskID creates a human-friendly task ID
// Format: TASK-{WORD}-{NUMBER} like "TASK-BLUE-42" or "FORGE-FAST-123"
func generateTaskID() string {
	adjectives := []string{"fast", "smart", "bold", "cool", "bright", "swift", "sharp", "keen", "brave", "wise",
		"quick", "calm", "safe", "sure", "true", "grand", "prime", "mega", "ultra", "super"}
	nouns := []string{"wave", "node", "flux", "core", "sync", "link", "beam", "flow", "pulse", "spark",
		"nova", "star", "bolt", "dash", "zoom", "surge", "glow", "flash", "blaze", "storm"}

	// Use current time to seed selection
	now := time.Now()
	seed := now.UnixNano()

	adjIndex := int(seed % int64(len(adjectives)))
	nounIndex := int((seed / 100) % int64(len(nouns)))
	number := int((seed / 10000) % 1000) // 0-999

	id := fmt.Sprintf("TASK-%s-%s-%03d",
		strings.ToUpper(adjectives[adjIndex]),
		strings.ToUpper(nouns[nounIndex]),
		number)
	// Sanitize: replace any accidental spaces with hyphens so IDs are always
	// safe for use in CLI arguments and API paths.
	return strings.ReplaceAll(id, " ", "-")
}

// GenerateEnvelope creates a context envelope from current state
func (cm *ContextManager) GenerateEnvelope(
	ctx context.Context,
	agentID, domain, project, taskID string,
	reason string,
) (*ContextEnvelope, error) {

	envelope := &ContextEnvelope{
		ID:        generateULID(),
		AgentID:   agentID,
		Domain:    domain,
		Project:   project,
		TaskID:    taskID,
		CreatedAt: time.Now(),
		ExpiresAt: time.Now().Add(7 * 24 * time.Hour), // 7 days
		Summary:   reason,
		Metadata:  make(map[string]any),
	}

	// Load from filesystem
	cm.loadFromFilesystem(envelope)

	// Store in SQLite
	if err := cm.storeInDB(ctx, envelope); err != nil {
		return nil, fmt.Errorf("store in db: %w", err)
	}

	// Write to filesystem
	if err := cm.storeInFilesystem(envelope); err != nil {
		return nil, fmt.Errorf("store in filesystem: %w", err)
	}

	return envelope, nil
}

// loadFromFilesystem populates the envelope with data from the filesystem
func (cm *ContextManager) loadFromFilesystem(envelope *ContextEnvelope) {
	domainDir := filepath.Join(cm.contextDir, envelope.Domain)
	projectDir := filepath.Join(domainDir, envelope.Project)

	// Load lead-context.md from domain
	leadContextPath := filepath.Join(domainDir, "lead-context.md")
	if data, err := os.ReadFile(leadContextPath); err == nil {
		if envelope.Metadata == nil {
			envelope.Metadata = make(map[string]any)
		}
		envelope.Metadata["lead_context"] = string(data)
	}

	// Load decisions from project dir if exists, else domain dir
	decisionsPath := filepath.Join(projectDir, "decisions.json")
	if _, err := os.Stat(decisionsPath); os.IsNotExist(err) {
		decisionsPath = filepath.Join(domainDir, "decisions.json")
	}
	if data, err := os.ReadFile(decisionsPath); err == nil {
		var decisions []Decision
		if err := json.Unmarshal(data, &decisions); err == nil {
			envelope.Decisions = decisions
		}
	}

	// Load failures from project dir if exists, else domain dir
	failuresPath := filepath.Join(projectDir, "failures.json")
	if _, err := os.Stat(failuresPath); os.IsNotExist(err) {
		failuresPath = filepath.Join(domainDir, "failures.json")
	}
	if data, err := os.ReadFile(failuresPath); err == nil {
		var failures []Failure
		if err := json.Unmarshal(data, &failures); err == nil {
			envelope.Failures = failures
		}
	}

	// Load calibration from project dir if exists, else domain dir
	calibrationPath := filepath.Join(projectDir, "calibration.md")
	if _, err := os.Stat(calibrationPath); os.IsNotExist(err) {
		calibrationPath = filepath.Join(domainDir, "calibration.md")
	}
	if data, err := os.ReadFile(calibrationPath); err == nil {
		if envelope.Calibration == nil {
			envelope.Calibration = make(map[string]string)
		}
		envelope.Calibration["main"] = string(data)
	}

	// Read agent sidecar JSON for context percentage and metadata
	sidecarPath := filepath.Join(".forge/agents", envelope.AgentID, "sidecar.json")
	if data, err := os.ReadFile(sidecarPath); err == nil {
		var sidecar struct {
			AgentID     string  `json:"agent_id"`
			ContextPct  float64 `json:"context_pct"`
			Timestamp   int64   `json:"timestamp"`
			CurrentTask string  `json:"current_task"`
			Status      string  `json:"status"`
		}
		if err := json.Unmarshal(data, &sidecar); err == nil {
			if envelope.Metadata == nil {
				envelope.Metadata = make(map[string]any)
			}
			envelope.Metadata["context_pct"] = sidecar.ContextPct
			envelope.Metadata["timestamp"] = sidecar.Timestamp
			envelope.Metadata["current_task"] = sidecar.CurrentTask
			envelope.Metadata["status"] = sidecar.Status
		}
	}
}

// storeInDB saves the envelope to the database
func (cm *ContextManager) storeInDB(ctx context.Context, envelope *ContextEnvelope) error {
	content, err := json.Marshal(envelope)
	if err != nil {
		return err
	}

	_, err = cm.db.ExecContext(ctx, `
		INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, summary, content, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, envelope.ID, envelope.AgentID, envelope.Domain, envelope.Project, envelope.TaskID, envelope.Summary, string(content), envelope.CreatedAt, envelope.ExpiresAt)

	return err
}

// storeInFilesystem saves the envelope to the filesystem
func (cm *ContextManager) storeInFilesystem(envelope *ContextEnvelope) error {
	envelopesDir := filepath.Join(cm.contextDir, "envelopes")
	if err := os.MkdirAll(envelopesDir, 0755); err != nil {
		return err
	}

	data, err := json.MarshalIndent(envelope, "", "  ")
	if err != nil {
		return err
	}

	envelopePath := filepath.Join(envelopesDir, envelope.ID+".json")
	return os.WriteFile(envelopePath, data, 0644)
}

// BootstrapAgent creates a bootstrap package for new agent session
func (cm *ContextManager) BootstrapAgent(
	ctx context.Context,
	agentID string,
	taskID string,
) (*ContextEnvelope, error) {

	// Find latest envelope for this agent/task
	envelope, err := cm.findLatestEnvelope(ctx, agentID, taskID)
	if err != nil {
		return nil, err
	}

	// Generate handoff document
	handoff := cm.generateHandoff(envelope)

	return handoff, nil
}

func (cm *ContextManager) findLatestEnvelope(ctx context.Context, agentID, taskID string) (*ContextEnvelope, error) {
	var content string
	err := cm.db.QueryRowContext(ctx, `
		SELECT content FROM context_envelopes 
		WHERE agent_id = ? AND task_id = ? 
		ORDER BY created_at DESC LIMIT 1
	`, agentID, taskID).Scan(&content)

	if err != nil {
		if err == sql.ErrNoRows {
			// Fallback to latest for agent regardless of task if task specified but not found
			err = cm.db.QueryRowContext(ctx, `
				SELECT content FROM context_envelopes 
				WHERE agent_id = ? 
				ORDER BY created_at DESC LIMIT 1
			`, agentID).Scan(&content)
			if err != nil {
				return nil, err
			}
		} else {
			return nil, err
		}
	}

	var envelope ContextEnvelope
	if err := json.Unmarshal([]byte(content), &envelope); err != nil {
		return nil, err
	}

	return &envelope, nil
}

func (cm *ContextManager) generateHandoff(envelope *ContextEnvelope) *ContextEnvelope {
	// For now, handoff IS the envelope, but we could add handoff-specific formatting or metadata
	return envelope
}

// SyncEnvelopesToFilesystem manually triggers a sync of all envelopes to filesystem
func (cm *ContextManager) SyncEnvelopesToFilesystem() error {
	if cm.sync == nil {
		return fmt.Errorf("sync not initialized")
	}
	return cm.sync.SyncEnvelopesToFilesystem()
}

// SyncDomainToFilesystem manually triggers a sync of a domain to filesystem
func (cm *ContextManager) SyncDomainToFilesystem(domain string) error {
	if cm.sync == nil {
		return fmt.Errorf("sync not initialized")
	}
	return cm.sync.SyncDomainToFilesystem(domain)
}

// HTTP Handlers

type CreateEnvelopeRequest struct {
	AgentID string `json:"agent_id"`
	Domain  string `json:"domain"`
	Project string `json:"project"`
	TaskID  string `json:"task_id"`
	Reason  string `json:"reason"`
}

func (cm *ContextManager) EnvelopesHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		// List envelopes
		rows, err := cm.db.QueryContext(r.Context(), "SELECT content FROM context_envelopes ORDER BY created_at DESC LIMIT 50")
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		defer rows.Close()

		var envelopes []ContextEnvelope
		for rows.Next() {
			var content string
			if err := rows.Scan(&content); err != nil {
				continue
			}
			var env ContextEnvelope
			if err := json.Unmarshal([]byte(content), &env); err == nil {
				envelopes = append(envelopes, env)
			}
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(envelopes)
		return
	}

	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req CreateEnvelopeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	// Sanitize and validate inputs
	req.TaskID = sanitizeID(req.TaskID)
	if req.TaskID == "" || !isValidID(req.TaskID) {
		http.Error(w, "invalid or missing task_id", http.StatusBadRequest)
		return
	}

	if strings.TrimSpace(req.AgentID) == "" {
		// Allow agent ID to be provided via header or fall back to a generic value.
		req.AgentID = strings.TrimSpace(r.Header.Get("X-Agent-ID"))
	}
	req.AgentID = sanitizeID(req.AgentID)
	if req.AgentID == "" {
		req.AgentID = "unknown-agent"
	}
	if !isValidID(req.AgentID) {
		http.Error(w, "invalid agent_id format", http.StatusBadRequest)
		return
	}

	req.Domain = sanitizeID(req.Domain)
	if req.Domain == "" {
		req.Domain = "default"
	}
	if !isValidID(req.Domain) {
		http.Error(w, "invalid domain format", http.StatusBadRequest)
		return
	}

	req.Project = sanitizeID(req.Project)
	if req.Project == "" {
		req.Project = "default"
	}
	if !isValidID(req.Project) {
		http.Error(w, "invalid project format", http.StatusBadRequest)
		return
	}

	req.Reason = sanitizeText(req.Reason)
	if req.Reason == "" {
		req.Reason = "API-generated envelope"
	}
	if !isValidText(req.Reason) {
		http.Error(w, "invalid reason format", http.StatusBadRequest)
		return
	}

	envelope, err := cm.GenerateEnvelope(r.Context(), req.AgentID, req.Domain, req.Project, req.TaskID, req.Reason)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(envelope)
}

func (cm *ContextManager) EnvelopeByIDHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	id := strings.TrimPrefix(r.URL.Path, "/api/context/envelopes/")
	id = sanitizeID(id)
	if id == "" {
		http.Error(w, "Missing envelope ID", http.StatusBadRequest)
		return
	}
	if !isValidID(id) {
		http.Error(w, "invalid envelope ID format", http.StatusBadRequest)
		return
	}

	var content string
	err := cm.db.QueryRowContext(r.Context(), "SELECT content FROM context_envelopes WHERE id = ?", id).Scan(&content)
	if err != nil {
		if err == sql.ErrNoRows {
			http.Error(w, "Envelope not found", http.StatusNotFound)
		} else {
			http.Error(w, err.Error(), http.StatusInternalServerError)
		}
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.Write([]byte(content))
}

type BootstrapRequest struct {
	AgentID string `json:"agent_id"`
	TaskID  string `json:"task_id"`
}

func (cm *ContextManager) BootstrapHandler(w http.ResponseWriter, r *http.Request) {
	var req BootstrapRequest

	switch r.Method {
	case http.MethodPost:
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
	case http.MethodGet:
		// Support bootstrap via query params for simple curl usage:
		// /api/context/bootstrap?agent_id=kimi&task_id=TASK-...
		req.AgentID = r.URL.Query().Get("agent_id")
		req.TaskID = r.URL.Query().Get("task_id")
	default:
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	if req.AgentID == "" {
		req.AgentID = r.URL.Query().Get("agent_id")
	}
	if req.TaskID == "" {
		req.TaskID = r.URL.Query().Get("task_id")
	}

	req.AgentID = sanitizeID(req.AgentID)
	req.TaskID = sanitizeID(req.TaskID)

	if req.AgentID == "" {
		http.Error(w, "agent_id is required", http.StatusBadRequest)
		return
	}
	if !isValidID(req.AgentID) {
		http.Error(w, "invalid agent_id format", http.StatusBadRequest)
		return
	}
	if req.TaskID != "" && !isValidID(req.TaskID) {
		http.Error(w, "invalid task_id format", http.StatusBadRequest)
		return
	}

	envelope, err := cm.BootstrapAgent(r.Context(), req.AgentID, req.TaskID)
	if err != nil {
		if err == sql.ErrNoRows {
			http.Error(w, "No context found for bootstrap", http.StatusNotFound)
		} else {
			http.Error(w, err.Error(), http.StatusInternalServerError)
		}
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(envelope)
}

// Interface implementation methods for interfaces.ContextEnvelope

// GetID implements interfaces.ContextEnvelope
func (e *ContextEnvelope) GetID() string { return e.ID }

// GetAgentID implements interfaces.ContextEnvelope
func (e *ContextEnvelope) GetAgentID() string { return e.AgentID }

// GetDomain implements interfaces.ContextEnvelope
func (e *ContextEnvelope) GetDomain() string { return e.Domain }

// GetProject implements interfaces.ContextEnvelope
func (e *ContextEnvelope) GetProject() string { return e.Project }

// GetTaskID implements interfaces.ContextEnvelope
func (e *ContextEnvelope) GetTaskID() string { return e.TaskID }

// GetSummary implements interfaces.ContextEnvelope
func (e *ContextEnvelope) GetSummary() string { return e.Summary }

// GetCreatedAt implements interfaces.ContextEnvelope
func (e *ContextEnvelope) GetCreatedAt() time.Time { return e.CreatedAt }

// GetExpiresAt implements interfaces.ContextEnvelope
func (e *ContextEnvelope) GetExpiresAt() time.Time { return e.ExpiresAt }
