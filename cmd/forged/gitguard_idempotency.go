package main

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/google/uuid"
)

// IdempotentAction represents a tracked idempotent operation
type IdempotentAction struct {
	ID          string          `json:"id" db:"id"`
	Type        string          `json:"type" db:"type"`
	PayloadHash string          `json:"payload_hash" db:"payload_hash"`
	ExecutedAt  time.Time       `json:"executed_at" db:"executed_at"`
	Result      json.RawMessage `json:"result" db:"result"`
}

// IdempotencyKey represents a unique key for idempotent operations
type IdempotencyKey struct {
	ActionType string `json:"action_type"`
	Payload    []byte `json:"payload"`
	ResourceID string `json:"resource_id,omitempty"`
}

// IdempotencyStore handles idempotent action storage
type IdempotencyStore struct {
	db *sql.DB
}

// NewIdempotencyStore creates a new idempotency store
func NewIdempotencyStore(db *sql.DB) *IdempotencyStore {
	return &IdempotencyStore{db: db}
}

// computeIdempotencyHash generates a SHA256 hash of the idempotency key
func computeIdempotencyHash(key IdempotencyKey) string {
	h := sha256.New()
	h.Write([]byte(key.ActionType))
	h.Write([]byte{0}) // separator
	h.Write(key.Payload)
	if key.ResourceID != "" {
		h.Write([]byte{0})
		h.Write([]byte(key.ResourceID))
	}
	return hex.EncodeToString(h.Sum(nil))
}

// hashPayload is a convenience function that hashes a payload directly
func hashPayload(actionType string, payload []byte) string {
	return computeIdempotencyHash(IdempotencyKey{
		ActionType: actionType,
		Payload:    payload,
	})
}

// ExecuteOnce ensures an action is executed only once for a given payload hash
// If the action was already executed, it returns the cached result
func (s *IdempotencyStore) ExecuteOnce(actionType string, payload []byte, fn func() (interface{}, error)) (interface{}, error) {
	payloadHash := hashPayload(actionType, payload)

	// Check if action already exists
	existing, err := s.GetByHash(payloadHash)
	if err != nil && err != sql.ErrNoRows {
		return nil, fmt.Errorf("checking existing action: %w", err)
	}

	if existing != nil {
		// Return cached result
		var result interface{}
		if err := json.Unmarshal(existing.Result, &result); err != nil {
			return nil, fmt.Errorf("unmarshaling cached result: %w", err)
		}
		return result, nil
	}

	// Execute the function
	result, err := fn()
	if err != nil {
		return nil, err
	}

	// Store the result
	resultJSON, err := json.Marshal(result)
	if err != nil {
		return nil, fmt.Errorf("marshaling result: %w", err)
	}

	action := &IdempotentAction{
		ID:          uuid.New().String(),
		Type:        actionType,
		PayloadHash: payloadHash,
		ExecutedAt:  time.Now().UTC(),
		Result:      resultJSON,
	}

	if err := s.Store(action); err != nil {
		return nil, fmt.Errorf("storing action: %w", err)
	}

	return result, nil
}

// GetByHash retrieves an action by its payload hash
func (s *IdempotencyStore) GetByHash(payloadHash string) (*IdempotentAction, error) {
	query := `SELECT id, type, payload_hash, executed_at, result FROM idempotent_actions WHERE payload_hash = ?`

	action := &IdempotentAction{}
	var executedAt string
	err := s.db.QueryRow(query, payloadHash).Scan(
		&action.ID,
		&action.Type,
		&action.PayloadHash,
		&executedAt,
		&action.Result,
	)
	if err != nil {
		return nil, err
	}
	if t, err := time.Parse(time.RFC3339, executedAt); err == nil {
		action.ExecutedAt = t
	} else if t, err := time.Parse("2006-01-02 15:04:05", executedAt); err == nil {
		action.ExecutedAt = t
	}

	return action, nil
}

// Store saves a new idempotent action
func (s *IdempotencyStore) Store(action *IdempotentAction) error {
	query := `INSERT INTO idempotent_actions (id, type, payload_hash, executed_at, result) VALUES (?, ?, ?, ?, ?)`
	_, err := s.db.Exec(query, action.ID, action.Type, action.PayloadHash, action.ExecutedAt.UTC().Format(time.RFC3339), action.Result)
	return err
}

