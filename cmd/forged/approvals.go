//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"gopkg.in/yaml.v3"
)

// ApprovalType represents the type of approval requested
type ApprovalType string

const (
	ApprovalTaskCompletion ApprovalType = "task_completion"
	ApprovalMerge          ApprovalType = "merge"
	ApprovalDeploy         ApprovalType = "deploy"
	ApprovalSecurity       ApprovalType = "security"
	ApprovalBudget         ApprovalType = "budget"
	ApprovalPattern        ApprovalType = "pattern"
	ApprovalLane           ApprovalType = "lane"
	ApprovalDestructive    ApprovalType = "destructive"
)

// ApprovalTier represents the risk tier for the approval
type ApprovalTier string

const (
	TierWatch   ApprovalTier = "watch"   // Auto-approve, log only
	TierPhone   ApprovalTier = "phone"   // Async notification
	TierDesktop ApprovalTier = "desktop" // Block for approval
)

// ApprovalStatus represents the current state of an approval
type ApprovalStatus string

const (
	StatusPending      ApprovalStatus = "pending"
	StatusApproved     ApprovalStatus = "approved"
	StatusRejected     ApprovalStatus = "rejected"
	StatusExpired      ApprovalStatus = "expired"
	StatusAutoApproved ApprovalStatus = "auto_approved"
)

// Approval represents a human-in-the-loop approval request
type Approval struct {
	ID              string         `json:"id"`
	Type            ApprovalType   `json:"type"`
	TaskID          *string        `json:"task_id,omitempty"`
	AgentID         string         `json:"agent_id"`
	Domain          string         `json:"domain"`
	Title           string         `json:"title"`
	Description     string         `json:"description,omitempty"`
	DiffSummary     *string        `json:"diff_summary,omitempty"`
	RiskScore       float64        `json:"risk_score"`
	ConfidenceScore float64        `json:"confidence_score"`
	Recommendation  string         `json:"recommendation,omitempty"`
	Tier            ApprovalTier   `json:"tier"`
	Status          ApprovalStatus `json:"status"`
	CreatedAt       time.Time      `json:"created_at"`
	ExpiresAt       time.Time      `json:"expires_at"`
	ResolvedAt      *time.Time     `json:"resolved_at,omitempty"`
	ResolvedBy      *string        `json:"resolved_by,omitempty"`
}

// ConfidenceResult represents the outcome of confidence scoring
type ConfidenceResult struct {
	Score       float64            `json:"score"`
	AutoApprove bool               `json:"auto_approve"`
	Tier        ApprovalTier       `json:"tier"`
	Components  map[string]float64 `json:"components"`
	Reasoning   string             `json:"reasoning"`
}

// ConfidenceContext provides context for confidence scoring
type ConfidenceContext struct {
	Task         *Task       `json:"task,omitempty"`
	TestResults  *TestResult `json:"test_results,omitempty"`
	PatternMatch float64     `json:"pattern_match"` // 0.0-1.0 pattern similarity
	BlastRadius  int         `json:"blast_radius"`  // Number of files affected
	Reversible   bool        `json:"reversible"`
}

// TestResult represents test execution results
type TestResult struct {
	Passed   int     `json:"passed"`
	Failed   int     `json:"failed"`
	Skipped  int     `json:"skipped"`
	Coverage float64 `json:"coverage"`
}

// ApprovalStore defines the interface for approval storage
type ApprovalStore interface {
	Create(ctx context.Context, approval Approval) error
	Get(ctx context.Context, id string) (Approval, error)
	Update(ctx context.Context, approval Approval) error
	ListPending(ctx context.Context, limit int) ([]Approval, error)
	ListByStatus(ctx context.Context, status ApprovalStatus, limit int) ([]Approval, error)
	ListByTier(ctx context.Context, tier ApprovalTier, limit int) ([]Approval, error)
	ListByAgent(ctx context.Context, agentID string, limit int) ([]Approval, error)
	ListByTask(ctx context.Context, taskID string, limit int) ([]Approval, error)
	Approve(ctx context.Context, id, userID string) error
	Reject(ctx context.Context, id, userID string) error
	ExpireOldApprovals(ctx context.Context, before time.Time) (int64, error)
}

