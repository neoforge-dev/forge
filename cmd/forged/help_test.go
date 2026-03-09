//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"os"
	"strings"
	"testing"
)

// captureStdout runs fn while capturing anything written to os.Stdout.
func captureStdout(t *testing.T, fn func()) string {
	t.Helper()

	orig := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("os.Pipe: %v", err)
	}
	os.Stdout = w

	outCh := make(chan string, 1)
	go func() {
		var buf bytes.Buffer
		_, _ = buf.ReadFrom(r)
		outCh <- buf.String()
	}()

	fn()

	_ = w.Close()
	os.Stdout = orig

	return <-outCh
}

func TestRunHelpRoot(t *testing.T) {
	out := captureStdout(t, func() {
		RunHelp(nil)
	})
	if !strings.Contains(out, "FORGE - Fleet Orchestration") {
		t.Fatalf("root help output missing banner, got:\n%s", out)
	}
}

func TestRunHelpTaskTopic(t *testing.T) {
	out := captureStdout(t, func() {
		RunHelp([]string{"task"})
	})
	if !strings.Contains(out, "Manage tasks in the FORGE system") {
		t.Fatalf("task help output missing expected text, got:\n%s", out)
	}
}

func TestHelpSystemShowVerbHelp(t *testing.T) {
	hs := NewHelpSystem()
	out := captureStdout(t, func() {
		hs.ShowVerbHelp("task", "create")
	})
	if !strings.Contains(out, "Create a new task") {
		t.Fatalf("verb help output missing expected text, got:\n%s", out)
	}
}

func TestDidYouMeanSuggestions(t *testing.T) {
	tests := []struct {
		in      string
		wantSub string
	}{
		{"tas", "task list"},
		{"fleat", "fleet status"},
		{"patrl", "system patrol"},
	}

	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			got := DidYouMean(tt.in)
			if tt.wantSub != "" && !strings.Contains(got, tt.wantSub) {
				t.Fatalf("DidYouMean(%q) = %q, want substring %q", tt.in, got, tt.wantSub)
			}
		})
	}
}

