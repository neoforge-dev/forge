package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// parseGatesFromContent
// ---------------------------------------------------------------------------

func TestParseGatesFromContent_BasicLine(t *testing.T) {
	content := "- GATE-VC (15 min) — railway up + API keys\n"
	gates := parseGatesFromContent(content, "brandfocus-ai", "lead-context.md", map[string]string{})
	if len(gates) != 1 {
		t.Fatalf("expected 1 gate, got %d", len(gates))
	}
	g := gates[0]
	if g.ID != "GATE-VC" {
		t.Errorf("expected ID=GATE-VC, got %q", g.ID)
	}
	if g.Duration != "15 min" {
		t.Errorf("expected Duration='15 min', got %q", g.Duration)
	}
	if g.Status != "PENDING" {
		t.Errorf("expected Status=PENDING, got %q", g.Status)
	}
	if g.Domain != "brandfocus-ai" {
		t.Errorf("expected Domain=brandfocus-ai, got %q", g.Domain)
	}
}

func TestParseGatesFromContent_TableFormat(t *testing.T) {
	content := "| GATE-VC | 15 min | railway up + Deepgram key | PENDING |\n"
	gates := parseGatesFromContent(content, "brandfocus-ai", "lead-context.md", map[string]string{})
	if len(gates) == 0 {
		t.Fatal("expected at least 1 gate")
	}
	g := gates[0]
	if g.ID != "GATE-VC" {
		t.Errorf("expected GATE-VC, got %q", g.ID)
	}
}

func TestParseGatesFromContent_RemovedStrikethrough(t *testing.T) {
	content := "| ~~GATE-SF~~ | — | Study Flow killed | REMOVED |\n"
	gates := parseGatesFromContent(content, "forge", "lead-context.md", map[string]string{})
	if len(gates) == 0 {
		t.Fatal("expected at least 1 gate entry for removed gate")
	}
	for _, g := range gates {
		if strings.Contains(g.ID, "SF") && g.Status != "REMOVED" {
			t.Errorf("expected GATE-SF to be REMOVED, got %q", g.Status)
		}
	}
}

func TestParseGatesFromContent_MultipleGates(t *testing.T) {
	content := `
- GATE-VC (15 min) — railway deploy
- GATE-C (30 min) — App Store Connect
- GATE-GSC (5 min) — Verify GSC
`
	gates := parseGatesFromContent(content, "domain-x", "lead-context.md", map[string]string{})
	if len(gates) != 3 {
		t.Fatalf("expected 3 gates, got %d", len(gates))
	}
	ids := map[string]bool{}
	for _, g := range gates {
		ids[g.ID] = true
	}
	for _, expected := range []string{"GATE-VC", "GATE-C", "GATE-GSC"} {
		if !ids[expected] {
			t.Errorf("expected %s in gates", expected)
		}
	}
}

func TestParseGatesFromContent_BoldMarkdown(t *testing.T) {
	content := "- **GATE-STRIPE-TEAM**: Create Team Stripe products (15 min)\n"
	gates := parseGatesFromContent(content, "codeswiftr-com", "lead-context.md", map[string]string{})
	if len(gates) == 0 {
		t.Fatal("expected at least 1 gate")
	}
	if gates[0].ID != "GATE-STRIPE-TEAM" {
		t.Errorf("expected GATE-STRIPE-TEAM, got %q", gates[0].ID)
	}
}

func TestParseGatesFromContent_ProductMapping(t *testing.T) {
	products := map[string]string{
		"brandfocus-ai": "voice-coach",
	}
	content := "- GATE-VC (15 min) — railway up\n"
	gates := parseGatesFromContent(content, "brandfocus-ai", "lead-context.md", products)
	if len(gates) == 0 {
		t.Fatal("expected at least 1 gate")
	}
	if gates[0].Product != "voice-coach" {
		t.Errorf("expected Product=voice-coach, got %q", gates[0].Product)
	}
}

