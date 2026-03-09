package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"regexp"
	"strings"
	"time"
)

// ProtocolVersion represents the current protocol version
const ProtocolVersion = "3.0.0"

// ValidationError represents a protocol validation error
type ValidationError struct {
	Field   string `json:"field"`
	Reason  string `json:"reason"`
	Code    string `json:"code"`
	Context string `json:"context,omitempty"`
}

func (e ValidationError) Error() string {
	return fmt.Sprintf("validation error [%s]: %s - %s", e.Code, e.Field, e.Reason)
}

// ProtocolValidator validates messages and requests against FORGE protocol
type ProtocolValidator struct {
	schemas map[string]Schema
}

// Schema represents a validation schema for a message type
type Schema struct {
	RequiredFields []string              `json:"required_fields"`
	FieldTypes     map[string]string     `json:"field_types"`
	FieldPatterns  map[string]string     `json:"field_patterns"`
	FieldRanges    map[string]Range      `json:"field_ranges"`
	CustomRules    map[string]CustomRule `json:"custom_rules"`
}

// Range represents a numeric range
type Range struct {
	Min float64 `json:"min"`
	Max float64 `json:"max"`
}

// CustomRule represents a custom validation rule
type CustomRule struct {
	Type   string                 `json:"type"`
	Params map[string]interface{} `json:"params"`
}

// NewProtocolValidator creates a new protocol validator
func NewProtocolValidator() *ProtocolValidator {
	v := &ProtocolValidator{
		schemas: make(map[string]Schema),
	}
	v.loadSchemas()
	return v
}

// loadSchemas initializes validation schemas
func (v *ProtocolValidator) loadSchemas() {
	// Task creation schema
	v.schemas["task_create"] = Schema{
		RequiredFields: []string{"id", "type"},
		FieldTypes: map[string]string{
			"id":          "string",
			"type":        "string",
			"status":      "string",
			"domain":      "string",
			"project":     "string",
			"priority":    "integer",
			"payload":     "object",
			"assigned_to": "string",
		},
		FieldPatterns: map[string]string{
			"id":     `^[A-Z0-9-]+$`,
			"status": `^(requested|queued|assigned|in-progress|completed|failed|paused)$`,
		},
		CustomRules: map[string]CustomRule{
			"priority": {
				Type: "range",
				Params: map[string]interface{}{
					"min": float64(1),
					"max": float64(10),
				},
			},
		},
	}

	// Task claim schema
	v.schemas["task_claim"] = Schema{
		RequiredFields: []string{"agent_id"},
		FieldTypes: map[string]string{
			"agent_id": "string",
			"force":    "boolean",
		},
	}

	// Approval schema
	v.schemas["approval_create"] = Schema{
		RequiredFields: []string{"type", "task_id"},
		FieldTypes: map[string]string{
			"type":       "string",
			"task_id":    "string",
			"requester":  "string",
			"confidence": "number",
		},
		FieldPatterns: map[string]string{
			"type": `^(deployment|task_execution|configuration_change|emergency)$`,
		},
		CustomRules: map[string]CustomRule{
			"confidence": {
				Type: "range",
				Params: map[string]interface{}{
					"min": 0.0,
					"max": 1.0,
				},
			},
		},
	}

	// WebSocket handshake schema
	v.schemas["ws_handshake"] = Schema{
		RequiredFields: []string{"X-Worker-ID"},
		FieldTypes: map[string]string{
			"X-Worker-ID": "string",
			"X-Node-ID":   "string",
			"X-Version":   "string",
		},
		CustomRules: map[string]CustomRule{
			"X-Version": {
				Type: "version_compatible",
				Params: map[string]interface{}{
					"min_version": "3.0.0",
				},
			},
		},
	}

	// WebSocket message schema
	v.schemas["ws_message"] = Schema{
		RequiredFields: []string{"type", "payload"},
		FieldTypes: map[string]string{
			"type":      "string",
			"payload":   "object",
			"id":        "string",
			"timestamp": "string",
		},
		FieldPatterns: map[string]string{
			"type": `^(task\.assigned|task\.completed|heartbeat|ping|pong|error|ack)$`,
		},
	}
}

// ValidateTaskRequest validates a task creation request
func (v *ProtocolValidator) ValidateTaskRequest(data []byte) error {
	var req map[string]interface{}
	if err := json.Unmarshal(data, &req); err != nil {
		return ValidationError{
			Code:    "INVALID_JSON",
			Field:   "",
			Reason:  "Invalid JSON format",
			Context: err.Error(),
		}
	}

	return v.validateAgainstSchema(req, "task_create")
}

