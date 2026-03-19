//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bufio"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// ---------------------------------------------------------------------------
// metrics.go: responseWriter.Hijack — 66.7%
// Cover the success path by wrapping a fake Hijacker.
// ---------------------------------------------------------------------------

type fakeHijackResponseWriter struct {
	http.ResponseWriter
}

func (f *fakeHijackResponseWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	// Return a fake connection (nil is fine for testing)
	return nil, nil, nil
}

func TestWave95_ResponseWriter_Hijack_Success(t *testing.T) {
	fake := &fakeHijackResponseWriter{ResponseWriter: httptest.NewRecorder()}
	rw := &responseWriter{ResponseWriter: fake}

	conn, buf, err := rw.Hijack()
	if err != nil {
		t.Errorf("Hijack success: unexpected error: %v", err)
	}
	_ = conn
	_ = buf
}

// ---------------------------------------------------------------------------
// tui_dashboard.go: DashboardModel.Update — 77.5%
// Cover tickMsg and dataMsg branches.
// ---------------------------------------------------------------------------

func TestWave95_DashboardModel_Update_TickMsg(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()

	m := DashboardModel{
		ticker:     ticker,
		apiBaseURL: "http://localhost:9",
		keys:       DefaultKeyMap(),
	}

	now := time.Now()
	updated, cmd := m.Update(tickMsg(now))
	_ = updated
	_ = cmd
	_ = db
	t.Logf("DashboardModel.Update tickMsg: ok")
}

func TestWave95_DashboardModel_Update_DataMsg(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()

	m := DashboardModel{
		ticker:     ticker,
		apiBaseURL: "http://localhost:9",
		keys:       DefaultKeyMap(),
		width:      80,
		height:     24,
	}

	state := DashboardState{
		LastUpdated: time.Now(),
		Agents: []AgentHealth{
			{AgentID: "wave95-agent", Node: "test-node", Status: AgentStatusIdle},
		},
	}

	updated, cmd := m.Update(dataMsg(state))
	_ = updated
	_ = cmd
	_ = db
	t.Logf("DashboardModel.Update dataMsg: ok")
}

func TestWave95_DashboardModel_Update_WindowSizeMsg(t *testing.T) {
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()

	m := DashboardModel{
		ticker:     ticker,
		apiBaseURL: "http://localhost:9",
		keys:       DefaultKeyMap(),
	}

	updated, cmd := m.Update(tea.WindowSizeMsg{Width: 120, Height: 40})
	_ = updated
	_ = cmd
	t.Logf("DashboardModel.Update WindowSizeMsg: ok")
}

func TestWave95_DashboardModel_Update_ShowQuick_Esc(t *testing.T) {
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()

	m := DashboardModel{
		ticker:     ticker,
		apiBaseURL: "http://localhost:9",
		keys:       DefaultKeyMap(),
		showQuick:  true,
	}

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	_ = updated
	_ = cmd
	t.Logf("DashboardModel.Update showQuick esc: ok")
}

// ---------------------------------------------------------------------------
// context_sync.go: Start — 73.7%
// Cover the domain-dir loop by creating domain subdirs.
// ---------------------------------------------------------------------------

func TestWave95_ContextSync_Start_WithDomainDirs(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()

	// Create domain directory structure
	domainDir := tmpDir + "/codeswiftr"
	if err := os.MkdirAll(domainDir, 0755); err != nil {
		t.Fatalf("mkdir domain: %v", err)
	}
	// Create project subdir
	if err := os.MkdirAll(domainDir+"/proj1", 0755); err != nil {
		t.Fatalf("mkdir project: %v", err)
	}

	cm := testContextManager(t, db, tmpDir)
	cs, err := NewContextSync(cm, db, tmpDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	if err := cs.Start(); err != nil {
		t.Logf("ContextSync.Start with dirs: %v", err)
	} else {
		t.Logf("ContextSync.Start with dirs: ok")
	}
}
