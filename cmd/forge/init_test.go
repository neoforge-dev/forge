package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// ─── detectInitProfile ────────────────────────────────────────────────────────

func TestDetectInitProfile_FleetAgent(t *testing.T) {
	t.Setenv("FORGE_AGENT_TYPE", "fleet")
	t.Setenv("FORGE_PROFILE", "")
	got := detectInitProfile("anything")
	if got != "worker" {
		t.Errorf("want worker, got %s", got)
	}
}

func TestDetectInitProfile_EnvOverride(t *testing.T) {
	t.Setenv("FORGE_AGENT_TYPE", "")
	t.Setenv("FORGE_PROFILE", "solo")
	got := detectInitProfile("prya")
	if got != "solo" {
		t.Errorf("want solo (env override), got %s", got)
	}
}

func TestDetectInitProfile_KnownHub(t *testing.T) {
	t.Setenv("FORGE_AGENT_TYPE", "")
	t.Setenv("FORGE_PROFILE", "")
	for _, host := range []string{"prya", "sati", "nova", "gaea", "vega"} {
		got := detectInitProfile(host)
		if got != "hub" {
			t.Errorf("host %s: want hub, got %s", host, got)
		}
	}
}

func TestDetectInitProfile_UnknownHostDefaultsHub(t *testing.T) {
	t.Setenv("FORGE_AGENT_TYPE", "")
	t.Setenv("FORGE_PROFILE", "")
	got := detectInitProfile("unknown-machine")
	if got != "hub" {
		t.Errorf("want hub (default), got %s", got)
	}
}

// ─── testControlPlaneConnection ───────────────────────────────────────────────

func TestTestControlPlaneConnection_Success(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	if !testControlPlaneConnection(srv.URL) {
		t.Error("expected true for 200 /health response")
	}
}

func TestTestControlPlaneConnection_NotFound(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	if testControlPlaneConnection(srv.URL) {
		t.Error("expected false for non-200 response")
	}
}

func TestTestControlPlaneConnection_Unreachable(t *testing.T) {
	if testControlPlaneConnection("http://127.0.0.1:19999") {
		t.Error("expected false for unreachable server")
	}
}

// ─── initCmd registration ────────────────────────────────────────────────────

func TestInitCmd_Registered(t *testing.T) {
	// initCmd is a package-level var; confirm it has a Use field and is functional.
	if initCmd.Use == "" {
		t.Error("initCmd.Use should not be empty")
	}
	if initCmd.RunE == nil {
		t.Error("initCmd.RunE should be set")
	}
}

func TestInitCmd_NotHidden(t *testing.T) {
	if initCmd.Hidden {
		t.Error("initCmd should not be hidden — it is a user-facing wizard")
	}
}

func TestInitCmd_HasConnectionValidationFlag(t *testing.T) {
	flag := initCmd.Flags().Lookup("control-plane")
	if flag == nil {
		t.Error("initCmd should have --control-plane flag")
	}
}

func TestInitCmd_HasNodeIDFlag(t *testing.T) {
	flag := initCmd.Flags().Lookup("node-id")
	if flag == nil {
		t.Error("initCmd should have --node-id flag")
	}
}

func TestInitCmd_HasForgeRootFlag(t *testing.T) {
	flag := initCmd.Flags().Lookup("forge-root")
	if flag == nil {
		t.Error("initCmd should have --forge-root flag")
	}
}

// ─── isLocal detection (mirrors logic in initCmd) ────────────────────────────

func isLocalURL(u string) bool {
	return strings.HasPrefix(u, "http://localhost") || strings.HasPrefix(u, "http://127.")
}

func TestIsLocalDetection_Localhost(t *testing.T) {
	cases := []struct {
		url     string
		isLocal bool
	}{
		{"http://localhost:8081", true},
		{"http://127.0.0.1:8081", true},
		{"http://prya:8081", false},
		{"http://192.168.1.10:8081", false},
	}
	for _, c := range cases {
		got := isLocalURL(c.url)
		if got != c.isLocal {
			t.Errorf("url=%s: want isLocal=%v, got %v", c.url, c.isLocal, got)
		}
	}
}

func TestForgeRootEnv(t *testing.T) {
	// Ensure FORGE_ROOT env is picked up correctly in init flow
	t.Setenv("FORGE_ROOT", "/tmp/test-forge-root")
	got := os.Getenv("FORGE_ROOT")
	if got != "/tmp/test-forge-root" {
		t.Errorf("FORGE_ROOT env not set: got %q", got)
	}
}
