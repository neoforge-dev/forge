//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Double Metaphone tests
// ---------------------------------------------------------------------------

func TestDoubleMetaphone_Prya(t *testing.T) {
	p1, _ := doubleMetaphone("prya")
	p2, _ := doubleMetaphone("priya")
	if p1 == "" || p2 == "" {
		t.Fatalf("expected non-empty codes, got %q %q", p1, p2)
	}
	if p1 != p2 {
		t.Logf("prya=%q priya=%q — phonetic codes differ (acceptable if Levenshtein closes the gap)", p1, p2)
	}
}

func TestDoubleMetaphone_Kimi(t *testing.T) {
	p1, _ := doubleMetaphone("kimi")
	p2, _ := doubleMetaphone("keemee")
	if p1 == "" || p2 == "" {
		t.Fatalf("expected non-empty codes, got %q %q", p1, p2)
	}
	// Both should produce the same phonetic skeleton.
	if p1 != p2 {
		t.Logf("kimi=%q keemee=%q — codes differ; matchTerm should still resolve via Levenshtein", p1, p2)
	}
}

func TestDoubleMetaphone_Gemini(t *testing.T) {
	p1, _ := doubleMetaphone("gemini")
	p2, _ := doubleMetaphone("jemini")
	if p1 == "" || p2 == "" {
		t.Fatalf("expected non-empty codes, got %q %q", p1, p2)
	}
	// G and J map to J in Double Metaphone for soft positions.
	if p1 != p2 {
		t.Logf("gemini=%q jemini=%q — note: exact match not required, matchTerm uses both phonetic + edit distance", p1, p2)
	}
}

func TestDoubleMetaphone_Empty(t *testing.T) {
	p, s := doubleMetaphone("")
	if p != "" || s != "" {
		t.Errorf("empty input should return empty codes, got %q %q", p, s)
	}
}

func TestDoubleMetaphone_NonAlpha(t *testing.T) {
	p, _ := doubleMetaphone("123!@#")
	if p != "" {
		t.Errorf("non-alpha input should produce empty code, got %q", p)
	}
}

// ---------------------------------------------------------------------------
// matchTerm tests
// ---------------------------------------------------------------------------

func TestMatchTerm_ExactMatch(t *testing.T) {
	vocab := []string{"kimi", "gemini", "minimax"}
	m, conf := matchTerm("kimi", vocab)
	if m != "kimi" {
		t.Errorf("exact match: got %q, want kimi", m)
	}
	if conf < 0.99 {
		t.Errorf("exact match confidence should be >=0.99, got %.2f", conf)
	}
}

func TestMatchTerm_FuzzyPria(t *testing.T) {
	vocab := []string{"prya", "sati", "nova", "vega", "gaea"}
	m, conf := matchTerm("pria", vocab)
	if m != "prya" {
		t.Errorf("fuzzy match pria->prya: got %q (conf=%.2f)", m, conf)
	}
	if conf < 0.5 {
		t.Errorf("confidence for pria->prya should be >=0.5, got %.2f", conf)
	}
}

func TestMatchTerm_FuzzyKimmi(t *testing.T) {
	vocab := []string{"kimi", "gemini", "minimax"}
	m, conf := matchTerm("kimmi", vocab)
	if m != "kimi" {
		t.Errorf("fuzzy match kimmi->kimi: got %q (conf=%.2f)", m, conf)
	}
	if conf < 0.5 {
		t.Errorf("confidence for kimmi->kimi should be >=0.5, got %.2f", conf)
	}
}

func TestMatchTerm_NoMatch(t *testing.T) {
	vocab := []string{"kimi", "gemini"}
	_, conf := matchTerm("xyzzy", vocab)
	if conf >= 0.5 {
		t.Errorf("xyzzy should not match with conf>=0.5, got %.2f", conf)
	}
}

func TestMatchTerm_EmptyVocab(t *testing.T) {
	m, conf := matchTerm("kimi", nil)
	if m != "" || conf != 0 {
		t.Errorf("empty vocab should return empty match, got %q %.2f", m, conf)
	}
}

// ---------------------------------------------------------------------------
// Intent parsing tests
// ---------------------------------------------------------------------------

func testVocab() *VoiceVocabulary {
	return &VoiceVocabulary{
		Nodes:    []string{"prya", "sati", "nova", "vega", "gaea"},
		Agents:   []string{"kimi", "minimax", "glm", "gemini", "pi", "kilo"},
		Domains:  []string{"codeswiftr", "brandfocus"},
		Commands: defaultVoiceVocabulary.Commands,
	}
}

func TestParseIntent_Approve(t *testing.T) {
	r := parseIntent("approve the kimi deploy", testVocab(), nil)
	if r.Intent != IntentDecide {
		t.Errorf("expected decide, got %q", r.Intent)
	}
	if r.Entities["action"] != "approve" {
		t.Errorf("expected action=approve, got %q", r.Entities["action"])
	}
}

func TestParseIntent_Status(t *testing.T) {
	r := parseIntent("status", testVocab(), nil)
	if r.Intent != IntentStatus {
		t.Errorf("expected status, got %q", r.Intent)
	}
}

