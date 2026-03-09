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
	"strings"
	"sync"
	"time"
)

// HandoffStatus represents the current state of an agent handoff
type HandoffStatus string

const (
	HandoffPending   HandoffStatus = "pending"
	HandoffAccepted  HandoffStatus = "accepted"
	HandoffRejected  HandoffStatus = "rejected"
	HandoffCompleted HandoffStatus = "completed"
)

// Handoff represents a session transfer between agents
type Handoff struct {
	ID              string        `json:"id"`
	FromAgent       string        `json:"from_agent"`
	ToAgent         string        `json:"to_agent"`
	TaskDescription string        `json:"task_description"`
	Files           []string      `json:"files"`
	Priority        string        `json:"priority"`
	Status          HandoffStatus `json:"status"`
	CreatedAt       time.Time     `json:"created_at"`
	UpdatedAt       time.Time     `json:"updated_at"`
	AcceptedAt      *time.Time    `json:"accepted_at,omitempty"`
	RejectedAt      *time.Time    `json:"rejected_at,omitempty"`
	CompletedAt     *time.Time    `json:"completed_at,omitempty"`
	Reason          *string       `json:"reason,omitempty"`
	Notes           *string       `json:"notes,omitempty"`
	AcceptingAgent  *string       `json:"accepting_agent,omitempty"`
}

// HandoffStore defines the interface for handoff storage
type HandoffStore interface {
	Create(ctx context.Context, handoff Handoff) error
	Get(ctx context.Context, id string) (Handoff, error)
	Update(ctx context.Context, handoff Handoff) error
	List(ctx context.Context, status string, fromAgent string, toAgent string, limit int) ([]Handoff, error)
}

// sqliteHandoffStore implements HandoffStore using SQLite
type sqliteHandoffStore struct {
	db *sql.DB
	mu sync.RWMutex
}

// NewHandoffStore creates a new handoff store from a database connection
func NewHandoffStore(db *sql.DB) HandoffStore {
	return &sqliteHandoffStore{db: db}
}

