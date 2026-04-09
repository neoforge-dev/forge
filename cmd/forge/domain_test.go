// Package main provides tests for the domain noun
package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDomainList(t *testing.T) {
	runner := NewTestRunner(t)

	// Test basic list
	_, err := runner.Execute("domain", "list")
	if err != nil {
		t.Errorf("domain list failed: %v", err)
	}

	// Test with json format
	_, err = runner.Execute("domain", "list", "--format", "json")
	if err != nil {
		t.Errorf("domain list --format json failed: %v", err)
	}

	// Test with csv format
	_, err = runner.Execute("domain", "list", "--format", "csv")
	if err != nil {
		t.Errorf("domain list --format csv failed: %v", err)
	}

	// Test with quiet format
	_, err = runner.Execute("domain", "list", "--format", "quiet")
	if err != nil {
		t.Errorf("domain list --format quiet failed: %v", err)
	}
}

func TestDomainListActive(t *testing.T) {
	runner := NewTestRunner(t)

	// Test list with active filter
	_, err := runner.Execute("domain", "list", "--active")
	if err != nil {
		t.Errorf("domain list --active failed: %v", err)
	}
}

func TestDomainShow(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with existing domain
	_, err := runner.Execute("domain", "show", "codeswiftr-com")
	if err != nil {
		t.Errorf("domain show failed: %v", err)
	}
}

func TestDomainShowNotFound(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with non-existent domain
	_, err := runner.Execute("domain", "show", "nonexistent-domain-xyz")
	if err == nil {
		t.Errorf("Expected error for non-existent domain, got nil")
	}
}

func TestDomainShowJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with json format
	_, err := runner.Execute("domain", "show", "codeswiftr-com", "--format", "json")
	if err != nil {
		t.Errorf("domain show --format json failed: %v", err)
	}
}

func TestDomainShowQuietFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with quiet format
	_, err := runner.Execute("domain", "show", "codeswiftr-com", "--format", "quiet")
	if err != nil {
		t.Errorf("domain show --format quiet failed: %v", err)
	}
}

func TestDomainCreate(t *testing.T) {
	runner := NewTestRunner(t)

	// Test create domain
	_, err := runner.Execute("domain", "create", "--key", "test-domain-com", "--name", "Test Domain")
	if err != nil {
		t.Errorf("domain create failed: %v", err)
	}
}

func TestDomainCreateMissingKey(t *testing.T) {
	runner := NewTestRunner(t)

	// Test create without key - command allows this (uses default)
	_, err := runner.Execute("domain", "create", "--name", "Test")
	if err != nil {
		t.Logf("domain create without key: %v", err)
	}
}

func TestDomainCreateMissingName(t *testing.T) {
	runner := NewTestRunner(t)

	// Test create without name - command allows this (uses default)
	_, err := runner.Execute("domain", "create", "--key", "test-key")
	if err != nil {
		t.Logf("domain create without name: %v", err)
	}
}

func TestDomainCreateJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test create with json format
	_, err := runner.Execute("domain", "create", "--key", "test-json", "--name", "Test JSON", "--format", "json")
	if err != nil {
		t.Errorf("domain create --format json failed: %v", err)
	}
}

func TestDomainListMultipleFormats(t *testing.T) {
	runner := NewTestRunner(t)

	formats := []string{"table", "json", "csv", "quiet"}
	for _, format := range formats {
		_, err := runner.Execute("domain", "list", "--format", format)
		if err != nil {
			t.Errorf("domain list --format %s failed: %v", format, err)
		}
	}
}

func TestDomainShowMultiple(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show various domains from demo data
	domains := []string{"codeswiftr-com", "leanvibe-ai", "brandfocus-ai"}
	for _, domain := range domains {
		_, err := runner.Execute("domain", "show", domain)
		if err != nil {
			t.Errorf("domain show %s failed: %v", domain, err)
		}
	}
}

// TestDomainUpdateNoFlags tests that domain update without flags returns an error.
func TestDomainUpdateNoFlags(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("domain", "update", "codeswiftr-com")
	if err == nil {
		t.Error("domain update with no flags should fail with missing flag error")
	}
}

// TestDomainUpdateInvalidScore tests that an out-of-range score is rejected.
func TestDomainUpdateInvalidScore(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("domain", "update", "codeswiftr-com", "--score", "150")
	if err == nil {
		t.Error("domain update --score 150 should fail with validation error")
	}
}

