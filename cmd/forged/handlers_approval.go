//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

// NotificationsHandler handles GET (list) and POST (create)
func notificationsHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	switch r.Method {
	case http.MethodGet:
		// List all notifications
		notifMu.RLock()
		var list []Notification
		for _, n := range notifications {
			list = append(list, n)
		}
		notifMu.RUnlock()
		json.NewEncoder(w).Encode(map[string]interface{}{
			"notifications": list,
			"total":         len(list),
		})

	case http.MethodPost:
		// Create notification
		var req struct {
			Type    string `json:"type"`
			Title   string `json:"title"`
			Message string `json:"message"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid request", http.StatusBadRequest)
			return
		}

		notifMu.Lock()
		notificationID++
		id := fmt.Sprintf("notif-%d", notificationID)
		notifications[id] = Notification{
			ID:        id,
			Type:      req.Type,
			Title:     req.Title,
			Message:   req.Message,
			Read:      false,
			CreatedAt: time.Now(),
		}
		notifMu.Unlock()

		json.NewEncoder(w).Encode(map[string]interface{}{
			"status":       "created",
			"notification": notifications[id],
		})

	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

// NotificationActionHandler handles /api/notifications/{id}/read
func notificationActionHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	// Extract ID from path: /api/notifications/{id}/read
	path := strings.TrimPrefix(r.URL.Path, "/api/notifications/")
	id := strings.TrimSuffix(path, "/read")

	if id == "" || path == "notifications/" {
		http.Error(w, "notification ID required", http.StatusBadRequest)
		return
	}

	notifMu.Lock()
	defer notifMu.Unlock()

	n, ok := notifications[id]
	if !ok {
		http.Error(w, "notification not found", http.StatusNotFound)
		return
	}

	if r.Method == http.MethodPost {
		n.Read = true
		notifications[id] = n
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status":       "marked_read",
			"notification": n,
		})
		return
	}

	http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
}
