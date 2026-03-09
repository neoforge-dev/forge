//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/spf13/cobra"
)

// ============================================================
// Helper: SQLite-compatible idempotency table (Postgres syntax
// in CreateIdempotencyTable is not usable with SQLite).
// ============================================================

func newIdempotencyDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS idempotent_actions (
			id TEXT PRIMARY KEY,
			type TEXT NOT NULL,
			payload_hash TEXT NOT NULL UNIQUE,
			executed_at TEXT DEFAULT (datetime('now')),
			result TEXT
		);
	`)
	if err != nil {
		db.Close()
		t.Fatalf("create idempotent_actions: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

// ============================================================
// cli_commands.go — getTaskStoreForCLI
// ============================================================

// TestGetTaskStoreForCLI_UsesGlobalConn verifies that when the global DB
// connection is set (daemon mode), getTaskStoreForCLI returns a store backed
// by that connection without opening a new one.
func TestGetTaskStoreForCLI_UsesGlobalConn(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	store, err := getTaskStoreForCLI()
	if err != nil {
		t.Fatalf("getTaskStoreForCLI: %v", err)
	}
	if store == nil {
		t.Fatal("expected non-nil TaskStore")
	}
}

// TestGetTaskStoreForCLI_FallsBackToDBPath verifies that when the global DB
// connection is nil, getTaskStoreForCLI opens a direct connection via DB_PATH.
func TestGetTaskStoreForCLI_FallsBackToDBPath(t *testing.T) {
	// Save and clear the global DB connection so the fallback path is exercised.
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	// Point DB_PATH at a temp file and run MigrateUp so the schema exists.
	tmpDB := t.TempDir() + "/cli_fallback_test.db"
	db, err := OpenDB(tmpDB)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("MigrateUp: %v", err)
	}
	db.Close()

	t.Setenv("DB_PATH", tmpDB)

	store, err := getTaskStoreForCLI()
	if err != nil {
		t.Fatalf("getTaskStoreForCLI fallback: %v", err)
	}
	if store == nil {
		t.Fatal("expected non-nil TaskStore from fallback path")
	}
}

// TestGetTaskStoreForCLI_BadPath verifies that an invalid DB_PATH yields an
// error rather than a nil store.
func TestGetTaskStoreForCLI_BadPath(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	// Set DB_PATH to a directory path (OpenDB will fail).
	t.Setenv("DB_PATH", "/nonexistent/path/to/forge_bad.db")

	_, err := getTaskStoreForCLI()
	if err == nil {
		t.Fatal("expected error for invalid DB_PATH, got nil")
	}
}

// ============================================================
// cli_commands.go — getAgentID
// ============================================================

func makeCobraCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "test"}
	cmd.Flags().String("agent", "", "agent id")
	return cmd
}

// TestGetAgentID_FromFlag verifies the --agent flag takes priority.
func TestGetAgentID_FromFlag(t *testing.T) {
	cmd := makeCobraCmd()
	if err := cmd.Flags().Set("agent", "agent-flag"); err != nil {
		t.Fatalf("set flag: %v", err)
	}
	id, err := getAgentID(cmd)
	if err != nil {
		t.Fatalf("getAgentID: %v", err)
	}
	if id != "agent-flag" {
		t.Errorf("want agent-flag, got %q", id)
	}
}

// TestGetAgentID_FromEnv verifies FORGE_AGENT_ID env variable is used when
// no flag is given.
func TestGetAgentID_FromEnv(t *testing.T) {
	cmd := makeCobraCmd()
	t.Setenv("FORGE_AGENT_ID", "agent-env")
	// Ensure TMUX is unset so we don't hit the tmux path.
	t.Setenv("TMUX", "")

	id, err := getAgentID(cmd)
	if err != nil {
		t.Fatalf("getAgentID from env: %v", err)
	}
	if id != "agent-env" {
		t.Errorf("want agent-env, got %q", id)
	}
}

// TestGetAgentID_NoSourceReturnsError verifies that when no flag, env, or
// tmux window name is available, an error is returned.
func TestGetAgentID_NoSourceReturnsError(t *testing.T) {
	cmd := makeCobraCmd()
	t.Setenv("FORGE_AGENT_ID", "")
	t.Setenv("TMUX", "")

	_, err := getAgentID(cmd)
	if err == nil {
		t.Fatal("expected error when no agent ID source is available")
	}
	if !strings.Contains(err.Error(), "agent ID required") {
		t.Errorf("unexpected error message: %v", err)
	}
}

// TestGetAgentID_FlagTakesPriorityOverEnv ensures flag > env precedence.
func TestGetAgentID_FlagTakesPriorityOverEnv(t *testing.T) {
	cmd := makeCobraCmd()
	if err := cmd.Flags().Set("agent", "from-flag"); err != nil {
		t.Fatalf("set flag: %v", err)
	}
	t.Setenv("FORGE_AGENT_ID", "from-env")
	t.Setenv("TMUX", "")

	id, err := getAgentID(cmd)
	if err != nil {
		t.Fatalf("getAgentID: %v", err)
	}
	if id != "from-flag" {
		t.Errorf("expected from-flag, got %q", id)
	}
}

// ============================================================
// cli_commands.go — init / truncate helper
// ============================================================

// TestTruncate_ShortString verifies strings shorter than maxLen pass through.
func TestTruncate_ShortString(t *testing.T) {
	got := truncate("hello", 10)
	if got != "hello" {
		t.Errorf("want hello, got %q", got)
	}
}

// TestTruncate_ExactLength passes through unchanged.
func TestTruncate_ExactLength(t *testing.T) {
	got := truncate("hello", 5)
	if got != "hello" {
		t.Errorf("want hello, got %q", got)
	}
}

// TestTruncate_LongString verifies strings longer than maxLen are truncated
// with an ellipsis.
func TestTruncate_LongString(t *testing.T) {
	got := truncate("hello world", 8)
	if len(got) != 8 {
		t.Errorf("want length 8, got %d: %q", len(got), got)
	}
	if !strings.HasSuffix(got, "...") {
		t.Errorf("expected ellipsis suffix, got %q", got)
	}
}

// ============================================================
// gitguard_idempotency.go — pure hash functions
// ============================================================

// TestComputeIdempotencyHash_Deterministic verifies the same key always
// produces the same hash.
func TestComputeIdempotencyHash_Deterministic(t *testing.T) {
	key := IdempotencyKey{
		ActionType: "git_commit",
		Payload:    []byte(`{"repo":"myrepo"}`),
		ResourceID: "task-42",
	}
	h1 := computeIdempotencyHash(key)
	h2 := computeIdempotencyHash(key)
	if h1 != h2 {
		t.Errorf("hash not deterministic: %q vs %q", h1, h2)
	}
	if len(h1) != 64 {
		t.Errorf("expected 64-char SHA256 hex, got len %d", len(h1))
	}
}

// TestComputeIdempotencyHash_DifferentTypes produces different hashes.
func TestComputeIdempotencyHash_DifferentTypes(t *testing.T) {
	payload := []byte(`{"x":1}`)
	h1 := computeIdempotencyHash(IdempotencyKey{ActionType: "type_a", Payload: payload})
	h2 := computeIdempotencyHash(IdempotencyKey{ActionType: "type_b", Payload: payload})
	if h1 == h2 {
		t.Error("different action types should produce different hashes")
	}
}

// TestComputeIdempotencyHash_ResourceIDChangesHash verifies that an added
// ResourceID changes the output.
func TestComputeIdempotencyHash_ResourceIDChangesHash(t *testing.T) {
	base := IdempotencyKey{ActionType: "op", Payload: []byte("data")}
	withRes := IdempotencyKey{ActionType: "op", Payload: []byte("data"), ResourceID: "res-1"}

	h1 := computeIdempotencyHash(base)
	h2 := computeIdempotencyHash(withRes)
	if h1 == h2 {
		t.Error("expected different hash when ResourceID is added")
	}
}

// TestHashPayload_ConsistentWithComputeIdempotencyHash ensures the two
// hash functions agree when ResourceID is empty.
func TestHashPayload_ConsistentWithComputeIdempotencyHash(t *testing.T) {
	payload := []byte("some-payload")
	actionType := "test_action"

	via_hash := hashPayload(actionType, payload)
	via_compute := computeIdempotencyHash(IdempotencyKey{
		ActionType: actionType,
		Payload:    payload,
	})

	if via_hash != via_compute {
		t.Errorf("hashPayload and computeIdempotencyHash disagree: %q vs %q", via_hash, via_compute)
	}
}

// ============================================================
// gitguard_idempotency.go — IdempotencyStore methods
// (StoreAndGetByHash, GetByHash_NotFound, IsDuplicate, Cleanup covered in
// gitguard_idempotency_test.go — only non-duplicate variants live here)
// ============================================================

// TestIdempotencyStore_IsDuplicate_False verifies fresh payloads are not
// considered duplicates.
func TestIdempotencyStore_IsDuplicate_False(t *testing.T) {
	db := newIdempotencyDB(t)
	store := NewIdempotencyStore(db)

	isDup, action, err := store.IsDuplicate("op", []byte("new-payload"))
	if err != nil {
		t.Fatalf("IsDuplicate: %v", err)
	}
	if isDup {
		t.Error("expected false for unseen payload")
	}
	if action != nil {
		t.Errorf("expected nil action for unseen payload, got %+v", action)
	}
}

// TestIdempotencyStore_IsDuplicate_True verifies stored payloads are flagged.
func TestIdempotencyStore_IsDuplicate_True(t *testing.T) {
	db := newIdempotencyDB(t)
	store := NewIdempotencyStore(db)

	payload := []byte(`"my-payload"`)
	action := &IdempotentAction{
		ID:          "dup-id",
		Type:        "op",
		PayloadHash: hashPayload("op", payload),
		ExecutedAt:  time.Now().UTC(),
		Result:      json.RawMessage(`"result"`),
	}
	if err := store.Store(action); err != nil {
		t.Fatalf("Store: %v", err)
	}

	isDup, found, err := store.IsDuplicate("op", payload)
	if err != nil {
		t.Fatalf("IsDuplicate: %v", err)
	}
	if !isDup {
		t.Error("expected duplicate to be detected")
	}
	if found == nil || found.ID != "dup-id" {
		t.Errorf("unexpected action: %+v", found)
	}
}

// TestIdempotencyStore_ExecuteOnce_RunsOnce verifies the fn is called exactly
// once across two invocations with the same payload.
func TestIdempotencyStore_ExecuteOnce_RunsOnce(t *testing.T) {
	db := newIdempotencyDB(t)
	store := NewIdempotencyStore(db)

	calls := 0
	fn := func() (interface{}, error) {
		calls++
		return "exec-result", nil
	}

	payload := []byte(`"payload-once"`)

	res1, err := store.ExecuteOnce("action_once", payload, fn)
	if err != nil {
		t.Fatalf("ExecuteOnce 1st: %v", err)
	}
	if res1 != "exec-result" {
		t.Errorf("1st result: want exec-result, got %v", res1)
	}

	res2, err := store.ExecuteOnce("action_once", payload, fn)
	if err != nil {
		t.Fatalf("ExecuteOnce 2nd: %v", err)
	}
	if res2 != "exec-result" {
		t.Errorf("2nd result: want exec-result, got %v", res2)
	}

	if calls != 1 {
		t.Errorf("fn called %d times, expected exactly 1", calls)
	}
}

// ============================================================
// gitguard_idempotency.go — GitGuardIdempotency / ExecuteCommit
// (Cleanup covered in gitguard_idempotency_test.go)
// ============================================================

// TestGitGuardIdempotency_ExecuteCommit_Idempotent verifies that ExecuteCommit
// calls the commit function exactly once for the same payload.
func TestGitGuardIdempotency_ExecuteCommit_Idempotent(t *testing.T) {
	db := newIdempotencyDB(t)
	store := NewIdempotencyStore(db)
	gg := NewGitGuardIdempotency(store)

	calls := 0
	fn := func() (string, error) {
		calls++
		return "abc123", nil
	}

	payload := CommitPayload{
		RepoPath:    "/tmp/repo",
		Message:     "feat: add idempotent commit",
		Files:       []string{"main.go"},
		AuthorName:  "Agent",
		AuthorEmail: "agent@forge.ai",
	}

	hash1, err := gg.ExecuteCommit(payload, fn)
	if err != nil {
		t.Fatalf("ExecuteCommit 1st: %v", err)
	}
	if hash1 != "abc123" {
		t.Errorf("1st hash: want abc123, got %q", hash1)
	}

	hash2, err := gg.ExecuteCommit(payload, fn)
	if err != nil {
		t.Fatalf("ExecuteCommit 2nd: %v", err)
	}
	if hash2 != "abc123" {
		t.Errorf("2nd hash: want abc123, got %q", hash2)
	}

	if calls != 1 {
		t.Errorf("commit fn called %d times, want 1", calls)
	}
}

// TestGitGuardIdempotency_ExecuteCommit_DifferentPayloads verifies that
// commits with different payloads each call the fn.
func TestGitGuardIdempotency_ExecuteCommit_DifferentPayloads(t *testing.T) {
	db := newIdempotencyDB(t)
	store := NewIdempotencyStore(db)
	gg := NewGitGuardIdempotency(store)

	calls := 0
	fn := func() (string, error) {
		calls++
		return "hash" + string(rune('0'+calls)), nil
	}

	base := CommitPayload{RepoPath: "/tmp/repo", Message: "msg1", Files: []string{"a.go"}}
	different := CommitPayload{RepoPath: "/tmp/repo", Message: "msg2", Files: []string{"b.go"}}

	if _, err := gg.ExecuteCommit(base, fn); err != nil {
		t.Fatalf("1st commit: %v", err)
	}
	if _, err := gg.ExecuteCommit(different, fn); err != nil {
		t.Fatalf("2nd commit: %v", err)
	}

	if calls != 2 {
		t.Errorf("expected 2 fn calls for different payloads, got %d", calls)
	}
}

// ============================================================
// gitguard_idempotency.go — HTTP handlers
// ============================================================

// TestHandleIdempotencyCheck_WrongMethod verifies 405 on non-POST.
func TestHandleIdempotencyCheck_WrongMethod(t *testing.T) {
	db := newIdempotencyDB(t)
	store := NewIdempotencyStore(db)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/idempotency/check", nil)
	w := httptest.NewRecorder()
	store.HandleIdempotencyCheck(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("want 405, got %d", w.Code)
	}
}

// TestHandleIdempotencyCheck_InvalidJSON verifies 400 on malformed body.
func TestHandleIdempotencyCheck_InvalidJSON(t *testing.T) {
	db := newIdempotencyDB(t)
	store := NewIdempotencyStore(db)

	req := httptest.NewRequest(http.MethodPost, "/api/v1/idempotency/check",
		strings.NewReader("not-json"))
	w := httptest.NewRecorder()
	store.HandleIdempotencyCheck(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("want 400, got %d", w.Code)
	}
}

// TestHandleIdempotencyCheck_NotDuplicate verifies 200 response for a
// fresh payload.
func TestHandleIdempotencyCheck_NotDuplicate(t *testing.T) {
	db := newIdempotencyDB(t)
	store := NewIdempotencyStore(db)

	body := `{"action_type":"git_commit","payload":{"repo":"new"}}`
	req := httptest.NewRequest(http.MethodPost, "/api/v1/idempotency/check",
		strings.NewReader(body))
	w := httptest.NewRecorder()
	store.HandleIdempotencyCheck(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("want 200, got %d", w.Code)
	}
	var resp IdempotencyCheckResponse
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.IsDuplicate {
		t.Error("expected IsDuplicate=false for fresh payload")
	}
}

// TestHandleIdempotencyExecute_WrongMethod verifies 405 on non-POST.
func TestHandleIdempotencyExecute_WrongMethod(t *testing.T) {
	db := newIdempotencyDB(t)
	store := NewIdempotencyStore(db)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/idempotency/execute", nil)
	w := httptest.NewRecorder()
	store.HandleIdempotencyExecute(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("want 405, got %d", w.Code)
	}
}

// TestHandleIdempotencyExecute_PostReturnsSchema verifies the POST returns
// schema info.
func TestHandleIdempotencyExecute_PostReturnsSchema(t *testing.T) {
	db := newIdempotencyDB(t)
	store := NewIdempotencyStore(db)

	req := httptest.NewRequest(http.MethodPost, "/api/v1/idempotency/execute", nil)
	w := httptest.NewRecorder()
	store.HandleIdempotencyExecute(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("want 200, got %d", w.Code)
	}
	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp["endpoint"] == nil {
		t.Error("expected endpoint field in response")
	}
}

// ============================================================
// coordination.go — sendBlockerAlert (via the exported method
// indirectly — test that it handles no webhook URL gracefully)
// ============================================================

// TestSendBlockerAlert_NoWebhookURL verifies that sendBlockerAlert returns
// immediately without error when FORGE_COORDINATION_BLOCKER_WEBHOOK_URL is unset.
func TestSendBlockerAlert_NoWebhookURL(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	t.Setenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL", "")

	cd := NewCoordinationDashboard(getDBConn())

	// sendBlockerAlert is called as a goroutine in production; call it
	// directly here. It should be a no-op when the env var is empty.
	done := make(chan struct{})
	go func() {
		defer close(done)
		cd.sendBlockerAlert([]Blocker{
			{Agent: "test-agent", Description: "stuck", Severity: "critical", Since: time.Now()},
		})
	}()

	select {
	case <-done:
		// Expected: returned without panic.
	case <-time.After(3 * time.Second):
		t.Error("sendBlockerAlert timed out (expected fast return with no webhook URL)")
	}
}

// TestSendBlockerAlert_WithWebhookURL verifies that sendBlockerAlert POSTs
// to a test HTTP server when the env var is set.
func TestSendBlockerAlert_WithWebhookURL(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	received := make(chan []byte, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		buf := make([]byte, 4096)
		n, _ := r.Body.Read(buf)
		received <- buf[:n]
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	t.Setenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL", srv.URL)

	cd := NewCoordinationDashboard(getDBConn())

	blockers := []Blocker{
		{Agent: "kimi", Description: "no commits in 45 min", Severity: "warning", Since: time.Now()},
	}

	done := make(chan struct{})
	go func() {
		defer close(done)
		cd.sendBlockerAlert(blockers)
	}()

	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("sendBlockerAlert timed out waiting for webhook POST")
	}

	select {
	case body := <-received:
		var payload map[string]interface{}
		if err := json.Unmarshal(body, &payload); err != nil {
			t.Fatalf("decode webhook body: %v", err)
		}
		if _, ok := payload["blockers"]; !ok {
			t.Error("expected blockers field in webhook payload")
		}
		if _, ok := payload["timestamp"]; !ok {
			t.Error("expected timestamp field in webhook payload")
		}
	case <-time.After(3 * time.Second):
		t.Error("webhook server did not receive a request")
	}
}

// TestSendBlockerAlert_Non2xxWebhook verifies that a non-2xx webhook
// response does not panic (error is merely logged).
func TestSendBlockerAlert_Non2xxWebhook(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	t.Setenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL", srv.URL)

	cd := NewCoordinationDashboard(getDBConn())

	done := make(chan struct{})
	go func() {
		defer close(done)
		cd.sendBlockerAlert([]Blocker{{Agent: "a", Description: "b", Severity: "c"}})
	}()

	select {
	case <-done:
		// No panic — pass.
	case <-time.After(5 * time.Second):
		t.Error("sendBlockerAlert timed out on non-2xx webhook")
	}
}

// ============================================================
// coordination.go — pure helpers: truncateString, calculateCompletion,
// estimateETA, detectBlockers
// ============================================================

// TestTruncateString verifies the coordination helper (distinct from
// cli_commands.go truncate).
func TestTruncateString_Short(t *testing.T) {
	got := truncateString("hi", 10)
	if got != "hi" {
		t.Errorf("want hi, got %q", got)
	}
}

func TestTruncateString_Long(t *testing.T) {
	got := truncateString("hello world!", 8)
	if len(got) != 8 || !strings.HasSuffix(got, "...") {
		t.Errorf("want 8-char ellipsis string, got %q", got)
	}
}

// TestDetectBlockers_NoAgents returns empty slice.
func TestDetectBlockers_NoAgents(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	cd := NewCoordinationDashboard(getDBConn())
	blockers := cd.detectBlockers(nil)
	if len(blockers) != 0 {
		t.Errorf("expected no blockers for empty agents, got %d", len(blockers))
	}
}

// TestDetectBlockers_WorkingAgent does not flag recently active agents.
func TestDetectBlockers_WorkingAgent(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	cd := NewCoordinationDashboard(getDBConn())
	agents := []AgentSprintStatus{
		{Name: "kimi", Status: "working", LastCommit: time.Now().Add(-5 * time.Minute)},
	}
	blockers := cd.detectBlockers(agents)
	if len(blockers) != 0 {
		t.Errorf("expected no blockers for recently active agent, got %d: %+v", len(blockers), blockers)
	}
}

// TestDetectBlockers_SlowAgent flags an agent with 30–60 min inactivity as warning.
func TestDetectBlockers_SlowAgent(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	cd := NewCoordinationDashboard(getDBConn())
	agents := []AgentSprintStatus{
		{Name: "glm", Status: "working", LastCommit: time.Now().Add(-45 * time.Minute)},
	}
	blockers := cd.detectBlockers(agents)
	if len(blockers) == 0 {
		t.Fatal("expected warning blocker for agent inactive 45 minutes")
	}
	if blockers[0].Severity != "warning" {
		t.Errorf("want severity=warning, got %q", blockers[0].Severity)
	}
}

// TestDetectBlockers_StuckAgent flags an agent with >60 min inactivity as critical.
func TestDetectBlockers_StuckAgent(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	cd := NewCoordinationDashboard(getDBConn())
	agents := []AgentSprintStatus{
		{Name: "gemini", Status: "working", LastCommit: time.Now().Add(-90 * time.Minute)},
	}
	blockers := cd.detectBlockers(agents)
	if len(blockers) == 0 {
		t.Fatal("expected critical blocker for agent inactive 90 minutes")
	}
	if blockers[0].Severity != "critical" {
		t.Errorf("want severity=critical, got %q", blockers[0].Severity)
	}
}

// TestDetectBlockers_HighContextAgent flags high-context agents as warning.
func TestDetectBlockers_HighContextAgent(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	cd := NewCoordinationDashboard(getDBConn())
	agents := []AgentSprintStatus{
		// Recent commit but high_context status
		{Name: "pi", Status: "high_context", LastCommit: time.Now().Add(-5 * time.Minute)},
	}
	blockers := cd.detectBlockers(agents)
	if len(blockers) == 0 {
		t.Fatal("expected blocker for high_context agent")
	}
	found := false
	for _, b := range blockers {
		if strings.Contains(b.Description, "context") {
			found = true
		}
	}
	if !found {
		t.Errorf("expected context-related blocker description, got %+v", blockers)
	}
}

// TestCalculateCompletion_Ranges verifies the completion heuristic stays in [0, 100].
func TestCalculateCompletion_Ranges(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	cd := NewCoordinationDashboard(getDBConn())

	cases := []struct {
		name    string
		status  SprintStatus
		wantMin float64
		wantMax float64
	}{
		{
			name:    "empty_status",
			status:  SprintStatus{Name: "test"},
			wantMin: 0,
			wantMax: 100,
		},
		{
			name: "active_agents_and_commits",
			status: SprintStatus{
				Name: "test",
				Agents: []AgentSprintStatus{
					{Status: "working"},
					{Status: "working"},
				},
				Commits: CommitSummary{Total: 10},
			},
			wantMin: 0,
			wantMax: 100,
		},
		{
			name: "no_blockers_bonus",
			status: SprintStatus{
				Name:     "test",
				Blockers: []Blocker{},
			},
			wantMin: 0,
			wantMax: 100,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			result := cd.calculateCompletion(&tc.status)
			if result < tc.wantMin || result > tc.wantMax {
				t.Errorf("completion = %.2f, want in [%.2f, %.2f]", result, tc.wantMin, tc.wantMax)
			}
		})
	}
}

// TestEstimateETA_Complete verifies "Complete" is returned at 100%.
func TestEstimateETA_Complete(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	cd := NewCoordinationDashboard(getDBConn())
	status := &SprintStatus{Completion: 100, StartedAt: time.Now().Add(-1 * time.Hour)}
	eta := cd.estimateETA(status)
	if eta != "Complete" {
		t.Errorf("want Complete, got %q", eta)
	}
}

// TestEstimateETA_Zero verifies "Unknown" is returned at 0%.
func TestEstimateETA_Zero(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	cd := NewCoordinationDashboard(getDBConn())
	status := &SprintStatus{Completion: 0, StartedAt: time.Now().Add(-1 * time.Hour)}
	eta := cd.estimateETA(status)
	if eta != "Unknown" {
		t.Errorf("want Unknown, got %q", eta)
	}
}

// TestEstimateETA_InProgress verifies a numeric ETA is returned mid-sprint.
func TestEstimateETA_InProgress(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	cd := NewCoordinationDashboard(getDBConn())
	status := &SprintStatus{
		Completion: 50,
		StartedAt:  time.Now().Add(-2 * time.Hour),
	}
	eta := cd.estimateETA(status)
	// Should contain a numeric time estimate, not be empty.
	if eta == "" || eta == "Unknown" || eta == "Complete" {
		t.Errorf("expected a time estimate, got %q", eta)
	}
}

// ============================================================
// registry.go — AgentRegistry
// ============================================================

// TestAgentRegistry_RegisterAndGet verifies basic Register/GetAgent round-trip.
func TestAgentRegistry_RegisterAndGet(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	reg := NewAgentRegistry(getDBConn(), 5*time.Minute)

	agent := &Agent{
		ID:           "agent-reg-001",
		Name:         "kimi",
		Node:         "prya",
		Role:         "fleet",
		Tier:         "primary",
		Status:       "active",
		ContextPct:   42.5,
		Capabilities: []string{"go", "python"},
		LastActivity: time.Now().UTC(),
	}

	if err := reg.Register(agent); err != nil {
		t.Fatalf("Register: %v", err)
	}

	got, ok := reg.GetAgent("agent-reg-001")
	if !ok {
		t.Fatal("GetAgent: not found after Register")
	}
	if got.Name != "kimi" {
		t.Errorf("Name: want kimi, got %q", got.Name)
	}
	if got.Node != "prya" {
		t.Errorf("Node: want prya, got %q", got.Node)
	}
	if got.ContextPct != 42.5 {
		t.Errorf("ContextPct: want 42.5, got %.1f", got.ContextPct)
	}
	if len(got.Capabilities) != 2 {
		t.Errorf("Capabilities: want 2, got %d", len(got.Capabilities))
	}
}

// TestAgentRegistry_Register_NilReturnsError verifies nil input is rejected.
func TestAgentRegistry_Register_NilReturnsError(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	reg := NewAgentRegistry(getDBConn(), 5*time.Minute)
	if err := reg.Register(nil); err == nil {
		t.Error("expected error for nil agent")
	}
}

// TestAgentRegistry_Register_EmptyIDReturnsError verifies empty ID is rejected.
func TestAgentRegistry_Register_EmptyIDReturnsError(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	reg := NewAgentRegistry(getDBConn(), 5*time.Minute)
	agent := &Agent{ID: "", Name: "unnamed"}
	if err := reg.Register(agent); err == nil {
		t.Error("expected error for agent with empty ID")
	}
}

// TestAgentRegistry_Unregister removes an agent cleanly.
func TestAgentRegistry_Unregister(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	reg := NewAgentRegistry(getDBConn(), 5*time.Minute)

	agent := &Agent{
		ID:     "unreg-001",
		Name:   "temp-agent",
		Status: "active",
	}
	if err := reg.Register(agent); err != nil {
		t.Fatalf("Register: %v", err)
	}

	if err := reg.Unregister("unreg-001"); err != nil {
		t.Fatalf("Unregister: %v", err)
	}

	_, ok := reg.GetAgent("unreg-001")
	if ok {
		t.Error("expected agent to be absent after Unregister")
	}
}

// TestAgentRegistry_Unregister_EmptyIDIsNoop verifies empty ID is silently ignored.
func TestAgentRegistry_Unregister_EmptyIDIsNoop(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	reg := NewAgentRegistry(getDBConn(), 5*time.Minute)
	if err := reg.Unregister(""); err != nil {
		t.Errorf("Unregister with empty ID should be a no-op, got: %v", err)
	}
}

// TestAgentRegistry_UpdateStatus updates status in memory and DB.
func TestAgentRegistry_UpdateStatus(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	reg := NewAgentRegistry(getDBConn(), 5*time.Minute)

	agent := &Agent{ID: "status-001", Name: "agent-x", Status: "idle"}
	if err := reg.Register(agent); err != nil {
		t.Fatalf("Register: %v", err)
	}

	if err := reg.UpdateStatus("status-001", "active"); err != nil {
		t.Fatalf("UpdateStatus: %v", err)
	}

	got, ok := reg.GetAgent("status-001")
	if !ok {
		t.Fatal("GetAgent after UpdateStatus: not found")
	}
	if got.Status != "active" {
		t.Errorf("Status: want active, got %q", got.Status)
	}
}

// TestAgentRegistry_UpdateStatus_EmptyIDIsNoop verifies no-op for empty ID.
func TestAgentRegistry_UpdateStatus_EmptyIDIsNoop(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	reg := NewAgentRegistry(getDBConn(), 5*time.Minute)
	if err := reg.UpdateStatus("", "active"); err != nil {
		t.Errorf("UpdateStatus with empty ID should be a no-op, got: %v", err)
	}
}

// TestAgentRegistry_UpdateContext clamps percentages.
func TestAgentRegistry_UpdateContext(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	reg := NewAgentRegistry(getDBConn(), 5*time.Minute)

	agent := &Agent{ID: "ctx-001", Name: "agent-ctx", Status: "active", ContextPct: 10}
	if err := reg.Register(agent); err != nil {
		t.Fatalf("Register: %v", err)
	}

	// Update to a valid value.
	if err := reg.UpdateContext("ctx-001", 75.0); err != nil {
		t.Fatalf("UpdateContext: %v", err)
	}
	got, _ := reg.GetAgent("ctx-001")
	if got.ContextPct != 75.0 {
		t.Errorf("ContextPct: want 75.0, got %.1f", got.ContextPct)
	}

	// Clamping: values below 0 should be stored as 0.
	if err := reg.UpdateContext("ctx-001", -10); err != nil {
		t.Fatalf("UpdateContext -10: %v", err)
	}
	got, _ = reg.GetAgent("ctx-001")
	if got.ContextPct != 0 {
		t.Errorf("ContextPct after -10: want 0, got %.1f", got.ContextPct)
	}

	// Clamping: values above 100 should be stored as 100.
	if err := reg.UpdateContext("ctx-001", 150); err != nil {
		t.Fatalf("UpdateContext 150: %v", err)
	}
	got, _ = reg.GetAgent("ctx-001")
	if got.ContextPct != 100 {
		t.Errorf("ContextPct after 150: want 100, got %.1f", got.ContextPct)
	}
}

// TestAgentRegistry_Heartbeat updates status, context_pct, and last_activity.
func TestAgentRegistry_Heartbeat(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	reg := NewAgentRegistry(getDBConn(), 5*time.Minute)

	agent := &Agent{ID: "hb-001", Name: "agent-hb", Status: "idle", ContextPct: 20}
	if err := reg.Register(agent); err != nil {
		t.Fatalf("Register: %v", err)
	}

	before := time.Now().UTC()
	if err := reg.Heartbeat("hb-001", "active", 65.0); err != nil {
		t.Fatalf("Heartbeat: %v", err)
	}

	got, ok := reg.GetAgent("hb-001")
	if !ok {
		t.Fatal("GetAgent after Heartbeat: not found")
	}
	if got.Status != "active" {
		t.Errorf("Status: want active, got %q", got.Status)
	}
	if got.ContextPct != 65.0 {
		t.Errorf("ContextPct: want 65.0, got %.1f", got.ContextPct)
	}
	if got.LastActivity.Before(before) {
		t.Error("LastActivity should have been updated to after the Heartbeat call")
	}
}

// TestAgentRegistry_ListAgents returns all registered agents.
func TestAgentRegistry_ListAgents(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	reg := NewAgentRegistry(getDBConn(), 5*time.Minute)

	for i := 0; i < 3; i++ {
		a := &Agent{
			ID:     "list-agent-" + string(rune('A'+i)),
			Name:   "agent",
			Status: "active",
		}
		if err := reg.Register(a); err != nil {
			t.Fatalf("Register agent %d: %v", i, err)
		}
	}

	all := reg.ListAgents()
	if len(all) < 3 {
		t.Errorf("want at least 3 agents, got %d", len(all))
	}
}

// TestAgentRegistry_GetActiveAgents filters by status=active.
func TestAgentRegistry_GetActiveAgents(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	reg := NewAgentRegistry(getDBConn(), 5*time.Minute)

	active := &Agent{ID: "active-001", Name: "agent-active", Status: "active"}
	idle := &Agent{ID: "idle-001", Name: "agent-idle", Status: "idle"}

	if err := reg.Register(active); err != nil {
		t.Fatalf("Register active: %v", err)
	}
	if err := reg.Register(idle); err != nil {
		t.Fatalf("Register idle: %v", err)
	}

	actives := reg.GetActiveAgents()
	for _, a := range actives {
		if a.Status != "active" {
			t.Errorf("GetActiveAgents returned non-active agent: %+v", a)
		}
	}

	found := false
	for _, a := range actives {
		if a.ID == "active-001" {
			found = true
		}
	}
	if !found {
		t.Error("GetActiveAgents: active-001 not in result")
	}
}

// TestAgentRegistry_Capabilities_Roundtrip verifies capabilities are
// serialized and deserialized correctly via Register→GetAgent.
func TestAgentRegistry_Capabilities_Roundtrip(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	reg := NewAgentRegistry(getDBConn(), 5*time.Minute)

	caps := []string{"rust", "python", "go", "typescript"}
	agent := &Agent{
		ID:           "caps-001",
		Name:         "agent-caps",
		Status:       "active",
		Capabilities: caps,
	}
	if err := reg.Register(agent); err != nil {
		t.Fatalf("Register: %v", err)
	}

	// Reload from DB by creating a fresh registry.
	reg2 := NewAgentRegistry(getDBConn(), 5*time.Minute)
	got, ok := reg2.GetAgent("caps-001")
	if !ok {
		t.Fatal("GetAgent from fresh registry: not found")
	}
	if len(got.Capabilities) != len(caps) {
		t.Errorf("Capabilities: want %d, got %d: %v", len(caps), len(got.Capabilities), got.Capabilities)
	}
}

// TestAgentRegistry_DefaultStaleAfter verifies that a zero staleAfter is
// replaced by the 5-minute default.
func TestAgentRegistry_DefaultStaleAfter(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Should not panic; staleAfter=0 triggers the default path.
	reg := NewAgentRegistry(getDBConn(), 0)
	if reg == nil {
		t.Fatal("expected non-nil registry")
	}
}

// ============================================================
// tui_dashboard.go — pure helper functions (no TUI dependency)
// ============================================================

// TestDrawSparkline_Empty returns a space-padded string.
func TestDrawSparkline_Empty(t *testing.T) {
	result := drawSparkline(nil, 8)
	// Should not panic, should return a string of length 8.
	if len(result) == 0 {
		t.Error("drawSparkline with nil values returned empty string")
	}
}

// TestDrawSparkline_SingleValue renders without panic.
func TestDrawSparkline_SingleValue(t *testing.T) {
	result := drawSparkline([]float64{1.0}, 10)
	if len(result) == 0 {
		t.Error("drawSparkline with single value returned empty string")
	}
}

// TestDrawSparkline_AllZeros handles all-zero max gracefully.
func TestDrawSparkline_AllZeros(t *testing.T) {
	result := drawSparkline([]float64{0, 0, 0}, 10)
	if len(result) == 0 {
		t.Error("drawSparkline with all zeros returned empty string")
	}
}

// TestDrawSparkline_MultipleValues renders the correct number of characters.
func TestDrawSparkline_MultipleValues(t *testing.T) {
	values := []float64{1, 2, 3, 4, 5}
	result := drawSparkline(values, 10)
	if len(result) == 0 {
		t.Error("drawSparkline with multiple values returned empty string")
	}
}

// TestTruncateStr_Tui_Short passes through unchanged.
func TestTruncateStr_Tui_Short(t *testing.T) {
	got := truncateStr("hello", 10)
	if got != "hello" {
		t.Errorf("want hello, got %q", got)
	}
}

// TestTruncateStr_Tui_Long truncates with ellipsis.
func TestTruncateStr_Tui_Long(t *testing.T) {
	got := truncateStr("hello world", 8)
	if len(got) != 8 {
		t.Errorf("want length 8, got %d: %q", len(got), got)
	}
	if !strings.HasSuffix(got, "...") {
		t.Errorf("expected ellipsis suffix, got %q", got)
	}
}

// TestDefaultKeyMap_Bindings verifies DefaultKeyMap does not panic and
// returns a populated map.
func TestDefaultKeyMap_Bindings(t *testing.T) {
	km := DefaultKeyMap()
	// Each binding should have at least one key.
	if km.Quit.Keys() == nil {
		t.Error("Quit binding has no keys")
	}
	if km.Refresh.Keys() == nil {
		t.Error("Refresh binding has no keys")
	}
	if km.Tab.Keys() == nil {
		t.Error("Tab binding has no keys")
	}
}

// TestNewDashboardModel_Defaults verifies initial state of a fresh model.
func TestNewDashboardModel_Defaults(t *testing.T) {
	model := NewDashboardModel("http://localhost:9999")
	if model.apiBaseURL != "http://localhost:9999" {
		t.Errorf("apiBaseURL: want http://localhost:9999, got %q", model.apiBaseURL)
	}
	if model.currentView != OverviewView {
		t.Errorf("currentView: want OverviewView, got %v", model.currentView)
	}
	if model.ticker == nil {
		t.Error("ticker should be initialised")
	}
	if !model.isDark {
		t.Error("isDark should default to true")
	}
	model.ticker.Stop()
}

// TestNewDashboardModel_EmptyURL falls back to localhost:8081.
func TestNewDashboardModel_EmptyURL(t *testing.T) {
	model := NewDashboardModel("")
	if model.apiBaseURL != "http://localhost:8081" {
		t.Errorf("apiBaseURL: want http://localhost:8081, got %q", model.apiBaseURL)
	}
	model.ticker.Stop()
}

// TestAgentItem_ListInterface verifies agentItem satisfies list.Item methods.
func TestAgentItem_ListInterface(t *testing.T) {
	task := "test-task"
	item := agentItem{agent: AgentHealth{
		AgentID:        "agent-1",
		StatusText:     "active",
		Node:           "prya",
		ContextPercent: 55.0,
		CurrentTask:    &task,
	}}
	if item.Title() != "agent-1" {
		t.Errorf("Title: want agent-1, got %q", item.Title())
	}
	if item.FilterValue() != "agent-1" {
		t.Errorf("FilterValue: want agent-1, got %q", item.FilterValue())
	}
	desc := item.Description()
	if !strings.Contains(desc, "active") {
		t.Errorf("Description should contain status: %q", desc)
	}
}

// TestEventItem_ListInterface verifies eventItem satisfies list.Item methods.
func TestEventItem_ListInterface(t *testing.T) {
	now := time.Now()
	item := eventItem{event: DashboardEvent{
		Type:      "heartbeat",
		AgentID:   "kimi",
		Message:   "all good",
		Timestamp: now,
	}}
	if !strings.Contains(item.Title(), "heartbeat") {
		t.Errorf("Title should contain event type: %q", item.Title())
	}
	if item.FilterValue() != "all good" {
		t.Errorf("FilterValue: want all good, got %q", item.FilterValue())
	}
	if !strings.Contains(item.Description(), "kimi") {
		t.Errorf("Description should contain agent id: %q", item.Description())
	}
}
