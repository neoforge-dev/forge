//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
)

// harnessProxy is the optional reverse proxy to the Python harness.
// Populated at startup when FORGE_HARNESS_URL is set.
var harnessProxy *httputil.ReverseProxy

// initHarnessProxy initialises the reverse proxy if FORGE_HARNESS_URL is set.
// Call this once during server startup before route registration.
func initHarnessProxy() {
	raw := os.Getenv("FORGE_HARNESS_URL")
	if raw == "" {
		return
	}
	target, err := url.Parse(raw)
	if err != nil {
		log.Printf("[proxy] invalid FORGE_HARNESS_URL %q: %v — proxy disabled", raw, err)
		return
	}
	harnessProxy = httputil.NewSingleHostReverseProxy(target)
	// Rewrite Host header so harness CORS/auth doesn't reject the request.
	origDirector := harnessProxy.Director
	harnessProxy.Director = func(r *http.Request) {
		origDirector(r)
		r.Host = target.Host
		r.Header.Set("X-Forwarded-By", "forged")
	}
	harnessProxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		log.Printf("[proxy] harness unreachable for %s %s: %v", r.Method, r.URL.Path, err)
		http.Error(w, `{"error":"harness unavailable","path":"`+r.URL.Path+`"}`, http.StatusBadGateway)
	}
	log.Printf("[proxy] CC fallback proxy enabled → %s", raw)
}

// harnessFallbackHandler is the catch-all "/" handler.
// If the proxy is configured it forwards the request to harness;
// otherwise returns a plain 404 with a helpful message.
func harnessFallbackHandler(w http.ResponseWriter, r *http.Request) {
	if harnessProxy != nil {
		harnessProxy.ServeHTTP(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusNotFound)
	w.Write([]byte(`{"error":"not_found","hint":"set FORGE_HARNESS_URL to proxy unimplemented routes to harness"}`)) //nolint:errcheck
}
