//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/oklog/ulid/v2"
)

// Topic represents an event topic with optional wildcard support
type Topic string

// Event topics as defined in ADR-17
const (
	TopicTask     Topic = "task.*"      // task.created, task.completed, etc.
	TopicAgent    Topic = "agent.*"     // agent.telemetry, agent.disconnected
	TopicApproval Topic = "approval.*"  // approval.requested, approval.granted
	TopicLane     Topic = "lane.*"      // lane.promoted, lane.blocked
	TopicPatrol   Topic = "patrol.*"    // patrol.finding, patrol.resolved
	TopicGit      Topic = "git.*"       // git.commit, git.push, git.conflict
)

// Specific event topics
const (
	TopicTaskCreated     Topic = "task.created"
	TopicTaskCompleted   Topic = "task.completed"
	TopicTaskFailed      Topic = "task.failed"
	TopicTaskAssigned    Topic = "task.assigned"
	TopicAgentTelemetry  Topic = "agent.telemetry"
	TopicAgentConnected  Topic = "agent.connected"
	TopicAgentDisconnected Topic = "agent.disconnected"
	TopicApprovalRequested Topic = "approval.requested"
	TopicApprovalGranted   Topic = "approval.granted"
	TopicApprovalRejected  Topic = "approval.rejected"
	TopicPatrolFinding     Topic = "patrol.finding"
	TopicPatrolResolved    Topic = "patrol.resolved"
)

// BusEvent represents an event on the unified event bus
type BusEvent struct {
	ID        string      `json:"id"`
	Topic     Topic       `json:"topic"`
	Data      interface{} `json:"data"`
	Timestamp time.Time   `json:"timestamp"`
	Sequence  int64       `json:"sequence"`
	AgentID   string      `json:"agent_id,omitempty"`
}

// Subscriber is a callback function that receives events
type Subscriber func(event BusEvent) error

// EventBus is the unified in-process event bus backed by SQLite
type EventBus struct {
	db          *sql.DB
	subscribers map[Topic][]Subscriber
	mu          sync.RWMutex
	sequence    int64
	seqMu       sync.Mutex
}

// NewEventBus creates a new EventBus instance
func NewEventBus(db *sql.DB) (*EventBus, error) {
	if db == nil {
		return nil, fmt.Errorf("database connection is required")
	}

	eb := &EventBus{
		db:          db,
		subscribers: make(map[Topic][]Subscriber),
		sequence:    0,
	}

	// Initialize the database schema for event bus if needed
	if err := eb.initSchema(); err != nil {
		return nil, fmt.Errorf("failed to initialize event bus schema: %w", err)
	}

	// Load the latest sequence number
	if err := eb.loadSequence(); err != nil {
		return nil, fmt.Errorf("failed to load sequence: %w", err)
	}

	return eb, nil
}

// initSchema ensures the events table exists with topic support
func (eb *EventBus) initSchema() error {
	// Check if events table exists
	var count int
	err := eb.db.QueryRow(`
		SELECT COUNT(*) FROM sqlite_master 
		WHERE type='table' AND name='events'
	`).Scan(&count)
	if err != nil {
		return err
	}

	if count == 0 {
		// Create events table with topic support
		_, err = eb.db.Exec(`
			CREATE TABLE IF NOT EXISTS events (
				id TEXT PRIMARY KEY,
				aggregate_id TEXT NOT NULL,
				topic TEXT NOT NULL,
				type TEXT NOT NULL,
				version INTEGER NOT NULL,
				payload JSON NOT NULL,
				timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
				agent_id TEXT,
				sequence INTEGER,
				UNIQUE(aggregate_id, version)
			)
		`)
		if err != nil {
			return err
		}

		// Create indexes
		_, err = eb.db.Exec(`
			CREATE INDEX IF NOT EXISTS idx_events_topic ON events(topic);
			CREATE INDEX IF NOT EXISTS idx_events_sequence ON events(sequence);
			CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
		`)
		if err != nil {
			return err
		}
	} else {
		// Check if topic column exists, add if missing
		var topicCol int
		err = eb.db.QueryRow(`
			SELECT COUNT(*) FROM pragma_table_info('events') WHERE name='topic'
		`).Scan(&topicCol)
		if err != nil {
			return err
		}

		if topicCol == 0 {
			// Add topic column
			_, err = eb.db.Exec(`ALTER TABLE events ADD COLUMN topic TEXT DEFAULT ''`)
			if err != nil {
				return err
			}
			// Add sequence column
			_, err = eb.db.Exec(`ALTER TABLE events ADD COLUMN sequence INTEGER DEFAULT 0`)
			if err != nil {
				return err
			}
			// Create index on topic
			_, err = eb.db.Exec(`CREATE INDEX IF NOT EXISTS idx_events_topic ON events(topic)`)
			if err != nil {
				return err
			}
			// Create index on sequence
			_, err = eb.db.Exec(`CREATE INDEX IF NOT EXISTS idx_events_sequence ON events(sequence)`)
			if err != nil {
				return err
			}
		}
	}

	return nil
}

