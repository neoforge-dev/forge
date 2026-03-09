//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// TestWave68_FetchData_HTTPError verifies that fetchData returns a dataMsg with
// an error string when the HTTP request fails (unreachable server).
func TestWave68_FetchData_HTTPError(t *testing.T) {
	m := NewDashboardModel("http://127.0.0.1:19999") // nothing listening
	m.ticker.Stop()

	cmd := m.fetchData()
	if cmd == nil {
		t.Fatal("fetchData should return a non-nil tea.Cmd")
	}

	msg := cmd()
	dm, ok := msg.(dataMsg)
	if !ok {
		t.Fatalf("expected dataMsg, got %T", msg)
	}
	if dm.Error == "" {
		t.Error("expected Error to be set when HTTP request fails")
	}
}

// TestWave68_FetchData_Dashboard200_Sprint200 verifies that fetchData decodes
// both /api/dashboard and /api/coordination/status correctly when both return 200 JSON.
func TestWave68_FetchData_Dashboard200_Sprint200(t *testing.T) {
	dashboardPayload := DashboardState{
		Summary: DashboardSummary{
			TotalAgents:  5,
			OnlineAgents: 3,
			SystemHealth: "healthy",
		},
		LastUpdated: time.Now(),
	}

	sprintPayload := SprintStatus{
		Name:       "S82",
		Completion: 72.0,
		StartedAt:  time.Now().Add(-24 * time.Hour),
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if strings.HasSuffix(r.URL.Path, "/api/dashboard") {
			json.NewEncoder(w).Encode(dashboardPayload)
		} else if strings.HasSuffix(r.URL.Path, "/api/coordination/status") {
			json.NewEncoder(w).Encode(sprintPayload)
		} else {
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	m := NewDashboardModel(srv.URL)
	m.ticker.Stop()

	cmd := m.fetchData()
	msg := cmd()
	dm, ok := msg.(dataMsg)
	if !ok {
		t.Fatalf("expected dataMsg, got %T", msg)
	}
	if dm.Error != "" {
		t.Errorf("unexpected error: %s", dm.Error)
	}
	if dm.Summary.TotalAgents != 5 {
		t.Errorf("expected TotalAgents=5, got %d", dm.Summary.TotalAgents)
	}
	if dm.Sprint == nil {
		t.Error("expected Sprint to be populated from /api/coordination/status")
	} else if dm.Sprint.Name != "S82" {
		t.Errorf("expected Sprint.Name=S82, got %s", dm.Sprint.Name)
	}
}

// TestWave68_FetchData_Dashboard200_SprintErr verifies that fetchData handles a
// non-200 or unparseable /api/coordination/status response gracefully (Sprint stays nil).
func TestWave68_FetchData_Dashboard200_SprintErr(t *testing.T) {
	dashboardPayload := DashboardState{
		Summary: DashboardSummary{TotalAgents: 2},
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/api/dashboard") {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(dashboardPayload)
		} else {
			// Return bad JSON for sprint endpoint so decode fails.
			w.WriteHeader(http.StatusOK)
			w.Write([]byte("not-json{{{"))
		}
	}))
	defer srv.Close()

	m := NewDashboardModel(srv.URL)
	m.ticker.Stop()

	cmd := m.fetchData()
	msg := cmd()
	dm, ok := msg.(dataMsg)
	if !ok {
		t.Fatalf("expected dataMsg, got %T", msg)
	}
	if dm.Error != "" {
		t.Errorf("unexpected error: %s", dm.Error)
	}
	if dm.Summary.TotalAgents != 2 {
		t.Errorf("expected TotalAgents=2, got %d", dm.Summary.TotalAgents)
	}
	// Sprint should remain nil because JSON decode failed.
	if dm.Sprint != nil {
		t.Errorf("expected Sprint=nil when /api/coordination/status returns bad JSON, got %+v", dm.Sprint)
	}
}

