// coverage_wave_doctor_test.go - Coverage wave for doctor.go
package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/neoforge-dev/forge/internal"
)

func TestDoctorResultSymbol(t *testing.T) {
	tests := []struct {
		name   string
		status string
		want   string
	}{
		{"ok", "ok", "✓"},
		{"error", "error", "✗"},
		{"warning", "warning", "⚠"},
		{"skip", "skip", "–"},
		{"unknown", "unknown", "–"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := doctorResult{Status: tt.status}
			got := r.symbol()
			if !strings.Contains(got, tt.want) && tt.status != "unknown" {
				t.Errorf("symbol() = %q, want to contain %q", got, tt.want)
			}
		})
	}
}

func TestCheckDaemon(t *testing.T) {
	tests := []struct {
		name       string
		statusCode int
		response   interface{}
		wantStatus string
	}{
		{
			name:       "healthy daemon",
			statusCode: http.StatusOK,
			response:   map[string]string{"status": "ok", "version": "4.0.0", "uptime": "1h30m"},
			wantStatus: "ok",
		},
		{
			name:       "daemon returns error",
			statusCode: http.StatusInternalServerError,
			response:   map[string]string{"error": "internal"},
			wantStatus: "error",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var handler http.HandlerFunc
			if tt.response != nil {
				body, _ := json.Marshal(tt.response)
				handler = func(w http.ResponseWriter, r *http.Request) {
					w.WriteHeader(tt.statusCode)
					w.Write(body)
				}
			} else {
				handler = func(w http.ResponseWriter, r *http.Request) {
					w.WriteHeader(tt.statusCode)
				}
			}

			server := httptest.NewServer(handler)
			defer server.Close()

			// Temporarily override the client URL
			oldURL := internal.ResolveControlPlaneURL()
			_ = oldURL

			result := checkDaemon(server.URL)
			if result.Status != tt.wantStatus {
				t.Errorf("checkDaemon() status = %q, want %q", result.Status, tt.wantStatus)
			}
		})
	}

	t.Run("daemon not reachable", func(t *testing.T) {
		result := checkDaemon("http://localhost:99999")
		if result.Status != "error" {
			t.Errorf("checkDaemon() with bad URL status = %q, want error", result.Status)
		}
	})
}

func TestCheckSQLite(t *testing.T) {
	t.Run("missing database", func(t *testing.T) {
		// Set a temporary FORGE_ROOT
		oldRoot := os.Getenv("FORGE_ROOT")
		tempDir := t.TempDir()
		os.Setenv("FORGE_ROOT", tempDir)
		defer os.Setenv("FORGE_ROOT", oldRoot)

		result := checkSQLite()
		if result.Status != "warning" {
			t.Errorf("checkSQLite() with missing DB status = %q, want warning", result.Status)
		}
	})

	t.Run("valid sqlite database", func(t *testing.T) {
		tempDir := t.TempDir()
		oldRoot := os.Getenv("FORGE_ROOT")
		os.Setenv("FORGE_ROOT", tempDir)
		defer os.Setenv("FORGE_ROOT", oldRoot)

		// Create a valid SQLite database file
		dbPath := filepath.Join(tempDir, ".forge", "forge-v3.db")
		os.MkdirAll(filepath.Dir(dbPath), 0755)
		
		// Write SQLite magic header
		f, err := os.Create(dbPath)
		if err != nil {
			t.Fatal(err)
		}
		f.Write([]byte("SQLite format 3\x00"))
		f.Write(make([]byte, 1024)) // Pad with some bytes
		f.Close()

		result := checkSQLite()
		if result.Status != "ok" {
			t.Errorf("checkSQLite() with valid DB status = %q, want ok", result.Status)
		}
	})

	t.Run("corrupt sqlite database", func(t *testing.T) {
		tempDir := t.TempDir()
		oldRoot := os.Getenv("FORGE_ROOT")
		os.Setenv("FORGE_ROOT", tempDir)
		defer os.Setenv("FORGE_ROOT", oldRoot)

		// Create an invalid database file
		dbPath := filepath.Join(tempDir, ".forge", "forge-v3.db")
		os.MkdirAll(filepath.Dir(dbPath), 0755)
		os.WriteFile(dbPath, []byte("not a sqlite database"), 0644)

		result := checkSQLite()
		if result.Status != "error" {
			t.Errorf("checkSQLite() with corrupt DB status = %q, want error", result.Status)
		}
	})
}

