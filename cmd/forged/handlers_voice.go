//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/oklog/ulid/v2"
)

// voiceCommandRequest is the body of POST /api/voice/command.
type voiceCommandRequest struct {
	Text      string `json:"text"`
	Context   string `json:"context"`    // e.g. "decisions", "tasks"
	SessionID string `json:"session_id"` // multi-turn session key
	Confirm   bool   `json:"confirm"`    // explicit confirmation for pending action
}

// voiceCommandResponse is the payload returned by POST /api/voice/command.
type voiceCommandResponse struct {
	Intent             string            `json:"intent"`
	Params             map[string]string `json:"params"`
	Response           string            `json:"response"`
	Executed           bool              `json:"executed"`
	NeedsConfirmation  bool              `json:"needs_confirmation"`
	ConfirmationPrompt string            `json:"confirmation_prompt,omitempty"`
	Confidence         float64           `json:"confidence"`
	SessionID          string            `json:"session_id"`
}

// voiceVocabCache is a short-lived in-memory cache for the vocabulary endpoint.
type voiceVocabCache struct {
	mu        sync.Mutex
	data      *voiceVocabularyResponse
	fetchedAt time.Time
}

var globalVocabCache = &voiceVocabCache{}

// voiceVocabularyResponse is the payload returned by GET /api/voice/vocabulary.
type voiceVocabularyResponse struct {
	Nodes                 []string `json:"nodes"`
	Agents                []string `json:"agents"`
	Domains               []string `json:"domains"`
	Commands              []string `json:"commands"`
	ActiveTaskTitles      []string `json:"active_task_titles"`
	PendingDecisionTitles []string `json:"pending_decision_titles"`
}

// ---------------------------------------------------------------------------
// POST /api/voice/command
// ---------------------------------------------------------------------------

func voiceCommandHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req voiceCommandRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON body: "+err.Error(), http.StatusBadRequest)
		return
	}
	if strings.TrimSpace(req.Text) == "" {
		http.Error(w, "text field is required", http.StatusBadRequest)
		return
	}
	if req.SessionID == "" {
		req.SessionID = ulid.Make().String()
	}

	ctx := r.Context()
	db := getDBConn()

	// Retrieve or create session state.
	session := globalConversationStore.Get(req.SessionID)

	// Build live vocabulary for better NLU accuracy.
	vocab := buildLiveVocab(ctx, db)

	// Handle explicit confirmation of a pending action.
	if req.Confirm && session.PendingConfirmation != nil {
		resp := executePendingAction(ctx, session, req.SessionID)
		session.PendingConfirmation = nil
		globalConversationStore.Put(session)
		logVoiceCmd(db, req, resp)
		writeJSONVoice(w, resp)
		return
	}

	// Parse intent from utterance.
	nlu := parseIntent(req.Text, vocab, session)

	// If session has pending confirmation but user didn't pass confirm=true,
	// check for natural-language yes/no in the NLU result.
	if session.PendingConfirmation != nil {
		if nlu.Intent == IntentConfirm {
			resp := executePendingAction(ctx, session, req.SessionID)
			session.PendingConfirmation = nil
			globalConversationStore.Put(session)
			logVoiceCmd(db, req, resp)
			writeJSONVoice(w, resp)
			return
		}
		if nlu.Intent == IntentCancel {
			session.PendingConfirmation = nil
			globalConversationStore.Put(session)
			resp := voiceCommandResponse{
				Intent:    IntentCancel,
				Response:  "Action cancelled.",
				Executed:  false,
				SessionID: req.SessionID,
			}
			logVoiceCmd(db, req, resp)
			writeJSONVoice(w, resp)
			return
		}
	}

	// Resolve disambiguated selection.
	if nlu.Intent == IntentDisambiguate {
		selectedID := nlu.Entities["selected_id"]
		session.PendingDisambiguation = nil
		nlu.Intent = IntentDecide
		nlu.Entities["approval_id"] = selectedID
		if nlu.Entities["action"] == "" {
			nlu.Entities["action"] = session.LastEntities["action"]
		}
	}

	// Update session context.
	session.LastIntent = nlu.Intent
	for k, v := range nlu.Entities {
		session.LastEntities[k] = v
	}

	resp := dispatchVoiceIntent(ctx, db, nlu, req.SessionID, session)
	globalConversationStore.Put(session)
	logVoiceCmd(db, req, resp)
	writeJSONVoice(w, resp)
}

