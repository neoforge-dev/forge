package output

import (
	"strings"
	"testing"
)

func TestPrintError(t *testing.T) {
	// Just ensure no panic; we can't easily capture stderr
	PrintError("test error")
}

func TestPrintWarning(t *testing.T) {
	PrintWarning("test warning")
}

func TestPrintSuccess(t *testing.T) {
	PrintSuccess("done")
}

func TestPrintInfo(t *testing.T) {
	PrintInfo("info message")
}

func TestPrintProgress(t *testing.T) {
	PrintProgress("working...")
}

func TestRed(t *testing.T) {
	s := Red("hello")
	if !strings.Contains(s, "hello") {
		t.Errorf("Red: got %q", s)
	}
}

func TestGreen(t *testing.T) {
	s := Green("ok")
	if !strings.Contains(s, "ok") {
		t.Errorf("Green: got %q", s)
	}
}

func TestYellow(t *testing.T) {
	s := Yellow("warn")
	if !strings.Contains(s, "warn") {
		t.Errorf("Yellow: got %q", s)
	}
}

func TestBlue(t *testing.T) {
	s := Blue("info")
	if !strings.Contains(s, "info") {
		t.Errorf("Blue: got %q", s)
	}
}

func TestCyan(t *testing.T) {
	s := Cyan("progress")
	if !strings.Contains(s, "progress") {
		t.Errorf("Cyan: got %q", s)
	}
}