// IsDuplicate checks if a payload hash already exists (duplicate detection)
func (s *IdempotencyStore) IsDuplicate(actionType string, payload []byte) (bool, *IdempotentAction, error) {
	payloadHash := hashPayload(actionType, payload)
	action, err := s.GetByHash(payloadHash)
	if err == sql.ErrNoRows {
		return false, nil, nil
	}
	if err != nil {
		return false, nil, err
	}
	return true, action, nil
}

// Cleanup removes actions older than the specified duration
func (s *IdempotencyStore) Cleanup(olderThan time.Duration) error {
	query := `DELETE FROM idempotent_actions WHERE executed_at < ?`
	cutoff := time.Now().UTC().Add(-olderThan).Format(time.RFC3339)
	_, err := s.db.Exec(query, cutoff)
	return err
}

// --- HTTP Handlers ---

// IdempotencyCheckRequest represents a request to check for duplicates
type IdempotencyCheckRequest struct {
	ActionType string          `json:"action_type"`
	Payload    json.RawMessage `json:"payload"`
}

// IdempotencyCheckResponse represents the response to a duplicate check
type IdempotencyCheckResponse struct {
	IsDuplicate bool              `json:"is_duplicate"`
	Action      *IdempotentAction `json:"action,omitempty"`
}

// HandleIdempotencyCheck is the HTTP endpoint for checking duplicate actions
func (s *IdempotencyStore) HandleIdempotencyCheck(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req IdempotencyCheckRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, fmt.Sprintf("Invalid JSON: %v", err), http.StatusBadRequest)
		return
	}

	isDup, action, err := s.IsDuplicate(req.ActionType, req.Payload)
	if err != nil {
		http.Error(w, fmt.Sprintf("Database error: %v", err), http.StatusInternalServerError)
		return
	}

	resp := IdempotencyCheckResponse{
		IsDuplicate: isDup,
		Action:      action,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// HandleIdempotencyExecute is the HTTP endpoint for executing idempotent actions
func (s *IdempotencyStore) HandleIdempotencyExecute(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// This endpoint would be extended to handle specific action types
	// For now, it returns the schema info
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"endpoint": "/api/v1/idempotency/execute",
		"methods":  []string{"POST"},
		"schema": map[string]string{
			"action_type": "string - type of action to execute",
			"payload":     "object - action-specific payload",
		},
	})
}

// CreateIdempotencyTable creates the idempotent_actions table
func CreateIdempotencyTable(db *sql.DB) error {
	schema := `
CREATE TABLE IF NOT EXISTS idempotent_actions (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload_hash TEXT NOT NULL UNIQUE,
    executed_at TIMESTAMPTZ DEFAULT NOW(),
    result JSONB
);

CREATE INDEX IF NOT EXISTS idx_idempotent_actions_hash ON idempotent_actions(payload_hash);
CREATE INDEX IF NOT EXISTS idx_idempotent_actions_type ON idempotent_actions(type);
CREATE INDEX IF NOT EXISTS idx_idempotent_actions_executed_at ON idempotent_actions(executed_at);
`
	_, err := db.Exec(schema)
	return err
}

// GitGuardIdempotency provides high-level idempotency for git operations
type GitGuardIdempotency struct {
	store *IdempotencyStore
}

// NewGitGuardIdempotency creates a new GitGuard idempotency handler
func NewGitGuardIdempotency(store *IdempotencyStore) *GitGuardIdempotency {
	return &GitGuardIdempotency{store: store}
}

// IdempotentCommit ensures a git commit is only executed once for given parameters
type CommitPayload struct {
	RepoPath    string   `json:"repo_path"`
	Message     string   `json:"message"`
	Files       []string `json:"files"`
	AuthorName  string   `json:"author_name"`
	AuthorEmail string   `json:"author_email"`
}

// ExecuteCommit runs a git commit idempotently
func (g *GitGuardIdempotency) ExecuteCommit(payload CommitPayload, fn func() (string, error)) (string, error) {
	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		return "", fmt.Errorf("marshaling commit payload: %w", err)
	}

	result, err := g.store.ExecuteOnce("git_commit", payloadBytes, func() (interface{}, error) {
		return fn()
	})
	if err != nil {
		return "", err
	}

	commitHash, ok := result.(string)
	if !ok {
		return "", fmt.Errorf("unexpected result type from commit operation")
	}

	return commitHash, nil
}
