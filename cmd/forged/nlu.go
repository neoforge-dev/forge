//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"math"
	"strings"
	"sync"
	"time"
	"unicode"
)

// Intent type constants for voice commands.
const (
	IntentStatus       = "status"
	IntentDecide       = "decide"
	IntentDecideAll    = "decide_all"
	IntentTaskList     = "task_list"
	IntentTaskDetail   = "task_detail"
	IntentAgentStatus  = "agent_status"
	IntentNodeStatus   = "node_status"
	IntentPauseTask    = "pause_task"
	IntentResumeTask   = "resume_task"
	IntentDisambiguate = "disambiguate"
	IntentConfirm      = "confirm"
	IntentCancel       = "cancel"
	IntentHelp         = "help"
	IntentUnknown      = "unknown"
)

// VoiceVocabulary holds all known FORGE terms organised by category.
type VoiceVocabulary struct {
	Nodes    []string
	Agents   []string
	Domains  []string
	Commands []string
}

// defaultVoiceVocabulary is the static fallback used when no live DB data is
// available.
var defaultVoiceVocabulary = &VoiceVocabulary{
	Nodes:   []string{"prya", "sati", "nova", "vega", "gaea"},
	Agents:  []string{"kimi", "minimax", "glm", "gemini", "pi", "kilo", "opencode", "amp", "cursor", "codex"},
	Domains: []string{},
	Commands: []string{
		"approve", "reject", "defer", "extend", "pause", "resume",
		"complete", "kill", "rollback", "dispatch", "create",
		"show", "list", "status", "check", "help",
	},
}

// NLUResult is the parsed intent from a voice utterance.
type NLUResult struct {
	Intent     string            `json:"intent"`
	Entities   map[string]string `json:"entities"` // agent, node, task_id, action, domain
	Confidence float64           `json:"confidence"`
	RawText    string            `json:"raw_text"`
}

// PendingAction holds a destructive action waiting for explicit confirmation.
type PendingAction struct {
	Intent      string
	Action      string
	TargetID    string
	Title       string
	Destructive bool
}

// ConversationState tracks multi-turn session context.
type ConversationState struct {
	SessionID             string
	LastIntent            string
	LastEntities          map[string]string
	PendingDisambiguation []string // list of option IDs
	PendingConfirmation   *PendingAction
	ExpiresAt             time.Time
}

// ConversationStore is an in-memory LRU-ish store for sessions (max 50, 60s TTL).
type ConversationStore struct {
	mu       sync.Mutex
	sessions map[string]*ConversationState
}

var globalConversationStore = &ConversationStore{
	sessions: make(map[string]*ConversationState),
}

// Get returns a session, creating a fresh one if not found or expired.
func (cs *ConversationStore) Get(sessionID string) *ConversationState {
	cs.mu.Lock()
	defer cs.mu.Unlock()
	cs.evict()
	s, ok := cs.sessions[sessionID]
	if !ok || time.Now().After(s.ExpiresAt) {
		s = &ConversationState{
			SessionID:    sessionID,
			LastEntities: make(map[string]string),
			ExpiresAt:    time.Now().Add(60 * time.Second),
		}
		cs.sessions[sessionID] = s
	}
	return s
}

// Put saves a session back (and refreshes TTL).
func (cs *ConversationStore) Put(s *ConversationState) {
	cs.mu.Lock()
	defer cs.mu.Unlock()
	s.ExpiresAt = time.Now().Add(60 * time.Second)
	cs.sessions[s.SessionID] = s
	cs.evict()
}

// evict removes expired entries and caps at 50 sessions.  Must be called with
// mu held.
func (cs *ConversationStore) evict() {
	now := time.Now()
	for id, s := range cs.sessions {
		if now.After(s.ExpiresAt) {
			delete(cs.sessions, id)
		}
	}
	// Evict oldest if still over cap.
	for len(cs.sessions) > 50 {
		for id := range cs.sessions {
			delete(cs.sessions, id)
			break
		}
	}
}

// ---------------------------------------------------------------------------
// Double Metaphone (simplified, produces 4-char primary and secondary codes)
// ---------------------------------------------------------------------------

