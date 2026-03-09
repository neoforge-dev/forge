package middleware

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestRateLimiter_NewRateLimiter(t *testing.T) {
	tests := []struct {
		name     string
		cfg      *RateLimitConfig
		wantLimit int
		wantWindow time.Duration
	}{
		{
			name:       "default values",
			cfg:        &RateLimitConfig{},
			wantLimit:  100,
			wantWindow: time.Minute,
		},
		{
			name: "custom values",
			cfg: &RateLimitConfig{
				DefaultLimit: 200,
				WindowSize:   5 * time.Minute,
			},
			wantLimit:  200,
			wantWindow: 5 * time.Minute,
		},
		{
			name: "zero values get defaults",
			cfg: &RateLimitConfig{
				DefaultLimit: 0,
				WindowSize:   0,
			},
			wantLimit:  100,
			wantWindow: time.Minute,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rl := NewRateLimiter(tt.cfg)
			if rl.defaultLimit != tt.wantLimit {
				t.Errorf("defaultLimit = %d, want %d", rl.defaultLimit, tt.wantLimit)
			}
			if rl.windowSize != tt.wantWindow {
				t.Errorf("windowSize = %v, want %v", rl.windowSize, tt.wantWindow)
			}
		})
	}
}

func TestRateLimiter_getClientIdentifier(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		TokenHeader: "X-API-Token",
	})

	tests := []struct {
		name       string
		remoteAddr string
		headers    map[string]string
		wantPrefix string
	}{
		{
			name:       "IP address fallback",
			remoteAddr: "192.168.1.1:12345",
			headers:    map[string]string{},
			wantPrefix: "ip:",
		},
		{
			name:       "API token takes priority",
			remoteAddr: "192.168.1.1:12345",
			headers:    map[string]string{"X-API-Token": "my-token-123"},
			wantPrefix: "token:",
		},
		{
			name:       "X-Forwarded-For header",
			remoteAddr: "10.0.0.1:12345",
			headers:    map[string]string{"X-Forwarded-For": "203.0.113.1, 10.0.0.1"},
			wantPrefix: "ip:",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest("GET", "/test", nil)
			req.RemoteAddr = tt.remoteAddr
			for k, v := range tt.headers {
				req.Header.Set(k, v)
			}

			got := rl.getClientIdentifier(req)
			if !strings.HasPrefix(got, tt.wantPrefix) {
				t.Errorf("getClientIdentifier() = %q, want prefix %q", got, tt.wantPrefix)
			}
		})
	}
}

func TestRateLimiter_shouldSkip(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		SkipPaths: []string{"/health", "/api/health"},
	})

	tests := []struct {
		path string
		want bool
	}{
		{"/health", true},
		{"/api/health", true},
		{"/api/health/detailed", true},
		{"/api/status", false},
		{"/api/tasks", false},
		{"/healthcheck", false}, // Not in skip list
	}

	for _, tt := range tests {
		t.Run(tt.path, func(t *testing.T) {
			if got := rl.shouldSkip(tt.path); got != tt.want {
				t.Errorf("shouldSkip(%q) = %v, want %v", tt.path, got, tt.want)
			}
		})
	}
}

func TestRateLimiter_getLimitForPath(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit: 100,
		EndpointLimits: map[string]int{
			"/api/status":    200,
			"/metrics":       60,
			"/api/unlimited": 0, // Use default
		},
	})

	tests := []struct {
		path string
		want int
	}{
		{"/api/status", 200},
		{"/metrics", 60},
		{"/api/unlimited", 100}, // Falls back to default
		{"/api/unknown", 100},   // Uses default
		{"/api/status/detailed", 200}, // Prefix match
	}

	for _, tt := range tests {
		t.Run(tt.path, func(t *testing.T) {
			if got := rl.getLimitForPath(tt.path); got != tt.want {
				t.Errorf("getLimitForPath(%q) = %d, want %d", tt.path, got, tt.want)
			}
		})
	}
}

