// adr_test.go — unit tests for ADR parsing + verification logic
package main

import (
	"os"
	"path/filepath"
	"testing"
)

// ─── TestADRParseRow_Implemented ──────────────────────────────────────────────

func TestADRParseRow_Implemented(t *testing.T) {
	line := `| [009](ADR-009-v3-agentic-patterns.md) | Agentic Patterns | ✅ | FSM wired ✅; **38 patrols** (grep ` + "`" + `ID:\s*"` + "`" + ` cmd/forged/patrol.go) |`
	entry, ok := parseADRRow(line)
	if !ok {
		t.Fatal("expected parseADRRow to return ok=true")
	}
	if entry.Number != "009" {
		t.Errorf("Number: want 009, got %q", entry.Number)
	}
	if entry.Status != "✅" {
		t.Errorf("Status: want ✅, got %q", entry.Status)
	}
	if entry.Title != "Agentic Patterns" {
		t.Errorf("Title: want 'Agentic Patterns', got %q", entry.Title)
	}
}

// ─── TestADRParseRow_Partial ──────────────────────────────────────────────────

func TestADRParseRow_Partial(t *testing.T) {
	line := `| [034](ADR-034-fluid-fleet-autoscaling.md) | Fluid Fleet Auto-Scaling | 🔧 | fleetScaleRecommendPatrol present; ` + "`" + `RecommendScale` + "`" + ` call-sites reference missing symbol |`
	entry, ok := parseADRRow(line)
	if !ok {
		t.Fatal("expected ok=true for 🔧 row")
	}
	if entry.Status != "🔧" {
		t.Errorf("Status: want 🔧, got %q", entry.Status)
	}
	if entry.Number != "034" {
		t.Errorf("Number: want 034, got %q", entry.Number)
	}
}

// ─── TestADRParseRow_SkipsHeader ──────────────────────────────────────────────

func TestADRParseRow_SkipsHeader(t *testing.T) {
	cases := []string{
		"| ADR | Title | Status | Notes |",
		"|-----|-------|--------|-------|",
		"",
		"## Quick Status Summary",
		"✅ 009, 010",
	}
	for _, line := range cases {
		_, ok := parseADRRow(line)
		if ok {
			t.Errorf("line %q: expected ok=false (should be skipped), got ok=true", line)
		}
	}
}

// ─── TestADRExtractRefs ───────────────────────────────────────────────────────

func TestADRExtractRefs(t *testing.T) {
	notes := "blueprint.go + `blueprint_runtime_test.go` live. CLI: `forge blueprint validate/run`. Config: `config/blueprints/` — `StandardPatrols()` wired"

	refs := extractRefs(notes)

	has := func(kind, val string) bool {
		for _, r := range refs {
			if r.Kind == kind && r.Value == val {
				return true
			}
		}
		return false
	}

	if !has("file", "blueprint_runtime_test.go") {
		t.Errorf("expected file ref 'blueprint_runtime_test.go'")
	}
	if !has("config", "config/blueprints/") {
		t.Errorf("expected config ref 'config/blueprints/'")
	}
	if !has("func", "StandardPatrols()") {
		t.Errorf("expected func ref 'StandardPatrols()'")
	}
}

func TestADRExtractRefs_EmptyNotes(t *testing.T) {
	refs := extractRefs("")
	if len(refs) != 0 {
		t.Errorf("expected 0 refs for empty notes, got %d", len(refs))
	}
}

func TestADRExtractRefs_Dedup(t *testing.T) {
	notes := "`patrol.go` and also `patrol.go` again"
	refs := extractRefs(notes)
	if len(refs) != 1 {
		t.Errorf("expected dedup to 1 ref, got %d", len(refs))
	}
}

func TestADRExtractRefs_MultipleKinds(t *testing.T) {
	notes := "`cmd/forged/patrol.go` hosts `ProgressTask()` and `config/nodes.yaml` is read on boot"
	refs := extractRefs(notes)

	kinds := map[string]int{}
	for _, r := range refs {
		kinds[r.Kind]++
	}
	if kinds["file"] != 1 {
		t.Errorf("want 1 file ref, got %d", kinds["file"])
	}
	if kinds["func"] != 1 {
		t.Errorf("want 1 func ref, got %d", kinds["func"])
	}
	if kinds["config"] != 1 {
		t.Errorf("want 1 config ref, got %d", kinds["config"])
	}
}

// ─── TestADRVerifyRef_ExistingFile ────────────────────────────────────────────