func TestCheckGitLock(t *testing.T) {
	t.Run("no lock file", func(t *testing.T) {
		tempDir := t.TempDir()
		oldRoot := os.Getenv("FORGE_ROOT")
		os.Setenv("FORGE_ROOT", tempDir)
		defer os.Setenv("FORGE_ROOT", oldRoot)

		// Create .git directory but no lock
		os.MkdirAll(filepath.Join(tempDir, ".git"), 0755)

		result := checkGitLock()
		if result.Status != "ok" {
			t.Errorf("checkGitLock() without lock status = %q, want ok", result.Status)
		}
	})

	t.Run("with lock file", func(t *testing.T) {
		tempDir := t.TempDir()
		oldRoot := os.Getenv("FORGE_ROOT")
		os.Setenv("FORGE_ROOT", tempDir)
		defer os.Setenv("FORGE_ROOT", oldRoot)

		// Create lock file
		lockPath := filepath.Join(tempDir, ".git", "index.lock")
		os.MkdirAll(filepath.Dir(lockPath), 0755)
		os.WriteFile(lockPath, []byte(""), 0644)

		result := checkGitLock()
		if result.Status != "error" {
			t.Errorf("checkGitLock() with lock status = %q, want error", result.Status)
		}
	})
}

func TestCheckForgeStructure(t *testing.T) {
	t.Run("missing directories", func(t *testing.T) {
		tempDir := t.TempDir()
		oldRoot := os.Getenv("FORGE_ROOT")
		os.Setenv("FORGE_ROOT", tempDir)
		defer os.Setenv("FORGE_ROOT", oldRoot)

		result := checkForgeStructure()
		if result.Status != "warning" {
			t.Errorf("checkForgeStructure() with missing dirs status = %q, want warning", result.Status)
		}
	})

	t.Run("all directories present", func(t *testing.T) {
		tempDir := t.TempDir()
		oldRoot := os.Getenv("FORGE_ROOT")
		os.Setenv("FORGE_ROOT", tempDir)
		defer os.Setenv("FORGE_ROOT", oldRoot)

		// Create required directories
		os.MkdirAll(filepath.Join(tempDir, ".forge", "dispatches"), 0755)
		os.MkdirAll(filepath.Join(tempDir, ".forge", "heartbeat", "results"), 0755)
		os.MkdirAll(filepath.Join(tempDir, ".forge", "context"), 0755)

		result := checkForgeStructure()
		if result.Status != "ok" {
			t.Errorf("checkForgeStructure() with all dirs status = %q, want ok", result.Status)
		}
	})
}

func TestCheckFleet(t *testing.T) {
	t.Run("no agents", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			json.NewEncoder(w).Encode(map[string]interface{}{
				"agents": []interface{}{},
			})
		}))
		defer server.Close()

		result := checkFleet(server.URL)
		if result.Status != "warning" {
			t.Errorf("checkFleet() with no agents status = %q, want warning", result.Status)
		}
	})

	t.Run("with online agents", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			json.NewEncoder(w).Encode(map[string]interface{}{
				"agents": []map[string]string{
					{"id": "agent1", "status": "online"},
					{"id": "agent2", "status": "offline"},
				},
			})
		}))
		defer server.Close()

		result := checkFleet(server.URL)
		if result.Status != "ok" {
			t.Errorf("checkFleet() with agents status = %q, want ok", result.Status)
		}
		if !strings.Contains(result.Detail, "1/2") {
			t.Errorf("checkFleet() detail = %q, want to contain '1/2'", result.Detail)
		}
	})

	t.Run("server error", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusInternalServerError)
		}))
		defer server.Close()

		result := checkFleet(server.URL)
		if result.Status != "warning" {
			t.Errorf("checkFleet() with server error status = %q, want warning", result.Status)
		}
	})
}

func TestCheckTokenBudget(t *testing.T) {
	t.Run("endpoint not found", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusNotFound)
		}))
		defer server.Close()

		result := checkTokenBudget(server.URL)
		if result.Status != "skip" {
			t.Errorf("checkTokenBudget() with 404 status = %q, want skip", result.Status)
		}
	})

	t.Run("low budget usage", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			json.NewEncoder(w).Encode(map[string]interface{}{
				"used":      30,
				"total":     100,
				"remaining": 70,
				"pct":       30.0,
			})
		}))
		defer server.Close()

		result := checkTokenBudget(server.URL)
		if result.Status != "ok" {
			t.Errorf("checkTokenBudget() with low usage status = %q, want ok", result.Status)
		}
	})

	t.Run("high budget usage", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			json.NewEncoder(w).Encode(map[string]interface{}{
				"used":      80,
				"total":     100,
				"remaining": 20,
				"pct":       80.0,
			})
		}))
		defer server.Close()

		result := checkTokenBudget(server.URL)
		if result.Status != "warning" {
			t.Errorf("checkTokenBudget() with high usage status = %q, want warning", result.Status)
		}
	})

	t.Run("server unreachable", func(t *testing.T) {
		result := checkTokenBudget("http://localhost:99999")
		if result.Status != "skip" {
			t.Errorf("checkTokenBudget() with unreachable server status = %q, want skip", result.Status)
		}
	})
}
