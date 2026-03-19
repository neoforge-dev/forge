//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"os"
	"path/filepath"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 152: context_sync.go pure functions + ContextSync filesystem helpers
// Targets:
//   extractSummary          (context_sync.go:507) — non-blank line, heading skip, empty
//   parseMarkdownDecisions  (context_sync.go:518) — bullet list → Decision slice
//   parseMarkdownFailures   (context_sync.go:536) — bullet list → Failure slice
//   sanitizeJSONKey         (context_sync.go:554) — extension removal, char sanitize
//   ContextSync.extractDomainProject (context_sync.go:478) — domain only, domain+project
//   ContextSync.getDomainDirectories (context_sync.go:447) — reads real temp dir
//   ContextSync.getProjectDirectories (context_sync.go:462) — reads project subdirs
// ---------------------------------------------------------------------------

// newMinimalContextSync builds a ContextSync with a real temp contextDir.
// The watcher is created internally by NewContextSync — close it on cleanup.
func newMinimalContextSync(t *testing.T, contextDir string) *ContextSync {
	t.Helper()
	db, _ := setupClaimTestDB(t)
	cm := testContextManager(t, db, contextDir)
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	t.Cleanup(func() { cs.watcher.Close() })
	return cs
}

// ---------------------------------------------------------------------------
// extractSummary
// ---------------------------------------------------------------------------

func TestWave152_ExtractSummary_NonBlankLine(t *testing.T) {
	content := "# Heading\n\nFirst real line.\nSecond line."
	got := extractSummary(content)
	if got != "First real line." {
		t.Errorf("unexpected summary: %q", got)
	}
}

func TestWave152_ExtractSummary_AllHeadings(t *testing.T) {
	content := "# Heading\n## Sub"
	got := extractSummary(content)
	if got != "" {
		t.Errorf("expected empty for all-headings, got %q", got)
	}
}

func TestWave152_ExtractSummary_Empty(t *testing.T) {
	if got := extractSummary(""); got != "" {
		t.Errorf("expected empty, got %q", got)
	}
}

// ---------------------------------------------------------------------------
// parseMarkdownDecisions
// ---------------------------------------------------------------------------

func TestWave152_ParseMarkdownDecisions_BulletList(t *testing.T) {
	content := "# Decisions\n- Use SQLite\n* Prefer uv over pip\n  ignored line"
	decisions := parseMarkdownDecisions(content)
	if len(decisions) != 2 {
		t.Errorf("expected 2 decisions, got %d: %v", len(decisions), decisions)
	}
	if decisions[0].Description != "Use SQLite" {
		t.Errorf("unexpected description: %q", decisions[0].Description)
	}
}

func TestWave152_ParseMarkdownDecisions_Empty(t *testing.T) {
	decisions := parseMarkdownDecisions("no bullets here")
	if len(decisions) != 0 {
		t.Errorf("expected 0 decisions, got %d", len(decisions))
	}
}

// ---------------------------------------------------------------------------
// parseMarkdownFailures
// ---------------------------------------------------------------------------

func TestWave152_ParseMarkdownFailures_BulletList(t *testing.T) {
	content := "- grep -E broken\n* tmux dispatch race"
	failures := parseMarkdownFailures(content)
	if len(failures) != 2 {
		t.Errorf("expected 2 failures, got %d: %v", len(failures), failures)
	}
	if failures[0].Description != "grep -E broken" {
		t.Errorf("unexpected description: %q", failures[0].Description)
	}
}

// ---------------------------------------------------------------------------
// sanitizeJSONKey
// ---------------------------------------------------------------------------

func TestWave152_SanitizeJSONKey_RemovesExtension(t *testing.T) {
	got := sanitizeJSONKey("lead-context.md")
	want := "lead_context"
	if got != want {
		t.Errorf("sanitizeJSONKey(%q) = %q, want %q", "lead-context.md", got, want)
	}
}

func TestWave152_SanitizeJSONKey_AlphanumericOnly(t *testing.T) {
	got := sanitizeJSONKey("my.key-with spaces")
	// After removing ".key-with spaces" extension? No — filepath.Ext returns ".key-with spaces" → complex
	// Just verify no panics and alphanumeric+underscore only
	for _, r := range got {
		if !((r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '_') {
			t.Errorf("unexpected char %q in sanitized key %q", r, got)
		}
	}
}

// ---------------------------------------------------------------------------
// ContextSync.extractDomainProject
// ---------------------------------------------------------------------------

func TestWave152_ExtractDomainProject_DomainOnly(t *testing.T) {
	contextDir := t.TempDir()
	cs := newMinimalContextSync(t, contextDir)

	filePath := filepath.Join(contextDir, "forge", "lead-context.md")
	domain, project, err := cs.extractDomainProject(filePath)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if domain != "forge" {
		t.Errorf("expected domain 'forge', got %q", domain)
	}
	if project != "" {
		t.Errorf("expected empty project, got %q", project)
	}
}

func TestWave152_ExtractDomainProject_DomainAndProject(t *testing.T) {
	contextDir := t.TempDir()
	cs := newMinimalContextSync(t, contextDir)

	filePath := filepath.Join(contextDir, "thebrightharbor", "study-flow", "decisions.md")
	domain, project, err := cs.extractDomainProject(filePath)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if domain != "thebrightharbor" {
		t.Errorf("expected domain 'thebrightharbor', got %q", domain)
	}
	if project != "study-flow" {
		t.Errorf("expected project 'study-flow', got %q", project)
	}
}

// ---------------------------------------------------------------------------
// ContextSync.getDomainDirectories + getProjectDirectories
// ---------------------------------------------------------------------------

func TestWave152_GetDomainDirectories(t *testing.T) {
	contextDir := t.TempDir()
	// Create domain dirs + envelopes (should be excluded)
	for _, d := range []string{"forge", "thebrightharbor", "envelopes"} {
		if err := os.MkdirAll(filepath.Join(contextDir, d), 0755); err != nil {
			t.Fatalf("mkdir: %v", err)
		}
	}
	// Regular file (not a dir) — should be ignored
	if err := os.WriteFile(filepath.Join(contextDir, "some-file.md"), []byte("x"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}

	cs := newMinimalContextSync(t, contextDir)
	domains, err := cs.getDomainDirectories()
	if err != nil {
		t.Fatalf("getDomainDirectories: %v", err)
	}
	// Should have forge + thebrightharbor (not envelopes, not files)
	if len(domains) != 2 {
		t.Errorf("expected 2 domains, got %d: %v", len(domains), domains)
	}
}

func TestWave152_GetProjectDirectories(t *testing.T) {
	contextDir := t.TempDir()
	domainDir := filepath.Join(contextDir, "forge")
	for _, p := range []string{"billing", "auth"} {
		if err := os.MkdirAll(filepath.Join(domainDir, p), 0755); err != nil {
			t.Fatalf("mkdir: %v", err)
		}
	}
	// A file in the domain dir — should be ignored
	if err := os.WriteFile(filepath.Join(domainDir, "lead-context.md"), []byte("x"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}

	cs := newMinimalContextSync(t, contextDir)
	projects, err := cs.getProjectDirectories("forge")
	if err != nil {
		t.Fatalf("getProjectDirectories: %v", err)
	}
	if len(projects) != 2 {
		t.Errorf("expected 2 projects, got %d: %v", len(projects), projects)
	}
}
