//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// messageRelayPatrol is the patrol action that processes cross-node inbox messages
// delivered via git-tracked JSONL files.  It runs every 60 seconds.
//
// For each unacknowledged message it:
//
//   - type "task"      → creates a forge task in the DB
//   - type "directive" → logs + stores as a task_event
//   - type "status"    → updates node status in the nodes table
//   - type "result"    → writes .forge/heartbeat/results/{from}-{id}.md
//   - type "ack"       → logs only (already handled by the sender)
//   - type "research"  → logs + stores as a task_event
//
// After processing, it writes an ack back to
// .forge/messages/outbox/{source}-acks.jsonl and marks the inbox entry as ack'd.
func messageRelayPatrol(ctx context.Context, db *sql.DB) error {
	msgs, err := ReadInbox()
	if err != nil {
		return fmt.Errorf("message-relay patrol: read inbox: %w", err)
	}
	if len(msgs) == 0 {
		return nil
	}

	// Dedup: load already-seen IDs from task_events within the last 7 days.
	seenIDs, err := loadSeenMessageIDs(ctx, db)
	if err != nil {
		// Non-fatal: we'll re-process duplicates harmlessly.
		log.Printf("[Patrol:message-relay] WARN: could not load seen IDs: %v", err)
		seenIDs = map[string]struct{}{}
	}

	processed := 0
	for _, msg := range msgs {
		if _, dup := seenIDs[msg.ID]; dup {
			// Already processed — just ack and skip.
			_ = AckMessage(msg.ID)
			continue
		}

		var processErr error
		switch msg.Type {
		case MsgTypeTask:
			processErr = relayHandleTask(ctx, db, msg)
		case MsgTypeDirective:
			processErr = relayHandleDirective(ctx, db, msg)
		case MsgTypeStatus:
			processErr = relayHandleStatus(ctx, db, msg)
		case MsgTypeResult:
			processErr = relayHandleResult(ctx, db, msg)
		case MsgTypeResearch:
			processErr = relayHandleResearch(ctx, db, msg)
		case MsgTypeAck:
			log.Printf("[Patrol:message-relay] ack from %s: %s", msg.From, msg.Subject)
		default:
			log.Printf("[Patrol:message-relay] unknown message type %q from %s — skipping", msg.Type, msg.From)
		}

		if processErr != nil {
			log.Printf("[Patrol:message-relay] error processing msg %s (%s): %v", msg.ID, msg.Type, processErr)
			// Continue to next message; don't ack so it's retried next cycle.
			continue
		}

		// Record in task_events for audit trail.
		_ = persistMessageEvent(ctx, db, msg)

		// Write ack back to the sender's outbox.
		_ = writeAck(msg)

		// Mark inbox entry as processed.
		if err := AckMessage(msg.ID); err != nil {
			log.Printf("[Patrol:message-relay] ack-inbox error for %s: %v", msg.ID, err)
		}

		processed++
	}

	if processed > 0 {
		log.Printf("[Patrol:message-relay] processed %d/%d inbox messages", processed, len(msgs))
	}
	return nil
}

// ── Handlers ─────────────────────────────────────────────────────────────────

// relayHandleTask creates a forge task from a task-type message.
func relayHandleTask(ctx context.Context, db *sql.DB, msg Message) error {
	if db == nil {
		return fmt.Errorf("no database connection")
	}

	// Parse optional JSON body for structured task fields.
	var meta struct {
		Domain   string `json:"domain"`
		Project  string `json:"project"`
		Type     string `json:"type"`
		Priority int    `json:"priority"`
	}
	// Defaults
	meta.Domain = "infrastructure"
	meta.Project = "cross-node"
	meta.Type = "research"
	meta.Priority = 5

	if msg.Body != "" && strings.HasPrefix(strings.TrimSpace(msg.Body), "{") {
		_ = json.Unmarshal([]byte(msg.Body), &meta)
	}
	if meta.Domain == "" {
		meta.Domain = "infrastructure"
	}
	if meta.Project == "" {
		meta.Project = "cross-node"
	}
	if meta.Type == "" {
		meta.Type = "research"
	}
	if meta.Priority == 0 {
		meta.Priority = 5
	}

	taskID := fmt.Sprintf("relay-%d", time.Now().UnixNano())
	_, err := db.ExecContext(ctx, `
		INSERT INTO tasks
			(id, domain, project, type, title, description, priority, status, state, created_at, updated_at)
		VALUES
			(?, ?, ?, ?, ?, ?, ?, 'queued', 'QUEUED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
	`, taskID, meta.Domain, meta.Project, meta.Type, msg.Subject, msg.Body, meta.Priority)
	if err != nil {
		return fmt.Errorf("insert relay task: %w", err)
	}
	log.Printf("[Patrol:message-relay] created task %s from node %s: %s", taskID, msg.From, msg.Subject)
	return nil
}