// dispatchVoiceIntent routes an NLU result to the appropriate handler.
func dispatchVoiceIntent(ctx context.Context, db *sql.DB, nlu *NLUResult, sessionID string, session *ConversationState) voiceCommandResponse {
	params := make(map[string]string)
	for k, v := range nlu.Entities {
		params[k] = v
	}

	switch nlu.Intent {
	case IntentStatus:
		return handleVoiceStatus(ctx, sessionID, nlu.Confidence)

	case IntentDecide:
		return handleVoiceDecide(ctx, nlu, sessionID, session)

	case IntentDecideAll:
		return handleVoiceDecideAll(ctx, nlu, sessionID, session)

	case IntentTaskList:
		return handleVoiceTaskList(ctx, db, sessionID, nlu.Confidence)

	case IntentAgentStatus:
		return handleVoiceAgentStatus(ctx, db, nlu, sessionID)

	case IntentNodeStatus:
		nodeName := nlu.Entities["node"]
		return voiceCommandResponse{
			Intent:     IntentNodeStatus,
			Params:     params,
			Response:   fmt.Sprintf("Node %s status is not yet implemented.", nodeName),
			Executed:   false,
			SessionID:  sessionID,
			Confidence: nlu.Confidence,
		}

	case IntentHelp:
		return voiceCommandResponse{
			Intent:     IntentHelp,
			Response:   "You can say: status, approve, reject, defer, list tasks, agent status, pause task, resume task, or help.",
			Executed:   false,
			SessionID:  sessionID,
			Confidence: 1.0,
		}

	case IntentCancel:
		return voiceCommandResponse{
			Intent:     IntentCancel,
			Response:   "Nothing to cancel.",
			Executed:   false,
			SessionID:  sessionID,
			Confidence: nlu.Confidence,
		}

	case IntentUnknown:
		return voiceCommandResponse{
			Intent:     IntentUnknown,
			Params:     params,
			Response:   "I didn't understand that. Try: status, approve, reject, list tasks, or help.",
			Executed:   false,
			SessionID:  sessionID,
			Confidence: nlu.Confidence,
		}

	default:
		return voiceCommandResponse{
			Intent:     nlu.Intent,
			Params:     params,
			Response:   fmt.Sprintf("Intent %q is recognised but not yet handled.", nlu.Intent),
			Executed:   false,
			SessionID:  sessionID,
			Confidence: nlu.Confidence,
		}
	}
}

// handleVoiceStatus builds a spoken summary from the watch summary.
func handleVoiceStatus(ctx context.Context, sessionID string, confidence float64) voiceCommandResponse {
	summary := buildWatchSummary(ctx, watchSummaryApprovalService)
	msg := fmt.Sprintf(
		"Fleet status: %d of %d agents online, %d tasks running, %d pending decisions.",
		summary.AgentsOnline, summary.AgentsTotal, summary.TasksRunning, summary.GatesPending,
	)
	return voiceCommandResponse{
		Intent:     IntentStatus,
		Response:   msg,
		Executed:   true,
		SessionID:  sessionID,
		Confidence: confidence,
	}
}

