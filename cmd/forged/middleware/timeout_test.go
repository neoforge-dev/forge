package middleware

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestTimeoutMiddleware_DefaultTimeout(t *testing.T) {
	handler := TimeoutMiddleware(100*time.Millisecond)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(200 * time.Millisecond)
		w.Write([]byte("should not reach here"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/test", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusGatewayTimeout {
		t.Errorf("expected status %d, got %d", http.StatusGatewayTimeout, rr.Code)
	}

	var resp timeoutResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if resp.Code != "GATEWAY_TIMEOUT" {
		t.Errorf("expected code GATEWAY_TIMEOUT, got %s", resp.Code)
	}

	if resp.Timeout != 100 {
		t.Errorf("expected timeout 100ms, got %d", resp.Timeout)
	}

	if resp.Method != http.MethodGet {
		t.Errorf("expected method GET, got %s", resp.Method)
	}

	if resp.Path != "/test" {
		t.Errorf("expected path /test, got %s", resp.Path)
	}

	if resp.Suggestion == "" {
		t.Error("expected non-empty suggestion")
	}

	// Check header
	if duration := rr.Header().Get("X-Timeout-Duration"); duration != "100ms" {
		t.Errorf("expected X-Timeout-Duration header 100ms, got %s", duration)
	}
}

func TestTimeoutMiddleware_NoTimeout(t *testing.T) {
	handler := TimeoutMiddleware(500*time.Millisecond)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("success"))
	}))

	req := httptest.NewRequest(http.MethodPost, "/api/data", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status %d, got %d", http.StatusOK, rr.Code)
	}

	if body := rr.Body.String(); !strings.Contains(body, "success") {
		t.Errorf("expected body to contain 'success', got %s", body)
	}
}

func TestTimeoutMiddleware_QuickResponse(t *testing.T) {
	handler := TimeoutMiddleware(50*time.Millisecond)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("quick"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status %d, got %d", http.StatusOK, rr.Code)
	}
}

func TestTimeoutMiddleware_WithinTimeout(t *testing.T) {
	handler := TimeoutMiddleware(200*time.Millisecond)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(50 * time.Millisecond)
		w.WriteHeader(http.StatusCreated)
		w.Write([]byte("created"))
	}))

	req := httptest.NewRequest(http.MethodPut, "/items/123", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusCreated {
		t.Errorf("expected status %d, got %d", http.StatusCreated, rr.Code)
	}
}

func TestTimeoutMiddleware_DefaultTimeoutConstant(t *testing.T) {
	if DefaultTimeout != 30*time.Second {
		t.Errorf("expected DefaultTimeout to be 30s, got %v", DefaultTimeout)
	}
}