// sqliteApprovalStore implements ApprovalStore using SQLite
type sqliteApprovalStore struct {
	db *sql.DB
	mu sync.RWMutex
}

// NewApprovalStore creates a new approval store from a database connection
func NewApprovalStore(db *sql.DB) ApprovalStore {
	return &sqliteApprovalStore{db: db}
}

func (s *sqliteApprovalStore) Create(ctx context.Context, approval Approval) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	_, err := s.db.ExecContext(ctx, `
		INSERT INTO approvals (
			id, type, task_id, agent_id, domain, title, description, diff_summary,
			risk_score, confidence_score, recommendation, tier, status,
			created_at, expires_at, resolved_at, resolved_by
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		approval.ID, approval.Type, approval.TaskID, approval.AgentID, approval.Domain,
		approval.Title, approval.Description, approval.DiffSummary,
		approval.RiskScore, approval.ConfidenceScore, approval.Recommendation,
		approval.Tier, approval.Status, approval.CreatedAt, approval.ExpiresAt,
		approval.ResolvedAt, approval.ResolvedBy,
	)
	return err
}

func (s *sqliteApprovalStore) Get(ctx context.Context, id string) (Approval, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var approval Approval
	var taskID sql.NullString
	var diffSummary, recommendation, resolvedBy sql.NullString
	var resolvedAt sql.NullTime

	err := s.db.QueryRowContext(ctx, `
		SELECT id, type, task_id, agent_id, domain, title, description, diff_summary,
			risk_score, confidence_score, recommendation, tier, status,
			created_at, expires_at, resolved_at, resolved_by
		FROM approvals WHERE id = ?`, id,
	).Scan(
		&approval.ID, &approval.Type, &taskID, &approval.AgentID, &approval.Domain,
		&approval.Title, &approval.Description, &diffSummary,
		&approval.RiskScore, &approval.ConfidenceScore, &recommendation,
		&approval.Tier, &approval.Status, &approval.CreatedAt, &approval.ExpiresAt,
		&resolvedAt, &resolvedBy,
	)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return Approval{}, fmt.Errorf("approval not found: %s", id)
		}
		return Approval{}, err
	}

	if taskID.Valid {
		approval.TaskID = &taskID.String
	}
	if diffSummary.Valid {
		approval.DiffSummary = &diffSummary.String
	}
	if recommendation.Valid {
		approval.Recommendation = recommendation.String
	}
	if resolvedAt.Valid {
		approval.ResolvedAt = &resolvedAt.Time
	}
	if resolvedBy.Valid {
		approval.ResolvedBy = &resolvedBy.String
	}

	return approval, nil
}

func (s *sqliteApprovalStore) Update(ctx context.Context, approval Approval) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	_, err := s.db.ExecContext(ctx, `
		UPDATE approvals SET
			type = ?, task_id = ?, agent_id = ?, domain = ?, title = ?,
			description = ?, diff_summary = ?, risk_score = ?, confidence_score = ?,
			recommendation = ?, tier = ?, status = ?, created_at = ?,
			expires_at = ?, resolved_at = ?, resolved_by = ?
		WHERE id = ?`,
		approval.Type, approval.TaskID, approval.AgentID, approval.Domain, approval.Title,
		approval.Description, approval.DiffSummary, approval.RiskScore, approval.ConfidenceScore,
		approval.Recommendation, approval.Tier, approval.Status, approval.CreatedAt,
		approval.ExpiresAt, approval.ResolvedAt, approval.ResolvedBy, approval.ID,
	)
	return err
}

func (s *sqliteApprovalStore) ListPending(ctx context.Context, limit int) ([]Approval, error) {
	return s.ListByStatus(ctx, StatusPending, limit)
}

func (s *sqliteApprovalStore) ListByStatus(ctx context.Context, status ApprovalStatus, limit int) ([]Approval, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if limit <= 0 {
		limit = 50
	}

	rows, err := s.db.QueryContext(ctx, `
		SELECT id, type, task_id, agent_id, domain, title, description, diff_summary,
			risk_score, confidence_score, recommendation, tier, status,
			created_at, expires_at, resolved_at, resolved_by
		FROM approvals WHERE status = ? ORDER BY created_at DESC LIMIT ?`,
		status, limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	return s.scanApprovals(rows)
}

func (s *sqliteApprovalStore) ListByTier(ctx context.Context, tier ApprovalTier, limit int) ([]Approval, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if limit <= 0 {
		limit = 50
	}

	rows, err := s.db.QueryContext(ctx, `
		SELECT id, type, task_id, agent_id, domain, title, description, diff_summary,
			risk_score, confidence_score, recommendation, tier, status,
			created_at, expires_at, resolved_at, resolved_by
		FROM approvals WHERE tier = ? ORDER BY created_at DESC LIMIT ?`,
		tier, limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	return s.scanApprovals(rows)
}

func (s *sqliteApprovalStore) ListByAgent(ctx context.Context, agentID string, limit int) ([]Approval, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if limit <= 0 {
		limit = 50
	}

	rows, err := s.db.QueryContext(ctx, `
		SELECT id, type, task_id, agent_id, domain, title, description, diff_summary,
			risk_score, confidence_score, recommendation, tier, status,
			created_at, expires_at, resolved_at, resolved_by
		FROM approvals WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?`,
		agentID, limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	return s.scanApprovals(rows)
}

func (s *sqliteApprovalStore) ListByTask(ctx context.Context, taskID string, limit int) ([]Approval, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if limit <= 0 {
		limit = 50
	}

	rows, err := s.db.QueryContext(ctx, `
		SELECT id, type, task_id, agent_id, domain, title, description, diff_summary,
			risk_score, confidence_score, recommendation, tier, status,
			created_at, expires_at, resolved_at, resolved_by
		FROM approvals WHERE task_id = ? ORDER BY created_at DESC LIMIT ?`,
		taskID, limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	return s.scanApprovals(rows)
}

func (s *sqliteApprovalStore) Approve(ctx context.Context, id, userID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	now := time.Now()
	result, err := s.db.ExecContext(ctx, `
		UPDATE approvals 
		SET status = ?, resolved_at = ?, resolved_by = ?
		WHERE id = ? AND status = ?`,
		StatusApproved, now, userID, id, StatusPending,
	)
	if err != nil {
		return err
	}

	rows, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if rows == 0 {
		return fmt.Errorf("approval not found or already resolved: %s", id)
	}

	return nil
}

func (s *sqliteApprovalStore) Reject(ctx context.Context, id, userID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	now := time.Now()
	result, err := s.db.ExecContext(ctx, `
		UPDATE approvals 
		SET status = ?, resolved_at = ?, resolved_by = ?
		WHERE id = ? AND status = ?`,
		StatusRejected, now, userID, id, StatusPending,
	)
	if err != nil {
		return err
	}

	rows, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if rows == 0 {
		return fmt.Errorf("approval not found or already resolved: %s", id)
	}

	return nil
}

func (s *sqliteApprovalStore) ExpireOldApprovals(ctx context.Context, before time.Time) (int64, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	result, err := s.db.ExecContext(ctx, `
		UPDATE approvals 
		SET status = ?
		WHERE status = ? AND expires_at < ?`,
		StatusExpired, StatusPending, before,
	)
	if err != nil {
		return 0, err
	}

	return result.RowsAffected()
}

func (s *sqliteApprovalStore) scanApprovals(rows *sql.Rows) ([]Approval, error) {
	var approvals []Approval

	for rows.Next() {
		var approval Approval
		var taskID sql.NullString
		var diffSummary, recommendation, resolvedBy sql.NullString
		var resolvedAt sql.NullTime

		err := rows.Scan(
			&approval.ID, &approval.Type, &taskID, &approval.AgentID, &approval.Domain,
			&approval.Title, &approval.Description, &diffSummary,
			&approval.RiskScore, &approval.ConfidenceScore, &recommendation,
			&approval.Tier, &approval.Status, &approval.CreatedAt, &approval.ExpiresAt,
			&resolvedAt, &resolvedBy,
		)
		if err != nil {
			return nil, err
		}

		if taskID.Valid {
			approval.TaskID = &taskID.String
		}
		if diffSummary.Valid {
			approval.DiffSummary = &diffSummary.String
		}
		if recommendation.Valid {
			approval.Recommendation = recommendation.String
		}
		if resolvedAt.Valid {
			approval.ResolvedAt = &resolvedAt.Time
		}
		if resolvedBy.Valid {
			approval.ResolvedBy = &resolvedBy.String
		}

		approvals = append(approvals, approval)
	}

	return approvals, rows.Err()
}

// ApprovalStagePolicyEntry defines the tier and confidence threshold for a portfolio stage.
type ApprovalStagePolicyEntry struct {
	Tier                ApprovalTier `yaml:"tier"`
	ConfidenceThreshold float64      `yaml:"confidence_threshold"`
	Notes               string       `yaml:"notes"`
}

// ApprovalTierConfig is the top-level structure of approval-tiers.yaml.
type ApprovalTierConfig struct {
	ApprovalTiers map[string]ApprovalStagePolicyEntry `yaml:"approval_tiers"`
}

// defaultApprovalTierPolicies are used when the YAML config cannot be loaded.
var defaultApprovalTierPolicies = map[string]ApprovalStagePolicyEntry{
	"idea":     {Tier: TierWatch, ConfidenceThreshold: 1.0},
	"validate": {Tier: TierPhone, ConfidenceThreshold: 0.0},
	"build":    {Tier: TierDesktop, ConfidenceThreshold: 0.80},
	"deploy":   {Tier: TierDesktop, ConfidenceThreshold: 0.95},
	"measure":  {Tier: TierPhone, ConfidenceThreshold: 0.70},
	"monetize": {Tier: TierDesktop, ConfidenceThreshold: 0.0},
	"scale":    {Tier: TierPhone, ConfidenceThreshold: 0.85},
	"kill":     {Tier: TierWatch, ConfidenceThreshold: 1.0},
}

// approvalTierPoliciesOnce guards one-time loading of approval-tiers.yaml.
var approvalTierPoliciesOnce sync.Once

// approvalTierPoliciesCache holds the loaded (or default) stage→policy map.
var approvalTierPoliciesCache map[string]ApprovalStagePolicyEntry

// loadApprovalTierPolicies reads config/dark-factory/approval-tiers.yaml from
// the forge root and returns a stage→policy map. It falls back to
// defaultApprovalTierPolicies when the file is absent or cannot be parsed.
// Results are cached after the first successful call.
func loadApprovalTierPolicies() map[string]ApprovalStagePolicyEntry {
	approvalTierPoliciesOnce.Do(func() {
		approvalTierPoliciesCache = loadApprovalTierPoliciesFromRoot(forgeRoot())
	})
	return approvalTierPoliciesCache
}

// loadApprovalTierPoliciesFromRoot is the testable, root-parameterized variant
// of loadApprovalTierPolicies. It does NOT use the sync.Once cache.
func loadApprovalTierPoliciesFromRoot(root string) map[string]ApprovalStagePolicyEntry {
	cfgPath := filepath.Join(root, "config", "dark-factory", "approval-tiers.yaml")
	data, err := os.ReadFile(cfgPath)
	if err != nil {
		// File absent or unreadable — fall back to hardcoded defaults.
		return defaultApprovalTierPolicies
	}
	var cfg ApprovalTierConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil || len(cfg.ApprovalTiers) == 0 {
		// Parse failure or empty file — fall back to hardcoded defaults.
		return defaultApprovalTierPolicies
	}
	return cfg.ApprovalTiers
}

// GetApprovalTierForStage returns the ApprovalTier and confidence threshold for
// a given portfolio stage. Returns ("", 0) for an empty stage. Falls back to
// TierDesktop (safest) with threshold 0.0 for unknown stages.
// Policies are sourced from config/dark-factory/approval-tiers.yaml when
// available, otherwise from the hardcoded defaultApprovalTierPolicies.
func GetApprovalTierForStage(stage string) (ApprovalTier, float64) {
	if stage == "" {
		return "", 0
	}
	policies := loadApprovalTierPolicies()
	if policy, ok := policies[strings.ToLower(stage)]; ok {
		return policy.Tier, policy.ConfidenceThreshold
	}
	return TierDesktop, 0.0 // unknown stage = safest default
}

// ApprovalService handles approval business logic including confidence scoring
type ApprovalService struct {
	store ApprovalStore
}

// NewApprovalService creates a new approval service
func NewApprovalService(store ApprovalStore) *ApprovalService {
	return &ApprovalService{store: store}
}

// CreateApprovalRequest creates a new approval request with confidence scoring
func (as *ApprovalService) CreateApprovalRequest(
	ctx context.Context,
	approvalType ApprovalType,
	taskID *string,
	agentID, domain, title, description string,
	diffSummary *string,
	context ConfidenceContext,
) (*Approval, error) {
	// Calculate confidence score
	confidence := as.CalculateConfidence(context)

	// Determine tier and auto-approve threshold
	tier := confidence.Tier
	status := StatusPending

	// Auto-approve if confidence >= 0.95
	if confidence.AutoApprove {
		status = StatusAutoApproved
	}

	// Create approval
	approval := Approval{
		ID:              generateULID(),
		Type:            approvalType,
		TaskID:          taskID,
		AgentID:         agentID,
		Domain:          domain,
		Title:           title,
		Description:     description,
		DiffSummary:     diffSummary,
		RiskScore:       1.0 - confidence.Score, // Risk is inverse of confidence
		ConfidenceScore: confidence.Score,
		Recommendation:  confidence.Reasoning,
		Tier:            tier,
		Status:          status,
		CreatedAt:       time.Now(),
		ExpiresAt:       time.Now().Add(24 * time.Hour), // Default 24h expiry
	}

	if err := as.store.Create(ctx, approval); err != nil {
		return nil, err
	}

	return &approval, nil
}

// CalculateConfidence computes a confidence score based on multiple factors
func (as *ApprovalService) CalculateConfidence(ctx ConfidenceContext) ConfidenceResult {
	components := make(map[string]float64)

	// Pattern match score (35% weight) - similarity to successful past patterns
	components["pattern"] = ctx.PatternMatch * 0.35

	// Test score (25% weight) - based on test results
	testScore := 0.5 // Neutral default
	if ctx.TestResults != nil {
		total := ctx.TestResults.Passed + ctx.TestResults.Failed + ctx.TestResults.Skipped
		if total > 0 {
			passRate := float64(ctx.TestResults.Passed) / float64(total)
			coverageWeight := ctx.TestResults.Coverage / 100.0
			testScore = (passRate*0.7 + coverageWeight*0.3)
		}
	}
	components["tests"] = testScore * 0.25

	// Blast radius score (15% weight) - smaller is better
	blastScore := 1.0
	if ctx.BlastRadius > 50 {
		blastScore = 0.3 // High blast radius = low score
	} else if ctx.BlastRadius > 20 {
		blastScore = 0.6 // Medium blast radius
	} else if ctx.BlastRadius > 5 {
		blastScore = 0.8 // Small blast radius
	} else {
		blastScore = 1.0 // Very small blast radius
	}
	components["blast_radius"] = blastScore * 0.15

	// Reversibility score (15% weight)
	reversibilityScore := 0.5
	if ctx.Reversible {
		reversibilityScore = 1.0
	} else {
		reversibilityScore = 0.3
	}
	components["reversibility"] = reversibilityScore * 0.15

	// Domain maturity score (10% weight) - would need domain history
	// For now, use a neutral score
	components["maturity"] = 0.7 * 0.10

	// Calculate overall score
	overall := 0.0
	for _, v := range components {
		overall += v
	}

	// Determine tier and auto-approve
	var tier ApprovalTier
	var autoApprove bool
	var reasoning string

	switch {
	case overall >= 0.95:
		tier = TierWatch
		autoApprove = true
		reasoning = fmt.Sprintf("High confidence (%.0f%%): All quality gates passed. Auto-approved.", overall*100)
	case overall >= 0.70:
		tier = TierPhone
		autoApprove = false
		reasoning = fmt.Sprintf("Medium confidence (%.0f%%): Minor review recommended.", overall*100)
	default:
		tier = TierDesktop
		autoApprove = false
		reasoning = fmt.Sprintf("Low confidence (%.0f%%): Requires careful human review.", overall*100)
	}

	return ConfidenceResult{
		Score:       overall,
		AutoApprove: autoApprove,
		Tier:        tier,
		Components:  components,
		Reasoning:   reasoning,
	}
}

// Approve approves an approval request
func (as *ApprovalService) Approve(ctx context.Context, id, userID string) error {
	return as.store.Approve(ctx, id, userID)
}

// Reject rejects an approval request
func (as *ApprovalService) Reject(ctx context.Context, id, userID string) error {
	return as.store.Reject(ctx, id, userID)
}

// GetPending returns all pending approvals
func (as *ApprovalService) GetPending(ctx context.Context, limit int) ([]Approval, error) {
	return as.store.ListPending(ctx, limit)
}

// ExpireOld marks expired approvals
func (as *ApprovalService) ExpireOld(ctx context.Context) (int64, error) {
	return as.store.ExpireOldApprovals(ctx, time.Now())
}

// ApprovalHandler handles HTTP requests for approvals
type ApprovalHandler struct {
	service *ApprovalService
}

// NewApprovalHandler creates a new approval HTTP handler
func NewApprovalHandler(service *ApprovalService) *ApprovalHandler {
	return &ApprovalHandler{service: service}
}

// RegisterRoutes registers the approval routes
func (h *ApprovalHandler) RegisterRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/api/approvals", h.handleApprovals)
	mux.HandleFunc("/api/approvals/count", h.handleApprovalCount)
	mux.HandleFunc("/api/approvals/pending", h.handlePending)
	mux.HandleFunc("/api/approvals/", h.handleApprovalAction)
}

// handleApprovalCount serves GET /api/approvals/count.
// Returns {"pending": N} for the React PWA approval badge.
func (h *ApprovalHandler) handleApprovalCount(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")

	approvals, err := h.service.GetPending(r.Context(), 1000)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, fmt.Sprintf("failed to count approvals: %v", err))
		return
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"pending": len(approvals),
	})
}

func (h *ApprovalHandler) handleApprovals(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	switch r.Method {
	case http.MethodGet:
		h.listApprovals(w, r)
	case http.MethodPost:
		h.createApproval(w, r)
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (h *ApprovalHandler) createApproval(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Type        string  `json:"type"`
		TaskID      *string `json:"task_id,omitempty"`
		AgentID     string  `json:"agent_id"`
		Domain      string  `json:"domain"`
		Title       string  `json:"title"`
		Description string  `json:"description,omitempty"`
		Tier        string  `json:"tier,omitempty"`
		DiffSummary *string `json:"diff_summary,omitempty"`
		Confidence  *struct {
			PatternMatch float64     `json:"pattern_match"`
			TestResults  *TestResult `json:"test_results,omitempty"`
			BlastRadius  int         `json:"blast_radius"`
			Reversible   bool        `json:"reversible"`
		} `json:"confidence_context,omitempty"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSONError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	// Validate required fields
	if req.Type == "" || req.AgentID == "" || req.Domain == "" || req.Title == "" {
		writeJSONError(w, http.StatusBadRequest, "missing required fields: type, agent_id, domain, title")
		return
	}

	// Validate task_id exists (required for approval creation).
	if req.TaskID == nil || strings.TrimSpace(*req.TaskID) == "" {
		writeJSONError(w, http.StatusBadRequest, "task_id required")
		return
	}
	sanitizedTaskID := sanitizeID(*req.TaskID)
	if sanitizedTaskID == "" || !isValidID(sanitizedTaskID) {
		writeJSONError(w, http.StatusBadRequest, "invalid task_id")
		return
	}
	req.TaskID = &sanitizedTaskID

	// Ensure referenced task exists.
	if taskQueue != nil {
		if _, err := taskQueue.GetTask(r.Context(), sanitizedTaskID); err != nil {
			writeJSONError(w, http.StatusBadRequest, "task_id not found")
			return
		}
	} else if db := getDBConn(); db != nil {
		var one int
		if err := db.QueryRowContext(r.Context(), "SELECT 1 FROM tasks WHERE id = ? LIMIT 1", sanitizedTaskID).Scan(&one); err != nil {
			writeJSONError(w, http.StatusBadRequest, "task_id not found")
			return
		}
	}

	// Validate tier if provided.
	if strings.TrimSpace(req.Tier) != "" {
		switch strings.ToUpper(strings.TrimSpace(req.Tier)) {
		case "WATCH", "PHONE", "DESKTOP":
			// valid, currently informational (tier is computed by confidence scoring)
		default:
			writeJSONError(w, http.StatusBadRequest, "tier must be one of WATCH, PHONE, DESKTOP")
			return
		}
	}

	// Validate approval type
	approvalType := ApprovalType(req.Type)
	validTypes := map[ApprovalType]bool{
		ApprovalTaskCompletion: true,
		ApprovalMerge:          true,
		ApprovalDeploy:         true,
		ApprovalSecurity:       true,
		ApprovalBudget:         true,
		ApprovalPattern:        true,
		ApprovalLane:           true,
		ApprovalDestructive:    true,
	}
	if !validTypes[approvalType] {
		writeJSONError(w, http.StatusBadRequest, fmt.Sprintf("invalid approval type: %s", req.Type))
		return
	}

	// Build confidence context
	ctx := ConfidenceContext{
		PatternMatch: 0.5,
		BlastRadius:  10,
		Reversible:   false,
	}
	if req.Confidence != nil {
		ctx.PatternMatch = req.Confidence.PatternMatch
		ctx.TestResults = req.Confidence.TestResults
		ctx.BlastRadius = req.Confidence.BlastRadius
		ctx.Reversible = req.Confidence.Reversible
	}

	approval, err := h.service.CreateApprovalRequest(
		r.Context(),
		approvalType,
		req.TaskID,
		req.AgentID,
		req.Domain,
		req.Title,
		req.Description,
		req.DiffSummary,
		ctx,
	)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, fmt.Sprintf("failed to create approval: %v", err))
		return
	}

	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(approval)
}

