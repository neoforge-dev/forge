//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestPushRegistry_Register(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	registry := NewPushRegistry(db)
	ctx := context.Background()

	device := PushDevice{
		Token:    "test-token-001",
		Platform: "watchos",
		UserID:   "user-1",
	}
	if err := registry.Register(ctx, device); err != nil {
		t.Fatalf("Register: %v", err)
	}

	devices, err := registry.ListByPlatform(ctx, "watchos")
	if err != nil {
		t.Fatalf("ListByPlatform: %v", err)
	}
	if len(devices) == 0 {
		t.Fatal("expected at least 1 device after register")
	}
	found := false
	for _, d := range devices {
		if d.Token == "test-token-001" {
			found = true
			break
		}
	}
	if !found {
		t.Error("registered token not found in ListByPlatform result")
	}
}

func TestPushRegistry_Register_InvalidPlatform(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	registry := NewPushRegistry(db)
	err := registry.Register(context.Background(), PushDevice{
		Token:    "tok",
		Platform: "android",
		UserID:   "user-1",
	})
	if err == nil {
		t.Error("expected error for invalid platform")
	}
}

func TestPushRegistry_Register_MissingToken(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	registry := NewPushRegistry(db)
	err := registry.Register(context.Background(), PushDevice{
		Token:    "",
		Platform: "ios",
		UserID:   "user-1",
	})
	if err == nil {
		t.Error("expected error for missing token")
	}
}

func TestPushRegistry_Unregister(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	registry := NewPushRegistry(db)
	ctx := context.Background()

	device := PushDevice{Token: "unregister-tok", Platform: "ios", UserID: "u1"}
	if err := registry.Register(ctx, device); err != nil {
		t.Fatalf("Register: %v", err)
	}
	if err := registry.Unregister(ctx, "unregister-tok"); err != nil {
		t.Fatalf("Unregister: %v", err)
	}

	// Should be gone
	devices, _ := registry.ListByPlatform(ctx, "ios")
	for _, d := range devices {
		if d.Token == "unregister-tok" {
			t.Error("token should be removed after Unregister")
		}
	}
}

func TestPushRegistry_Unregister_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	registry := NewPushRegistry(db)
	err := registry.Unregister(context.Background(), "nonexistent-token")
	if err == nil {
		t.Error("expected error when unregistering nonexistent token")
	}
}

func TestPushRegistry_ListByPlatform(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	registry := NewPushRegistry(db)
	ctx := context.Background()

	if err := registry.Register(ctx, PushDevice{Token: "ios-1", Platform: "ios", UserID: "u1"}); err != nil {
		t.Fatalf("Register ios: %v", err)
	}
	if err := registry.Register(ctx, PushDevice{Token: "watch-1", Platform: "watchos", UserID: "u1"}); err != nil {
		t.Fatalf("Register watchos: %v", err)
	}

	ios, err := registry.ListByPlatform(ctx, "ios")
	if err != nil {
		t.Fatalf("ListByPlatform ios: %v", err)
	}
	for _, d := range ios {
		if d.Platform != "ios" {
			t.Errorf("expected ios platform, got %s", d.Platform)
		}
	}

	watch, err := registry.ListByPlatform(ctx, "watchos")
	if err != nil {
		t.Fatalf("ListByPlatform watchos: %v", err)
	}
	for _, d := range watch {
		if d.Platform != "watchos" {
			t.Errorf("expected watchos platform, got %s", d.Platform)
		}
	}
	_ = watch
}

func TestHandlePushRegister_POST(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := []byte(`{"token":"http-tok-001","platform":"watchos","user_id":"user-http"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/push/register", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handlePushRegister(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]string
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp["status"] != "registered" {
		t.Errorf("status = %q, want registered", resp["status"])
	}
}

func TestHandlePushRegister_InvalidMethod(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/push/register", nil)
	w := httptest.NewRecorder()
	handlePushRegister(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestHandlePushDevices_GET(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// First register a device
	body := []byte(`{"token":"dev-list-tok","platform":"ios","user_id":"list-user"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/push/register", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handlePushRegister(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("register setup failed: %d %s", w.Code, w.Body.String())
	}

	// Now list
	req2 := httptest.NewRequest(http.MethodGet, "/api/push/devices", nil)
	w2 := httptest.NewRecorder()
	handlePushDevices(w2, req2)

	if w2.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w2.Code, w2.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w2.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, ok := resp["devices"]; !ok {
		t.Error("response missing 'devices' key")
	}
	if _, ok := resp["count"]; !ok {
		t.Error("response missing 'count' key")
	}
}

func TestHandlePushDevices_FilterByPlatform(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Register both platforms
	for _, tok := range []struct{ token, platform string }{
		{"plat-ios-1", "ios"},
		{"plat-watch-1", "watchos"},
	} {
		body, _ := json.Marshal(map[string]string{"token": tok.token, "platform": tok.platform, "user_id": "u"})
		req := httptest.NewRequest(http.MethodPost, "/api/push/register", bytes.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		handlePushRegister(w, req)
		if w.Code != http.StatusOK {
			t.Fatalf("register %s: %d %s", tok.token, w.Code, w.Body.String())
		}
	}

	req := httptest.NewRequest(http.MethodGet, "/api/push/devices?platform=watchos", nil)
	w := httptest.NewRecorder()
	handlePushDevices(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp struct {
		Devices []PushDevice `json:"devices"`
		Count   int          `json:"count"`
	}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	for _, d := range resp.Devices {
		if d.Platform != "watchos" {
			t.Errorf("got platform %q, expected watchos only", d.Platform)
		}
	}
}

func TestPushRegisterHandler_Dispatch(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// POST → register
	body := []byte(`{"token":"dispatch-tok","platform":"ios","user_id":"u"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/push/register", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	pushRegisterHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("POST: expected 200, got %d: %s", w.Code, w.Body.String())
	}

	// DELETE → unregister
	req2 := httptest.NewRequest(http.MethodDelete, "/api/push/register?token=dispatch-tok", nil)
	w2 := httptest.NewRecorder()
	pushRegisterHandler(w2, req2)
	if w2.Code != http.StatusOK {
		t.Errorf("DELETE: expected 200, got %d: %s", w2.Code, w2.Body.String())
	}

	// PUT → method not allowed
	req3 := httptest.NewRequest(http.MethodPut, "/api/push/register", nil)
	w3 := httptest.NewRecorder()
	pushRegisterHandler(w3, req3)
	if w3.Code != http.StatusMethodNotAllowed {
		t.Errorf("PUT: expected 405, got %d", w3.Code)
	}
}
