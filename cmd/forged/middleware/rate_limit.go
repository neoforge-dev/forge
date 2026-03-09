package middleware

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"
)

// RateLimiter provides configurable rate limiting with per-endpoint support
type RateLimiter struct {
	clients     map[string]*clientStats
	mu          sync.RWMutex
	defaultLimit int
	windowSize  time.Duration
	// Per-endpoint limits: path -> requests per window
	endpointLimits map[string]int
	// Skip list: paths that bypass rate limiting
	skipPaths map[string]bool
	// Enable API token extraction from headers
	tokenHeader string
}

// clientStats tracks request count and window for a client
type clientStats struct {
	hits       int
	lastWindow time.Time
}

// RateLimitConfig allows customization of the rate limiter
type RateLimitConfig struct {
	DefaultLimit   int
	WindowSize     time.Duration
	EndpointLimits map[string]int // path -> limit (0 = use default)
	SkipPaths      []string       // paths to skip rate limiting
	TokenHeader    string         // header name for API token (e.g., "X-API-Token")
}

// DefaultRateLimiter is the global rate limiter instance
var DefaultRateLimiter = NewRateLimiter(&RateLimitConfig{
	DefaultLimit: 100,
	WindowSize:   time.Minute,
	EndpointLimits: map[string]int{
		"/api/health":      0, // Use default (no special limit)
		"/health":          0,
		"/api/status":      200, // Higher limit for status
		"/metrics":         60,  // Lower limit for metrics
		"/api/tui/logs":    300, // Higher for dashboard
		"/api/tui/dashboard": 300,
	},
	SkipPaths:   []string{"/health", "/api/health"},
	TokenHeader: "X-API-Token",
})

// NewRateLimiter creates a new rate limiter with the given configuration
func NewRateLimiter(cfg *RateLimitConfig) *RateLimiter {
	if cfg.DefaultLimit <= 0 {
		cfg.DefaultLimit = 100
	}
	if cfg.WindowSize <= 0 {
		cfg.WindowSize = time.Minute
	}

	rl := &RateLimiter{
		clients:        make(map[string]*clientStats),
		defaultLimit:   cfg.DefaultLimit,
		windowSize:     cfg.WindowSize,
		endpointLimits: cfg.EndpointLimits,
		skipPaths:      make(map[string]bool),
		tokenHeader:    cfg.TokenHeader,
	}

	for _, path := range cfg.SkipPaths {
		rl.skipPaths[path] = true
	}

	return rl
}

// getClientIdentifier extracts a unique identifier for the client
// Priority: API token > IP address
func (rl *RateLimiter) getClientIdentifier(r *http.Request) string {
	// Check for API token first
	if rl.tokenHeader != "" {
		if token := r.Header.Get(rl.tokenHeader); token != "" {
			return "token:" + token
		}
	}

	// Fall back to IP address
	ip, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		ip = r.RemoteAddr
	}

	// Handle X-Forwarded-For if behind proxy
	if forwarded := r.Header.Get("X-Forwarded-For"); forwarded != "" {
		// Use the first IP in the chain (client IP)
		ips := strings.Split(forwarded, ",")
		if len(ips) > 0 {
			ip = strings.TrimSpace(ips[0])
		}
	}

	return "ip:" + ip
}

// shouldSkip checks if the request path should skip rate limiting
func (rl *RateLimiter) shouldSkip(path string) bool {
	rl.mu.RLock()
	defer rl.mu.RUnlock()

	// Exact match
	if rl.skipPaths[path] {
		return true
	}

	// Prefix match for paths like /api/health/detailed
	for skipPath := range rl.skipPaths {
		if strings.HasPrefix(path, skipPath+"/") {
			return true
		}
	}

	return false
}