func TestTimeoutMiddleware_ResponseHeaders(t *testing.T) {
	handler := TimeoutMiddleware(100*time.Millisecond)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(200 * time.Millisecond)
		w.Write([]byte("too slow"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/slow", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	contentType := rr.Header().Get("Content-Type")
	if contentType != "application/json" {
		t.Errorf("expected Content-Type application/json, got %s", contentType)
	}

	timeoutDuration := rr.Header().Get("X-Timeout-Duration")
	if timeoutDuration != "100ms" {
		t.Errorf("expected X-Timeout-Duration 100ms, got %s", timeoutDuration)
	}
}

func TestTimeoutMiddleware_RequestID(t *testing.T) {
	handler := TimeoutMiddleware(50*time.Millisecond)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(100 * time.Millisecond)
		w.Write([]byte("timeout"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set("X-Request-ID", "test-request-123")
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	var resp timeoutResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if resp.RequestID != "test-request-123" {
		t.Errorf("expected request_id test-request-123, got %s", resp.RequestID)
	}
}

func TestTimeoutMiddleware_TraceIDFallback(t *testing.T) {
	handler := TimeoutMiddleware(50*time.Millisecond)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(100 * time.Millisecond)
		w.Write([]byte("timeout"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set("X-Trace-ID", "trace-abc-123")
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	var resp timeoutResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if resp.RequestID != "trace-abc-123" {
		t.Errorf("expected request_id trace-abc-123, got %s", resp.RequestID)
	}
}

func TestTimeoutMiddleware_CustomResponse(t *testing.T) {
	// Test that handler returns custom response when not timing out
	handler := TimeoutMiddleware(1*time.Second)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Custom-Header", "custom-value")
		w.WriteHeader(http.StatusAccepted)
		w.Write([]byte(`{"status":"ok"}`))
	}))

	req := httptest.NewRequest(http.MethodGet, "/custom", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusAccepted {
		t.Errorf("expected status %d, got %d", http.StatusAccepted, rr.Code)
	}

	if header := rr.Header().Get("X-Custom-Header"); header != "custom-value" {
		t.Errorf("expected X-Custom-Header custom-value, got %s", header)
	}

	body := rr.Body.String()
	if !strings.Contains(body, "status") {
		t.Errorf("expected body to contain 'status', got %s", body)
	}
}

func TestTimeoutMiddleware_ChainedMiddleware(t *testing.T) {
	// Test timeout middleware in a chain
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(100 * time.Millisecond)
		w.Write([]byte("done"))
	})

	// Chain: Timeout -> inner
	handler := TimeoutMiddleware(50*time.Millisecond)(inner)

	req := httptest.NewRequest(http.MethodGet, "/chain", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusGatewayTimeout {
		t.Errorf("expected status %d, got %d", http.StatusGatewayTimeout, rr.Code)
	}
}

func TestTimeoutMiddleware_ResourceCleanup(t *testing.T) {
	cleanupCalled := false

	handler := TimeoutMiddleware(50*time.Millisecond)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			cleanupCalled = true
		}()
		time.Sleep(200 * time.Millisecond)
		w.Write([]byte("won't complete"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/cleanup", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	// Give a moment for deferred cleanup to be called
	time.Sleep(100 * time.Millisecond)

	if !cleanupCalled {
		t.Error("expected cleanup (defer) to be called after timeout")
	}
}

func TestWithTimeout_ContextValue(t *testing.T) {
	var capturedTimeout time.Duration

	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if cfg, ok := r.Context().Value(timeoutContextKey{}).(*TimeoutConfig); ok {
			capturedTimeout = cfg.Timeout
		}
		w.Write([]byte("ok"))
	})

	handler := WithTimeout(5 * time.Second)(inner)

	req := httptest.NewRequest(http.MethodGet, "/context", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	if capturedTimeout != 5*time.Second {
		t.Errorf("expected timeout 5s in context, got %v", capturedTimeout)
	}
}

func TestTimeoutWithEndpoints_PerEndpointConfig(t *testing.T) {
	endpoints := PerEndpointTimeouts{
		"/fast":   100 * time.Millisecond,
		"/slow":   500 * time.Millisecond,
		"/medium": 300 * time.Millisecond,
	}

	handler := TimeoutWithEndpoints(1*time.Second, endpoints)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Simulate work that exceeds /fast timeout but not /slow
		time.Sleep(200 * time.Millisecond)
		w.Write([]byte("completed"))
	}))

	// Test /fast endpoint - should timeout
	t.Run("FastEndpoint", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/fast", nil)
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)

		if rr.Code != http.StatusGatewayTimeout {
			t.Errorf("expected timeout for /fast, got status %d", rr.Code)
		}
	})

	// Test /slow endpoint - should succeed
	t.Run("SlowEndpoint", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/slow", nil)
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)

		if rr.Code != http.StatusOK {
			t.Errorf("expected success for /slow, got status %d", rr.Code)
		}
	})

	// Test unknown endpoint - should use default (1s)
	t.Run("DefaultEndpoint", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/unknown", nil)
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)

		if rr.Code != http.StatusOK {
			t.Errorf("expected success for /unknown with default timeout, got status %d", rr.Code)
		}
	})
}

