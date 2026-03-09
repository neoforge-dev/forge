package internal

import (
	"os"
	"path/filepath"
	"testing"
)

func TestResolveControlPlaneURL(t *testing.T) {
	t.Run("env wins over config", func(t *testing.T) {
		tempHome := t.TempDir()
		writeConfigFile(t, filepath.Join(tempHome, ".forge", "config.toml"), `[control_plane]
url = "http://config:8081"
`)

		t.Setenv("HOME", tempHome)
		t.Setenv("FORGE_API_URL", "http://env:8081/api")
		t.Chdir(t.TempDir())

		if got := ResolveControlPlaneURL(); got != "http://env:8081" {
			t.Fatalf("expected env URL, got %q", got)
		}
		if got := ResolveAPIBaseURL(); got != "http://env:8081/api" {
			t.Fatalf("expected env API URL, got %q", got)
		}
	})

	t.Run("user config is used when env is absent", func(t *testing.T) {
		tempHome := t.TempDir()
		writeConfigFile(t, filepath.Join(tempHome, ".forge", "config.toml"), `[control_plane]
url = "http://prya-config:8081"
`)

		t.Setenv("HOME", tempHome)
		t.Setenv("FORGE_API_URL", "")
		t.Chdir(t.TempDir())

		if got := ResolveControlPlaneURL(); got != "http://prya-config:8081" {
			t.Fatalf("expected config URL, got %q", got)
		}
	})

	t.Run("project yaml is used as fallback", func(t *testing.T) {
		tempHome := t.TempDir()
		projectDir := t.TempDir()
		writeConfigFile(t, filepath.Join(projectDir, ".forge", "forge.yaml"), "api_url: http://project:8081\n")

		t.Setenv("HOME", tempHome)
		if err := os.Unsetenv("FORGE_API_URL"); err != nil {
			t.Fatalf("unset FORGE_API_URL: %v", err)
		}
		t.Chdir(projectDir)

		if got := ResolveControlPlaneURL(); got != "http://project:8081" {
			t.Fatalf("expected project URL, got %q", got)
		}
	})

	t.Run("default is hub on prya", func(t *testing.T) {
		tempHome := t.TempDir()
		t.Setenv("HOME", tempHome)
		if err := os.Unsetenv("FORGE_API_URL"); err != nil {
			t.Fatalf("unset FORGE_API_URL: %v", err)
		}
		t.Chdir(t.TempDir())

		if got := ResolveControlPlaneURL(); got != DefaultControlPlaneURL {
			t.Fatalf("expected default control plane %q, got %q", DefaultControlPlaneURL, got)
		}
	})
}

func writeConfigFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		t.Fatalf("mkdir %s: %v", filepath.Dir(path), err)
	}
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		t.Fatalf("write %s: %v", path, err)
	}
}