func TestRateLimiter_isAllowed(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit: 5,
		WindowSize:   time.Minute,
	})

	clientID := "ip:192.168.1.1"
	path := "/api/test"

	// Make requests up to the limit
	for i := 1; i <= 5; i++ {
		allowed, hits, limit, _ := rl.isAllowed(clientID, path)
		if !allowed {
			t.Errorf("Request %d should be allowed", i)
		}
		if hits != i {
			t.Errorf("hits = %d, want %d", hits, i)
		}
		if limit != 5 {
			t.Errorf("limit = %d, want 5", limit)
		}
	}

	// 6th request should be blocked
	allowed, hits, _, _ := rl.isAllowed(clientID, path)
	if allowed {
		t.Error("6th request should be blocked")
	}
	if hits != 6 {
		t.Errorf("hits = %d, want 6", hits)
	}
}

func TestRateLimiter_Middleware_AllowsRequests(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit: 100,
		WindowSize:   time.Minute,
	})

	handler := rl.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	}))

	req := httptest.NewRequest("GET", "/api/test", nil)
	rec := httptest.NewRecorder()

	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("Status = %d, want %d", rec.Code, http.StatusOK)
	}

	// Check headers
	headers := []string{
		"X-RateLimit-Limit",
		"X-RateLimit-Remaining",
		"X-RateLimit-Reset",
	}
	for _, h := range headers {
		if rec.Header().Get(h) == "" {
			t.Errorf("Missing header: %s", h)
		}
	}
}

func TestRateLimiter_Middleware_BlocksExceededRequests(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit: 3,
		WindowSize:   time.Minute,
	})

	handler := rl.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	path := "/api/test"

	// Make requests up to the limit
	for i := 0; i < 3; i++ {
		req := httptest.NewRequest("GET", path, nil)
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)

		if rec.Code != http.StatusOK {
			t.Errorf("Request %d: Status = %d, want %d", i+1, rec.Code, http.StatusOK)
		}
	}

	// 4th request should be rate limited
	req := httptest.NewRequest("GET", path, nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusTooManyRequests {
		t.Errorf("Status = %d, want %d", rec.Code, http.StatusTooManyRequests)
	}

	// Check response body
	var resp map[string]interface{}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("Failed to parse response: %v", err)
	}

	if resp["code"] != "RATE_LIMIT_EXCEEDED" {
		t.Errorf("code = %v, want RATE_LIMIT_EXCEEDED", resp["code"])
	}

	if resp["limit"].(float64) != 3 {
		t.Errorf("limit = %v, want 3", resp["limit"])
	}
}

func TestRateLimiter_Middleware_SkipsHealthEndpoint(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit: 2,
		WindowSize:   time.Minute,
		SkipPaths:    []string{"/health"},
	})

	handler := rl.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	// Make many requests to health endpoint - should never be rate limited
	for i := 0; i < 10; i++ {
		req := httptest.NewRequest("GET", "/health", nil)
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)

		if rec.Code != http.StatusOK {
			t.Errorf("Request %d to /health: Status = %d, want %d", i+1, rec.Code, http.StatusOK)
		}
	}
}

func TestRateLimiter_Middleware_PerEndpointLimit(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit: 100,
		WindowSize:   time.Minute,
		EndpointLimits: map[string]int{
			"/api/limited": 2,
		},
	})

	handler := rl.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	// Test limited endpoint
	for i := 0; i < 2; i++ {
		req := httptest.NewRequest("GET", "/api/limited", nil)
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Errorf("Request %d: Status = %d, want %d", i+1, rec.Code, http.StatusOK)
		}
	}

	// 3rd request to limited endpoint should be blocked
	req := httptest.NewRequest("GET", "/api/limited", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusTooManyRequests {
		t.Errorf("Limited endpoint: Status = %d, want %d", rec.Code, http.StatusTooManyRequests)
	}

	// Regular endpoint should still work (has higher limit)
	req = httptest.NewRequest("GET", "/api/regular", nil)
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Errorf("Regular endpoint: Status = %d, want %d", rec.Code, http.StatusOK)
	}
}

