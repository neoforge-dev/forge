//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"testing"
)

// --- OutputFormatter.Name() ---

func TestJSONFormatterName(t *testing.T) {
	if got := (JSONFormatter{}).Name(); got != OutputFormatJSON {
		t.Errorf("JSONFormatter.Name() = %q, want %q", got, OutputFormatJSON)
	}
}

func TestYAMLFormatterName(t *testing.T) {
	if got := (YAMLFormatter{}).Name(); got != OutputFormatYAML {
		t.Errorf("YAMLFormatter.Name() = %q, want %q", got, OutputFormatYAML)
	}
}

func TestTableFormatterName(t *testing.T) {
	if got := (TableFormatter{}).Name(); got != OutputFormatTable {
		t.Errorf("TableFormatter.Name() = %q, want %q", got, OutputFormatTable)
	}
}

func TestPlainFormatterName(t *testing.T) {
	if got := (PlainFormatter{}).Name(); got != OutputFormatPlain {
		t.Errorf("PlainFormatter.Name() = %q, want %q", got, OutputFormatPlain)
	}
}

// --- writePlain branches ---

func TestWritePlain_Nil(t *testing.T) {
	var buf bytes.Buffer
	if err := writePlain(&buf, nil); err != nil {
		t.Fatalf("writePlain(nil): %v", err)
	}
	if buf.String() != "(no data)\n" {
		t.Errorf("writePlain(nil) = %q, want \"(no data)\\n\"", buf.String())
	}
}

func TestWritePlain_ByteSlice(t *testing.T) {
	var buf bytes.Buffer
	payload := []byte("raw bytes here")
	if err := writePlain(&buf, payload); err != nil {
		t.Fatalf("writePlain([]byte): %v", err)
	}
	if !bytes.Equal(buf.Bytes(), payload) {
		t.Errorf("writePlain([]byte) = %q, want %q", buf.Bytes(), payload)
	}
}

// stringer implements fmt.Stringer for testing.
type stringer struct{ val string }

func (s stringer) String() string { return s.val }

func TestWritePlain_Stringer(t *testing.T) {
	var buf bytes.Buffer
	if err := writePlain(&buf, stringer{"hello stringer"}); err != nil {
		t.Fatalf("writePlain(Stringer): %v", err)
	}
	if buf.String() != "hello stringer\n" {
		t.Errorf("writePlain(Stringer) = %q, want %q", buf.String(), "hello stringer\n")
	}
}

func TestWritePlain_ComplexFallbackToJSON(t *testing.T) {
	var buf bytes.Buffer
	data := map[string]int{"x": 42}
	if err := writePlain(&buf, data); err != nil {
		t.Fatalf("writePlain(map): %v", err)
	}
	if buf.Len() == 0 {
		t.Error("writePlain(map) produced empty output; expected JSON fallback")
	}
}

// --- WriteOutput dispatch for all formats ---

func TestWriteOutput_YAML_Extra(t *testing.T) {
	var buf bytes.Buffer
	data := map[string]string{"key": "val"}
	if err := WriteOutput(&buf, OutputFormatYAML, data); err != nil {
		t.Fatalf("WriteOutput YAML: %v", err)
	}
	if buf.Len() == 0 {
		t.Error("WriteOutput YAML produced empty output")
	}
}

func TestWriteOutput_Table(t *testing.T) {
	type row struct{ ID int }
	var buf bytes.Buffer
	if err := WriteOutput(&buf, OutputFormatTable, []row{{1}, {2}}); err != nil {
		t.Fatalf("WriteOutput Table: %v", err)
	}
	if buf.Len() == 0 {
		t.Error("WriteOutput Table produced empty output")
	}
}

func TestWriteOutput_Plain(t *testing.T) {
	var buf bytes.Buffer
	if err := WriteOutput(&buf, OutputFormatPlain, "hello plain"); err != nil {
		t.Fatalf("WriteOutput Plain: %v", err)
	}
	if buf.String() != "hello plain\n" {
		t.Errorf("WriteOutput Plain = %q, want \"hello plain\\n\"", buf.String())
	}
}

func TestWriteOutput_Default(t *testing.T) {
	var buf bytes.Buffer
	// Unknown format should fall through to PlainFormatter.
	if err := WriteOutput(&buf, OutputFormat("unknown"), "test"); err != nil {
		t.Fatalf("WriteOutput unknown format: %v", err)
	}
	if buf.Len() == 0 {
		t.Error("WriteOutput unknown format produced empty output")
	}
}

// --- writeTable edge cases ---

func TestWriteTable_EmptySlice(t *testing.T) {
	type row struct{ ID int }
	var buf bytes.Buffer
	if err := writeTable(&buf, []row{}); err != nil {
		t.Fatalf("writeTable empty slice: %v", err)
	}
	if buf.String() != "(no rows)\n" {
		t.Errorf("writeTable empty = %q, want \"(no rows)\\n\"", buf.String())
	}
}

func TestWriteTable_NonSliceFallsBackToPlain(t *testing.T) {
	var buf bytes.Buffer
	if err := writeTable(&buf, "not a slice"); err != nil {
		t.Fatalf("writeTable non-slice: %v", err)
	}
	if buf.Len() == 0 {
		t.Error("writeTable non-slice produced empty output; expected plain fallback")
	}
}

func TestWriteTable_MapSlice(t *testing.T) {
	var buf bytes.Buffer
	rows := []map[string]string{
		{"name": "alice", "role": "admin"},
		{"name": "bob", "role": "user"},
	}
	if err := writeTable(&buf, rows); err != nil {
		t.Fatalf("writeTable map slice: %v", err)
	}
	out := buf.String()
	if out == "" {
		t.Fatal("writeTable map slice produced empty output")
	}
	if !bytes.Contains([]byte(out), []byte("name")) || !bytes.Contains([]byte(out), []byte("role")) {
		t.Errorf("writeTable map slice output missing headers: %q", out)
	}
}

func TestWriteTable_PointerSlice(t *testing.T) {
	type row struct {
		Name  string
		Score int
	}
	var buf bytes.Buffer
	rows := []*row{
		{Name: "alpha", Score: 10},
		{Name: "beta", Score: 20},
	}
	if err := writeTable(&buf, rows); err != nil {
		t.Fatalf("writeTable pointer slice: %v", err)
	}
	out := buf.String()
	if !bytes.Contains([]byte(out), []byte("alpha")) {
		t.Errorf("writeTable pointer slice output missing value: %q", out)
	}
}

// --- DetectOutputFormat with TTY branch ---

func TestDetectOutputFormat_WithFlag_YAML(t *testing.T) {
	var buf bytes.Buffer
	got := DetectOutputFormat("yaml", &buf)
	if got != OutputFormatYAML {
		t.Errorf("DetectOutputFormat(yaml) = %q, want yaml", got)
	}
}

func TestDetectOutputFormat_EmptyFlagNonTTY(t *testing.T) {
	var buf bytes.Buffer
	got := DetectOutputFormat("", &buf)
	if got != OutputFormatJSON {
		t.Errorf("DetectOutputFormat empty non-tty = %q, want json", got)
	}
}
