//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
	"time"
)

// TestWave68_EventBus_Stats_EmptyDB verifies Stats returns sensible zero values on empty DB.
func TestWave68_EventBus_Stats_EmptyDB(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	stats, err := eb.Stats()
	if err != nil {
		t.Fatalf("Stats() on empty DB: %v", err)
	}
	if stats["total_events"] != 0 {
		t.Errorf("expected total_events=0, got %v", stats["total_events"])
	}
	if stats["subscriber_count"] != 0 {
		t.Errorf("expected subscriber_count=0, got %v", stats["subscriber_count"])
	}
	if stats["topics"] != 0 {
		t.Errorf("expected topics=0, got %v", stats["topics"])
	}
	if stats["current_sequence"].(int64) != 0 {
		t.Errorf("expected current_sequence=0, got %v", stats["current_sequence"])
	}
}

// TestWave68_EventBus_Replay_EmptyTopics verifies Replay with empty topics slice returns all events.
func TestWave68_EventBus_Replay_EmptyTopics(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	since := time.Now().Add(-time.Second)

	eb.Publish(TopicTaskCreated, map[string]string{"id": "1"})
	eb.Publish(TopicAgentTelemetry, map[string]string{"id": "2"})

	events, err := eb.Replay(since, []Topic{}) // empty slice — should return all
	if err != nil {
		t.Fatalf("Replay with empty topics: %v", err)
	}
	if len(events) != 2 {
		t.Errorf("expected 2 events, got %d", len(events))
	}
}

// TestWave68_EventBus_Replay_NilTopics verifies Replay with nil topics returns all events.
func TestWave68_EventBus_Replay_NilTopics(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	since := time.Now().Add(-time.Second)
	eb.Publish(TopicTaskCreated, map[string]string{"id": "t1"})
	eb.Publish(TopicTaskFailed, map[string]string{"id": "t2"})
	eb.Publish(TopicAgentConnected, map[string]string{"id": "a1"})

	events, err := eb.Replay(since, nil)
	if err != nil {
		t.Fatalf("Replay with nil topics: %v", err)
	}
	if len(events) != 3 {
		t.Errorf("expected 3 events, got %d", len(events))
	}
}

// TestWave68_EventBus_Replay_WildcardFilter verifies Replay with wildcard topic pattern.
func TestWave68_EventBus_Replay_WildcardFilter(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	since := time.Now().Add(-time.Second)
	eb.Publish(TopicTaskCreated, map[string]string{"id": "t1"})
	eb.Publish(TopicTaskCompleted, map[string]string{"id": "t2"})
	eb.Publish(TopicAgentConnected, map[string]string{"id": "a1"})

	// Wildcard filter for task.* topics.
	events, err := eb.Replay(since, []Topic{TopicTask}) // TopicTask = "task.*"
	if err != nil {
		t.Fatalf("Replay with wildcard: %v", err)
	}
	if len(events) != 2 {
		t.Errorf("expected 2 task events, got %d", len(events))
	}
	for _, e := range events {
		if e.Topic != TopicTaskCreated && e.Topic != TopicTaskCompleted {
			t.Errorf("expected task topic, got %s", e.Topic)
		}
	}
}

// TestWave68_EventBus_Replay_MultipleWildcardTopics verifies Replay with multiple wildcard patterns.
func TestWave68_EventBus_Replay_MultipleWildcardTopics(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	since := time.Now().Add(-time.Second)
	eb.Publish(TopicTaskCreated, map[string]string{"id": "t1"})
	eb.Publish(TopicAgentConnected, map[string]string{"id": "a1"})
	eb.Publish(TopicApprovalRequested, map[string]string{"id": "ap1"})

	// Filter for task.* AND agent.* — both wildcards.
	events, err := eb.Replay(since, []Topic{TopicTask, TopicAgent})
	if err != nil {
		t.Fatalf("Replay with multiple wildcards: %v", err)
	}
	if len(events) != 2 {
		t.Errorf("expected 2 events (task + agent), got %d", len(events))
	}
}

