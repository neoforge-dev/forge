package middleware

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"runtime/debug"
	"sync"
	"time"
)

// DefaultTimeout is the standard 30s timeout for most API requests.
const DefaultTimeout = 30 * time.Second

// timeoutContextKey is used to store timeout configuration in request context.
type timeoutContextKey struct{}

// TimeoutConfig allows per-endpoint timeout configuration.
type TimeoutConfig struct {
	Timeout  time.Duration
	Endpoint string
}

// TimeoutMiddleware wraps an http.Handler with a timeout.
// If the request takes longer than the specified duration, the handler returns
// a 504 Gateway Timeout error and cancels the request context.
func TimeoutMiddleware(timeout time.Duration) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Check if per-endpoint timeout is configured in context
			if cfg, ok := r.Context().Value(timeoutContextKey{}).(*TimeoutConfig); ok && cfg != nil {
				if cfg.Timeout > 0 {
					timeout = cfg.Timeout
				}
			}

			// Buffer handler output so we never concurrently write to the underlying
			// ResponseWriter from both the handler goroutine and the timeout path.
			wr := newTimeoutResponseWriter(w)

			// Create context with timeout
			ctx, cancel := context.WithTimeout(r.Context(), timeout)
			defer cancel()

			// Create channel for handler completion
			done := make(chan struct{})
			panicChan := make(chan interface{}, 1)

			// Run handler in goroutine
			go func() {
				defer func() {
					if r := recover(); r != nil {
						panicChan <- r
					}
					close(done)
				}()
				next.ServeHTTP(wr, r.WithContext(ctx))
			}()

			// Wait for completion, timeout, or panic
			select {
			case <-done:
				// Handler completed normally
				wr.flush()
				return
			case p := <-panicChan:
				// Recover from panic and log error
				log.Printf("[Timeout] Panic recovered in handler: %v", p)
				debug.Stack()
				wr.setTimedOut()
				writeTimeoutResponse(w, r, timeout)
				return
			case <-ctx.Done():
				// Timeout occurred
				wr.setTimedOut()
				writeTimeoutResponse(w, r, timeout)
				// Wait for goroutine to finish to ensure resource cleanup
				select {
				case <-done:
					// Handler completed after timeout
				case <-time.After(5 * time.Second):
					// Force cleanup after grace period
					log.Printf("[Timeout] Handler for %s %s did not complete after timeout, forcing cleanup",
						r.Method, r.URL.Path)
				}
			}
		})
	}
}

// timeoutResponseWriter buffers output from the handler goroutine until the
// request completes. On timeout, buffered output is discarded.
type timeoutResponseWriter struct {
	underlying http.ResponseWriter

	mu          sync.Mutex
	header      http.Header
	buf         bytes.Buffer
	wroteHeader bool
	status      int
	timedOut    bool
}

func (w *timeoutResponseWriter) WriteHeader(status int) {
	w.mu.Lock()
	defer w.mu.Unlock()

	if w.timedOut || w.wroteHeader {
		return
	}
	w.status = status
	w.wroteHeader = true
}

func (w *timeoutResponseWriter) Write(b []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()

	if w.timedOut {
		return 0, fmt.Errorf("request timed out")
	}
	if !w.wroteHeader {
		w.wroteHeader = true
		if w.status == 0 {
			w.status = http.StatusOK
		}
	}
	return w.buf.Write(b)
}

func (w *timeoutResponseWriter) Header() http.Header {
	return w.header
}

func newTimeoutResponseWriter(underlying http.ResponseWriter) *timeoutResponseWriter {
	return &timeoutResponseWriter{
		underlying: underlying,
		header:     make(http.Header),
		status:     http.StatusOK,
	}
}

func (w *timeoutResponseWriter) setTimedOut() {
	w.mu.Lock()
	w.timedOut = true
	w.mu.Unlock()
}

