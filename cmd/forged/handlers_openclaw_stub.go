//go:build !openclaw
// +build !openclaw

package main

import "net/http"

// openclawHandler is a no-op stub when built without the openclaw tag.
// Full implementation: handlers_openclaw.go (build with -tags openclaw)
// Future: ADR-031 plugin system will replace this build-tag approach.
func openclawHandler(w http.ResponseWriter, r *http.Request) {
	http.Error(w, "openclaw integration not enabled in this build", http.StatusNotFound)
}
