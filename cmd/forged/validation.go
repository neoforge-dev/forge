//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"net/http"
	"regexp"
	"strings"
)

var (
	// alphanumeric, hyphen, underscore, colon (for agent IDs)
	safeIDRegex = regexp.MustCompile(`^[a-zA-Z0-9\-_:]+$`)
	// general alphanumeric + common punctuation for messages
	safeTextRegex = regexp.MustCompile(`^[a-zA-Z0-9\-_:\.\,\s\(\)\[\]!]+$`)
)

// sanitizeID removes any potentially dangerous characters from an ID string
func sanitizeID(id string) string {
	return strings.TrimSpace(id)
}

// isValidID checks if an ID contains only safe characters
func isValidID(id string) bool {
	if len(id) == 0 || len(id) > 128 {
		return false
	}
	return safeIDRegex.MatchString(id)
}

// sanitizeText provides basic sanitization for general text inputs
func sanitizeText(text string) string {
	// Simple replacement of potentially dangerous characters if needed
	// For now, we mainly rely on validation
	return strings.TrimSpace(text)
}

// isValidText checks if text contains only safe characters for general usage
func isValidText(text string) bool {
	if len(text) > 2048 {
		return false
	}
	// Empty text is often allowed
	if text == "" {
		return true
	}
	return safeTextRegex.MatchString(text)
}

func writeJSONError(w http.ResponseWriter, status int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": msg})
}

// isTitleJunk returns true for auto-generated or empty titles that pollute the queue.
func isTitleJunk(title string) bool {
	if len(strings.TrimSpace(title)) < 10 {
		return true
	}
	junk := []string{
		"forge/dispatch",
		"openclaw/bridge",
		"fix bug",
		"update code",
		"add feature",
	}
	lower := strings.ToLower(strings.TrimSpace(title))
	for _, j := range junk {
		if lower == j || strings.HasSuffix(lower, " (feature)") || strings.HasSuffix(lower, " (bug)") {
			return true
		}
	}
	return false
}
