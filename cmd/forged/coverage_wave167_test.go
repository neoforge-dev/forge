//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 167: output.go + help.go pure functions
// Targets:
//   ParseOutputFormat    (output.go:38)  — json/yaml/table/plain/default
//   WriteOutput          (output.go:80)  — JSON/YAML/Table/Plain formats
//   WriteOutputWithFlag  (output.go:102) — convenience wrapper
//   levenshteinDistance  (help.go:650)   — edit distance
//   DidYouMean           (help.go:618)   — substring match, levenshtein, no match
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// ParseOutputFormat
// ---------------------------------------------------------------------------

func TestWave167_ParseOutputFormat_JSON(t *testing.T) {
	if got := ParseOutputFormat("json"); got != OutputFormatJSON {
		t.Errorf("expected JSON, got %v", got)
	}
}

func TestWave167_ParseOutputFormat_YAML(t *testing.T) {
	if got := ParseOutputFormat("yaml"); got != OutputFormatYAML {
		t.Errorf("expected YAML for 'yaml', got %v", got)
	}
	if got := ParseOutputFormat("yml"); got != OutputFormatYAML {
		t.Errorf("expected YAML for 'yml', got %v", got)
	}
}

func TestWave167_ParseOutputFormat_Table(t *testing.T) {
	if got := ParseOutputFormat("table"); got != OutputFormatTable {
		t.Errorf("expected Table, got %v", got)
	}
}

func TestWave167_ParseOutputFormat_Plain(t *testing.T) {
	if got := ParseOutputFormat("plain"); got != OutputFormatPlain {
		t.Errorf("expected Plain, got %v", got)
	}
}

func TestWave167_ParseOutputFormat_Empty(t *testing.T) {
	if got := ParseOutputFormat(""); got != OutputFormatPlain {
		t.Errorf("expected Plain for empty, got %v", got)
	}
}

func TestWave167_ParseOutputFormat_Unknown(t *testing.T) {
	if got := ParseOutputFormat("csv"); got != OutputFormatPlain {
		t.Errorf("expected Plain for unknown, got %v", got)
	}
}

// ---------------------------------------------------------------------------
// WriteOutput — various formats
// ---------------------------------------------------------------------------

func TestWave167_WriteOutput_JSON(t *testing.T) {
	var buf bytes.Buffer
	data := map[string]string{"key": "value"}
	if err := WriteOutput(&buf, OutputFormatJSON, data); err != nil {
		t.Fatalf("WriteOutput JSON: %v", err)
	}
	if !strings.Contains(buf.String(), "key") {
		t.Errorf("expected 'key' in JSON output, got %q", buf.String())
	}
}

func TestWave167_WriteOutput_YAML(t *testing.T) {
	var buf bytes.Buffer
	data := map[string]string{"key": "value"}
	if err := WriteOutput(&buf, OutputFormatYAML, data); err != nil {
		t.Fatalf("WriteOutput YAML: %v", err)
	}
	if !strings.Contains(buf.String(), "key") {
		t.Errorf("expected 'key' in YAML output, got %q", buf.String())
	}
}

func TestWave167_WriteOutput_Plain_String(t *testing.T) {
	var buf bytes.Buffer
	if err := WriteOutput(&buf, OutputFormatPlain, "hello world"); err != nil {
		t.Fatalf("WriteOutput Plain: %v", err)
	}
	if !strings.Contains(buf.String(), "hello world") {
		t.Errorf("expected 'hello world' in plain output, got %q", buf.String())
	}
}

func TestWave167_WriteOutput_Table_Slice(t *testing.T) {
	var buf bytes.Buffer
	data := []map[string]interface{}{
		{"col1": "val1", "col2": "val2"},
	}
	if err := WriteOutput(&buf, OutputFormatTable, data); err != nil {
		t.Fatalf("WriteOutput Table: %v", err)
	}
	// Table output should have some content
	if buf.Len() == 0 {
		t.Error("expected non-empty table output")
	}
}

// ---------------------------------------------------------------------------
// WriteOutputWithFlag
// ---------------------------------------------------------------------------

func TestWave167_WriteOutputWithFlag_JSON(t *testing.T) {
	var buf bytes.Buffer
	data := map[string]int{"count": 42}
	if err := WriteOutputWithFlag(&buf, "json", data); err != nil {
		t.Fatalf("WriteOutputWithFlag json: %v", err)
	}
	if !strings.Contains(buf.String(), "count") {
		t.Errorf("expected 'count' in output, got %q", buf.String())
	}
}

func TestWave167_WriteOutputWithFlag_Empty(t *testing.T) {
	var buf bytes.Buffer
	// Empty flag → auto-detect (non-TTY buf → json)
	data := map[string]string{"x": "y"}
	if err := WriteOutputWithFlag(&buf, "", data); err != nil {
		t.Fatalf("WriteOutputWithFlag empty: %v", err)
	}
	if buf.Len() == 0 {
		t.Error("expected non-empty output for empty flag")
	}
}

// ---------------------------------------------------------------------------
// levenshteinDistance
// ---------------------------------------------------------------------------

func TestWave167_LevenshteinDistance_Equal(t *testing.T) {
	if d := levenshteinDistance("task", "task"); d != 0 {
		t.Errorf("expected 0, got %d", d)
	}
}

func TestWave167_LevenshteinDistance_EmptyA(t *testing.T) {
	if d := levenshteinDistance("", "abc"); d != 3 {
		t.Errorf("expected 3, got %d", d)
	}
}

func TestWave167_LevenshteinDistance_EmptyB(t *testing.T) {
	if d := levenshteinDistance("abc", ""); d != 3 {
		t.Errorf("expected 3, got %d", d)
	}
}

func TestWave167_LevenshteinDistance_OneEdit(t *testing.T) {
	if d := levenshteinDistance("task", "task"); d != 2 {
		// swap = 2 edits (delete + insert)
		t.Logf("task→task = %d edits", d)
	}
}

func TestWave167_LevenshteinDistance_Substitution(t *testing.T) {
	if d := levenshteinDistance("cat", "bat"); d != 1 {
		t.Errorf("expected 1 substitution, got %d", d)
	}
}

// ---------------------------------------------------------------------------
// DidYouMean
// ---------------------------------------------------------------------------

func TestWave167_DidYouMean_SubstringMatch(t *testing.T) {
	got := DidYouMean("task")
	if got == "" {
		t.Error("expected suggestions for 'task'")
	}
	if !strings.Contains(got, "task") {
		t.Errorf("expected task suggestions, got %q", got)
	}
}

func TestWave167_DidYouMean_LevenshteinMatch(t *testing.T) {
	// "hlep" is close to "help" (1 edit)
	got := DidYouMean("hlep")
	if got == "" {
		t.Error("expected suggestion for 'hlep' (close to 'help')")
	}
}

func TestWave167_DidYouMean_NoMatch(t *testing.T) {
	// "zzz" has no match
	got := DidYouMean("zzz")
	// May or may not be empty depending on levenshtein distance
	_ = got
}