// relayHandleDirective logs the directive and stores it as a task_event.
func relayHandleDirective(ctx context.Context, db *sql.DB, msg Message) error {
	log.Printf("[Patrol:message-relay] directive from %s: %s", msg.From, msg.Subject)
	// Stored via persistMessageEvent — nothing extra to do here.
	return nil
}

// relayHandleStatus updates the sender's node status in the nodes table.
func relayHandleStatus(ctx context.Context, db *sql.DB, msg Message) error {
	if db == nil {
		return nil
	}
	_, err := db.ExecContext(ctx, `
		UPDATE nodes SET status = 'online', last_heartbeat = CURRENT_TIMESTAMP
		WHERE id = ?
	`, msg.From)
	if err != nil {
		return fmt.Errorf("update node status: %w", err)
	}
	log.Printf("[Patrol:message-relay] status update from node %s", msg.From)
	return nil
}

// relayHandleResult writes the result body to
// .forge/heartbeat/results/{from}-{id}.md so the local orchestrator can pick it up.
func relayHandleResult(_ context.Context, _ *sql.DB, msg Message) error {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	resultsDir := filepath.Join(root, ".forge", "heartbeat", "results")
	if err := os.MkdirAll(resultsDir, 0755); err != nil {
		return fmt.Errorf("create results dir: %w", err)
	}

	// Sanitise the message ID to produce a safe filename.
	safeID := strings.ReplaceAll(msg.ID, "/", "-")
	path := filepath.Join(resultsDir, fmt.Sprintf("%s-%s.md", msg.From, safeID))

	content := fmt.Sprintf("# Result from %s\n\n**Subject:** %s\n**Timestamp:** %s\n\n%s\n",
		msg.From, msg.Subject, msg.Timestamp.Format(time.RFC3339), msg.Body)

	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		return fmt.Errorf("write result file: %w", err)
	}
	log.Printf("[Patrol:message-relay] result from %s written to %s", msg.From, path)
	return nil
}

// relayHandleResearch logs and persists the message (no extra action needed).
func relayHandleResearch(_ context.Context, _ *sql.DB, msg Message) error {
	log.Printf("[Patrol:message-relay] research from %s: %s", msg.From, msg.Subject)
	return nil
}

// ── Persistence helpers ───────────────────────────────────────────────────────

// persistMessageEvent stores a Message in task_events for the audit trail.
func persistMessageEvent(ctx context.Context, db *sql.DB, msg Message) error {
	if db == nil {
		return nil
	}

	payload, err := json.Marshal(msg)
	if err != nil {
		return err
	}

	// task_id is synthetic for relay messages; use msg.ID.
	_, err = db.ExecContext(ctx, `
		INSERT OR IGNORE INTO task_events (task_id, domain, project, event_type, payload, created_at)
		VALUES (?, 'infrastructure', 'cross-node', ?, ?, CURRENT_TIMESTAMP)
	`, msg.ID, "relay_message_"+string(msg.Type), payload)
	return err
}

// loadSeenMessageIDs queries task_events for relay message IDs stored in the
// last 7 days, returning them as a set for O(1) dedup lookups.
func loadSeenMessageIDs(ctx context.Context, db *sql.DB) (map[string]struct{}, error) {
	if db == nil {
		return map[string]struct{}{}, nil
	}

	cutoff := time.Now().Add(-7 * 24 * time.Hour).UTC().Format(time.RFC3339)
	rows, err := db.QueryContext(ctx, `
		SELECT task_id FROM task_events
		WHERE event_type LIKE 'relay_message_%'
		AND created_at >= ?
	`, cutoff)
	if err != nil {
		if err == sql.ErrNoRows {
			return map[string]struct{}{}, nil
		}
		return nil, err
	}
	defer rows.Close()

	seen := map[string]struct{}{}
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err == nil {
			seen[id] = struct{}{}
		}
	}
	return seen, rows.Err()
}

// writeAck sends an ack message back to the original sender via the local outbox.
func writeAck(original Message) error {
	ackBody := fmt.Sprintf(`{"acked_id":"%s","acked_at":"%s"}`,
		original.ID, time.Now().UTC().Format(time.RFC3339))

	target := original.From
	// Acks go to a separate file: {sender}-acks.jsonl
	// We use SendMessage but override the target to use the acks suffix.
	outDir := filepath.Join(messagesDir(), "outbox")
	if err := os.MkdirAll(outDir, 0755); err != nil {
		return err
	}
	ackPath := filepath.Join(outDir, target+"-acks.jsonl")

	ack := Message{
		ID:        generateMsgID(),
		From:      nodeID(),
		To:        target,
		Timestamp: time.Now().UTC(),
		Type:      MsgTypeAck,
		Subject:   "ack:" + original.Subject,
		Body:      ackBody,
		Ack:       false,
	}
	line, err := json.Marshal(ack)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(ackPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = fmt.Fprintf(f, "%s\n", line)
	return err
}