// loadSequence loads the latest sequence number from the database
func (eb *EventBus) loadSequence() error {
	var seq sql.NullInt64
	err := eb.db.QueryRow(`SELECT MAX(sequence) FROM events`).Scan(&seq)
	if err != nil && err != sql.ErrNoRows {
		return err
	}
	if seq.Valid {
		eb.sequence = seq.Int64
	}
	return nil
}

// nextSequence returns the next sequence number atomically
func (eb *EventBus) nextSequence() int64 {
	eb.seqMu.Lock()
	defer eb.seqMu.Unlock()
	eb.sequence++
	return eb.sequence
}

// Publish persists an event to SQLite and notifies subscribers
func (eb *EventBus) Publish(topic Topic, data interface{}) error {
	return eb.PublishWithAgent(topic, data, "")
}

// PublishWithAgent publishes an event with an associated agent ID
func (eb *EventBus) PublishWithAgent(topic Topic, data interface{}, agentID string) error {
	if topic == "" {
		return fmt.Errorf("topic cannot be empty")
	}

	// Create event
	event := BusEvent{
		ID:        ulid.Make().String(),
		Topic:     topic,
		Data:      data,
		Timestamp: time.Now(),
		Sequence:  eb.nextSequence(),
		AgentID:   agentID,
	}

	// Serialize data
	payload, err := json.Marshal(data)
	if err != nil {
		return fmt.Errorf("failed to marshal event data: %w", err)
	}

	// Persist to database - use event ID as aggregate_id to avoid unique constraint issues
	ctx := context.Background()
	_, err = eb.db.ExecContext(ctx, `
		INSERT INTO events (id, aggregate_id, topic, type, version, payload, timestamp, agent_id, sequence)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, event.ID, event.ID, string(topic), string(topic), 1, payload, event.Timestamp.Format(time.RFC3339), agentID, event.Sequence)
	if err != nil {
		return fmt.Errorf("failed to persist event: %w", err)
	}

	// Notify subscribers asynchronously
	go eb.notifySubscribers(event)

	return nil
}

// notifySubscribers notifies all matching subscribers
func (eb *EventBus) notifySubscribers(event BusEvent) {
	eb.mu.RLock()
	defer eb.mu.RUnlock()

	for topic, handlers := range eb.subscribers {
		if eb.topicMatches(topic, event.Topic) {
			for _, handler := range handlers {
				go func(h Subscriber, e BusEvent) {
					if err := h(e); err != nil {
						// Log error but don't stop other handlers
						fmt.Printf("[EventBus] Subscriber error for topic %s: %v\n", e.Topic, err)
					}
				}(handler, event)
			}
		}
	}
}

// Subscribe registers a handler for a topic (supports wildcards)
func (eb *EventBus) Subscribe(topic Topic, handler Subscriber) error {
	if topic == "" {
		return fmt.Errorf("topic cannot be empty")
	}
	if handler == nil {
		return fmt.Errorf("handler cannot be nil")
	}

	eb.mu.Lock()
	defer eb.mu.Unlock()

	eb.subscribers[topic] = append(eb.subscribers[topic], handler)
	return nil
}

// Unsubscribe removes a handler from a topic (not typically used, but available)
func (eb *EventBus) Unsubscribe(topic Topic, handler Subscriber) error {
	eb.mu.Lock()
	defer eb.mu.Unlock()

	handlers, exists := eb.subscribers[topic]
	if !exists {
		return nil
	}

	// Note: This is a simple implementation. In production, you'd need
	// a way to identify specific handlers (e.g., using a subscription ID)
	// For now, we just clear all handlers for the topic
	delete(eb.subscribers, topic)

	// Re-add all but the last handler as a simple workaround
	if len(handlers) > 1 {
		eb.subscribers[topic] = handlers[:len(handlers)-1]
	}

	return nil
}

// topicMatches checks if an event topic matches a subscription pattern
// Supports wildcards: "*" matches any single segment, "task.*" matches task.created, etc.
func (eb *EventBus) topicMatches(pattern Topic, eventTopic Topic) bool {
	patternStr := string(pattern)
	eventStr := string(eventTopic)

	// Exact match
	if patternStr == eventStr {
		return true
	}

	// Wildcard match
	if strings.HasSuffix(patternStr, ".*") {
		prefix := strings.TrimSuffix(patternStr, ".*")
		return strings.HasPrefix(eventStr, prefix+".")
	}

	// Full wildcard
	if patternStr == "*" {
		return true
	}

	return false
}

// Replay returns all events since a timestamp for catch-up
func (eb *EventBus) Replay(since time.Time, topics []Topic) ([]BusEvent, error) {
	ctx := context.Background()

	// Check if we have wildcard topics that need in-memory filtering
	hasWildcard := false
	for _, t := range topics {
		if strings.Contains(string(t), "*") {
			hasWildcard = true
			break
		}
	}

	// Build query - if we have wildcards, fetch all and filter in-memory
	query := `
		SELECT id, topic, payload, timestamp, agent_id, sequence
		FROM events
		WHERE datetime(timestamp) >= datetime(?)
	`
	args := []interface{}{since.Format(time.RFC3339)}

	// Only add SQL topic filter for non-wildcard topics
	if len(topics) > 0 && !hasWildcard {
		topicPlaceholders := make([]string, len(topics))
		for i, t := range topics {
			topicPlaceholders[i] = "?"
			args = append(args, string(t))
		}
		query += fmt.Sprintf(" AND topic IN (%s)", strings.Join(topicPlaceholders, ","))
	}

	query += " ORDER BY sequence ASC"

	rows, err := eb.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("failed to query events: %w", err)
	}
	defer rows.Close()

	var events []BusEvent
	for rows.Next() {
		var e BusEvent
		var ts string
		var agentID sql.NullString
		var topicStr string
		var rawPayload []byte

		if err := rows.Scan(&e.ID, &topicStr, &rawPayload, &ts, &agentID, &e.Sequence); err != nil {
			return nil, fmt.Errorf("failed to scan event: %w", err)
		}

		e.Topic = Topic(topicStr)
		e.Timestamp, _ = time.Parse(time.RFC3339, ts)
		if agentID.Valid {
			e.AgentID = agentID.String
		}

		// Deserialize payload
		var dataMap map[string]interface{}
		if err := json.Unmarshal(rawPayload, &dataMap); err != nil {
			// Keep as raw bytes if unmarshal fails
			e.Data = rawPayload
		} else {
			e.Data = dataMap
		}

		// Apply in-memory filtering for wildcard topics
		if len(topics) > 0 && hasWildcard {
			matched := false
			for _, pattern := range topics {
				if eb.topicMatches(pattern, e.Topic) {
					matched = true
					break
				}
			}
			if !matched {
				continue
			}
		}

		events = append(events, e)
	}

	return events, rows.Err()
}

// ReplayForTopic replays events for a specific topic pattern
func (eb *EventBus) ReplayForTopic(since time.Time, topicPattern Topic) ([]BusEvent, error) {
	// Get all events since timestamp
	events, err := eb.Replay(since, nil)
	if err != nil {
		return nil, err
	}

	// Filter by topic pattern
	var filtered []BusEvent
	for _, e := range events {
		if eb.topicMatches(topicPattern, e.Topic) {
			filtered = append(filtered, e)
		}
	}

	return filtered, nil
}

// GetLatestEvents returns the most recent events up to a limit
func (eb *EventBus) GetLatestEvents(limit int) ([]BusEvent, error) {
	if limit <= 0 {
		limit = 100
	}

	ctx := context.Background()
	rows, err := eb.db.QueryContext(ctx, `
		SELECT id, topic, payload, timestamp, agent_id, sequence
		FROM events
		ORDER BY sequence DESC
		LIMIT ?
	`, limit)
	if err != nil {
		return nil, fmt.Errorf("failed to query events: %w", err)
	}
	defer rows.Close()

	var events []BusEvent
	for rows.Next() {
		var e BusEvent
		var ts string
		var agentID sql.NullString
		var topicStr string
		var rawPayload []byte

		if err := rows.Scan(&e.ID, &topicStr, &rawPayload, &ts, &agentID, &e.Sequence); err != nil {
			return nil, fmt.Errorf("failed to scan event: %w", err)
		}

		e.Topic = Topic(topicStr)
		e.Timestamp, _ = time.Parse(time.RFC3339, ts)
		if agentID.Valid {
			e.AgentID = agentID.String
		}

		// Deserialize payload
		var dataMap map[string]interface{}
		if err := json.Unmarshal(rawPayload, &dataMap); err != nil {
			e.Data = rawPayload
		} else {
			e.Data = dataMap
		}

		events = append(events, e)
	}

	// Reverse to get chronological order
	for i, j := 0, len(events)-1; i < j; i, j = i+1, j-1 {
		events[i], events[j] = events[j], events[i]
	}

	return events, rows.Err()
}

// Close cleans up resources
func (eb *EventBus) Close() error {
	eb.mu.Lock()
	defer eb.mu.Unlock()

	// Clear all subscribers
	eb.subscribers = make(map[Topic][]Subscriber)

	return nil
}

// Stats returns statistics about the event bus
func (eb *EventBus) Stats() (map[string]interface{}, error) {
	stats := make(map[string]interface{})

	// Get total event count
	var totalEvents int
	err := eb.db.QueryRow(`SELECT COUNT(*) FROM events`).Scan(&totalEvents)
	if err != nil {
		return nil, err
	}
	stats["total_events"] = totalEvents

	// Get subscriber count
	eb.mu.RLock()
	subscriberCount := 0
	for _, handlers := range eb.subscribers {
		subscriberCount += len(handlers)
	}
	eb.mu.RUnlock()
	stats["subscriber_count"] = subscriberCount

	// Get topic counts
	stats["topics"] = len(eb.subscribers)

	// Get current sequence
	eb.seqMu.Lock()
	stats["current_sequence"] = eb.sequence
	eb.seqMu.Unlock()

	return stats, nil
}
