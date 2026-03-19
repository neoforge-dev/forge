//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 209: event_bus.go
// Targets:
//   NewEventBus         (event_bus.go:70)  — nil DB / with DB
//   EventBus.Subscribe  (event_bus.go:256) — empty topic / nil handler / success
//   EventBus.Unsubscribe(event_bus.go:272) — non-existent / after subscribe
//   EventBus.topicMatches(event_bus.go:296)— exact/wildcard/full-wildcard/no-match
//   EventBus.GetLatestEvents(event_bus.go:425)— empty
//   EventBus.Stats      (event_bus.go:491) — non-nil map
//   EventBus.Close      (event_bus.go:480)
// ---------------------------------------------------------------------------

func setupEventBus(t *testing.T) (*EventBus, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	eb, err := NewEventBus(db)
	if err != nil {
		cleanup()
		t.Fatalf("NewEventBus: %v", err)
	}
	return eb, cleanup
}

// ---------------------------------------------------------------------------
// NewEventBus
// ---------------------------------------------------------------------------

func TestWave209_NewEventBus_NilDB(t *testing.T) {
	_, err := NewEventBus(nil)
	if err == nil {
		t.Error("expected error for nil db")
	}
}

func TestWave209_NewEventBus_WithDB(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()
	if eb == nil {
		t.Error("expected non-nil EventBus")
	}
}

// ---------------------------------------------------------------------------
// Subscribe
// ---------------------------------------------------------------------------

func TestWave209_Subscribe_EmptyTopic(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	err := eb.Subscribe("", func(event BusEvent) error { return nil })
	if err == nil {
		t.Error("expected error for empty topic")
	}
}

func TestWave209_Subscribe_NilHandler(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	err := eb.Subscribe("task.created", nil)
	if err == nil {
		t.Error("expected error for nil handler")
	}
}

func TestWave209_Subscribe_Success(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	err := eb.Subscribe("task.created", func(event BusEvent) error { return nil })
	if err != nil {
		t.Errorf("Subscribe: %v", err)
	}
}

// ---------------------------------------------------------------------------
// Unsubscribe
// ---------------------------------------------------------------------------

func TestWave209_Unsubscribe_NonExistentTopic(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	// No-op for non-existent topic
	err := eb.Unsubscribe("nonexistent.topic", func(event BusEvent) error { return nil })
	if err != nil {
		t.Errorf("Unsubscribe non-existent: %v", err)
	}
}

func TestWave209_Unsubscribe_AfterSubscribe(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	handler := func(event BusEvent) error { return nil }
	eb.Subscribe("task.wave209", handler)
	err := eb.Unsubscribe("task.wave209", handler)
	if err != nil {
		t.Errorf("Unsubscribe after subscribe: %v", err)
	}
}

// ---------------------------------------------------------------------------
// topicMatches
// ---------------------------------------------------------------------------

func TestWave209_TopicMatches_Exact(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	if !eb.topicMatches("task.created", "task.created") {
		t.Error("expected exact match")
	}
}

func TestWave209_TopicMatches_Wildcard(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	if !eb.topicMatches("task.*", "task.created") {
		t.Error("expected wildcard match for task.*")
	}
}

func TestWave209_TopicMatches_FullWildcard(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	if !eb.topicMatches("*", "task.created") {
		t.Error("expected full wildcard to match")
	}
}

func TestWave209_TopicMatches_NoMatch(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	if eb.topicMatches("agent.created", "task.created") {
		t.Error("expected no match for different topics")
	}
}

func TestWave209_TopicMatches_WildcardNoMatch(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	if eb.topicMatches("task.*", "agent.started") {
		t.Error("expected no match for task.* vs agent.started")
	}
}

// ---------------------------------------------------------------------------
// GetLatestEvents
// ---------------------------------------------------------------------------

func TestWave209_GetLatestEvents_Empty(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	events, err := eb.GetLatestEvents(10)
	if err != nil {
		t.Errorf("GetLatestEvents: %v", err)
	}
	_ = events
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

func TestWave209_Stats_NonNil(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	stats, err := eb.Stats()
	if err != nil {
		t.Errorf("Stats: %v", err)
	}
	if stats == nil {
		t.Error("expected non-nil stats")
	}
}

// ---------------------------------------------------------------------------
// Replay
// ---------------------------------------------------------------------------

func TestWave209_Replay_Empty(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	events, err := eb.Replay(time.Now().Add(-1*time.Hour), []Topic{"task.*"})
	if err != nil {
		t.Errorf("Replay: %v", err)
	}
	_ = events
}

// ---------------------------------------------------------------------------
// Close
// ---------------------------------------------------------------------------

func TestWave209_Close_NoPanic(t *testing.T) {
	eb, cleanup := setupEventBus(t)
	defer cleanup()

	if err := eb.Close(); err != nil {
		t.Errorf("Close: %v", err)
	}
}
