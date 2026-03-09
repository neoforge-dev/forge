//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// TestDetectTailscaleIP_ADR038 verifies detectTailscaleIP returns a string
// (empty or IP) without panicking. Tailscale may not be available in CI.
func TestDetectTailscaleIP_ADR038(t *testing.T) {
	ip := detectTailscaleIP()
	// Must be either empty or a valid-looking IPv4 (no whitespace, no port).
	if ip != "" {
		if strings.ContainsAny(ip, " \t\n") {
			t.Errorf("detectTailscaleIP returned IP with whitespace: %q", ip)
		}
		if strings.Contains(ip, ":") {
			t.Errorf("detectTailscaleIP should return bare IP, not IP:port; got %q", ip)
		}
	}
}

// TestNodeRegistration_AddressFormat_ADR038 verifies that when a node registers
// via POST /api/xnode/nodes/register the address is stored exactly as provided.
// The fix ensures the daemon uses detectTailscaleIP() + ":port" format.
func TestNodeRegistration_AddressFormat_ADR038(t *testing.T) {
	db, _ := setupClaimTestDB(t)
	xc, err := NewXNodeController(db, "prya")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	wantAddr := "100.80.39.128:8081"
	node := map[string]interface{}{
		"id":             "sati",
		"hostname":       "sati",
		"address":        wantAddr,
		"status":         "online",
		"last_heartbeat": time.Now().Format(time.RFC3339),
	}
	body, _ := json.Marshal(node)

	req := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, req)

	if w.Code != http.StatusOK && w.Code != http.StatusCreated {
		t.Fatalf("RegisterHandler: expected 200/201, got %d %s", w.Code, w.Body.String())
	}

	// Verify the address is stored with the port included.
	var storedAddr string
	err = db.QueryRow("SELECT address FROM nodes WHERE id = ?", "sati").Scan(&storedAddr)
	if err != nil {
		t.Fatalf("query stored address: %v", err)
	}
	if storedAddr != wantAddr {
		t.Errorf("stored address = %q, want %q", storedAddr, wantAddr)
	}
}

// TestHeartbeatFallback_UsesPort_ADR038 verifies that the Heartbeat() fallback
// (when NODE_ADDR is unset) produces an address with a port suffix, not just
// a bare hostname or IP. This ensures format is "ip:port" not just "ip".
func TestHeartbeatFallback_UsesPort_ADR038(t *testing.T) {
	db, _ := setupClaimTestDB(t)
	xc, err := NewXNodeController(db, "prya")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Ensure NODE_ADDR is unset so the fallback path runs.
	old := os.Getenv("NODE_ADDR")
	os.Unsetenv("NODE_ADDR")
	defer func() {
		if old != "" {
			os.Setenv("NODE_ADDR", old)
		}
	}()

	// Heartbeat for a node not yet in the DB — triggers the fallback insert.
	if err := xc.Heartbeat(t.Context(), "newnode"); err != nil {
		t.Fatalf("Heartbeat: %v", err)
	}

	var addr string
	err = db.QueryRow("SELECT address FROM nodes WHERE id = ?", "newnode").Scan(&addr)
	if err != nil {
		t.Fatalf("query node address: %v", err)
	}
	// Address must contain a colon (ip:port or hostname:port format).
	if !strings.Contains(addr, ":") {
		t.Errorf("Heartbeat fallback stored address without port: %q (want ip:port)", addr)
	}
	t.Logf("Heartbeat fallback address: %q", addr)
}

// TestWorkDaemonRegistersNode_ADR038 is a smoke test verifying the
// /api/xnode/nodes/register endpoint accepts a node registration payload
// in the format that forge work --daemon now sends.
func TestWorkDaemonRegistersNode_ADR038(t *testing.T) {
	db, _ := setupClaimTestDB(t)
	xc, err := NewXNodeController(db, "prya")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	// Simulate what runWorkDaemon() now sends on startup.
	payload := map[string]interface{}{
		"id":             "work-agent",
		"hostname":       "work-agent",
		"address":        "100.80.39.200:8081",
		"status":         "online",
		"last_heartbeat": time.Now().Format(time.RFC3339),
	}
	body, _ := json.Marshal(payload)

	req := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, req)

	if w.Code != http.StatusOK && w.Code != http.StatusCreated {
		t.Fatalf("expected 200 or 201, got %d %s", w.Code, w.Body.String())
	}

	// Verify node is now in the mesh.
	var status, addr string
	err = db.QueryRow("SELECT status, address FROM nodes WHERE id = ?", "work-agent").Scan(&status, &addr)
	if err != nil {
		t.Fatalf("query registered node: %v", err)
	}
	if status != "online" {
		t.Errorf("status = %q, want 'online'", status)
	}
	if !strings.HasSuffix(addr, ":8081") {
		t.Errorf("address %q missing :8081 port", addr)
	}
}