// doubleMetaphone returns a simplified Double Metaphone primary and secondary
// phonetic code for English words.  The implementation covers the most common
// English phonetic patterns and is specifically tuned to match FORGE agent/node
// names correctly under typical voice-recognition noise.
func doubleMetaphone(s string) (primary, secondary string) {
	if s == "" {
		return "", ""
	}

	// Normalise: upper-case, strip non-alpha.
	var runes []rune
	for _, r := range strings.ToUpper(s) {
		if unicode.IsLetter(r) {
			runes = append(runes, r)
		}
	}
	if len(runes) == 0 {
		return "", ""
	}
	word := string(runes)
	n := len(word)

	var p, sec strings.Builder

	addBoth := func(c string) {
		p.WriteString(c)
		sec.WriteString(c)
	}
	addDiff := func(primary2, secondary2 string) {
		p.WriteString(primary2)
		sec.WriteString(secondary2)
	}

	i := 0

	// Skip leading vowel — vowels at start map to A, otherwise silent.
	if isVowel(word[0]) {
		addBoth("A")
		i = 1
	}

	for i < n {
		c := word[i]
		switch c {
		case 'B':
			// Silent B at end.
			addBoth("P")
			i++
		case 'C':
			if i > 0 && word[i-1] == 'S' && i+1 < n && isVowel(word[i+1]) {
				i++
				continue
			}
			if i+1 < n && word[i+1] == 'H' {
				addDiff("X", "K")
				i += 2
				continue
			}
			if i+1 < n && (word[i+1] == 'I' || word[i+1] == 'E' || word[i+1] == 'Y') {
				addBoth("S")
				i++
				continue
			}
			if i+1 < n && word[i+1] == 'K' {
				i++ // skip K
			}
			addBoth("K")
			i++
		case 'D':
			if i+1 < n && word[i+1] == 'G' {
				if i+2 < n && (word[i+2] == 'I' || word[i+2] == 'E' || word[i+2] == 'Y') {
					addBoth("J")
					i += 3
					continue
				}
			}
			addBoth("T")
			i++
		case 'F':
			addBoth("F")
			i++
			if i < n && word[i] == 'F' {
				i++
			}
		case 'G':
			if i+1 < n && word[i+1] == 'H' {
				if i == 0 {
					addBoth("K")
					i += 2
					continue
				}
				if i > 0 && isVowel(word[i-1]) {
					i += 2
					continue
				}
			}
			if i+1 < n && (word[i+1] == 'N') {
				if i == 0 {
					i++
					continue
				}
			}
			if i+1 < n && (word[i+1] == 'I' || word[i+1] == 'E' || word[i+1] == 'Y') {
				addBoth("J")
				i++
				continue
			}
			addBoth("K")
			i++
		case 'H':
			if isVowel(safeAt(word, i+1)) && (i == 0 || !isVowel(safeAt(word, i-1))) {
				addBoth("H")
			}
			i++
		case 'J':
			addBoth("J")
			i++
		case 'K':
			if i > 0 && word[i-1] == 'C' {
				i++
				continue
			}
			addBoth("K")
			i++
		case 'L':
			addBoth("L")
			i++
		case 'M':
			addBoth("M")
			i++
		case 'N':
			addBoth("N")
			i++
		case 'P':
			if i+1 < n && word[i+1] == 'H' {
				addBoth("F")
				i += 2
				continue
			}
			addBoth("P")
			i++
		case 'Q':
			addBoth("K")
			i++
		case 'R':
			addBoth("R")
			i++
		case 'S':
			if i+1 < n && word[i+1] == 'H' {
				addBoth("X")
				i += 2
				continue
			}
			if i+1 < n && word[i+1] == 'I' && i+2 < n && (word[i+2] == 'O' || word[i+2] == 'A') {
				addBoth("X")
				i++
				continue
			}
			addBoth("S")
			i++
		case 'T':
			if i+1 < n && word[i+1] == 'H' {
				addBoth("0")
				i += 2
				continue
			}
			if i+1 < n && word[i+1] == 'I' && i+2 < n && (word[i+2] == 'A' || word[i+2] == 'O') {
				addBoth("X")
				i++
				continue
			}
			addBoth("T")
			i++
		case 'V':
			addBoth("F")
			i++
		case 'W':
			if i+1 < n && isVowel(word[i+1]) {
				addBoth("A")
			}
			i++
		case 'X':
			addBoth("KS")
			i++
		case 'Y':
			if i+1 < n && isVowel(word[i+1]) {
				addBoth("A")
			}
			i++
		case 'Z':
			addBoth("S")
			i++
		default:
			// Vowels inside word — skip silently.
			i++
		}
	}

	// Truncate to 4 chars for compact comparison.
	pStr := p.String()
	sStr := sec.String()
	if len(pStr) > 4 {
		pStr = pStr[:4]
	}
	if len(sStr) > 4 {
		sStr = sStr[:4]
	}
	return pStr, sStr
}

