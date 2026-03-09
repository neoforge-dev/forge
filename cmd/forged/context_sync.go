//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"crypto/md5"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/fsnotify/fsnotify"
)

// ContextSync manages bidirectional synchronization between filesystem and SQLite
type ContextSync struct {
	cm         *ContextManager
	db         *sql.DB
	watcher    *fsnotify.Watcher
	contextDir string

	// Track file hashes to avoid redundant updates
	fileHashes map[string]string
	hashMu     sync.RWMutex

	// Track last DB update time to avoid echo updates
	lastDBUpdate map[string]time.Time
	dbMu         sync.RWMutex

	stopCh chan struct{}
}

// NewContextSync creates a new bidirectional sync manager
func NewContextSync(cm *ContextManager, db *sql.DB, contextDir string) (*ContextSync, error) {
	watcher, err := fsnotify.NewWatcher()
	if err != nil {
		return nil, fmt.Errorf("create fsnotify watcher: %w", err)
	}

	return &ContextSync{
		cm:           cm,
		db:           db,
		watcher:      watcher,
		contextDir:   contextDir,
		fileHashes:   make(map[string]string),
		lastDBUpdate: make(map[string]time.Time),
		stopCh:       make(chan struct{}),
	}, nil
}

// Start begins watching filesystem and syncing changes
func (cs *ContextSync) Start() error {
	// Watch all domain directories
	domains, err := cs.getDomainDirectories()
	if err != nil {
		return fmt.Errorf("get domain directories: %w", err)
	}

	for _, domain := range domains {
		domainDir := filepath.Join(cs.contextDir, domain)
		if err := cs.watcher.Add(domainDir); err != nil {
			log.Printf("Warning: failed to watch domain dir %s: %v", domainDir, err)
			continue
		}
		log.Printf("Watching context directory: %s", domainDir)

		// Watch project subdirectories
		projects, err := cs.getProjectDirectories(domain)
		if err != nil {
			continue
		}
		for _, project := range projects {
			projectDir := filepath.Join(domainDir, project)
			if err := cs.watcher.Add(projectDir); err != nil {
				log.Printf("Warning: failed to watch project dir %s: %v", projectDir, err)
			}
		}
	}

	// Start the sync loop
	go cs.syncLoop()

	// Start periodic full sync (every 30 seconds)
	go cs.periodicFullSync()

	return nil
}

// Stop stops the filesystem watcher
func (cs *ContextSync) Stop() {
	close(cs.stopCh)
	cs.watcher.Close()
}

func (cs *ContextSync) syncLoop() {
	debounceTimer := time.NewTimer(0)
	<-debounceTimer.C

	var pendingFiles sync.Map

	for {
		select {
		case event, ok := <-cs.watcher.Events:
			if !ok {
				return
			}

			// Only process write and create events
			if event.Op&(fsnotify.Write|fsnotify.Create) == 0 {
				continue
			}

			// Skip envelope files (we write those)
			if strings.Contains(event.Name, "/envelopes/") {
				continue
			}

			// Skip hidden files
			if strings.HasPrefix(filepath.Base(event.Name), ".") {
				continue
			}

			// Mark file as pending
			pendingFiles.Store(event.Name, time.Now())

			// Reset debounce timer
			debounceTimer.Reset(500 * time.Millisecond)

		case <-debounceTimer.C:
			// Process all pending files
			pendingFiles.Range(func(key, value interface{}) bool {
				filePath := key.(string)
				if err := cs.handleFileChange(filePath); err != nil {
					log.Printf("Error handling file change %s: %v", filePath, err)
				}
				pendingFiles.Delete(key)
				return true
			})

		case err, ok := <-cs.watcher.Errors:
			if !ok {
				return
			}
			log.Printf("Watcher error: %v", err)

		case <-cs.stopCh:
			return
		}
	}
}

func (cs *ContextSync) handleFileChange(filePath string) error {
	// Check if file still exists
	info, err := os.Stat(filePath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil // File was deleted, ignore
		}
		return err
	}

	if info.IsDir() {
		return nil // Skip directories
	}

	// Calculate file hash
	hash, err := cs.calculateFileHash(filePath)
	if err != nil {
		return fmt.Errorf("calculate hash: %w", err)
	}

	// Check if content actually changed
	cs.hashMu.RLock()
	lastHash, exists := cs.fileHashes[filePath]
	cs.hashMu.RUnlock()

	if exists && lastHash == hash {
		return nil // No change, skip
	}

	// Update hash
	cs.hashMu.Lock()
	cs.fileHashes[filePath] = hash
	cs.hashMu.Unlock()

	// Extract domain and project from path
	domain, project, err := cs.extractDomainProject(filePath)
	if err != nil {
		return fmt.Errorf("extract domain/project: %w", err)
	}

	// Read file content
	content, err := os.ReadFile(filePath)
	if err != nil {
		return fmt.Errorf("read file: %w", err)
	}

	// Determine file type and update appropriate table
	filename := filepath.Base(filePath)
	if err := cs.syncFileToDB(domain, project, filename, string(content)); err != nil {
		return fmt.Errorf("sync to db: %w", err)
	}

	log.Printf("Synced file to DB: %s", filePath)
	return nil
}