func (h *ApprovalHandler) listApprovals(w http.ResponseWriter, r *http.Request) {
	status := r.URL.Query().Get("status")
	tier := r.URL.Query().Get("tier")
	agentID := r.URL.Query().Get("agent_id")
	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		fmt.Sscanf(l, "%d", &limit)
		if limit > 100 {
			limit = 100
		}
	}

	var approvals []Approval
	var err error

	ctx := r.Context()

	switch {
	case status != "":
		approvals, err = h.service.store.ListByStatus(ctx, ApprovalStatus(status), limit)
	case tier != "":
		approvals, err = h.service.store.ListByTier(ctx, ApprovalTier(tier), limit)
	case agentID != "":
		approvals, err = h.service.store.ListByAgent(ctx, agentID, limit)
	default:
		// List all recent
		approvals, err = h.service.store.ListByStatus(ctx, StatusApproved, limit)
	}

	if err != nil {
		http.Error(w, fmt.Sprintf("failed to list approvals: %v", err), http.StatusInternalServerError)
		return
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"approvals": approvals,
		"count":     len(approvals),
	})
}

func (h *ApprovalHandler) handlePending(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Content-Type", "application/json")

	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		fmt.Sscanf(l, "%d", &limit)
		if limit > 100 {
			limit = 100
		}
	}

	approvals, err := h.service.GetPending(r.Context(), limit)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to get pending approvals: %v", err), http.StatusInternalServerError)
		return
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"approvals": approvals,
		"count":     len(approvals),
	})
}

