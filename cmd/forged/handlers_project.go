//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

// Project represents a workspace or repository being managed
type Project struct {
	ID          string    `json:"id"`
	Key         string    `json:"key"`
	Name        string    `json:"name"`
	Domain      string    `json:"domain"`
	Description string    `json:"description,omitempty"`
	Path        string    `json:"path,omitempty"`
	Type        string    `json:"type,omitempty"`
	Status      string    `json:"status,omitempty"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}

// projectsHandler handles GET and POST requests for /projects
func projectsHandler(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		listProjectsHandler(w, r)
	case http.MethodPost:
		createProjectHandler(w, r)
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

// listProjectsHandler returns a list of all projects, optionally filtered by domain
func listProjectsHandler(w http.ResponseWriter, r *http.Request) {
	domain := r.URL.Query().Get("domain")

	var rows *sql.Rows
	var err error

	if domain != "" {
		rows, err = getDBConn().Query("SELECT id, key, name, domain, description, path, type, status, created_at, updated_at FROM projects WHERE domain = ?", domain)
	} else {
		rows, err = getDBConn().Query("SELECT id, key, name, domain, description, path, type, status, created_at, updated_at FROM projects")
	}

	if err != nil {
		http.Error(w, fmt.Sprintf("failed to query projects: %v", err), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	projects := []Project{}
	for rows.Next() {
		var p Project
		var desc, ppath, ptype, status, createdAt, updatedAt sql.NullString
		err := rows.Scan(&p.ID, &p.Key, &p.Name, &p.Domain, &desc, &ppath, &ptype, &status, &createdAt, &updatedAt)
		if err != nil {
			http.Error(w, fmt.Sprintf("failed to scan project: %v", err), http.StatusInternalServerError)
			return
		}
		if desc.Valid {
			p.Description = desc.String
		}
		if ppath.Valid {
			p.Path = ppath.String
		}
		if ptype.Valid {
			p.Type = ptype.String
		}
		if status.Valid {
			p.Status = status.String
		}

		if createdAt.Valid {
			t, _ := time.Parse(time.RFC3339, createdAt.String)
			if t.IsZero() {
				t, _ = time.Parse("2006-01-02 15:04:05", createdAt.String)
			}
			p.CreatedAt = t
		}
		if updatedAt.Valid {
			t, _ := time.Parse(time.RFC3339, updatedAt.String)
			if t.IsZero() {
				t, _ = time.Parse("2006-01-02 15:04:05", updatedAt.String)
			}
			p.UpdatedAt = t
		}
		projects = append(projects, p)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"projects": projects,
		"count":    len(projects),
	})
}

// projectByIDHandler returns a specific project by ID or key
func projectByIDHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	path := r.URL.Path
	// Handle both /projects/ID and /api/projects/ID
	id := strings.TrimPrefix(path, "/projects/")
	if strings.HasPrefix(path, "/api/projects/") {
		id = strings.TrimPrefix(path, "/api/projects/")
	}

	if id == "" {
		http.Error(w, "missing project id", http.StatusBadRequest)
		return
	}

	var p Project
	var desc, ppath, ptype, status, createdAt, updatedAt sql.NullString
	err := getDBConn().QueryRow("SELECT id, key, name, domain, description, path, type, status, created_at, updated_at FROM projects WHERE id = ? OR key = ?", id, id).Scan(
		&p.ID, &p.Key, &p.Name, &p.Domain, &desc, &ppath, &ptype, &status, &createdAt, &updatedAt)

	if err == sql.ErrNoRows {
		http.Error(w, "project not found", http.StatusNotFound)
		return
	} else if err != nil {
		http.Error(w, fmt.Sprintf("failed to query project: %v", err), http.StatusInternalServerError)
		return
	}

	if desc.Valid {
		p.Description = desc.String
	}
	if ppath.Valid {
		p.Path = ppath.String
	}
	if ptype.Valid {
		p.Type = ptype.String
	}
	if status.Valid {
		p.Status = status.String
	}

	if createdAt.Valid {
		t, _ := time.Parse(time.RFC3339, createdAt.String)
		if t.IsZero() {
			t, _ = time.Parse("2006-01-02 15:04:05", createdAt.String)
		}
		p.CreatedAt = t
	}
	if updatedAt.Valid {
		t, _ := time.Parse(time.RFC3339, updatedAt.String)
		if t.IsZero() {
			t, _ = time.Parse("2006-01-02 15:04:05", updatedAt.String)
		}
		p.UpdatedAt = t
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(p)
}

// createProjectHandler creates a new project
func createProjectHandler(w http.ResponseWriter, r *http.Request) {
	var p Project
	if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	p.Key = sanitizeID(p.Key)
	if p.Key == "" || !isValidID(p.Key) {
		http.Error(w, "invalid or missing project key", http.StatusBadRequest)
		return
	}
	if p.Name == "" {
		p.Name = p.Key
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
		INSERT INTO projects (id, key, name, domain, description, path, type, status, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		p.ID, p.Key, p.Name, p.Domain, p.Description, p.Path, p.Type, p.Status, now, now)

	if err != nil {
		http.Error(w, fmt.Sprintf("failed to create project: %v", err), http.StatusInternalServerError)
		return
	}

	p.CreatedAt, _ = time.Parse("2006-01-02 15:04:05", now)
	p.UpdatedAt, _ = time.Parse("2006-01-02 15:04:05", now)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(p)
}
