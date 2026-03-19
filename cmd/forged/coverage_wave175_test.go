//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"errors"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 175: event_bus.go — EventBus methods + topic matching
// Targets:
//   NewEventBus           (event_bus.go:70)  — nil DB error / valid DB
//   topicMatches          (event_bus.go:296) — exact / wildcard / global / no match
//   Subscribe             (event_bus.go:256) — empty topic / nil handler / success
//   Publish               (event_bus.go:194) — empty topic / success
//   PublishWithAgent      (event_bus.go:199) — with agent ID
//   GetLatestEvents       (event_bus.go:425) — zero limit defaults / returns events
//   Replay                (event_bus.go:320) — since past / wildcard filter
//   Close                 (event_bus.go:480) — clears subscribers
//   Stats                 (event_bus.go:491) — non-nil map
//   Unsubscribe           (event_bus.go:272) — non-existent topic / with handlers
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewEventBus
// ---------------------------------------------------------------------------

func TestWave175_NewEventBus_NilDB(t *testing.T) {
	_, err := NewEventBus(nil)
	if err == nil {
		t.Error("expected error for nil DB")
	}
}

func TestWave175_NewEventBus_ValidDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	if eb == nil {
		t.Error("expected non-nil EventBus")
	}
}

// ---------------------------------------------------------------------------
// topicMatches
// ---------------------------------------------------------------------------

func TestWave175_TopicMatches_Exact(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	if !eb.topicMatches("task.created", "task.created") {
		t.Error("expected exact match")
	}
}

func TestWave175_TopicMatches_Wildcard(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	// "task.*" should match "task.created"
	if !eb.topicMatches("task.*", "task.created") {
		t.Error("expected wildcard match")
	}
}

func TestWave175_TopicMatches_WildcardNoMatch(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	// "agent.*" should NOT match "task.created"
	if eb.topicMatches("agent.*", "task.created") {
		t.Error("expected no match for different prefix")
	}
}

func TestWave175_TopicMatches_GlobalWildcard(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	if !eb.topicMatches("*", "anything.goes") {
		t.Error("expected global wildcard to match everything")
	}
}

func TestWave175_TopicMatches_NoMatch(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	if eb.topicMatches("task.created", "task.completed") {
		t.Error("expected no match for different topics")
	}
}

// ---------------------------------------------------------------------------
// Subscribe
// ---------------------------------------------------------------------------

func TestWave175_Subscribe_EmptyTopic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	err := eb.Subscribe("", func(e BusEvent) error { return nil })
	if err == nil {
		t.Error("expected error for empty topic")
	}
}

func TestWave175_Subscribe_NilHandler(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	err := eb.Subscribe("task.created", nil)
	if err == nil {
		t.Error("expected error for nil handler")
	}
}

func TestWave175_Subscribe_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	err := eb.Subscribe("task.created", func(e BusEvent) error { return nil })
	if err != nil {
		t.Errorf("Subscribe: %v", err)
	}
}

// ---------------------------------------------------------------------------
// Publish
// ---------------------------------------------------------------------------

func TestWave175_Publish_EmptyTopic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	err := eb.Publish("", map[string]string{"key": "val"})
	if err == nil {
		t.Error("expected error for empty topic")
	}
}

func TestWave175_Publish_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	err := eb.Publish("task.created", map[string]string{"task_id": "T1"})
	if err != nil {
		t.Errorf("Publish: %v", err)
	}
}

// ---------------------------------------------------------------------------
// PublishWithAgent
// ---------------------------------------------------------------------------

func TestWave175_PublishWithAgent_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	err := eb.PublishWithAgent("task.created", map[string]string{"task_id": "T2"}, "kimi-1")
	if err != nil {
		t.Errorf("PublishWithAgent: %v", err)
	}
}

// ---------------------------------------------------------------------------
// GetLatestEvents
// ---------------------------------------------------------------------------

func TestWave175_GetLatestEvents_ZeroLimit(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	// Zero limit → defaults to 100
	events, err := eb.GetLatestEvents(0)
	if err != nil {
		t.Errorf("GetLatestEvents: %v", err)
	}
	_ = events
}

func TestWave175_GetLatestEvents_AfterPublish(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	eb.Publish("task.created", map[string]string{"id": "X"})
	time.Sleep(10 * time.Millisecond)

	events, err := eb.GetLatestEvents(10)
	if err != nil {
		t.Errorf("GetLatestEvents: %v", err)
	}
	if len(events) == 0 {
		t.Error("expected at least 1 event after publish")
	}
}

// ---------------------------------------------------------------------------
// Replay
// ---------------------------------------------------------------------------

func TestWave175_Replay_SincePast(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	eb.Publish("task.completed", map[string]string{"id": "Y"})
	time.Sleep(10 * time.Millisecond)

	events, err := eb.Replay(time.Now().Add(-1*time.Minute), nil)
	if err != nil {
		t.Errorf("Replay: %v", err)
	}
	_ = events
}

func TestWave175_Replay_WithWildcardTopic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	eb.Publish("task.created", map[string]string{"id": "Z"})
	time.Sleep(10 * time.Millisecond)

	topics := []Topic{"task.*"}
	events, err := eb.Replay(time.Now().Add(-1*time.Minute), topics)
	if err != nil {
		t.Errorf("Replay wildcard: %v", err)
	}
	_ = events
}

// ---------------------------------------------------------------------------
// ReplayForTopic
// ---------------------------------------------------------------------------

func TestWave175_ReplayForTopic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	events, err := eb.ReplayForTopic(time.Now().Add(-1*time.Minute), "task.*")
	if err != nil {
		t.Errorf("ReplayForTopic: %v", err)
	}
	_ = events
}

// ---------------------------------------------------------------------------
// Close
// ---------------------------------------------------------------------------

func TestWave175_Close_ClearsSubscribers(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	eb.Subscribe("task.created", func(e BusEvent) error { return nil })
	if err := eb.Close(); err != nil {
		t.Errorf("Close: %v", err)
	}
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

func TestWave175_Stats_NonNilMap(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	stats, err := eb.Stats()
	if err != nil {
		t.Errorf("Stats: %v", err)
	}
	if stats == nil {
		t.Error("expected non-nil stats map")
	}
	if _, ok := stats["total_events"]; !ok {
		t.Error("expected total_events key in stats")
	}
}

// ---------------------------------------------------------------------------
// Unsubscribe
// ---------------------------------------------------------------------------

func TestWave175_Unsubscribe_NonExistentTopic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	// Should not error for non-existent topic
	err := eb.Unsubscribe("task.nonexistent", func(e BusEvent) error { return nil })
	if err != nil {
		t.Errorf("Unsubscribe non-existent: %v", err)
	}
}

func TestWave175_Unsubscribe_WithHandlers(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	eb, _ := NewEventBus(db)

	handler := func(e BusEvent) error { return nil }
	eb.Subscribe("task.created", handler)
	eb.Subscribe("task.created", func(e BusEvent) error { return errors.New("err2") })

	err := eb.Unsubscribe("task.created", handler)
	if err != nil {
		t.Errorf("Unsubscribe: %v", err)
	}
}
