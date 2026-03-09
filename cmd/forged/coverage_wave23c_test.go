//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"strings"
	"testing"
	"time"
)

// Wave 23c: tui_dashboard.go functions (previously 0%)

func TestDefaultKeyMap_W23C(t *testing.T) {
	km := DefaultKeyMap()
	if len(km.Up.Keys()) == 0 {
		t.Error("expected Up key binding")
	}
	if len(km.Quit.Keys()) == 0 {
		t.Error("expected Quit key binding")
	}
	if len(km.Refresh.Keys()) == 0 {
		t.Error("expected Refresh key binding")
	}
}

func TestNewDashboardModel_W23C(t *testing.T) {
	m := NewDashboardModel("http://localhost:8081")
	if m.apiBaseURL != "http://localhost:8081" {
		t.Errorf("unexpected apiBaseURL: %s", m.apiBaseURL)
	}
}

func TestNewDashboardModel_DefaultURL_W23C(t *testing.T) {
	m := NewDashboardModel("")
	if m.apiBaseURL == "" {
		t.Error("expected default apiBaseURL")
	}
}

func TestDashboardModel_Init_W23C(t *testing.T) {
	m := NewDashboardModel("http://localhost:9999")
	cmd := m.Init()
	_ = cmd // just verify no panic
}

func TestTruncateStr_W23C(t *testing.T) {
	if got := truncateStr("hello world", 8); !strings.HasPrefix(got, "hello") {
		t.Errorf("expected truncation, got %q", got)
	}
	if got := truncateStr("hi", 10); got != "hi" {
		t.Errorf("expected 'hi', got %q", got)
	}
	if got := truncateStr("", 5); got != "" {
		t.Errorf("expected empty, got %q", got)
	}
	if got := truncateStr("exactly5", 8); got != "exactly5" {
		t.Errorf("no truncation needed, got %q", got)
	}
}

func TestDrawSparkline_W23C(t *testing.T) {
	// Verify no panic on various inputs
	_ = drawSparkline([]float64{0, 25, 50, 75, 100}, 10)
	_ = drawSparkline(nil, 5)
	_ = drawSparkline([]float64{}, 5)
	_ = drawSparkline([]float64{42}, 5)
	_ = drawSparkline([]float64{0, 0, 0}, 5)
	_ = drawSparkline([]float64{100, 100, 100}, 5)
}

func TestAgentItem_Methods_W23C(t *testing.T) {
	item := agentItem{agent: AgentHealth{
		AgentID:    "test-agent",
		Node:       "prya",
		StatusText: "online",
	}}
	if item.Title() != "test-agent" {
		t.Errorf("Title() = %q, want 'test-agent'", item.Title())
	}
	if item.FilterValue() != "test-agent" {
		t.Errorf("FilterValue() = %q, want 'test-agent'", item.FilterValue())
	}
	desc := item.Description()
	if !strings.Contains(desc, "online") {
		t.Errorf("Description() missing status: %q", desc)
	}
}

func TestEventItem_Methods_W23C(t *testing.T) {
	item := eventItem{event: DashboardEvent{
		Timestamp: time.Now(),
		Type:      "heartbeat",
		AgentID:   "test-agent",
		Message:   "online",
	}}
	title := item.Title()
	if !strings.Contains(title, "heartbeat") {
		t.Errorf("Title() missing type: %q", title)
	}
	desc := item.Description()
	if !strings.Contains(desc, "test-agent") {
		t.Errorf("Description() missing agent: %q", desc)
	}
	if item.FilterValue() != "online" {
		t.Errorf("FilterValue() = %q, want 'online'", item.FilterValue())
	}
}