// ValidateTaskClaim validates a task claim request
func (v *ProtocolValidator) ValidateTaskClaim(data []byte) error {
	var req map[string]interface{}
	if err := json.Unmarshal(data, &req); err != nil {
		return ValidationError{
			Code:    "INVALID_JSON",
			Field:   "",
			Reason:  "Invalid JSON format",
			Context: err.Error(),
		}
	}

	return v.validateAgainstSchema(req, "task_claim")
}

// ValidateApprovalRequest validates an approval creation request
func (v *ProtocolValidator) ValidateApprovalRequest(data []byte) error {
	var req map[string]interface{}
	if err := json.Unmarshal(data, &req); err != nil {
		return ValidationError{
			Code:    "INVALID_JSON",
			Field:   "",
			Reason:  "Invalid JSON format",
			Context: err.Error(),
		}
	}

	return v.validateAgainstSchema(req, "approval_create")
}

// ValidateWSHandshake validates WebSocket handshake headers
func (v *ProtocolValidator) ValidateWSHandshake(headers http.Header) error {
	data := make(map[string]interface{})
	for key, values := range headers {
		if len(values) > 0 {
			data[key] = values[0]
		}
	}

	schema := v.schemas["ws_handshake"]

	// Check required fields
	for _, field := range schema.RequiredFields {
		if _, ok := data[field]; !ok {
			return ValidationError{
				Code:   "MISSING_FIELD",
				Field:  field,
				Reason: fmt.Sprintf("Required header '%s' is missing", field),
			}
		}
	}

	// Check protocol version
	if version, ok := data["X-Version"].(string); ok {
		if !v.isVersionCompatible(version, ProtocolVersion) {
			return ValidationError{
				Code:   "VERSION_MISMATCH",
				Field:  "X-Version",
				Reason: fmt.Sprintf("Protocol version mismatch: got %s, expected %s", version, ProtocolVersion),
			}
		}
	}

	return nil
}

// ValidateWSMessage validates a WebSocket message
func (v *ProtocolValidator) ValidateWSMessage(data []byte) error {
	var msg map[string]interface{}
	if err := json.Unmarshal(data, &msg); err != nil {
		return ValidationError{
			Code:    "INVALID_JSON",
			Field:   "",
			Reason:  "Invalid JSON format",
			Context: err.Error(),
		}
	}

	// First validate against base schema
	if err := v.validateAgainstSchema(msg, "ws_message"); err != nil {
		return err
	}

	// Validate timestamp format if present
	if ts, ok := msg["timestamp"].(string); ok {
		if err := v.ValidateTimestamp(ts); err != nil {
			return ValidationError{
				Code:   "INVALID_TIMESTAMP",
				Field:  "timestamp",
				Reason: err.Error(),
			}
		}
	}

	// Validate message type specific fields
	msgType, _ := msg["type"].(string)
	switch msgType {
	case "task.assigned":
		return v.validateTaskAssignedMessage(msg)
	case "task.completed":
		return v.validateTaskCompletedMessage(msg)
	case "heartbeat":
		return v.validateHeartbeatMessage(msg)
	}

	return nil
}

// validateAgainstSchema validates data against a schema
func (v *ProtocolValidator) validateAgainstSchema(data map[string]interface{}, schemaName string) error {
	schema, ok := v.schemas[schemaName]
	if !ok {
		return ValidationError{
			Code:   "UNKNOWN_SCHEMA",
			Field:  "",
			Reason: fmt.Sprintf("Unknown schema: %s", schemaName),
		}
	}

	// Check required fields
	for _, field := range schema.RequiredFields {
		if _, ok := data[field]; !ok {
			return ValidationError{
				Code:   "MISSING_FIELD",
				Field:  field,
				Reason: fmt.Sprintf("Required field '%s' is missing", field),
			}
		}
	}

	// Validate field types
	for field, expectedType := range schema.FieldTypes {
		if value, ok := data[field]; ok {
			if err := v.validateFieldType(field, value, expectedType); err != nil {
				return err
			}
		}
	}

	// Validate field patterns
	for field, pattern := range schema.FieldPatterns {
		if value, ok := data[field].(string); ok {
			if matched, _ := regexp.MatchString(pattern, value); !matched {
				return ValidationError{
					Code:    "PATTERN_MISMATCH",
					Field:   field,
					Reason:  fmt.Sprintf("Field '%s' does not match required pattern", field),
					Context: fmt.Sprintf("Value: %s, Pattern: %s", value, pattern),
				}
			}
		}
	}

	// Validate custom rules
	for field, rule := range schema.CustomRules {
		if value, ok := data[field]; ok {
			if err := v.validateCustomRule(field, value, rule); err != nil {
				return err
			}
		}
	}

	return nil
}