func isVowel(c byte) bool {
	switch c {
	case 'A', 'E', 'I', 'O', 'U', 'a', 'e', 'i', 'o', 'u':
		return true
	}
	return false
}

func safeAt(s string, i int) byte {
	if i < 0 || i >= len(s) {
		return 0
	}
	return s[i]
}

// ---------------------------------------------------------------------------
// Levenshtein distance
// ---------------------------------------------------------------------------

func levenshtein(a, b string) int {
	ra := []rune(a)
	rb := []rune(b)
	la, lb := len(ra), len(rb)
	if la == 0 {
		return lb
	}
	if lb == 0 {
		return la
	}
	prev := make([]int, lb+1)
	curr := make([]int, lb+1)
	for j := 0; j <= lb; j++ {
		prev[j] = j
	}
	for i := 1; i <= la; i++ {
		curr[0] = i
		for j := 1; j <= lb; j++ {
			cost := 1
			if ra[i-1] == rb[j-1] {
				cost = 0
			}
			curr[j] = minInt(curr[j-1]+1, minInt(prev[j]+1, prev[j-1]+cost))
		}
		prev, curr = curr, prev
	}
	return prev[lb]
}

// ---------------------------------------------------------------------------
// Term matching
// ---------------------------------------------------------------------------

// matchTerm returns the best match from vocabulary and a confidence in [0,1].
func matchTerm(input string, vocabulary []string) (match string, confidence float64) {
	if len(vocabulary) == 0 {
		return "", 0
	}
	norm := strings.ToLower(strings.TrimSpace(input))
	if norm == "" {
		return "", 0
	}

	// Exact match.
	for _, v := range vocabulary {
		if strings.ToLower(v) == norm {
			return v, 1.0
		}
	}

	ip, is := doubleMetaphone(norm)
	bestMatch := ""
	bestScore := 0.0

	for _, v := range vocabulary {
		vl := strings.ToLower(v)
		vp, vs := doubleMetaphone(vl)

		// Phonetic match score.
		phoneticScore := 0.0
		if ip != "" && vp != "" {
			if ip == vp {
				phoneticScore = 0.9
			} else if is != "" && is == vs {
				phoneticScore = 0.85
			} else if ip == vs || is == vp {
				phoneticScore = 0.8
			}
		}

		// Levenshtein edit-distance score.
		maxLen := len([]rune(norm))
		if len([]rune(vl)) > maxLen {
			maxLen = len([]rune(vl))
		}
		dist := levenshtein(norm, vl)
		editScore := 0.0
		if maxLen > 0 {
			editScore = 1.0 - float64(dist)/float64(maxLen)
		}

		// Prefix bonus.
		prefixScore := 0.0
		shorter := norm
		longer := vl
		if len(longer) < len(shorter) {
			shorter, longer = longer, shorter
		}
		if len(shorter) >= 2 && strings.HasPrefix(longer, shorter) {
			prefixScore = 0.7
		}

		score := math.Max(phoneticScore, math.Max(editScore*0.85, prefixScore))
		if score > bestScore {
			bestScore = score
			bestMatch = v
		}
	}

	if bestScore < 0.5 {
		return "", 0
	}
	return bestMatch, bestScore
}

// ---------------------------------------------------------------------------
// Entity extraction
// ---------------------------------------------------------------------------

