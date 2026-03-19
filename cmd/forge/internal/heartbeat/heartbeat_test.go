package heartbeat

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestHeartbeatStruct(t *testing.T) {
	now := time.Now()
	hb := Heartbeat{
		AgentID:      "forge:minimax",
		Status:       StatusWorking,
		CurrentTask:  "V4-001",
		Progress:     50,
		LastActivity: now,
		Node:         "prya",
		ContextPct:   45.5,
	}

	if hb.AgentID != "forge:minimax" {
		t.Error("expected forge:minimax")
	}
	if hb.Status != StatusWorking {
		t.Error("expected working status")
	}
	if hb.Progress != 50 {
		t.Error("expected 50")
	}
}

func TestStatusConstants(t *testing.T) {
	if StatusIdle != "idle" {
		t.Error("StatusIdle should be 'idle'")
	}
	if StatusWorking != "working" {
		t.Error("StatusWorking should be 'working'")
	}
	if StatusBlocked != "blocked" {
		t.Error("StatusBlocked should be 'blocked'")
	}
	if StatusError != "error" {
		t.Error("StatusError should be 'error'")
	}
	if StatusComplete != "complete" {
		t.Error("StatusComplete should be 'complete'")
	}
}

func TestNewReporter(t *testing.T) {
	reporter := NewReporter("forge:test", "test-node")
	if reporter == nil {
		t.Fatal("expected reporter")
	}
	if reporter.agentID != "forge:test" {
		t.Error("expected forge:test")
	}
	if reporter.node != "test-node" {
		t.Error("expected test-node")
	}
}

func TestNewReporterWithEnvURL(t *testing.T) {
	os.Setenv("FORGE_SERVER_URL", "http://custom:9999")
	defer os.Unsetenv("FORGE_SERVER_URL")

	reporter := NewReporter("forge:test", "test-node")
	if reporter.serverURL != "http://custom:9999" {
		t.Error("expected custom URL from env")
	}
}

func TestReporterSend(t *testing.T) {
	// Create temp directory
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	reporter := NewReporter("forge:test", "test-node")
	err := reporter.Send(StatusWorking, "TASK-001", 75)
	if err != nil {
		t.Fatalf("Send failed: %v", err)
	}

	// Check file was created
	path := filepath.Join(".forge", "heartbeat", "agents", "forge:test", "heartbeat.json")
	if _, err := os.Stat(path); os.IsNotExist(err) {
		t.Error("heartbeat file was not created")
	}
}

func TestReporterSendWithBlockedReason(t *testing.T) {
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	reporter := NewReporter("forge:blocked", "test-node")
	err := reporter.Send(StatusBlocked, "TASK-002", 25)
	if err != nil {
		t.Fatalf("Send failed: %v", err)
	}
}

func TestReporterSendComplete(t *testing.T) {
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	reporter := NewReporter("forge:complete", "test-node")
	err := reporter.Send(StatusComplete, "TASK-003", 100)
	if err != nil {
		t.Fatalf("Send failed: %v", err)
	}
}

func TestNewMonitor(t *testing.T) {
	monitor := NewMonitor()
	if monitor == nil {
		t.Fatal("expected monitor")
	}
	if monitor.agentsDir != ".forge/heartbeat/agents" {
		t.Error("expected default agents dir")
	}
	if monitor.stallThreshold != 30*time.Minute {
		t.Error("expected 30 minute stall threshold")
	}
}

func TestMonitorCheckAllEmpty(t *testing.T) {
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	// Create agents dir but empty
	os.MkdirAll(".forge/heartbeat/agents", 0755)

	monitor := NewMonitor()
	heartbeats, err := monitor.CheckAll()
	if err != nil {
		t.Fatalf("CheckAll failed: %v", err)
	}
	if len(heartbeats) != 0 {
		t.Error("expected empty heartbeats")
	}
}

func TestMonitorCheckAllWithAgent(t *testing.T) {
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	// Create agent heartbeat
	os.MkdirAll(".forge/heartbeat/agents/forge:test", 0755)
	hbData := `{"agent_id":"forge:test","status":"working","current_task":"TASK-001","progress":50,"last_activity":"2026-03-05T12:00:00Z"}`
	os.WriteFile(".forge/heartbeat/agents/forge:test/heartbeat.json", []byte(hbData), 0644)

	monitor := NewMonitor()
	heartbeats, err := monitor.CheckAll()
	if err != nil {
		t.Fatalf("CheckAll failed: %v", err)
	}
	if len(heartbeats) != 1 {
		t.Errorf("expected 1 heartbeat, got %d", len(heartbeats))
	}
	if heartbeats[0].AgentID != "forge:test" {
		t.Error("expected forge:test")
	}
}

func TestMonitorDetectStalls(t *testing.T) {
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	// Create agent with old heartbeat (stalled)
	os.MkdirAll(".forge/heartbeat/agents/forge:stalled", 0755)
	oldTime := time.Now().Add(-60 * time.Minute).Format(time.RFC3339)
	hbData := `{"agent_id":"forge:stalled","status":"working","last_activity":"` + oldTime + `"}`
	os.WriteFile(".forge/heartbeat/agents/forge:stalled/heartbeat.json", []byte(hbData), 0644)

	monitor := NewMonitor()
	stalls, err := monitor.DetectStalls()
	if err != nil {
		t.Fatalf("DetectStalls failed: %v", err)
	}
	if len(stalls) != 1 {
		t.Errorf("expected 1 stall, got %d", len(stalls))
	}
}
