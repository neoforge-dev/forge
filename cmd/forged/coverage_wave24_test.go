//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"strings"
	"testing"
)

// Wave 24: patrol.go helper functions (previously 0%)

func TestBuildLeadContextFromEnvelope_W24(t *testing.T) {
	result := buildLeadContextFromEnvelope("infra", "Test summary", nil, nil)
	if !strings.Contains(result, "INFRA") {
		t.Errorf("expected domain in output, got: %q", result)
	}
	if !strings.Contains(result, "Test summary") {
		t.Errorf("expected summary in output, got: %q", result)
	}
}

func TestBuildLeadContextFromEnvelope_WithDecisions_W24(t *testing.T) {
	decisions := []Decision{
		{ID: "decis-001-abc", Description: "Use SQLite WAL mode"},
	}
	failures := []Failure{
		{ID: "fail-001-xyz", Lesson: "Don't use global state"},
	}
	result := buildLeadContextFromEnvelope("forge", "Summary", decisions, failures)
	if !strings.Contains(result, "Recent Decisions") {
		t.Errorf("expected decisions section: %q", result)
	}
	if !strings.Contains(result, "Lessons Learned") {
		t.Errorf("expected failures section: %q", result)
	}
}

func TestBuildLeadContextFromEnvelope_EmptySummary_W24(t *testing.T) {
	result := buildLeadContextFromEnvelope("domain", "", nil, nil)
	if result == "" {
		t.Error("expected non-empty output even with empty summary")
	}
}

func TestFindTaskIDInFilename_W24(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// No running tasks — should return not found
	_, found := findTaskIDInFilename(ctx, db, "some-result-file.md")
	if found {
		t.Error("expected not found with no running tasks")
	}
}

func TestFindTaskIDInFilename_EmptyFilename_W24(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_, found := findTaskIDInFilename(context.Background(), db, "")
	if found {
		t.Error("expected not found for empty filename")
	}
}

func TestBlastRadiusFromResult_W24(t *testing.T) {
	// Empty — default 1
	if got := blastRadiusFromResult(""); got != 1 {
		t.Errorf("empty: expected 1, got %d", got)
	}
	// Invalid JSON — default 1
	if got := blastRadiusFromResult("not json"); got != 1 {
		t.Errorf("invalid json: expected 1, got %d", got)
	}
	// files_changed
	if got := blastRadiusFromResult(`{"files_changed":5}`); got != 5 {
		t.Errorf("files_changed: expected 5, got %d", got)
	}
	// blast_radius fallback
	if got := blastRadiusFromResult(`{"blast_radius":3}`); got != 3 {
		t.Errorf("blast_radius: expected 3, got %d", got)
	}
	// Neither set — default 1
	if got := blastRadiusFromResult(`{"other":"field"}`); got != 1 {
		t.Errorf("no radius field: expected 1, got %d", got)
	}
}

func TestExtractResultSummary_W24(t *testing.T) {
	content := "## Status\nAll tests pass\n"
	result := extractResultSummary(content)
	if result != "## Status" {
		t.Errorf("expected '## Status', got %q", result)
	}
}

func TestExtractResultSummary_Truncate_W24(t *testing.T) {
	// Content over 500 chars with no Status header
	long := strings.Repeat("x", 600)
	result := extractResultSummary(long)
	if len(result) > 500 {
		t.Errorf("expected truncation to 500, got len=%d", len(result))
	}
}

func TestExtractResultSummary_Empty_W24(t *testing.T) {
	result := extractResultSummary("")
	if result != "" {
		t.Errorf("expected empty for empty input, got %q", result)
	}
}

func TestCalculateConfidenceScore_W24(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Create a minimal task to score
	task := &Task{
		ID:    "TASK-TEST-CONF-001",
		Title: "test confidence scoring",
		State: StateCompleted,
	}
	score := calculateConfidenceScore(context.Background(), db, task)
	// Should return a valid float between 0 and 1
	if score < 0 || score > 1 {
		t.Errorf("score out of range [0,1]: %f", score)
	}
}
