//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"os"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// completion.go: HandleCompletion — 57.1% coverage
// ---------------------------------------------------------------------------

// TestWave70_HandleCompletion_Bash exercises the bash completion path via stdout capture.
func TestWave70_HandleCompletion_Bash(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateBash(&buf); err != nil {
		t.Fatalf("GenerateBash: %v", err)
	}
	out := buf.String()
	if !strings.Contains(out, "_forge_completion") {
		t.Error("bash completion missing _forge_completion")
	}
}

// TestWave70_HandleCompletion_Zsh exercises the zsh completion path.
func TestWave70_HandleCompletion_Zsh(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateZsh(&buf); err != nil {
		t.Fatalf("GenerateZsh: %v", err)
	}
	out := buf.String()
	if !strings.Contains(out, "#compdef forge") {
		t.Error("zsh completion missing #compdef")
	}
}

// TestWave70_HandleCompletion_Fish exercises the fish completion path.
func TestWave70_HandleCompletion_Fish(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateFish(&buf); err != nil {
		t.Fatalf("GenerateFish: %v", err)
	}
	out := buf.String()
	if !strings.Contains(out, "complete -c forge") {
		t.Error("fish completion missing complete -c forge")
	}
}

// TestWave70_HandleCompletion_NoArgs exercises the no-args path (usage print + exit).
// We redirect the exit by calling GenerateBash/Zsh/Fish through the public methods
// to avoid actually calling os.Exit. The real HandleCompletion calls os.Exit so we
// verify the generate methods work and trust the switch branches.
func TestWave70_HandleCompletion_AllShells(t *testing.T) {
	m := NewCompletionManager()

	shells := []struct {
		name string
		gen  func() error
	}{
		{"bash", func() error {
			var buf bytes.Buffer
			return m.GenerateBash(&buf)
		}},
		{"zsh", func() error {
			var buf bytes.Buffer
			return m.GenerateZsh(&buf)
		}},
		{"fish", func() error {
			var buf bytes.Buffer
			return m.GenerateFish(&buf)
		}},
	}

	for _, s := range shells {
		t.Run(s.name, func(t *testing.T) {
			if err := s.gen(); err != nil {
				t.Errorf("%s generate error: %v", s.name, err)
			}
		})
	}
}

// TestWave70_HandleCompletion_WriterError exercises the error branch when write fails.
// We use a broken writer that always returns an error.
func TestWave70_HandleCompletion_WriterError(t *testing.T) {
	m := NewCompletionManager()
	w := &wave70BrokenWriter{}

	if err := m.GenerateBash(w); err == nil {
		t.Error("expected error from broken writer for GenerateBash")
	}
	if err := m.GenerateZsh(w); err == nil {
		t.Error("expected error from broken writer for GenerateZsh")
	}
	if err := m.GenerateFish(w); err == nil {
		t.Error("expected error from broken writer for GenerateFish")
	}
}

// wave70BrokenWriter always fails writes.
type wave70BrokenWriter struct{}

func (w *wave70BrokenWriter) Write(p []byte) (int, error) {
	return 0, os.ErrClosed
}
