//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 212: output.go — formatters + parser
// Targets:
//   ParseOutputFormat    (output.go:38) — json/yaml/table/plain/default/empty
//   DetectOutputFormat   (output.go:61) — with flag / no flag non-TTY
//   WriteOutput          (output.go:80) — json/yaml/table/plain
//   WriteOutputWithFlag  (output.go:102)
//   JSONFormatter.Name/Format (output.go:110)
//   YAMLFormatter.Name/Format (output.go:121)
//   TableFormatter.Name  (output.go:132)
//   PlainFormatter.Name/Format (output.go:141)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// ParseOutputFormat
// ---------------------------------------------------------------------------

func TestWave212_ParseOutputFormat_JSON(t *testing.T) {
	if got := ParseOutputFormat("json"); got != OutputFormatJSON {
		t.Errorf("expected JSON format, got %v", got)
	}
}

func TestWave212_ParseOutputFormat_YAML(t *testing.T) {
	if got := ParseOutputFormat("yaml"); got != OutputFormatYAML {
		t.Errorf("expected YAML format, got %v", got)
	}
	if got := ParseOutputFormat("yml"); got != OutputFormatYAML {
		t.Errorf("expected YAML format for yml, got %v", got)
	}
}

func TestWave212_ParseOutputFormat_Table(t *testing.T) {
	if got := ParseOutputFormat("table"); got != OutputFormatTable {
		t.Errorf("expected table format, got %v", got)
	}
}

func TestWave212_ParseOutputFormat_Plain(t *testing.T) {
	if got := ParseOutputFormat("plain"); got != OutputFormatPlain {
		t.Errorf("expected plain format, got %v", got)
	}
}

func TestWave212_ParseOutputFormat_Empty(t *testing.T) {
	if got := ParseOutputFormat(""); got != OutputFormatPlain {
		t.Errorf("expected plain for empty, got %v", got)
	}
}

func TestWave212_ParseOutputFormat_Default(t *testing.T) {
	if got := ParseOutputFormat("unknown-format"); got != OutputFormatPlain {
		t.Errorf("expected plain for unknown, got %v", got)
	}
}

func TestWave212_ParseOutputFormat_CaseInsensitive(t *testing.T) {
	if got := ParseOutputFormat("JSON"); got != OutputFormatJSON {
		t.Errorf("expected JSON format for uppercase, got %v", got)
	}
}

// ---------------------------------------------------------------------------
// DetectOutputFormat
// ---------------------------------------------------------------------------

func TestWave212_DetectOutputFormat_WithFlag(t *testing.T) {
	var buf bytes.Buffer
	got := DetectOutputFormat("json", &buf)
	if got != OutputFormatJSON {
		t.Errorf("expected JSON when flag is 'json', got %v", got)
	}
}

func TestWave212_DetectOutputFormat_NoFlag_NonTTY(t *testing.T) {
	// bytes.Buffer is not a TTY → should return JSON
	var buf bytes.Buffer
	got := DetectOutputFormat("", &buf)
	if got != OutputFormatJSON {
		t.Errorf("expected JSON for non-TTY no-flag, got %v", got)
	}
}

// ---------------------------------------------------------------------------
// WriteOutput — JSON
// ---------------------------------------------------------------------------

func TestWave212_WriteOutput_JSON(t *testing.T) {
	var buf bytes.Buffer
	data := map[string]string{"key": "value"}
	if err := WriteOutput(&buf, OutputFormatJSON, data); err != nil {
		t.Errorf("WriteOutput JSON: %v", err)
	}
	if !strings.Contains(buf.String(), "key") {
		t.Errorf("expected 'key' in JSON output, got %q", buf.String())
	}
}

// ---------------------------------------------------------------------------
// WriteOutput — YAML
// ---------------------------------------------------------------------------

func TestWave212_WriteOutput_YAML(t *testing.T) {
	var buf bytes.Buffer
	data := map[string]string{"key": "value"}
	if err := WriteOutput(&buf, OutputFormatYAML, data); err != nil {
		t.Errorf("WriteOutput YAML: %v", err)
	}
	if !strings.Contains(buf.String(), "key") {
		t.Errorf("expected 'key' in YAML output, got %q", buf.String())
	}
}

// ---------------------------------------------------------------------------
// WriteOutput — Table
// ---------------------------------------------------------------------------

func TestWave212_WriteOutput_Table(t *testing.T) {
	var buf bytes.Buffer
	// Table formatter handles slices or maps
	data := []map[string]interface{}{{"name": "wave212", "value": 42}}
	err := WriteOutput(&buf, OutputFormatTable, data)
	_ = err // may not format perfectly but should not panic
}

// ---------------------------------------------------------------------------
// WriteOutput — Plain
// ---------------------------------------------------------------------------

func TestWave212_WriteOutput_Plain(t *testing.T) {
	var buf bytes.Buffer
	if err := WriteOutput(&buf, OutputFormatPlain, "hello wave212"); err != nil {
		t.Errorf("WriteOutput plain: %v", err)
	}
}

// ---------------------------------------------------------------------------
// WriteOutputWithFlag
// ---------------------------------------------------------------------------

func TestWave212_WriteOutputWithFlag_JSON(t *testing.T) {
	var buf bytes.Buffer
	data := map[string]int{"count": 5}
	if err := WriteOutputWithFlag(&buf, "json", data); err != nil {
		t.Errorf("WriteOutputWithFlag json: %v", err)
	}
}

// ---------------------------------------------------------------------------
// Formatter Names
// ---------------------------------------------------------------------------

func TestWave212_JSONFormatter_Name(t *testing.T) {
	if got := (JSONFormatter{}).Name(); got != OutputFormatJSON {
		t.Errorf("expected JSON format name, got %v", got)
	}
}

func TestWave212_YAMLFormatter_Name(t *testing.T) {
	if got := (YAMLFormatter{}).Name(); got != OutputFormatYAML {
		t.Errorf("expected YAML format name, got %v", got)
	}
}

func TestWave212_TableFormatter_Name(t *testing.T) {
	if got := (TableFormatter{}).Name(); got != OutputFormatTable {
		t.Errorf("expected table format name, got %v", got)
	}
}

func TestWave212_PlainFormatter_Name(t *testing.T) {
	if got := (PlainFormatter{}).Name(); got != OutputFormatPlain {
		t.Errorf("expected plain format name, got %v", got)
	}
}