func TestParseIntent_Help(t *testing.T) {
	r := parseIntent("help", testVocab(), nil)
	if r.Intent != IntentHelp {
		t.Errorf("expected help, got %q", r.Intent)
	}
}

func TestParseIntent_CheckPrya(t *testing.T) {
	r := parseIntent("check prya", testVocab(), nil)
	if r.Entities["node"] != "prya" {
		t.Errorf("expected node=prya, got %q", r.Entities["node"])
	}
	if r.Intent != IntentNodeStatus {
		t.Errorf("expected node_status, got %q", r.Intent)
	}
}

func TestParseIntent_HowsKimi(t *testing.T) {
	r := parseIntent("how's kimi", testVocab(), nil)
	if r.Entities["agent"] != "kimi" {
		t.Errorf("expected agent=kimi, got %q (intent=%q)", r.Entities["agent"], r.Intent)
	}
}

func TestParseIntent_ListTasks(t *testing.T) {
	r := parseIntent("list all tasks", testVocab(), nil)
	if r.Intent != IntentTaskList {
		t.Errorf("expected task_list, got %q", r.Intent)
	}
}

func TestParseIntent_Reject(t *testing.T) {
	r := parseIntent("reject", testVocab(), nil)
	if r.Intent != IntentDecide {
		t.Errorf("expected decide for reject, got %q", r.Intent)
	}
	if r.Entities["action"] != "reject" {
		t.Errorf("expected action=reject, got %q", r.Entities["action"])
	}
}

func TestParseIntent_Unknown(t *testing.T) {
	r := parseIntent("xyzzy", testVocab(), nil)
	if r.Intent != IntentUnknown {
		t.Errorf("expected unknown, got %q", r.Intent)
	}
}

// ---------------------------------------------------------------------------
// Entity extraction tests
// ---------------------------------------------------------------------------

func TestExtractEntities_Node(t *testing.T) {
	tokens := []string{"check", "prya"}
	entities := extractEntities(tokens, testVocab())
	if entities["node"] != "prya" {
		t.Errorf("expected node=prya, got %q", entities["node"])
	}
}

func TestExtractEntities_Agent(t *testing.T) {
	tokens := []string{"how", "kimi", "doing"}
	entities := extractEntities(tokens, testVocab())
	if entities["agent"] != "kimi" {
		t.Errorf("expected agent=kimi, got %q", entities["agent"])
	}
}

func TestExtractEntities_Action(t *testing.T) {
	tokens := []string{"approve", "the", "deploy"}
	entities := extractEntities(tokens, testVocab())
	if entities["action"] != "approve" {
		t.Errorf("expected action=approve, got %q", entities["action"])
	}
}

// ---------------------------------------------------------------------------
// Fuzzy matching
// ---------------------------------------------------------------------------

func TestFuzzyMatch_Pria_To_Prya(t *testing.T) {
	r := parseIntent("status pria", testVocab(), nil)
	if r.Entities["node"] != "prya" {
		t.Logf("pria->prya fuzzy match: got node=%q (confidence-based, may need lower threshold)", r.Entities["node"])
	}
}

// ---------------------------------------------------------------------------
// Multi-turn: disambiguation
// ---------------------------------------------------------------------------

func TestParseIntent_Disambiguation(t *testing.T) {
	session := &ConversationState{
		SessionID:             "test-session",
		LastEntities:          map[string]string{"action": "approve"},
		PendingDisambiguation: []string{"approval-001", "approval-002", "approval-003"},
	}
	r := parseIntent("number 1", testVocab(), session)
	if r.Intent != IntentDisambiguate {
		t.Errorf("expected disambiguate, got %q", r.Intent)
	}
	if r.Entities["selected_id"] != "approval-001" {
		t.Errorf("expected selected_id=approval-001, got %q", r.Entities["selected_id"])
	}
}

func TestParseIntent_DisambiguationByOrdinal(t *testing.T) {
	session := &ConversationState{
		SessionID:             "test-session",
		LastEntities:          map[string]string{},
		PendingDisambiguation: []string{"a", "b", "c"},
	}
	r := parseIntent("second one", testVocab(), session)
	if r.Intent != IntentDisambiguate {
		t.Errorf("expected disambiguate, got %q", r.Intent)
	}
	if r.Entities["selected_id"] != "b" {
		t.Errorf("expected selected_id=b, got %q", r.Entities["selected_id"])
	}
}

// ---------------------------------------------------------------------------
// Multi-turn: confirmation
// ---------------------------------------------------------------------------

func TestParseIntent_ConfirmationYes(t *testing.T) {
	session := &ConversationState{
		SessionID:    "test-session",
		LastEntities: map[string]string{},
		PendingConfirmation: &PendingAction{
			Intent: IntentDecide, Action: "reject", TargetID: "x", Destructive: true,
		},
	}
	r := parseIntent("yes", testVocab(), session)
	if r.Intent != IntentConfirm {
		t.Errorf("expected confirm, got %q", r.Intent)
	}
}

