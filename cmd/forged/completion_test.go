//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestCompletionManagerGenerateBash(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer

	if err := m.GenerateBash(&buf); err != nil {
		t.Fatalf("GenerateBash error: %v", err)
	}
	out := buf.String()
	if out == "" {
		t.Fatal("GenerateBash produced empty output")
	}
	if !strings.Contains(out, "_forge_completion") || !strings.Contains(out, "complete -F _forge_completion forge") {
		t.Fatalf("GenerateBash output missing expected content:\n%s", out)
	}
}

func TestCompletionManagerGenerateZsh(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer

	if err := m.GenerateZsh(&buf); err != nil {
		t.Fatalf("GenerateZsh error: %v", err)
	}
	out := buf.String()
	if out == "" {
		t.Fatal("GenerateZsh produced empty output")
	}
	if !strings.Contains(out, "#compdef forge") || !strings.Contains(out, "_forge_nouns") {
		t.Fatalf("GenerateZsh output missing expected content:\n%s", out)
	}
}

func TestCompletionManagerGenerateFish(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer

	if err := m.GenerateFish(&buf); err != nil {
		t.Fatalf("GenerateFish error: %v", err)
	}
	out := buf.String()
	if out == "" {
		t.Fatal("GenerateFish produced empty output")
	}
	if !strings.Contains(out, "complete -c forge") {
		t.Fatalf("GenerateFish output missing expected content:\n%s", out)
	}
}

