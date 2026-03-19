package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestDispatchCommand tests dispatch command
func TestDispatchCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("dispatch")
	if err != nil {
		t.Logf("dispatch: %v", err)
	}
}

// TestDispatchSendArgs tests that send requires arguments
func TestDispatchSendArgs(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("dispatch", "send")
	if err == nil {
		t.Error("expected error for missing agent")
	}
}

// TestDispatchSend tests dispatch send command
func TestDispatchSend(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("dispatch", "send", "forge:minimax", "Test message")
	if err != nil {
		t.Logf("dispatch send: %v", err)
	}
}

// TestDispatchShowArgs tests that show requires arguments
func TestDispatchShowArgs(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("dispatch", "show")
	if err == nil {
		t.Error("expected error for missing task ID")
	}
}

// TestDispatchList tests dispatch list command
func TestDispatchList(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("dispatch", "list")
	if err != nil {
		t.Logf("dispatch list: %v", err)
	}
}

// TestDispatchFormats tests dispatch with different formats
func TestDispatchFormats(t *testing.T) {
	runner := NewTestRunner(t)

	formats := []string{"table", "json", "csv", "quiet"}
	for _, format := range formats {
		_, err := runner.Execute("dispatch", "list", "--format", format)
		if err != nil {
			t.Errorf("dispatch list --format %s failed: %v", format, err)
		}
	}
}

// ─── parseDispatchFilename ───────────────────────────────────────────────────

func TestParseDispatchFilename_WithDate(t *testing.T) {
	agent, taskID := parseDispatchFilename("kimi-COVERAGE-2026-03-19")
	if agent != "kimi" {
		t.Errorf("agent: want kimi, got %s", agent)
	}
	if taskID != "COVERAGE" {
		t.Errorf("taskID: want COVERAGE, got %s", taskID)
	}
}

func TestParseDispatchFilename_WithoutDate(t *testing.T) {
	agent, taskID := parseDispatchFilename("pi-ANALYSIS")
	if agent != "pi" {
		t.Errorf("agent: want pi, got %s", agent)
	}
	if taskID != "ANALYSIS" {
		t.Errorf("taskID: want ANALYSIS, got %s", taskID)
	}
}

func TestParseDispatchFilename_ArchivePattern(t *testing.T) {
	// claude-007-2026-02-28 → agent=claude, taskID=007
	agent, taskID := parseDispatchFilename("claude-007-2026-02-28")
	if agent != "claude" {
		t.Errorf("agent: want claude, got %s", agent)
	}
	if taskID != "007" {
		t.Errorf("taskID: want 007, got %s", taskID)
	}
}

func TestParseDispatchFilename_NoHyphen(t *testing.T) {
	// Single token → agent=token, taskID=""
	agent, taskID := parseDispatchFilename("orphan")
	if agent != "orphan" {
		t.Errorf("agent: want orphan, got %s", agent)
	}
	if taskID != "" {
		t.Errorf("taskID: want empty, got %s", taskID)
	}
}

func TestParseDispatchFilename_MultiPartTaskID(t *testing.T) {
	// minimax-COVERAGE-WAVE-2026-03-19 → agent=minimax, taskID=COVERAGE-WAVE
	agent, taskID := parseDispatchFilename("minimax-COVERAGE-WAVE-2026-03-19")
	if agent != "minimax" {
		t.Errorf("agent: want minimax, got %s", agent)
	}
	if taskID != "COVERAGE-WAVE" {
		t.Errorf("taskID: want COVERAGE-WAVE, got %s", taskID)
	}
}

// ─── readPendingDispatches ───────────────────────────────────────────────────

