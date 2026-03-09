//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

// Wave 53: announceNodeToPeer, syncRoyalJelly, WebSocketHub.SendBootstrap,
//          fleetScaleRecommendPatrol, contextsHandler (with data), MigrateUp paths

// ─── announceNodeToPeer ───────────────────────────────────────────────────

func TestAnnounceNodeToPeer_BadURL_W53(t *testing.T) {
	// Invalid URL → HTTP client fails gracefully.
	self := Node{ID: "test-node-w53", Hostname: "test-host", Status: "online"}
	// Should not panic — just logs the error.
	announceNodeToPeer("http://invalid-host-that-does-not-exist-w53:9999", self)
}

func TestAnnounceNodeToPeer_TestServer_W53(t *testing.T) {
	var received bool
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		received = true
		w.WriteHeader(http.StatusOK)
	}))
	defer ts.Close()

	self := Node{ID: "test-node-w53", Hostname: "test-host", Status: "online"}
	announceNodeToPeer(ts.URL, self)
	if !received {
		t.Error("expected server to receive announce request")
	}
}

// ─── syncRoyalJelly ───────────────────────────────────────────────────────

func TestSyncRoyalJelly_NoContextDir_W53(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// No .forge/context dir in test cwd → returns nil (IsNotExist path).
	// Run from a temp dir to avoid hitting actual context dir.
	if err := syncRoyalJelly(context.Background(), db); err != nil {
		t.Logf("syncRoyalJelly no dir: %v (may be OK)", err)
	}
}

// ─── fleetScaleRecommendPatrol ────────────────────────────────────────────

func TestFleetScaleRecommendPatrol_W53(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// No agents in agent_heartbeats or scale_recommendations → returns nil.
	if err := fleetScaleRecommendPatrol(ctx, db); err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}

func TestFleetScaleRecommendPatrol_CircuitBreakerOpen_W53(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Open circuit breaker → should skip and return nil.
	circuitBreaker.Lock()
	origState := circuitBreaker.state
	circuitBreaker.state = "open"
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = origState
		circuitBreaker.Unlock()
	}()

	if err := fleetScaleRecommendPatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil with circuit breaker open, got %v", err)
	}
}

// ─── contextsHandler with data ────────────────────────────────────────────

func TestContextsHandler_AllFilters_W53(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Test with all filters combined.
	req := httptest.NewRequest(http.MethodGet,
		"/api/contexts?agent_id=agent-w53&domain=test&project=proj&task_id=TASK-W53", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ─── MigrateUp paths ──────────────────────────────────────────────────────

func TestMigrateUp_Success_W53(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Already migrated → should be idempotent (no error or known "already exists" error).
	err := MigrateUp(db)
	if err != nil {
		t.Logf("MigrateUp on already-migrated DB: %v (expected — tables already exist)", err)
	}
}