// handleVoiceDecide approves, rejects, or defers a single pending approval.
func handleVoiceDecide(ctx context.Context, nlu *NLUResult, sessionID string, session *ConversationState) voiceCommandResponse {
	action := strings.ToLower(nlu.Entities["action"])
	if action == "" {
		action = "approve"
	}

	approvalID := nlu.Entities["approval_id"]
	approvalTitle := ""

	if approvalID == "" && watchSummaryApprovalService != nil {
		agentID := nlu.Entities["agent"]
		pending, err := watchSummaryApprovalService.GetPending(ctx, 20)
		if err != nil || len(pending) == 0 {
			return voiceCommandResponse{
				Intent:    IntentDecide,
				Response:  "No pending decisions to act on.",
				Executed:  false,
				SessionID: sessionID,
			}
		}
		// Default: first pending item.
		approvalID = pending[0].ID
		approvalTitle = pending[0].Title
		// If an agent was mentioned, prefer their approval.
		if agentID != "" {
			for _, a := range pending {
				if strings.EqualFold(a.AgentID, agentID) {
					approvalID = a.ID
					approvalTitle = a.Title
					break
				}
			}
		}
	}

	// Destructive actions require confirmation.
	if needs, _ := needsConfirmation(IntentDecide, action); needs {
		session.PendingConfirmation = &PendingAction{
			Intent:      IntentDecide,
			Action:      action,
			TargetID:    approvalID,
			Title:       approvalTitle,
			Destructive: isDestructive(action),
		}
		prompt := fmt.Sprintf("Are you sure you want to %s %q? Say yes to confirm or no to cancel.", action, approvalTitle)
		return voiceCommandResponse{
			Intent:             IntentDecide,
			Params:             map[string]string{"approval_id": approvalID, "action": action},
			Response:           prompt,
			Executed:           false,
			NeedsConfirmation:  true,
			ConfirmationPrompt: prompt,
			SessionID:          sessionID,
			Confidence:         nlu.Confidence,
		}
	}

	err := executeApprovalAction(ctx, approvalID, action)
	if err != nil {
		return voiceCommandResponse{
			Intent:    IntentDecide,
			Params:    map[string]string{"approval_id": approvalID, "action": action},
			Response:  fmt.Sprintf("Failed to %s: %s", action, err.Error()),
			Executed:  false,
			SessionID: sessionID,
			Confidence: nlu.Confidence,
		}
	}
	subject := approvalTitle
	if subject == "" {
		subject = approvalID
	}
	return voiceCommandResponse{
		Intent:     IntentDecide,
		Params:     map[string]string{"approval_id": approvalID, "action": action},
		Response:   fmt.Sprintf("Done. %sd %q.", strings.Title(action), subject),
		Executed:   true,
		SessionID:  sessionID,
		Confidence: nlu.Confidence,
	}
}

// handleVoiceDecideAll bulk-acts on pending approvals. Requires confirmation and caps at 5.
func handleVoiceDecideAll(ctx context.Context, nlu *NLUResult, sessionID string, session *ConversationState) voiceCommandResponse {
	action := strings.ToLower(nlu.Entities["action"])
	if action == "" {
		action = "approve"
	}
	if watchSummaryApprovalService == nil {
		return voiceCommandResponse{
			Intent:    IntentDecideAll,
			Response:  "Approval service unavailable.",
			Executed:  false,
			SessionID: sessionID,
		}
	}
	pending, err := watchSummaryApprovalService.GetPending(ctx, 20)
	if err != nil {
		return voiceCommandResponse{Intent: IntentDecideAll, Response: "Could not fetch pending decisions: " + err.Error(), SessionID: sessionID}
	}
	if len(pending) == 0 {
		return voiceCommandResponse{Intent: IntentDecideAll, Response: "No pending decisions.", Executed: false, SessionID: sessionID}
	}
	if len(pending) > 5 {
		return voiceCommandResponse{
			Intent:    IntentDecideAll,
			Response:  fmt.Sprintf("Too many pending decisions (%d). Bulk action is capped at 5. Please resolve them individually.", len(pending)),
			Executed:  false,
			SessionID: sessionID,
		}
	}

	// Always require confirmation for bulk action.
	if session.PendingConfirmation == nil {
		session.PendingConfirmation = &PendingAction{
			Intent:      IntentDecideAll,
			Action:      action,
			TargetID:    "all",
			Destructive: isDestructive(action),
		}
		prompt := fmt.Sprintf("This will %s %d pending decisions. Say yes to confirm.", action, len(pending))
		return voiceCommandResponse{
			Intent:             IntentDecideAll,
			Response:           prompt,
			Executed:           false,
			NeedsConfirmation:  true,
			ConfirmationPrompt: prompt,
			SessionID:          sessionID,
			Confidence:         nlu.Confidence,
		}
	}

	sqlDB := getDBConn()
	count := 0
	for _, a := range pending {
		if err := executeApprovalAction(ctx, a.ID, action); err != nil {
			logVoiceError(sqlDB, sessionID, err)
			continue
		}
		count++
	}
	session.PendingConfirmation = nil
	return voiceCommandResponse{
		Intent:     IntentDecideAll,
		Response:   fmt.Sprintf("%sd %d of %d pending decisions.", strings.Title(action), count, len(pending)),
		Executed:   count > 0,
		SessionID:  sessionID,
		Confidence: nlu.Confidence,
	}
}

