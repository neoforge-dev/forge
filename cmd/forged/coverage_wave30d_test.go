//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
	"time"
)

// Wave 30d: circuitBreakerTripped (66.7%), checkTierCompatibility (55.6%), deflationGateCheck paths

func TestCircuitBreakerTripped_Closed_W30D(t *testing.T) {
	// Closed state — should return false
	circuitBreaker.Lock()
	orig := circuitBreaker.state
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = orig
		circuitBreaker.Unlock()
	}()

	if circuitBreakerTripped() {
		t.Error("expected false for closed circuit breaker")
	}
}

func TestCircuitBreakerTripped_OpenRecent_W30D(t *testing.T) {
	// Open within 5 minutes — should return true
	circuitBreaker.Lock()
	orig := circuitBreaker.state
	origLast := circuitBreaker.lastFailure
	circuitBreaker.state = "open"
	circuitBreaker.lastFailure = time.Now() // just now
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = orig
		circuitBreaker.lastFailure = origLast
		circuitBreaker.Unlock()
	}()

	if !circuitBreakerTripped() {
		t.Error("expected true for recently opened circuit breaker")
	}
}

func TestCircuitBreakerTripped_OpenExpired_W30D(t *testing.T) {
	// Open but 6 minutes ago — should auto-reset and return false
	circuitBreaker.Lock()
	orig := circuitBreaker.state
	origLast := circuitBreaker.lastFailure
	origFails := circuitBreaker.consecutiveFailures
	circuitBreaker.state = "open"
	circuitBreaker.lastFailure = time.Now().Add(-6 * time.Minute)
	circuitBreaker.consecutiveFailures = 3
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = orig
		circuitBreaker.lastFailure = origLast
		circuitBreaker.consecutiveFailures = origFails
		circuitBreaker.Unlock()
	}()

	if circuitBreakerTripped() {
		t.Error("expected false for expired circuit breaker (auto-reset)")
	}

	// Verify it reset
	circuitBreaker.Lock()
	st := circuitBreaker.state
	circuitBreaker.Unlock()
	if st != "closed" {
		t.Errorf("expected state 'closed' after auto-reset, got %q", st)
	}
}

func TestCheckTierCompatibility_NilDB_W30D(t *testing.T) {
	orig := getDBConn()
	setDBConn(nil)
	defer setDBConn(orig)

	ctx := context.Background()
	err := checkTierCompatibility(ctx, "TASK-X", "agent-1")
	if err != nil {
		t.Errorf("expected nil error with nil DB, got %v", err)
	}
}

func TestCheckTierCompatibility_AnyTier_W30D(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()
	// Task with no required_tier (defaults to "any") — should always succeed
	err := checkTierCompatibility(ctx, "TASK-NONEXISTENT", "agent-1")
	if err != nil {
		t.Errorf("expected nil for task with no tier req, got %v", err)
	}
}

func TestRecordSpawnFailure_W30D(t *testing.T) {
	// Save and restore state
	circuitBreaker.Lock()
	origState := circuitBreaker.state
	origFails := circuitBreaker.consecutiveFailures
	origLast := circuitBreaker.lastFailure
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = origState
		circuitBreaker.consecutiveFailures = origFails
		circuitBreaker.lastFailure = origLast
		circuitBreaker.Unlock()
	}()

	recordSpawnFailure()
	circuitBreaker.Lock()
	n := circuitBreaker.consecutiveFailures
	circuitBreaker.Unlock()
	if n != 1 {
		t.Errorf("expected 1 consecutive failure, got %d", n)
	}
}

func TestRecordSpawnFailure_OpensCircuitBreaker_W30D(t *testing.T) {
	circuitBreaker.Lock()
	origState := circuitBreaker.state
	origFails := circuitBreaker.consecutiveFailures
	origLast := circuitBreaker.lastFailure
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 2
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = origState
		circuitBreaker.consecutiveFailures = origFails
		circuitBreaker.lastFailure = origLast
		circuitBreaker.Unlock()
	}()

	// 3rd failure should open the circuit breaker
	recordSpawnFailure()

	circuitBreaker.Lock()
	st := circuitBreaker.state
	circuitBreaker.Unlock()
	if st != "open" {
		t.Errorf("expected 'open' after 3 failures, got %q", st)
	}
}

func TestRecordSpawnSuccess_W30D(t *testing.T) {
	circuitBreaker.Lock()
	origFails := circuitBreaker.consecutiveFailures
	circuitBreaker.consecutiveFailures = 2
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.consecutiveFailures = origFails
		circuitBreaker.Unlock()
	}()

	recordSpawnSuccess()

	circuitBreaker.Lock()
	n := circuitBreaker.consecutiveFailures
	circuitBreaker.Unlock()
	if n != 0 {
		t.Errorf("expected 0 after success, got %d", n)
	}
}
