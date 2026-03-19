//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 191: handlers_approval.go + handlers_lane.go + handlers_context.go
// Targets:
//   notificationsHandler     (handlers_approval.go:15) — GET / POST / invalid body / 405
//   notificationActionHandler(handlers_approval.go:68) — POST mark-read / empty ID / not found / 405
//   lanesHandler             (handlers_lane.go:15)     — GET with DB / non-GET 405
//   laneByIDHandler          (handlers_lane.go:70)     — non-GET 405 / empty ID / not found
//   contextsHandler          (handlers_context.go:14)  — non-GET 405 / GET with DB
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// notificationsHandler
// ---------------------------------------------------------------------------

func TestWave191_NotificationsHandler_Get(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/notifications", nil)
	w := httptest.NewRecorder()
	notificationsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

func TestWave191_NotificationsHandler_Post(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/notifications",
		strings.NewReader(`{"type":"info","title":"Test","message":"hello"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	notificationsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for POST, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave191_NotificationsHandler_Post_InvalidBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/notifications",
		strings.NewReader("not-json"))
	w := httptest.NewRecorder()
	notificationsHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid body, got %d", w.Code)
	}
}

func TestWave191_NotificationsHandler_Delete405(t *testing.T) {
	req := httptest.NewRequest(http.MethodDelete, "/api/notifications", nil)
	w := httptest.NewRecorder()
	notificationsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// notificationActionHandler
// ---------------------------------------------------------------------------

func TestWave191_NotificationActionHandler_MarkRead(t *testing.T) {
	// First create a notification
	postReq := httptest.NewRequest(http.MethodPost, "/api/notifications",
		strings.NewReader(`{"type":"alert","title":"Ack","message":"msg"}`))
	postReq.Header.Set("Content-Type", "application/json")
	postW := httptest.NewRecorder()
	notificationsHandler(postW, postReq)

	// Extract created ID from response
	body := postW.Body.String()
	// body contains {"status":"created","notification":{...,"id":"notif-N",...}}
	// parse the id simply
	idx := strings.Index(body, `"id":"`)
	if idx < 0 {
		t.Fatalf("could not find id in response: %s", body)
	}
	idStart := idx + len(`"id":"`)
	idEnd := strings.Index(body[idStart:], `"`)
	notifID := body[idStart : idStart+idEnd]

	req := httptest.NewRequest(http.MethodPost, "/api/notifications/"+notifID+"/read", nil)
	w := httptest.NewRecorder()
	notificationActionHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for mark-read, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave191_NotificationActionHandler_NotFound(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/notifications/nonexistent/read", nil)
	w := httptest.NewRecorder()
	notificationActionHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestWave191_NotificationActionHandler_WrongMethod(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/notifications/notif-1/read", nil)
	w := httptest.NewRecorder()
	notificationActionHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// lanesHandler
// ---------------------------------------------------------------------------

func TestWave191_LanesHandler_GetWithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/lanes", nil)
	w := httptest.NewRecorder()
	lanesHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave191_LanesHandler_Post405(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/lanes", nil)
	w := httptest.NewRecorder()
	lanesHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// laneByIDHandler
// ---------------------------------------------------------------------------

func TestWave191_LaneByIDHandler_Post405(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/lanes/lane-1", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave191_LaneByIDHandler_EmptyID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/lanes/", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty ID, got %d", w.Code)
	}
}

func TestWave191_LaneByIDHandler_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/lanes/nonexistent-lane", nil)
	w := httptest.NewRecorder()
	laneByIDHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// contextsHandler
// ---------------------------------------------------------------------------

func TestWave191_ContextsHandler_Post405(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave191_ContextsHandler_GetWithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave191_ContextsHandler_GetWithFilters(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/contexts?agent_id=kimi-1&domain=forge&project=test&task_id=t1", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with filters, got %d: %s", w.Code, w.Body.String())
	}
}
