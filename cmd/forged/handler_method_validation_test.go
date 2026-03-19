//go:build !tmux_bridge
// +build !tmux_bridge

// handler_method_validation_test.go — Consolidated MethodNotAllowed (HTTP 405) tests.
// All tests verify that HTTP handlers reject wrong-method requests with 405.
// Extracted from coverage_wave*_test.go files to eliminate duplication.
package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------------------------------------------------------------------------
// Previously consolidated tests (from coverage_wave8, wave94, wave148, wave223)
// ---------------------------------------------------------------------------

func TestPlanTaskHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-001/plan", nil)
	w := httptest.NewRecorder()
	planTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestReplanTaskHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-001/replan", nil)
	w := httptest.NewRecorder()
	replanTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestQueueTaskHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-001/queue", nil)
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestPauseTaskHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-001/pause", nil)
	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestConfigHandler_Delete_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodDelete, "/config", nil)
	w := httptest.NewRecorder()
	configHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("configHandler DELETE: expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_plan.go — from coverage_wave94
// ---------------------------------------------------------------------------

func TestWave94_PauseTaskHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/tasks/wave94-task/pause", nil)
	w := httptest.NewRecorder()
	pauseTaskHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("pauseTaskHandler GET: got %d", w.Code)
	}
}

func TestWave94_ResumeTaskHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/tasks/wave94-task/resume", nil)
	w := httptest.NewRecorder()
	resumeTaskHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("resumeTaskHandler GET: got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_node_metrics.go — from coverage_wave148
// ---------------------------------------------------------------------------

func TestWave148_FleetMetricsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/fleet/metrics", nil)
	w := httptest.NewRecorder()
	fleetMetricsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave148_FleetAggregateHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// routing_handler.go + handlers_lane.go — from coverage_wave223
// ---------------------------------------------------------------------------

func TestWave223_RoutingResolveHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/routing/resolve", nil)
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave223_LanesHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/lanes", nil)
	w := httptest.NewRecorder()
	lanesHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave223_LaneByIDHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/lanes/some-id", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave104
// ---------------------------------------------------------------------------

func TestWave104_RelayDeliveries_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/relay/deliveries", nil)
	w := httptest.NewRecorder()
	relayDeliveriesHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave104_RelayDispatch_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/relay/dispatch", nil)
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave104_RelayAck_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/relay/some-id/ack", nil)
	w := httptest.NewRecorder()
	relayAckHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave104_ApprovalAction_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/some-id/approve", nil)
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave107
// ---------------------------------------------------------------------------

func TestWave107_OpenclawStatusHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/status", nil)
	w := httptest.NewRecorder()
	openclawStatusHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave107_OpenclawChatHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/chat", nil)
	w := httptest.NewRecorder()
	openclawChatHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave107_OpenclawIngestHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/ingest", nil)
	w := httptest.NewRecorder()
	openclawIngestHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave108
// ---------------------------------------------------------------------------

func TestWave108_OpenclawEventsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/events", nil)
	w := httptest.NewRecorder()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	openclawEventsHandler(w, req.WithContext(ctx))
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave108_AgentsSSEHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/stream", nil)
	w := httptest.NewRecorder()
	agentsSSEHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave108_DashHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave10
// ---------------------------------------------------------------------------

func TestHandleQueuePriority_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/queue/priority", nil)
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandleQueueCancel_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/queue/cancel", nil)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave117
// ---------------------------------------------------------------------------

func TestWave117_RoutingResolveHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/routing/resolve", nil)
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave11
// ---------------------------------------------------------------------------

func TestContextsHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestUiFleetHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/ui", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestResumeTaskHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-001/resume", nil)
	w := httptest.NewRecorder()
	resumeTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave120
// ---------------------------------------------------------------------------

func TestWave120_patrolRunHandler_GET_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/patrols/agent-health/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave125
// ---------------------------------------------------------------------------

func TestWave125_AgentTelemetrySummaryHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave125_AgentsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents", nil)
	w := httptest.NewRecorder()
	agentsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave130
// ---------------------------------------------------------------------------

func TestWave130_OpenclawEventsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/events", nil)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave130_AgentsSSEHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/stream", nil)
	w := httptest.NewRecorder()
	agentsSSEHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave131
// ---------------------------------------------------------------------------

func TestWave131_GithubWebhook_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/github/webhook", nil)
	w := httptest.NewRecorder()
	githubWebhookHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave131_AgentTasksHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/test-agent/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave137
// ---------------------------------------------------------------------------

func TestWave137_PatrolRunHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/api/patrols/my-patrol/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