func (w *timeoutResponseWriter) flush() {
	w.mu.Lock()
	if w.timedOut {
		w.mu.Unlock()
		return
	}

	status := w.status
	hdr := w.header.Clone()
	body := append([]byte(nil), w.buf.Bytes()...)
	w.mu.Unlock()

	dst := w.underlying
	for k, vv := range hdr {
		for _, v := range vv {
			dst.Header().Add(k, v)
		}
	}
	dst.WriteHeader(status)
	_, _ = dst.Write(body)
}

// timeoutResponse represents the JSON response for timeout errors.
type timeoutResponse struct {
	Error      string    `json:"error"`
	Code       string    `json:"code"`
	Timeout    int64     `json:"timeout_ms"`
	Path       string    `json:"path"`
	Method     string    `json:"method"`
	Timestamp  time.Time `json:"timestamp"`
	RequestID  string    `json:"request_id,omitempty"`
	Suggestion string    `json:"suggestion,omitempty"`
}

// writeTimeoutResponse writes a 504 Gateway Timeout response.
func writeTimeoutResponse(w http.ResponseWriter, r *http.Request, timeout time.Duration) {
	// Get request ID from header if available
	requestID := r.Header.Get("X-Request-ID")
	if requestID == "" {
		requestID = r.Header.Get("X-Trace-ID")
	}

	response := timeoutResponse{
		Error:      "Request timeout",
		Code:       "GATEWAY_TIMEOUT",
		Timeout:    timeout.Milliseconds(),
		Path:       r.URL.Path,
		Method:     r.Method,
		Timestamp:  time.Now().UTC(),
		RequestID:  requestID,
		Suggestion: "The request took longer than expected. Try with a smaller payload or retry later.",
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Timeout-Duration", fmt.Sprintf("%dms", timeout.Milliseconds()))
	w.WriteHeader(http.StatusGatewayTimeout)

	if err := json.NewEncoder(w).Encode(response); err != nil {
		log.Printf("[Timeout] Failed to encode timeout response: %v", err)
		// Fallback to plain text
		http.Error(w, "Gateway Timeout", http.StatusGatewayTimeout)
	}

	log.Printf("[Timeout] %s %s exceeded timeout of %v (RequestID: %s)",
		r.Method, r.URL.Path, timeout, requestID)
}

// WithTimeout adds timeout configuration to request context.
// Usage: router.With(WithTimeout(5*time.Second)).HandleFunc(...)
func WithTimeout(timeout time.Duration) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			cfg := &TimeoutConfig{
				Timeout:  timeout,
				Endpoint: r.URL.Path,
			}
			ctx := context.WithValue(r.Context(), timeoutContextKey{}, cfg)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

// PerEndpointTimeouts maps path patterns to custom timeout durations.
type PerEndpointTimeouts map[string]time.Duration

// TimeoutWithEndpoints creates middleware with per-endpoint timeout overrides.
// The defaultTimeout is used unless a matching pattern is found.
// Patterns are matched in order; first match wins.
func TimeoutWithEndpoints(defaultTimeout time.Duration, endpoints PerEndpointTimeouts) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			timeout := defaultTimeout

			// Check for per-endpoint override
			if endpoints != nil {
				if customTimeout, ok := endpoints[r.URL.Path]; ok {
					timeout = customTimeout
				}
			}

			// Apply timeout middleware with determined duration
			TimeoutMiddleware(timeout)(next).ServeHTTP(w, r)
		})
	}
}

// RecoveryMiddleware recovers from panics and returns 500 error.
func RecoveryMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				log.Printf("[Recovery] Panic recovered: %v\n%s", rec, debug.Stack())
				w.WriteHeader(http.StatusInternalServerError)
				json.NewEncoder(w).Encode(map[string]interface{}{
					"error":     "Internal server error",
					"code":      "INTERNAL_ERROR",
					"timestamp": time.Now().UTC(),
				})
			}
		}()
		next.ServeHTTP(w, r)
	})
}