func TestReadPendingDispatches_Empty(t *testing.T) {
	tmp := t.TempDir()
	rows, err := readPendingDispatches(tmp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(rows) != 0 {
		t.Errorf("expected 0 rows, got %d", len(rows))
	}
}

func TestReadPendingDispatches_WithFiles(t *testing.T) {
	tmp := t.TempDir()
	dispDir := filepath.Join(tmp, ".forge", "dispatches")
	resultsDir := filepath.Join(tmp, ".forge", "heartbeat", "results")
	if err := os.MkdirAll(dispDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(resultsDir, 0o755); err != nil {
		t.Fatal(err)
	}

	// Create two dispatch files.
	dispFiles := []string{"kimi-TASK1-2026-03-19.md", "pi-TASK2-2026-03-19.md"}
	for _, f := range dispFiles {
		if err := os.WriteFile(filepath.Join(dispDir, f), []byte("dispatch"), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// Create result for kimi-TASK1 only.
	if err := os.WriteFile(filepath.Join(resultsDir, "kimi-TASK1.md"), []byte("result"), 0o644); err != nil {
		t.Fatal(err)
	}

	rows, err := readPendingDispatches(tmp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(rows) != 2 {
		t.Fatalf("expected 2 rows, got %d", len(rows))
	}

	// Find rows by agent.
	byAgent := map[string]pendingDispatchRow{}
	for _, r := range rows {
		byAgent[r.Agent] = r
	}

	if !byAgent["kimi"].HasResult {
		t.Error("kimi TASK1 should have a result")
	}
	if byAgent["pi"].HasResult {
		t.Error("pi TASK2 should NOT have a result")
	}
}

func TestReadPendingDispatches_SkipsArchiveDir(t *testing.T) {
	tmp := t.TempDir()
	dispDir := filepath.Join(tmp, ".forge", "dispatches")
	archiveDir := filepath.Join(dispDir, "archive")
	if err := os.MkdirAll(archiveDir, 0o755); err != nil {
		t.Fatal(err)
	}

	// Dispatch file outside archive → should appear.
	if err := os.WriteFile(filepath.Join(dispDir, "gemini-RESEARCH-2026-03-19.md"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	// File inside archive → should be skipped (it's a dir entry, not a .md in root).
	// Dirs named "archive" are skipped by the IsDir check.

	rows, err := readPendingDispatches(tmp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(rows) != 1 {
		t.Errorf("expected 1 row, got %d", len(rows))
	}
	if rows[0].Agent != "gemini" {
		t.Errorf("expected gemini, got %s", rows[0].Agent)
	}
}

// ─── formatDuration ──────────────────────────────────────────────────────────

func TestFormatDuration(t *testing.T) {
	cases := []struct {
		d    time.Duration
		want string
	}{
		{30 * time.Second, "just now"},
		{5 * time.Minute, "5m"},
		{3 * time.Hour, "3h"},
		{2 * 24 * time.Hour, "2d"},
	}
	for _, c := range cases {
		got := formatDuration(c.d)
		if got != c.want {
			t.Errorf("formatDuration(%v): want %q, got %q", c.d, c.want, got)
		}
	}
}

// ─── checkResultFile ─────────────────────────────────────────────────────────

func TestCheckResultFile_TooSmall(t *testing.T) {
	tmp := t.TempDir()
	f := filepath.Join(tmp, "tiny.md")
	if err := os.WriteFile(f, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	reason := checkResultFile(f, 100)
	if reason == "" {
		t.Error("expected a reason for too-small file")
	}
}

func TestCheckResultFile_MissingStatus(t *testing.T) {
	tmp := t.TempDir()
	f := filepath.Join(tmp, "nostatus.md")
	content := "# Some result\n\nHere is some content without any status indicator. " +
		"It is long enough to pass the size check but has no status markers at all."
	if err := os.WriteFile(f, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	reason := checkResultFile(f, 10)
	if reason == "" {
		t.Error("expected a reason for file with no status indicator")
	}
}

func TestCheckResultFile_PassesWithCompleteStatus(t *testing.T) {
	tmp := t.TempDir()
	f := filepath.Join(tmp, "good.md")
	content := "# Task Result\n\n## Status: COMPLETE\n\nAll tasks done.\n"
	if err := os.WriteFile(f, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	reason := checkResultFile(f, 10)
	if reason != "" {
		t.Errorf("expected pass, got reason: %s", reason)
	}
}

func TestCheckResultFile_PassesWithCheckmark(t *testing.T) {
	tmp := t.TempDir()
	f := filepath.Join(tmp, "emoji.md")
	content := "# Result\n\n✅ All checks passed.\n"
	if err := os.WriteFile(f, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	reason := checkResultFile(f, 10)
	if reason != "" {
		t.Errorf("expected pass, got reason: %s", reason)
	}
}

func TestCheckResultFile_PassesDigestFormat(t *testing.T) {
	tmp := t.TempDir()
	f := filepath.Join(tmp, "daily-digest.md")
	content := "# FORGE Daily Digest\n\nGenerated: 2026-03-19\n\n## Tasks\n\n24h summary.\n"
	if err := os.WriteFile(f, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	reason := checkResultFile(f, 10)
	if reason != "" {
		t.Errorf("expected pass for digest format, got: %s", reason)
	}
}

func TestCheckResultFile_Unreadable(t *testing.T) {
	reason := checkResultFile("/nonexistent/path/file.md", 10)
	if reason == "" {
		t.Error("expected reason for unreadable file")
	}
}

// ─── extractFirstLine ─────────────────────────────────────────────────────────

func TestExtractFirstLine_SkipsHeadings(t *testing.T) {
	text := "# Heading\n\n## Sub\n\nActual content here."
	got := extractFirstLine(text)
	if got != "Actual content here." {
		t.Errorf("want %q, got %q", "Actual content here.", got)
	}
}

func TestExtractFirstLine_SkipsSeparators(t *testing.T) {
	text := "---\n\nObjective: Research auth patterns."
	got := extractFirstLine(text)
	if got != "Objective: Research auth patterns." {
		t.Errorf("want %q, got %q", "Objective: Research auth patterns.", got)
	}
}

func TestExtractFirstLine_TruncatesLongLines(t *testing.T) {
	longLine := strings.Repeat("x", 200)
	got := extractFirstLine(longLine)
	if len(got) > 120 {
		t.Errorf("expected truncation, got len=%d", len(got))
	}
	if !strings.HasSuffix(got, "...") {
		t.Error("expected truncated line to end with '...'")
	}
}

func TestExtractFirstLine_Empty(t *testing.T) {
	got := extractFirstLine("")
	if got != "" {
		t.Errorf("expected empty, got %q", got)
	}
}

func TestExtractFirstLine_AllHeadings(t *testing.T) {
	got := extractFirstLine("# H1\n## H2\n### H3")
	if got != "" {
		t.Errorf("expected empty for all-headings text, got %q", got)
	}
}