func TestADRVerifyRef_ExistingFile(t *testing.T) {
	dir := t.TempDir()
	subdir := filepath.Join(dir, "cmd", "forged")
	if err := os.MkdirAll(subdir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	f := filepath.Join(subdir, "patrol.go")
	if err := os.WriteFile(f, []byte("package main\n"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	ref := adrRef{Kind: "file", Value: "cmd/forged/patrol.go"}
	msg, ok := verifyRef(ref, dir)
	if !ok {
		t.Errorf("expected ok=true for existing file, got msg=%q", msg)
	}
	if msg != "OK" {
		t.Errorf("expected msg=OK, got %q", msg)
	}
}

// ─── TestADRVerifyRef_MissingFile ─────────────────────────────────────────────

func TestADRVerifyRef_MissingFile(t *testing.T) {
	dir := t.TempDir()

	ref := adrRef{Kind: "file", Value: "config/dark-factory/approval-tiers.yaml"}
	msg, ok := verifyRef(ref, dir)
	if ok {
		t.Errorf("expected ok=false for missing file")
	}
	if !adrContains(msg, "MISSING") {
		t.Errorf("expected msg to contain MISSING, got %q", msg)
	}
}

func TestADRVerifyRef_MissingConfig(t *testing.T) {
	dir := t.TempDir()

	ref := adrRef{Kind: "config", Value: "config/nodes.yaml"}
	_, ok := verifyRef(ref, dir)
	if ok {
		t.Errorf("expected ok=false for missing config")
	}
}

func TestADRVerifyRef_ExistingConfig(t *testing.T) {
	dir := t.TempDir()
	cfgDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(cfgDir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(cfgDir, "nodes.yaml"), []byte("nodes: []\n"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	ref := adrRef{Kind: "config", Value: "config/nodes.yaml"}
	msg, ok := verifyRef(ref, dir)
	if !ok {
		t.Errorf("expected ok=true for existing config, got msg=%q", msg)
	}
	if msg != "OK" {
		t.Errorf("expected OK, got %q", msg)
	}
}

// ─── TestADRDriftSummary ──────────────────────────────────────────────────────

func TestADRDriftSummary_NoRefs(t *testing.T) {
	r := adrResult{Entry: adrEntry{Number: "042"}}
	got := driftSummary(r)
	if got != "no refs" {
		t.Errorf("want 'no refs', got %q", got)
	}
}

func TestADRDriftSummary_AllOK(t *testing.T) {
	r := adrResult{
		Entry:    adrEntry{Number: "009"},
		Refs:     []adrRef{{Kind: "file", Value: "patrol.go"}},
		Verified: 1,
	}
	got := driftSummary(r)
	if got != "OK (1/1 refs verified)" {
		t.Errorf("want 'OK (1/1 refs verified)', got %q", got)
	}
}

func TestADRDriftSummary_HasMissing(t *testing.T) {
	r := adrResult{
		Entry:   adrEntry{Number: "036"},
		Refs:    []adrRef{{Kind: "config", Value: "config/dark-factory/approval-tiers.yaml"}},
		Missing: []string{"MISSING: config/dark-factory/approval-tiers.yaml"},
	}
	got := driftSummary(r)
	if !adrContains(got, "DRIFT") {
		t.Errorf("want DRIFT prefix, got %q", got)
	}
}

// ─── TestADRCmd_Registered ────────────────────────────────────────────────────

func TestADRCmd_Registered(t *testing.T) {
	if adrCmd.Use == "" {
		t.Error("adrCmd.Use should not be empty")
	}
}

func TestADRStatusCmd_Registered(t *testing.T) {
	if adrStatusCmd.Use == "" {
		t.Error("adrStatusCmd.Use should not be empty")
	}
	if adrStatusCmd.RunE == nil {
		t.Error("adrStatusCmd.RunE should be set")
	}
}

func TestADRStatusCmd_Flags(t *testing.T) {
	flags := []string{"fail-on-drift", "verbose", "only"}
	for _, name := range flags {
		if adrStatusCmd.Flags().Lookup(name) == nil {
			t.Errorf("adrStatusCmd missing flag --%s", name)
		}
	}
}

// ─── TestADRParseIndex_Integration ───────────────────────────────────────────

func TestADRParseIndex_Integration(t *testing.T) {
	dir := t.TempDir()
	adrDir := filepath.Join(dir, "docs", "adr")
	if err := os.MkdirAll(adrDir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	content := `# ADR Index

| ADR | Title | Status | Notes |
|-----|-------|--------|-------|
| [009](ADR-009.md) | Agentic Patterns | ✅ | ` + "`" + `cmd/forged/patrol.go` + "`" + ` wired |
| [036](ADR-036.md) | Autonomous Fleet Execution | 🔧 | ` + "`" + `config/dark-factory/approval-tiers.yaml` + "`" + ` template-only |
| [042](ADR-042.md) | Public/Private Repo Split | 📋 | Deferred |
`
	if err := os.WriteFile(filepath.Join(adrDir, "INDEX.md"), []byte(content), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	entries, err := parseADRIndex(filepath.Join(adrDir, "INDEX.md"))
	if err != nil {
		t.Fatalf("parseADRIndex: %v", err)
	}
	if len(entries) != 3 {
		t.Fatalf("want 3 entries, got %d", len(entries))
	}
	if entries[0].Number != "009" || entries[0].Status != "✅" {
		t.Errorf("entry[0]: got %+v", entries[0])
	}
	if entries[1].Number != "036" || entries[1].Status != "🔧" {
		t.Errorf("entry[1]: got %+v", entries[1])
	}
	if entries[2].Number != "042" || entries[2].Status != "📋" {
		t.Errorf("entry[2]: got %+v", entries[2])
	}
}

// ─── helpers ─────────────────────────────────────────────────────────────────

// adrContains reports whether s contains sub. Uses a local name to avoid
// collision with any other helpers in the test package.
func adrContains(s, sub string) bool {
	if len(sub) == 0 {
		return true
	}
	for i := 0; i <= len(s)-len(sub); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}
