//go:build tmux_bridge
// +build tmux_bridge

package main

import "net/http"

// openclawHandler stub for tmux_bridge builds only.
func openclawHandler(w http.ResponseWriter, r *http.Request) {
	http.Error(w, "openclaw integration not enabled in this build", http.StatusNotFound)
}
