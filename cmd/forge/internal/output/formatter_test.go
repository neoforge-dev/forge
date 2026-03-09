package output

import (
	"bytes"
	"testing"
)

func TestNew(t *testing.T) {
	w := &bytes.Buffer{}
	f, err := New("json", w)
	if err != nil || f == nil {
		t.Fatalf("New(json): err=%v", err)
	}
	f, err = New("csv", w)
	if err != nil || f == nil {
		t.Fatalf("New(csv): err=%v", err)
	}
	f, err = New("table", w)
	if err != nil || f == nil {
		t.Fatalf("New(table): err=%v", err)
	}
	f, err = New("quiet", w)
	if err != nil || f == nil {
		t.Fatalf("New(quiet): err=%v", err)
	}
	_, err = New("invalid", w)
	if err == nil {
		t.Error("New(invalid) should error")
	}
}

func TestAutoFormat(t *testing.T) {
	s := AutoFormat()
	if s != "json" && s != "table" {
		t.Errorf("AutoFormat() = %q", s)
	}
}

func TestJSONFormatter_Format(t *testing.T) {
	w := &bytes.Buffer{}
	f := &JSONFormatter{w: w}
	err := f.Format(map[string]string{"a": "b"})
	if err != nil {
		t.Fatalf("Format: %v", err)
	}
	if w.Len() == 0 {
		t.Error("expected JSON output")
	}
}

func TestCSVFormatter_Format(t *testing.T) {
	w := &bytes.Buffer{}
	f := &CSVFormatter{w: w}
	type row struct {
		A string
		B int
	}
	err := f.Format([]row{{"x", 1}, {"y", 2}})
	if err != nil {
		t.Fatalf("Format: %v", err)
	}
	if w.Len() == 0 {
		t.Error("expected CSV output")
	}
}

func TestCSVFormatter_FormatEmptySlice(t *testing.T) {
	w := &bytes.Buffer{}
	f := &CSVFormatter{w: w}
	err := f.Format([]struct{}{})
	if err != nil {
		t.Fatalf("Format: %v", err)
	}
}

func TestTableFormatter_Format(t *testing.T) {
	w := &bytes.Buffer{}
	f := &TableFormatter{w: w}
	err := f.Format("hello")
	if err != nil {
		t.Fatalf("Format: %v", err)
	}
	if w.Len() == 0 {
		t.Error("expected table output")
	}
}

func TestQuietFormatter_Format(t *testing.T) {
	f := &QuietFormatter{}
	err := f.Format(nil)
	if err != nil {
		t.Fatalf("Format: %v", err)
	}
}