func (s *sqliteHandoffStore) Create(ctx context.Context, h Handoff) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	filesJSON, _ := json.Marshal(h.Files)

	_, err := s.db.ExecContext(ctx, `
		INSERT INTO handoffs (
			id, from_agent, to_agent, task_description, files, priority, status,
			created_at, updated_at, accepted_at, rejected_at, completed_at,
			reason, notes, accepting_agent
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		h.ID, h.FromAgent, h.ToAgent, h.TaskDescription, string(filesJSON), h.Priority, string(h.Status),
		h.CreatedAt, h.UpdatedAt, h.AcceptedAt, h.RejectedAt, h.CompletedAt,
		h.Reason, h.Notes, h.AcceptingAgent,
	)
	return err
}

func parseHandoffTime(s string) time.Time {
	for _, layout := range []string{time.RFC3339Nano, time.RFC3339, "2006-01-02 15:04:05"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t
		}
	}
	return time.Time{}
}

func parseHandoffNullTime(s sql.NullString) *time.Time {
	if !s.Valid || s.String == "" {
		return nil
	}
	t := parseHandoffTime(s.String)
	return &t
}

func (s *sqliteHandoffStore) Get(ctx context.Context, id string) (Handoff, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var h Handoff
	var filesJSON, createdAt, updatedAt string
	var acceptedAt, rejectedAt, completedAt sql.NullString
	var reason, notes, acceptingAgent sql.NullString

	err := s.db.QueryRowContext(ctx, `
		SELECT id, from_agent, to_agent, task_description, files, priority, status,
			created_at, updated_at, accepted_at, rejected_at, completed_at,
			reason, notes, accepting_agent
		FROM handoffs WHERE id = ?`, id,
	).Scan(
		&h.ID, &h.FromAgent, &h.ToAgent, &h.TaskDescription, &filesJSON, &h.Priority, &h.Status,
		&createdAt, &updatedAt, &acceptedAt, &rejectedAt, &completedAt,
		&reason, &notes, &acceptingAgent,
	)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return Handoff{}, fmt.Errorf("handoff not found: %s", id)
		}
		return Handoff{}, err
	}

	h.CreatedAt = parseHandoffTime(createdAt)
	h.UpdatedAt = parseHandoffTime(updatedAt)
	json.Unmarshal([]byte(filesJSON), &h.Files)
	h.AcceptedAt = parseHandoffNullTime(acceptedAt)
	h.RejectedAt = parseHandoffNullTime(rejectedAt)
	h.CompletedAt = parseHandoffNullTime(completedAt)
	if reason.Valid {
		h.Reason = &reason.String
	}
	if notes.Valid {
		h.Notes = &notes.String
	}
	if acceptingAgent.Valid {
		h.AcceptingAgent = &acceptingAgent.String
	}

	return h, nil
}

func (s *sqliteHandoffStore) Update(ctx context.Context, h Handoff) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	filesJSON, _ := json.Marshal(h.Files)

	_, err := s.db.ExecContext(ctx, `
		UPDATE handoffs SET
			from_agent = ?, to_agent = ?, task_description = ?, files = ?,
			priority = ?, status = ?, updated_at = ?, accepted_at = ?,
			rejected_at = ?, completed_at = ?, reason = ?, notes = ?,
			accepting_agent = ?
		WHERE id = ?`,
		h.FromAgent, h.ToAgent, h.TaskDescription, string(filesJSON),
		h.Priority, string(h.Status), h.UpdatedAt, h.AcceptedAt,
		h.RejectedAt, h.CompletedAt, h.Reason, h.Notes,
		h.AcceptingAgent, h.ID,
	)
	return err
}

func (s *sqliteHandoffStore) List(ctx context.Context, status string, fromAgent string, toAgent string, limit int) ([]Handoff, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if limit <= 0 {
		limit = 50
	}

	query := `
		SELECT id, from_agent, to_agent, task_description, files, priority, status,
			created_at, updated_at, accepted_at, rejected_at, completed_at,
			reason, notes, accepting_agent
		FROM handoffs WHERE 1=1`
	var args []interface{}

	if status != "" {
		query += " AND status = ?"
		args = append(args, status)
	}
	if fromAgent != "" {
		query += " AND from_agent = ?"
		args = append(args, fromAgent)
	}
	if toAgent != "" {
		query += " AND to_agent = ?"
		args = append(args, toAgent)
	}

	query += " ORDER BY created_at DESC LIMIT ?"
	args = append(args, limit)

	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var handoffs []Handoff
	for rows.Next() {
		var h Handoff
		var filesJSON, createdAt, updatedAt string
		var acceptedAt, rejectedAt, completedAt sql.NullString
		var reason, notes, acceptingAgent sql.NullString

		err := rows.Scan(
			&h.ID, &h.FromAgent, &h.ToAgent, &h.TaskDescription, &filesJSON, &h.Priority, &h.Status,
			&createdAt, &updatedAt, &acceptedAt, &rejectedAt, &completedAt,
			&reason, &notes, &acceptingAgent,
		)
		if err != nil {
			return nil, err
		}

		h.CreatedAt = parseHandoffTime(createdAt)
		h.UpdatedAt = parseHandoffTime(updatedAt)
		json.Unmarshal([]byte(filesJSON), &h.Files)
		h.AcceptedAt = parseHandoffNullTime(acceptedAt)
		h.RejectedAt = parseHandoffNullTime(rejectedAt)
		h.CompletedAt = parseHandoffNullTime(completedAt)
		if reason.Valid {
			h.Reason = &reason.String
		}
		if notes.Valid {
			h.Notes = &notes.String
		}
		if acceptingAgent.Valid {
			h.AcceptingAgent = &acceptingAgent.String
		}

		handoffs = append(handoffs, h)
	}

	return handoffs, rows.Err()
}

// HandoffService handles handoff business logic
type HandoffService struct {
	store HandoffStore
}

// NewHandoffService creates a new handoff service
func NewHandoffService(store HandoffStore) *HandoffService {
	return &HandoffService{store: store}
}

// Create creates a new handoff
func (s *HandoffService) Create(ctx context.Context, from, to, desc string, files []string, priority string) (Handoff, error) {
	if priority == "" {
		priority = "medium"
	}
	h := Handoff{
		ID:              generateULID(),
		FromAgent:       from,
		ToAgent:         to,
		TaskDescription: desc,
		Files:           files,
		Priority:        priority,
		Status:          HandoffPending,
		CreatedAt:       time.Now(),
		UpdatedAt:       time.Now(),
	}
	if err := s.store.Create(ctx, h); err != nil {
		return Handoff{}, err
	}
	return h, nil
}

// Accept marks a handoff as accepted
func (s *HandoffService) Accept(ctx context.Context, id string, acceptingAgent string) (Handoff, error) {
	h, err := s.store.Get(ctx, id)
	if err != nil {
		return Handoff{}, err
	}
	if h.Status != HandoffPending {
		return Handoff{}, fmt.Errorf("handoff is in status %s, cannot accept", h.Status)
	}

	now := time.Now()
	h.Status = HandoffAccepted
	h.AcceptedAt = &now
	h.UpdatedAt = now
	if acceptingAgent != "" {
		h.AcceptingAgent = &acceptingAgent
	} else {
		h.AcceptingAgent = &h.ToAgent
	}

	if err := s.store.Update(ctx, h); err != nil {
		return Handoff{}, err
	}
	return h, nil
}

// Reject marks a handoff as rejected
func (s *HandoffService) Reject(ctx context.Context, id string, reason string) (Handoff, error) {
	h, err := s.store.Get(ctx, id)
	if err != nil {
		return Handoff{}, err
	}
	if h.Status != HandoffPending {
		return Handoff{}, fmt.Errorf("handoff is in status %s, cannot reject", h.Status)
	}

	now := time.Now()
	h.Status = HandoffRejected
	h.RejectedAt = &now
	h.UpdatedAt = now
	h.Reason = &reason

	if err := s.store.Update(ctx, h); err != nil {
		return Handoff{}, err
	}
	return h, nil
}

// Complete marks a handoff as completed
func (s *HandoffService) Complete(ctx context.Context, id string, notes string) (Handoff, error) {
	h, err := s.store.Get(ctx, id)
	if err != nil {
		return Handoff{}, err
	}
	if h.Status != HandoffAccepted {
		return Handoff{}, fmt.Errorf("handoff is in status %s, cannot complete", h.Status)
	}

	now := time.Now()
	h.Status = HandoffCompleted
	h.CompletedAt = &now
	h.UpdatedAt = now
	h.Notes = &notes

	if err := s.store.Update(ctx, h); err != nil {
		return Handoff{}, err
	}
	return h, nil
}

// HandoffHandler handles HTTP requests for handoffs
type HandoffHandler struct {
	service *HandoffService
}

// NewHandoffHandler creates a new handoff HTTP handler
func NewHandoffHandler(service *HandoffService) *HandoffHandler {
	return &HandoffHandler{service: service}
}

// RegisterRoutes registers the handoff routes
func (h *HandoffHandler) RegisterRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/api/handoffs", h.handleHandoffs)
	mux.HandleFunc("/api/handoffs/", h.handleHandoffAction)
}

func (h *HandoffHandler) handleHandoffs(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	switch r.Method {
	case http.MethodGet:
		h.listHandoffs(w, r)
	case http.MethodPost:
		h.createHandoff(w, r)
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (h *HandoffHandler) createHandoff(w http.ResponseWriter, r *http.Request) {
	var req struct {
		FromAgent       string   `json:"from_agent"`
		ToAgent         string   `json:"to_agent"`
		TaskDescription string   `json:"task_description"`
		Files           []string `json:"files"`
		Priority        string   `json:"priority"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSONError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	if req.FromAgent == "" || req.ToAgent == "" || req.TaskDescription == "" {
		writeJSONError(w, http.StatusBadRequest, "missing required fields")
		return
	}

	handoff, err := h.service.Create(r.Context(), req.FromAgent, req.ToAgent, req.TaskDescription, req.Files, req.Priority)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, err.Error())
		return
	}

	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"success": True,
		"data":    handoff,
	})
}

