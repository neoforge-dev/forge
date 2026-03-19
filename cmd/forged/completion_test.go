//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"os"
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

// TestHandleCompletion_BashRoute exercises the HandleCompletion bash branch.
// It redirects os.Stdout to capture the output without calling os.Exit.
func TestHandleCompletion_BashRoute(t *testing.T) {
	m := NewCompletionManager()

	// Capture stdout since HandleCompletion writes to os.Stdout.
	old := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	os.Stdout = w

	// Recover from os.Exit if it fires (should not for valid shell).
	defer func() {
		w.Close()
		os.Stdout = old
	}()

	m.HandleCompletion([]string{"bash"})
	w.Close()
	os.Stdout = old

	var buf bytes.Buffer
	buf.ReadFrom(r)
	if !strings.Contains(buf.String(), "_forge_completion") {
		t.Errorf("expected bash completion script in output, got: %s", buf.String())
	}
}

// TestHandleCompletion_ZshRoute exercises the zsh branch.
func TestHandleCompletion_ZshRoute(t *testing.T) {
	m := NewCompletionManager()

	old := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	os.Stdout = w

	defer func() {
		w.Close()
		os.Stdout = old
	}()

	m.HandleCompletion([]string{"zsh"})
	w.Close()
	os.Stdout = old

	var buf bytes.Buffer
	buf.ReadFrom(r)
	if !strings.Contains(buf.String(), "#compdef forge") {
		t.Errorf("expected zsh completion script in output, got: %s", buf.String())
	}
}

// TestHandleCompletion_FishRoute exercises the fish branch.
func TestHandleCompletion_FishRoute(t *testing.T) {
	m := NewCompletionManager()

	old := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	os.Stdout = w

	defer func() {
		w.Close()
		os.Stdout = old
	}()

	m.HandleCompletion([]string{"fish"})
	w.Close()
	os.Stdout = old

	var buf bytes.Buffer
	buf.ReadFrom(r)
	if !strings.Contains(buf.String(), "complete -c forge") {
		t.Errorf("expected fish completion script in output, got: %s", buf.String())
	}
}