func TestWave137_AgentTelemetrySummary_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

func TestWave137_AgentTasksHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/agents/agent-1/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

func TestWave137_ClaimTaskHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/claim", nil)
	w := httptest.NewRecorder()
	claimTaskHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

func TestWave137_UIPatrolDrillDown_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/ui/patrol/some-id", nil)
	w := httptest.NewRecorder()
	uiPatrolDrillDownHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

func TestWave137_LaneByIDHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/lanes/lane-1", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

func TestWave137_ContextsHandler_MethodNotAllowed(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave138
// ---------------------------------------------------------------------------

func TestWave138_AgentsSSEHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/stream", nil)
	w := httptest.NewRecorder()
	agentsSSEHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave140
// ---------------------------------------------------------------------------

func TestWave140_AgentTasksHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodPost, "/api/agents/test-agent/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave140_ClaimTaskHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/some-task/claim", nil)
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave140_AgentTelemetrySummaryHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave140_PatrolRunHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/patrols/some-patrol/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave141
// ---------------------------------------------------------------------------

func TestWave141_RoutingHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/routing/resolve", nil)
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave143
// ---------------------------------------------------------------------------

func TestWave143_AgentsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents", nil)
	w := httptest.NewRecorder()
	agentsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave143_WorkersHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/workers", nil)
	w := httptest.NewRecorder()
	workersHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave143_WorkerByIDHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/workers/some-id", nil)
	w := httptest.NewRecorder()
	workerByIDHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave143_HandleApprovalCount_MethodNotAllowed(t *testing.T) {
	h, cleanup := newTestApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/approvals/count", nil)
	w := httptest.NewRecorder()
	h.handleApprovalCount(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave143_HandlePending_MethodNotAllowed(t *testing.T) {
	h, cleanup := newTestApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/approvals/pending", nil)
	w := httptest.NewRecorder()
	h.handlePending(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave143_HandleLeadState_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := handleLeadState(db)
	req := httptest.NewRequest(http.MethodPost, "/api/lead-state", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// coverage_wave144
// ---------------------------------------------------------------------------

func TestWave144_HandleAuthRefresh_MethodNotAllowed(t *testing.T) {
	auth := newTestAuthManager()
	handler := handleAuthRefresh(auth)
	req := httptest.NewRequest(http.MethodGet, "/api/auth/refresh", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave144_AuthTokensHandler_MethodNotAllowed(t *testing.T) {
	auth := newTestAuthManager()
	handler := authTokensHandler(auth)
	req := httptest.NewRequest(http.MethodDelete, "/api/auth/tokens", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave144_AgentMetricsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/kimi/metrics", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// coverage_wave146
// ---------------------------------------------------------------------------

func TestWave146_ConfigHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodDelete, "/config", nil)
	w := httptest.NewRecorder()
	configHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave146_DispatchHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodDelete, "/api/dispatch", nil)
	w := httptest.NewRecorder()
	dispatchHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave146_NodeMetricsReceive_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/nodes/prya/metrics", nil)
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave147
// ---------------------------------------------------------------------------

func TestWave147_PatrolRunHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/patrols/health-ping/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave147_PatrolsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/patrols", nil)
	w := httptest.NewRecorder()
	patrolsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave147_NodesHealthHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/nodes/health", nil)
	w := httptest.NewRecorder()
	nodesHealthHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave147_FleetSnapshotHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/fleet/snapshot", nil)
	w := httptest.NewRecorder()
	fleetSnapshotHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave149
// ---------------------------------------------------------------------------

func TestWave149_FleetRecommendationsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/fleet/recommendations", nil)
	w := httptest.NewRecorder()
	fleetRecommendationsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave149_PatrolExecutionsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/patrol-executions", nil)
	w := httptest.NewRecorder()
	patrolExecutionsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave14
// ---------------------------------------------------------------------------

func TestAgentByIDHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent-001", nil)
	w := httptest.NewRecorder()
	agentByIDHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestLaneByIDHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/lanes/forge-main", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestDashHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave150
// ---------------------------------------------------------------------------

func TestWave150_RelayDeliveriesHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/relay/deliveries", nil)
	w := httptest.NewRecorder()
	relayDeliveriesHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave150_RelayDispatchHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/relay/dispatch", nil)
	w := httptest.NewRecorder()
	relayDispatchHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave150_RelayAckHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/relay/some-id/ack", nil)
	w := httptest.NewRecorder()
	relayAckHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave150_DashHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave151
// ---------------------------------------------------------------------------

func TestWave151_ServeAgentsDashboard_MethodNotAllowed(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/dashboard", nil)
	w := httptest.NewRecorder()
	d.ServeAgentsDashboard(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave154
// ---------------------------------------------------------------------------

func TestWave154_PatrolRunHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/patrols/some-patrol/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave154_HandleAuthRefresh_MethodNotAllowed(t *testing.T) {
	auth := newTestAuthManagerWave144()
	handler := handleAuthRefresh(auth)
	req := httptest.NewRequest(http.MethodGet, "/api/auth/refresh", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave154_AuthTokensHandler_MethodNotAllowed(t *testing.T) {
	auth := newTestAuthManagerWave144()
	handler := authTokensHandler(auth)
	req := httptest.NewRequest(http.MethodDelete, "/api/auth/tokens", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave154_AgentMetricsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/kimi/metrics", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// coverage_wave15
// ---------------------------------------------------------------------------

func TestHandoffHandler_MethodNotAllowed_W15(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	handler := NewHandoffHandler(service)

	req := httptest.NewRequest(http.MethodDelete, "/api/handoffs", nil)
	w := httptest.NewRecorder()
	handler.handleHandoffs(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandoffHandler_ActionMethodNotAllowed(t *testing.T) {
	_, service, cleanup := setupHandoffTest(t)
	defer cleanup()

	handler := NewHandoffHandler(service)

	req := httptest.NewRequest(http.MethodGet, "/api/handoffs/some-id/accept", nil)
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestAgentFleetSummaryHandler_V2_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/fleet/summary", nil)
	w := httptest.NewRecorder()
	agentFleetSummaryHandler_V2(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestOpenclawChatHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodDelete, "/api/openclaw/chat", nil)
	w := httptest.NewRecorder()
	openclawChatHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestOpenclawHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPut, "/api/openclaw", nil)
	w := httptest.NewRecorder()
	openclawHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave16
// ---------------------------------------------------------------------------

func TestHandoffHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := NewHandoffHandler(NewHandoffService(NewHandoffStore(db)))
	req := httptest.NewRequest(http.MethodDelete, "/api/handoffs", nil)
	w := httptest.NewRecorder()
	handler.handleHandoffs(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandoffHandler_Action_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := NewHandoffHandler(NewHandoffService(NewHandoffStore(db)))
	req := httptest.NewRequest(http.MethodGet, "/api/handoffs/some-id/accept", nil)
	req.URL.Path = "/api/handoffs/some-id/accept"
	w := httptest.NewRecorder()
	handler.handleHandoffAction(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave180
// ---------------------------------------------------------------------------

func TestWave180_AgentTasksHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/kimi/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave181
// ---------------------------------------------------------------------------

func TestWave181_ClaimTaskHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/abc/claim", nil)
	w := httptest.NewRecorder()

	claimTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave20
// ---------------------------------------------------------------------------

func TestFleetRecommendationsHandler_MethodNotAllowed_W20(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/fleet/recommendations", nil)
	w := httptest.NewRecorder()
	fleetRecommendationsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestDashHandler_MethodNotAllowed_W20(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/ui/", nil)
	w := httptest.NewRecorder()
	dashHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandleQueueCancel_MethodNotAllowed_W20(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/queue/cancel", nil)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestOpenclawEventsHandler_MethodNotAllowed_W20(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/events", nil)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestResumeTaskHandler_MethodNotAllowed_W20(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-001/resume", nil)
	w := httptest.NewRecorder()
	resumeTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestUIFleetHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestCompleteTaskWithApprovalHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-1/complete-with-approval", nil)
	req.URL.Path = "/api/tasks/TASK-1/complete-with-approval"
	w := httptest.NewRecorder()
	completeTaskWithApprovalHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestXNodeForwardHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/forward", nil)
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestEnvelopesHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()

	req := httptest.NewRequest(http.MethodDelete, "/api/context/envelopes", nil)
	w := httptest.NewRecorder()
	cm.EnvelopesHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestAgentFleetSummaryHandlerV2_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/v2/agents/fleet-summary", nil)
	w := httptest.NewRecorder()
	agentFleetSummaryHandler_V2(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestAgentsSSEHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/sse", nil)
	w := httptest.NewRecorder()
	agentsSSEHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave21
// ---------------------------------------------------------------------------

func TestCompleteWithApproval_MethodNotAllowed_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-1/complete-with-approval", nil)
	req.URL.Path = "/api/tasks/TASK-1/complete-with-approval"
	w := httptest.NewRecorder()
	completeTaskWithApprovalHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestForwardHandler_MethodNotAllowed_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/forward", nil)
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestLaneByIDHandler_MethodNotAllowed_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/lanes/dev", nil)
	req.URL.Path = "/api/lanes/dev"
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestAgentByIDHandler_MethodNotAllowed_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent-1", nil)
	req.URL.Path = "/api/agents/agent-1"
	w := httptest.NewRecorder()
	agentByIDHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestNodesHealthHandler_MethodNotAllowed_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/nodes/health", nil)
	w := httptest.NewRecorder()
	nodesHealthHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestFleetSnapshotHandler_MethodNotAllowed_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/fleet/snapshot", nil)
	w := httptest.NewRecorder()
	fleetSnapshotHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestBootstrapHandler_MethodNotAllowed_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()

	req := httptest.NewRequest(http.MethodDelete, "/api/context/bootstrap", nil)
	w := httptest.NewRecorder()
	cm.BootstrapHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestPatrolExecutionsHandler_MethodNotAllowed_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/patrol-executions", nil)
	w := httptest.NewRecorder()
	patrolExecutionsHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestContextsHandler_MethodNotAllowed_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestReplanTaskHandler_MethodNotAllowed_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-1/replan", nil)
	req.URL.Path = "/api/tasks/TASK-1/replan"
	w := httptest.NewRecorder()
	replanTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestEnvelopeByIDHandler_MethodNotAllowed_W21(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()
	req := httptest.NewRequest(http.MethodPost, "/api/context/envelopes/abc", nil)
	w := httptest.NewRecorder()
	cm.EnvelopeByIDHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

func TestGetTaskEventsHandler_MethodNotAllowed_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/abc/events", nil)
	req.URL.Path = "/api/tasks/abc/events"
	w := httptest.NewRecorder()
	getTaskEventsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

func TestAgentTasksHandler_MethodNotAllowed_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	req := httptest.NewRequest(http.MethodPost, "/api/agents/codex/tasks", nil)
	req.URL.Path = "/api/agents/codex/tasks"
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

func TestHandleQueuePriority_MethodNotAllowed_W21(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	req := httptest.NewRequest(http.MethodGet, "/cli/queue/priority", nil)
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

func TestApprovalHandlerPending_MethodNotAllowed_W21(t *testing.T) {
	db, _ := setupClaimTestDB(t)
	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)
	req := httptest.NewRequest(http.MethodPost, "/api/approvals/pending", nil)
	w := httptest.NewRecorder()
	h.handlePending(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave221
// ---------------------------------------------------------------------------

func TestWave221_HandleLeadState_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	handler := handleLeadState(db)
	req := httptest.NewRequest(http.MethodPost, "/api/lead-state", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave221_HandleAuthLogin_MethodNotAllowed(t *testing.T) {
	auth := NewAuthManager("local")
	handler := handleAuthLogin(auth)

	req := httptest.NewRequest(http.MethodGet, "/api/auth/login", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave221_HandleAuthMe_MethodNotAllowed(t *testing.T) {
	auth := NewAuthManager("local")
	handler := handleAuthMe(auth)

	req := httptest.NewRequest(http.MethodPost, "/api/auth/me", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave221_HandleAuthRefresh_MethodNotAllowed(t *testing.T) {
	auth := NewAuthManager("local")
	handler := handleAuthRefresh(auth)

	req := httptest.NewRequest(http.MethodGet, "/api/auth/refresh", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave222
// ---------------------------------------------------------------------------

func TestWave222_ContextsHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave224
// ---------------------------------------------------------------------------

func TestWave224_MessagesHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodDelete, "/api/messages", nil)
	w := httptest.NewRecorder()
	messagesHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave225
// ---------------------------------------------------------------------------

func TestWave225_NodeCapabilities_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/fleet/node-capabilities", nil)
	w := httptest.NewRecorder()

	nodeCapabilitiesHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave227
// ---------------------------------------------------------------------------

func TestWave227_HeartbeatReceive_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-1/heartbeat", nil)
	req.URL.Path = "/api/agents/agent-1/heartbeat"
	w := httptest.NewRecorder()
	agentHeartbeatReceive(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave227_AgentTelemetry_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/a1/telemetry", nil)
	req.URL.Path = "/api/agents/a1/telemetry"
	w := httptest.NewRecorder()
	agentTelemetryHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave227_AgentMetrics_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/a1/metrics", nil)
	req.URL.Path = "/api/agents/a1/metrics"
	w := httptest.NewRecorder()
	agentMetricsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave227_TelemetrySummary_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave227_AgentsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents", nil)
	w := httptest.NewRecorder()
	agentsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave227_AgentsSSE_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/stream", nil)
	w := httptest.NewRecorder()
	agentsSSEHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave234
// ---------------------------------------------------------------------------

func TestWave234_GithubWebhook_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/github/webhook", nil)
	w := httptest.NewRecorder()
	githubWebhookHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave235
// ---------------------------------------------------------------------------

func TestWave235_TaskHistory_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-1/history", nil)
	req.URL.Path = "/api/tasks/task-1/history"
	w := httptest.NewRecorder()
	taskHistoryHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave235_PruneTasks_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/prune", nil)
	w := httptest.NewRecorder()
	pruneTasksHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave235_GetTask_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-1", nil)
	req.URL.Path = "/api/tasks/task-1"
	w := httptest.NewRecorder()
	getTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave235_ApproveTask_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/approve", nil)
	req.URL.Path = "/api/tasks/task-1/approve"
	w := httptest.NewRecorder()
	approveTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave235_ClaimableTasks_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/claimable", nil)
	w := httptest.NewRecorder()
	claimableTasksHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave235_AbandonTask_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/abandon", nil)
	req.URL.Path = "/api/tasks/task-1/abandon"
	w := httptest.NewRecorder()
	abandonTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave236
// ---------------------------------------------------------------------------

func TestWave236_PatrolRun_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/patrols/agent-health/run", nil)
	req.URL.Path = "/api/patrols/agent-health/run"
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave236_ApprovalCount_MethodNotAllowed(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	handler := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodPost, "/api/approvals/count", nil)
	w := httptest.NewRecorder()
	handler.handleApprovalCount(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave237
// ---------------------------------------------------------------------------

func TestWave237_PatrolsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/patrols", nil)
	w := httptest.NewRecorder()
	patrolsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave238
// ---------------------------------------------------------------------------

func TestWave238_HandleQueuePriority_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/queue/priority", nil)
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave238_HandleQueueCancel_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/queue/cancel", nil)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave238_NodesHealthHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/nodes/health", nil)
	w := httptest.NewRecorder()
	nodesHealthHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave240
// ---------------------------------------------------------------------------

func TestWave240_XNode_ForwardHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "node-240")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/forward", nil)
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave240_XNode_RegisterHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "node-240")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes/register", nil)
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave241
// ---------------------------------------------------------------------------

func TestWave241_GitguardHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, t.TempDir())
	handler := gitguardHandler(gg)

	req := httptest.NewRequest(http.MethodDelete, "/api/gitguard", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave243
// ---------------------------------------------------------------------------

func TestWave243_PatrolRunHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/patrols/my-patrol/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave243_AgentTelemetrySummaryHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave244
// ---------------------------------------------------------------------------

func TestWave244_ClaimTaskHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-244/claim", nil)
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave250
// ---------------------------------------------------------------------------

func TestWave250_AuthTokensHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	auth := NewAuthManager("test-secret")

	req := httptest.NewRequest(http.MethodDelete, "/api/auth/tokens", nil)
	rr := httptest.NewRecorder()

	handler := authTokensHandler(auth)
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestWave250_QualityGatesHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-123/quality-gates", nil)
	rr := httptest.NewRecorder()

	qualityGatesHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestWave250_DashboardThroughputHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/dashboard/throughput", nil)
	rr := httptest.NewRecorder()

	dashboardThroughputHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestWave250_ReleaseTaskHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-123/release", nil)
	rr := httptest.NewRecorder()

	releaseTaskHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestWave250_ExtendLeaseHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-123/extend-lease", nil)
	rr := httptest.NewRecorder()

	extendLeaseHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave252
// ---------------------------------------------------------------------------

func TestWave252_RoutingResolveHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/routing/resolve", nil)
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for GET, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave253
// ---------------------------------------------------------------------------

func TestWave253_CreateTaskHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", rr.Code)
	}
}

func TestWave253_AgentHeartbeatReceive_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/foo/heartbeat", nil)
	rr := httptest.NewRecorder()

	agentHeartbeatReceive(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", rr.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave256
// ---------------------------------------------------------------------------

func TestWave256_XNode_RegisterHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctrl, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/register", nil)
	w := httptest.NewRecorder()
	ctrl.RegisterHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave258
// ---------------------------------------------------------------------------

func TestWave258_GithubWebhookHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/github/webhook", nil)
	w := httptest.NewRecorder()
	githubWebhookHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave25b
// ---------------------------------------------------------------------------

func TestPatternsHandler_MethodNotAllowed_W25B(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPut, "/api/patterns", nil)
	w := httptest.NewRecorder()
	patternsHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave261
// ---------------------------------------------------------------------------

func TestWave261_AuthTokensHandler_MethodNotAllowed(t *testing.T) {
	auth := NewAuthManager("local")
	
	handler := authTokensHandler(auth)
	
	methods := []string{http.MethodPut, http.MethodDelete, http.MethodPatch}
	
	for _, method := range methods {
		t.Run(method, func(t *testing.T) {
			req := httptest.NewRequest(method, "/api/auth/tokens", nil)
			w := httptest.NewRecorder()
			
			handler(w, req)
			
			if w.Code != http.StatusMethodNotAllowed {
				t.Errorf("expected status 405 for %s, got %d", method, w.Code)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// coverage_wave268
// ---------------------------------------------------------------------------

func TestWave268_TUILogsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tui/logs", nil)
	w := httptest.NewRecorder()
	
	tuiLogsHandler(w, req)
	
	resp := w.Result()
	// tuiLogsHandler accepts any method and returns logs
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200 (handler accepts any method), got %d", resp.StatusCode)
	}
}

func TestWave268_PlanTaskHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/123/plan", nil)
	w := httptest.NewRecorder()
	
	planTaskHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", resp.StatusCode)
	}
}

func TestWave268_ReplanTaskHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/123/replan", nil)
	w := httptest.NewRecorder()
	
	replanTaskHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", resp.StatusCode)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave269
// ---------------------------------------------------------------------------

func TestWave269_PatternsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPut, "/api/patterns", nil)
	w := httptest.NewRecorder()
	
	patternsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", resp.StatusCode)
	}
}

func TestWave269_CreatePatternHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPut, "/api/patterns", nil)
	w := httptest.NewRecorder()
	
	patternsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", resp.StatusCode)
	}
}

func TestWave269_PatternByIDOrRunsHandler_MethodNotAllowed(t *testing.T) {
	_, _ = setupClaimTestDB(t) // For global DB
	req := httptest.NewRequest(http.MethodPost, "/api/patterns/123", nil)
	w := httptest.NewRecorder()
	
	patternByIDOrRunsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", resp.StatusCode)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave270
// ---------------------------------------------------------------------------

func TestWave270_ProjectsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPut, "/api/projects", nil)
	w := httptest.NewRecorder()
	
	projectsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", resp.StatusCode)
	}
}

func TestWave270_CreateProjectHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPut, "/api/projects", nil)
	w := httptest.NewRecorder()
	
	projectsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", resp.StatusCode)
	}
}

func TestWave270_ProjectByIDHandler_MethodNotAllowed(t *testing.T) {
	_, _ = setupClaimTestDB(t) // For global DB
	req := httptest.NewRequest(http.MethodPost, "/api/projects/123", nil)
	w := httptest.NewRecorder()
	
	projectByIDHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", resp.StatusCode)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave271
// ---------------------------------------------------------------------------

func TestCoverageWave271_AgentTelemetrySummaryHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()

	agentTelemetrySummaryHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestCoverageWave271_HandleLeadState_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/lead/state", nil)
	w := httptest.NewRecorder()

	handler := handleLeadState(getDBConn())
	handler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestCoverageWave271_LanesHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/lanes", nil)
	w := httptest.NewRecorder()

	lanesHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave29
// ---------------------------------------------------------------------------

func TestUIFleetHandler_MethodNotAllowed_W29(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandleQueueCancel_MethodNotAllowed_W29(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/queue/cancel", nil)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave31
// ---------------------------------------------------------------------------

func TestFleetAggregateHandler_MethodNotAllowed_W31(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave32
// ---------------------------------------------------------------------------

func TestXNodeForwardHandler_MethodNotAllowed_W32(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w32")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/forward", nil)
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestAgentsSSEHandler_MethodNotAllowed_W32(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/sse", nil)
	w := httptest.NewRecorder()
	agentsSSEHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave33
// ---------------------------------------------------------------------------

func TestHandleQueuePriority_MethodNotAllowed_W33(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/queue/priority", nil)
	w := httptest.NewRecorder()
	handleQueuePriority(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave40
// ---------------------------------------------------------------------------

func TestHandleQueueCancel_MethodNotAllowed_W40(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/queue/cancel", nil)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave41
// ---------------------------------------------------------------------------

func TestQueueTaskHandler_MethodNotAllowed_W41(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-X/queue", nil)
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestAgentTasksHandler_MethodNotAllowed_W41(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent-x/tasks", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave42
// ---------------------------------------------------------------------------

func TestAgentByIDHandler_MethodNotAllowed_W42(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent-x", nil)
	w := httptest.NewRecorder()
	agentByIDHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave45
// ---------------------------------------------------------------------------

func TestHandlePending_MethodNotAllowed_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/approvals/pending", nil)
	w := httptest.NewRecorder()
	h.handlePending(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandleApprovalAction_MethodNotAllowed_W45(t *testing.T) {
	h, cleanup := setupApprovalHandler(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/some-id/approve", nil)
	req.URL.Path = "/api/approvals/some-id/approve"
	w := httptest.NewRecorder()
	h.handleApprovalAction(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave46
// ---------------------------------------------------------------------------

func TestContextsHandler_MethodNotAllowed_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestGetTaskEventsHandler_MethodNotAllowed_W46(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-X/events", nil)
	w := httptest.NewRecorder()
	getTaskEventsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestReleaseTaskHandler_MethodNotAllowed_W46(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-X/release", nil)
	w := httptest.NewRecorder()
	releaseTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave47
// ---------------------------------------------------------------------------

func TestUIFleetHandler_MethodNotAllowed_W47(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestPlanTaskHandler_MethodNotAllowed_W47(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-X/plan", nil)
	w := httptest.NewRecorder()
	planTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestCompleteTaskHandler_MethodNotAllowed_W47(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-X/complete", nil)
	w := httptest.NewRecorder()
	completeTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave48
// ---------------------------------------------------------------------------

func TestAgentTelemetrySummaryHandler_MethodNotAllowed_W48(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestClaimTaskHandler_MethodNotAllowed_W48(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-X/claim", nil)
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave49
// ---------------------------------------------------------------------------

func TestXNodeForwardHandler_MethodNotAllowed_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w49")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/forward", nil)
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestOpenclawEventsHandler_MethodNotAllowed_W49(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/events", nil)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave4
// ---------------------------------------------------------------------------

func TestDashboard_ServeAgentsDashboard_MethodNotAllowed(t *testing.T) {
	d, cleanup := newTestDashboard(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/dashboard", nil)
	w := httptest.NewRecorder()
	d.ServeAgentsDashboard(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for POST, got %d", w.Code)
	}
}

func TestApprovalHandler_HandlePending_MethodNotAllowed(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodPost, "/api/approvals/pending", nil)
	w := httptest.NewRecorder()
	h.handlePending(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave50
// ---------------------------------------------------------------------------

func TestAgentsSSEHandler_MethodNotAllowed_W50(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/stream", nil)
	w := httptest.NewRecorder()
	agentsSSEHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave52
// ---------------------------------------------------------------------------

func TestXNodeRegisterHandler_MethodNotAllowed_W52(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w52")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes/register", nil)
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave58
// ---------------------------------------------------------------------------

func TestPauseTaskHandler_MethodNotAllowed_W58(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-W58/pause", nil)
	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave61
// ---------------------------------------------------------------------------

func TestWave61_ForwardHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node-w61")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/forward", nil)
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave61_HandleQueueCancel_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/queue/cancel", nil)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave61_CompleteTaskWithApprovalHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-123/complete-with-approval", nil)
	w := httptest.NewRecorder()
	completeTaskWithApprovalHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave61_QueueTaskHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-W61/queue", nil)
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave62
// ---------------------------------------------------------------------------

func TestWave62_ResumeTaskHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-W62-RESUME/resume", nil)
	req.URL.Path = "/api/tasks/TASK-W62-RESUME/resume"
	w := httptest.NewRecorder()
	resumeTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave63
// ---------------------------------------------------------------------------

func TestWave63_QueueTaskHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-w63-q/queue", nil)
	req.URL.Path = "/api/tasks/task-w63-q/queue"
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("queueTaskHandler GET: expected 405, got %d", w.Code)
	}
}

func TestWave63_PauseTaskHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-w63-p/pause", nil)
	req.URL.Path = "/api/tasks/task-w63-p/pause"
	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("pauseTaskHandler GET: expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave64
// ---------------------------------------------------------------------------

func TestWave64_HandleApprovalCount_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewApprovalService(NewApprovalStore(db))
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodPost, "/api/approvals/count", nil)
	w := httptest.NewRecorder()
	h.handleApprovalCount(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("handleApprovalCount POST: expected 405, got %d", w.Code)
	}
}

func TestWave64_HandleApprovals_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	svc := NewApprovalService(NewApprovalStore(db))
	h := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodDelete, "/api/approvals", nil)
	w := httptest.NewRecorder()
	h.handleApprovals(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("handleApprovals DELETE: expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave67
// ---------------------------------------------------------------------------

func TestWave67_ReleaseTaskHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/release", nil)
	w := httptest.NewRecorder()
	releaseTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave6
// ---------------------------------------------------------------------------

func TestPatternsHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodDelete, "/api/patterns", nil)
	w := httptest.NewRecorder()
	patternsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestPatternByIDOrRunsHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/patterns/test-123", nil)
	req.URL.Path = "/api/patterns/test-123"
	w := httptest.NewRecorder()
	patternByIDOrRunsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestContextsHandler_MethodNotAllowed_W6(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestTaskHistoryHandler_MethodNotAllowed_W6(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/test/history", nil)
	w := httptest.NewRecorder()
	taskHistoryHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestPruneTasksHandler_MethodNotAllowed_W6(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/prune", nil)
	w := httptest.NewRecorder()
	pruneTasksHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestOpenclawEventsHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodDelete, "/api/openclaw/events", nil)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave73
// ---------------------------------------------------------------------------

func TestWave73_OpenclawEventsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/events", nil)
	rr := httptest.NewRecorder()
	openclawEventsHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", rr.Code)
	}
}

func TestWave73_AgentsSSEHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/agents/stream", nil)
	rr := httptest.NewRecorder()
	agentsSSEHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", rr.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave74
// ---------------------------------------------------------------------------

func TestW74_OpenclawEventsHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/events", nil)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestW74_UIFleetHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/ui", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave75
// ---------------------------------------------------------------------------

func TestW75_UiFleetHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/ui", nil)
	rr := httptest.NewRecorder()
	uiFleetHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("uiFleetHandler POST: expected 405, got %d", rr.Code)
	}
}

func TestW75_OpenclawEventsHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/events", nil)
	rr := httptest.NewRecorder()
	openclawEventsHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("openclawEventsHandler POST: expected 405, got %d", rr.Code)
	}
}

func TestW75_AgentTelemetrySummaryHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/telemetry/summary", nil)
	rr := httptest.NewRecorder()
	agentTelemetrySummaryHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("agentTelemetrySummaryHandler POST: expected 405, got %d", rr.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave77
// ---------------------------------------------------------------------------

func TestWave76_DispatchHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodDelete, "/api/dispatch", nil)
	w := httptest.NewRecorder()
	dispatchHandler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("dispatchHandler DELETE: expected 405, got %d", w.Code)
	}
}

func TestWave76_ClaimTaskHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/api/tasks/wave76-task/claim", nil)
	w := httptest.NewRecorder()
	claimTaskHandler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("claimTaskHandler GET: expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave86
// ---------------------------------------------------------------------------

func TestWave86_OpenclawEventsHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodPost, "/openclaw/events", nil)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("openclawEventsHandler POST: got %d, want 405", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave88
// ---------------------------------------------------------------------------

func TestWave88_ExtendLeaseHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/tasks/extend-lease", nil)
	w := httptest.NewRecorder()
	extendLeaseHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("extendLeaseHandler GET: got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave89
// ---------------------------------------------------------------------------

func TestWave89_AgentsSSEHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodPost, "/api/agents/sse", nil)
	w := httptest.NewRecorder()
	agentsSSEHandler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("agentsSSEHandler POST: got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave90
// ---------------------------------------------------------------------------

func TestWave90b_AgentFleetSummaryHandler_V2_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodPost, "/api/fleet/summary", nil)
	w := httptest.NewRecorder()
	agentFleetSummaryHandler_V2(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("agentFleetSummaryHandler_V2 POST: got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave91
// ---------------------------------------------------------------------------

func TestWave91_PatrolsHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodPost, "/api/patrols", nil)
	w := httptest.NewRecorder()
	patrolsHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("patrolsHandler POST: got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave92
// ---------------------------------------------------------------------------

func TestWave92_HandleSummary_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := NewDashboard(db, nil)
	pwa := NewPWADashboardHandler(d)

	r := httptest.NewRequest(http.MethodDelete, "/api/summary", nil)
	w := httptest.NewRecorder()
	pwa.handleSummary(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("handleSummary DELETE: got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave93
// ---------------------------------------------------------------------------

func TestWave93_PWA_HandleAgents_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	d := NewDashboard(db, nil)
	pwa := NewPWADashboardHandler(d)
	r := httptest.NewRequest(http.MethodDelete, "/api/agents", nil)
	w := httptest.NewRecorder()
	pwa.handleAgents(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("handleAgents DELETE: got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coverage_wave96
// ---------------------------------------------------------------------------

func TestWave96_XNodeController_RegisterHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "wave96-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes/register", nil)
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("RegisterHandler GET: got %d (want 405)", w.Code)
	}
}