// extractEntities identifies agent, node, action, task_id, and domain tokens
// from the slice of lower-cased tokens.
func extractEntities(tokens []string, vocab *VoiceVocabulary) map[string]string {
	entities := make(map[string]string)

	for _, tok := range tokens {
		// Skip stop words.
		if isStopWord(tok) {
			continue
		}
		// Agent match.
		if _, ok := entities["agent"]; !ok {
			if m, conf := matchTerm(tok, vocab.Agents); conf >= 0.75 {
				entities["agent"] = m
				continue
			}
		}
		// Node match.
		if _, ok := entities["node"]; !ok {
			if m, conf := matchTerm(tok, vocab.Nodes); conf >= 0.75 {
				entities["node"] = m
				continue
			}
		}
		// Action / command match.
		if _, ok := entities["action"]; !ok {
			if m, conf := matchTerm(tok, vocab.Commands); conf >= 0.85 {
				entities["action"] = m
				continue
			}
		}
		// Domain match.
		if _, ok := entities["domain"]; !ok {
			if len(vocab.Domains) > 0 {
				if m, conf := matchTerm(tok, vocab.Domains); conf >= 0.8 {
					entities["domain"] = m
					continue
				}
			}
		}
	}
	return entities
}

var stopWords = map[string]bool{
	"the": true, "a": true, "an": true, "is": true, "are": true,
	"to": true, "for": true, "of": true, "on": true, "in": true,
	"it": true, "this": true, "that": true, "and": true, "or": true,
	"how": true, "what": true, "show": true, "me": true, "all": true,
	"s": true, "from": true, "please": true, "can": true, "you": true,
}

func isStopWord(s string) bool {
	return stopWords[strings.ToLower(s)]
}

// ---------------------------------------------------------------------------
// Destructive / confirmation rules
// ---------------------------------------------------------------------------

// isDestructive returns true when the action string is considered irreversible.
func isDestructive(action string) bool {
	switch strings.ToLower(action) {
	case "kill", "rollback", "reject", "delete", "remove":
		return true
	}
	return false
}

// needsConfirmation returns (needsConfirm, requiresDoubleConfirm) for an intent
// and its action.
func needsConfirmation(intent, action string) (needs bool, doubleConfirm bool) {
	switch intent {
	case IntentDecideAll:
		return true, false
	case IntentDecide:
		if isDestructive(action) {
			return true, true
		}
	case IntentPauseTask, IntentResumeTask:
		return false, false
	}
	return false, false
}

// ---------------------------------------------------------------------------
// Intent parser
// ---------------------------------------------------------------------------

// parseIntent is the top-level NLU entry point.  It uses session context for
// multi-turn disambiguation and confirmation flows, then falls back to a
// keyword and entity matching pipeline.
func parseIntent(text string, vocab *VoiceVocabulary, session *ConversationState) *NLUResult {
	if vocab == nil {
		vocab = defaultVoiceVocabulary
	}
	raw := strings.TrimSpace(text)
	lower := strings.ToLower(raw)
	tokens := tokenise(lower)

	result := &NLUResult{
		RawText:  raw,
		Entities: make(map[string]string),
	}

	// --- Priority 1: pending disambiguation ---
	if session != nil && len(session.PendingDisambiguation) > 0 {
		if idx := resolveOrdinal(tokens); idx >= 0 && idx < len(session.PendingDisambiguation) {
			result.Intent = IntentDisambiguate
			result.Entities["selected_id"] = session.PendingDisambiguation[idx]
			result.Confidence = 0.95
			return result
		}
	}

	// --- Priority 2: pending confirmation ---
	if session != nil && session.PendingConfirmation != nil {
		if containsAny(tokens, []string{"yes", "confirm", "do it", "proceed", "affirmative"}) {
			result.Intent = IntentConfirm
			result.Confidence = 0.97
			return result
		}
		if containsAny(tokens, []string{"no", "cancel", "stop", "abort", "negative"}) {
			result.Intent = IntentCancel
			result.Confidence = 0.97
			return result
		}
	}

	// --- Priority 3: explicit cancel / help ---
	if containsAny(tokens, []string{"cancel", "stop", "abort", "never mind"}) {
		result.Intent = IntentCancel
		result.Confidence = 0.9
		return result
	}
	if containsAny(tokens, []string{"help", "what can", "commands", "options"}) {
		result.Intent = IntentHelp
		result.Confidence = 0.9
		return result
	}

	// --- Priority 4: entity extraction ---
	entities := extractEntities(tokens, vocab)
	for k, v := range entities {
		result.Entities[k] = v
	}

	// --- Priority 5: verb-object intent matching ---
	intent, confidence := matchVerbIntent(tokens, entities)
	result.Intent = intent
	result.Confidence = confidence

	return result
}

