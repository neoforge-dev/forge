//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 156: event_bus.go — EventBus publish/subscribe/query
// Targets:
//   EventBus.topicMatches       (event_bus.go:296) — exact, wildcard prefix, full wildcard, no match
//   EventBus.Publish            (event_bus.go:194) — valid publish, empty topic→error
//   EventBus.PublishWithAgent   (event_bus.go:199) — with agent ID
//   EventBus.Subscribe          (event_bus.go:256) — valid, empty topic→error, nil handler→error
//   EventBus.Unsubscribe        (event_bus.go:272) — nonexistent (no-op)
//   EventBus.GetLatestEvents    (event_bus.go:425) — empty DB, with events
//   EventBus.Stats              (event_bus.go:491) — returns stats map
//   EventBus.Close              (event_bus.go:480) — closes DB cleanly
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// topicMatches
// ---------------------------------------------------------------------------

func TestWave156_TopicMatches_Exact(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	if !eb.topicMatches("task.created", "task.created") {
		t.Error("expected exact match")
	}
}

func TestWave156_TopicMatches_WildcardPrefix(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	if !eb.topicMatches("task.*", "task.created") {
		t.Error("expected wildcard prefix match")
	}
	if eb.topicMatches("task.*", "agent.created") {
		t.Error("wildcard prefix should not match different root")
	}
}

func TestWave156_TopicMatches_FullWildcard(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	if !eb.topicMatches("*", "task.created") {
		t.Error("expected full wildcard to match any topic")
	}
}

func TestWave156_TopicMatches_NoMatch(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	if eb.topicMatches("agent.updated", "task.created") {
		t.Error("expected no match for different topics")
	}
}

// ---------------------------------------------------------------------------
// Publish / PublishWithAgent
// ---------------------------------------------------------------------------

func TestWave156_Publish_Valid(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	err := eb.Publish("task.created", map[string]string{"task_id": "t1"})
	if err != nil {
		t.Errorf("expected nil error, got: %v", err)
	}
}

func TestWave156_Publish_EmptyTopic(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	err := eb.Publish("", "data")
	if err == nil {
		t.Error("expected error for empty topic")
	}
}

func TestWave156_PublishWithAgent_Valid(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	err := eb.PublishWithAgent("agent.heartbeat", map[string]string{"node": "prya"}, "kimi")
	if err != nil {
		t.Errorf("expected nil error, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// Subscribe / Unsubscribe
// ---------------------------------------------------------------------------

func TestWave156_Subscribe_Valid(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	err := eb.Subscribe("task.created", func(e BusEvent) error { return nil })
	if err != nil {
		t.Errorf("expected nil error, got: %v", err)
	}
}

func TestWave156_Subscribe_EmptyTopic(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	err := eb.Subscribe("", func(e BusEvent) error { return nil })
	if err == nil {
		t.Error("expected error for empty topic")
	}
}

func TestWave156_Subscribe_NilHandler(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	err := eb.Subscribe("task.created", nil)
	if err == nil {
		t.Error("expected error for nil handler")
	}
}

func TestWave156_Unsubscribe_Nonexistent(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	// Unsubscribing a nonexistent handler should not panic
	err := eb.Unsubscribe("task.created", func(e BusEvent) error { return nil })
	if err != nil {
		t.Errorf("expected nil error for nonexistent unsubscribe, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// GetLatestEvents
// ---------------------------------------------------------------------------

func TestWave156_GetLatestEvents_Empty(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	events, err := eb.GetLatestEvents(10)
	if err != nil {
		t.Fatalf("GetLatestEvents: %v", err)
	}
	// nil is acceptable when no events exist — just ensure no error
	_ = events
}

func TestWave156_GetLatestEvents_AfterPublish(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	if err := eb.Publish("task.created", map[string]string{"id": "t1"}); err != nil {
		t.Fatalf("Publish: %v", err)
	}
	// Give async notifySubscribers time to complete
	time.Sleep(10 * time.Millisecond)

	events, err := eb.GetLatestEvents(10)
	if err != nil {
		t.Fatalf("GetLatestEvents: %v", err)
	}
	if len(events) == 0 {
		t.Error("expected at least 1 event after publish")
	}
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

func TestWave156_Stats_ReturnsMap(t *testing.T) {
	eb, ebCleanup := newTestEventBus(t)
	defer ebCleanup()
	stats, err := eb.Stats()
	if err != nil {
		t.Fatalf("Stats: %v", err)
	}
	if stats == nil {
		t.Error("expected non-nil stats map")
	}
}

// ---------------------------------------------------------------------------
// Close
// ---------------------------------------------------------------------------

func TestWave156_Close_NoError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	if err := eb.Close(); err != nil {
		t.Errorf("Close: %v", err)
	}
}