// TestWave68_EventBus_Replay_NonWildcardTopics verifies Replay with exact (non-wildcard) topics.
func TestWave68_EventBus_Replay_NonWildcardTopics(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	since := time.Now().Add(-time.Second)
	eb.Publish(TopicTaskCreated, map[string]string{"id": "t1"})
	eb.Publish(TopicTaskCompleted, map[string]string{"id": "t2"})
	eb.Publish(TopicAgentConnected, map[string]string{"id": "a1"})

	// Exact topic filter — no wildcards — SQL IN clause path.
	events, err := eb.Replay(since, []Topic{TopicTaskCreated})
	if err != nil {
		t.Fatalf("Replay with exact topic: %v", err)
	}
	if len(events) != 1 {
		t.Errorf("expected 1 event for task.created, got %d", len(events))
	}
	if events[0].Topic != TopicTaskCreated {
		t.Errorf("expected topic task.created, got %s", events[0].Topic)
	}
}

// TestWave68_EventBus_Replay_FutureSince verifies Replay with future timestamp returns no events.
func TestWave68_EventBus_Replay_FutureSince(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	eb.Publish(TopicTaskCreated, map[string]string{"id": "t1"})

	// Future timestamp — no events should match.
	future := time.Now().Add(time.Hour)
	events, err := eb.Replay(future, nil)
	if err != nil {
		t.Fatalf("Replay with future since: %v", err)
	}
	if len(events) != 0 {
		t.Errorf("expected 0 events with future timestamp, got %d", len(events))
	}
}

// TestWave68_EventBus_GetLatestEvents_ZeroLimit verifies GetLatestEvents uses 100 default.
func TestWave68_EventBus_GetLatestEvents_ZeroLimit(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	// Publish a few events.
	for i := 0; i < 5; i++ {
		eb.Publish(TopicTaskCreated, map[string]int{"i": i})
	}

	// Limit=0 should default to 100.
	events, err := eb.GetLatestEvents(0)
	if err != nil {
		t.Fatalf("GetLatestEvents(0): %v", err)
	}
	if len(events) != 5 {
		t.Errorf("expected 5 events, got %d", len(events))
	}
}

// TestWave68_EventBus_Unsubscribe_Multiple verifies Unsubscribe trims subscriber list.
func TestWave68_EventBus_Unsubscribe_Multiple(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	h1 := func(e BusEvent) error { return nil }
	h2 := func(e BusEvent) error { return nil }

	eb.Subscribe(TopicTaskCreated, h1)
	eb.Subscribe(TopicTaskCreated, h2)

	// Unsubscribe should remove one handler.
	if err := eb.Unsubscribe(TopicTaskCreated, h1); err != nil {
		t.Fatalf("Unsubscribe: %v", err)
	}

	// Check subscriber count decreased.
	stats, _ := eb.Stats()
	if stats["subscriber_count"].(int) != 1 {
		t.Errorf("expected 1 subscriber after unsubscribe, got %v", stats["subscriber_count"])
	}
}

// TestWave68_EventBus_Unsubscribe_NonExistent verifies Unsubscribe on missing topic is a no-op.
func TestWave68_EventBus_Unsubscribe_NonExistent(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("NewEventBus: %v", err)
	}
	defer eb.Close()

	h := func(e BusEvent) error { return nil }
	// Topic was never subscribed — should return nil.
	if err := eb.Unsubscribe(TopicTaskCreated, h); err != nil {
		t.Errorf("Unsubscribe non-existent topic should return nil, got: %v", err)
	}
}

// TestWave68_EventBus_NewEventBus_NilDB verifies that NewEventBus returns error for nil db.
func TestWave68_EventBus_NewEventBus_NilDB(t *testing.T) {
	_, err := NewEventBus(nil)
	if err == nil {
		t.Error("expected error for nil db, got nil")
	}
}

// TestWave68_EventBus_Replay_ExistingTopicColumn verifies Replay works on DB that already
// has the topic column (covers the else-branch of initSchema).
func TestWave68_EventBus_Replay_ExistingTopicColumn(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	// Create a fresh event bus — this triggers initSchema which creates the table.
	eb1, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("first NewEventBus: %v", err)
	}
	eb1.Publish(TopicTaskCreated, map[string]string{"x": "1"})
	eb1.Close()

	// Second NewEventBus on same DB — table already exists (else branch in initSchema).
	eb2, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("second NewEventBus: %v", err)
	}
	defer eb2.Close()

	since := time.Now().Add(-time.Hour)
	events, err := eb2.Replay(since, nil)
	if err != nil {
		t.Fatalf("Replay after re-open: %v", err)
	}
	if len(events) != 1 {
		t.Errorf("expected 1 event after re-open, got %d", len(events))
	}
}
