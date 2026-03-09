//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"os/exec"
	"sync"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

func setupEventBusTestDB(t *testing.T) *sql.DB {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("Failed to open test database: %v", err)
	}
	return db
}

func TestNewEventBus(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("Failed to create EventBus: %v", err)
	}
	defer eb.Close()

	if eb == nil {
		t.Fatal("EventBus should not be nil")
	}

	if eb.db != db {
		t.Error("EventBus database should match")
	}
}

func TestEventBus_Publish(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("Failed to create EventBus: %v", err)
	}
	defer eb.Close()

	tests := []struct {
		name    string
		topic   Topic
		data    interface{}
		wantErr bool
	}{
		{
			name:    "publish task created event",
			topic:   TopicTaskCreated,
			data:    map[string]string{"task_id": "test-123", "title": "Test Task"},
			wantErr: false,
		},
		{
			name:    "publish with empty topic should fail",
			topic:   "",
			data:    map[string]string{"test": "data"},
			wantErr: true,
		},
		{
			name:    "publish agent telemetry",
			topic:   TopicAgentTelemetry,
			data:    map[string]interface{}{"agent_id": "agent-1", "cpu": 45.5, "memory": 1024},
			wantErr: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := eb.Publish(tt.topic, tt.data)
			if (err != nil) != tt.wantErr {
				t.Errorf("Publish() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

func TestEventBus_Subscribe(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("Failed to create EventBus: %v", err)
	}
	defer eb.Close()

	handler := func(event BusEvent) error {
		return nil
	}

	tests := []struct {
		name    string
		topic   Topic
		handler Subscriber
		wantErr bool
	}{
		{
			name:    "subscribe to specific topic",
			topic:   TopicTaskCreated,
			handler: handler,
			wantErr: false,
		},
		{
			name:    "subscribe to wildcard topic",
			topic:   TopicTask,
			handler: handler,
			wantErr: false,
		},
		{
			name:    "subscribe with empty topic should fail",
			topic:   "",
			handler: handler,
			wantErr: true,
		},
		{
			name:    "subscribe with nil handler should fail",
			topic:   TopicTaskCreated,
			handler: nil,
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := eb.Subscribe(tt.topic, tt.handler)
			if (err != nil) != tt.wantErr {
				t.Errorf("Subscribe() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

func TestEventBus_SubscribeAndPublish(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("Failed to create EventBus: %v", err)
	}
	defer eb.Close()

	var receivedEvents []BusEvent
	var mu sync.Mutex
	done := make(chan struct{}, 1)

	handler := func(event BusEvent) error {
		mu.Lock()
		receivedEvents = append(receivedEvents, event)
		n := len(receivedEvents)
		mu.Unlock()
		if n >= 2 {
			select {
			case done <- struct{}{}:
			default:
			}
		}
		return nil
	}

	// Subscribe to task events
	if err := eb.Subscribe(TopicTaskCreated, handler); err != nil {
		t.Fatalf("Failed to subscribe: %v", err)
	}

	// Publish events
	testData := map[string]string{"task_id": "task-1", "title": "Test Task"}
	if err := eb.Publish(TopicTaskCreated, testData); err != nil {
		t.Fatalf("Failed to publish: %v", err)
	}

	if err := eb.Publish(TopicTaskCreated, testData); err != nil {
		t.Fatalf("Failed to publish: %v", err)
	}

	// Wait for events to be processed
	select {
	case <-done:
		// Success
	case <-time.After(2 * time.Second):
		t.Fatal("Timeout waiting for events")
	}

	// Small delay to ensure all async processing is done
	time.Sleep(100 * time.Millisecond)

	mu.Lock()
	if len(receivedEvents) != 2 {
		t.Errorf("Expected 2 events, got %d", len(receivedEvents))
	}
	mu.Unlock()
}

func TestEventBus_WildcardMatching(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("Failed to create EventBus: %v", err)
	}
	defer eb.Close()

	tests := []struct {
		pattern string
		topic   string
		match   bool
	}{
		{"task.*", "task.created", true},
		{"task.*", "task.completed", true},
		{"task.*", "task.failed", true},
		{"task.*", "agent.telemetry", false},
		{"agent.*", "agent.telemetry", true},
		{"agent.*", "agent.connected", true},
		{"approval.*", "approval.requested", true},
		{"approval.*", "approval.granted", true},
		{"approval.*", "task.created", false},
		{"*", "anything", true},
		{"task.created", "task.created", true},
		{"task.created", "task.completed", false},
	}

	for _, tt := range tests {
		t.Run(tt.pattern+"_"+tt.topic, func(t *testing.T) {
			result := eb.topicMatches(Topic(tt.pattern), Topic(tt.topic))
			if result != tt.match {
				t.Errorf("topicMatches(%s, %s) = %v, want %v", tt.pattern, tt.topic, result, tt.match)
			}
		})
	}
}

func TestEventBus_WildcardSubscription(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("Failed to create EventBus: %v", err)
	}
	defer eb.Close()

	var receivedEvents []BusEvent
	var mu sync.Mutex
	done := make(chan struct{}, 1)

	// Subscribe to wildcard topic
	handler := func(event BusEvent) error {
		mu.Lock()
		receivedEvents = append(receivedEvents, event)
		n := len(receivedEvents)
		mu.Unlock()
		if n >= 3 {
			select {
			case done <- struct{}{}:
			default:
			}
		}
		return nil
	}

	if err := eb.Subscribe(TopicTask, handler); err != nil {
		t.Fatalf("Failed to subscribe: %v", err)
	}

	// Publish different task events
	eb.Publish(TopicTaskCreated, map[string]string{"task_id": "1"})
	eb.Publish(TopicTaskCompleted, map[string]string{"task_id": "2"})
	eb.Publish(TopicTaskFailed, map[string]string{"task_id": "3"})

	// Wait for events
	select {
	case <-done:
		// Success
	case <-time.After(2 * time.Second):
		t.Fatal("Timeout waiting for events")
	}

	time.Sleep(100 * time.Millisecond)

	mu.Lock()
	if len(receivedEvents) != 3 {
		t.Errorf("Expected 3 events, got %d", len(receivedEvents))
	}
	mu.Unlock()
}

func TestEventBus_Replay(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("Failed to create EventBus: %v", err)
	}
	defer eb.Close()

	// Publish some events
	startTime := time.Now()
	time.Sleep(10 * time.Millisecond)

	eb.Publish(TopicTaskCreated, map[string]string{"task_id": "1"})
	eb.Publish(TopicTaskCompleted, map[string]string{"task_id": "2"})
	eb.Publish(TopicAgentTelemetry, map[string]interface{}{"cpu": 50})

	time.Sleep(10 * time.Millisecond)

	// Replay all events since start
	events, err := eb.Replay(startTime.Add(-time.Second), nil)
	if err != nil {
		t.Fatalf("Replay failed: %v", err)
	}

	if len(events) != 3 {
		t.Errorf("Expected 3 events, got %d", len(events))
	}

	// Replay with topic filter
	events, err = eb.Replay(startTime.Add(-time.Second), []Topic{TopicTask})
	if err != nil {
		t.Fatalf("Replay with filter failed: %v", err)
	}

	// Should get task.created and task.completed
	if len(events) != 2 {
		t.Errorf("Expected 2 task events, got %d", len(events))
	}
}

func TestEventBus_ReplayForTopic(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("Failed to create EventBus: %v", err)
	}
	defer eb.Close()

	startTime := time.Now()
	time.Sleep(10 * time.Millisecond)

	// Publish mixed events
	eb.Publish(TopicTaskCreated, map[string]string{"task_id": "1"})
	eb.Publish(TopicTaskCompleted, map[string]string{"task_id": "2"})
	eb.Publish(TopicAgentTelemetry, map[string]interface{}{"cpu": 50})
	eb.Publish(TopicAgentConnected, map[string]string{"agent_id": "agent-1"})

	time.Sleep(10 * time.Millisecond)

	// Replay only task events using wildcard
	events, err := eb.ReplayForTopic(startTime.Add(-time.Second), TopicTask)
	if err != nil {
		t.Fatalf("ReplayForTopic failed: %v", err)
	}

	if len(events) != 2 {
		t.Errorf("Expected 2 task events, got %d", len(events))
	}

	// Verify topics
	for _, e := range events {
		if e.Topic != TopicTaskCreated && e.Topic != TopicTaskCompleted {
			t.Errorf("Expected task topic, got %s", e.Topic)
		}
	}
}

func TestEventBus_GetLatestEvents(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("Failed to create EventBus: %v", err)
	}
	defer eb.Close()

	// Publish events
	for i := 0; i < 10; i++ {
		eb.Publish(TopicTaskCreated, map[string]int{"index": i})
	}

	// Get latest 5
	events, err := eb.GetLatestEvents(5)
	if err != nil {
		t.Fatalf("GetLatestEvents failed: %v", err)
	}

	if len(events) != 5 {
		t.Errorf("Expected 5 events, got %d", len(events))
	}

	// Verify chronological order
	for i := 1; i < len(events); i++ {
		if events[i].Sequence <= events[i-1].Sequence {
			t.Error("Events should be in chronological order")
		}
	}
}

func TestEventBus_Stats(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("Failed to create EventBus: %v", err)
	}
	defer eb.Close()

	// Publish events
	eb.Publish(TopicTaskCreated, map[string]string{"task_id": "1"})
	eb.Publish(TopicTaskCompleted, map[string]string{"task_id": "2"})

	// Add subscriber
	eb.Subscribe(TopicTask, func(e BusEvent) error { return nil })
	eb.Subscribe(TopicAgent, func(e BusEvent) error { return nil })

	stats, err := eb.Stats()
	if err != nil {
		t.Fatalf("Stats failed: %v", err)
	}

	if stats["total_events"] != 2 {
		t.Errorf("Expected 2 total events, got %v", stats["total_events"])
	}

	if stats["subscriber_count"] != 2 {
		t.Errorf("Expected 2 subscribers, got %v", stats["subscriber_count"])
	}

	if stats["topics"] != 2 {
		t.Errorf("Expected 2 topics, got %v", stats["topics"])
	}

	if stats["current_sequence"].(int64) < 2 {
		t.Errorf("Expected sequence >= 2, got %v", stats["current_sequence"])
	}
}

func TestEventBus_ConcurrentPublish(t *testing.T) {
	// Use file-based database for concurrent test to avoid in-memory DB issues
	db, err := sql.Open("sqlite3", "/tmp/event_bus_concurrent_test.db")
	if err != nil {
		t.Fatalf("Failed to open test database: %v", err)
	}
	defer db.Close()
	defer func() {
		// Clean up test database
		exec.Command("rm", "-f", "/tmp/event_bus_concurrent_test.db").Run()
	}()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("Failed to create EventBus: %v", err)
	}
	defer eb.Close()

	var wg sync.WaitGroup
	numGoroutines := 10
	numEventsPerGoroutine := 10

	for i := 0; i < numGoroutines; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			for j := 0; j < numEventsPerGoroutine; j++ {
				eb.Publish(TopicTaskCreated, map[string]int{"goroutine": id, "event": j})
			}
		}(i)
	}

	wg.Wait()
	time.Sleep(200 * time.Millisecond)

	stats, err := eb.Stats()
	if err != nil {
		t.Fatalf("Stats failed: %v", err)
	}

	expectedEvents := numGoroutines * numEventsPerGoroutine
	if stats["total_events"] != expectedEvents {
		t.Errorf("Expected %d events, got %v", expectedEvents, stats["total_events"])
	}

	if stats["current_sequence"] != int64(expectedEvents) {
		t.Errorf("Expected sequence %d, got %v", expectedEvents, stats["current_sequence"])
	}
}