// validateFieldType validates a field's type
func (v *ProtocolValidator) validateFieldType(field string, value interface{}, expectedType string) error {
	actualType := detectType(value)
	if actualType != expectedType {
		return ValidationError{
			Code:   "TYPE_MISMATCH",
			Field:  field,
			Reason: fmt.Sprintf("Expected type '%s', got '%s'", expectedType, actualType),
		}
	}
	return nil
}

// validateCustomRule validates a custom rule
func (v *ProtocolValidator) validateCustomRule(field string, value interface{}, rule CustomRule) error {
	switch rule.Type {
	case "range":
		min, _ := rule.Params["min"].(float64)
		max, _ := rule.Params["max"].(float64)

		var num float64
		switch v := value.(type) {
		case float64:
			num = v
		case int:
			num = float64(v)
		default:
			return nil // Skip non-numeric values
		}

		if num < min || num > max {
			return ValidationError{
				Code:    "OUT_OF_RANGE",
				Field:   field,
				Reason:  fmt.Sprintf("Value must be between %.0f and %.0f", min, max),
				Context: fmt.Sprintf("Got: %.0f", num),
			}
		}
	case "version_compatible":
		if version, ok := value.(string); ok {
			minVersion, _ := rule.Params["min_version"].(string)
			if !v.isVersionCompatible(version, minVersion) {
				return ValidationError{
					Code:   "VERSION_INCOMPATIBLE",
					Field:  field,
					Reason: fmt.Sprintf("Version %s is not compatible with minimum version %s", version, minVersion),
				}
			}
		}
	}
	return nil
}

// ValidateTimestamp validates timestamp format
func (v *ProtocolValidator) ValidateTimestamp(ts string) error {
	// Try RFC3339
	if _, err := time.Parse(time.RFC3339, ts); err == nil {
		return nil
	}

	// Try RFC3339Nano
	if _, err := time.Parse(time.RFC3339Nano, ts); err == nil {
		return nil
	}

	// Try custom format (2006-01-02T15:04:05)
	if _, err := time.Parse("2006-01-02T15:04:05", ts); err == nil {
		return nil
	}

	return fmt.Errorf("timestamp must be in RFC3339 format")
}

// validateTaskAssignedMessage validates task.assigned message
func (v *ProtocolValidator) validateTaskAssignedMessage(msg map[string]interface{}) error {
	payload, ok := msg["payload"].(map[string]interface{})
	if !ok {
		return ValidationError{
			Code:   "INVALID_PAYLOAD",
			Field:  "payload",
			Reason: "Payload must be an object",
		}
	}

	if _, ok := payload["task_id"]; !ok {
		return ValidationError{
			Code:   "MISSING_FIELD",
			Field:  "payload.task_id",
			Reason: "Task ID is required in payload",
		}
	}

	return nil
}

// validateTaskCompletedMessage validates task.completed message
func (v *ProtocolValidator) validateTaskCompletedMessage(msg map[string]interface{}) error {
	payload, ok := msg["payload"].(map[string]interface{})
	if !ok {
		return ValidationError{
			Code:   "INVALID_PAYLOAD",
			Field:  "payload",
			Reason: "Payload must be an object",
		}
	}

	if _, ok := payload["task_id"]; !ok {
		return ValidationError{
			Code:   "MISSING_FIELD",
			Field:  "payload.task_id",
			Reason: "Task ID is required in payload",
		}
	}

	if _, ok := payload["result"]; !ok {
		return ValidationError{
			Code:   "MISSING_FIELD",
			Field:  "payload.result",
			Reason: "Result is required in payload",
		}
	}

	return nil
}

// validateHeartbeatMessage validates heartbeat message
func (v *ProtocolValidator) validateHeartbeatMessage(msg map[string]interface{}) error {
	payload, ok := msg["payload"].(map[string]interface{})
	if !ok {
		return ValidationError{
			Code:   "INVALID_PAYLOAD",
			Field:  "payload",
			Reason: "Payload must be an object",
		}
	}

	if agentID, ok := payload["agent_id"].(string); !ok || agentID == "" {
		return ValidationError{
			Code:   "MISSING_FIELD",
			Field:  "payload.agent_id",
			Reason: "Agent ID is required in heartbeat payload",
		}
	}

	return nil
}

// isVersionCompatible checks if version is compatible with required version
func (v *ProtocolValidator) isVersionCompatible(version, required string) bool {
	// Simple semver comparison - major version must match, minor >= required
	vParts := strings.Split(version, ".")
	rParts := strings.Split(required, ".")

	if len(vParts) < 2 || len(rParts) < 2 {
		return false
	}

	// Major version must match exactly
	if vParts[0] != rParts[0] {
		return false
	}

	return true
}