// getLimitForPath returns the rate limit for a specific path
func (rl *RateLimiter) getLimitForPath(path string) int {
	rl.mu.RLock()
	defer rl.mu.RUnlock()

	// Check for exact match first
	if limit, ok := rl.endpointLimits[path]; ok {
		if limit > 0 {
			return limit
		}
		// limit == 0 means use default
	}

	// Check for prefix matches (e.g., /api/tasks/123/events matches /api/tasks)
	for endpoint, limit := range rl.endpointLimits {
		if strings.HasPrefix(path, endpoint+"/") && limit > 0 {
			return limit
		}
	}

	return rl.defaultLimit
}

// isAllowed checks if a request from the given client is allowed
// Returns (allowed, currentHits, limit, resetTime)
func (rl *RateLimiter) isAllowed(clientID, path string) (bool, int, int, time.Time) {
	// Get limit first (needs read lock)
	limit := rl.getLimitForPath(path)

	rl.mu.Lock()
	defer rl.mu.Unlock()

	now := time.Now()
	window := now.Truncate(rl.windowSize)
	resetTime := window.Add(rl.windowSize)

	// Create composite key: clientID + path for per-endpoint limiting
	key := clientID + ":" + path

	stats, exists := rl.clients[key]
	if !exists || stats.lastWindow.Before(window) {
		stats = &clientStats{
			hits:       0,
			lastWindow: window,
		}
		rl.clients[key] = stats
	}

	stats.hits++
	return stats.hits <= limit, stats.hits, limit, resetTime
}

// Middleware returns the rate limiting middleware function
func (rl *RateLimiter) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Check if path should skip rate limiting
		if rl.shouldSkip(r.URL.Path) {
			next.ServeHTTP(w, r)
			return
		}

		clientID := rl.getClientIdentifier(r)
		allowed, hits, limit, resetTime := rl.isAllowed(clientID, r.URL.Path)

		remaining := limit - hits
		if remaining < 0 {
			remaining = 0
		}

		// Set rate limit headers
		w.Header().Set("X-RateLimit-Limit", fmt.Sprintf("%d", limit))
		w.Header().Set("X-RateLimit-Remaining", fmt.Sprintf("%d", remaining))
		w.Header().Set("X-RateLimit-Reset", fmt.Sprintf("%d", resetTime.Unix()))

		if !allowed {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusTooManyRequests)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"error":       "rate limit exceeded",
				"code":        "RATE_LIMIT_EXCEEDED",
				"limit":       limit,
				"window":      rl.windowSize.String(),
				"retry_after": int(time.Until(resetTime).Seconds()),
			})
			return
		}

		next.ServeHTTP(w, r)
	})
}

// RateLimitMiddleware is the legacy middleware function using the default rate limiter
// It provides backward compatibility while using the new configurable backend
func RateLimitMiddleware(next http.Handler) http.Handler {
	return DefaultRateLimiter.Middleware(next)
}

// WithRateLimitConfig creates a new middleware with custom configuration
func WithRateLimitConfig(cfg *RateLimitConfig) func(http.Handler) http.Handler {
	rl := NewRateLimiter(cfg)
	return rl.Middleware
}

// SkipPaths returns the paths that are configured to skip rate limiting
func (rl *RateLimiter) SkipPaths() []string {
	rl.mu.RLock()
	defer rl.mu.RUnlock()

	paths := make([]string, 0, len(rl.skipPaths))
	for path := range rl.skipPaths {
		paths = append(paths, path)
	}
	return paths
}

// SetEndpointLimit dynamically sets a limit for a specific endpoint
func (rl *RateLimiter) SetEndpointLimit(path string, limit int) {
	rl.mu.Lock()
	defer rl.mu.Unlock()

	if rl.endpointLimits == nil {
		rl.endpointLimits = make(map[string]int)
	}
	rl.endpointLimits[path] = limit
}

// GetStats returns current statistics for debugging/monitoring
func (rl *RateLimiter) GetStats() map[string]interface{} {
	rl.mu.RLock()
	defer rl.mu.RUnlock()

	return map[string]interface{}{
		"default_limit":   rl.defaultLimit,
		"window_size":     rl.windowSize.String(),
		"endpoint_limits": rl.endpointLimits,
		"skip_paths":      rl.SkipPaths(),
		"tracked_clients": len(rl.clients),
	}
}