// matchVerbIntent maps tokens + entities to an intent and confidence.
func matchVerbIntent(tokens []string, entities map[string]string) (intent string, confidence float64) {
	hasToken := func(want ...string) bool { return containsAny(tokens, want) }

	// "approve all" / "decide all" / "approve everything"
	if hasToken("approve", "reject", "defer") && hasToken("all", "everything", "pending") {
		return IntentDecideAll, 0.88
	}

	// decide / approval actions
	if hasToken("approve", "reject", "defer", "extend") {
		action := ""
		for _, t := range tokens {
			switch t {
			case "approve":
				action = "approve"
			case "reject":
				action = "reject"
			case "defer":
				action = "defer"
			case "extend":
				action = "extend"
			}
		}
		if action != "" {
			entities["action"] = action
		}
		return IntentDecide, 0.85
	}

	// task list
	if hasToken("list", "tasks", "queue", "show") && hasToken("tasks", "queue", "work") {
		return IntentTaskList, 0.82
	}
	if hasToken("tasks") && hasToken("all", "pending", "running") {
		return IntentTaskList, 0.8
	}

	// task detail
	if hasToken("task", "detail", "info", "about") && (hasToken("task") || entities["task_id"] != "") {
		return IntentTaskDetail, 0.78
	}

	// pause / resume task
	if hasToken("pause", "stop", "halt") && hasToken("task") {
		return IntentPauseTask, 0.82
	}
	if hasToken("resume", "continue", "restart") && hasToken("task") {
		return IntentResumeTask, 0.82
	}

	// agent status
	if _, ok := entities["agent"]; ok {
		if hasToken("status", "health", "check", "how") {
			return IntentAgentStatus, 0.85
		}
	}

	// node status
	if _, ok := entities["node"]; ok {
		if hasToken("status", "health", "check", "how") {
			return IntentNodeStatus, 0.85
		}
	}

	// generic status
	if hasToken("status", "health", "overview", "summary", "dashboard") {
		return IntentStatus, 0.82
	}

	// fallback using entity hints
	if _, ok := entities["agent"]; ok {
		return IntentAgentStatus, 0.6
	}
	if _, ok := entities["node"]; ok {
		return IntentNodeStatus, 0.6
	}

	return IntentUnknown, 0.0
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// tokenise lower-cases and splits text into word tokens.
func tokenise(s string) []string {
	lower := strings.ToLower(s)
	// Replace punctuation with spaces.
	var b strings.Builder
	for _, r := range lower {
		if unicode.IsLetter(r) || unicode.IsDigit(r) || r == '\'' {
			b.WriteRune(r)
		} else {
			b.WriteRune(' ')
		}
	}
	return strings.Fields(b.String())
}

// containsAny returns true if any of the needles appears in tokens.
func containsAny(tokens []string, needles []string) bool {
	set := make(map[string]bool, len(tokens))
	for _, t := range tokens {
		set[t] = true
	}
	// Also check raw joined string for multi-word needles.
	joined := strings.Join(tokens, " ")
	for _, n := range needles {
		if set[n] {
			return true
		}
		if strings.Contains(n, " ") && strings.Contains(joined, n) {
			return true
		}
	}
	return false
}

// resolveOrdinal maps words like "one", "first", "1" to a 0-based index.
func resolveOrdinal(tokens []string) int {
	ordinals := map[string]int{
		"1": 0, "one": 0, "first": 0,
		"2": 1, "two": 1, "second": 1,
		"3": 2, "three": 2, "third": 2,
		"4": 3, "four": 3, "fourth": 3,
		"5": 4, "five": 4, "fifth": 4,
	}
	for _, t := range tokens {
		if idx, ok := ordinals[t]; ok {
			return idx
		}
	}
	return -1
}
