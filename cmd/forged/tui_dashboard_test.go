//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"runtime"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/stretchr/testify/assert"
)

// newTuiTestDashboard returns a DashboardModel with a fixed size for rendering tests.
// Rescued from coverage_tui_test.go.
func newTuiTestDashboard() DashboardModel {
	m := NewDashboardModel("http://localhost:9999")
	m.width = 80
	m.height = 24
	return m
}

func TestDashboardModel_GoroutineLeak(t *testing.T) {
	// Record goroutines before
	before := runtime.NumGoroutine()

	// Create and run dashboard model (not the full tea program, just init/cleanup)
	model := NewDashboardModel("http://localhost:8081")

	// Simulate Init which starts the ticker
	initCmd := model.Init()
	if initCmd != nil {
		// Execute the init command to start the ticker loop
		msg := initCmd()
		if msg != nil {
			// Let one tick happen
			time.Sleep(100 * time.Millisecond)
		}
	}

	// Clean up the ticker (simulating what RunDashboard does)
	if model.ticker != nil {
		model.ticker.Stop()
	}

	// Give a moment for goroutines to clean up
	time.Sleep(50 * time.Millisecond)

	// Record goroutines after
	after := runtime.NumGoroutine()

	// Should not have leaked goroutines
	assert.LessOrEqual(t, after, before+1, "goroutine leak detected: before=%d, after=%d", before, after)
}

func TestDashboardModel_TickerCleanup(t *testing.T) {
	model := NewDashboardModel("http://localhost:8081")

	// Verify ticker is created
	assert.NotNil(t, model.ticker)

	// Stop the ticker
	model.ticker.Stop()

	// Verify we can stop it again without panic (idempotent)
	model.ticker.Stop()
}

// Wave 25c: tui_dashboard.go fetchData + Update (previously 5.9% and 42.3%)

func TestFetchData_NoServer_W25C(t *testing.T) {
	m := NewDashboardModel("http://127.0.0.1:19999") // no server on this port
	cmd := m.fetchData()
	if cmd == nil {
		t.Error("fetchData should return a non-nil tea.Cmd")
	}
	// Execute the command — should return dataMsg with error
	msg := cmd()
	if msg == nil {
		t.Error("fetchData cmd should return a non-nil message")
	}
}

func TestDashboardUpdate_TickMsg_W25C(t *testing.T) {
	m := NewDashboardModel("http://127.0.0.1:19999")
	newModel, cmd := m.Update(tickMsg{})
	_ = newModel
	_ = cmd
}

func TestDashboardUpdate_DataMsg_W25C(t *testing.T) {
	m := NewDashboardModel("http://127.0.0.1:19999")
	state := DashboardState{Error: "test error"}
	newModel, cmd := m.Update(dataMsg(state))
	_ = newModel
	_ = cmd
}

func TestDashboardUpdate_KeyQuit_W25C(t *testing.T) {
	m := NewDashboardModel("http://127.0.0.1:19999")
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'q'}})
	_ = cmd
}

func TestDashboardUpdate_KeyRefresh_W25C(t *testing.T) {
	m := NewDashboardModel("http://127.0.0.1:19999")
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'r'}})
	_ = cmd
}

func TestDashboardUpdate_KeyTab_W25C(t *testing.T) {
	m := NewDashboardModel("http://127.0.0.1:19999")
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyTab})
	_ = cmd
}

func TestDashboardUpdate_WindowSize_W25C(t *testing.T) {
	m := NewDashboardModel("http://127.0.0.1:19999")
	_, cmd := m.Update(tea.WindowSizeMsg{Width: 80, Height: 24})
	_ = cmd
}

func TestDashboardUpdate_KeyUp_W25C(t *testing.T) {
	m := NewDashboardModel("http://127.0.0.1:19999")
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyUp})
	_ = cmd
}

func TestDashboardUpdate_KeyDown_W25C(t *testing.T) {
	m := NewDashboardModel("http://127.0.0.1:19999")
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyDown})
	_ = cmd
}