func TestParseGatesFromContent_EmptyContent(t *testing.T) {
	gates := parseGatesFromContent("", "some-domain", "lead-context.md", map[string]string{})
	if len(gates) != 0 {
		t.Errorf("expected 0 gates for empty content, got %d", len(gates))
	}
}

func TestParseGatesFromContent_NoGates(t *testing.T) {
	content := "# Domain Context\n\nNo gates here. Just regular content about the product.\n"
	gates := parseGatesFromContent(content, "some-domain", "lead-context.md", map[string]string{})
	if len(gates) != 0 {
		t.Errorf("expected 0 gates, got %d", len(gates))
	}
}

// ---------------------------------------------------------------------------
// extractGateDescription
// ---------------------------------------------------------------------------

func TestExtractGateDescription_DashSeparator(t *testing.T) {
	desc := extractGateDescription("- GATE-VC (15 min) — railway up + API keys", "GATE-VC")
	if desc == "" {
		t.Error("expected non-empty description")
	}
	if strings.Contains(desc, "GATE-VC") {
		t.Errorf("description should not contain gate ID, got: %q", desc)
	}
	if strings.Contains(desc, "15 min") {
		t.Errorf("description should not contain duration, got: %q", desc)
	}
}

func TestExtractGateDescription_ColonSeparator(t *testing.T) {
	desc := extractGateDescription("**GATE-STRIPE-TEAM**: Create Team Stripe products (15 min)", "GATE-STRIPE-TEAM")
	if desc == "" {
		t.Error("expected non-empty description")
	}
	if !strings.Contains(desc, "Stripe") {
		t.Errorf("expected 'Stripe' in description, got: %q", desc)
	}
}

func TestExtractGateDescription_TableFormat(t *testing.T) {
	desc := extractGateDescription("| GATE-C | 30 min | App Store Connect | PENDING |", "GATE-C")
	if !strings.Contains(desc, "App Store") {
		t.Errorf("expected 'App Store' in description, got: %q", desc)
	}
}

// ---------------------------------------------------------------------------
// estimateGateMinutes
// ---------------------------------------------------------------------------

func TestEstimateGateMinutes_WithDurations(t *testing.T) {
	gates := []Gate{
		{ID: "GATE-VC", Duration: "15 min", Status: "PENDING"},
		{ID: "GATE-C", Duration: "30 min", Status: "PENDING"},
		{ID: "GATE-GSC", Duration: "5 min", Status: "PENDING"},
	}
	total := estimateGateMinutes(gates)
	if total != 50 {
		t.Errorf("expected 50 min, got %d", total)
	}
}

func TestEstimateGateMinutes_NoDuration(t *testing.T) {
	gates := []Gate{
		{ID: "GATE-X", Duration: "", Status: "PENDING"},
	}
	total := estimateGateMinutes(gates)
	if total != 15 { // default estimate
		t.Errorf("expected default 15 min, got %d", total)
	}
}

func TestEstimateGateMinutes_Empty(t *testing.T) {
	total := estimateGateMinutes(nil)
	if total != 0 {
		t.Errorf("expected 0 for empty gates, got %d", total)
	}
}

// ---------------------------------------------------------------------------
// buildValidationDescription
// ---------------------------------------------------------------------------

func TestBuildValidationDescription_KnownCodes(t *testing.T) {
	codes := []string{"IS", "VC", "BB", "CC", "KC"}
	for _, code := range codes {
		desc := buildValidationDescription(code)
		if desc == "" {
			t.Errorf("expected non-empty description for code %q", code)
		}
		if desc == "interviews required" {
			t.Errorf("expected specific description for known code %q", code)
		}
	}
}

func TestBuildValidationDescription_UnknownCode(t *testing.T) {
	desc := buildValidationDescription("ZZ")
	if desc != "interviews required" {
		t.Errorf("expected fallback description for unknown code, got %q", desc)
	}
}

// ---------------------------------------------------------------------------
// isInfraDir
// ---------------------------------------------------------------------------

