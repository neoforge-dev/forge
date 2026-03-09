package main

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"
)

func TestProtocolValidator_NewValidator(t *testing.T) {
	v := NewProtocolValidator()
	if v == nil {
		t.Fatal("Failed to create validator")
	}
	if len(v.schemas) == 0 {
		t.Error("No schemas loaded")
	}
}

func TestProtocolValidator_ValidateTaskRequest(t *testing.T) {
	v := NewProtocolValidator()

	tests := []struct {
		name    string
		data    string
		wantErr bool
		errCode string
	}{
		{
			name:    "valid task request",
			data:    `{"id": "TASK-001", "type": "feature", "priority": 5}`,
			wantErr: false,
		},
		{
			name:    "missing id",
			data:    `{"type": "feature"}`,
			wantErr: true,
			errCode: "MISSING_FIELD",
		},
		{
			name:    "missing type",
			data:    `{"id": "TASK-001"}`,
			wantErr: true,
			errCode: "MISSING_FIELD",
		},
		{
			name:    "invalid json",
			data:    `{"id": "TASK-001", "type":`,
			wantErr: true,
			errCode: "INVALID_JSON",
		},
		{
			name:    "priority out of range",
			data:    `{"id": "TASK-001", "type": "feature", "priority": 15}`,
			wantErr: true,
			errCode: "OUT_OF_RANGE",
		},
		{
			name:    "invalid status pattern",
			data:    `{"id": "TASK-001", "type": "feature", "status": "invalid"}`,
			wantErr: true,
			errCode: "PATTERN_MISMATCH",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := v.ValidateTaskRequest([]byte(tt.data))
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateTaskRequest() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if err != nil {
				if valErr, ok := err.(ValidationError); ok {
					if valErr.Code != tt.errCode {
						t.Errorf("Expected error code %s, got %s", tt.errCode, valErr.Code)
					}
				} else {
					t.Errorf("Expected ValidationError, got %T", err)
				}
			}
		})
	}
}