func (cs *ContextSync) syncFileToDB(domain, project, filename, content string) error {
	// Map filenames to sync types
	switch filename {
	case "lead-context.md":
		return cs.updateLeadContext(domain, content)
	case "decisions.json", "decisions.md":
		return cs.updateDecisions(domain, project, content)
	case "failures.json", "failures.md":
		return cs.updateFailures(domain, project, content)
	case "calibration.md":
		return cs.updateCalibration(domain, project, content)
	default:
		// Store as metadata
		return cs.updateMetadata(domain, project, filename, content)
	}
}

func (cs *ContextSync) updateLeadContext(domain, content string) error {
	// Update the context_envelopes table for all envelopes in this domain
	_, err := cs.db.Exec(`
		UPDATE context_envelopes 
		SET content = json_set(content, '$.metadata.lead_context', ?),
		    summary = CASE 
				WHEN summary = '' OR summary IS NULL THEN ?
				ELSE summary 
			END
		WHERE domain = ?
	`, content, extractSummary(content), domain)
	return err
}

func (cs *ContextSync) updateDecisions(domain, project, content string) error {
	var decisions []Decision
	if err := json.Unmarshal([]byte(content), &decisions); err != nil {
		// Try parsing as markdown list
		decisions = parseMarkdownDecisions(content)
	}

	decisionsJSON, _ := json.Marshal(decisions)

	_, err := cs.db.Exec(`
		UPDATE context_envelopes 
		SET content = json_set(content, '$.decisions', json(?))
		WHERE domain = ? AND (project = ? OR ? = '')
	`, string(decisionsJSON), domain, project, project)
	return err
}

func (cs *ContextSync) updateFailures(domain, project, content string) error {
	var failures []Failure
	if err := json.Unmarshal([]byte(content), &failures); err != nil {
		failures = parseMarkdownFailures(content)
	}

	failuresJSON, _ := json.Marshal(failures)

	_, err := cs.db.Exec(`
		UPDATE context_envelopes 
		SET content = json_set(content, '$.failures', json(?))
		WHERE domain = ? AND (project = ? OR ? = '')
	`, string(failuresJSON), domain, project, project)
	return err
}

func (cs *ContextSync) updateCalibration(domain, project, content string) error {
	_, err := cs.db.Exec(`
		UPDATE context_envelopes 
		SET content = json_set(
			content, 
			'$.calibration.main', 
			?
		)
		WHERE domain = ? AND (project = ? OR ? = '')
	`, content, domain, project, project)
	return err
}

func (cs *ContextSync) updateMetadata(domain, project, key, content string) error {
	jsonKey := fmt.Sprintf("$.metadata.%s", sanitizeJSONKey(key))
	_, err := cs.db.Exec(`
		UPDATE context_envelopes 
		SET content = json_set(content, ?, ?)
		WHERE domain = ? AND (project = ? OR ? = '')
	`, jsonKey, content, domain, project, project)
	return err
}

// SyncEnvelopesToFilesystem writes all envelopes from SQLite to filesystem
func (cs *ContextSync) SyncEnvelopesToFilesystem() error {
	rows, err := cs.db.Query(`
		SELECT id, agent_id, domain, project, task_id, content 
		FROM context_envelopes 
		WHERE expires_at > datetime('now')
	`)
	if err != nil {
		return fmt.Errorf("query envelopes: %w", err)
	}
	defer rows.Close()

	for rows.Next() {
		var envelope ContextEnvelope
		var content string

		err := rows.Scan(&envelope.ID, &envelope.AgentID, &envelope.Domain,
			&envelope.Project, &envelope.TaskID, &content)
		if err != nil {
			log.Printf("Error scanning envelope: %v", err)
			continue
		}

		if err := json.Unmarshal([]byte(content), &envelope); err != nil {
			log.Printf("Error unmarshaling envelope %s: %v", envelope.ID, err)
			continue
		}

		if err := cs.cm.storeInFilesystem(&envelope); err != nil {
			log.Printf("Error storing envelope %s: %v", envelope.ID, err)
			continue
		}
	}

	return rows.Err()
}