// handleVoiceTaskList returns a spoken list of active tasks.
func handleVoiceTaskList(ctx context.Context, db *sql.DB, sessionID string, confidence float64) voiceCommandResponse {
	if db == nil {
		return voiceCommandResponse{Intent: IntentTaskList, Response: "Database unavailable.", SessionID: sessionID}
	}
	rows, err := db.QueryContext(ctx, `
		SELECT title, status FROM tasks
		WHERE status NOT IN ('completed','failed')
		ORDER BY created_at DESC LIMIT 5`)
	if err != nil {
		return voiceCommandResponse{Intent: IntentTaskList, Response: "Could not query tasks.", SessionID: sessionID}
	}
	defer rows.Close()

	var titles []string
	for rows.Next() {
		var title, status string
		if err := rows.Scan(&title, &status); err == nil {
			titles = append(titles, fmt.Sprintf("%s (%s)", title, status))
		}
	}
	if len(titles) == 0 {
		return voiceCommandResponse{Intent: IntentTaskList, Response: "No active tasks.", Executed: true, SessionID: sessionID, Confidence: confidence}
	}
	return voiceCommandResponse{
		Intent:     IntentTaskList,
		Response:   "Active tasks: " + strings.Join(titles, "; ") + ".",
		Executed:   true,
		SessionID:  sessionID,
		Confidence: confidence,
	}
}

// handleVoiceAgentStatus returns a spoken status for a specific agent.
func handleVoiceAgentStatus(ctx context.Context, db *sql.DB, nlu *NLUResult, sessionID string) voiceCommandResponse {
	agentID := nlu.Entities["agent"]
	if db == nil || agentID == "" {
		return voiceCommandResponse{Intent: IntentAgentStatus, Response: "Agent name not recognised.", SessionID: sessionID}
	}
	var status, lastActivity string
	err := db.QueryRowContext(ctx,
		`SELECT COALESCE(status,'unknown'), COALESCE(last_activity,'') FROM agent_heartbeats WHERE agent_id = ? LIMIT 1`,
		agentID,
	).Scan(&status, &lastActivity)
	if err != nil {
		return voiceCommandResponse{
			Intent:     IntentAgentStatus,
			Response:   fmt.Sprintf("No heartbeat found for agent %s.", agentID),
			Executed:   true,
			SessionID:  sessionID,
			Confidence: nlu.Confidence,
		}
	}
	return voiceCommandResponse{
		Intent:     IntentAgentStatus,
		Response:   fmt.Sprintf("Agent %s is %s. Last seen: %s.", agentID, status, lastActivity),
		Executed:   true,
		SessionID:  sessionID,
		Confidence: nlu.Confidence,
	}
}

