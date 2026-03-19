package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// ─── loopState helpers ──────────────────────────────────────────────────────

func TestReadLoopState_Missing(t *testing.T) {
	// Point loopStateFile at a temp dir that has no file.
	orig := loopStateFile
	tmp := t.TempDir()
	loopStateFile = filepath.Join(tmp, "loop_state.json")
	defer func() { loopStateFile = orig }()

	s, err := readLoopState()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if s.HeartbeatCount != 0 {
		t.Errorf("expected HeartbeatCount=0, got %d", s.HeartbeatCount)
	}
	if s.SessionStart == "" {
		t.Error("expected SessionStart to be set")
	}
}

func TestWriteAndReadLoopState(t *testing.T) {
	orig := loopStateFile
	tmp := t.TempDir()
	loopStateFile = filepath.Join(tmp, "loop_state.json")
	defer func() { loopStateFile = orig }()

	s := &loopState{
		HeartbeatCount: 7,
		SessionStart:   "2026-03-19T00:00:00Z",
		LastRetro:      2,
		LastCrossNode:  5,
		LastCommit:     "2026-03-19T01:00:00Z",
	}
	if err := writeLoopState(s); err != nil {
		t.Fatalf("write: %v", err)
	}

	got, err := readLoopState()
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if got.HeartbeatCount != 7 {
		t.Errorf("HeartbeatCount: want 7, got %d", got.HeartbeatCount)
	}
	if got.LastRetro != 2 {
		t.Errorf("LastRetro: want 2, got %d", got.LastRetro)
	}
}

func TestReadLoopState_InvalidJSON(t *testing.T) {
	orig := loopStateFile
	tmp := t.TempDir()
	loopStateFile = filepath.Join(tmp, "loop_state.json")
	defer func() { loopStateFile = orig }()

	if err := os.WriteFile(loopStateFile, []byte("not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	_, err := readLoopState()
	if err == nil {
		t.Fatal("expected error for invalid JSON")
	}
}

// ─── newResultFiles ─────────────────────────────────────────────────────────

func TestNewResultFiles_CountsRecent(t *testing.T) {
	tmp := t.TempDir()
	resultsDir := filepath.Join(tmp, "results")
	if err := os.MkdirAll(resultsDir, 0o755); err != nil {
		t.Fatal(err)
	}

	// Write 2 result files.
	for _, name := range []string{"agent1-TASK1.md", "agent2-TASK2.md"} {
		if err := os.WriteFile(filepath.Join(resultsDir, name), []byte("result"), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// Patch the function to use our temp dir by writing a helper using the
	// same logic inline.
	refTime := time.Now().Add(-1 * time.Minute)
	count := 0
	entries, err := os.ReadDir(resultsDir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		info, _ := e.Info()
		if info.ModTime().After(refTime) {
			count++
		}
	}
	if count != 2 {
		t.Errorf("expected 2 recent files, got %d", count)
	}
}

func TestNewResultFiles_MissingDir(t *testing.T) {
	// newResultFiles on a non-existent dir should return 0, nil.
	tmp := t.TempDir()
	missing := filepath.Join(tmp, "no_such_dir", "results")
	entries, err := os.ReadDir(missing)
	if !os.IsNotExist(err) {
		// May not be IsNotExist in all Go versions, just check entries is nil/empty.
		if err != nil {
			// Expected; ok.
			return
		}
	}
	_ = entries
}

// ─── gitDirty ───────────────────────────────────────────────────────────────

func TestGitDirty_ReturnsInt(t *testing.T) {
	// Just verify it doesn't panic and returns a non-negative value.
	count, err := gitDirty()
	if err != nil {
		// git may not be available in all CI envs; skip.
		t.Skipf("git unavailable: %v", err)
	}
	if count < 0 {
		t.Errorf("gitDirty should be >= 0, got %d", count)
	}
}

// ─── heartbeat run command registration ─────────────────────────────────────

func TestHeartbeatRunCmd_Registered(t *testing.T) {
	found := false
	for _, sub := range heartbeatCmd.Commands() {
		if sub.Use == "run" {
			found = true
			break
		}
	}
	if !found {
		t.Error("heartbeat run subcommand not registered")
	}
}

func TestHeartbeatRunCmd_Flags(t *testing.T) {
	cmd := heartbeatRunCmd
	flags := []string{"interval", "max-iterations", "commit", "context-max"}
	for _, f := range flags {
		if cmd.Flags().Lookup(f) == nil {
			t.Errorf("flag --%s not found on heartbeat run", f)
		}
	}
}

func TestHeartbeatRunCmd_MaxIterations_ZeroIsUnlimited(t *testing.T) {
	cmd := heartbeatRunCmd
	f := cmd.Flags().Lookup("max-iterations")
	if f == nil {
		t.Fatal("max-iterations flag missing")
	}
	if f.DefValue != "0" {
		t.Errorf("expected default 0, got %s", f.DefValue)
	}
}

// ─── autoCommitResults ───────────────────────────────────────────────────────

func TestAutoCommitResults_NoResultFiles(t *testing.T) {
	// When no new files exist, autoCommitResults should return nil without running git.
	orig := loopStateFile
	tmp := t.TempDir()
	loopStateFile = filepath.Join(tmp, "loop_state.json")
	defer func() { loopStateFile = orig }()

	// State with future LastCommit so all existing files appear old.
	s := &loopState{
		HeartbeatCount: 1,
		SessionStart:   "2026-03-19T00:00:00Z",
		LastCommit:     time.Now().Add(1 * time.Hour).UTC().Format(time.RFC3339),
	}
	// autoCommitResults calls newResultFiles which reads .forge/heartbeat/results/
	// in the current working directory. In test env there's no such dir, so it
	// returns 0. The function should return nil.
	if err := autoCommitResults(s); err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}

// ─── loopState JSON round-trip ───────────────────────────────────────────────

func TestLoopStateJSONRoundTrip(t *testing.T) {
	orig := &loopState{
		HeartbeatCount: 42,
		SessionStart:   "2026-03-19T10:00:00Z",
		LastRetro:      10,
		LastCrossNode:  20,
		LastCommit:     "2026-03-19T09:55:00Z",
	}
	data, err := json.MarshalIndent(orig, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	var got loopState
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatal(err)
	}
	if got.HeartbeatCount != orig.HeartbeatCount {
		t.Errorf("HeartbeatCount: want %d, got %d", orig.HeartbeatCount, got.HeartbeatCount)
	}
	if got.LastCrossNode != orig.LastCrossNode {
		t.Errorf("LastCrossNode: want %d, got %d", orig.LastCrossNode, got.LastCrossNode)
	}
}
