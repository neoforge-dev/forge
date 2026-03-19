// cmd/forged/auth.go
package main

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

// AuthConfig holds authentication configuration
type AuthConfig struct {
	Mode      string                // "local" or "api"
	APITokens map[string]TokenInfo // token -> info
	LocalUID  int                   // Unix UID for local auth
}

// TokenInfo holds information about an API token
type TokenInfo struct {
	Token       string
	Description string
	CreatedAt   time.Time
	ExpiresAt   *time.Time
	Scopes      []string
}

// AuthManager handles authentication
type AuthManager struct {
	config  AuthConfig
	mu      sync.RWMutex
}

// NewAuthManager creates a new auth manager
func NewAuthManager(mode string) *AuthManager {
	return &AuthManager{
		config: AuthConfig{
			Mode:      mode,
			APITokens: make(map[string]TokenInfo),
			LocalUID:  os.Getuid(),
		},
	}
}

// GenerateToken creates a new API token
func (a *AuthManager) GenerateToken(description string, scopes []string) (string, error) {
	// Generate random token
	bytes := make([]byte, 32)
	if _, err := rand.Read(bytes); err != nil {
		return "", err
	}
	token := hex.EncodeToString(bytes)

	a.mu.Lock()
	defer a.mu.Unlock()

	a.config.APITokens[token] = TokenInfo{
		Token:       token,
		Description: description,
		CreatedAt:   time.Now(),
		Scopes:      scopes,
	}

	return token, nil
}

// RevokeToken removes a token
func (a *AuthManager) RevokeToken(token string) error {
	a.mu.Lock()
	defer a.mu.Unlock()

	if _, exists := a.config.APITokens[token]; !exists {
		return errors.New("token not found")
	}

	delete(a.config.APITokens, token)
	return nil
}

// ListTokens returns all tokens (without secret)
func (a *AuthManager) ListTokens() []TokenInfo {
	a.mu.RLock()
	defer a.mu.RUnlock()

	result := make([]TokenInfo, 0, len(a.config.APITokens))
	for _, info := range a.config.APITokens {
		result = append(result, TokenInfo{
			Token:       "****" + info.Token[len(info.Token)-4:],
			Description: info.Description,
			CreatedAt:   info.CreatedAt,
			ExpiresAt:   info.ExpiresAt,
			Scopes:      info.Scopes,
		})
	}
	return result
}

// ValidateToken checks if a token is valid
func (a *AuthManager) ValidateToken(token string, requiredScope string) error {
	a.mu.RLock()
	defer a.mu.RUnlock()

	info, exists := a.config.APITokens[token]
	if !exists {
		return errors.New("invalid token")
	}

	// Check expiration
	if info.ExpiresAt != nil && time.Now().After(*info.ExpiresAt) {
		return errors.New("token expired")
	}

	// Check scope
	if requiredScope != "" {
		found := false
		for _, scope := range info.Scopes {
			if scope == requiredScope || scope == "*" {
				found = true
				break
			}
		}
		if !found {
			return errors.New("insufficient scope")
		}
	}

	return nil
}

// ValidateLocal checks if the connection is from the same UID
func (a *AuthManager) ValidateLocal(uid int) bool {
	return uid == a.config.LocalUID
}

// GetTokenInfo returns info about a specific token
func (a *AuthManager) GetTokenInfo(token string) (*TokenInfo, error) {
	a.mu.RLock()
	defer a.mu.RUnlock()

	info, exists := a.config.APITokens[token]
	if !exists {
		return nil, errors.New("token not found")
	}
	return &info, nil
}

// AuthMiddleware creates middleware for HTTP authentication
func AuthMiddleware(auth *AuthManager) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Skip auth for health check and external webhooks.
			if r.URL.Path == "/health" || r.URL.Path == "/api/health" ||
				r.URL.Path == "/api/github/webhook" {
				next.ServeHTTP(w, r)
				return
			}

			if auth.config.Mode == "local" {
				// Local mode: check peer UID from socket
				// In production, this would use SO_PEERCRED
				// For now, allow all local requests
				next.ServeHTTP(w, r)
				return
			}

			// API mode: validate Bearer token
			authHeader := r.Header.Get("Authorization")
			if authHeader == "" {
				http.Error(w, "Authorization required", http.StatusUnauthorized)
				return
			}

			parts := strings.SplitN(authHeader, " ", 2)
			if len(parts) != 2 || parts[0] != "Bearer" {
				http.Error(w, "Invalid authorization header", http.StatusUnauthorized)
				return
			}

			token := parts[1]
			if err := auth.ValidateToken(token, ""); err != nil {
				http.Error(w, "Invalid or expired token", http.StatusUnauthorized)
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}

// HasScope checks if a token has a specific scope
func (a *AuthManager) HasScope(token string, scope string) bool {
	a.mu.RLock()
	defer a.mu.RUnlock()

	info, exists := a.config.APITokens[token]
	if !exists {
		return false
	}

	for _, s := range info.Scopes {
		if s == scope || s == "*" {
			return true
		}
	}
	return false
}

// SetTokenExpiry sets expiration time for a token
func (a *AuthManager) SetTokenExpiry(token string, expiresAt time.Time) error {
	a.mu.Lock()
	defer a.mu.Unlock()

	info, exists := a.config.APITokens[token]
	if !exists {
		return errors.New("token not found")
	}

	info.ExpiresAt = &expiresAt
	a.config.APITokens[token] = info
	return nil
}