// SyncDomainToFilesystem writes domain context files from SQLite
func (cs *ContextSync) SyncDomainToFilesystem(domain string) error {
	// Get latest envelope for domain
	var content string
	err := cs.db.QueryRow(`
		SELECT content FROM context_envelopes 
		WHERE domain = ? 
		ORDER BY created_at DESC LIMIT 1
	`, domain).Scan(&content)

	if err != nil {
		if err == sql.ErrNoRows {
			return nil // No data yet
		}
		return err
	}

	var envelope ContextEnvelope
	if err := json.Unmarshal([]byte(content), &envelope); err != nil {
		return err
	}

	// Write to filesystem
	domainDir := filepath.Join(cs.contextDir, domain)
	os.MkdirAll(domainDir, 0755)

	// Write lead-context.md
	if leadContext, ok := envelope.Metadata["lead_context"].(string); ok && leadContext != "" {
		path := filepath.Join(domainDir, "lead-context.md")
		if err := os.WriteFile(path, []byte(leadContext), 0644); err != nil {
			log.Printf("Error writing lead-context.md: %v", err)
		}
	}

	// Write decisions
	if len(envelope.Decisions) > 0 {
		data, _ := json.MarshalIndent(envelope.Decisions, "", "  ")
		path := filepath.Join(domainDir, "decisions.json")
		if err := os.WriteFile(path, data, 0644); err != nil {
			log.Printf("Error writing decisions.json: %v", err)
		}
	}

	// Write failures
	if len(envelope.Failures) > 0 {
		data, _ := json.MarshalIndent(envelope.Failures, "", "  ")
		path := filepath.Join(domainDir, "failures.json")
		if err := os.WriteFile(path, data, 0644); err != nil {
			log.Printf("Error writing failures.json: %v", err)
		}
	}

	return nil
}

// periodicFullSync runs a full sync every 30 seconds
func (cs *ContextSync) periodicFullSync() {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			if err := cs.fullSync(); err != nil {
				log.Printf("Error in periodic full sync: %v", err)
			}
		case <-cs.stopCh:
			return
		}
	}
}

func (cs *ContextSync) fullSync() error {
	// Sync all domains
	domains, err := cs.getDomainDirectories()
	if err != nil {
		return err
	}

	for _, domain := range domains {
		// Sync filesystem changes to DB
		domainDir := filepath.Join(cs.contextDir, domain)
		files, err := os.ReadDir(domainDir)
		if err != nil {
			continue
		}

		for _, file := range files {
			if file.IsDir() {
				continue
			}
			filePath := filepath.Join(domainDir, file.Name())
			if err := cs.handleFileChange(filePath); err != nil {
				log.Printf("Error syncing %s: %v", filePath, err)
			}
		}

		// Sync DB to filesystem
		if err := cs.SyncDomainToFilesystem(domain); err != nil {
			log.Printf("Error syncing domain %s to filesystem: %v", domain, err)
		}
	}

	return nil
}

// Helper functions

func (cs *ContextSync) getDomainDirectories() ([]string, error) {
	entries, err := os.ReadDir(cs.contextDir)
	if err != nil {
		return nil, err
	}

	var domains []string
	for _, e := range entries {
		if e.IsDir() && e.Name() != "envelopes" {
			domains = append(domains, e.Name())
		}
	}
	return domains, nil
}

func (cs *ContextSync) getProjectDirectories(domain string) ([]string, error) {
	domainDir := filepath.Join(cs.contextDir, domain)
	entries, err := os.ReadDir(domainDir)
	if err != nil {
		return nil, err
	}

	var projects []string
	for _, e := range entries {
		if e.IsDir() {
			projects = append(projects, e.Name())
		}
	}
	return projects, nil
}

func (cs *ContextSync) extractDomainProject(filePath string) (string, string, error) {
	rel, err := filepath.Rel(cs.contextDir, filePath)
	if err != nil {
		return "", "", err
	}

	parts := strings.Split(rel, string(filepath.Separator))
	if len(parts) < 1 {
		return "", "", fmt.Errorf("invalid path structure")
	}

	domain := parts[0]
	project := ""
	if len(parts) > 2 {
		project = parts[1]
	}

	return domain, project, nil
}

func (cs *ContextSync) calculateFileHash(filePath string) (string, error) {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return "", err
	}
	hash := md5.Sum(data)
	return hex.EncodeToString(hash[:]), nil
}

func extractSummary(content string) string {
	lines := strings.Split(content, "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line != "" && !strings.HasPrefix(line, "#") {
			return line
		}
	}
	return ""
}

func parseMarkdownDecisions(content string) []Decision {
	var decisions []Decision
	lines := strings.Split(content, "\n")

	for _, line := range lines {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "- ") || strings.HasPrefix(line, "* ") {
			decisions = append(decisions, Decision{
				ID:          generateULID(),
				Description: strings.TrimPrefix(strings.TrimPrefix(line, "- "), "* "),
				Timestamp:   time.Now(),
			})
		}
	}

	return decisions
}

func parseMarkdownFailures(content string) []Failure {
	var failures []Failure
	lines := strings.Split(content, "\n")

	for _, line := range lines {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "- ") || strings.HasPrefix(line, "* ") {
			failures = append(failures, Failure{
				ID:          generateULID(),
				Description: strings.TrimPrefix(strings.TrimPrefix(line, "- "), "* "),
				Timestamp:   time.Now(),
			})
		}
	}

	return failures
}

func sanitizeJSONKey(key string) string {
	// Remove file extension
	key = strings.TrimSuffix(key, filepath.Ext(key))
	// Replace non-alphanumeric with underscore
	var result strings.Builder
	for _, r := range key {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') {
			result.WriteRune(r)
		} else {
			result.WriteRune('_')
		}
	}
	return result.String()
}