func (h *ApprovalHandler) handleApprovalAction(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	// Parse path: /api/approvals/{id}/approve or /api/approvals/{id}/reject
	// Also supports /api/approvals/{id}/decide with form field "decision"
	path := strings.TrimPrefix(r.URL.Path, "/api/approvals/")
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

	var user string
	var source string

	// Support both JSON and form-encoded bodies (HTMX sends application/x-www-form-urlencoded)
	ct := r.Header.Get("Content-Type")
	if strings.HasPrefix(ct, "application/x-www-form-urlencoded") || strings.HasPrefix(ct, "multipart/form-data") {
		if err := r.ParseForm(); err == nil {
			user = r.FormValue("user")
			source = r.FormValue("source")
			// For "decide" action: read decision from form field
			if action == "decide" {
				decision := r.FormValue("decision")
				switch decision {
				case "approve":
					action = "approve"
				case "reject":
					action = "reject"
				case "defer":
					action = "defer"
				case "kill":
					action = "kill"
				case "extend":
					action = "extend"
				default:
					http.Error(w, "decision must be approve, reject, defer, kill, or extend", http.StatusBadRequest)
					return
				}
			}
		}
	} else {
		var req struct {
			User     string `json:"user"`
			Decision string `json:"decision,omitempty"`
			Source   string `json:"source,omitempty"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err == nil {
			user = req.User
			source = req.Source
			// For "decide" action: read decision from JSON field
			if action == "decide" {
				switch req.Decision {
				case "approve":
					action = "approve"
				case "reject":
					action = "reject"
				case "defer":
					action = "defer"
				case "kill":
					action = "kill"
				case "extend":
					action = "extend"
				default:
					http.Error(w, "decision must be approve, reject, defer, kill, or extend", http.StatusBadRequest)
					return
				}
			}
		}
	}

	if source == "" {
		source = "dashboard"
	}

	if user == "" {
		user = "dashboard"
	}

	var err error
	switch action {
	case "approve":
		err = h.service.Approve(r.Context(), id, user)
	case "reject":
		err = h.service.Reject(r.Context(), id, user)
	case "defer":
		err = h.service.Reject(r.Context(), id, user) // reuse reject flow for now
	case "kill":
		err = h.service.Reject(r.Context(), id, user)
	case "extend":
		err = h.service.Approve(r.Context(), id, user)
	default:
		http.Error(w, fmt.Sprintf("unknown action: %s", action), http.StatusBadRequest)
		return
	}

	if err != nil {
		if strings.Contains(err.Error(), "not found") {
			http.Error(w, err.Error(), http.StatusNotFound)
		} else {
			http.Error(w, err.Error(), http.StatusBadRequest)
		}
		return
	}

	// When the action results in an approval, complete the underlying task.
	// This wires ProcessApprovedTask into the approval decision path so that
	// tasks waiting in pending_approval state are automatically completed once
	// a human approves.  Best-effort: failures are logged but never surfaced
	// to the caller (approval itself already succeeded at this point).
	if action == "approve" || action == "extend" {
		if taskQueue != nil && globalApprovalService != nil {
			go func() {
				tai := NewTaskApprovalIntegration(taskQueue, globalApprovalService)
				if procErr := tai.ProcessApprovedTask(context.Background(), id); procErr != nil {
					log.Printf("[handleApprovalAction] ProcessApprovedTask(%s): %v", id, procErr)
				}
			}()
		}
	} else if action == "reject" || action == "defer" || action == "kill" {
		if taskQueue != nil && globalApprovalService != nil {
			go func() {
				tai := NewTaskApprovalIntegration(taskQueue, globalApprovalService)
				if procErr := tai.ProcessRejectedTask(context.Background(), id); procErr != nil {
					log.Printf("[handleApprovalAction] ProcessRejectedTask(%s): %v", id, procErr)
				}
			}()
		}
	}

	// Epic 2 Phase 4: when a merge-type approval is approved, trigger GitHub merge.
	// Best-effort — never blocks the HTTP response.
	if action == "approve" {
		go triggerGithubMergeIfApplicable(r.Context(), id)
	}

	json.NewEncoder(w).Encode(map[string]string{
		"status":      action + "ed",
		"approval_id": id,
		"user":        user,
		"source":      source,
	})
}

// RunExpirationCheck periodically expires old approvals
func (h *ApprovalHandler) RunExpirationCheck(ctx context.Context, interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			count, err := h.service.ExpireOld(ctx)
			if err != nil {
				// Log error but continue
				continue
			}
			if count > 0 {
				// Could log or emit event here
			}
		}
	}
}