// TestWave68_FetchData_Dashboard_MalformedJSON verifies that fetchData sets
// state.Error when /api/dashboard returns malformed JSON.
func TestWave68_FetchData_Dashboard_MalformedJSON(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("{{invalid json}}"))
	}))
	defer srv.Close()

	m := NewDashboardModel(srv.URL)
	m.ticker.Stop()

	cmd := m.fetchData()
	msg := cmd()
	dm, ok := msg.(dataMsg)
	if !ok {
		t.Fatalf("expected dataMsg, got %T", msg)
	}
	if dm.Error == "" {
		t.Error("expected Error to be set when /api/dashboard returns malformed JSON")
	}
	if !strings.Contains(dm.Error, "Decode error") {
		t.Errorf("expected 'Decode error' in Error, got: %s", dm.Error)
	}
}

// TestWave68_FetchData_Dashboard_SprintRequestFails verifies that fetchData
// does not crash when the sprint endpoint connection is refused (after dashboard succeeded).
func TestWave68_FetchData_Dashboard_SprintRequestFails(t *testing.T) {
	// Serve dashboard OK but refuse sprint request by closing the server immediately after
	// the first request.
	var requestCount int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requestCount++
		if requestCount == 1 {
			// First call = /api/dashboard
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(DashboardState{
				Summary: DashboardSummary{OnlineAgents: 1},
			})
		} else {
			// Second call = /api/coordination/status — simulate error by closing connection.
			hj, ok := w.(http.Hijacker)
			if ok {
				conn, _, _ := hj.Hijack()
				conn.Close()
			}
		}
	}))
	defer srv.Close()

	m := NewDashboardModel(srv.URL)
	m.ticker.Stop()

	cmd := m.fetchData()
	msg := cmd()
	dm, ok := msg.(dataMsg)
	if !ok {
		t.Fatalf("expected dataMsg, got %T", msg)
	}
	// Dashboard succeeded so no error; sprint is just skipped.
	if dm.Error != "" {
		t.Errorf("unexpected dashboard error: %s", dm.Error)
	}
}

// TestWave68_TruncateStr verifies the truncateStr helper.
func TestWave68_TruncateStr(t *testing.T) {
	tests := []struct {
		input  string
		maxLen int
		want   string
	}{
		{"hello", 10, "hello"},
		{"hello world", 8, "hello..."},
		{"", 5, ""},
		{"abc", 3, "abc"},
	}
	for _, tt := range tests {
		got := truncateStr(tt.input, tt.maxLen)
		if got != tt.want {
			t.Errorf("truncateStr(%q, %d) = %q, want %q", tt.input, tt.maxLen, got, tt.want)
		}
	}
}

// TestWave68_NewDashboardModel_DefaultURL verifies that empty apiBaseURL
// defaults to "http://localhost:8081".
func TestWave68_NewDashboardModel_DefaultURL(t *testing.T) {
	m := NewDashboardModel("")
	if m.apiBaseURL != "http://localhost:8081" {
		t.Errorf("expected default URL http://localhost:8081, got %s", m.apiBaseURL)
	}
	m.ticker.Stop()
}

// TestWave68_DashboardUpdate_QuickTask_UnknownMsg verifies Update handles unknown messages.
func TestWave68_DashboardUpdate_QuickTask_UnknownMsg(t *testing.T) {
	m := NewDashboardModel("http://127.0.0.1:19999")
	m.ticker.Stop()
	m.showQuick = true

	// Unknown message type — should not panic, returns model unchanged.
	type wave68UnknownMsg struct{ Val int }
	m2, _ := m.Update(wave68UnknownMsg{Val: 42})
	if m2 == nil {
		t.Error("Update returned nil model")
	}
}

// TestWave68_DashboardUpdate_StatusBar_AgentsView verifies help text switches.
func TestWave68_DashboardUpdate_StatusBar_AgentsView(t *testing.T) {
	m := newTuiTestDashboard()
	m.currentView = AgentsView
	result := m.drawStatusBar()
	if !strings.Contains(result, "enter: details") {
		t.Errorf("drawStatusBar AgentsView should mention 'enter: details', got: %s", result)
	}
}

// TestWave68_DashboardUpdate_StatusBar_AgentDetailView verifies help text for detail view.
func TestWave68_DashboardUpdate_StatusBar_AgentDetailView(t *testing.T) {
	m := newTuiTestDashboard()
	m.currentView = AgentDetailView
	result := m.drawStatusBar()
	if !strings.Contains(result, "esc: back") {
		t.Errorf("drawStatusBar AgentDetailView should mention 'esc: back', got: %s", result)
	}
}