// TestDomainUpdateInvalidStatus tests that an unrecognized status is rejected.
func TestDomainUpdateInvalidStatus(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("domain", "update", "codeswiftr-com", "--status", "unknown-status")
	if err == nil {
		t.Error("domain update --status unknown-status should fail with validation error")
	}
}

// TestDomainUpdateMissingKey tests that domain update requires a domain key arg.
func TestDomainUpdateMissingKey(t *testing.T) {
	runner := NewTestRunner(t)

	// Missing positional arg — cobra should reject with ExactArgs(1)
	_, err := runner.Execute("domain", "update")
	if err == nil {
		t.Error("domain update with no key should fail")
	}
}

// TestDomainUpdateDaemonUnreachable tests that domain update gracefully fails when daemon is down.
func TestDomainUpdateDaemonUnreachable(t *testing.T) {
	runner := NewTestRunner(t)
	t.Setenv("FORGE_API_URL", "http://127.0.0.1:19999") // nothing listening

	_, err := runner.Execute("domain", "update", "codeswiftr-com", "--score", "80")
	// Should fail because daemon is unreachable; the command should not panic
	if err == nil {
		t.Log("domain update unexpectedly succeeded (daemon may be running)")
	}
}

// ─── Validation tracker helpers ────────────────────────────────────────────

const sampleTracker = `# CodeSwiftr (Interview Simulator) — Validation Tracker

**Stage:** ASSUMPTIONS (ready for RECRUIT)
**Owner:** sati | **Score:** 82/100 (Tier A)
**Deadline:** 2026-04-20 (kill if no interviews by then)

## P0 Assumptions

| # | Assumption | Status | Evidence |
|---|-----------|--------|----------|
| IS-1 | Devs will pay for AI interview practice | UNTESTED | — |
| IS-2 | $19-29/mo is acceptable price | UNTESTED | — |
| IS-3 | Blog SEO drives qualified traffic | UNTESTED | 1253 posts |
| IS-4 | Users complete >1 practice session | CONFIRMED | 3 sessions avg |

## Decision
- [ ] GO / KILL — pending interviews
`

// makeTrackerDir creates a temporary docs/validation directory with a sample tracker file.
// It returns the temp dir path. Callers must set FORGE_ROOT after calling NewTestRunner,
// since NewTestRunner calls os.Setenv("FORGE_ROOT", ...) directly and would overwrite
// any earlier t.Setenv call.
func makeTrackerDir(t *testing.T, domain, content string) string {
	t.Helper()
	tmp := t.TempDir()
	valDir := filepath.Join(tmp, "docs", "validation")
	if err := os.MkdirAll(valDir, 0755); err != nil {
		t.Fatalf("failed to create validation dir: %v", err)
	}
	path := filepath.Join(valDir, domain+".md")
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		t.Fatalf("failed to write tracker file: %v", err)
	}
	return tmp
}

// ─── parseTrackerMeta ───────────────────────────────────────────────────────

func TestParseTrackerMeta_Stage(t *testing.T) {
	m := parseTrackerMeta(sampleTracker)
	want := "ASSUMPTIONS (ready for RECRUIT)"
	if m.Stage != want {
		t.Errorf("Stage: want %q, got %q", want, m.Stage)
	}
}

func TestParseTrackerMeta_Owner(t *testing.T) {
	m := parseTrackerMeta(sampleTracker)
	if m.Owner != "sati" {
		t.Errorf("Owner: want %q, got %q", "sati", m.Owner)
	}
}

func TestParseTrackerMeta_Score(t *testing.T) {
	m := parseTrackerMeta(sampleTracker)
	if !strings.Contains(m.Score, "82/100") {
		t.Errorf("Score: want to contain %q, got %q", "82/100", m.Score)
	}
}

func TestParseTrackerMeta_Deadline(t *testing.T) {
	m := parseTrackerMeta(sampleTracker)
	if !strings.Contains(m.Deadline, "2026-04-20") {
		t.Errorf("Deadline: want to contain %q, got %q", "2026-04-20", m.Deadline)
	}
}

func TestParseTrackerMeta_Empty(t *testing.T) {
	m := parseTrackerMeta("# No metadata here\nSome text.")
	if m.Stage != "" || m.Owner != "" || m.Score != "" || m.Deadline != "" {
		t.Errorf("expected all empty fields for content without metadata, got %+v", m)
	}
}

// ─── parseAssumptions ───────────────────────────────────────────────────────

func TestParseAssumptions_Count(t *testing.T) {
	assumptions := parseAssumptions(sampleTracker)
	if len(assumptions) != 4 {
		t.Errorf("want 4 assumptions, got %d", len(assumptions))
	}
}