func (h *HandoffHandler) listHandoffs(w http.ResponseWriter, r *http.Request) {
	status := r.URL.Query().Get("status")
	fromAgent := r.URL.Query().Get("from_agent")
	toAgent := r.URL.Query().Get("to_agent")
	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		fmt.Sscanf(l, "%d", &limit)
	}

	handoffs, err := h.service.store.List(r.Context(), status, fromAgent, toAgent, limit)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, err.Error())
		return
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"success": True,
		"data": map[string]interface{}{
			"handoffs": handoffs,
			"count":     len(handoffs),
		},
	})
}

func (h *HandoffHandler) handleHandoffAction(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	// Parse path: /api/handoffs/{id}/accept, reject, or complete
	path := strings.TrimPrefix(r.URL.Path, "/api/handoffs/")
	parts := strings.Split(path, "/")
	if len(parts) != 2 {
		http.Error(w, "invalid path", http.StatusBadRequest)
		return
	}

	id := parts[0]
	action := parts[1]

	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var handoff Handoff
	var err error

	switch action {
	case "accept":
		var req struct {
			AcceptingAgent string `json:"accepting_agent"`
		}
		json.NewDecoder(r.Body).Decode(&req)
		handoff, err = h.service.Accept(r.Context(), id, req.AcceptingAgent)
	case "reject":
		var req struct {
			Reason string `json:"reason"`
		}
		json.NewDecoder(r.Body).Decode(&req)
		if req.Reason == "" {
			writeJSONError(w, http.StatusBadRequest, "reason required for rejection")
			return
		}
		handoff, err = h.service.Reject(r.Context(), id, req.Reason)
	case "complete":
		var req struct {
			Notes string `json:"notes"`
		}
		json.NewDecoder(r.Body).Decode(&req)
		handoff, err = h.service.Complete(r.Context(), id, req.Notes)
	default:
		http.Error(w, "unknown action", http.StatusBadRequest)
		return
	}

	if err != nil {
		if strings.Contains(err.Error(), "not found") {
			writeJSONError(w, http.StatusNotFound, err.Error())
		} else {
			writeJSONError(w, http.StatusBadRequest, err.Error())
		}
		return
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"success": True,
		"data":    handoff,
	})
}

// True constant for JSON consistency with Python harness if needed
const True = true
