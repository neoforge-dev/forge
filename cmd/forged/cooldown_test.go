//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func setupCooldownTestDB(t *testing.T) (*sql.DB, func()) {
	t.Helper()
	dbPath := filepath.Join(os.TempDir(), fmt.Sprintf("test_cooldown_%d_%d.db", time.Now().UnixNano(), os.Getpid()))
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("failed to migrate: %v", err)
	}
	cleanup := func() {
		db.Close()
		_ = os.Remove(dbPath)
	}
	return db, cleanup
}

func TestSetAgentCooldown(t *testing.T) {
	db, cleanup := setupCooldownTestDB(t)
	defer cleanup()

	err := SetAgentCooldown(db, "kimi", "rate_limit", 5*time.Minute)
	if err != nil {
		t.Fatalf("SetAgentCooldown: %v", err)
	}

	in, err := IsAgentInCooldown(db, "kimi")
	if err != nil {
		t.Fatalf("IsAgentInCooldown: %v", err)
	}
	if !in {
		t.Error("expected kimi to be in cooldown")
	}
}

func TestIsAgentInCooldown_NotSet(t *testing.T) {
	db, cleanup := setupCooldownTestDB(t)
	defer cleanup()

	in, err := IsAgentInCooldown(db, "nonexistent")
	if err != nil {
		t.Fatalf("IsAgentInCooldown: %v", err)
	}
	if in {
		t.Error("expected nonexistent agent to NOT be in cooldown")
	}
}

func TestClearAgentCooldown(t *testing.T) {
	db, cleanup := setupCooldownTestDB(t)
	defer cleanup()

	_ = SetAgentCooldown(db, "glm", "error", 10*time.Minute)
	err := ClearAgentCooldown(db, "glm")
	if err != nil {
		t.Fatalf("ClearAgentCooldown: %v", err)
	}

	in, _ := IsAgentInCooldown(db, "glm")
	if in {
		t.Error("expected glm cooldown to be cleared")
	}
}

func TestGetCooldownAgents(t *testing.T) {
	db, cleanup := setupCooldownTestDB(t)
	defer cleanup()

	_ = SetAgentCooldown(db, "kimi", "rate_limit", 5*time.Minute)
	_ = SetAgentCooldown(db, "glm", "crash", 10*time.Minute)

	entries, err := GetCooldownAgents(db)
	if err != nil {
		t.Fatalf("GetCooldownAgents: %v", err)
	}
	if len(entries) != 2 {
		t.Fatalf("expected 2 cooldown entries, got %d", len(entries))
	}
}

func TestSetAgentCooldown_Upsert(t *testing.T) {
	db, cleanup := setupCooldownTestDB(t)
	defer cleanup()

	_ = SetAgentCooldown(db, "kimi", "rate_limit", 5*time.Minute)
	_ = SetAgentCooldown(db, "kimi", "crash", 15*time.Minute)

	entries, _ := GetCooldownAgents(db)
	if len(entries) != 1 {
		t.Fatalf("expected 1 entry after upsert, got %d", len(entries))
	}
	if entries[0].Reason != "crash" {
		t.Errorf("expected reason 'crash', got %q", entries[0].Reason)
	}
}

func TestPurgeExpiredCooldowns(t *testing.T) {
	db, cleanup := setupCooldownTestDB(t)
	defer cleanup()

	// Set a cooldown that's already expired (0 duration)
	_, err := db.Exec(`
		INSERT INTO agent_cooldowns (agent_id, reason, cooldown_until, created_at)
		VALUES (?, ?, datetime('now', '-1 minute'), datetime('now'))
	`, "expired-agent", "test")
	if err != nil {
		t.Fatalf("insert expired cooldown: %v", err)
	}

	// Set a cooldown that's still active
	_ = SetAgentCooldown(db, "active-agent", "test", 10*time.Minute)

	purged, err := PurgeExpiredCooldowns(db)
	if err != nil {
		t.Fatalf("PurgeExpiredCooldowns: %v", err)
	}
	if purged != 1 {
		t.Errorf("expected 1 purged, got %d", purged)
	}

	entries, _ := GetCooldownAgents(db)
	if len(entries) != 1 {
		t.Errorf("expected 1 active cooldown remaining, got %d", len(entries))
	}
}

// --- Handler tests ---

func TestAgentCooldownsHandler_ListEmpty(t *testing.T) {
	db, cleanup := setupCooldownTestDB(t)
	defer cleanup()
	setDBConn(db)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/cooldowns", nil)
	w := httptest.NewRecorder()
	agentCooldownsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	if resp["count"].(float64) != 0 {
		t.Errorf("expected 0 cooldowns, got %v", resp["count"])
	}
}

func TestAgentCooldownHandler_SetAndGet(t *testing.T) {
	db, cleanup := setupCooldownTestDB(t)
	defer cleanup()
	setDBConn(db)

	// Set cooldown
	body := `{"reason":"rate_limit","duration_seconds":300}`
	req := httptest.NewRequest(http.MethodPost, "/api/agents/kimi/cooldown", bytes.NewBufferString(body))
	w := httptest.NewRecorder()
	agentCooldownHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("POST expected 200, got %d: %s", w.Code, w.Body.String())
	}

	// Check via GET
	req2 := httptest.NewRequest(http.MethodGet, "/api/agents/kimi/cooldown", nil)
	w2 := httptest.NewRecorder()
	agentCooldownHandler(w2, req2)

	var resp map[string]interface{}
	json.Unmarshal(w2.Body.Bytes(), &resp)
	if resp["in_cooldown"] != true {
		t.Error("expected kimi to be in cooldown after POST")
	}
}

func TestAgentCooldownHandler_Delete(t *testing.T) {
	db, cleanup := setupCooldownTestDB(t)
	defer cleanup()
	setDBConn(db)

	_ = SetAgentCooldown(db, "glm", "test", 5*time.Minute)

	req := httptest.NewRequest(http.MethodDelete, "/api/agents/glm/cooldown", nil)
	w := httptest.NewRecorder()
	agentCooldownHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("DELETE expected 200, got %d", w.Code)
	}

	in, _ := IsAgentInCooldown(db, "glm")
	if in {
		t.Error("expected cooldown to be cleared after DELETE")
	}
}