func TestTimeoutWithEndpoints_NilEndpoints(t *testing.T) {
	// Test with nil endpoints map - should use default
	handler := TimeoutWithEndpoints(200*time.Millisecond, nil)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(300 * time.Millisecond)
		w.Write([]byte("slow"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/any", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusGatewayTimeout {
		t.Errorf("expected status %d, got %d", http.StatusGatewayTimeout, rr.Code)
	}
}

func TestRecoveryMiddleware_PanicRecovery(t *testing.T) {
	handler := RecoveryMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		panic("something went wrong")
	}))

	req := httptest.NewRequest(http.MethodGet, "/panic", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusInternalServerError {
		t.Errorf("expected status %d, got %d", http.StatusInternalServerError, rr.Code)
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if resp["code"] != "INTERNAL_ERROR" {
		t.Errorf("expected code INTERNAL_ERROR, got %v", resp["code"])
	}
}

func TestRecoveryMiddleware_NormalRequest(t *testing.T) {
	handler := RecoveryMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("all good"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/normal", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status %d, got %d", http.StatusOK, rr.Code)
	}

	if body := rr.Body.String(); !strings.Contains(body, "all good") {
		t.Errorf("expected body 'all good', got %s", body)
	}
}

func TestTimeoutResponseWriter(t *testing.T) {
	// Test that writes after timeout are blocked
	rr := httptest.NewRecorder()
	wr := newTimeoutResponseWriter(rr)
	wr.setTimedOut()

	n, err := wr.Write([]byte("data"))
	if n != 0 {
		t.Errorf("expected 0 bytes written after timeout, got %d", n)
	}
	if err == nil {
		t.Error("expected error when writing after timeout")
	}

	// WriteHeader should be blocked when timed out
	wr.WriteHeader(http.StatusOK)
	// Note: The wrapper itself blocks WriteHeader, but httptest.ResponseRecorder
	// won't reflect this in Code because we block before delegating
}

func BenchmarkTimeoutMiddleware_NoTimeout(b *testing.B) {
	handler := TimeoutMiddleware(1*time.Second)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("ok"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/bench", nil)

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)
	}
}

func BenchmarkTimeoutMiddleware_Timeout(b *testing.B) {
	handler := TimeoutMiddleware(1*time.Millisecond)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(10 * time.Millisecond)
		w.Write([]byte("too slow"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/bench", nil)

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)
	}
}

func TestTimeoutMiddleware_ContextCancellation(t *testing.T) {
	ctxCancelled := false

	handler := TimeoutMiddleware(50*time.Millisecond)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Check if context is cancelled
		<-r.Context().Done()
		ctxCancelled = true
		w.Write([]byte("won't complete"))
	}))

	req := httptest.NewRequest(http.MethodGet, "/ctx", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	if !ctxCancelled {
		t.Error("expected context to be cancelled after timeout")
	}
}

func TestTimeoutMiddleware_LargeResponse(t *testing.T) {
	// Test that large response bodies are handled correctly
	handler := TimeoutMiddleware(500*time.Millisecond)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Write large response
		for i := 0; i < 1000; i++ {
			w.Write([]byte("this is a test line that will be repeated many times to create a large response body\n"))
		}
	}))

	req := httptest.NewRequest(http.MethodGet, "/large", nil)
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status %d, got %d", http.StatusOK, rr.Code)
	}

	// Check that body is not empty
	if rr.Body.Len() == 0 {
		t.Error("expected non-empty body")
	}
}

func TestTimeoutMiddleware_MultipleRequests(t *testing.T) {
	handler := TimeoutMiddleware(100*time.Millisecond)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Simulate variable response times
		if r.URL.Path == "/slow" {
			time.Sleep(200 * time.Millisecond)
		} else {
			time.Sleep(10 * time.Millisecond)
		}
		w.Write([]byte("done"))
	}))

	// Run multiple requests
	for i := 0; i < 10; i++ {
		path := "/fast"
		expectedStatus := http.StatusOK
		if i%2 == 0 {
			path = "/slow"
			expectedStatus = http.StatusGatewayTimeout
		}

		req := httptest.NewRequest(http.MethodGet, path, nil)
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)

		if rr.Code != expectedStatus {
			t.Errorf("request %d: expected status %d, got %d", i, expectedStatus, rr.Code)
		}
	}
}

func TestTimeoutMiddleware_ReadBody(t *testing.T) {
	handler := TimeoutMiddleware(1*time.Second)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		w.Write(body)
	}))

	req := httptest.NewRequest(http.MethodPost, "/echo", strings.NewReader("hello world"))
	rr := httptest.NewRecorder()

	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status %d, got %d", http.StatusOK, rr.Code)
	}

	if body := rr.Body.String(); body != "hello world" {
		t.Errorf("expected body 'hello world', got %s", body)
	}
}
