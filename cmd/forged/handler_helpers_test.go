//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// TestWriteJSON tests writeJSON sets correct Content-Type and encodes JSON
func TestWriteJSON(t *testing.T) {
	w := httptest.NewRecorder()

	type TestData struct {
		Name string `json:"name"`
		Value int   `json:"value"`
	}

	writeJSON(w, TestData{Name: "test", Value: 42})

	if w.Header().Get("Content-Type") != "application/json" {
		t.Errorf("Content-Type = %q, want %q", w.Header().Get("Content-Type"), "application/json")
	}

	body := w.Body.String()
	if !strings.Contains(body, `"name":"test"`) || !strings.Contains(body, `"value":42`) {
		t.Errorf("Body = %q, expected JSON with name and value", body)
	}
}

// TestRequireMethod tests requireMethod validates HTTP methods correctly
func TestRequireMethod(t *testing.T) {
	// Test POST request with POST required - should pass
	req := httptest.NewRequest(http.MethodPost, "/test", nil)
	w := httptest.NewRecorder()

	result := requireMethod(w, req, http.MethodPost)
	if result != true {
		t.Error("requireMethod returned false for correct POST method")
	}

	// Test GET request with POST required - should fail with 405
	req = httptest.NewRequest(http.MethodGet, "/test", nil)
	w = httptest.NewRecorder()

	result = requireMethod(w, req, http.MethodPost)
	if result != false {
		t.Error("requireMethod returned true for incorrect GET method")
	}

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("StatusCode = %d, want %d", w.Code, http.StatusMethodNotAllowed)
	}
}

// TestWithDBNil tests withDB returns 503 when DB is unavailable
// Note: Cannot mock getDBConn directly (not addressable). This test verifies
// the function exists and has correct signature.
func TestWithDBNilSignature(t *testing.T) {
	// Verify withDB is a function with correct signature
	// The actual behavior is tested via integration tests
	_ = withDB
}