func TestParseAssumptions_IDs(t *testing.T) {
	assumptions := parseAssumptions(sampleTracker)
	want := []string{"IS-1", "IS-2", "IS-3", "IS-4"}
	for i, a := range assumptions {
		if a.ID != want[i] {
			t.Errorf("assumptions[%d].ID: want %q, got %q", i, want[i], a.ID)
		}
	}
}

func TestParseAssumptions_Status(t *testing.T) {
	assumptions := parseAssumptions(sampleTracker)
	// IS-4 should be CONFIRMED
	if assumptions[3].Status != "CONFIRMED" {
		t.Errorf("IS-4 Status: want CONFIRMED, got %q", assumptions[3].Status)
	}
	// IS-1 should be UNTESTED
	if assumptions[0].Status != "UNTESTED" {
		t.Errorf("IS-1 Status: want UNTESTED, got %q", assumptions[0].Status)
	}
}

func TestParseAssumptions_Empty(t *testing.T) {
	assumptions := parseAssumptions("# No table here\nSome text.")
	if len(assumptions) != 0 {
		t.Errorf("expected 0 assumptions for content without table, got %d", len(assumptions))
	}
}

// ─── forge domain validate ──────────────────────────────────────────────────

func TestDomainValidate_Success(t *testing.T) {
	runner := NewTestRunner(t)
	tmp := makeTrackerDir(t, "test-domain", sampleTracker)
	t.Setenv("FORGE_ROOT", tmp)

	output, err := runner.Execute("domain", "validate", "test-domain")
	if err != nil {
		t.Fatalf("domain validate failed: %v", err)
	}
	if !strings.Contains(output, "ASSUMPTIONS") {
		t.Errorf("expected output to contain stage ASSUMPTIONS, got: %s", output)
	}
	if !strings.Contains(output, "test-domain") {
		t.Errorf("expected output to contain domain name, got: %s", output)
	}
	if !strings.Contains(output, "untested") {
		t.Errorf("expected output to contain untested count, got: %s", output)
	}
}