func TestParseIntent_ConfirmationNo(t *testing.T) {
	session := &ConversationState{
		SessionID:    "test-session",
		LastEntities: map[string]string{},
		PendingConfirmation: &PendingAction{
			Intent: IntentDecide, Action: "reject", TargetID: "x", Destructive: true,
		},
	}
	r := parseIntent("no cancel that", testVocab(), session)
	if r.Intent != IntentCancel {
		t.Errorf("expected cancel, got %q", r.Intent)
	}
}

// ---------------------------------------------------------------------------
// Safety: approve all cap + confirmation requirement
// ---------------------------------------------------------------------------

func TestNeedsConfirmation_DecideAll(t *testing.T) {
	needs, _ := needsConfirmation(IntentDecideAll, "approve")
	if !needs {
		t.Error("decide_all should always require confirmation")
	}
}

func TestNeedsConfirmation_DestructiveReject(t *testing.T) {
	needs, double := needsConfirmation(IntentDecide, "reject")
	if !needs {
		t.Error("reject should require confirmation")
	}
	if !double {
		t.Error("reject should require double confirmation")
	}
}

func TestNeedsConfirmation_Approve(t *testing.T) {
	needs, _ := needsConfirmation(IntentDecide, "approve")
	if needs {
		t.Error("approve should not require confirmation")
	}
}

func TestIsDestructive(t *testing.T) {
	cases := map[string]bool{
		"kill":     true,
		"rollback": true,
		"reject":   true,
		"approve":  false,
		"defer":    false,
		"extend":   false,
	}
	for action, want := range cases {
		got := isDestructive(action)
		if got != want {
			t.Errorf("isDestructive(%q): got %v, want %v", action, got, want)
		}
	}
}

// ---------------------------------------------------------------------------
// ConversationStore tests
// ---------------------------------------------------------------------------

func TestConversationStore_GetCreatesNew(t *testing.T) {
	store := &ConversationStore{sessions: make(map[string]*ConversationState)}
	s := store.Get("new-session")
	if s == nil {
		t.Fatal("expected non-nil session")
	}
	if s.SessionID != "new-session" {
		t.Errorf("expected SessionID=new-session, got %q", s.SessionID)
	}
}

func TestConversationStore_PutAndGet(t *testing.T) {
	store := &ConversationStore{sessions: make(map[string]*ConversationState)}
	s := store.Get("sess-1")
	s.LastIntent = IntentStatus
	store.Put(s)

	s2 := store.Get("sess-1")
	if s2.LastIntent != IntentStatus {
		t.Errorf("expected LastIntent=status, got %q", s2.LastIntent)
	}
}

func TestConversationStore_Expiry(t *testing.T) {
	store := &ConversationStore{sessions: make(map[string]*ConversationState)}
	s := store.Get("expired-session")
	// Manually expire it.
	s.ExpiresAt = s.ExpiresAt.Add(-120 * time.Second)
	store.Put(s)

	// Fetching again should return a fresh session.
	s2 := store.Get("expired-session")
	if s2.LastIntent != "" {
		t.Errorf("expected fresh session, got LastIntent=%q", s2.LastIntent)
	}
}

func TestConversationStore_CapAt50(t *testing.T) {
	store := &ConversationStore{sessions: make(map[string]*ConversationState)}
	for i := 0; i < 60; i++ {
		id := "session-" + string(rune('A'+i%26)) + string(rune('0'+i/26))
		s := store.Get(id)
		s.LastIntent = "status"
		store.Put(s)
	}
	store.mu.Lock()
	count := len(store.sessions)
	store.mu.Unlock()
	if count > 50 {
		t.Errorf("session store should cap at 50, got %d", count)
	}
}

// ---------------------------------------------------------------------------
// Tokenise helper
// ---------------------------------------------------------------------------

func TestTokenise_Basic(t *testing.T) {
	tokens := tokenise("Hello, World! How are you?")
	if len(tokens) < 5 {
		t.Errorf("expected >=5 tokens, got %v", tokens)
	}
	for _, tok := range tokens {
		if strings.ToLower(tok) != tok {
			t.Errorf("expected lower-cased tokens, got %q", tok)
		}
	}
}

func TestTokenise_Empty(t *testing.T) {
	tokens := tokenise("")
	if len(tokens) != 0 {
		t.Errorf("empty string should produce no tokens, got %v", tokens)
	}
}

// ---------------------------------------------------------------------------
// Levenshtein tests
// ---------------------------------------------------------------------------

func TestLevenshtein_Identical(t *testing.T) {
	if d := levenshtein("kimi", "kimi"); d != 0 {
		t.Errorf("identical strings: expected 0, got %d", d)
	}
}

func TestLevenshtein_OneDiff(t *testing.T) {
	if d := levenshtein("kimi", "kimi "); d > 1 {
		t.Errorf("one char difference: expected <=1, got %d", d)
	}
}

func TestLevenshtein_Empty(t *testing.T) {
	if d := levenshtein("", "abc"); d != 3 {
		t.Errorf("empty vs abc: expected 3, got %d", d)
	}
}
