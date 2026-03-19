//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"strings"
	"testing"
)

// ============================================================
// coverage_wave231_test.go — context_sync pure functions + dashboard helpers
// Target: extractSummary, parseMarkdownDecisions, parseMarkdownFailures,
//         sanitizeJSONKey, AgentStatus.String(), splitCapabilities, splitString
// ============================================================

// --- extractSummary ---

func TestWave231_ExtractSummary_FirstNonHeader(t *testing.T) {
	content := "# Title\n\nThis is the summary line.\nAnother line."
	got := extractSummary(content)
	if got != "This is the summary line." {
		t.Errorf("expected 'This is the summary line.', got: %q", got)
	}
}

func TestWave231_ExtractSummary_NoHeaders(t *testing.T) {
	content := "Direct summary without header."
	got := extractSummary(content)
	if got != "Direct summary without header." {
		t.Errorf("expected direct summary, got: %q", got)
	}
}

func TestWave231_ExtractSummary_EmptyContent(t *testing.T) {
	got := extractSummary("")
	if got != "" {
		t.Errorf("expected empty for empty content, got: %q", got)
	}
}

func TestWave231_ExtractSummary_OnlyHeaders(t *testing.T) {
	content := "# Header1\n## Header2\n### Header3"
	got := extractSummary(content)
	if got != "" {
		t.Errorf("expected empty for header-only content, got: %q", got)
	}
}

func TestWave231_ExtractSummary_BlankLines(t *testing.T) {
	content := "\n\n\n   \nActual content here."
	got := extractSummary(content)
	if got != "Actual content here." {
		t.Errorf("expected 'Actual content here.', got: %q", got)
	}
}

// --- parseMarkdownDecisions ---

func TestWave231_ParseMarkdownDecisions_DashItems(t *testing.T) {
	content := "# Decisions\n\n- Use PostgreSQL for persistence\n- Adopt FastAPI backend\n"
	decisions := parseMarkdownDecisions(content)
	if len(decisions) != 2 {
		t.Errorf("expected 2 decisions, got %d", len(decisions))
	}
	if !strings.Contains(decisions[0].Description, "PostgreSQL") {
		t.Errorf("expected PostgreSQL in first decision: %s", decisions[0].Description)
	}
}

func TestWave231_ParseMarkdownDecisions_StarItems(t *testing.T) {
	content := "* Use Redis for caching\n* Deploy on Railway\n"
	decisions := parseMarkdownDecisions(content)
	if len(decisions) != 2 {
		t.Errorf("expected 2 decisions, got %d", len(decisions))
	}
}

func TestWave231_ParseMarkdownDecisions_Empty(t *testing.T) {
	decisions := parseMarkdownDecisions("No bullet items here.")
	if len(decisions) != 0 {
		t.Errorf("expected 0 decisions, got %d", len(decisions))
	}
}

func TestWave231_ParseMarkdownDecisions_IDNotEmpty(t *testing.T) {
	content := "- Decision with ID\n"
	decisions := parseMarkdownDecisions(content)
	if len(decisions) == 0 {
		t.Fatal("expected 1 decision")
	}
	if decisions[0].ID == "" {
		t.Error("expected non-empty ID (generated ULID)")
	}
}

// --- parseMarkdownFailures ---

func TestWave231_ParseMarkdownFailures_DashItems(t *testing.T) {
	content := "- API timeout on cold start\n- Redis connection reset\n"
	failures := parseMarkdownFailures(content)
	if len(failures) != 2 {
		t.Errorf("expected 2 failures, got %d", len(failures))
	}
}

func TestWave231_ParseMarkdownFailures_Empty(t *testing.T) {
	failures := parseMarkdownFailures("No failures to report.")
	if len(failures) != 0 {
		t.Errorf("expected 0 failures, got %d", len(failures))
	}
}

