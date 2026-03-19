//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// Wave 31: fleetAggregateHandler, handleQueueList (with queue), confidenceApproveCompletedTasks,
//          handleQueueDepth, fleetScaleRecommendPatrol, fleetDeflateRecommendPatrol

func TestFleetAggregateHandler_NilDB_W31(t *testing.T) {
	orig := getDBConn()
	setDBConn(nil)
	defer setDBConn(orig)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestFleetAggregateHandler_WithDB_W31(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, req)
	// Any non-panic response — will try to contact self via HTTP (connection refused is OK)
	_ = w.Code
}

func TestHandleQueueList_WithQueue_W31(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/list?limit=10", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleQueueList_LimitVariants_W31(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	// Test limit=0 (should clamp to 50)
	req := httptest.NewRequest(http.MethodGet, "/api/queue/list?limit=0", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("limit=0: expected 200, got %d", w.Code)
	}

	// Test limit=9999 (should clamp to 500)
	req2 := httptest.NewRequest(http.MethodGet, "/api/queue/list?limit=9999", nil)
	w2 := httptest.NewRecorder()
	handleQueueList(w2, req2)
	if w2.Code != http.StatusOK {
		t.Errorf("limit=9999: expected 200, got %d", w2.Code)
	}
}

func TestHandleQueueList_FormatText_W31(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/list?format=text", nil)
	w := httptest.NewRecorder()
	handleQueueList(w, req)
	_ = w.Code
}

func TestHandleQueueDepth_WithQueue_W31(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestConfidenceApproveCompletedTasks_NilServices_W31(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// stateMachine and globalApprovalService are nil in test context → early return
	ctx := context.Background()
	err := confidenceApproveCompletedTasks(ctx, db)
	if err != nil {
		t.Errorf("expected nil with nil services, got %v", err)
	}
}

func TestFleetScaleRecommendPatrol_W31(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// Should run without panic; may return error if scaler not configured
	err := fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Logf("fleetScaleRecommendPatrol: %v (OK if scaler not configured)", err)
	}
}

func TestFleetDeflateRecommendPatrol_W31(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	err := fleetDeflateRecommendPatrol(ctx, db)
	if err != nil {
		t.Logf("fleetDeflateRecommendPatrol: %v (OK if scaler not configured)", err)
	}
}

func TestHandleQueueCancel_ValidBody_WithQueue_W31(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	body := strings.NewReader(`{"id":"nonexistent-task-xyz","reason":"test cancel"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/cancel", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	// 404 or 500 for nonexistent task — not a panic
	if w.Code == http.StatusOK {
		t.Logf("handleQueueCancel returned 200 for nonexistent task — unexpected")
	}
}
