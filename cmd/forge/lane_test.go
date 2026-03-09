package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/neoforge-dev/forge/internal"
)

func TestLaneListWithMock(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/lanes" {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(internal.LaneListResponse{
				Lanes: []internal.Lane{
					{ID: "dev", Name: "Development"},
					{ID: "prod", Name: "Production"},
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

	output, err := runner.Execute("lane", "list")
	if err != nil {
		t.Fatalf("lane list failed: %v", err)
	}

	if !strings.Contains(output, "dev") || !strings.Contains(output, "prod") {
		t.Errorf("expected output to contain lane names, got: %s", output)
	}
}

func TestLaneShowWithMock(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/lanes/dev" {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(internal.Lane{
				ID:   "dev",
				Name: "Development",
			})
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	t.Setenv("FORGE_API_URL", server.URL)
	runner := NewTestRunner(t)

	output, err := runner.Execute("lane", "show", "dev")
	if err != nil {
		t.Fatalf("lane show dev failed: %v", err)
	}

	if !strings.Contains(output, "dev") {
		t.Errorf("expected output to contain 'dev', got: %s", output)
	}
}

func TestLanePromoteWithMock(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/api/tasks/") && strings.HasSuffix(r.URL.Path, "/promote") && r.Method == http.MethodPost {
			w.WriteHeader(http.StatusOK)
			w.Write([]byte("{}"))
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	t.Setenv("FORGE_API_URL", server.URL)
	runner := NewTestRunner(t)

	output, err := runner.Execute("lane", "promote", "task-1", "--to", "test")
	if err != nil {
		t.Fatalf("lane promote task-1 --to test failed: %v", err)
	}

	if !strings.Contains(output, "promoted successfully") {
		t.Errorf("expected output to contain success message, got: %s", output)
	}
}

func TestLaneListEmptyWithMock(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/lanes" {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(internal.LaneListResponse{
				Lanes: []internal.Lane{},
				Count: 0,
			})
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	t.Setenv("FORGE_API_URL", server.URL)
	runner := NewTestRunner(t)

	output, err := runner.Execute("lane", "list")
	if err != nil {
		t.Fatalf("lane list failed: %v", err)
	}

	if !strings.Contains(output, "0 lanes") {
		t.Errorf("expected output to contain '0 lanes', got: %s", output)
	}
}

// TestLanePromoteArgs tests that promote requires arguments
func TestLanePromoteArgs(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("lane", "promote")
	if err == nil {
		t.Error("expected error for missing task ID")
	}
}

// TestLaneShowArgs tests that show requires arguments
func TestLaneShowArgs(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("lane", "show")
	if err == nil {
		t.Error("expected error for missing lane key")
	}
}