func TestWave231_ParseMarkdownFailures_IDNotEmpty(t *testing.T) {
	content := "* failure item\n"
	failures := parseMarkdownFailures(content)
	if len(failures) == 0 {
		t.Fatal("expected 1 failure")
	}
	if failures[0].ID == "" {
		t.Error("expected non-empty ID")
	}
}

// --- sanitizeJSONKey ---

func TestWave231_SanitizeJSONKey_RemovesExtension(t *testing.T) {
	got := sanitizeJSONKey("lead-context.md")
	if strings.HasSuffix(got, ".md") {
		t.Errorf("expected extension removed, got: %s", got)
	}
}

func TestWave231_SanitizeJSONKey_ReplacesHyphens(t *testing.T) {
	got := sanitizeJSONKey("lead-context.md")
	if strings.Contains(got, "-") {
		t.Errorf("expected hyphens replaced, got: %s", got)
	}
	// hyphens → underscore
	if !strings.Contains(got, "_") {
		t.Errorf("expected underscore replacement, got: %s", got)
	}
}

func TestWave231_SanitizeJSONKey_Alphanumeric(t *testing.T) {
	got := sanitizeJSONKey("decisions123.json")
	if !strings.HasPrefix(got, "decisions") {
		t.Errorf("expected 'decisions' prefix, got: %s", got)
	}
	if strings.Contains(got, ".") {
		t.Errorf("expected no dots, got: %s", got)
	}
}

func TestWave231_SanitizeJSONKey_NoExtension(t *testing.T) {
	got := sanitizeJSONKey("lead_context")
	if got != "lead_context" {
		t.Errorf("expected unchanged alphanumeric+underscore, got: %s", got)
	}
}

// --- AgentStatus.String() ---

func TestWave231_AgentStatus_String(t *testing.T) {
	cases := []struct {
		status AgentStatus
		want   string
	}{
		{AgentStatusOffline, "offline"},
		{AgentStatusIdle, "idle"},
		{AgentStatusWorking, "working"},
		{AgentStatusBlocked, "blocked"},
		{AgentStatusError, "error"},
		{AgentStatusMaintenance, "maintenance"},
		{AgentStatus(99), "unknown"},
	}
	for _, tc := range cases {
		got := tc.status.String()
		if got != tc.want {
			t.Errorf("AgentStatus(%d).String() = %q, want %q", tc.status, got, tc.want)
		}
	}
}

// --- splitCapabilities ---

func TestWave231_SplitCapabilities_Empty(t *testing.T) {
	got := splitCapabilities("")
	if len(got) != 0 {
		t.Errorf("expected nil/empty for empty string, got %v", got)
	}
}

func TestWave231_SplitCapabilities_Single(t *testing.T) {
	got := splitCapabilities("go")
	if len(got) != 1 || got[0] != "go" {
		t.Errorf("expected [go], got %v", got)
	}
}

func TestWave231_SplitCapabilities_Multiple(t *testing.T) {
	got := splitCapabilities("go,python,swift")
	if len(got) != 3 {
		t.Errorf("expected 3 capabilities, got %d: %v", len(got), got)
	}
}

// --- splitString ---

func TestWave231_SplitString_Basic(t *testing.T) {
	got := splitString("a,b,c", ",")
	if len(got) != 3 {
		t.Errorf("expected 3 parts, got %d: %v", len(got), got)
	}
	if got[0] != "a" || got[1] != "b" || got[2] != "c" {
		t.Errorf("unexpected parts: %v", got)
	}
}

func TestWave231_SplitString_NoSep(t *testing.T) {
	got := splitString("abc", ",")
	if len(got) != 1 || got[0] != "abc" {
		t.Errorf("expected ['abc'], got %v", got)
	}
}

func TestWave231_SplitString_EmptyString(t *testing.T) {
	got := splitString("", ",")
	if len(got) != 1 {
		t.Errorf("expected 1 element for empty string, got %d", len(got))
	}
}