// executeApprovalAction delegates to the global ApprovalService.
func executeApprovalAction(ctx context.Context, approvalID, action string) error {
	if watchSummaryApprovalService == nil {
		return fmt.Errorf("approval service not initialised — start the daemon first")
	}
	switch strings.ToLower(action) {
	case "approve":
		return watchSummaryApprovalService.Approve(ctx, approvalID, "voice")
	case "reject":
		return watchSummaryApprovalService.Reject(ctx, approvalID, "voice")
	case "defer", "extend":
		return fmt.Errorf("defer action not yet implemented — use the dashboard to defer approvals")
	default:
		return fmt.Errorf("unknown action %q — supported: approve, reject, defer", action)
	}
}

// executePendingAction runs the action stored in session.PendingConfirmation.
func executePendingAction(ctx context.Context, session *ConversationState, sessionID string) voiceCommandResponse {
	pa := session.PendingConfirmation
	if pa == nil {
		return voiceCommandResponse{Response: "No pending action.", SessionID: sessionID}
	}
	if pa.Intent == IntentDecideAll {
		nlu := &NLUResult{Intent: IntentDecideAll, Entities: map[string]string{"action": pa.Action}}
		return handleVoiceDecideAll(ctx, nlu, sessionID, session)
	}
	err := executeApprovalAction(ctx, pa.TargetID, pa.Action)
	if err != nil {
		return voiceCommandResponse{
			Intent:    pa.Intent,
			Response:  fmt.Sprintf("Failed to %s: %s", pa.Action, err.Error()),
			Executed:  false,
			SessionID: sessionID,
		}
	}
	subject := pa.Title
	if subject == "" {
		subject = pa.TargetID
	}
	return voiceCommandResponse{
		Intent:    pa.Intent,
		Response:  fmt.Sprintf("Confirmed. %sd %q.", strings.Title(pa.Action), subject),
		Executed:  true,
		SessionID: sessionID,
	}
}

// ---------------------------------------------------------------------------
// GET /api/voice/vocabulary
// ---------------------------------------------------------------------------

func voiceVocabularyHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	sinceStr := r.URL.Query().Get("since")

	globalVocabCache.mu.Lock()
	cached := globalVocabCache.data
	fetchedAt := globalVocabCache.fetchedAt
	globalVocabCache.mu.Unlock()

	if cached != nil && time.Since(fetchedAt) < 60*time.Second {
		if sinceStr != "" {
			since, err := strconv.ParseInt(sinceStr, 10, 64)
			if err == nil && fetchedAt.Unix() <= since {
				w.WriteHeader(http.StatusNotModified)
				return
			}
		}
		writeJSONVoice(w, cached)
		return
	}

	ctx := r.Context()
	db := getDBConn()
	vocab := buildLiveVocab(ctx, db)

	var activeTitles []string
	if db != nil {
		rows, err := db.QueryContext(ctx, `SELECT title FROM tasks WHERE status NOT IN ('completed','failed') ORDER BY created_at DESC LIMIT 30`)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var t string
				if rows.Scan(&t) == nil {
					activeTitles = append(activeTitles, t)
				}
			}
		}
	}

	var pendingTitles []string
	if watchSummaryApprovalService != nil {
		pending, err := watchSummaryApprovalService.GetPending(ctx, 20)
		if err == nil {
			for _, a := range pending {
				pendingTitles = append(pendingTitles, a.Title)
			}
		}
	}

	if activeTitles == nil {
		activeTitles = []string{}
	}
	if pendingTitles == nil {
		pendingTitles = []string{}
	}

	resp := &voiceVocabularyResponse{
		Nodes:                 vocab.Nodes,
		Agents:                vocab.Agents,
		Domains:               vocab.Domains,
		Commands:              vocab.Commands,
		ActiveTaskTitles:      activeTitles,
		PendingDecisionTitles: pendingTitles,
	}

	globalVocabCache.mu.Lock()
	globalVocabCache.data = resp
	globalVocabCache.fetchedAt = time.Now()
	globalVocabCache.mu.Unlock()

	writeJSONVoice(w, resp)
}

