package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/neoforge-dev/forge/internal"
)

func TestContextListWithMock(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/contexts" {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(internal.ContextListResponse{
				Contexts: []internal.Context{
					{Key: "ctx-1", Domain: "is", Project: "proj"},
					{Key: "ctx-2", Domain: "as", Project: "proj2"},
				},
				Count: 2,
			})
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	t.Setenv("FORGE_API_URL", server.URL)
	runner := NewTestRunner(t)

	output, err := runner.Execute("context", "list")
	if err != nil {
		t.Fatalf("context list failed: %v", err)
	}

	if !strings.Contains(output, "ctx-1") || !strings.Contains(output, "ctx-2") {
		t.Errorf("expected output to contain context keys, got: %s", output)
	}
}

func TestContextShowWithMock(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/contexts/ctx-1" {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(internal.Context{
				Key:    "ctx-1",
				Domain: "is",
			})
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	t.Setenv("FORGE_API_URL", server.URL)
	runner := NewTestRunner(t)

	output, err := runner.Execute("context", "show", "ctx-1")
	if err != nil {
		t.Fatalf("context show ctx-1 failed: %v", err)
	}

	if !strings.Contains(output, "is") {
		t.Errorf("expected output to contain 'is' (domain), got: %s", output)
	}
}

func TestContextListEmptyWithMock(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/contexts" {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(internal.ContextListResponse{
				Contexts: []internal.Context{},
				Count:    0,
			})
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	t.Setenv("FORGE_API_URL", server.URL)
	runner := NewTestRunner(t)

	_, err := runner.Execute("context", "list")
	if err != nil {
		t.Fatalf("context list failed: %v", err)
	}
}

// TestContextCommand tests context command
func TestContextCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("context")
	if err != nil {
		t.Logf("context: %v", err)
	}
}

// TestContextList tests context list command
func TestContextList(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("context", "list")
	if err != nil {
		t.Logf("context list: %v", err)
	}
}

// TestContextShow tests context show command
func TestContextShow(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("context", "show", "test-context")
	if err != nil {
		t.Logf("context show: %v", err)
	}
}

// TestContextShowArgs tests that show requires arguments
func TestContextShowArgs(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("context", "show")
	if err == nil {
		t.Error("expected error for missing context key")
	}
}

// --- buildBar tests ----------------------------------------------------------

func TestBuildBar(t *testing.T) {
	cases := []struct {
		pct  float64
		n    int
		want string
	}{
		{0, 10, "░░░░░░░░░░"},
		{100, 10, "██████████"},
		{50, 10, "█████░░░░░"},
		{30, 10, "███░░░░░░░"},
		{0, 0, ""},
	}
	for _, tc := range cases {
		got := buildBar(tc.pct, tc.n)
		if got != tc.want {
			t.Errorf("buildBar(%v, %d) = %q, want %q", tc.pct, tc.n, got, tc.want)
		}
	}
}
