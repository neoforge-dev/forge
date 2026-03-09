//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestParseOutputFormat(t *testing.T) {
	tests := []struct {
		name string
		in   string
		want OutputFormat
	}{
		{"empty defaults to plain", "", OutputFormatPlain},
		{"json", "json", OutputFormatJSON},
		{"yaml", "yaml", OutputFormatYAML},
		{"yml alias", "yml", OutputFormatYAML},
		{"table", "table", OutputFormatTable},
		{"plain", "plain", OutputFormatPlain},
		{"unknown falls back to plain", "xml", OutputFormatPlain},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := ParseOutputFormat(tt.in); got != tt.want {
				t.Fatalf("ParseOutputFormat(%q) = %q, want %q", tt.in, got, tt.want)
			}
		})
	}
}

func TestDetectOutputFormat_RespectsFlag(t *testing.T) {
	var buf bytes.Buffer
	got := DetectOutputFormat("json", &buf)
	if got != OutputFormatJSON {
		t.Fatalf("DetectOutputFormat with flag json = %q, want %q", got, OutputFormatJSON)
	}
}

func TestDetectOutputFormat_DefaultsToJSONForNonTTY(t *testing.T) {
	// bytes.Buffer is never a TTY, so DetectOutputFormat should choose JSON
	var buf bytes.Buffer
	got := DetectOutputFormat("", &buf)
	if got != OutputFormatJSON {
		t.Fatalf("DetectOutputFormat (non-tty, no flag) = %q, want %q", got, OutputFormatJSON)
	}
}

func TestJSONFormatter(t *testing.T) {
	data := map[string]string{"foo": "bar"}
	var buf bytes.Buffer

	if err := (JSONFormatter{}).Format(&buf, data); err != nil {
		t.Fatalf("JSONFormatter.Format error: %v", err)
	}

	var decoded map[string]string
	if err := json.Unmarshal(buf.Bytes(), &decoded); err != nil {
		t.Fatalf("JSON output is not valid JSON: %v\n%s", err, buf.String())
	}
	if decoded["foo"] != "bar" {
		t.Fatalf("decoded[\"foo\"] = %q, want %q", decoded["foo"], "bar")
	}
}

func TestYAMLFormatter(t *testing.T) {
	data := struct {
		Name string `yaml:"name"`
	}{Name: "forge"}

	var buf bytes.Buffer
	if err := (YAMLFormatter{}).Format(&buf, data); err != nil {
		t.Fatalf("YAMLFormatter.Format error: %v", err)
	}
	if got := buf.String(); got == "" {
		t.Fatal("YAMLFormatter produced empty output")
	}
	if !bytes.Contains(buf.Bytes(), []byte("forge")) {
		t.Fatalf("YAMLFormatter output %q does not contain expected value", buf.String())
	}
}

func TestTableFormatter_WithStructs(t *testing.T) {
	type row struct {
		ID   int
		Name string
	}

	rows := []row{
		{ID: 1, Name: "alpha"},
		{ID: 2, Name: "beta"},
	}

	var buf bytes.Buffer
	if err := (TableFormatter{}).Format(&buf, rows); err != nil {
		t.Fatalf("TableFormatter.Format error: %v", err)
	}

	out := buf.String()
	if out == "" {
		t.Fatal("TableFormatter produced empty output")
	}
	if !bytes.Contains([]byte(out), []byte("ID")) || !bytes.Contains([]byte(out), []byte("Name")) {
		t.Fatalf("TableFormatter output missing headers: %q", out)
	}
	if !bytes.Contains([]byte(out), []byte("alpha")) || !bytes.Contains([]byte(out), []byte("beta")) {
		t.Fatalf("TableFormatter output missing row values: %q", out)
	}
}

func TestPlainFormatter_BasicTypes(t *testing.T) {
	var buf bytes.Buffer
	if err := (PlainFormatter{}).Format(&buf, "hello"); err != nil {
		t.Fatalf("PlainFormatter.Format error: %v", err)
	}
	if got := buf.String(); got != "hello\n" {
		t.Fatalf("PlainFormatter output = %q, want %q", got, "hello\n")
	}
}

func TestWriteOutputWithFlag_UsesDetectedFormat(t *testing.T) {
	var buf bytes.Buffer
	data := map[string]string{"k": "v"}

	if err := WriteOutputWithFlag(&buf, "json", data); err != nil {
		t.Fatalf("WriteOutputWithFlag error: %v", err)
	}

	var decoded map[string]string
	if err := json.Unmarshal(buf.Bytes(), &decoded); err != nil {
		t.Fatalf("WriteOutputWithFlag did not write valid JSON: %v", err)
	}
	if decoded["k"] != "v" {
		t.Fatalf("decoded[\"k\"] = %q, want %q", decoded["k"], "v")
	}
}