// buildLiveVocab returns a VoiceVocabulary enriched with live data from the DB.
func buildLiveVocab(ctx context.Context, db *sql.DB) *VoiceVocabulary {
	vocab := &VoiceVocabulary{
		Nodes:    defaultVoiceVocabulary.Nodes,
		Agents:   make([]string, len(defaultVoiceVocabulary.Agents)),
		Domains:  []string{},
		Commands: defaultVoiceVocabulary.Commands,
	}
	copy(vocab.Agents, defaultVoiceVocabulary.Agents)

	if db == nil {
		return vocab
	}

	// Merge live agents from heartbeat table.
	rows, err := db.QueryContext(ctx, `SELECT DISTINCT agent_id FROM agent_heartbeats WHERE agent_id != '' LIMIT 50`)
	if err == nil {
		defer rows.Close()
		agentSet := make(map[string]bool, len(defaultVoiceVocabulary.Agents))
		for _, a := range defaultVoiceVocabulary.Agents {
			agentSet[a] = true
		}
		for rows.Next() {
			var id string
			if rows.Scan(&id) == nil && !agentSet[id] {
				vocab.Agents = append(vocab.Agents, id)
				agentSet[id] = true
			}
		}
	}

	// Collect live domains.
	drows, err := db.QueryContext(ctx, `SELECT DISTINCT domain FROM tasks WHERE domain != '' LIMIT 50`)
	if err == nil {
		defer drows.Close()
		for drows.Next() {
			var d string
			if drows.Scan(&d) == nil {
				vocab.Domains = append(vocab.Domains, d)
			}
		}
	}

	return vocab
}

// ---------------------------------------------------------------------------
// POST /api/voice/transcribe  (Phase V3 stub — 501 Not Implemented)
// ---------------------------------------------------------------------------

func voiceTranscribeHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusNotImplemented)
	json.NewEncoder(w).Encode(map[string]string{
		"error":   "not_implemented",
		"message": "Whisper transcription not yet available. Use /api/voice/command with text.",
	})
}

// ---------------------------------------------------------------------------
// GET /api/voice/analytics
// ---------------------------------------------------------------------------

func voiceAnalyticsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	db := getDBConn()
	if db == nil {
		http.Error(w, "database unavailable — ensure the daemon is running with a valid DB path", http.StatusServiceUnavailable)
		return
	}
	analytics, err := getVoiceAnalytics(db)
	if err != nil {
		http.Error(w, "failed to compute analytics: "+err.Error(), http.StatusInternalServerError)
		return
	}
	writeJSONVoice(w, analytics)
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

func writeJSONVoice(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(v); err != nil {
		http.Error(w, "json encode error: "+err.Error(), http.StatusInternalServerError)
	}
}

// logVoiceCmd inserts a command log record (best-effort, never panics).
func logVoiceCmd(db *sql.DB, req voiceCommandRequest, resp voiceCommandResponse) {
	if db == nil {
		return
	}
	_ = logVoiceCommand(db, voiceCommandLog{
		ID:         ulid.Make().String(),
		SessionID:  req.SessionID,
		RawText:    req.Text,
		Intent:     resp.Intent,
		Confidence: resp.Confidence,
		Executed:   resp.Executed,
		Success:    resp.Executed && !resp.NeedsConfirmation,
		CreatedAt:  time.Now().UTC(),
	})
}

// logVoiceError records a non-fatal operational error.
func logVoiceError(db *sql.DB, sessionID string, err error) {
	if db == nil {
		return
	}
	_ = logVoiceCommand(db, voiceCommandLog{
		ID:        ulid.Make().String(),
		SessionID: sessionID,
		RawText:   "",
		Intent:    "error",
		Error:     err.Error(),
		CreatedAt: time.Now().UTC(),
	})
}