func TestProtocolValidator_ValidateWSHandshake(t *testing.T) {
	v := NewProtocolValidator()

	tests := []struct {
		name    string
		headers http.Header
		wantErr bool
	}{
		{
			name: "valid handshake",
			headers: http.Header{
				"X-Worker-ID": []string{"agent-001"},
				"X-Version":   []string{"3.0.0"},
			},
			wantErr: false,
		},
		{
			name:    "missing worker id",
			headers: http.Header{},
			wantErr: true,
		},
		{
			name: "version mismatch",
			headers: http.Header{
				"X-Worker-ID": []string{"agent-001"},
				"X-Version":   []string{"2.0.0"},
			},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := v.ValidateWSHandshake(tt.headers)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateWSHandshake() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

func TestProtocolValidator_ValidateWSMessage(t *testing.T) {
	v := NewProtocolValidator()

	tests := []struct {
		name    string
		data    string
		wantErr bool
	}{
		{
			name:    "valid task.assigned",
			data:    `{"type": "task.assigned", "payload": {"task_id": "TASK-001"}, "timestamp": "2026-03-04T10:00:00Z"}`,
			wantErr: false,
		},
		{
			name:    "valid heartbeat",
			data:    `{"type": "heartbeat", "payload": {"agent_id": "agent-001"}}`,
			wantErr: false,
		},
		{
			name:    "missing type",
			data:    `{"payload": {"task_id": "TASK-001"}}`,
			wantErr: true,
		},
		{
			name:    "invalid type",
			data:    `{"type": "invalid.type", "payload": {}}`,
			wantErr: true,
		},
		{
			name:    "task.assigned missing task_id",
			data:    `{"type": "task.assigned", "payload": {}}`,
			wantErr: true,
		},
		{
			name:    "heartbeat missing agent_id",
			data:    `{"type": "heartbeat", "payload": {}}`,
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := v.ValidateWSMessage([]byte(tt.data))
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateWSMessage() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

func TestProtocolValidator_ValidateTimestamp(t *testing.T) {
	v := NewProtocolValidator()

	tests := []struct {
		name    string
		timestamp string
		wantErr bool
	}{
		{
			name:    "valid RFC3339",
			timestamp: time.Now().Format(time.RFC3339),
			wantErr: false,
		},
		{
			name:    "valid RFC3339Nano",
			timestamp: time.Now().Format(time.RFC3339Nano),
			wantErr: false,
		},
		{
			name:    "invalid format",
			timestamp: "2026-03-04",
			wantErr: true,
		},
		{
			name:    "empty timestamp",
			timestamp: "",
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := v.ValidateTimestamp(tt.timestamp)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateTimestamp() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

func TestIsValidTaskID(t *testing.T) {
	tests := []struct {
		id    string
		valid bool
	}{
		{"TASK-001", true},
		{"TASK001", true},
		{"task-001", false},
		{"", false},
		{"123-TASK", false},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			if got := IsValidTaskID(tt.id); got != tt.valid {
				t.Errorf("IsValidTaskID(%q) = %v, want %v", tt.id, got, tt.valid)
			}
		})
	}
}

func TestIsValidAgentID(t *testing.T) {
	tests := []struct {
		id    string
		valid bool
	}{
		{"agent-001", true},
		{"agent001", true},
		{"AGENT-001", false},
		{"", false},
		{"123-agent", false},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			if got := IsValidAgentID(tt.id); got != tt.valid {
				t.Errorf("IsValidAgentID(%q) = %v, want %v", tt.id, got, tt.valid)
			}
		})
	}
}

func TestProtocolValidator_RunProtocolComplianceCheck(t *testing.T) {
	v := NewProtocolValidator()
	result := v.RunProtocolComplianceCheck()

	if !result.Valid {
		t.Errorf("Protocol compliance check failed: %v", result.Errors)
	}

	if result.Version != ProtocolVersion {
		t.Errorf("Expected version %s, got %s", ProtocolVersion, result.Version)
	}
}

func TestProtocolValidator_ValidateApprovalRequest(t *testing.T) {
	v := NewProtocolValidator()

	tests := []struct {
		name    string
		data    string
		wantErr bool
		errCode string
	}{
		{
			name:    "valid approval",
			data:    `{"type": "deployment", "task_id": "TASK-001", "confidence": 0.85}`,
			wantErr: false,
		},
		{
			name:    "invalid type",
			data:    `{"type": "invalid", "task_id": "TASK-001"}`,
			wantErr: true,
			errCode: "PATTERN_MISMATCH",
		},
		{
			name:    "confidence out of range",
			data:    `{"type": "deployment", "task_id": "TASK-001", "confidence": 1.5}`,
			wantErr: true,
			errCode: "OUT_OF_RANGE",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := v.ValidateApprovalRequest([]byte(tt.data))
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateApprovalRequest() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if err != nil {
				if valErr, ok := err.(ValidationError); ok {
					if valErr.Code != tt.errCode {
						t.Errorf("Expected error code %s, got %s", tt.errCode, valErr.Code)
					}
				}
			}
		})
	}
}

// TestCrossNodeValidation tests protocol validation between SATI and PRYA nodes
func TestCrossNodeValidation(t *testing.T) {
	v := NewProtocolValidator()

	t.Run("SATI to PRYA task creation", func(t *testing.T) {
		// SATI node creates task
		taskData := map[string]interface{}{
			"id":       "SATI-TASK-001",
			"type":     "feature",
			"domain":   "test-domain",
			"project":  "test-project",
			"priority": 5,
			"status":   "requested",
		}

		data, _ := json.Marshal(taskData)
		if err := v.ValidateTaskRequest(data); err != nil {
			t.Errorf("SATI task validation failed: %v", err)
		}
	})

	t.Run("PRYA to SATI WebSocket handshake", func(t *testing.T) {
		// PRYA control plane validates SATI worker connection
		headers := http.Header{
			"X-Worker-ID": []string{"sati-kimi"},
			"X-Node-ID":   []string{"sati"},
			"X-Version":   []string{"3.0.0"},
		}

		if err := v.ValidateWSHandshake(headers); err != nil {
			t.Errorf("PRYA handshake validation failed: %v", err)
		}
	})

	t.Run("Cross-node task assignment", func(t *testing.T) {
		// Task assigned from PRYA to SATI agent
		msg := map[string]interface{}{
			"type": "task.assigned",
			"payload": map[string]interface{}{
				"task_id":   "TASK-001",
				"agent_id":  "sati-kimi",
				"timestamp": time.Now().Format(time.RFC3339),
			},
		}

		data, _ := json.Marshal(msg)
		if err := v.ValidateWSMessage(data); err != nil {
			t.Errorf("Cross-node task assignment validation failed: %v", err)
		}
	})

	t.Run("Version compatibility check", func(t *testing.T) {
		// SATI runs 3.0.0, PRYA runs 3.0.0 - should be compatible
		if !v.isVersionCompatible("3.0.0", "3.0.0") {
			t.Error("Same version should be compatible")
		}

		// SATI runs 3.1.0, PRYA requires 3.0.0 - should be compatible
		if !v.isVersionCompatible("3.1.0", "3.0.0") {
			t.Error("Minor version bump should be compatible")
		}

		// SATI runs 2.0.0, PRYA requires 3.0.0 - should NOT be compatible
		if v.isVersionCompatible("2.0.0", "3.0.0") {
			t.Error("Major version mismatch should not be compatible")
		}
	})
}

// TestTimestampValidationAcrossNodes tests timestamp format consistency
func TestTimestampValidationAcrossNodes(t *testing.T) {
	v := NewProtocolValidator()

	// Different nodes might generate timestamps in different formats
	// but all should be valid
	timestamps := []string{
		time.Now().UTC().Format(time.RFC3339),
		time.Now().UTC().Format(time.RFC3339Nano),
		"2026-03-04T10:00:00Z",
	}

	for _, ts := range timestamps {
		if err := v.ValidateTimestamp(ts); err != nil {
			t.Errorf("Timestamp validation failed for %s: %v", ts, err)
		}
	}
}

// Benchmark validation performance
func BenchmarkValidateTaskRequest(b *testing.B) {
	v := NewProtocolValidator()
	data := []byte(`{"id": "TASK-001", "type": "feature", "priority": 5}`)

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		v.ValidateTaskRequest(data)
	}
}

func BenchmarkValidateWSMessage(b *testing.B) {
	v := NewProtocolValidator()
	data := []byte(`{"type": "heartbeat", "payload": {"agent_id": "agent-001"}}`)

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		v.ValidateWSMessage(data)
	}
}
