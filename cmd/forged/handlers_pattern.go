//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// Pattern represents a reusable pattern definition
type Pattern struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	Domain      string    `json:"domain"`
	YAMLContent string    `json:"yaml_content"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}

// PatternRun represents an execution of a pattern
type PatternRun struct {
	RunID       string   `json:"run_id"`
	PatternID   string   `json:"pattern_id"`
	TaskID      string   `json:"task_id"`
	AgentID     string   `json:"agent_id"`
	Domain      string   `json:"domain"`
	StartedAt   string   `json:"started_at"`
	CompletedAt *string  `json:"completed_at,omitempty"`
	Success     *bool    `json:"success,omitempty"`
	Confidence  *float64 `json:"confidence,omitempty"`
	TokensUsed  *int     `json:"tokens_used,omitempty"`
	DurationS   *int     `json:"duration_s,omitempty"`
	ErrorType   *string  `json:"error_type,omitempty"`
	Metadata    *string  `json:"metadata,omitempty"`
}

// patternsHandler handles GET/POST for /api/patterns
func patternsHandler(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		listPatternsHandler(w, r)
	case http.MethodPost:
		createPatternHandler(w, r)
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

// listPatternsHandler returns a list of all patterns, optionally filtered by domain
func listPatternsHandler(w http.ResponseWriter, r *http.Request) {
	domain := r.URL.Query().Get("domain")

	var rows *sql.Rows
	var err error

	if domain != "" {
		rows, err = getDBConn().Query("SELECT id, name, domain, yaml_content, created_at, updated_at FROM patterns WHERE domain = ? ORDER BY updated_at DESC", domain)
	} else {
		rows, err = getDBConn().Query("SELECT id, name, domain, yaml_content, created_at, updated_at FROM patterns ORDER BY updated_at DESC")
	}

	if err != nil {
		http.Error(w, fmt.Sprintf("failed to query patterns: %v", err), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	patterns := []Pattern{}
	for rows.Next() {
		var p Pattern
		var createdAt, updatedAt string
		err := rows.Scan(&p.ID, &p.Name, &p.Domain, &p.YAMLContent, &createdAt, &updatedAt)
		if err != nil {
			http.Error(w, fmt.Sprintf("failed to scan pattern: %v", err), http.StatusInternalServerError)
			return
		}

		p.CreatedAt, _ = time.Parse("2006-01-02 15:04:05", createdAt)
		if p.CreatedAt.IsZero() {
			p.CreatedAt, _ = time.Parse(time.RFC3339, createdAt)
		}
		p.UpdatedAt, _ = time.Parse("2006-01-02 15:04:05", updatedAt)
		if p.UpdatedAt.IsZero() {
			p.UpdatedAt, _ = time.Parse(time.RFC3339, updatedAt)
		}
		patterns = append(patterns, p)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"patterns": patterns,
		"count":    len(patterns),
	})
}

// createPatternHandler creates a new pattern
func createPatternHandler(w http.ResponseWriter, r *http.Request) {
	var p Pattern
	if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	p.Name = strings.TrimSpace(p.Name)
	if p.Name == "" {
		http.Error(w, "name required", http.StatusBadRequest)
		return
	}

	p.Domain = sanitizeID(p.Domain)
	if p.Domain == "" {
		p.Domain = "default"
	}

	if p.ID == "" {
		p.ID = generateULID()
	}

	now := time.Now().UTC().Format("2006-01-02 15:04:05")

	_, err := getDBConn().Exec(`
		INSERT INTO patterns (id, name, domain, yaml_content, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?)`,
		p.ID, p.Name, p.Domain, p.YAMLContent, now, now)

	if err != nil {
		http.Error(w, fmt.Sprintf("failed to create pattern: %v", err), http.StatusInternalServerError)
		return
	}

	p.CreatedAt, _ = time.Parse("2006-01-02 15:04:05", now)
	p.UpdatedAt, _ = time.Parse("2006-01-02 15:04:05", now)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(p)
}

// patternByIDOrRunsHandler routes /api/patterns/:id and /api/patterns/:id/runs
func patternByIDOrRunsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/api/patterns/")
	parts := strings.Split(path, "/")
	if len(parts) < 1 || parts[0] == "" {
		http.Error(w, "pattern ID required", http.StatusBadRequest)
		return
	}

	patternID := parts[0]

	// Check if this is a /runs sub-route
	if len(parts) >= 2 && parts[1] == "runs" {
		listPatternRunsHandler(w, r, patternID)
		return
	}

	// Single pattern lookup
	getPatternByIDHandler(w, r, patternID)
}

// getPatternByIDHandler returns a single pattern by ID
func getPatternByIDHandler(w http.ResponseWriter, r *http.Request, patternID string) {
	var p Pattern
	var createdAt, updatedAt string

	err := getDBConn().QueryRow(
		"SELECT id, name, domain, yaml_content, created_at, updated_at FROM patterns WHERE id = ?",
		patternID,
	).Scan(&p.ID, &p.Name, &p.Domain, &p.YAMLContent, &createdAt, &updatedAt)

	if err == sql.ErrNoRows {
		http.Error(w, "pattern not found", http.StatusNotFound)
		return
	}
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to query pattern: %v", err), http.StatusInternalServerError)
		return
	}

	p.CreatedAt, _ = time.Parse("2006-01-02 15:04:05", createdAt)
	if p.CreatedAt.IsZero() {
		p.CreatedAt, _ = time.Parse(time.RFC3339, createdAt)
	}
	p.UpdatedAt, _ = time.Parse("2006-01-02 15:04:05", updatedAt)
	if p.UpdatedAt.IsZero() {
		p.UpdatedAt, _ = time.Parse(time.RFC3339, updatedAt)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(p)
}

// listPatternRunsHandler returns run history for a pattern (limit=50)
func listPatternRunsHandler(w http.ResponseWriter, r *http.Request, patternID string) {
	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 && parsed <= 100 {
			limit = parsed
		}
	}

	rows, err := getDBConn().Query(`
		SELECT run_id, pattern_id, task_id, agent_id, domain, started_at, completed_at, 
		       success, confidence, tokens_used, duration_s, error_type, metadata
		FROM pattern_runs
		WHERE pattern_id = ?
		ORDER BY started_at DESC
		LIMIT ?`, patternID, limit)

	if err != nil {
		http.Error(w, fmt.Sprintf("failed to query pattern runs: %v", err), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	runs := []PatternRun{}
	for rows.Next() {
		var run PatternRun
		var completedAt, success, confidence, tokensUsed, durationS, errorType, metadata sql.NullString
		err := rows.Scan(&run.RunID, &run.PatternID, &run.TaskID, &run.AgentID, &run.Domain,
			&run.StartedAt, &completedAt, &success, &confidence, &tokensUsed, &durationS, &errorType, &metadata)
		if err != nil {
			http.Error(w, fmt.Sprintf("failed to scan pattern run: %v", err), http.StatusInternalServerError)
			return
		}

		if completedAt.Valid {
			run.CompletedAt = &completedAt.String
		}
		if success.Valid {
			b := success.String == "1" || success.String == "true"
			run.Success = &b
		}
		if confidence.Valid {
			if f, err := strconv.ParseFloat(confidence.String, 64); err == nil {
				run.Confidence = &f
			}
		}
		if tokensUsed.Valid {
			if i, err := strconv.Atoi(tokensUsed.String); err == nil {
				run.TokensUsed = &i
			}
		}
		if durationS.Valid {
			if i, err := strconv.Atoi(durationS.String); err == nil {
				run.DurationS = &i
			}
		}
		if errorType.Valid {
			run.ErrorType = &errorType.String
		}
		if metadata.Valid {
			run.Metadata = &metadata.String
		}

		runs = append(runs, run)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"pattern_id": patternID,
		"runs":       runs,
		"count":      len(runs),
	})
}

// loadPatternsFromDocs reads all *.yaml files from docs/patterns/ under FORGE_ROOT
// and upserts them into the patterns table. Called at daemon startup (ADR-018).
// Patterns in docs/ are git-tracked; this makes them available via /api/patterns.
func loadPatternsFromDocs(db *sql.DB) {
	root := forgeRoot()
	patternsDir := filepath.Join(root, "docs", "patterns")

	if _, err := os.Stat(patternsDir); os.IsNotExist(err) {
		return // docs/patterns/ doesn't exist yet — skip silently
	}

	loaded, skipped := 0, 0
	err := filepath.WalkDir(patternsDir, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() || filepath.Ext(path) != ".yaml" {
			return nil
		}

		data, err := os.ReadFile(path)
		if err != nil {
			log.Printf("[patterns] read %s: %v", path, err)
			return nil
		}
		content := string(data)

		// Extract id and domain from YAML (simple line-scan — no heavy parser dep).
		id, domain, name := "", "", ""
		for _, line := range strings.Split(content, "\n") {
			if strings.HasPrefix(line, "id:") {
				id = strings.TrimSpace(strings.TrimPrefix(line, "id:"))
			} else if strings.HasPrefix(line, "domain:") {
				domain = strings.TrimSpace(strings.TrimPrefix(line, "domain:"))
			} else if strings.HasPrefix(line, "name:") {
				name = strings.TrimSpace(strings.TrimPrefix(line, "name:"))
			}
		}
		if id == "" {
			// Fall back to filename stem as id.
			id = strings.TrimSuffix(filepath.Base(path), ".yaml")
		}
		if name == "" {
			name = id
		}

		_, err = db.Exec(`
			INSERT INTO patterns (id, name, domain, yaml_content, created_at, updated_at)
			VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
			ON CONFLICT(id) DO UPDATE SET
				name         = excluded.name,
				domain       = excluded.domain,
				yaml_content = excluded.yaml_content,
				updated_at   = datetime('now')
		`, id, name, domain, content)
		if err != nil {
			log.Printf("[patterns] upsert %s: %v", id, err)
			skipped++
		} else {
			loaded++
		}
		return nil
	})
	if err != nil {
		log.Printf("[patterns] walkdir error: %v", err)
	}
	if loaded > 0 || skipped > 0 {
		log.Printf("[patterns] loaded %d patterns from docs/patterns/ (%d skipped)", loaded, skipped)
	}
}
