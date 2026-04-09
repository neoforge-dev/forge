//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Test DB helpers
// ---------------------------------------------------------------------------

func setupVoiceTestDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open test db: %v", err)
	}
	// Minimal schema needed by voice handlers.
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS voice_commands (
			id TEXT PRIMARY KEY,
			session_id TEXT,
			raw_text TEXT NOT NULL,
			intent TEXT,
			confidence REAL,
			entities JSON,
			executed BOOLEAN DEFAULT 0,
			success BOOLEAN DEFAULT 0,
			error TEXT,
			result JSON,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			executed_at TIMESTAMP
		);
		CREATE TABLE IF NOT EXISTS tasks (
			id TEXT PRIMARY KEY,
			title TEXT,
			status TEXT,
			domain TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);
		CREATE TABLE IF NOT EXISTS agent_heartbeats (
			agent_id TEXT PRIMARY KEY,
			status TEXT,
			last_activity TEXT
		);
		CREATE TABLE IF NOT EXISTS events (
			id TEXT PRIMARY KEY,
			aggregate_id TEXT NOT NULL,
			topic TEXT NOT NULL,
			type TEXT NOT NULL,
			version INTEGER NOT NULL,
			payload JSON NOT NULL,
			timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
			agent_id TEXT,
			sequence INTEGER
		);
	`)
	if err != nil {
		t.Fatalf("create schema: %v", err)
	}
	return db
}

// ---------------------------------------------------------------------------
// POST /api/voice/command tests
// ---------------------------------------------------------------------------

func TestVoiceCommandHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/voice/command", nil)
	rr := httptest.NewRecorder()
	voiceCommandHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestVoiceCommandHandler_MissingText(t *testing.T) {
	db := setupVoiceTestDB(t)
	defer db.Close()
	setDBConn(db)

	body := `{"text": "", "session_id": "test-sess"}`
	req := httptest.NewRequest(http.MethodPost, "/api/voice/command", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	voiceCommandHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty text, got %d", rr.Code)
	}
}

func TestVoiceCommandHandler_InvalidJSON(t *testing.T) {
	db := setupVoiceTestDB(t)
	defer db.Close()
	setDBConn(db)

	req := httptest.NewRequest(http.MethodPost, "/api/voice/command", strings.NewReader("{bad json"))
	rr := httptest.NewRecorder()
	voiceCommandHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid JSON, got %d", rr.Code)
	}
}

func TestVoiceCommandHandler_StatusIntent(t *testing.T) {
	db := setupVoiceTestDB(t)
	defer db.Close()
	setDBConn(db)

	body := `{"text": "status", "session_id": "sess-status"}`
	req := httptest.NewRequest(http.MethodPost, "/api/voice/command", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	voiceCommandHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d — body: %s", rr.Code, rr.Body.String())
	}
	var resp voiceCommandResponse
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal response: %v", err)
	}
	if resp.Intent != IntentStatus {
		t.Errorf("expected intent=status, got %q", resp.Intent)
	}
	if resp.SessionID == "" {
		t.Error("expected non-empty session_id in response")
	}
}

func TestVoiceCommandHandler_HelpIntent(t *testing.T) {
	db := setupVoiceTestDB(t)
	defer db.Close()
	setDBConn(db)

	body := `{"text": "help", "session_id": "sess-help"}`
	req := httptest.NewRequest(http.MethodPost, "/api/voice/command", strings.NewReader(body))
	rr := httptest.NewRecorder()
	voiceCommandHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", rr.Code)
	}
	var resp voiceCommandResponse
	json.Unmarshal(rr.Body.Bytes(), &resp)
	if resp.Intent != IntentHelp {
		t.Errorf("expected help intent, got %q", resp.Intent)
	}
	if resp.Response == "" {
		t.Error("expected non-empty response text for help intent")
	}
}

func TestVoiceCommandHandler_UnknownIntent(t *testing.T) {
	db := setupVoiceTestDB(t)
	defer db.Close()
	setDBConn(db)

	body := `{"text": "xyzzy teleport", "session_id": "sess-unknown"}`
	req := httptest.NewRequest(http.MethodPost, "/api/voice/command", strings.NewReader(body))
	rr := httptest.NewRecorder()
	voiceCommandHandler(rr, req)

	var resp voiceCommandResponse
	json.Unmarshal(rr.Body.Bytes(), &resp)
	if resp.Intent != IntentUnknown {
		t.Errorf("expected unknown intent, got %q", resp.Intent)
	}
	if !strings.Contains(resp.Response, "didn't understand") && !strings.Contains(resp.Response, "Try") {
		t.Errorf("expected helpful suggestion in response, got: %q", resp.Response)
	}
}

func TestVoiceCommandHandler_SessionIDGenerated(t *testing.T) {
	db := setupVoiceTestDB(t)
	defer db.Close()
	setDBConn(db)

	body := `{"text": "status"}`
	req := httptest.NewRequest(http.MethodPost, "/api/voice/command", strings.NewReader(body))
	rr := httptest.NewRecorder()
	voiceCommandHandler(rr, req)

	var resp voiceCommandResponse
	json.Unmarshal(rr.Body.Bytes(), &resp)
	if resp.SessionID == "" {
		t.Error("expected auto-generated session_id when none provided")
	}
}

func TestVoiceCommandHandler_DecideNoApprovalService(t *testing.T) {
	db := setupVoiceTestDB(t)
	defer db.Close()
	setDBConn(db)

	// Ensure no approval service is set.
	old := watchSummaryApprovalService
	watchSummaryApprovalService = nil
	defer func() { watchSummaryApprovalService = old }()

	body := `{"text": "approve the deploy", "session_id": "sess-decide"}`
	req := httptest.NewRequest(http.MethodPost, "/api/voice/command", strings.NewReader(body))
	rr := httptest.NewRecorder()
	voiceCommandHandler(rr, req)

	// Should respond with an appropriate message, not a 500.
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200 even with no approval service, got %d", rr.Code)
	}
	var resp voiceCommandResponse
	json.Unmarshal(rr.Body.Bytes(), &resp)
	if resp.Executed {
		t.Error("should not mark executed when no approval service available")
	}
}

func TestVoiceCommandHandler_SessionStatePersistence(t *testing.T) {
	db := setupVoiceTestDB(t)
	defer db.Close()
	setDBConn(db)

	store := &ConversationStore{sessions: make(map[string]*ConversationState)}
	origStore := globalConversationStore
	globalConversationStore = store
	defer func() { globalConversationStore = origStore }()

	sessID := "persist-test-session"

	// First request.
	body1 := `{"text": "status", "session_id": "` + sessID + `"}`
	req1 := httptest.NewRequest(http.MethodPost, "/api/voice/command", strings.NewReader(body1))
	rr1 := httptest.NewRecorder()
	voiceCommandHandler(rr1, req1)
	if rr1.Code != http.StatusOK {
		t.Fatalf("first request failed: %d", rr1.Code)
	}

	// Session should be stored.
	store.mu.Lock()
	sess, exists := store.sessions[sessID]
	store.mu.Unlock()
	if !exists {
		t.Fatal("session should be persisted after first request")
	}
	if sess.LastIntent == "" {
		t.Error("expected LastIntent to be set after first request")
	}
}

func TestVoiceCommandHandler_ConfirmFlag(t *testing.T) {
	db := setupVoiceTestDB(t)
	defer db.Close()
	setDBConn(db)

	store := &ConversationStore{sessions: make(map[string]*ConversationState)}
	origStore := globalConversationStore
	globalConversationStore = store
	defer func() { globalConversationStore = origStore }()

	// Pre-seed session with pending confirmation (no real approval service needed).
	sessID := "confirm-test"
	sess := store.Get(sessID)
	sess.PendingConfirmation = &PendingAction{
		Intent:      IntentDecide,
		Action:      "approve",
		TargetID:    "approval-xyz",
		Title:       "test approval",
		Destructive: false,
	}
	store.Put(sess)

	// Send confirm=true.  executeApprovalAction will fail (no service) but the
	// handler should return 200 with a graceful error message.
	body := `{"text": "yes confirm", "session_id": "` + sessID + `", "confirm": true}`
	req := httptest.NewRequest(http.MethodPost, "/api/voice/command", strings.NewReader(body))
	rr := httptest.NewRecorder()
	voiceCommandHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200 even on failed approval, got %d — %s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// GET /api/voice/vocabulary tests
// ---------------------------------------------------------------------------

func TestVoiceVocabularyHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/voice/vocabulary", nil)
	rr := httptest.NewRecorder()
	voiceVocabularyHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestVoiceVocabularyHandler_ReturnsStructuredData(t *testing.T) {
	db := setupVoiceTestDB(t)
	defer db.Close()
	setDBConn(db)

	// Reset cache.
	globalVocabCache.mu.Lock()
	globalVocabCache.data = nil
	globalVocabCache.mu.Unlock()

	req := httptest.NewRequest(http.MethodGet, "/api/voice/vocabulary", nil)
	rr := httptest.NewRecorder()
	voiceVocabularyHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d — %s", rr.Code, rr.Body.String())
	}
	var resp voiceVocabularyResponse
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal vocab response: %v", err)
	}
	if len(resp.Nodes) == 0 {
		t.Error("expected non-empty nodes list")
	}
	if len(resp.Agents) == 0 {
		t.Error("expected non-empty agents list")
	}
	if len(resp.Commands) == 0 {
		t.Error("expected non-empty commands list")
	}
	if resp.ActiveTaskTitles == nil {
		t.Error("expected active_task_titles to be present (may be empty slice)")
	}
	if resp.PendingDecisionTitles == nil {
		t.Error("expected pending_decision_titles to be present (may be empty slice)")
	}
}

func TestVoiceVocabularyHandler_UsesCache(t *testing.T) {
	db := setupVoiceTestDB(t)
	defer db.Close()
	setDBConn(db)

	// Pre-seed the cache.
	globalVocabCache.mu.Lock()
	globalVocabCache.data = &voiceVocabularyResponse{
		Nodes:                 []string{"cached-node"},
		Agents:                []string{"cached-agent"},
		Commands:              []string{"cached-cmd"},
		Domains:               []string{},
		ActiveTaskTitles:      []string{},
		PendingDecisionTitles: []string{},
	}
	globalVocabCache.fetchedAt = time.Now()
	globalVocabCache.mu.Unlock()

	req := httptest.NewRequest(http.MethodGet, "/api/voice/vocabulary", nil)
	rr := httptest.NewRecorder()
	voiceVocabularyHandler(rr, req)

	var resp voiceVocabularyResponse
	json.Unmarshal(rr.Body.Bytes(), &resp)
	if len(resp.Nodes) == 0 || resp.Nodes[0] != "cached-node" {
		t.Errorf("expected cached response, got nodes=%v", resp.Nodes)
	}

	// Reset cache.
	globalVocabCache.mu.Lock()
	globalVocabCache.data = nil
	globalVocabCache.mu.Unlock()
}

// ---------------------------------------------------------------------------
// POST /api/voice/transcribe tests
// ---------------------------------------------------------------------------

func TestVoiceTranscribeHandler_Returns501(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/voice/transcribe", bytes.NewReader([]byte("{}")))
	rr := httptest.NewRecorder()
	voiceTranscribeHandler(rr, req)
	if rr.Code != http.StatusNotImplemented {
		t.Errorf("expected 501 Not Implemented, got %d", rr.Code)
	}
	var body map[string]string
	json.Unmarshal(rr.Body.Bytes(), &body)
	if !strings.Contains(body["message"], "Whisper") {
		t.Errorf("expected Whisper mention in 501 message, got: %q", body["message"])
	}
}

func TestVoiceTranscribeHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/voice/transcribe", nil)
	rr := httptest.NewRecorder()
	voiceTranscribeHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for GET, got %d", rr.Code)
	}
}

// ---------------------------------------------------------------------------
// GET /api/voice/analytics tests
// ---------------------------------------------------------------------------

func TestVoiceAnalyticsHandler_ReturnsData(t *testing.T) {
	db := setupVoiceTestDB(t)
	defer db.Close()
	setDBConn(db)

	req := httptest.NewRequest(http.MethodGet, "/api/voice/analytics", nil)
	rr := httptest.NewRecorder()
	voiceAnalyticsHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d — %s", rr.Code, rr.Body.String())
	}
	var analytics VoiceAnalytics
	if err := json.Unmarshal(rr.Body.Bytes(), &analytics); err != nil {
		t.Fatalf("unmarshal analytics: %v", err)
	}
	if analytics.TopIntents == nil {
		t.Error("expected non-nil top_intents map")
	}
	if analytics.FailedTerms == nil {
		t.Error("expected non-nil failed_terms slice")
	}
}

func TestVoiceAnalyticsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/voice/analytics", nil)
	rr := httptest.NewRecorder()
	voiceAnalyticsHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