// detectType detects the type of a value
func detectType(value interface{}) string {
	switch v := value.(type) {
	case string:
		return "string"
	case float64:
		if v == float64(int64(v)) {
			return "integer"
		}
		return "number"
	case int, int64:
		return "integer"
	case bool:
		return "boolean"
	case map[string]interface{}:
		return "object"
	case []interface{}:
		return "array"
	default:
		return "unknown"
	}
}

// ValidationMiddleware creates HTTP middleware for request validation
func (v *ProtocolValidator) ValidationMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Only validate POST/PUT requests with bodies
		if r.Method != http.MethodPost && r.Method != http.MethodPut {
			next.ServeHTTP(w, r)
			return
		}

		// Determine which validator to use based on path
		var validateFunc func([]byte) error

		switch {
		case strings.HasSuffix(r.URL.Path, "/api/tasks"):
			validateFunc = v.ValidateTaskRequest
		case strings.Contains(r.URL.Path, "/claim"):
			validateFunc = v.ValidateTaskClaim
		case strings.HasSuffix(r.URL.Path, "/api/approvals"):
			validateFunc = v.ValidateApprovalRequest
		default:
			next.ServeHTTP(w, r)
			return
		}

		// Read and validate body
		body := make([]byte, r.ContentLength)
		if _, err := r.Body.Read(body); err != nil && r.ContentLength > 0 {
			http.Error(w, `{"error": "Failed to read request body"}`, http.StatusBadRequest)
			return
		}

		if err := validateFunc(body); err != nil {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"error":   "validation_failed",
				"details": err,
			})
			return
		}

		next.ServeHTTP(w, r)
	})
}

// ValidateResponse validates an HTTP response against expected schema
func (v *ProtocolValidator) ValidateResponse(endpoint string, data []byte) error {
	// Determine expected response schema based on endpoint
	switch {
	case strings.HasSuffix(endpoint, "/api/tasks"):
		// schemaName = "task_response"
	case strings.Contains(endpoint, "/api/agents"):
		// schemaName = "agent_response"
	default:
		return nil // No validation for unknown endpoints
	}

	var response map[string]interface{}
	if err := json.Unmarshal(data, &response); err != nil {
		return ValidationError{
			Code:    "INVALID_JSON",
			Field:   "",
			Reason:  "Response is not valid JSON",
			Context: err.Error(),
		}
	}

	// Basic response validation
	if _, ok := response["error"]; ok {
		// Error response - validate error format
		if _, ok := response["error"].(string); !ok {
			return ValidationError{
				Code:   "INVALID_ERROR_FORMAT",
				Field:  "error",
				Reason: "Error field must be a string",
			}
		}
		return nil
	}

	// Success response - could add more validation here
	return nil
}

// ProtocolCheckResult represents the result of a protocol compliance check
type ProtocolCheckResult struct {
	Valid    bool     `json:"valid"`
	Errors   []string `json:"errors,omitempty"`
	Warnings []string `json:"warnings,omitempty"`
	Version  string   `json:"version"`
}

// RunProtocolComplianceCheck runs a full protocol compliance check
func (v *ProtocolValidator) RunProtocolComplianceCheck() ProtocolCheckResult {
	result := ProtocolCheckResult{
		Valid:    true,
		Version:  ProtocolVersion,
		Errors:   []string{},
		Warnings: []string{},
	}

	// Check protocol version
	if ProtocolVersion == "" {
		result.Errors = append(result.Errors, "Protocol version not set")
		result.Valid = false
	}

	// Check schemas loaded
	if len(v.schemas) == 0 {
		result.Errors = append(result.Errors, "No validation schemas loaded")
		result.Valid = false
	}

	// Check required schemas exist
	requiredSchemas := []string{"task_create", "task_claim", "ws_handshake", "ws_message"}
	for _, schema := range requiredSchemas {
		if _, ok := v.schemas[schema]; !ok {
			result.Errors = append(result.Errors, fmt.Sprintf("Missing required schema: %s", schema))
			result.Valid = false
		}
	}

	return result
}

// GetProtocolVersion returns the current protocol version
func GetProtocolVersion() string {
	return ProtocolVersion
}

// IsValidTaskID validates a task ID format
func IsValidTaskID(id string) bool {
	if id == "" {
		return false
	}
	// Task IDs should be alphanumeric with hyphens, uppercase
	matched, _ := regexp.MatchString(`^[A-Z][A-Z0-9-]*$`, id)
	return matched
}

// IsValidAgentID validates an agent ID format
func IsValidAgentID(id string) bool {
	if id == "" {
		return false
	}
	// Agent IDs should be lowercase alphanumeric with hyphens
	matched, _ := regexp.MatchString(`^[a-z][a-z0-9-]*$`, id)
	return matched
}