func TestDomainValidate_NotFound(t *testing.T) {
	runner := NewTestRunner(t)
	tmp := t.TempDir()
	if err := os.MkdirAll(filepath.Join(tmp, "docs", "validation"), 0755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("FORGE_ROOT", tmp)

	_, err := runner.Execute("domain", "validate", "nonexistent-domain")
	if err == nil {
		t.Error("expected error for missing tracker file, got nil")
	}
}

func TestDomainValidate_MissingArg(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("domain", "validate")
	if err == nil {
		t.Error("expected error when domain arg is missing, got nil")
	}
}

// ─── forge domain assumptions ───────────────────────────────────────────────

func TestDomainAssumptions_Success(t *testing.T) {
	runner := NewTestRunner(t)
	tmp := makeTrackerDir(t, "test-domain", sampleTracker)
	t.Setenv("FORGE_ROOT", tmp)

	output, err := runner.Execute("domain", "assumptions", "test-domain")
	if err != nil {
		t.Fatalf("domain assumptions failed: %v", err)
	}
	if !strings.Contains(output, "IS-1") {
		t.Errorf("expected output to contain IS-1, got: %s", output)
	}
	if !strings.Contains(output, "IS-4") {
		t.Errorf("expected output to contain IS-4, got: %s", output)
	}
	if !strings.Contains(output, "UNTESTED") {
		t.Errorf("expected output to contain UNTESTED status, got: %s", output)
	}
	if !strings.Contains(output, "CONFIRMED") {
		t.Errorf("expected output to contain CONFIRMED status, got: %s", output)
	}
}

func TestDomainAssumptions_NotFound(t *testing.T) {
	runner := NewTestRunner(t)
	tmp := t.TempDir()
	if err := os.MkdirAll(filepath.Join(tmp, "docs", "validation"), 0755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("FORGE_ROOT", tmp)

	_, err := runner.Execute("domain", "assumptions", "no-such-domain")
	if err == nil {
		t.Error("expected error for missing tracker file, got nil")
	}
}

func TestDomainAssumptions_MissingArg(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("domain", "assumptions")
	if err == nil {
		t.Error("expected error when domain arg is missing, got nil")
	}
}

func TestDomainAssumptions_NoTable(t *testing.T) {
	content := "# Tracker\n**Stage:** EARLY\nNo assumptions table here.\n"
	runner := NewTestRunner(t)
	tmp := makeTrackerDir(t, "sparse-domain", content)
	t.Setenv("FORGE_ROOT", tmp)

	output, err := runner.Execute("domain", "assumptions", "sparse-domain")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(output, "No P0 assumptions found") {
		t.Errorf("expected 'No P0 assumptions found' message, got: %s", output)
	}
}

// ─── forge domain decide ────────────────────────────────────────────────────

func TestDomainDecide_GO(t *testing.T) {
	runner := NewTestRunner(t)
	tmp := makeTrackerDir(t, "decide-domain", sampleTracker)
	t.Setenv("FORGE_ROOT", tmp)

	output, err := runner.Execute("domain", "decide", "decide-domain", "GO")
	if err != nil {
		t.Fatalf("domain decide GO failed: %v", err)
	}
	if !strings.Contains(output, "GO") {
		t.Errorf("expected output to contain GO, got: %s", output)
	}

	// Verify the file was actually updated.
	written, err := os.ReadFile(filepath.Join(tmp, "docs", "validation", "decide-domain.md"))
	if err != nil {
		t.Fatalf("failed to read written tracker: %v", err)
	}
	if !strings.Contains(string(written), "[x] GO") {
		t.Errorf("expected tracker to contain [x] GO, got: %s", string(written))
	}
}

func TestDomainDecide_KILL(t *testing.T) {
	runner := NewTestRunner(t)
	tmp := makeTrackerDir(t, "kill-domain", sampleTracker)
	t.Setenv("FORGE_ROOT", tmp)

	_, err := runner.Execute("domain", "decide", "kill-domain", "KILL")
	if err != nil {
		t.Fatalf("domain decide KILL failed: %v", err)
	}

	written, err := os.ReadFile(filepath.Join(tmp, "docs", "validation", "kill-domain.md"))
	if err != nil {
		t.Fatalf("failed to read written tracker: %v", err)
	}
	if !strings.Contains(string(written), "[x] KILL") {
		t.Errorf("expected tracker to contain [x] KILL, got: %s", string(written))
	}
}

func TestDomainDecide_LowercaseAccepted(t *testing.T) {
	runner := NewTestRunner(t)
	tmp := makeTrackerDir(t, "lower-domain", sampleTracker)
	t.Setenv("FORGE_ROOT", tmp)

	_, err := runner.Execute("domain", "decide", "lower-domain", "go")
	if err != nil {
		t.Fatalf("domain decide 'go' (lowercase) failed: %v", err)
	}
}

func TestDomainDecide_InvalidDecision(t *testing.T) {
	runner := NewTestRunner(t)
	tmp := makeTrackerDir(t, "inv-domain", sampleTracker)
	t.Setenv("FORGE_ROOT", tmp)

	_, err := runner.Execute("domain", "decide", "inv-domain", "MAYBE")
	if err == nil {
		t.Error("expected error for invalid decision MAYBE, got nil")
	}
}

func TestDomainDecide_MissingArgs(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("domain", "decide", "some-domain")
	if err == nil {
		t.Error("expected error when decision arg is missing, got nil")
	}
}

func TestDomainDecide_TrackerNotFound(t *testing.T) {
	runner := NewTestRunner(t)
	tmp := t.TempDir()
	if err := os.MkdirAll(filepath.Join(tmp, "docs", "validation"), 0755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("FORGE_ROOT", tmp)

	_, err := runner.Execute("domain", "decide", "ghost-domain", "GO")
	if err == nil {
		t.Error("expected error when tracker file does not exist, got nil")
	}
}

func TestDomainDecide_AppendsToNoDecisionSection(t *testing.T) {
	// Tracker with no ## Decision section.
	content := "# Tracker\n**Stage:** EARLY\n\n## P0 Assumptions\n\n| # | Assumption | Status | Evidence |\n|---|---|---|---|\n| IS-1 | Test | UNTESTED | — |\n"
	runner := NewTestRunner(t)
	tmp := makeTrackerDir(t, "nodecision-domain", content)
	t.Setenv("FORGE_ROOT", tmp)

	_, err := runner.Execute("domain", "decide", "nodecision-domain", "GO")
	if err != nil {
		t.Fatalf("domain decide on tracker without Decision section failed: %v", err)
	}

	written, err := os.ReadFile(filepath.Join(tmp, "docs", "validation", "nodecision-domain.md"))
	if err != nil {
		t.Fatalf("failed to read written tracker: %v", err)
	}
	if !strings.Contains(string(written), "## Decision") {
		t.Errorf("expected ## Decision section to be added, got: %s", string(written))
	}
	if !strings.Contains(string(written), "[x] GO") {
		t.Errorf("expected [x] GO entry, got: %s", string(written))
	}
}