func TestEventBus_MultipleSubscribers(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("Failed to create EventBus: %v", err)
	}
	defer eb.Close()

	var counter1, counter2 int
	var mu sync.Mutex

	// Add two subscribers to the same topic
	eb.Subscribe(TopicTaskCreated, func(e BusEvent) error {
		mu.Lock()
		counter1++
		mu.Unlock()
		return nil
	})

	eb.Subscribe(TopicTaskCreated, func(e BusEvent) error {
		mu.Lock()
		counter2++
		mu.Unlock()
		return nil
	})

	// Publish an event
	eb.Publish(TopicTaskCreated, map[string]string{"task_id": "1"})

	time.Sleep(200 * time.Millisecond)

	mu.Lock()
	if counter1 != 1 {
		t.Errorf("Subscriber 1 expected 1 event, got %d", counter1)
	}
	if counter2 != 1 {
		t.Errorf("Subscriber 2 expected 1 event, got %d", counter2)
	}
	mu.Unlock()
}

func TestEventBus_PublishWithAgent(t *testing.T) {
	db := setupEventBusTestDB(t)
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("Failed to create EventBus: %v", err)
	}
	defer eb.Close()

	var receivedAgentID string
	done := make(chan bool, 1)

	eb.Subscribe(TopicAgentTelemetry, func(e BusEvent) error {
		receivedAgentID = e.AgentID
		done <- true
		return nil
	})

	// Publish with agent ID
	eb.PublishWithAgent(TopicAgentTelemetry, map[string]interface{}{"cpu": 50}, "agent-123")

	select {
	case <-done:
		if receivedAgentID != "agent-123" {
			t.Errorf("Expected agent ID 'agent-123', got '%s'", receivedAgentID)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Timeout waiting for event")
	}
}