func TestIsInfraDir(t *testing.T) {
	infraDomains := []string{"prya", "sati", "nova", "vega", "gaea", "oc"}
	for _, d := range infraDomains {
		if !isInfraDir(d) {
			t.Errorf("expected %q to be an infra dir", d)
		}
	}

	portfolioDomains := []string{"codeswiftr-com", "brandfocus-ai", "babybit-es", "calmconnect-io"}
	for _, d := range portfolioDomains {
		if isInfraDir(d) {
			t.Errorf("expected %q to NOT be an infra dir", d)
		}
	}
}

// ---------------------------------------------------------------------------
// collectGates (integration — uses temp filesystem)
// ---------------------------------------------------------------------------

func TestCollectGates_FromTempDir(t *testing.T) {
	// Build a minimal .forge/context/ tree in a temp dir.
	tmp := t.TempDir()

	// Create domain context with gates.
	ctxDir := filepath.Join(tmp, ".forge", "context", "test-domain")
	if err := os.MkdirAll(ctxDir, 0o755); err != nil {
		t.Fatal(err)
	}

	content := `# Test Domain — Lead Context

## Human Gates

- GATE-VC (15 min) — Deploy on Railway
- GATE-GSC (5 min) — Verify GSC

## Other Info
Some other text.
`
	if err := os.WriteFile(filepath.Join(ctxDir, "lead-context.md"), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	// Also create forge infra context (empty).
	forgeCtxDir := filepath.Join(tmp, ".forge", "context", "forge")
	if err := os.MkdirAll(forgeCtxDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(forgeCtxDir, "lead-context.md"), []byte("# FORGE\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	gates, err := collectGates(tmp, map[string]string{"test-domain": "test-product"})
	if err != nil {
		t.Fatalf("collectGates failed: %v", err)
	}

	if len(gates) < 2 {
		t.Fatalf("expected at least 2 gates, got %d", len(gates))
	}

	ids := map[string]bool{}
	for _, g := range gates {
		ids[g.ID] = true
	}
	if !ids["GATE-VC"] {
		t.Error("expected GATE-VC in collected gates")
	}
	if !ids["GATE-GSC"] {
		t.Error("expected GATE-GSC in collected gates")
	}
}

func TestCollectGates_Deduplication(t *testing.T) {
	// Same gate in two domain context files should appear only once.
	tmp := t.TempDir()
	ctxBase := filepath.Join(tmp, ".forge", "context")

	domains := []string{"domain-a", "domain-b"}
	for _, d := range domains {
		dir := filepath.Join(ctxBase, d)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		content := "- GATE-SHARED (10 min) — Shared gate\n"
		if err := os.WriteFile(filepath.Join(dir, "lead-context.md"), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// Forge context.
	forgeDir := filepath.Join(ctxBase, "forge")
	if err := os.MkdirAll(forgeDir, 0o755); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(forgeDir, "lead-context.md"), []byte("# FORGE\n"), 0o644)

	gates, err := collectGates(tmp, map[string]string{})
	if err != nil {
		t.Fatalf("collectGates failed: %v", err)
	}

	count := 0
	for _, g := range gates {
		if g.ID == "GATE-SHARED" {
			count++
		}
	}
	if count != 1 {
		t.Errorf("expected GATE-SHARED to appear exactly once, got %d times", count)
	}
}

func TestCollectGates_NoContextDir(t *testing.T) {
	// Should return an error when the context dir doesn't exist.
	_, err := collectGates("/nonexistent/path", map[string]string{})
	if err == nil {
		t.Error("expected error for non-existent context dir")
	}
}

// ---------------------------------------------------------------------------
// loadDomainProducts
// ---------------------------------------------------------------------------

func TestLoadDomainProducts_FromYAML(t *testing.T) {
	tmp := t.TempDir()
	cfgDir := filepath.Join(tmp, "config")
	if err := os.MkdirAll(cfgDir, 0o755); err != nil {
		t.Fatal(err)
	}

	yaml := `domains:
  codeswiftr-com:
    display_name: CodeSwiftr
    active: true
    products:
      - interview-simulator
  brandfocus-ai:
    display_name: BrandFocus AI
    active: true
    products:
      - voice-coach
`
	if err := os.WriteFile(filepath.Join(cfgDir, "domains.yaml"), []byte(yaml), 0o644); err != nil {
		t.Fatal(err)
	}

	products, err := loadDomainProducts(tmp)
	if err != nil {
		t.Fatalf("loadDomainProducts failed: %v", err)
	}

	if products["codeswiftr-com"] != "interview-simulator" {
		t.Errorf("expected interview-simulator, got %q", products["codeswiftr-com"])
	}
	if products["brandfocus-ai"] != "voice-coach" {
		t.Errorf("expected voice-coach, got %q", products["brandfocus-ai"])
	}
}

func TestLoadDomainProducts_NoFile(t *testing.T) {
	_, err := loadDomainProducts("/nonexistent")
	if err == nil {
		t.Error("expected error when no domains.yaml exists")
	}
}

// ---------------------------------------------------------------------------
// findChecklist
// ---------------------------------------------------------------------------

func TestFindChecklist_Found(t *testing.T) {
	tmp := t.TempDir()
	// Set FORGE_ROOT so findChecklist resolves paths correctly.
	t.Setenv("FORGE_ROOT", tmp)

	productDir := filepath.Join(tmp, "services", "voice-coach")
	if err := os.MkdirAll(productDir, 0o755); err != nil {
		t.Fatal(err)
	}
	checklistPath := filepath.Join(productDir, "DEPLOY_CHECKLIST.md")
	if err := os.WriteFile(checklistPath, []byte("# Checklist\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	result := findChecklist("brandfocus-ai", "voice-coach")
	if result == "" {
		t.Error("expected to find checklist, got empty string")
	}
	if !strings.Contains(result, "voice-coach") {
		t.Errorf("expected path to contain 'voice-coach', got %q", result)
	}
}

func TestFindChecklist_NotFound(t *testing.T) {
	t.Setenv("FORGE_ROOT", t.TempDir())
	result := findChecklist("nonexistent-domain", "nonexistent-product")
	if result != "" {
		t.Errorf("expected empty string for missing checklist, got %q", result)
	}
}

// ---------------------------------------------------------------------------
// printGateStatus (smoke test — just verify it doesn't panic)
// ---------------------------------------------------------------------------

func TestPrintGateStatus_Smoke(t *testing.T) {
	gates := []Gate{
		{ID: "GATE-VC", Duration: "15 min", What: "railway deploy", Domain: "brandfocus-ai", Product: "voice-coach", Status: "PENDING"},
		{ID: "GATE-SF", Duration: "", What: "Study Flow killed", Domain: "thebrightharbor-com", Product: "study-flow", Status: "REMOVED"},
	}
	validations := []ValidationItem{
		{Code: "IS", Description: "5 engineer interviews (Day 15 GSC check)"},
		{Code: "VC", Description: "5 presenter interviews (Day 30 kill gate)"},
	}

	// Redirect stdout to /dev/null for the smoke test.
	old := os.Stdout
	null, err := os.Open(os.DevNull)
	if err != nil {
		t.Fatal(err)
	}
	os.Stdout = null
	defer func() { os.Stdout = old; null.Close() }()

	// Must not panic.
	printGateStatus(gates, validations)
}

func TestPrintGateStatus_NoGates(t *testing.T) {
	old := os.Stdout
	null, _ := os.Open(os.DevNull)
	os.Stdout = null
	defer func() { os.Stdout = old; null.Close() }()

	printGateStatus(nil, nil)
}

func TestPrintGateStatus_AllResolved(t *testing.T) {
	old := os.Stdout
	null, _ := os.Open(os.DevNull)
	os.Stdout = null
	defer func() { os.Stdout = old; null.Close() }()

	gates := []Gate{
		{ID: "GATE-SF", Status: "REMOVED"},
	}
	printGateStatus(gates, nil)
}
