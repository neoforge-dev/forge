//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"net/http"
)

// authTokensHandler returns an HTTP handler for token management at /api/auth/tokens.
func authTokensHandler(auth *AuthManager) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			tokens := auth.ListTokens()
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]interface{}{"tokens": tokens})
		case http.MethodPost:
			var req struct {
				Description string   `json:"description"`
				Scopes      []string `json:"scopes"`
			}
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				http.Error(w, "invalid request body", http.StatusBadRequest)
				return
			}
			if len(req.Scopes) == 0 {
				req.Scopes = []string{"*"}
			}
			token, err := auth.GenerateToken(req.Description, req.Scopes)
			if err != nil {
				http.Error(w, "failed to generate token", http.StatusInternalServerError)
				return
			}
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]string{"token": token})
		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
	}
}
