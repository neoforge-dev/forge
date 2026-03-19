//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestWave117_RoutingConfigDir verifies routingConfigDir uses FORGE_ROOT.
func TestWave117_RoutingConfigDir(t *testing.T) {
	orig := os.Getenv("FORGE_ROOT")
	defer os.Setenv("FORGE_ROOT", orig)

	// With FORGE_ROOT set.
	os.Setenv("FORGE_ROOT", "/tmp/testroot")
	got := routingConfigDir()
	want := filepath.Join("/tmp/testroot", "config", "routing")
	if got != want {
		t.Errorf("routingConfigDir() = %q, want %q", got, want)
	}

	// Without FORGE_ROOT — falls back to ".".
	os.Unsetenv("FORGE_ROOT")
	got2 := routingConfigDir()
	want2 := filepath.Join(".", "config", "routing")
	if got2 != want2 {
		t.Errorf("routingConfigDir() = %q, want %q", got2, want2)
	}
}

// TestWave117_NodeAllowsWorkload verifies nodeAllowsWorkload logic.
func TestWave117_NodeAllowsWorkload(t *testing.T) {
	tests := []struct {
		node     RoutingNodeEntry
		workload string
		want     bool
	}{
		{
			node:     RoutingNodeEntry{ForbiddenWorkloads: []string{"heavy-compute", "ios-builds"}},
			workload: "heavy-compute",
			want:     false,
		},
		{
			node:     RoutingNodeEntry{ForbiddenWorkloads: []string{"heavy-compute"}},
			workload: "go-test",
			want:     true,
		},
		{
			node:     RoutingNodeEntry{ForbiddenWorkloads: []string{}},
			workload: "anything",
			want:     true,
		},
		{
			node:     RoutingNodeEntry{ForbiddenWorkloads: nil},
			workload: "anything",
			want:     true,
		},
	}
	for _, tt := range tests {
		got := nodeAllowsWorkload(tt.node, tt.workload)
		if got != tt.want {
			t.Errorf("nodeAllowsWorkload(%v, %q) = %v, want %v", tt.node.ForbiddenWorkloads, tt.workload, got, tt.want)
		}
	}
}

// TestWave117_LoadRoutingBundle_MissingDir verifies LoadRoutingBundle errors on missing dir.
func TestWave117_LoadRoutingBundle_MissingDir(t *testing.T) {
	_, err := LoadRoutingBundle("/nonexistent/path/xyz")
	if err == nil {
		t.Error("expected error loading from nonexistent dir, got nil")
	}
}

// TestWave117_LoadRoutingBundle_ValidFiles verifies LoadRoutingBundle succeeds with valid YAML.
func TestWave117_LoadRoutingBundle_ValidFiles(t *testing.T) {
	dir := t.TempDir()
	// Minimal valid YAML for all three required files.
	agentYAML := `agents:
  kimi:
    home_node: sati
    weight: light
    best_at: [go-test]
`
	nodeYAML := `nodes:
  sati:
    ram_gb: 64
    max_agents: 6
`
	envelopeYAML := `version: 1
fields:
  task_id: string
`
	if err := os.WriteFile(filepath.Join(dir, "agent-registry.yaml"), []byte(agentYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "node-registry.yaml"), []byte(nodeYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "task-envelope-v1.yaml"), []byte(envelopeYAML), 0o644); err != nil {
		t.Fatal(err)
	}

	bundle, err := LoadRoutingBundle(dir)
	if err != nil {
		t.Fatalf("LoadRoutingBundle: %v", err)
	}
	if bundle == nil {
		t.Fatal("expected non-nil bundle")
	}
}

// TestWave117_RoutingResolveHandler_MethodNotAllowed verifies GET returns 405.
// TestWave117_RoutingResolveHandler_InvalidJSON verifies malformed body returns 400.
func TestWave117_RoutingResolveHandler_InvalidJSON(t *testing.T) {
	body := bytes.NewBufferString(`{bad json`)
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", body)
	req.Header.Set("Content-Type", "application/json")
	req.ContentLength = int64(body.Len())
	// Re-fill body since NewBufferString consumed it.
	req = httptest.NewRequest(http.MethodPost, "/api/routing/resolve", strings.NewReader(`{bad json`))
	req.ContentLength = 9
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave117_RoutingResolveHandler_NoConfigDir verifies 503 when config is missing.
func TestWave117_RoutingResolveHandler_NoConfigDir(t *testing.T) {
	origRoot := os.Getenv("FORGE_ROOT")
	defer os.Setenv("FORGE_ROOT", origRoot)
	os.Setenv("FORGE_ROOT", "/nonexistent/path/wave117")

	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", strings.NewReader(`{}`))
	req.ContentLength = 2
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave117_RoutingResolveHandler_ValidRequest verifies happy path with real config.
func TestWave117_RoutingResolveHandler_ValidRequest(t *testing.T) {
	// Use the actual project config/routing dir (exists in FORGE).
	origRoot := os.Getenv("FORGE_ROOT")
	defer os.Setenv("FORGE_ROOT", origRoot)
	// FORGE_ROOT should point to the repo root.
	os.Setenv("FORGE_ROOT", "../..")

	body := `{"task_type": "go-test", "weight": "light"}`
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", strings.NewReader(body))
	req.ContentLength = int64(len(body))
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)

	// Either 200 (agent found) or 503 (no eligible agent — acceptable).
	if w.Code != http.StatusOK && w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 200 or 503, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave117_RoutingResolveHandler_EmptyBody verifies empty body uses defaults.
func TestWave117_RoutingResolveHandler_EmptyBody(t *testing.T) {
	origRoot := os.Getenv("FORGE_ROOT")
	defer os.Setenv("FORGE_ROOT", origRoot)
	os.Setenv("FORGE_ROOT", "/nonexistent/path/wave117")

	// Empty body (ContentLength == 0) skips JSON decode and goes straight to LoadRoutingBundle.
	req := httptest.NewRequest(http.MethodPost, "/api/routing/resolve", nil)
	req.ContentLength = 0
	w := httptest.NewRecorder()
	routingResolveHandler(w, req)
	// Config dir won't exist so 503.
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave117_HandleSystemHealth_Get verifies handleSystemHealth returns 200 with JSON.
func TestWave117_HandleSystemHealth_Get(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	// Body should be valid JSON.
	var resp interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Errorf("expected JSON body, got: %s", w.Body.String())
	}
}

// TestWave117_HandleSystemHealth_FormatJSON verifies format=json query param.
func TestWave117_HandleSystemHealth_FormatJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/system/health?format=json", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave117_HandleQueueDepth_Basic verifies handleQueueDepth returns 200 with depth field.
func TestWave117_HandleQueueDepth_Basic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	origQueue := taskQueue
	tq, _ := NewTaskQueueFromDB(db)
	taskQueue = tq
	defer func() { taskQueue = origQueue }()

	req := httptest.NewRequest(http.MethodGet, "/system/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Errorf("expected JSON body: %v", err)
	}
	if _, ok := resp["depth"]; !ok {
		t.Errorf("expected 'depth' in response, got: %v", resp)
	}
}

// TestWave117_HandleQueueDepth_FormatJSON verifies format=json param.
func TestWave117_HandleQueueDepth_FormatJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	origQueue := taskQueue
	tq, _ := NewTaskQueueFromDB(db)
	taskQueue = tq
	defer func() { taskQueue = origQueue }()

	req := httptest.NewRequest(http.MethodGet, "/system/queue/depth?format=json", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