func TestRateLimiter_Middleware_APIToken(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit: 2,
		WindowSize:   time.Minute,
		TokenHeader:  "X-API-Token",
	})

	handler := rl.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	path := "/api/test"

	// Client 1 uses token
	for i := 0; i < 2; i++ {
		req := httptest.NewRequest("GET", path, nil)
		req.Header.Set("X-API-Token", "token-1")
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Errorf("Token 1, Request %d: Status = %d, want %d", i+1, rec.Code, http.StatusOK)
		}
	}

	// Client 1 is now rate limited
	req := httptest.NewRequest("GET", path, nil)
	req.Header.Set("X-API-Token", "token-1")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusTooManyRequests {
		t.Errorf("Token 1 (exceeded): Status = %d, want %d", rec.Code, http.StatusTooManyRequests)
	}

	// Client 2 with different token should still work
	req = httptest.NewRequest("GET", path, nil)
	req.Header.Set("X-API-Token", "token-2")
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Errorf("Token 2: Status = %d, want %d", rec.Code, http.StatusOK)
	}
}

func TestRateLimiter_SetEndpointLimit(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit: 100,
	})

	rl.SetEndpointLimit("/api/dynamic", 50)

	if got := rl.getLimitForPath("/api/dynamic"); got != 50 {
		t.Errorf("getLimitForPath(/api/dynamic) = %d, want 50", got)
	}
}

func TestRateLimiter_GetStats(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit:   100,
		WindowSize:     time.Minute,
		EndpointLimits: map[string]int{"/api/test": 50},
		SkipPaths:      []string{"/health"},
	})

	// Add some clients
	rl.isAllowed("ip:1.2.3.4", "/test")
	rl.isAllowed("ip:5.6.7.8", "/test")

	stats := rl.GetStats()

	if stats["default_limit"].(int) != 100 {
		t.Errorf("default_limit = %v, want 100", stats["default_limit"])
	}

	if stats["tracked_clients"].(int) != 2 {
		t.Errorf("tracked_clients = %v, want 2", stats["tracked_clients"])
	}
}

func TestDefaultRateLimiter(t *testing.T) {
	// Ensure DefaultRateLimiter is properly initialized
	if DefaultRateLimiter == nil {
		t.Fatal("DefaultRateLimiter is nil")
	}

	if DefaultRateLimiter.defaultLimit != 100 {
		t.Errorf("DefaultRateLimiter.defaultLimit = %d, want 100", DefaultRateLimiter.defaultLimit)
	}

	// Check skip paths
	skips := DefaultRateLimiter.SkipPaths()
	hasHealth := false
	hasAPIHealth := false
	for _, path := range skips {
		if path == "/health" {
			hasHealth = true
		}
		if path == "/api/health" {
			hasAPIHealth = true
		}
	}
	if !hasHealth {
		t.Error("Default skip paths missing /health")
	}
	if !hasAPIHealth {
		t.Error("Default skip paths missing /api/health")
	}
}

func TestRateLimitMiddleware_BackwardCompatibility(t *testing.T) {
	// Test that the legacy RateLimitMiddleware function works
	handler := RateLimitMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest("GET", "/api/test", nil)
	rec := httptest.NewRecorder()

	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("Status = %d, want %d", rec.Code, http.StatusOK)
	}

	if rec.Header().Get("X-RateLimit-Limit") == "" {
		t.Error("Missing X-RateLimit-Limit header")
	}
}

func TestWithRateLimitConfig(t *testing.T) {
	middleware := WithRateLimitConfig(&RateLimitConfig{
		DefaultLimit: 10,
		SkipPaths:    []string{"/skip"},
	})

	handler := middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	// Test that skip paths work
	for i := 0; i < 15; i++ {
		req := httptest.NewRequest("GET", "/skip", nil)
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Errorf("Request %d to /skip: Status = %d, want %d", i+1, rec.Code, http.StatusOK)
		}
	}
}

func BenchmarkRateLimiter_Middleware(b *testing.B) {
	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit: 10000,
		WindowSize:   time.Minute,
	})

	handler := rl.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest("GET", "/api/test", nil)

	b.ResetTimer()
	b.RunParallel(func(pb *testing.PB) {
		for pb.Next() {
			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, req)
		}
	})
}

