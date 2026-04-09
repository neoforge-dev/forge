//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// PushDevice represents a registered push notification device.
type PushDevice struct {
	Token     string    `json:"token"`
	Platform  string    `json:"platform"`
	UserID    string    `json:"user_id"`
	CreatedAt time.Time `json:"created_at"`
}

// PushRegistry defines operations for device token management.
type PushRegistry interface {
	Register(ctx context.Context, device PushDevice) error
	Unregister(ctx context.Context, token string) error
	ListByPlatform(ctx context.Context, platform string) ([]PushDevice, error)
	ListAll(ctx context.Context) ([]PushDevice, error)
}

// sqlitePushRegistry is the SQLite-backed implementation of PushRegistry.
type sqlitePushRegistry struct {
	db *sql.DB
}

// NewPushRegistry creates a new sqlitePushRegistry using the provided DB.
func NewPushRegistry(db *sql.DB) PushRegistry {
	return &sqlitePushRegistry{db: db}
}

// Register inserts or replaces a device token record.
func (r *sqlitePushRegistry) Register(ctx context.Context, device PushDevice) error {
	if device.Token == "" {
		return fmt.Errorf("token is required")
	}
	if device.Platform != "ios" && device.Platform != "watchos" {
		return fmt.Errorf("platform must be ios or watchos")
	}
	if device.UserID == "" {
		return fmt.Errorf("user_id is required")
	}
	_, err := r.db.ExecContext(ctx,
		`INSERT OR REPLACE INTO push_devices (token, platform, user_id, created_at)
		 VALUES (?, ?, ?, ?)`,
		device.Token, device.Platform, device.UserID, time.Now().UTC(),
	)
	return err
}

// Unregister removes a device token from the registry.
func (r *sqlitePushRegistry) Unregister(ctx context.Context, token string) error {
	res, err := r.db.ExecContext(ctx, `DELETE FROM push_devices WHERE token = ?`, token)
	if err != nil {
		return err
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return fmt.Errorf("device token not found")
	}
	return nil
}

// ListByPlatform returns all devices registered for a given platform.
func (r *sqlitePushRegistry) ListByPlatform(ctx context.Context, platform string) ([]PushDevice, error) {
	rows, err := r.db.QueryContext(ctx,
		`SELECT token, platform, user_id, created_at FROM push_devices WHERE platform = ?`,
		platform,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanPushDevices(rows)
}

// ListAll returns all registered devices.
func (r *sqlitePushRegistry) ListAll(ctx context.Context) ([]PushDevice, error) {
	rows, err := r.db.QueryContext(ctx,
		`SELECT token, platform, user_id, created_at FROM push_devices ORDER BY created_at DESC`,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanPushDevices(rows)
}

func scanPushDevices(rows *sql.Rows) ([]PushDevice, error) {
	var devices []PushDevice
	for rows.Next() {
		var d PushDevice
		var createdAt string
		if err := rows.Scan(&d.Token, &d.Platform, &d.UserID, &createdAt); err != nil {
			return nil, err
		}
		if t, err := time.Parse(time.RFC3339, createdAt); err == nil {
			d.CreatedAt = t
		} else {
			// Fallback: try common SQLite timestamp format
			if t2, err2 := time.Parse("2006-01-02 15:04:05", createdAt); err2 == nil {
				d.CreatedAt = t2
			}
		}
		devices = append(devices, d)
	}
	return devices, rows.Err()
}

// handlePushRegister handles POST /api/push/register — registers a device token.
func handlePushRegister(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "database unavailable", http.StatusServiceUnavailable)
		return
	}

	var req struct {
		Token    string `json:"token"`
		Platform string `json:"platform"`
		UserID   string `json:"user_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON body", http.StatusBadRequest)
		return
	}

	registry := NewPushRegistry(db)
	device := PushDevice{
		Token:    req.Token,
		Platform: req.Platform,
		UserID:   req.UserID,
	}
	if err := registry.Register(r.Context(), device); err != nil {
		http.Error(w, fmt.Sprintf("register failed: %v", err), http.StatusBadRequest)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "registered", "token": req.Token})
}

// handlePushUnregister handles DELETE /api/push/register — removes a device token.
func handlePushUnregister(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodDelete {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "database unavailable", http.StatusServiceUnavailable)
		return
	}

	token := r.URL.Query().Get("token")
	if token == "" {
		http.Error(w, "token query parameter required", http.StatusBadRequest)
		return
	}

	registry := NewPushRegistry(db)
	if err := registry.Unregister(r.Context(), token); err != nil {
		http.Error(w, fmt.Sprintf("unregister failed: %v", err), http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "unregistered", "token": token})
}

// handlePushDevices handles GET /api/push/devices — lists registered devices.
func handlePushDevices(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "database unavailable", http.StatusServiceUnavailable)
		return
	}

	registry := NewPushRegistry(db)

	var (
		devices []PushDevice
		err     error
	)
	platform := r.URL.Query().Get("platform")
	if platform != "" {
		devices, err = registry.ListByPlatform(r.Context(), platform)
	} else {
		devices, err = registry.ListAll(r.Context())
	}
	if err != nil {
		http.Error(w, fmt.Sprintf("list failed: %v", err), http.StatusInternalServerError)
		return
	}
	if devices == nil {
		devices = []PushDevice{}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"devices": devices,
		"count":   len(devices),
	})
}

// pushRegisterHandler dispatches POST → register, DELETE → unregister.
func pushRegisterHandler(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodPost:
		handlePushRegister(w, r)
	case http.MethodDelete:
		handlePushUnregister(w, r)
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

// pushDevicesHandler handles GET /api/push/devices.
func pushDevicesHandler(w http.ResponseWriter, r *http.Request) {
	handlePushDevices(w, r)
}
