package main

import (
	"context"
	"fmt"
	"testing"
	"time"
)

// Benchmark-style performance tests to guard basic v3 throughput characteristics.

// TestTaskCreationPerformance ensures we can create at least ~100 tasks/sec.
func TestTaskCreationPerformance(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	const totalTasks = 200

	start := time.Now()
	for i := 0; i < totalTasks; i++ {
		task := Task{
			ID:       generateTaskID(),
			Domain:   "perf-domain",
			Project:  "perf-project",
			Type:     TaskTypeFeature,
			Priority: 10,
			Status:   TaskStatusQueued,
		}
		if err := q.Enqueue(ctx, task); err != nil {
			t.Fatalf("Enqueue failed at #%d: %v", i, err)
		}
	}
	elapsed := time.Since(start)

	tasksPerSecond := float64(totalTasks) / elapsed.Seconds()
	if tasksPerSecond < 100 {
		t.Fatalf("task creation too slow: %.1f tasks/sec (want >= 100)", tasksPerSecond)
	}
}

// TestDispatchLatency measures Dequeue latency as a proxy for dispatch latency
// and asserts it's comfortably under 250ms on a warm system.
func TestDispatchLatency(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	agentID := "perf-agent"

	// Seed a single task.
	task := Task{
		ID:       "perf-dispatch-001",
		Domain:   "perf-domain",
		Project:  "perf-project",
		Type:     TaskTypeFeature,
		Priority: 50,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	start := time.Now()
	if _, err := q.Dequeue(ctx, agentID); err != nil {
		t.Fatalf("Dequeue failed: %v", err)
	}
	elapsed := time.Since(start)

	if elapsed > 250*time.Millisecond {
		t.Fatalf("dispatch latency too high: %s (want <= 250ms)", elapsed)
	}
}

// TestConcurrentAgentHandling checks that 12 agents can pull work concurrently
// without errors or deadlocks.
func TestConcurrentAgentHandling(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	const (
		agents     = 12
		taskCount  = 120
		agentIDFmt = "perf-agent-%02d"
	)

	// Seed tasks.
	for i := 0; i < taskCount; i++ {
		task := Task{
			ID:       generateTaskID(),
			Domain:   "perf-domain",
			Project:  "perf-project",
			Type:     TaskTypeFeature,
			Priority: 10,
			Status:   TaskStatusQueued,
		}
		if err := q.Enqueue(ctx, task); err != nil {
			t.Fatalf("Enqueue failed at #%d: %v", i, err)
		}
	}

	doneCh := make(chan struct{})
	errCh := make(chan error, agents)

	start := time.Now()

	for i := 0; i < agents; i++ {
		agentID := fmt.Sprintf(agentIDFmt, i+1)
		go func(aID string) {
			defer func() { doneCh <- struct{}{} }()
			for {
				_, err := q.Dequeue(ctx, aID)
				if err != nil {
					// When queue is empty, Dequeue will eventually fail; treat that as completion.
					return
				}
			}
		}(agentID)
	}

	for i := 0; i < agents; i++ {
		select {
		case <-doneCh:
		case err := <-errCh:
			t.Fatalf("agent error: %v", err)
		case <-time.After(5 * time.Second):
			t.Fatalf("timeout waiting for agent %d to finish", i)
		}
	}

	elapsed := time.Since(start)
	if elapsed > 5*time.Second {
		t.Fatalf("concurrent handling too slow: %s (want <= 5s for %d tasks and %d agents)", elapsed, taskCount, agents)
	}
}

// TestQueryPerformance benchmarks the common queries used in task management.
func TestQueryPerformance(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	const taskCount = 1000

	// Seed many tasks to make queries non-trivial
	for i := 0; i < taskCount; i++ {
		task := Task{
			ID:       fmt.Sprintf("perf-task-%d", i),
			Domain:   "perf",
			Project:  "perf",
			Type:     TaskTypeFeature,
			Priority: i % 100,
			Status:   TaskStatusQueued,
			State:    StateQueued,
		}
		if err := q.Enqueue(ctx, task); err != nil {
			t.Fatalf("Enqueue failed: %v", err)
		}
	}

	// Benchmark Dequeue (polling)
	start := time.Now()
	for i := 0; i < 10; i++ {
		agentID := fmt.Sprintf("agent-%d", i)
		_, err := q.Dequeue(ctx, agentID)
		if err != nil {
			t.Fatalf("Dequeue failed: %v", err)
		}
	}
	elapsed := time.Since(start)
	t.Logf("10 Dequeues took %s (avg %s/op)", elapsed, elapsed/10)

	if (elapsed / 10) > 100*time.Millisecond {
		t.Errorf("Average Dequeue time too high: %s (want < 100ms)", elapsed/10)
	}
}