// TestRealTimeWindow tests that the rate limiter properly resets after the window expires
func TestRateLimiter_RealTimeWindow(t *testing.T) {
	if testing.Short() {
		t.Skip("Skipping real-time window test in short mode")
	}

	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit: 2,
		WindowSize:   500 * time.Millisecond,
	})

	clientID := "ip:192.168.1.1"
	path := "/api/test"

	// Use up the limit
	for i := 0; i < 2; i++ {
		allowed, _, _, _ := rl.isAllowed(clientID, path)
		if !allowed {
			t.Fatalf("Request %d should be allowed", i+1)
		}
	}

	// Next request should be blocked
	allowed, _, _, _ := rl.isAllowed(clientID, path)
	if allowed {
		t.Fatal("Request should be blocked after limit reached")
	}

	// Wait for window to reset
	time.Sleep(600 * time.Millisecond)

	// Should be allowed again
	allowed, _, _, _ = rl.isAllowed(clientID, path)
	if !allowed {
		t.Fatal("Request should be allowed after window reset")
	}
}

// TestMultiplePathsIndependence tests that rate limits are independent per path
func TestRateLimiter_MultiplePathsIndependence(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit: 2,
		WindowSize:   time.Minute,
	})

	clientID := "ip:192.168.1.1"

	// Use up limit on path1
	for i := 0; i < 2; i++ {
		allowed, _, _, _ := rl.isAllowed(clientID, "/api/path1")
		if !allowed {
			t.Fatalf("path1 request %d should be allowed", i+1)
		}
	}

	// path1 should now be blocked
	allowed, _, _, _ := rl.isAllowed(clientID, "/api/path1")
	if allowed {
		t.Error("path1 should be blocked after limit reached")
	}

	// path2 should still work
	allowed, _, _, _ = rl.isAllowed(clientID, "/api/path2")
	if !allowed {
		t.Error("path2 should still be allowed when path1 is blocked")
	}
}

// TestConcurrentAccess tests thread safety
func TestRateLimiter_ConcurrentAccess(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit: 1000,
		WindowSize:   time.Minute,
	})

	handler := rl.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	done := make(chan bool, 100)

	// Spawn 100 concurrent requests
	for i := 0; i < 100; i++ {
		go func() {
			req := httptest.NewRequest("GET", "/api/test", nil)
			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, req)
			done <- rec.Code == http.StatusOK
		}()
	}

	// Wait for all to complete
	okCount := 0
	for i := 0; i < 100; i++ {
		if <-done {
			okCount++
		}
	}

	if okCount != 100 {
		t.Errorf("Expected all 100 requests to succeed, got %d", okCount)
	}
}

// TestResponseHeaders tests that correct headers are set in various scenarios
func TestRateLimiter_ResponseHeaders(t *testing.T) {
	rl := NewRateLimiter(&RateLimitConfig{
		DefaultLimit: 5,
		WindowSize:   time.Minute,
	})

	handler := rl.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	path := "/api/test"

	// First request
	req := httptest.NewRequest("GET", path, nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	limit := rec.Header().Get("X-RateLimit-Limit")
	remaining := rec.Header().Get("X-RateLimit-Remaining")
	reset := rec.Header().Get("X-RateLimit-Reset")

	if limit != "5" {
		t.Errorf("X-RateLimit-Limit = %s, want 5", limit)
	}
	if remaining != "4" {
		t.Errorf("X-RateLimit-Remaining = %s, want 4", remaining)
	}
	if reset == "" {
		t.Error("X-RateLimit-Reset header is missing")
	}

	// Make requests until limit exceeded
	for i := 0; i < 5; i++ {
		req = httptest.NewRequest("GET", path, nil)
		rec = httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
	}

	// Check blocked request headers
	remaining = rec.Header().Get("X-RateLimit-Remaining")
	if remaining != "0" {
		t.Errorf("X-RateLimit-Remaining on blocked = %s, want 0", remaining)
	}
}

// Example test demonstrating usage
func ExampleWithRateLimitConfig() {
	// Create custom rate limiter configuration
	cfg := &RateLimitConfig{
		DefaultLimit: 100,
		WindowSize:   time.Minute,
		EndpointLimits: map[string]int{
			"/api/upload":    10,  // Stricter limit for uploads
			"/api/webhook":   500, // Higher limit for webhooks
		},
		SkipPaths:   []string{"/health", "/ready"},
		TokenHeader: "Authorization",
	}

	// Apply the middleware
	_ = WithRateLimitConfig(cfg)

	fmt.Println("Rate limiter configured successfully")
	// Output: Rate limiter configured successfully
}
